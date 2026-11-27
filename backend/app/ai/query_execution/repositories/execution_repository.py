"""Data access for executions, result metadata, result payloads and execution events.

Repositories never commit; ``QueryExecutionService`` owns every transaction.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.query_execution.policies.failures import ExecutionErrorCategory, ExecutionStatus, message_for
from app.models.sql_execution import QueryResultMetadata, QueryResultPayload, SQLExecutionEvent, SQLExecutionRecord
from app.models.user import User
from app.repositories.base_repository import Page


@dataclass(frozen=True, slots=True)
class ExecutionQuery:
    user_id: uuid.UUID | None  # None: every user (requires ai.sql.execution.read_all)
    statuses: tuple[str, ...] = ()
    created_from: datetime | None = None
    created_to: datetime | None = None
    generation_id: uuid.UUID | None = None


class ExecutionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # --- records ---------------------------------------------------------------------------------

    async def lock_user(self, user_id: uuid.UUID) -> None:
        """Serialise execution starts per user for this transaction (the concurrency limit check)."""
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": f"opspilot:sql-execution:{user_id}"}
        )

    async def count_active(self, user_id: uuid.UUID) -> int:
        result = await self.session.execute(
            select(func.count())
            .select_from(SQLExecutionRecord)
            .where(
                SQLExecutionRecord.user_id == user_id,
                SQLExecutionRecord.execution_status.in_([ExecutionStatus.PENDING.value, ExecutionStatus.RUNNING.value]),
            )
        )
        return int(result.scalar_one())

    async def end_stale(self, cutoff: datetime, now: datetime, *, user_id: uuid.UUID | None = None) -> list[SQLExecutionRecord]:
        """Executions left in flight by a crashed or killed process.

        ``pending`` ones never reached the connector: ``cancelled``. ``running`` ones may
        have reached the company API: ``outcome_unknown`` (never ``failed``, never retried).
        """
        ended: list[SQLExecutionRecord] = []
        for status, new_status, category in (
            (ExecutionStatus.PENDING, ExecutionStatus.CANCELLED, ExecutionErrorCategory.INTERNAL_ERROR),
            (ExecutionStatus.RUNNING, ExecutionStatus.OUTCOME_UNKNOWN, ExecutionErrorCategory.OUTCOME_UNKNOWN),
        ):
            conditions: list[ColumnElement[bool]] = [
                SQLExecutionRecord.execution_status == status.value,
                func.coalesce(SQLExecutionRecord.started_at, SQLExecutionRecord.created_at) < cutoff,
            ]
            if user_id is not None:
                conditions.append(SQLExecutionRecord.user_id == user_id)
            message = (
                "The execution did not start and was cancelled." if new_status == ExecutionStatus.CANCELLED
                else message_for(category)
            )
            result = await self.session.execute(
                update(SQLExecutionRecord)
                .where(*conditions)
                .values(execution_status=new_status.value, error_category=category.value, error_message=message,
                        completed_at=now)
                .returning(SQLExecutionRecord)
                .execution_options(synchronize_session=False)
            )
            ended.extend(result.scalars().all())
        return ended

    async def get(self, execution_id: uuid.UUID) -> SQLExecutionRecord | None:
        return await self.session.get(SQLExecutionRecord, execution_id, populate_existing=True)

    async def page(self, query: ExecutionQuery, *, offset: int, limit: int) -> Page[SQLExecutionRecord]:
        conditions: list[ColumnElement[bool]] = []
        if query.user_id is not None:
            conditions.append(SQLExecutionRecord.user_id == query.user_id)
        if query.statuses:
            conditions.append(SQLExecutionRecord.execution_status.in_(query.statuses))
        if query.created_from is not None:
            conditions.append(SQLExecutionRecord.created_at >= query.created_from)
        if query.created_to is not None:
            conditions.append(SQLExecutionRecord.created_at < query.created_to)
        if query.generation_id is not None:
            conditions.append(SQLExecutionRecord.generation_id == query.generation_id)
        total = int((await self.session.execute(
            select(func.count()).select_from(SQLExecutionRecord).where(*conditions)
        )).scalar_one())
        items = (await self.session.execute(
            select(SQLExecutionRecord).where(*conditions)
            .order_by(SQLExecutionRecord.created_at.desc(), SQLExecutionRecord.id.desc())
            .offset(offset).limit(limit)
        )).scalars().all()
        return Page(items=items, total=total, offset=offset, limit=limit)

    async def user_names(self, user_ids: set[uuid.UUID]) -> dict[uuid.UUID, tuple[str, str]]:
        if not user_ids:
            return {}
        rows = await self.session.execute(select(User.id, User.full_name, User.email).where(User.id.in_(user_ids)))
        return {row.id: (row.full_name, row.email) for row in rows}

    # --- results ---------------------------------------------------------------------------------

    async def metadata(self, execution_id: uuid.UUID) -> QueryResultMetadata | None:
        return await self.session.get(QueryResultMetadata, execution_id, populate_existing=True)

    async def payload(self, execution_id: uuid.UUID) -> QueryResultPayload | None:
        return await self.session.get(QueryResultPayload, execution_id, populate_existing=True)

    async def delete_payload(self, execution_id: uuid.UUID) -> bool:
        result = await self.session.execute(
            delete(QueryResultPayload).where(QueryResultPayload.execution_id == execution_id)
            .returning(QueryResultPayload.execution_id)
        )
        return result.scalar_one_or_none() is not None

    async def purge_expired_payloads(self, now: datetime, *, batch: int = 500) -> int:
        expired = select(QueryResultPayload.execution_id).where(QueryResultPayload.expires_at <= now).limit(batch)
        result = await self.session.execute(
            delete(QueryResultPayload).where(QueryResultPayload.execution_id.in_(expired))
            .returning(QueryResultPayload.execution_id)
        )
        return len(result.scalars().all())

    # --- audit -----------------------------------------------------------------------------------

    def event(self, event_type: str, record: SQLExecutionRecord | None = None, **fields: Any) -> SQLExecutionEvent:
        values: dict[str, Any] = {}
        if record is not None:
            values = {
                "execution_id": record.id, "approval_id": record.approval_id, "generation_id": record.generation_id,
                "user_id": record.user_id, "connector_id": record.connector_id,
                "schema_source_id": record.schema_source_id, "schema_version": record.schema_version,
                "query_hash": record.query_hash, "status": record.execution_status,
                "error_category": record.error_category, "execution_reference": record.execution_reference,
                "request_id": record.request_id,
            }
        values.update(fields)
        event = SQLExecutionEvent(event_type=event_type, **values)
        self.session.add(event)
        return event

    async def events(self, execution_id: uuid.UUID) -> list[SQLExecutionEvent]:
        result = await self.session.execute(
            select(SQLExecutionEvent).where(SQLExecutionEvent.execution_id == execution_id)
            .order_by(SQLExecutionEvent.created_at, SQLExecutionEvent.id)
        )
        return list(result.scalars().all())

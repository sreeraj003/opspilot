"""Queries over ``ai_usage_records`` (flush, never commit)."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import ColumnElement, and_, case, func, select, true

from app.models.ai_usage import AIUsageRecord, AIUsageStatus
from app.repositories.base_repository import BaseRepository, Page, validate_pagination


@dataclass(frozen=True, slots=True)
class UsageFilter:
    provider: str | None = None
    status: str | None = None
    operation: str | None = None
    since: datetime | None = None


@dataclass(frozen=True, slots=True)
class UsageTotals:
    requests: int
    successful: int
    failed: int
    requests_with_usage: int
    input_tokens: int
    output_tokens: int
    total_tokens: int


class AIUsageRepository(BaseRepository[AIUsageRecord]):
    model = AIUsageRecord
    default_order_by = (AIUsageRecord.created_at.desc(), AIUsageRecord.id)

    def _conditions(self, filters: UsageFilter) -> list[ColumnElement[bool]]:
        conditions: list[ColumnElement[bool]] = []
        if filters.provider:
            conditions.append(AIUsageRecord.provider == filters.provider)
        if filters.status:
            conditions.append(AIUsageRecord.status == filters.status)
        if filters.operation:
            conditions.append(AIUsageRecord.operation == filters.operation)
        if filters.since is not None:
            conditions.append(AIUsageRecord.created_at >= filters.since)
        return conditions

    async def search(self, filters: UsageFilter, *, offset: int, limit: int) -> Page[AIUsageRecord]:
        validate_pagination(offset, limit)
        where = and_(true(), *self._conditions(filters))
        items: Sequence[AIUsageRecord] = (
            await self.session.scalars(
                select(AIUsageRecord).where(where).order_by(*self.default_order_by).offset(offset).limit(limit)
            )
        ).all()
        total = await self.session.scalar(select(func.count()).select_from(AIUsageRecord).where(where))
        return Page(items=items, total=int(total or 0), offset=offset, limit=limit)

    async def totals(self, filters: UsageFilter) -> UsageTotals:
        r = AIUsageRecord
        has_usage = (r.input_tokens.is_not(None)) | (r.output_tokens.is_not(None)) | (r.total_tokens.is_not(None))
        statement = select(
            func.count(),
            func.count().filter(r.status == AIUsageStatus.SUCCESS),
            func.count().filter(r.status == AIUsageStatus.FAILURE),
            func.count().filter(has_usage),
            func.coalesce(func.sum(r.input_tokens), 0),
            func.coalesce(func.sum(r.output_tokens), 0),
            func.coalesce(func.sum(case((r.total_tokens.is_not(None), r.total_tokens), else_=0)), 0),
        ).where(and_(true(), *self._conditions(filters)))
        row = (await self.session.execute(statement)).one()
        return UsageTotals(*(int(v) for v in row))

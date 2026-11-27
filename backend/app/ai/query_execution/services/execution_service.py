"""``QueryExecutionService``: approved SQL in, validated and normalised result out (Module 12).

The only way to run SQL against the company database. It never accepts SQL,
a URL or a connector from a caller: only an approval id and the
authenticated user. Execution flow (``execute_approved_query``):

1.  Load the approval (application database). Unknown, or another user's:
    "not found" (nothing reveals that it exists).
2.  Concurrency: under a per-user advisory lock, end stale in-flight
    executions and refuse when ``AI_SQL_EXEC_MAX_CONCURRENT_PER_USER`` are
    already in flight. Create the ``pending`` execution record.
3.  Connector association: the approval's connector must still exist, be
    active and serve the approval's schema source.
4.  Module 11's ``SQLExecutionGuard.authorize_execution`` (one short
    transaction, row lock on the approval): status, expiry, user, connector;
    the stored generation's SQL is re-run through the full guard with the
    *current* schema version and permissions and must produce exactly the
    approved SQL (same SHA-256) with the same permission fingerprint; then the
    approval is consumed atomically (conditional UPDATE) and a sealed
    ``ValidatedQueryRequest`` is issued. Any failure: ``rejected``.
5.  Mark the record ``running`` (committed: no transaction stays open
    during the HTTP request).
6.  Dispatch through Module 07 (``ConnectorDispatcher``): the connector
    service verifies the seal and hash, claims the approval once
    (consumed -> executed) and sends the GET with its destination policy,
    timeouts and streamed size limit.
7.  Map connector failures: definite failures are ``failed``; a request that
    may have reached the company API without a complete answer is
    ``timed_out`` / ``outcome_unknown`` and is **never retried**.
8.  Validate and normalise the result (``normalize_result``) within the
    resource limits; persist metadata, the short-lived payload and the
    ``succeeded`` record.
9.  Post-execution authorization (``_authorize_delivery``): the guard must
    approve the same query *now* with the same permission fingerprint, or the
    rows are withheld (deleted, never shown) and the event is recorded.
10. Return a typed ``QueryExecutionResult``.

Every step writes ``sql_execution_events`` (ids, hashes and codes; never SQL
or values).
"""

import csv
import io
import json
import logging
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.query_execution.adapters.connector import ConnectorDispatcher
from app.ai.query_execution.normalizers.result import NormalizedTable, normalize_result
from app.ai.query_execution.policies.failures import (
    UNCERTAIN_STATUSES,
    ExecutionErrorCategory,
    ExecutionStatus,
    from_connector_error,
    from_guard_reason,
    message_for,
)
from app.ai.query_execution.policies.limits import ExecutionLimits
from app.ai.query_execution.repositories.execution_repository import ExecutionQuery, ExecutionRepository
from app.ai.query_execution.schemas.result import (
    Delivery,
    ExecutionError,
    ExecutionPage,
    ExecutionSummary,
    QueryExecutionResult,
    RequestingUser,
    ResultColumnView,
    ResultMetadataView,
)
from app.ai.query_execution.validators.projection import approved_projection
from app.ai.query_execution.validators.response import ResultValidationError
from app.ai.sql_security.services.execution_service import ExecutionNotAuthorizedError, SQLExecutionGuard
from app.ai.sql_security.services.validation_service import SQLSecurityValidationService
from app.connectors.errors import ConnectorExecutionError
from app.connectors.trusted import ExecutionApprovalAuthority
from app.core.exceptions import AppError, NotFoundError, PermissionDeniedError
from app.core.logging import get_request_id
from app.database.transaction import transaction
from app.models.connector import ExternalAPIConnector
from app.models.sql_execution import QueryResultMetadata, QueryResultPayload, SQLExecutionRecord
from app.models.sql_generation import SQLGenerationRecord
from app.models.sql_security import SQLExecutionApproval
from app.models.user import User
from app.services.connector_service import ConnectorService, ConnectorUnavailableError

logger = logging.getLogger("app.ai.query_execution")

S = ExecutionStatus
C = ExecutionErrorCategory


class ExecutionApprovalNotFoundError(NotFoundError):
    code = "SQL_APPROVAL_NOT_FOUND"
    message = "The execution approval was not found."


class ExecutionNotFoundError(NotFoundError):
    code = "SQL_EXECUTION_NOT_FOUND"
    message = "The execution was not found."


class ExecutionConcurrencyLimitError(AppError):
    status_code = 429
    code = "SQL_EXECUTION_CONCURRENCY_LIMIT"
    message = message_for(C.CONCURRENCY_LIMIT)

    def __init__(self) -> None:
        super().__init__(headers={"Retry-After": "5"})


class ResultNotAvailableError(AppError):
    status_code = 409
    code = "SQL_RESULT_NOT_AVAILABLE"
    message = "This execution has no result that can be exported."


class ExportNotPermittedError(PermissionDeniedError):
    code = "SQL_RESULT_EXPORT_DENIED"
    message = "Your data permissions do not allow exporting this result."


@dataclass(frozen=True, slots=True)
class DeliveryDecision:
    allowed: bool
    sql_visible: bool = False
    export_allowed: bool = False
    reason: str | None = None


_WITHHELD_MESSAGE = (
    "The result is not shown because your data permissions or the query's context changed after it was approved. "
    "Validate and run the query again."
)
_EXPIRED_MESSAGE = "The result rows are no longer kept. Run the query again to see them."
_RESTRICTED_MESSAGE = "Only the user who ran this query can see its result."


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


class QueryExecutionService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: object,
        validation: SQLSecurityValidationService,
        authority: ExecutionApprovalAuthority,
        connectors: ConnectorService,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session = session
        self._validation = validation
        self._guard = SQLExecutionGuard(validation, authority)
        self._limits = ExecutionLimits.from_settings(settings)
        self._dispatcher = ConnectorDispatcher(connectors, self._limits)
        self._repo = ExecutionRepository(session)
        self._clock = clock or validation.now

    def now(self) -> datetime:
        return self._clock()

    # =========================================================================================
    # execute
    # =========================================================================================

    async def execute_approved_query(self, approval_id: uuid.UUID, requesting_user_id: uuid.UUID) -> QueryExecutionResult:
        session = self._session
        user = await self._validation._live_user(requesting_user_id)
        approval = await session.get(SQLExecutionApproval, approval_id, populate_existing=True)
        if user is None or approval is None or approval.user_id != requesting_user_id:
            async with transaction(session):
                self._repo.event("execution_rejected", user_id=user.id if user else None, approval_id=approval_id,
                                 status=S.REJECTED.value, error_category=C.APPROVAL_NOT_FOUND.value,
                                 request_id=get_request_id())
            raise ExecutionApprovalNotFoundError()

        record = await self._start(user, approval)

        # Connector association (checked here so a temporarily inactive connector does not burn the approval).
        connector = await session.get(ExternalAPIConnector, approval.connector_id, populate_existing=True)
        if (connector is None or connector.deleted_at is not None or not connector.is_active
                or connector.schema_source_id != approval.schema_source_id):
            return await self._reject(record, C.CONNECTOR_INACTIVE, "connector_not_active_for_schema", user)

        # Module 11: lock, bind check, full re-validation, atomic single-use consumption, sealed request.
        try:
            request = await self._guard.authorize_execution(user, approval.id, connector_id=approval.connector_id)
        except ExecutionNotAuthorizedError as exc:
            return await self._reject(record, from_guard_reason(exc.reason), exc.reason, user)
        except SQLAlchemyError:
            await self._recover(record)
            logger.exception("Execution authorization failed in the database", extra={"event": "ai.sql_execution.db_error"})
            return await self._finish(record, S.FAILED, C.INTERNAL_ERROR, user, internal="authorization_database_error")

        async with transaction(session):
            record.execution_status = S.RUNNING.value
            record.started_at = self.now()
            record.execution_reference = request.execution_reference
            self._repo.event("execution_dispatched", record)
        logger.info("SQL execution dispatched", extra={"event": "ai.sql_execution.dispatched",
                                                        "execution_id": str(record.id),
                                                        "execution_reference": str(request.execution_reference)})

        started = time.monotonic()
        try:
            raw = await self._dispatcher.dispatch(request)
        except ConnectorExecutionError as exc:
            status, category = from_connector_error(exc)
            return await self._finish(record, status, category, user, started=started,
                                      internal=f"connector:{exc.category.value}", http_status=exc.http_status)
        except ConnectorUnavailableError:
            # Raised before the approval is claimed or anything is sent (no HTTP client in this process).
            await self._recover(record)
            return await self._finish(record, S.FAILED, C.CONNECTOR_MISCONFIGURED, user, started=started,
                                      internal="connector_service_unavailable")
        except Exception:
            # Unknown failure after the request was handed to the connector: it may have been sent.
            logger.exception("SQL execution failed unexpectedly", extra={"event": "ai.sql_execution.unexpected"})
            await self._recover(record)
            return await self._finish(record, S.OUTCOME_UNKNOWN, C.OUTCOME_UNKNOWN, user, started=started,
                                      internal="unexpected_dispatch_error")
        duration_ms = int((time.monotonic() - started) * 1000)

        try:
            projection = approved_projection(approval.exact_sql, approval.dialect)
            table = normalize_result(raw.rows, metadata=raw.metadata, limits=self._limits, projection=projection)
        except ResultValidationError as exc:
            return await self._finish(record, S.FAILED, exc.category, user, started=started, internal="result_validation",
                                      response_size=raw.response_size_bytes)
        await self._store_success(record, table, raw.response_size_bytes, raw.execution_time_ms, duration_ms)
        return await self.result(user, record.id, _record=record)

    async def _recover(self, record: SQLExecutionRecord) -> None:
        """After a failed step: discard the broken transaction and reload the record's committed state."""
        await self._session.rollback()
        await self._session.refresh(record)

    async def _start(self, user: User, approval: SQLExecutionApproval) -> SQLExecutionRecord:
        now = self.now()
        limited = False
        record: SQLExecutionRecord | None = None
        async with transaction(self._session):
            await self._repo.lock_user(user.id)
            await self._end_stale(now, user_id=user.id)
            await self._repo.purge_expired_payloads(now)
            if await self._repo.count_active(user.id) >= self._limits.max_concurrent_per_user:
                limited = True
            else:
                record = SQLExecutionRecord(
                    approval_id=approval.id, generation_id=approval.generation_id, user_id=user.id,
                    connector_id=approval.connector_id, schema_source_id=approval.schema_source_id,
                    schema_version=approval.schema_version, query_hash=approval.exact_sql_hash,
                    execution_status=S.PENDING.value, request_id=get_request_id(),
                )
                self._session.add(record)
                await self._session.flush()
                self._repo.event("execution_requested", record)
        if limited:
            async with transaction(self._session):
                self._repo.event("execution_rejected", user_id=user.id, approval_id=approval.id,
                                 generation_id=approval.generation_id, query_hash=approval.exact_sql_hash,
                                 status=S.REJECTED.value, error_category=C.CONCURRENCY_LIMIT.value,
                                 request_id=get_request_id())
            raise ExecutionConcurrencyLimitError()
        assert record is not None
        return record

    async def _reject(self, record: SQLExecutionRecord, category: ExecutionErrorCategory, internal: str,
                      user: User) -> QueryExecutionResult:
        return await self._finish(record, S.REJECTED, category, user, internal=internal)

    async def _finish(
        self,
        record: SQLExecutionRecord,
        status: ExecutionStatus,
        category: ExecutionErrorCategory,
        user: User,
        *,
        started: float | None = None,
        internal: str | None = None,
        http_status: int | None = None,
        response_size: int | None = None,
    ) -> QueryExecutionResult:
        now = self.now()
        async with transaction(self._session):
            record.execution_status = status.value
            record.error_category = category.value
            record.error_message = message_for(category)
            record.completed_at = now
            if started is not None:
                record.duration_ms = int((time.monotonic() - started) * 1000)
            if response_size is not None:
                record.response_size_bytes = response_size
            event_type = {
                S.REJECTED: "execution_rejected", S.TIMED_OUT: "execution_timed_out",
                S.OUTCOME_UNKNOWN: "execution_outcome_unknown", S.CANCELLED: "execution_cancelled",
            }.get(status, "execution_failed")
            details: dict[str, object] = {}
            if internal:
                details["reason"] = internal[:120]
            if http_status is not None:
                details["http_status"] = http_status
            if status in UNCERTAIN_STATUSES:
                details["retried"] = False
            self._repo.event(event_type, record, details=details)
        log = logger.warning if status in UNCERTAIN_STATUSES else logger.info
        log("SQL execution ended without a result", extra={
            "event": f"ai.sql_execution.{status.value}", "execution_id": str(record.id),
            "error_category": category.value, "reason": internal,
            "execution_reference": str(record.execution_reference) if record.execution_reference else None,
        })
        return self._result_view(record, user, decision=None, metadata=None, payload=None)

    async def _store_success(self, record: SQLExecutionRecord, table: NormalizedTable, response_size: int,
                             external_ms: float | None, duration_ms: int) -> None:
        now = self.now()
        async with transaction(self._session):
            record.execution_status = S.SUCCEEDED.value
            record.completed_at = now
            record.duration_ms = duration_ms
            record.rows_returned = table.row_count
            record.response_size_bytes = response_size
            record.external_execution_time_ms = int(external_ms) if external_ms is not None else None
            record.truncated = table.truncated
            self._session.add(QueryResultMetadata(
                execution_id=record.id, columns=[c.as_dict() for c in table.columns], row_count=table.row_count,
                rows_received=table.rows_received, truncated=table.truncated,
                truncation_reasons=list(dict.fromkeys(table.truncation_reasons)), withheld_columns=table.withheld_columns,
                query_row_limit=table.query_row_limit, limit_reached=table.limit_reached,
                normalized_size_bytes=table.normalized_size_bytes,
            ))
            self._session.add(QueryResultPayload(
                execution_id=record.id, rows=table.rows, created_at=now,
                expires_at=now + timedelta(minutes=self._limits.result_retention_minutes),
            ))
            self._repo.event("execution_succeeded", record, details={
                "rows_received": table.rows_received, "rows_kept": table.row_count, "truncated": table.truncated,
                "truncation_reasons": table.truncation_reasons, "limit_reached": table.limit_reached,
            })
            if table.withheld_columns:
                self._repo.event("columns_withheld", record, details={"count": len(table.withheld_columns),
                                                                       "columns": table.withheld_columns[:50]})
        logger.info("SQL execution succeeded", extra={
            "event": "ai.sql_execution.succeeded", "execution_id": str(record.id), "rows_returned": table.row_count,
            "truncated": table.truncated, "duration_ms": duration_ms, "response_size_bytes": response_size,
        })

    async def _end_stale(self, now: datetime, *, user_id: uuid.UUID | None = None) -> None:
        cutoff = now - timedelta(seconds=self._limits.stale_after_seconds)
        for ended in await self._repo.end_stale(cutoff, now, user_id=user_id):
            event = "execution_cancelled" if ended.execution_status == S.CANCELLED.value else "execution_outcome_unknown"
            self._repo.event(event, ended, details={"reason": "stale_in_flight", "retried": False})

    # =========================================================================================
    # read
    # =========================================================================================

    async def _visible_record(self, viewer: User, execution_id: uuid.UUID, *, read_all: bool) -> SQLExecutionRecord:
        async with transaction(self._session):
            await self._end_stale(self.now())
        record = await self._repo.get(execution_id)
        if record is None or (record.user_id != viewer.id and not read_all):
            raise ExecutionNotFoundError()
        return record

    async def result(self, viewer: User, execution_id: uuid.UUID, *, read_all: bool = False,
                     _record: SQLExecutionRecord | None = None) -> QueryExecutionResult:
        record = _record or await self._visible_record(viewer, execution_id, read_all=read_all)
        metadata = await self._repo.metadata(record.id)
        if record.user_id != viewer.id:
            view = self._result_view(record, viewer, decision=None, metadata=metadata, payload=None)
            view.delivery = Delivery.RESTRICTED if record.execution_status == S.SUCCEEDED.value else Delivery.NONE
            view.delivery_message = _RESTRICTED_MESSAGE if view.delivery == Delivery.RESTRICTED else None
            return await self._with_names(view, record, read_all)
        if record.execution_status != S.SUCCEEDED.value:
            return self._result_view(record, viewer, decision=None, metadata=metadata, payload=None)
        payload = await self._repo.payload(record.id)
        now = self.now()
        if payload is None or _aware(payload.expires_at) <= now:  # type: ignore[operator]
            async with transaction(self._session):
                if payload is not None and await self._repo.delete_payload(record.id):
                    self._repo.event("result_expired", record)
            view = self._result_view(record, viewer, decision=None, metadata=metadata, payload=None)
            view.delivery, view.delivery_message = Delivery.EXPIRED, _EXPIRED_MESSAGE
            return view
        decision = await self._authorize_delivery(viewer, record)
        async with transaction(self._session):
            if decision.allowed:
                self._repo.event("result_delivered", record, actor_user_id=viewer.id,
                                 details={"rows": len(payload.rows)})
            else:
                await self._repo.delete_payload(record.id)
                self._repo.event("result_withheld", record, actor_user_id=viewer.id,
                                 details={"reason": (decision.reason or "unknown")[:120]})
                logger.warning("SQL result withheld after a permission or context change", extra={
                    "event": "ai.sql_execution.result_withheld", "execution_id": str(record.id),
                    "reason": decision.reason})
        if not decision.allowed:
            view = self._result_view(record, viewer, decision=None, metadata=metadata, payload=None)
            view.delivery, view.delivery_message = Delivery.WITHHELD, _WITHHELD_MESSAGE
            return view
        view = self._result_view(record, viewer, decision=decision, metadata=metadata, payload=payload)
        if decision.sql_visible and record.approval_id is not None:
            approval = await self._session.get(SQLExecutionApproval, record.approval_id)
            if approval is not None and approval.exact_sql_hash == record.query_hash:
                view.sql, view.sql_visible = approval.exact_sql, True
        return view

    async def metadata(self, viewer: User, execution_id: uuid.UUID, *, read_all: bool = False) -> ResultMetadataView:
        record = await self._visible_record(viewer, execution_id, read_all=read_all)
        metadata = await self._repo.metadata(record.id)
        payload = await self._repo.payload(record.id) if record.user_id == viewer.id else None
        now = self.now()
        available = payload is not None and _aware(payload.expires_at) > now  # type: ignore[operator]
        view = ResultMetadataView(
            execution_id=record.id, status=S(record.execution_status), response_size_bytes=record.response_size_bytes,
            external_execution_time_ms=record.external_execution_time_ms, duration_ms=record.duration_ms,
            result_available=available, correlation_id=record.execution_reference,
            result_expires_at=_aware(payload.expires_at) if payload is not None else None,
        )
        if metadata is not None:
            view.columns = [ResultColumnView(**c) for c in metadata.columns]
            view.row_count = metadata.row_count
            view.rows_received = metadata.rows_received
            view.truncated = metadata.truncated
            view.truncation_reasons = list(metadata.truncation_reasons)  # type: ignore[arg-type]
            view.withheld_columns_count = len(metadata.withheld_columns)
            view.query_row_limit = metadata.query_row_limit
            view.limit_reached = metadata.limit_reached
            view.result_format = metadata.result_format
            view.normalized_size_bytes = metadata.normalized_size_bytes
            view.result_created_at = _aware(metadata.created_at)
        return view

    async def history(
        self, viewer: User, *, read_all: bool, all_users: bool, statuses: tuple[str, ...], created_from: datetime | None,
        created_to: datetime | None, generation_id: uuid.UUID | None, offset: int, limit: int,
    ) -> ExecutionPage:
        async with transaction(self._session):
            await self._end_stale(self.now())
        query = ExecutionQuery(
            user_id=None if (all_users and read_all) else viewer.id, statuses=statuses, created_from=created_from,
            created_to=created_to, generation_id=generation_id,
        )
        page = await self._repo.page(query, offset=offset, limit=limit)
        names = await self._repo.user_names({r.user_id for r in page.items}) if read_all else {}
        items = [self._summary(r, viewer, names if read_all else None) for r in page.items]
        return ExecutionPage(items=items, total=page.total, offset=page.offset, limit=page.limit)

    async def export_csv(self, viewer: User, execution_id: uuid.UUID) -> tuple[str, str, bool]:
        """(filename, CSV text, truncated). Needs ai.sql.result.export (route) and allow_export on the data."""
        record = await self._visible_record(viewer, execution_id, read_all=False)
        if record.execution_status != S.SUCCEEDED.value:
            raise ResultNotAvailableError()
        payload = await self._repo.payload(record.id)
        metadata = await self._repo.metadata(record.id)
        if payload is None or metadata is None or _aware(payload.expires_at) <= self.now():  # type: ignore[operator]
            raise ResultNotAvailableError()
        decision = await self._authorize_delivery(viewer, record)
        if not decision.allowed:
            async with transaction(self._session):
                await self._repo.delete_payload(record.id)
                self._repo.event("result_withheld", record, actor_user_id=viewer.id,
                                 details={"reason": (decision.reason or "unknown")[:120], "export": True})
            raise ResultNotAvailableError()
        if not decision.export_allowed:
            async with transaction(self._session):
                self._repo.event("result_withheld", record, actor_user_id=viewer.id,
                                 details={"reason": "export_not_permitted", "export": True})
            raise ExportNotPermittedError()
        columns = [c["name"] for c in metadata.columns]
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\r\n")
        writer.writerow([_csv_cell(c) for c in columns])
        for row in payload.rows:
            writer.writerow([_csv_cell(row.get(c)) for c in columns])
        async with transaction(self._session):
            self._repo.event("result_exported", record, actor_user_id=viewer.id,
                             details={"format": "csv", "rows": len(payload.rows), "truncated": metadata.truncated})
        return f"opspilot-result-{record.id}.csv", buffer.getvalue(), metadata.truncated

    # =========================================================================================
    # post-execution authorization
    # =========================================================================================

    async def _authorize_delivery(self, viewer: User, record: SQLExecutionRecord) -> DeliveryDecision:
        """The result belongs to this approved execution and the viewer may still see it, under *current* rules.

        The guard re-evaluates the stored generation now: it must still approve exactly the
        executed SQL (same hash) with the same permission fingerprint the approval was bound to.
        Any change (revoked column or table access, new row restriction, schema change) withholds
        the result instead of disclosing it under stale authorization.
        """
        if record.user_id != viewer.id or record.approval_id is None or record.generation_id is None:
            return DeliveryDecision(False, reason="not_owner_or_unbound")
        approval = await self._session.get(SQLExecutionApproval, record.approval_id, populate_existing=True)
        generation = await self._session.get(SQLGenerationRecord, record.generation_id, populate_existing=True)
        live = await self._validation._live_user(viewer.id)
        if approval is None or generation is None or live is None:
            return DeliveryDecision(False, reason="context_unavailable")
        if approval.exact_sql_hash != record.query_hash or approval.user_id != viewer.id:
            return DeliveryDecision(False, reason="approval_mismatch")
        evaluation = await self._validation.evaluate(live, generation, record.connector_id)
        outcome = evaluation.outcome
        if not outcome.approved or outcome.access is None:
            category = outcome.category.value if outcome.category else "unknown"
            return DeliveryDecision(False, reason=f"revalidation_{outcome.status.value}:{category}")
        if evaluation.schema_version != record.schema_version:
            return DeliveryDecision(False, reason="schema_version_changed")
        if outcome.access.fingerprint != approval.permission_fingerprint:
            return DeliveryDecision(False, reason="permissions_changed")
        if outcome.query_hash != record.query_hash:
            return DeliveryDecision(False, reason="sql_changed")
        return DeliveryDecision(True, sql_visible=outcome.access.sql_visible, export_allowed=outcome.access.export_allowed)

    # =========================================================================================
    # views
    # =========================================================================================

    def _summary(self, record: SQLExecutionRecord, viewer: User,
                 names: dict[uuid.UUID, tuple[str, str]] | None) -> ExecutionSummary:
        status = S(record.execution_status)
        summary = ExecutionSummary(
            execution_id=record.id, approval_id=record.approval_id, generation_id=record.generation_id, status=status,
            outcome_known=status in (S.SUCCEEDED, S.FAILED, S.REJECTED, S.CANCELLED),
            error=(ExecutionError(category=C(record.error_category), message=record.error_message or "")
                   if record.error_category else None),
            created_at=_aware(record.created_at),  # type: ignore[arg-type]
            started_at=_aware(record.started_at), completed_at=_aware(record.completed_at),
            duration_ms=record.duration_ms, rows_returned=record.rows_returned, truncated=record.truncated,
            schema_version=record.schema_version, query_hash=record.query_hash, is_own=record.user_id == viewer.id,
        )
        if names is not None:
            full_name, email = names.get(record.user_id, (None, None))
            summary.requested_by = RequestingUser(user_id=record.user_id, full_name=full_name, email=email)
        return summary

    async def _with_names(self, view: QueryExecutionResult, record: SQLExecutionRecord, read_all: bool) -> QueryExecutionResult:
        if read_all:
            full_name, email = (await self._repo.user_names({record.user_id})).get(record.user_id, (None, None))
            view.requested_by = RequestingUser(user_id=record.user_id, full_name=full_name, email=email)
        return view

    def _result_view(self, record: SQLExecutionRecord, viewer: User, *, decision: DeliveryDecision | None,
                     metadata: QueryResultMetadata | None, payload: QueryResultPayload | None) -> QueryExecutionResult:
        summary = self._summary(record, viewer, None)
        view = QueryExecutionResult(
            **summary.model_dump(), external_execution_time_ms=record.external_execution_time_ms,
            response_size_bytes=record.response_size_bytes, correlation_id=record.execution_reference,
        )
        if metadata is not None:
            view.columns = [ResultColumnView(**c) for c in metadata.columns]
            view.rows_received = metadata.rows_received
            view.truncation_reasons = list(metadata.truncation_reasons)  # type: ignore[arg-type]
            view.limit_reached = metadata.limit_reached
            view.query_row_limit = metadata.query_row_limit
            view.withheld_columns_count = len(metadata.withheld_columns)
            view.row_count = metadata.row_count
        if decision is not None and decision.allowed and payload is not None:
            view.delivery = Delivery.DELIVERED
            view.rows = list(payload.rows)
            view.row_count = len(payload.rows)
            view.result_expires_at = _aware(payload.expires_at)
            view.can_export = decision.export_allowed
        return view


_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
_NUMBER = re.compile(r"^-?\d+(\.\d+)?([eE][+-]?\d+)?$")


def _csv_cell(value: object) -> str:
    """CSV text for one value; text that a spreadsheet would run as a formula is prefixed with '."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        text = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    else:
        text = str(value)
    if isinstance(value, str) and text.startswith(_FORMULA_PREFIXES) and not _NUMBER.match(text):
        return "'" + text
    return text

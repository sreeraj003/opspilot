"""``SQLSecurityValidationService``: persisted SQL generation -> security decision -> approval.

Validation pipeline (``POST /ai/sql/generations/{id}/validate``):

1. Receive a persisted SQL generation record (owned by the caller).
2. Verify the requesting user's identity (re-read: live and active).
3. Verify access to the schema source (the user holds data permissions on it).
4. Retrieve the current schema version (Module 06); a generation made for an
   older version is not validated.
5-15. ``SQLSecurityInspector``: parse the exact stored SQL with SQLGlot,
   verify the dialect, inspect the whole AST, statement type, tables,
   columns, joins, functions, dynamic permissions (Module 04's engine, for
   exactly the parsed references), row-level restrictions, complexity and
   result limits.
16. Store a structured, append-only ``SQLSecurityValidation``.
17. If approved, create a short-lived, single-use ``SQLExecutionApproval``
   bound to the exact rewritten SQL (SHA-256), user, connector, schema
   source and version, and the permission fingerprint. Earlier unused
   approvals for the same generation are invalidated ("superseded").

The stored SQL's own SHA-256 (Module 10's ``sql_sha256``) is checked first, so
a stored generation that was altered after it was generated is refused.
Nothing is executed and the company API is never called here.
"""

import hashlib
import json
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.sql_generation.dialects.registry import SQLDialect, UnsupportedDialectError, dialect_for_database_type
from app.ai.sql_security.policies.limits import GuardOptions
from app.ai.sql_security.schemas.result import (
    ApprovalStatus,
    ApprovalView,
    InternalReason,
    ValidationMessage,
    ValidationStatus,
    ValidationView,
)
from app.ai.sql_security.services.inspector import (
    AccessDecision,
    InspectionOutcome,
    SQLSecurityInspector,
    sql_sha256,
    status_for,
)
from app.ai.sql_security.validators.violations import Category, Violation, violation
from app.ai.understanding.services.permission_view import PermissionView, authorize_references
from app.ai.understanding.services.schema_context import SchemaContext
from app.ai.understanding.services.terminology_service import TerminologyService
from app.authorization.engine import AuthorizationDecision, AuthorizationEngine
from app.core.authorization import is_live
from app.core.config import Settings
from app.core.exceptions import ConflictError, NotFoundError
from app.core.logging import get_request_id
from app.database.transaction import transaction
from app.models.connector import ExternalAPIConnector
from app.models.sql_generation import SQLGenerationRecord
from app.models.sql_security import SQLExecutionApproval, SQLSecurityEvent, SQLSecurityValidation
from app.models.user import User

logger = logging.getLogger("app.ai.sql_security")

SUMMARIES: dict[ValidationStatus, str] = {
    ValidationStatus.APPROVED: "The query passed every security check and is approved for one execution.",
    ValidationStatus.REJECTED: "The query was rejected by the SQL security guard.",
    ValidationStatus.CLARIFICATION_REQUIRED: "The question needs clarification; there is no SQL to validate yet.",
    ValidationStatus.VALIDATION_FAILED: "The query could not be validated.",
    ValidationStatus.APPROVAL_EXPIRED: "The query was approved, but its approval expired. Validate it again to run it.",
}
EXECUTION_TIMEOUT_WARNING = (
    "The connector's timeout ({timeout} s) is longer than the {limit} s the external API contract supports; "
    "the company backend must enforce its own statement timeout."
)


class ValidationNotFoundError(NotFoundError):
    code = "SQL_VALIDATION_NOT_FOUND"
    message = "The SQL security validation was not found."


class ApprovalNotFoundError(NotFoundError):
    code = "SQL_APPROVAL_NOT_FOUND"
    message = "The execution approval was not found."


class ApprovalNotRevocableError(ConflictError):
    code = "SQL_APPROVAL_NOT_REVOCABLE"
    message = "Only an unused, unexpired approval can be revoked."


class GenerationNotFoundError(NotFoundError):
    code = "SQL_GENERATION_NOT_FOUND"
    message = "The SQL generation was not found."


@dataclass
class Evaluation:
    """Everything a validation (or an execution-time re-validation) decided."""

    generation: SQLGenerationRecord
    outcome: InspectionOutcome
    schema: SchemaContext | None = None
    dialect: SQLDialect | None = None
    connector: ExternalAPIConnector | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def schema_version(self) -> int | None:
        return self.schema.schema_version if self.schema else None


def permission_fingerprint(user_id: uuid.UUID, decision: AuthorizationDecision, operation: str) -> str:
    """SHA-256 over the exact permission decision the approval relies on (canonical JSON)."""
    payload = {
        "user_id": str(user_id),
        "revision": decision.policy_revision,
        "action": decision.action,
        "operation": operation,
        "matched": sorted(decision.matched_permissions),
        "tables": sorted(
            (
                {
                    "table": a.table,
                    "columns": sorted(a.columns),
                    "denied": sorted(a.denied_columns),
                    "operations": sorted(a.operations),
                    "row_filter": a.row_filter,
                    "sql_view": a.allow_sql_view,
                }
                for a in decision.table_access
            ),
            key=lambda t: str(t["table"]),
        ),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


class SQLSecurityValidationService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: Settings,
        terminology: TerminologyService,
        engine: AuthorizationEngine,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._terminology = terminology
        self._engine = engine
        self._clock = clock or (lambda: datetime.now(UTC))
        self._options = GuardOptions.from_settings(settings)

    def now(self) -> datetime:
        return self._clock()

    # =========================================================================================
    # API entry points
    # =========================================================================================

    async def validate(self, user: User, generation_id: uuid.UUID, connector_id: uuid.UUID | None = None) -> ValidationView:
        generation = await self._owned_generation(user, generation_id)
        live = await self._live_user(user.id)
        if live is None:
            raise GenerationNotFoundError()
        evaluation = await self.evaluate(live, generation, connector_id)
        record, approval = await self._persist(live, evaluation)
        return await self._view(live, record, approval, auditor=False)

    async def get_validation(self, user: User, validation_id: uuid.UUID, *, auditor: bool) -> ValidationView:
        record = await self._session.get(SQLSecurityValidation, validation_id)
        if record is None or (record.user_id != user.id and not auditor):
            raise ValidationNotFoundError()
        approval = await self._approval_for_validation(record.id)
        if approval is not None:
            approval = await self._expire_if_due(approval)
        return await self._view(user, record, approval, auditor=auditor)

    async def latest_for_generation(self, user: User, generation_id: uuid.UUID) -> ValidationView | None:
        await self._owned_generation(user, generation_id)
        record = (
            await self._session.execute(
                select(SQLSecurityValidation)
                .where(SQLSecurityValidation.generation_id == generation_id, SQLSecurityValidation.user_id == user.id)
                .order_by(SQLSecurityValidation.validated_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if record is None:
            return None
        return await self.get_validation(user, record.id, auditor=False)

    async def get_approval(self, user: User, approval_id: uuid.UUID) -> ApprovalView:
        approval = await self._owned_approval(user, approval_id)
        approval = await self._expire_if_due(approval)
        return self._approval_view(approval)

    async def revoke(self, user: User, approval_id: uuid.UUID) -> ApprovalView:
        approval = await self._owned_approval(user, approval_id)
        approval = await self._expire_if_due(approval)
        now = self.now()
        async with transaction(self._session):
            result = await self._session.execute(
                update(SQLExecutionApproval)
                .where(SQLExecutionApproval.id == approval.id, SQLExecutionApproval.status == "approved")
                .values(status="revoked", revoked_at=now, revoked_by=user.id, end_reason="revoked_by_user")
                .returning(SQLExecutionApproval.id)
                .execution_options(synchronize_session=False)
            )
            if result.scalar_one_or_none() is None:
                raise ApprovalNotRevocableError()
            self._event("approval_revoked", user_id=approval.user_id, actor_user_id=user.id, approval=approval,
                        status="revoked")
        await self._session.refresh(approval)
        return self._approval_view(approval)

    # =========================================================================================
    # evaluation (also used for re-validation right before execution)
    # =========================================================================================

    async def evaluate(
        self, user: User, generation: SQLGenerationRecord, connector_id: uuid.UUID | None
    ) -> Evaluation:
        def failed(*violations: Violation) -> Evaluation:
            outcome = InspectionOutcome(status=status_for(list(violations)), violations=list(violations))
            return Evaluation(generation=generation, outcome=outcome)

        # 1. the persisted generation
        if generation.generation_status == "clarification_required":
            outcome = InspectionOutcome(
                status=ValidationStatus.CLARIFICATION_REQUIRED,
                violations=[violation("clarification_required", Category.GENERATION_NOT_READY,
                                      "The question needs clarification before any SQL can be validated.")],
            )
            return Evaluation(generation=generation, outcome=outcome)
        if generation.generation_status != "generated" or not generation.generated_sql:
            return failed(violation("generation_not_ready", Category.GENERATION_NOT_READY,
                                    "This generation produced no SQL to validate.",
                                    f"generation status {generation.generation_status}"))
        if not generation.sql_sha256 or sql_sha256(generation.generated_sql) != generation.sql_sha256:
            return failed(violation("integrity_failure", Category.INTEGRITY_FAILURE,
                                    "The stored query does not match its recorded fingerprint and cannot be validated.",
                                    "sql_sha256 mismatch on the stored generation"))
        # 2-4. schema source and current version
        if generation.schema_source_id is None:
            return failed(violation("schema_unavailable", Category.SCHEMA_UNAVAILABLE, "The schema of this query is no longer available."))
        try:
            schema = await self._terminology.schema_context(generation.schema_source_id)
        except NotFoundError:
            return failed(violation("schema_unavailable", Category.SCHEMA_UNAVAILABLE, "The schema of this query is no longer available."))
        if not schema.is_active or not schema.tables:
            evaluation = failed(violation("schema_unavailable", Category.SCHEMA_UNAVAILABLE,
                                          "The schema of this query is not active."))
            evaluation.schema = schema
            return evaluation
        if schema.schema_version != generation.schema_version:
            evaluation = failed(violation("schema_changed", Category.SCHEMA_CHANGED,
                                          "The schema changed after this SQL was generated. Generate the SQL again.",
                                          f"generation v{generation.schema_version}, current v{schema.schema_version}"))
            evaluation.schema = schema
            return evaluation
        # dialect
        try:
            dialect = dialect_for_database_type(schema.database_type)
        except UnsupportedDialectError:
            return failed(violation("dialect_unsupported", Category.DIALECT_MISMATCH, "This schema's SQL dialect is not supported."))
        if dialect.key != generation.dialect:
            return failed(violation("dialect_mismatch", Category.DIALECT_MISMATCH,
                                    "The query was generated for a different SQL dialect than the schema uses.",
                                    f"generation {generation.dialect}, schema {dialect.key}"))
        # connector the approval will be bound to
        connector = await self._connector(schema.source_id, connector_id)
        if connector is None:
            evaluation = failed(violation("connector_unavailable", Category.CONNECTOR_UNAVAILABLE,
                                          "No active connector serves this schema, so the query cannot be approved."))
            evaluation.schema, evaluation.dialect = schema, dialect
            return evaluation
        # schema access, then the inspector (permissions for exactly the parsed references)
        permissions = PermissionView(await self._engine.ai_authorization_context(user), schema)
        if not permissions.has_any_data_access:
            outcome = InspectionOutcome(
                status=ValidationStatus.REJECTED,
                violations=[violation("permission_denied", Category.PERMISSION_DENIED,
                                      "You are not permitted to query this schema.", "no data permission on the schema")],
            )
            return Evaluation(generation, outcome, schema, dialect, connector)
        inspector = SQLSecurityInspector(schema, dialect, self._options)
        outcome = await inspector.inspect(generation.generated_sql, self._authorizer(user, permissions))
        warnings = list(outcome.warnings)
        limit = self._options.limits.max_execution_seconds
        if outcome.approved and connector.timeout_seconds > limit:
            warnings.append(EXECUTION_TIMEOUT_WARNING.format(timeout=connector.timeout_seconds, limit=limit))
        return Evaluation(generation, outcome, schema, dialect, connector, warnings)

    def _authorizer(self, user: User, permissions: PermissionView):  # type: ignore[no-untyped-def]
        async def authorize(referenced: dict[str, set[str]], operation: str) -> AccessDecision:
            revision = permissions.policy_revision
            for table in referenced:
                if not permissions.table_allowed(table):
                    return AccessDecision(False, revision, internal_reason=f"no permission covers table {table}")
            decision = await authorize_references(self._engine, user.id, permissions, referenced, operation)
            if decision is None:
                denied = sorted(f"{t}.{c}" for t, cols in referenced.items() for c in cols
                                if not permissions.column_allowed(t, c))
                reason = f"denied columns {denied}" if denied else f"operation {operation} or column combination not permitted"
                return AccessDecision(False, revision, internal_reason=reason)
            filters: dict[str, dict[str, Any]] = {}
            for table in referenced:
                policy_table = permissions.policy_table(table, decision.action)
                access = next((a for a in decision.table_access if a.table == policy_table), None)
                if access is None:
                    return AccessDecision(False, decision.policy_revision, internal_reason=f"no decision for {table}")
                if access.row_filter is not None:
                    filters[table.lower()] = access.row_filter
            visible = await authorize_references(self._engine, user.id, permissions, referenced, operation, sql_view=True)
            return AccessDecision(
                allowed=True,
                revision=decision.policy_revision,
                fingerprint=permission_fingerprint(user.id, decision, operation),
                row_filters=filters,
                sql_visible=visible is not None,
            )

        return authorize

    # =========================================================================================
    # persistence
    # =========================================================================================

    async def _persist(
        self, user: User, evaluation: Evaluation
    ) -> tuple[SQLSecurityValidation, SQLExecutionApproval | None]:
        outcome = evaluation.outcome
        generation = evaluation.generation
        access = outcome.access
        now = self.now()
        record = SQLSecurityValidation(
            id=uuid.uuid4(),
            generation_id=generation.id,
            user_id=user.id,
            schema_source_id=evaluation.schema.source_id if evaluation.schema else generation.schema_source_id,
            connector_id=evaluation.connector.id if evaluation.connector else None,
            status=outcome.status.value,
            approved=outcome.approved,
            rejection_category=None if outcome.approved else (outcome.category or Category.INTEGRITY_FAILURE).value,
            dialect=evaluation.dialect.key if evaluation.dialect else None,
            schema_version=evaluation.schema_version,
            permission_version=access.revision if access else None,
            permission_fingerprint=access.fingerprint if access and outcome.approved else None,
            generated_sql_sha256=generation.sql_sha256,
            query_hash=outcome.query_hash if outcome.approved else None,
            user_errors=[v.user_view() for v in outcome.violations],
            internal_reasons=[v.internal_view() for v in outcome.violations],
            warnings=evaluation.warnings if outcome.approved else [],
            violation_codes=sorted({v.code for v in outcome.violations}),
            referenced_tables=outcome.referenced_tables if outcome.approved else [],
            referenced_columns=outcome.referenced_columns if outcome.approved else [],
            detected_functions=outcome.functions,
            applied_row_restrictions=outcome.applied_row_restrictions if outcome.approved else [],
            sql_visible=bool(access and access.sql_visible and outcome.approved),
            validated_at=now,
        )
        approval: SQLExecutionApproval | None = None
        async with transaction(self._session):
            self._session.add(record)
            await self._session.flush()
            self._event("validation_completed", user_id=user.id, validation=record, status=record.status,
                        rejection_category=record.rejection_category, violation_codes=record.violation_codes,
                        details={"functions": len(record.detected_functions),
                                 "row_restrictions": len(record.applied_row_restrictions)})
            if outcome.approved:
                assert evaluation.connector and evaluation.schema and evaluation.dialect and access
                assert outcome.exact_sql and outcome.query_hash and access.fingerprint
                superseded = await self._session.execute(
                    update(SQLExecutionApproval)
                    .where(SQLExecutionApproval.generation_id == generation.id,
                           SQLExecutionApproval.user_id == user.id, SQLExecutionApproval.status == "approved")
                    .values(status="invalidated", end_reason="superseded")
                    .returning(SQLExecutionApproval.id)
                    .execution_options(synchronize_session=False)
                )
                for (old_id,) in superseded.all():
                    self._event("approval_superseded", user_id=user.id, approval_id=old_id, status="invalidated")
                approval = SQLExecutionApproval(
                    id=uuid.uuid4(),
                    validation_id=record.id,
                    generation_id=generation.id,
                    user_id=user.id,
                    connector_id=evaluation.connector.id,
                    schema_source_id=evaluation.schema.source_id,
                    schema_version=evaluation.schema.schema_version,
                    permission_version=access.revision,
                    permission_fingerprint=access.fingerprint,
                    dialect=evaluation.dialect.key,
                    exact_sql=outcome.exact_sql,
                    exact_sql_hash=outcome.query_hash,
                    status="approved",
                    approved_at=now,
                    expires_at=now + timedelta(seconds=self._settings.ai_sql_approval_ttl_seconds),
                )
                self._session.add(approval)
                await self._session.flush()
                self._event("approval_created", user_id=user.id, approval=approval, status="approved",
                            details={"ttl_seconds": self._settings.ai_sql_approval_ttl_seconds})
        logger.info(
            "SQL security validation",
            extra={
                "event": "ai.sql_security.validated",
                "validation_id": str(record.id),
                "generation_id": str(generation.id),
                "status": record.status,
                "rejection_category": record.rejection_category,
                "violation_codes": record.violation_codes,
                "query_hash": record.query_hash,
                "approval_id": str(approval.id) if approval else None,
            },
        )
        return record, approval

    def _event(
        self,
        event_type: str,
        *,
        user_id: uuid.UUID | None,
        actor_user_id: uuid.UUID | None = None,
        validation: SQLSecurityValidation | None = None,
        approval: SQLExecutionApproval | None = None,
        approval_id: uuid.UUID | None = None,
        status: str | None = None,
        rejection_category: str | None = None,
        violation_codes: list[str] | None = None,
        details: dict[str, Any] | None = None,
    ) -> SQLSecurityEvent:
        """Add one audit row to the current transaction (ids, versions, hashes; never SQL)."""
        source = approval or validation
        event = SQLSecurityEvent(
            event_type=event_type,
            request_id=get_request_id(),
            validation_id=(approval.validation_id if approval else validation.id if validation else None),
            approval_id=approval.id if approval else approval_id,
            generation_id=source.generation_id if source else None,
            user_id=user_id,
            actor_user_id=actor_user_id or user_id,
            connector_id=source.connector_id if source else None,
            schema_source_id=source.schema_source_id if source else None,
            schema_version=source.schema_version if source else None,
            permission_version=source.permission_version if source else None,
            permission_fingerprint=source.permission_fingerprint if source else None,
            query_hash=(approval.exact_sql_hash if approval else validation.query_hash if validation else None),
            status=status,
            rejection_category=rejection_category,
            violation_codes=violation_codes or [],
            details={k: v for k, v in (details or {}).items() if isinstance(v, (str, int, float, bool)) or v is None},
        )
        self._session.add(event)
        return event

    async def record_event(self, event_type: str, **kwargs: Any) -> None:
        """Store one audit event in its own transaction (used by the execution guard)."""
        async with transaction(self._session):
            self._event(event_type, **kwargs)

    # =========================================================================================
    # lookups
    # =========================================================================================

    async def _live_user(self, user_id: uuid.UUID) -> User | None:
        user = await self._session.get(User, user_id, populate_existing=True)
        return user if user is not None and is_live(user) else None

    async def _owned_generation(self, user: User, generation_id: uuid.UUID) -> SQLGenerationRecord:
        record = await self._session.get(SQLGenerationRecord, generation_id)
        if record is None or record.user_id != user.id:
            raise GenerationNotFoundError()
        return record

    async def _owned_approval(self, user: User, approval_id: uuid.UUID) -> SQLExecutionApproval:
        approval = await self._session.get(SQLExecutionApproval, approval_id, populate_existing=True)
        if approval is None or approval.user_id != user.id:
            raise ApprovalNotFoundError()
        return approval

    async def _approval_for_validation(self, validation_id: uuid.UUID) -> SQLExecutionApproval | None:
        return (
            await self._session.execute(
                select(SQLExecutionApproval).where(SQLExecutionApproval.validation_id == validation_id)
            )
        ).scalar_one_or_none()

    async def _connector(self, schema_source_id: uuid.UUID, connector_id: uuid.UUID | None) -> ExternalAPIConnector | None:
        statement = select(ExternalAPIConnector).where(
            ExternalAPIConnector.schema_source_id == schema_source_id,
            ExternalAPIConnector.is_active.is_(True),
            ExternalAPIConnector.deleted_at.is_(None),
        )
        if connector_id is not None:
            statement = statement.where(ExternalAPIConnector.id == connector_id)
        return (await self._session.execute(statement.limit(1))).scalar_one_or_none()

    async def _expire_if_due(self, approval: SQLExecutionApproval) -> SQLExecutionApproval:
        if approval.status != "approved" or _aware(approval.expires_at) > self.now():
            return approval
        async with transaction(self._session):
            result = await self._session.execute(
                update(SQLExecutionApproval)
                .where(SQLExecutionApproval.id == approval.id, SQLExecutionApproval.status == "approved")
                .values(status="expired", end_reason="expired")
                .returning(SQLExecutionApproval.id)
                .execution_options(synchronize_session=False)
            )
            if result.scalar_one_or_none() is not None:
                self._event("approval_expired", user_id=approval.user_id, approval=approval, status="expired")
        await self._session.refresh(approval)
        return approval

    # =========================================================================================
    # views
    # =========================================================================================

    def _approval_view(self, approval: SQLExecutionApproval) -> ApprovalView:
        now = self.now()
        expires = _aware(approval.expires_at)
        usable = approval.status == "approved" and expires > now
        return ApprovalView(
            approval_id=approval.id,
            validation_id=approval.validation_id,
            generation_id=approval.generation_id,
            connector_id=approval.connector_id,
            schema_source_id=approval.schema_source_id,
            schema_version=approval.schema_version,
            permission_version=approval.permission_version,
            exact_sql_hash=approval.exact_sql_hash,
            dialect=approval.dialect,
            status=ApprovalStatus(approval.status),
            approved_at=_aware(approval.approved_at),
            expires_at=expires,
            consumed_at=_aware(approval.consumed_at) if approval.consumed_at else None,
            revoked_at=_aware(approval.revoked_at) if approval.revoked_at else None,
            expires_in_seconds=max(0, int((expires - now).total_seconds())) if usable else 0,
            is_usable=usable,
        )

    async def _view(
        self, user: User, record: SQLSecurityValidation, approval: SQLExecutionApproval | None, *, auditor: bool
    ) -> ValidationView:
        status = ValidationStatus(record.status)
        if status == ValidationStatus.APPROVED and approval is not None and approval.status == "expired":
            status = ValidationStatus.APPROVAL_EXPIRED
        sql_visible = (
            record.sql_visible
            and approval is not None
            and record.user_id == user.id
            and await self._sql_visible_now(user, record)
        )
        return ValidationView(
            validation_id=record.id,
            generation_id=record.generation_id,
            approved=record.approved,
            status=status,
            validation_errors=[ValidationMessage(**e) for e in record.user_errors],
            warnings=list(record.warnings),
            referenced_tables=list(record.referenced_tables),
            referenced_columns=list(record.referenced_columns),
            detected_functions=list(record.detected_functions),
            applied_row_restrictions=list(record.applied_row_restrictions),
            schema_version=record.schema_version,
            permission_version=record.permission_version,
            query_hash=record.query_hash,
            validated_at=_aware(record.validated_at),
            rejection_category=record.rejection_category,
            summary=SUMMARIES[status],
            dialect=record.dialect,
            connector_id=record.connector_id,
            approved_sql=approval.exact_sql if sql_visible and approval is not None else None,
            sql_visible=sql_visible,
            approval=self._approval_view(approval) if approval is not None else None,
            execution_available=False,
            internal_reasons=[InternalReason(**r) for r in record.internal_reasons] if auditor else None,
        )

    async def _sql_visible_now(self, user: User, record: SQLSecurityValidation) -> bool:
        """SQL text follows the user's *current* SQL visibility permission."""
        if record.schema_source_id is None:
            return False
        live = await self._live_user(user.id)
        if live is None:
            return False
        try:
            schema = await self._terminology.schema_context(record.schema_source_id)
        except NotFoundError:
            return False
        permissions = PermissionView(await self._engine.ai_authorization_context(live), schema)
        referenced: dict[str, set[str]] = {t: set() for t in record.referenced_tables}
        for column in record.referenced_columns:
            table, name = column.rsplit(".", 1)
            referenced.setdefault(table, set()).add(name)
        if not referenced or not all(permissions.table_allowed(t) for t in referenced):
            return False
        for operation in ("aggregate", "read"):
            if await authorize_references(self._engine, live.id, permissions, referenced, operation, sql_view=True):
                return True
        return False

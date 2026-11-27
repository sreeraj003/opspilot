"""``SQLExecutionGuard``: the only component that turns an approval into a trusted request.

This is an **internal service boundary**: no API route calls it in Module 11
(the question-to-answer workflow that will is Module 12), and nothing accepts
SQL from a client. ``authorize_execution`` runs in one database transaction:

1. Lock the approval row (``SELECT ... FOR UPDATE``).
2. The approval must belong to this user, be for this connector, be
   ``approved``, unconsumed and unexpired. If the caller states the SQL it
   expects to run, its SHA-256 must equal the approved hash.
3. Re-validate immediately: the stored generation is run through the full
   guard again with the *current* schema, connector and permissions. The
   re-validation must approve the same exact SQL (same hash), for the same
   schema version, connector and permission fingerprint. Any difference
   invalidates the approval (it can never be used again).
4. Consume the approval with a conditional ``UPDATE`` (single use: a
   concurrent second attempt matches nothing) and record the execution
   reference.
5. Seal a ``ValidatedQueryRequest`` (``ExecutionApprovalAuthority.issue_guarded_query``)
   carrying the approval, generation, exact SQL, its hash, the user,
   connector, schema source and version, the permission fingerprint, the
   validation reference and the expiry.

``ConnectorService.execute`` then verifies the seal and SQL hash and claims
the approval (consumed -> executed) for exactly this request before the HTTP
request, so an approved query reaches the connector at most once.
"""

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update

from app.ai.sql_security.approvals.ledger import consume
from app.ai.sql_security.services.inspector import sql_sha256
from app.ai.sql_security.services.validation_service import SQLSecurityValidationService
from app.connectors.adapters.response import NormalizedResult
from app.connectors.errors import TrustedRequestRejectedError
from app.connectors.trusted import ExecutionApprovalAuthority, ValidatedQueryRequest
from app.core.exceptions import ConflictError
from app.database.transaction import transaction
from app.models.sql_generation import SQLGenerationRecord
from app.models.sql_security import SQLExecutionApproval
from app.models.user import User
from app.services.connector_service import ConnectorService

logger = logging.getLogger("app.ai.sql_security")


class ExecutionNotAuthorizedError(ConflictError):
    """Safe message; the detailed reason is in the security audit log."""

    code = "SQL_EXECUTION_NOT_AUTHORIZED"
    message = "This query is not approved for execution. Validate it again."

    def __init__(self, reason: str) -> None:
        super().__init__()
        self.reason = reason


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


class SQLExecutionGuard:
    def __init__(self, validation: SQLSecurityValidationService, authority: ExecutionApprovalAuthority) -> None:
        self._validation = validation
        self._session = validation._session
        self._authority = authority

    async def authorize_execution(
        self,
        user: User,
        approval_id: uuid.UUID,
        *,
        connector_id: uuid.UUID,
        exact_sql: str | None = None,
    ) -> ValidatedQueryRequest:
        session = self._session
        reason: str | None = None
        invalidate = False
        request: ValidatedQueryRequest | None = None
        approval: SQLExecutionApproval | None = None
        async with transaction(session):
            approval = (
                await session.execute(
                    select(SQLExecutionApproval)
                    .where(SQLExecutionApproval.id == approval_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).scalar_one_or_none()
            now = self._validation.now()
            if approval is None:
                reason = "unknown_approval"
            elif approval.user_id != user.id:
                reason = "different_user"
            elif approval.connector_id != connector_id:
                reason = "different_connector"
            elif approval.status != "approved" or approval.consumed_at is not None:
                reason = f"approval_{approval.status}"
            elif _aware(approval.expires_at) <= now:
                reason = "approval_expired"
            elif exact_sql is not None and sql_sha256(exact_sql) != approval.exact_sql_hash:
                reason = "sql_modified"
            if reason is None and approval is not None:
                reason = await self._revalidate(user, approval)
                invalidate = reason is not None
            if reason is None and approval is not None:
                reference = uuid.uuid4()
                if not await consume(session, approval.id, execution_reference=reference, now=now):
                    reason = "replay"
                else:
                    await session.refresh(approval)
                    request = self._authority.issue_guarded_query(approval)
                    self._validation._event("execution_authorized", user_id=user.id, approval=approval,
                                            status="consumed", details={"execution_reference": str(reference)})
        if reason is not None:
            await self._reject(user, approval, reason, invalidate=invalidate)
            raise ExecutionNotAuthorizedError(reason)
        assert request is not None
        return request

    async def execute(
        self, user: User, approval_id: uuid.UUID, connectors: ConnectorService, *, connector_id: uuid.UUID,
        exact_sql: str | None = None,
    ) -> NormalizedResult:
        """Authorize, then run through Module 07's connector boundary (which claims the approval once)."""
        request = await self.authorize_execution(user, approval_id, connector_id=connector_id, exact_sql=exact_sql)
        try:
            return await connectors.execute(request)
        except TrustedRequestRejectedError:
            await self._validation.record_event("execution_rejected", user_id=user.id, approval_id=approval_id,
                                                status="rejected", violation_codes=["connector_boundary_rejected"])
            raise

    async def _revalidate(self, user: User, approval: SQLExecutionApproval) -> str | None:
        generation = await self._session.get(SQLGenerationRecord, approval.generation_id, populate_existing=True)
        live = await self._validation._live_user(user.id)
        if generation is None or live is None:
            return "revalidation_unavailable"
        evaluation = await self._validation.evaluate(live, generation, approval.connector_id)
        outcome = evaluation.outcome
        if not outcome.approved:
            return f"revalidation_{outcome.status.value}:{outcome.category.value if outcome.category else 'unknown'}"
        if evaluation.schema_version != approval.schema_version:
            return "schema_version_changed"
        if evaluation.connector is None or evaluation.connector.id != approval.connector_id:
            return "connector_changed"
        if outcome.access is None or outcome.access.fingerprint != approval.permission_fingerprint:
            return "permissions_changed"
        if outcome.query_hash != approval.exact_sql_hash or outcome.exact_sql != approval.exact_sql:
            return "sql_changed"
        return None

    async def _reject(self, user: User, approval: SQLExecutionApproval | None, reason: str, *, invalidate: bool) -> None:
        async with transaction(self._session):
            if invalidate and approval is not None:
                await self._session.execute(
                    update(SQLExecutionApproval)
                    .where(SQLExecutionApproval.id == approval.id, SQLExecutionApproval.status == "approved")
                    .values(status="invalidated", end_reason=reason[:64])
                    .execution_options(synchronize_session=False)
                )
                self._validation._event("approval_invalidated", user_id=approval.user_id, approval=approval,
                                        status="invalidated", violation_codes=[reason[:64]])
            self._validation._event(
                "execution_rejected", user_id=user.id, approval=approval if approval and approval.user_id == user.id else None,
                approval_id=approval.id if approval else None, status="rejected", violation_codes=[reason[:64]],
            )
        logger.warning(
            "SQL execution refused by the security guard",
            extra={"event": "ai.sql_security.execution_rejected", "reason": reason,
                   "approval_id": str(approval.id) if approval else None},
        )

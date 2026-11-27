"""Single-use approval state transitions, each one a conditional ``UPDATE``.

Every transition names the state it expects in its ``WHERE`` clause, so two
concurrent callers can never both succeed: PostgreSQL row locking makes the
second ``UPDATE`` see the first one's result and match nothing. The trigger
from migration 0009 additionally refuses any change to the bound fields and
any transition not listed here.

* ``consume``: approved -> consumed (by ``SQLExecutionGuard``, after
  re-validation), recording the execution reference of the trusted request.
* ``claim_execution``: consumed -> executed (by the connector service at its
  boundary, right before the HTTP request), only for the exact request that
  consumed it: same execution reference, SQL hash, user and connector.
"""

import uuid
from datetime import datetime

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.trusted import ValidatedQueryRequest
from app.models.sql_security import SQLExecutionApproval, SQLSecurityEvent


async def consume(
    session: AsyncSession, approval_id: uuid.UUID, *, execution_reference: uuid.UUID, now: datetime
) -> bool:
    result = await session.execute(
        update(SQLExecutionApproval)
        .where(
            SQLExecutionApproval.id == approval_id,
            SQLExecutionApproval.status == "approved",
            SQLExecutionApproval.consumed_at.is_(None),
            SQLExecutionApproval.expires_at > now,
        )
        .values(status="consumed", consumed_at=now, execution_reference=execution_reference)
        .returning(SQLExecutionApproval.id)
        .execution_options(synchronize_session=False)
    )
    return result.scalar_one_or_none() is not None


async def claim_execution(session: AsyncSession, request: ValidatedQueryRequest, *, now: datetime) -> bool:
    result = await session.execute(
        update(SQLExecutionApproval)
        .where(
            SQLExecutionApproval.id == request.approval_id,
            SQLExecutionApproval.status == "consumed",
            SQLExecutionApproval.executed_at.is_(None),
            SQLExecutionApproval.execution_reference == request.execution_reference,
            SQLExecutionApproval.exact_sql_hash == request.exact_sql_hash,
            SQLExecutionApproval.user_id == request.requesting_user_id,
            SQLExecutionApproval.connector_id == request.connector_id,
            SQLExecutionApproval.generation_id == request.generation_id,
        )
        .values(status="executed", executed_at=now)
        .returning(SQLExecutionApproval.id)
        .execution_options(synchronize_session=False)
    )
    claimed = result.scalar_one_or_none() is not None
    # Audited in the same transaction as the claim: no SQL text, only references.
    session.add(SQLSecurityEvent(
        event_type="execution_claimed" if claimed else "execution_rejected",
        approval_id=request.approval_id, generation_id=request.generation_id, user_id=request.requesting_user_id,
        connector_id=request.connector_id, schema_source_id=request.schema_source_id,
        schema_version=request.schema_version, query_hash=request.exact_sql_hash,
        status="executed" if claimed else "rejected",
        violation_codes=[] if claimed else ["claim_failed"],
        details={"execution_reference": str(request.execution_reference)},
    ))
    return claimed

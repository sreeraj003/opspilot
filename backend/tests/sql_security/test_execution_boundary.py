"""Module 11 connector boundary tests: only guard-approved SQL reaches the (mocked) company API, exactly once."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.ai.sql_security.services.execution_service import ExecutionNotAuthorizedError
from app.ai.sql_security.services.inspector import sql_sha256
from app.connectors.errors import TrustedRequestRejectedError
from app.connectors.trusted import SQL_GUARD_ISSUER, ValidatedQueryRequest
from app.models.user import User
from tests.sql_security.conftest import GOOD_SQL, VIEW_SQL_POLICY, Guard

pytestmark = pytest.mark.postgres


def _approval(guard: Guard, sql: str = GOOD_SQL) -> tuple[uuid.UUID, str, dict[str, Any]]:
    user_id, headers = guard.member()
    body = guard.validated(guard.stored_generation(user_id, sql), headers)
    assert body["status"] == "approved", body
    return user_id, body["approval"]["approval_id"], body


def test_approved_sql_reaches_the_connector_exactly_once(guard: Guard) -> None:
    user_id, approval_id, body = _approval(guard)
    result = guard.execute(user_id, approval_id, exact_sql=body["approved_sql"])
    assert result is not None
    requests = guard.sql_requests()
    assert len(requests) == 1
    assert requests[0].url.params["sql"] == body["approved_sql"]  # the exact approved SQL, row security included
    row = guard.approvals()[0]
    assert row.status == "executed" and row.consumed_at is not None and row.executed_at is not None
    assert guard.security_events("execution_authorized") and guard.security_events("execution_claimed")


def test_replay_is_refused(guard: Guard) -> None:
    user_id, approval_id, _ = _approval(guard)
    guard.execute(user_id, approval_id)
    with pytest.raises(ExecutionNotAuthorizedError) as error:
        guard.execute(user_id, approval_id)
    assert error.value.reason == "approval_executed"
    assert len(guard.sql_requests()) == 1


def test_modified_sql_is_refused(guard: Guard) -> None:
    user_id, approval_id, body = _approval(guard)
    with pytest.raises(ExecutionNotAuthorizedError) as error:
        guard.execute(user_id, approval_id, exact_sql=body["approved_sql"] + " ")
    assert error.value.reason == "sql_modified"
    with pytest.raises(ExecutionNotAuthorizedError):
        guard.execute(user_id, approval_id, exact_sql=GOOD_SQL)  # the unrestricted original, without row security
    assert guard.sql_requests() == []
    assert guard.approvals()[0].status == "approved"  # refused attempts by the owner do not burn the approval


def test_different_user_or_connector_is_refused(guard: Guard) -> None:
    user_id, approval_id, _ = _approval(guard)
    other, _ = guard.member()
    with pytest.raises(ExecutionNotAuthorizedError) as error:
        guard.execute(other, approval_id)
    assert error.value.reason == "different_user"
    with pytest.raises(ExecutionNotAuthorizedError) as error:
        guard.execute(user_id, approval_id, connector_id=str(uuid.uuid4()))
    assert error.value.reason == "different_connector"
    with pytest.raises(ExecutionNotAuthorizedError) as error:
        guard.execute(user_id, str(uuid.uuid4()))
    assert error.value.reason == "unknown_approval"
    assert guard.sql_requests() == []
    assert guard.security_events("execution_rejected")


def test_schema_change_after_approval_invalidates_it(guard: Guard) -> None:
    user_id, approval_id, _ = _approval(guard)
    guard.api.sql("UPDATE schema_sources SET schema_version = schema_version + 1 WHERE id = :s", s=guard.schema_id)
    with pytest.raises(ExecutionNotAuthorizedError) as error:
        guard.execute(user_id, approval_id)
    assert error.value.reason.startswith("revalidation_validation_failed")
    assert guard.approvals()[0].status == "invalidated"
    assert guard.sql_requests() == []


def test_deactivated_connector_invalidates_the_approval(guard: Guard) -> None:
    user_id, approval_id, _ = _approval(guard)
    guard.ok(guard.patch(f"/connectors/{guard.connector_id}/status", {"is_active": False}))
    with pytest.raises(ExecutionNotAuthorizedError):
        guard.execute(user_id, approval_id)
    assert guard.approvals()[0].status == "invalidated"
    assert guard.sql_requests() == []


def test_expired_approval_never_reaches_httpx(guard: Guard) -> None:
    user_id, approval_id, _ = _approval(guard)
    guard.clock.advance(400)
    with pytest.raises(ExecutionNotAuthorizedError) as error:
        guard.execute(user_id, approval_id)
    assert error.value.reason == "approval_expired"
    assert guard.sql_requests() == []


def test_concurrent_double_consumption_has_one_winner(guard: Guard) -> None:
    user_id, approval_id, _ = _approval(guard)

    async def attempt() -> str:
        async with guard.client.app.state.database.session() as session:  # type: ignore[attr-defined]
            _, execution, _ = guard._services(session)
            user = await session.get(User, user_id)
            assert user is not None
            try:
                await execution.authorize_execution(user, uuid.UUID(approval_id), connector_id=uuid.UUID(guard.connector_id))
                return "ok"
            except ExecutionNotAuthorizedError as exc:
                return exc.reason

    async def race() -> list[str]:
        return list(await asyncio.gather(attempt(), attempt(), attempt()))

    results = guard.api.run(race)
    assert results.count("ok") == 1, results
    assert guard.approvals()[0].status == "consumed"


def test_tampered_trusted_request_is_rejected(guard: Guard) -> None:
    user_id, approval_id, _ = _approval(guard)
    request = guard.authorize_execution(user_id, approval_id)
    hostile = "SELECT c.name, c.region FROM sales.customers AS c LIMIT 1000"
    tampered = request.model_copy(update={"exact_sql": hostile, "exact_sql_hash": sql_sha256(hostile)})
    with pytest.raises(TrustedRequestRejectedError):
        guard.connector_execute(tampered)
    assert guard.sql_requests() == []
    guard.connector_execute(request)  # the untouched request still works once
    with pytest.raises(TrustedRequestRejectedError):
        guard.connector_execute(request)  # and only once
    assert len(guard.sql_requests()) == 1


def test_forged_request_without_approval_is_rejected(guard: Guard) -> None:
    user_id, _ = guard.member()
    now = datetime.now(UTC)
    forged = ValidatedQueryRequest(
        connector_id=uuid.UUID(guard.connector_id), exact_sql=GOOD_SQL, requesting_user_id=user_id,
        schema_source_id=uuid.UUID(guard.schema_id), schema_version=guard.schema_version(),
        validation_reference="validation-forged", permission_context_reference="a" * 64, approved_at=now,
        issuer=SQL_GUARD_ISSUER, execution_reference=uuid.uuid4(), approval_id=uuid.uuid4(), generation_id=uuid.uuid4(),
        exact_sql_hash=sql_sha256(GOOD_SQL), expires_at=now + timedelta(minutes=5), seal="0" * 64,
    )
    with pytest.raises(TrustedRequestRejectedError):
        guard.connector_execute(forged)
    assert guard.sql_requests() == []


def test_raw_sql_cannot_be_sent_to_the_connector(guard: Guard) -> None:
    with pytest.raises((TrustedRequestRejectedError, TypeError, ValueError, AttributeError)):
        guard.connector_execute(GOOD_SQL)
    assert guard.sql_requests() == []


def test_no_public_endpoint_accepts_sql_for_execution(guard: Guard) -> None:
    paths = guard.ok(guard.client.get("/openapi.json"))["paths"]
    sql_paths = [p for p in paths if "/ai/sql" in p]
    assert sql_paths and not any("execute" in p or "/run" in p for p in sql_paths)
    for path, methods in paths.items():
        for operation in methods.values():
            body = str(operation.get("requestBody", ""))
            assert not ("execute" in path and "sql" in body.lower())


def test_rejected_sql_never_reaches_httpx(guard: Guard) -> None:
    user_id, headers = guard.member()
    body = guard.validated(guard.stored_generation(user_id, "SELECT * FROM sales.orders; DROP TABLE sales.orders"), headers)
    assert body["approval"] is None
    assert guard.approvals() == []
    assert guard.sql_requests() == []


def test_end_to_end_through_module_10(guard: Guard) -> None:
    headers = guard.generator(policy=VIEW_SQL_POLICY, extra=("ai.sql.validate",))
    intent_id = guard.intent(headers)
    guard.model_sql(GOOD_SQL)
    generation = guard.ok(guard.generate(intent_id, headers))
    assert generation["status"] == "generated", generation
    body = guard.validated(str(generation["generation_id"]), headers)
    assert body["status"] == "approved", body
    user_id = guard.records()[-1].user_id
    guard.execute(user_id, body["approval"]["approval_id"], exact_sql=body["approved_sql"])
    assert len(guard.sql_requests()) == 1

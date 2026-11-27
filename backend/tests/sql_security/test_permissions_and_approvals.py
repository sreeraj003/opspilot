"""Module 11 API tests: permissions (Module 04), approvals and the validation API.

PostgreSQL-backed; the company API is mocked and no SQL is ever executed here
except through the internal guard boundary.
"""

from typing import Any

import pytest

from app.ai.sql_security.services.execution_service import ExecutionNotAuthorizedError
from app.ai.sql_security.services.inspector import sql_sha256
from tests.sql_security.conftest import GOOD_SQL, SALES_POLICY, SOUTH_POLICY, Guard

pytestmark = pytest.mark.postgres


def _approved(body: dict[str, Any]) -> dict[str, Any]:
    assert body["status"] == "approved", body
    assert body["approved"] is True and body["approval"] is not None
    return body


# --- validation API ----------------------------------------------------------------------------------


def test_validate_approves_good_sql_and_returns_the_structured_result(guard: Guard) -> None:
    body = _approved(guard.check(GOOD_SQL))
    for field in ("validation_id", "generation_id", "validation_errors", "warnings", "referenced_tables", "referenced_columns",
                  "detected_functions", "applied_row_restrictions", "schema_version", "permission_version", "query_hash",
                  "validated_at"):
        assert field in body
    assert body["referenced_tables"] == ["sales.customers", "sales.orders"]
    assert "sum" in [f.lower() for f in body["detected_functions"]]
    assert body["schema_version"] == guard.schema_version()
    assert body["execution_available"] is False
    approval = body["approval"]
    assert approval["status"] == "approved" and approval["is_usable"] is True
    assert 0 < approval["expires_in_seconds"] <= 300
    # The approved SQL carries the row-level restriction of the user's permission.
    assert "Kerala" in body["approved_sql"]
    assert approval["exact_sql_hash"] == sql_sha256(body["approved_sql"]) == body["query_hash"]
    row = guard.approvals()[0]
    assert row.exact_sql == body["approved_sql"] and row.status == "approved"


def test_rejected_sql_gets_no_approval_and_safe_errors(guard: Guard) -> None:
    body = guard.check("DELETE FROM sales.orders")
    assert body["status"] == "rejected" and body["approved"] is False and body["approval"] is None
    assert body["validation_errors"] and body["internal_reasons"] is None
    assert guard.approvals() == []
    assert guard.security_events("validation_completed")


def test_inaccessible_names_are_never_exposed(guard: Guard) -> None:
    body = guard.check("SELECT p.secret_margin FROM sales.payroll_internal AS p LIMIT 5")
    assert body["status"] == "rejected"
    text = str(body["validation_errors"]) + body["summary"]
    assert "payroll_internal" not in text and "secret_margin" not in text


def test_sql_is_hidden_without_sql_view(guard: Guard) -> None:
    body = _approved(guard.check(GOOD_SQL, policy=SALES_POLICY))
    assert body["sql_visible"] is False and body["approved_sql"] is None


def test_validate_requires_the_validate_permission(guard: Guard) -> None:
    user_id, headers = guard.member("ai.sql.generate")
    assert guard.validate(guard.stored_generation(user_id, GOOD_SQL), headers).status_code == 403


def test_another_users_generation_cannot_be_validated(guard: Guard) -> None:
    owner, _ = guard.member()
    _, intruder = guard.member()
    assert guard.validate(guard.stored_generation(owner, GOOD_SQL), intruder).status_code == 404


def test_clarification_and_failed_generations_are_not_approved(guard: Guard) -> None:
    user_id, headers = guard.member()
    clarification = guard.validated(guard.stored_generation(user_id, None, status="clarification_required"), headers)
    assert clarification["status"] == "clarification_required" and clarification["approval"] is None
    failed = guard.validated(guard.stored_generation(user_id, None, status="generation_failed"), headers)
    assert failed["status"] in {"rejected", "validation_failed"} and failed["approval"] is None


def test_tampered_stored_sql_fails_integrity(guard: Guard) -> None:
    user_id, headers = guard.member()
    body = guard.validated(guard.stored_generation(user_id, GOOD_SQL, sha="0" * 64), headers)
    assert body["status"] == "validation_failed" and body["approval"] is None


def test_stale_schema_version_is_not_approved(guard: Guard) -> None:
    user_id, headers = guard.member()
    generation = guard.stored_generation(user_id, GOOD_SQL)
    guard.api.sql("UPDATE schema_sources SET schema_version = schema_version + 1 WHERE id = :s", s=guard.schema_id)
    body = guard.validated(generation, headers)
    assert body["status"] == "validation_failed" and body["approval"] is None


def test_get_validation_and_latest_validation(guard: Guard) -> None:
    user_id, headers = guard.member()
    generation = guard.stored_generation(user_id, GOOD_SQL)
    body = guard.validated(generation, headers)
    again = guard.ok(guard.get(f"/ai/sql/validations/{body['validation_id']}", headers=headers))
    assert again["validation_id"] == body["validation_id"] and again["internal_reasons"] is None
    latest = guard.ok(guard.get(f"/ai/sql/generations/{generation}/validation", headers=headers))
    assert latest["validation_id"] == body["validation_id"]
    _, other = guard.member()
    assert guard.get(f"/ai/sql/validations/{body['validation_id']}", headers=other).status_code == 404


def test_auditors_see_internal_reasons_others_do_not(guard: Guard) -> None:
    user_id, headers = guard.member()
    body = guard.validated(guard.stored_generation(user_id, "SELECT o.id FROM sales.orders AS o; DROP TABLE x"), headers)
    assert body["internal_reasons"] is None
    audited = guard.ok(guard.get(f"/ai/sql/validations/{body['validation_id']}"))  # administrator (audit permission)
    assert audited["internal_reasons"]


def test_validation_is_rate_limited(guard_factory: Any) -> None:
    guard = guard_factory(ai_sql_validation_rate_limit_per_user=2)
    user_id, headers = guard.member()
    generation = guard.stored_generation(user_id, GOOD_SQL)
    statuses = [guard.validate(generation, headers).status_code for _ in range(3)]
    assert statuses[:2] == [200, 200] and statuses[2] == 429


def test_audit_never_stores_sql_text(guard: Guard) -> None:
    guard.check(GOOD_SQL)
    for event in guard.security_events():
        text = str(dict(event._mapping))
        assert "Kerala" not in text and "SUM(" not in text.upper() and "sales.orders AS" not in text


# --- permissions (Module 04) --------------------------------------------------------------------------


def test_explicit_deny_wins(guard: Guard) -> None:
    user_id, headers = guard.member()
    deny = guard.create_permission(f"sales.blocked.{guard._users}", {"effect": "deny", "deny_all": True})
    guard.ok(guard.grant_direct(user_id, deny), 201)
    body = guard.validated(guard.stored_generation(user_id, GOOD_SQL), headers)
    assert body["status"] == "rejected" and body["approval"] is None


def test_expired_grants_are_ignored(guard: Guard) -> None:
    user_id, headers = guard.member()
    guard.api.sql("UPDATE permissions SET expires_at = now() - interval '1 minute' WHERE key = :k", k=f"sales.view.{guard._users}")
    body = guard.validated(guard.stored_generation(user_id, GOOD_SQL), headers)
    assert body["status"] == "rejected" and body["approval"] is None


def test_revoked_grants_are_ignored(guard: Guard) -> None:
    user_id, headers = guard.member()
    generation = guard.stored_generation(user_id, GOOD_SQL)
    deny_role = guard.api.sql(
        "SELECT rp.role_id FROM role_permissions rp JOIN permissions p ON p.id = rp.permission_id WHERE p.key = :k",
        k=f"sales.view.{guard._users}",
    )[0].role_id
    guard.api.sql("UPDATE role_permissions SET revoked_at = now() WHERE role_id = :r AND permission_id IN "
                  "(SELECT id FROM permissions WHERE key = :k)", r=deny_role, k=f"sales.view.{guard._users}")
    body = guard.validated(generation, headers)
    assert body["status"] == "rejected" and body["approval"] is None


def test_no_data_permission_means_deny(guard: Guard) -> None:
    user_id, headers = guard.member(policy=None)
    body = guard.validated(guard.stored_generation(user_id, GOOD_SQL), headers)
    assert body["status"] == "rejected" and body["approval"] is None


def test_row_filters_of_the_permission_are_applied(guard: Guard) -> None:
    body = _approved(guard.check("SELECT o.id, o.total_amount FROM sales.orders AS o LIMIT 5", policy=SOUTH_POLICY))
    sql = body["approved_sql"]
    # The customers filter cannot apply to orders alone; the orders filter must.
    assert "deleted" in sql and "orders" in sql
    assert any("orders" in r for r in body["applied_row_restrictions"])


def test_permission_change_after_approval_invalidates_it(guard: Guard) -> None:
    user_id, headers = guard.member()
    body = _approved(guard.validated(guard.stored_generation(user_id, GOOD_SQL), headers))
    deny = guard.create_permission(f"sales.blocked.{guard._users}", {"effect": "deny", "deny_all": True})
    guard.ok(guard.grant_direct(user_id, deny), 201)
    with pytest.raises(ExecutionNotAuthorizedError):
        guard.execute(user_id, body["approval"]["approval_id"])
    assert guard.approvals()[0].status == "invalidated"
    assert guard.sql_requests() == []


def test_row_filter_change_after_approval_invalidates_it(guard: Guard) -> None:
    user_id, headers = guard.member()
    body = _approved(guard.validated(guard.stored_generation(user_id, GOOD_SQL), headers))
    narrower = guard.create_permission(f"sales.narrow.{guard._users}", {
        "effect": "deny", "excluded_rows": [{"column": "orders.status", "operator": "equals", "value": "refunded"}],
    })
    guard.ok(guard.grant_direct(user_id, narrower), 201)
    with pytest.raises(ExecutionNotAuthorizedError) as error:
        guard.execute(user_id, body["approval"]["approval_id"])
    assert error.value.reason.startswith(("permissions_changed", "sql_changed", "revalidation_"))
    assert guard.approvals()[0].status == "invalidated"
    assert guard.sql_requests() == []


# --- approval lifecycle -------------------------------------------------------------------------------


def test_get_and_revoke_approval(guard: Guard) -> None:
    user_id, headers = guard.member()
    approval_id = _approved(guard.validated(guard.stored_generation(user_id, GOOD_SQL), headers))["approval"]["approval_id"]
    assert guard.ok(guard.approval(approval_id, headers))["status"] == "approved"
    _, other = guard.member()
    assert guard.approval(approval_id, other).status_code == 404
    assert guard.revoke(approval_id, other).status_code == 404
    revoked = guard.ok(guard.revoke(approval_id, headers))
    assert revoked["status"] == "revoked" and revoked["is_usable"] is False
    assert guard.revoke(approval_id, headers).status_code == 409
    with pytest.raises(ExecutionNotAuthorizedError) as error:
        guard.execute(user_id, approval_id)
    assert error.value.reason == "approval_revoked"
    assert guard.security_events("approval_revoked")


def test_approval_expires(guard: Guard) -> None:
    user_id, headers = guard.member()
    approval_id = _approved(guard.validated(guard.stored_generation(user_id, GOOD_SQL), headers))["approval"]["approval_id"]
    guard.clock.advance(301)
    view = guard.ok(guard.approval(approval_id, headers))
    assert view["status"] == "expired" and view["is_usable"] is False
    with pytest.raises(ExecutionNotAuthorizedError):
        guard.execute(user_id, approval_id)
    assert guard.sql_requests() == []


def test_revalidation_supersedes_the_previous_approval(guard: Guard) -> None:
    user_id, headers = guard.member()
    generation = guard.stored_generation(user_id, GOOD_SQL)
    first = _approved(guard.validated(generation, headers))["approval"]["approval_id"]
    second = _approved(guard.validated(generation, headers))["approval"]["approval_id"]
    assert first != second
    assert guard.ok(guard.approval(first, headers))["status"] == "invalidated"
    with pytest.raises(ExecutionNotAuthorizedError):
        guard.execute(user_id, first)


def test_approved_fields_are_immutable_in_the_database(guard: Guard) -> None:
    guard.check(GOOD_SQL)
    with pytest.raises(Exception):
        guard.api.sql("UPDATE sql_execution_approvals SET exact_sql = 'SELECT 1'")
    with pytest.raises(Exception):
        guard.api.sql("UPDATE sql_execution_approvals SET expires_at = expires_at + interval '1 day'")
    with pytest.raises(Exception):
        guard.api.sql("UPDATE sql_security_validations SET status = 'rejected', approved = false")


"""Assignments and authorization decisions, end to end: HTTP API -> database -> engine.

Nothing here mocks the engine: every decision comes from ``AuthorizationEngine``
reading the real tables.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from app.authorization.engine import AuthorizationEngine, AuthorizationRequest, evaluate
from app.core.exceptions import ConflictError
from app.models.user import User
from app.services.rbac_service import RbacService
from tests.rbac.conftest import SALES_VIEW, Rbac

pytestmark = pytest.mark.postgres

DATA = {"resource_type": "database_data", "action": "view"}


def _kerala() -> dict[str, Any]:
    return {**SALES_VIEW, "row_filters": [{"column": "region", "operator": "equals", "value": "Kerala"}]}


# --- assignments ---------------------------------------------------------------------------------


def test_assign_and_remove_permission_on_role(rbac: Rbac) -> None:
    permission = rbac.create_permission("sales.karnataka.view")
    role = rbac.create_role("regional_manager")
    rbac.grant_role_permissions(role, permission)
    detail = rbac.ok(rbac.get(f"/roles/{role}"))
    assert [p["key"] for p in detail["permissions"]] == ["sales.karnataka.view"]
    assert detail["permission_count"] == 1

    assert rbac.post(f"/roles/{role}/permissions", {"permission_ids": [permission]}).status_code == 409  # duplicate
    assert rbac.delete(f"/roles/{role}/permissions/{permission}").status_code == 204
    assert rbac.delete(f"/roles/{role}/permissions/{permission}").status_code == 404
    assert rbac.ok(rbac.get(f"/roles/{role}"))["permissions"] == []
    # History rows survive revocation.
    assert rbac.api.sql("SELECT count(*) FROM role_permissions WHERE role_id = :r", r=role)[0][0] == 1


def test_replace_role_permissions_atomically(rbac: Rbac) -> None:
    a, b, c = (rbac.create_permission(f"p.{n}", {}, resource_type="reports") for n in "abc")
    role = rbac.role_with("analyst", a, b)
    assert rbac.ok(rbac.put(f"/roles/{role}/permissions", {"permission_ids": [b, c]})) == {"added": 1, "removed": 1}
    assert [p["key"] for p in rbac.ok(rbac.get(f"/roles/{role}"))["permissions"]] == ["p.b", "p.c"]


def test_user_can_hold_multiple_roles(rbac: Rbac) -> None:
    user_id, _ = rbac.user()
    first, second = rbac.create_role("sales_manager"), rbac.create_role("inventory_manager")
    rbac.assign_role(user_id, first)
    rbac.assign_role(user_id, second, expires_at="2099-01-01T00:00:00Z")
    roles = rbac.ok(rbac.get(f"/users/{user_id}/roles"))
    assert {r["role"]["key"] for r in roles} == {"sales_manager", "inventory_manager"}
    assert rbac.post(f"/users/{user_id}/roles", {"role_id": first}).status_code == 409  # duplicate active
    assert rbac.ok(rbac.get(f"/roles/{first}"))["users"][0]["id"] == str(user_id)


def test_direct_permission_grant_and_duplicate(rbac: Rbac) -> None:
    user_id, _ = rbac.user()
    permission = rbac.create_permission("sales.view")
    assigned = rbac.ok(rbac.grant_direct(user_id, permission), 201)
    assert assigned["assignment_type"] == "grant"
    assert rbac.grant_direct(user_id, permission).status_code == 409
    assert rbac.authorize(user_id, **DATA).allowed
    assert rbac.authorize(user_id, **DATA).decision_reason == "Permission granted directly to the user."


def test_expired_assignments_are_ignored(rbac: Rbac) -> None:
    user_id, _ = rbac.user()
    permission = rbac.create_permission("sales.view")
    role = rbac.role_with("analyst", permission)
    rbac.assign_role(user_id, role)
    assert rbac.authorize(user_id, **DATA).allowed

    # Raw SQL bypasses the service (and its revision bump), so read uncached state here;
    # test_expiry_needs_no_invalidation covers expiry reached naturally through the cache.
    rbac.expire("user_roles", user_id=user_id, role_id=role)
    decision = rbac.authorize(user_id, fresh=True, **DATA)
    assert not decision.allowed
    assert rbac.ok(rbac.get(f"/users/{user_id}/roles"))[0]["is_expired"] is True
    # An expired assignment does not block a new one.
    rbac.assign_role(user_id, role)
    assert rbac.authorize(user_id, **DATA).allowed

    other_id, _ = rbac.user()
    rbac.ok(rbac.grant_direct(other_id, permission), 201)
    rbac.expire("user_permissions", user_id=other_id, permission_id=permission)
    assert not rbac.authorize(other_id, fresh=True, **DATA).allowed


def test_expiry_needs_no_invalidation(rbac: Rbac) -> None:
    """Cached grants carry their expiry; it is checked against the clock on every decision."""
    user_id, _ = rbac.user()
    role = rbac.role_with("contractor", rbac.create_permission("sales.view"))
    rbac.assign_role(user_id, role, expires_at="2099-01-01T00:00:00Z")

    async def effective_at(moment: datetime) -> bool:
        async with rbac.client.app.state.database.session() as session:  # type: ignore[attr-defined]
            user = await session.get(User, user_id)
            assert user is not None
            engine = AuthorizationEngine(session, rbac.client.app.state.authorization_cache)  # type: ignore[attr-defined]
            effective = await engine.effective(user, now=moment)
            return evaluate(effective, AuthorizationRequest(user_id, "database_data", "view")).allowed

    assert rbac.api.run(effective_at, datetime(2098, 12, 31, tzinfo=UTC))
    assert not rbac.api.run(effective_at, datetime(2099, 1, 1, 0, 0, 1, tzinfo=UTC))


def test_expired_permission_definition_grants_nothing(rbac: Rbac) -> None:
    user_id, _ = rbac.user()
    permission = rbac.create_permission("sales.view")
    rbac.assign_role(user_id, rbac.role_with("analyst", permission))
    rbac.api.sql("UPDATE permissions SET expires_at = now() - interval '1 minute' WHERE id = :p", p=permission)
    assert not rbac.authorize(user_id, fresh=True, **DATA).allowed


def test_revoked_assignments_are_ignored(rbac: Rbac) -> None:
    user_id, _ = rbac.user()
    permission = rbac.create_permission("sales.view")
    role = rbac.role_with("analyst", permission)
    rbac.assign_role(user_id, role)
    rbac.ok(rbac.grant_direct(user_id, permission), 201)

    assert rbac.delete(f"/users/{user_id}/roles/{role}").status_code == 204
    assert rbac.authorize(user_id, **DATA).allowed  # still held directly
    assert rbac.delete(f"/users/{user_id}/permissions/{permission}").status_code == 204
    assert not rbac.authorize(user_id, **DATA).allowed
    assert rbac.delete(f"/users/{user_id}/permissions/{permission}").status_code == 404


# --- authorization decisions ------------------------------------------------------------------------


def test_allowed_and_denied_actions(rbac: Rbac) -> None:
    user_id, _ = rbac.user()
    rbac.assign_role(user_id, rbac.role_with("analyst", rbac.create_permission("sales.karnataka.view")))

    allowed = rbac.authorize(user_id, tables=("sales",), columns=("sales.amount", "sales.region"), **DATA)
    assert allowed.allowed and allowed.decision_reason == "Permission granted through assigned role."
    assert allowed.allowed_tables == ("sales",)
    assert allowed.row_filters[0].filter == {
        "column": "sales.region",
        "operator": "equals",
        "value": "Karnataka",
        "value_type": "string",
    }
    assert not allowed.allow_export and not allowed.allow_sql_view

    assert not rbac.authorize(user_id, resource_type="database_data", action="export").allowed
    assert not rbac.authorize(user_id, tables=("customers",), **DATA).allowed
    assert not rbac.authorize(user_id, tables=("sales",), export=True, **DATA).allowed


def test_deny_by_default(rbac: Rbac) -> None:
    user_id, headers = rbac.user()
    decision = rbac.authorize(user_id, **DATA)
    assert not decision.allowed
    assert decision.decision_reason == "No applicable permission was found."
    assert not rbac.authorize(user_id, resource_type="system", action="users:read").allowed
    assert rbac.get("/users", headers).status_code == 403
    assert rbac.get("/roles", headers).status_code == 403
    me = rbac.ok(rbac.get("/authorization/me", headers))
    assert me["permission_keys"] == [] and me["system_permissions"] == []


def test_multiple_roles_aggregate(rbac: Rbac) -> None:
    user_id, _ = rbac.user()
    karnataka = rbac.create_permission("sales.karnataka.view")
    kerala = rbac.create_permission("sales.kerala.view", _kerala())
    reports = rbac.create_permission("executive_reports.view", {"allow_export": True}, resource_type="executive_reports")
    rbac.assign_role(user_id, rbac.role_with("south_a", karnataka))
    rbac.assign_role(user_id, rbac.role_with("south_b", kerala, reports))

    decision = rbac.authorize(user_id, tables=("sales",), columns=("amount",), **DATA)
    assert decision.allowed
    assert decision.row_filters[0].filter["logic"] == "or"
    assert {c["value"] for c in decision.row_filters[0].filter["conditions"]} == {"Karnataka", "Kerala"}
    assert set(decision.matched_permissions) == {"sales.karnataka.view", "sales.kerala.view"}
    report = rbac.authorize(user_id, resource_type="executive_reports", action="view", export=True)
    assert report.allowed and report.allow_export


def test_explicit_deny_takes_precedence(rbac: Rbac) -> None:
    user_id, _ = rbac.user()
    unrestricted = rbac.create_permission(
        "sales.all.view", {"allowed_tables": ["sales"], "allowed_columns": ["*"], "allow_export": True}
    )
    no_pii = rbac.create_permission(
        "sales.no_pii",
        {
            "effect": "deny",
            "denied_columns": ["sales.customer_email"],
            "excluded_rows": [{"column": "sales.region", "operator": "equals", "value": "Internal"}],
            "deny_export": True,
        },
    )
    rbac.assign_role(user_id, rbac.role_with("analyst", unrestricted))
    rbac.assign_role(user_id, rbac.role_with("compliance", no_pii))

    pii = rbac.authorize(user_id, tables=("sales",), columns=("customer_email",), **DATA)
    assert not pii.allowed and "explicitly denied" in pii.decision_reason
    assert not rbac.authorize(user_id, tables=("sales",), columns=("amount",), export=True, **DATA).allowed

    ok = rbac.authorize(user_id, tables=("sales",), columns=("amount",), **DATA)
    assert ok.allowed
    assert ok.denied_columns == ("sales.customer_email",)
    assert ok.allow_export is False
    assert ok.row_filters[0].filter == {
        "logic": "not",
        "conditions": [{"column": "sales.region", "operator": "equals", "value": "Internal", "value_type": "string"}],
    }

    deny_all = rbac.create_permission("sales.blocked", {"effect": "deny", "deny_all": True})
    rbac.ok(rbac.grant_direct(user_id, deny_all), 201)
    assert not rbac.authorize(user_id, **DATA).allowed


def test_direct_deny_assignment_removes_a_role_permission(rbac: Rbac) -> None:
    user_id, _ = rbac.user()
    permission = rbac.create_permission("sales.view")
    rbac.assign_role(user_id, rbac.role_with("analyst", permission))
    rbac.ok(rbac.grant_direct(user_id, permission, assignment_type="deny"), 201)
    assert not rbac.authorize(user_id, **DATA).allowed
    me = rbac.ok(rbac.get(f"/users/{user_id}/effective-permissions"))
    assert me["revoked_permission_keys"] == ["sales.view"] and me["permission_keys"] == []


def test_column_restriction(rbac: Rbac) -> None:
    user_id, _ = rbac.user()
    rbac.assign_role(user_id, rbac.role_with("analyst", rbac.create_permission("sales.view")))
    assert rbac.authorize(user_id, tables=("sales",), columns=("sales.amount", "sales.product_id"), **DATA).allowed
    for forbidden in ("sales.customer_email", "sales.internal_cost"):
        decision = rbac.authorize(user_id, tables=("sales",), columns=("sales.amount", forbidden), **DATA)
        assert not decision.allowed
        assert decision.decision_reason == f"Column '{forbidden}' is not permitted."


def test_row_restriction_per_user(rbac: Rbac) -> None:
    karnataka_id, _ = rbac.user("karnataka")
    kerala_id, _ = rbac.user("kerala")
    rbac.assign_role(karnataka_id, rbac.role_with("ka", rbac.create_permission("sales.karnataka.view")))
    rbac.assign_role(kerala_id, rbac.role_with("kl", rbac.create_permission("sales.kerala.view", _kerala())))
    assert rbac.authorize(karnataka_id, tables=("sales",), **DATA).row_filters[0].filter["value"] == "Karnataka"
    assert rbac.authorize(kerala_id, tables=("sales",), **DATA).row_filters[0].filter["value"] == "Kerala"


def test_operations_restriction(rbac: Rbac) -> None:
    user_id, _ = rbac.user()
    totals = rbac.create_permission("profit.totals", {**SALES_VIEW, "row_filters": [], "allowed_operations": ["aggregate"]})
    rbac.assign_role(user_id, rbac.role_with("exec", totals))
    assert rbac.authorize(user_id, tables=("sales",), operation="aggregate", **DATA).allowed
    assert not rbac.authorize(user_id, tables=("sales",), operation="read", **DATA).allowed


def test_inactive_user_is_denied(rbac: Rbac) -> None:
    user_id, headers = rbac.user()
    rbac.assign_role(user_id, rbac.role_with("analyst", rbac.create_permission("sales.view")))
    assert rbac.authorize(user_id, **DATA).allowed
    assert rbac.post(f"/users/{user_id}/deactivate").status_code == 200
    decision = rbac.authorize(user_id, **DATA)
    assert not decision.allowed and decision.decision_reason == "The user account is inactive."
    assert rbac.get("/authorization/me", headers).status_code == 401  # sessions ended too


def test_deactivated_role_is_denied(rbac: Rbac) -> None:
    user_id, _ = rbac.user()
    role = rbac.role_with("analyst", rbac.create_permission("sales.view"))
    rbac.assign_role(user_id, role)
    rbac.ok(rbac.patch(f"/roles/{role}", {"is_active": False}))
    assert not rbac.authorize(user_id, **DATA).allowed
    assert rbac.post(f"/users/{user_id}/roles", {"role_id": role}).status_code == 422  # inactive roles are not assignable
    other, _ = rbac.user()
    assert rbac.post(f"/users/{other}/roles", {"role_id": role}).status_code == 422  # inactive roles are not assignable
    rbac.ok(rbac.patch(f"/roles/{role}", {"is_active": True}))
    assert rbac.authorize(user_id, **DATA).allowed


def test_permission_revocation_and_deactivation_take_effect_immediately(rbac: Rbac) -> None:
    user_id, _ = rbac.user()
    permission = rbac.create_permission("sales.view")
    role = rbac.role_with("analyst", permission)
    rbac.assign_role(user_id, role)
    assert rbac.authorize(user_id, **DATA).allowed  # now cached
    rbac.ok(rbac.patch(f"/permissions/{permission}", {"is_active": False}))
    assert not rbac.authorize(user_id, **DATA).allowed
    rbac.ok(rbac.patch(f"/permissions/{permission}", {"is_active": True}))
    assert rbac.authorize(user_id, **DATA).allowed
    assert rbac.delete(f"/roles/{role}/permissions/{permission}").status_code == 204
    assert not rbac.authorize(user_id, **DATA).allowed


def test_role_modification_is_reflected(rbac: Rbac) -> None:
    user_id, _ = rbac.user()
    permission = rbac.create_permission("sales.view")
    role = rbac.role_with("analyst", permission)
    rbac.assign_role(user_id, role)
    assert rbac.authorize(user_id, tables=("sales",), columns=("amount",), **DATA).row_filters[0].filter["value"] == "Karnataka"
    rbac.ok(rbac.patch(f"/permissions/{permission}", {"policy_definition": _kerala()}))
    assert rbac.authorize(user_id, tables=("sales",), columns=("amount",), **DATA).row_filters[0].filter["value"] == "Kerala"
    extra = rbac.create_permission("reports.view", {}, resource_type="reports")
    rbac.grant_role_permissions(role, extra)
    assert rbac.authorize(user_id, resource_type="reports", action="view").allowed


def test_direct_permission_cannot_bypass_mandatory_restrictions(rbac: Rbac) -> None:
    user_id, _ = rbac.user()
    restriction = rbac.create_permission(
        "sales.restricted", {"effect": "deny", "denied_columns": ["sales.internal_cost"], "deny_sql_view": True}
    )
    rbac.assign_role(user_id, rbac.role_with("compliance", restriction))
    everything = rbac.create_permission(
        "sales.everything", {"allowed_tables": ["sales"], "allowed_columns": ["*"], "allow_sql_view": True}
    )
    rbac.ok(rbac.grant_direct(user_id, everything), 201)

    assert not rbac.authorize(user_id, tables=("sales",), columns=("internal_cost",), **DATA).allowed
    assert not rbac.authorize(user_id, tables=("sales",), sql_view=True, **DATA).allowed
    assert rbac.authorize(user_id, tables=("sales",), columns=("amount",), **DATA).allowed


def test_direct_grant_override_only_narrows(rbac: Rbac) -> None:
    user_id, _ = rbac.user()
    regional = rbac.create_permission("sales.regional", {**SALES_VIEW, "row_filters": []})
    override = {"row_filters": [{"column": "region", "operator": "equals", "value": "Goa"}]}
    rbac.ok(rbac.grant_direct(user_id, regional, policy_override=override), 201)
    assert rbac.authorize(user_id, tables=("sales",), **DATA).row_filters[0].filter["value"] == "Goa"
    other, _ = rbac.user()
    response = rbac.grant_direct(other, regional, policy_override={"allow_export": True})
    assert response.status_code == 422 and response.json()["error"]["code"] == "INVALID_POLICY"


# --- privilege escalation ----------------------------------------------------------------------------


def test_unauthorized_users_cannot_administer(rbac: Rbac) -> None:
    user_id, headers = rbac.user()
    role = rbac.create_role("ceo")
    permission = rbac.create_permission("sales.view")
    attempts = [
        rbac.post("/roles", {"key": "x", "name": "X"}, headers),
        rbac.patch(f"/roles/{role}", {"name": "Y"}, headers),
        rbac.delete(f"/roles/{role}", headers),
        rbac.post("/permissions", {"key": "x", "name": "X", "resource_type": "r", "action": "a"}, headers),
        rbac.patch(f"/permissions/{permission}", {"name": "Y"}, headers),
        rbac.post(f"/roles/{role}/permissions", {"permission_ids": [permission]}, headers),
        rbac.get(f"/users/{rbac.admin_id}/effective-permissions", headers),
        rbac.post("/authorization/check", {"user_id": str(rbac.admin_id), **DATA}, headers),
    ]
    assert [r.status_code for r in attempts] == [403] * len(attempts)


def test_user_cannot_grant_themselves_permissions_or_roles(rbac: Rbac) -> None:
    user_id, headers = rbac.user()
    role = rbac.role_with("analyst", rbac.create_permission("sales.view"))
    permission = rbac.create_permission("other.view")
    assert rbac.post(f"/users/{user_id}/permissions", {"permission_id": permission}, headers).status_code == 403
    assert rbac.post(f"/users/{user_id}/roles", {"role_id": role}, headers).status_code == 403
    admin_role = rbac.role_id("administrator")
    assert rbac.post(f"/users/{user_id}/roles", {"role_id": admin_role}, headers).status_code == 403
    assert rbac.ok(rbac.get(f"/users/{user_id}/roles")) == []


def test_user_cannot_modify_their_own_roles_even_as_delegated_administrator(rbac: Rbac) -> None:
    delegate_id, delegate = rbac.user("delegate")
    rbac.assign_role(delegate_id, rbac.role_id("administrator"))
    role = rbac.create_role("analyst")
    # The delegated administrator can manage others...
    other_id, _ = rbac.user()
    assert rbac.post(f"/users/{other_id}/roles", {"role_id": role}, delegate).status_code == 201
    # ...but not themselves, in either direction.
    response = rbac.post(f"/users/{delegate_id}/roles", {"role_id": role}, delegate)
    assert response.status_code == 403 and "your own" in response.json()["error"]["message"]
    assert rbac.delete(f"/users/{delegate_id}/roles/{rbac.role_id('administrator')}", delegate).status_code == 403


def test_administrator_only_permissions_cannot_be_delegated(rbac: Rbac) -> None:
    role = rbac.create_role("helpdesk")
    users_manage = rbac.system_permission_id("users:manage")
    access_assign = rbac.system_permission_id("access:assign")
    assert rbac.post(f"/roles/{role}/permissions", {"permission_ids": [users_manage]}).status_code == 422
    user_id, headers = rbac.user()
    assert rbac.grant_direct(user_id, access_assign).status_code == 422
    # Read-only system permissions can be delegated and are enforced by the real routes.
    rbac.grant_role_permissions(role, rbac.system_permission_id("users:read"))
    rbac.assign_role(user_id, role)
    assert rbac.get("/users", headers).status_code == 200
    assert rbac.post("/users", {"email": "x@example.com", "full_name": "X", "password": "a" * 20}, headers).status_code == 403
    me = rbac.ok(rbac.get("/auth/me", headers))
    assert me["permissions"] == ["users:read"]


def test_administrator_role_grants_administration(rbac: Rbac) -> None:
    user_id, headers = rbac.user()
    assert rbac.get("/roles", headers).status_code == 403
    rbac.assign_role(user_id, rbac.role_id("administrator"))
    assert rbac.get("/roles", headers).status_code == 200
    me = rbac.ok(rbac.get("/authorization/me", headers))
    assert me["is_administrator"] is True and "access:assign" in me["system_permissions"]
    assert rbac.delete(f"/users/{user_id}/roles/{rbac.role_id('administrator')}").status_code == 204
    assert rbac.get("/roles", headers).status_code == 403


def test_last_active_administrator_keeps_the_role(rbac: Rbac) -> None:
    """Defence in depth behind the self-change rule: even an installation-level actor
    cannot remove the administrator role from the only remaining active administrator."""
    first_id, _ = rbac.user("first")
    admin_role = rbac.role_id("administrator")
    rbac.assign_role(first_id, admin_role)
    rbac.api.sql("UPDATE users SET is_active = false WHERE is_superuser")  # no break-glass account left
    actor = User(id=uuid.uuid4(), email="host@example.com", full_name="Host", password_hash="x",
                 is_active=True, is_superuser=True)

    async def revoke() -> None:
        async with rbac.client.app.state.database.session() as session:  # type: ignore[attr-defined]
            await RbacService(session, cache=None).revoke_user_role(first_id, uuid.UUID(admin_role), actor)

    with pytest.raises(ConflictError, match="last active administrator"):
        rbac.api.run(revoke)
    rbac.api.sql("UPDATE users SET is_active = true WHERE is_superuser")
    assert rbac.delete(f"/users/{first_id}/roles/{admin_role}").status_code == 204


def test_installation_administrator_cannot_be_deactivated(rbac: Rbac) -> None:
    delegate_id, delegate = rbac.user("delegate")
    rbac.assign_role(delegate_id, rbac.role_id("administrator"))
    assert rbac.post(f"/users/{rbac.admin_id}/deactivate", None, delegate).status_code == 409
    assert rbac.delete(f"/users/{rbac.admin_id}", delegate).status_code == 409


def test_diagnostic_check_endpoint(rbac: Rbac) -> None:
    user_id, headers = rbac.user()
    rbac.assign_role(user_id, rbac.role_with("analyst", rbac.create_permission("sales.view")))
    own = rbac.check(headers, tables=["sales"], columns=["sales.amount"], **DATA)
    assert own["allowed"] and own["diagnostic"] and own["row_filters"][0]["table"] == "sales"
    for_other = rbac.check(user_id=str(user_id), **DATA)  # administrator diagnosing another user
    assert for_other["allowed"] and for_other["user_id"] == str(user_id)
    # The decision format is response-only: posting one back is refused, not trusted.
    forged = rbac.post("/authorization/check", {**DATA, "allowed": True, "allowed_tables": ["payroll"]}, headers)
    assert forged.status_code == 422


def test_unknown_ids_return_404(rbac: Rbac) -> None:
    missing = "00000000-0000-4000-8000-000000000000"
    user_id, _ = rbac.user()
    assert rbac.get(f"/roles/{missing}").status_code == 404
    assert rbac.get(f"/permissions/{missing}").status_code == 404
    assert rbac.post(f"/users/{missing}/roles", {"role_id": rbac.create_role("rr")}).status_code == 404
    assert rbac.post(f"/users/{user_id}/roles", {"role_id": missing}).status_code == 404
    assert rbac.get(f"/users/{missing}/effective-permissions").status_code == 404

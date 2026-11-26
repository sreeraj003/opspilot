"""Roles and permission definitions through the HTTP API (real database)."""

import pytest

from app.core.authorization import SYSTEM_PERMISSIONS
from tests.rbac.conftest import SALES_VIEW, Rbac

pytestmark = pytest.mark.postgres


# --- roles ------------------------------------------------------------------------------------


def test_create_retrieve_update_and_deactivate_role(rbac: Rbac) -> None:
    created = rbac.ok(
        rbac.post("/roles", {"key": "finance_manager", "name": "Finance  Manager", "description": " Finance team "}), 201
    )
    assert created["name"] == "Finance Manager"
    assert created["description"] == "Finance team"
    assert created["is_system"] is False and created["is_active"] is True

    detail = rbac.ok(rbac.get(f"/roles/{created['id']}"))
    assert detail["key"] == "finance_manager"
    assert detail["permissions"] == [] and detail["users"] == []
    assert [e["event_type"] for e in detail["history"]] == ["role_created"]

    updated = rbac.ok(rbac.patch(f"/roles/{created['id']}", {"name": "Finance Lead", "description": "Leads finance"}))
    assert updated["name"] == "Finance Lead"

    deactivated = rbac.ok(rbac.patch(f"/roles/{created['id']}", {"is_active": False}))
    assert deactivated["is_active"] is False
    history = [e["event_type"] for e in rbac.ok(rbac.get(f"/roles/{created['id']}"))["history"]]
    assert history[:2] == ["role_deactivated", "role_updated"]


def test_list_roles_with_search_status_and_pagination(rbac: Rbac) -> None:
    for key in ("ceo", "sales_manager", "regional_manager"):
        rbac.create_role(key)
    rbac.patch(f"/roles/{rbac.role_id('ceo')}", {"is_active": False})

    everything = rbac.ok(rbac.get("/roles?limit=2"))
    assert everything["total"] == 4  # three custom roles + the built-in Administrator
    assert len(everything["items"]) == 2
    assert [r["key"] for r in rbac.ok(rbac.get("/roles?search=manager"))["items"]] == ["regional_manager", "sales_manager"]
    assert [r["key"] for r in rbac.ok(rbac.get("/roles?status=inactive"))["items"]] == ["ceo"]
    admin = next(r for r in rbac.ok(rbac.get("/roles?search=administrator"))["items"])
    assert admin["is_system"] and admin["permission_count"] == len(SYSTEM_PERMISSIONS)


def test_duplicate_role_keys_and_names_are_rejected(rbac: Rbac) -> None:
    rbac.create_role("ceo", "Chief Executive")
    assert rbac.post("/roles", {"key": "ceo", "name": "Another"}).status_code == 409
    assert rbac.post("/roles", {"key": "chief", "name": "chief executive"}).status_code == 409
    assert rbac.post("/roles", {"key": "administrator", "name": "Admin 2"}).status_code == 409
    assert rbac.post("/roles", {"key": "Bad Key!", "name": "X"}).status_code == 422


def test_protected_system_role_cannot_be_deleted_or_changed(rbac: Rbac) -> None:
    admin_role = rbac.role_id("administrator")
    assert rbac.delete(f"/roles/{admin_role}").status_code == 409
    assert rbac.patch(f"/roles/{admin_role}", {"is_active": False}).status_code == 409
    assert rbac.patch(f"/roles/{admin_role}", {"key": "admins"}).status_code == 409
    assert rbac.ok(rbac.patch(f"/roles/{admin_role}", {"description": "Our admins"}))["description"] == "Our admins"
    custom = rbac.create_permission("reports.view", {}, resource_type="reports")
    assert rbac.post(f"/roles/{admin_role}/permissions", {"permission_ids": [custom]}).status_code == 409


def test_delete_custom_role(rbac: Rbac) -> None:
    role = rbac.create_role("temporary")
    user_id, _ = rbac.user()
    rbac.assign_role(user_id, role)
    assert rbac.delete(f"/roles/{role}").status_code == 409  # still assigned
    assert rbac.delete(f"/users/{user_id}/roles/{role}").status_code == 204
    assert rbac.delete(f"/roles/{role}").status_code == 204
    assert rbac.get(f"/roles/{role}").status_code == 404
    # History is kept and the key can be reused.
    assert rbac.api.sql("SELECT count(*) FROM rbac_audit_events WHERE role_id = :r", r=role)[0][0] == 4
    rbac.create_role("temporary")


# --- permissions --------------------------------------------------------------------------------


def test_create_retrieve_update_and_deactivate_permission(rbac: Rbac) -> None:
    created = rbac.ok(
        rbac.post(
            "/permissions",
            {
                "name": "Karnataka Sales Access",
                "key": "sales.karnataka.view",
                "resource_type": "database_data",
                "action": "view",
                "policy_definition": SALES_VIEW,
            },
        ),
        201,
    )
    assert created["policy_definition"]["allowed_columns"] == [
        "sales.amount",
        "sales.product_id",
        "sales.region",
        "sales.sale_date",
    ]
    assert created["policy_definition"]["version"] == 1

    detail = rbac.ok(rbac.get(f"/permissions/{created['id']}"))
    assert detail["roles"] == [] and detail["history"][0]["event_type"] == "permission_created"

    new_policy = {**SALES_VIEW, "allow_export": True}
    updated = rbac.ok(rbac.patch(f"/permissions/{created['id']}", {"policy_definition": new_policy, "name": "Karnataka sales"}))
    assert updated["policy_definition"]["allow_export"] is True
    event = rbac.ok(rbac.get(f"/permissions/{created['id']}"))["history"][0]
    assert event["event_type"] == "permission_updated"
    assert event["details"]["before"]["policy_definition"]["allow_export"] is False

    assert rbac.ok(rbac.patch(f"/permissions/{created['id']}", {"is_active": False}))["is_active"] is False
    assert [p["key"] for p in rbac.ok(rbac.get("/permissions?status=inactive"))["items"]] == ["sales.karnataka.view"]


def test_duplicate_permission_keys_are_rejected(rbac: Rbac) -> None:
    rbac.create_permission("sales.view")
    response = rbac.post(
        "/permissions", {"key": "sales.view", "name": "x", "resource_type": "database_data", "action": "view", "policy_definition": SALES_VIEW}
    )
    assert response.status_code == 409
    builtin = rbac.post("/permissions", {"key": "users:read", "name": "x", "resource_type": "reports", "action": "view"})
    assert builtin.status_code == 409


def test_malformed_policy_is_rejected_with_locations(rbac: Rbac) -> None:
    response = rbac.post(
        "/permissions",
        {
            "key": "bad.policy",
            "name": "Bad",
            "resource_type": "database_data",
            "action": "view",
            "policy_definition": {
                "allowed_tables": ["sales"],
                "allowed_columns": ["amount"],
                "row_filters": [{"column": "region", "operator": "matches", "value": "K%"}],
            },
        },
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "INVALID_POLICY"
    assert error["details"][0]["location"] == ["policy_definition", "row_filters", "0", "operator"]
    assert rbac.ok(rbac.get("/permissions?search=bad.policy"))["total"] == 0


def test_unsupported_operator_and_excessive_nesting_rejected_by_api(rbac: Rbac) -> None:
    node = {"column": "amount", "operator": "greater_than", "value": 1}
    for _ in range(5):
        node = {"logic": "and", "conditions": [node, {"column": "region", "operator": "is_not_null"}]}
    response = rbac.post(
        "/permissions",
        {"key": "deep", "name": "Deep", "resource_type": "database_data", "action": "view",
         "policy_definition": {"allowed_tables": ["sales"], "allowed_columns": ["*"], "row_filters": [node]}},
    )
    assert response.status_code == 422
    assert "nested too deeply" in str(response.json()["error"]["details"])

    shallow = rbac.post(
        "/permissions/validate-policy",
        {"resource_type": "database_data", "policy_definition": {"allowed_tables": ["sales"], "allowed_columns": ["*"]}},
    )
    assert shallow.status_code == 200
    assert shallow.json()["policy_definition"]["allowed_columns"] == ["sales.*"]


def test_nesting_limit_is_configurable(make_rbac) -> None:  # type: ignore[no-untyped-def]
    rbac = make_rbac(authorization_policy_max_depth=1)
    nested = {"logic": "or", "conditions": [{"logic": "and", "conditions": [{"column": "a", "operator": "is_null"}]}]}
    response = rbac.post(
        "/permissions/validate-policy",
        {"resource_type": "database_data", "policy_definition": {"allowed_tables": ["t"], "allowed_columns": ["*"], "row_filters": [nested]}},
    )
    assert response.status_code == 422


def test_system_resource_type_and_system_permissions_are_protected(rbac: Rbac) -> None:
    assert rbac.post("/permissions", {"key": "x.y", "name": "x", "resource_type": "system", "action": "x"}).status_code == 422
    users_read = rbac.system_permission_id("users:read")
    assert rbac.patch(f"/permissions/{users_read}", {"name": "Renamed"}).status_code == 409
    assert rbac.delete(f"/permissions/{users_read}").status_code == 409


def test_assigned_permission_must_be_deactivated_not_deleted(rbac: Rbac) -> None:
    permission = rbac.create_permission("sales.view")
    role = rbac.role_with("analyst", permission)
    assert rbac.delete(f"/permissions/{permission}").status_code == 409
    assert rbac.delete(f"/roles/{role}/permissions/{permission}").status_code == 204
    assert rbac.delete(f"/permissions/{permission}").status_code == 204
    assert rbac.get(f"/permissions/{permission}").status_code == 404
    events = rbac.api.sql("SELECT event_type FROM rbac_audit_events WHERE permission_id = :p ORDER BY created_at", p=permission)
    assert [e[0] for e in events] == [
        "permission_created",
        "role_permission_assigned",
        "role_permission_revoked",
        "permission_deleted",
    ]


def test_permission_expiry_must_be_in_the_future_and_timezone_aware(rbac: Rbac) -> None:
    body = {"key": "p.e", "name": "x", "resource_type": "reports", "action": "view"}
    assert rbac.post("/permissions", {**body, "expires_at": "2020-01-01T00:00:00Z"}).status_code == 422
    assert rbac.post("/permissions", {**body, "expires_at": "2099-01-01T00:00:00"}).status_code == 422
    assert rbac.post("/permissions", {**body, "expires_at": "2099-01-01T00:00:00Z"}).status_code == 201

"""Role-based access control and organization isolation, end to end."""

from collections.abc import Callable
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.deps import require_permissions
from app.core.config import Settings
from app.database.session import Database
from app.main import create_app
from app.models import AuditEvent
from app.security.principal import Principal
from app.security.rbac import Permission
from tests.helpers import DEFAULT_PASSWORD, Factory, bearer, login

USERS = "/api/v1/users"


def _new_user(email: str, *roles: str) -> dict:
    return {"email": email, "full_name": "New User", "password": DEFAULT_PASSWORD, "roles": list(roles)}


# Permission checks ------------------------------------------------------------------------


def test_permission_denied_is_403_and_audited(api: TestClient, factory: Factory, database: Database) -> None:
    viewer = factory.user(factory.organization(), "viewer")
    tokens = login(api, viewer)

    response = api.get(USERS, headers=bearer(tokens))
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"
    assert api.post(USERS, json=_new_user("x@acme.io", "viewer"), headers=bearer(tokens)).status_code == 403
    assert api.get("/api/v1/audit-events", headers=bearer(tokens)).status_code == 403

    with database.session() as session:
        denied = session.scalars(select(AuditEvent).where(AuditEvent.action == "authz.permission_denied")).all()
        assert len(denied) == 3
        assert denied[0].details["missing_permissions"] == ["users.read"]
        assert denied[0].actor_user_id == viewer.id


def test_unauthenticated_requests_get_401_not_403(api: TestClient) -> None:
    assert api.get(USERS).status_code == 401


@pytest.mark.parametrize(
    "role,allowed",
    [("org_admin", True), ("operations_manager", True), ("analyst", False), ("support_agent", False)],
)
def test_access_follows_role_permissions(api: TestClient, factory: Factory, role: str, allowed: bool) -> None:
    tokens = login(api, factory.user(factory.organization(), role))
    status = api.get(USERS, headers=bearer(tokens)).status_code
    assert status == (200 if allowed else 403)


def test_require_permissions_needs_every_listed_permission(
    make_settings: Callable[..., Settings], db_overrides: dict, factory: Factory
) -> None:
    app: FastAPI = create_app(make_settings(**db_overrides))

    @app.get("/probe")
    def probe(
        _: Annotated[Principal, Depends(require_permissions(Permission.TICKETS_READ, Permission.TICKETS_MANAGE))],
    ) -> dict:
        return {"ok": True}

    with TestClient(app) as client:
        org = factory.organization()
        agent = login(client, factory.user(org, "support_agent"))
        analyst = login(client, factory.user(org, "analyst"))  # has tickets.read only
        assert client.get("/probe", headers=bearer(agent)).status_code == 200
        assert client.get("/probe", headers=bearer(analyst)).status_code == 403


def test_permission_changes_apply_without_new_login(api: TestClient, factory: Factory) -> None:
    org = factory.organization()
    admin = login(api, factory.user(org, "org_admin"))
    member = factory.user(org, "viewer")
    member_tokens = login(api, member)
    assert api.get(USERS, headers=bearer(member_tokens)).status_code == 403

    assert (
        api.put(
            f"{USERS}/{member.id}/roles",
            json={"roles": ["analyst", "operations_manager"]},
            headers=bearer(admin),
        ).status_code
        == 200
    )
    assert api.get(USERS, headers=bearer(member_tokens)).status_code == 200


# Organization isolation ---------------------------------------------------------------------


def test_users_only_see_their_own_organization(api: TestClient, factory: Factory) -> None:
    acme, globex = factory.organization("Acme"), factory.organization("Globex")
    acme_admin = factory.user(acme, "org_admin")
    acme_member = factory.user(acme, "viewer")
    globex_member = factory.user(globex, "viewer")
    tokens = login(api, acme_admin)

    listed = api.get(USERS, headers=bearer(tokens)).json()
    assert {u["id"] for u in listed["items"]} == {str(acme_admin.id), str(acme_member.id)}
    assert listed["total"] == 2

    # Another tenant's user is invisible: 404, not 403, so existence is not disclosed.
    assert api.get(f"{USERS}/{globex_member.id}", headers=bearer(tokens)).status_code == 404
    assert api.get(f"{USERS}/{acme_member.id}", headers=bearer(tokens)).status_code == 200


def test_cross_organization_writes_are_denied(api: TestClient, factory: Factory, database: Database) -> None:
    acme, globex = factory.organization("Acme"), factory.organization("Globex")
    tokens = login(api, factory.user(acme, "org_admin"))
    victim = factory.user(globex, "viewer")

    assert api.post(f"{USERS}/{victim.id}/deactivate", headers=bearer(tokens)).status_code == 404
    assert (
        api.put(f"{USERS}/{victim.id}/roles", json={"roles": ["org_admin"]}, headers=bearer(tokens)).status_code == 404
    )
    login(api, victim)  # still active and unchanged


def test_client_supplied_tenant_ids_are_not_trusted(api: TestClient, factory: Factory) -> None:
    acme, globex = factory.organization("Acme"), factory.organization("Globex")
    tokens = login(api, factory.user(acme, "org_admin"))

    listing = api.get(USERS, params={"organization_id": str(globex)}, headers=bearer(tokens))
    assert listing.status_code == 403
    created = api.post(
        USERS,
        params={"organization_id": str(globex)},
        json=_new_user("intruder@acme.io", "viewer"),
        headers=bearer(tokens),
    )
    assert created.status_code == 403
    audit = api.get("/api/v1/audit-events", params={"organization_id": str(globex)}, headers=bearer(tokens))
    assert audit.status_code == 403
    # Unknown body fields (such as an organization id) are rejected outright.
    smuggled = api.post(
        USERS,
        json={**_new_user("y@acme.io", "viewer"), "organization_id": str(globex)},
        headers=bearer(tokens),
    )
    assert smuggled.status_code == 422


def test_platform_admin_can_access_other_organizations(api: TestClient, factory: Factory) -> None:
    platform, globex = factory.organization("Platform"), factory.organization("Globex")
    tokens = login(api, factory.user(platform, "super_admin"))
    globex_member = factory.user(globex, "viewer")

    listing = api.get(USERS, params={"organization_id": str(globex)}, headers=bearer(tokens))
    assert listing.status_code == 200
    assert [u["id"] for u in listing.json()["items"]] == [str(globex_member.id)]
    assert api.get(f"{USERS}/{globex_member.id}", headers=bearer(tokens)).status_code == 200

    created = api.post(
        USERS,
        params={"organization_id": str(globex)},
        json=_new_user("new@globex.io", "analyst"),
        headers=bearer(tokens),
    )
    assert created.status_code == 201
    assert created.json()["organization_id"] == str(globex)


def test_audit_log_is_scoped_to_the_organization(api: TestClient, factory: Factory) -> None:
    acme, globex = factory.organization("Acme"), factory.organization("Globex")
    acme_admin = factory.user(acme, "org_admin")
    login(api, factory.user(globex, "viewer"))
    tokens = login(api, acme_admin)

    events = api.get("/api/v1/audit-events", headers=bearer(tokens)).json()["items"]
    assert events and all(e["organization_id"] == str(acme) for e in events)
    assert {e["actor_user_id"] for e in events} == {str(acme_admin.id)}


# User administration ----------------------------------------------------------------------------


def test_admin_creates_user_who_can_log_in(api: TestClient, factory: Factory) -> None:
    org = factory.organization()
    admin = login(api, factory.user(org, "org_admin"))
    response = api.post(USERS, json=_new_user("New.Person@Acme.io", "support_agent"), headers=bearer(admin))
    assert response.status_code == 201, response.text
    assert response.json()["email"] == "new.person@acme.io"
    assert response.json()["organization_id"] == str(org)

    tokens = api.post("/api/v1/auth/login", json={"email": "new.person@acme.io", "password": DEFAULT_PASSWORD})
    assert tokens.status_code == 200


def test_roles_cannot_be_escalated(api: TestClient, factory: Factory) -> None:
    org = factory.organization()
    admin = login(api, factory.user(org, "org_admin"))
    member = factory.user(org, "viewer")

    assert api.post(USERS, json=_new_user("boss@acme.io", "super_admin"), headers=bearer(admin)).status_code == 403
    assert (
        api.put(f"{USERS}/{member.id}/roles", json={"roles": ["super_admin"]}, headers=bearer(admin)).status_code == 403
    )
    assert (
        api.put(f"{USERS}/{member.id}/roles", json={"roles": ["no_such_role"]}, headers=bearer(admin)).status_code
        == 422
    )


def test_users_cannot_manage_themselves_or_stronger_users(api: TestClient, factory: Factory) -> None:
    org = factory.organization()
    admin = factory.user(org, "org_admin")
    super_admin = factory.user(org, "super_admin")
    tokens = login(api, admin)

    assert api.post(f"{USERS}/{admin.id}/deactivate", headers=bearer(tokens)).status_code == 403
    assert api.put(f"{USERS}/{admin.id}/roles", json={"roles": ["viewer"]}, headers=bearer(tokens)).status_code == 403
    assert api.post(f"{USERS}/{super_admin.id}/deactivate", headers=bearer(tokens)).status_code == 403


def test_deactivation_takes_effect_immediately_and_activation_restores_access(
    api: TestClient, factory: Factory, database: Database
) -> None:
    org = factory.organization()
    admin = login(api, factory.user(org, "org_admin"))
    member = factory.user(org, "analyst")
    member_tokens = login(api, member)

    response = api.post(f"{USERS}/{member.id}/deactivate", headers=bearer(admin))
    assert response.status_code == 200
    assert response.json()["is_active"] is False and response.json()["deactivated_at"] is not None

    assert api.get("/api/v1/auth/me", headers=bearer(member_tokens)).status_code == 401
    assert api.post("/api/v1/auth/refresh", json={"refresh_token": member_tokens["refresh_token"]}).status_code == 401
    assert api.post("/api/v1/auth/login", json={"email": member.email, "password": member.password}).status_code == 401

    assert api.post(f"{USERS}/{member.id}/activate", headers=bearer(admin)).json()["is_active"] is True
    login(api, member)

    with database.session() as session:
        actions = set(session.scalars(select(AuditEvent.action)).all())
    assert {"users.deactivate", "users.activate"} <= actions


def test_roles_endpoint_lists_catalog(api: TestClient, factory: Factory) -> None:
    tokens = login(api, factory.user(factory.organization(), "org_admin"))
    roles = {r["code"]: r for r in api.get("/api/v1/roles", headers=bearer(tokens)).json()}
    assert set(roles) == {
        "super_admin",
        "org_admin",
        "operations_manager",
        "support_agent",
        "analyst",
        "developer",
        "viewer",
    }
    assert "platform.admin" in roles["super_admin"]["permissions"]
    assert "platform.admin" not in roles["org_admin"]["permissions"]

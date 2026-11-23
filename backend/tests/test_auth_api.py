"""End-to-end authentication tests against a migrated database (no mocked auth)."""

import uuid
from collections.abc import Callable
from datetime import timedelta

import jwt
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database.session import Database
from app.models import AuditEvent, AuthSession, RefreshToken, User
from app.security.tokens import TokenService, hash_refresh_token
from app.utils.time import utc_now
from tests.helpers import DEFAULT_PASSWORD, TEST_JWT_SECRET, Factory, bearer, login

AUTH = "/api/v1/auth"
GENERIC_LOGIN_ERROR = "Invalid email or password."


def _audit_actions(database: Database) -> list[tuple[str, str, dict]]:
    with database.session() as session:
        events = session.scalars(select(AuditEvent).order_by(AuditEvent.created_at)).all()
        return [(e.action, e.outcome, e.details) for e in events]


# Registration ---------------------------------------------------------------------------


def test_register_creates_organization_admin_and_signs_in(api: TestClient, database: Database) -> None:
    response = api.post(
        f"{AUTH}/register",
        json={
            "organization_name": "Northwind Ops",
            "full_name": "Ada Admin",
            "email": "  Ada@Northwind.io ",
            "password": DEFAULT_PASSWORD,
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["user"]["email"] == "ada@northwind.io"
    assert body["user"]["roles"] == ["org_admin"]
    assert body["user"]["organization"]["slug"] == "northwind-ops"
    assert "users.create" in body["user"]["permissions"]
    assert "platform.admin" not in body["user"]["permissions"]

    me = api.get(f"{AUTH}/me", headers=bearer(body["tokens"]))
    assert me.status_code == 200 and me.json()["id"] == body["user"]["id"]
    assert ("auth.register", "success", {}) in _audit_actions(database)


def test_register_rejects_weak_password_and_duplicate_email(api: TestClient, factory: Factory) -> None:
    payload = {"organization_name": "Org", "full_name": "A", "email": "a@acme.io", "password": "weak"}
    weak = api.post(f"{AUTH}/register", json=payload)
    assert weak.status_code == 422
    assert weak.json()["error"]["code"] == "validation_error"

    factory.user(factory.organization(), "viewer", email="taken@acme.io")
    duplicate = api.post(f"{AUTH}/register", json={**payload, "email": "taken@acme.io", "password": DEFAULT_PASSWORD})
    assert duplicate.status_code == 409


def test_register_can_be_disabled(make_api: Callable[..., TestClient]) -> None:
    api = make_api(allow_public_registration=False)
    response = api.post(
        f"{AUTH}/register",
        json={
            "organization_name": "Org",
            "full_name": "A",
            "email": "a@acme.io",
            "password": DEFAULT_PASSWORD,
        },
    )
    assert response.status_code == 403


# Password storage -------------------------------------------------------------------------


def test_passwords_are_stored_as_argon2_hashes(api: TestClient, factory: Factory, database: Database) -> None:
    user = factory.user(factory.organization(), "viewer")
    with database.session() as session:
        stored = session.get(User, user.id)
        assert stored is not None
        assert stored.password_hash.startswith("$argon2id$")
        assert DEFAULT_PASSWORD not in stored.password_hash


def test_login_upgrades_outdated_password_hash(
    make_api: Callable[..., TestClient], factory: Factory, database: Database
) -> None:
    user = factory.user(factory.organization(), "viewer")
    api = make_api(argon2_time_cost=2)
    login(api, user)
    with database.session() as session:
        stored = session.get(User, user.id)
        assert stored is not None and "t=2" in stored.password_hash


# Login ------------------------------------------------------------------------------------


def test_successful_login_returns_tokens_and_cookie(api: TestClient, factory: Factory, database: Database) -> None:
    user = factory.user(factory.organization(), "analyst", email="ana@acme.io")
    response = api.post(f"{AUTH}/login", json={"email": "ANA@acme.io", "password": DEFAULT_PASSWORD})

    assert response.status_code == 200, response.text
    tokens = response.json()
    assert tokens["token_type"] == "bearer"
    assert tokens["expires_in"] == 15 * 60
    claims = jwt.decode(tokens["access_token"], TEST_JWT_SECRET, algorithms=["HS256"], audience="opspilot")
    assert claims["sub"] == str(user.id) and claims["org"] == str(user.organization_id)

    cookie = response.headers["set-cookie"]
    assert "opspilot_refresh_token=" in cookie
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "Path=/api/v1/auth" in cookie

    me = api.get(f"{AUTH}/me", headers=bearer(tokens))
    assert me.status_code == 200
    assert me.json()["roles"] == ["analyst"]
    assert me.json()["last_login_at"] is not None
    assert ("auth.login", "success", {}) in _audit_actions(database)


def test_refresh_tokens_are_stored_hashed(api: TestClient, factory: Factory, database: Database) -> None:
    tokens = login(api, factory.user(factory.organization(), "viewer"))
    with database.session() as session:
        stored = session.scalars(select(RefreshToken.token_hash)).all()
    assert stored == [hash_refresh_token(tokens["refresh_token"])]
    assert tokens["refresh_token"] not in stored


def test_invalid_credentials_return_one_generic_error(api: TestClient, factory: Factory, database: Database) -> None:
    user = factory.user(factory.organization(), "viewer")
    wrong_password = api.post(f"{AUTH}/login", json={"email": user.email, "password": "Wrong-Password-1"})
    unknown_email = api.post(f"{AUTH}/login", json={"email": "nobody@acme.io", "password": DEFAULT_PASSWORD})
    malformed = api.post(f"{AUTH}/login", json={"email": "not-an-email", "password": DEFAULT_PASSWORD})

    for response in (wrong_password, unknown_email, malformed):
        assert response.status_code == 401
        assert response.json()["error"]["message"] == GENERIC_LOGIN_ERROR
        assert response.headers["www-authenticate"] == "Bearer"

    reasons = [d.get("reason") for action, outcome, d in _audit_actions(database) if outcome == "failure"]
    assert reasons == ["invalid_password", "unknown_email", "unknown_email"]


def test_disabled_account_cannot_log_in(api: TestClient, factory: Factory, database: Database) -> None:
    user = factory.user(factory.organization(), "viewer", active=False)
    response = api.post(f"{AUTH}/login", json={"email": user.email, "password": user.password})
    assert response.status_code == 401
    assert response.json()["error"]["message"] == GENERIC_LOGIN_ERROR
    assert ("auth.login", "failure", {"reason": "account_disabled"}) in _audit_actions(database)


def test_disabled_organization_cannot_log_in(api: TestClient, factory: Factory) -> None:
    user = factory.user(factory.organization(active=False), "org_admin")
    response = api.post(f"{AUTH}/login", json={"email": user.email, "password": user.password})
    assert response.status_code == 401
    assert response.json()["error"]["message"] == GENERIC_LOGIN_ERROR


def test_login_is_rate_limited_per_account(make_api: Callable[..., TestClient], factory: Factory) -> None:
    api = make_api(login_max_failures_per_account=3)
    user = factory.user(factory.organization(), "viewer")
    for _ in range(3):
        assert api.post(f"{AUTH}/login", json={"email": user.email, "password": "Wrong-Pass-123"}).status_code == 401

    blocked = api.post(f"{AUTH}/login", json={"email": user.email, "password": user.password})
    assert blocked.status_code == 429
    assert int(blocked.headers["retry-after"]) > 0
    assert blocked.json()["error"]["code"] == "too_many_requests"


def test_login_is_rate_limited_per_ip(make_api: Callable[..., TestClient], factory: Factory) -> None:
    api = make_api(login_max_attempts_per_ip=2)
    user = factory.user(factory.organization(), "viewer")
    login(api, user)
    login(api, user)
    assert api.post(f"{AUTH}/login", json={"email": user.email, "password": user.password}).status_code == 429


def test_successful_login_resets_account_failures(make_api: Callable[..., TestClient], factory: Factory) -> None:
    api = make_api(login_max_failures_per_account=2)
    user = factory.user(factory.organization(), "viewer")
    api.post(f"{AUTH}/login", json={"email": user.email, "password": "Wrong-Pass-123"})
    login(api, user)
    api.post(f"{AUTH}/login", json={"email": user.email, "password": "Wrong-Pass-123"})
    login(api, user)


# Access tokens ----------------------------------------------------------------------------


def test_missing_and_malformed_tokens_are_rejected(api: TestClient) -> None:
    assert api.get(f"{AUTH}/me").status_code == 401
    assert api.get(f"{AUTH}/me", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert api.get(f"{AUTH}/me", headers={"Authorization": "Basic abc"}).status_code == 401


def test_expired_access_token_is_rejected(api: TestClient, factory: Factory, db_settings) -> None:
    user = factory.user(factory.organization(), "viewer")
    tokens = login(api, user)
    claims = jwt.decode(tokens["access_token"], options={"verify_signature": False})

    past = TokenService.from_settings(db_settings, clock=lambda: utc_now() - timedelta(hours=1))
    expired = past.create_access_token(
        user_id=user.id, organization_id=user.organization_id, session_id=uuid.UUID(claims["sid"])
    )
    response = api.get(f"{AUTH}/me", headers=bearer(expired.token))
    assert response.status_code == 401
    assert response.json()["error"]["message"] == "Invalid or expired access token."


def test_token_with_forged_organization_is_rejected(api: TestClient, factory: Factory) -> None:
    user = factory.user(factory.organization(), "viewer")
    other_org = factory.organization("Other")
    tokens = login(api, user)
    claims = jwt.decode(tokens["access_token"], options={"verify_signature": False})
    # Even a correctly signed token cannot move a user into another tenant.
    forged = jwt.encode({**claims, "org": str(other_org)}, TEST_JWT_SECRET, algorithm="HS256")
    assert api.get(f"{AUTH}/me", headers=bearer(forged)).status_code == 401


# Refresh rotation ---------------------------------------------------------------------------


def test_refresh_rotates_tokens(api: TestClient, factory: Factory) -> None:
    tokens = login(api, factory.user(factory.organization(), "viewer"))
    rotated = api.post(f"{AUTH}/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert rotated.status_code == 200, rotated.text
    new = rotated.json()
    assert new["refresh_token"] != tokens["refresh_token"]
    assert new["access_token"] != tokens["access_token"]
    assert api.get(f"{AUTH}/me", headers=bearer(new)).status_code == 200

    again = api.post(f"{AUTH}/refresh", json={"refresh_token": new["refresh_token"]})
    assert again.status_code == 200


def test_refresh_works_from_http_only_cookie(api: TestClient, factory: Factory) -> None:
    login(api, factory.user(factory.organization(), "viewer"))  # sets the cookie on the client
    assert api.post(f"{AUTH}/refresh").status_code == 200
    assert api.post(f"{AUTH}/refresh").status_code == 200  # cookie was rotated in the response


def test_refresh_token_reuse_revokes_the_whole_session(api: TestClient, factory: Factory, database: Database) -> None:
    tokens = login(api, factory.user(factory.organization(), "viewer"))
    first = tokens["refresh_token"]
    rotated = api.post(f"{AUTH}/refresh", json={"refresh_token": first}).json()

    # An attacker replays the stolen, already-used token.
    replay = api.post(f"{AUTH}/refresh", json={"refresh_token": first})
    assert replay.status_code == 401

    # Everything issued in that session is now dead, including the legitimate client's tokens.
    assert api.post(f"{AUTH}/refresh", json={"refresh_token": rotated["refresh_token"]}).status_code == 401
    assert api.get(f"{AUTH}/me", headers=bearer(rotated)).status_code == 401
    assert api.get(f"{AUTH}/me", headers=bearer(tokens)).status_code == 401

    with database.session() as session:
        auth_session = session.scalars(select(AuthSession)).one()
        assert auth_session.revoked_reason == "refresh_token_reuse"
    assert any(action == "auth.refresh_token_reuse_detected" for action, _, _ in _audit_actions(database))


def test_revoked_refresh_token_is_rejected(api: TestClient, factory: Factory) -> None:
    tokens = login(api, factory.user(factory.organization(), "viewer"))
    assert api.post(f"{AUTH}/logout", json={"refresh_token": tokens["refresh_token"]}).status_code == 204

    response = api.post(f"{AUTH}/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert response.status_code == 401
    assert response.json()["error"]["message"] == "Invalid or expired refresh token."


def test_unknown_and_missing_refresh_tokens_are_rejected(api: TestClient) -> None:
    assert api.post(f"{AUTH}/refresh", json={"refresh_token": "made-up"}).status_code == 401
    assert api.post(f"{AUTH}/refresh").status_code == 401


def test_expired_refresh_token_is_rejected(api: TestClient, factory: Factory, database: Database) -> None:
    tokens = login(api, factory.user(factory.organization(), "viewer"))
    with database.session() as session:
        token = session.scalars(select(RefreshToken)).one()
        token.expires_at = utc_now() - timedelta(seconds=1)
    assert api.post(f"{AUTH}/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code == 401


# Logout -------------------------------------------------------------------------------------


def test_logout_revokes_session_and_access_token(api: TestClient, factory: Factory) -> None:
    tokens = login(api, factory.user(factory.organization(), "viewer"))
    response = api.post(f"{AUTH}/logout", headers=bearer(tokens))
    assert response.status_code == 204
    assert "Max-Age=0" in response.headers["set-cookie"] or "expires=" in response.headers["set-cookie"].lower()

    assert api.get(f"{AUTH}/me", headers=bearer(tokens)).status_code == 401
    assert api.post(f"{AUTH}/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code == 401
    # Idempotent: logging out again with dead credentials still succeeds.
    assert api.post(f"{AUTH}/logout", json={"refresh_token": tokens["refresh_token"]}).status_code == 204


def test_logout_all_ends_every_session(api: TestClient, factory: Factory) -> None:
    user = factory.user(factory.organization(), "viewer")
    laptop, phone = login(api, user), login(api, user)
    assert api.post(f"{AUTH}/logout-all", headers=bearer(laptop)).status_code == 204
    assert api.get(f"{AUTH}/me", headers=bearer(phone)).status_code == 401
    assert api.post(f"{AUTH}/refresh", json={"refresh_token": phone["refresh_token"]}).status_code == 401


def test_auth_endpoints_require_a_database(client: TestClient) -> None:
    response = client.post(f"{AUTH}/login", json={"email": "a@acme.io", "password": "x"})
    assert response.status_code == 503

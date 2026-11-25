"""Authentication endpoints against PostgreSQL: login, tokens, refresh rotation, logout, passwords."""

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.core.security import AccessTokenService, hash_token
from tests.auth.conftest import ADMIN_EMAIL, CSRF, PASSWORD, SECRET_KEY, Api

pytestmark = pytest.mark.postgres

LOGIN = "/api/v1/auth/login"
ME = "/api/v1/auth/me"
GENERIC_FAILURE = "Invalid email or password."


def _assert_generic_login_failure(response) -> None:  # type: ignore[no-untyped-def]
    assert response.status_code == 401
    assert response.json()["error"] == {
        "code": "INVALID_CREDENTIALS",
        "message": GENERIC_FAILURE,
        "request_id": response.json()["error"]["request_id"],
    }
    assert "set-cookie" not in response.headers


# --- login ----------------------------------------------------------------------------------


def test_successful_login(api: Api) -> None:
    user_id = api.create_user("Ada@Example.com".lower(), full_name="Ada Lovelace")

    response = api.login("  ADA@example.COM ", PASSWORD)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 15 * 60
    assert body["user"]["id"] == str(user_id)
    assert body["user"]["email"] == "ada@example.com"
    assert body["user"]["permissions"] == []
    for forbidden in ("password", "password_hash", "refresh_token", "hash"):
        assert forbidden not in body and forbidden not in body["user"]
    assert response.headers["cache-control"] == "no-store"

    cookie = api.refresh_cookie()
    assert cookie and len(cookie) >= 40
    assert cookie not in response.text  # the refresh token is never in the body

    me = api.client.get(ME, headers=api.bearer(body["access_token"]))
    assert me.status_code == 200
    assert me.json()["email"] == "ada@example.com"
    assert me.json()["last_login_at"] is not None
    assert len(api.events("login_succeeded")) == 1


def test_refresh_cookie_attributes(api_factory: Callable[..., Api]) -> None:
    api = api_factory(auth_cookie_secure=True)
    api.create_user("ada@example.com")

    header = api.login("ada@example.com").headers["set-cookie"]

    assert header.startswith("opspilot_refresh=")
    lowered = header.lower()
    for attribute in ("httponly", "secure", "samesite=strict", "path=/api/v1/auth", "max-age="):
        assert attribute in lowered


def test_invalid_password(api: Api) -> None:
    user_id = api.create_user("ada@example.com")

    _assert_generic_login_failure(api.login("ada@example.com", "wrong password entirely"))

    assert api.sql("SELECT failed_login_attempts FROM users WHERE id = :id", id=user_id)[0][0] == 1
    events = api.events("login_failed")
    assert events[0][0] == user_id
    assert events[0][1]["reason"] == "invalid_password"


def test_unknown_account_is_indistinguishable_from_wrong_password(api: Api) -> None:
    api.create_user("ada@example.com")

    unknown = api.login("nobody@example.com", "wrong password entirely")
    wrong = api.login("ada@example.com", "wrong password entirely")
    malformed = api.login("not-an-email", "wrong password entirely")

    for response in (unknown, wrong, malformed):
        _assert_generic_login_failure(response)
    assert unknown.json()["error"]["message"] == wrong.json()["error"]["message"]


def test_inactive_account_cannot_log_in(api: Api) -> None:
    api.create_user("ada@example.com", active=False)

    _assert_generic_login_failure(api.login("ada@example.com"))
    assert api.events("login_failed")[0][1]["reason"] == "account_inactive"


def test_login_validates_input(api: Api) -> None:
    assert api.client.post(LOGIN, json={"email": "a@example.com"}).status_code == 422
    response = api.client.post(LOGIN, json={"email": "a@example.com", "password": "x", "is_superuser": True})
    assert response.status_code == 422


# --- access tokens ------------------------------------------------------------------------------


def test_expired_access_token_is_rejected(api: Api) -> None:
    user_id = api.create_user("ada@example.com")
    api.login("ada@example.com")
    session_id = api.sql("SELECT id FROM user_sessions WHERE user_id = :id", id=user_id)[0][0]
    tokens = AccessTokenService(SECRET_KEY, timedelta(minutes=15))
    expired = tokens.issue(user_id, session_id, now=datetime.now(UTC) - timedelta(hours=1)).token
    fresh = tokens.issue(user_id, session_id).token

    response = api.client.get(ME, headers=api.bearer(expired))

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == "UNAUTHORIZED"
    assert api.client.get(ME, headers=api.bearer(fresh)).status_code == 200


@pytest.mark.parametrize(
    "make_token",
    [
        lambda uid, sid: "not-a-jwt",
        lambda uid, sid: jwt.encode({"sub": str(uid), "sid": str(sid)}, "x" * 40, algorithm="HS256"),
        lambda uid, sid: AccessTokenService("a-different-signing-key" + "y" * 30, timedelta(minutes=5))
        .issue(uid, sid)
        .token,
        lambda uid, sid: jwt.encode(
            {
                "iss": "opspilot",
                "aud": "opspilot-api",
                "typ": "access",
                "sub": str(uid),
                "sid": str(sid),
                "iat": int(datetime.now(UTC).timestamp()),
                "exp": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp()),
            },
            key=None,
            algorithm="none",
        ),
        lambda uid, sid: AccessTokenService(SECRET_KEY, timedelta(minutes=5)).issue(uid, uuid.uuid4()).token,
    ],
    ids=["garbage", "missing-claims", "wrong-key", "alg-none", "unknown-session"],
)
def test_invalid_access_tokens_are_rejected(api: Api, make_token: Callable[[uuid.UUID, uuid.UUID], str]) -> None:
    user_id = api.create_user("ada@example.com")
    api.login("ada@example.com")
    session_id = api.sql("SELECT id FROM user_sessions WHERE user_id = :id", id=user_id)[0][0]

    response = api.client.get(ME, headers=api.bearer(make_token(user_id, session_id)))

    assert response.status_code == 401


def test_missing_or_non_bearer_authorization_is_rejected(api: Api) -> None:
    assert api.client.get(ME).status_code == 401
    assert api.client.get(ME, headers={"Authorization": "Basic YWRhOnB3"}).status_code == 401


# --- refresh and rotation --------------------------------------------------------------------------


def test_successful_refresh_issues_a_working_access_token(api: Api) -> None:
    api.create_user("ada@example.com")
    api.login("ada@example.com")

    response = api.refresh()

    assert response.status_code == 200, response.text
    assert api.client.get(ME, headers=api.bearer(response.json()["access_token"])).status_code == 200


def test_refresh_rotates_the_token_and_stores_only_digests(api: Api) -> None:
    api.create_user("ada@example.com")
    api.login("ada@example.com")
    first = api.refresh_cookie()
    assert first

    assert api.refresh().status_code == 200
    second = api.refresh_cookie()

    assert second and second != first
    rows = api.sql("SELECT refresh_token_hash FROM user_sessions")
    assert rows == [(hash_token(second),)]
    assert api.sql("SELECT token_hash FROM retired_refresh_tokens") == [(hash_token(first),)]
    dump = str(api.sql("SELECT * FROM user_sessions")) + str(api.sql("SELECT * FROM retired_refresh_tokens"))
    assert first not in dump and second not in dump


def test_reuse_of_a_rotated_refresh_token_revokes_the_session(api_factory: Callable[..., Api]) -> None:
    api = api_factory(refresh_reuse_grace_seconds=0)
    api.create_user("ada@example.com")
    access = api.login("ada@example.com").json()["access_token"]
    stolen = api.refresh_cookie()
    assert api.refresh().status_code == 200
    current = api.refresh_cookie()

    replay = api.refresh(cookie=stolen)

    assert replay.status_code == 401
    assert 'opspilot_refresh=""' in replay.headers["set-cookie"] or "Max-Age=0" in replay.headers["set-cookie"]
    assert api.sql("SELECT revoked_reason FROM user_sessions") == [("token_reuse",)]
    assert len(api.events("refresh_token_reuse")) == 1
    # The legitimate holder's newer token and access token are dead too.
    assert api.refresh(cookie=current).status_code == 401
    assert api.client.get(ME, headers=api.bearer(access)).status_code == 401


def test_concurrent_refresh_within_grace_period_is_a_conflict_not_reuse(api: Api) -> None:
    api.create_user("ada@example.com")
    api.login("ada@example.com")
    old = api.refresh_cookie()
    assert api.refresh().status_code == 200

    response = api.refresh(cookie=old)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "REFRESH_CONFLICT"
    assert api.sql("SELECT revoked_at FROM user_sessions") == [(None,)]


def test_refresh_rejects_unknown_expired_and_missing_tokens(api: Api) -> None:
    api.create_user("ada@example.com")
    api.login("ada@example.com")

    assert api.refresh(cookie="made-up-token-" + "z" * 30).status_code == 401
    api.clear_cookies()
    assert api.client.post("/api/v1/auth/refresh", headers=CSRF).status_code == 401

    api.login("ada@example.com")
    api.sql("UPDATE user_sessions SET expires_at = now() - interval '1 second'")
    assert api.refresh().status_code == 401


def test_refresh_requires_csrf_header_and_allowed_origin(api: Api) -> None:
    api.create_user("ada@example.com")
    api.login("ada@example.com")

    no_header = api.client.post("/api/v1/auth/refresh")
    evil_origin = api.refresh(Origin="https://evil.example")
    evil_referer = api.client.post(
        "/api/v1/auth/refresh", headers={**CSRF, "Referer": "https://evil.example/page"}
    )
    allowed = api.refresh(Origin="http://localhost:5173")

    for response in (no_header, evil_origin, evil_referer):
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "CSRF_FAILED"
    assert allowed.status_code == 200


# --- logout -------------------------------------------------------------------------------------------


def test_logout_revokes_the_current_session(api: Api) -> None:
    api.create_user("ada@example.com")
    access = api.login("ada@example.com").json()["access_token"]
    cookie = api.refresh_cookie()

    response = api.client.post("/api/v1/auth/logout", headers=CSRF)

    assert response.status_code == 204
    assert api.refresh_cookie() is None
    assert api.client.get(ME, headers=api.bearer(access)).status_code == 401
    assert api.refresh(cookie=cookie).status_code == 401
    assert api.sql("SELECT revoked_reason FROM user_sessions") == [("logout",)]
    assert len(api.events("logout")) == 1
    # Logging out again (or without a cookie) is harmless.
    assert api.client.post("/api/v1/auth/logout", headers=CSRF).status_code == 204


def test_logout_requires_csrf_header(api: Api) -> None:
    api.create_user("ada@example.com")
    api.login("ada@example.com")

    assert api.client.post("/api/v1/auth/logout").status_code == 403
    assert api.sql("SELECT revoked_at FROM user_sessions") == [(None,)]


def test_logout_all_revokes_every_session(api: Api) -> None:
    api.create_user("ada@example.com")
    first = api.login("ada@example.com").json()["access_token"]
    first_cookie = api.refresh_cookie()
    api.clear_cookies()
    second = api.login("ada@example.com").json()["access_token"]

    response = api.client.post("/api/v1/auth/logout-all", headers=api.bearer(second))

    assert response.status_code == 204
    for token in (first, second):
        assert api.client.get(ME, headers=api.bearer(token)).status_code == 401
    assert api.refresh(cookie=first_cookie).status_code == 401
    assert api.sql("SELECT count(*) FROM user_sessions WHERE revoked_at IS NULL") == [(0,)]
    assert api.client.post("/api/v1/auth/logout-all").status_code == 401


# --- passwords ------------------------------------------------------------------------------------------


def test_change_password(api: Api) -> None:
    api.create_user("ada@example.com")
    other_session = api.token_for("ada@example.com")
    api.clear_cookies()
    current = api.token_for("ada@example.com")
    new_password = "a brand new passphrase 42"

    wrong = api.client.post(
        "/api/v1/auth/change-password",
        json={"current_password": "not my password at all", "new_password": new_password},
        headers=api.bearer(current),
    )
    weak = api.client.post(
        "/api/v1/auth/change-password",
        json={"current_password": PASSWORD, "new_password": "short"},
        headers=api.bearer(current),
    )
    changed = api.client.post(
        "/api/v1/auth/change-password",
        json={"current_password": PASSWORD, "new_password": new_password},
        headers=api.bearer(current),
    )

    assert wrong.status_code == 422
    assert weak.status_code == 422
    assert "12 characters" in weak.json()["error"]["message"]
    assert changed.status_code == 200, changed.text
    # Every earlier session ends; the response carries a fresh session for this browser.
    assert api.client.get(ME, headers=api.bearer(other_session)).status_code == 401
    assert api.client.get(ME, headers=api.bearer(current)).status_code == 401
    assert api.client.get(ME, headers=api.bearer(changed.json()["access_token"])).status_code == 200
    _assert_generic_login_failure(api.login("ada@example.com", PASSWORD))
    assert api.login("ada@example.com", new_password).status_code == 200
    assert len(api.events("password_changed")) == 1


def test_password_reset_flow(api: Api) -> None:
    user_id = api.create_user("ada@example.com")
    old_access = api.token_for("ada@example.com")
    new_password = "reset passphrase works 7"

    requested = api.client.post("/api/v1/auth/forgot-password", json={"email": "ADA@example.com"})
    unknown = api.client.post("/api/v1/auth/forgot-password", json={"email": "nobody@example.com"})

    assert requested.status_code == unknown.status_code == 202
    assert requested.json() == unknown.json()
    assert len(api.notifier.sent) == 1
    assert api.notifier.sent[0][0] == user_id
    token = api.notifier.last_token()
    assert token not in requested.text
    assert api.sql("SELECT token_hash FROM password_reset_tokens") == [(hash_token(token),)]

    reset = api.client.post("/api/v1/auth/reset-password", json={"token": token, "new_password": new_password})

    assert reset.status_code == 200, reset.text
    assert api.client.get(ME, headers=api.bearer(old_access)).status_code == 401
    _assert_generic_login_failure(api.login("ada@example.com", PASSWORD))
    assert api.login("ada@example.com", new_password).status_code == 200
    assert len(api.events("password_reset_completed")) == 1


def test_expired_reset_token_is_rejected(api: Api) -> None:
    api.create_user("ada@example.com")
    api.client.post("/api/v1/auth/forgot-password", json={"email": "ada@example.com"})
    api.sql("UPDATE password_reset_tokens SET expires_at = now() - interval '1 second'")

    response = api.client.post(
        "/api/v1/auth/reset-password", json={"token": api.notifier.last_token(), "new_password": "another passphrase 1"}
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_RESET_TOKEN"
    assert api.login("ada@example.com").status_code == 200


def test_reset_token_is_single_use_and_superseded_by_newer_ones(api: Api) -> None:
    api.create_user("ada@example.com")
    api.client.post("/api/v1/auth/forgot-password", json={"email": "ada@example.com"})
    first = api.notifier.last_token()
    api.client.post("/api/v1/auth/forgot-password", json={"email": "ada@example.com"})
    second = api.notifier.last_token()

    superseded = api.client.post("/api/v1/auth/reset-password", json={"token": first, "new_password": "first new pass 123"})
    used = api.client.post("/api/v1/auth/reset-password", json={"token": second, "new_password": "second new pass 123"})
    reused = api.client.post("/api/v1/auth/reset-password", json={"token": second, "new_password": "third new pass 1234"})

    assert superseded.status_code == 400
    assert used.status_code == 200
    assert reused.status_code == 400
    assert api.login("ada@example.com", "second new pass 123").status_code == 200


def test_reset_with_weak_password_does_not_consume_the_token(api: Api) -> None:
    api.create_user("ada@example.com")
    api.client.post("/api/v1/auth/forgot-password", json={"email": "ada@example.com"})
    token = api.notifier.last_token()

    weak = api.client.post("/api/v1/auth/reset-password", json={"token": token, "new_password": "password1234"})
    ok = api.client.post("/api/v1/auth/reset-password", json={"token": token, "new_password": "a strong new passphrase"})

    assert weak.status_code == 422
    assert ok.status_code == 200


# --- brute force and rate limiting -------------------------------------------------------------------------


def test_repeated_failures_lock_the_account_temporarily(api_factory: Callable[..., Api]) -> None:
    api = api_factory(login_max_failed_attempts=3, login_lockout_base_seconds=60)
    user_id = api.create_user("ada@example.com")

    for _ in range(3):
        _assert_generic_login_failure(api.login("ada@example.com", "wrong password entirely"))
    locked_until = api.sql("SELECT locked_until FROM users WHERE id = :id", id=user_id)[0][0]
    assert locked_until is not None and locked_until > datetime.now(UTC) + timedelta(seconds=50)

    # Even the right password fails while locked, with the same generic message.
    _assert_generic_login_failure(api.login("ada@example.com", PASSWORD))
    assert api.events("login_failed")[-1][1]["reason"] == "account_locked"

    # The lock expires by itself (simulated); a success resets the counter.
    api.sql("UPDATE users SET locked_until = now() - interval '1 second'")
    assert api.login("ada@example.com", PASSWORD).status_code == 200
    assert api.sql("SELECT failed_login_attempts, locked_until FROM users") == [(0, None)]


def test_lockout_grows_progressively_and_is_capped(api_factory: Callable[..., Api]) -> None:
    api = api_factory(login_max_failed_attempts=1, login_lockout_base_seconds=60, login_lockout_max_seconds=100)
    api.create_user("ada@example.com")

    durations = []
    for _ in range(3):
        api.login("ada@example.com", "wrong password entirely")
        locked_until = api.sql("SELECT locked_until FROM users")[0][0]
        durations.append((locked_until - datetime.now(UTC)).total_seconds())
        api.sql("UPDATE users SET locked_until = now() - interval '1 second'")

    assert 50 < durations[0] <= 60
    assert 90 < durations[1] <= 100  # 120 capped to 100
    assert 90 < durations[2] <= 100


def test_login_is_rate_limited_per_ip(api_factory: Callable[..., Api]) -> None:
    api = api_factory(auth_rate_limit_per_ip=3)
    api.create_user("ada@example.com")

    statuses = [api.login(f"user{i}@example.com", "wrong password entirely").status_code for i in range(3)]
    limited = api.login("ada@example.com")

    assert statuses == [401, 401, 401]
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "TOO_MANY_REQUESTS"
    assert int(limited.headers["retry-after"]) >= 1


def test_login_is_rate_limited_per_account(api_factory: Callable[..., Api]) -> None:
    api = api_factory(auth_rate_limit_per_account=2, login_max_failed_attempts=100)
    api.create_user("ada@example.com")

    for _ in range(2):
        api.login("ada@example.com", "wrong password entirely")

    assert api.login("ada@example.com").status_code == 429
    assert api.login("someone.else@example.com", "wrong password entirely").status_code == 401


def test_forgot_password_is_rate_limited(api_factory: Callable[..., Api]) -> None:
    api = api_factory(auth_rate_limit_per_account=2)

    statuses = [
        api.client.post("/api/v1/auth/forgot-password", json={"email": "x@example.com"}).status_code for _ in range(3)
    ]

    assert statuses == [202, 202, 429]


# --- account status ------------------------------------------------------------------------------------------


def test_deactivated_user_sessions_stop_working_immediately(api: Api, admin_token: str) -> None:
    user_id = api.create_user("ada@example.com")
    access = api.token_for("ada@example.com")
    cookie = api.refresh_cookie()

    deactivated = api.client.post(f"/api/v1/users/{user_id}/deactivate", headers=api.bearer(admin_token))

    assert deactivated.status_code == 200
    assert api.client.get(ME, headers=api.bearer(access)).status_code == 401
    assert api.refresh(cookie=cookie).status_code == 401
    _assert_generic_login_failure(api.login("ada@example.com"))


def test_user_deactivated_directly_in_the_database_loses_access(api: Api) -> None:
    """Status is re-checked on every request, not only when sessions are revoked."""
    user_id = api.create_user("ada@example.com")
    access = api.token_for("ada@example.com")

    api.sql("UPDATE users SET is_active = false WHERE id = :id", id=user_id)

    assert api.client.get(ME, headers=api.bearer(access)).status_code == 401
    assert api.refresh().status_code == 401


def test_admin_has_permissions_and_secrets_are_never_logged(api: Api, caplog: pytest.LogCaptureFixture) -> None:
    api.create_user(ADMIN_EMAIL, superuser=True)
    caplog.set_level("DEBUG")

    response = api.login(ADMIN_EMAIL)
    api.login(ADMIN_EMAIL, "an incorrect password typed")
    api.refresh()

    assert set(response.json()["user"]["permissions"]) >= {"users:manage", "users:read"}
    logged = caplog.text + str([record.__dict__ for record in caplog.records])
    for secret in (PASSWORD, "an incorrect password typed", response.json()["access_token"], api.refresh_cookie() or "?"):
        assert secret not in logged

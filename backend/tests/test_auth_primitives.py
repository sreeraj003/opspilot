"""Tokens, rate limiting, authorization policy, CSRF and auth configuration (no database)."""

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.authorization import BootstrapAuthorizationPolicy, Permission
from app.core.rate_limit import InMemoryRateLimiter
from app.core.security import (
    AccessTokenService,
    InvalidAccessTokenError,
    generate_opaque_token,
    hash_token,
)
from app.models.user import User
from tests.conftest import make_settings

KEY = "k" * 64


def test_opaque_tokens_are_random_and_hashed() -> None:
    tokens = {generate_opaque_token() for _ in range(100)}

    assert len(tokens) == 100
    token = next(iter(tokens))
    assert len(token) >= 43
    assert hash_token(token) == hash_token(token)
    assert len(hash_token(token)) == 64 and token not in hash_token(token)


def test_access_token_round_trip() -> None:
    service = AccessTokenService(KEY, timedelta(minutes=10))
    user_id, session_id = uuid.uuid4(), uuid.uuid4()

    issued = service.issue(user_id, session_id)
    claims = service.verify(issued.token)

    assert (claims.user_id, claims.session_id) == (user_id, session_id)
    assert issued.expires_in == 600
    payload = jwt.decode(issued.token, options={"verify_signature": False})
    assert set(payload) == {"iss", "aud", "typ", "sub", "sid", "jti", "iat", "exp"}
    assert "email" not in payload and "is_superuser" not in payload  # no privilege claims to trust


@pytest.mark.parametrize(
    "token",
    [
        AccessTokenService(KEY, timedelta(minutes=-5)).issue(uuid.uuid4(), uuid.uuid4()).token,
        AccessTokenService("z" * 40, timedelta(minutes=5)).issue(uuid.uuid4(), uuid.uuid4()).token,
        jwt.encode({"typ": "refresh", "sub": str(uuid.uuid4()), "sid": str(uuid.uuid4()), "iss": "opspilot",
                    "aud": "opspilot-api", "iat": 0, "exp": 2**31}, KEY, algorithm="HS256"),
        jwt.encode({"typ": "access", "sub": "x", "sid": "y", "iss": "opspilot", "aud": "opspilot-api", "iat": 0,
                    "exp": 2**31}, KEY, algorithm="HS256"),
        jwt.encode({"typ": "access", "sub": str(uuid.uuid4()), "sid": str(uuid.uuid4()), "iss": "opspilot",
                    "aud": "other", "iat": 0, "exp": 2**31}, KEY, algorithm="HS256"),
        jwt.encode({"typ": "access", "sub": str(uuid.uuid4()), "sid": str(uuid.uuid4()), "iss": "opspilot",
                    "aud": "opspilot-api", "iat": 0, "exp": 2**31}, KEY, algorithm="HS512"),
        "",
        "a.b.c",
    ],
    ids=["expired", "wrong-key", "wrong-type", "bad-ids", "wrong-audience", "wrong-algorithm", "empty", "garbage"],
)
def test_invalid_access_tokens(token: str) -> None:
    with pytest.raises(InvalidAccessTokenError):
        AccessTokenService(KEY, timedelta(minutes=5)).verify(token)


def test_development_without_secret_key_uses_a_temporary_key() -> None:
    first = AccessTokenService.from_settings(make_settings())
    second = AccessTokenService.from_settings(make_settings())
    token = first.issue(uuid.uuid4(), uuid.uuid4()).token

    with pytest.raises(InvalidAccessTokenError):
        second.verify(token)


def test_rate_limiter_sliding_window() -> None:
    now = [1000.0]
    limiter = InMemoryRateLimiter(clock=lambda: now[0])

    results = [limiter.hit("k", limit=3, window_seconds=60).allowed for _ in range(4)]
    assert results == [True, True, True, False]
    blocked = limiter.hit("k", limit=3, window_seconds=60)
    assert 1 <= blocked.retry_after_seconds <= 61
    assert limiter.hit("other", limit=3, window_seconds=60).allowed

    now[0] += 61
    assert limiter.hit("k", limit=3, window_seconds=60).allowed
    limiter.reset("k")
    assert all(limiter.hit("k", limit=3, window_seconds=60).allowed for _ in range(3))


def test_rate_limiter_memory_is_bounded() -> None:
    limiter = InMemoryRateLimiter(max_keys=10)
    for index in range(100):
        limiter.hit(f"key-{index}", limit=1, window_seconds=60)

    assert len(limiter._hits) == 10  # noqa: SLF001


def _user(**values: object) -> User:
    defaults: dict[str, object] = {"is_active": True, "is_superuser": False}
    defaults.update(values)
    return User(email="u@example.com", full_name="U", password_hash="x", **defaults)


def test_bootstrap_policy() -> None:
    policy = BootstrapAuthorizationPolicy()

    assert policy.permissions_for(_user(is_superuser=True)) == frozenset(Permission)
    assert policy.permissions_for(_user()) == frozenset()
    assert policy.permissions_for(_user(is_superuser=True, is_active=False)) == frozenset()
    assert not policy.is_allowed(_user(), Permission.USERS_MANAGE)


def test_cookie_security_defaults() -> None:
    assert make_settings(app_env="development").cookie_secure is False
    assert make_settings(app_env="staging").cookie_secure is True
    assert make_settings(app_env="development", auth_cookie_secure=True).cookie_secure is True
    production = make_settings(app_env="production", secret_key=KEY, database_url="postgresql://u:p@h/db")
    assert production.cookie_secure is True


def test_production_refuses_insecure_cookies() -> None:
    with pytest.raises(ValidationError, match="AUTH_COOKIE_SECURE"):
        make_settings(
            app_env="production", secret_key=KEY, database_url="postgresql://u:p@h/db", auth_cookie_secure=False
        )


def test_reset_links_use_the_frontend_url() -> None:
    assert make_settings().public_frontend_url == "http://localhost:5173"
    assert make_settings(frontend_base_url="https://bi.example.com/").public_frontend_url == "https://bi.example.com"
    with pytest.raises(ValidationError):
        make_settings(frontend_base_url="bi.example.com")


def test_cors_allows_credentials_for_configured_origins_only(client: TestClient) -> None:
    allowed = client.options(
        "/api/v1/auth/refresh",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "x-opspilot-csrf",
        },
    )
    denied = client.options(
        "/api/v1/auth/refresh",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "x-opspilot-csrf",
        },
    )

    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-credentials"] == "true"
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "access-control-allow-origin" not in denied.headers


def test_refresh_without_database_still_enforces_csrf(client: TestClient) -> None:
    assert client.post("/api/v1/auth/refresh").status_code == 403


def test_auth_endpoints_are_documented(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]

    for path in ("login", "refresh", "logout", "logout-all", "change-password", "forgot-password", "reset-password"):
        assert "post" in paths[f"/api/v1/auth/{path}"]
    assert "get" in paths["/api/v1/auth/me"]
    assert set(paths["/api/v1/users"]) == {"get", "post"}
    assert set(paths["/api/v1/users/{user_id}"]) == {"get", "patch", "delete"}


def test_unused_now_helpers_are_timezone_aware() -> None:
    from app.services.auth_service import utcnow

    assert utcnow().tzinfo is not None
    assert abs(utcnow() - datetime.now(UTC)) < timedelta(seconds=5)

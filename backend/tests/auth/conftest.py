"""Fixtures for authentication and user-management tests against the test database.

* Argon2 runs with minimal cost parameters here so the suite stays fast; the
  production parameters are asserted separately in ``test_passwords.py``.
* Password-reset links are captured by ``CapturingNotifier`` (the test-only
  delivery channel) instead of being returned by any API.
* Rate limits are set high by default; tests that exercise them lower them.
"""

import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import text

from app.authorization.system_catalog import ensure_system_rbac
from app.main import create_app
from app.models.user import User
from app.repositories.user_repository import UserRepository
from app.services.password_service import PasswordService
from tests.conftest import make_settings

pytestmark = pytest.mark.postgres

SECRET_KEY = "test-signing-key-" + "x" * 32
PASSWORD = "correct horse battery staple"
ADMIN_EMAIL = "admin@example.com"
CSRF = {"X-OpsPilot-CSRF": "1"}
AUTH_TABLES = (
    "auth_events, retired_refresh_tokens, user_sessions, password_reset_tokens, users, "
    "rbac_audit_events, user_permissions, user_roles, role_permissions, roles, permissions, "
    "sql_generation_events, sql_generations, business_intents, "
    "connector_audit_events, external_api_connectors, ai_usage_records, question_understanding_events, business_terms, "
    "schema_change_events, schema_relationships, schema_columns, schema_tables, schema_sources"
)

FAST_PASSWORDS = PasswordService(PasswordHasher(time_cost=1, memory_cost=1024, parallelism=1))


@dataclass
class CapturingNotifier:
    """Test-only reset delivery: records the links it would have sent."""

    sent: list[tuple[uuid.UUID, str]] = field(default_factory=list)

    async def send(self, user: User, reset_url: str, expires_at: datetime) -> bool:
        self.sent.append((user.id, reset_url))
        return True

    def last_token(self) -> str:
        return self.sent[-1][1].split("#token=", 1)[1]


class Api:
    """A TestClient plus helpers for common auth flows."""

    def __init__(self, client: TestClient, notifier: CapturingNotifier) -> None:
        self.client = client
        self.notifier = notifier

    def run(self, fn: Callable[..., Any], *args: Any) -> Any:
        return self.client.portal.call(fn, *args)  # type: ignore[union-attr]

    def sql(self, statement: str, **params: Any) -> list[Any]:
        async def execute() -> list[Any]:
            async with self.client.app.state.database.session() as session:  # type: ignore[attr-defined]
                result = await session.execute(text(statement), params)
                rows = list(result.all()) if result.returns_rows else []
                await session.commit()
                return rows

        return self.run(execute)

    def create_user(
        self,
        email: str,
        password: str = PASSWORD,
        *,
        full_name: str = "Test User",
        superuser: bool = False,
        active: bool = True,
        username: str | None = None,
    ) -> uuid.UUID:
        async def create() -> uuid.UUID:
            async with self.client.app.state.database.session() as session:  # type: ignore[attr-defined]
                user = await UserRepository(session).create(
                    email=email,
                    username=username,
                    full_name=full_name,
                    password_hash=FAST_PASSWORDS.hash_unchecked(password),
                    is_active=active,
                    is_superuser=superuser,
                )
                await session.commit()
                return user.id

        return self.run(create)

    def reseed_system_rbac(self) -> None:
        """Restore what migration 0003 seeds (built-in permissions, Administrator role)."""

        async def seed() -> None:
            async with self.client.app.state.database.session() as session:  # type: ignore[attr-defined]
                await ensure_system_rbac(session)

        self.run(seed)

    def login(self, email: str, password: str = PASSWORD) -> Response:
        return self.client.post("/api/v1/auth/login", json={"email": email, "password": password})

    def token_for(self, email: str, password: str = PASSWORD) -> str:
        response = self.login(email, password)
        assert response.status_code == 200, response.text
        return response.json()["access_token"]

    def refresh(self, cookie: str | None = None, **headers: str) -> Response:
        if cookie is not None:
            self.client.cookies.set("opspilot_refresh", cookie, path="/api/v1/auth")
        return self.client.post("/api/v1/auth/refresh", headers={**CSRF, **headers})

    def refresh_cookie(self) -> str | None:
        return self.client.cookies.get("opspilot_refresh")

    def clear_cookies(self) -> None:
        self.client.cookies.clear()

    @staticmethod
    def bearer(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def events(self, event_type: str) -> list[Any]:
        return self.sql("SELECT user_id, details FROM auth_events WHERE event_type = :t ORDER BY created_at", t=event_type)


@pytest.fixture
def api_factory(client_factory: Callable[..., TestClient], migrated_database: str) -> Iterator[Callable[..., Api]]:
    created: list[Api] = []

    def factory(**overrides: Any) -> Api:
        values: dict[str, Any] = {
            "database_url": migrated_database,
            "secret_key": SECRET_KEY,
            "auth_rate_limit_per_ip": 10_000,
            "auth_rate_limit_per_account": 10_000,
            "auth_refresh_rate_limit_per_ip": 10_000,
        }
        prepare = overrides.pop("prepare", None)
        values.update(overrides)
        app = create_app(make_settings(**values))
        if prepare is not None:  # adjust app.state before startup (e.g. a mock connector transport)
            prepare(app)
        notifier = CapturingNotifier()
        app.state.password_service = FAST_PASSWORDS
        app.state.password_reset_notifier = notifier
        api = Api(client_factory(app), notifier)
        created.append(api)
        return api

    yield factory
    if created:
        created[0].sql(f"TRUNCATE {AUTH_TABLES} CASCADE")
        created[0].reseed_system_rbac()


@pytest.fixture
def api(api_factory: Callable[..., Api]) -> Api:
    return api_factory()


@pytest.fixture
def admin_token(api: Api) -> str:
    api.create_user(ADMIN_EMAIL, full_name="Installation Admin", superuser=True)
    token = api.token_for(ADMIN_EMAIL)
    api.clear_cookies()
    return token

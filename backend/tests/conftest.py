"""Shared fixtures.

Tests build settings explicitly (``_env_file=None``) so a developer's local
``.env`` never changes test behaviour.

Database tests run against a **separate, disposable** PostgreSQL database named
by ``TEST_DATABASE_URL`` (environment variable, or the root/backend ``.env``).
They are marked ``postgres`` and skipped when it is not set. Because they drop
and recreate tables, the URL must name a database whose name contains "test"
and must differ from ``DATABASE_URL``; otherwise the run stops.
"""

import asyncio
import sys
from collections.abc import Callable, Iterator
from functools import cache
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

from app.api.deps import get_current_principal
from app.core.config import Settings
from app.main import create_app
from app.models.user import User
from app.services.auth_service import Principal

BACKEND_DIR = Path(__file__).resolve().parents[1]

# A PostgreSQL URL on a port where nothing listens: exercises the "unavailable" path.
UNREACHABLE_DATABASE_URL = "postgresql+psycopg://opspilot:not-a-real-password@127.0.0.1:1/opspilot"

# psycopg's async mode cannot use Windows' default ProactorEventLoop.
ON_WINDOWS = sys.platform == "win32"
TEST_CLIENT_BACKEND_OPTIONS: dict[str, Any] = {"loop_factory": asyncio.SelectorEventLoop} if ON_WINDOWS else {}

if ON_WINDOWS:

    def pytest_asyncio_loop_factories(config: pytest.Config, item: pytest.Item) -> dict[str, Any]:
        return {"selector": asyncio.SelectorEventLoop}


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {"app_env": "test", "log_level": "WARNING"}
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


class _TestEnvironment(BaseSettings):
    """Reads only the test-database variables (environment first, then .env files)."""

    model_config = SettingsConfigDict(
        env_file=(BACKEND_DIR.parent / ".env", BACKEND_DIR / ".env"), extra="ignore", hide_input_in_errors=True
    )

    test_database_url: SecretStr | None = None
    database_url: SecretStr | None = None


def _same_database(first: str, second: str) -> bool:
    a, b = make_url(first), make_url(second)
    return (a.host or "localhost", a.port or 5432, a.database) == (b.host or "localhost", b.port or 5432, b.database)


@cache
def get_test_database_url() -> str | None:
    """Normalised TEST_DATABASE_URL, or None when unset. Refuses anything that is not disposable."""
    environment = _TestEnvironment()
    raw = environment.test_database_url.get_secret_value().strip() if environment.test_database_url else ""
    if not raw:
        return None
    url = make_settings(database_url=raw).database_url.get_secret_value()  # type: ignore[union-attr]
    database_name = make_url(url).database or ""
    if "test" not in database_name.lower():
        pytest.exit(
            "TEST_DATABASE_URL must name a disposable database whose name contains 'test' "
            "(database tests drop and recreate tables).",
            returncode=4,
        )
    configured = environment.database_url.get_secret_value().strip() if environment.database_url else ""
    if configured and _same_database(url, make_settings(database_url=configured).database_url.get_secret_value()):  # type: ignore[union-attr]
        pytest.exit("TEST_DATABASE_URL must not point at the same database as DATABASE_URL.", returncode=4)
    return url


def alembic_config(database_url: str) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.attributes["database_url"] = database_url
    # Keep pytest's log capture intact (alembic.ini would reconfigure the root logger).
    config.attributes["configure_logger"] = False
    return config


@pytest.fixture
def settings_factory() -> Callable[..., Settings]:
    return make_settings


@pytest.fixture
def client_factory() -> Iterator[Callable[..., TestClient]]:
    """Create a TestClient (with lifespan) for an app built from the given settings."""
    clients: list[TestClient] = []

    def factory(app: FastAPI | None = None, *, raise_server_exceptions: bool = True, **overrides: Any) -> TestClient:
        application = app or create_app(make_settings(**overrides))
        client = TestClient(
            application, raise_server_exceptions=raise_server_exceptions, backend_options=TEST_CLIENT_BACKEND_OPTIONS
        )
        client.__enter__()
        clients.append(client)
        return client

    yield factory
    for client in clients:
        client.__exit__(None, None, None)


def act_as_installation_admin(app: FastAPI) -> FastAPI:
    """Bypass authentication for tests of endpoints whose subject is not authentication itself.

    Only ``get_current_principal`` is replaced: the authorization policy still
    runs for real against this (unsaved) administrator account.
    """
    import uuid

    admin = User(
        id=uuid.uuid4(), email="admin@example.com", full_name="Test Admin", password_hash="x",
        is_active=True, is_superuser=True,
    )

    async def principal() -> Principal:
        return Principal(user=admin, session_id=uuid.uuid4())

    app.dependency_overrides[get_current_principal] = principal
    return app


@pytest.fixture
def admin_client_factory(client_factory: Callable[..., TestClient]) -> Callable[..., TestClient]:
    """Like ``client_factory``, but requests are made as the installation administrator."""

    def factory(*, raise_server_exceptions: bool = True, **overrides: Any) -> TestClient:
        app = act_as_installation_admin(create_app(make_settings(**overrides)))
        return client_factory(app, raise_server_exceptions=raise_server_exceptions)

    return factory


@pytest.fixture
def client(client_factory: Callable[..., TestClient]) -> TestClient:
    return client_factory()


@pytest.fixture(scope="session")
def postgres_url() -> str:
    url = get_test_database_url()
    if url is None:
        pytest.skip("TEST_DATABASE_URL is not set; PostgreSQL test skipped")
    return url


@pytest.fixture(scope="session")
def migrated_database(postgres_url: str) -> str:
    """The test database rebuilt from scratch to the latest migration, once per run."""
    config = alembic_config(postgres_url)
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    return postgres_url

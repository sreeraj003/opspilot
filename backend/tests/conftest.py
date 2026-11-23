"""Shared fixtures. Tests build isolated apps from explicit settings and never read a local .env.

Database-backed tests run against a real database migrated with Alembic. By
default that is a temporary SQLite file; set TEST_DATABASE_URL to run the same
tests against PostgreSQL, for example:

    TEST_DATABASE_URL=postgresql+psycopg://postgres@localhost:5432/opspilot_test pytest

The TEST_DATABASE_URL database is wiped (downgraded to base and re-migrated) at
the start of the run, so never point it at a database you care about.
"""

import os
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.core.config import Settings
from app.database.session import Database
from app.main import create_app
from app.models import AuditEvent, AuthSession, Organization, RefreshToken, User, user_roles
from app.security.passwords import PasswordHasher
from tests.helpers import FAST_ARGON2, TEST_JWT_SECRET, Factory

BACKEND_DIR = Path(__file__).resolve().parent.parent


@pytest.fixture
def make_settings() -> Callable[..., Settings]:
    def _make(**overrides: Any) -> Settings:
        values: dict[str, Any] = {"environment": "test", "log_format": "console", "database_url": None}
        values.update(overrides)
        return Settings(_env_file=None, **values)

    return _make


@pytest.fixture
def make_client(make_settings: Callable[..., Settings]) -> Iterator[Callable[..., TestClient]]:
    clients: list[TestClient] = []

    def _make(app: FastAPI | None = None, **overrides: Any) -> TestClient:
        client = TestClient(app or create_app(make_settings(**overrides)), raise_server_exceptions=False)
        client.__enter__()  # runs the lifespan
        clients.append(client)
        return client

    yield _make
    for client in clients:
        client.__exit__(None, None, None)


@pytest.fixture
def client(make_client: Callable[..., TestClient]) -> TestClient:
    return make_client()


# Database-backed fixtures --------------------------------------------------------------


def alembic_config(database_url: str) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    config.attributes["database_url"] = database_url
    config.attributes["configure_logging"] = False
    return config


@pytest.fixture(scope="session")
def database_url(tmp_path_factory: pytest.TempPathFactory) -> str:
    url = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{tmp_path_factory.mktemp('db') / 'opspilot.db'}"
    config = alembic_config(url)
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    return url


@pytest.fixture
def db_overrides(database_url: str) -> dict[str, Any]:
    return {
        "database_url": database_url,
        "jwt_secret_key": TEST_JWT_SECRET,
        "refresh_cookie_secure": False,
        **FAST_ARGON2,
    }


@pytest.fixture
def db_settings(make_settings: Callable[..., Settings], db_overrides: dict[str, Any]) -> Settings:
    return make_settings(**db_overrides)


@pytest.fixture
def database(db_settings: Settings) -> Iterator[Database]:
    db = Database.from_settings(db_settings)
    yield db
    # Remove everything except the seeded RBAC catalog so tests stay independent.
    with db.session() as session:
        for table in (AuditEvent, RefreshToken, AuthSession):
            session.execute(delete(table))
        session.execute(delete(user_roles))
        session.execute(delete(User))
        session.execute(delete(Organization))
    db.dispose()


@pytest.fixture
def make_api(
    make_client: Callable[..., TestClient], db_overrides: dict[str, Any], database: Database
) -> Callable[..., TestClient]:
    """Build a database-backed client, optionally with extra settings overrides."""

    def _make(**overrides: Any) -> TestClient:
        return make_client(**{**db_overrides, **overrides})

    return _make


@pytest.fixture
def api(make_api: Callable[..., TestClient]) -> TestClient:
    return make_api()


@pytest.fixture
def factory(database: Database) -> Factory:
    return Factory(database=database, hasher=PasswordHasher(1, 1024, 1))

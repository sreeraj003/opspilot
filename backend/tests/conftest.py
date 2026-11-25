"""Shared fixtures.

Tests build settings explicitly (``_env_file=None``) so a developer's local
``.env`` never changes test behaviour. Tests that need a real PostgreSQL
server use ``TEST_DATABASE_URL`` and are skipped when it is not set.
"""

import os
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app

# A PostgreSQL URL on a port where nothing listens: exercises the "unavailable" path.
UNREACHABLE_DATABASE_URL = "postgresql+psycopg://opspilot:not-a-real-password@127.0.0.1:1/opspilot"


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {"app_env": "test", "log_level": "WARNING"}
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


@pytest.fixture
def settings_factory() -> Callable[..., Settings]:
    return make_settings


@pytest.fixture
def client_factory() -> Iterator[Callable[..., TestClient]]:
    """Create a TestClient (with lifespan) for an app built from the given settings."""
    clients: list[TestClient] = []

    def factory(app: FastAPI | None = None, *, raise_server_exceptions: bool = True, **overrides: Any) -> TestClient:
        application = app or create_app(make_settings(**overrides))
        client = TestClient(application, raise_server_exceptions=raise_server_exceptions)
        client.__enter__()
        clients.append(client)
        return client

    yield factory
    for client in clients:
        client.__exit__(None, None, None)


@pytest.fixture
def client(client_factory: Callable[..., TestClient]) -> TestClient:
    return client_factory()


@pytest.fixture
def postgres_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL", "").strip()
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set; PostgreSQL connectivity test skipped")
    return url

"""Database connectivity health behaviour.

The unconfigured and unreachable cases always run. The reachable case needs a
real PostgreSQL server (TEST_DATABASE_URL) and is skipped otherwise, so a
passing run without it never claims database connectivity.
"""

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.deps import get_db_session
from app.core.exceptions import ServiceUnavailableError
from app.database.session import Database
from tests.conftest import UNREACHABLE_DATABASE_URL, make_settings


def test_database_health_reports_not_configured(client: TestClient) -> None:
    response = client.get("/health/database")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_configured"
    assert body["database"] == "postgresql"


def test_database_health_reports_unreachable_without_leaking_credentials(
    client_factory: Callable[..., TestClient],
) -> None:
    client = client_factory(database_url=UNREACHABLE_DATABASE_URL, database_connect_timeout_seconds=1)

    response = client.get("/health/database")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "unavailable"
    assert body["detail"] == "The application database is not reachable."
    raw = response.text
    for secret_part in ("not-a-real-password", "opspilot:", "127.0.0.1", "postgresql+psycopg"):
        assert secret_part not in raw


def test_db_session_dependency_requires_configured_database() -> None:
    dependency = get_db_session(None)
    with pytest.raises(ServiceUnavailableError):
        next(dependency)


@pytest.mark.postgres
def test_database_health_reports_ok_against_real_postgres(
    client_factory: Callable[..., TestClient], postgres_url: str
) -> None:
    client = client_factory(database_url=postgres_url)

    response = client.get("/health/database")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "postgresql"
    assert body["latency_ms"] >= 0


@pytest.mark.postgres
def test_db_session_dependency_commits_and_closes(postgres_url: str) -> None:
    database = Database.from_settings(make_settings(database_url=postgres_url))
    try:
        dependency = get_db_session(database)
        session = next(dependency)
        assert session.execute(text("SELECT 1")).scalar_one() == 1
        with pytest.raises(StopIteration):
            next(dependency)
        assert not session.in_transaction()
    finally:
        database.dispose()


@pytest.mark.postgres
def test_db_session_rolls_back_on_error(postgres_url: str) -> None:
    database = Database.from_settings(make_settings(database_url=postgres_url))
    try:
        with pytest.raises(RuntimeError), database.session() as session:
            session.execute(text("CREATE TEMPORARY TABLE rollback_probe (id int)"))
            raise RuntimeError("boom")
        assert not session.in_transaction()
    finally:
        database.dispose()

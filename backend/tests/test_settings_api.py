"""/api/v1/settings endpoints.

Validation and unavailable-database behaviour run without PostgreSQL; the CRUD
flow runs against the disposable test database (``postgres`` marker).
"""

from collections.abc import AsyncIterator, Callable, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.main import create_app
from app.repositories.application_setting_repository import ApplicationSettingRepository
from tests.conftest import UNREACHABLE_DATABASE_URL, make_settings

URL = "/api/v1/settings"
LEAKS = ("not-a-real-password", "opspilot:", "127.0.0.1", "postgresql+psycopg", "psycopg", "Traceback")


def _assert_error(response_json: dict, code: str) -> None:
    assert response_json["success"] is False
    assert response_json["error"]["code"] == code
    assert response_json["error"]["request_id"]


# --- without a database ---------------------------------------------------------------


def test_settings_return_503_when_database_not_configured(client: TestClient) -> None:
    response = client.get(URL)

    assert response.status_code == 503
    _assert_error(response.json(), "SERVICE_UNAVAILABLE")


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", URL, None),
        ("GET", f"{URL}/ui.theme", None),
        ("PUT", f"{URL}/ui.theme", {"value": "dark"}),
        ("DELETE", f"{URL}/ui.theme", None),
    ],
)
def test_settings_return_503_without_leaking_details_when_database_is_down(
    client_factory: Callable[..., TestClient], method: str, path: str, body: dict | None
) -> None:
    client = client_factory(
        database_url=UNREACHABLE_DATABASE_URL, database_connect_timeout_seconds=1, raise_server_exceptions=False
    )

    response = client.request(method, path, json=body)

    assert response.status_code == 503
    _assert_error(response.json(), "DATABASE_UNAVAILABLE")
    assert response.json()["error"]["message"] == "The application database is unavailable."
    for leak in LEAKS:
        assert leak not in response.text


@pytest.mark.parametrize(
    ("path", "body"),
    [
        (f"{URL}/Upper.Case", {"value": 1}),
        (f"{URL}/9starts.with.digit", {"value": 1}),
        (f"{URL}/{'x' * 129}", {"value": 1}),
        (f"{URL}/ui.theme", {}),
        (f"{URL}/ui.theme", {"value": None}),
        (f"{URL}/ui.theme", {"value": 1, "extra": 1}),
        (f"{URL}/ui.theme", {"value": 1, "description": "x" * 1001}),
        (f"{URL}/ui.theme", {"value": {"password": "hunter2"}}),
    ],
)
def test_invalid_input_is_rejected_before_touching_the_database(
    client_factory: Callable[..., TestClient], path: str, body: dict
) -> None:
    # An unreachable database proves validation happens first (no 503).
    client = client_factory(database_url=UNREACHABLE_DATABASE_URL, database_connect_timeout_seconds=1)

    response = client.put(path, json=body)

    assert response.status_code == 422
    _assert_error(response.json(), "VALIDATION_ERROR")
    assert "hunter2" not in response.text


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "offset=-1", "limit=abc"])
def test_invalid_pagination_is_rejected(client_factory: Callable[..., TestClient], query: str) -> None:
    client = client_factory(database_url=UNREACHABLE_DATABASE_URL, database_connect_timeout_seconds=1)

    response = client.get(f"{URL}?{query}")

    assert response.status_code == 422


def test_settings_endpoints_are_documented(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]

    assert set(paths[URL]) == {"get"}
    assert set(paths[f"{URL}/{{key}}"]) == {"get", "put", "delete"}


# --- against PostgreSQL ------------------------------------------------------------------


@pytest.fixture
def db_client(client_factory: Callable[..., TestClient], migrated_database: str) -> Iterator[TestClient]:
    client = client_factory(database_url=migrated_database)
    yield client
    _truncate(client)


def _truncate(client: TestClient) -> None:
    async def truncate() -> None:
        async with client.app.state.database.session() as session:  # type: ignore[attr-defined]
            await session.execute(text("TRUNCATE application_settings"))
            await session.commit()

    client.portal.call(truncate)  # type: ignore[union-attr]


@pytest.mark.postgres
def test_settings_crud_flow(db_client: TestClient) -> None:
    created = db_client.put(f"{URL}/ui.page_size", json={"value": 25, "description": "Rows per page"})
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["key"] == "ui.page_size"
    assert body["value"] == 25
    assert body["description"] == "Rows per page"
    assert set(body) == {"id", "key", "value", "description", "created_at", "updated_at"}

    fetched = db_client.get(f"{URL}/ui.page_size")
    assert fetched.status_code == 200
    assert fetched.json() == body

    updated = db_client.put(f"{URL}/ui.page_size", json={"value": {"default": 50, "max": 100}})
    assert updated.status_code == 200
    assert updated.json()["id"] == body["id"]
    assert updated.json()["value"] == {"default": 50, "max": 100}
    assert updated.json()["description"] is None
    assert updated.json()["created_at"] == body["created_at"]
    assert updated.json()["updated_at"] > body["updated_at"]

    deleted = db_client.delete(f"{URL}/ui.page_size")
    assert deleted.status_code == 204
    assert deleted.content == b""

    missing = db_client.get(f"{URL}/ui.page_size")
    assert missing.status_code == 404
    _assert_error(missing.json(), "NOT_FOUND")


@pytest.mark.postgres
def test_list_settings_is_paginated_and_ordered_by_key(db_client: TestClient) -> None:
    for key in ("c.key", "a.key", "b.key"):
        assert db_client.put(f"{URL}/{key}", json={"value": key}).status_code == 201

    first = db_client.get(f"{URL}?limit=2").json()
    second = db_client.get(f"{URL}?limit=2&offset=2").json()

    assert [item["key"] for item in first["items"]] == ["a.key", "b.key"]
    assert (first["total"], first["offset"], first["limit"]) == (3, 0, 2)
    assert [item["key"] for item in second["items"]] == ["c.key"]
    assert db_client.get(URL).json()["limit"] == 50


@pytest.mark.postgres
def test_missing_setting_returns_404(db_client: TestClient) -> None:
    for response in (db_client.get(f"{URL}/not.there"), db_client.delete(f"{URL}/not.there")):
        assert response.status_code == 404
        assert response.json()["error"]["message"] == "The setting was not found."


@pytest.mark.postgres
def test_secret_like_key_is_rejected(db_client: TestClient) -> None:
    response = db_client.put(f"{URL}/smtp.password", json={"value": "hunter2"})

    assert response.status_code == 422
    _assert_error(response.json(), "VALIDATION_ERROR")
    assert "hunter2" not in response.text
    assert db_client.get(URL).json()["total"] == 0


@pytest.mark.postgres
def test_secret_like_data_is_never_serialized(db_client: TestClient) -> None:
    async def insert_behind_the_api() -> None:
        async with db_client.app.state.database.session() as session:  # type: ignore[attr-defined]
            repository = ApplicationSettingRepository(session)
            await repository.create(key="legacy.api_key", value="sk-live-123")
            await repository.create(key="mail", value={"host": "smtp.example", "token": "sk-live-123"})
            await session.commit()

    db_client.portal.call(insert_behind_the_api)  # type: ignore[union-attr]

    listing = db_client.get(URL)
    single = db_client.get(f"{URL}/legacy.api_key")

    assert "sk-live-123" not in listing.text
    assert "sk-live-123" not in single.text
    assert single.json()["value"] == "[REDACTED]"
    assert {item["key"]: item["value"] for item in listing.json()["items"]}["mail"] == {
        "host": "smtp.example",
        "token": "[REDACTED]",
    }


@pytest.mark.postgres
def test_each_request_gets_its_own_session(migrated_database: str, client_factory: Callable[..., TestClient]) -> None:
    app = create_app(make_settings(database_url=migrated_database))
    seen: list[AsyncSession] = []

    async def recording_session() -> AsyncIterator[AsyncSession]:
        async with app.state.database.session() as session:
            seen.append(session)
            yield session

    app.dependency_overrides[get_db_session] = recording_session
    client = client_factory(app)

    client.get(URL)
    client.get(URL)

    assert len(seen) == 2
    assert seen[0] is not seen[1]
    assert not any(session.in_transaction() for session in seen)

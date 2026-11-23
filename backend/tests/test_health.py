from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient


def test_health_returns_application_status(client: TestClient) -> None:
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "OpsPilot AI API"
    assert body["version"] == "0.1.0"
    assert body["environment"] == "test"
    assert datetime.fromisoformat(body["timestamp"]).tzinfo is not None


def test_ready_without_database_reports_not_configured(client: TestClient) -> None:
    response = client.get("/api/v1/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    checks = {check["name"]: check for check in body["checks"]}
    assert checks["application"]["status"] == "ok"
    assert checks["database"]["status"] == "not_configured"
    assert checks["database"]["required"] is False


def test_ready_with_reachable_database_reports_ok(
    make_client: Callable[..., TestClient], tmp_path: Path
) -> None:
    client = make_client(database_url=f"sqlite:///{tmp_path / 'ready.db'}")

    response = client.get("/api/v1/ready")

    assert response.status_code == 200
    database = next(c for c in response.json()["checks"] if c["name"] == "database")
    assert database["status"] == "ok"
    assert database["required"] is True
    assert database["latency_ms"] is not None


def test_ready_with_unreachable_database_returns_503(
    make_client: Callable[..., TestClient], tmp_path: Path
) -> None:
    # A path inside a directory that does not exist cannot be opened by SQLite.
    client = make_client(database_url=f"sqlite:///{tmp_path / 'missing' / 'x.db'}")

    response = client.get("/api/v1/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    database = next(c for c in body["checks"] if c["name"] == "database")
    assert database["status"] == "error"
    assert "OperationalError" in database["detail"]


def test_routes_are_served_only_under_version_prefix(client: TestClient) -> None:
    assert client.get("/health").status_code == 404
    assert client.get("/api/v1/openapi.json").status_code == 200

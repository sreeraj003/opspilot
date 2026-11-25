"""Root and health endpoints."""

from datetime import datetime

from fastapi.testclient import TestClient


def test_root_returns_application_information(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.json() == {
        "name": "OpsPilot AI",
        "description": "AI-powered Business Intelligence Assistant.",
        "version": "1.0.0",
        "environment": "test",
        "docs_url": "/docs",
    }


def test_health_returns_ok(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "OpsPilot AI"
    assert body["version"] == "1.0.0"
    assert body["environment"] == "test"
    datetime.fromisoformat(body["timestamp"])
    assert response.headers["X-Request-ID"]


def test_openapi_document_describes_the_application(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()

    assert schema["info"]["title"] == "OpsPilot AI"
    assert schema["info"]["description"] == "AI-powered Business Intelligence Assistant."
    assert schema["info"]["version"] == "1.0.0"
    assert {"/", "/health", "/health/database"} <= set(schema["paths"])


def test_docs_page_is_served(client: TestClient) -> None:
    assert client.get("/docs").status_code == 200


def test_cors_allows_configured_origin_only(client: TestClient) -> None:
    allowed = client.get("/health", headers={"Origin": "http://localhost:5173"})
    denied = client.get("/health", headers={"Origin": "https://evil.example"})

    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "access-control-allow-origin" not in denied.headers

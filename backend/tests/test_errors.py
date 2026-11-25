"""Error response structure and production-safe error handling."""

from collections.abc import Callable

from fastapi import FastAPI, Query
from fastapi.testclient import TestClient

from app.core.exceptions import NotFoundError
from app.main import create_app
from tests.conftest import make_settings


def _app_with_failing_routes() -> FastAPI:
    app = create_app(make_settings())

    @app.get("/test/crash")
    def crash() -> None:
        raise RuntimeError("database password is hunter2")

    @app.get("/test/missing")
    def missing() -> None:
        raise NotFoundError("Widget not found.")

    @app.get("/test/validate")
    def validate(limit: int = Query(ge=1)) -> dict[str, int]:
        return {"limit": limit}

    return app


def test_unknown_route_returns_not_found_envelope(client: TestClient) -> None:
    response = client.get("/does-not-exist")

    assert response.status_code == 404
    assert response.json() == {
        "success": False,
        "error": {
            "code": "NOT_FOUND",
            "message": "The requested resource was not found.",
            "request_id": response.headers["X-Request-ID"],
        },
    }


def test_wrong_method_returns_method_not_allowed(client: TestClient) -> None:
    response = client.post("/health")

    assert response.status_code == 405
    assert response.json()["error"]["code"] == "METHOD_NOT_ALLOWED"


def test_unhandled_exception_returns_generic_error_without_details(
    client_factory: Callable[..., TestClient],
) -> None:
    client = client_factory(_app_with_failing_routes(), raise_server_exceptions=False)

    response = client.get("/test/crash")

    assert response.status_code == 500
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "INTERNAL_SERVER_ERROR"
    assert body["error"]["message"] == "An unexpected error occurred."
    assert "hunter2" not in response.text
    assert "Traceback" not in response.text
    assert "RuntimeError" not in response.text


def test_application_error_uses_its_code_and_message(client_factory: Callable[..., TestClient]) -> None:
    client = client_factory(_app_with_failing_routes())

    response = client.get("/test/missing")

    assert response.status_code == 404
    assert response.json()["error"] == {
        "code": "NOT_FOUND",
        "message": "Widget not found.",
        "request_id": response.headers["X-Request-ID"],
    }


def test_validation_error_lists_problems(client_factory: Callable[..., TestClient]) -> None:
    client = client_factory(_app_with_failing_routes())

    response = client.get("/test/validate", params={"limit": 0})

    assert response.status_code == 422
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["error"]["details"][0]["location"] == ["query", "limit"]


def test_valid_incoming_request_id_is_propagated(client: TestClient) -> None:
    response = client.get("/missing", headers={"X-Request-ID": "abc-123"})

    assert response.headers["X-Request-ID"] == "abc-123"
    assert response.json()["error"]["request_id"] == "abc-123"


def test_unsafe_incoming_request_id_is_replaced(client: TestClient) -> None:
    response = client.get("/health", headers={"X-Request-ID": "bad id\nwith newline"})

    assert response.headers["X-Request-ID"] != "bad id\nwith newline"
    assert len(response.headers["X-Request-ID"]) == 32

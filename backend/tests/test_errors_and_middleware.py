from collections.abc import Callable

from fastapi import FastAPI, Query
from fastapi.testclient import TestClient

from app.core.exceptions import NotFoundError
from app.main import create_app


def _app_with_test_routes(settings) -> FastAPI:
    app = create_app(settings)

    @app.get("/api/v1/_test/not-found")
    def raise_not_found() -> None:
        raise NotFoundError("Widget not found.", details={"id": 7})

    @app.get("/api/v1/_test/crash")
    def crash() -> None:
        raise RuntimeError("secret internal detail")

    @app.get("/api/v1/_test/validated")
    def validated(limit: int = Query(ge=1)) -> dict[str, int]:
        return {"limit": limit}

    return app


def test_unknown_route_uses_error_envelope(client: TestClient) -> None:
    response = client.get("/api/v1/does-not-exist")

    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "not_found"
    assert error["request_id"] == response.headers["X-Request-ID"]


def test_app_error_is_mapped_to_status_and_code(make_settings, make_client: Callable[..., TestClient]) -> None:
    client = make_client(_app_with_test_routes(make_settings()))

    response = client.get("/api/v1/_test/not-found")

    assert response.status_code == 404
    assert response.json()["error"] == {
        "code": "not_found",
        "message": "Widget not found.",
        "details": {"id": 7},
        "request_id": response.headers["X-Request-ID"],
    }


def test_unhandled_exception_returns_generic_500(make_settings, make_client: Callable[..., TestClient]) -> None:
    client = make_client(_app_with_test_routes(make_settings()))

    response = client.get("/api/v1/_test/crash")

    assert response.status_code == 500
    error = response.json()["error"]
    assert error["code"] == "internal_error"
    assert "secret" not in response.text


def test_validation_error_returns_422_with_details(make_settings, make_client: Callable[..., TestClient]) -> None:
    client = make_client(_app_with_test_routes(make_settings()))

    response = client.get("/api/v1/_test/validated", params={"limit": 0})

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["details"][0]["loc"] == ["query", "limit"]


def test_request_id_is_generated_or_propagated(client: TestClient) -> None:
    generated = client.get("/api/v1/health").headers["X-Request-ID"]
    assert len(generated) == 32

    echoed = client.get("/api/v1/health", headers={"X-Request-ID": "trace-abc-123"})
    assert echoed.headers["X-Request-ID"] == "trace-abc-123"

    unsafe = client.get("/api/v1/health", headers={"X-Request-ID": "bad id\twith spaces"})
    assert unsafe.headers["X-Request-ID"] != "bad id\twith spaces"


def test_cors_allows_configured_origin_only(make_client: Callable[..., TestClient]) -> None:
    client = make_client(cors_origins="http://localhost:5173")

    allowed = client.get("/api/v1/health", headers={"Origin": "http://localhost:5173"})
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "X-Request-ID" in allowed.headers["access-control-expose-headers"]

    blocked = client.get("/api/v1/health", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in blocked.headers

    preflight = client.options(
        "/api/v1/health",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"},
    )
    assert preflight.status_code == 200

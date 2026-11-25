"""Structured logging output."""

import json
import logging

import pytest
from fastapi.testclient import TestClient

from app.core.logging import REDACTED, JsonFormatter, reset_request_id, set_request_id


def _record(message: str = "hello", **extra: object) -> logging.LogRecord:
    record = logging.LogRecord("app.example", logging.INFO, __file__, 1, message, None, None)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_json_formatter_includes_required_fields() -> None:
    token = set_request_id("req-1")
    try:
        payload = json.loads(JsonFormatter().format(_record(user_count=3)))
    finally:
        reset_request_id(token)

    assert payload["level"] == "INFO"
    assert payload["module"] == "app.example"
    assert payload["message"] == "hello"
    assert payload["request_id"] == "req-1"
    assert payload["user_count"] == 3
    assert "timestamp" in payload


def test_sensitive_extra_fields_are_redacted() -> None:
    payload = json.loads(
        JsonFormatter().format(
            _record(password="p", api_key="k", authorization="Bearer t", database_url="postgresql://u:p@h/d")
        )
    )

    assert payload["password"] == REDACTED
    assert payload["api_key"] == REDACTED
    assert payload["authorization"] == REDACTED
    assert payload["database_url"] == REDACTED


def test_requests_are_logged_with_correlation_id(client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="app.access"):
        response = client.get("/health", params={"token": "should-not-be-logged"})

    records = [record for record in caplog.records if record.name == "app.access"]
    assert records, "expected an access log record"
    record = records[-1]
    assert record.path == "/health"  # type: ignore[attr-defined]
    assert record.status_code == 200  # type: ignore[attr-defined]
    assert "should-not-be-logged" not in caplog.text
    assert response.headers["X-Request-ID"]

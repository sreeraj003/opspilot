import json
import logging

from app.core.config import Settings
from app.core.logging import JsonFormatter
from app.core.request_context import reset_request_id, set_request_id


def _record(**extra) -> logging.LogRecord:
    record = logging.LogRecord("app.test", logging.INFO, __file__, 1, "hello %s", ("world",), None)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_json_formatter_emits_structured_fields_and_request_id() -> None:
    token = set_request_id("req-1")
    try:
        line = JsonFormatter().format(_record(path="/api/v1/health", status_code=200))
    finally:
        reset_request_id(token)

    payload = json.loads(line)
    assert payload["message"] == "hello world"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "app.test"
    assert payload["request_id"] == "req-1"
    assert payload["path"] == "/api/v1/health"
    assert payload["status_code"] == 200


def test_configure_logging_installs_single_handler() -> None:
    from app.core.logging import configure_logging

    settings = Settings(_env_file=None, log_format="json", log_level="WARNING")
    configure_logging(settings)
    configure_logging(settings)

    root = logging.getLogger()
    assert len(root.handlers) == 1
    assert isinstance(root.handlers[0].formatter, JsonFormatter)
    assert root.level == logging.WARNING

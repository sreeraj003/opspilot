"""Structured JSON logging with request correlation IDs.

Every record is emitted as one JSON object per line with a timestamp, level,
module, message and (inside a request) the request's correlation ID. Fields
passed through ``extra={...}`` are included, except keys that look like
secrets, which are redacted.
"""

import json
import logging
import sys
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from typing import Any

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)

REDACTED = "[REDACTED]"
_SENSITIVE_KEY_PARTS = ("password", "secret", "token", "api_key", "apikey", "authorization", "database_url", "credential")

# Attributes present on every LogRecord; anything else came from ``extra``.
_RESERVED_ATTRS = frozenset(vars(logging.LogRecord("", 0, "", 0, "", None, None)).keys()) | {
    "message",
    "asctime",
    "taskName",
    "color_message",  # uvicorn's ANSI-coloured duplicate of the message
}


def get_request_id() -> str | None:
    return _request_id.get()


def set_request_id(request_id: str | None) -> Token[str | None]:
    return _request_id.set(request_id)


def reset_request_id(token: Token[str | None]) -> None:
    _request_id.reset(token)


def _is_sensitive(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in _SENSITIVE_KEY_PARTS)


def _extra_fields(record: logging.LogRecord) -> dict[str, Any]:
    return {
        key: REDACTED if _is_sensitive(key) else value
        for key, value in vars(record).items()
        if key not in _RESERVED_ATTRS
    }


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "module": record.name,
            "message": record.getMessage(),
        }
        request_id = get_request_id()
        if request_id:
            payload["request_id"] = request_id
        payload.update(_extra_fields(record))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str) -> None:
    """Install a single JSON stdout handler on the root logger and align uvicorn's loggers."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # Route uvicorn's logs through the root handler; the request middleware logs access itself.
    for name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
    logging.getLogger("uvicorn.access").disabled = True

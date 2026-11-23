"""Structured logging configuration.

Emits one JSON object per line (or a readable console format for local work).
Contextual fields passed through ``extra={...}`` are included in the output,
and the current request ID is attached automatically when available.
"""

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from app.core.config import LogFormat, Settings
from app.core.request_context import get_request_id

# Attributes present on every LogRecord; anything else came from ``extra``.
_RESERVED_ATTRS = frozenset(vars(logging.LogRecord("", 0, "", 0, "", None, None)).keys()) | {
    "message",
    "asctime",
    "taskName",
    "color_message",  # uvicorn's ANSI-coloured duplicate of the message
}


def _extra_fields(record: logging.LogRecord) -> dict[str, Any]:
    return {key: value for key, value in vars(record).items() if key not in _RESERVED_ATTRS}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = get_request_id()
        if request_id:
            payload["request_id"] = request_id
        payload.update(_extra_fields(record))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class ConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created, tz=UTC).strftime("%H:%M:%S")
        extras = _extra_fields(record)
        request_id = get_request_id()
        if request_id:
            extras = {"request_id": request_id, **extras}
        suffix = " ".join(f"{key}={value}" for key, value in extras.items())
        line = f"{timestamp} {record.levelname:<8} {record.name}: {record.getMessage()}"
        if suffix:
            line = f"{line} | {suffix}"
        if record.exc_info:
            line = f"{line}\n{self.formatException(record.exc_info)}"
        return line


def configure_logging(settings: Settings) -> None:
    """Install a single stdout handler on the root logger and align uvicorn's loggers."""
    formatter: logging.Formatter = (
        JsonFormatter() if settings.log_format == LogFormat.JSON else ConsoleFormatter()
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(settings.log_level)

    # Route uvicorn logs through the root handler; our middleware logs requests itself.
    for name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
    logging.getLogger("uvicorn.access").disabled = True

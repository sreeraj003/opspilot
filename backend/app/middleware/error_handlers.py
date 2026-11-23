"""Centralized exception handlers producing the standard error envelope."""

import logging
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.exceptions import AppError
from app.core.request_context import get_request_id
from app.schemas.errors import ErrorBody, ErrorResponse

logger = logging.getLogger(__name__)

_HTTP_ERROR_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    429: "too_many_requests",
}


def _error_response(
    status_code: int,
    code: str,
    message: str,
    details: Any | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body = ErrorResponse(error=ErrorBody(code=code, message=message, details=details, request_id=get_request_id()))
    return JSONResponse(status_code=status_code, content=jsonable_encoder(body), headers=headers)


async def app_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    if exc.status_code >= 500:
        logger.error("Application error", extra={"code": exc.code, "error_message": exc.message})
    return _error_response(exc.status_code, exc.code, exc.message, exc.details, headers=exc.headers)


async def http_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    code = _HTTP_ERROR_CODES.get(exc.status_code, "http_error")
    message = exc.detail if isinstance(exc.detail, str) else "Request failed."
    return _error_response(exc.status_code, code, message, headers=getattr(exc, "headers", None))


async def validation_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    details = [
        {"loc": list(error.get("loc", ())), "message": error.get("msg"), "type": error.get("type")}
        for error in exc.errors()
    ]
    return _error_response(
        status.HTTP_422_UNPROCESSABLE_CONTENT, "validation_error", "Request validation failed.", details
    )


async def unhandled_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    # Full traceback goes to logs only; clients get a generic message.
    logger.exception("Unhandled exception", exc_info=exc)
    return _error_response(status.HTTP_500_INTERNAL_SERVER_ERROR, "internal_error", "An unexpected error occurred.")


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)

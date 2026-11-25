"""Application exceptions and centralized exception handlers.

Services raise ``AppError`` subclasses; the handlers registered here turn every
failure into the standard envelope::

    {"success": false, "error": {"code": "...", "message": "...", "request_id": "..."}}

Unexpected exceptions are logged with their traceback and returned to the
client as a generic ``INTERNAL_SERVER_ERROR``, so internal details never leak.
"""

import logging
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError, SQLAlchemyError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_request_id
from app.schemas.errors import ErrorDetail, ErrorResponse

logger = logging.getLogger(__name__)


class AppError(Exception):
    """Base class for errors that map to a specific HTTP response."""

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    code: str = "INTERNAL_SERVER_ERROR"
    message: str = "An unexpected error occurred."

    def __init__(
        self, message: str | None = None, *, details: Any | None = None, headers: dict[str, str] | None = None
    ) -> None:
        self.message = message or self.message
        self.details = details
        self.headers = headers
        super().__init__(self.message)


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "NOT_FOUND"
    message = "The requested resource was not found."


class ConflictError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "CONFLICT"
    message = "The request conflicts with the current state of the resource."


class InvalidInputError(AppError):
    """Input that passed schema validation but breaks a business rule."""

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "VALIDATION_ERROR"
    message = "The request is not valid."


class AuthenticationError(AppError):
    """Missing, invalid or expired credentials. Messages never say which."""

    status_code = status.HTTP_401_UNAUTHORIZED
    code = "UNAUTHORIZED"
    message = "Authentication is required."

    def __init__(self, message: str | None = None, *, code: str | None = None) -> None:
        super().__init__(message, headers={"WWW-Authenticate": "Bearer"})
        if code is not None:
            self.code = code


class PermissionDeniedError(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "FORBIDDEN"
    message = "You do not have permission to perform this action."


class TooManyRequestsError(AppError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "TOO_MANY_REQUESTS"
    message = "Too many requests. Try again later."

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__(headers={"Retry-After": str(max(1, retry_after_seconds))})


class ServiceUnavailableError(AppError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "SERVICE_UNAVAILABLE"
    message = "A required service is unavailable."


_HTTP_ERROR_CODES: dict[int, tuple[str, str]] = {
    400: ("BAD_REQUEST", "The request is invalid."),
    401: ("UNAUTHORIZED", "Authentication is required."),
    403: ("FORBIDDEN", "You do not have permission to perform this action."),
    404: ("NOT_FOUND", "The requested resource was not found."),
    405: ("METHOD_NOT_ALLOWED", "This method is not allowed for the requested resource."),
    409: ("CONFLICT", "The request conflicts with the current state of the resource."),
    429: ("TOO_MANY_REQUESTS", "Too many requests. Try again later."),
}

# Starlette's default reason phrases, replaced by the friendlier messages above.
_STARLETTE_DEFAULTS = frozenset({"Not Found", "Method Not Allowed", "Bad Request", "Unauthorized", "Forbidden"})


def error_response(
    status_code: int,
    code: str,
    message: str,
    details: Any | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body = ErrorResponse(error=ErrorDetail(code=code, message=message, details=details, request_id=get_request_id()))
    return JSONResponse(
        status_code=status_code,
        content=jsonable_encoder(body, exclude_none=True),
        headers=headers,
    )


async def _app_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    if exc.status_code >= 500:
        logger.error("Application error", extra={"error_code": exc.code, "error_message": exc.message})
    return error_response(exc.status_code, exc.code, exc.message, exc.details, headers=exc.headers)


async def _http_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    code, default_message = _HTTP_ERROR_CODES.get(exc.status_code, ("HTTP_ERROR", "The request failed."))
    # Framework messages such as "Not Found" are replaced with consistent wording;
    # an explicit message passed to HTTPException is kept.
    message = exc.detail if isinstance(exc.detail, str) and exc.detail not in _STARLETTE_DEFAULTS else default_message
    return error_response(exc.status_code, code, message, headers=getattr(exc, "headers", None))


async def _validation_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    details = [
        {"location": [str(part) for part in error.get("loc", ())], "message": error.get("msg"), "type": error.get("type")}
        for error in exc.errors()
    ]
    return error_response(
        status.HTTP_422_UNPROCESSABLE_CONTENT, "VALIDATION_ERROR", "The request is not valid.", details
    )


def is_database_unavailable(exc: SQLAlchemyError) -> bool:
    """True when the error means the database cannot be reached, not that a query was wrong."""
    if isinstance(exc, (OperationalError, InterfaceError, PoolTimeoutError)):
        return True
    return isinstance(exc, DBAPIError) and exc.connection_invalidated


async def _database_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, SQLAlchemyError)
    # Never log str(exc): driver messages can contain host names, user names or
    # (for constraint violations) the offending values. The type and SQLSTATE suffice.
    sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None)
    if is_database_unavailable(exc):
        logger.error("Application database unavailable", extra={"error_type": type(exc).__name__, "sqlstate": sqlstate})
        return error_response(
            status.HTTP_503_SERVICE_UNAVAILABLE, "DATABASE_UNAVAILABLE", "The application database is unavailable."
        )
    logger.error("Unhandled database error", extra={"error_type": type(exc).__name__, "sqlstate": sqlstate})
    return error_response(
        status.HTTP_500_INTERNAL_SERVER_ERROR, "INTERNAL_SERVER_ERROR", "An unexpected error occurred."
    )


async def _unhandled_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    # The traceback goes to the logs only; the client gets a generic message.
    logger.exception("Unhandled exception", exc_info=exc)
    return error_response(
        status.HTTP_500_INTERNAL_SERVER_ERROR, "INTERNAL_SERVER_ERROR", "An unexpected error occurred."
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_exception_handler)
    app.add_exception_handler(SQLAlchemyError, _database_error_handler)
    app.add_exception_handler(Exception, _unhandled_exception_handler)

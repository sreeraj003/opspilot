"""Application exception hierarchy.

Services raise these; centralized handlers convert them into the standard
error response envelope, so route handlers never build error payloads.
"""

from typing import Any


class AppError(Exception):
    status_code: int = 500
    code: str = "internal_error"
    message: str = "An unexpected error occurred."

    def __init__(
        self,
        message: str | None = None,
        *,
        details: Any | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.message = message or self.message
        self.details = details
        self.headers = headers
        super().__init__(self.message)


class AuthenticationError(AppError):
    """Missing, invalid or expired credentials. Messages are deliberately generic."""

    status_code = 401
    code = "unauthorized"
    message = "Authentication required."

    def __init__(self, message: str | None = None, *, details: Any | None = None) -> None:
        super().__init__(message, details=details, headers={"WWW-Authenticate": "Bearer"})


class PermissionDeniedError(AppError):
    status_code = 403
    code = "forbidden"
    message = "You do not have permission to perform this action."


class RateLimitedError(AppError):
    status_code = 429
    code = "too_many_requests"
    message = "Too many attempts. Try again later."

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__(headers={"Retry-After": str(max(1, retry_after_seconds))})
        self.retry_after_seconds = retry_after_seconds


class InvalidInputError(AppError):
    """Input that passed schema validation but violates a business rule (e.g. password policy)."""

    status_code = 422
    code = "validation_error"
    message = "Request validation failed."


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"
    message = "The requested resource was not found."


class ConflictError(AppError):
    status_code = 409
    code = "conflict"
    message = "The request conflicts with the current state of the resource."


class ServiceUnavailableError(AppError):
    status_code = 503
    code = "service_unavailable"
    message = "A required service is unavailable."

"""Connector failure categories.

Every failure of a connector operation is reported as one ``ErrorCategory`` and
a short message that is safe to show and to store: it never contains
credentials, SQL, response bodies, resolved addresses or stack traces.
"""

from enum import StrEnum


class ErrorCategory(StrEnum):
    CONFIGURATION_INVALID = "configuration_invalid"
    ENCRYPTION_UNAVAILABLE = "encryption_unavailable"
    DESTINATION_REJECTED = "destination_rejected"
    DNS_RESOLUTION_FAILED = "dns_resolution_failed"
    CONNECTION_FAILED = "connection_failed"
    TLS_FAILED = "tls_failed"
    TIMEOUT = "timeout"
    AUTHENTICATION_FAILED = "authentication_failed"
    REDIRECT_REJECTED = "redirect_rejected"
    HTTP_ERROR = "http_error"
    RESPONSE_TOO_LARGE = "response_too_large"
    INVALID_CONTENT_TYPE = "invalid_content_type"
    MALFORMED_RESPONSE = "malformed_response"
    EXTERNAL_API_ERROR = "external_api_error"
    REQUEST_TOO_LARGE = "request_too_large"
    VALIDATION_REJECTED = "validation_rejected"


class ConnectorExecutionError(Exception):
    """A connector request failed or was refused. ``message`` is safe to show and to store."""

    def __init__(self, category: ErrorCategory, message: str, *, http_status: int | None = None) -> None:
        super().__init__(message)
        self.category = category
        self.message = message
        self.http_status = http_status


class TrustedRequestRejectedError(ConnectorExecutionError):
    """The execution request is not a valid, sealed, currently permitted trusted request."""

    def __init__(self, message: str) -> None:
        super().__init__(ErrorCategory.VALIDATION_REJECTED, message)

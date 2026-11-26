"""Typed errors raised by AI providers and ``LLMService``.

Every error is an ``AppError``, so an error that reaches a route becomes the
standard error envelope. Client messages are fixed strings chosen here: a
provider's own error text, its HTTP body, URLs, headers and API keys are never
copied into a message, a ``details`` field or a log line.

Each error says whether it is ``retryable``. Only failures where the request
certainly did not produce a result are retryable (the connection was never
established, or the provider answered 429 / 502 / 503). Authentication
failures, invalid requests, malformed responses and timeouts after the request
was sent are not retried, because the outcome is either permanent or
ambiguous.
"""

from enum import StrEnum

from fastapi import status

from app.core.exceptions import AppError


class ProviderErrorCategory(StrEnum):
    CONFIGURATION = "configuration"
    AUTHENTICATION = "authentication"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    CONNECTION = "connection"
    INVALID_REQUEST = "invalid_request"
    MALFORMED_RESPONSE = "malformed_response"
    UNAVAILABLE = "unavailable"


class ProviderError(AppError):
    """Base class. ``provider`` names the provider (never a URL or key)."""

    status_code = status.HTTP_502_BAD_GATEWAY
    code = "AI_PROVIDER_ERROR"
    message = "The AI provider request failed."
    category: ProviderErrorCategory = ProviderErrorCategory.UNAVAILABLE

    def __init__(
        self,
        message: str | None = None,
        *,
        provider: str | None = None,
        retryable: bool = False,
        retry_after_seconds: float | None = None,
        http_status: int | None = None,
    ) -> None:
        self.provider = provider
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds
        self.http_status = http_status
        headers = None
        if retry_after_seconds is not None and self.status_code in (429, 503):
            headers = {"Retry-After": str(max(1, int(retry_after_seconds + 0.999)))}
        super().__init__(message, details={"category": self.category.value}, headers=headers)


class ProviderConfigurationError(ProviderError):
    """The provider is not configured, or its configuration is unusable (unknown model, bad endpoint)."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "AI_PROVIDER_NOT_CONFIGURED"
    message = "The AI provider is not configured. An administrator must set its API key and model on the server."
    category = ProviderErrorCategory.CONFIGURATION


class ProviderAuthenticationError(ProviderError):
    code = "AI_PROVIDER_AUTHENTICATION_FAILED"
    message = "The AI provider rejected the configured credentials."
    category = ProviderErrorCategory.AUTHENTICATION


class ProviderRateLimitError(ProviderError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "AI_PROVIDER_RATE_LIMITED"
    message = "The AI provider is rate limiting requests. Try again later."
    category = ProviderErrorCategory.RATE_LIMITED


class ProviderTimeoutError(ProviderError):
    status_code = status.HTTP_504_GATEWAY_TIMEOUT
    code = "AI_PROVIDER_TIMEOUT"
    message = "The AI provider did not answer in time."
    category = ProviderErrorCategory.TIMEOUT


class ProviderConnectionError(ProviderError):
    code = "AI_PROVIDER_CONNECTION_FAILED"
    message = "The AI provider could not be reached."
    category = ProviderErrorCategory.CONNECTION


class ProviderResponseError(ProviderError):
    """The provider answered, but not with a usable completion (malformed body, rejected request)."""

    code = "AI_PROVIDER_BAD_RESPONSE"
    message = "The AI provider returned a response that could not be used."
    category = ProviderErrorCategory.MALFORMED_RESPONSE

    def __init__(self, message: str | None = None, *, category: ProviderErrorCategory | None = None, **kwargs: object) -> None:
        if category is not None:
            self.category = category
        super().__init__(message, **kwargs)  # type: ignore[arg-type]


class ProviderUnavailableError(ProviderError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "AI_PROVIDER_UNAVAILABLE"
    message = "The AI provider is temporarily unavailable."
    category = ProviderErrorCategory.UNAVAILABLE

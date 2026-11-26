"""LLM request and response models.

``LLMGenerationRequest`` is what application modules hand to ``LLMService``;
``LLMGenerationResponse`` is what they get back, whatever the provider.
Providers return a ``ProviderCompletion`` (their answer, normalised), and the
service adds provider name, latency and request id.

Token counts are optional everywhere: a provider that does not report usage
yields ``None``, never an estimate.
"""

import re
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from app.core.logging import is_sensitive_key

# Bounds on what one request may carry. They protect the provider bill and the
# process, not the model's context window (each model has its own).
MAX_MESSAGES = 50
MAX_MESSAGE_CHARS = 32_000
MAX_REQUEST_CHARS = 120_000
MAX_OUTPUT_TOKENS_LIMIT = 32_768
MAX_METADATA_KEYS = 20
MODEL_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$"
_METADATA_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

MessageRole = Literal["system", "user", "assistant"]
MetadataValue = str | int | float | bool | None


class LLMMessage(BaseModel):
    """One chat message. Content is plain text; it is never logged by default."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    role: MessageRole
    content: Annotated[str, StringConstraints(min_length=1, max_length=MAX_MESSAGE_CHARS)]

    @field_validator("content")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message content must not be blank")
        if "\x00" in value:
            raise ValueError("message content must not contain NUL characters")
        return value

    def __repr__(self) -> str:  # keep prompt text out of reprs and tracebacks
        return f"LLMMessage(role={self.role!r}, chars={len(self.content)})"

    __str__ = __repr__


class LLMGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messages: Annotated[list[LLMMessage], Field(min_length=1, max_length=MAX_MESSAGES)]
    temperature: Annotated[float | None, Field(ge=0.0, le=2.0)] = None
    max_tokens: Annotated[int | None, Field(ge=1, le=MAX_OUTPUT_TOKENS_LIMIT)] = None
    # Use another model of the active provider for this request only.
    model: Annotated[str | None, Field(pattern=MODEL_NAME_PATTERN)] = None
    # Caller context for logs and usage records (for example {"feature": "sql_generation"}).
    # Simple values only; never prompt text or secrets.
    metadata: dict[str, MetadataValue] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def _validate_metadata(cls, value: dict[str, MetadataValue]) -> dict[str, MetadataValue]:
        if len(value) > MAX_METADATA_KEYS:
            raise ValueError(f"metadata may have at most {MAX_METADATA_KEYS} keys")
        for key, item in value.items():
            if not _METADATA_KEY_RE.fullmatch(key):
                raise ValueError("metadata keys must be lower_snake_case identifiers")
            if is_sensitive_key(key):
                raise ValueError(f"metadata key {key!r} looks like a credential")
            if isinstance(item, str) and len(item) > 200:
                raise ValueError("metadata values must be at most 200 characters")
        return value

    @model_validator(mode="after")
    def _validate_conversation(self) -> "LLMGenerationRequest":
        if sum(len(message.content) for message in self.messages) > MAX_REQUEST_CHARS:
            raise ValueError(f"the messages exceed {MAX_REQUEST_CHARS} characters in total")
        if all(message.role == "system" for message in self.messages):
            raise ValueError("at least one user or assistant message is required")
        return self


class ProviderCompletion(BaseModel):
    """One provider answer, normalised. Built by providers; never contains request text."""

    model_config = ConfigDict(frozen=True)

    content: str
    model: str
    finish_reason: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    provider_request_id: str | None = None


class LLMGenerationResponse(BaseModel):
    """What callers of ``LLMService.generate`` receive, for every provider."""

    model_config = ConfigDict(frozen=True)

    content: str
    provider: str
    model: str
    finish_reason: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    latency_ms: int
    request_id: str
    attempts: int = 1

    @property
    def usage_available(self) -> bool:
        return any(v is not None for v in (self.input_tokens, self.output_tokens, self.total_tokens))

    def __repr__(self) -> str:
        return (
            f"LLMGenerationResponse(provider={self.provider!r}, model={self.model!r}, "
            f"chars={len(self.content)}, request_id={self.request_id!r})"
        )

    __str__ = __repr__


class ProviderHealthStatus(StrEnum):
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    NOT_CONFIGURED = "not_configured"


class ProviderHealth(BaseModel):
    provider: str
    model: str | None
    status: ProviderHealthStatus
    latency_ms: int | None = None
    # Whether the provider's model list includes the configured model (None: list not available).
    model_available: bool | None = None
    error_category: str | None = None
    message: str
    checked_at: datetime

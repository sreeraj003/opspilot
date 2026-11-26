"""Response contracts of the AI administration API. None of them can carry a credential."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.ai.schemas.llm import ProviderHealthStatus


class ProviderCredentialRead(BaseModel):
    configured: bool = Field(description="Whether an API key is set. The key itself is never returned.")
    source: Literal["environment"] = "environment"
    reference: str = Field(description="The environment variable that holds the key, e.g. NVIDIA_API_KEY.")


class ProviderRead(BaseModel):
    name: str
    display_name: str
    model: str | None
    base_url: str | None
    is_active: bool
    configured: bool
    missing_settings: list[str]
    credential: ProviderCredentialRead
    temperature: float
    max_output_tokens: int
    timeout_seconds: float
    max_retries: int
    retry_max_wait_seconds: float


class ProviderList(BaseModel):
    active_provider: str
    items: list[ProviderRead]


class ProviderHealthRead(BaseModel):
    provider: str
    model: str | None
    status: ProviderHealthStatus
    latency_ms: int | None = None
    model_available: bool | None = None
    error_category: str | None = None
    message: str
    checked_at: datetime


class ProviderTestResult(BaseModel):
    status: Literal["passed", "failed"]
    provider: str
    model: str | None
    message: str
    latency_ms: int | None = None
    attempts: int | None = None
    finish_reason: str | None = None
    usage_available: bool = False
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    request_id: str | None = None
    error_category: str | None = None
    error_code: str | None = None
    # The model's reply to the fixed test prompt, shortened. Never anything a user typed.
    response_preview: str | None = None
    tested_at: datetime


class UsageRecordRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    provider: str
    model: str
    operation: str
    status: Literal["success", "failure"]
    error_category: str | None
    finish_reason: str | None
    usage_available: bool
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    latency_ms: int
    attempts: int
    request_id: str
    correlation_id: str | None
    user_id: uuid.UUID | None
    created_at: datetime


class UsageSummary(BaseModel):
    requests: int
    successful: int
    failed: int
    requests_with_usage: int = Field(description="Requests for which the provider reported token counts.")
    input_tokens: int = Field(description="Sum of reported input tokens. Requests without usage add nothing.")
    output_tokens: int
    total_tokens: int


class UsagePage(BaseModel):
    items: list[UsageRecordRead]
    total: int
    offset: int
    limit: int
    summary: UsageSummary

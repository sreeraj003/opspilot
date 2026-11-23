"""Health and readiness response contracts."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str = Field(description="Always 'ok' when the process is serving requests.")
    service: str
    version: str
    environment: str
    timestamp: datetime


class CheckStatus(StrEnum):
    OK = "ok"
    ERROR = "error"
    NOT_CONFIGURED = "not_configured"


class ReadinessStatus(StrEnum):
    READY = "ready"
    NOT_READY = "not_ready"


class DependencyCheck(BaseModel):
    name: str
    status: CheckStatus
    required: bool = Field(description="Whether a failure of this check makes the service not ready.")
    latency_ms: float | None = None
    detail: str | None = None


class ReadinessResponse(BaseModel):
    status: ReadinessStatus
    checks: list[DependencyCheck]
    timestamp: datetime

"""Response contracts for the application information and health endpoints."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class AppInfoResponse(BaseModel):
    name: str
    description: str
    version: str
    environment: str
    docs_url: str


class HealthResponse(BaseModel):
    status: str = Field(description="Always 'ok' while the process is serving requests.")
    service: str
    version: str
    environment: str
    timestamp: datetime


class DatabaseStatus(StrEnum):
    OK = "ok"
    UNAVAILABLE = "unavailable"
    NOT_CONFIGURED = "not_configured"


class DatabaseHealthResponse(BaseModel):
    status: DatabaseStatus
    database: str = Field(description="Database dialect, never the connection URL.")
    latency_ms: float | None = None
    detail: str | None = None
    timestamp: datetime

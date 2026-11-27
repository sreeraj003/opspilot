"""API contracts for query execution and results (Module 12).

Nothing here is accepted from a client except an approval id in the path and
list filters. Results are always produced by the server: ``rows`` are present
only for a ``succeeded`` execution whose result the requesting user may still
see (``delivery == "delivered"``).
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.ai.query_execution.policies.failures import ExecutionErrorCategory, ExecutionStatus


class Delivery(StrEnum):
    DELIVERED = "delivered"  # rows are included
    WITHHELD = "withheld"  # permissions changed; rows removed and never shown
    EXPIRED = "expired"  # the retention period ended; metadata only
    RESTRICTED = "restricted"  # the viewer is not the requesting user (metadata only)
    NONE = "none"  # the execution did not succeed: there is no result


class ExecutionError(BaseModel):
    category: ExecutionErrorCategory
    message: str


class ResultColumnView(BaseModel):
    name: str
    data_type: Literal["string", "integer", "decimal", "float", "boolean", "date", "datetime", "json", "null", "unknown"]
    nullable: bool
    timezone: Literal["utc", "naive", "mixed"] | None = None
    type_source: Literal["declared", "inferred"] = "inferred"


class RequestingUser(BaseModel):
    user_id: uuid.UUID
    full_name: str | None = None
    email: str | None = None


class ExecutionSummary(BaseModel):
    """One execution in the history (no SQL, no values)."""

    execution_id: uuid.UUID
    approval_id: uuid.UUID | None
    generation_id: uuid.UUID | None
    status: ExecutionStatus
    # False for timed_out / outcome_unknown: the company API may have run the query.
    outcome_known: bool
    error: ExecutionError | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int | None = None
    rows_returned: int | None = None
    truncated: bool = False
    schema_version: int | None = None
    query_hash: str | None = None
    is_own: bool = True
    # Only for viewers with ai.sql.execution.read_all.
    requested_by: RequestingUser | None = None


class ExecutionPage(BaseModel):
    items: list[ExecutionSummary]
    total: int
    offset: int
    limit: int


class ResultMetadataView(BaseModel):
    execution_id: uuid.UUID
    status: ExecutionStatus
    columns: list[ResultColumnView] = Field(default_factory=list)
    row_count: int | None = None
    rows_received: int | None = None
    truncated: bool = False
    truncation_reasons: list[Literal["row_limit", "size_limit"]] = Field(default_factory=list)
    withheld_columns_count: int = 0
    query_row_limit: int | None = None
    limit_reached: bool = False
    result_format: str = "table"
    response_size_bytes: int | None = None
    normalized_size_bytes: int | None = None
    external_execution_time_ms: int | None = None
    duration_ms: int | None = None
    result_created_at: datetime | None = None
    result_expires_at: datetime | None = None
    result_available: bool = False
    # The trusted request's execution reference, also sent to the company API as X-OpsPilot-Execution-Id.
    correlation_id: uuid.UUID | None = None


class QueryExecutionResult(ExecutionSummary):
    """The structured outcome of one execution, with rows when they may be shown."""

    delivery: Delivery = Delivery.NONE
    delivery_message: str | None = None
    columns: list[ResultColumnView] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0
    rows_received: int | None = None
    truncation_reasons: list[Literal["row_limit", "size_limit"]] = Field(default_factory=list)
    limit_reached: bool = False
    query_row_limit: int | None = None
    withheld_columns_count: int = 0
    external_execution_time_ms: int | None = None
    response_size_bytes: int | None = None
    result_expires_at: datetime | None = None
    correlation_id: uuid.UUID | None = None
    # The exact approved SQL, only for the requesting user with a current allow_sql_view permission.
    sql: str | None = None
    sql_visible: bool = False
    can_export: bool = False

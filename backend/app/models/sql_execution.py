"""Query execution records, result metadata, short-lived result rows and the execution audit trail (Module 12).

* ``SQLExecutionRecord`` is one attempt to run one guard approval (Module 11)
  through the company API connector (Module 07). It holds ids, versions, the
  query hash, timings, sizes and the outcome; never SQL text, result values
  or credentials. A partial unique index allows at most one *dispatched*
  execution per approval, and a trigger (migration 0010) allows only forward
  status transitions, so an uncertain or failed outcome can never be turned
  into a success afterwards.
* ``QueryResultMetadata`` describes the normalised result of a successful
  execution: column names and types, rows received and kept, truncation and
  withheld columns. It is kept as long as the execution record.
* ``QueryResultPayload`` holds the normalised result rows, only for the
  requesting user and only until ``expires_at``
  (``AI_SQL_EXEC_RESULT_RETENTION_MINUTES``). It is deleted when it expires or
  when delivery is refused because permissions changed.
* ``SQLExecutionEvent`` is the append-only execution audit log, separate from
  result payloads: ids, hashes, status, error categories and correlation
  references only.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.base_model import TimestampMixin, UUIDPrimaryKeyMixin

EXECUTION_STATUSES = (
    "pending",
    "running",
    "succeeded",
    "failed",
    "timed_out",
    "outcome_unknown",
    "rejected",
    "cancelled",
)
# Statuses an execution can only reach after its request was handed to the connector.
DISPATCHED_STATUSES = ("running", "succeeded", "failed", "timed_out", "outcome_unknown")
TERMINAL_STATUSES = ("succeeded", "failed", "timed_out", "outcome_unknown", "rejected", "cancelled")
EXECUTION_ERROR_CATEGORIES = (
    "approval_not_found",
    "approval_rejected",
    "approval_expired",
    "approval_consumed",
    "approval_revoked",
    "approval_invalidated",
    "sql_mismatch",
    "permission_changed",
    "schema_changed",
    "connector_inactive",
    "connector_misconfigured",
    "connector_auth_failed",
    "destination_rejected",
    "external_api_unavailable",
    "external_api_timeout",
    "external_api_error",
    "url_too_long",
    "invalid_json",
    "invalid_response_structure",
    "response_too_large",
    "normalization_failed",
    "outcome_unknown",
    "concurrency_limit",
    "internal_error",
)
EXECUTION_EVENT_TYPES = (
    "execution_requested",
    "execution_rejected",
    "execution_dispatched",
    "execution_succeeded",
    "execution_failed",
    "execution_timed_out",
    "execution_outcome_unknown",
    "execution_cancelled",
    "result_delivered",
    "result_withheld",
    "result_exported",
    "columns_withheld",
    "result_expired",
)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


class SQLExecutionRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "sql_execution_records"
    __table_args__ = (
        CheckConstraint(_in("execution_status", EXECUTION_STATUSES), name="execution_status_valid"),
        CheckConstraint(
            f"error_category IS NULL OR {_in('error_category', EXECUTION_ERROR_CATEGORIES)}", name="error_category_valid"
        ),
        CheckConstraint(
            "(execution_status IN ('pending', 'running', 'succeeded')) = (error_category IS NULL)",
            name="error_category_when_unsuccessful",
        ),
        CheckConstraint(f"NOT {_in('execution_status', TERMINAL_STATUSES)} OR completed_at IS NOT NULL",
                        name="terminal_is_completed"),
        CheckConstraint(f"NOT {_in('execution_status', DISPATCHED_STATUSES)} OR started_at IS NOT NULL",
                        name="dispatched_is_started"),
        CheckConstraint("query_hash IS NULL OR char_length(query_hash) = 64", name="query_hash_length"),
        CheckConstraint("schema_version IS NULL OR schema_version >= 1", name="schema_version_positive"),
        CheckConstraint("duration_ms IS NULL OR duration_ms >= 0", name="duration_non_negative"),
        CheckConstraint("rows_returned IS NULL OR rows_returned >= 0", name="rows_non_negative"),
        CheckConstraint("response_size_bytes IS NULL OR response_size_bytes >= 0", name="size_non_negative"),
        Index("ix_sql_execution_records_user_created", "user_id", "created_at"),
        Index("ix_sql_execution_records_status_created", "execution_status", "created_at"),
        Index("ix_sql_execution_records_generation", "generation_id"),
        # One approval can be dispatched to the company API at most once.
        Index(
            "ix_sql_execution_records_one_dispatch_per_approval",
            "approval_id",
            unique=True,
            postgresql_where=text("execution_status IN ('running', 'succeeded', 'failed', 'timed_out', 'outcome_unknown')"),
        ),
        {"comment": "Executions of guard-approved SQL through the company API. No SQL text or result values."},
    )

    approval_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sql_execution_approvals.id", ondelete="SET NULL"))
    generation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sql_generations.id", ondelete="SET NULL"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    connector_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("external_api_connectors.id", ondelete="SET NULL"))
    schema_source_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("schema_sources.id", ondelete="SET NULL"))
    schema_version: Mapped[int | None] = mapped_column(Integer)
    # SHA-256 of the exact approved SQL (the same value as the approval's exact_sql_hash).
    query_hash: Mapped[str | None] = mapped_column(String(64))
    execution_status: Mapped[str] = mapped_column(String(20), default="pending", server_default=text("'pending'"))
    error_category: Mapped[str | None] = mapped_column(String(40))
    # A safe, user-facing message (never SQL, credentials, response bodies or stack traces).
    error_message: Mapped[str | None] = mapped_column(String(500))
    # The trusted request's execution reference: sent as X-OpsPilot-Execution-Id and kept in the
    # connector audit trail, so one execution can be followed across services and the company API.
    execution_reference: Mapped[uuid.UUID | None] = mapped_column()
    request_id: Mapped[str | None] = mapped_column(String(128))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    external_execution_time_ms: Mapped[int | None] = mapped_column(Integer)
    rows_returned: Mapped[int | None] = mapped_column(Integer)
    response_size_bytes: Mapped[int | None] = mapped_column(Integer)
    truncated: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))

    def __repr__(self) -> str:
        return f"SQLExecutionRecord(id={self.id!s}, status={self.execution_status!r})"


class QueryResultMetadata(Base):
    __tablename__ = "sql_query_result_metadata"
    __table_args__ = (
        CheckConstraint("jsonb_typeof(columns) = 'array'", name="columns_is_array"),
        CheckConstraint("jsonb_typeof(truncation_reasons) = 'array'", name="truncation_reasons_is_array"),
        CheckConstraint("jsonb_typeof(withheld_columns) = 'array'", name="withheld_columns_is_array"),
        CheckConstraint("row_count >= 0 AND rows_received >= row_count", name="row_counts_consistent"),
        CheckConstraint("truncated = (rows_received > row_count OR jsonb_array_length(truncation_reasons) > 0)",
                        name="truncated_matches_counts"),
        {"comment": "Column and size metadata of a normalised query result (no values)."},
    )

    execution_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sql_execution_records.id", ondelete="CASCADE"), primary_key=True
    )
    # [{"name": ..., "data_type": ..., "nullable": ..., "timezone": ...}]
    columns: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    row_count: Mapped[int] = mapped_column(Integer)
    rows_received: Mapped[int] = mapped_column(Integer)
    truncated: Mapped[bool] = mapped_column(Boolean)
    truncation_reasons: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    # Columns the company API returned that the approved query did not select (removed, never shown).
    withheld_columns: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    # The approved query's own row limit, and whether the result reached it (more rows may exist).
    query_row_limit: Mapped[int | None] = mapped_column(Integer)
    limit_reached: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    result_format: Mapped[str] = mapped_column(String(16), default="table", server_default=text("'table'"))
    normalized_size_bytes: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    def __repr__(self) -> str:
        return f"QueryResultMetadata(execution_id={self.execution_id!s}, row_count={self.row_count})"


class QueryResultPayload(Base):
    __tablename__ = "sql_query_result_payloads"
    __table_args__ = (
        CheckConstraint("jsonb_typeof(rows) = 'array'", name="rows_is_array"),
        CheckConstraint("expires_at > created_at", name="expiry_after_creation"),
        Index("ix_sql_query_result_payloads_expires_at", "expires_at"),
        {"comment": "Short-lived normalised result rows, readable only by the requesting user."},
    )

    execution_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sql_execution_records.id", ondelete="CASCADE"), primary_key=True
    )
    rows: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:  # never print result values
        return f"QueryResultPayload(execution_id={self.execution_id!s})"


class SQLExecutionEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "sql_execution_events"
    __table_args__ = (
        CheckConstraint(_in("event_type", EXECUTION_EVENT_TYPES), name="event_type_valid"),
        CheckConstraint("jsonb_typeof(details) = 'object'", name="details_is_object"),
        Index("ix_sql_execution_events_execution", "execution_id"),
        Index("ix_sql_execution_events_user_created", "user_id", "created_at"),
        Index("ix_sql_execution_events_created_at", "created_at"),
        {"comment": "Append-only execution audit log: ids, hashes, outcomes. No SQL, result values or secrets."},
    )

    event_type: Mapped[str] = mapped_column(String(32))
    execution_id: Mapped[uuid.UUID | None]
    approval_id: Mapped[uuid.UUID | None]
    generation_id: Mapped[uuid.UUID | None]
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    connector_id: Mapped[uuid.UUID | None]
    schema_source_id: Mapped[uuid.UUID | None]
    schema_version: Mapped[int | None] = mapped_column(Integer)
    query_hash: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str | None] = mapped_column(String(20))
    error_category: Mapped[str | None] = mapped_column(String(40))
    execution_reference: Mapped[uuid.UUID | None]
    request_id: Mapped[str | None] = mapped_column(String(128))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    def __repr__(self) -> str:
        return f"SQLExecutionEvent(event_type={self.event_type!r})"

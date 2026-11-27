"""SQL security validations, execution approvals and the security audit trail (Module 11).

* ``SQLSecurityValidation`` is one decision of the SQL Security and Execution
  Guard about one stored SQL generation. Rows are **append-only**: a database
  trigger (migration 0009) refuses every ``UPDATE``, so a rejected decision can
  never be turned into an approved one. ``approved`` is true exactly when the
  status is ``approved`` (check constraint). ``user_errors`` are safe for the
  user; ``internal_reasons`` keep the detailed causes (which can name tables
  the user may not see) for security investigation and are returned only to
  holders of ``ai.sql.security.audit``.
* ``SQLExecutionApproval`` is the short-lived, single-use authorization to run
  one exact SQL statement (``exact_sql``, the guard's rewritten SQL with
  row-level restrictions and the result limit applied) through one connector,
  for one user, against one schema version and one permission fingerprint.
  A trigger refuses any change to the bound fields after creation and any
  status transition that is not ``approved -> consumed / revoked / expired /
  invalidated`` or ``consumed -> executed``.
* ``SQLSecurityEvent`` is the append-only security log: ids, versions,
  fingerprints, hashes, outcomes and violation codes. It never contains SQL
  text, row-restriction values or credentials.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.base_model import UUIDPrimaryKeyMixin

VALIDATION_STATUSES = ("approved", "rejected", "clarification_required", "validation_failed", "approval_expired")
APPROVAL_STATUSES = ("approved", "consumed", "executed", "revoked", "expired", "invalidated")
REJECTION_CATEGORIES = (
    "generation_not_ready",
    "schema_unavailable",
    "schema_changed",
    "connector_unavailable",
    "parse_error",
    "dialect_mismatch",
    "statement_not_allowed",
    "multiple_statements",
    "unsafe_construct",
    "unsafe_function",
    "function_not_allowed",
    "schema_reference",
    "join_not_allowed",
    "cross_schema",
    "permission_denied",
    "row_security_unenforceable",
    "complexity_limit",
    "result_limit",
    "integrity_failure",
)
SECURITY_EVENT_TYPES = (
    "validation_completed",
    "approval_created",
    "approval_superseded",
    "approval_revoked",
    "approval_expired",
    "approval_invalidated",
    "execution_authorized",
    "execution_rejected",
    "execution_claimed",
)
SQL_DIALECTS = ("postgres", "mysql", "tsql")
APPROVED_SQL_MAX_LENGTH = 100000


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


class SQLSecurityValidation(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "sql_security_validations"
    __table_args__ = (
        CheckConstraint(_in("status", VALIDATION_STATUSES), name="status_valid"),
        CheckConstraint("approved = (status = 'approved')", name="approved_matches_status"),
        CheckConstraint(
            f"rejection_category IS NULL OR {_in('rejection_category', REJECTION_CATEGORIES)}",
            name="rejection_category_valid",
        ),
        CheckConstraint("(status = 'approved') = (rejection_category IS NULL)", name="rejection_category_when_not_approved"),
        CheckConstraint("NOT approved OR (query_hash IS NOT NULL AND permission_fingerprint IS NOT NULL)", name="approved_is_bound"),
        CheckConstraint("query_hash IS NULL OR char_length(query_hash) = 64", name="query_hash_length"),
        CheckConstraint("generated_sql_sha256 IS NULL OR char_length(generated_sql_sha256) = 64", name="generated_hash_length"),
        CheckConstraint("permission_fingerprint IS NULL OR char_length(permission_fingerprint) = 64", name="fingerprint_length"),
        CheckConstraint(f"dialect IS NULL OR {_in('dialect', SQL_DIALECTS)}", name="dialect_valid"),
        CheckConstraint("schema_version IS NULL OR schema_version >= 1", name="schema_version_positive"),
        CheckConstraint("jsonb_typeof(user_errors) = 'array'", name="user_errors_is_array"),
        CheckConstraint("jsonb_typeof(internal_reasons) = 'array'", name="internal_reasons_is_array"),
        CheckConstraint("jsonb_typeof(warnings) = 'array'", name="warnings_is_array"),
        CheckConstraint("jsonb_typeof(referenced_tables) = 'array'", name="referenced_tables_is_array"),
        CheckConstraint("jsonb_typeof(referenced_columns) = 'array'", name="referenced_columns_is_array"),
        CheckConstraint("jsonb_typeof(detected_functions) = 'array'", name="detected_functions_is_array"),
        CheckConstraint("jsonb_typeof(applied_row_restrictions) = 'array'", name="row_restrictions_is_array"),
        CheckConstraint("jsonb_typeof(violation_codes) = 'array'", name="violation_codes_is_array"),
        Index("ix_sql_security_validations_generation", "generation_id"),
        Index("ix_sql_security_validations_user_validated", "user_id", "validated_at"),
        {"comment": "Append-only SQL security guard decisions about stored SQL generations."},
    )

    generation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sql_generations.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    schema_source_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("schema_sources.id", ondelete="SET NULL"))
    connector_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("external_api_connectors.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(32))
    approved: Mapped[bool] = mapped_column(Boolean)
    rejection_category: Mapped[str | None] = mapped_column(String(40))
    dialect: Mapped[str | None] = mapped_column(String(16))
    schema_version: Mapped[int | None] = mapped_column(Integer)
    # The authorization revision (Module 04) and a SHA-256 fingerprint of the exact decision used.
    permission_version: Mapped[int | None] = mapped_column(Integer)
    permission_fingerprint: Mapped[str | None] = mapped_column(String(64))
    # SHA-256 of the SQL as generated (Module 10) and of the exact SQL approved for execution.
    generated_sql_sha256: Mapped[str | None] = mapped_column(String(64))
    query_hash: Mapped[str | None] = mapped_column(String(64))
    # Safe, user-facing messages: [{"code": ..., "message": ...}].
    user_errors: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    # Detailed reasons for security investigation (may name restricted objects).
    internal_reasons: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    warnings: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    violation_codes: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    # Parsed from the AST (never the model's claims).
    referenced_tables: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    referenced_columns: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    detected_functions: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    # Tables whose row-level restrictions were injected (table names only, never filter values).
    applied_row_restrictions: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    sql_visible: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    validated_at: Mapped[datetime] = mapped_column(server_default=func.now())

    def __repr__(self) -> str:
        return f"SQLSecurityValidation(id={self.id!s}, status={self.status!r})"


class SQLExecutionApproval(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "sql_execution_approvals"
    __table_args__ = (
        CheckConstraint(_in("status", APPROVAL_STATUSES), name="status_valid"),
        CheckConstraint(_in("dialect", SQL_DIALECTS), name="dialect_valid"),
        CheckConstraint("expires_at > approved_at", name="expiry_after_approval"),
        CheckConstraint("char_length(exact_sql_hash) = 64", name="exact_sql_hash_length"),
        CheckConstraint("char_length(permission_fingerprint) = 64", name="fingerprint_length"),
        CheckConstraint(f"char_length(exact_sql) BETWEEN 1 AND {APPROVED_SQL_MAX_LENGTH}", name="exact_sql_length"),
        CheckConstraint("schema_version >= 1", name="schema_version_positive"),
        CheckConstraint(
            "(status IN ('consumed', 'executed')) = (consumed_at IS NOT NULL AND execution_reference IS NOT NULL)",
            name="consumed_is_recorded",
        ),
        CheckConstraint("(status = 'executed') = (executed_at IS NOT NULL)", name="executed_is_recorded"),
        CheckConstraint("(status = 'revoked') = (revoked_at IS NOT NULL)", name="revoked_is_recorded"),
        Index("ix_sql_execution_approvals_generation", "generation_id"),
        Index("ix_sql_execution_approvals_user_status", "user_id", "status"),
        {"comment": "Short-lived, single-use approvals bound to one exact SQL statement and execution context."},
    )

    validation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sql_security_validations.id", ondelete="CASCADE"), unique=True
    )
    generation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sql_generations.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    connector_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("external_api_connectors.id", ondelete="CASCADE"))
    schema_source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("schema_sources.id", ondelete="CASCADE"))
    schema_version: Mapped[int] = mapped_column(Integer)
    permission_version: Mapped[int] = mapped_column(Integer)
    permission_fingerprint: Mapped[str] = mapped_column(String(64))
    dialect: Mapped[str] = mapped_column(String(16))
    # The exact statement the connector may send, and its SHA-256 (UTF-8 bytes of exact_sql).
    exact_sql: Mapped[str] = mapped_column(Text)
    exact_sql_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="approved", server_default=text("'approved'"))
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The trusted request issued when the approval was consumed (single use).
    execution_reference: Mapped[uuid.UUID | None] = mapped_column()
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    # Why an approval was superseded, revoked or invalidated (a code, never SQL).
    end_reason: Mapped[str | None] = mapped_column(String(64))

    def __repr__(self) -> str:  # never print the SQL
        return f"SQLExecutionApproval(id={self.id!s}, status={self.status!r})"


class SQLSecurityEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "sql_security_events"
    __table_args__ = (
        CheckConstraint(_in("event_type", SECURITY_EVENT_TYPES), name="event_type_valid"),
        CheckConstraint("jsonb_typeof(violation_codes) = 'array'", name="violation_codes_is_array"),
        CheckConstraint("jsonb_typeof(details) = 'object'", name="details_is_object"),
        Index("ix_sql_security_events_created_at", "created_at"),
        Index("ix_sql_security_events_user_created", "user_id", "created_at"),
        Index("ix_sql_security_events_approval", "approval_id"),
        {"comment": "Append-only SQL security audit log. Ids, versions, hashes and outcomes only: no SQL or secrets."},
    )

    event_type: Mapped[str] = mapped_column(String(32))
    request_id: Mapped[str | None] = mapped_column(String(128))
    validation_id: Mapped[uuid.UUID | None]
    approval_id: Mapped[uuid.UUID | None]
    generation_id: Mapped[uuid.UUID | None]
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    connector_id: Mapped[uuid.UUID | None]
    schema_source_id: Mapped[uuid.UUID | None]
    schema_version: Mapped[int | None] = mapped_column(Integer)
    permission_version: Mapped[int | None] = mapped_column(Integer)
    permission_fingerprint: Mapped[str | None] = mapped_column(String(64))
    query_hash: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str | None] = mapped_column(String(32))
    rejection_category: Mapped[str | None] = mapped_column(String(40))
    violation_codes: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    def __repr__(self) -> str:
        return f"SQLSecurityEvent(event_type={self.event_type!r})"

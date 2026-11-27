"""Business terminology and question-understanding audit (Module 09).

* ``BusinessTerm`` is an administrator-defined business concept ("Revenue",
  "Order count") for one registered schema source: what it means, its
  synonyms, which registered tables and columns it is computed from and how it
  is aggregated. The model may *use* a term; it never invents one. Terms are
  soft-deleted so audit records keep pointing at them.
* ``QuestionUnderstandingEvent`` is an append-only record of each
  interpretation request: who asked, about which schema and version, which
  provider and model answered, how long it took and what the outcome was. It
  stores the question's length, never its text, and no prompt, permission
  details or credentials.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, Float, ForeignKey, Index, Integer, SmallInteger, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.base_model import SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin

TERM_NAME_MAX_LENGTH = 100
TERM_DESCRIPTION_MAX_LENGTH = 2000
TERM_GUIDANCE_MAX_LENGTH = 1000
TERM_KINDS = ("metric", "dimension", "attribute")
TERM_AGGREGATIONS = ("sum", "count", "count_distinct", "average", "min", "max", "none")

UNDERSTANDING_STATUSES = ("understood", "needs_clarification", "access_denied", "rejected", "failed")
VALIDATION_OUTCOMES = ("passed", "corrected", "rejected", "not_run")


class BusinessTerm(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "business_terms"
    __table_args__ = (
        CheckConstraint(f"char_length(btrim(name)) BETWEEN 1 AND {TERM_NAME_MAX_LENGTH}", name="name_length"),
        CheckConstraint(f"char_length(description) BETWEEN 1 AND {TERM_DESCRIPTION_MAX_LENGTH}", name="description_length"),
        CheckConstraint(
            f"aggregation_guidance IS NULL OR char_length(aggregation_guidance) <= {TERM_GUIDANCE_MAX_LENGTH}",
            name="guidance_length",
        ),
        CheckConstraint("kind IN (" + ", ".join(f"'{k}'" for k in TERM_KINDS) + ")", name="kind_valid"),
        CheckConstraint(
            "default_aggregation IS NULL OR default_aggregation IN ("
            + ", ".join(f"'{a}'" for a in TERM_AGGREGATIONS)
            + ")",
            name="aggregation_valid",
        ),
        CheckConstraint("jsonb_typeof(synonyms) = 'array'", name="synonyms_is_array"),
        CheckConstraint("jsonb_typeof(related_tables) = 'array'", name="related_tables_is_array"),
        CheckConstraint("jsonb_typeof(related_columns) = 'array'", name="related_columns_is_array"),
        # A term name is unique within its schema source among live terms.
        Index(
            "ix_business_terms_name_live",
            "schema_source_id",
            func.lower(text("name")),
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_business_terms_source", "schema_source_id"),
        {"comment": "Administrator-defined business terms (metrics, dimensions) mapped to registered schema fields."},
    )

    schema_source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("schema_sources.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(TERM_NAME_MAX_LENGTH))
    description: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(16), default="metric", server_default=text("'metric'"))
    synonyms: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    # "schema.table" names of registered tables.
    related_tables: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    # "schema.table.column" names of registered columns.
    related_columns: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    default_aggregation: Mapped[str | None] = mapped_column(String(32))
    # Plain-language guidance ("exclude cancelled orders"). Context for the model, never executed.
    aggregation_guidance: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    def __repr__(self) -> str:
        return f"BusinessTerm(name={self.name!r}, kind={self.kind!r})"


class QuestionUnderstandingEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "question_understanding_events"
    __table_args__ = (
        CheckConstraint("status IN (" + ", ".join(f"'{s}'" for s in UNDERSTANDING_STATUSES) + ")", name="status_valid"),
        CheckConstraint(
            "validation_outcome IN (" + ", ".join(f"'{s}'" for s in VALIDATION_OUTCOMES) + ")",
            name="validation_outcome_valid",
        ),
        CheckConstraint("confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="confidence_range"),
        CheckConstraint("latency_ms >= 0", name="latency_non_negative"),
        CheckConstraint("question_chars >= 0", name="question_chars_non_negative"),
        CheckConstraint("llm_attempts >= 0", name="llm_attempts_non_negative"),
        Index("ix_question_understanding_events_created_at", "created_at"),
        Index("ix_question_understanding_events_user_created", "user_id", "created_at"),
        {"comment": "One row per question interpretation. Outcome metadata only: no question text, prompt or secrets."},
    )

    request_id: Mapped[str | None] = mapped_column(String(128))
    intent_id: Mapped[uuid.UUID | None]
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    schema_source_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("schema_sources.id", ondelete="SET NULL"))
    schema_version: Mapped[int | None] = mapped_column(Integer)
    is_preview: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    provider: Mapped[str | None] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(200))
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    llm_attempts: Mapped[int] = mapped_column(SmallInteger, default=0)
    question_chars: Mapped[int] = mapped_column(Integer, default=0)
    intent_type: Mapped[str | None] = mapped_column(String(32))
    confidence: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(32))
    requires_clarification: Mapped[bool] = mapped_column(Boolean, default=False)
    validation_outcome: Mapped[str] = mapped_column(String(16))
    error_category: Mapped[str | None] = mapped_column(String(64))
    # Issue codes only (for example ["unknown_column"]), never values from the question.
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    def __repr__(self) -> str:
        return f"QuestionUnderstandingEvent(status={self.status!r}, intent_type={self.intent_type!r})"

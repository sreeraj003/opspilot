"""AI provider usage records (Module 08).

One row per ``LLMService`` request (after retries), successful or not. Token
counts are exactly what the provider reported, or NULL when it reported
nothing: they are never estimated. No prompt text, response text, API key or
monetary cost is stored.
"""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, SmallInteger, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.base_model import UUIDPrimaryKeyMixin


class AIUsageStatus:
    SUCCESS = "success"
    FAILURE = "failure"


class AIUsageRecord(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "ai_usage_records"
    __table_args__ = (
        CheckConstraint("status IN ('success', 'failure')", name="status_valid"),
        CheckConstraint("input_tokens IS NULL OR input_tokens >= 0", name="input_tokens_non_negative"),
        CheckConstraint("output_tokens IS NULL OR output_tokens >= 0", name="output_tokens_non_negative"),
        CheckConstraint("total_tokens IS NULL OR total_tokens >= 0", name="total_tokens_non_negative"),
        CheckConstraint("latency_ms >= 0", name="latency_non_negative"),
        CheckConstraint("attempts >= 1", name="attempts_positive"),
        CheckConstraint("status = 'failure' OR error_category IS NULL", name="error_only_on_failure"),
        # The usage page lists newest first, optionally filtered by provider.
        Index("ix_ai_usage_records_created_at", "created_at"),
        Index("ix_ai_usage_records_provider_created", "provider", "created_at"),
        {"comment": "One row per LLM request. Provider-reported token counts only; no prompts, keys or costs."},
    )

    provider: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(200))
    # What the request was for, chosen by the calling module (for example "provider_test").
    operation: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    error_category: Mapped[str | None] = mapped_column(String(64))
    finish_reason: Mapped[str | None] = mapped_column(String(32))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    total_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int] = mapped_column(Integer)
    attempts: Mapped[int] = mapped_column(SmallInteger)
    # OpsPilot's id for this LLM request, the HTTP correlation id, and the provider's own id.
    request_id: Mapped[str] = mapped_column(String(64))
    correlation_id: Mapped[str | None] = mapped_column(String(128))
    provider_request_id: Mapped[str | None] = mapped_column(String(128))
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), sort_order=100)

    @property
    def usage_available(self) -> bool:
        return any(v is not None for v in (self.input_tokens, self.output_tokens, self.total_tokens))

    def __repr__(self) -> str:
        return f"AIUsageRecord(provider={self.provider!r}, model={self.model!r}, status={self.status!r})"

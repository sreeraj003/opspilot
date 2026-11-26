"""Usage tracking for LLM requests, stored in OpsPilot's own PostgreSQL database.

``UsageService`` implements the ``UsageRecorder`` protocol that ``LLMService``
writes to, and answers the usage administration API. It stores exactly what
the provider reported: missing token counts stay NULL, nothing is estimated
from text length, and no monetary cost is calculated (OpsPilot has no
trustworthy price list).

Usage records are separate from the audit trail. Operational events such as
"an administrator ran the provider test" go through ``AuditService``.
"""

import logging
import uuid
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.transaction import transaction, transaction_depth
from app.models.ai_usage import AIUsageRecord
from app.repositories.ai_usage_repository import AIUsageRepository, UsageFilter, UsageTotals
from app.repositories.base_repository import Page

logger = logging.getLogger("app.ai")


@dataclass(frozen=True, slots=True)
class UsageEntry:
    provider: str
    model: str
    operation: str
    status: str
    latency_ms: int
    attempts: int
    request_id: str
    correlation_id: str | None = None
    provider_request_id: str | None = None
    error_category: str | None = None
    finish_reason: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    user_id: uuid.UUID | None = None


class UsageRecorder(Protocol):
    async def before_request(self) -> None:
        """Called before any network call: release database resources the caller holds."""
        ...

    async def record(self, entry: UsageEntry) -> None: ...


class UsageService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repository = AIUsageRepository(session)

    async def before_request(self) -> None:
        # End the request's implicit read transaction (for example the authentication
        # lookup) so no pooled connection is held while the provider is called.
        # Inside an explicit transaction block the owner decides, so nothing happens.
        if transaction_depth(self._session) == 0 and self._session.in_transaction():
            await self._session.commit()

    async def record(self, entry: UsageEntry) -> None:
        """Store one usage row. A storage failure is logged, never raised: the answer is already paid for."""
        try:
            async with transaction(self._session):
                await self._repository.add(
                    AIUsageRecord(
                        provider=entry.provider,
                        model=entry.model[:200],
                        operation=entry.operation[:64],
                        status=entry.status,
                        error_category=entry.error_category,
                        finish_reason=entry.finish_reason,
                        input_tokens=entry.input_tokens,
                        output_tokens=entry.output_tokens,
                        total_tokens=entry.total_tokens,
                        latency_ms=entry.latency_ms,
                        attempts=entry.attempts,
                        request_id=entry.request_id,
                        correlation_id=entry.correlation_id,
                        provider_request_id=entry.provider_request_id,
                        user_id=entry.user_id,
                    )
                )
        except SQLAlchemyError as exc:
            logger.error(
                "AI usage record could not be stored",
                extra={"event": "ai.usage.store_failed", "request_id": entry.request_id, "error_type": type(exc).__name__},
            )

    async def search(self, filters: UsageFilter, *, offset: int, limit: int) -> Page[AIUsageRecord]:
        return await self._repository.search(filters, offset=offset, limit=limit)

    async def totals(self, filters: UsageFilter) -> UsageTotals:
        return await self._repository.totals(filters)

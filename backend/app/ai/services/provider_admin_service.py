"""Administration of the AI provider: configuration view, health, fixed test prompt, usage.

There is deliberately no endpoint that sends caller-supplied text to the
provider: the test uses a fixed, system-generated prompt.
Provider configuration is changed in the server environment (``.env``), not
through the API, so no API can change it.
"""

import uuid
from dataclasses import asdict
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.config.provider_config import ProviderConfig, ProviderConfigService
from app.ai.exceptions.errors import ProviderError
from app.ai.schemas.admin import (
    ProviderCredentialRead,
    ProviderHealthRead,
    ProviderList,
    ProviderRead,
    ProviderTestResult,
    UsagePage,
    UsageRecordRead,
    UsageSummary,
)
from app.ai.schemas.llm import LLMMessage
from app.ai.services.llm_service import LLMService
from app.ai.services.usage_service import UsageService
from app.database.transaction import transaction
from app.models.auth_event import AuthEventType
from app.repositories.ai_usage_repository import UsageFilter
from app.services.audit_service import AuditService

TEST_OPERATION = "provider_test"
# Fixed and harmless: it contains no user or business data.
TEST_MESSAGES: tuple[LLMMessage, ...] = (
    LLMMessage(role="system", content="You are a connectivity check for OpsPilot AI. Answer with the single word OK."),
    LLMMessage(role="user", content="Reply with OK."),
)
TEST_MAX_TOKENS = 32
PREVIEW_CHARS = 200


def provider_read(config: ProviderConfig) -> ProviderRead:
    return ProviderRead(
        name=config.name,
        display_name=config.display_name,
        model=config.model,
        base_url=config.base_url,
        is_active=config.is_active,
        configured=config.configured,
        missing_settings=list(config.missing_settings),
        credential=ProviderCredentialRead(configured=config.has_credentials, reference=config.credential_reference),
        temperature=config.temperature,
        max_output_tokens=config.max_output_tokens,
        timeout_seconds=config.timeout_seconds,
        max_retries=config.max_retries,
        retry_max_wait_seconds=config.retry_max_wait_seconds,
    )


class ProviderAdminService:
    def __init__(
        self, session: AsyncSession, *, configs: ProviderConfigService, llm: LLMService, usage: UsageService
    ) -> None:
        self._session = session
        self._configs = configs
        self._llm = llm
        self._usage = usage

    def providers(self) -> ProviderList:
        return ProviderList(
            active_provider=self._configs.active_name, items=[provider_read(c) for c in self._configs.all()]
        )

    def active(self) -> ProviderRead:
        return provider_read(self._configs.active())

    async def health(self) -> ProviderHealthRead:
        await self._usage.before_request()
        health = await self._llm.health_check()
        return ProviderHealthRead(**health.model_dump())

    async def test(self, actor_id: uuid.UUID) -> ProviderTestResult:
        config = self._llm.config
        tested_at = datetime.now(UTC)
        try:
            response = await self._llm.generate(
                list(TEST_MESSAGES),
                temperature=0.0,
                max_tokens=min(TEST_MAX_TOKENS, config.max_output_tokens),
                metadata={"feature": "provider_test"},
                operation=TEST_OPERATION,
                user_id=actor_id,
            )
        except ProviderError as exc:
            result = ProviderTestResult(
                status="failed",
                provider=config.name,
                model=config.model,
                message=exc.message,
                error_category=exc.category.value,
                error_code=exc.code,
                tested_at=tested_at,
            )
        else:
            preview = response.content.strip()
            result = ProviderTestResult(
                status="passed",
                provider=response.provider,
                model=response.model,
                message="The provider answered the test prompt.",
                latency_ms=response.latency_ms,
                attempts=response.attempts,
                finish_reason=response.finish_reason,
                usage_available=response.usage_available,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                total_tokens=response.total_tokens,
                request_id=response.request_id,
                response_preview=preview[:PREVIEW_CHARS] if preview else None,
                tested_at=tested_at,
            )
        async with transaction(self._session):
            await AuditService(self._session).record(
                AuthEventType.AI_PROVIDER_TESTED,
                actor_user_id=actor_id,
                details={
                    "provider": result.provider,
                    "model": result.model,
                    "status": result.status,
                    "error_category": result.error_category,
                    "llm_request_id": result.request_id,
                },
            )
        return result

    async def usage(self, filters: UsageFilter, *, offset: int, limit: int) -> UsagePage:
        page = await self._usage.search(filters, offset=offset, limit=limit)
        totals = await self._usage.totals(filters)
        return UsagePage(
            items=[UsageRecordRead.model_validate(item) for item in page.items],
            total=page.total,
            offset=page.offset,
            limit=page.limit,
            summary=UsageSummary(**asdict(totals)),
        )

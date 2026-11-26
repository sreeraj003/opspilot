"""``LLMService``: the single entry point for talking to a language model.

Application modules call ``await llm_service.generate(messages=[...])`` and get
an ``LLMGenerationResponse``. They never import a provider, an SDK or HTTPX.

Responsibilities:

* select the active provider (``AI_PROVIDER``) from the central configuration;
* validate the request (roles, counts, sizes, generation parameters);
* apply defaults and ceilings (temperature, max output tokens);
* call the provider with **bounded** retries for transient failures only,
  honouring ``Retry-After`` up to ``AI_RETRY_MAX_WAIT_SECONDS``;
* normalise the answer, measure latency and record usage;
* log structured events with correlation ids, never prompts, answers or keys.
"""

import asyncio
import logging
import random
import time
import uuid
from datetime import UTC, datetime
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

import httpx
from pydantic import ValidationError
from fastapi import status

from app.ai.config.provider_config import ProviderConfig, ProviderConfigService
from app.ai.exceptions.errors import ProviderConfigurationError, ProviderError
from app.ai.providers.base import LLMProvider
from app.ai.providers.registry import build_provider
from app.ai.schemas.llm import (
    LLMGenerationRequest,
    LLMGenerationResponse,
    LLMMessage,
    MetadataValue,
    ProviderHealth,
    ProviderHealthStatus,
)
from app.ai.services.usage_service import UsageEntry, UsageRecorder
from app.core.exceptions import InvalidInputError
from app.core.logging import get_request_id
from app.models.ai_usage import AIUsageStatus

logger = logging.getLogger("app.ai")

# First retry waits about this long; each further retry doubles it (with jitter), capped by settings.
RETRY_BASE_DELAY_SECONDS = 0.5


class LLMRequestValidationError(InvalidInputError):
    code = "AI_REQUEST_INVALID"
    message = "The AI request is not valid."
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT


class LLMService:
    def __init__(
        self,
        *,
        configs: ProviderConfigService,
        http: httpx.AsyncClient | None,
        usage: UsageRecorder | None = None,
        provider: LLMProvider | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self._configs = configs
        self._http = http
        self._usage = usage
        self._provider_override = provider
        self._sleep = sleep
        self._jitter = jitter

    # --- provider selection ---------------------------------------------------------------------

    @property
    def config(self) -> ProviderConfig:
        if self._provider_override is not None:
            return self._provider_override.config
        return self._configs.active()

    def provider(self) -> LLMProvider:
        """The active provider, ready to call. Raises ``ProviderConfigurationError`` when it cannot be used."""
        if self._provider_override is not None:
            return self._provider_override
        config = self._configs.active()
        if not config.configured:
            raise ProviderConfigurationError(provider=config.name)
        if self._http is None or self._http.is_closed:
            raise ProviderConfigurationError("The AI provider client is not running.", provider=config.name)
        provider = build_provider(config, self._http)
        logger.debug("AI provider selected", extra={"event": "ai.provider.selected", "provider": config.name, "model": config.model})
        return provider

    # --- generation -------------------------------------------------------------------------------

    async def generate(
        self,
        messages: Sequence[LLMMessage | dict[str, Any]] | None = None,
        *,
        request: LLMGenerationRequest | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        model: str | None = None,
        metadata: dict[str, MetadataValue] | None = None,
        response_format: str = "text",
        operation: str = "generate",
        user_id: uuid.UUID | None = None,
    ) -> LLMGenerationResponse:
        """Generate a chat completion with the active provider.

        Pass either ``messages`` (plus optional parameters) or a ready ``request``.
        Raises ``LLMRequestValidationError`` (422) for an invalid request and a
        ``ProviderError`` subclass when the provider cannot produce an answer.
        """
        validated = self._validate(request, messages, temperature, max_tokens, model, metadata, response_format)
        provider = self.provider()
        config = provider.config
        effective_model = validated.model or config.model or ""
        effective_temperature = config.temperature if validated.temperature is None else validated.temperature
        effective_max_tokens = min(validated.max_tokens or config.max_output_tokens, config.max_output_tokens)
        request_id = f"llm_{uuid.uuid4().hex}"
        log_context = {
            "provider": config.name,
            "model": effective_model,
            "operation": operation,
            "llm_request_id": request_id,
            **{f"meta_{key}": value for key, value in validated.metadata.items()},
        }

        if self._usage is not None:
            await self._usage.before_request()
        logger.info(
            "AI request started",
            extra={
                **log_context,
                "event": "ai.request.started",
                "message_count": len(validated.messages),
                "input_chars": sum(len(m.content) for m in validated.messages),
                "max_output": effective_max_tokens,
                "temperature": effective_temperature,
            },
        )

        started = time.perf_counter()
        attempt = 0
        while True:
            attempt += 1
            try:
                # json_output is passed only when asked for, so providers written for Module 08 keep working.
                extra: dict[str, Any] = {"json_output": True} if validated.response_format == "json_object" else {}
                completion = await provider.generate(
                    validated.messages,
                    model=effective_model,
                    temperature=effective_temperature,
                    max_tokens=effective_max_tokens,
                    request_id=request_id,
                    **extra,
                )
                break
            except ProviderError as exc:
                delay = self._retry_delay(exc, attempt, config)
                if delay is None:
                    latency = _elapsed_ms(started)
                    self._log_failure(exc, log_context, attempt, latency)
                    await self._record(
                        UsageEntry(
                            provider=config.name, model=effective_model, operation=operation,
                            status=AIUsageStatus.FAILURE, error_category=exc.category.value,
                            latency_ms=latency, attempts=attempt, request_id=request_id,
                            correlation_id=get_request_id(), user_id=user_id,
                        )
                    )
                    raise
                logger.warning(
                    "AI request will be retried",
                    extra={
                        **log_context,
                        "event": "ai.request.retry",
                        "attempt": attempt,
                        "error_category": exc.category.value,
                        "http_status": exc.http_status,
                        "retry_in_seconds": round(delay, 3),
                    },
                )
                await self._sleep(delay)

        latency = _elapsed_ms(started)
        response = LLMGenerationResponse(
            content=completion.content,
            provider=config.name,
            model=completion.model or effective_model,
            finish_reason=completion.finish_reason,
            input_tokens=completion.input_tokens,
            output_tokens=completion.output_tokens,
            total_tokens=completion.total_tokens,
            latency_ms=latency,
            request_id=request_id,
            attempts=attempt,
        )
        logger.info(
            "AI request completed",
            extra={
                **log_context,
                "event": "ai.request.completed",
                "model": response.model,
                "attempts": attempt,
                "latency_ms": latency,
                "finish_reason": response.finish_reason,
                "usage_reported": response.usage_available,
                "usage_input": response.input_tokens,
                "usage_output": response.output_tokens,
                "usage_total": response.total_tokens,
                "output_chars": len(response.content),
                "provider_request_id": completion.provider_request_id,
            },
        )
        await self._record(
            UsageEntry(
                provider=config.name, model=response.model, operation=operation, status=AIUsageStatus.SUCCESS,
                finish_reason=response.finish_reason, input_tokens=response.input_tokens,
                output_tokens=response.output_tokens, total_tokens=response.total_tokens, latency_ms=latency,
                attempts=attempt, request_id=request_id, correlation_id=get_request_id(),
                provider_request_id=completion.provider_request_id, user_id=user_id,
            )
        )
        return response

    async def health_check(self) -> ProviderHealth:
        """Health of the active provider. Never raises for provider problems; reports them."""
        if self._provider_override is not None:
            provider = self._provider_override
        else:
            config = self._configs.active()
            if self._http is None or self._http.is_closed:
                return ProviderHealth(
                    provider=config.name,
                    model=config.model,
                    status=ProviderHealthStatus.UNHEALTHY if config.configured else ProviderHealthStatus.NOT_CONFIGURED,
                    error_category="configuration",
                    message="The AI provider client is not running.",
                    checked_at=datetime.now(UTC),
                )
            # An unconfigured provider reports "not_configured" itself, without a network call.
            provider = build_provider(config, self._http)
        health = await provider.health_check()
        logger.info(
            "AI provider health checked",
            extra={
                "event": "ai.provider.health",
                "provider": health.provider,
                "health_status": health.status.value,
                "latency_ms": health.latency_ms,
                "error_category": health.error_category,
            },
        )
        return health

    # --- internals ------------------------------------------------------------------------------

    @staticmethod
    def _validate(
        request: LLMGenerationRequest | None,
        messages: Sequence[LLMMessage | dict[str, Any]] | None,
        temperature: float | None,
        max_tokens: int | None,
        model: str | None,
        metadata: dict[str, MetadataValue] | None,
        response_format: str = "text",
    ) -> LLMGenerationRequest:
        if request is not None:
            if messages is not None:
                raise ValueError("pass either request or messages, not both")
            return request
        try:
            return LLMGenerationRequest.model_validate(
                {
                    "messages": [m.model_dump() if isinstance(m, LLMMessage) else m for m in (messages or [])],
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "model": model,
                    "metadata": metadata or {},
                    "response_format": response_format,
                }
            )
        except ValidationError as exc:
            # Locations and messages only: never the offending input (it may be prompt text).
            details = [
                {"location": [str(p) for p in error.get("loc", ())], "message": error.get("msg"), "type": error.get("type")}
                for error in exc.errors(include_input=False, include_url=False)
            ]
            raise LLMRequestValidationError(details=details) from None

    def _retry_delay(self, exc: ProviderError, attempt: int, config: ProviderConfig) -> float | None:
        """Seconds to wait before the next attempt, or None to give up now."""
        if not exc.retryable or attempt > config.max_retries:
            return None
        cap = config.retry_max_wait_seconds
        if exc.retry_after_seconds is not None:
            if exc.retry_after_seconds > cap:
                logger.warning(
                    "AI provider asked to wait longer than allowed; not retrying",
                    extra={"event": "ai.request.retry_after_too_long", "provider": config.name,
                           "retry_after_seconds": exc.retry_after_seconds, "max_wait_seconds": cap},
                )
                return None
            return exc.retry_after_seconds
        backoff = RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1))
        return min(cap, backoff * (0.5 + self._jitter() / 2))

    @staticmethod
    def _log_failure(exc: ProviderError, context: dict[str, Any], attempts: int, latency: int) -> None:
        event = "ai.request.rate_limited" if exc.category.value == "rate_limited" else "ai.request.failed"
        logger.warning(
            "AI request failed",
            extra={
                **context,
                "event": event,
                "error_category": exc.category.value,
                "error_code": exc.code,
                "http_status": exc.http_status,
                "attempts": attempts,
                "latency_ms": latency,
                "retry_after_seconds": exc.retry_after_seconds,
            },
        )

    async def _record(self, entry: UsageEntry) -> None:
        if self._usage is not None:
            await self._usage.record(entry)


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))

"""Adapter for any OpenAI-compatible chat-completions API, over HTTPX.

No vendor SDK is used: ``POST {base_url}/chat/completions`` and
``GET {base_url}/models`` with a bearer key are the whole contract. NVIDIA's
API is OpenAI-compatible, so ``NvidiaProvider`` is this adapter with
NVIDIA's name and defaults.

Error mapping (one attempt; ``LLMService`` decides about retries):

=====================================  ============================  =========
Outcome                                Error                         Retryable
=====================================  ============================  =========
connection refused / DNS failure       ProviderConnectionError       yes
connect timeout, pool timeout          ProviderTimeoutError          yes
read/write timeout (request was sent)  ProviderTimeoutError          no
connection dropped mid-response        ProviderConnectionError       no
401, 403                               ProviderAuthenticationError   no
404                                    ProviderConfigurationError    no
400, 413, 422                          ProviderResponseError         no
429                                    ProviderRateLimitError        yes
502, 503                               ProviderUnavailableError      yes
500 and other 5xx                      ProviderUnavailableError      no
504                                    ProviderTimeoutError          no
non-JSON or malformed body             ProviderResponseError         no
=====================================  ============================  =========

The provider's error body is never returned or logged; only its HTTP status
and a short, sanitised error type are logged.
"""

import json
import logging
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from app.ai.config.provider_config import ProviderConfig
from app.ai.exceptions.errors import (
    ProviderAuthenticationError,
    ProviderConfigurationError,
    ProviderConnectionError,
    ProviderError,
    ProviderErrorCategory,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.ai.providers.base import LLMProvider
from app.ai.schemas.llm import LLMMessage, ProviderCompletion, ProviderHealth, ProviderHealthStatus

logger = logging.getLogger("app.ai")

# Largest provider response body read into memory.
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
# Health checks use a shorter timeout than generation.
HEALTH_TIMEOUT_SECONDS = 10.0
_SAFE_TOKEN = re.compile(r"[^A-Za-z0-9_.:/-]")
_REQUEST_ID_HEADERS = ("x-request-id", "nvcf-reqid", "request-id")


class OpenAICompatibleProvider(LLMProvider):
    def __init__(self, config: ProviderConfig, http: httpx.AsyncClient) -> None:
        super().__init__(config)
        self._http = http

    # --- generation -----------------------------------------------------------------------------

    async def generate(
        self,
        messages: Sequence[LLMMessage],
        *,
        model: str,
        temperature: float,
        max_tokens: int,
        request_id: str,
    ) -> ProviderCompletion:
        api_key, base_url = self._require_configuration()
        payload = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        response = await self._send(
            "POST",
            f"{base_url}/chat/completions",
            api_key=api_key,
            request_id=request_id,
            json_body=payload,
            timeout=self.config.timeout_seconds,
        )
        body = self._json(response)
        return self._normalize(body, response, requested_model=model)

    # --- health -------------------------------------------------------------------------------

    async def health_check(self) -> ProviderHealth:
        now = datetime.now(UTC)
        if not self.config.configured:
            return ProviderHealth(
                provider=self.name,
                model=self.config.model,
                status=ProviderHealthStatus.NOT_CONFIGURED,
                error_category=ProviderErrorCategory.CONFIGURATION.value,
                message="Not configured. Missing: " + ", ".join(self.config.missing_settings) + ".",
                checked_at=now,
            )
        api_key, base_url = self._require_configuration()
        started = time.perf_counter()
        try:
            response = await self._send(
                "GET",
                f"{base_url}/models",
                api_key=api_key,
                request_id=None,
                json_body=None,
                timeout=min(self.config.timeout_seconds, HEALTH_TIMEOUT_SECONDS),
            )
            body = self._json(response)
        except ProviderError as exc:
            return ProviderHealth(
                provider=self.name,
                model=self.config.model,
                status=ProviderHealthStatus.UNHEALTHY,
                latency_ms=_elapsed_ms(started),
                error_category=exc.category.value,
                message=exc.message,
                checked_at=now,
            )
        latency = _elapsed_ms(started)
        available = _model_listed(body, self.config.model)
        message = "The provider is reachable."
        if available is False:
            message = "The provider is reachable, but its model list does not include the configured model."
        return ProviderHealth(
            provider=self.name,
            model=self.config.model,
            status=ProviderHealthStatus.HEALTHY,
            latency_ms=latency,
            model_available=available,
            message=message,
            checked_at=now,
        )

    # --- transport ------------------------------------------------------------------------------

    def _require_configuration(self) -> tuple[str, str]:
        config = self.config
        if not config.configured or config.api_key is None or not config.base_url:
            raise ProviderConfigurationError(provider=self.name)
        return config.api_key.get_secret_value(), config.base_url

    async def _send(
        self,
        method: str,
        url: str,
        *,
        api_key: str,
        request_id: str | None,
        json_body: dict[str, Any] | None,
        timeout: float,
    ) -> "_Reply":
        headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
        if request_id:
            headers["X-Request-ID"] = request_id
        request_timeout = httpx.Timeout(timeout, connect=min(timeout, 10.0))
        try:
            async with self._http.stream(method, url, headers=headers, json=json_body, timeout=request_timeout) as response:
                if response.status_code >= 300:
                    body = await self._read_limited(response, 64 * 1024, swallow=True)
                    raise self._status_error(_Reply(response.status_code, response.headers, body))
                body = await self._read_limited(response, MAX_RESPONSE_BYTES)
                return _Reply(response.status_code, response.headers, body)
        except ProviderError:
            raise
        except (httpx.ConnectTimeout, httpx.PoolTimeout):
            # The request was never sent: safe to try again.
            raise ProviderTimeoutError(provider=self.name, retryable=True) from None
        except httpx.TimeoutException:
            # Sent, but no complete answer: the outcome is ambiguous, so never retried.
            raise ProviderTimeoutError(provider=self.name, retryable=False) from None
        except httpx.ConnectError:
            raise ProviderConnectionError(provider=self.name, retryable=True) from None
        except httpx.TransportError:
            raise ProviderConnectionError(provider=self.name, retryable=False) from None

    async def _read_limited(self, response: httpx.Response, limit: int, *, swallow: bool = False) -> bytes:
        chunks: list[bytes] = []
        size = 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > limit:
                if swallow:
                    break
                raise ProviderResponseError("The AI provider response was too large.", provider=self.name)
            chunks.append(chunk)
        return b"".join(chunks)

    def _status_error(self, response: "_Reply") -> ProviderError:
        status = response.status_code
        _log_provider_error(self.name, status, response)
        if 300 <= status < 400:
            return ProviderResponseError(
                "The AI provider answered with a redirect. Check the configured base URL.",
                provider=self.name, http_status=status,
            )
        if status in (401, 403):
            return ProviderAuthenticationError(provider=self.name, http_status=status)
        if status == 404:
            return ProviderConfigurationError(
                "The AI provider does not know the configured endpoint or model. Check the base URL and model name.",
                provider=self.name, http_status=status,
            )
        if status == 429:
            return ProviderRateLimitError(
                provider=self.name, retryable=True, retry_after_seconds=_retry_after(response), http_status=status
            )
        if status in (400, 413, 422):
            return ProviderResponseError(
                "The AI provider rejected the request.",
                category=ProviderErrorCategory.INVALID_REQUEST, provider=self.name, http_status=status,
            )
        if status in (502, 503):
            return ProviderUnavailableError(
                provider=self.name, retryable=True, retry_after_seconds=_retry_after(response), http_status=status
            )
        if status == 504:
            return ProviderTimeoutError(provider=self.name, retryable=False, http_status=status)
        if status >= 500:
            return ProviderUnavailableError(provider=self.name, retryable=False, http_status=status)
        return ProviderResponseError(provider=self.name, http_status=status)

    def _json(self, response: "_Reply") -> dict[str, Any]:
        content_type = response.headers.get("content-type", "")
        if "json" not in content_type.lower():
            raise ProviderResponseError(provider=self.name, http_status=response.status_code)
        try:
            body = json.loads(response.body)
        except ValueError:
            raise ProviderResponseError(provider=self.name, http_status=response.status_code) from None
        if not isinstance(body, dict):
            raise ProviderResponseError(provider=self.name, http_status=response.status_code)
        return body

    # --- normalisation --------------------------------------------------------------------------

    def _normalize(self, body: dict[str, Any], response: "_Reply", *, requested_model: str) -> ProviderCompletion:
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ProviderResponseError(provider=self.name, http_status=response.status_code)
        choice = choices[0]
        message = choice.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise ProviderResponseError(provider=self.name, http_status=response.status_code)
        finish_reason = choice.get("finish_reason")
        usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        input_tokens = _count(usage.get("prompt_tokens"))
        output_tokens = _count(usage.get("completion_tokens"))
        total_tokens = _count(usage.get("total_tokens"))
        if total_tokens is None and input_tokens is not None and output_tokens is not None:
            total_tokens = input_tokens + output_tokens  # exact sum of reported counts, not an estimate
        model = body.get("model")
        provider_request_id = _provider_request_id(body, response)
        return ProviderCompletion(
            content=content,
            model=_safe_label(model, 200) or requested_model,
            finish_reason=_safe_label(finish_reason, 32),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            provider_request_id=provider_request_id,
        )


# --- helpers --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Reply:
    """A fully read provider response (status, headers, bounded body)."""

    status_code: int
    headers: httpx.Headers
    body: bytes


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))


def _count(value: Any) -> int | None:
    """A reported token count, or None. Booleans, negatives and non-integers are not counts."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _safe_label(value: Any, limit: int) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return _SAFE_TOKEN.sub("", value)[:limit] or None


def _provider_request_id(body: dict[str, Any], response: _Reply) -> str | None:
    for header in _REQUEST_ID_HEADERS:
        if response.headers.get(header):
            return _safe_label(response.headers[header], 128)
    return _safe_label(body.get("id"), 128)


def _model_listed(body: dict[str, Any], model: str | None) -> bool | None:
    data = body.get("data")
    if model is None or not isinstance(data, list):
        return None
    ids = {item.get("id") for item in data if isinstance(item, dict)}
    return model in ids


def _retry_after(response: _Reply) -> float | None:
    """Seconds from a Retry-After header (delta-seconds or HTTP date), or None."""
    value = response.headers.get("retry-after")
    if not value:
        return None
    value = value.strip()
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        seconds = (when - datetime.now(UTC)).total_seconds()
    if seconds != seconds or seconds < 0:  # NaN or in the past
        return 0.0
    return min(seconds, 86_400.0)


def _log_provider_error(provider: str, status: int, response: _Reply) -> None:
    """Log the status and a sanitised error *type* only: provider messages may echo request text."""
    error_type = None
    try:
        body = json.loads(response.body or b"{}")
        error = body.get("error") if isinstance(body, dict) else None
        if isinstance(error, dict):
            error_type = _safe_label(error.get("type") or error.get("code"), 64)
        elif isinstance(body, dict):
            error_type = _safe_label(body.get("type") or body.get("title"), 64)
    except (ValueError, AttributeError):
        pass
    logger.warning(
        "AI provider returned an error status",
        extra={"event": "ai.provider.http_error", "provider": provider, "http_status": status, "provider_error_type": error_type},
    )

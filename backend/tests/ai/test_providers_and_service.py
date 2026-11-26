"""Provider interface, OpenAI-compatible and NVIDIA adapters, LLMService (no database, no network)."""

import json
import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from app.ai.config.provider_config import ProviderConfig, ProviderConfigService
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
from app.ai.providers import PROVIDER_REGISTRY, LLMProvider, NvidiaProvider, OpenAICompatibleProvider, build_provider
from app.ai.schemas.llm import (
    LLMGenerationRequest,
    LLMMessage,
    ProviderCompletion,
    ProviderHealth,
    ProviderHealthStatus,
)
from app.ai.services.llm_service import LLMRequestValidationError, LLMService
from app.ai.services.usage_service import UsageEntry
from tests.ai.conftest import API_KEY, MODEL, NVIDIA_URL, OTHER_KEY, FakeProvider, completion
from tests.conftest import make_settings

MESSAGES = [
    LLMMessage(role="system", content="You are a business intelligence assistant."),
    LLMMessage(role="user", content="Explain what total revenue means."),
]


def nvidia_settings(**overrides: Any) -> Any:
    values: dict[str, Any] = {"nvidia_api_key": API_KEY, "nvidia_model": MODEL}
    values.update(overrides)
    return make_settings(**values)


class RecordingUsage:
    def __init__(self) -> None:
        self.entries: list[UsageEntry] = []
        self.released = 0

    async def before_request(self) -> None:
        self.released += 1

    async def record(self, entry: UsageEntry) -> None:
        self.entries.append(entry)


class Sleeps:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


def make_service(fake: FakeProvider, settings: Any = None, **kwargs: Any) -> tuple[LLMService, RecordingUsage, Sleeps, httpx.AsyncClient]:
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake))
    usage, sleeps = RecordingUsage(), Sleeps()
    service = LLMService(
        configs=ProviderConfigService(settings or nvidia_settings()), http=http, usage=usage, sleep=sleeps,
        jitter=lambda: 1.0, **kwargs,
    )
    return service, usage, sleeps, http


# --- configuration -------------------------------------------------------------------------------------------


def test_missing_key_means_not_configured_and_the_app_still_builds() -> None:
    settings = make_settings()
    configs = ProviderConfigService(settings)
    active = configs.active()

    assert settings.ai_configured is False
    assert active.name == "nvidia" and active.is_active and not active.configured
    assert active.missing_settings == ("NVIDIA_API_KEY", "NVIDIA_MODEL")
    assert active.base_url == NVIDIA_URL


def test_provider_configuration_is_centralised_and_never_shows_the_key() -> None:
    settings = nvidia_settings(ai_temperature=0.4, ai_max_output_tokens=512, ai_request_timeout_seconds=30)
    active = ProviderConfigService(settings).active()

    assert active.configured and active.model == MODEL and active.credential_reference == "NVIDIA_API_KEY"
    assert (active.temperature, active.max_output_tokens, active.timeout_seconds) == (0.4, 512, 30)
    assert API_KEY not in repr(active) and API_KEY not in str(active)
    assert API_KEY not in repr(settings)


def test_openai_compatible_provider_can_be_selected() -> None:
    settings = make_settings(
        ai_provider="openai_compatible",
        openai_compatible_api_key=OTHER_KEY,
        openai_compatible_base_url="https://llm.example.com/v1/",
        openai_compatible_model="gpt-4o-mini",
    )
    configs = ProviderConfigService(settings)

    assert configs.active().name == "openai_compatible" and configs.active().configured
    assert configs.active().base_url == "https://llm.example.com/v1"
    assert [c.is_active for c in configs.all()] == [False, True]
    assert settings.ai_configured


@pytest.mark.parametrize(
    "overrides",
    [
        {"ai_provider": "anthropic-magic"},
        {"nvidia_base_url": "http://integrate.api.nvidia.com/v1"},
        {"openai_compatible_base_url": "ftp://llm.example.com"},
        {"openai_compatible_base_url": "https://user:pass@llm.example.com/v1"},
        {"nvidia_model": "model; DROP TABLE"},
        {"nvidia_api_key": "two words"},
        {"ai_temperature": 3},
        {"ai_max_output_tokens": 0},
        {"ai_max_retries": 50},
        {"ai_request_timeout_seconds": 0},
    ],
)
def test_invalid_provider_configuration_is_rejected(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError) as error:
        make_settings(**overrides)
    assert "user:pass" not in str(error.value)


def test_invalid_api_key_error_never_echoes_the_key() -> None:
    with pytest.raises(ValidationError) as error:
        make_settings(nvidia_api_key="nvapi secret with spaces")
    assert "nvapi secret" not in str(error.value)


def test_production_refuses_plain_http_openai_compatible_endpoint() -> None:
    with pytest.raises(ValidationError, match="OPENAI_COMPATIBLE_BASE_URL"):
        make_settings(app_env="production", secret_key="s" * 40, database_url="postgresql://u:p@db/app",
                      openai_compatible_base_url="http://localhost:11434/v1")


# --- provider interface and registry ---------------------------------------------------------------------------


def test_every_registered_provider_implements_the_interface() -> None:
    assert set(PROVIDER_REGISTRY) == {"nvidia", "openai_compatible"}
    http = httpx.AsyncClient()
    for name in PROVIDER_REGISTRY:
        config = ProviderConfigService(nvidia_settings()).get(name)
        assert config is not None
        provider = build_provider(config, http)
        assert isinstance(provider, LLMProvider) and provider.name == name
    assert isinstance(build_provider(ProviderConfigService(nvidia_settings()).active(), http), NvidiaProvider)


def test_interface_cannot_be_instantiated_without_both_methods() -> None:
    class Incomplete(LLMProvider):
        async def generate(self, messages: Sequence[LLMMessage], **kwargs: Any) -> ProviderCompletion:  # type: ignore[override]
            raise NotImplementedError

    with pytest.raises(TypeError):
        Incomplete(ProviderConfigService(nvidia_settings()).active())  # type: ignore[abstract]


def test_unknown_provider_in_registry_is_a_configuration_error() -> None:
    config = ProviderConfig(
        name="unknown", display_name="?", model="m", base_url="https://x", credential_reference="X",
        temperature=0, max_output_tokens=1, timeout_seconds=1, max_retries=0, retry_max_wait_seconds=0, is_active=True,
    )
    with pytest.raises(ProviderConfigurationError):
        build_provider(config, httpx.AsyncClient())


# --- request validation ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        {"messages": []},
        {"messages": [{"role": "tool", "content": "x"}]},
        {"messages": [{"role": "user", "content": "   "}]},
        {"messages": [{"role": "user", "content": "x" * 32_001}]},
        {"messages": [{"role": "user", "content": "hi"}] * 51},
        {"messages": [{"role": "user", "content": "x" * 30_000}] * 5},
        {"messages": [{"role": "system", "content": "only system"}]},
        {"messages": [{"role": "user", "content": "hi"}], "temperature": 2.5},
        {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 0},
        {"messages": [{"role": "user", "content": "hi"}], "model": "bad model"},
        {"messages": [{"role": "user", "content": "hi"}], "metadata": {"api_key": "x"}},
        {"messages": [{"role": "user", "content": "hi"}], "unexpected": 1},
    ],
)
def test_invalid_generation_requests_are_rejected(body: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        LLMGenerationRequest.model_validate(body)


def test_message_repr_never_contains_the_prompt() -> None:
    message = LLMMessage(role="user", content="confidential revenue question")
    assert "confidential" not in repr(message) and "confidential" not in str(message)


async def test_service_turns_validation_errors_into_safe_422(provider_api: FakeProvider) -> None:
    service, *_ = make_service(provider_api)
    with pytest.raises(LLMRequestValidationError) as error:
        await service.generate([{"role": "user", "content": "secret prompt " * 3000}])
    assert error.value.status_code == 422
    assert "secret prompt" not in json.dumps(error.value.details)
    assert provider_api.requests == []


# --- successful generation and normalisation ------------------------------------------------------------------


async def test_successful_generation_sends_openai_compatible_request(provider_api: FakeProvider) -> None:
    service, usage, sleeps, _ = make_service(provider_api)

    response = await service.generate(MESSAGES)

    request = provider_api.last
    assert request.method == "POST" and str(request.url) == f"{NVIDIA_URL}/chat/completions"
    assert request.headers["authorization"] == f"Bearer {API_KEY}"
    assert request.headers["x-request-id"] == response.request_id
    body = provider_api.last_json()
    assert body["model"] == MODEL and body["stream"] is False
    assert body["messages"] == [m.model_dump() for m in MESSAGES]
    assert body["temperature"] == 0.2 and body["max_tokens"] == 1024

    assert response.content == "OK" and response.provider == "nvidia" and response.model == MODEL
    assert response.finish_reason == "stop" and response.attempts == 1
    assert (response.input_tokens, response.output_tokens, response.total_tokens) == (21, 2, 23)
    assert response.latency_ms >= 0 and response.request_id.startswith("llm_")
    assert usage.released == 1 and sleeps.delays == []
    [entry] = usage.entries
    assert entry.status == "success" and entry.total_tokens == 23 and entry.provider_request_id == "chatcmpl-abc123"


async def test_assistant_messages_temperature_tokens_and_model_override(provider_api: FakeProvider) -> None:
    service, *_ = make_service(provider_api, nvidia_settings(ai_max_output_tokens=100))
    await service.generate(
        [*MESSAGES, LLMMessage(role="assistant", content="Revenue is..."), LLMMessage(role="user", content="And net?")],
        temperature=0.7, max_tokens=50, model="other/model",
    )
    body = provider_api.last_json()
    assert [m["role"] for m in body["messages"]] == ["system", "user", "assistant", "user"]
    assert body["temperature"] == 0.7 and body["max_tokens"] == 50 and body["model"] == "other/model"

    await service.generate(MESSAGES, max_tokens=5000)
    assert provider_api.last_json()["max_tokens"] == 100  # never above the configured ceiling


async def test_request_object_is_accepted(provider_api: FakeProvider) -> None:
    service, *_ = make_service(provider_api)
    response = await service.generate(request=LLMGenerationRequest(messages=MESSAGES, metadata={"feature": "x"}))
    assert response.content == "OK"


@pytest.mark.parametrize(
    ("usage", "expected"),
    [
        (False, (None, None, None)),
        ({"prompt_tokens": 5}, (5, None, None)),
        ({"prompt_tokens": 5, "completion_tokens": 3}, (5, 3, 8)),
        ({"prompt_tokens": -1, "completion_tokens": "3", "total_tokens": True}, (None, None, None)),
        ({"total_tokens": 9}, (None, None, 9)),
    ],
)
async def test_usage_is_never_invented(provider_api: FakeProvider, usage: Any, expected: tuple[Any, ...]) -> None:
    provider_api.reply(body=completion("A much longer answer " * 20, usage=usage))
    service, recorder, *_ = make_service(provider_api)

    response = await service.generate(MESSAGES)

    assert (response.input_tokens, response.output_tokens, response.total_tokens) == expected
    assert response.usage_available is (expected != (None, None, None))
    assert (recorder.entries[0].input_tokens, recorder.entries[0].output_tokens, recorder.entries[0].total_tokens) == expected


async def test_missing_finish_reason_and_model_are_normalised(provider_api: FakeProvider) -> None:
    body = completion(finish_reason=None)
    del body["model"]
    provider_api.reply(body=body)
    service, *_ = make_service(provider_api)
    response = await service.generate(MESSAGES)
    assert response.finish_reason is None and response.model == MODEL


async def test_openai_compatible_adapter_uses_its_own_configuration(provider_api: FakeProvider) -> None:
    settings = make_settings(
        ai_provider="openai_compatible", openai_compatible_api_key=OTHER_KEY,
        openai_compatible_base_url="https://llm.example.com/v1", openai_compatible_model="gpt-4o-mini",
    )
    provider_api.reply(body=completion(model="gpt-4o-mini-2024", usage=False))
    service, *_ = make_service(provider_api, settings)

    response = await service.generate(MESSAGES)

    assert str(provider_api.last.url) == "https://llm.example.com/v1/chat/completions"
    assert provider_api.last.headers["authorization"] == f"Bearer {OTHER_KEY}"
    assert response.provider == "openai_compatible" and response.model == "gpt-4o-mini-2024"
    assert response.total_tokens is None


# --- errors ---------------------------------------------------------------------------------------------------


async def test_missing_api_key_fails_before_any_request(provider_api: FakeProvider) -> None:
    service, usage, *_ = make_service(provider_api, make_settings(nvidia_model=MODEL))
    with pytest.raises(ProviderConfigurationError) as error:
        await service.generate(MESSAGES)
    assert error.value.status_code == 503 and error.value.code == "AI_PROVIDER_NOT_CONFIGURED"
    assert provider_api.requests == [] and usage.entries == []


async def test_closed_client_is_a_configuration_error(provider_api: FakeProvider) -> None:
    service, _, _, http = make_service(provider_api)
    await http.aclose()
    with pytest.raises(ProviderConfigurationError):
        await service.generate(MESSAGES)


@pytest.mark.parametrize(
    ("status", "error_type", "category"),
    [
        (400, ProviderResponseError, "invalid_request"),
        (401, ProviderAuthenticationError, "authentication"),
        (403, ProviderAuthenticationError, "authentication"),
        (404, ProviderConfigurationError, "configuration"),
        (422, ProviderResponseError, "invalid_request"),
        (500, ProviderUnavailableError, "unavailable"),
        (504, ProviderTimeoutError, "timeout"),
        (302, ProviderResponseError, "malformed_response"),
    ],
)
async def test_non_retryable_http_errors(provider_api: FakeProvider, status: int, error_type: type, category: str) -> None:
    provider_api.reply(status, {"error": {"message": f"key {API_KEY} is bad; prompt: Explain revenue", "type": "bad_thing"}},
                       headers={"location": "https://elsewhere.example"} if status == 302 else None)
    service, usage, sleeps, _ = make_service(provider_api)

    with pytest.raises(error_type) as error:
        await service.generate(MESSAGES)

    assert error.value.category.value == category and error.value.http_status == status
    assert len(provider_api.requests) == 1 and sleeps.delays == []  # never retried
    assert API_KEY not in error.value.message and "Explain revenue" not in error.value.message
    [entry] = usage.entries
    assert entry.status == "failure" and entry.error_category == category and entry.attempts == 1
    assert entry.input_tokens is None


@pytest.mark.parametrize(
    "body",
    [
        {"choices": []},
        {"choices": [{"message": {"role": "assistant"}}]},
        {"choices": [{"message": {"content": None}}]},
        {"choices": ["text"]},
        {"no": "choices"},
        ["not", "an", "object"],
    ],
)
async def test_malformed_responses(provider_api: FakeProvider, body: Any) -> None:
    provider_api.reply(200, body)
    service, usage, *_ = make_service(provider_api)
    with pytest.raises(ProviderResponseError) as error:
        await service.generate(MESSAGES)
    assert error.value.category is ProviderErrorCategory.MALFORMED_RESPONSE
    assert usage.entries[0].status == "failure"


async def test_non_json_and_oversized_responses_are_malformed(provider_api: FakeProvider) -> None:
    provider_api.reply(200, raw=b"<html>gateway</html>", content_type="text/html")
    service, *_ = make_service(provider_api)
    with pytest.raises(ProviderResponseError):
        await service.generate(MESSAGES)

    big = FakeProvider().reply(200, raw=b"{" + b" " * (5 * 1024 * 1024) + b"}")
    service, *_ = make_service(big)
    with pytest.raises(ProviderResponseError, match="too large"):
        await service.generate(MESSAGES)


async def test_read_timeout_is_not_retried_because_the_outcome_is_ambiguous(provider_api: FakeProvider) -> None:
    provider_api.fail(httpx.ReadTimeout("slow"))
    service, usage, sleeps, _ = make_service(provider_api)
    with pytest.raises(ProviderTimeoutError) as error:
        await service.generate(MESSAGES)
    assert error.value.status_code == 504 and not error.value.retryable
    assert len(provider_api.requests) == 1 and sleeps.delays == []
    assert usage.entries[0].error_category == "timeout"


async def test_dropped_connection_mid_response_is_not_retried(provider_api: FakeProvider) -> None:
    provider_api.fail(httpx.RemoteProtocolError("peer closed"))
    service, _, sleeps, _ = make_service(provider_api)
    with pytest.raises(ProviderConnectionError):
        await service.generate(MESSAGES)
    assert len(provider_api.requests) == 1 and sleeps.delays == []


# --- retries ---------------------------------------------------------------------------------------------------


async def test_connection_failures_are_retried_with_bounded_backoff(provider_api: FakeProvider) -> None:
    provider_api.fail(httpx.ConnectError("refused")).fail(httpx.ConnectTimeout("slow connect")).reply(200)
    service, usage, sleeps, _ = make_service(provider_api)

    response = await service.generate(MESSAGES)

    assert response.attempts == 3 and len(provider_api.requests) == 3
    assert sleeps.delays == [0.5, 1.0]  # exponential backoff (jitter fixed at 1.0)
    assert usage.entries[0].status == "success" and usage.entries[0].attempts == 3


async def test_retries_stop_at_the_configured_maximum(provider_api: FakeProvider) -> None:
    provider_api.reply(503)
    service, usage, sleeps, _ = make_service(provider_api, nvidia_settings(ai_max_retries=2))
    with pytest.raises(ProviderUnavailableError):
        await service.generate(MESSAGES)
    assert len(provider_api.requests) == 3 and len(sleeps.delays) == 2
    assert usage.entries[0].attempts == 3 and usage.entries[0].status == "failure"


async def test_retries_can_be_disabled(provider_api: FakeProvider) -> None:
    provider_api.reply(502)
    service, _, sleeps, _ = make_service(provider_api, nvidia_settings(ai_max_retries=0))
    with pytest.raises(ProviderUnavailableError):
        await service.generate(MESSAGES)
    assert len(provider_api.requests) == 1 and sleeps.delays == []


async def test_backoff_is_capped(provider_api: FakeProvider) -> None:
    provider_api.fail(httpx.ConnectError("refused"))
    service, _, sleeps, _ = make_service(provider_api, nvidia_settings(ai_max_retries=5, ai_retry_max_wait_seconds=1.5))
    with pytest.raises(ProviderConnectionError):
        await service.generate(MESSAGES)
    assert sleeps.delays == [0.5, 1.0, 1.5, 1.5, 1.5]


async def test_rate_limit_respects_retry_after(provider_api: FakeProvider) -> None:
    provider_api.reply(429, {"error": "slow down"}, headers={"retry-after": "3"}).reply(200)
    service, _, sleeps, _ = make_service(provider_api)
    response = await service.generate(MESSAGES)
    assert sleeps.delays == [3.0] and response.attempts == 2


async def test_rate_limit_with_http_date_retry_after(provider_api: FakeProvider) -> None:
    provider_api.reply(429, headers={"retry-after": "Wed, 21 Oct 2015 07:28:00 GMT"}).reply(200)
    service, _, sleeps, _ = make_service(provider_api)
    await service.generate(MESSAGES)
    assert sleeps.delays == [0.0]  # a date in the past means "now"


async def test_rate_limit_longer_than_allowed_wait_fails_without_retry(provider_api: FakeProvider) -> None:
    provider_api.reply(429, headers={"retry-after": "120"})
    service, usage, sleeps, _ = make_service(provider_api, nvidia_settings(ai_retry_max_wait_seconds=10))
    with pytest.raises(ProviderRateLimitError) as error:
        await service.generate(MESSAGES)
    assert sleeps.delays == [] and len(provider_api.requests) == 1
    assert error.value.retry_after_seconds == 120 and error.value.headers == {"Retry-After": "120"}
    assert usage.entries[0].error_category == "rate_limited"


async def test_rate_limit_persisting_after_retries_raises_rate_limit_error(provider_api: FakeProvider) -> None:
    provider_api.reply(429)
    service, _, sleeps, _ = make_service(provider_api, nvidia_settings(ai_max_retries=1))
    with pytest.raises(ProviderRateLimitError):
        await service.generate(MESSAGES)
    assert len(provider_api.requests) == 2 and len(sleeps.delays) == 1


async def test_authentication_failure_is_never_retried(provider_api: FakeProvider) -> None:
    provider_api.reply(401)
    service, _, sleeps, _ = make_service(provider_api, nvidia_settings(ai_max_retries=5))
    with pytest.raises(ProviderAuthenticationError):
        await service.generate(MESSAGES)
    assert len(provider_api.requests) == 1 and sleeps.delays == []


# --- observability --------------------------------------------------------------------------------------------


async def test_logs_have_events_but_no_prompts_answers_or_keys(provider_api: FakeProvider, caplog: pytest.LogCaptureFixture) -> None:
    provider_api.reply(503).reply(200, completion("The secret answer"))
    service, *_ = make_service(provider_api)
    with caplog.at_level(logging.DEBUG, logger="app.ai"):
        response = await service.generate(MESSAGES, metadata={"feature": "explain"})

    events = [getattr(r, "event", None) for r in caplog.records]
    assert {"ai.request.started", "ai.request.retry", "ai.request.completed"} <= set(events)
    completed = next(r for r in caplog.records if getattr(r, "event", None) == "ai.request.completed")
    assert completed.llm_request_id == response.request_id and completed.usage_total == 23  # type: ignore[attr-defined]
    assert completed.meta_feature == "explain"  # type: ignore[attr-defined]
    text = " ".join(json.dumps(vars(r), default=str) for r in caplog.records)
    for forbidden in (API_KEY, "Explain what total revenue", "business intelligence assistant", "The secret answer"):
        assert forbidden not in text


async def test_failures_and_rate_limits_are_logged(provider_api: FakeProvider, caplog: pytest.LogCaptureFixture) -> None:
    provider_api.reply(429, headers={"retry-after": "500"})
    service, *_ = make_service(provider_api)
    with caplog.at_level(logging.INFO, logger="app.ai"), pytest.raises(ProviderRateLimitError):
        await service.generate(MESSAGES)
    events = [getattr(r, "event", None) for r in caplog.records]
    assert "ai.request.rate_limited" in events and "ai.provider.http_error" in events


# --- health checks --------------------------------------------------------------------------------------------


async def test_health_check_lists_models_without_generating(provider_api: FakeProvider) -> None:
    service, usage, *_ = make_service(provider_api)
    health = await service.health_check()
    assert health.status is ProviderHealthStatus.HEALTHY and health.model_available is True
    assert provider_api.last.method == "GET" and str(provider_api.last.url) == f"{NVIDIA_URL}/models"
    assert provider_api.last.headers["authorization"] == f"Bearer {API_KEY}"
    assert usage.entries == []


async def test_health_check_reports_unknown_model(provider_api: FakeProvider) -> None:
    provider_api.models = ["someone/else"]
    service, *_ = make_service(provider_api)
    health = await service.health_check()
    assert health.status is ProviderHealthStatus.HEALTHY and health.model_available is False
    assert "does not include" in health.message


@pytest.mark.parametrize(
    ("setup", "category"),
    [
        (lambda f: f.reply(401), "authentication"),
        (lambda f: f.fail(httpx.ConnectError("refused")), "connection"),
        (lambda f: f.fail(httpx.ReadTimeout("slow")), "timeout"),
        (lambda f: f.reply(200, raw=b"nope", content_type="text/plain"), "malformed_response"),
    ],
)
async def test_health_check_reports_failures_without_raising(provider_api: FakeProvider, setup: Any, category: str) -> None:
    setup(provider_api)
    service, *_ = make_service(provider_api)
    health = await service.health_check()
    assert health.status is ProviderHealthStatus.UNHEALTHY and health.error_category == category
    assert API_KEY not in health.model_dump_json()


async def test_health_check_without_configuration_makes_no_request(provider_api: FakeProvider) -> None:
    service, *_ = make_service(provider_api, make_settings())
    health = await service.health_check()
    assert health.status is ProviderHealthStatus.NOT_CONFIGURED and "NVIDIA_API_KEY" in health.message
    assert provider_api.requests == []


# --- provider replacement ---------------------------------------------------------------------------------------


class StaticProvider(LLMProvider):
    """A provider that is not HTTP at all: proves LLMService depends only on the interface."""

    async def generate(self, messages: Sequence[LLMMessage], *, model: str, temperature: float, max_tokens: int,
                       request_id: str) -> ProviderCompletion:
        return ProviderCompletion(content=f"{len(messages)} messages", model=model)

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth(provider=self.name, model=self.config.model, status=ProviderHealthStatus.HEALTHY,
                              message="ok", checked_at=datetime.now(UTC))


async def test_any_provider_implementation_can_replace_the_active_one() -> None:
    config = ProviderConfigService(nvidia_settings()).active()
    service = LLMService(configs=ProviderConfigService(nvidia_settings()), http=None, provider=StaticProvider(config))

    response = await service.generate(MESSAGES)
    assert response.content == "2 messages" and response.provider == "nvidia" and response.total_tokens is None
    assert (await service.health_check()).status is ProviderHealthStatus.HEALTHY


def test_errors_are_app_errors_with_safe_envelopes() -> None:
    for error in (ProviderConfigurationError(), ProviderAuthenticationError(), ProviderRateLimitError(retry_after_seconds=2.2),
                  ProviderTimeoutError(), ProviderConnectionError(), ProviderResponseError(), ProviderUnavailableError()):
        assert isinstance(error, ProviderError) and error.code.startswith("AI_PROVIDER_")
        assert error.details == {"category": error.category.value}
    assert ProviderRateLimitError(retry_after_seconds=2.2).headers == {"Retry-After": "3"}


def test_nvidia_provider_reuses_the_openai_compatible_adapter() -> None:
    assert issubclass(NvidiaProvider, OpenAICompatibleProvider)

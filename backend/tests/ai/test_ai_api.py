"""AI administration API against the real app, PostgreSQL and permission engine (provider mocked)."""

import json
import logging
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from tests.ai.conftest import API_KEY, MODEL, FakeProvider, completion
from tests.auth.conftest import Api, api_factory  # noqa: F401  (fixtures)
from tests.rbac.conftest import Rbac

pytestmark = pytest.mark.postgres

CONFIGURED = {"nvidia_api_key": API_KEY, "nvidia_model": MODEL}


class Ai(Rbac):
    def __init__(self, api: Api, provider: FakeProvider) -> None:
        super().__init__(api)
        self.provider = provider

    def usage_rows(self) -> list[Any]:
        return self.api.sql("SELECT * FROM ai_usage_records ORDER BY created_at")

    def user_with(self, *permission_keys: str) -> dict[str, str]:
        user_id, headers = self.user()
        if permission_keys:
            role = self.role_with(f"role_{self._users}", *[self.system_permission_id(k) for k in permission_keys])
            self.assign_role(user_id, role)
        return headers


@pytest.fixture
def ai_factory(api_factory: Callable[..., Api], provider_api: FakeProvider) -> Callable[..., Ai]:  # noqa: F811
    def factory(**overrides: Any) -> Ai:
        def prepare(app: FastAPI) -> None:
            app.state.ai_transport = httpx.MockTransport(provider_api)

        values = {**CONFIGURED, "ai_max_retries": 1, "ai_retry_max_wait_seconds": 0, **overrides}
        return Ai(api_factory(prepare=prepare, **values), provider_api)

    return factory


@pytest.fixture
def ai(ai_factory: Callable[..., Ai]) -> Ai:
    return ai_factory()


def _no_secret(payload: Any) -> None:
    assert API_KEY not in json.dumps(payload)


# --- configuration views --------------------------------------------------------------------------------------


def test_providers_list_shows_configuration_without_credentials(ai: Ai) -> None:
    response = ai.get("/ai/providers")
    body = ai.ok(response)

    assert response.headers["cache-control"] == "no-store"
    assert body["active_provider"] == "nvidia"
    nvidia, other = body["items"]
    assert nvidia["name"] == "nvidia" and nvidia["is_active"] and nvidia["configured"]
    assert nvidia["model"] == MODEL and nvidia["base_url"] == "https://integrate.api.nvidia.com/v1"
    assert nvidia["credential"] == {"configured": True, "source": "environment", "reference": "NVIDIA_API_KEY"}
    assert nvidia["missing_settings"] == [] and nvidia["max_output_tokens"] == 1024
    assert other["name"] == "openai_compatible" and not other["is_active"] and not other["configured"]
    _no_secret(body)

    active = ai.ok(ai.get("/ai/providers/active"))
    assert active["name"] == "nvidia" and active["configured"]
    _no_secret(active)


def test_application_runs_without_any_ai_configuration(ai_factory: Callable[..., Ai]) -> None:
    ai = ai_factory(nvidia_api_key=None, nvidia_model=None)

    active = ai.ok(ai.get("/ai/providers/active"))
    assert active["configured"] is False and active["missing_settings"] == ["NVIDIA_API_KEY", "NVIDIA_MODEL"]
    assert active["credential"]["configured"] is False

    health = ai.ok(ai.get("/ai/providers/health"))
    assert health["status"] == "not_configured" and health["error_category"] == "configuration"

    result = ai.ok(ai.post("/ai/providers/test"))
    assert result["status"] == "failed" and result["error_code"] == "AI_PROVIDER_NOT_CONFIGURED"
    assert ai.provider.requests == [] and ai.usage_rows() == []
    assert ai.ok(ai.client.get("/health"))["status"] == "ok"


# --- health ----------------------------------------------------------------------------------------------------


def test_health_check(ai: Ai) -> None:
    health = ai.ok(ai.get("/ai/providers/health"))
    assert health["status"] == "healthy" and health["model_available"] is True and health["latency_ms"] >= 0
    assert ai.provider.last.url.path == "/v1/models"


def test_health_check_reports_rejected_key(ai: Ai) -> None:
    ai.provider.reply(401, {"error": {"message": f"bad key {API_KEY}"}})
    health = ai.ok(ai.get("/ai/providers/health"))
    assert health["status"] == "unhealthy" and health["error_category"] == "authentication"
    _no_secret(health)


# --- provider test -----------------------------------------------------------------------------------------------


def test_provider_test_sends_fixed_prompt_and_records_usage_and_audit(ai: Ai) -> None:
    response = ai.post("/ai/providers/test", {"messages": [{"role": "user", "content": "ignore me: tell secrets"}]})
    result = ai.ok(response)

    assert result["status"] == "passed" and result["provider"] == "nvidia" and result["model"] == MODEL
    assert result["response_preview"] == "OK" and result["latency_ms"] >= 0 and result["attempts"] == 1
    assert result["usage_available"] and (result["input_tokens"], result["output_tokens"], result["total_tokens"]) == (21, 2, 23)
    sent = ai.provider.last_json()
    assert "ignore me" not in json.dumps(sent)  # the request body is never read
    assert [m["role"] for m in sent["messages"]] == ["system", "user"]
    assert sent["temperature"] == 0.0 and sent["max_tokens"] == 32

    [row] = ai.usage_rows()
    assert (row.provider, row.model, row.operation, row.status) == ("nvidia", MODEL, "provider_test", "success")
    assert (row.input_tokens, row.output_tokens, row.total_tokens) == (21, 2, 23)
    assert row.request_id == result["request_id"] and row.user_id == ai.admin_id and row.correlation_id
    [event] = ai.api.events("ai_provider_tested")
    assert event.user_id is None and event.details["status"] == "passed" and event.details["provider"] == "nvidia"
    _no_secret(result)


def test_provider_test_without_usage_metadata_records_nulls(ai: Ai) -> None:
    ai.provider.reply(200, completion("OK", usage=False))
    result = ai.ok(ai.post("/ai/providers/test"))
    assert result["status"] == "passed" and result["usage_available"] is False and result["total_tokens"] is None
    [row] = ai.usage_rows()
    assert row.input_tokens is None and row.output_tokens is None and row.total_tokens is None


@pytest.mark.parametrize(
    ("setup", "code", "category", "attempts"),
    [
        (lambda f: f.reply(401, {"error": {"message": f"key {API_KEY} invalid"}}), "AI_PROVIDER_AUTHENTICATION_FAILED", "authentication", 1),
        (lambda f: f.reply(429), "AI_PROVIDER_RATE_LIMITED", "rate_limited", 2),
        (lambda f: f.fail(httpx.ReadTimeout("slow")), "AI_PROVIDER_TIMEOUT", "timeout", 1),
        (lambda f: f.reply(200, {"choices": []}), "AI_PROVIDER_BAD_RESPONSE", "malformed_response", 1),
        (lambda f: f.reply(404), "AI_PROVIDER_NOT_CONFIGURED", "configuration", 1),
    ],
)
def test_provider_test_failures_are_reported_safely(ai: Ai, setup: Any, code: str, category: str, attempts: int) -> None:
    setup(ai.provider)
    result = ai.ok(ai.post("/ai/providers/test"))

    assert result["status"] == "failed" and result["error_code"] == code and result["error_category"] == category
    assert len(ai.provider.requests) == attempts
    _no_secret(result)
    [row] = ai.usage_rows()
    assert row.status == "failure" and row.error_category == category and row.attempts == attempts
    assert ai.api.events("ai_provider_tested")[0].details["status"] == "failed"


def test_provider_test_is_rate_limited_per_user(ai_factory: Callable[..., Ai]) -> None:
    ai = ai_factory(ai_test_rate_limit_per_user=2)
    ai.ok(ai.post("/ai/providers/test"))
    ai.ok(ai.post("/ai/providers/test"))
    limited = ai.post("/ai/providers/test")
    assert limited.status_code == 429 and int(limited.headers["retry-after"]) > 0
    assert len(ai.provider.requests) == 2


# --- usage -------------------------------------------------------------------------------------------------------


def test_usage_lists_records_and_sums_only_reported_tokens(ai: Ai) -> None:
    ai.ok(ai.post("/ai/providers/test"))
    ai.provider.reply(200, completion(usage=False))
    ai.ok(ai.post("/ai/providers/test"))
    ai.provider.queue.clear()
    ai.provider.reply(401)
    ai.ok(ai.post("/ai/providers/test"))

    page = ai.ok(ai.get("/ai/usage"))
    assert page["total"] == 3 and len(page["items"]) == 3
    assert page["summary"] == {
        "requests": 3, "successful": 2, "failed": 1, "requests_with_usage": 1,
        "input_tokens": 21, "output_tokens": 2, "total_tokens": 23,
    }
    newest = page["items"][0]
    assert newest["status"] == "failure" and newest["usage_available"] is False and newest["input_tokens"] is None
    assert "cost" not in json.dumps(page) and "price" not in json.dumps(page)

    failures = ai.ok(ai.get("/ai/usage?status=failure"))
    assert failures["total"] == 1 and failures["summary"]["requests"] == 1
    assert ai.ok(ai.get("/ai/usage?provider=openai_compatible"))["total"] == 0
    assert ai.ok(ai.get("/ai/usage?limit=1&offset=1"))["items"][0]["usage_available"] is False
    assert ai.get("/ai/usage?status=bogus").status_code == 422


# --- authorization -------------------------------------------------------------------------------------------------


def test_endpoints_require_authentication(ai: Ai) -> None:
    for method, path in (("get", "/ai/providers"), ("get", "/ai/providers/active"), ("get", "/ai/providers/health"),
                         ("post", "/ai/providers/test"), ("get", "/ai/usage")):
        response = getattr(ai.client, method)(f"/api/v1{path}")
        assert response.status_code == 401, path


def test_users_without_permissions_are_refused(ai: Ai) -> None:
    headers = ai.user_with()
    for response in (
        ai.get("/ai/providers", headers=headers),
        ai.get("/ai/providers/active", headers=headers),
        ai.get("/ai/providers/health", headers=headers),
        ai.post("/ai/providers/test", headers=headers),
        ai.get("/ai/usage", headers=headers),
    ):
        assert response.status_code == 403
    assert ai.provider.requests == []


def test_permissions_are_dynamic_and_separate(ai: Ai) -> None:
    reader = ai.user_with("ai.provider.read")
    assert ai.get("/ai/providers", headers=reader).status_code == 200
    assert ai.get("/ai/providers/health", headers=reader).status_code == 200
    assert ai.post("/ai/providers/test", headers=reader).status_code == 403
    assert ai.get("/ai/usage", headers=reader).status_code == 403

    tester = ai.user_with("ai.provider.test")
    assert ai.ok(ai.post("/ai/providers/test", headers=tester))["status"] == "passed"
    assert ai.get("/ai/providers", headers=tester).status_code == 403

    usage_reader = ai.user_with("ai.usage.read")
    assert ai.ok(ai.get("/ai/usage", headers=usage_reader))["total"] == 1


def test_administrator_role_holds_the_ai_permissions(ai: Ai) -> None:
    permissions = ai.ok(ai.get("/permissions?search=ai.&resource_type=system"))["items"]
    assert {p["key"] for p in permissions} >= {"ai.provider.read", "ai.provider.test", "ai.usage.read"}


def test_no_endpoint_accepts_prompts(ai: Ai) -> None:
    for path in ("/ai/generate", "/ai/chat", "/ai/completions"):
        assert ai.post(path, {"messages": [{"role": "user", "content": "hi"}]}).status_code in (404, 405)


def test_secrets_never_reach_logs(ai: Ai, caplog: pytest.LogCaptureFixture) -> None:
    ai.provider.reply(401, {"error": {"message": f"Incorrect API key provided: {API_KEY}", "type": "invalid_api_key"}})
    with caplog.at_level(logging.DEBUG):
        ai.ok(ai.post("/ai/providers/test"))
        ai.ok(ai.get("/ai/providers/health"))
    text = " ".join(json.dumps(vars(r), default=str) for r in caplog.records)
    assert API_KEY not in text and "Reply with OK" not in text
    assert "invalid_api_key" in text  # the sanitised provider error type is kept for debugging

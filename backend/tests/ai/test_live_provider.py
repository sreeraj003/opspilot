"""Optional manual integration test against the real configured provider.

Skipped unless you opt in, so automated runs never need an API key or spend tokens::

    # from a development checkout; NVIDIA_API_KEY / NVIDIA_MODEL come from the root .env
    cd backend
    OPSPILOT_LIVE_AI_TESTS=1 pytest -m live_ai -q        # PowerShell: $env:OPSPILOT_LIVE_AI_TESTS="1"; pytest -m live_ai -q

It sends the same fixed, harmless test prompt as the administration page.
"""

import os

import pytest

from app.ai.config.provider_config import ProviderConfigService
from app.ai.providers.http import create_ai_http_client
from app.ai.schemas.llm import ProviderHealthStatus
from app.ai.services.llm_service import LLMService
from app.ai.services.provider_admin_service import TEST_MESSAGES
from app.core.config import Settings

pytestmark = pytest.mark.live_ai


@pytest.fixture
def live_settings() -> Settings:
    if os.environ.get("OPSPILOT_LIVE_AI_TESTS") != "1":
        pytest.skip("Set OPSPILOT_LIVE_AI_TESTS=1 to run the live AI provider test")
    settings = Settings()  # reads the real environment / .env
    if not settings.ai_configured:
        pytest.skip("The active AI provider is not configured (API key and model)")
    return settings


async def test_live_provider_health_and_generation(live_settings: Settings) -> None:
    async with create_ai_http_client() as http:
        service = LLMService(configs=ProviderConfigService(live_settings), http=http)

        health = await service.health_check()
        assert health.status is ProviderHealthStatus.HEALTHY, health.message

        response = await service.generate(list(TEST_MESSAGES), temperature=0.0, max_tokens=32, operation="live_test")
        assert response.provider == live_settings.ai_provider
        assert response.latency_ms > 0 and response.request_id.startswith("llm_")
        # Token counts are whatever the provider reported; they may legitimately be absent.
        if response.usage_available:
            assert (response.total_tokens or 0) >= (response.output_tokens or 0)

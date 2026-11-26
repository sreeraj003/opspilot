"""Maps provider names to implementations.

To add a provider: implement ``LLMProvider`` (or subclass
``OpenAICompatibleProvider`` if it speaks that protocol), add its settings and a
``ProviderConfig`` builder in ``app.ai.config.provider_config``, and register
the class here. Nothing that calls ``LLMService`` changes.
"""

from collections.abc import Callable

import httpx

from app.ai.config.provider_config import ProviderConfig, ProviderName
from app.ai.exceptions.errors import ProviderConfigurationError
from app.ai.providers.base import LLMProvider
from app.ai.providers.nvidia import NvidiaProvider
from app.ai.providers.openai_compatible import OpenAICompatibleProvider

ProviderFactory = Callable[[ProviderConfig, httpx.AsyncClient], LLMProvider]

PROVIDER_REGISTRY: dict[str, ProviderFactory] = {
    ProviderName.NVIDIA.value: NvidiaProvider,
    ProviderName.OPENAI_COMPATIBLE.value: OpenAICompatibleProvider,
}


def build_provider(config: ProviderConfig, http: httpx.AsyncClient) -> LLMProvider:
    factory = PROVIDER_REGISTRY.get(config.name)
    if factory is None:
        raise ProviderConfigurationError("The configured AI provider is not supported.", provider=config.name)
    return factory(config, http)

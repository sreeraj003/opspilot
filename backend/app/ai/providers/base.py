"""The provider interface every LLM backend implements.

A provider makes **one** attempt per call and reports failures as typed
``ProviderError`` subclasses, marking which ones are safe to retry.
Retries, latency, usage recording and logging are the job of ``LLMService``,
so they behave the same for every provider.
"""

from abc import ABC, abstractmethod
from collections.abc import Sequence

from app.ai.config.provider_config import ProviderConfig
from app.ai.schemas.llm import LLMMessage, ProviderCompletion, ProviderHealth


class LLMProvider(ABC):
    """Base class for providers. Subclasses must not log prompts, responses or keys."""

    def __init__(self, config: ProviderConfig) -> None:
        self.config = config

    @property
    def name(self) -> str:
        return self.config.name

    @abstractmethod
    async def generate(
        self,
        messages: Sequence[LLMMessage],
        *,
        model: str,
        temperature: float,
        max_tokens: int,
        request_id: str,
        json_output: bool = False,
    ) -> ProviderCompletion:
        """Run one chat completion and return it normalised. Raise ``ProviderError`` on failure.

        ``json_output`` asks for a JSON object where the provider supports it; the
        caller validates the content either way.
        """

    @abstractmethod
    async def health_check(self) -> ProviderHealth:
        """Check reachability and credentials cheaply (no generation). Never raises for provider failures."""

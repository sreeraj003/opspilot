"""NVIDIA API provider (``NVIDIA_API_KEY``, ``NVIDIA_BASE_URL``, ``NVIDIA_MODEL``).

NVIDIA's hosted models (``https://integrate.api.nvidia.com/v1``) speak the
OpenAI chat-completions protocol, so this provider reuses the OpenAI-compatible
adapter and its single shared HTTP client. Anything NVIDIA-specific belongs
here, so the generic adapter stays generic.
"""

from app.ai.providers.openai_compatible import OpenAICompatibleProvider


class NvidiaProvider(OpenAICompatibleProvider):
    """NVIDIA-hosted chat completions. Identical wire protocol to the OpenAI-compatible adapter."""

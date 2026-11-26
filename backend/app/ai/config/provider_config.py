"""Provider configuration service.

There is one configuration system in OpsPilot: ``app.core.config.Settings``
(environment variables / ``.env``). This module does not read the environment
itself; it turns those settings into one ``ProviderConfig`` per supported
provider, and says which one is active.

Credentials
-----------
API keys live only in the server environment (``NVIDIA_API_KEY``,
``OPENAI_COMPATIBLE_API_KEY``) as ``SecretStr``. They are never written to the
database, so no plaintext credential is stored anywhere. ``ProviderConfig``
keeps the key out of its ``repr``; ``public()`` views expose only whether a
key is present and the name of the variable it comes from (the *credential
reference*). If provider credentials are ever stored in the database, they
must use the Module 07 ``CredentialCipher`` (AES-256-GCM); see
docs/AI_PROVIDERS.md, "Adding a provider".
"""

from dataclasses import dataclass, field
from enum import StrEnum

from pydantic import SecretStr

from app.core.config import Settings


class ProviderName(StrEnum):
    NVIDIA = "nvidia"
    OPENAI_COMPATIBLE = "openai_compatible"


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    """Everything a provider needs to make a request. Immutable; built per settings object."""

    name: str
    display_name: str
    model: str | None
    base_url: str | None
    # Name of the environment variable holding the key: the "credential reference".
    credential_reference: str
    temperature: float
    max_output_tokens: int
    timeout_seconds: float
    max_retries: int
    retry_max_wait_seconds: float
    is_active: bool
    # Environment variables still missing before this provider can be used.
    missing_settings: tuple[str, ...] = ()
    api_key: SecretStr | None = field(default=None, repr=False)

    @property
    def configured(self) -> bool:
        return not self.missing_settings

    @property
    def has_credentials(self) -> bool:
        return self.api_key is not None

    def __repr__(self) -> str:  # never the key, not even masked
        return f"ProviderConfig(name={self.name!r}, model={self.model!r}, active={self.is_active})"


class ProviderConfigService:
    """Builds and selects provider configurations. Pure: no I/O, no secrets in output."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def active_name(self) -> str:
        return self._settings.ai_provider

    def all(self) -> list[ProviderConfig]:
        return [self._nvidia(), self._openai_compatible()]

    def get(self, name: str) -> ProviderConfig | None:
        return next((config for config in self.all() if config.name == name), None)

    def active(self) -> ProviderConfig:
        config = self.get(self.active_name)
        assert config is not None  # Settings restricts AI_PROVIDER to known names
        return config

    # --- per provider -------------------------------------------------------------------------

    def _common(self) -> dict[str, float | int]:
        s = self._settings
        return {
            "temperature": s.ai_temperature,
            "max_output_tokens": s.ai_max_output_tokens,
            "timeout_seconds": s.ai_request_timeout_seconds,
            "max_retries": s.ai_max_retries,
            "retry_max_wait_seconds": s.ai_retry_max_wait_seconds,
        }

    def _nvidia(self) -> ProviderConfig:
        s = self._settings
        missing = [name for name, value in (("NVIDIA_API_KEY", s.nvidia_api_key), ("NVIDIA_MODEL", s.nvidia_model)) if value is None]
        return ProviderConfig(
            name=ProviderName.NVIDIA.value,
            display_name="NVIDIA",
            model=s.nvidia_model,
            base_url=s.nvidia_base_url,
            credential_reference="NVIDIA_API_KEY",
            is_active=s.ai_provider == ProviderName.NVIDIA.value,
            missing_settings=tuple(missing),
            api_key=s.nvidia_api_key,
            **self._common(),  # type: ignore[arg-type]
        )

    def _openai_compatible(self) -> ProviderConfig:
        s = self._settings
        missing = [
            name
            for name, value in (
                ("OPENAI_COMPATIBLE_BASE_URL", s.openai_compatible_base_url),
                ("OPENAI_COMPATIBLE_API_KEY", s.openai_compatible_api_key),
                ("OPENAI_COMPATIBLE_MODEL", s.openai_compatible_model),
            )
            if value is None
        ]
        return ProviderConfig(
            name=ProviderName.OPENAI_COMPATIBLE.value,
            display_name="OpenAI-compatible",
            model=s.openai_compatible_model,
            base_url=s.openai_compatible_base_url,
            credential_reference="OPENAI_COMPATIBLE_API_KEY",
            is_active=s.ai_provider == ProviderName.OPENAI_COMPATIBLE.value,
            missing_settings=tuple(missing),
            api_key=s.openai_compatible_api_key,
            **self._common(),  # type: ignore[arg-type]
        )

"""Centralized application configuration.

Settings are read from the process environment first, then from ``.env`` files
(the repository root ``.env`` and, if present, ``backend/.env``). Secrets are
held in ``SecretStr`` so they never appear in logs, reprs or error messages.
"""

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

APP_VERSION = "1.0.0"
APP_DESCRIPTION = "AI-powered Business Intelligence Assistant."

_BACKEND_DIR = Path(__file__).resolve().parents[2]
# Later files take priority: backend/.env overrides the repository root .env.
_ENV_FILES = (_BACKEND_DIR.parent / ".env", _BACKEND_DIR / ".env")

_MIN_SECRET_KEY_LENGTH = 32
_POSTGRES_SCHEMES = ("postgresql", "postgres")


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


def _blank_to_none(value: object) -> object:
    if isinstance(value, str) and not value.strip():
        return None
    return value


class Settings(BaseSettings):
    """Typed settings for one OpsPilot AI installation."""

    model_config = SettingsConfigDict(
        env_file=_ENV_FILES,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        # Validation errors must never echo raw input: it may be a password or API key.
        hide_input_in_errors=True,
    )

    app_name: str = Field(default="OpsPilot AI", min_length=1)
    app_env: Environment = Environment.DEVELOPMENT
    app_debug: bool = False
    app_host: str = "0.0.0.0"
    app_port: int = Field(default=8000, ge=1, le=65535)

    database_url: SecretStr | None = None
    database_pool_size: int = Field(default=5, ge=1, le=100)
    database_max_overflow: int = Field(default=10, ge=0, le=100)
    database_connect_timeout_seconds: int = Field(default=5, ge=1, le=60)

    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["http://localhost:5173"])

    log_level: str = "INFO"

    secret_key: SecretStr | None = None

    # AI provider settings. Optional until the LLM module is implemented.
    nvidia_api_key: SecretStr | None = None
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    nvidia_model: str | None = None

    @field_validator("database_url", "secret_key", "nvidia_api_key", "nvidia_model", mode="before")
    @classmethod
    def _empty_is_unset(cls, value: object) -> object:
        return _blank_to_none(value)

    @field_validator("database_url")
    @classmethod
    def _normalize_database_url(cls, value: SecretStr | None) -> SecretStr | None:
        """Require PostgreSQL and use the psycopg 3 driver for plain postgres URLs."""
        if value is None:
            return None
        url = value.get_secret_value().strip()
        scheme, separator, rest = url.partition("://")
        if not separator:
            raise ValueError("DATABASE_URL must be a URL such as postgresql+psycopg://user:pass@host:5432/db")
        if scheme in _POSTGRES_SCHEMES:
            scheme = "postgresql+psycopg"
        if scheme != "postgresql+psycopg":
            # Message deliberately omits the URL: it may contain a password.
            raise ValueError("DATABASE_URL must be a PostgreSQL URL (postgresql+psycopg://...)")
        return SecretStr(f"{scheme}://{rest}")

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_cors_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip().rstrip("/") for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("cors_origins")
    @classmethod
    def _validate_cors_origins(cls, value: list[str]) -> list[str]:
        for origin in value:
            if origin == "*":
                raise ValueError("CORS_ORIGINS must list explicit origins; '*' is not allowed")
            if not origin.startswith(("http://", "https://")):
                raise ValueError(f"CORS origin must start with http:// or https://: {origin}")
        return value

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        level = value.strip().upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError(f"LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR or CRITICAL (got {value!r})")
        return level

    @field_validator("secret_key")
    @classmethod
    def _validate_secret_key_length(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and len(value.get_secret_value()) < _MIN_SECRET_KEY_LENGTH:
            raise ValueError(f"SECRET_KEY must be at least {_MIN_SECRET_KEY_LENGTH} characters")
        return value

    @field_validator("nvidia_base_url")
    @classmethod
    def _validate_nvidia_base_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value.startswith("https://"):
            raise ValueError("NVIDIA_BASE_URL must be an https:// URL")
        return value

    @model_validator(mode="after")
    def _validate_production_requirements(self) -> Self:
        if self.app_env is not Environment.PRODUCTION:
            return self
        problems: list[str] = []
        if self.app_debug:
            problems.append("APP_DEBUG must be false")
        if self.secret_key is None:
            problems.append("SECRET_KEY must be set")
        if self.database_url is None:
            problems.append("DATABASE_URL must be set")
        if problems:
            raise ValueError("Invalid production configuration: " + "; ".join(problems))
        return self

    @property
    def app_version(self) -> str:
        return APP_VERSION

    @property
    def is_production(self) -> bool:
        return self.app_env is Environment.PRODUCTION

    @property
    def database_configured(self) -> bool:
        return self.database_url is not None

    @property
    def ai_configured(self) -> bool:
        return self.nvidia_api_key is not None and self.nvidia_model is not None


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance (cached)."""
    return Settings()

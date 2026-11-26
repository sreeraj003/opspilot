"""Centralized application configuration.

Settings are read from the process environment first, then from ``.env`` files
(the repository root ``.env`` and, if present, ``backend/.env``). Secrets are
held in ``SecretStr`` so they never appear in logs, reprs or error messages.
"""

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal, Self

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
    # Seconds a request waits for a free pooled connection before failing with 503.
    database_pool_timeout_seconds: int = Field(default=10, ge=1, le=120)
    # Connections older than this are replaced, so server/firewall idle timeouts never hit a live request.
    database_pool_recycle_seconds: int = Field(default=1800, ge=60, le=86400)

    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["http://localhost:5173"])

    log_level: str = "INFO"

    secret_key: SecretStr | None = None

    # --- Authentication (Module 03) ----------------------------------------------
    # Access tokens are short-lived signed JWTs held in browser memory.
    access_token_expire_minutes: int = Field(default=15, ge=1, le=60)
    # Refresh tokens rotate on every use. The idle lifetime restarts on each
    # rotation; the absolute lifetime caps a session no matter how active it is.
    refresh_token_expire_days: int = Field(default=7, ge=1, le=30)
    session_absolute_max_days: int = Field(default=30, ge=1, le=365)
    # Seconds during which a just-rotated refresh token answers 409 (two tabs
    # refreshing at once) instead of being treated as stolen-token reuse.
    refresh_reuse_grace_seconds: int = Field(default=20, ge=0, le=120)
    # None: Secure cookies everywhere except APP_ENV=development/test (plain
    # http://localhost). Production refuses false.
    auth_cookie_secure: bool | None = None
    auth_cookie_samesite: Literal["strict", "lax"] = "strict"
    auth_cookie_domain: str | None = None
    # Store user agent and IP address on sessions (shown to administrators).
    auth_record_client_metadata: bool = True

    password_reset_token_expire_minutes: int = Field(default=30, ge=5, le=1440)
    # Lets an administrator generate a one-time reset link to hand to a user
    # (there is no email delivery yet).
    password_reset_admin_links_enabled: bool = True
    # Where the browser app lives; used to build password-reset links.
    # Defaults to the first CORS origin.
    frontend_base_url: str | None = None

    # Account throttling: after this many consecutive failures the account is
    # temporarily locked, doubling each further failure up to the maximum.
    login_max_failed_attempts: int = Field(default=5, ge=1, le=100)
    login_lockout_base_seconds: int = Field(default=60, ge=1, le=3600)
    login_lockout_max_seconds: int = Field(default=900, ge=1, le=86400)

    # In-process rate limits (per backend process; see ARCHITECTURE.md).
    auth_rate_limit_window_seconds: int = Field(default=300, ge=1, le=86400)
    auth_rate_limit_per_ip: int = Field(default=30, ge=1, le=100000)
    auth_rate_limit_per_account: int = Field(default=10, ge=1, le=100000)
    auth_refresh_rate_limit_per_ip: int = Field(default=300, ge=1, le=100000)

    # Permission engine (Module 04).
    # Deepest nesting of AND/OR groups allowed in a permission's row filters.
    authorization_policy_max_depth: int = Field(default=4, ge=1, le=8)
    # Most row-filter conditions one permission policy may contain.
    authorization_policy_max_conditions: int = Field(default=50, ge=1, le=500)
    # Users whose resolved grants are cached per process (bounded LRU).
    authorization_cache_size: int = Field(default=1024, ge=0, le=100000)

    # Schema registry (Module 06). Imported metadata is untrusted input: these bound it.
    # Largest accepted schema import request body, in bytes.
    schema_import_max_bytes: int = Field(default=5 * 1024 * 1024, ge=1024, le=50 * 1024 * 1024)
    # Deepest JSON nesting accepted in an import request (the format itself needs 6).
    schema_import_max_depth: int = Field(default=10, ge=6, le=32)
    # Most tables one schema source may hold, and most columns one table may hold.
    schema_max_tables: int = Field(default=2000, ge=1, le=20000)
    schema_max_columns_per_table: int = Field(default=1000, ge=1, le=5000)
    # Most relationships one schema source may hold.
    schema_max_relationships: int = Field(default=5000, ge=1, le=50000)

    # AI provider settings. Optional until the LLM module is implemented.
    nvidia_api_key: SecretStr | None = None
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    nvidia_model: str | None = None

    @field_validator(
        "database_url",
        "secret_key",
        "nvidia_api_key",
        "nvidia_model",
        "frontend_base_url",
        "auth_cookie_domain",
        "auth_cookie_secure",
        mode="before",
    )
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

    @field_validator("frontend_base_url")
    @classmethod
    def _validate_frontend_base_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().rstrip("/")
        if not value.startswith(("http://", "https://")):
            raise ValueError("FRONTEND_BASE_URL must start with http:// or https://")
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
        if self.auth_cookie_secure is False:
            problems.append("AUTH_COOKIE_SECURE must not be false (production requires HTTPS)")
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
    def cookie_secure(self) -> bool:
        if self.auth_cookie_secure is not None:
            return self.auth_cookie_secure
        return self.app_env not in (Environment.DEVELOPMENT, Environment.TEST)

    @property
    def public_frontend_url(self) -> str:
        if self.frontend_base_url:
            return self.frontend_base_url
        return self.cors_origins[0] if self.cors_origins else "http://localhost:5173"

    @property
    def ai_configured(self) -> bool:
        return self.nvidia_api_key is not None and self.nvidia_model is not None


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance (cached)."""
    return Settings()

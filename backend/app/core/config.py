"""Application configuration loaded from environment variables and an optional .env file."""

import secrets
from enum import StrEnum
from functools import lru_cache
from typing import Annotated, Self

from pydantic import Field, PrivateAttr, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class LogFormat(StrEnum):
    JSON = "json"
    CONSOLE = "console"


class Settings(BaseSettings):
    """Typed application settings.

    Values are read from the process environment first, then from ``.env``.
    Secrets (such as the database URL, which may embed a password) are held in
    ``SecretStr`` so they are never rendered in logs or reprs.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "OpsPilot AI API"
    app_version: str = "0.1.0"
    environment: Environment = Environment.DEVELOPMENT
    debug: bool = False
    api_v1_prefix: str = "/api/v1"

    log_level: str = "INFO"
    log_format: LogFormat = LogFormat.JSON

    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"]
    )

    database_url: SecretStr | None = None
    database_echo: bool = False
    database_pool_size: int = Field(default=5, ge=1, le=100)
    database_max_overflow: int = Field(default=10, ge=0, le=100)
    database_connect_timeout_seconds: int = Field(default=5, ge=1, le=60)

    # Authentication. JWT_SECRET_KEY is mandatory in staging and production; in
    # development and test an ephemeral random key is generated when it is unset,
    # which invalidates every token on restart.
    jwt_secret_key: SecretStr | None = None
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "opspilot-api"
    jwt_audience: str = "opspilot"
    access_token_expire_minutes: int = Field(default=15, ge=1, le=24 * 60)
    refresh_token_expire_days: int = Field(default=14, ge=1, le=90)
    refresh_cookie_name: str = "opspilot_refresh_token"
    refresh_cookie_secure: bool = True
    allow_public_registration: bool = True

    password_min_length: int = Field(default=12, ge=8, le=64)

    # Argon2id parameters (OWASP baseline: m=19 MiB, t=2, p=1 or stronger).
    argon2_time_cost: int = Field(default=3, ge=1, le=10)
    argon2_memory_cost_kib: int = Field(default=65536, ge=8, le=1048576)
    argon2_parallelism: int = Field(default=4, ge=1, le=16)

    # Login rate limiting (per client IP and per account).
    login_rate_limit_enabled: bool = True
    login_max_attempts_per_ip: int = Field(default=20, ge=1)
    login_max_failures_per_account: int = Field(default=5, ge=1)
    login_rate_limit_window_seconds: int = Field(default=900, ge=1)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_cors_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip().rstrip("/") for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("cors_origins")
    @classmethod
    def _reject_wildcard_origin(cls, value: list[str]) -> list[str]:
        # Credentials (Authorization header, refresh cookie) are allowed, so a
        # wildcard origin would let any site make authenticated requests.
        if "*" in value:
            raise ValueError("CORS_ORIGINS must list explicit origins; '*' is not allowed")
        return value

    @field_validator("jwt_secret_key", mode="before")
    @classmethod
    def _empty_secret_is_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("jwt_algorithm")
    @classmethod
    def _validate_jwt_algorithm(cls, value: str) -> str:
        if value not in {"HS256", "HS384", "HS512"}:
            raise ValueError("JWT_ALGORITHM must be one of HS256, HS384, HS512")
        return value

    @model_validator(mode="after")
    def _ensure_jwt_secret(self) -> Self:
        if self.jwt_secret_key is None:
            if self.environment in (Environment.STAGING, Environment.PRODUCTION):
                raise ValueError("JWT_SECRET_KEY must be set in staging and production")
            self.jwt_secret_key = SecretStr(secrets.token_urlsafe(48))
            self._jwt_secret_ephemeral = True
        elif len(self.jwt_secret_key.get_secret_value()) < 32:
            raise ValueError("JWT_SECRET_KEY must be at least 32 characters")
        return self

    @field_validator("database_url", mode="before")
    @classmethod
    def _empty_database_url_is_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        level = value.upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError(f"Invalid log level: {value}")
        return level

    @field_validator("api_v1_prefix")
    @classmethod
    def _validate_prefix(cls, value: str) -> str:
        if not value.startswith("/") or value.endswith("/"):
            raise ValueError("api_v1_prefix must start with '/' and must not end with '/'")
        return value

    _jwt_secret_ephemeral: bool = PrivateAttr(default=False)

    @property
    def jwt_secret_ephemeral(self) -> bool:
        """True when no JWT_SECRET_KEY was configured and a random one was generated."""
        return self._jwt_secret_ephemeral

    @property
    def database_configured(self) -> bool:
        return self.database_url is not None


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance (cached)."""
    return Settings()

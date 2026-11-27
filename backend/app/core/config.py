"""Centralized application configuration.

Settings are read from the process environment first, then from ``.env`` files
(the repository root ``.env`` and, if present, ``backend/.env``). Secrets are
held in ``SecretStr`` so they never appear in logs, reprs or error messages.
"""

import re
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
_AI_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")


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

    # External GET API connectors (Module 07). See docs/CONNECTORS.md.
    # AES-256-GCM key that encrypts connector credentials at rest: 32 random bytes,
    # base64-encoded. Must differ from SECRET_KEY. Without it, connector credentials
    # can be neither stored nor used (the rest of the application still works).
    connector_encryption_key: SecretStr | None = None
    # Retired keys, comma-separated, still accepted for decryption during a key rotation.
    connector_encryption_previous_keys: SecretStr | None = None
    # Administrator-approved destination hosts, comma-separated: exact names
    # ("api.company.com") or one leading wildcard label ("*.company.com"). Empty
    # means no connector destination is allowed (fail closed).
    connector_allowed_hosts: Annotated[list[str], NoDecode] = Field(default_factory=list)
    # Private, loopback or other non-public networks (CIDR) that connectors may reach.
    # Public addresses are always allowed for approved hosts; nothing else is, unless listed here.
    connector_allowed_private_networks: Annotated[list[str], NoDecode] = Field(default_factory=list)
    # Plain http:// destinations. Only honoured with APP_ENV=development or test.
    connector_allow_http: bool = False
    # Extra CA bundle (PEM file) for company backends signed by a private CA. TLS
    # verification itself can never be disabled.
    connector_ca_bundle: str | None = None
    # Longest request URL (scheme, host, path and encoded query) a connector may send.
    connector_max_url_length: int = Field(default=8192, ge=512, le=65536)
    # Ceilings for the per-connector limits administrators configure.
    connector_max_response_bytes_limit: int = Field(default=50 * 1024 * 1024, ge=1024, le=512 * 1024 * 1024)
    connector_max_timeout_seconds: int = Field(default=120, ge=1, le=600)
    # Pooled outbound connections shared by every connector (one client per process).
    connector_pool_max_connections: int = Field(default=20, ge=1, le=1000)
    connector_pool_max_keepalive: int = Field(default=10, ge=0, le=1000)

    # --- AI provider (Module 08). See docs/AI_PROVIDERS.md. ---------------------------------
    # Every AI setting is optional: without an API key and model the application still
    # starts and the provider simply reports itself as not configured.
    # Which provider LLMService uses: "nvidia" or "openai_compatible".
    ai_provider: Literal["nvidia", "openai_compatible"] = "nvidia"
    # NVIDIA API (OpenAI-compatible chat completions at NVIDIA_BASE_URL).
    nvidia_api_key: SecretStr | None = None
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    nvidia_model: str | None = None
    # Any other OpenAI-compatible endpoint (OpenAI, Azure OpenAI proxy, vLLM, a gateway, ...).
    openai_compatible_api_key: SecretStr | None = None
    openai_compatible_base_url: str | None = None
    openai_compatible_model: str | None = None
    # Generation defaults applied to every request; a request may lower max tokens, never raise it.
    ai_temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    ai_max_output_tokens: int = Field(default=1024, ge=1, le=32768)
    # Seconds one provider request may take (connect + response), per attempt.
    ai_request_timeout_seconds: float = Field(default=60.0, ge=1.0, le=600.0)
    # Extra attempts for transient failures only (connection refused, 429, 502, 503). 0 disables retries.
    ai_max_retries: int = Field(default=2, ge=0, le=5)
    # Longest wait between attempts, including a provider's Retry-After. Longer waits fail instead.
    ai_retry_max_wait_seconds: float = Field(default=20.0, ge=0.0, le=120.0)
    # Administrator "test provider" runs allowed per user per 5 minutes (each run spends tokens).
    ai_test_rate_limit_per_user: int = Field(default=10, ge=1, le=1000)
    # Ask the provider for a JSON object ("response_format": {"type": "json_object"}) when a
    # feature needs structured output. Off by default: not every model behind an
    # OpenAI-compatible endpoint accepts it. Output is validated strictly either way.
    ai_json_response_format: bool = False

    # --- Question understanding (Module 09). See ARCHITECTURE.md section 10. -----------------
    # Timezone used to resolve "today", "last month", ... when the application setting
    # "business.timezone" is not set. An IANA name; never the server's local timezone.
    business_timezone: str = "UTC"
    # Questions each user may submit for interpretation per minute (each one is an LLM call).
    ai_understand_rate_limit_per_user: int = Field(default=20, ge=1, le=1000)
    # Most tables sent to the model as context for one question (relevance-ranked).
    ai_understand_max_schema_tables: int = Field(default=12, ge=1, le=100)
    # Bounded correction requests when the model's output fails validation. 0 disables them.
    ai_understand_max_corrections: int = Field(default=1, ge=0, le=3)
    # Output token ceiling for one interpretation (still capped by AI_MAX_OUTPUT_TOKENS).
    ai_understand_max_output_tokens: int = Field(default=1500, ge=256, le=32768)

    # --- SQL generation (Module 10). See ARCHITECTURE.md section 11. --------------------------
    # SQL generation requests (generate + regenerate) each user may make per minute.
    ai_sql_rate_limit_per_user: int = Field(default=10, ge=1, le=1000)
    # Extra model requests allowed when generated SQL fails preliminary validation (0 to 3).
    ai_sql_max_regenerations: int = Field(default=2, ge=0, le=3)
    # Manual regenerations allowed for one intent (each one is a new generation record).
    ai_sql_max_manual_regenerations: int = Field(default=3, ge=0, le=10)
    # Output token ceiling for one SQL generation request (still capped by AI_MAX_OUTPUT_TOKENS).
    ai_sql_max_output_tokens: int = Field(default=1500, ge=256, le=32768)
    # Most tables sent to the model as SQL context (intent tables plus join paths).
    ai_sql_max_schema_tables: int = Field(default=8, ge=1, le=50)
    # Largest row limit a generated query may request; detail queries must always be limited.
    ai_sql_max_row_limit: int = Field(default=1000, ge=1, le=100000)
    # How long a stored business intent may be used for SQL generation (relative dates age).
    ai_sql_intent_max_age_minutes: int = Field(default=60, ge=1, le=10080)

    # --- SQL security and execution guard (Module 11) ---------------------------------------
    # Validation requests each user may make per minute.
    ai_sql_validation_rate_limit_per_user: int = Field(default=30, ge=1, le=1000)
    # Lifetime of an execution approval. Approvals are single-use and bound to the exact SQL.
    ai_sql_approval_ttl_seconds: int = Field(default=300, ge=10, le=3600)
    # Complexity limits, enforced on the parsed SQL (never on the text).
    ai_sql_guard_max_sql_length: int = Field(default=20000, ge=100, le=100000)
    ai_sql_guard_max_joins: int = Field(default=6, ge=0, le=50)
    ai_sql_guard_max_tables: int = Field(default=8, ge=1, le=50)
    ai_sql_guard_max_nesting_depth: int = Field(default=3, ge=0, le=20)
    ai_sql_guard_max_ctes: int = Field(default=5, ge=0, le=50)
    ai_sql_guard_max_subqueries: int = Field(default=6, ge=0, le=100)
    ai_sql_guard_max_expressions: int = Field(default=600, ge=10, le=20000)
    # Most rows an approved query may return. A missing limit is added; a larger one is rejected.
    ai_sql_guard_max_result_rows: int = Field(default=1000, ge=1, le=100000)
    # Longest execution the external API contract supports; connectors with a longer timeout get a warning.
    ai_sql_guard_max_execution_seconds: int = Field(default=30, ge=1, le=600)
    # Window functions (ROW_NUMBER, RANK, ...) and UNION / INTERSECT / EXCEPT must be enabled explicitly.
    ai_sql_guard_allow_window_functions: bool = False
    ai_sql_guard_allow_set_operations: bool = False
    # Queries over tables from more than one schema namespace (for example sales and hr).
    ai_sql_guard_allow_cross_schema: bool = False
    # Schema namespaces queries may use (empty: every registered namespace).
    ai_sql_guard_allowed_namespaces: Annotated[list[str], NoDecode] = Field(default_factory=list)
    # Extra approved functions as dialect:function (for example postgres:length or *:upper).
    ai_sql_guard_extra_functions: Annotated[list[str], NoDecode] = Field(default_factory=list)
    # Extra approved join rules beyond registered relationships: schema.table.column=schema.table.column.
    ai_sql_guard_approved_joins: Annotated[list[str], NoDecode] = Field(default_factory=list)

    @field_validator(
        "database_url",
        "secret_key",
        "nvidia_api_key",
        "nvidia_model",
        "openai_compatible_api_key",
        "openai_compatible_base_url",
        "openai_compatible_model",
        "frontend_base_url",
        "auth_cookie_domain",
        "auth_cookie_secure",
        "connector_encryption_key",
        "connector_encryption_previous_keys",
        "connector_ca_bundle",
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

    @field_validator(
        "connector_allowed_hosts",
        "connector_allowed_private_networks",
        "ai_sql_guard_allowed_namespaces",
        "ai_sql_guard_extra_functions",
        "ai_sql_guard_approved_joins",
        mode="before",
    )
    @classmethod
    def _parse_comma_list(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("connector_allowed_hosts")
    @classmethod
    def _validate_connector_hosts(cls, value: list[str]) -> list[str]:
        from app.connectors.security.destinations import normalize_host_pattern

        return [normalize_host_pattern(item) for item in value]

    @field_validator("connector_allowed_private_networks")
    @classmethod
    def _validate_connector_networks(cls, value: list[str]) -> list[str]:
        import ipaddress

        networks: list[str] = []
        for item in value:
            try:
                networks.append(str(ipaddress.ip_network(item, strict=False)))
            except ValueError:
                raise ValueError(f"CONNECTOR_ALLOWED_PRIVATE_NETWORKS entry is not a CIDR network: {item!r}") from None
        return networks

    @field_validator("connector_encryption_key", "connector_encryption_previous_keys")
    @classmethod
    def _validate_connector_keys(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        from app.connectors.security.credentials import parse_key

        for part in value.get_secret_value().split(","):
            if part.strip():
                parse_key(part)  # raises a ValueError that never contains the key
        return value

    @field_validator("business_timezone")
    @classmethod
    def _validate_business_timezone(cls, value: str) -> str:
        from app.core.timezones import timezone_problem

        value = value.strip()
        problem = timezone_problem(value)
        if problem is not None:
            raise ValueError(problem)
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

    @field_validator("openai_compatible_base_url")
    @classmethod
    def _validate_openai_compatible_base_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().rstrip("/")
        # http:// is accepted here for a local model server during development; the
        # production check below refuses it.
        from urllib.parse import urlsplit

        parts = urlsplit(value)
        if parts.scheme not in ("https", "http") or not parts.hostname or "@" in parts.netloc or parts.query:
            raise ValueError("OPENAI_COMPATIBLE_BASE_URL must be an https:// URL without credentials or query string")
        return value

    @field_validator("nvidia_api_key", "openai_compatible_api_key")
    @classmethod
    def _validate_ai_api_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        key = value.get_secret_value().strip()
        if any(ch.isspace() for ch in key) or not key.isprintable():
            # The message never includes the value.
            raise ValueError("AI provider API keys must be a single line without spaces")
        return SecretStr(key)

    @field_validator("nvidia_model", "openai_compatible_model")
    @classmethod
    def _validate_ai_model(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if len(value) > 200 or not _AI_MODEL_RE.fullmatch(value):
            raise ValueError("AI model names may contain letters, digits and . _ : / - only (at most 200 characters)")
        return value

    @model_validator(mode="after")
    def _validate_connector_key_is_dedicated(self) -> Self:
        key = self.connector_encryption_key
        if key is not None and self.secret_key is not None:
            if key.get_secret_value().strip() == self.secret_key.get_secret_value().strip():
                raise ValueError("CONNECTOR_ENCRYPTION_KEY must not be the same value as SECRET_KEY")
        return self

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
        if self.connector_allow_http:
            problems.append("CONNECTOR_ALLOW_HTTP must be false (connectors use HTTPS only)")
        if any(_is_loopback_network(n) for n in self.connector_allowed_private_networks):
            problems.append("CONNECTOR_ALLOWED_PRIVATE_NETWORKS must not include loopback networks")
        if self.auth_cookie_secure is False:
            problems.append("AUTH_COOKIE_SECURE must not be false (production requires HTTPS)")
        if self.openai_compatible_base_url and not self.openai_compatible_base_url.startswith("https://"):
            problems.append("OPENAI_COMPATIBLE_BASE_URL must be an https:// URL")
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
    def connector_http_allowed(self) -> bool:
        """Plain HTTP destinations: only when explicitly enabled in development or test."""
        return self.connector_allow_http and self.app_env in (Environment.DEVELOPMENT, Environment.TEST)

    @property
    def connector_loopback_allowed(self) -> bool:
        return self.app_env in (Environment.DEVELOPMENT, Environment.TEST)

    @property
    def ai_configured(self) -> bool:
        """True when the active AI provider has a key, a model and a base URL."""
        if self.ai_provider == "openai_compatible":
            return (
                self.openai_compatible_api_key is not None
                and self.openai_compatible_model is not None
                and self.openai_compatible_base_url is not None
            )
        return self.nvidia_api_key is not None and self.nvidia_model is not None


def _is_loopback_network(network: str) -> bool:
    import ipaddress

    parsed = ipaddress.ip_network(network, strict=False)
    loopback = ipaddress.ip_network("127.0.0.0/8") if parsed.version == 4 else ipaddress.ip_network("::1/128")
    return parsed.overlaps(loopback)  # type: ignore[arg-type]


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance (cached)."""
    return Settings()

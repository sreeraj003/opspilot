"""Configuration loading and validation."""

import pytest
from pydantic import ValidationError

from app.core.config import Environment, Settings
from tests.conftest import make_settings

STRONG_SECRET = "x" * 40


def test_defaults_allow_startup_without_optional_services(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("APP_NAME", "APP_ENV", "DATABASE_URL", "SECRET_KEY", "NVIDIA_API_KEY", "NVIDIA_MODEL", "CORS_ORIGINS"):
        monkeypatch.delenv(name, raising=False)

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.app_name == "OpsPilot AI"
    assert settings.app_env is Environment.DEVELOPMENT
    assert settings.app_version == "1.0.0"
    assert settings.app_port == 8000
    assert settings.database_url is None
    assert settings.secret_key is None
    assert settings.nvidia_api_key is None
    assert settings.ai_configured is False
    assert settings.nvidia_base_url == "https://integrate.api.nvidia.com/v1"
    assert settings.cors_origins == ["http://localhost:5173"]


def test_values_are_read_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_NAME", "Acme OpsPilot")
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("APP_DEBUG", "true")
    monkeypatch.setenv("APP_PORT", "9000")
    monkeypatch.setenv("LOG_LEVEL", "debug")
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:5173, https://bi.acme.example/")
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test")
    monkeypatch.setenv("NVIDIA_MODEL", "meta/llama-3.1-70b-instruct")

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.app_name == "Acme OpsPilot"
    assert settings.app_env is Environment.STAGING
    assert settings.app_debug is True
    assert settings.app_port == 9000
    assert settings.log_level == "DEBUG"
    assert settings.cors_origins == ["http://localhost:5173", "https://bi.acme.example"]
    assert settings.ai_configured is True


def test_empty_optional_values_are_treated_as_unset() -> None:
    settings = make_settings(database_url="", secret_key="  ", nvidia_api_key="", nvidia_model="")

    assert settings.database_url is None
    assert settings.secret_key is None
    assert settings.nvidia_api_key is None
    assert settings.nvidia_model is None


@pytest.mark.parametrize(
    ("raw", "expected_prefix"),
    [
        ("postgresql://u:p@db:5432/app", "postgresql+psycopg://"),
        ("postgres://u:p@db:5432/app", "postgresql+psycopg://"),
        ("postgresql+psycopg://u:p@db:5432/app", "postgresql+psycopg://"),
    ],
)
def test_postgres_urls_use_the_psycopg_driver(raw: str, expected_prefix: str) -> None:
    settings = make_settings(database_url=raw)

    assert settings.database_url is not None
    assert settings.database_url.get_secret_value().startswith(expected_prefix)


def test_non_postgres_database_url_is_rejected_without_echoing_it() -> None:
    with pytest.raises(ValidationError) as excinfo:
        make_settings(database_url="mysql://root:topsecret@db/app")

    assert "PostgreSQL" in str(excinfo.value)
    assert "topsecret" not in str(excinfo.value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("app_env", "qa"),
        ("log_level", "VERBOSE"),
        ("app_port", 70000),
        ("cors_origins", "*"),
        ("cors_origins", "localhost:5173"),
        ("secret_key", "too-short"),
        ("nvidia_base_url", "http://insecure.example/v1"),
    ],
)
def test_invalid_values_are_rejected(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        make_settings(**{field: value})


def test_production_requires_secret_key_database_and_no_debug() -> None:
    with pytest.raises(ValidationError) as excinfo:
        make_settings(app_env="production", app_debug=True)

    message = str(excinfo.value)
    assert "APP_DEBUG must be false" in message
    assert "SECRET_KEY must be set" in message
    assert "DATABASE_URL must be set" in message


def test_valid_production_configuration_loads() -> None:
    settings = make_settings(
        app_env="production",
        secret_key=STRONG_SECRET,
        database_url="postgresql://u:p@db:5432/app",
        cors_origins="https://bi.acme.example",
    )

    assert settings.is_production


def test_secrets_are_masked_in_repr() -> None:
    settings = make_settings(
        secret_key=STRONG_SECRET, nvidia_api_key="nvapi-secret", database_url="postgresql://u:dbpass@db/app"
    )

    rendered = repr(settings) + str(settings.model_dump())
    assert STRONG_SECRET not in rendered
    assert "nvapi-secret" not in rendered
    assert "dbpass" not in rendered

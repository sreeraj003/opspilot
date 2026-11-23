import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_defaults_without_env_file() -> None:
    settings = Settings(_env_file=None)
    assert settings.api_v1_prefix == "/api/v1"
    assert settings.database_configured is False


def test_reads_values_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "http://a.test, http://b.test/")
    monkeypatch.setenv("LOG_LEVEL", "debug")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@db:5432/x")

    settings = Settings(_env_file=None)

    assert settings.cors_origins == ["http://a.test", "http://b.test"]
    assert settings.log_level == "DEBUG"
    assert settings.database_configured is True


def test_empty_database_url_means_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "")
    assert Settings(_env_file=None).database_url is None


def test_database_url_secret_is_not_exposed_in_repr() -> None:
    settings = Settings(_env_file=None, database_url="postgresql+psycopg://u:supersecret@db/x")
    assert "supersecret" not in repr(settings)
    assert "supersecret" not in str(settings.model_dump())


@pytest.mark.parametrize("field,value", [("log_level", "LOUD"), ("api_v1_prefix", "api/v1"), ("environment", "qa")])
def test_invalid_values_are_rejected(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})


def test_jwt_secret_required_outside_development() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, environment="production")
    settings = Settings(_env_file=None, environment="production", jwt_secret_key="s" * 40)
    assert settings.jwt_secret_ephemeral is False
    assert "s" * 40 not in repr(settings)


def test_development_generates_ephemeral_jwt_secret() -> None:
    settings = Settings(_env_file=None)
    assert settings.jwt_secret_ephemeral is True
    assert settings.jwt_secret_key is not None and len(settings.jwt_secret_key.get_secret_value()) >= 32


@pytest.mark.parametrize(
    "field,value",
    [
        ("jwt_secret_key", "too-short"),
        ("cors_origins", "*"),
        ("jwt_algorithm", "none"),
        ("jwt_algorithm", "RS256"),
    ],
)
def test_insecure_auth_settings_are_rejected(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})

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

"""First-administrator bootstrap and host-level password recovery (CLI)."""

from collections.abc import Callable

import pytest

from app.cli.__main__ import main
from tests.auth.conftest import Api

pytestmark = pytest.mark.postgres

ADMIN_PASSWORD = "bootstrap admin passphrase"


@pytest.fixture
def cli_env(monkeypatch: pytest.MonkeyPatch, migrated_database: str) -> Callable[..., None]:
    from app.core.config import get_settings

    def configure(**variables: str) -> None:
        monkeypatch.setenv("DATABASE_URL", migrated_database)
        monkeypatch.setenv("APP_ENV", "test")
        for name, value in variables.items():
            monkeypatch.setenv(name, value)
        get_settings.cache_clear()

    yield configure
    get_settings.cache_clear()


def _admin_env(**overrides: str) -> dict[str, str]:
    values = {
        "OPSPILOT_ADMIN_EMAIL": "Owner@Example.com",
        "OPSPILOT_ADMIN_FULL_NAME": "Installation Owner",
        "OPSPILOT_ADMIN_PASSWORD": ADMIN_PASSWORD,
    }
    values.update(overrides)
    return values


def test_bootstrap_creates_the_first_administrator_once(
    api: Api, cli_env: Callable[..., None], capsys: pytest.CaptureFixture[str]
) -> None:
    cli_env(**_admin_env())

    assert main(["create-admin", "--no-input"]) == 0
    second = main(["create-admin", "--no-input", "--email", "another@example.com"])

    output = capsys.readouterr()
    assert second == 1
    assert "already exists" in output.err
    assert ADMIN_PASSWORD not in output.out + output.err
    assert api.sql("SELECT email, is_superuser, is_active, is_verified FROM users") == [
        ("owner@example.com", True, True, True)
    ]
    assert api.sql("SELECT password_hash LIKE '$argon2id$%' FROM users") == [(True,)]
    assert len(api.events("admin_bootstrapped")) == 1

    login = api.login("owner@example.com", ADMIN_PASSWORD)
    assert login.status_code == 200
    assert "users:manage" in login.json()["user"]["permissions"]


def test_bootstrap_refuses_weak_or_missing_credentials(
    api: Api, cli_env: Callable[..., None], capsys: pytest.CaptureFixture[str]
) -> None:
    cli_env(**_admin_env(OPSPILOT_ADMIN_PASSWORD="password1234"))
    assert main(["create-admin", "--no-input"]) == 1

    cli_env(**_admin_env(OPSPILOT_ADMIN_PASSWORD=""))
    assert main(["create-admin", "--no-input"]) == 1

    output = capsys.readouterr().err
    assert "too common" in output
    assert "OPSPILOT_ADMIN_PASSWORD" in output
    assert api.sql("SELECT count(*) FROM users") == [(0,)]


def test_bootstrap_has_no_http_endpoint(api: Api) -> None:
    paths = api.client.get("/openapi.json").json()["paths"]

    assert not any("bootstrap" in path or "setup" in path or "register" in path for path in paths)


def test_host_password_reset_recovers_access(api: Api, cli_env: Callable[..., None]) -> None:
    cli_env(**_admin_env())
    assert main(["create-admin", "--no-input"]) == 0
    old_access = api.token_for("owner@example.com", ADMIN_PASSWORD)

    cli_env(OPSPILOT_NEW_PASSWORD="recovered owner passphrase")
    assert main(["reset-password", "--no-input", "--email", "owner@example.com"]) == 0

    assert api.client.get("/api/v1/auth/me", headers=api.bearer(old_access)).status_code == 401
    assert api.login("owner@example.com", ADMIN_PASSWORD).status_code == 401
    assert api.login("owner@example.com", "recovered owner passphrase").status_code == 200

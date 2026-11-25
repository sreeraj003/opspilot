"""Alembic integration."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect

from app.database.base import Base
from tests.conftest import make_settings

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _alembic_config() -> Config:
    return Config(str(BACKEND_DIR / "alembic.ini"))


def test_alembic_configuration_loads() -> None:
    script = ScriptDirectory.from_config(_alembic_config())

    # Module 01 defines no tables, so there are no revisions yet.
    assert script.get_heads() == []
    assert Base.metadata.tables == {}


def test_alembic_ini_contains_no_database_url() -> None:
    assert "sqlalchemy.url" not in (BACKEND_DIR / "alembic.ini").read_text()


@pytest.mark.postgres
def test_upgrade_head_runs_against_postgres(postgres_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setenv("DATABASE_URL", postgres_url)
    get_settings.cache_clear()
    try:
        command.upgrade(_alembic_config(), "head")
    finally:
        get_settings.cache_clear()

    engine = create_engine(make_settings(database_url=postgres_url).database_url.get_secret_value())  # type: ignore[union-attr]
    try:
        assert "alembic_version" in inspect(engine).get_table_names()
    finally:
        engine.dispose()


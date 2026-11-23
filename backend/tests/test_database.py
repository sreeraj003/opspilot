from pathlib import Path

import pytest
from sqlalchemy import text

from app.core.config import Settings
from app.database.session import Database


def _db(tmp_path: Path) -> Database:
    return Database.from_settings(Settings(_env_file=None, database_url=f"sqlite:///{tmp_path / 't.db'}"))


def test_from_settings_requires_url() -> None:
    with pytest.raises(ValueError):
        Database.from_settings(Settings(_env_file=None))


def test_session_commits_on_success_and_rolls_back_on_error(tmp_path: Path) -> None:
    db = _db(tmp_path)
    db.ping()
    with db.session() as session:
        session.execute(text("CREATE TABLE t (v INTEGER)"))
        session.execute(text("INSERT INTO t VALUES (1)"))

    with pytest.raises(RuntimeError), db.session() as session:
        session.execute(text("INSERT INTO t VALUES (2)"))
        raise RuntimeError("boom")

    with db.session() as session:
        assert session.execute(text("SELECT v FROM t")).scalars().all() == [1]
    db.dispose()

"""Database engine and session management."""

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings

logger = logging.getLogger(__name__)


class Database:
    """Owns the SQLAlchemy engine and session factory for the application lifetime."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self._session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    @classmethod
    def from_settings(cls, settings: Settings) -> "Database":
        if settings.database_url is None:
            raise ValueError("DATABASE_URL is not configured")
        url = make_url(settings.database_url.get_secret_value())
        engine_kwargs: dict[str, object] = {"echo": settings.database_echo, "pool_pre_ping": True}
        if url.get_backend_name() == "sqlite":
            engine_kwargs["connect_args"] = {"check_same_thread": False}
        else:
            engine_kwargs["pool_size"] = settings.database_pool_size
            engine_kwargs["max_overflow"] = settings.database_max_overflow
            engine_kwargs["connect_args"] = {"connect_timeout": settings.database_connect_timeout_seconds}
        return cls(create_engine(url, **engine_kwargs))

    @property
    def dialect(self) -> str:
        return self.engine.dialect.name

    def ping(self) -> None:
        """Run a trivial query; raises if the database is unreachable."""
        with self.engine.connect() as connection:
            connection.execute(text("SELECT 1"))

    @contextmanager
    def session(self) -> Iterator[Session]:
        """Provide a transactional session: commit on success, roll back on error."""
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def dispose(self) -> None:
        self.engine.dispose()
        logger.info("Database engine disposed")

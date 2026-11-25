"""SQLAlchemy engine and session management for the application database.

One ``Database`` instance is created at application startup and disposed at
shutdown. Each request that needs persistence gets its own session through
the ``get_db_session`` dependency (see ``app.api.deps``): the session commits
when the request succeeds, rolls back on any error and is always closed.
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings

logger = logging.getLogger(__name__)


class Database:
    """Owns the engine and session factory for the application lifetime."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self._session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    @classmethod
    def from_settings(cls, settings: Settings) -> "Database":
        if settings.database_url is None:
            raise ValueError("DATABASE_URL is not configured")
        # Engine creation is lazy: no connection is opened until first use,
        # so the application starts even while the database is still booting.
        engine = create_engine(
            settings.database_url.get_secret_value(),
            pool_pre_ping=True,
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            connect_args={"connect_timeout": settings.database_connect_timeout_seconds},
        )
        return cls(engine)

    @property
    def dialect(self) -> str:
        return self.engine.dialect.name

    def ping(self) -> None:
        """Run ``SELECT 1``; raises if the database is unreachable."""
        with self.engine.connect() as connection:
            connection.execute(text("SELECT 1"))

    @contextmanager
    def session(self) -> Iterator[Session]:
        """Provide a transactional session: commit on success, roll back on error, always close."""
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
        logger.info("Database connection pool closed")

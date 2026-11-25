"""Async engine and session management for the application database.

One ``Database`` instance is created per process at application startup and
disposed at shutdown. It owns the only engine (and therefore the only
connection pool). Request handlers never share sessions: each request gets a
fresh ``AsyncSession`` from ``app.api.deps.get_db_session``.

Sessions do not commit on their own. Services decide when work is committed
using ``app.database.transaction.transaction``; anything left uncommitted when
a session closes is rolled back.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings

logger = logging.getLogger(__name__)


def create_engine_from_settings(settings: Settings) -> AsyncEngine:
    if settings.database_url is None:
        raise ValueError("DATABASE_URL is not configured")
    # Engine creation is lazy: no connection is opened until first use, so the
    # application starts even while the database is still booting.
    return create_async_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_timeout=settings.database_pool_timeout_seconds,
        pool_recycle=settings.database_pool_recycle_seconds,
        # Bound parameter values (setting values, later user data) never appear in
        # exception messages or SQL logs.
        hide_parameters=True,
        connect_args={
            "connect_timeout": settings.database_connect_timeout_seconds,
            # now() and timestamptz values are returned in UTC.
            "options": "-c timezone=UTC",
        },
    )


class Database:
    """Owns the engine and session factory for the application lifetime."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        # expire_on_commit=False: objects stay readable after commit without
        # another round trip (implicit refreshes are not possible in async code).
        self.session_factory = async_sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    @classmethod
    def from_settings(cls, settings: Settings) -> "Database":
        return cls(create_engine_from_settings(settings))

    @property
    def dialect(self) -> str:
        return self.engine.dialect.name

    async def ping(self) -> None:
        """Run ``SELECT 1`` on a pooled connection; raises if the database is unreachable."""
        async with self.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Yield a new session; roll back on error and always close.

        It does not commit: wrap writes in ``transaction(session)``. Closing a
        session discards (rolls back) anything that was not committed.
        """
        session = self.session_factory()
        try:
            yield session
        except BaseException:
            await session.rollback()
            raise
        finally:
            await session.close()

    async def dispose(self) -> None:
        await self.engine.dispose()
        logger.info("Database connection pool closed")

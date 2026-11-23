"""Application startup and shutdown."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import Settings
from app.database.session import Database

logger = logging.getLogger(__name__)


def build_lifespan(settings: Settings):
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        database: Database | None = None
        if settings.database_configured:
            # Engine creation is lazy: no connection is opened here, so startup
            # does not fail when the database is down. Readiness reports that.
            database = Database.from_settings(settings)
            logger.info("Database engine created", extra={"dialect": database.dialect})
        else:
            logger.warning("DATABASE_URL not set; running without a database")
        app.state.database = database

        logger.info(
            "Application started",
            extra={"app_version": settings.app_version, "environment": settings.environment.value},
        )
        try:
            yield
        finally:
            if database is not None:
                database.dispose()
            logger.info("Application stopped")

    return lifespan

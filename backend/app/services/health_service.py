"""Application information and health checks.

The database check reports only what it observed: an unset DATABASE_URL is
``not_configured`` and a failed ``SELECT 1`` is ``unavailable``; neither is
ever reported as connected. Responses never contain URLs or credentials.
"""

import logging
import time
from datetime import UTC, datetime

from starlette.concurrency import run_in_threadpool

from app.core.config import APP_DESCRIPTION, Settings
from app.database.session import Database
from app.schemas.health import AppInfoResponse, DatabaseHealthResponse, DatabaseStatus, HealthResponse

logger = logging.getLogger(__name__)


class HealthService:
    def __init__(self, settings: Settings, database: Database | None) -> None:
        self._settings = settings
        self._database = database

    def app_info(self, docs_url: str) -> AppInfoResponse:
        return AppInfoResponse(
            name=self._settings.app_name,
            description=APP_DESCRIPTION,
            version=self._settings.app_version,
            environment=self._settings.app_env.value,
            docs_url=docs_url,
        )

    def health(self) -> HealthResponse:
        return HealthResponse(
            status="ok",
            service=self._settings.app_name,
            version=self._settings.app_version,
            environment=self._settings.app_env.value,
            timestamp=datetime.now(UTC),
        )

    async def database_health(self) -> DatabaseHealthResponse:
        if self._database is None:
            return DatabaseHealthResponse(
                status=DatabaseStatus.NOT_CONFIGURED,
                database="postgresql",
                detail="The application database is not configured.",
                timestamp=datetime.now(UTC),
            )
        started = time.perf_counter()
        try:
            await run_in_threadpool(self._database.ping)
        except Exception as exc:  # any driver or network failure means unavailable
            # Log the exception type only: driver messages can include host names or user names.
            logger.warning("Database health check failed", extra={"error_type": type(exc).__name__})
            return DatabaseHealthResponse(
                status=DatabaseStatus.UNAVAILABLE,
                database=self._database.dialect,
                latency_ms=_elapsed_ms(started),
                detail="The application database is not reachable.",
                timestamp=datetime.now(UTC),
            )
        return DatabaseHealthResponse(
            status=DatabaseStatus.OK,
            database=self._database.dialect,
            latency_ms=_elapsed_ms(started),
            timestamp=datetime.now(UTC),
        )


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)

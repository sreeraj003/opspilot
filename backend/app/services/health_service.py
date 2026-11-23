"""Health and readiness logic.

Readiness reports only what is actually configured: a dependency that has not
been configured is reported as ``not_configured`` rather than as connected.
"""

import logging
import time

from starlette.concurrency import run_in_threadpool

from app.core.config import Settings
from app.database.session import Database
from app.schemas.health import (
    CheckStatus,
    DependencyCheck,
    HealthResponse,
    ReadinessResponse,
    ReadinessStatus,
)
from app.utils.time import utc_now

logger = logging.getLogger(__name__)


class HealthService:
    def __init__(self, settings: Settings, database: Database | None) -> None:
        self._settings = settings
        self._database = database

    def health(self) -> HealthResponse:
        return HealthResponse(
            status="ok",
            service=self._settings.app_name,
            version=self._settings.app_version,
            environment=self._settings.environment.value,
            timestamp=utc_now(),
        )

    async def readiness(self) -> ReadinessResponse:
        checks = [self._application_check(), await self._database_check()]
        ready = all(check.status == CheckStatus.OK for check in checks if check.required)
        return ReadinessResponse(
            status=ReadinessStatus.READY if ready else ReadinessStatus.NOT_READY,
            checks=checks,
            timestamp=utc_now(),
        )

    def _application_check(self) -> DependencyCheck:
        # Reaching this code means configuration loaded and the lifespan completed.
        return DependencyCheck(name="application", status=CheckStatus.OK, required=True)

    async def _database_check(self) -> DependencyCheck:
        if self._database is None:
            return DependencyCheck(
                name="database",
                status=CheckStatus.NOT_CONFIGURED,
                required=False,
                detail="DATABASE_URL is not set.",
            )
        started = time.perf_counter()
        try:
            await run_in_threadpool(self._database.ping)
        except Exception as exc:  # any driver/network failure means not ready
            logger.warning("Database readiness check failed", extra={"error_type": type(exc).__name__})
            return DependencyCheck(
                name="database",
                status=CheckStatus.ERROR,
                required=True,
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
                detail=f"Database unreachable ({type(exc).__name__}).",
            )
        return DependencyCheck(
            name="database",
            status=CheckStatus.OK,
            required=True,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
        )

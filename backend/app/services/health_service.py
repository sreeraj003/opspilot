"""Application information and health checks.

The database check reports only what it observed: an unset DATABASE_URL is
``not_configured`` and a failed ``SELECT 1`` is ``unavailable``; neither is
ever reported as connected. Responses never contain URLs or credentials.
"""

from datetime import UTC, datetime

from app.core.config import APP_DESCRIPTION, Settings
from app.database.health import check_database
from app.database.session import Database
from app.schemas.health import AppInfoResponse, DatabaseHealthResponse, DatabaseStatus, HealthResponse

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
        # Slightly above the connect timeout so a slow connect is reported as such.
        result = await check_database(self._database, timeout_seconds=self._settings.database_connect_timeout_seconds + 1)
        if not result.reachable:
            return DatabaseHealthResponse(
                status=DatabaseStatus.UNAVAILABLE,
                database=self._database.dialect,
                latency_ms=result.latency_ms,
                detail="The application database is not reachable.",
                timestamp=datetime.now(UTC),
            )
        return DatabaseHealthResponse(
            status=DatabaseStatus.OK,
            database=self._database.dialect,
            latency_ms=result.latency_ms,
            timestamp=datetime.now(UTC),
        )

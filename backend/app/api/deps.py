"""FastAPI dependency providers.

Routes depend on these providers instead of constructing services directly,
so tests can replace any of them through ``app.dependency_overrides``.
"""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.exceptions import ServiceUnavailableError
from app.database.session import Database
from app.services.application_settings_service import ApplicationSettingsService
from app.services.health_service import HealthService

SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_database(request: Request) -> Database | None:
    return getattr(request.app.state, "database", None)


DatabaseDep = Annotated[Database | None, Depends(get_database)]


async def get_db_session(database: DatabaseDep) -> AsyncIterator[AsyncSession]:
    """A new session for this request only: rolled back on error, always closed.

    Services commit their own work through ``app.database.transaction``; anything
    left uncommitted when the request ends is discarded.
    """
    if database is None:
        raise ServiceUnavailableError("The application database is not configured.")
    async with database.session() as session:
        yield session


DbSessionDep = Annotated[AsyncSession, Depends(get_db_session)]


def get_health_service(settings: SettingsDep, database: DatabaseDep) -> HealthService:
    return HealthService(settings=settings, database=database)


HealthServiceDep = Annotated[HealthService, Depends(get_health_service)]


def get_application_settings_service(session: DbSessionDep) -> ApplicationSettingsService:
    return ApplicationSettingsService(session)


ApplicationSettingsServiceDep = Annotated[ApplicationSettingsService, Depends(get_application_settings_service)]

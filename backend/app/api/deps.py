"""FastAPI dependency providers.

Routes depend on these providers instead of constructing services directly,
so tests can replace any of them through ``app.dependency_overrides``.
"""

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.exceptions import ServiceUnavailableError
from app.database.session import Database
from app.services.health_service import HealthService

SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_database(request: Request) -> Database | None:
    return getattr(request.app.state, "database", None)


DatabaseDep = Annotated[Database | None, Depends(get_database)]


def get_db_session(database: DatabaseDep) -> Iterator[Session]:
    """Request-scoped session: committed on success, rolled back on error, always closed."""
    if database is None:
        raise ServiceUnavailableError("The application database is not configured.")
    with database.session() as session:
        yield session


DbSessionDep = Annotated[Session, Depends(get_db_session)]


def get_health_service(settings: SettingsDep, database: DatabaseDep) -> HealthService:
    return HealthService(settings=settings, database=database)


HealthServiceDep = Annotated[HealthService, Depends(get_health_service)]

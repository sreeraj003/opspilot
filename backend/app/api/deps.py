"""FastAPI dependency providers (the dependency-injection foundation).

Routes depend on these providers rather than constructing services directly,
so tests can replace any of them via ``app.dependency_overrides``.
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
    """Request-scoped database session for routes that need persistence."""
    if database is None:
        raise ServiceUnavailableError("Database is not configured.")
    with database.session() as session:
        yield session


DbSessionDep = Annotated[Session, Depends(get_db_session)]


def get_health_service(settings: SettingsDep, database: DatabaseDep) -> HealthService:
    return HealthService(settings=settings, database=database)


HealthServiceDep = Annotated[HealthService, Depends(get_health_service)]

"""Health endpoints."""

from fastapi import APIRouter, Response, status

from app.api.deps import HealthServiceDep
from app.schemas.health import DatabaseHealthResponse, DatabaseStatus, HealthResponse

router = APIRouter(prefix="/health", tags=["health"])


@router.get(
    "",
    response_model=HealthResponse,
    summary="Application health",
    description="Returns 200 while the application process is running and serving requests.",
)
def health(service: HealthServiceDep) -> HealthResponse:
    return service.health()


@router.get(
    "/database",
    response_model=DatabaseHealthResponse,
    summary="Application database health",
    description=(
        "Runs `SELECT 1` against the application database. Returns 200 when it is reachable and 503 "
        "when it is unreachable or not configured."
    ),
    responses={503: {"model": DatabaseHealthResponse, "description": "The database is unavailable or not configured."}},
)
async def database_health(service: HealthServiceDep, response: Response) -> DatabaseHealthResponse:
    result = await service.database_health()
    if result.status is not DatabaseStatus.OK:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return result

"""Liveness and readiness endpoints."""

from fastapi import APIRouter, Response, status

from app.api.deps import HealthServiceDep
from app.schemas.health import HealthResponse, ReadinessResponse, ReadinessStatus

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Liveness check",
    description="Returns 200 while the application process is running and serving requests.",
)
def health(service: HealthServiceDep) -> HealthResponse:
    return service.health()


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    summary="Readiness check",
    description=(
        "Reports whether the service can handle traffic. Returns 200 when every required "
        "check passes and 503 otherwise. Dependencies that are not configured are reported "
        "as 'not_configured' and do not count as connected."
    ),
    responses={503: {"model": ReadinessResponse, "description": "A required dependency is unavailable."}},
)
async def ready(service: HealthServiceDep, response: Response) -> ReadinessResponse:
    result = await service.readiness()
    if result.status == ReadinessStatus.NOT_READY:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return result

"""Application information endpoint."""

from fastapi import APIRouter, Request

from app.api.deps import HealthServiceDep
from app.schemas.health import AppInfoResponse

router = APIRouter(tags=["system"])


@router.get("/", response_model=AppInfoResponse, summary="Application information")
def app_info(service: HealthServiceDep, request: Request) -> AppInfoResponse:
    return service.app_info(docs_url=request.app.docs_url or "")

"""AI provider administration API (``/api/v1/ai``).

=====================  ==================================================================
``ai.provider.read``   ``GET /ai/providers``, ``GET /ai/providers/active``, ``GET /ai/providers/health``
``ai.provider.test``   ``POST /ai/providers/test`` (fixed system prompt; no body is read)
``ai.usage.read``      ``GET /ai/usage``
=====================  ==================================================================

No response contains an API key, and no endpoint accepts prompt text: there is
no way to send arbitrary input to the provider through this API. Provider
configuration comes from the server environment, so no endpoint changes it.
"""

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Response

from app.ai.dependencies.deps import ProviderAdminServiceDep
from app.ai.schemas.admin import ProviderHealthRead, ProviderList, ProviderRead, ProviderTestResult, UsagePage
from app.api.deps import RateLimiterDep, SettingsDep, require_permission
from app.core.authorization import Permission
from app.core.exceptions import TooManyRequestsError
from app.repositories.ai_usage_repository import UsageFilter
from app.repositories.base_repository import MAX_PAGE_SIZE
from app.schemas.errors import ErrorResponse
from app.services.auth_service import Principal

router = APIRouter(prefix="/ai", tags=["ai"])

CanRead = Annotated[Principal, Depends(require_permission(Permission.AI_PROVIDER_READ))]
CanTest = Annotated[Principal, Depends(require_permission(Permission.AI_PROVIDER_TEST))]
CanReadUsage = Annotated[Principal, Depends(require_permission(Permission.AI_USAGE_READ))]

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Not authenticated."},
    403: {"model": ErrorResponse, "description": "Not permitted."},
}
TEST_RATE_WINDOW_SECONDS = 300


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


@router.get("/providers", response_model=ProviderList, summary="Configured AI providers (no credentials)",
            responses=_ERRORS)
async def list_providers(_: CanRead, service: ProviderAdminServiceDep, response: Response) -> ProviderList:
    _no_store(response)
    return service.providers()


@router.get("/providers/active", response_model=ProviderRead, summary="The provider LLMService uses",
            responses=_ERRORS)
async def active_provider(_: CanRead, service: ProviderAdminServiceDep, response: Response) -> ProviderRead:
    _no_store(response)
    return service.active()


@router.get("/providers/health", response_model=ProviderHealthRead,
            summary="Check the active provider (lists models; generates nothing)", responses=_ERRORS)
async def provider_health(_: CanRead, service: ProviderAdminServiceDep, response: Response) -> ProviderHealthRead:
    _no_store(response)
    return await service.health()


@router.post("/providers/test", response_model=ProviderTestResult,
             summary="Send the fixed system test prompt to the active provider (no input is accepted)",
             responses={**_ERRORS, 429: {"model": ErrorResponse, "description": "Too many test runs; see Retry-After."}})
async def test_provider(
    principal: CanTest,
    service: ProviderAdminServiceDep,
    limiter: RateLimiterDep,
    settings: SettingsDep,
    response: Response,
) -> ProviderTestResult:
    result = limiter.hit(
        f"ai-provider-test:{principal.user.id}",
        limit=settings.ai_test_rate_limit_per_user,
        window_seconds=TEST_RATE_WINDOW_SECONDS,
    )
    if not result.allowed:
        raise TooManyRequestsError(result.retry_after_seconds)
    _no_store(response)
    return await service.test(principal.user.id)


@router.get("/usage", response_model=UsagePage, summary="AI usage records and totals (reported tokens only)",
            responses=_ERRORS)
async def usage(
    _: CanReadUsage,
    service: ProviderAdminServiceDep,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 20,
    provider: Annotated[str | None, Query(max_length=64, pattern=r"^[a-z0-9_]+$")] = None,
    status: Annotated[Literal["success", "failure"] | None, Query()] = None,
    operation: Annotated[str | None, Query(max_length=64, pattern=r"^[a-z0-9_]+$")] = None,
    since: Annotated[datetime | None, Query(description="Only records at or after this time (ISO 8601).")] = None,
) -> UsagePage:
    filters = UsageFilter(provider=provider, status=status, operation=operation, since=since)
    return await service.usage(filters, offset=offset, limit=limit)

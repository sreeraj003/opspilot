"""Question understanding API (``/api/v1/ai/understand``).

==========================  ==========================================================
``ai.question.understand``  ``POST /ai/understand``, ``GET /ai/understand/options``
``ai.question.preview``     ``POST /ai/understand/preview``, ``GET /ai/understand/options``
==========================  ==========================================================

Both interpretation endpoints run the same pipeline with the caller's own data
permissions; the preview only adds diagnostics (selected tables and scores,
matched terms, prompt size, attempts) and never shows prompts, keys or other
users' permissions. Each call is an LLM request, so both are rate-limited per
user (``AI_UNDERSTAND_RATE_LIMIT_PER_USER`` per minute, shared).
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response

from app.ai.dependencies.deps import QuestionUnderstandingServiceDep
from app.ai.understanding.schemas.api import PreviewResponse, UnderstandingOptions, UnderstandingResponse, UnderstandRequest
from app.api.deps import CurrentPrincipalDep, RateLimiterDep, SettingsDep, get_authorization_policy, require_permission
from app.core.authorization import AuthorizationPolicy, Permission
from app.core.exceptions import PermissionDeniedError, TooManyRequestsError
from app.schemas.errors import ErrorResponse
from app.services.auth_service import Principal

router = APIRouter(prefix="/ai/understand", tags=["question understanding"])

CanUnderstand = Annotated[Principal, Depends(require_permission(Permission.AI_QUESTION_UNDERSTAND))]
CanPreview = Annotated[Principal, Depends(require_permission(Permission.AI_QUESTION_PREVIEW))]

RATE_WINDOW_SECONDS = 60

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Not authenticated."},
    403: {"model": ErrorResponse, "description": "Not permitted."},
    404: {"model": ErrorResponse, "description": "Schema not found."},
    409: {"model": ErrorResponse, "description": "Schema inactive or empty."},
    422: {"model": ErrorResponse, "description": "Invalid question or request."},
    429: {"model": ErrorResponse, "description": "Too many questions; see Retry-After."},
    502: {"model": ErrorResponse, "description": "The AI provider failed."},
    503: {"model": ErrorResponse, "description": "The AI provider is not configured or unavailable."},
    504: {"model": ErrorResponse, "description": "The AI provider timed out."},
}


def _limit(principal: Principal, limiter: RateLimiterDep, settings: SettingsDep) -> None:
    result = limiter.hit(
        f"ai-understand:{principal.user.id}",
        limit=settings.ai_understand_rate_limit_per_user,
        window_seconds=RATE_WINDOW_SECONDS,
    )
    if not result.allowed:
        raise TooManyRequestsError(result.retry_after_seconds)


@router.post("", response_model=UnderstandingResponse, summary="Interpret a business question as a structured intent",
             responses=_ERRORS)
async def understand(
    body: UnderstandRequest,
    principal: CanUnderstand,
    service: QuestionUnderstandingServiceDep,
    limiter: RateLimiterDep,
    settings: SettingsDep,
    response: Response,
) -> UnderstandingResponse:
    _limit(principal, limiter, settings)
    response.headers["Cache-Control"] = "no-store"
    return await service.understand(principal.user, body)


@router.post("/preview", response_model=PreviewResponse,
             summary="Interpret a question with diagnostics (testing and administration)", responses=_ERRORS)
async def preview(
    body: UnderstandRequest,
    principal: CanPreview,
    service: QuestionUnderstandingServiceDep,
    limiter: RateLimiterDep,
    settings: SettingsDep,
    response: Response,
) -> PreviewResponse:
    _limit(principal, limiter, settings)
    response.headers["Cache-Control"] = "no-store"
    result = await service.understand(principal.user, body, preview=True)
    assert isinstance(result, PreviewResponse)
    return result


@router.get("/options", response_model=UnderstandingOptions,
            summary="Schemas that can be asked about and the business timezone", responses=_ERRORS)
async def options(
    principal: CurrentPrincipalDep,
    policy: Annotated[AuthorizationPolicy, Depends(get_authorization_policy)],
    service: QuestionUnderstandingServiceDep,
) -> UnderstandingOptions:
    held = await policy.permissions_for(principal.user)
    if not {Permission.AI_QUESTION_UNDERSTAND, Permission.AI_QUESTION_PREVIEW} & held:
        raise PermissionDeniedError()
    return await service.options()

"""SQL generation API (``/api/v1/ai/sql``).

=====================  ===============================================================
``ai.sql.generate``    ``POST /ai/sql/generate``, ``GET /ai/sql/generations/{id}``,
                       ``POST /ai/sql/generations/{id}/regenerate``
=====================  ===============================================================

Generation takes only ids: the backend loads the caller's own stored intent
(Module 09) and never trusts an intent object from the client. Every result is
an **untrusted** candidate query awaiting the SQL security guard (Module 11);
there is no execution endpoint here and nothing is executed. The SQL text is
returned only when the caller's data permissions allow viewing SQL
(``allow_sql_view``). Generations are visible only to the user who created
them. Each generate or regenerate call is an LLM request, so both share one
per-user rate limit (``AI_SQL_RATE_LIMIT_PER_USER`` per minute). Prompts,
provider keys and connector credentials are never returned.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response

from app.ai.dependencies.deps import SQLGenerationServiceDep
from app.ai.sql_generation.schemas.api import GenerateSQLRequest, SQLGenerationView
from app.api.deps import RateLimiterDep, SettingsDep, require_permission
from app.core.authorization import Permission
from app.core.exceptions import TooManyRequestsError
from app.schemas.errors import ErrorResponse
from app.services.auth_service import Principal

router = APIRouter(prefix="/ai/sql", tags=["sql generation"])

CanGenerate = Annotated[Principal, Depends(require_permission(Permission.AI_SQL_GENERATE))]

RATE_WINDOW_SECONDS = 60

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Not authenticated."},
    403: {"model": ErrorResponse, "description": "Not permitted."},
    404: {"model": ErrorResponse, "description": "Intent, schema or generation not found."},
    409: {"model": ErrorResponse, "description": "Intent needs clarification, expired or schema changed; limit reached."},
    422: {"model": ErrorResponse, "description": "Invalid request, mismatched schema or unsupported dialect."},
    429: {"model": ErrorResponse, "description": "Too many generation requests; see Retry-After."},
    502: {"model": ErrorResponse, "description": "The AI provider failed."},
    503: {"model": ErrorResponse, "description": "The AI provider is not configured or unavailable."},
    504: {"model": ErrorResponse, "description": "The AI provider timed out."},
}


def _limit(principal: Principal, limiter: RateLimiterDep, settings: SettingsDep) -> None:
    result = limiter.hit(
        f"ai-sql:{principal.user.id}", limit=settings.ai_sql_rate_limit_per_user, window_seconds=RATE_WINDOW_SECONDS
    )
    if not result.allowed:
        raise TooManyRequestsError(result.retry_after_seconds)


@router.post("/generate", response_model=SQLGenerationView, responses=_ERRORS,
             summary="Generate untrusted candidate SQL for an interpreted question (never executed)")
async def generate(
    body: GenerateSQLRequest,
    principal: CanGenerate,
    service: SQLGenerationServiceDep,
    limiter: RateLimiterDep,
    settings: SettingsDep,
    response: Response,
) -> SQLGenerationView:
    _limit(principal, limiter, settings)
    response.headers["Cache-Control"] = "no-store"
    return await service.generate(principal.user, body.intent_id, body.schema_source_id)


@router.get("/generations/{generation_id}", response_model=SQLGenerationView, responses=_ERRORS,
            summary="One of your SQL generations")
async def get_generation(
    generation_id: uuid.UUID, principal: CanGenerate, service: SQLGenerationServiceDep, response: Response
) -> SQLGenerationView:
    response.headers["Cache-Control"] = "no-store"
    return await service.get(principal.user, generation_id)


@router.post("/generations/{generation_id}/regenerate", response_model=SQLGenerationView, responses=_ERRORS,
             summary="Generate the SQL again for the same intent (bounded)")
async def regenerate(
    generation_id: uuid.UUID,
    principal: CanGenerate,
    service: SQLGenerationServiceDep,
    limiter: RateLimiterDep,
    settings: SettingsDep,
    response: Response,
) -> SQLGenerationView:
    _limit(principal, limiter, settings)
    response.headers["Cache-Control"] = "no-store"
    return await service.regenerate(principal.user, generation_id)

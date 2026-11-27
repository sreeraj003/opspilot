"""SQL security and execution guard API (``/api/v1/ai/sql``).

=========================  ================================================================
``ai.sql.validate``        ``POST /ai/sql/generations/{id}/validate``,
                           ``GET /ai/sql/generations/{id}/validation`` (latest),
                           ``GET /ai/sql/validations/{id}``, ``GET /ai/sql/approvals/{id}``,
                           ``POST /ai/sql/approvals/{id}/revoke``
``ai.sql.security.audit``  adds ``internal_reasons`` to validations, and access to other
                           users' validations (administrators only)
=========================  ================================================================

Validation takes only a generation id (and optionally the connector to bind
the approval to): the guard loads the caller's own stored generation and its
exact SQL; it never accepts SQL, a validation result or an approval from the
client. Approvals are created only by the guard. None of these endpoints
executes anything, and there is no endpoint that accepts SQL for execution.
Validation requests are rate-limited per user
(``AI_SQL_VALIDATION_RATE_LIMIT_PER_USER`` per minute).
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Response

from app.ai.dependencies.deps import SQLSecurityServiceDep
from app.ai.sql_security.schemas.result import ApprovalView, ValidateRequest, ValidationView
from app.api.deps import RateLimiterDep, SettingsDep, get_authorization_policy, require_permission
from app.core.authorization import AuthorizationPolicy
from app.core.authorization import Permission
from app.core.exceptions import NotFoundError, TooManyRequestsError
from app.schemas.errors import ErrorResponse
from app.services.auth_service import Principal

router = APIRouter(prefix="/ai/sql", tags=["sql security"])

CanValidate = Annotated[Principal, Depends(require_permission(Permission.AI_SQL_VALIDATE))]

RATE_WINDOW_SECONDS = 60

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Not authenticated."},
    403: {"model": ErrorResponse, "description": "Not permitted."},
    404: {"model": ErrorResponse, "description": "Generation, validation or approval not found."},
    409: {"model": ErrorResponse, "description": "The approval can no longer be revoked."},
    422: {"model": ErrorResponse, "description": "Invalid request."},
    429: {"model": ErrorResponse, "description": "Too many validation requests; see Retry-After."},
}


class NoValidationError(NotFoundError):
    code = "SQL_VALIDATION_NOT_FOUND"
    message = "This generation has not been validated yet."


@router.post("/generations/{generation_id}/validate", response_model=ValidationView, responses=_ERRORS,
             summary="Run the SQL security guard on one of your generations (never executes it)")
async def validate(
    generation_id: uuid.UUID,
    principal: CanValidate,
    service: SQLSecurityServiceDep,
    limiter: RateLimiterDep,
    settings: SettingsDep,
    response: Response,
    body: Annotated[ValidateRequest | None, Body()] = None,
) -> ValidationView:
    result = limiter.hit(
        f"ai-sql-validate:{principal.user.id}", limit=settings.ai_sql_validation_rate_limit_per_user,
        window_seconds=RATE_WINDOW_SECONDS,
    )
    if not result.allowed:
        raise TooManyRequestsError(result.retry_after_seconds)
    response.headers["Cache-Control"] = "no-store"
    return await service.validate(principal.user, generation_id, body.connector_id if body else None)


@router.get("/generations/{generation_id}/validation", response_model=ValidationView, responses=_ERRORS,
            summary="The latest security validation of one of your generations")
async def latest_validation(
    generation_id: uuid.UUID, principal: CanValidate, service: SQLSecurityServiceDep, response: Response
) -> ValidationView:
    response.headers["Cache-Control"] = "no-store"
    view = await service.latest_for_generation(principal.user, generation_id)
    if view is None:
        raise NoValidationError()
    return view


@router.get("/validations/{validation_id}", response_model=ValidationView, responses=_ERRORS,
            summary="A security validation result")
async def get_validation(
    validation_id: uuid.UUID,
    principal: CanValidate,
    service: SQLSecurityServiceDep,
    policy: Annotated[AuthorizationPolicy, Depends(get_authorization_policy)],
    response: Response,
) -> ValidationView:
    response.headers["Cache-Control"] = "no-store"
    auditor = await policy.is_allowed(principal.user, Permission.AI_SQL_SECURITY_AUDIT)
    return await service.get_validation(principal.user, validation_id, auditor=auditor)


@router.get("/approvals/{approval_id}", response_model=ApprovalView, responses=_ERRORS,
            summary="The status of one of your execution approvals")
async def get_approval(
    approval_id: uuid.UUID, principal: CanValidate, service: SQLSecurityServiceDep, response: Response
) -> ApprovalView:
    response.headers["Cache-Control"] = "no-store"
    return await service.get_approval(principal.user, approval_id)


@router.post("/approvals/{approval_id}/revoke", response_model=ApprovalView, responses=_ERRORS,
             summary="Revoke one of your unused execution approvals")
async def revoke_approval(
    approval_id: uuid.UUID, principal: CanValidate, service: SQLSecurityServiceDep, response: Response
) -> ApprovalView:
    response.headers["Cache-Control"] = "no-store"
    return await service.revoke(principal.user, approval_id)

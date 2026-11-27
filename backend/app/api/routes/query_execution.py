"""Query execution and results API (``/api/v1/ai/sql``, Module 12).

==============================  ==============================================================
``ai.sql.execute``              ``POST /ai/sql/approvals/{id}/execute``,
                                ``GET /ai/sql/executions``, ``GET /ai/sql/executions/{id}``,
                                ``GET /ai/sql/executions/{id}/metadata``
``ai.sql.execution.read_all``   adds other users' executions (metadata only, never their rows or
                                SQL) to the history, detail and metadata endpoints
``ai.sql.result.export``        ``GET /ai/sql/executions/{id}/export`` (CSV; the data permission
                                must also allow export)
==============================  ==============================================================

Execution takes **only an approval id** in the path. The user comes from the
authenticated session; the SQL, connector and URL come from the server's own
approval record. There is no endpoint that accepts SQL. Executions are
rate-limited per user (``AI_SQL_EXEC_RATE_LIMIT_PER_USER`` per minute) and
limited in concurrency (``AI_SQL_EXEC_MAX_CONCURRENT_PER_USER``).
"""

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response

from app.ai.dependencies.deps import QueryExecutionServiceDep
from app.ai.query_execution.policies.failures import ExecutionStatus
from app.ai.query_execution.schemas.result import ExecutionPage, QueryExecutionResult, ResultMetadataView
from app.api.deps import RateLimiterDep, SettingsDep, get_authorization_policy, require_permission
from app.core.authorization import AuthorizationPolicy, Permission
from app.core.exceptions import InvalidInputError, TooManyRequestsError
from app.repositories.base_repository import MAX_PAGE_SIZE
from app.schemas.errors import ErrorResponse
from app.services.auth_service import Principal

router = APIRouter(prefix="/ai/sql", tags=["query execution"])

CanExecute = Annotated[Principal, Depends(require_permission(Permission.AI_SQL_EXECUTE))]
CanExport = Annotated[Principal, Depends(require_permission(Permission.AI_SQL_RESULT_EXPORT))]
Policy = Annotated[AuthorizationPolicy, Depends(get_authorization_policy)]

RATE_WINDOW_SECONDS = 60

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Not authenticated."},
    403: {"model": ErrorResponse, "description": "Not permitted."},
    404: {"model": ErrorResponse, "description": "Approval or execution not found."},
    409: {"model": ErrorResponse, "description": "No result is available."},
    422: {"model": ErrorResponse, "description": "Invalid request."},
    429: {"model": ErrorResponse, "description": "Too many executions; see Retry-After."},
}


@router.post("/approvals/{approval_id}/execute", response_model=QueryExecutionResult, responses=_ERRORS,
             summary="Run one of your guard-approved queries once through the company API")
async def execute(
    approval_id: uuid.UUID,
    principal: CanExecute,
    service: QueryExecutionServiceDep,
    limiter: RateLimiterDep,
    settings: SettingsDep,
    response: Response,
) -> QueryExecutionResult:
    result = limiter.hit(
        f"ai-sql-execute:{principal.user.id}", limit=settings.ai_sql_exec_rate_limit_per_user,
        window_seconds=RATE_WINDOW_SECONDS,
    )
    if not result.allowed:
        raise TooManyRequestsError(result.retry_after_seconds)
    response.headers["Cache-Control"] = "no-store"
    return await service.execute_approved_query(approval_id, principal.user.id)


@router.get("/executions", response_model=ExecutionPage, responses=_ERRORS, summary="Execution history")
async def history(
    principal: CanExecute,
    service: QueryExecutionServiceDep,
    policy: Policy,
    response: Response,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 20,
    status: Annotated[list[ExecutionStatus] | None, Query(description="Only these statuses.")] = None,
    created_from: Annotated[datetime | None, Query(alias="from", description="Created at or after (ISO 8601).")] = None,
    created_to: Annotated[datetime | None, Query(alias="to", description="Created before (ISO 8601).")] = None,
    generation_id: Annotated[uuid.UUID | None, Query()] = None,
    scope: Annotated[str, Query(pattern="^(mine|all)$", description="'all' needs ai.sql.execution.read_all.")] = "mine",
) -> ExecutionPage:
    response.headers["Cache-Control"] = "no-store"
    if created_from and created_to and created_from >= created_to:
        raise InvalidInputError("'from' must be earlier than 'to'.")
    read_all = await policy.is_allowed(principal.user, Permission.AI_SQL_EXECUTION_READ_ALL)
    if scope == "all" and not read_all:
        raise InvalidInputError("You can only list your own executions.")
    return await service.history(
        principal.user, read_all=read_all, all_users=scope == "all", statuses=tuple(s.value for s in status or ()),
        created_from=created_from, created_to=created_to, generation_id=generation_id, offset=offset, limit=limit,
    )


@router.get("/executions/{execution_id}", response_model=QueryExecutionResult, responses=_ERRORS,
            summary="An execution's status and, for its requesting user, its result")
async def get_execution(
    execution_id: uuid.UUID, principal: CanExecute, service: QueryExecutionServiceDep, policy: Policy, response: Response
) -> QueryExecutionResult:
    response.headers["Cache-Control"] = "no-store"
    read_all = await policy.is_allowed(principal.user, Permission.AI_SQL_EXECUTION_READ_ALL)
    return await service.result(principal.user, execution_id, read_all=read_all)


@router.get("/executions/{execution_id}/metadata", response_model=ResultMetadataView, responses=_ERRORS,
            summary="Column, size and truncation metadata of an execution's result (no values)")
async def get_metadata(
    execution_id: uuid.UUID, principal: CanExecute, service: QueryExecutionServiceDep, policy: Policy, response: Response
) -> ResultMetadataView:
    response.headers["Cache-Control"] = "no-store"
    read_all = await policy.is_allowed(principal.user, Permission.AI_SQL_EXECUTION_READ_ALL)
    return await service.metadata(principal.user, execution_id, read_all=read_all)


@router.get("/executions/{execution_id}/export", responses={**_ERRORS, 200: {"content": {"text/csv": {}}}},
            response_class=Response, summary="Download one of your results as CSV (needs export permission)")
async def export_result(execution_id: uuid.UUID, principal: CanExport, service: QueryExecutionServiceDep) -> Response:
    filename, body, truncated = await service.export_csv(principal.user, execution_id)
    return Response(
        content=body.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
            "X-OpsPilot-Result-Truncated": "true" if truncated else "false",
        },
    )

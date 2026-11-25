"""User access assignments and authorization diagnostics.

* ``/api/v1/users/{user_id}/roles`` and ``/permissions``: assign and revoke
  (``access:assign``, administrators only) and list (``access:review``).
* ``/api/v1/users/{user_id}/effective-permissions``: ``access:review``.
* ``/api/v1/authorization/me``: the caller's own effective permissions (any
  signed-in user). The frontend uses it to hide actions; the server still
  checks every request.
* ``/api/v1/authorization/me/ai-context``: the caller's own AI authorization context.
* ``/api/v1/authorization/check``: diagnostic decision for the caller, or for
  another user with ``access:review``. The result authorizes nothing.

User ids in paths are never trusted for "who is acting": the actor is always
the authenticated principal, and ``RbacService`` refuses self-assignment.
"""

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response, status

from app.api.deps import AuthorizationEngineDep, CurrentPrincipalDep, RbacServiceDep, require_permission
from app.api.presenters import decision_read, effective_read
from app.authorization.engine import AuthorizationRequest
from app.core.authorization import Permission
from app.core.exceptions import PermissionDeniedError
from app.schemas.errors import ErrorResponse
from app.schemas.rbac import (
    AuthorizationCheckRequest,
    AuthorizationDecisionRead,
    EffectivePermissionsRead,
    PermissionSummary,
    RoleSummary,
    UserPermissionAssign,
    UserPermissionRead,
    UserRoleAssign,
    UserRoleRead,
)
from app.services.auth_service import Principal

users_router = APIRouter(prefix="/users", tags=["user access"])
authorization_router = APIRouter(prefix="/authorization", tags=["authorization"])

CanReviewAccess = Annotated[Principal, Depends(require_permission(Permission.ACCESS_REVIEW))]
CanAssignAccess = Annotated[Principal, Depends(require_permission(Permission.ACCESS_ASSIGN))]

_ERRORS = {
    401: {"model": ErrorResponse, "description": "Not authenticated."},
    403: {"model": ErrorResponse, "description": "Not permitted (including changes to your own access)."},
}
_NOT_FOUND = {404: {"model": ErrorResponse, "description": "No such user, role, permission or assignment."}}
_CONFLICT = {409: {"model": ErrorResponse, "description": "Duplicate assignment or last administrator."}}
_INVALID = {422: {"model": ErrorResponse, "description": "Invalid input."}}


def _now() -> datetime:
    return datetime.now(UTC)


@users_router.get("/{user_id}/roles", response_model=list[UserRoleRead], summary="List a user's roles",
                  responses={**_ERRORS, **_NOT_FOUND})
async def list_user_roles(user_id: uuid.UUID, _: CanReviewAccess, service: RbacServiceDep) -> list[UserRoleRead]:
    now = _now()
    return [
        UserRoleRead(
            role=RoleSummary.model_validate(role),
            assigned_at=assignment.assigned_at,
            assigned_by=assignment.assigned_by,
            expires_at=assignment.expires_at,
            is_expired=assignment.expires_at is not None and assignment.expires_at <= now,
        )
        for assignment, role in await service.list_user_roles(user_id)
    ]


@users_router.post("/{user_id}/roles", response_model=UserRoleRead, status_code=status.HTTP_201_CREATED,
                   summary="Assign a role to a user", responses={**_ERRORS, **_NOT_FOUND, **_CONFLICT, **_INVALID})
async def assign_user_role(
    user_id: uuid.UUID, body: UserRoleAssign, principal: CanAssignAccess, service: RbacServiceDep
) -> UserRoleRead:
    assignment, role = await service.assign_user_role(user_id, body, principal.user)
    return UserRoleRead(
        role=RoleSummary.model_validate(role),
        assigned_at=assignment.assigned_at,
        assigned_by=assignment.assigned_by,
        expires_at=assignment.expires_at,
        is_expired=False,
    )


@users_router.delete("/{user_id}/roles/{role_id}", status_code=status.HTTP_204_NO_CONTENT,
                     summary="Revoke a role from a user", responses={**_ERRORS, **_NOT_FOUND, **_CONFLICT})
async def revoke_user_role(
    user_id: uuid.UUID, role_id: uuid.UUID, principal: CanAssignAccess, service: RbacServiceDep
) -> Response:
    await service.revoke_user_role(user_id, role_id, principal.user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _user_permission_read(assignment: Any, permission: Any) -> UserPermissionRead:
    return UserPermissionRead(
        permission=PermissionSummary.model_validate(permission),
        assignment_type=assignment.assignment_type,
        policy_override=assignment.policy_override,
        assigned_at=assignment.assigned_at,
        assigned_by=assignment.assigned_by,
        expires_at=assignment.expires_at,
    )


@users_router.get("/{user_id}/permissions", response_model=list[UserPermissionRead],
                  summary="List a user's direct permissions", responses={**_ERRORS, **_NOT_FOUND})
async def list_user_permissions(
    user_id: uuid.UUID, _: CanReviewAccess, service: RbacServiceDep
) -> list[UserPermissionRead]:
    return [_user_permission_read(a, p) for a, p in await service.list_user_permissions(user_id)]


@users_router.post("/{user_id}/permissions", response_model=UserPermissionRead, status_code=status.HTTP_201_CREATED,
                   summary="Grant (or explicitly deny) a permission directly to a user",
                   responses={**_ERRORS, **_NOT_FOUND, **_CONFLICT, **_INVALID})
async def assign_user_permission(
    user_id: uuid.UUID, body: UserPermissionAssign, principal: CanAssignAccess, service: RbacServiceDep
) -> UserPermissionRead:
    assignment, permission = await service.assign_user_permission(user_id, body, principal.user)
    return _user_permission_read(assignment, permission)


@users_router.delete("/{user_id}/permissions/{permission_id}", status_code=status.HTTP_204_NO_CONTENT,
                     summary="Revoke a direct permission assignment", responses={**_ERRORS, **_NOT_FOUND})
async def revoke_user_permission(
    user_id: uuid.UUID, permission_id: uuid.UUID, principal: CanAssignAccess, service: RbacServiceDep
) -> Response:
    await service.revoke_user_permission(user_id, permission_id, principal.user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@users_router.get("/{user_id}/effective-permissions", response_model=EffectivePermissionsRead,
                  summary="What a user can currently do", responses={**_ERRORS, **_NOT_FOUND})
async def effective_permissions(
    user_id: uuid.UUID, _: CanReviewAccess, service: RbacServiceDep
) -> EffectivePermissionsRead:
    user, effective = await service.effective_for(user_id)
    return effective_read(user, effective)


@authorization_router.get("/me", response_model=EffectivePermissionsRead,
                          summary="Your own effective permissions (for display; the server enforces them)",
                          responses={401: _ERRORS[401]})
async def my_permissions(principal: CurrentPrincipalDep, engine: AuthorizationEngineDep, response: Response) -> EffectivePermissionsRead:
    response.headers["Cache-Control"] = "no-store"
    return effective_read(principal.user, await engine.effective(principal.user))


@authorization_router.get("/me/ai-context", summary="Your own AI authorization context (not an authorization token)",
                          responses={401: _ERRORS[401]})
async def my_ai_context(principal: CurrentPrincipalDep, engine: AuthorizationEngineDep, response: Response) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    return await engine.ai_authorization_context(principal.user)


@authorization_router.post("/check", response_model=AuthorizationDecisionRead,
                           summary="Diagnostic authorization check", responses={**_ERRORS, **_INVALID})
async def check(
    body: AuthorizationCheckRequest, principal: CurrentPrincipalDep, engine: AuthorizationEngineDep
) -> AuthorizationDecisionRead:
    target = body.user_id or principal.user.id
    if target != principal.user.id and Permission.ACCESS_REVIEW not in principal.permissions:
        # Same answer whether or not the user exists: no account enumeration.
        raise PermissionDeniedError()
    decision = await engine.authorize(
        AuthorizationRequest(
            user_id=target,
            resource_type=body.resource_type,
            action=body.action,
            tables=tuple(body.tables),
            columns=tuple(body.columns),
            operation=body.operation,
            export=body.export,
            sql_view=body.sql_view,
        ),
        fresh=True,
    )
    return decision_read(decision)

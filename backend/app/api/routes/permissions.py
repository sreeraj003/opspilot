"""Permission definitions (``/api/v1/permissions``)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.deps import RbacServiceDep, require_permission
from app.api.presenters import permission_read
from app.core.authorization import Permission
from app.repositories.base_repository import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from app.repositories.rbac_repository import CatalogQuery, StatusFilter
from app.schemas.errors import ErrorResponse
from app.schemas.rbac import (
    AuditEventRead,
    PermissionCreate,
    PermissionDetail,
    PermissionPage,
    PermissionRead,
    PermissionUpdate,
    PolicyValidationRequest,
    PolicyValidationResponse,
    RoleSummary,
)
from app.services.auth_service import Principal

router = APIRouter(prefix="/permissions", tags=["permissions"])

CanReadPermissions = Annotated[Principal, Depends(require_permission(Permission.PERMISSIONS_READ))]
CanManagePermissions = Annotated[Principal, Depends(require_permission(Permission.PERMISSIONS_MANAGE))]

_ERRORS = {
    401: {"model": ErrorResponse, "description": "Not authenticated."},
    403: {"model": ErrorResponse, "description": "Not permitted."},
}
_NOT_FOUND = {404: {"model": ErrorResponse, "description": "No such permission."}}
_CONFLICT = {409: {"model": ErrorResponse, "description": "Duplicate key, built-in permission or still assigned."}}
_INVALID = {422: {"model": ErrorResponse, "description": "Invalid input or policy (details list each problem)."}}


@router.post("", response_model=PermissionRead, status_code=status.HTTP_201_CREATED, summary="Create a permission",
             responses={**_ERRORS, **_CONFLICT, **_INVALID})
async def create_permission(body: PermissionCreate, principal: CanManagePermissions, service: RbacServiceDep) -> PermissionRead:
    return permission_read(await service.create_permission(body, principal.user), (0, 0))


@router.post("/validate-policy", response_model=PolicyValidationResponse,
             summary="Validate a policy without saving it (used by the policy builder's review step)",
             responses={**_ERRORS, **_INVALID})
async def validate_policy(
    body: PolicyValidationRequest, _: CanManagePermissions, service: RbacServiceDep
) -> PolicyValidationResponse:
    return PolicyValidationResponse(policy_definition=service.validate_policy(body.resource_type, body.policy_definition))


@router.get("", response_model=PermissionPage, summary="List and search permissions", responses=_ERRORS)
async def list_permissions(
    _: CanReadPermissions,
    service: RbacServiceDep,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    search: Annotated[str | None, Query(max_length=100, description="Matches name, key or description.")] = None,
    status_filter: Annotated[StatusFilter, Query(alias="status")] = StatusFilter.ALL,
    resource_type: Annotated[str | None, Query(max_length=64)] = None,
) -> PermissionPage:
    page, counts = await service.list_permissions(
        CatalogQuery(search=search or None, status=status_filter, resource_type=resource_type or None),
        offset=offset,
        limit=limit,
    )
    return PermissionPage(
        items=[permission_read(p, counts.get(p.id)) for p in page.items],
        total=page.total,
        offset=page.offset,
        limit=page.limit,
    )


@router.get("/{permission_id}", response_model=PermissionDetail, summary="Get a permission",
            responses={**_ERRORS, **_NOT_FOUND})
async def get_permission(permission_id: uuid.UUID, _: CanReadPermissions, service: RbacServiceDep) -> PermissionDetail:
    data = await service.get_permission(permission_id)
    return PermissionDetail(
        **permission_read(data.permission, (data.role_count, data.user_count)).model_dump(),
        roles=[RoleSummary.model_validate(role) for role in data.roles],
        history=[AuditEventRead.model_validate(event) for event in data.history],
    )


@router.patch("/{permission_id}", response_model=PermissionRead,
              summary="Update, activate or deactivate a permission",
              responses={**_ERRORS, **_NOT_FOUND, **_CONFLICT, **_INVALID})
async def update_permission(
    permission_id: uuid.UUID, body: PermissionUpdate, principal: CanManagePermissions, service: RbacServiceDep
) -> PermissionRead:
    await service.update_permission(permission_id, body, principal.user)
    data = await service.get_permission(permission_id)
    return permission_read(data.permission, (data.role_count, data.user_count))


@router.delete("/{permission_id}", status_code=status.HTTP_204_NO_CONTENT,
               summary="Delete an unassigned permission (soft delete). Deactivate assigned ones instead.",
               responses={**_ERRORS, **_NOT_FOUND, **_CONFLICT})
async def delete_permission(permission_id: uuid.UUID, principal: CanManagePermissions, service: RbacServiceDep) -> Response:
    await service.delete_permission(permission_id, principal.user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

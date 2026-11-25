"""Role administration (``/api/v1/roles``)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.deps import RbacServiceDep, require_permission
from app.api.presenters import role_read
from app.core.authorization import Permission
from app.repositories.base_repository import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from app.repositories.rbac_repository import CatalogQuery, StatusFilter
from app.schemas.errors import ErrorResponse
from app.schemas.rbac import (
    AuditEventRead,
    RoleCreate,
    RoleDetail,
    RolePage,
    RolePermissionRead,
    RolePermissionsAssign,
    RolePermissionsChanged,
    RolePermissionsReplace,
    RoleRead,
    RoleUpdate,
    RoleUserRead,
)
from app.services.auth_service import Principal

router = APIRouter(prefix="/roles", tags=["roles"])

CanReadRoles = Annotated[Principal, Depends(require_permission(Permission.ROLES_READ))]
CanManageRoles = Annotated[Principal, Depends(require_permission(Permission.ROLES_MANAGE))]

_ERRORS = {
    401: {"model": ErrorResponse, "description": "Not authenticated."},
    403: {"model": ErrorResponse, "description": "Not permitted."},
}
_NOT_FOUND = {404: {"model": ErrorResponse, "description": "No such role or permission."}}
_CONFLICT = {409: {"model": ErrorResponse, "description": "Duplicate, protected or still in use."}}
_INVALID = {422: {"model": ErrorResponse, "description": "Invalid input."}}


@router.post("", response_model=RoleRead, status_code=status.HTTP_201_CREATED, summary="Create a role",
             responses={**_ERRORS, **_CONFLICT, **_INVALID})
async def create_role(body: RoleCreate, principal: CanManageRoles, service: RbacServiceDep) -> RoleRead:
    return role_read(await service.create_role(body, principal.user))


@router.get("", response_model=RolePage, summary="List and search roles", responses=_ERRORS)
async def list_roles(
    _: CanReadRoles,
    service: RbacServiceDep,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    search: Annotated[str | None, Query(max_length=100, description="Matches name, key or description.")] = None,
    status_filter: Annotated[StatusFilter, Query(alias="status")] = StatusFilter.ALL,
) -> RolePage:
    page, counts = await service.list_roles(
        CatalogQuery(search=search or None, status=status_filter), offset=offset, limit=limit
    )
    return RolePage(
        items=[role_read(role, counts.get(role.id)) for role in page.items],
        total=page.total,
        offset=page.offset,
        limit=page.limit,
    )


@router.get("/{role_id}", response_model=RoleDetail, summary="Get a role with its permissions, users and history",
            responses={**_ERRORS, **_NOT_FOUND})
async def get_role(role_id: uuid.UUID, _: CanReadRoles, service: RbacServiceDep) -> RoleDetail:
    data = await service.get_role(role_id)
    return RoleDetail(
        **role_read(data.role, data.counts).model_dump(),
        permissions=[
            RolePermissionRead(
                **{field: getattr(permission, field) for field in RolePermissionRead.model_fields if field != "assigned_at"},
                assigned_at=assignment.assigned_at,
            )
            for assignment, permission in data.permissions
        ],
        users=[
            RoleUserRead(
                id=user.id,
                email=user.email,
                full_name=user.full_name,
                is_active=user.is_active,
                assigned_at=assignment.assigned_at,
                expires_at=assignment.expires_at,
            )
            for assignment, user in data.users
        ],
        history=[AuditEventRead.model_validate(event) for event in data.history],
    )


@router.patch("/{role_id}", response_model=RoleRead, summary="Update, activate or deactivate a role",
              responses={**_ERRORS, **_NOT_FOUND, **_CONFLICT, **_INVALID})
async def update_role(role_id: uuid.UUID, body: RoleUpdate, principal: CanManageRoles, service: RbacServiceDep) -> RoleRead:
    return role_read(await service.update_role(role_id, body, principal.user))


@router.delete("/{role_id}", status_code=status.HTTP_204_NO_CONTENT,
               summary="Delete a custom role that no user holds (soft delete; history is kept)",
               responses={**_ERRORS, **_NOT_FOUND, **_CONFLICT})
async def delete_role(role_id: uuid.UUID, principal: CanManageRoles, service: RbacServiceDep) -> Response:
    await service.delete_role(role_id, principal.user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{role_id}/permissions", response_model=RolePermissionsChanged, status_code=status.HTTP_201_CREATED,
             summary="Add permissions to a role", responses={**_ERRORS, **_NOT_FOUND, **_CONFLICT, **_INVALID})
async def assign_role_permissions(
    role_id: uuid.UUID, body: RolePermissionsAssign, principal: CanManageRoles, service: RbacServiceDep
) -> RolePermissionsChanged:
    added = await service.assign_role_permissions(role_id, body.permission_ids, principal.user)
    return RolePermissionsChanged(added=len(added), removed=0)


@router.put("/{role_id}/permissions", response_model=RolePermissionsChanged,
            summary="Set a role's permissions exactly (one atomic change)",
            responses={**_ERRORS, **_NOT_FOUND, **_CONFLICT, **_INVALID})
async def replace_role_permissions(
    role_id: uuid.UUID, body: RolePermissionsReplace, principal: CanManageRoles, service: RbacServiceDep
) -> RolePermissionsChanged:
    added, removed = await service.replace_role_permissions(role_id, body.permission_ids, principal.user)
    return RolePermissionsChanged(added=added, removed=removed)


@router.delete("/{role_id}/permissions/{permission_id}", status_code=status.HTTP_204_NO_CONTENT,
               summary="Remove a permission from a role", responses={**_ERRORS, **_NOT_FOUND, **_CONFLICT})
async def revoke_role_permission(
    role_id: uuid.UUID, permission_id: uuid.UUID, principal: CanManageRoles, service: RbacServiceDep
) -> Response:
    await service.revoke_role_permission(role_id, permission_id, principal.user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

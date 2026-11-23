"""User and role administration under /api/v1/users and /api/v1/roles."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import ClientInfoDep, UserServiceDep, require_permissions
from app.models.rbac import Role
from app.models.user import User
from app.schemas.errors import ErrorResponse
from app.schemas.users import (
    RoleResponse,
    UserCreateRequest,
    UserListResponse,
    UserResponse,
    UserRolesUpdateRequest,
)
from app.security.principal import Principal
from app.security.rbac import Permission

router = APIRouter(tags=["users"])

_ERRORS = {
    401: {"model": ErrorResponse, "description": "Not authenticated."},
    403: {"model": ErrorResponse, "description": "Missing permission or cross-organization access."},
}
_NOT_FOUND = {404: {"model": ErrorResponse, "description": "No such user in your organization."}}

CanRead = Annotated[Principal, Depends(require_permissions(Permission.USERS_READ))]
CanCreate = Annotated[Principal, Depends(require_permissions(Permission.USERS_CREATE))]
CanUpdate = Annotated[Principal, Depends(require_permissions(Permission.USERS_UPDATE))]
OrganizationFilter = Annotated[
    uuid.UUID | None,
    Query(description="Platform administrators only: act on another organization. Ignored when equal to your own."),
]


def user_response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        organization_id=user.organization_id,
        email=user.email,
        full_name=user.full_name,
        is_active=user.is_active,
        roles=user.role_codes,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
        deactivated_at=user.deactivated_at,
    )


def role_response(role: Role) -> RoleResponse:
    return RoleResponse(
        code=role.code,
        name=role.name,
        description=role.description,
        permissions=sorted(role.permission_codes),
    )


@router.get("/users", response_model=UserListResponse, summary="List users in your organization", responses=_ERRORS)
def list_users(
    principal: CanRead,
    service: UserServiceDep,
    organization_id: OrganizationFilter = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> UserListResponse:
    users, total = service.list_users(principal, organization_id=organization_id, limit=limit, offset=offset)
    return UserListResponse(items=[user_response(u) for u in users], total=total, limit=limit, offset=offset)


@router.post(
    "/users",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a user in your organization",
    description="You may only assign roles whose permissions you hold yourself.",
    responses={**_ERRORS, 409: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
def create_user(
    data: UserCreateRequest,
    principal: CanCreate,
    service: UserServiceDep,
    client: ClientInfoDep,
    organization_id: OrganizationFilter = None,
) -> UserResponse:
    return user_response(service.create_user(principal, data, client, organization_id=organization_id))


@router.get(
    "/users/{user_id}",
    response_model=UserResponse,
    summary="Get a user",
    responses={**_ERRORS, **_NOT_FOUND},
)
def get_user(user_id: uuid.UUID, principal: CanRead, service: UserServiceDep) -> UserResponse:
    return user_response(service.get_user(principal, user_id))


@router.put(
    "/users/{user_id}/roles",
    response_model=UserResponse,
    summary="Replace a user's roles",
    responses={**_ERRORS, **_NOT_FOUND, 422: {"model": ErrorResponse}},
)
def set_user_roles(
    user_id: uuid.UUID,
    data: UserRolesUpdateRequest,
    principal: CanUpdate,
    service: UserServiceDep,
    client: ClientInfoDep,
) -> UserResponse:
    return user_response(service.set_roles(principal, user_id, data.roles, client))


@router.post(
    "/users/{user_id}/deactivate",
    response_model=UserResponse,
    summary="Deactivate a user and revoke all of their sessions",
    responses={**_ERRORS, **_NOT_FOUND},
)
def deactivate_user(
    user_id: uuid.UUID, principal: CanUpdate, service: UserServiceDep, client: ClientInfoDep
) -> UserResponse:
    return user_response(service.set_active(principal, user_id, False, client))


@router.post(
    "/users/{user_id}/activate",
    response_model=UserResponse,
    summary="Reactivate a user",
    responses={**_ERRORS, **_NOT_FOUND},
)
def activate_user(
    user_id: uuid.UUID, principal: CanUpdate, service: UserServiceDep, client: ClientInfoDep
) -> UserResponse:
    return user_response(service.set_active(principal, user_id, True, client))


@router.get("/roles", response_model=list[RoleResponse], summary="List roles and their permissions", responses=_ERRORS)
def list_roles(_: CanRead, service: UserServiceDep) -> list[RoleResponse]:
    return [role_response(role) for role in service.list_roles()]

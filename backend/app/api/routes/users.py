"""Administrator user management (``/api/v1/users``).

Every endpoint requires a permission through ``require_permission``; in
Module 03 only the installation administrator holds them.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.deps import ClientInfoDep, SettingsDep, UserServiceDep, require_permission
from app.api.presenters import user_detail, user_read
from app.core.authorization import Permission
from app.core.exceptions import AppError
from app.repositories.user_repository import UserQuery, UserSortField, UserStatusFilter
from app.schemas.errors import ErrorResponse
from app.schemas.user import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    AdminPasswordResetResponse,
    SessionsRevokedResponse,
    UserCreate,
    UserDetail,
    UserPage,
    UserRead,
    UserSessionRead,
    UserUpdate,
)
from app.services.auth_service import Principal

router = APIRouter(prefix="/users", tags=["users"])

CanReadUsers = Annotated[Principal, Depends(require_permission(Permission.USERS_READ))]
CanManageUsers = Annotated[Principal, Depends(require_permission(Permission.USERS_MANAGE))]

_ERRORS = {
    401: {"model": ErrorResponse, "description": "Not authenticated."},
    403: {"model": ErrorResponse, "description": "Not permitted."},
}
_NOT_FOUND = {404: {"model": ErrorResponse, "description": "No such user."}}
_CONFLICT = {409: {"model": ErrorResponse, "description": "Conflicts with another account or a protected account."}}


class AdminLinksDisabledError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "RESET_LINKS_DISABLED"
    message = "Administrator reset links are disabled (PASSWORD_RESET_ADMIN_LINKS_ENABLED=false)."


@router.post(
    "",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a user",
    responses={**_ERRORS, **_CONFLICT, 422: {"model": ErrorResponse, "description": "Invalid input or weak password."}},
)
async def create_user(
    body: UserCreate, principal: CanManageUsers, service: UserServiceDep, client: ClientInfoDep
) -> UserRead:
    return user_read(await service.create_user(body, principal.user, client))


@router.get("", response_model=UserPage, summary="List and search users", responses=_ERRORS)
async def list_users(
    _: CanReadUsers,
    service: UserServiceDep,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    search: Annotated[str | None, Query(max_length=100, description="Matches email, name or username.")] = None,
    status_filter: Annotated[UserStatusFilter, Query(alias="status")] = UserStatusFilter.ALL,
    sort: Annotated[UserSortField, Query()] = UserSortField.CREATED_AT,
    order: Annotated[str, Query(pattern="^(asc|desc)$")] = "desc",
) -> UserPage:
    query = UserQuery(search=search or None, status=status_filter, sort=sort, descending=order == "desc")
    page = await service.list_users(query, offset=offset, limit=limit)
    return UserPage(items=[user_read(user) for user in page.items], total=page.total, offset=page.offset, limit=page.limit)


@router.get("/{user_id}", response_model=UserDetail, summary="Get a user", responses={**_ERRORS, **_NOT_FOUND})
async def get_user(user_id: uuid.UUID, _: CanReadUsers, service: UserServiceDep) -> UserDetail:
    user = await service.get_user(user_id)
    return user_detail(user, await service.active_session_count(user.id))


@router.get(
    "/{user_id}/sessions",
    response_model=list[UserSessionRead],
    summary="List a user's active sessions",
    responses={**_ERRORS, **_NOT_FOUND},
)
async def list_user_sessions(user_id: uuid.UUID, _: CanReadUsers, service: UserServiceDep) -> list[UserSessionRead]:
    return [UserSessionRead.model_validate(item) for item in await service.list_sessions(user_id)]


@router.patch(
    "/{user_id}",
    response_model=UserRead,
    summary="Update a user's email, username or name",
    responses={**_ERRORS, **_NOT_FOUND, **_CONFLICT},
)
async def update_user(
    user_id: uuid.UUID, body: UserUpdate, principal: CanManageUsers, service: UserServiceDep, client: ClientInfoDep
) -> UserRead:
    return user_read(await service.update_user(user_id, body, principal.user, client))


@router.post(
    "/{user_id}/activate",
    response_model=UserRead,
    summary="Activate a user (also clears a temporary lockout)",
    responses={**_ERRORS, **_NOT_FOUND},
)
async def activate_user(
    user_id: uuid.UUID, principal: CanManageUsers, service: UserServiceDep, client: ClientInfoDep
) -> UserRead:
    return user_read(await service.activate(user_id, principal.user, client))


@router.post(
    "/{user_id}/deactivate",
    response_model=UserRead,
    summary="Deactivate a user and end all of their sessions",
    responses={**_ERRORS, **_NOT_FOUND, **_CONFLICT},
)
async def deactivate_user(
    user_id: uuid.UUID, principal: CanManageUsers, service: UserServiceDep, client: ClientInfoDep
) -> UserRead:
    return user_read(await service.deactivate(user_id, principal.user, client))


@router.post(
    "/{user_id}/revoke-sessions",
    response_model=SessionsRevokedResponse,
    summary="End all sessions of a user",
    responses={**_ERRORS, **_NOT_FOUND},
)
async def revoke_user_sessions(
    user_id: uuid.UUID, principal: CanManageUsers, service: UserServiceDep, client: ClientInfoDep
) -> SessionsRevokedResponse:
    return SessionsRevokedResponse(revoked_sessions=await service.revoke_sessions(user_id, principal.user, client))


@router.post(
    "/{user_id}/password-reset",
    response_model=AdminPasswordResetResponse,
    summary="Generate a one-time password reset link for a user",
    description=(
        "Returns a single-use link for the administrator to hand to the user over a trusted channel. "
        "The link is shown once; only its digest is stored. Existing sessions end when the user sets the new password."
    ),
    responses={**_ERRORS, **_NOT_FOUND, **_CONFLICT},
)
async def start_password_reset(
    user_id: uuid.UUID,
    principal: CanManageUsers,
    service: UserServiceDep,
    settings: SettingsDep,
    client: ClientInfoDep,
    response: Response,
) -> AdminPasswordResetResponse:
    if not settings.password_reset_admin_links_enabled:
        raise AdminLinksDisabledError()
    url, expires_at = await service.start_password_reset(user_id, principal.user, client)
    response.headers["Cache-Control"] = "no-store"
    return AdminPasswordResetResponse(reset_url=url, expires_at=expires_at)


@router.delete(
    "/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a user (soft delete; history is kept)",
    responses={**_ERRORS, **_NOT_FOUND, **_CONFLICT},
)
async def delete_user(
    user_id: uuid.UUID, principal: CanManageUsers, service: UserServiceDep, client: ClientInfoDep
) -> Response:
    await service.delete(user_id, principal.user, client)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

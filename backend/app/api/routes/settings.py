"""Application settings endpoints (non-secret configuration only).

Handlers validate input and delegate to ``ApplicationSettingsService``; they
never touch the database session directly.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response, status

from app.api.deps import ApplicationSettingsServiceDep, require_permission
from app.core.authorization import Permission
from app.schemas.application_setting import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    SETTING_KEY_PATTERN,
    ApplicationSettingPage,
    ApplicationSettingRead,
    ApplicationSettingWrite,
)
from app.schemas.errors import ErrorResponse

# Installation settings are administrative: reading and writing both require a
# permission (the installation administrator in Module 03).
router = APIRouter(prefix="/settings", tags=["settings"])
CanRead = Depends(require_permission(Permission.SETTINGS_READ))
CanManage = Depends(require_permission(Permission.SETTINGS_MANAGE))

KeyPath = Annotated[
    str,
    Path(min_length=1, max_length=128, pattern=SETTING_KEY_PATTERN, description="Setting key", examples=["ui.theme"]),
]

_ERRORS = {
    401: {"model": ErrorResponse, "description": "Not authenticated."},
    403: {"model": ErrorResponse, "description": "Not permitted."},
    422: {"model": ErrorResponse, "description": "Invalid key or body."},
    503: {"model": ErrorResponse, "description": "The application database is unavailable or not configured."},
}


@router.get(
    "", response_model=ApplicationSettingPage, summary="List settings", responses=_ERRORS, dependencies=[CanRead]
)
async def list_settings(
    service: ApplicationSettingsServiceDep,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
) -> ApplicationSettingPage:
    page = await service.list_settings(offset=offset, limit=limit)
    return ApplicationSettingPage(
        items=[ApplicationSettingRead.model_validate(item) for item in page.items],
        total=page.total,
        offset=page.offset,
        limit=page.limit,
    )


@router.get(
    "/{key}",
    response_model=ApplicationSettingRead,
    summary="Get one setting",
    dependencies=[CanRead],
    responses={404: {"model": ErrorResponse, "description": "No setting with this key."}, **_ERRORS},
)
async def get_setting(key: KeyPath, service: ApplicationSettingsServiceDep) -> ApplicationSettingRead:
    return ApplicationSettingRead.model_validate(await service.get_setting(key))


@router.put(
    "/{key}",
    response_model=ApplicationSettingRead,
    summary="Create or replace a setting",
    dependencies=[CanManage],
    description="Returns 201 when the setting was created and 200 when an existing one was replaced.",
    responses={
        201: {"model": ApplicationSettingRead, "description": "Created."},
        409: {"model": ErrorResponse, "description": "Created concurrently by another request."},
        **_ERRORS,
    },
)
async def put_setting(
    key: KeyPath, body: ApplicationSettingWrite, service: ApplicationSettingsServiceDep, response: Response
) -> ApplicationSettingRead:
    setting, created = await service.put_setting(key, body)
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return ApplicationSettingRead.model_validate(setting)


@router.delete(
    "/{key}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a setting",
    dependencies=[CanManage],
    responses={404: {"model": ErrorResponse, "description": "No setting with this key."}, **_ERRORS},
)
async def delete_setting(key: KeyPath, service: ApplicationSettingsServiceDep) -> Response:
    await service.delete_setting(key)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

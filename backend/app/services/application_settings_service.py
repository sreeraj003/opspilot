"""Business rules for application settings.

This service owns its transaction boundaries: each write method runs inside one
``transaction`` and commits exactly once. Reads run without one.
"""

import logging

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, InvalidInputError, NotFoundError
from app.database.transaction import transaction
from app.models.application_setting import ApplicationSetting
from app.repositories.application_setting_repository import ApplicationSettingRepository
from app.repositories.base_repository import Page
from app.schemas.application_setting import ApplicationSettingWrite, setting_key_problem

logger = logging.getLogger(__name__)

_UNIQUE_VIOLATION = "23505"
_CHECK_VIOLATION = "23514"


class SettingNotFoundError(NotFoundError):
    message = "The setting was not found."


class ApplicationSettingsService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repository = ApplicationSettingRepository(session)

    async def list_settings(self, *, offset: int, limit: int) -> Page[ApplicationSetting]:
        return await self._repository.paginate(offset=offset, limit=limit)

    async def get_setting(self, key: str) -> ApplicationSetting:
        setting = await self._repository.get_by_key(key)
        if setting is None:
            raise SettingNotFoundError()
        return setting

    async def put_setting(self, key: str, data: ApplicationSettingWrite) -> tuple[ApplicationSetting, bool]:
        """Create or replace a setting. Returns the setting and whether it was created."""
        _ensure_valid_key(key)
        try:
            async with transaction(self._session):
                setting = await self._repository.get_by_key(key)
                if setting is None:
                    setting = await self._repository.create(key=key, value=data.value, description=data.description)
                    created = True
                else:
                    await self._repository.update(setting, {"value": data.value, "description": data.description})
                    created = False
        except IntegrityError as exc:
            raise _translate_integrity_error(exc) from None
        logger.info("Application setting saved", extra={"setting_key": key, "was_created": created})
        return setting, created

    async def delete_setting(self, key: str) -> None:
        async with transaction(self._session):
            setting = await self._repository.get_by_key(key)
            if setting is None:
                raise SettingNotFoundError()
            await self._repository.delete(setting)
        logger.info("Application setting deleted", extra={"setting_key": key})


def _ensure_valid_key(key: str) -> None:
    problem = setting_key_problem(key)
    if problem is not None:
        raise InvalidInputError(problem)


def _translate_integrity_error(exc: IntegrityError) -> Exception:
    sqlstate = getattr(exc.orig, "sqlstate", None)
    if sqlstate == _UNIQUE_VIOLATION:
        # Another request created the same key between our lookup and insert.
        return ConflictError("A setting with this key already exists.")
    if sqlstate == _CHECK_VIOLATION:
        return InvalidInputError("The setting violates a database constraint.")
    return exc

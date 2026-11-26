"""The installation's business timezone (see ``app.core.timezones``)."""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.timezones import BUSINESS_TIMEZONE_SETTING_KEY, DEFAULT_BUSINESS_TIMEZONE, BusinessTimezone, timezone_problem
from app.repositories.application_setting_repository import ApplicationSettingRepository

logger = logging.getLogger("app.ai.understanding")


class BusinessTimezoneService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._settings = settings
        self._repository = ApplicationSettingRepository(session)

    async def current(self) -> BusinessTimezone:
        setting = await self._repository.get_by_key(BUSINESS_TIMEZONE_SETTING_KEY)
        if setting is not None:
            if timezone_problem(setting.value) is None:
                return BusinessTimezone(str(setting.value), "setting")
            logger.warning(
                "Ignoring invalid business timezone setting",
                extra={"event": "ai.understanding.invalid_timezone_setting", "setting_key": BUSINESS_TIMEZONE_SETTING_KEY},
            )
        if "business_timezone" in self._settings.model_fields_set:
            return BusinessTimezone(self._settings.business_timezone, "environment")
        return BusinessTimezone(DEFAULT_BUSINESS_TIMEZONE, "default")

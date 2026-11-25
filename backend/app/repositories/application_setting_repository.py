"""Persistence for ``ApplicationSetting``."""

from sqlalchemy import select

from app.models.application_setting import ApplicationSetting
from app.repositories.base_repository import BaseRepository


class ApplicationSettingRepository(BaseRepository[ApplicationSetting]):
    model = ApplicationSetting
    # Served by the unique index on key.
    default_order_by = (ApplicationSetting.key,)

    async def get_by_key(self, key: str) -> ApplicationSetting | None:
        statement = select(ApplicationSetting).where(ApplicationSetting.key == key)
        return (await self.session.execute(statement)).scalar_one_or_none()

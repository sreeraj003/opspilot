"""Persistence for ``User``. Every read excludes soft-deleted accounts."""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Select, func, or_, select

from app.models.user import User
from app.repositories.base_repository import BaseRepository, Page, validate_pagination


class UserStatusFilter(StrEnum):
    ALL = "all"
    ACTIVE = "active"
    INACTIVE = "inactive"
    LOCKED = "locked"


class UserSortField(StrEnum):
    CREATED_AT = "created_at"
    EMAIL = "email"
    FULL_NAME = "full_name"
    LAST_LOGIN_AT = "last_login_at"


_SORT_COLUMNS = {
    UserSortField.CREATED_AT: User.created_at,
    UserSortField.EMAIL: User.email,
    UserSortField.FULL_NAME: User.full_name,
    UserSortField.LAST_LOGIN_AT: User.last_login_at,
}


@dataclass(frozen=True, slots=True)
class UserQuery:
    search: str | None = None
    status: UserStatusFilter = UserStatusFilter.ALL
    sort: UserSortField = UserSortField.CREATED_AT
    descending: bool = True


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class UserRepository(BaseRepository[User]):
    model = User
    default_order_by = (User.created_at.desc(), User.id)

    async def get_by_email(self, email: str) -> User | None:
        """``email`` must already be normalised."""
        statement = self._select().where(User.email == email)
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def get_for_update(self, user_id: uuid.UUID) -> User | None:
        statement = self._select().where(User.id == user_id).with_for_update()
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def get_by_email_for_update(self, email: str) -> User | None:
        statement = self._select().where(User.email == email).with_for_update()
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def email_taken(self, email: str, *, exclude_id: uuid.UUID | None = None) -> bool:
        statement = self._select().where(User.email == email)
        if exclude_id is not None:
            statement = statement.where(User.id != exclude_id)
        return bool(await self.session.scalar(select(statement.exists())))

    async def username_taken(self, username: str, *, exclude_id: uuid.UUID | None = None) -> bool:
        statement = self._select().where(func.lower(User.username) == username.lower())
        if exclude_id is not None:
            statement = statement.where(User.id != exclude_id)
        return bool(await self.session.scalar(select(statement.exists())))

    async def superuser_exists(self) -> bool:
        # Deleted administrators count too: bootstrap runs only on a fresh installation.
        statement = select(User.id).where(User.is_superuser.is_(True))
        return bool(await self.session.scalar(select(statement.exists())))

    async def search(self, query: UserQuery, *, offset: int, limit: int, now: datetime) -> Page[User]:
        validate_pagination(offset, limit)
        statement = self._filtered(query, now)
        column = _SORT_COLUMNS[query.sort]
        order = column.desc().nulls_last() if query.descending else column.asc().nulls_last()
        items: Sequence[User] = (
            (await self.session.execute(statement.order_by(order, User.id).offset(offset).limit(limit))).scalars().all()
        )
        total = int(await self.session.scalar(select(func.count()).select_from(statement.subquery())) or 0)
        return Page(items=items, total=total, offset=offset, limit=limit)

    def _filtered(self, query: UserQuery, now: datetime) -> Select[tuple[User]]:
        statement = self._select()
        if query.search:
            pattern = f"%{_escape_like(query.search.strip().lower())}%"
            statement = statement.where(
                or_(
                    User.email.like(pattern, escape="\\"),
                    func.lower(User.full_name).like(pattern, escape="\\"),
                    func.lower(User.username).like(pattern, escape="\\"),
                )
            )
        if query.status is UserStatusFilter.ACTIVE:
            statement = statement.where(User.is_active.is_(True))
        elif query.status is UserStatusFilter.INACTIVE:
            statement = statement.where(User.is_active.is_(False))
        elif query.status is UserStatusFilter.LOCKED:
            statement = statement.where(User.locked_until.is_not(None), User.locked_until > now)
        return statement

"""Model -> response conversions shared by the auth and user routes."""

from collections.abc import Iterable
from datetime import UTC, datetime

from app.core.authorization import Permission
from app.models.user import User
from app.schemas.auth import CurrentUser
from app.schemas.user import UserDetail, UserRead


def _is_locked(user: User, now: datetime) -> bool:
    return user.locked_until is not None and user.locked_until > now


def user_read(user: User, now: datetime | None = None) -> UserRead:
    read = UserRead.model_validate(user)
    read.is_locked = _is_locked(user, now or datetime.now(UTC))
    return read


def user_detail(user: User, active_session_count: int) -> UserDetail:
    return UserDetail(**user_read(user).model_dump(), active_session_count=active_session_count)


def current_user_read(user: User, permissions: Iterable[Permission]) -> CurrentUser:
    return CurrentUser(**user_read(user).model_dump(), permissions=sorted(p.value for p in permissions))

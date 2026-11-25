"""Persistence for refresh-token sessions and retired refresh-token digests."""

import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta

from sqlalchemy import delete, func, or_, select, update

from app.models.user import User
from app.models.user_session import RetiredRefreshToken, UserSession
from app.repositories.base_repository import BaseRepository


class UserSessionRepository(BaseRepository[UserSession]):
    model = UserSession
    default_order_by = (UserSession.created_at.desc(), UserSession.id)

    async def get_by_token_hash_for_update(self, token_hash: str) -> UserSession | None:
        # Row lock: two concurrent refreshes with the same token serialise, so
        # exactly one rotates and the other sees the token as just retired.
        statement = select(UserSession).where(UserSession.refresh_token_hash == token_hash).with_for_update()
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def get_for_update(self, session_id: uuid.UUID) -> UserSession | None:
        statement = select(UserSession).where(UserSession.id == session_id).with_for_update()
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def find_retired(self, token_hash: str) -> RetiredRefreshToken | None:
        return await self.session.get(RetiredRefreshToken, token_hash)

    async def retire_token(self, session_id: uuid.UUID, token_hash: str, retired_at: datetime) -> None:
        self.session.add(RetiredRefreshToken(token_hash=token_hash, session_id=session_id, retired_at=retired_at))
        await self.session.flush()

    async def get_active_with_user(
        self, session_id: uuid.UUID, user_id: uuid.UUID, now: datetime
    ) -> tuple[UserSession, User] | None:
        """The session and its user, only if both are still valid right now."""
        statement = (
            select(UserSession, User)
            .join(User, User.id == UserSession.user_id)
            .where(
                UserSession.id == session_id,
                UserSession.user_id == user_id,
                UserSession.revoked_at.is_(None),
                UserSession.expires_at > now,
                User.deleted_at.is_(None),
                User.is_active.is_(True),
            )
        )
        row = (await self.session.execute(statement)).one_or_none()
        return (row[0], row[1]) if row is not None else None

    async def list_active_for_user(self, user_id: uuid.UUID, now: datetime) -> Sequence[UserSession]:
        statement = (
            select(UserSession)
            .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None), UserSession.expires_at > now)
            .order_by(UserSession.created_at.desc())
            .limit(100)
        )
        return (await self.session.execute(statement)).scalars().all()

    async def count_active_for_user(self, user_id: uuid.UUID, now: datetime) -> int:
        statement = select(func.count()).where(
            UserSession.user_id == user_id, UserSession.revoked_at.is_(None), UserSession.expires_at > now
        )
        return int(await self.session.scalar(statement) or 0)

    async def revoke_all_for_user(
        self, user_id: uuid.UUID, *, reason: str, now: datetime, except_session_id: uuid.UUID | None = None
    ) -> int:
        statement = (
            update(UserSession)
            .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
            .values(revoked_at=now, revoked_reason=reason)
        )
        if except_session_id is not None:
            statement = statement.where(UserSession.id != except_session_id)
        result = await self.session.execute(statement)
        return int(result.rowcount or 0)  # type: ignore[attr-defined]

    async def prune_for_user(self, user_id: uuid.UUID, *, now: datetime, revoked_retention: timedelta) -> None:
        """Delete this user's sessions that can never be used again (and their retired digests).

        Revoked sessions are kept for ``revoked_retention`` so a replayed old
        token is still recognised as reuse, then removed: session metadata
        (IP address, user agent) is not kept longer than it is useful.
        """
        await self.session.execute(
            delete(UserSession).where(
                UserSession.user_id == user_id,
                or_(UserSession.expires_at <= now, UserSession.revoked_at <= now - revoked_retention),
            )
        )

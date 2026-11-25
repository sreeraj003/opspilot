"""Persistence for password-reset tokens."""

import uuid
from datetime import datetime

from sqlalchemy import delete, or_, select, update

from app.models.password_reset_token import PasswordResetToken
from app.repositories.base_repository import BaseRepository


class PasswordResetTokenRepository(BaseRepository[PasswordResetToken]):
    model = PasswordResetToken
    default_order_by = (PasswordResetToken.created_at.desc(), PasswordResetToken.id)

    async def get_by_hash_for_update(self, token_hash: str) -> PasswordResetToken | None:
        statement = select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash).with_for_update()
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def invalidate_unused_for_user(self, user_id: uuid.UUID, now: datetime) -> None:
        await self.session.execute(
            update(PasswordResetToken)
            .where(
                PasswordResetToken.user_id == user_id,
                PasswordResetToken.used_at.is_(None),
                PasswordResetToken.invalidated_at.is_(None),
            )
            .values(invalidated_at=now)
        )

    async def prune_for_user(self, user_id: uuid.UUID, now: datetime) -> None:
        """Remove tokens that can never be used again."""
        await self.session.execute(
            delete(PasswordResetToken).where(
                PasswordResetToken.user_id == user_id,
                or_(
                    PasswordResetToken.expires_at <= now,
                    PasswordResetToken.used_at.is_not(None),
                    PasswordResetToken.invalidated_at.is_not(None),
                ),
            )
        )

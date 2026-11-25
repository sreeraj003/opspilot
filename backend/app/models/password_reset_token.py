"""Single-use password-reset tokens (digest only)."""

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.base_model import UUIDPrimaryKeyMixin
from app.models.user_session import TOKEN_HASH_LENGTH


class PasswordResetToken(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "password_reset_tokens"
    __table_args__ = (
        Index("ix_password_reset_tokens_user_id", "user_id"),
        {"comment": "Password-reset tokens. Only digests are stored; each token works once."},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(TOKEN_HASH_LENGTH), unique=True)
    expires_at: Mapped[datetime] = mapped_column()
    used_at: Mapped[datetime | None] = mapped_column()
    # Set when a newer token is issued or the password changes another way.
    invalidated_at: Mapped[datetime | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), sort_order=100)

    def __repr__(self) -> str:
        return f"PasswordResetToken(id={self.id!s}, user_id={self.user_id!s})"

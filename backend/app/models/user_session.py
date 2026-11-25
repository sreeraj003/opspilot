"""Refresh-token sessions.

One ``UserSession`` row is one signed-in browser (a refresh-token *family*).
It holds the SHA-256 digest of the current refresh token only. Each refresh
rotates the token: the old digest moves to ``RetiredRefreshToken`` and a new
one replaces it. Presenting a retired token again is reuse of a stolen or
leaked token, and revokes the whole session.
"""

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.base_model import TimestampMixin, UUIDPrimaryKeyMixin

TOKEN_HASH_LENGTH = 64  # hex SHA-256
USER_AGENT_MAX_LENGTH = 256
IP_ADDRESS_MAX_LENGTH = 45  # IPv6 text form


class SessionRevocationReason:
    LOGOUT = "logout"
    LOGOUT_ALL = "logout_all"
    PASSWORD_CHANGED = "password_changed"
    PASSWORD_RESET = "password_reset"
    ADMIN_REVOKED = "admin_revoked"
    USER_DEACTIVATED = "user_deactivated"
    USER_DELETED = "user_deleted"
    TOKEN_REUSE = "token_reuse"


class UserSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "user_sessions"
    __table_args__ = (
        Index("ix_user_sessions_user_id", "user_id"),
        Index("ix_user_sessions_expires_at", "expires_at"),
        {"comment": "Refresh-token sessions. Only token digests are stored, never raw tokens."},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    refresh_token_hash: Mapped[str] = mapped_column(String(TOKEN_HASH_LENGTH), unique=True)
    # Idle expiry: moves forward on every rotation, never past absolute_expires_at.
    expires_at: Mapped[datetime] = mapped_column()
    absolute_expires_at: Mapped[datetime] = mapped_column()
    revoked_at: Mapped[datetime | None] = mapped_column()
    revoked_reason: Mapped[str | None] = mapped_column(String(32))
    last_used_at: Mapped[datetime | None] = mapped_column()
    rotated_at: Mapped[datetime] = mapped_column(server_default=func.now())
    user_agent: Mapped[str | None] = mapped_column(String(USER_AGENT_MAX_LENGTH))
    ip_address: Mapped[str | None] = mapped_column(String(IP_ADDRESS_MAX_LENGTH))

    def __repr__(self) -> str:
        return f"UserSession(id={self.id!s}, user_id={self.user_id!s})"


class RetiredRefreshToken(Base):
    """Digest of a refresh token that was rotated away (kept to detect reuse)."""

    __tablename__ = "retired_refresh_tokens"
    __table_args__ = (
        Index("ix_retired_refresh_tokens_session_id", "session_id"),
        {"comment": "Digests of rotated refresh tokens, for reuse detection. Deleted with their session."},
    )

    token_hash: Mapped[str] = mapped_column(String(TOKEN_HASH_LENGTH), primary_key=True)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_sessions.id", ondelete="CASCADE"))
    retired_at: Mapped[datetime] = mapped_column(server_default=func.now())

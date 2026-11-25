"""Accounts of one OpsPilot AI installation.

There is exactly one user database per installation (no tenants). Emails are
stored normalised (lower case, IDNA-normalised domain) and are unique among
non-deleted accounts; a soft-deleted account keeps its row, so audit history
and foreign keys stay intact, and its email can be used again.
"""

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, Index, Integer, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.base_model import SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin

EMAIL_MAX_LENGTH = 320
USERNAME_MAX_LENGTH = 64
USERNAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$"
FULL_NAME_MAX_LENGTH = 200
PASSWORD_HASH_MAX_LENGTH = 255


class User(UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("email = lower(email)", name="email_lowercase"),
        CheckConstraint("char_length(email) BETWEEN 3 AND 320", name="email_length"),
        CheckConstraint(
            "username IS NULL OR username ~ '^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$'", name="username_format"
        ),
        CheckConstraint("char_length(btrim(full_name)) BETWEEN 1 AND 200", name="full_name_length"),
        CheckConstraint("failed_login_attempts >= 0", name="failed_login_attempts_non_negative"),
        # Unique among live accounts only, so a deleted account's email can be reused.
        Index("ix_users_email_live", "email", unique=True, postgresql_where=text("deleted_at IS NULL")),
        Index(
            "ix_users_username_live",
            func.lower(text("username")),
            unique=True,
            postgresql_where=text("deleted_at IS NULL AND username IS NOT NULL"),
        ),
        Index("ix_users_created_at", "created_at"),
        {"comment": "Accounts of this installation. password_hash is Argon2id; never returned by the API."},
    )

    email: Mapped[str] = mapped_column(String(EMAIL_MAX_LENGTH))
    username: Mapped[str | None] = mapped_column(String(USERNAME_MAX_LENGTH))
    full_name: Mapped[str] = mapped_column(String(FULL_NAME_MAX_LENGTH))
    password_hash: Mapped[str] = mapped_column(String(PASSWORD_HASH_MAX_LENGTH))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    # Installation administrator created by the bootstrap command. Never settable through the API.
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    locked_until: Mapped[datetime | None] = mapped_column()
    last_login_at: Mapped[datetime | None] = mapped_column()
    password_changed_at: Mapped[datetime | None] = mapped_column()

    def __repr__(self) -> str:  # never include credentials
        return f"User(id={self.id!s})"

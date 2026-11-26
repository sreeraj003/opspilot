"""Append-only authentication and account security events.

A minimal audit trail for Module 03; Module 16 builds the full audit
architecture. Rows are never updated or deleted by the application, and
``details`` never contains passwords, tokens or other credentials.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.base_model import UUIDPrimaryKeyMixin
from app.models.user_session import IP_ADDRESS_MAX_LENGTH


class AuthEventType:
    LOGIN_SUCCEEDED = "login_succeeded"
    LOGIN_FAILED = "login_failed"
    LOGOUT = "logout"
    LOGOUT_ALL = "logout_all"
    PASSWORD_CHANGED = "password_changed"
    PASSWORD_RESET_REQUESTED = "password_reset_requested"
    PASSWORD_RESET_COMPLETED = "password_reset_completed"
    ACCOUNT_CREATED = "account_created"
    ACCOUNT_UPDATED = "account_updated"
    ACCOUNT_ACTIVATED = "account_activated"
    ACCOUNT_DEACTIVATED = "account_deactivated"
    ACCOUNT_DELETED = "account_deleted"
    SESSIONS_REVOKED = "sessions_revoked"
    REFRESH_TOKEN_REUSE = "refresh_token_reuse"
    ADMIN_BOOTSTRAPPED = "admin_bootstrapped"
    AI_PROVIDER_TESTED = "ai_provider_tested"


class AuthEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "auth_events"
    __table_args__ = (
        Index("ix_auth_events_created_at", "created_at"),
        Index("ix_auth_events_user_id", "user_id"),
        Index("ix_auth_events_event_type", "event_type"),
        {"comment": "Append-only security events. Never contains credentials."},
    )

    event_type: Mapped[str] = mapped_column(String(64))
    # The account the event is about, and who caused it (an administrator, or the user).
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    ip_address: Mapped[str | None] = mapped_column(String(IP_ADDRESS_MAX_LENGTH))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), sort_order=100)

    def __repr__(self) -> str:
        return f"AuthEvent(event_type={self.event_type!r})"

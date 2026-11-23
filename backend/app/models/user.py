"""User accounts. Every user belongs to exactly one organization."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base
from app.database.types import TimestampMixin, UTCDateTime, UUIDPrimaryKeyMixin
from app.models.organization import Organization
from app.models.rbac import Role, user_roles


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    # Stored normalized (trimmed, lower-case); unique across the platform so a
    # login identifies exactly one account without a client-supplied tenant.
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    deactivated_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    organization: Mapped[Organization] = relationship(lazy="joined")
    roles: Mapped[list[Role]] = relationship(secondary=user_roles, lazy="selectin", order_by=Role.code)

    @property
    def role_codes(self) -> list[str]:
        return [role.code for role in self.roles]

    @property
    def permission_codes(self) -> frozenset[str]:
        codes: set[str] = set()
        for role in self.roles:
            codes |= role.permission_codes
        return frozenset(codes)

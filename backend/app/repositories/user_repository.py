"""Data access for users.

Tenant-scoped reads take the organization id explicitly, so a query can never
forget the tenant filter. Unscoped lookups exist only for authentication, where
the organization is not known yet.
"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.user import User


class UserRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    # Authentication-only lookups -------------------------------------------------
    def get_by_email(self, email: str) -> User | None:
        return self._session.scalar(select(User).where(User.email == email))

    def get_by_id(self, user_id: uuid.UUID) -> User | None:
        return self._session.get(User, user_id)

    def email_exists(self, email: str) -> bool:
        return self._session.scalar(select(User.id).where(User.email == email)) is not None

    # Tenant-scoped access ----------------------------------------------------------
    def get_in_organization(self, user_id: uuid.UUID, organization_id: uuid.UUID) -> User | None:
        return self._session.scalar(select(User).where(User.id == user_id, User.organization_id == organization_id))

    def list_in_organization(self, organization_id: uuid.UUID, *, limit: int, offset: int) -> list[User]:
        return list(
            self._session.scalars(
                select(User)
                .where(User.organization_id == organization_id)
                .order_by(User.created_at, User.id)
                .limit(limit)
                .offset(offset)
            )
        )

    def count_in_organization(self, organization_id: uuid.UUID) -> int:
        return (
            self._session.scalar(select(func.count()).select_from(User).where(User.organization_id == organization_id))
            or 0
        )

    def add(self, user: User) -> User:
        self._session.add(user)
        self._session.flush()
        return user

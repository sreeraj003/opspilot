"""Data access for roles and permissions."""

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.rbac import Permission, Role


class RoleRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_roles(self) -> list[Role]:
        return list(self._session.scalars(select(Role).order_by(Role.code)))

    def get_by_codes(self, codes: Iterable[str]) -> list[Role]:
        wanted = set(codes)
        if not wanted:
            return []
        return list(self._session.scalars(select(Role).where(Role.code.in_(wanted)).order_by(Role.code)))

    def list_permissions(self) -> list[Permission]:
        return list(self._session.scalars(select(Permission).order_by(Permission.code)))

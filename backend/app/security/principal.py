"""The authenticated identity attached to a request."""

import uuid
from dataclasses import dataclass

from app.security.rbac import Permission


@dataclass(frozen=True)
class Principal:
    user_id: uuid.UUID
    organization_id: uuid.UUID
    session_id: uuid.UUID
    email: str
    roles: tuple[str, ...]
    permissions: frozenset[str]

    def has_permission(self, permission: Permission | str) -> bool:
        return str(permission) in self.permissions

    def has_all(self, *permissions: Permission | str) -> bool:
        return all(self.has_permission(p) for p in permissions)

    @property
    def is_platform_admin(self) -> bool:
        return self.has_permission(Permission.PLATFORM_ADMIN)

"""Authorization: what an authenticated user may do.

Routes never inspect ``is_superuser`` themselves. They declare the permission
they need with ``require_permission(Permission.X)`` (see ``app.api.deps``),
and the active ``AuthorizationPolicy`` decides.

Module 03 ships ``BootstrapAuthorizationPolicy``: the installation
administrator (``is_superuser``) holds every permission and other users hold
none of the administrative ones. Module 04 replaces the policy with the
dynamic role/permission engine by providing another ``AuthorizationPolicy``
(``app.api.deps.get_authorization_policy``); routes and permission names stay
the same, and "administrator" does not become a hard-coded role.
"""

from enum import StrEnum
from typing import Protocol

from app.models.user import User


class Permission(StrEnum):
    USERS_READ = "users:read"
    USERS_MANAGE = "users:manage"
    SETTINGS_READ = "settings:read"
    SETTINGS_MANAGE = "settings:manage"


class AuthorizationPolicy(Protocol):
    def permissions_for(self, user: User) -> frozenset[Permission]: ...

    def is_allowed(self, user: User, permission: Permission) -> bool: ...


class BootstrapAuthorizationPolicy:
    """Installation administrator: everything. Everyone else: no administrative permission."""

    def permissions_for(self, user: User) -> frozenset[Permission]:
        if user.is_superuser and user.is_active and user.deleted_at is None:
            return frozenset(Permission)
        return frozenset()

    def is_allowed(self, user: User, permission: Permission) -> bool:
        return permission in self.permissions_for(user)

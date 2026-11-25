"""Administrative (system) permissions and the authorization-policy contract.

Routes never inspect ``is_superuser`` or roles themselves. They declare the
system permission they need with ``require_permission(Permission.X)`` (see
``app.api.deps``) and the active ``AuthorizationPolicy`` decides.

Module 03 introduced this contract with ``BootstrapAuthorizationPolicy``
(installation administrator: everything; everyone else: nothing). Module 04
plugs in the database-driven engine (``app.authorization.engine``): system
permissions are rows in the ``permissions`` table, granted through roles.

System permissions are the only permissions defined in code, because routes
reference them. Business permissions (what data a user may query) are created
by administrators at run time and never appear here.

Delegation model
----------------
* ``delegable`` system permissions (read-only views) may be added to any
  custom role, so an administrator can let, say, an auditor read the user list.
* Every other system permission is *administrator-only*: it is held through
  the protected ``administrator`` system role (or by the installation
  administrator account) and can never be assigned to a custom role or
  directly to a user. This is the "controlled administrative policy": only
  administrators can change users, roles, permissions or assignments.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from app.models.user import User

ADMINISTRATOR_ROLE_KEY = "administrator"
SYSTEM_RESOURCE_TYPE = "system"


class Permission(StrEnum):
    USERS_READ = "users:read"
    USERS_MANAGE = "users:manage"
    SETTINGS_READ = "settings:read"
    SETTINGS_MANAGE = "settings:manage"
    ROLES_READ = "roles:read"
    ROLES_MANAGE = "roles:manage"
    PERMISSIONS_READ = "permissions:read"
    PERMISSIONS_MANAGE = "permissions:manage"
    ACCESS_REVIEW = "access:review"
    ACCESS_ASSIGN = "access:assign"


@dataclass(frozen=True, slots=True)
class SystemPermissionDefinition:
    permission: Permission
    name: str
    description: str
    delegable: bool

    @property
    def key(self) -> str:
        return self.permission.value


SYSTEM_PERMISSIONS: tuple[SystemPermissionDefinition, ...] = (
    SystemPermissionDefinition(Permission.USERS_READ, "View users", "List and view user accounts.", True),
    SystemPermissionDefinition(
        Permission.USERS_MANAGE, "Manage users", "Create, edit, activate, deactivate and delete user accounts.", False
    ),
    SystemPermissionDefinition(Permission.SETTINGS_READ, "View settings", "Read application settings.", True),
    SystemPermissionDefinition(Permission.SETTINGS_MANAGE, "Manage settings", "Change application settings.", False),
    SystemPermissionDefinition(Permission.ROLES_READ, "View roles", "List and view roles.", True),
    SystemPermissionDefinition(
        Permission.ROLES_MANAGE, "Manage roles", "Create, edit and delete roles and change their permissions.", False
    ),
    SystemPermissionDefinition(Permission.PERMISSIONS_READ, "View permissions", "List and view permissions.", True),
    SystemPermissionDefinition(
        Permission.PERMISSIONS_MANAGE, "Manage permissions", "Create, edit and delete permission definitions.", False
    ),
    SystemPermissionDefinition(
        Permission.ACCESS_REVIEW,
        "Review user access",
        "View other users' roles and effective permissions and run authorization checks for them.",
        True,
    ),
    SystemPermissionDefinition(
        Permission.ACCESS_ASSIGN,
        "Assign access",
        "Assign and revoke roles and direct permissions of users.",
        False,
    ),
)

SYSTEM_PERMISSION_BY_KEY: dict[str, SystemPermissionDefinition] = {d.key: d for d in SYSTEM_PERMISSIONS}
DELEGABLE_SYSTEM_PERMISSIONS: frozenset[str] = frozenset(d.key for d in SYSTEM_PERMISSIONS if d.delegable)
ADMINISTRATOR_ONLY_PERMISSIONS: frozenset[str] = frozenset(d.key for d in SYSTEM_PERMISSIONS if not d.delegable)


class AuthorizationPolicy(Protocol):
    """Decides which system permissions a user holds.

    Asynchronous because the Module 04 implementation reads the database.
    Implementations must deny by default and must deny inactive or deleted users.
    """

    async def permissions_for(self, user: User) -> frozenset[Permission]: ...

    async def is_allowed(self, user: User, permission: Permission) -> bool: ...


def is_live(user: User) -> bool:
    return user.is_active and user.deleted_at is None


class BootstrapAuthorizationPolicy:
    """Installation administrator: everything. Everyone else: nothing.

    Kept as the fallback when no database is configured (the engine needs one).
    """

    async def permissions_for(self, user: User) -> frozenset[Permission]:
        if user.is_superuser and is_live(user):
            return frozenset(Permission)
        return frozenset()

    async def is_allowed(self, user: User, permission: Permission) -> bool:
        return permission in await self.permissions_for(user)

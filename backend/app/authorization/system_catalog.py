"""Keeps the built-in system permissions and the administrator role in the database
in step with ``app.core.authorization.SYSTEM_PERMISSIONS``.

Migrations seed them; ``ensure_system_rbac`` repairs or completes them (for
example after a database restore) and is idempotent. It never touches custom
roles or permissions. Run it with ``python -m app.cli sync-permissions``.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authorization.policy import system_policy
from app.core.authorization import ADMINISTRATOR_ROLE_KEY, SYSTEM_PERMISSIONS, SYSTEM_RESOURCE_TYPE
from app.database.transaction import transaction
from app.models.rbac import AuthorizationState, PermissionDefinition, Role, RolePermission
from app.repositories.rbac_repository import AuthorizationStateRepository

ADMINISTRATOR_ROLE_NAME = "Administrator"
ADMINISTRATOR_ROLE_DESCRIPTION = "Manages users, roles, permissions and settings. Protected system role."


async def ensure_system_rbac(session: AsyncSession) -> int:
    """Create whatever is missing. Returns how many rows were created or repaired."""
    changes = 0
    async with transaction(session):
        if await session.get(AuthorizationState, 1) is None:
            session.add(AuthorizationState(id=1, revision=1))
            changes += 1
        existing = {
            p.key: p
            for p in (
                await session.execute(
                    select(PermissionDefinition).where(
                        PermissionDefinition.key.in_([d.key for d in SYSTEM_PERMISSIONS]),
                        PermissionDefinition.deleted_at.is_(None),
                    )
                )
            ).scalars()
        }
        permissions: list[PermissionDefinition] = []
        for definition in SYSTEM_PERMISSIONS:
            permission = existing.get(definition.key)
            if permission is None:
                permission = PermissionDefinition(
                    key=definition.key,
                    name=definition.name,
                    description=definition.description,
                    resource_type=SYSTEM_RESOURCE_TYPE,
                    action=definition.key,
                    policy_definition=system_policy(),
                    is_system=True,
                    is_active=True,
                )
                session.add(permission)
                changes += 1
            elif not (permission.is_system and permission.is_active and permission.resource_type == SYSTEM_RESOURCE_TYPE):
                permission.is_system, permission.is_active = True, True
                permission.resource_type, permission.action = SYSTEM_RESOURCE_TYPE, definition.key
                permission.policy_definition = system_policy()
                changes += 1
            permissions.append(permission)
        role = (
            await session.execute(select(Role).where(Role.key == ADMINISTRATOR_ROLE_KEY, Role.deleted_at.is_(None)))
        ).scalar_one_or_none()
        if role is None:
            role = Role(
                key=ADMINISTRATOR_ROLE_KEY,
                name=ADMINISTRATOR_ROLE_NAME,
                description=ADMINISTRATOR_ROLE_DESCRIPTION,
                is_system=True,
                is_active=True,
            )
            session.add(role)
            changes += 1
        elif not (role.is_system and role.is_active):
            role.is_system, role.is_active = True, True
            changes += 1
        await session.flush()
        held = set(
            (
                await session.execute(
                    select(RolePermission.permission_id).where(
                        RolePermission.role_id == role.id, RolePermission.revoked_at.is_(None)
                    )
                )
            ).scalars()
        )
        for permission in permissions:
            if permission.id not in held:
                session.add(RolePermission(role_id=role.id, permission_id=permission.id))
                changes += 1
        if changes:
            await session.flush()
            await AuthorizationStateRepository(session).bump()
    return changes

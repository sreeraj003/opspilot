"""Administration of roles, permissions and assignments.

Route-level permission checks (``require_permission``) happen first; this
service enforces the rules that hold whatever the caller's permissions:

* System roles and permissions (seeded by migrations) cannot be deleted,
  renamed, deactivated or re-scoped; the ``administrator`` role's permission
  set is fixed.
* Administrator-only system permissions can never be granted to a custom role
  or directly to a user (no delegation model exists yet).
* Nobody except the installation administrator can change their own role or
  permission assignments (no self-escalation, no accidental self-lockout).
* The last active administrator cannot lose the administrator role.
* Every policy is validated and stored in normalised form; arbitrary JSON is
  never stored.
* Each change writes an ``RbacAuditEvent`` and increments the authorization
  revision in the same transaction, so every cache sees it immediately.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.authorization.cache import AuthorizationCache
from app.authorization.engine import AuthorizationEngine, EffectiveAccess
from app.authorization.policy import (
    PolicyLimits,
    PolicyValidationError,
    SchemaCatalog,
    UnknownSchemaCatalog,
    validate_override,
    validate_policy,
)
from app.core.authorization import (
    ADMINISTRATOR_ONLY_PERMISSIONS,
    ADMINISTRATOR_ROLE_KEY,
    SYSTEM_RESOURCE_TYPE,
)
from app.core.exceptions import ConflictError, InvalidInputError, NotFoundError, PermissionDeniedError
from app.database.transaction import transaction
from app.models.rbac import (
    AssignmentType,
    PermissionDefinition,
    RbacAuditEvent,
    RbacEventType,
    Role,
    RolePermission,
    UserPermission,
    UserRole,
)
from app.models.user import User
from app.repositories.base_repository import Page
from app.repositories.rbac_repository import (
    AssignmentRepository,
    AuthorizationStateRepository,
    CatalogQuery,
    PermissionRepository,
    RbacAuditRepository,
    RoleRepository,
)
from app.repositories.user_repository import UserRepository
from app.schemas.rbac import (
    PermissionCreate,
    PermissionUpdate,
    RoleCreate,
    RoleUpdate,
    UserPermissionAssign,
    UserRoleAssign,
)

_UNIQUE_VIOLATION = "23505"


def utcnow() -> datetime:
    return datetime.now(UTC)


class RoleNotFoundError(NotFoundError):
    message = "The role was not found."


class PermissionNotFoundError(NotFoundError):
    message = "The permission was not found."


class UserNotFoundError(NotFoundError):
    message = "The user was not found."


class AssignmentNotFoundError(NotFoundError):
    message = "No active assignment was found."


class PolicyInvalidError(InvalidInputError):
    code = "INVALID_POLICY"
    message = "The permission policy is not valid."


class SelfAssignmentError(PermissionDeniedError):
    message = "You cannot change your own roles or permissions. Ask another administrator."


@dataclass(frozen=True, slots=True)
class RoleCounts:
    users: int
    permissions: int


@dataclass(frozen=True, slots=True)
class RoleDetailData:
    role: Role
    counts: RoleCounts
    permissions: Sequence[tuple[RolePermission, PermissionDefinition]]
    users: Sequence[tuple[UserRole, User]]
    history: Sequence[RbacAuditEvent]


@dataclass(frozen=True, slots=True)
class PermissionDetailData:
    permission: PermissionDefinition
    role_count: int
    user_count: int
    roles: Sequence[Role]
    history: Sequence[RbacAuditEvent]


def _policy_error(exc: PolicyValidationError) -> PolicyInvalidError:
    return PolicyInvalidError(details=[issue.as_dict() for issue in exc.issues])


class RbacService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        cache: AuthorizationCache | None,
        limits: PolicyLimits | None = None,
        catalog: SchemaCatalog | None = None,
    ) -> None:
        self._session = session
        self._cache = cache
        self._limits = limits or PolicyLimits()
        self._catalog = catalog or UnknownSchemaCatalog()
        self._roles = RoleRepository(session)
        self._permissions = PermissionRepository(session)
        self._assignments = AssignmentRepository(session)
        self._audit = RbacAuditRepository(session)
        self._state = AuthorizationStateRepository(session)
        self._users = UserRepository(session)
        self.engine = AuthorizationEngine(session, cache)

    # --- roles: reads --------------------------------------------------------------------------

    async def list_roles(self, query: CatalogQuery, *, offset: int, limit: int) -> tuple[Page[Role], dict[uuid.UUID, RoleCounts]]:
        page = await self._roles.search(query, offset=offset, limit=limit)
        counts = await self._roles.counts([r.id for r in page.items], utcnow())
        return page, {rid: RoleCounts(*c) for rid, c in counts.items()}

    async def get_role(self, role_id: uuid.UUID) -> RoleDetailData:
        role = await self._role(role_id)
        now = utcnow()
        counts = (await self._roles.counts([role.id], now))[role.id]
        return RoleDetailData(
            role=role,
            counts=RoleCounts(*counts),
            permissions=await self._assignments.active_role_permissions(role.id),
            users=await self._assignments.role_users(role.id, now),
            history=await self._audit.history(role_id=role.id),
        )

    # --- roles: writes --------------------------------------------------------------------------

    async def create_role(self, data: RoleCreate, actor: User) -> Role:
        try:
            async with transaction(self._session):
                await self._ensure_role_unique(data.key, data.name)
                role = await self._roles.create(
                    key=data.key, name=data.name, description=data.description, is_active=data.is_active, is_system=False
                )
                await self._record(
                    RbacEventType.ROLE_CREATED, actor, role=role, details={"key": role.key, "name": role.name}
                )
        except IntegrityError as exc:
            raise _conflict(exc, "A role with this key or name already exists.") from None
        return role

    async def update_role(self, role_id: uuid.UUID, data: RoleUpdate, actor: User) -> Role:
        changes = data.model_dump(exclude_unset=True)
        for required in ("key", "name", "is_active"):
            if required in changes and changes[required] is None:
                raise InvalidInputError(f"{required} cannot be removed.")
        try:
            async with transaction(self._session):
                role = await self._role(role_id, for_update=True)
                changes = {k: v for k, v in changes.items() if getattr(role, k) != v}
                if not changes:
                    return role
                if role.is_system and set(changes) - {"description"}:
                    raise ConflictError("System roles cannot be renamed or deactivated; only the description can change.")
                await self._ensure_role_unique(changes.get("key"), changes.get("name"), exclude_id=role.id)
                before = {k: getattr(role, k) for k in changes}
                await self._roles.update(role, changes)
                event = RbacEventType.ROLE_UPDATED
                if set(changes) == {"is_active"}:
                    event = RbacEventType.ROLE_ACTIVATED if role.is_active else RbacEventType.ROLE_DEACTIVATED
                await self._record(
                    event, actor, role=role, details={"key": role.key, "before": before, "after": changes}
                )
        except IntegrityError as exc:
            raise _conflict(exc, "A role with this key or name already exists.") from None
        return role

    async def delete_role(self, role_id: uuid.UUID, actor: User) -> None:
        async with transaction(self._session):
            role = await self._role(role_id, for_update=True)
            if role.is_system:
                raise ConflictError("System roles cannot be deleted.")
            now = utcnow()
            assigned = await self._assignments.active_user_count_for_role(role.id, now)
            if assigned:
                raise ConflictError(
                    f"This role is assigned to {assigned} user{'s' if assigned != 1 else ''}. "
                    "Remove it from them (or deactivate the role) before deleting it."
                )
            await self._assignments.revoke_all_for_role(role.id, actor.id, now)  # expired leftovers
            for assignment, _ in await self._assignments.active_role_permissions(role.id):
                assignment.revoked_at, assignment.revoked_by = now, actor.id
            role.is_active = False
            await self._roles.delete(role)  # soft delete keeps history readable
            await self._record(RbacEventType.ROLE_DELETED, actor, role=role, details={"key": role.key, "name": role.name})

    # --- role permissions ----------------------------------------------------------------------

    async def assign_role_permissions(
        self, role_id: uuid.UUID, permission_ids: Sequence[uuid.UUID], actor: User
    ) -> list[PermissionDefinition]:
        try:
            async with transaction(self._session):
                role = await self._role(role_id, for_update=True)
                self._ensure_role_permissions_editable(role)
                permissions = await self._permissions_for_assignment(permission_ids)
                for permission in permissions:
                    if await self._assignments.active_role_permission(role.id, permission.id):
                        raise ConflictError(f"The role already has the permission '{permission.key}'.")
                    await self._add_role_permission(role, permission, actor)
        except IntegrityError as exc:
            raise _conflict(exc, "The role already has one of these permissions.") from None
        return permissions

    async def replace_role_permissions(
        self, role_id: uuid.UUID, permission_ids: Sequence[uuid.UUID], actor: User
    ) -> tuple[int, int]:
        """Make the role's permissions exactly ``permission_ids``. Returns (added, removed)."""
        try:
            async with transaction(self._session):
                role = await self._role(role_id, for_update=True)
                self._ensure_role_permissions_editable(role)
                wanted = await self._permissions_for_assignment(permission_ids, allow_empty=True)
                current = {p.id: (rp, p) for rp, p in await self._assignments.active_role_permissions(role.id)}
                wanted_ids = {p.id for p in wanted}
                added = removed = 0
                for permission in wanted:
                    if permission.id not in current:
                        await self._add_role_permission(role, permission, actor)
                        added += 1
                for permission_id, (assignment, permission) in current.items():
                    if permission_id not in wanted_ids:
                        await self._revoke_role_permission(role, assignment, permission, actor)
                        removed += 1
        except IntegrityError as exc:
            raise _conflict(exc, "The role's permissions were changed concurrently. Reload and retry.") from None
        return added, removed

    async def revoke_role_permission(self, role_id: uuid.UUID, permission_id: uuid.UUID, actor: User) -> None:
        async with transaction(self._session):
            role = await self._role(role_id, for_update=True)
            self._ensure_role_permissions_editable(role)
            assignment = await self._assignments.active_role_permission(role.id, permission_id)
            if assignment is None:
                raise AssignmentNotFoundError("The role does not have this permission.")
            permission = await self._permissions.get(permission_id, include_deleted=True)
            assert permission is not None
            await self._revoke_role_permission(role, assignment, permission, actor)

    async def _add_role_permission(self, role: Role, permission: PermissionDefinition, actor: User) -> None:
        self._session.add(RolePermission(role_id=role.id, permission_id=permission.id, assigned_by=actor.id))
        await self._session.flush()
        await self._record(
            RbacEventType.ROLE_PERMISSION_ASSIGNED,
            actor,
            role=role,
            permission=permission,
            details={"role_key": role.key, "permission_key": permission.key},
        )

    async def _revoke_role_permission(
        self, role: Role, assignment: RolePermission, permission: PermissionDefinition, actor: User
    ) -> None:
        assignment.revoked_at, assignment.revoked_by = utcnow(), actor.id
        await self._session.flush()
        await self._record(
            RbacEventType.ROLE_PERMISSION_REVOKED,
            actor,
            role=role,
            permission=permission,
            details={"role_key": role.key, "permission_key": permission.key},
        )

    @staticmethod
    def _ensure_role_permissions_editable(role: Role) -> None:
        if role.is_system:
            raise ConflictError("The permissions of system roles are fixed and cannot be changed.")

    async def _permissions_for_assignment(
        self, permission_ids: Sequence[uuid.UUID], *, allow_empty: bool = False
    ) -> list[PermissionDefinition]:
        ids = list(dict.fromkeys(permission_ids))
        if not ids and not allow_empty:
            raise InvalidInputError("Choose at least one permission.")
        found = await self._permissions.get_many(ids)
        missing = [pid for pid in ids if pid not in found]
        if missing:
            raise PermissionNotFoundError(f"Permission {missing[0]} was not found.")
        permissions = [found[pid] for pid in ids]
        for permission in permissions:
            self._ensure_assignable(permission)
        return permissions

    @staticmethod
    def _ensure_assignable(permission: PermissionDefinition) -> None:
        if permission.key in ADMINISTRATOR_ONLY_PERMISSIONS:
            raise InvalidInputError(
                f"'{permission.key}' is reserved for administrators: assign the Administrator role instead."
            )

    # --- permissions: reads -------------------------------------------------------------------

    async def list_permissions(
        self, query: CatalogQuery, *, offset: int, limit: int
    ) -> tuple[Page[PermissionDefinition], dict[uuid.UUID, tuple[int, int]]]:
        page = await self._permissions.search(query, offset=offset, limit=limit)
        return page, await self._permissions.assignment_counts([p.id for p in page.items])

    async def get_permission(self, permission_id: uuid.UUID) -> PermissionDetailData:
        permission = await self._permission(permission_id)
        roles, users = (await self._permissions.assignment_counts([permission.id]))[permission.id]
        return PermissionDetailData(
            permission=permission,
            role_count=roles,
            user_count=users,
            roles=await self._permissions.roles_with_permission(permission.id),
            history=await self._audit.history(permission_id=permission.id),
        )

    def validate_policy(self, resource_type: str, policy: Any) -> dict[str, Any]:
        try:
            return validate_policy(resource_type, policy, limits=self._limits, catalog=self._catalog)
        except PolicyValidationError as exc:
            raise _policy_error(exc) from None

    # --- permissions: writes -------------------------------------------------------------------

    async def create_permission(self, data: PermissionCreate, actor: User) -> PermissionDefinition:
        if data.resource_type == SYSTEM_RESOURCE_TYPE:
            raise InvalidInputError("The 'system' resource type is reserved for built-in permissions.")
        self._ensure_future(data.expires_at, "expires_at")
        policy = self.validate_policy(data.resource_type, data.policy_definition)
        try:
            async with transaction(self._session):
                if await self._permissions.key_taken(data.key):
                    raise ConflictError("A permission with this key already exists.")
                permission = await self._permissions.create(
                    key=data.key,
                    name=data.name,
                    description=data.description,
                    resource_type=data.resource_type,
                    action=data.action,
                    policy_definition=policy,
                    is_active=data.is_active,
                    expires_at=data.expires_at,
                    is_system=False,
                )
                await self._record(
                    RbacEventType.PERMISSION_CREATED,
                    actor,
                    permission=permission,
                    details={
                        "key": permission.key,
                        "resource_type": permission.resource_type,
                        "action": permission.action,
                        "policy": policy,
                    },
                )
        except IntegrityError as exc:
            raise _conflict(exc, "A permission with this key already exists.") from None
        return permission

    async def update_permission(self, permission_id: uuid.UUID, data: PermissionUpdate, actor: User) -> PermissionDefinition:
        changes = data.model_dump(exclude_unset=True)
        for required in ("name", "resource_type", "action", "policy_definition", "is_active"):
            if required in changes and changes[required] is None:
                raise InvalidInputError(f"{required} cannot be removed.")
        if changes.get("resource_type") == SYSTEM_RESOURCE_TYPE:
            raise InvalidInputError("The 'system' resource type is reserved for built-in permissions.")
        if "expires_at" in changes:
            self._ensure_future(changes["expires_at"], "expires_at")
        async with transaction(self._session):
            permission = await self._permission(permission_id, for_update=True)
            if permission.is_system:
                raise ConflictError("Built-in system permissions cannot be changed.")
            resource_type = changes.get("resource_type", permission.resource_type)
            if "policy_definition" in changes or "resource_type" in changes:
                changes["policy_definition"] = self.validate_policy(
                    resource_type, changes.get("policy_definition", permission.policy_definition)
                )
            changes = {k: v for k, v in changes.items() if getattr(permission, k) != v}
            if not changes:
                return permission
            before = {k: _jsonable(getattr(permission, k)) for k in changes}
            await self._permissions.update(permission, changes)
            event = RbacEventType.PERMISSION_UPDATED
            if set(changes) == {"is_active"}:
                event = RbacEventType.PERMISSION_ACTIVATED if permission.is_active else RbacEventType.PERMISSION_DEACTIVATED
            await self._record(
                event,
                actor,
                permission=permission,
                details={"key": permission.key, "before": before, "after": {k: _jsonable(v) for k, v in changes.items()}},
            )
        return permission

    async def delete_permission(self, permission_id: uuid.UUID, actor: User) -> None:
        async with transaction(self._session):
            permission = await self._permission(permission_id, for_update=True)
            if permission.is_system:
                raise ConflictError("Built-in system permissions cannot be deleted.")
            assigned = await self._assignments.active_assignment_count(permission.id)
            if assigned:
                raise ConflictError(
                    "This permission is still assigned. Deactivate it instead, or remove it from every role and user first."
                )
            permission.is_active = False
            await self._permissions.delete(permission)  # soft delete: history keeps pointing at it
            await self._record(
                RbacEventType.PERMISSION_DELETED, actor, permission=permission, details={"key": permission.key}
            )

    # --- user roles --------------------------------------------------------------------------------

    async def list_user_roles(self, user_id: uuid.UUID) -> Sequence[tuple[UserRole, Role]]:
        await self._user(user_id)
        return await self._assignments.active_user_roles(user_id, utcnow(), include_expired=True)

    async def assign_user_role(self, user_id: uuid.UUID, data: UserRoleAssign, actor: User) -> tuple[UserRole, Role]:
        self._ensure_not_self(user_id, actor)
        self._ensure_future(data.expires_at, "expires_at")
        try:
            async with transaction(self._session):
                user = await self._user(user_id, for_update=True)
                role = await self._role(data.role_id)
                if not role.is_active:
                    raise InvalidInputError("Activate the role before assigning it.")
                now = utcnow()
                existing = await self._assignments.unrevoked_user_role(user.id, role.id)
                if existing is not None:
                    if existing.expires_at is None or existing.expires_at > now:
                        raise ConflictError("The user already has this role.")
                    existing.revoked_at = now  # an expired assignment is closed before a new one starts
                    await self._session.flush()
                assignment = UserRole(user_id=user.id, role_id=role.id, assigned_by=actor.id, expires_at=data.expires_at)
                self._session.add(assignment)
                await self._session.flush()
                await self._record(
                    RbacEventType.USER_ROLE_ASSIGNED,
                    actor,
                    role=role,
                    target_user_id=user.id,
                    details={"role_key": role.key, "expires_at": _jsonable(data.expires_at)},
                )
        except IntegrityError as exc:
            raise _conflict(exc, "The user already has this role.") from None
        return assignment, role

    async def revoke_user_role(self, user_id: uuid.UUID, role_id: uuid.UUID, actor: User) -> None:
        self._ensure_not_self(user_id, actor)
        async with transaction(self._session):
            user = await self._user(user_id, for_update=True)
            role = await self._role(role_id)
            assignment = await self._assignments.unrevoked_user_role(user.id, role.id)
            if assignment is None:
                raise AssignmentNotFoundError("The user does not have this role.")
            now = utcnow()
            if role.key == ADMINISTRATOR_ROLE_KEY and role.is_system:
                remaining = await self._assignments.count_active_administrators(role.id, now)
                still_admin = user.is_superuser and user.is_active
                if remaining <= 1 and not still_admin:
                    raise ConflictError("This is the last active administrator. Make someone else an administrator first.")
            assignment.revoked_at, assignment.revoked_by = now, actor.id
            await self._session.flush()
            await self._record(
                RbacEventType.USER_ROLE_REVOKED, actor, role=role, target_user_id=user.id, details={"role_key": role.key}
            )

    # --- direct user permissions ----------------------------------------------------------------------

    async def list_user_permissions(self, user_id: uuid.UUID) -> Sequence[tuple[UserPermission, PermissionDefinition]]:
        await self._user(user_id)
        return await self._assignments.active_user_permissions(user_id, utcnow())

    async def assign_user_permission(
        self, user_id: uuid.UUID, data: UserPermissionAssign, actor: User
    ) -> tuple[UserPermission, PermissionDefinition]:
        self._ensure_not_self(user_id, actor)
        self._ensure_future(data.expires_at, "expires_at")
        try:
            async with transaction(self._session):
                user = await self._user(user_id, for_update=True)
                permission = await self._permission(data.permission_id)
                self._ensure_assignable(permission)
                override = None
                if data.policy_override is not None:
                    if data.assignment_type != AssignmentType.GRANT:
                        raise InvalidInputError("A policy override can only narrow a granted permission.")
                    try:
                        override = validate_override(
                            permission.policy_definition, data.policy_override, limits=self._limits, catalog=self._catalog
                        )
                    except PolicyValidationError as exc:
                        raise _policy_error(exc) from None
                now = utcnow()
                existing = await self._assignments.unrevoked_user_permission(user.id, permission.id)
                if existing is not None:
                    if existing.expires_at is None or existing.expires_at > now:
                        raise ConflictError("The user already has an assignment for this permission.")
                    existing.revoked_at = now
                    await self._session.flush()
                assignment = UserPermission(
                    user_id=user.id,
                    permission_id=permission.id,
                    assignment_type=data.assignment_type,
                    policy_override=override,
                    assigned_by=actor.id,
                    expires_at=data.expires_at,
                )
                self._session.add(assignment)
                await self._session.flush()
                await self._record(
                    RbacEventType.USER_PERMISSION_ASSIGNED,
                    actor,
                    permission=permission,
                    target_user_id=user.id,
                    details={
                        "permission_key": permission.key,
                        "assignment_type": data.assignment_type,
                        "policy_override": override,
                        "expires_at": _jsonable(data.expires_at),
                    },
                )
        except IntegrityError as exc:
            raise _conflict(exc, "The user already has an assignment for this permission.") from None
        return assignment, permission

    async def revoke_user_permission(self, user_id: uuid.UUID, permission_id: uuid.UUID, actor: User) -> None:
        self._ensure_not_self(user_id, actor)
        async with transaction(self._session):
            user = await self._user(user_id, for_update=True)
            assignment = await self._assignments.unrevoked_user_permission(user.id, permission_id)
            if assignment is None:
                raise AssignmentNotFoundError("The user has no assignment for this permission.")
            permission = await self._permissions.get(permission_id, include_deleted=True)
            assignment.revoked_at, assignment.revoked_by = utcnow(), actor.id
            await self._session.flush()
            await self._record(
                RbacEventType.USER_PERMISSION_REVOKED,
                actor,
                permission=permission,
                target_user_id=user.id,
                details={"permission_key": permission.key if permission else None},
            )

    # --- effective permissions ---------------------------------------------------------------------------

    async def effective_for(self, user_id: uuid.UUID) -> tuple[User, EffectiveAccess]:
        user = await self._user(user_id)
        return user, await self.engine.effective(user)

    # --- internals ------------------------------------------------------------------------------------

    async def _role(self, role_id: uuid.UUID, *, for_update: bool = False) -> Role:
        role = await (self._roles.get_for_update(role_id) if for_update else self._roles.get(role_id))
        if role is None:
            raise RoleNotFoundError()
        return role

    async def _permission(self, permission_id: uuid.UUID, *, for_update: bool = False) -> PermissionDefinition:
        permission = await (
            self._permissions.get_for_update(permission_id) if for_update else self._permissions.get(permission_id)
        )
        if permission is None:
            raise PermissionNotFoundError()
        return permission

    async def _user(self, user_id: uuid.UUID, *, for_update: bool = False) -> User:
        user = await (self._users.get_for_update(user_id) if for_update else self._users.get(user_id))
        if user is None:
            raise UserNotFoundError()
        return user

    async def _ensure_role_unique(self, key: str | None, name: str | None, *, exclude_id: uuid.UUID | None = None) -> None:
        if key is not None and await self._roles.key_taken(key, exclude_id=exclude_id):
            raise ConflictError("A role with this key already exists.")
        if name is not None and await self._roles.name_taken(name, exclude_id=exclude_id):
            raise ConflictError("A role with this name already exists.")

    @staticmethod
    def _ensure_not_self(user_id: uuid.UUID, actor: User) -> None:
        # The installation administrator is the root of trust (the host CLI can do
        # anything anyway); everyone else needs a second administrator.
        if user_id == actor.id and not actor.is_superuser:
            raise SelfAssignmentError()

    @staticmethod
    def _ensure_future(value: datetime | None, field_name: str) -> None:
        if value is not None and value <= utcnow():
            raise InvalidInputError(f"{field_name} must be in the future.")

    async def _record(
        self,
        event_type: str,
        actor: User,
        *,
        role: Role | None = None,
        permission: PermissionDefinition | None = None,
        target_user_id: uuid.UUID | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        await self._audit.record(
            event_type,
            actor_user_id=actor.id,
            target_user_id=target_user_id,
            role_id=role.id if role else None,
            permission_id=permission.id if permission else None,
            details=details,
        )
        # Same transaction as the change: every cache sees it as soon as it commits.
        await self._state.bump()


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def _conflict(exc: IntegrityError, message: str) -> Exception:
    if getattr(exc.orig, "sqlstate", None) == _UNIQUE_VIOLATION:
        return ConflictError(message)
    return exc

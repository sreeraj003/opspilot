"""Persistence for roles, permissions, assignments, RBAC audit events and the authorization revision."""

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import Select, and_, func, or_, select, update
from sqlalchemy.engine import Row

from app.models.rbac import (
    AuthorizationState,
    PermissionDefinition,
    RbacAuditEvent,
    Role,
    RolePermission,
    UserPermission,
    UserRole,
)
from app.models.user import User
from app.repositories.base_repository import BaseRepository, Page, validate_pagination


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class StatusFilter(StrEnum):
    ALL = "all"
    ACTIVE = "active"
    INACTIVE = "inactive"


@dataclass(frozen=True, slots=True)
class CatalogQuery:
    search: str | None = None
    status: StatusFilter = StatusFilter.ALL
    resource_type: str | None = None


def _active(column: Any, now: datetime) -> Any:
    """``column`` (an expiry) is unset or in the future."""
    return or_(column.is_(None), column > now)


# --- roles ------------------------------------------------------------------------------------


class RoleRepository(BaseRepository[Role]):
    model = Role
    default_order_by = (Role.name, Role.id)

    async def get_for_update(self, role_id: uuid.UUID) -> Role | None:
        statement = self._select().where(Role.id == role_id).with_for_update()
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def get_by_key(self, key: str) -> Role | None:
        return (await self.session.execute(self._select().where(Role.key == key))).scalar_one_or_none()

    async def key_taken(self, key: str, *, exclude_id: uuid.UUID | None = None) -> bool:
        statement = self._select().where(Role.key == key)
        if exclude_id is not None:
            statement = statement.where(Role.id != exclude_id)
        return bool(await self.session.scalar(select(statement.exists())))

    async def name_taken(self, name: str, *, exclude_id: uuid.UUID | None = None) -> bool:
        statement = self._select().where(func.lower(Role.name) == name.lower())
        if exclude_id is not None:
            statement = statement.where(Role.id != exclude_id)
        return bool(await self.session.scalar(select(statement.exists())))

    async def search(self, query: CatalogQuery, *, offset: int, limit: int) -> Page[Role]:
        validate_pagination(offset, limit)
        statement = self._select()
        if query.search:
            pattern = f"%{_escape_like(query.search.strip().lower())}%"
            statement = statement.where(
                or_(
                    func.lower(Role.name).like(pattern, escape="\\"),
                    Role.key.like(pattern, escape="\\"),
                    func.lower(func.coalesce(Role.description, "")).like(pattern, escape="\\"),
                )
            )
        if query.status is StatusFilter.ACTIVE:
            statement = statement.where(Role.is_active.is_(True))
        elif query.status is StatusFilter.INACTIVE:
            statement = statement.where(Role.is_active.is_(False))
        items = (
            (await self.session.execute(self._ordered(statement).offset(offset).limit(limit))).scalars().all()
        )
        total = int(await self.session.scalar(select(func.count()).select_from(statement.subquery())) or 0)
        return Page(items=items, total=total, offset=offset, limit=limit)

    async def counts(self, role_ids: Sequence[uuid.UUID], now: datetime) -> dict[uuid.UUID, tuple[int, int]]:
        """(active users, active permissions) per role, in two grouped queries."""
        if not role_ids:
            return {}
        users = dict(
            (
                await self.session.execute(
                    select(UserRole.role_id, func.count())
                    .join(User, User.id == UserRole.user_id)
                    .where(
                        UserRole.role_id.in_(role_ids),
                        UserRole.revoked_at.is_(None),
                        _active(UserRole.expires_at, now),
                        User.deleted_at.is_(None),
                    )
                    .group_by(UserRole.role_id)
                )
            ).all()
        )
        permissions = dict(
            (
                await self.session.execute(
                    select(RolePermission.role_id, func.count())
                    .join(PermissionDefinition, PermissionDefinition.id == RolePermission.permission_id)
                    .where(
                        RolePermission.role_id.in_(role_ids),
                        RolePermission.revoked_at.is_(None),
                        PermissionDefinition.deleted_at.is_(None),
                    )
                    .group_by(RolePermission.role_id)
                )
            ).all()
        )
        return {role_id: (int(users.get(role_id, 0)), int(permissions.get(role_id, 0))) for role_id in role_ids}


# --- permissions ---------------------------------------------------------------------------


class PermissionRepository(BaseRepository[PermissionDefinition]):
    model = PermissionDefinition
    default_order_by = (PermissionDefinition.key, PermissionDefinition.id)

    async def get_for_update(self, permission_id: uuid.UUID) -> PermissionDefinition | None:
        statement = self._select().where(PermissionDefinition.id == permission_id).with_for_update()
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def get_many(self, ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, PermissionDefinition]:
        ids = list(ids)
        if not ids:
            return {}
        rows = (await self.session.execute(self._select().where(PermissionDefinition.id.in_(ids)))).scalars().all()
        return {row.id: row for row in rows}

    async def get_by_keys(self, keys: Iterable[str]) -> dict[str, PermissionDefinition]:
        keys = list(keys)
        if not keys:
            return {}
        rows = (await self.session.execute(self._select().where(PermissionDefinition.key.in_(keys)))).scalars().all()
        return {row.key: row for row in rows}

    async def key_taken(self, key: str) -> bool:
        statement = self._select().where(PermissionDefinition.key == key)
        return bool(await self.session.scalar(select(statement.exists())))

    async def search(self, query: CatalogQuery, *, offset: int, limit: int) -> Page[PermissionDefinition]:
        validate_pagination(offset, limit)
        statement = self._select()
        if query.search:
            pattern = f"%{_escape_like(query.search.strip().lower())}%"
            statement = statement.where(
                or_(
                    func.lower(PermissionDefinition.name).like(pattern, escape="\\"),
                    PermissionDefinition.key.like(pattern, escape="\\"),
                    func.lower(func.coalesce(PermissionDefinition.description, "")).like(pattern, escape="\\"),
                )
            )
        if query.status is StatusFilter.ACTIVE:
            statement = statement.where(PermissionDefinition.is_active.is_(True))
        elif query.status is StatusFilter.INACTIVE:
            statement = statement.where(PermissionDefinition.is_active.is_(False))
        if query.resource_type:
            statement = statement.where(PermissionDefinition.resource_type == query.resource_type)
        items = (
            (await self.session.execute(self._ordered(statement).offset(offset).limit(limit))).scalars().all()
        )
        total = int(await self.session.scalar(select(func.count()).select_from(statement.subquery())) or 0)
        return Page(items=items, total=total, offset=offset, limit=limit)

    async def assignment_counts(self, permission_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, tuple[int, int]]:
        """(roles, direct user assignments) currently holding each permission."""
        if not permission_ids:
            return {}
        roles = dict(
            (
                await self.session.execute(
                    select(RolePermission.permission_id, func.count())
                    .join(Role, Role.id == RolePermission.role_id)
                    .where(
                        RolePermission.permission_id.in_(permission_ids),
                        RolePermission.revoked_at.is_(None),
                        Role.deleted_at.is_(None),
                    )
                    .group_by(RolePermission.permission_id)
                )
            ).all()
        )
        users = dict(
            (
                await self.session.execute(
                    select(UserPermission.permission_id, func.count())
                    .where(UserPermission.permission_id.in_(permission_ids), UserPermission.revoked_at.is_(None))
                    .group_by(UserPermission.permission_id)
                )
            ).all()
        )
        return {pid: (int(roles.get(pid, 0)), int(users.get(pid, 0))) for pid in permission_ids}

    async def roles_with_permission(self, permission_id: uuid.UUID) -> Sequence[Role]:
        statement = (
            select(Role)
            .join(RolePermission, RolePermission.role_id == Role.id)
            .where(
                RolePermission.permission_id == permission_id,
                RolePermission.revoked_at.is_(None),
                Role.deleted_at.is_(None),
            )
            .order_by(Role.name)
        )
        return (await self.session.execute(statement)).scalars().all()


# --- assignments ------------------------------------------------------------------------------


class AssignmentRepository:
    def __init__(self, session: Any) -> None:
        self.session = session

    # role <-> permission

    async def active_role_permissions(self, role_id: uuid.UUID) -> Sequence[tuple[RolePermission, PermissionDefinition]]:
        statement = (
            select(RolePermission, PermissionDefinition)
            .join(PermissionDefinition, PermissionDefinition.id == RolePermission.permission_id)
            .where(
                RolePermission.role_id == role_id,
                RolePermission.revoked_at.is_(None),
                PermissionDefinition.deleted_at.is_(None),
            )
            .order_by(PermissionDefinition.key)
        )
        return [(rp, p) for rp, p in (await self.session.execute(statement)).all()]

    async def active_role_permission(self, role_id: uuid.UUID, permission_id: uuid.UUID) -> RolePermission | None:
        statement = select(RolePermission).where(
            RolePermission.role_id == role_id,
            RolePermission.permission_id == permission_id,
            RolePermission.revoked_at.is_(None),
        )
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def revoke_all_for_role(self, role_id: uuid.UUID, actor_id: uuid.UUID, now: datetime) -> int:
        result = await self.session.execute(
            update(UserRole)
            .where(UserRole.role_id == role_id, UserRole.revoked_at.is_(None))
            .values(revoked_at=now, revoked_by=actor_id)
        )
        return int(result.rowcount or 0)

    # user <-> role

    async def active_user_roles(
        self, user_id: uuid.UUID, now: datetime, *, include_expired: bool = False
    ) -> Sequence[tuple[UserRole, Role]]:
        statement = (
            select(UserRole, Role)
            .join(Role, Role.id == UserRole.role_id)
            .where(UserRole.user_id == user_id, UserRole.revoked_at.is_(None), Role.deleted_at.is_(None))
            .order_by(Role.name)
        )
        if not include_expired:
            statement = statement.where(_active(UserRole.expires_at, now))
        return [(ur, r) for ur, r in (await self.session.execute(statement)).all()]

    async def unrevoked_user_role(self, user_id: uuid.UUID, role_id: uuid.UUID) -> UserRole | None:
        statement = (
            select(UserRole)
            .where(UserRole.user_id == user_id, UserRole.role_id == role_id, UserRole.revoked_at.is_(None))
            .with_for_update()
        )
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def role_users(self, role_id: uuid.UUID, now: datetime, limit: int = 500) -> Sequence[tuple[UserRole, User]]:
        statement = (
            select(UserRole, User)
            .join(User, User.id == UserRole.user_id)
            .where(
                UserRole.role_id == role_id,
                UserRole.revoked_at.is_(None),
                _active(UserRole.expires_at, now),
                User.deleted_at.is_(None),
            )
            .order_by(User.full_name, User.id)
            .limit(limit)
        )
        return [(ur, u) for ur, u in (await self.session.execute(statement)).all()]

    async def count_active_administrators(self, admin_role_id: uuid.UUID, now: datetime) -> int:
        """Live, active accounts that are administrators (installation admin or administrator role)."""
        has_role = (
            select(UserRole.id)
            .where(
                UserRole.user_id == User.id,
                UserRole.role_id == admin_role_id,
                UserRole.revoked_at.is_(None),
                _active(UserRole.expires_at, now),
            )
            .exists()
        )
        statement = select(func.count()).where(
            User.deleted_at.is_(None), User.is_active.is_(True), or_(User.is_superuser.is_(True), has_role)
        )
        return int(await self.session.scalar(statement) or 0)

    # user <-> permission

    async def active_user_permissions(
        self, user_id: uuid.UUID, now: datetime
    ) -> Sequence[tuple[UserPermission, PermissionDefinition]]:
        statement = (
            select(UserPermission, PermissionDefinition)
            .join(PermissionDefinition, PermissionDefinition.id == UserPermission.permission_id)
            .where(
                UserPermission.user_id == user_id,
                UserPermission.revoked_at.is_(None),
                _active(UserPermission.expires_at, now),
                PermissionDefinition.deleted_at.is_(None),
            )
            .order_by(PermissionDefinition.key)
        )
        return [(up, p) for up, p in (await self.session.execute(statement)).all()]

    async def unrevoked_user_permission(self, user_id: uuid.UUID, permission_id: uuid.UUID) -> UserPermission | None:
        statement = (
            select(UserPermission)
            .where(
                UserPermission.user_id == user_id,
                UserPermission.permission_id == permission_id,
                UserPermission.revoked_at.is_(None),
            )
            .with_for_update()
        )
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def active_assignment_count(self, permission_id: uuid.UUID) -> int:
        roles = await self.session.scalar(
            select(func.count())
            .select_from(RolePermission)
            .join(Role, Role.id == RolePermission.role_id)
            .where(
                RolePermission.permission_id == permission_id,
                RolePermission.revoked_at.is_(None),
                Role.deleted_at.is_(None),
            )
        )
        users = await self.session.scalar(
            select(func.count())
            .select_from(UserPermission)
            .where(UserPermission.permission_id == permission_id, UserPermission.revoked_at.is_(None))
        )
        return int(roles or 0) + int(users or 0)

    async def active_user_count_for_role(self, role_id: uuid.UUID, now: datetime) -> int:
        statement = (
            select(func.count())
            .select_from(UserRole)
            .join(User, User.id == UserRole.user_id)
            .where(
                UserRole.role_id == role_id,
                UserRole.revoked_at.is_(None),
                _active(UserRole.expires_at, now),
                User.deleted_at.is_(None),
            )
        )
        return int(await self.session.scalar(statement) or 0)

    # --- grant resolution (used by the engine): three queries, any number of users ---------------

    async def load_roles(self, user_ids: Sequence[uuid.UUID], now: datetime) -> Sequence[Row[Any]]:
        statement = (
            select(UserRole.user_id, UserRole.expires_at, Role.id, Role.key, Role.name, Role.is_system)
            .join(Role, Role.id == UserRole.role_id)
            .where(
                UserRole.user_id.in_(user_ids),
                UserRole.revoked_at.is_(None),
                _active(UserRole.expires_at, now),
                Role.is_active.is_(True),
                Role.deleted_at.is_(None),
            )
            .order_by(Role.key)
        )
        return (await self.session.execute(statement)).all()

    async def load_role_grants(self, user_ids: Sequence[uuid.UUID], now: datetime) -> Sequence[Row[Any]]:
        statement = (
            select(UserRole.user_id, UserRole.expires_at, Role.id, Role.key, Role.is_system, PermissionDefinition)
            .join(Role, Role.id == UserRole.role_id)
            .join(RolePermission, and_(RolePermission.role_id == Role.id, RolePermission.revoked_at.is_(None)))
            .join(PermissionDefinition, PermissionDefinition.id == RolePermission.permission_id)
            .where(
                UserRole.user_id.in_(user_ids),
                UserRole.revoked_at.is_(None),
                _active(UserRole.expires_at, now),
                Role.is_active.is_(True),
                Role.deleted_at.is_(None),
                PermissionDefinition.is_active.is_(True),
                PermissionDefinition.deleted_at.is_(None),
                _active(PermissionDefinition.expires_at, now),
            )
        )
        return (await self.session.execute(statement)).all()

    async def load_direct_grants(self, user_ids: Sequence[uuid.UUID], now: datetime) -> Sequence[Row[Any]]:
        statement = (
            select(UserPermission, PermissionDefinition)
            .join(PermissionDefinition, PermissionDefinition.id == UserPermission.permission_id)
            .where(
                UserPermission.user_id.in_(user_ids),
                UserPermission.revoked_at.is_(None),
                _active(UserPermission.expires_at, now),
                PermissionDefinition.is_active.is_(True),
                PermissionDefinition.deleted_at.is_(None),
                _active(PermissionDefinition.expires_at, now),
            )
        )
        return (await self.session.execute(statement)).all()


# --- audit and revision ---------------------------------------------------------------------


class RbacAuditRepository:
    def __init__(self, session: Any) -> None:
        self.session = session

    async def record(
        self,
        event_type: str,
        *,
        actor_user_id: uuid.UUID | None,
        target_user_id: uuid.UUID | None = None,
        role_id: uuid.UUID | None = None,
        permission_id: uuid.UUID | None = None,
        details: dict[str, Any] | None = None,
    ) -> RbacAuditEvent:
        event = RbacAuditEvent(
            event_type=event_type,
            actor_user_id=actor_user_id,
            target_user_id=target_user_id,
            role_id=role_id,
            permission_id=permission_id,
            details=details or {},
        )
        self.session.add(event)
        await self.session.flush()
        return event

    async def history(
        self,
        *,
        role_id: uuid.UUID | None = None,
        permission_id: uuid.UUID | None = None,
        target_user_id: uuid.UUID | None = None,
        limit: int = 50,
    ) -> Sequence[RbacAuditEvent]:
        statement: Select[tuple[RbacAuditEvent]] = select(RbacAuditEvent)
        if role_id is not None:
            statement = statement.where(RbacAuditEvent.role_id == role_id)
        if permission_id is not None:
            statement = statement.where(RbacAuditEvent.permission_id == permission_id)
        if target_user_id is not None:
            statement = statement.where(RbacAuditEvent.target_user_id == target_user_id)
        statement = statement.order_by(RbacAuditEvent.created_at.desc(), RbacAuditEvent.id).limit(limit)
        return (await self.session.execute(statement)).scalars().all()


class AuthorizationStateRepository:
    def __init__(self, session: Any) -> None:
        self.session = session

    async def current_revision(self) -> int:
        revision = await self.session.scalar(select(AuthorizationState.revision).where(AuthorizationState.id == 1))
        return int(revision or 0)

    async def bump(self) -> int:
        """Increment the revision in the caller's transaction (serialises concurrent RBAC writes)."""
        revision = await self.session.scalar(
            update(AuthorizationState)
            .where(AuthorizationState.id == 1)
            .values(revision=AuthorizationState.revision + 1)
            .returning(AuthorizationState.revision)
        )
        if revision is None:  # the seed row is missing (never expected after migration 0003)
            self.session.add(AuthorizationState(id=1, revision=2))
            await self.session.flush()
            return 2
        return int(revision)

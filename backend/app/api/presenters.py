"""Model -> response conversions shared by the auth and user routes."""

from collections.abc import Iterable
from datetime import UTC, datetime

from app.authorization.engine import AuthorizationDecision, EffectiveAccess, Grant, ai_context
from app.core.authorization import SYSTEM_RESOURCE_TYPE, Permission
from app.models.rbac import PermissionDefinition, Role
from app.models.user import User
from app.schemas.auth import CurrentUser
from app.schemas.rbac import (
    AuthorizationDecisionRead,
    DataAccessRead,
    EffectiveGrantRead,
    EffectivePermissionsRead,
    EffectiveRoleRead,
    PermissionRead,
    ResourceAccessRead,
    RoleRead,
    RowFilterRead,
)
from app.schemas.user import UserDetail, UserRead
from app.services.rbac_service import RoleCounts


def _is_locked(user: User, now: datetime) -> bool:
    return user.locked_until is not None and user.locked_until > now


def user_read(user: User, now: datetime | None = None) -> UserRead:
    read = UserRead.model_validate(user)
    read.is_locked = _is_locked(user, now or datetime.now(UTC))
    return read


def user_detail(user: User, active_session_count: int) -> UserDetail:
    return UserDetail(**user_read(user).model_dump(), active_session_count=active_session_count)


def current_user_read(user: User, permissions: Iterable[Permission]) -> CurrentUser:
    return CurrentUser(**user_read(user).model_dump(), permissions=sorted(p.value for p in permissions))


# --- roles, permissions and effective access (Module 04) ---------------------------------------


def role_read(role: Role, counts: RoleCounts | None = None) -> RoleRead:
    read = RoleRead.model_validate(role)
    if counts is not None:
        read.user_count, read.permission_count = counts.users, counts.permissions
    return read


def permission_read(permission: PermissionDefinition, counts: tuple[int, int] | None = None) -> PermissionRead:
    read = PermissionRead.model_validate(permission)
    if counts is not None:
        read.role_count, read.user_count = counts
    return read


def effective_read(user: User, effective: EffectiveAccess) -> EffectivePermissionsRead:
    def grant(g: Grant) -> EffectiveGrantRead:
        return EffectiveGrantRead(
            key=g.key,
            name=g.name,
            resource_type=g.resource_type,
            action=g.action,
            effect=g.effect,
            source=g.source,  # type: ignore[arg-type]
            role_key=g.role_key,
            expires_at=g.expires_at,
        )

    context = ai_context(effective)
    return EffectivePermissionsRead(
        user_id=user.id,
        policy_revision=effective.revision,
        evaluated_at=effective.evaluated_at,
        is_active=user.is_active and user.deleted_at is None,
        is_administrator=effective.is_administrator,
        roles=[EffectiveRoleRead(id=r.id, key=r.key, name=r.name, expires_at=r.expires_at) for r in effective.roles],
        system_permissions=sorted(p.value for p in effective.system_permissions),
        permission_keys=sorted({g.key for g in effective.allows if g.resource_type != SYSTEM_RESOURCE_TYPE}),
        revoked_permission_keys=sorted(effective.revoked_keys),
        grants=[grant(g) for g in effective.allows],
        restrictions=[grant(g) for g in effective.denies],
        data_access=[DataAccessRead.model_validate(d) for d in context["data_access"]],
        resources=[ResourceAccessRead.model_validate(r) for r in context["resources"]],
    )


def decision_read(decision: AuthorizationDecision) -> AuthorizationDecisionRead:
    return AuthorizationDecisionRead(
        allowed=decision.allowed,
        decision_reason=decision.decision_reason,
        user_id=decision.user_id,
        resource=decision.resource,
        action=decision.action,
        policy_revision=decision.policy_revision,
        evaluated_at=decision.evaluated_at,
        allowed_tables=list(decision.allowed_tables),
        allowed_columns=list(decision.allowed_columns),
        denied_columns=list(decision.denied_columns),
        allowed_operations=list(decision.allowed_operations),
        row_filters=[RowFilterRead(table=f.table, filter=f.filter) for f in decision.row_filters],
        allow_export=decision.allow_export,
        allow_sql_view=decision.allow_sql_view,
        matched_permissions=list(decision.matched_permissions),
    )

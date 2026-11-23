"""User administration within an organization: create, list, roles, activation.

Every operation is scoped to the caller's organization; a platform
administrator may act on other organizations. Role assignment is limited to
roles whose permissions the caller already holds, which prevents privilege
escalation without naming any role in code.
"""

import uuid
from collections.abc import Callable
from datetime import datetime

from app.core.exceptions import ConflictError, InvalidInputError, NotFoundError, PermissionDeniedError
from app.models.rbac import Role
from app.models.user import User
from app.repositories.auth_session_repository import AuthSessionRepository
from app.repositories.organization_repository import OrganizationRepository
from app.repositories.rbac_repository import RoleRepository
from app.repositories.user_repository import UserRepository
from app.schemas.users import UserCreateRequest
from app.security.passwords import PasswordHasher, PasswordPolicy
from app.security.principal import Principal
from app.security.tenancy import resolve_organization_scope
from app.services.audit_service import AuditAction, AuditOutcome, AuditService, ClientInfo
from app.utils.time import utc_now


class UserService:
    def __init__(
        self,
        *,
        users: UserRepository,
        organizations: OrganizationRepository,
        roles: RoleRepository,
        auth_sessions: AuthSessionRepository,
        audit: AuditService,
        hasher: PasswordHasher,
        password_policy: PasswordPolicy,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._users = users
        self._organizations = organizations
        self._roles = roles
        self._auth_sessions = auth_sessions
        self._audit = audit
        self._hasher = hasher
        self._policy = password_policy
        self._clock = clock

    def list_users(
        self, principal: Principal, *, organization_id: uuid.UUID | None, limit: int, offset: int
    ) -> tuple[list[User], int]:
        scope = resolve_organization_scope(principal, organization_id)
        return (
            self._users.list_in_organization(scope, limit=limit, offset=offset),
            self._users.count_in_organization(scope),
        )

    def get_user(self, principal: Principal, user_id: uuid.UUID) -> User:
        if principal.is_platform_admin:
            user = self._users.get_by_id(user_id)
        else:
            user = self._users.get_in_organization(user_id, principal.organization_id)
        # Users in other organizations are reported as not found, not forbidden,
        # so their existence is not disclosed.
        if user is None:
            raise NotFoundError("User not found.")
        return user

    def list_roles(self) -> list[Role]:
        return self._roles.list_roles()

    def create_user(
        self,
        principal: Principal,
        data: UserCreateRequest,
        client: ClientInfo,
        *,
        organization_id: uuid.UUID | None = None,
    ) -> User:
        scope = resolve_organization_scope(principal, organization_id)
        organization = self._organizations.get(scope)
        if organization is None:
            raise NotFoundError("Organization not found.")
        roles = self._assignable_roles(principal, data.roles)
        problems = self._policy.violations(data.password, email=data.email)
        if problems:
            raise InvalidInputError("Password does not meet the password policy.", details=problems)
        if self._users.email_exists(data.email):
            raise ConflictError("An account with this email already exists.")

        user = self._users.add(
            User(
                organization_id=organization.id,
                organization=organization,
                email=data.email,
                full_name=data.full_name,
                password_hash=self._hasher.hash(data.password),
                roles=roles,
            )
        )
        self._audit.record(
            AuditAction.USER_CREATE,
            AuditOutcome.SUCCESS,
            client=client,
            organization_id=organization.id,
            actor_user_id=principal.user_id,
            target_type="user",
            target_id=user.id,
            details={"roles": [role.code for role in roles]},
        )
        return user

    def set_roles(self, principal: Principal, user_id: uuid.UUID, role_codes: list[str], client: ClientInfo) -> User:
        user = self._manageable_user(principal, user_id)
        roles = self._assignable_roles(principal, role_codes)
        previous = user.role_codes
        user.roles = roles
        self._audit.record(
            AuditAction.USER_ROLES_UPDATE,
            AuditOutcome.SUCCESS,
            client=client,
            organization_id=user.organization_id,
            actor_user_id=principal.user_id,
            target_type="user",
            target_id=user.id,
            details={"previous_roles": previous, "roles": [role.code for role in roles]},
        )
        return user

    def set_active(self, principal: Principal, user_id: uuid.UUID, active: bool, client: ClientInfo) -> User:
        user = self._manageable_user(principal, user_id)
        if user.is_active == active:
            return user
        now = self._clock()
        user.is_active = active
        user.deactivated_at = None if active else now
        revoked = 0
        if not active:
            # Sign the account out everywhere immediately.
            revoked = self._auth_sessions.revoke_all_for_user(user.id, reason="account_deactivated", at=now)
        self._audit.record(
            AuditAction.USER_ACTIVATE if active else AuditAction.USER_DEACTIVATE,
            AuditOutcome.SUCCESS,
            client=client,
            organization_id=user.organization_id,
            actor_user_id=principal.user_id,
            target_type="user",
            target_id=user.id,
            details={} if active else {"sessions_revoked": revoked},
        )
        return user

    def _manageable_user(self, principal: Principal, user_id: uuid.UUID) -> User:
        user = self.get_user(principal, user_id)
        if user.id == principal.user_id:
            raise PermissionDeniedError("You cannot change your own roles or activation state.")
        if not user.permission_codes <= principal.permissions:
            raise PermissionDeniedError("You cannot manage a user who has permissions you do not hold.")
        return user

    def _assignable_roles(self, principal: Principal, role_codes: list[str]) -> list[Role]:
        wanted = set(role_codes)
        roles = self._roles.get_by_codes(wanted)
        unknown = wanted - {role.code for role in roles}
        if unknown:
            raise InvalidInputError("Unknown roles.", details=sorted(unknown))
        for role in roles:
            if not role.permission_codes <= principal.permissions:
                raise PermissionDeniedError(f"You cannot assign the role '{role.code}'.")
        return roles

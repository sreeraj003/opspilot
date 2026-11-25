"""Administrator account management.

Authorization happens before these methods are called (``require_permission``
in the routes); this service enforces the account rules that hold whatever
the caller's permissions:

* Privileges are never assigned here: ``is_superuser`` is set only by the
  bootstrap command, and the write schemas reject it.
* The installation administrator cannot be deactivated or deleted through the
  API, and an administrator cannot deactivate or delete their own account, so
  an installation can never lose its last way in.
* Deactivation and deletion revoke every session immediately.
"""

import asyncio
import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, InvalidInputError, NotFoundError
from app.database.transaction import transaction
from app.models.auth_event import AuthEventType
from app.models.user import User
from app.models.user_session import SessionRevocationReason, UserSession
from app.repositories.base_repository import Page
from app.repositories.password_reset_repository import PasswordResetTokenRepository
from app.repositories.rbac_repository import AuthorizationStateRepository
from app.repositories.session_repository import UserSessionRepository
from app.repositories.user_repository import UserQuery, UserRepository
from app.schemas.user import UserCreate, UserUpdate
from app.services.audit_service import AuditService
from app.services.auth_service import AuthService, ClientInfo, utcnow
from app.services.password_service import PasswordPolicyError, PasswordService

_UNIQUE_VIOLATION = "23505"


class UserNotFoundError(NotFoundError):
    message = "The user was not found."


class UserService:
    def __init__(self, session: AsyncSession, *, passwords: PasswordService, auth: AuthService) -> None:
        self._session = session
        self._passwords = passwords
        self._auth = auth
        self._users = UserRepository(session)
        self._sessions = UserSessionRepository(session)
        self._resets = PasswordResetTokenRepository(session)
        self._audit = AuditService(session)
        self._authorization_state = AuthorizationStateRepository(session)

    # --- reads --------------------------------------------------------------------------

    async def list_users(self, query: UserQuery, *, offset: int, limit: int) -> Page[User]:
        return await self._users.search(query, offset=offset, limit=limit, now=utcnow())

    async def get_user(self, user_id: uuid.UUID) -> User:
        user = await self._users.get(user_id)
        if user is None:
            raise UserNotFoundError()
        return user

    async def active_session_count(self, user_id: uuid.UUID) -> int:
        return await self._sessions.count_active_for_user(user_id, utcnow())

    async def list_sessions(self, user_id: uuid.UUID) -> Sequence[UserSession]:
        await self.get_user(user_id)
        return await self._sessions.list_active_for_user(user_id, utcnow())

    # --- writes -------------------------------------------------------------------------

    async def create_user(self, data: UserCreate, actor: User, client: ClientInfo) -> User:
        try:
            password_hash = await asyncio.to_thread(
                self._passwords.hash, data.password, identifiers=(data.email, data.username, data.full_name)
            )
        except PasswordPolicyError as exc:
            raise InvalidInputError(str(exc)) from None
        try:
            async with transaction(self._session):
                await self._ensure_unique(email=data.email, username=data.username)
                user = await self._users.create(
                    email=data.email,
                    username=data.username,
                    full_name=data.full_name,
                    password_hash=password_hash,
                    is_active=data.is_active,
                    is_superuser=False,
                    is_verified=False,
                )
                await self._record(AuthEventType.ACCOUNT_CREATED, user, actor, client, {"is_active": data.is_active})
        except IntegrityError as exc:
            raise _translate_integrity_error(exc) from None
        return user

    async def update_user(self, user_id: uuid.UUID, data: UserUpdate, actor: User, client: ClientInfo) -> User:
        changes = data.model_dump(exclude_unset=True)
        if "email" in changes and changes["email"] is None:
            raise InvalidInputError("Email address cannot be removed.")
        if "full_name" in changes and changes["full_name"] is None:
            raise InvalidInputError("Full name cannot be removed.")
        try:
            async with transaction(self._session):
                user = await self._get_for_update(user_id)
                changes = {key: value for key, value in changes.items() if getattr(user, key) != value}
                if not changes:
                    return user
                await self._ensure_unique(
                    email=changes.get("email"), username=changes.get("username"), exclude_id=user.id
                )
                await self._users.update(user, changes)
                if "email" in changes:
                    user.is_verified = False
                await self._record(
                    AuthEventType.ACCOUNT_UPDATED, user, actor, client, {"fields": ",".join(sorted(changes))}
                )
        except IntegrityError as exc:
            raise _translate_integrity_error(exc) from None
        return user

    async def activate(self, user_id: uuid.UUID, actor: User, client: ClientInfo) -> User:
        async with transaction(self._session):
            user = await self._get_for_update(user_id)
            was_locked = user.locked_until is not None
            user.is_active = True
            # Activation also clears a temporary lockout.
            user.failed_login_attempts = 0
            user.locked_until = None
            # Permission caches must not outlive a status change (see app.authorization.cache).
            await self._authorization_state.bump()
            await self._record(AuthEventType.ACCOUNT_ACTIVATED, user, actor, client, {"cleared_lockout": was_locked})
        return user

    async def deactivate(self, user_id: uuid.UUID, actor: User, client: ClientInfo) -> User:
        async with transaction(self._session):
            user = await self._get_for_update(user_id)
            self._ensure_not_protected(user, actor, "deactivate")
            user.is_active = False
            revoked = await self._sessions.revoke_all_for_user(
                user.id, reason=SessionRevocationReason.USER_DEACTIVATED, now=utcnow()
            )
            await self._resets.invalidate_unused_for_user(user.id, utcnow())
            # Permission caches must not outlive a status change (see app.authorization.cache).
            await self._authorization_state.bump()
            await self._record(AuthEventType.ACCOUNT_DEACTIVATED, user, actor, client, {"revoked_sessions": revoked})
        return user

    async def delete(self, user_id: uuid.UUID, actor: User, client: ClientInfo) -> None:
        async with transaction(self._session):
            user = await self._get_for_update(user_id)
            self._ensure_not_protected(user, actor, "delete")
            now = utcnow()
            user.is_active = False
            revoked = await self._sessions.revoke_all_for_user(
                user.id, reason=SessionRevocationReason.USER_DELETED, now=now
            )
            await self._resets.invalidate_unused_for_user(user.id, now)
            await self._users.delete(user)  # soft delete: history and foreign keys stay intact
            # Permission caches must not outlive a status change (see app.authorization.cache).
            await self._authorization_state.bump()
            await self._record(AuthEventType.ACCOUNT_DELETED, user, actor, client, {"revoked_sessions": revoked})

    async def revoke_sessions(self, user_id: uuid.UUID, actor: User, client: ClientInfo) -> int:
        async with transaction(self._session):
            user = await self._get_for_update(user_id)
            revoked = await self._sessions.revoke_all_for_user(
                user.id, reason=SessionRevocationReason.ADMIN_REVOKED, now=utcnow()
            )
            await self._record(AuthEventType.SESSIONS_REVOKED, user, actor, client, {"revoked_sessions": revoked})
        return revoked

    async def start_password_reset(self, user_id: uuid.UUID, actor: User, client: ClientInfo) -> tuple[str, datetime]:
        user = await self.get_user(user_id)
        if not user.is_active:
            raise InvalidInputError("Activate the account before resetting its password.")
        return await self._auth.issue_reset_link_for(user, actor, client)

    # --- internals --------------------------------------------------------------------------

    async def _get_for_update(self, user_id: uuid.UUID) -> User:
        user = await self._users.get_for_update(user_id)
        if user is None:
            raise UserNotFoundError()
        return user

    @staticmethod
    def _ensure_not_protected(user: User, actor: User, action: str) -> None:
        if user.id == actor.id:
            raise ConflictError(f"You cannot {action} your own account.")
        if user.is_superuser:
            raise ConflictError(f"The installation administrator account cannot be {action}d.")

    async def _ensure_unique(
        self, *, email: str | None, username: str | None, exclude_id: uuid.UUID | None = None
    ) -> None:
        if email is not None and await self._users.email_taken(email, exclude_id=exclude_id):
            raise ConflictError("A user with this email address already exists.")
        if username is not None and await self._users.username_taken(username, exclude_id=exclude_id):
            raise ConflictError("A user with this username already exists.")

    async def _record(
        self, event_type: str, user: User, actor: User, client: ClientInfo, details: dict[str, str | int | bool]
    ) -> None:
        await self._audit.record(
            event_type, user_id=user.id, actor_user_id=actor.id, ip_address=client.ip_address, details=details
        )


def _translate_integrity_error(exc: IntegrityError) -> Exception:
    if getattr(exc.orig, "sqlstate", None) == _UNIQUE_VIOLATION:
        # Created concurrently between the uniqueness check and the insert.
        return ConflictError("A user with this email address or username already exists.")
    return exc

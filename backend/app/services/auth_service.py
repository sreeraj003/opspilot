"""Authentication: login, sessions, refresh-token rotation, passwords.

Security properties (see ARCHITECTURE.md, "Authentication"):

* Every failure that could reveal whether an account exists returns the same
  ``AuthenticationError`` with the same message, after the same password-
  hashing work.
* Consecutive failed logins lock the account temporarily, with a lock that
  doubles per further failure up to a cap. Locks expire on their own, so an
  attacker cannot lock an account permanently.
* Refresh tokens are random, stored as digests, and rotated on every use. A
  rotated token presented again (outside a short grace period for concurrent
  tabs) revokes its whole session.
* Each authenticated request re-reads the user and session, so logout,
  revocation, deactivation and deletion take effect on the next request.

Methods that change data own their transaction (``app.database.transaction``).
Argon2 work runs in a worker thread so it never blocks the event loop.
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authorization import AuthorizationPolicy, Permission
from app.core.config import Settings
from app.core.emails import try_normalize_email
from app.core.exceptions import AppError, AuthenticationError, InvalidInputError
from app.core.security import AccessTokenService, IssuedAccessToken, InvalidAccessTokenError, generate_opaque_token, hash_token
from app.database.transaction import transaction
from app.models.auth_event import AuthEventType
from app.models.user import User
from app.models.user_session import (
    IP_ADDRESS_MAX_LENGTH,
    USER_AGENT_MAX_LENGTH,
    SessionRevocationReason,
    UserSession,
)
from app.repositories.password_reset_repository import PasswordResetTokenRepository
from app.repositories.session_repository import UserSessionRepository
from app.repositories.user_repository import UserRepository
from app.services.audit_service import AuditService
from app.services.password_service import PasswordPolicyError, PasswordService

logger = logging.getLogger(__name__)

INVALID_CREDENTIALS_MESSAGE = "Invalid email or password."
INVALID_SESSION_MESSAGE = "Your session has expired. Sign in again."
INVALID_RESET_TOKEN_MESSAGE = "This password reset link is invalid or has expired. Request a new one."


class RefreshConflictError(AppError):
    """The refresh token was rotated moments ago by a concurrent request (another tab)."""

    status_code = 409
    code = "REFRESH_CONFLICT"
    message = "The session was refreshed by another request. Retry."


class InvalidResetTokenError(AppError):
    status_code = 400
    code = "INVALID_RESET_TOKEN"
    message = INVALID_RESET_TOKEN_MESSAGE


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class ClientInfo:
    """Request metadata recorded on sessions and audit events (when enabled)."""

    ip_address: str | None = None
    user_agent: str | None = None

    def trimmed(self) -> "ClientInfo":
        return ClientInfo(
            ip_address=self.ip_address[:IP_ADDRESS_MAX_LENGTH] if self.ip_address else None,
            user_agent=self.user_agent[:USER_AGENT_MAX_LENGTH] if self.user_agent else None,
        )


@dataclass(frozen=True, slots=True)
class Principal:
    """The authenticated caller of one request."""

    user: User
    session_id: uuid.UUID
    permissions: frozenset[Permission] = field(default_factory=frozenset)


@dataclass(frozen=True, slots=True)
class AuthResult:
    """A new or rotated session: the caller sets ``refresh_token`` as a cookie and never logs it."""

    user: User
    session: UserSession
    refresh_token: str
    refresh_expires_at: datetime
    access: IssuedAccessToken


class PasswordResetNotifier(Protocol):
    """Delivers a reset link to the account owner. Returns whether it was delivered."""

    async def send(self, user: User, reset_url: str, expires_at: datetime) -> bool: ...


class NoDeliveryNotifier:
    """Default: no delivery channel (no email integration exists yet).

    The request is still recorded as an audit event so an administrator can see
    it and issue a reset link from the user-management page.
    """

    async def send(self, user: User, reset_url: str, expires_at: datetime) -> bool:
        logger.info("Password reset requested but no delivery channel is configured", extra={"user_id": str(user.id)})
        return False


class AuthService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: Settings,
        tokens: AccessTokenService,
        passwords: PasswordService,
        policy: AuthorizationPolicy,
        notifier: PasswordResetNotifier | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._tokens = tokens
        self._passwords = passwords
        self._policy = policy
        self._notifier = notifier or NoDeliveryNotifier()
        self._users = UserRepository(session)
        self._sessions = UserSessionRepository(session)
        self._resets = PasswordResetTokenRepository(session)
        self._audit = AuditService(session)

    # --- access tokens ---------------------------------------------------------------

    async def authenticate(self, access_token: str) -> Principal:
        try:
            claims = self._tokens.verify(access_token)
        except InvalidAccessTokenError:
            raise AuthenticationError() from None
        found = await self._sessions.get_active_with_user(claims.session_id, claims.user_id, utcnow())
        if found is None:
            # Session revoked or expired, or the user was deactivated or deleted.
            raise AuthenticationError()
        user_session, user = found
        return Principal(user=user, session_id=user_session.id, permissions=await self._policy.permissions_for(user))

    async def permissions_for(self, user: User) -> frozenset[Permission]:
        return await self._policy.permissions_for(user)

    # --- login --------------------------------------------------------------------------

    async def login(self, email: str, password: str, client: ClientInfo) -> AuthResult:
        client = self._client(client)
        normalized = try_normalize_email(email)
        now = utcnow()
        failure_reason: str | None = None
        result: AuthResult | None = None

        async with transaction(self._session):
            user = await self._users.get_by_email_for_update(normalized) if normalized else None
            if user is None:
                await asyncio.to_thread(self._passwords.burn_time)
                failure_reason = "unknown_account"
            elif user.locked_until is not None and user.locked_until > now:
                await asyncio.to_thread(self._passwords.burn_time)
                failure_reason = "account_locked"
            elif not await asyncio.to_thread(self._passwords.verify, user.password_hash, password):
                failure_reason = "invalid_password"
                self._register_failure(user, now)
            elif not user.is_active:
                # Checked after the password so status is never revealed to someone without it.
                failure_reason = "account_inactive"
            else:
                if self._passwords.needs_rehash(user.password_hash):
                    user.password_hash = await asyncio.to_thread(self._passwords.hash_unchecked, password)
                user.failed_login_attempts = 0
                user.locked_until = None
                user.last_login_at = now
                await self._sessions.prune_for_user(user.id, now=now, revoked_retention=self._refresh_lifetime)
                result = await self._start_session(user, client, now)
                await self._audit.record(
                    AuthEventType.LOGIN_SUCCEEDED,
                    user_id=user.id,
                    actor_user_id=user.id,
                    ip_address=client.ip_address,
                    details={"session_id": str(result.session.id)},
                )

            if failure_reason is not None:
                await self._audit.record(
                    AuthEventType.LOGIN_FAILED,
                    user_id=user.id if user is not None else None,
                    ip_address=client.ip_address,
                    details={
                        "reason": failure_reason,
                        "failed_attempts": user.failed_login_attempts if user is not None else None,
                        "locked": bool(user and user.locked_until and user.locked_until > now),
                    },
                )

        if result is None:
            raise AuthenticationError(INVALID_CREDENTIALS_MESSAGE, code="INVALID_CREDENTIALS")
        return result

    def _register_failure(self, user: User, now: datetime) -> None:
        user.failed_login_attempts += 1
        excess = user.failed_login_attempts - self._settings.login_max_failed_attempts
        if excess >= 0:
            seconds = min(
                self._settings.login_lockout_base_seconds * (2 ** min(excess, 20)),
                self._settings.login_lockout_max_seconds,
            )
            user.locked_until = now + timedelta(seconds=seconds)

    # --- refresh and logout ------------------------------------------------------------------

    async def refresh(self, refresh_token: str, client: ClientInfo) -> AuthResult:
        client = self._client(client)
        token_hash = hash_token(refresh_token)
        now = utcnow()
        reuse_detected = False
        result: AuthResult | None = None

        async with transaction(self._session):
            user_session = await self._sessions.get_by_token_hash_for_update(token_hash)
            if user_session is None:
                retired = await self._sessions.find_retired(token_hash)
                if retired is None:
                    raise AuthenticationError(INVALID_SESSION_MESSAGE)
                family = await self._sessions.get_for_update(retired.session_id)
                grace = timedelta(seconds=self._settings.refresh_reuse_grace_seconds)
                if family is not None and family.revoked_at is None and now - retired.retired_at <= grace:
                    raise RefreshConflictError()
                reuse_detected = True
                if family is not None and family.revoked_at is None:
                    family.revoked_at = now
                    family.revoked_reason = SessionRevocationReason.TOKEN_REUSE
                await self._audit.record(
                    AuthEventType.REFRESH_TOKEN_REUSE,
                    user_id=family.user_id if family is not None else None,
                    ip_address=client.ip_address,
                    details={"session_id": str(retired.session_id)},
                )
            elif user_session.revoked_at is not None or user_session.expires_at <= now:
                raise AuthenticationError(INVALID_SESSION_MESSAGE)
            else:
                user = await self._users.get(user_session.user_id)
                if user is None or not user.is_active:
                    raise AuthenticationError(INVALID_SESSION_MESSAGE)
                result = await self._rotate(user_session, user, client, now)

        if reuse_detected or result is None:
            logger.warning("Refresh token reuse detected; session revoked")
            raise AuthenticationError(INVALID_SESSION_MESSAGE)
        return result

    async def logout(self, refresh_token: str | None, client: ClientInfo) -> None:
        """Revoke the session that owns ``refresh_token``. Unknown tokens are ignored (logout is idempotent)."""
        if not refresh_token:
            return
        client = self._client(client)
        now = utcnow()
        async with transaction(self._session):
            user_session = await self._sessions.get_by_token_hash_for_update(hash_token(refresh_token))
            if user_session is None or user_session.revoked_at is not None:
                return
            user_session.revoked_at = now
            user_session.revoked_reason = SessionRevocationReason.LOGOUT
            await self._audit.record(
                AuthEventType.LOGOUT,
                user_id=user_session.user_id,
                actor_user_id=user_session.user_id,
                ip_address=client.ip_address,
                details={"session_id": str(user_session.id)},
            )

    async def logout_all(self, principal: Principal, client: ClientInfo) -> int:
        client = self._client(client)
        async with transaction(self._session):
            revoked = await self._sessions.revoke_all_for_user(
                principal.user.id, reason=SessionRevocationReason.LOGOUT_ALL, now=utcnow()
            )
            await self._audit.record(
                AuthEventType.LOGOUT_ALL,
                user_id=principal.user.id,
                actor_user_id=principal.user.id,
                ip_address=client.ip_address,
                details={"revoked_sessions": revoked},
            )
        return revoked

    # --- passwords -----------------------------------------------------------------------------

    async def change_password(
        self, principal: Principal, current_password: str, new_password: str, client: ClientInfo
    ) -> AuthResult:
        """Change the caller's password, end every session, and start a fresh one for this browser."""
        client = self._client(client)
        now = utcnow()
        async with transaction(self._session):
            user = await self._users.get_for_update(principal.user.id)
            if user is None or not user.is_active:
                raise AuthenticationError()
            if not await asyncio.to_thread(self._passwords.verify, user.password_hash, current_password):
                raise InvalidInputError("The current password is incorrect.")
            if await asyncio.to_thread(self._passwords.verify, user.password_hash, new_password):
                raise InvalidInputError("The new password must be different from the current password.")
            user.password_hash = await self._hash_new_password(new_password, user)
            user.password_changed_at = now
            user.failed_login_attempts = 0
            user.locked_until = None
            await self._sessions.revoke_all_for_user(user.id, reason=SessionRevocationReason.PASSWORD_CHANGED, now=now)
            await self._resets.invalidate_unused_for_user(user.id, now)
            result = await self._start_session(user, client, now)
            await self._audit.record(
                AuthEventType.PASSWORD_CHANGED, user_id=user.id, actor_user_id=user.id, ip_address=client.ip_address
            )
        return result

    async def request_password_reset(self, email: str, client: ClientInfo) -> None:
        """Self-service reset request. Behaves identically whether or not the account exists."""
        client = self._client(client)
        normalized = try_normalize_email(email)
        if normalized is None:
            return
        delivered = False
        async with transaction(self._session):
            user = await self._users.get_by_email(normalized)
            if user is None or not user.is_active:
                return
            raw, expires_at = await self._issue_reset_token(user)
            delivered = await self._notifier.send(user, self.reset_url(raw), expires_at)
            await self._audit.record(
                AuthEventType.PASSWORD_RESET_REQUESTED,
                user_id=user.id,
                ip_address=client.ip_address,
                details={"initiated_by": "user", "delivered": delivered},
            )

    async def issue_reset_link_for(self, user: User, actor: User, client: ClientInfo) -> tuple[str, datetime]:
        """Administrator-initiated reset. Returns the one-time link (shown once, never stored)."""
        client = self._client(client)
        async with transaction(self._session):
            raw, expires_at = await self._issue_reset_token(user)
            await self._audit.record(
                AuthEventType.PASSWORD_RESET_REQUESTED,
                user_id=user.id,
                actor_user_id=actor.id,
                ip_address=client.ip_address,
                details={"initiated_by": "administrator"},
            )
        return self.reset_url(raw), expires_at

    async def reset_password(self, token: str, new_password: str, client: ClientInfo) -> None:
        client = self._client(client)
        now = utcnow()
        async with transaction(self._session):
            reset = await self._resets.get_by_hash_for_update(hash_token(token))
            if (
                reset is None
                or reset.used_at is not None
                or reset.invalidated_at is not None
                or reset.expires_at <= now
            ):
                raise InvalidResetTokenError()
            user = await self._users.get_for_update(reset.user_id)
            if user is None or not user.is_active:
                raise InvalidResetTokenError()
            user.password_hash = await self._hash_new_password(new_password, user)
            reset.used_at = now
            await self._resets.invalidate_unused_for_user(user.id, now)
            user.password_changed_at = now
            user.failed_login_attempts = 0
            user.locked_until = None
            revoked = await self._sessions.revoke_all_for_user(
                user.id, reason=SessionRevocationReason.PASSWORD_RESET, now=now
            )
            await self._audit.record(
                AuthEventType.PASSWORD_RESET_COMPLETED,
                user_id=user.id,
                actor_user_id=user.id,
                ip_address=client.ip_address,
                details={"revoked_sessions": revoked},
            )

    def reset_url(self, raw_token: str) -> str:
        # The token travels in the URL fragment, which browsers never send to
        # servers (no access logs, no Referer leaks).
        return f"{self._settings.public_frontend_url}/reset-password#token={raw_token}"

    # --- internals --------------------------------------------------------------------------------

    @property
    def _refresh_lifetime(self) -> timedelta:
        return timedelta(days=self._settings.refresh_token_expire_days)

    def _client(self, client: ClientInfo) -> ClientInfo:
        if not self._settings.auth_record_client_metadata:
            return ClientInfo()
        return client.trimmed()

    async def _hash_new_password(self, password: str, user: User) -> str:
        try:
            return await asyncio.to_thread(
                self._passwords.hash, password, identifiers=(user.email, user.username, user.full_name)
            )
        except PasswordPolicyError as exc:
            raise InvalidInputError(str(exc)) from None

    async def _issue_reset_token(self, user: User) -> tuple[str, datetime]:
        now = utcnow()
        await self._resets.prune_for_user(user.id, now)
        await self._resets.invalidate_unused_for_user(user.id, now)
        raw = generate_opaque_token()
        expires_at = now + timedelta(minutes=self._settings.password_reset_token_expire_minutes)
        await self._resets.create(user_id=user.id, token_hash=hash_token(raw), expires_at=expires_at)
        return raw, expires_at

    async def _start_session(self, user: User, client: ClientInfo, now: datetime) -> AuthResult:
        raw = generate_opaque_token()
        absolute = now + timedelta(days=self._settings.session_absolute_max_days)
        expires_at = min(now + self._refresh_lifetime, absolute)
        user_session = await self._sessions.create(
            user_id=user.id,
            refresh_token_hash=hash_token(raw),
            expires_at=expires_at,
            absolute_expires_at=absolute,
            last_used_at=now,
            rotated_at=now,
            user_agent=client.user_agent,
            ip_address=client.ip_address,
        )
        return AuthResult(
            user=user,
            session=user_session,
            refresh_token=raw,
            refresh_expires_at=expires_at,
            access=self._tokens.issue(user.id, user_session.id, now=now),
        )

    async def _rotate(self, user_session: UserSession, user: User, client: ClientInfo, now: datetime) -> AuthResult:
        await self._sessions.retire_token(user_session.id, user_session.refresh_token_hash, now)
        raw = generate_opaque_token()
        user_session.refresh_token_hash = hash_token(raw)
        user_session.rotated_at = now
        user_session.last_used_at = now
        user_session.expires_at = min(now + self._refresh_lifetime, user_session.absolute_expires_at)
        if client.user_agent:
            user_session.user_agent = client.user_agent
        if client.ip_address:
            user_session.ip_address = client.ip_address
        await self._session.flush()
        return AuthResult(
            user=user,
            session=user_session,
            refresh_token=raw,
            refresh_expires_at=user_session.expires_at,
            access=self._tokens.issue(user.id, user_session.id, now=now),
        )

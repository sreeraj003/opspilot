"""Authentication: registration, login, refresh-token rotation, logout and token validation."""

import logging
import re
import secrets
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.exceptions import (
    AuthenticationError,
    ConflictError,
    InvalidInputError,
    PermissionDeniedError,
    RateLimitedError,
)
from app.models.auth_session import AuthSession, RefreshToken
from app.models.organization import Organization
from app.models.user import User
from app.repositories.auth_session_repository import AuthSessionRepository
from app.repositories.organization_repository import OrganizationRepository
from app.repositories.rbac_repository import RoleRepository
from app.repositories.user_repository import UserRepository
from app.schemas.auth import LoginRequest, RegisterRequest
from app.security.passwords import PasswordHasher, PasswordPolicy
from app.security.principal import Principal
from app.security.rate_limit import RateLimiter
from app.security.rbac import ORGANIZATION_OWNER_ROLE
from app.security.tokens import TokenService, generate_refresh_token, hash_refresh_token
from app.services.audit_service import AuditAction, AuditOutcome, AuditService, ClientInfo
from app.utils.time import utc_now

logger = logging.getLogger(__name__)

INVALID_CREDENTIALS = "Invalid email or password."
INVALID_REFRESH_TOKEN = "Invalid or expired refresh token."
INVALID_ACCESS_TOKEN = "Invalid or expired access token."


@dataclass(frozen=True)
class IssuedTokens:
    access_token: str
    expires_in: int
    refresh_token: str
    refresh_expires_at: datetime


@dataclass(frozen=True)
class AuthResult:
    user: User
    tokens: IssuedTokens


class AuthService:
    def __init__(
        self,
        *,
        session: Session,
        settings: Settings,
        users: UserRepository,
        organizations: OrganizationRepository,
        roles: RoleRepository,
        auth_sessions: AuthSessionRepository,
        audit: AuditService,
        hasher: PasswordHasher,
        password_policy: PasswordPolicy,
        tokens: TokenService,
        rate_limiter: RateLimiter,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._session = session
        self._settings = settings
        self._users = users
        self._organizations = organizations
        self._roles = roles
        self._auth_sessions = auth_sessions
        self._audit = audit
        self._hasher = hasher
        self._policy = password_policy
        self._tokens = tokens
        self._limiter = rate_limiter
        self._clock = clock

    # Registration -------------------------------------------------------------------
    def register(self, data: RegisterRequest, client: ClientInfo) -> AuthResult:
        """Self-service sign-up: creates a new organization and its first administrator."""
        if not self._settings.allow_public_registration:
            raise PermissionDeniedError("Public registration is disabled.")
        self._enforce_ip_limit(f"register:ip:{client.ip_address}", client)

        problems = self._policy.violations(data.password, email=data.email)
        if problems:
            raise InvalidInputError("Password does not meet the password policy.", details=problems)
        if self._users.email_exists(data.email):
            raise ConflictError("An account with this email already exists.")

        owner_roles = self._roles.get_by_codes([ORGANIZATION_OWNER_ROLE])
        if not owner_roles:
            raise RuntimeError("RBAC catalog is not seeded; run database migrations")

        organization = self._organizations.add(
            Organization(name=data.organization_name, slug=self._unique_slug(data.organization_name))
        )
        user = self._users.add(
            User(
                organization_id=organization.id,
                organization=organization,
                email=data.email,
                full_name=data.full_name,
                password_hash=self._hasher.hash(data.password),
                roles=owner_roles,
            )
        )
        self._audit.record(
            AuditAction.REGISTER,
            AuditOutcome.SUCCESS,
            client=client,
            organization_id=organization.id,
            actor_user_id=user.id,
            target_type="organization",
            target_id=organization.id,
        )
        tokens = self._start_session(user, client)
        return AuthResult(user=user, tokens=tokens)

    # Login --------------------------------------------------------------------------
    def login(self, data: LoginRequest, client: ClientInfo) -> AuthResult:
        account_key = f"login:account:{data.email}"
        self._enforce_ip_limit(f"login:ip:{client.ip_address}", client)
        if self._settings.login_rate_limit_enabled:
            status = self._limiter.check(
                account_key,
                self._settings.login_max_failures_per_account,
                self._settings.login_rate_limit_window_seconds,
            )
            if not status.allowed:
                self._fail_login(None, client, "account_rate_limited")
                raise RateLimitedError(status.retry_after_seconds)

        user = self._users.get_by_email(data.email)
        if user is None:
            self._hasher.verify_dummy(data.password)
            self._record_login_failure(account_key, None, client, "unknown_email")
        elif not self._hasher.verify(user.password_hash, data.password):
            self._record_login_failure(account_key, user, client, "invalid_password")
        elif not user.is_active:
            self._record_login_failure(account_key, user, client, "account_disabled")
        elif not user.organization.is_active:
            self._record_login_failure(account_key, user, client, "organization_disabled")
        assert user is not None  # every failure path above raises

        if self._settings.login_rate_limit_enabled:
            self._limiter.reset(account_key)
        if self._hasher.needs_rehash(user.password_hash):
            user.password_hash = self._hasher.hash(data.password)
        user.last_login_at = self._clock()
        self._audit.record(
            AuditAction.LOGIN,
            AuditOutcome.SUCCESS,
            client=client,
            organization_id=user.organization_id,
            actor_user_id=user.id,
        )
        tokens = self._start_session(user, client)
        return AuthResult(user=user, tokens=tokens)

    def _record_login_failure(self, account_key: str, user: User | None, client: ClientInfo, reason: str) -> None:
        if self._settings.login_rate_limit_enabled:
            self._limiter.hit(account_key, self._settings.login_rate_limit_window_seconds)
        self._fail_login(user, client, reason)
        raise AuthenticationError(INVALID_CREDENTIALS)

    def _fail_login(self, user: User | None, client: ClientInfo, reason: str) -> None:
        self._audit.record(
            AuditAction.LOGIN,
            AuditOutcome.FAILURE,
            client=client,
            organization_id=user.organization_id if user else None,
            actor_user_id=user.id if user else None,
            details={"reason": reason},
        )
        # Persist the audit event even though the request is about to fail.
        self._session.commit()

    def _enforce_ip_limit(self, key: str, client: ClientInfo) -> None:
        if not self._settings.login_rate_limit_enabled or client.ip_address is None:
            return
        window = self._settings.login_rate_limit_window_seconds
        status = self._limiter.check(key, self._settings.login_max_attempts_per_ip, window)
        if not status.allowed:
            logger.warning("Rate limit exceeded", extra={"rate_limit_key": key.rsplit(":", 1)[0]})
            raise RateLimitedError(status.retry_after_seconds)
        self._limiter.hit(key, window)

    # Refresh ------------------------------------------------------------------------
    def refresh(self, raw_token: str | None, client: ClientInfo) -> AuthResult:
        if not raw_token:
            raise AuthenticationError(INVALID_REFRESH_TOKEN)
        now = self._clock()
        token = self._auth_sessions.get_refresh_token_by_hash(hash_refresh_token(raw_token), for_update=True)
        if token is None:
            raise AuthenticationError(INVALID_REFRESH_TOKEN)

        auth_session = token.session
        if token.used_at is not None or token.revoked_at is not None:
            self._handle_reuse(token, auth_session, client, now)
        if token.expires_at <= now or not auth_session.is_active_at(now):
            self._refresh_failed(auth_session, client, "expired")

        user = self._users.get_by_id(auth_session.user_id)
        if user is None or not user.is_active or not user.organization.is_active:
            self._auth_sessions.revoke_session(auth_session, reason="account_disabled", at=now)
            self._refresh_failed(auth_session, client, "account_disabled")
        assert user is not None

        token.used_at = now
        auth_session.last_used_at = now
        new_raw = generate_refresh_token()
        self._auth_sessions.add_refresh_token(
            RefreshToken(
                session_id=auth_session.id,
                token_hash=hash_refresh_token(new_raw),
                expires_at=auth_session.expires_at,
            )
        )
        access = self._tokens.create_access_token(
            user_id=user.id, organization_id=user.organization_id, session_id=auth_session.id
        )
        self._audit.record(
            AuditAction.TOKEN_REFRESH,
            AuditOutcome.SUCCESS,
            client=client,
            organization_id=user.organization_id,
            actor_user_id=user.id,
            target_type="auth_session",
            target_id=auth_session.id,
        )
        return AuthResult(
            user=user,
            tokens=IssuedTokens(
                access_token=access.token,
                expires_in=access.expires_in,
                refresh_token=new_raw,
                refresh_expires_at=auth_session.expires_at,
            ),
        )

    def _handle_reuse(self, token: RefreshToken, auth_session: AuthSession, client: ClientInfo, now: datetime) -> None:
        if token.used_at is not None and auth_session.revoked_at is None:
            # A rotated token was presented again: either the legitimate client or an
            # attacker holds a stolen copy. Revoke the whole session to cut off both.
            self._auth_sessions.revoke_session(auth_session, reason="refresh_token_reuse", at=now)
            self._audit.record(
                AuditAction.REFRESH_TOKEN_REUSE,
                AuditOutcome.DENIED,
                client=client,
                organization_id=auth_session.organization_id,
                actor_user_id=auth_session.user_id,
                target_type="auth_session",
                target_id=auth_session.id,
            )
            logger.warning(
                "Refresh token reuse detected; session revoked",
                extra={"auth_session_id": str(auth_session.id)},
            )
            self._session.commit()
            raise AuthenticationError(INVALID_REFRESH_TOKEN)
        self._refresh_failed(auth_session, client, "revoked")

    def _refresh_failed(self, auth_session: AuthSession, client: ClientInfo, reason: str) -> None:
        self._audit.record(
            AuditAction.TOKEN_REFRESH,
            AuditOutcome.FAILURE,
            client=client,
            organization_id=auth_session.organization_id,
            actor_user_id=auth_session.user_id,
            target_type="auth_session",
            target_id=auth_session.id,
            details={"reason": reason},
        )
        self._session.commit()
        raise AuthenticationError(INVALID_REFRESH_TOKEN)

    # Logout -------------------------------------------------------------------------
    def logout(self, *, principal: Principal | None, raw_refresh_token: str | None, client: ClientInfo) -> None:
        """Revoke the session identified by the access token and/or the refresh token.

        Unknown or already revoked tokens are ignored so logout is idempotent and
        reveals nothing about token validity.
        """
        now = self._clock()
        sessions: dict[uuid.UUID, AuthSession] = {}
        if principal is not None:
            current = self._auth_sessions.get_session(principal.session_id)
            if current is not None:
                sessions[current.id] = current
        if raw_refresh_token:
            token = self._auth_sessions.get_refresh_token_by_hash(hash_refresh_token(raw_refresh_token))
            if token is not None and (principal is None or token.session.user_id == principal.user_id):
                sessions[token.session.id] = token.session
        for auth_session in sessions.values():
            if auth_session.revoked_at is not None:
                continue
            self._auth_sessions.revoke_session(auth_session, reason="logout", at=now)
            self._audit.record(
                AuditAction.LOGOUT,
                AuditOutcome.SUCCESS,
                client=client,
                organization_id=auth_session.organization_id,
                actor_user_id=auth_session.user_id,
                target_type="auth_session",
                target_id=auth_session.id,
            )

    def logout_all(self, principal: Principal, client: ClientInfo) -> int:
        revoked = self._auth_sessions.revoke_all_for_user(principal.user_id, reason="logout_all", at=self._clock())
        self._audit.record(
            AuditAction.LOGOUT_ALL,
            AuditOutcome.SUCCESS,
            client=client,
            organization_id=principal.organization_id,
            actor_user_id=principal.user_id,
            details={"sessions_revoked": revoked},
        )
        return revoked

    # Access-token validation ----------------------------------------------------------
    def authenticate(self, access_token: str) -> Principal:
        """Resolve a bearer token to a principal, re-checking account, organization and session state."""
        claims = self._tokens.decode_access_token(access_token)
        user = self._users.get_by_id(claims.user_id)
        if (
            user is None
            or user.organization_id != claims.organization_id
            or not user.is_active
            or not user.organization.is_active
        ):
            raise AuthenticationError(INVALID_ACCESS_TOKEN)
        auth_session = self._auth_sessions.get_session(claims.session_id)
        if auth_session is None or auth_session.user_id != user.id or not auth_session.is_active_at(self._clock()):
            raise AuthenticationError(INVALID_ACCESS_TOKEN)
        return principal_for(user, session_id=auth_session.id)

    # Helpers --------------------------------------------------------------------------
    def _start_session(self, user: User, client: ClientInfo) -> IssuedTokens:
        now = self._clock()
        expires_at = now + timedelta(days=self._settings.refresh_token_expire_days)
        auth_session = self._auth_sessions.add_session(
            AuthSession(
                user_id=user.id,
                organization_id=user.organization_id,
                created_at=now,
                last_used_at=now,
                expires_at=expires_at,
                ip_address=client.ip_address,
                user_agent=client.user_agent,
            )
        )
        raw = generate_refresh_token()
        self._auth_sessions.add_refresh_token(
            RefreshToken(
                session_id=auth_session.id,
                token_hash=hash_refresh_token(raw),
                created_at=now,
                expires_at=expires_at,
            )
        )
        access = self._tokens.create_access_token(
            user_id=user.id, organization_id=user.organization_id, session_id=auth_session.id
        )
        return IssuedTokens(
            access_token=access.token,
            expires_in=access.expires_in,
            refresh_token=raw,
            refresh_expires_at=expires_at,
        )

    def _unique_slug(self, name: str) -> str:
        base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:80] or "org"
        slug = base
        while self._organizations.slug_exists(slug):
            slug = f"{base}-{secrets.token_hex(3)}"
        return slug


def principal_for(user: User, *, session_id: uuid.UUID) -> Principal:
    return Principal(
        user_id=user.id,
        organization_id=user.organization_id,
        session_id=session_id,
        email=user.email,
        roles=tuple(user.role_codes),
        permissions=user.permission_codes,
    )

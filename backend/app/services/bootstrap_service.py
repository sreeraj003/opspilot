"""First-installation administrator bootstrap and host-level password recovery.

Used by the command line only (``python -m app.cli``): there is deliberately
no HTTP endpoint that can create an administrator. Whoever can run commands
on the server already controls the installation, so the CLI is the trust
boundary.
"""

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.emails import normalize_email
from app.database.transaction import transaction
from app.models.auth_event import AuthEventType
from app.models.user_session import SessionRevocationReason
from app.repositories.password_reset_repository import PasswordResetTokenRepository
from app.repositories.session_repository import UserSessionRepository
from app.repositories.user_repository import UserRepository
from app.services.audit_service import AuditService
from app.services.password_service import PasswordService

# Arbitrary constant: serialises concurrent bootstrap runs across processes.
_BOOTSTRAP_LOCK_ID = 7_301_203


class BootstrapError(Exception):
    """A refusal with a message that is safe to print."""


@dataclass(frozen=True, slots=True)
class AdminCredentials:
    email: str
    full_name: str
    password: str
    username: str | None = None


async def create_initial_admin(session: AsyncSession, passwords: PasswordService, data: AdminCredentials) -> uuid.UUID:
    email = normalize_email(data.email)
    full_name = " ".join(data.full_name.split())
    if not full_name:
        raise BootstrapError("Full name must not be empty.")
    password_hash = await asyncio.to_thread(
        passwords.hash, data.password, identifiers=(email, data.username, full_name)
    )
    users = UserRepository(session)
    async with transaction(session):
        await session.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": _BOOTSTRAP_LOCK_ID})
        if await users.superuser_exists():
            raise BootstrapError(
                "An installation administrator already exists. Bootstrap runs only once; "
                "use 'python -m app.cli reset-password' to recover access."
            )
        if await users.email_taken(email):
            raise BootstrapError("A user with this email address already exists.")
        user = await users.create(
            email=email,
            username=data.username,
            full_name=full_name,
            password_hash=password_hash,
            is_active=True,
            is_verified=True,
            is_superuser=True,
        )
        await AuditService(session).record(
            AuthEventType.ADMIN_BOOTSTRAPPED, user_id=user.id, details={"source": "cli"}
        )
    return user.id


async def reset_password_from_host(session: AsyncSession, passwords: PasswordService, email: str, password: str) -> None:
    """Set a user's password from the server's command line (lost administrator password)."""
    normalized = normalize_email(email)
    users = UserRepository(session)
    async with transaction(session):
        user = await users.get_by_email_for_update(normalized)
        if user is None:
            raise BootstrapError("No active account exists with this email address.")
        user.password_hash = await asyncio.to_thread(
            passwords.hash, password, identifiers=(user.email, user.username, user.full_name)
        )
        now = datetime.now(UTC)
        user.password_changed_at = now
        user.failed_login_attempts = 0
        user.locked_until = None
        revoked = await UserSessionRepository(session).revoke_all_for_user(
            user.id, reason=SessionRevocationReason.PASSWORD_RESET, now=now
        )
        await PasswordResetTokenRepository(session).invalidate_unused_for_user(user.id, now)
        await AuditService(session).record(
            AuthEventType.PASSWORD_RESET_COMPLETED,
            user_id=user.id,
            details={"initiated_by": "host_cli", "revoked_sessions": revoked},
        )

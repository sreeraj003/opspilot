"""FastAPI dependency providers.

Routes depend on these providers instead of constructing services directly,
so tests can replace any of them through ``app.dependency_overrides``.
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authorization import AuthorizationPolicy, BootstrapAuthorizationPolicy, Permission
from app.core.config import Settings, get_settings
from app.core.exceptions import AuthenticationError, PermissionDeniedError, ServiceUnavailableError
from app.core.rate_limit import RateLimiter
from app.core.security import AccessTokenService
from app.database.session import Database
from app.services.application_settings_service import ApplicationSettingsService
from app.services.auth_service import AuthService, ClientInfo, PasswordResetNotifier, Principal
from app.services.health_service import HealthService
from app.services.password_service import PasswordService
from app.services.user_service import UserService

SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_database(request: Request) -> Database | None:
    return getattr(request.app.state, "database", None)


DatabaseDep = Annotated[Database | None, Depends(get_database)]


async def get_db_session(database: DatabaseDep) -> AsyncIterator[AsyncSession]:
    """A new session for this request only: rolled back on error, always closed.

    Services commit their own work through ``app.database.transaction``; anything
    left uncommitted when the request ends is discarded.
    """
    if database is None:
        raise ServiceUnavailableError("The application database is not configured.")
    async with database.session() as session:
        yield session


DbSessionDep = Annotated[AsyncSession, Depends(get_db_session)]


def get_health_service(settings: SettingsDep, database: DatabaseDep) -> HealthService:
    return HealthService(settings=settings, database=database)


HealthServiceDep = Annotated[HealthService, Depends(get_health_service)]


def get_application_settings_service(session: DbSessionDep) -> ApplicationSettingsService:
    return ApplicationSettingsService(session)


ApplicationSettingsServiceDep = Annotated[ApplicationSettingsService, Depends(get_application_settings_service)]


# --- authentication and authorization (Module 03) ---------------------------------------


def get_access_token_service(request: Request) -> AccessTokenService:
    return request.app.state.access_token_service


def get_password_service(request: Request) -> PasswordService:
    return request.app.state.password_service


def get_rate_limiter(request: Request) -> RateLimiter:
    return request.app.state.rate_limiter


def get_password_reset_notifier(request: Request) -> PasswordResetNotifier:
    return request.app.state.password_reset_notifier


def get_authorization_policy() -> AuthorizationPolicy:
    """The active authorization policy. Module 04 overrides this with the dynamic permission engine."""
    return _BOOTSTRAP_POLICY


_BOOTSTRAP_POLICY = BootstrapAuthorizationPolicy()

RateLimiterDep = Annotated[RateLimiter, Depends(get_rate_limiter)]


def get_client_info(request: Request, settings: SettingsDep) -> ClientInfo:
    if not settings.auth_record_client_metadata:
        return ClientInfo()
    # The direct peer address. X-Forwarded-For is not trusted: behind a reverse
    # proxy, configure uvicorn's --forwarded-allow-ips so request.client is the real client.
    return ClientInfo(
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    ).trimmed()


ClientInfoDep = Annotated[ClientInfo, Depends(get_client_info)]


def get_auth_service(
    session: DbSessionDep,
    settings: SettingsDep,
    tokens: Annotated[AccessTokenService, Depends(get_access_token_service)],
    passwords: Annotated[PasswordService, Depends(get_password_service)],
    policy: Annotated[AuthorizationPolicy, Depends(get_authorization_policy)],
    notifier: Annotated[PasswordResetNotifier, Depends(get_password_reset_notifier)],
) -> AuthService:
    return AuthService(
        session, settings=settings, tokens=tokens, passwords=passwords, policy=policy, notifier=notifier
    )


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]

_bearer = HTTPBearer(auto_error=False, description="Access token from POST /api/v1/auth/login or /refresh.")


def get_bearer_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> str:
    """The raw bearer token, or 401 before any database work happens."""
    if credentials is None or credentials.scheme.lower() != "bearer" or not credentials.credentials:
        raise AuthenticationError()
    return credentials.credentials


async def get_current_principal(
    token: Annotated[str, Depends(get_bearer_token)], service: AuthServiceDep
) -> Principal:
    """Authenticate the request: valid signature and expiry, live session, active user.

    Every protected endpoint depends on this, directly or through ``require_permission``.
    """
    return await service.authenticate(token)


CurrentPrincipalDep = Annotated[Principal, Depends(get_current_principal)]


def require_permission(permission: Permission) -> Callable[..., Awaitable[Principal]]:
    """Dependency factory: the caller must be authenticated and hold ``permission``.

    The one place authorization is decided for routes. Module 04 changes the
    decision by replacing ``get_authorization_policy``, not the routes.
    """

    async def dependency(
        principal: CurrentPrincipalDep,
        policy: Annotated[AuthorizationPolicy, Depends(get_authorization_policy)],
    ) -> Principal:
        if not policy.is_allowed(principal.user, permission):
            raise PermissionDeniedError()
        return principal

    dependency.__name__ = f"require_{permission.value.replace(':', '_')}"
    return dependency


def get_user_service(
    session: DbSessionDep,
    passwords: Annotated[PasswordService, Depends(get_password_service)],
    auth: AuthServiceDep,
) -> UserService:
    return UserService(session, passwords=passwords, auth=auth)


UserServiceDep = Annotated[UserService, Depends(get_user_service)]

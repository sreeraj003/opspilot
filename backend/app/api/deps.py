"""FastAPI dependency providers (the dependency-injection foundation).

Routes depend on these providers rather than constructing services directly,
so tests can replace any of them via ``app.dependency_overrides``.
"""

from collections.abc import Callable, Iterator
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.exceptions import AuthenticationError, PermissionDeniedError, ServiceUnavailableError
from app.database.session import Database
from app.repositories.audit_repository import AuditRepository
from app.repositories.auth_session_repository import AuthSessionRepository
from app.repositories.organization_repository import OrganizationRepository
from app.repositories.rbac_repository import RoleRepository
from app.repositories.user_repository import UserRepository
from app.security.components import SecurityComponents
from app.security.principal import Principal
from app.security.rbac import Permission
from app.services.audit_query_service import AuditQueryService
from app.services.audit_service import AuditAction, AuditOutcome, AuditService, ClientInfo
from app.services.auth_service import AuthService
from app.services.health_service import HealthService
from app.services.user_service import UserService

SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_database(request: Request) -> Database | None:
    return getattr(request.app.state, "database", None)


DatabaseDep = Annotated[Database | None, Depends(get_database)]


def get_db_session(database: DatabaseDep) -> Iterator[Session]:
    """Request-scoped database session for routes that need persistence."""
    if database is None:
        raise ServiceUnavailableError("Database is not configured.")
    with database.session() as session:
        yield session


# scope="function": commit (or rollback) runs before the response is sent, so a
# client never sees success for a write that then fails to commit.
DbSessionDep = Annotated[Session, Depends(get_db_session, scope="function")]


def get_health_service(settings: SettingsDep, database: DatabaseDep) -> HealthService:
    return HealthService(settings=settings, database=database)


HealthServiceDep = Annotated[HealthService, Depends(get_health_service)]


# Security --------------------------------------------------------------------------


def get_security(request: Request) -> SecurityComponents:
    return request.app.state.security


SecurityDep = Annotated[SecurityComponents, Depends(get_security)]


def get_client_info(request: Request) -> ClientInfo:
    # The direct peer address. Behind a reverse proxy, run uvicorn with
    # --proxy-headers and --forwarded-allow-ips so this is the real client.
    user_agent = request.headers.get("user-agent")
    return ClientInfo(
        ip_address=request.client.host if request.client else None,
        user_agent=user_agent[:255] if user_agent else None,
    )


ClientInfoDep = Annotated[ClientInfo, Depends(get_client_info)]


def get_audit_service(session: DbSessionDep) -> AuditService:
    return AuditService(AuditRepository(session))


AuditServiceDep = Annotated[AuditService, Depends(get_audit_service)]


def get_auth_service(
    session: DbSessionDep, settings: SettingsDep, security: SecurityDep, audit: AuditServiceDep
) -> AuthService:
    return AuthService(
        session=session,
        settings=settings,
        users=UserRepository(session),
        organizations=OrganizationRepository(session),
        roles=RoleRepository(session),
        auth_sessions=AuthSessionRepository(session),
        audit=audit,
        hasher=security.password_hasher,
        password_policy=security.password_policy,
        tokens=security.token_service,
        rate_limiter=security.rate_limiter,
    )


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]


def get_user_service(session: DbSessionDep, security: SecurityDep, audit: AuditServiceDep) -> UserService:
    return UserService(
        users=UserRepository(session),
        organizations=OrganizationRepository(session),
        roles=RoleRepository(session),
        auth_sessions=AuthSessionRepository(session),
        audit=audit,
        hasher=security.password_hasher,
        password_policy=security.password_policy,
    )


UserServiceDep = Annotated[UserService, Depends(get_user_service)]


def get_audit_query_service(session: DbSessionDep) -> AuditQueryService:
    return AuditQueryService(AuditRepository(session))


AuditQueryServiceDep = Annotated[AuditQueryService, Depends(get_audit_query_service)]


_bearer = HTTPBearer(auto_error=False, description="Access token from POST /auth/login.")
BearerDep = Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]


def get_optional_principal(credentials: BearerDep, auth: AuthServiceDep) -> Principal | None:
    if credentials is None:
        return None
    return auth.authenticate(credentials.credentials)


OptionalPrincipalDep = Annotated[Principal | None, Depends(get_optional_principal)]


def get_current_principal(principal: OptionalPrincipalDep) -> Principal:
    """The authenticated caller. Organization context always comes from here."""
    if principal is None:
        raise AuthenticationError()
    return principal


CurrentPrincipalDep = Annotated[Principal, Depends(get_current_principal)]


def require_permissions(*required: Permission) -> Callable[..., Principal]:
    """Dependency factory: the caller must hold every listed permission.

    Usage: ``principal: Annotated[Principal, Depends(require_permissions(Permission.USERS_READ))]``
    """

    def dependency(
        principal: CurrentPrincipalDep,
        audit: AuditServiceDep,
        session: DbSessionDep,
        client: ClientInfoDep,
        request: Request,
    ) -> Principal:
        missing = sorted(str(p) for p in required if not principal.has_permission(p))
        if missing:
            audit.record(
                AuditAction.PERMISSION_DENIED,
                AuditOutcome.DENIED,
                client=client,
                organization_id=principal.organization_id,
                actor_user_id=principal.user_id,
                details={"missing_permissions": missing, "method": request.method, "path": request.url.path},
            )
            session.commit()
            raise PermissionDeniedError()
        return principal

    dependency.__name__ = "require_" + "_".join(str(p).replace(".", "_") for p in required)
    return dependency

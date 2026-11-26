"""OpsPilot AI backend: application factory and ASGI entry point.

Run locally with ``uvicorn app.main:app --reload`` from the ``backend`` directory.
"""

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.ai.config.provider_config import ProviderConfigService
from app.ai.providers.http import create_ai_http_client
from app.api.router import api_router
from app.authorization.cache import AuthorizationCache
from app.connectors.adapters.http_client import ConnectorHttpClient
from app.connectors.security.credentials import CredentialCipher
from app.connectors.security.destinations import DestinationPolicy
from app.connectors.trusted import ExecutionApprovalAuthority
from app.api.routes.auth import CSRF_HEADER
from app.core.config import APP_DESCRIPTION, Settings, get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import REQUEST_ID_HEADER, RequestContextMiddleware
from app.core.rate_limit import InMemoryRateLimiter
from app.core.security import AccessTokenService
from app.database.session import Database
from app.services.auth_service import NoDeliveryNotifier
from app.services.password_service import PasswordService

logger = logging.getLogger(__name__)


def _build_lifespan(settings: Settings) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        database = Database.from_settings(settings) if settings.database_configured else None
        if database is None:
            logger.warning("DATABASE_URL is not set; database features are unavailable")
        app.state.database = database
        # Module 07: one pooled outbound HTTP client per process, shared by every connector.
        # Tests may set app.state.connector_transport (a mock transport) before startup.
        connector_http = ConnectorHttpClient(
            app.state.connector_policy,
            max_url_length=settings.connector_max_url_length,
            max_connections=settings.connector_pool_max_connections,
            max_keepalive=settings.connector_pool_max_keepalive,
            ca_bundle=settings.connector_ca_bundle,
            transport=getattr(app.state, "connector_transport", None),
        )
        app.state.connector_http = connector_http
        # Module 08: one pooled HTTP client shared by every AI provider. Tests may set
        # app.state.ai_transport (a mock transport) before startup.
        ai_http = create_ai_http_client(transport=getattr(app.state, "ai_transport", None))
        app.state.ai_http = ai_http
        active_ai = ProviderConfigService(settings).active()
        if active_ai.configured:
            logger.info("AI provider configured", extra={"provider": active_ai.name, "model": active_ai.model})
        else:
            # Not an error: the application runs without AI; the provider reports "not configured".
            logger.warning(
                "AI provider is not configured; AI features are unavailable",
                extra={"provider": active_ai.name, "missing_settings": ", ".join(active_ai.missing_settings)},
            )
        if app.state.connector_cipher is None:
            logger.warning("CONNECTOR_ENCRYPTION_KEY is not set; connector credentials can be neither stored nor used")
        logger.info(
            "Application started",
            extra={"version": settings.app_version, "environment": settings.app_env.value},
        )
        try:
            yield
        finally:
            await connector_http.aclose()
            app.state.connector_http = None
            await ai_http.aclose()
            app.state.ai_http = None
            if database is not None:
                await database.dispose()
            logger.info("Application stopped")

    return lifespan


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title=settings.app_name,
        description=APP_DESCRIPTION,
        version=settings.app_version,
        debug=settings.app_debug,
        lifespan=_build_lifespan(settings),
    )
    app.state.settings = settings
    app.dependency_overrides[get_settings] = lambda: settings

    # Authentication components: one instance per application (see app.api.deps).
    app.state.access_token_service = AccessTokenService.from_settings(settings)
    app.state.password_service = PasswordService()
    app.state.rate_limiter = InMemoryRateLimiter()
    app.state.password_reset_notifier = NoDeliveryNotifier()
    # Module 04: per-process permission cache, validated against the database revision.
    app.state.authorization_cache = AuthorizationCache(settings.authorization_cache_size)
    # Module 07: credential encryption (None without CONNECTOR_ENCRYPTION_KEY: secrets are then
    # refused, never stored in plaintext), the SSRF destination policy, and the in-memory
    # authority that seals trusted query requests.
    app.state.connector_cipher = CredentialCipher.from_settings(settings)
    app.state.connector_policy = DestinationPolicy.from_settings(settings)
    app.state.execution_authority = ExecutionApprovalAuthority()

    # Middleware added last runs first: the request context wraps CORS so every
    # response, including preflights and errors, carries a request ID.
    # Credentials are allowed (the refresh-token cookie) for the explicit
    # origins only; Settings rejects "*".
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", REQUEST_ID_HEADER, CSRF_HEADER],
        expose_headers=[REQUEST_ID_HEADER, "Retry-After"],
    )
    app.add_middleware(RequestContextMiddleware)

    register_exception_handlers(app)
    app.include_router(api_router)
    return app


app = create_app()

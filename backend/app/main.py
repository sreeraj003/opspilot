"""OpsPilot AI backend: application factory and ASGI entry point.

Run locally with ``uvicorn app.main:app --reload`` from the ``backend`` directory.
"""

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.authorization.cache import AuthorizationCache
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
        logger.info(
            "Application started",
            extra={"version": settings.app_version, "environment": settings.app_env.value},
        )
        try:
            yield
        finally:
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

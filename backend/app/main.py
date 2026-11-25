"""OpsPilot AI backend: application factory and ASGI entry point.

Run locally with ``uvicorn app.main:app --reload`` from the ``backend`` directory.
"""

import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import APP_DESCRIPTION, Settings, get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import REQUEST_ID_HEADER, RequestContextMiddleware
from app.database.session import Database

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

    # Middleware added last runs first: the request context wraps CORS so every
    # response, including preflights and errors, carries a request ID.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", REQUEST_ID_HEADER],
        expose_headers=[REQUEST_ID_HEADER],
    )
    app.add_middleware(RequestContextMiddleware)

    register_exception_handlers(app)
    app.include_router(api_router)
    return app


app = create_app()

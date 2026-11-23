"""OpsPilot AI API application factory and ASGI entry point.

Run locally with:  uvicorn app.main:app --reload
"""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import Settings, get_settings
from app.core.lifespan import build_lifespan
from app.core.logging import configure_logging
from app.middleware.error_handlers import register_exception_handlers
from app.middleware.request_context import REQUEST_ID_HEADER, RequestContextMiddleware
from app.security.components import SecurityComponents

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings)

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        debug=settings.debug,
        lifespan=build_lifespan(settings),
        openapi_url=f"{settings.api_v1_prefix}/openapi.json",
        docs_url=f"{settings.api_v1_prefix}/docs",
        redoc_url=f"{settings.api_v1_prefix}/redoc",
    )
    app.state.settings = settings
    app.state.security = SecurityComponents.from_settings(settings)
    if settings.jwt_secret_ephemeral:
        logger.warning("JWT_SECRET_KEY not set; using an ephemeral key (tokens are invalidated on restart)")
    app.dependency_overrides[get_settings] = lambda: settings

    # Middleware added last runs first: request context wraps CORS so every
    # response, including CORS preflights and errors, carries a request ID.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", REQUEST_ID_HEADER],
        expose_headers=[REQUEST_ID_HEADER],
    )
    app.add_middleware(RequestContextMiddleware)

    register_exception_handlers(app)
    app.include_router(api_router, prefix=settings.api_v1_prefix)
    return app


app = create_app()

"""Aggregates every route module into one router included by the application.

Operational endpoints (``/``, ``/health``) stay unversioned; business APIs live
under ``/api/v1``.
"""

from fastapi import APIRouter

from app.api.routes import health, settings, system

API_V1_PREFIX = "/api/v1"

api_v1_router = APIRouter(prefix=API_V1_PREFIX)
api_v1_router.include_router(settings.router)

api_router = APIRouter()
api_router.include_router(system.router)
api_router.include_router(health.router)
api_router.include_router(api_v1_router)

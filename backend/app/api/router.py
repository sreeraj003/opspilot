"""Aggregates every route module into one router included by the application.

Operational endpoints (``/``, ``/health``) stay unversioned; business APIs live
under ``/api/v1``.
"""

from fastapi import APIRouter

from app.api.routes import access, auth, health, permissions, roles, schema_registry, settings, system, users

API_V1_PREFIX = "/api/v1"

api_v1_router = APIRouter(prefix=API_V1_PREFIX)
api_v1_router.include_router(auth.router)
api_v1_router.include_router(users.router)
api_v1_router.include_router(settings.router)
api_v1_router.include_router(roles.router)
api_v1_router.include_router(permissions.router)
api_v1_router.include_router(access.users_router)
api_v1_router.include_router(access.authorization_router)
api_v1_router.include_router(schema_registry.schemas_router)
api_v1_router.include_router(schema_registry.tables_router)
api_v1_router.include_router(schema_registry.columns_router)
api_v1_router.include_router(schema_registry.relationships_router)

api_router = APIRouter()
api_router.include_router(system.router)
api_router.include_router(health.router)
api_router.include_router(api_v1_router)

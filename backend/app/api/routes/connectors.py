"""External API connector API (``/api/v1/connectors``).

Every endpoint requires authentication and one connector permission, checked by
``require_permission`` (the Module 04 engine):

=======================  ===============================================================
``connector.read``       list, get, health, audit history, server environment
``connector.create``     ``POST /connectors``
``connector.update``     ``PATCH /connectors/{id}`` (including credential replacement)
``connector.delete``     ``DELETE /connectors/{id}``
``connector.activate``   ``PATCH /connectors/{id}/status``
``connector.test``       ``POST /connectors/{id}/test`` (fixed system test query only)
=======================  ===============================================================

Responses carry masked configuration only: no credential, secret header value or
ciphertext is ever returned. No endpoint accepts SQL.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.deps import ConnectorServiceDep, require_permission
from app.connectors.adapters.response import ResponseContract
from app.connectors.security.destinations import DestinationPolicy, DestinationError
from app.connectors.validators.config import join_url
from app.core.authorization import Permission
from app.models.connector import ConnectorAuditEvent, ExternalAPIConnector
from app.models.schema_registry import SchemaSource
from app.repositories.base_repository import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from app.repositories.connector_repository import ConnectorQuery, ConnectorSort, ConnectorStatusFilter, SortOrder
from app.schemas.connector import (
    MASK,
    AuthenticationType,
    ConnectorAuditPage,
    ConnectorAuditRead,
    ConnectorCreate,
    ConnectorEnvironment,
    ConnectorHealth,
    ConnectorPage,
    ConnectorRead,
    ConnectorStatusUpdate,
    ConnectorTestResult,
    ConnectorUpdate,
    CustomHeaderRead,
    QueryParamRead,
    TestStatus,
)
from app.schemas.errors import ErrorResponse
from app.services.auth_service import Principal
from app.services.connector_service import ConnectorService

router = APIRouter(prefix="/connectors", tags=["connectors"])

CanRead = Annotated[Principal, Depends(require_permission(Permission.CONNECTOR_READ))]
CanCreate = Annotated[Principal, Depends(require_permission(Permission.CONNECTOR_CREATE))]
CanUpdate = Annotated[Principal, Depends(require_permission(Permission.CONNECTOR_UPDATE))]
CanDelete = Annotated[Principal, Depends(require_permission(Permission.CONNECTOR_DELETE))]
CanTest = Annotated[Principal, Depends(require_permission(Permission.CONNECTOR_TEST))]
CanActivate = Annotated[Principal, Depends(require_permission(Permission.CONNECTOR_ACTIVATE))]

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Not authenticated."},
    403: {"model": ErrorResponse, "description": "Not permitted."},
}
_NOT_FOUND: dict[int | str, dict[str, Any]] = {404: {"model": ErrorResponse, "description": "Not found."}}
_CONFLICT: dict[int | str, dict[str, Any]] = {
    409: {"model": ErrorResponse, "description": "Duplicate name or endpoint, or another active connector serves the schema."}
}
_INVALID: dict[int | str, dict[str, Any]] = {
    422: {"model": ErrorResponse, "description": "Invalid configuration or destination not permitted."}
}
_NO_KEY: dict[int | str, dict[str, Any]] = {
    503: {"model": ErrorResponse, "description": "Credential encryption is not configured on the server."}
}


def connector_read(connector: ExternalAPIConnector, source: SchemaSource | None, service: ConnectorService) -> ConnectorRead:
    problem = service.destination_problem(connector)
    try:
        host = DestinationPolicy().parse(join_url(connector.base_url, connector.endpoint_path)).host_header
    except DestinationError:
        host = connector.base_url.split("://", 1)[-1].split("/", 1)[0]
    return ConnectorRead(
        id=connector.id,
        name=connector.name,
        description=connector.description,
        schema_source_id=connector.schema_source_id,
        schema_source_name=source.name if source is not None else None,
        schema_source_available=source is not None and source.deleted_at is None,
        base_url=connector.base_url,
        endpoint_path=connector.endpoint_path,
        endpoint_host=host,
        http_method="GET",
        sql_parameter_name=connector.sql_parameter_name,
        extra_query_params=[QueryParamRead(name=p[0], value=p[1]) for p in connector.extra_query_params],
        authentication_type=connector.authentication_type,  # type: ignore[arg-type]
        auth_header_name=connector.auth_header_name,
        has_credentials=connector.has_credentials,
        credential_mask=MASK if connector.has_credentials else None,
        custom_headers=[
            CustomHeaderRead(
                name=h["name"],
                secret=bool(h.get("secret")),
                value=None if h.get("secret") else h.get("value"),
                has_value=bool(h.get("encrypted_value") if h.get("secret") else h.get("value")),
            )
            for h in connector.custom_headers
        ],
        health_check_path=connector.health_check_path,
        response_contract=ResponseContract.model_validate(connector.response_contract),
        timeout_seconds=connector.timeout_seconds,
        connect_timeout_seconds=connector.connect_timeout_seconds,
        read_timeout_seconds=connector.read_timeout_seconds,
        max_response_size_bytes=connector.max_response_size_bytes,
        max_sql_length=connector.max_sql_length,
        is_active=connector.is_active,
        last_tested_at=connector.last_tested_at,
        last_test_status=connector.last_test_status,  # type: ignore[arg-type]
        last_error_category=connector.last_error_category,
        last_error_summary=connector.last_error_summary,
        destination_allowed=problem is None,
        destination_problem=problem,
        created_at=connector.created_at,
        updated_at=connector.updated_at,
        created_by=connector.created_by,
        updated_by=connector.updated_by,
    )


def audit_read(event: ConnectorAuditEvent, names: dict[uuid.UUID, str]) -> ConnectorAuditRead:
    read = ConnectorAuditRead.model_validate(event)
    read.requesting_user_name = names.get(event.requesting_user_id) if event.requesting_user_id else None
    return read


async def _read(service: ConnectorService, connector_id: uuid.UUID) -> ConnectorRead:
    connector, source = await service.get(connector_id)
    return connector_read(connector, source, service)


@router.get("/environment", response_model=ConnectorEnvironment, summary="Server-side connector settings (no secrets)",
            responses=_ERRORS)
async def get_environment(_: CanRead, service: ConnectorServiceDep) -> ConnectorEnvironment:
    return ConnectorEnvironment(**service.environment())


@router.post("", response_model=ConnectorRead, status_code=status.HTTP_201_CREATED, summary="Create a connector",
             responses={**_ERRORS, **_CONFLICT, **_INVALID, **_NO_KEY})
async def create_connector(body: ConnectorCreate, principal: CanCreate, service: ConnectorServiceDep) -> ConnectorRead:
    connector = await service.create(body, principal.user)
    return await _read(service, connector.id)


@router.get("", response_model=ConnectorPage, summary="List, search and filter connectors", responses=_ERRORS)
async def list_connectors(
    _: CanRead,
    service: ConnectorServiceDep,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    search: Annotated[str | None, Query(max_length=100, description="Matches name, description or base URL.")] = None,
    status_filter: Annotated[ConnectorStatusFilter, Query(alias="status")] = ConnectorStatusFilter.ALL,
    authentication_type: Annotated[AuthenticationType | None, Query()] = None,
    last_test_status: Annotated[TestStatus | None, Query()] = None,
    schema_source_id: Annotated[uuid.UUID | None, Query()] = None,
    sort: Annotated[ConnectorSort, Query()] = ConnectorSort.NAME,
    order: Annotated[SortOrder, Query()] = SortOrder.ASC,
) -> ConnectorPage:
    query = ConnectorQuery(
        search=search or None,
        status=status_filter,
        authentication_type=authentication_type,
        last_test_status=last_test_status,
        schema_source_id=schema_source_id,
        sort=sort,
        order=order,
    )
    page, sources = await service.list_connectors(query, offset=offset, limit=limit)
    return ConnectorPage(
        items=[connector_read(c, sources.get(c.schema_source_id), service) for c in page.items],
        total=page.total,
        offset=page.offset,
        limit=page.limit,
    )


@router.get("/{connector_id}", response_model=ConnectorRead, summary="Get a connector (masked configuration)",
            responses={**_ERRORS, **_NOT_FOUND})
async def get_connector(connector_id: uuid.UUID, _: CanRead, service: ConnectorServiceDep) -> ConnectorRead:
    return await _read(service, connector_id)


@router.patch("/{connector_id}", response_model=ConnectorRead,
              summary="Update a connector; omit `credential` to keep the stored one",
              responses={**_ERRORS, **_NOT_FOUND, **_CONFLICT, **_INVALID, **_NO_KEY})
async def update_connector(
    connector_id: uuid.UUID, body: ConnectorUpdate, principal: CanUpdate, service: ConnectorServiceDep
) -> ConnectorRead:
    await service.update(connector_id, body, principal.user)
    return await _read(service, connector_id)


@router.delete("/{connector_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a connector",
               responses={**_ERRORS, **_NOT_FOUND})
async def delete_connector(connector_id: uuid.UUID, principal: CanDelete, service: ConnectorServiceDep) -> Response:
    await service.delete(connector_id, principal.user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/{connector_id}/status", response_model=ConnectorRead, summary="Activate or deactivate a connector",
              responses={**_ERRORS, **_NOT_FOUND, **_CONFLICT, **_INVALID, **_NO_KEY})
async def set_connector_status(
    connector_id: uuid.UUID, body: ConnectorStatusUpdate, principal: CanActivate, service: ConnectorServiceDep
) -> ConnectorRead:
    await service.set_status(connector_id, body.is_active, principal.user)
    return await _read(service, connector_id)


@router.post("/{connector_id}/test", response_model=ConnectorTestResult,
             summary="Test the connection with the fixed system test query (no SQL is accepted)",
             responses={**_ERRORS, **_NOT_FOUND})
async def test_connector(connector_id: uuid.UUID, principal: CanTest, service: ConnectorServiceDep) -> ConnectorTestResult:
    outcome = await service.test(connector_id, principal.user)
    return ConnectorTestResult(
        connector_id=outcome.connector_id,
        status="passed" if outcome.passed else "failed",
        method=outcome.method,  # type: ignore[arg-type]
        message=outcome.message,
        error_category=outcome.error_category,
        http_status=outcome.http_status,
        row_count=outcome.row_count,
        duration_ms=outcome.duration_ms,
        tested_at=outcome.tested_at,
        execution_reference=outcome.execution_reference,
        steps=outcome.steps,  # type: ignore[arg-type]
    )


@router.get("/{connector_id}/health", response_model=ConnectorHealth,
            summary="Connector readiness from its configuration and last test (no network call)",
            responses={**_ERRORS, **_NOT_FOUND})
async def connector_health(connector_id: uuid.UUID, _: CanRead, service: ConnectorServiceDep) -> ConnectorHealth:
    return ConnectorHealth(**await service.health(connector_id))


@router.get("/{connector_id}/audit", response_model=ConnectorAuditPage, summary="Connector audit history",
            responses={**_ERRORS, **_NOT_FOUND})
async def connector_audit(
    connector_id: uuid.UUID,
    _: CanRead,
    service: ConnectorServiceDep,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 20,
    operation_type: Annotated[str | None, Query(max_length=32, pattern=r"^[a-z_]+$")] = None,
) -> ConnectorAuditPage:
    page, names = await service.audit_history(connector_id, offset=offset, limit=limit, operation_type=operation_type)
    return ConnectorAuditPage(items=[audit_read(e, names) for e in page.items], total=page.total, offset=page.offset, limit=page.limit)

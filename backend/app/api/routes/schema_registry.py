"""Schema registry API (``/api/v1/schemas``, ``/tables``, ``/columns``, ``/relationships``).

Every endpoint requires authentication and one schema permission, checked by
``require_permission`` (the Module 04 engine). The backend enforces these
regardless of what the interface shows:

==============================  ==========================================================
``schema.read``                 every GET except export
``schema.create``               ``POST /schemas``
``schema.update``               ``PATCH /schemas/{id}``; create and edit tables and columns
``schema.delete``               delete schemas, tables and columns
``schema.relationship.manage``  create, edit and delete relationships
``schema.import``               ``POST /schemas/{id}/import`` (including dry runs)
``schema.export``               ``GET /schemas/{id}/export``
==============================  ==========================================================
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response, status
from pydantic import ValidationError

from app.api.deps import SchemaRegistryServiceDep, SettingsDep, require_permission
from app.core.authorization import Permission
from app.core.exceptions import AppError, InvalidInputError
from app.models.schema_registry import SchemaChangeEvent, SchemaColumn, SchemaSource, SchemaTable
from app.repositories.base_repository import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from app.repositories.schema_registry_repository import (
    ColumnQuery,
    ColumnSort,
    RelationshipQuery,
    RelationshipRow,
    SortOrder,
    SourceCounts,
    SourceQuery,
    SourceSort,
    StatusFilter,
    TableQuery,
    TableSort,
)
from app.schema_registry.data_types import data_type_category
from app.schema_registry.json_limits import JsonLimitError, parse_json
from app.schema_registry.representation import RepresentationFilter
from app.schemas.errors import ErrorResponse
from app.schemas.schema_registry import (
    AiSchemaRepresentation,
    DatabaseType,
    OutlineColumn,
    OutlineTable,
    RelationshipEndpointRead,
    RelationshipType,
    SchemaChangePage,
    SchemaChangeRead,
    SchemaColumnCreate,
    SchemaColumnPage,
    SchemaColumnRead,
    SchemaColumnUpdate,
    SchemaImportOptions,
    SchemaImportResult,
    SchemaOutline,
    SchemaRelationshipCreate,
    SchemaRelationshipPage,
    SchemaRelationshipRead,
    SchemaRelationshipUpdate,
    SchemaSourceCreate,
    SchemaSourcePage,
    SchemaSourceRead,
    SchemaSourceUpdate,
    SchemaSummary,
    SchemaTableCreate,
    SchemaTableDetail,
    SchemaTablePage,
    SchemaTableRead,
    SchemaTableUpdate,
    SchemaVersionRead,
)
from app.services.auth_service import Principal

schemas_router = APIRouter(prefix="/schemas", tags=["schema registry"])
tables_router = APIRouter(prefix="/tables", tags=["schema registry"])
columns_router = APIRouter(prefix="/columns", tags=["schema registry"])
relationships_router = APIRouter(prefix="/relationships", tags=["schema registry"])

CanRead = Annotated[Principal, Depends(require_permission(Permission.SCHEMA_READ))]
CanCreate = Annotated[Principal, Depends(require_permission(Permission.SCHEMA_CREATE))]
CanUpdate = Annotated[Principal, Depends(require_permission(Permission.SCHEMA_UPDATE))]
CanDelete = Annotated[Principal, Depends(require_permission(Permission.SCHEMA_DELETE))]
CanImport = Annotated[Principal, Depends(require_permission(Permission.SCHEMA_IMPORT))]
CanExport = Annotated[Principal, Depends(require_permission(Permission.SCHEMA_EXPORT))]
CanManageRelationships = Annotated[Principal, Depends(require_permission(Permission.SCHEMA_RELATIONSHIP_MANAGE))]

Offset = Annotated[int, Query(ge=0, le=1_000_000)]
Limit = Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)]
Search = Annotated[str | None, Query(max_length=100)]
Status = Annotated[StatusFilter, Query(alias="status")]
Order = Annotated[SortOrder, Query()]
Cascade = Annotated[
    bool, Query(description="Also delete the relationships that use it, in the same transaction.")
]

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Not authenticated."},
    403: {"model": ErrorResponse, "description": "Not permitted."},
}
_NOT_FOUND: dict[int | str, dict[str, Any]] = {404: {"model": ErrorResponse, "description": "Not found."}}
_CONFLICT: dict[int | str, dict[str, Any]] = {
    409: {"model": ErrorResponse, "description": "Duplicate name, in use, or version conflict."}
}
_INVALID: dict[int | str, dict[str, Any]] = {
    422: {"model": ErrorResponse, "description": "Invalid input; details lists every validation issue."}
}


class PayloadTooLargeError(AppError):
    status_code = status.HTTP_413_CONTENT_TOO_LARGE
    code = "PAYLOAD_TOO_LARGE"
    message = "The request body is too large."


# --- presenters ------------------------------------------------------------------------------------


def source_read(source: SchemaSource, counts: SourceCounts | None = None) -> SchemaSourceRead:
    read = SchemaSourceRead.model_validate(source)
    if counts is not None:
        read.table_count, read.column_count, read.relationship_count = counts.tables, counts.columns, counts.relationships
    return read


def table_read(table: SchemaTable, columns: int = 0, relationships: int = 0) -> SchemaTableRead:
    read = SchemaTableRead.model_validate(table)
    read.column_count, read.relationship_count = columns, relationships
    return read


def column_read(column: SchemaColumn) -> SchemaColumnRead:
    read = SchemaColumnRead.model_validate(column)
    read.data_type_category = data_type_category(column.data_type)
    return read


def relationship_read(row: RelationshipRow) -> SchemaRelationshipRead:
    def end(table: SchemaTable, column: SchemaColumn) -> RelationshipEndpointRead:
        return RelationshipEndpointRead(
            table_id=table.id, column_id=column.id, schema_name=table.schema_name, table_name=table.table_name,
            column_name=column.column_name, data_type=column.data_type, is_active=table.is_active and column.is_active,
        )

    relationship = row.relationship
    return SchemaRelationshipRead(
        id=relationship.id,
        schema_source_id=relationship.schema_source_id,
        source=end(row.source_table, row.source_column),
        target=end(row.target_table, row.target_column),
        relationship_type=relationship.relationship_type,
        description=relationship.description,
        is_active=relationship.is_active,
        created_at=relationship.created_at,
        updated_at=relationship.updated_at,
    )


def change_read(event: SchemaChangeEvent, actor_name: str | None) -> SchemaChangeRead:
    read = SchemaChangeRead.model_validate(event)
    read.actor_name = actor_name
    return read


# --- schema sources ------------------------------------------------------------------------------------


@schemas_router.post("", response_model=SchemaSourceRead, status_code=status.HTTP_201_CREATED,
                     summary="Register a schema source", responses={**_ERRORS, **_CONFLICT, **_INVALID})
async def create_schema(body: SchemaSourceCreate, principal: CanCreate, service: SchemaRegistryServiceDep) -> SchemaSourceRead:
    return source_read(await service.create_source(body, principal.user), SourceCounts())


@schemas_router.get("", response_model=SchemaSourcePage, summary="List, search and filter schema sources",
                    responses=_ERRORS)
async def list_schemas(
    _: CanRead,
    service: SchemaRegistryServiceDep,
    offset: Offset = 0,
    limit: Limit = DEFAULT_PAGE_SIZE,
    search: Annotated[str | None, Query(max_length=100, description="Matches name or description.")] = None,
    status_filter: Status = StatusFilter.ALL,
    database_type: Annotated[DatabaseType | None, Query()] = None,
    sort: Annotated[SourceSort, Query()] = SourceSort.NAME,
    order: Order = SortOrder.ASC,
) -> SchemaSourcePage:
    query = SourceQuery(search=search or None, status=status_filter, database_type=database_type, sort=sort, order=order)
    page, counts = await service.list_sources(query, offset=offset, limit=limit)
    return SchemaSourcePage(
        items=[source_read(s, counts.get(s.id)) for s in page.items], total=page.total, offset=page.offset, limit=page.limit
    )


@schemas_router.get("/{schema_id}", response_model=SchemaSourceRead, summary="Get a schema source",
                    responses={**_ERRORS, **_NOT_FOUND})
async def get_schema(schema_id: uuid.UUID, _: CanRead, service: SchemaRegistryServiceDep) -> SchemaSourceRead:
    source, counts = await service.get_source(schema_id)
    return source_read(source, counts)


@schemas_router.patch("/{schema_id}", response_model=SchemaSourceRead, summary="Update a schema source",
                      responses={**_ERRORS, **_NOT_FOUND, **_CONFLICT, **_INVALID})
async def update_schema(
    schema_id: uuid.UUID, body: SchemaSourceUpdate, principal: CanUpdate, service: SchemaRegistryServiceDep
) -> SchemaSourceRead:
    await service.update_source(schema_id, body, principal.user)
    source, counts = await service.get_source(schema_id)
    return source_read(source, counts)


@schemas_router.delete("/{schema_id}", status_code=status.HTTP_204_NO_CONTENT,
                       summary="Delete a schema source with all its metadata (soft delete; history is kept)",
                       responses={**_ERRORS, **_NOT_FOUND})
async def delete_schema(schema_id: uuid.UUID, principal: CanDelete, service: SchemaRegistryServiceDep) -> Response:
    await service.delete_source(schema_id, principal.user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- tables -----------------------------------------------------------------------------------------------


@schemas_router.post("/{schema_id}/tables", response_model=SchemaTableDetail, status_code=status.HTTP_201_CREATED,
                     summary="Register a table (optionally with its columns)",
                     responses={**_ERRORS, **_NOT_FOUND, **_CONFLICT, **_INVALID})
async def create_table(
    schema_id: uuid.UUID, body: SchemaTableCreate, principal: CanUpdate, service: SchemaRegistryServiceDep
) -> SchemaTableDetail:
    table = await service.create_table(schema_id, body, principal.user)
    return await _table_detail(service, table.id)


@schemas_router.get("/{schema_id}/tables", response_model=SchemaTablePage, summary="List, search and filter tables",
                    responses={**_ERRORS, **_NOT_FOUND})
async def list_tables(
    schema_id: uuid.UUID,
    _: CanRead,
    service: SchemaRegistryServiceDep,
    offset: Offset = 0,
    limit: Limit = DEFAULT_PAGE_SIZE,
    search: Annotated[str | None, Query(max_length=100, description="Matches name, description or purpose.")] = None,
    status_filter: Status = StatusFilter.ALL,
    schema_name: Annotated[str | None, Query(max_length=63, description="Only this namespace.")] = None,
    sort: Annotated[TableSort, Query()] = TableSort.TABLE_NAME,
    order: Order = SortOrder.ASC,
) -> SchemaTablePage:
    query = TableQuery(search=search or None, status=status_filter, schema_name=schema_name or None, sort=sort, order=order)
    data = await service.list_tables(schema_id, query, offset=offset, limit=limit)
    return SchemaTablePage(
        items=[
            table_read(t, data.column_counts.get(t.id, 0), data.relationship_counts.get(t.id, 0)) for t in data.page.items
        ],
        total=data.page.total,
        offset=data.page.offset,
        limit=data.page.limit,
    )


async def _table_detail(service: Any, table_id: uuid.UUID) -> SchemaTableDetail:
    table, columns, relationships = await service.get_table(table_id)
    return SchemaTableDetail(
        **table_read(table, len(columns), relationships).model_dump(), columns=[column_read(c) for c in columns]
    )


@tables_router.get("/{table_id}", response_model=SchemaTableDetail, summary="Get a table with its columns",
                   responses={**_ERRORS, **_NOT_FOUND})
async def get_table(table_id: uuid.UUID, _: CanRead, service: SchemaRegistryServiceDep) -> SchemaTableDetail:
    return await _table_detail(service, table_id)


@tables_router.patch("/{table_id}", response_model=SchemaTableDetail, summary="Update a table",
                     responses={**_ERRORS, **_NOT_FOUND, **_CONFLICT, **_INVALID})
async def update_table(
    table_id: uuid.UUID, body: SchemaTableUpdate, principal: CanUpdate, service: SchemaRegistryServiceDep
) -> SchemaTableDetail:
    await service.update_table(table_id, body, principal.user)
    return await _table_detail(service, table_id)


@tables_router.delete("/{table_id}", status_code=status.HTTP_204_NO_CONTENT,
                      summary="Delete a table and its columns (soft delete)",
                      responses={**_ERRORS, **_NOT_FOUND, **_CONFLICT})
async def delete_table(
    table_id: uuid.UUID, principal: CanDelete, service: SchemaRegistryServiceDep, cascade_relationships: Cascade = False
) -> Response:
    await service.delete_table(table_id, principal.user, cascade_relationships=cascade_relationships)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- columns -----------------------------------------------------------------------------------------------


@tables_router.post("/{table_id}/columns", response_model=SchemaColumnRead, status_code=status.HTTP_201_CREATED,
                    summary="Register a column", responses={**_ERRORS, **_NOT_FOUND, **_CONFLICT, **_INVALID})
async def create_column(
    table_id: uuid.UUID, body: SchemaColumnCreate, principal: CanUpdate, service: SchemaRegistryServiceDep
) -> SchemaColumnRead:
    return column_read(await service.create_column(table_id, body, principal.user))


@tables_router.get("/{table_id}/columns", response_model=SchemaColumnPage, summary="List, search and filter columns",
                   responses={**_ERRORS, **_NOT_FOUND})
async def list_columns(
    table_id: uuid.UUID,
    _: CanRead,
    service: SchemaRegistryServiceDep,
    offset: Offset = 0,
    limit: Limit = DEFAULT_PAGE_SIZE,
    search: Annotated[str | None, Query(max_length=100, description="Matches name, description or meaning.")] = None,
    status_filter: Status = StatusFilter.ALL,
    primary_key: Annotated[bool | None, Query()] = None,
    foreign_key: Annotated[bool | None, Query()] = None,
    sensitive: Annotated[bool | None, Query()] = None,
    sort: Annotated[ColumnSort, Query()] = ColumnSort.ORDINAL_POSITION,
    order: Order = SortOrder.ASC,
) -> SchemaColumnPage:
    query = ColumnQuery(
        search=search or None, status=status_filter, primary_key=primary_key, foreign_key=foreign_key,
        sensitive=sensitive, sort=sort, order=order,
    )
    page = await service.list_columns(table_id, query, offset=offset, limit=limit)
    return SchemaColumnPage(items=[column_read(c) for c in page.items], total=page.total, offset=page.offset, limit=page.limit)


@columns_router.patch("/{column_id}", response_model=SchemaColumnRead, summary="Update a column",
                      responses={**_ERRORS, **_NOT_FOUND, **_CONFLICT, **_INVALID})
async def update_column(
    column_id: uuid.UUID, body: SchemaColumnUpdate, principal: CanUpdate, service: SchemaRegistryServiceDep
) -> SchemaColumnRead:
    return column_read(await service.update_column(column_id, body, principal.user))


@columns_router.delete("/{column_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a column (soft delete)",
                       responses={**_ERRORS, **_NOT_FOUND, **_CONFLICT})
async def delete_column(
    column_id: uuid.UUID, principal: CanDelete, service: SchemaRegistryServiceDep, cascade_relationships: Cascade = False
) -> Response:
    await service.delete_column(column_id, principal.user, cascade_relationships=cascade_relationships)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- relationships ----------------------------------------------------------------------------------------------


@schemas_router.post("/{schema_id}/relationships", response_model=SchemaRelationshipRead,
                     status_code=status.HTTP_201_CREATED, summary="Register a relationship between two tables",
                     responses={**_ERRORS, **_NOT_FOUND, **_CONFLICT, **_INVALID})
async def create_relationship(
    schema_id: uuid.UUID, body: SchemaRelationshipCreate, principal: CanManageRelationships,
    service: SchemaRegistryServiceDep,
) -> SchemaRelationshipRead:
    return relationship_read(await service.create_relationship(schema_id, body, principal.user))


@schemas_router.get("/{schema_id}/relationships", response_model=SchemaRelationshipPage,
                    summary="List and filter relationships", responses={**_ERRORS, **_NOT_FOUND})
async def list_relationships(
    schema_id: uuid.UUID,
    _: CanRead,
    service: SchemaRegistryServiceDep,
    offset: Offset = 0,
    limit: Limit = DEFAULT_PAGE_SIZE,
    status_filter: Status = StatusFilter.ALL,
    table_id: Annotated[uuid.UUID | None, Query(description="Only relationships touching this table.")] = None,
    relationship_type: Annotated[RelationshipType | None, Query()] = None,
    search: Search = None,
) -> SchemaRelationshipPage:
    query = RelationshipQuery(status=status_filter, table_id=table_id, relationship_type=relationship_type, search=search or None)
    rows, total = await service.list_relationships(schema_id, query, offset=offset, limit=limit)
    return SchemaRelationshipPage(items=[relationship_read(r) for r in rows], total=total, offset=offset, limit=limit)


@relationships_router.patch("/{relationship_id}", response_model=SchemaRelationshipRead,
                            summary="Update a relationship", responses={**_ERRORS, **_NOT_FOUND, **_CONFLICT, **_INVALID})
async def update_relationship(
    relationship_id: uuid.UUID, body: SchemaRelationshipUpdate, principal: CanManageRelationships,
    service: SchemaRegistryServiceDep,
) -> SchemaRelationshipRead:
    return relationship_read(await service.update_relationship(relationship_id, body, principal.user))


@relationships_router.delete("/{relationship_id}", status_code=status.HTTP_204_NO_CONTENT,
                             summary="Remove a relationship (soft delete)", responses={**_ERRORS, **_NOT_FOUND})
async def delete_relationship(
    relationship_id: uuid.UUID, principal: CanManageRelationships, service: SchemaRegistryServiceDep
) -> Response:
    await service.delete_relationship(relationship_id, principal.user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- import, export and metadata ---------------------------------------------------------------------------------------


_IMPORT_BODY = {
    "requestBody": {
        "required": True,
        "content": {"application/json": {"schema": SchemaImportOptions.model_json_schema()}},
        "description": "The import options and the document (see docs/SCHEMA_IMPORT_FORMAT.md).",
    }
}


@schemas_router.post(
    "/{schema_id}/import", response_model=SchemaImportResult,
    summary="Validate, preview (dry_run) or apply a JSON schema import",
    description=(
        "Strategies: create_only (default; refuses to touch registered items), merge (adds and updates; keeps "
        "what the document omits), replace (matches the document exactly; needs confirm_replace). The import is "
        "one transaction: on any error nothing changes. A dry run returns the same result without writing."
    ),
    openapi_extra=_IMPORT_BODY,
    responses={**_ERRORS, **_NOT_FOUND, **_CONFLICT, **_INVALID,
               413: {"model": ErrorResponse, "description": "Body larger than SCHEMA_IMPORT_MAX_BYTES."}},
)
async def import_schema(
    schema_id: uuid.UUID, request: Request, principal: CanImport, service: SchemaRegistryServiceDep,
    settings: SettingsDep,
) -> SchemaImportResult:
    options = await _read_import_options(request, settings.schema_import_max_bytes, settings.schema_import_max_depth)
    return SchemaImportResult.model_validate(await service.import_schema(schema_id, options, principal.user))


async def _read_import_options(request: Request, max_bytes: int, max_depth: int) -> SchemaImportOptions:
    """Read the body with a byte limit, parse it with a depth limit, then validate the envelope."""
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > max_bytes:
        raise PayloadTooLargeError(f"The import is larger than the {max_bytes}-byte limit.")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > max_bytes:
            raise PayloadTooLargeError(f"The import is larger than the {max_bytes}-byte limit.")
    try:
        raw = parse_json(bytes(body), max_bytes=max_bytes, max_depth=max_depth)
    except JsonLimitError as exc:
        if exc.code == "PAYLOAD_TOO_LARGE":
            raise PayloadTooLargeError(str(exc)) from None
        error = InvalidInputError(str(exc))
        error.code = exc.code
        raise error from None
    try:
        return SchemaImportOptions.model_validate(raw)
    except ValidationError as exc:
        raise InvalidInputError(
            details=[
                {"location": [str(p) for p in e.get("loc", ())], "message": e.get("msg"), "type": e.get("type")}
                for e in exc.errors(include_url=False, include_input=False)
            ]
        ) from None


@schemas_router.get("/{schema_id}/export", summary="Export the schema metadata as an import document (JSON)",
                    responses={**_ERRORS, **_NOT_FOUND})
async def export_schema(
    schema_id: uuid.UUID, principal: CanExport, service: SchemaRegistryServiceDep
) -> dict[str, Any]:
    return await service.export(schema_id, principal.user)


@schemas_router.get("/{schema_id}/summary", response_model=SchemaSummary,
                    summary="Counts and current validation issues of a schema", responses={**_ERRORS, **_NOT_FOUND})
async def schema_summary(schema_id: uuid.UUID, _: CanRead, service: SchemaRegistryServiceDep) -> SchemaSummary:
    return SchemaSummary.model_validate(await service.summary(schema_id))


@schemas_router.get("/{schema_id}/version", response_model=SchemaVersionRead,
                    summary="Current schema version and the change that produced it", responses={**_ERRORS, **_NOT_FOUND})
async def schema_version(schema_id: uuid.UUID, _: CanRead, service: SchemaRegistryServiceDep) -> SchemaVersionRead:
    source, last = await service.version(schema_id)
    return SchemaVersionRead(
        schema_id=source.id,
        schema_version=source.schema_version,
        metadata_updated_at=source.metadata_updated_at,
        last_change=change_read(*last) if last else None,
    )


@schemas_router.get("/{schema_id}/history", response_model=SchemaChangePage,
                    summary="Change history (audit log) of a schema, newest first", responses={**_ERRORS, **_NOT_FOUND})
async def schema_history(
    schema_id: uuid.UUID, _: CanRead, service: SchemaRegistryServiceDep, offset: Offset = 0, limit: Limit = DEFAULT_PAGE_SIZE
) -> SchemaChangePage:
    items, total = await service.history(schema_id, offset=offset, limit=limit)
    return SchemaChangePage(items=[change_read(e, n) for e, n in items], total=total, offset=offset, limit=limit)


@schemas_router.get("/{schema_id}/outline", response_model=SchemaOutline,
                    summary="Every table and column, compact (for pickers)", responses={**_ERRORS, **_NOT_FOUND})
async def schema_outline(schema_id: uuid.UUID, _: CanRead, service: SchemaRegistryServiceDep) -> SchemaOutline:
    source, loaded = await service.outline(schema_id)
    return SchemaOutline(
        schema_id=source.id,
        schema_version=source.schema_version,
        tables=[
            OutlineTable(
                id=t.id, schema_name=t.schema_name, table_name=t.table_name, is_active=t.is_active,
                columns=[
                    OutlineColumn(
                        id=c.id, column_name=c.column_name, data_type=c.data_type,
                        data_type_category=data_type_category(c.data_type), is_primary_key=c.is_primary_key,
                        is_foreign_key=c.is_foreign_key, is_nullable=c.is_nullable, is_sensitive=c.is_sensitive,
                        is_active=c.is_active,
                    )
                    for c in loaded.columns_of(t.id)
                ],
            )
            for t in loaded.tables
        ],
    )


@schemas_router.get(
    "/{schema_id}/representation", response_model=AiSchemaRepresentation, response_model_exclude_none=True,
    summary="AI-ready structured representation of the active metadata",
    description="Structured data for AI and SQL-generation modules, not a prompt. Inactive metadata is excluded; "
    "sensitive columns are excluded unless include_sensitive is true.",
    responses={**_ERRORS, **_NOT_FOUND},
)
async def schema_representation(
    schema_id: uuid.UUID,
    _: CanRead,
    service: SchemaRegistryServiceDep,
    schema_name: Annotated[list[str] | None, Query(max_length=50, description="Only these namespaces.")] = None,
    table: Annotated[list[str] | None, Query(max_length=200, description="Only these tables ('orders' or 'sales.orders').")] = None,
    include_related: Annotated[bool, Query(description="Add tables directly related to the selected ones.")] = False,
    include_sensitive: Annotated[bool, Query(description="Include columns marked sensitive (flagged).")] = False,
    include_descriptions: bool = True,
    include_business_terms: bool = True,
) -> AiSchemaRepresentation:
    selection = RepresentationFilter(
        schema_names=tuple(s[:63] for s in schema_name or ()),
        tables=tuple(t[:127] for t in table or ()),
        include_related=include_related,
        include_sensitive_columns=include_sensitive,
        include_descriptions=include_descriptions,
        include_business_terms=include_business_terms,
    )
    return await service.representation(schema_id, selection)

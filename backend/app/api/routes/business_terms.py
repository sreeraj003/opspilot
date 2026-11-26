"""Business terminology API (``/api/v1/business-terms``).

``business_term.read`` lists and reads terms; ``business_term.manage`` creates,
edits, (de)activates and deletes them and reads the registered fields a term
can be associated with (``GET /business-terms/schema-fields``).
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response, status

from app.ai.dependencies.deps import TerminologyServiceDep
from app.ai.understanding.schemas.intent import TermKind
from app.ai.understanding.schemas.terms import (
    BusinessTermCreate,
    BusinessTermPage,
    BusinessTermRead,
    BusinessTermUpdate,
    SchemaFieldsRead,
)
from app.api.deps import require_permission
from app.core.authorization import Permission
from app.repositories.base_repository import MAX_PAGE_SIZE
from app.repositories.business_term_repository import TermQuery
from app.schemas.errors import ErrorResponse
from app.services.auth_service import Principal

router = APIRouter(prefix="/business-terms", tags=["business terms"])

CanRead = Annotated[Principal, Depends(require_permission(Permission.BUSINESS_TERM_READ))]
CanManage = Annotated[Principal, Depends(require_permission(Permission.BUSINESS_TERM_MANAGE))]

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Not authenticated."},
    403: {"model": ErrorResponse, "description": "Not permitted."},
    404: {"model": ErrorResponse, "description": "Not found."},
}
_WRITE_ERRORS: dict[int | str, dict[str, Any]] = {
    **_ERRORS,
    409: {"model": ErrorResponse, "description": "Name or synonym conflict."},
    422: {"model": ErrorResponse, "description": "Invalid term or unknown schema fields."},
}


@router.get("", response_model=BusinessTermPage, summary="List business terms", responses=_ERRORS)
async def list_terms(
    _: CanRead,
    service: TerminologyServiceDep,
    schema_source_id: uuid.UUID | None = None,
    search: Annotated[str | None, Query(max_length=100)] = None,
    kind: TermKind | None = None,
    is_active: bool | None = None,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 50,
) -> BusinessTermPage:
    query = TermQuery(
        schema_source_id=schema_source_id,
        search=search.strip() if search and search.strip() else None,
        kind=kind.value if kind else None,
        is_active=is_active,
    )
    return await service.list_terms(query, offset=offset, limit=limit)


@router.get("/schema-fields", response_model=SchemaFieldsRead,
            summary="Registered tables and columns a term can be associated with", responses=_ERRORS)
async def schema_fields(_: CanManage, service: TerminologyServiceDep, schema_source_id: uuid.UUID) -> SchemaFieldsRead:
    return await service.schema_fields(schema_source_id)


@router.post("", response_model=BusinessTermRead, status_code=status.HTTP_201_CREATED,
             summary="Create a business term", responses=_WRITE_ERRORS)
async def create_term(body: BusinessTermCreate, principal: CanManage, service: TerminologyServiceDep) -> BusinessTermRead:
    return await service.create_term(body, principal.user)


@router.get("/{term_id}", response_model=BusinessTermRead, summary="Get a business term", responses=_ERRORS)
async def get_term(term_id: uuid.UUID, _: CanRead, service: TerminologyServiceDep) -> BusinessTermRead:
    return await service.get_term(term_id)


@router.patch("/{term_id}", response_model=BusinessTermRead, summary="Update a business term", responses=_WRITE_ERRORS)
async def update_term(
    term_id: uuid.UUID, body: BusinessTermUpdate, principal: CanManage, service: TerminologyServiceDep
) -> BusinessTermRead:
    return await service.update_term(term_id, body, principal.user)


@router.delete("/{term_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a business term", responses=_ERRORS)
async def delete_term(term_id: uuid.UUID, principal: CanManage, service: TerminologyServiceDep) -> Response:
    await service.delete_term(term_id, principal.user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

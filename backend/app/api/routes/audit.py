"""Audit log endpoint under /api/v1/audit-events."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import AuditQueryServiceDep, require_permissions
from app.schemas.audit import AuditEventListResponse, AuditEventResponse
from app.schemas.errors import ErrorResponse
from app.security.principal import Principal
from app.security.rbac import Permission

router = APIRouter(tags=["audit"])


@router.get(
    "/audit-events",
    response_model=AuditEventListResponse,
    summary="List audit events for your organization, newest first",
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}},
)
def list_audit_events(
    principal: Annotated[Principal, Depends(require_permissions(Permission.AUDIT_READ))],
    service: AuditQueryServiceDep,
    organization_id: Annotated[uuid.UUID | None, Query(description="Platform administrators only.")] = None,
    action: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AuditEventListResponse:
    events = service.list_events(principal, organization_id=organization_id, action=action, limit=limit, offset=offset)
    return AuditEventListResponse(
        items=[AuditEventResponse.model_validate(e) for e in events], limit=limit, offset=offset
    )

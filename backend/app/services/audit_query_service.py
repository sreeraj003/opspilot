"""Read access to the audit trail, scoped to the caller's organization."""

import uuid

from app.models.audit import AuditEvent
from app.repositories.audit_repository import AuditRepository
from app.security.principal import Principal
from app.security.tenancy import resolve_organization_scope


class AuditQueryService:
    def __init__(self, repository: AuditRepository) -> None:
        self._repository = repository

    def list_events(
        self,
        principal: Principal,
        *,
        organization_id: uuid.UUID | None,
        action: str | None,
        limit: int,
        offset: int,
    ) -> list[AuditEvent]:
        scope = resolve_organization_scope(principal, organization_id)
        return self._repository.list_for_organization(scope, action=action, limit=limit, offset=offset)

"""Data access for audit events."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit import AuditEvent


class AuditRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, event: AuditEvent) -> AuditEvent:
        self._session.add(event)
        self._session.flush()
        return event

    def list_for_organization(
        self, organization_id: uuid.UUID, *, action: str | None, limit: int, offset: int
    ) -> list[AuditEvent]:
        statement = select(AuditEvent).where(AuditEvent.organization_id == organization_id)
        if action:
            statement = statement.where(AuditEvent.action == action)
        statement = statement.order_by(AuditEvent.created_at.desc(), AuditEvent.id).limit(limit).offset(offset)
        return list(self._session.scalars(statement))

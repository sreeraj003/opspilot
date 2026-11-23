"""Data access for organizations."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.organization import Organization


class OrganizationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, organization_id: uuid.UUID) -> Organization | None:
        return self._session.get(Organization, organization_id)

    def get_by_slug(self, slug: str) -> Organization | None:
        return self._session.scalar(select(Organization).where(Organization.slug == slug))

    def slug_exists(self, slug: str) -> bool:
        return self._session.scalar(select(Organization.id).where(Organization.slug == slug)) is not None

    def add(self, organization: Organization) -> Organization:
        self._session.add(organization)
        self._session.flush()
        return organization

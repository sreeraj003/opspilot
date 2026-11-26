"""Persistence for external API connectors and their audit events.

Reads exclude soft-deleted connectors. Searches are parameterised (``ILIKE``
with escaped wildcards); sort columns come from a fixed allow-list.
"""

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from sqlalchemy import Select, func, or_, select

from app.models.connector import ConnectorAuditEvent, ExternalAPIConnector
from app.models.schema_registry import SchemaSource
from app.models.user import User
from app.repositories.base_repository import BaseRepository, Page, validate_pagination


def _contains(value: str) -> str:
    escaped = value.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


class ConnectorStatusFilter(StrEnum):
    ALL = "all"
    ACTIVE = "active"
    INACTIVE = "inactive"


class ConnectorSort(StrEnum):
    NAME = "name"
    UPDATED_AT = "updated_at"
    CREATED_AT = "created_at"
    LAST_TESTED_AT = "last_tested_at"


class SortOrder(StrEnum):
    ASC = "asc"
    DESC = "desc"


@dataclass(frozen=True, slots=True)
class ConnectorQuery:
    search: str | None = None
    status: ConnectorStatusFilter = ConnectorStatusFilter.ALL
    authentication_type: str | None = None
    last_test_status: str | None = None
    schema_source_id: uuid.UUID | None = None
    sort: ConnectorSort = ConnectorSort.NAME
    order: SortOrder = SortOrder.ASC


async def _page[T](repository: BaseRepository[T], statement: Select[Any], order_by: Sequence[Any], offset: int, limit: int) -> Page[T]:
    validate_pagination(offset, limit)
    total = int(await repository.session.scalar(select(func.count()).select_from(statement.subquery())) or 0)
    items = (await repository.session.execute(statement.order_by(*order_by).offset(offset).limit(limit))).scalars().all()
    return Page(items=items, total=total, offset=offset, limit=limit)


class ConnectorRepository(BaseRepository[ExternalAPIConnector]):
    model = ExternalAPIConnector
    default_order_by = (ExternalAPIConnector.name, ExternalAPIConnector.id)

    async def get_for_update(self, connector_id: uuid.UUID) -> ExternalAPIConnector | None:
        statement = self._select().where(ExternalAPIConnector.id == connector_id).with_for_update()
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def name_taken(self, name: str, *, exclude_id: uuid.UUID | None = None) -> bool:
        statement = self._select().where(func.lower(ExternalAPIConnector.name) == name.lower())
        if exclude_id is not None:
            statement = statement.where(ExternalAPIConnector.id != exclude_id)
        return bool(await self.session.scalar(select(statement.exists())))

    async def endpoints(self, *, exclude_id: uuid.UUID | None = None) -> Sequence[tuple[uuid.UUID, str, str]]:
        statement = select(ExternalAPIConnector.id, ExternalAPIConnector.base_url, ExternalAPIConnector.endpoint_path).where(
            ExternalAPIConnector.deleted_at.is_(None)
        )
        if exclude_id is not None:
            statement = statement.where(ExternalAPIConnector.id != exclude_id)
        return [tuple(row) for row in (await self.session.execute(statement)).all()]  # type: ignore[misc]

    async def active_for_source(self, schema_source_id: uuid.UUID, *, exclude_id: uuid.UUID | None = None) -> ExternalAPIConnector | None:
        statement = self._select().where(
            ExternalAPIConnector.schema_source_id == schema_source_id, ExternalAPIConnector.is_active.is_(True)
        )
        if exclude_id is not None:
            statement = statement.where(ExternalAPIConnector.id != exclude_id)
        return (await self.session.execute(statement.limit(1))).scalar_one_or_none()

    async def search(self, query: ConnectorQuery, *, offset: int, limit: int) -> Page[ExternalAPIConnector]:
        statement = self._select()
        if query.search:
            pattern = _contains(query.search)
            statement = statement.where(
                or_(
                    func.lower(ExternalAPIConnector.name).like(pattern, escape="\\"),
                    func.lower(func.coalesce(ExternalAPIConnector.description, "")).like(pattern, escape="\\"),
                    func.lower(ExternalAPIConnector.base_url).like(pattern, escape="\\"),
                )
            )
        if query.status == ConnectorStatusFilter.ACTIVE:
            statement = statement.where(ExternalAPIConnector.is_active.is_(True))
        elif query.status == ConnectorStatusFilter.INACTIVE:
            statement = statement.where(ExternalAPIConnector.is_active.is_(False))
        if query.authentication_type:
            statement = statement.where(ExternalAPIConnector.authentication_type == query.authentication_type)
        if query.last_test_status:
            statement = statement.where(ExternalAPIConnector.last_test_status == query.last_test_status)
        if query.schema_source_id:
            statement = statement.where(ExternalAPIConnector.schema_source_id == query.schema_source_id)
        column = {
            ConnectorSort.NAME: func.lower(ExternalAPIConnector.name),
            ConnectorSort.UPDATED_AT: ExternalAPIConnector.updated_at,
            ConnectorSort.CREATED_AT: ExternalAPIConnector.created_at,
            ConnectorSort.LAST_TESTED_AT: ExternalAPIConnector.last_tested_at,
        }[query.sort]
        ordered = column.desc().nulls_last() if query.order == SortOrder.DESC else column.asc().nulls_last()
        return await _page(self, statement, (ordered, ExternalAPIConnector.id), offset, limit)

    async def all_with_secrets(self) -> Sequence[ExternalAPIConnector]:
        """Every live connector holding ciphertext, locked for a key rotation."""
        statement = (
            self._select()
            .where(or_(ExternalAPIConnector.encrypted_credentials.is_not(None), func.jsonb_array_length(ExternalAPIConnector.custom_headers) > 0))
            .order_by(ExternalAPIConnector.id)
            .with_for_update()
        )
        return (await self.session.execute(statement)).scalars().all()

    async def schema_sources(self, ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, SchemaSource]:
        """Schema sources by id, including soft-deleted ones (shown as unavailable)."""
        wanted = list(set(ids))
        if not wanted:
            return {}
        rows = (await self.session.execute(select(SchemaSource).where(SchemaSource.id.in_(wanted)))).scalars().all()
        return {s.id: s for s in rows}


class ConnectorAuditRepository(BaseRepository[ConnectorAuditEvent]):
    model = ConnectorAuditEvent
    default_order_by = (ConnectorAuditEvent.created_at.desc(), ConnectorAuditEvent.id)

    async def for_connector(
        self, connector_id: uuid.UUID, *, offset: int, limit: int, operation_type: str | None = None
    ) -> tuple[Page[ConnectorAuditEvent], dict[uuid.UUID, str]]:
        statement = select(ConnectorAuditEvent).where(ConnectorAuditEvent.connector_id == connector_id)
        if operation_type:
            statement = statement.where(ConnectorAuditEvent.operation_type == operation_type)
        page = await _page(self, statement, (ConnectorAuditEvent.created_at.desc(), ConnectorAuditEvent.id), offset, limit)
        user_ids = {e.requesting_user_id for e in page.items if e.requesting_user_id}
        names: dict[uuid.UUID, str] = {}
        if user_ids:
            rows = (await self.session.execute(select(User.id, User.full_name).where(User.id.in_(user_ids)))).all()
            names = {row[0]: row[1] for row in rows}
        return page, names

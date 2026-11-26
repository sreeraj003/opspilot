"""Persistence for the schema registry: sources, tables, columns, relationships and change events.

All reads exclude soft-deleted rows. Searches are parameterised (``ILIKE`` with
escaped wildcards); sort columns come from fixed allow-lists, never from input.
"""

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from sqlalchemy import Select, and_, func, or_, select, update
from sqlalchemy.orm import aliased

from app.models.schema_registry import (
    SchemaChangeEvent,
    SchemaColumn,
    SchemaRelationship,
    SchemaSource,
    SchemaTable,
)
from app.models.user import User
from app.repositories.base_repository import BaseRepository, Page, validate_pagination


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _contains(value: str) -> str:
    return f"%{_escape_like(value.strip().lower())}%"


class StatusFilter(StrEnum):
    ALL = "all"
    ACTIVE = "active"
    INACTIVE = "inactive"


class SortOrder(StrEnum):
    ASC = "asc"
    DESC = "desc"


class SourceSort(StrEnum):
    NAME = "name"
    UPDATED_AT = "updated_at"
    CREATED_AT = "created_at"
    SCHEMA_VERSION = "schema_version"


class TableSort(StrEnum):
    TABLE_NAME = "table_name"
    SCHEMA_NAME = "schema_name"
    UPDATED_AT = "updated_at"
    CREATED_AT = "created_at"


class ColumnSort(StrEnum):
    ORDINAL_POSITION = "ordinal_position"
    COLUMN_NAME = "column_name"
    DATA_TYPE = "data_type"


@dataclass(frozen=True, slots=True)
class SourceQuery:
    search: str | None = None
    status: StatusFilter = StatusFilter.ALL
    database_type: str | None = None
    sort: SourceSort = SourceSort.NAME
    order: SortOrder = SortOrder.ASC


@dataclass(frozen=True, slots=True)
class TableQuery:
    search: str | None = None
    status: StatusFilter = StatusFilter.ALL
    schema_name: str | None = None
    sort: TableSort = TableSort.TABLE_NAME
    order: SortOrder = SortOrder.ASC


@dataclass(frozen=True, slots=True)
class ColumnQuery:
    search: str | None = None
    status: StatusFilter = StatusFilter.ALL
    primary_key: bool | None = None
    foreign_key: bool | None = None
    sensitive: bool | None = None
    sort: ColumnSort = ColumnSort.ORDINAL_POSITION
    order: SortOrder = SortOrder.ASC


@dataclass(frozen=True, slots=True)
class RelationshipQuery:
    status: StatusFilter = StatusFilter.ALL
    table_id: uuid.UUID | None = None
    relationship_type: str | None = None
    search: str | None = None


@dataclass(frozen=True, slots=True)
class SourceCounts:
    tables: int = 0
    columns: int = 0
    relationships: int = 0


def _status(statement: Select[Any], column: Any, status: StatusFilter) -> Select[Any]:
    if status == StatusFilter.ACTIVE:
        return statement.where(column.is_(True))
    if status == StatusFilter.INACTIVE:
        return statement.where(column.is_(False))
    return statement


def _ordered(column: Any, order: SortOrder) -> Any:
    return column.desc() if order == SortOrder.DESC else column.asc()


async def _page[T](repository: BaseRepository[T], statement: Select[Any], order_by: Sequence[Any], offset: int, limit: int) -> Page[T]:
    validate_pagination(offset, limit)
    total = int(await repository.session.scalar(select(func.count()).select_from(statement.subquery())) or 0)
    items = (await repository.session.execute(statement.order_by(*order_by).offset(offset).limit(limit))).scalars().all()
    return Page(items=items, total=total, offset=offset, limit=limit)


# --- sources ----------------------------------------------------------------------------------------


class SchemaSourceRepository(BaseRepository[SchemaSource]):
    model = SchemaSource
    default_order_by = (SchemaSource.name, SchemaSource.id)

    async def get_for_update(self, source_id: uuid.UUID) -> SchemaSource | None:
        statement = self._select().where(SchemaSource.id == source_id).with_for_update()
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def active_sources(self, *, limit: int = 200) -> Sequence[SchemaSource]:
        """Active, live sources by name (question understanding offers these)."""
        statement = (
            self._select().where(SchemaSource.is_active.is_(True)).order_by(func.lower(SchemaSource.name), SchemaSource.id).limit(limit)
        )
        return (await self.session.execute(statement)).scalars().all()

    async def name_taken(self, name: str, *, exclude_id: uuid.UUID | None = None) -> bool:
        statement = self._select().where(func.lower(SchemaSource.name) == name.lower())
        if exclude_id is not None:
            statement = statement.where(SchemaSource.id != exclude_id)
        return bool(await self.session.scalar(select(statement.exists())))

    async def search(self, query: SourceQuery, *, offset: int, limit: int) -> Page[SchemaSource]:
        statement = self._select()
        if query.search:
            pattern = _contains(query.search)
            statement = statement.where(
                or_(
                    func.lower(SchemaSource.name).like(pattern, escape="\\"),
                    func.lower(func.coalesce(SchemaSource.description, "")).like(pattern, escape="\\"),
                )
            )
        if query.database_type:
            statement = statement.where(SchemaSource.database_type == query.database_type)
        statement = _status(statement, SchemaSource.is_active, query.status)
        sort_column = {
            SourceSort.NAME: func.lower(SchemaSource.name),
            SourceSort.UPDATED_AT: SchemaSource.metadata_updated_at,
            SourceSort.CREATED_AT: SchemaSource.created_at,
            SourceSort.SCHEMA_VERSION: SchemaSource.schema_version,
        }[query.sort]
        return await _page(self, statement, (_ordered(sort_column, query.order), SchemaSource.id), offset, limit)

    async def counts(self, source_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, SourceCounts]:
        """Live table, column and relationship counts per source (one query each)."""
        ids = list(source_ids)
        if not ids:
            return {}
        tables = dict(
            (
                await self.session.execute(
                    select(SchemaTable.schema_source_id, func.count())
                    .where(SchemaTable.schema_source_id.in_(ids), SchemaTable.deleted_at.is_(None))
                    .group_by(SchemaTable.schema_source_id)
                )
            ).all()
        )
        columns = dict(
            (
                await self.session.execute(
                    select(SchemaTable.schema_source_id, func.count())
                    .join(SchemaColumn, SchemaColumn.table_id == SchemaTable.id)
                    .where(
                        SchemaTable.schema_source_id.in_(ids),
                        SchemaTable.deleted_at.is_(None),
                        SchemaColumn.deleted_at.is_(None),
                    )
                    .group_by(SchemaTable.schema_source_id)
                )
            ).all()
        )
        relationships = dict(
            (
                await self.session.execute(
                    select(SchemaRelationship.schema_source_id, func.count())
                    .where(SchemaRelationship.schema_source_id.in_(ids), SchemaRelationship.deleted_at.is_(None))
                    .group_by(SchemaRelationship.schema_source_id)
                )
            ).all()
        )
        return {
            source_id: SourceCounts(
                tables=int(tables.get(source_id, 0)),
                columns=int(columns.get(source_id, 0)),
                relationships=int(relationships.get(source_id, 0)),
            )
            for source_id in ids
        }


# --- tables -------------------------------------------------------------------------------------------


class SchemaTableRepository(BaseRepository[SchemaTable]):
    model = SchemaTable
    default_order_by = (SchemaTable.schema_name, SchemaTable.table_name, SchemaTable.id)

    async def for_source(self, source_id: uuid.UUID) -> Sequence[SchemaTable]:
        statement = self._ordered(self._select().where(SchemaTable.schema_source_id == source_id))
        return (await self.session.execute(statement)).scalars().all()

    async def search(self, source_id: uuid.UUID, query: TableQuery, *, offset: int, limit: int) -> Page[SchemaTable]:
        statement = self._select().where(SchemaTable.schema_source_id == source_id)
        if query.search:
            pattern = _contains(query.search)
            statement = statement.where(
                or_(
                    func.lower(SchemaTable.table_name).like(pattern, escape="\\"),
                    func.lower(func.coalesce(SchemaTable.description, "")).like(pattern, escape="\\"),
                    func.lower(func.coalesce(SchemaTable.business_purpose, "")).like(pattern, escape="\\"),
                )
            )
        if query.schema_name:
            statement = statement.where(func.lower(SchemaTable.schema_name) == query.schema_name.lower())
        statement = _status(statement, SchemaTable.is_active, query.status)
        sort_column = {
            TableSort.TABLE_NAME: func.lower(SchemaTable.table_name),
            TableSort.SCHEMA_NAME: func.lower(SchemaTable.schema_name),
            TableSort.UPDATED_AT: SchemaTable.updated_at,
            TableSort.CREATED_AT: SchemaTable.created_at,
        }[query.sort]
        order = [_ordered(sort_column, query.order)]
        if query.sort == TableSort.SCHEMA_NAME:
            order.append(func.lower(SchemaTable.table_name))
        return await _page(self, statement, (*order, SchemaTable.id), offset, limit)

    async def column_counts(self, table_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, int]:
        ids = list(table_ids)
        if not ids:
            return {}
        rows = await self.session.execute(
            select(SchemaColumn.table_id, func.count())
            .where(SchemaColumn.table_id.in_(ids), SchemaColumn.deleted_at.is_(None))
            .group_by(SchemaColumn.table_id)
        )
        return {table_id: int(count) for table_id, count in rows}

    async def relationship_counts(self, table_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, int]:
        ids = list(table_ids)
        if not ids:
            return {}
        counts: dict[uuid.UUID, int] = {}
        for column in (SchemaRelationship.source_table_id, SchemaRelationship.target_table_id):
            rows = await self.session.execute(
                select(column, func.count())
                .where(column.in_(ids), SchemaRelationship.deleted_at.is_(None))
                .group_by(column)
            )
            for table_id, count in rows:
                counts[table_id] = counts.get(table_id, 0) + int(count)
        return counts


# --- columns -----------------------------------------------------------------------------------------------


class SchemaColumnRepository(BaseRepository[SchemaColumn]):
    model = SchemaColumn
    default_order_by = (SchemaColumn.ordinal_position, SchemaColumn.column_name, SchemaColumn.id)

    async def for_tables(self, table_ids: Iterable[uuid.UUID]) -> Sequence[SchemaColumn]:
        ids = list(table_ids)
        if not ids:
            return []
        statement = self._ordered(self._select().where(SchemaColumn.table_id.in_(ids)))
        return (await self.session.execute(statement)).scalars().all()

    async def search(self, table_id: uuid.UUID, query: ColumnQuery, *, offset: int, limit: int) -> Page[SchemaColumn]:
        statement = self._select().where(SchemaColumn.table_id == table_id)
        if query.search:
            pattern = _contains(query.search)
            statement = statement.where(
                or_(
                    func.lower(SchemaColumn.column_name).like(pattern, escape="\\"),
                    func.lower(func.coalesce(SchemaColumn.description, "")).like(pattern, escape="\\"),
                    func.lower(func.coalesce(SchemaColumn.business_meaning, "")).like(pattern, escape="\\"),
                )
            )
        for flag, column in (
            (query.primary_key, SchemaColumn.is_primary_key),
            (query.foreign_key, SchemaColumn.is_foreign_key),
            (query.sensitive, SchemaColumn.is_sensitive),
        ):
            if flag is not None:
                statement = statement.where(column.is_(flag))
        statement = _status(statement, SchemaColumn.is_active, query.status)
        sort_column = {
            ColumnSort.ORDINAL_POSITION: SchemaColumn.ordinal_position,
            ColumnSort.COLUMN_NAME: func.lower(SchemaColumn.column_name),
            ColumnSort.DATA_TYPE: SchemaColumn.data_type,
        }[query.sort]
        return await _page(
            self, statement, (_ordered(sort_column, query.order), func.lower(SchemaColumn.column_name), SchemaColumn.id),
            offset, limit,
        )

    async def next_position(self, table_id: uuid.UUID) -> int:
        current = await self.session.scalar(
            select(func.max(SchemaColumn.ordinal_position)).where(
                SchemaColumn.table_id == table_id, SchemaColumn.deleted_at.is_(None)
            )
        )
        return 0 if current is None else int(current) + 1

    async def soft_delete_for_tables(self, table_ids: Iterable[uuid.UUID]) -> None:
        ids = list(table_ids)
        if ids:
            await self.session.execute(
                update(SchemaColumn)
                .where(SchemaColumn.table_id.in_(ids), SchemaColumn.deleted_at.is_(None))
                .values(deleted_at=func.now(), updated_at=func.now())
                .execution_options(synchronize_session=False)
            )


# --- relationships -------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RelationshipRow:
    relationship: SchemaRelationship
    source_table: SchemaTable
    source_column: SchemaColumn
    target_table: SchemaTable
    target_column: SchemaColumn


class SchemaRelationshipRepository(BaseRepository[SchemaRelationship]):
    model = SchemaRelationship
    default_order_by = (SchemaRelationship.created_at, SchemaRelationship.id)

    async def for_source(self, source_id: uuid.UUID) -> Sequence[SchemaRelationship]:
        statement = self._ordered(self._select().where(SchemaRelationship.schema_source_id == source_id))
        return (await self.session.execute(statement)).scalars().all()

    def _with_endpoints(self) -> tuple[Select[Any], Any, Any, Any, Any]:
        source_table, target_table = aliased(SchemaTable), aliased(SchemaTable)
        source_column, target_column = aliased(SchemaColumn), aliased(SchemaColumn)
        statement = (
            select(SchemaRelationship, source_table, source_column, target_table, target_column)
            .join(source_table, source_table.id == SchemaRelationship.source_table_id)
            .join(source_column, source_column.id == SchemaRelationship.source_column_id)
            .join(target_table, target_table.id == SchemaRelationship.target_table_id)
            .join(target_column, target_column.id == SchemaRelationship.target_column_id)
            .where(SchemaRelationship.deleted_at.is_(None))
        )
        return statement, source_table, source_column, target_table, target_column

    async def search(
        self, source_id: uuid.UUID, query: RelationshipQuery, *, offset: int, limit: int
    ) -> tuple[list[RelationshipRow], int]:
        validate_pagination(offset, limit)
        statement, source_table, source_column, target_table, target_column = self._with_endpoints()
        statement = statement.where(SchemaRelationship.schema_source_id == source_id)
        if query.table_id is not None:
            statement = statement.where(
                or_(
                    SchemaRelationship.source_table_id == query.table_id,
                    SchemaRelationship.target_table_id == query.table_id,
                )
            )
        if query.relationship_type:
            statement = statement.where(SchemaRelationship.relationship_type == query.relationship_type)
        if query.search:
            pattern = _contains(query.search)
            statement = statement.where(
                or_(
                    *(
                        func.lower(column).like(pattern, escape="\\")
                        for column in (
                            source_table.table_name, source_column.column_name,
                            target_table.table_name, target_column.column_name,
                            func.coalesce(SchemaRelationship.description, ""),
                        )
                    )
                )
            )
        statement = _status(statement, SchemaRelationship.is_active, query.status)
        total = int(await self.session.scalar(select(func.count()).select_from(statement.subquery())) or 0)
        rows = await self.session.execute(
            statement.order_by(
                func.lower(source_table.table_name), func.lower(source_column.column_name),
                func.lower(target_table.table_name), SchemaRelationship.id,
            ).offset(offset).limit(limit)
        )
        return [RelationshipRow(*row) for row in rows], total

    async def get_row(self, relationship_id: uuid.UUID) -> RelationshipRow | None:
        statement, *_ = self._with_endpoints()
        row = (await self.session.execute(statement.where(SchemaRelationship.id == relationship_id))).first()
        return RelationshipRow(*row) if row else None

    async def get_for_update(self, relationship_id: uuid.UUID) -> SchemaRelationship | None:
        statement = self._select().where(SchemaRelationship.id == relationship_id).with_for_update()
        return (await self.session.execute(statement)).scalar_one_or_none()

    async def referencing_tables(self, table_ids: Iterable[uuid.UUID]) -> Sequence[SchemaRelationship]:
        ids = list(table_ids)
        statement = self._select().where(
            or_(SchemaRelationship.source_table_id.in_(ids), SchemaRelationship.target_table_id.in_(ids))
        )
        return (await self.session.execute(statement)).scalars().all()

    async def referencing_columns(self, column_ids: Iterable[uuid.UUID]) -> Sequence[SchemaRelationship]:
        ids = list(column_ids)
        statement = self._select().where(
            or_(SchemaRelationship.source_column_id.in_(ids), SchemaRelationship.target_column_id.in_(ids))
        )
        return (await self.session.execute(statement)).scalars().all()

    async def soft_delete_for_source(self, source_id: uuid.UUID) -> None:
        await self.session.execute(
            update(SchemaRelationship)
            .where(SchemaRelationship.schema_source_id == source_id, SchemaRelationship.deleted_at.is_(None))
            .values(deleted_at=func.now(), updated_at=func.now())
            .execution_options(synchronize_session=False)
        )


# --- change events -----------------------------------------------------------------------------------------------


class SchemaChangeRepository(BaseRepository[SchemaChangeEvent]):
    model = SchemaChangeEvent
    default_order_by = (SchemaChangeEvent.created_at.desc(), SchemaChangeEvent.id)

    async def history(
        self, source_id: uuid.UUID, *, offset: int, limit: int, versions_only: bool = False
    ) -> tuple[list[tuple[SchemaChangeEvent, str | None]], int]:
        validate_pagination(offset, limit)
        conditions = [SchemaChangeEvent.schema_source_id == source_id]
        if versions_only:
            conditions.append(SchemaChangeEvent.version_incremented.is_(True))
        total = int(
            await self.session.scalar(select(func.count()).select_from(SchemaChangeEvent).where(and_(*conditions))) or 0
        )
        rows = await self.session.execute(
            select(SchemaChangeEvent, User.full_name)
            .outerjoin(User, User.id == SchemaChangeEvent.actor_user_id)
            .where(*conditions)
            .order_by(SchemaChangeEvent.created_at.desc(), SchemaChangeEvent.schema_version.desc(), SchemaChangeEvent.id)
            .offset(offset)
            .limit(limit)
        )
        return [(event, name) for event, name in rows], total

    async def last_version_change(self, source_id: uuid.UUID) -> tuple[SchemaChangeEvent, str | None] | None:
        items, _ = await self.history(source_id, offset=0, limit=1, versions_only=True)
        return items[0] if items else None

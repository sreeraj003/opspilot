"""Schema registry: register and maintain external schema metadata (Module 06).

Route-level permission checks happen first (``require_permission``); this
service enforces the rules that hold whatever the caller may do:

* **Validation outside the routes.** Every change is checked by
  ``app.schema_registry.validation`` against the whole resulting schema
  (unique names, identifiers, data types, primary keys, relationship
  endpoints, duplicates, limits). Only errors the change *introduces* block it.
* **One change at a time per schema.** Writes lock the schema source row, so
  versions increase strictly and validation sees committed state.
* **Versioning.** ``schema_version`` increases by one for each committed
  change to tables, columns, relationships, the business glossary, or the
  source's database type or dialect, and ``metadata_updated_at`` records when.
  Reads, exports, no-op updates and edits to the source's own name,
  description or active flag do not change the version.
* **Audit.** Each change (and each export) writes a ``SchemaChangeEvent`` in
  the same transaction, with a one-line summary and the changed fields.
* **Safe deletion.** Deletes are soft. A table or column used by a
  relationship can only be deleted together with those relationships
  (``cascade_relationships``), in the same transaction.
* **Metadata only.** Nothing here connects to an external database, stores
  credentials or executes SQL from imported metadata.
"""

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, InvalidInputError, NotFoundError
from app.database.transaction import transaction
from app.models.schema_registry import (
    SchemaChangeEvent,
    SchemaChangeType,
    SchemaColumn,
    SchemaRelationship,
    SchemaSource,
    SchemaTable,
)
from app.models.user import User
from app.repositories.base_repository import Page
from app.repositories.schema_registry_repository import (
    ColumnQuery,
    RelationshipQuery,
    RelationshipRow,
    SchemaChangeRepository,
    SchemaColumnRepository,
    SchemaRelationshipRepository,
    SchemaSourceRepository,
    SchemaTableRepository,
    SourceCounts,
    SourceQuery,
    TableQuery,
)
from app.schema_registry.importer import (
    CREATE,
    REMOVE,
    UPDATE,
    ExistingColumn,
    ExistingRelationship,
    ExistingSchema,
    ExistingTable,
    ImportPlan,
    plan_import,
)
from app.schema_registry.representation import RepresentationFilter, build_representation
from app.schema_registry.validation import (
    ERROR,
    ColumnState,
    RelationshipState,
    SchemaLimits,
    SchemaState,
    TableState,
    ValidationIssue,
    issues_as_dicts,
    sort_issues,
    validate_schema_state,
)
from app.schemas.schema_registry import (
    AiSchemaRepresentation,
    SchemaColumnCreate,
    SchemaColumnUpdate,
    SchemaImportDocument,
    SchemaImportOptions,
    SchemaRelationshipCreate,
    SchemaRelationshipUpdate,
    SchemaSourceCreate,
    SchemaSourceUpdate,
    SchemaTableCreate,
    SchemaTableUpdate,
)

logger = logging.getLogger("app.schema_registry")

_UNIQUE_VIOLATION = "23505"
_MAX_REPORTED_CHANGES = 1000
_MAX_AUDITED_CHANGES = 200


def utcnow() -> datetime:
    return datetime.now(UTC)


# --- errors --------------------------------------------------------------------------------------------


class SchemaNotFoundError(NotFoundError):
    message = "The schema was not found."


class TableNotFoundError(NotFoundError):
    message = "The table was not found."


class ColumnNotFoundError(NotFoundError):
    message = "The column was not found."


class RelationshipNotFoundError(NotFoundError):
    message = "The relationship was not found."


class SchemaValidationError(InvalidInputError):
    """The change would make the schema metadata invalid. ``details`` lists every issue."""

    code = "SCHEMA_VALIDATION_FAILED"
    message = "The schema metadata is not valid."

    def __init__(self, issues: Sequence[ValidationIssue], message: str | None = None) -> None:
        ordered = sort_issues(issues)
        super().__init__(message or (ordered[0].message if ordered else None), details=issues_as_dicts(ordered))


class InUseError(ConflictError):
    code = "IN_USE"


class VersionConflictError(ConflictError):
    code = "SCHEMA_VERSION_CONFLICT"


# --- results ---------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TablePageData:
    page: Page[SchemaTable]
    column_counts: dict[uuid.UUID, int]
    relationship_counts: dict[uuid.UUID, int]


@dataclass(frozen=True, slots=True)
class LoadedSchema:
    tables: list[SchemaTable]
    columns: list[SchemaColumn]
    relationships: list[SchemaRelationship]

    def columns_of(self, table_id: uuid.UUID) -> list[SchemaColumn]:
        return [c for c in self.columns if c.table_id == table_id]


@dataclass(frozen=True, slots=True)
class ImportOutcome:
    strategy: str
    dry_run: bool
    applied: bool
    version_before: int
    version_after: int
    plan: ImportPlan | None
    errors: list[ValidationIssue]

    def as_result(self) -> dict[str, Any]:
        plan = self.plan
        changes = plan.changes() if plan else []
        warnings = plan.warnings if plan else []
        empty = {"created": 0, "updated": 0, "unchanged": 0, "removed": 0}
        return {
            "strategy": self.strategy,
            "dry_run": self.dry_run,
            "applied": self.applied,
            "valid": not self.errors,
            "schema_version_before": self.version_before,
            "schema_version_after": self.version_after,
            "version_incremented": self.version_after != self.version_before,
            "counts": plan.counts() if plan else {k: empty for k in ("tables", "columns", "relationships", "glossary_terms")},
            "changes": changes[:_MAX_REPORTED_CHANGES],
            "changes_truncated": len(changes) > _MAX_REPORTED_CHANGES,
            "errors": issues_as_dicts(sort_issues(self.errors)),
            "warnings": issues_as_dicts(sort_issues(warnings)),
        }


# --- state helpers -------------------------------------------------------------------------------------------------


def _state(loaded: LoadedSchema) -> SchemaState:
    by_table: dict[uuid.UUID, list[SchemaColumn]] = {}
    for column in loaded.columns:
        by_table.setdefault(column.table_id, []).append(column)
    return SchemaState(
        tables=[
            TableState(
                key=str(table.id),
                schema_name=table.schema_name,
                table_name=table.table_name,
                is_active=table.is_active,
                columns=[
                    ColumnState(
                        key=str(c.id),
                        column_name=c.column_name,
                        data_type=c.data_type,
                        is_primary_key=c.is_primary_key,
                        is_foreign_key=c.is_foreign_key,
                        is_nullable=c.is_nullable,
                        is_sensitive=c.is_sensitive,
                        is_active=c.is_active,
                    )
                    for c in by_table.get(table.id, [])
                ],
            )
            for table in loaded.tables
        ],
        relationships=[
            RelationshipState(
                key=str(r.id),
                source_table_key=str(r.source_table_id),
                source_column_key=str(r.source_column_id),
                target_table_key=str(r.target_table_id),
                target_column_key=str(r.target_column_id),
                relationship_type=r.relationship_type,
                is_active=r.is_active,
                location=("relationships", str(r.id)),
            )
            for r in loaded.relationships
        ],
    )


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def _diff(entity: Any, changes: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {name: {"from": _jsonable(getattr(entity, name)), "to": _jsonable(value)} for name, value in changes.items()}


def _pydantic_issues(exc: ValidationError, prefix: tuple[str, ...]) -> list[ValidationIssue]:
    codes = {"missing": "missing_field", "extra_forbidden": "unknown_field", "value_error": "invalid_value"}
    issues = []
    for error in exc.errors(include_url=False, include_input=False):
        message = str(error.get("msg", "Invalid value."))
        if message.startswith("Value error, "):
            message = message[len("Value error, "):]
        error_type = str(error.get("type", "invalid_value"))
        issues.append(
            ValidationIssue(codes.get(error_type, error_type), message, (*prefix, *error.get("loc", ())), ERROR)
        )
    return issues


class SchemaRegistryService:
    def __init__(self, session: AsyncSession, *, limits: SchemaLimits | None = None) -> None:
        self._session = session
        self._limits = limits or SchemaLimits()
        self._sources = SchemaSourceRepository(session)
        self._tables = SchemaTableRepository(session)
        self._columns = SchemaColumnRepository(session)
        self._relationships = SchemaRelationshipRepository(session)
        self._changes = SchemaChangeRepository(session)

    # =========================================================================================
    # schema sources
    # =========================================================================================

    async def list_sources(
        self, query: SourceQuery, *, offset: int, limit: int
    ) -> tuple[Page[SchemaSource], dict[uuid.UUID, SourceCounts]]:
        page = await self._sources.search(query, offset=offset, limit=limit)
        return page, await self._sources.counts(s.id for s in page.items)

    async def get_source(self, source_id: uuid.UUID) -> tuple[SchemaSource, SourceCounts]:
        source = await self._source(source_id)
        counts = await self._sources.counts([source.id])
        return source, counts[source.id]

    async def create_source(self, data: SchemaSourceCreate, actor: User) -> SchemaSource:
        try:
            async with transaction(self._session):
                if await self._sources.name_taken(data.name):
                    raise ConflictError("A schema with this name already exists.")
                source = await self._sources.create(
                    name=data.name,
                    description=data.description,
                    database_type=data.database_type,
                    dialect=data.dialect or data.database_type,
                    is_active=data.is_active,
                    business_glossary=[t.model_dump() for t in data.business_glossary],
                    schema_version=1,
                    created_by=actor.id,
                )
                await self._record(
                    source, SchemaChangeType.SCHEMA_CREATED, "schema", source.id, actor,
                    f"Registered schema '{source.name}'",
                    {"name": source.name, "database_type": source.database_type, "dialect": source.dialect},
                    bump=False,
                )
        except IntegrityError as exc:
            raise _conflict(exc, "A schema with this name already exists.") from None
        return source

    async def update_source(self, source_id: uuid.UUID, data: SchemaSourceUpdate, actor: User) -> SchemaSource:
        requested = data.model_dump(exclude_unset=True)
        for required in ("name", "database_type", "dialect", "is_active", "business_glossary"):
            if required in requested and requested[required] is None:
                raise InvalidInputError(f"{required} cannot be removed.")
        try:
            async with transaction(self._session):
                source = await self._source(source_id, for_update=True)
                changes = {k: v for k, v in requested.items() if getattr(source, k) != v}
                if not changes:
                    return source
                if "name" in changes and await self._sources.name_taken(changes["name"], exclude_id=source.id):
                    raise ConflictError("A schema with this name already exists.")
                # Changes that alter what AI and SQL validation see produce a new version.
                meaningful = sorted(set(changes) & {"database_type", "dialect", "business_glossary"})
                details: dict[str, Any] = {"changed_fields": sorted(changes)}
                details.update({k: v for k, v in _diff(source, changes).items() if k != "business_glossary"})
                if "business_glossary" in changes:
                    details["glossary_terms"] = {"from": len(source.business_glossary), "to": len(changes["business_glossary"])}
                for name, value in changes.items():
                    setattr(source, name, value)
                await self._session.flush()
                await self._record(
                    source, SchemaChangeType.SCHEMA_UPDATED, "schema", source.id, actor,
                    f"Updated schema '{source.name}' ({', '.join(sorted(changes))})", details, bump=bool(meaningful),
                )
        except IntegrityError as exc:
            raise _conflict(exc, "A schema with this name already exists.") from None
        return source

    async def delete_source(self, source_id: uuid.UUID, actor: User) -> None:
        async with transaction(self._session):
            source = await self._source(source_id, for_update=True)
            loaded = await self._load(source.id)
            await self._relationships.soft_delete_for_source(source.id)
            await self._columns.soft_delete_for_tables(t.id for t in loaded.tables)
            for table in loaded.tables:
                table.mark_deleted()
            source.mark_deleted()
            await self._session.flush()
            await self._record(
                source, SchemaChangeType.SCHEMA_DELETED, "schema", source.id, actor,
                f"Deleted schema '{source.name}'",
                {
                    "name": source.name,
                    "tables": len(loaded.tables),
                    "columns": len(loaded.columns),
                    "relationships": len(loaded.relationships),
                },
                bump=False,
            )

    # =========================================================================================
    # tables
    # =========================================================================================

    async def list_tables(self, source_id: uuid.UUID, query: TableQuery, *, offset: int, limit: int) -> TablePageData:
        await self._source(source_id)
        page = await self._tables.search(source_id, query, offset=offset, limit=limit)
        ids = [t.id for t in page.items]
        return TablePageData(page, await self._tables.column_counts(ids), await self._tables.relationship_counts(ids))

    async def get_table(self, table_id: uuid.UUID) -> tuple[SchemaTable, Sequence[SchemaColumn], int]:
        table = await self._table(table_id)
        columns = await self._columns.for_tables([table.id])
        relationships = (await self._tables.relationship_counts([table.id])).get(table.id, 0)
        return table, columns, relationships

    async def create_table(self, source_id: uuid.UUID, data: SchemaTableCreate, actor: User) -> SchemaTable:
        try:
            async with transaction(self._session):
                source = await self._source(source_id, for_update=True)
                loaded = await self._load(source.id)
                table = SchemaTable(
                    id=uuid.uuid4(),
                    schema_source_id=source.id,
                    schema_name=data.schema_name,
                    table_name=data.table_name,
                    description=data.description,
                    business_purpose=data.business_purpose,
                    is_active=data.is_active,
                )
                columns = [
                    self._new_column(table.id, column, position)
                    for position, column in enumerate(data.columns)
                ]
                self._check(
                    loaded,
                    LoadedSchema([*loaded.tables, table], [*loaded.columns, *columns], loaded.relationships),
                )
                # No ORM relationships are mapped, so flush parents before children.
                self._session.add(table)
                await self._session.flush()
                self._session.add_all(columns)
                await self._session.flush()
                suffix = f" with {len(columns)} column{'s' if len(columns) != 1 else ''}" if columns else ""
                await self._record(
                    source, SchemaChangeType.TABLE_CREATED, "table", table.id, actor,
                    f"Added table {table.qualified_name}{suffix}",
                    {"table": table.qualified_name, "columns": [c.column_name for c in columns][:_MAX_AUDITED_CHANGES]},
                )
        except IntegrityError as exc:
            raise _conflict(exc, "A table with this name already exists in this schema.") from None
        return table

    async def update_table(self, table_id: uuid.UUID, data: SchemaTableUpdate, actor: User) -> SchemaTable:
        requested = data.model_dump(exclude_unset=True)
        for required in ("schema_name", "table_name", "is_active"):
            if required in requested and requested[required] is None:
                raise InvalidInputError(f"{required} cannot be removed.")
        try:
            async with transaction(self._session):
                table = await self._table(table_id)
                source = await self._source(table.schema_source_id, for_update=True)
                await self._session.refresh(table)  # current values, now that the schema is locked
                changes = {k: v for k, v in requested.items() if getattr(table, k) != v}
                if not changes:
                    return table
                loaded = await self._load(source.id)
                before = validate_schema_state(_state(loaded), self._limits)
                details = {"table": table.qualified_name, "changes": _diff(table, changes)}
                for name, value in changes.items():
                    setattr(table, name, value)
                self._raise_new_errors(before, validate_schema_state(_state(loaded), self._limits))
                await self._session.flush()
                await self._record(
                    source, SchemaChangeType.TABLE_UPDATED, "table", table.id, actor,
                    f"Updated table {table.qualified_name} ({', '.join(sorted(changes))})", details,
                )
        except IntegrityError as exc:
            raise _conflict(exc, "A table with this name already exists in this schema.") from None
        return table

    async def delete_table(self, table_id: uuid.UUID, actor: User, *, cascade_relationships: bool = False) -> None:
        async with transaction(self._session):
            table = await self._table(table_id)
            source = await self._source(table.schema_source_id, for_update=True)
            relationships = await self._relationships.referencing_tables([table.id])
            await self._ensure_removable(relationships, f"Table {table.qualified_name}", cascade_relationships)
            for relationship in relationships:
                relationship.mark_deleted()
            await self._columns.soft_delete_for_tables([table.id])
            table.mark_deleted()
            await self._session.flush()
            suffix = f" and {len(relationships)} relationship{'s' if len(relationships) != 1 else ''}" if relationships else ""
            await self._record(
                source, SchemaChangeType.TABLE_DELETED, "table", table.id, actor,
                f"Deleted table {table.qualified_name}{suffix}",
                {"table": table.qualified_name, "relationships_removed": [str(r.id) for r in relationships]},
            )

    # =========================================================================================
    # columns
    # =========================================================================================

    async def list_columns(self, table_id: uuid.UUID, query: ColumnQuery, *, offset: int, limit: int) -> Page[SchemaColumn]:
        await self._table(table_id)
        return await self._columns.search(table_id, query, offset=offset, limit=limit)

    async def create_column(self, table_id: uuid.UUID, data: SchemaColumnCreate, actor: User) -> SchemaColumn:
        try:
            async with transaction(self._session):
                table = await self._table(table_id)
                source = await self._source(table.schema_source_id, for_update=True)
                loaded = await self._load(source.id)
                position = data.ordinal_position
                if position is None:
                    position = await self._columns.next_position(table.id)
                column = self._new_column(table.id, data, position)
                self._check(loaded, LoadedSchema(loaded.tables, [*loaded.columns, column], loaded.relationships))
                self._session.add(column)
                await self._session.flush()
                await self._record(
                    source, SchemaChangeType.COLUMN_CREATED, "column", column.id, actor,
                    f"Added column {table.qualified_name}.{column.column_name} ({column.data_type})",
                    {"table": table.qualified_name, "column": column.column_name, "data_type": column.data_type},
                )
        except IntegrityError as exc:
            raise _conflict(exc, "A column with this name already exists in this table.") from None
        return column

    async def update_column(self, column_id: uuid.UUID, data: SchemaColumnUpdate, actor: User) -> SchemaColumn:
        requested = data.model_dump(exclude_unset=True)
        for required in COLUMN_REQUIRED_FIELDS:
            if required in requested and requested[required] is None:
                raise InvalidInputError(f"{required} cannot be removed.")
        try:
            async with transaction(self._session):
                column = await self._column(column_id)
                table = await self._table(column.table_id)
                source = await self._source(table.schema_source_id, for_update=True)
                await self._session.refresh(column)
                if requested.get("is_primary_key") and "is_nullable" not in requested:
                    requested["is_nullable"] = False  # a primary key is NOT NULL unless told otherwise
                changes = {k: v for k, v in requested.items() if getattr(column, k) != v}
                if not changes:
                    return column
                loaded = await self._load(source.id)
                # The loaded column is the same object (identity map), so the change is visible in the state.
                before = validate_schema_state(_state(loaded), self._limits)
                details = {"table": table.qualified_name, "column": column.column_name, "changes": _diff(column, changes)}
                for name, value in changes.items():
                    setattr(column, name, value)
                self._raise_new_errors(before, validate_schema_state(_state(loaded), self._limits))
                await self._session.flush()
                await self._record(
                    source, SchemaChangeType.COLUMN_UPDATED, "column", column.id, actor,
                    f"Updated column {table.qualified_name}.{column.column_name} ({', '.join(sorted(changes))})",
                    details,
                )
        except IntegrityError as exc:
            raise _conflict(exc, "A column with this name already exists in this table.") from None
        return column

    async def delete_column(self, column_id: uuid.UUID, actor: User, *, cascade_relationships: bool = False) -> None:
        async with transaction(self._session):
            column = await self._column(column_id)
            table = await self._table(column.table_id)
            source = await self._source(table.schema_source_id, for_update=True)
            relationships = await self._relationships.referencing_columns([column.id])
            name = f"{table.qualified_name}.{column.column_name}"
            await self._ensure_removable(relationships, f"Column {name}", cascade_relationships)
            for relationship in relationships:
                relationship.mark_deleted()
            column.mark_deleted()
            await self._session.flush()
            suffix = f" and {len(relationships)} relationship{'s' if len(relationships) != 1 else ''}" if relationships else ""
            await self._record(
                source, SchemaChangeType.COLUMN_DELETED, "column", column.id, actor,
                f"Deleted column {name}{suffix}",
                {"table": table.qualified_name, "column": column.column_name,
                 "relationships_removed": [str(r.id) for r in relationships]},
            )

    # =========================================================================================
    # relationships
    # =========================================================================================

    async def list_relationships(
        self, source_id: uuid.UUID, query: RelationshipQuery, *, offset: int, limit: int
    ) -> tuple[list[RelationshipRow], int]:
        await self._source(source_id)
        return await self._relationships.search(source_id, query, offset=offset, limit=limit)

    async def get_relationship(self, relationship_id: uuid.UUID) -> RelationshipRow:
        row = await self._relationships.get_row(relationship_id)
        if row is None:
            raise RelationshipNotFoundError()
        return row

    async def create_relationship(
        self, source_id: uuid.UUID, data: SchemaRelationshipCreate, actor: User
    ) -> RelationshipRow:
        try:
            async with transaction(self._session):
                source = await self._source(source_id, for_update=True)
                loaded = await self._load(source.id)
                relationship = SchemaRelationship(
                    id=uuid.uuid4(),
                    schema_source_id=source.id,
                    source_table_id=data.source_table_id,
                    source_column_id=data.source_column_id,
                    target_table_id=data.target_table_id,
                    target_column_id=data.target_column_id,
                    relationship_type=data.relationship_type,
                    description=data.description,
                    is_active=data.is_active,
                )
                await self._check_endpoints(source, loaded, relationship)
                self._check(loaded, LoadedSchema(loaded.tables, loaded.columns, [*loaded.relationships, relationship]))
                self._session.add(relationship)
                await self._session.flush()
                row = await self.get_relationship(relationship.id)
                await self._record(
                    source, SchemaChangeType.RELATIONSHIP_CREATED, "relationship", relationship.id, actor,
                    f"Added relationship {_row_label(row)} ({relationship.relationship_type})",
                    {"relationship": _row_label(row), "relationship_type": relationship.relationship_type},
                )
        except IntegrityError as exc:
            raise _conflict(exc, "This relationship is already registered.") from None
        return row

    async def update_relationship(
        self, relationship_id: uuid.UUID, data: SchemaRelationshipUpdate, actor: User
    ) -> RelationshipRow:
        requested = data.model_dump(exclude_unset=True)
        for name, value in requested.items():
            if value is None and name != "description":
                raise InvalidInputError(f"{name} cannot be removed.")
        try:
            async with transaction(self._session):
                relationship = await self._relationships.get(relationship_id)
                if relationship is None:
                    raise RelationshipNotFoundError()
                source = await self._source(relationship.schema_source_id, for_update=True)
                await self._session.refresh(relationship)
                changes = {k: v for k, v in requested.items() if getattr(relationship, k) != v}
                if not changes:
                    return await self.get_relationship(relationship.id)
                loaded = await self._load(source.id)
                before = validate_schema_state(_state(loaded), self._limits)
                old_label = _row_label(await self.get_relationship(relationship.id))
                details: dict[str, Any] = {"changes": _diff(relationship, changes)}
                for name, value in changes.items():
                    setattr(relationship, name, value)
                if changes.keys() & {"source_table_id", "source_column_id", "target_table_id", "target_column_id"} or (
                    changes.get("is_active") is True
                ):
                    await self._check_endpoints(source, loaded, relationship)
                self._raise_new_errors(before, validate_schema_state(_state(loaded), self._limits))
                await self._session.flush()
                row = await self.get_relationship(relationship.id)
                details["relationship"] = _row_label(row)
                if _row_label(row) != old_label:
                    details["previous"] = old_label
                await self._record(
                    source, SchemaChangeType.RELATIONSHIP_UPDATED, "relationship", relationship.id, actor,
                    f"Updated relationship {_row_label(row)} ({', '.join(sorted(changes))})", details,
                )
        except IntegrityError as exc:
            raise _conflict(exc, "This relationship is already registered.") from None
        return row

    async def delete_relationship(self, relationship_id: uuid.UUID, actor: User) -> None:
        async with transaction(self._session):
            row = await self.get_relationship(relationship_id)
            source = await self._source(row.relationship.schema_source_id, for_update=True)
            row.relationship.mark_deleted()
            await self._session.flush()
            await self._record(
                source, SchemaChangeType.RELATIONSHIP_DELETED, "relationship", row.relationship.id, actor,
                f"Removed relationship {_row_label(row)}", {"relationship": _row_label(row)},
            )

    # =========================================================================================
    # metadata: outline, summary, version, history, representation
    # =========================================================================================

    async def outline(self, source_id: uuid.UUID) -> tuple[SchemaSource, LoadedSchema]:
        source = await self._source(source_id)
        return source, await self._load(source.id)

    async def summary(self, source_id: uuid.UUID) -> dict[str, Any]:
        source = await self._source(source_id)
        loaded = await self._load(source.id)
        report = validate_schema_state(_state(loaded), self._limits)
        active_tables = {t.id for t in loaded.tables if t.is_active}
        return {
            "schema_id": source.id,
            "name": source.name,
            "database_type": source.database_type,
            "dialect": source.dialect,
            "is_active": source.is_active,
            "schema_version": source.schema_version,
            "metadata_updated_at": source.metadata_updated_at,
            "counts": {
                "tables": len(loaded.tables),
                "active_tables": len(active_tables),
                "columns": len(loaded.columns),
                "active_columns": sum(1 for c in loaded.columns if c.is_active and c.table_id in active_tables),
                "primary_key_columns": sum(1 for c in loaded.columns if c.is_primary_key),
                "foreign_key_columns": sum(1 for c in loaded.columns if c.is_foreign_key),
                "sensitive_columns": sum(1 for c in loaded.columns if c.is_sensitive),
                "relationships": len(loaded.relationships),
                "active_relationships": sum(1 for r in loaded.relationships if r.is_active),
                "schema_namespaces": sorted({t.schema_name for t in loaded.tables}, key=str.lower),
                "glossary_terms": len(source.business_glossary or []),
            },
            "issues": issues_as_dicts(sort_issues(report.issues)),
        }

    async def version(self, source_id: uuid.UUID) -> tuple[SchemaSource, tuple[SchemaChangeEvent, str | None] | None]:
        source = await self._source(source_id)
        return source, await self._changes.last_version_change(source.id)

    async def history(
        self, source_id: uuid.UUID, *, offset: int, limit: int
    ) -> tuple[list[tuple[SchemaChangeEvent, str | None]], int]:
        source = await self._source(source_id)
        return await self._changes.history(source.id, offset=offset, limit=limit)

    async def representation(
        self, source_id: uuid.UUID, selection: RepresentationFilter | None = None
    ) -> AiSchemaRepresentation:
        """The AI-ready representation of one schema source (see ``app.schema_registry.representation``)."""
        source = await self._source(source_id)
        loaded = await self._load(source.id)
        return build_representation(source, loaded.tables, loaded.columns, loaded.relationships, selection)

    # =========================================================================================
    # export and import
    # =========================================================================================

    async def export(self, source_id: uuid.UUID, actor: User) -> dict[str, Any]:
        async with transaction(self._session):
            source = await self._source(source_id)
            loaded = await self._load(source.id)
            tables_by_id = {t.id: t for t in loaded.tables}
            columns_by_id = {c.id: c for c in loaded.columns}
            namespaces = {t.schema_name for t in loaded.tables}
            document: dict[str, Any] = {
                "format_version": 1,
                "source": {
                    "name": source.name,
                    "description": source.description,
                    "database_type": source.database_type,
                    "dialect": source.dialect,
                },
                "schema_version": source.schema_version,
                "exported_at": utcnow().isoformat(),
                "schema_name": next(iter(namespaces)) if len(namespaces) == 1 else None,
                "tables": [
                    {
                        "schema_name": t.schema_name,
                        "table_name": t.table_name,
                        "description": t.description,
                        "business_purpose": t.business_purpose,
                        "is_active": t.is_active,
                        "columns": [
                            {
                                "column_name": c.column_name,
                                "data_type": c.data_type,
                                "description": c.description,
                                "business_meaning": c.business_meaning,
                                "is_primary_key": c.is_primary_key,
                                "is_foreign_key": c.is_foreign_key,
                                "is_nullable": c.is_nullable,
                                "is_sensitive": c.is_sensitive,
                                "is_active": c.is_active,
                            }
                            for c in loaded.columns_of(t.id)
                        ],
                    }
                    for t in loaded.tables
                ],
                "relationships": [
                    {
                        "source_schema": tables_by_id[r.source_table_id].schema_name,
                        "source_table": tables_by_id[r.source_table_id].table_name,
                        "source_column": columns_by_id[r.source_column_id].column_name,
                        "target_schema": tables_by_id[r.target_table_id].schema_name,
                        "target_table": tables_by_id[r.target_table_id].table_name,
                        "target_column": columns_by_id[r.target_column_id].column_name,
                        "relationship_type": r.relationship_type,
                        "description": r.description,
                        "is_active": r.is_active,
                    }
                    for r in loaded.relationships
                    if r.source_table_id in tables_by_id and r.target_table_id in tables_by_id
                    and r.source_column_id in columns_by_id and r.target_column_id in columns_by_id
                ],
                "business_glossary": list(source.business_glossary or []),
            }
            await self._record(
                source, SchemaChangeType.SCHEMA_EXPORTED, "schema", source.id, actor,
                f"Exported schema '{source.name}' (version {source.schema_version})",
                {"tables": len(loaded.tables), "columns": len(loaded.columns), "relationships": len(loaded.relationships)},
                bump=False,
            )
        return document

    async def import_schema(self, source_id: uuid.UUID, options: SchemaImportOptions, actor: User) -> dict[str, Any]:
        """Validate, preview (``dry_run``) or apply a JSON import in one transaction.

        A dry run always answers with a result (``valid`` false and the errors
        when the document is not acceptable). A real import with errors raises
        ``SchemaValidationError`` and changes nothing.
        """
        source = await self._source(source_id)
        version = source.schema_version
        try:
            document = SchemaImportDocument.model_validate(options.document)
        except ValidationError as exc:
            issues = _pydantic_issues(exc, ("document",))
            if options.dry_run:
                return ImportOutcome(options.strategy, True, False, version, version, None, issues).as_result()
            raise SchemaValidationError(issues, "The import document is not valid.") from None
        if options.strategy == "replace" and not options.confirm_replace and not options.dry_run:
            raise InvalidInputError(
                "The replace strategy removes registered tables, columns and relationships that the document does "
                "not list. Confirm it explicitly (confirm_replace) to continue."
            )
        async with transaction(self._session):
            source = await self._source(source_id, for_update=True)
            version = source.schema_version
            if options.expected_schema_version is not None and options.expected_schema_version != version:
                raise VersionConflictError(
                    f"The schema changed since it was previewed (version {options.expected_schema_version}, now "
                    f"{version}). Preview the import again."
                )
            loaded = await self._load(source.id)
            plan = plan_import(_existing(source, loaded), document, options.strategy, self._limits)
            if plan.errors:
                if options.dry_run:
                    return ImportOutcome(options.strategy, True, False, version, version, plan, plan.errors).as_result()
                raise SchemaValidationError(plan.errors, "The import was rejected; nothing was changed.")
            if options.dry_run:
                return ImportOutcome(options.strategy, True, False, version, version, plan, []).as_result()
            changed = plan.has_changes
            if changed:
                await self._apply(source, loaded, plan)
            counts = plan.counts()
            summary_parts = [
                f"{counts[kind][action]} {label} {action}"
                for kind, label in (("tables", "tables"), ("columns", "columns"), ("relationships", "relationships"),
                                    ("glossary_terms", "glossary terms"))
                for action in ("created", "updated", "removed")
                if counts[kind][action]
            ]
            await self._record(
                source, SchemaChangeType.SCHEMA_IMPORTED, "import", source.id, actor,
                f"Imported schema metadata ({options.strategy}): " + (", ".join(summary_parts) or "no changes"),
                {"strategy": options.strategy, "counts": counts, "changes": plan.changes()[:_MAX_AUDITED_CHANGES]},
                bump=changed,
            )
            return ImportOutcome(options.strategy, False, True, version, source.schema_version, plan, []).as_result()

    async def _apply(self, source: SchemaSource, loaded: LoadedSchema, plan: ImportPlan) -> None:
        """Write a validated plan. Runs inside the caller's transaction: any failure rolls everything back."""
        tables_by_id = {t.id: t for t in loaded.tables}
        columns_by_id = {c.id: c for c in loaded.columns}
        ids: dict[str, uuid.UUID] = {}
        for planned in plan.tables:
            if planned.action == CREATE:
                table = SchemaTable(id=uuid.uuid4(), schema_source_id=source.id, **planned.values)
                self._session.add(table)
                ids[planned.key] = table.id
            else:
                assert planned.existing_id is not None
                table = tables_by_id[planned.existing_id]
                ids[planned.key] = table.id
                if planned.action == UPDATE:
                    for name in planned.changed_fields:
                        setattr(table, name, planned.values[name])
                elif planned.action == REMOVE:
                    table.mark_deleted()
        # No ORM relationships are mapped, so parents are flushed before their children.
        await self._session.flush()
        for planned in plan.tables:
            for column in planned.columns:
                if column.action == CREATE:
                    created = SchemaColumn(
                        id=uuid.uuid4(), table_id=ids[planned.key], ordinal_position=column.ordinal_position,
                        **column.values,
                    )
                    self._session.add(created)
                    ids[column.key] = created.id
                    continue
                assert column.existing_id is not None
                existing = columns_by_id[column.existing_id]
                ids[column.key] = existing.id
                if column.action == UPDATE:
                    for name in column.changed_fields:
                        setattr(existing, name, column.values[name])
                elif column.action == REMOVE or planned.action == REMOVE:
                    existing.mark_deleted()
        await self._session.flush()
        relationships_by_id = {r.id: r for r in loaded.relationships}
        # Removals first, so a relationship removed and re-created never collides with itself.
        for planned_relationship in sorted(plan.relationships, key=lambda r: r.action != REMOVE):
            if planned_relationship.action == CREATE:
                self._session.add(
                    SchemaRelationship(
                        id=uuid.uuid4(),
                        schema_source_id=source.id,
                        source_table_id=ids[planned_relationship.source_table_key],
                        source_column_id=ids[planned_relationship.source_column_key],
                        target_table_id=ids[planned_relationship.target_table_key],
                        target_column_id=ids[planned_relationship.target_column_key],
                        **planned_relationship.values,
                    )
                )
                continue
            assert planned_relationship.existing_id is not None
            relationship = relationships_by_id[planned_relationship.existing_id]
            if planned_relationship.action == UPDATE:
                for name in planned_relationship.changed_fields:
                    setattr(relationship, name, planned_relationship.values[name])
            elif planned_relationship.action == REMOVE:
                relationship.mark_deleted()
                await self._session.flush()
        if plan.glossary is not None:
            source.business_glossary = plan.glossary
        await self._session.flush()

    # =========================================================================================
    # helpers
    # =========================================================================================

    async def _source(self, source_id: uuid.UUID, *, for_update: bool = False) -> SchemaSource:
        source = await (self._sources.get_for_update(source_id) if for_update else self._sources.get(source_id))
        if source is None:
            raise SchemaNotFoundError()
        return source

    async def _table(self, table_id: uuid.UUID) -> SchemaTable:
        table = await self._tables.get(table_id)
        if table is None:
            raise TableNotFoundError()
        return table

    async def _column(self, column_id: uuid.UUID) -> SchemaColumn:
        column = await self._columns.get(column_id)
        if column is None:
            raise ColumnNotFoundError()
        return column

    async def _load(self, source_id: uuid.UUID) -> LoadedSchema:
        tables = list(await self._tables.for_source(source_id))
        columns = list(await self._columns.for_tables(t.id for t in tables))
        relationships = list(await self._relationships.for_source(source_id))
        return LoadedSchema(tables, columns, relationships)

    @staticmethod
    def _new_column(table_id: uuid.UUID, data: SchemaColumnCreate, position: int) -> SchemaColumn:
        return SchemaColumn(
            id=uuid.uuid4(),
            table_id=table_id,
            column_name=data.column_name,
            data_type=data.data_type,
            description=data.description,
            business_meaning=data.business_meaning,
            is_primary_key=data.is_primary_key,
            is_foreign_key=data.is_foreign_key,
            is_nullable=(not data.is_primary_key) if data.is_nullable is None else data.is_nullable,
            is_sensitive=data.is_sensitive,
            is_active=data.is_active,
            ordinal_position=position,
        )

    def _check(self, before: LoadedSchema, after: LoadedSchema) -> None:
        self._raise_new_errors(
            validate_schema_state(_state(before), self._limits),
            validate_schema_state(_state(after), self._limits),
        )

    @staticmethod
    def _raise_new_errors(before: Any, after: Any) -> None:
        new_errors = after.new_errors_since(before)
        if new_errors:
            raise SchemaValidationError(new_errors)

    async def _check_endpoints(self, source: SchemaSource, loaded: LoadedSchema, relationship: SchemaRelationship) -> None:
        """Both endpoints exist, belong to this schema source and are active."""
        tables = {t.id: t for t in loaded.tables}
        columns = {c.id: c for c in loaded.columns}
        issues: list[ValidationIssue] = []
        for side in ("source", "target"):
            table_id = getattr(relationship, f"{side}_table_id")
            column_id = getattr(relationship, f"{side}_column_id")
            table, column = tables.get(table_id), columns.get(column_id)
            if table is None or column is None:
                other_table = await self._tables.get(table_id) if table is None else None
                other_column = await self._columns.get(column_id) if column is None else None
                if other_table is not None or other_column is not None:
                    issues.append(ValidationIssue(
                        "relationship_endpoint_outside_schema",
                        f"The {side} table or column belongs to a different schema. Both ends of a relationship "
                        "must be in the same schema.",
                        (f"{side}_column_id",),
                    ))
                else:
                    issues.append(ValidationIssue(
                        "dangling_relationship", f"The {side} table or column is not registered.", (f"{side}_column_id",)
                    ))
                continue
            if column.table_id != table.id:
                issues.append(ValidationIssue(
                    "relationship_column_mismatch",
                    f"Column '{column.column_name}' does not belong to table '{table.qualified_name}'.",
                    (f"{side}_column_id",),
                ))
            elif relationship.is_active and not (table.is_active and column.is_active):
                issues.append(ValidationIssue(
                    "relationship_inactive_endpoint",
                    f"The {side} {table.qualified_name}.{column.column_name} is inactive. Activate it first.",
                    (f"{side}_column_id",),
                ))
        if issues:
            raise SchemaValidationError(issues)

    async def _ensure_removable(
        self, relationships: Sequence[SchemaRelationship], what: str, cascade: bool
    ) -> None:
        if relationships and not cascade:
            raise InUseError(
                f"{what} is used by {len(relationships)} relationship{'s' if len(relationships) != 1 else ''}. "
                "Remove them first, or delete together with its relationships.",
                details={"relationship_ids": [str(r.id) for r in relationships]},
            )

    async def _record(
        self,
        source: SchemaSource,
        event_type: str,
        entity_type: str,
        entity_id: uuid.UUID | None,
        actor: User,
        summary: str,
        details: dict[str, Any],
        *,
        bump: bool = True,
    ) -> None:
        """Audit the change; with ``bump`` also produce the next schema version (same transaction)."""
        if bump:
            source.schema_version += 1
            source.metadata_updated_at = utcnow()
            await self._session.flush()
        self._session.add(
            SchemaChangeEvent(
                schema_source_id=source.id,
                event_type=event_type,
                actor_user_id=actor.id,
                entity_type=entity_type,
                entity_id=entity_id,
                schema_version=source.schema_version,
                version_incremented=bump,
                summary=summary[:1000],
                details=details,
            )
        )
        await self._session.flush()
        logger.info(
            "Schema registry change",
            extra={
                "event_type": event_type,
                "schema_id": str(source.id),
                "schema_version": source.schema_version,
                "version_incremented": bump,
                "actor_user_id": str(actor.id),
            },
        )


COLUMN_REQUIRED_FIELDS = (
    "column_name", "data_type", "is_primary_key", "is_foreign_key", "is_nullable", "is_sensitive", "is_active",
    "ordinal_position",
)


def _existing(source: SchemaSource, loaded: LoadedSchema) -> ExistingSchema:
    by_table: dict[uuid.UUID, list[SchemaColumn]] = {}
    for column in loaded.columns:
        by_table.setdefault(column.table_id, []).append(column)
    return ExistingSchema(
        tables=[
            ExistingTable(
                id=t.id,
                values={
                    "schema_name": t.schema_name, "table_name": t.table_name, "description": t.description,
                    "business_purpose": t.business_purpose, "is_active": t.is_active,
                },
                columns=[
                    ExistingColumn(
                        id=c.id,
                        values={
                            "column_name": c.column_name, "data_type": c.data_type, "description": c.description,
                            "business_meaning": c.business_meaning, "is_primary_key": c.is_primary_key,
                            "is_foreign_key": c.is_foreign_key, "is_nullable": c.is_nullable,
                            "is_sensitive": c.is_sensitive, "is_active": c.is_active,
                        },
                        ordinal_position=c.ordinal_position,
                    )
                    for c in by_table.get(t.id, [])
                ],
            )
            for t in loaded.tables
        ],
        relationships=[
            ExistingRelationship(
                id=r.id, source_table_id=r.source_table_id, source_column_id=r.source_column_id,
                target_table_id=r.target_table_id, target_column_id=r.target_column_id,
                values={"relationship_type": r.relationship_type, "description": r.description, "is_active": r.is_active},
            )
            for r in loaded.relationships
        ],
        glossary=list(source.business_glossary or []),
    )


def _row_label(row: RelationshipRow) -> str:
    return (
        f"{row.source_table.table_name}.{row.source_column.column_name} → "
        f"{row.target_table.table_name}.{row.target_column.column_name}"
    )


def _conflict(exc: IntegrityError, message: str) -> Exception:
    if getattr(exc.orig, "sqlstate", None) == _UNIQUE_VIOLATION:
        return ConflictError(message)
    return exc

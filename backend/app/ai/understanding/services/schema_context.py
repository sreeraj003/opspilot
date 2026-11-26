"""An in-memory, read-only view of one registered schema for question understanding.

Built from the Module 06 registry (``SchemaRegistryService.outline``): only
active, live tables, columns and relationships. Lookups are case-insensitive
and use qualified names (``schema.table``, ``schema.table.column``), the same
names the model is given and must use.
"""

import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from app.models.schema_registry import SchemaSource
from app.schema_registry.data_types import data_type_category
from app.services.schema_registry_service import LoadedSchema


@dataclass(frozen=True, slots=True)
class ColumnInfo:
    name: str
    data_type: str
    category: str  # string / number / boolean / date / datetime / other
    nullable: bool
    description: str | None
    business_meaning: str | None
    is_sensitive: bool
    is_primary_key: bool
    is_foreign_key: bool


@dataclass(frozen=True, slots=True)
class TableInfo:
    schema_name: str
    table_name: str
    description: str | None
    business_purpose: str | None
    columns: dict[str, ColumnInfo]  # lower-case name -> column, in ordinal order

    @property
    def qualified(self) -> str:
        return f"{self.schema_name}.{self.table_name}"

    def column(self, name: str) -> ColumnInfo | None:
        return self.columns.get(name.lower())


@dataclass(frozen=True, slots=True)
class RelationshipInfo:
    source_table: str  # qualified
    source_column: str
    target_table: str
    target_column: str
    relationship_type: str
    description: str | None


@dataclass(frozen=True, slots=True)
class SchemaContext:
    source_id: uuid.UUID
    source_name: str
    schema_version: int
    database_type: str
    dialect: str
    is_active: bool
    tables: dict[str, TableInfo]  # lower-case qualified name -> table
    relationships: tuple[RelationshipInfo, ...]
    glossary: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    @classmethod
    def build(cls, source: SchemaSource, loaded: LoadedSchema) -> "SchemaContext":
        live_tables = {t.id: t for t in loaded.tables if t.is_active and t.deleted_at is None}
        columns_by_table: dict[uuid.UUID, dict[str, ColumnInfo]] = {table_id: {} for table_id in live_tables}
        names_by_column_id: dict[uuid.UUID, str] = {}
        for column in sorted(loaded.columns, key=lambda c: (c.ordinal_position, c.column_name.lower())):
            if column.table_id not in live_tables or not column.is_active or column.deleted_at is not None:
                continue
            columns_by_table[column.table_id][column.column_name.lower()] = ColumnInfo(
                name=column.column_name,
                data_type=column.data_type,
                category=data_type_category(column.data_type),
                nullable=column.is_nullable,
                description=column.description,
                business_meaning=column.business_meaning,
                is_sensitive=column.is_sensitive,
                is_primary_key=column.is_primary_key,
                is_foreign_key=column.is_foreign_key,
            )
            names_by_column_id[column.id] = column.column_name
        tables = {
            f"{t.schema_name}.{t.table_name}".lower(): TableInfo(
                schema_name=t.schema_name,
                table_name=t.table_name,
                description=t.description,
                business_purpose=t.business_purpose,
                columns=columns_by_table[t.id],
            )
            for t in live_tables.values()
        }
        relationships = tuple(
            RelationshipInfo(
                source_table=f"{live_tables[r.source_table_id].schema_name}.{live_tables[r.source_table_id].table_name}",
                source_column=names_by_column_id[r.source_column_id],
                target_table=f"{live_tables[r.target_table_id].schema_name}.{live_tables[r.target_table_id].table_name}",
                target_column=names_by_column_id[r.target_column_id],
                relationship_type=r.relationship_type,
                description=r.description,
            )
            for r in loaded.relationships
            if r.is_active
            and r.deleted_at is None
            and r.source_table_id in live_tables
            and r.target_table_id in live_tables
            and r.source_column_id in names_by_column_id
            and r.target_column_id in names_by_column_id
        )
        return cls(
            source_id=source.id,
            source_name=source.name,
            schema_version=source.schema_version,
            database_type=source.database_type,
            dialect=source.dialect,
            is_active=source.is_active,
            tables=dict(sorted(tables.items())),
            relationships=relationships,
            glossary=tuple(source.business_glossary or ()),
        )

    def table(self, qualified: str) -> TableInfo | None:
        return self.tables.get(qualified.lower())

    def column(self, qualified_table: str, column: str) -> ColumnInfo | None:
        table = self.table(qualified_table)
        return table.column(column) if table else None

    def resolve_column(self, qualified_column: str) -> tuple[TableInfo, ColumnInfo] | None:
        """``schema.table.column`` -> (table, column), or None."""
        parts = qualified_column.split(".")
        if len(parts) != 3:
            return None
        table = self.table(f"{parts[0]}.{parts[1]}")
        if table is None:
            return None
        column = table.column(parts[2])
        return (table, column) if column else None

    def related_tables(self, qualified_tables: Iterable[str]) -> set[str]:
        """Lower-case qualified names of tables directly related to any of ``qualified_tables``."""
        wanted = {t.lower() for t in qualified_tables}
        related: set[str] = set()
        for r in self.relationships:
            source, target = r.source_table.lower(), r.target_table.lower()
            if source in wanted:
                related.add(target)
            if target in wanted:
                related.add(source)
        return related - wanted

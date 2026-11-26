"""``SchemaCatalog`` backed by the schema registry (the Module 04 plug-in point).

Permission policies (``app.authorization.policy``) name tables and columns.
Until any schema was registered, those names could not be checked. With this
catalog, once at least one active table is registered, a policy that names an
unregistered table or column is rejected, and row filters are type-checked
against the registered column types.

The catalog is a snapshot loaded once per request (policy validation is
synchronous). It covers active tables and columns of active schema sources.
A table is matched as ``table`` or ``schema.table``, case-insensitively, across
all sources; when two sources disagree about a column's type it is reported as
``unknown`` and not type-checked.
"""

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.schema_registry import SchemaColumn, SchemaSource, SchemaTable
from app.schema_registry.data_types import OTHER, data_type_category


class RegistrySchemaCatalog:
    def __init__(self, columns: dict[str, dict[str, set[str]]]) -> None:
        # table key ("orders" and "sales.orders", lower case) -> column (lower case) -> categories
        self._columns = columns

    @classmethod
    async def load(cls, session: AsyncSession) -> "RegistrySchemaCatalog":
        rows = await session.execute(
            select(SchemaTable.schema_name, SchemaTable.table_name, SchemaColumn.column_name, SchemaColumn.data_type)
            .join(SchemaSource, SchemaSource.id == SchemaTable.schema_source_id)
            .outerjoin(
                SchemaColumn,
                (SchemaColumn.table_id == SchemaTable.id)
                & SchemaColumn.deleted_at.is_(None)
                & SchemaColumn.is_active.is_(True),
            )
            .where(
                SchemaSource.deleted_at.is_(None),
                SchemaSource.is_active.is_(True),
                SchemaTable.deleted_at.is_(None),
                SchemaTable.is_active.is_(True),
            )
        )
        columns: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
        for schema_name, table_name, column_name, data_type in rows:
            for key in (table_name.lower(), f"{schema_name}.{table_name}".lower()):
                table = columns[key]
                if column_name is not None:
                    table[column_name.lower()].add(data_type_category(data_type))
        return cls({key: dict(value) for key, value in columns.items()})

    @property
    def is_empty(self) -> bool:
        return not self._columns

    def has_table(self, table: str) -> bool | None:
        if self.is_empty:
            return None
        return table.lower() in self._columns

    def column_type(self, table: str, column: str) -> str | None:
        categories = self._columns.get(table.lower(), {}).get(column.lower())
        if not categories:
            return None
        if len(categories) > 1:
            return "unknown"
        category = next(iter(categories))
        return "unknown" if category == OTHER else category

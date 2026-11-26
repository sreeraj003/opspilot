"""AI-ready schema representation.

Turns registered metadata into structured data that future AI and SQL
generation modules consume (``AiSchemaRepresentation``). It is data, not a
prompt: prompt assembly belongs to those modules.

Rules:

* Only active, live metadata is included: inactive tables, columns and
  relationships (and relationships touching inactive endpoints) are left out.
* No application internals: no database ids of OpsPilot rows other than the
  schema id, no timestamps, users or audit data, never credentials.
* Columns flagged ``is_sensitive`` are excluded unless explicitly requested,
  and then carry ``"sensitive": true`` so consumers can treat them carefully.
* Filtering by schema namespace, table name or "tables related to these" keeps
  the representation small for retrieval in later modules. No embeddings,
  vector search or semantic ranking happen here.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from app.models.schema_registry import SchemaColumn, SchemaRelationship, SchemaSource, SchemaTable
from app.schemas.schema_registry import (
    AiBusinessTerm,
    AiColumn,
    AiRelationship,
    AiRelationshipEnd,
    AiSchemaRepresentation,
    AiTable,
    AiTableRelationship,
)


@dataclass(frozen=True, slots=True)
class RepresentationFilter:
    # Restrict to these schema namespaces (case-insensitive).
    schema_names: tuple[str, ...] = ()
    # Restrict to these tables: "orders" or "sales.orders" (case-insensitive).
    tables: tuple[str, ...] = ()
    # Also include tables directly related to the selected ones.
    include_related: bool = False
    include_sensitive_columns: bool = False
    include_descriptions: bool = True
    include_business_terms: bool = True


def build_representation(
    source: SchemaSource,
    tables: Sequence[SchemaTable],
    columns: Sequence[SchemaColumn],
    relationships: Sequence[SchemaRelationship],
    selection: RepresentationFilter | None = None,
) -> AiSchemaRepresentation:
    selection = selection or RepresentationFilter()
    live_tables = {t.id: t for t in tables if t.is_active and t.deleted_at is None}
    columns_by_table: dict[object, list[SchemaColumn]] = {}
    visible_columns: dict[object, SchemaColumn] = {}
    for column in sorted(columns, key=lambda c: (c.ordinal_position, c.column_name.lower())):
        if column.table_id not in live_tables or not column.is_active or column.deleted_at is not None:
            continue
        if column.is_sensitive and not selection.include_sensitive_columns:
            continue
        columns_by_table.setdefault(column.table_id, []).append(column)
        visible_columns[column.id] = column

    usable = [
        r
        for r in relationships
        if r.is_active
        and r.deleted_at is None
        and r.source_table_id in live_tables
        and r.target_table_id in live_tables
        and r.source_column_id in visible_columns
        and r.target_column_id in visible_columns
    ]
    selected = _select_tables(live_tables.values(), usable, selection)
    included = [r for r in usable if r.source_table_id in selected and r.target_table_id in selected]

    def end(table_id: object, column_id: object) -> AiRelationshipEnd:
        table = live_tables[table_id]
        return AiRelationshipEnd(schema_name=table.schema_name, table=table.table_name, column=visible_columns[column_id].column_name)

    describe = selection.include_descriptions
    ai_tables: list[AiTable] = []
    for table in sorted((live_tables[i] for i in selected), key=lambda t: (t.schema_name.lower(), t.table_name.lower())):
        table_relationships = [
            AiTableRelationship(
                type=r.relationship_type,
                column=visible_columns[r.source_column_id].column_name,
                references=end(r.target_table_id, r.target_column_id),
                description=r.description if describe else None,
            )
            for r in included
            if r.source_table_id == table.id
        ]
        ai_tables.append(
            AiTable(
                schema_name=table.schema_name,
                table=table.table_name,
                description=table.description if describe else None,
                business_purpose=table.business_purpose if describe else None,
                columns=[
                    AiColumn(
                        name=c.column_name,
                        type=c.data_type,
                        nullable=c.is_nullable,
                        description=c.description if describe else None,
                        business_meaning=c.business_meaning if describe else None,
                        primary_key=True if c.is_primary_key else None,
                        foreign_key=True if c.is_foreign_key else None,
                        sensitive=True if c.is_sensitive else None,
                    )
                    for c in columns_by_table.get(table.id, [])
                ],
                relationships=table_relationships,
            )
        )
    return AiSchemaRepresentation(
        schema_id=source.id,
        schema_source=source.name,
        database_type=source.database_type,
        dialect=source.dialect,
        schema_version=source.schema_version,
        tables=ai_tables,
        relationships=[
            AiRelationship(
                type=r.relationship_type,
                source=end(r.source_table_id, r.source_column_id),
                target=end(r.target_table_id, r.target_column_id),
                description=r.description if describe else None,
            )
            for r in included
        ],
        business_terms=[
            AiBusinessTerm(term=t["term"], definition=t["definition"], synonyms=list(t.get("synonyms") or []))
            for t in (source.business_glossary or [])
        ]
        if selection.include_business_terms
        else [],
    )


def _select_tables(
    tables: Iterable[SchemaTable], relationships: Sequence[SchemaRelationship], selection: RepresentationFilter
) -> set[object]:
    candidates = list(tables)
    if selection.schema_names:
        namespaces = {s.lower() for s in selection.schema_names}
        candidates = [t for t in candidates if t.schema_name.lower() in namespaces]
    if not selection.tables:
        return {t.id for t in candidates}
    wanted = {name.lower() for name in selection.tables}
    chosen = {
        t.id for t in candidates if t.table_name.lower() in wanted or f"{t.schema_name}.{t.table_name}".lower() in wanted
    }
    if selection.include_related:
        candidate_ids = {t.id for t in candidates}
        related: set[object] = set()
        for r in relationships:
            if r.source_table_id in chosen and r.target_table_id in candidate_ids:
                related.add(r.target_table_id)
            if r.target_table_id in chosen and r.source_table_id in candidate_ids:
                related.add(r.source_table_id)
        chosen |= related
    return chosen

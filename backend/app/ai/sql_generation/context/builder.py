"""The constrained context for SQL generation.

Built only from backend truth: the stored ``BusinessIntent`` (Module 09), the
registered schema (Module 06), active business terms (Module 09) and the user's
*current* effective permissions (Module 04). It contains:

A. the intent (type, metrics, dimensions, filters, time range, grouping,
   sorting, limit, assumptions, ambiguities, clarification);
B. the relevant tables: every table the intent names, plus the shortest join
   paths between them over registered relationships, through permitted tables
   only (at most ``AI_SQL_MAX_SCHEMA_TABLES``). Only columns the user may see are
   listed, with types, keys, descriptions and relationships;
C. the business terms the intent uses or that describe those tables;
D. the permission context: allowed tables and columns, operations, row-level
   restrictions and the data scope.

It never contains credentials, connector details, other users' permissions,
tables outside the relevant set, or columns the user may not see. Permission
data in it is guidance for the model, not authorization: the backend
authorizes the parsed SQL again, and the SQL security guard (Module 11) does so
independently before anything runs.
"""

from collections import deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.ai.sql_generation.dialects.registry import SQLDialect
from app.ai.understanding.schemas.intent import BusinessIntent, FieldReference
from app.ai.understanding.services.permission_view import PermissionView
from app.ai.understanding.services.schema_context import RelationshipInfo, SchemaContext, TableInfo
from app.models.business_term import BusinessTerm

MAX_JOIN_PATH = 3  # tables added between two intent tables, at most
MAX_CONTEXT_TERMS = 20

# Registered type categories -> the types SQLGlot validation uses.
_SQLGLOT_TYPES = {
    "number": "DOUBLE",
    "string": "TEXT",
    "boolean": "BOOLEAN",
    "date": "DATE",
    "datetime": "TIMESTAMP",
}


class ContextPermissionError(Exception):
    """The intent needs a table or column the user may no longer see. Never names it."""


class ContextSchemaError(Exception):
    """The intent names a table or column that is no longer registered."""


@dataclass(frozen=True, slots=True)
class ContextTable:
    table: TableInfo
    columns: tuple[str, ...]  # visible column names, registry spelling, ordinal order
    from_intent: bool


@dataclass
class SQLGenerationContext:
    intent: BusinessIntent
    schema: SchemaContext
    dialect: SQLDialect
    tables: list[ContextTable]
    relationships: list[RelationshipInfo]
    terms: list[BusinessTerm]
    permissions: PermissionView
    max_row_limit: int
    truncated: bool = False
    notes: list[str] = field(default_factory=list)

    # --- lookups used by validation ---------------------------------------------------------------------

    def table(self, qualified: str) -> ContextTable | None:
        key = qualified.lower()
        return next((t for t in self.tables if t.table.qualified.lower() == key), None)

    def column_visible(self, qualified_table: str, column: str) -> bool:
        table = self.table(qualified_table)
        return table is not None and column.lower() in {c.lower() for c in table.columns}

    def sqlglot_mapping(self) -> dict[str, dict[str, dict[str, str]]]:
        """``{schema: {table: {column: type}}}`` in lower case: visible columns only."""
        mapping: dict[str, dict[str, dict[str, str]]] = {}
        for item in self.tables:
            schema_part = mapping.setdefault(item.table.schema_name.lower(), {})
            schema_part[item.table.table_name.lower()] = {
                name.lower(): _SQLGLOT_TYPES.get(item.table.column(name).category, "TEXT")  # type: ignore[union-attr]
                for name in item.columns
            }
        return mapping

    def relationship_between(self, left: str, right: str) -> RelationshipInfo | None:
        """A registered relationship joining ``schema.table.column`` ``left`` and ``right`` (either direction)."""
        a, b = left.lower(), right.lower()
        for r in self.relationships:
            source = f"{r.source_table}.{r.source_column}".lower()
            target = f"{r.target_table}.{r.target_column}".lower()
            if {a, b} == {source, target}:
                return r
        return None

    @property
    def intent_tables(self) -> list[str]:
        return [t.table.qualified for t in self.tables if t.from_intent]

    # --- the prompt payload -----------------------------------------------------------------------------

    def payload(self) -> dict[str, Any]:
        return {
            "business_intent": _intent_section(self.intent),
            "schema": {
                "schema_version": self.schema.schema_version,
                "tables": [_table_section(item, self.schema) for item in self.tables],
                "relationships": [
                    {
                        "from": f"{r.source_table}.{r.source_column}",
                        "to": f"{r.target_table}.{r.target_column}",
                        "type": r.relationship_type,
                        **({"description": r.description} if r.description else {}),
                    }
                    for r in self.relationships
                ],
            },
            "business_terms": [_term_section(t) for t in self.terms],
            "permissions": self._permission_section(),
            "dialect": self.dialect.guidance(),
            "limits": {"max_row_limit": self.max_row_limit},
        }

    def _permission_section(self) -> dict[str, Any]:
        tables = []
        for item in self.tables:
            qualified = item.table.qualified
            entry: dict[str, Any] = {
                "table": qualified,
                "allowed_columns": list(item.columns),
                "operations": self.permissions.operations(qualified),
            }
            row_filter = self.permissions.row_filter(qualified)
            if row_filter is not None:
                entry["row_restriction"] = row_filter
            tables.append(entry)
        return {
            "note": "Use only these tables and columns. Every listed row restriction must hold in the query.",
            "tables": tables,
            "row_level_restrictions": any("row_restriction" in t for t in tables),
            "data_scope": "only rows permitted by the row restrictions above" if any("row_restriction" in t for t in tables)
            else "all rows of the listed tables",
        }


# --- building ---------------------------------------------------------------------------------------------


def _field_references(intent: BusinessIntent) -> list[FieldReference]:
    refs: list[FieldReference] = []
    refs += [m.column for m in intent.requested_metrics if m.column is not None]
    refs += [d.column for d in intent.requested_dimensions]
    refs += [a.column for a in intent.requested_attributes]
    refs += [f.field for f in intent.filters]
    if intent.time.date_column is not None:
        refs.append(intent.time.date_column)
    return refs


def intent_references(intent: BusinessIntent, terms: Sequence[BusinessTerm]) -> dict[str, set[str]]:
    """Qualified table -> column names the intent itself needs (lower-case keys)."""
    referenced: dict[str, set[str]] = {}
    for table in intent.candidate_tables:
        referenced.setdefault(table.lower(), set())
    for column in intent.candidate_columns:
        table, name = column.rsplit(".", 1)
        referenced.setdefault(table.lower(), set()).add(name)
    for ref in _field_references(intent):
        referenced.setdefault(ref.table.lower(), set()).add(ref.column)
    term_ids = {m.term_id for m in intent.requested_metrics if m.term_id} | {
        d.term_id for d in intent.requested_dimensions if d.term_id
    }
    for term in terms:
        if term.id in term_ids:
            for table in term.related_tables:
                referenced.setdefault(table.lower(), set())
            for column in term.related_columns:
                table, name = column.rsplit(".", 1)
                referenced.setdefault(table.lower(), set()).add(name)
    return referenced


def build_context(
    intent: BusinessIntent,
    schema: SchemaContext,
    permissions: PermissionView,
    terms: Sequence[BusinessTerm],
    dialect: SQLDialect,
    *,
    max_tables: int,
    max_row_limit: int,
) -> SQLGenerationContext:
    referenced = intent_references(intent, terms)
    # Every table and column the intent needs must still be registered and permitted.
    for key, columns in referenced.items():
        table = schema.table(key)
        if table is None:
            raise ContextSchemaError()
        if not permissions.table_allowed(table.qualified):
            raise ContextPermissionError()
        for name in columns:
            if table.column(name) is None:
                raise ContextSchemaError()
            if not permissions.column_allowed(table.qualified, name):
                raise ContextPermissionError()

    intent_keys = list(dict.fromkeys(referenced))
    usable_relationships = [
        r
        for r in schema.relationships
        if permissions.table_allowed(r.source_table)
        and permissions.table_allowed(r.target_table)
        and permissions.column_allowed(r.source_table, r.source_column)
        and permissions.column_allowed(r.target_table, r.target_column)
    ]
    bridges, truncated = _join_paths(intent_keys, usable_relationships, budget=max(0, max_tables - len(intent_keys)))
    selected = intent_keys + [b for b in bridges if b not in intent_keys]

    tables: list[ContextTable] = []
    for key in selected:
        table = schema.table(key)
        assert table is not None
        visible = tuple(c.name for c in permissions.visible_columns(table))
        tables.append(ContextTable(table=table, columns=visible, from_intent=key in referenced))
    selected_set = set(selected)
    relationships = [
        r for r in usable_relationships if r.source_table.lower() in selected_set and r.target_table.lower() in selected_set
    ]
    return SQLGenerationContext(
        intent=intent,
        schema=schema,
        dialect=dialect,
        tables=tables,
        relationships=relationships,
        terms=_context_terms(intent, terms, selected_set, permissions),
        permissions=permissions,
        max_row_limit=max_row_limit,
        truncated=truncated,
    )


def _join_paths(
    intent_tables: Sequence[str], relationships: Iterable[RelationshipInfo], *, budget: int
) -> tuple[list[str], bool]:
    """Tables on the shortest relationship paths connecting the intent's tables (within ``budget``)."""
    graph: dict[str, set[str]] = {}
    for r in relationships:
        a, b = r.source_table.lower(), r.target_table.lower()
        graph.setdefault(a, set()).add(b)
        graph.setdefault(b, set()).add(a)
    added: list[str] = []
    truncated = False
    connected = set(intent_tables[:1])
    for target in intent_tables[1:]:
        if target in connected:
            continue
        path = _shortest_path(graph, connected, target)
        if path is None:
            connected.add(target)  # no registered path: the model must not invent one
            continue
        middle = [t for t in path[1:-1] if t not in connected and t not in intent_tables]
        if len(added) + len(middle) > budget:
            truncated = True
            connected.add(target)
            continue
        added.extend(middle)
        connected.update(path)
    return added, truncated


def _shortest_path(graph: dict[str, set[str]], sources: set[str], target: str) -> list[str] | None:
    queue: deque[list[str]] = deque([s] for s in sorted(sources))
    seen = set(sources)
    while queue:
        path = queue.popleft()
        if path[-1] == target:
            return path
        if len(path) > MAX_JOIN_PATH + 1:
            continue
        for neighbour in sorted(graph.get(path[-1], ())):
            if neighbour not in seen:
                seen.add(neighbour)
                queue.append([*path, neighbour])
    return None


def _context_terms(
    intent: BusinessIntent, terms: Sequence[BusinessTerm], tables: set[str], permissions: PermissionView
) -> list[BusinessTerm]:
    """Terms the intent uses first, then terms about the selected tables; never terms on restricted fields."""
    used = {m.term_id for m in intent.requested_metrics if m.term_id} | {
        d.term_id for d in intent.requested_dimensions if d.term_id
    }

    def usable(term: BusinessTerm) -> bool:
        if any(t.lower() not in tables for t in term.related_tables):
            return False
        return all(
            permissions.column_allowed(c.rsplit(".", 1)[0], c.rsplit(".", 1)[1]) for c in term.related_columns
        )

    chosen = [t for t in terms if t.id in used and usable(t)]
    chosen += [t for t in terms if t.id not in used and usable(t) and t.related_tables]
    return chosen[:MAX_CONTEXT_TERMS]


# --- payload sections -----------------------------------------------------------------------------------


def _ref(ref: FieldReference | None) -> str | None:
    return ref.qualified if ref is not None else None


def _intent_section(intent: BusinessIntent) -> dict[str, Any]:
    time_range = intent.time.time_range
    comparison = intent.time.comparison_period
    return {
        "intent_type": intent.intent_type.value,
        "question": intent.normalized_question,
        "metrics": [
            {
                "name": m.name,
                "business_term": m.term_name,
                "column": _ref(m.column),
                "aggregation": m.aggregation.value,
            }
            for m in intent.requested_metrics
        ],
        "dimensions": [
            {
                "name": d.name,
                "column": d.column.qualified,
                **({"time_granularity": d.time_granularity.value} if d.time_granularity else {}),
            }
            for d in intent.requested_dimensions
        ],
        "attributes": [a.column.qualified for a in intent.requested_attributes],
        "filters": [
            {
                "column": f.field.qualified,
                "operator": f.operator.value,
                "value": f.value,
                "value_type": f.value_type.value,
            }
            for f in intent.filters
        ],
        "time_range": (
            {
                "label": time_range.label,
                "column": _ref(intent.time.date_column),
                "start_inclusive": time_range.start_at.isoformat(),
                "end_exclusive": time_range.end_at.isoformat(),
                "start_date": time_range.start_date.isoformat(),
                "end_date_exclusive": time_range.end_date.isoformat(),
                "timezone": time_range.timezone,
            }
            if time_range
            else None
        ),
        "comparison_period": (
            {
                "label": comparison.label,
                "start_inclusive": comparison.start_at.isoformat(),
                "end_exclusive": comparison.end_at.isoformat(),
            }
            if comparison
            else None
        ),
        "time_granularity": intent.time.time_granularity.value if intent.time.time_granularity else None,
        "grouping": list(intent.aggregation.grouping_dimensions),
        "sorting": [{"target": s.target, "direction": s.direction.value} for s in intent.aggregation.sorting],
        "limit": intent.aggregation.limit,
        "assumptions": list(intent.assumptions),
        "ambiguities": list(intent.ambiguities),
        "clarification_question": intent.clarification_question,
    }


def _table_section(item: ContextTable, schema: SchemaContext) -> dict[str, Any]:
    table = item.table
    columns = []
    for name in item.columns:
        column = table.column(name)
        assert column is not None
        entry: dict[str, Any] = {"name": column.name, "type": column.data_type}
        if column.is_primary_key:
            entry["primary_key"] = True
        if column.is_foreign_key:
            entry["foreign_key"] = True
        if column.description:
            entry["description"] = column.description
        if column.business_meaning:
            entry["business_meaning"] = column.business_meaning
        columns.append(entry)
    section: dict[str, Any] = {"table": table.qualified, "columns": columns}
    if table.description:
        section["description"] = table.description
    if table.business_purpose:
        section["business_purpose"] = table.business_purpose
    if not item.from_intent:
        section["role"] = "join path only"
    return section


def _term_section(term: BusinessTerm) -> dict[str, Any]:
    section: dict[str, Any] = {
        "name": term.name,
        "kind": term.kind,
        "definition": term.description,
        "synonyms": list(term.synonyms),
        "related_columns": list(term.related_columns),
        "related_tables": list(term.related_tables),
    }
    if term.default_aggregation:
        section["aggregation"] = term.default_aggregation
    if term.aggregation_guidance:
        section["calculation_rule"] = term.aggregation_guidance
    return section

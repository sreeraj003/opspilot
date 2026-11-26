"""Plan a JSON schema import: what it would create, update and remove.

Planning is pure (no database access): the service loads the source's current
metadata, calls ``plan_import`` and either reports the plan (dry run, preview)
or applies it in one transaction. Because the plan is computed from the
current state *inside* that transaction, a preview and the real import give
the same answer for the same schema version.

Strategies (nothing is ever overwritten silently):

``create_only`` (default)
    Adds new tables (with their columns), relationships and glossary terms.
    If the document names a table, relationship or term that is already
    registered, the import is refused and nothing changes.
``merge``
    Adds what is new and updates what matches by name, changing only the
    fields the document actually contains. Registered items the document does
    not mention are kept. Every changed field is listed in the result.
``replace``
    Makes the registry match the document exactly: matched items take the
    document's values (omitted fields take their defaults) and registered
    items the document does not list are removed (soft-deleted). Requires
    ``confirm_replace``.

Tables match on ``(schema_name, table_name)``, columns on ``column_name``
within their table, relationships on their two endpoint columns, and glossary
terms on ``term``; all case-insensitively.
"""

import uuid
from dataclasses import dataclass, field
from typing import Any

from app.schema_registry.validation import (
    ERROR,
    ColumnState,
    Location,
    RelationshipState,
    SchemaLimits,
    SchemaState,
    TableState,
    ValidationIssue,
    validate_schema_state,
)
from app.schemas.schema_registry import GlossaryTerm, ImportColumn, ImportTable, SchemaImportDocument

CREATE, UPDATE, UNCHANGED, REMOVE = "create", "update", "unchanged", "remove"

TABLE_FIELDS = ("schema_name", "table_name", "description", "business_purpose", "is_active")
COLUMN_FIELDS = (
    "column_name", "data_type", "description", "business_meaning", "is_primary_key", "is_foreign_key",
    "is_nullable", "is_sensitive", "is_active",
)
RELATIONSHIP_FIELDS = ("relationship_type", "description", "is_active")
MAX_REPORTED_CHANGES = 1000


# --- current state (loaded by the service) -------------------------------------------------------


@dataclass(slots=True)
class ExistingColumn:
    id: uuid.UUID
    values: dict[str, Any]
    ordinal_position: int = 0


@dataclass(slots=True)
class ExistingTable:
    id: uuid.UUID
    values: dict[str, Any]
    columns: list[ExistingColumn] = field(default_factory=list)


@dataclass(slots=True)
class ExistingRelationship:
    id: uuid.UUID
    source_table_id: uuid.UUID
    source_column_id: uuid.UUID
    target_table_id: uuid.UUID
    target_column_id: uuid.UUID
    values: dict[str, Any]


@dataclass(slots=True)
class ExistingSchema:
    tables: list[ExistingTable] = field(default_factory=list)
    relationships: list[ExistingRelationship] = field(default_factory=list)
    glossary: list[dict[str, Any]] = field(default_factory=list)


# --- the plan -----------------------------------------------------------------------------------------


@dataclass(slots=True)
class PlannedColumn:
    key: str
    existing_id: uuid.UUID | None
    values: dict[str, Any]
    action: str
    changed_fields: list[str] = field(default_factory=list)
    ordinal_position: int = 0
    location: Location = ()


@dataclass(slots=True)
class PlannedTable:
    key: str
    existing_id: uuid.UUID | None
    values: dict[str, Any]
    action: str
    changed_fields: list[str] = field(default_factory=list)
    columns: list[PlannedColumn] = field(default_factory=list)
    location: Location = ()

    @property
    def name(self) -> str:
        return f"{self.values['schema_name']}.{self.values['table_name']}"

    @property
    def name_key(self) -> tuple[str, str]:
        return self.values["schema_name"].lower(), self.values["table_name"].lower()


@dataclass(slots=True)
class PlannedRelationship:
    key: str
    existing_id: uuid.UUID | None
    source_table_key: str
    source_column_key: str
    target_table_key: str
    target_column_key: str
    values: dict[str, Any]
    action: str
    label: str
    changed_fields: list[str] = field(default_factory=list)
    location: Location = ()


@dataclass(slots=True)
class Count:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    removed: int = 0

    def add(self, action: str) -> None:
        if action == CREATE:
            self.created += 1
        elif action == UPDATE:
            self.updated += 1
        elif action == REMOVE:
            self.removed += 1
        else:
            self.unchanged += 1

    def as_dict(self) -> dict[str, int]:
        return {"created": self.created, "updated": self.updated, "unchanged": self.unchanged, "removed": self.removed}


@dataclass(slots=True)
class ImportPlan:
    strategy: str
    tables: list[PlannedTable] = field(default_factory=list)
    relationships: list[PlannedRelationship] = field(default_factory=list)
    # None: the glossary is not touched.
    glossary: list[dict[str, Any]] | None = None
    glossary_actions: list[tuple[str, str]] = field(default_factory=list)
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == ERROR]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity != ERROR]

    def counts(self) -> dict[str, dict[str, int]]:
        tables, columns, relationships, terms = Count(), Count(), Count(), Count()
        for table in self.tables:
            tables.add(table.action)
            for column in table.columns:
                columns.add(column.action)
        for relationship in self.relationships:
            relationships.add(relationship.action)
        for action, _ in self.glossary_actions:
            terms.add(action)
        return {
            "tables": tables.as_dict(),
            "columns": columns.as_dict(),
            "relationships": relationships.as_dict(),
            "glossary_terms": terms.as_dict(),
        }

    def changes(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for table in self.tables:
            if table.action != UNCHANGED:
                out.append({"action": table.action, "entity_type": "table", "name": table.name, "fields": table.changed_fields})
            for column in table.columns:
                if column.action != UNCHANGED:
                    out.append({
                        "action": column.action, "entity_type": "column",
                        "name": f"{table.name}.{column.values['column_name']}", "fields": column.changed_fields,
                    })
        for relationship in self.relationships:
            if relationship.action != UNCHANGED:
                out.append({
                    "action": relationship.action, "entity_type": "relationship", "name": relationship.label,
                    "fields": relationship.changed_fields,
                })
        for action, term in self.glossary_actions:
            if action != UNCHANGED:
                out.append({"action": action, "entity_type": "glossary_term", "name": term, "fields": []})
        return out

    @property
    def has_changes(self) -> bool:
        return any(change for change in self.changes())


def plan_import(
    existing: ExistingSchema,
    document: SchemaImportDocument,
    strategy: str,
    limits: SchemaLimits | None = None,
) -> ImportPlan:
    planner = _Planner(existing, document, strategy)
    plan = planner.run()
    if not plan.errors:
        plan.issues.extend(validate_schema_state(planner.target_state(), limits).issues)
    return plan


class _Planner:
    def __init__(self, existing: ExistingSchema, document: SchemaImportDocument, strategy: str) -> None:
        if strategy not in ("create_only", "merge", "replace"):
            raise ValueError(f"unknown import strategy {strategy!r}")
        self.existing = existing
        self.document = document
        self.strategy = strategy
        self.plan = ImportPlan(strategy=strategy)
        self.replace = strategy == "replace"
        self.tables: dict[tuple[str, str], PlannedTable] = {}

    def error(self, code: str, message: str, location: Location) -> None:
        self.plan.issues.append(ValidationIssue(code, message, location, ERROR))

    def run(self) -> ImportPlan:
        for table in self.existing.tables:
            planned = PlannedTable(
                key=str(table.id), existing_id=table.id, values=dict(table.values),
                action=REMOVE if self.replace else UNCHANGED,
            )
            planned.columns = [
                PlannedColumn(
                    key=str(column.id), existing_id=column.id, values=dict(column.values),
                    action=REMOVE if self.replace else UNCHANGED, ordinal_position=column.ordinal_position,
                )
                for column in table.columns
            ]
            self.tables[planned.name_key] = planned
        seen: set[tuple[str, str]] = set()
        for index, table in enumerate(self.document.tables):
            self._table(index, table, seen)
        self.plan.tables = list(self.tables.values())
        self._relationships()
        self._glossary()
        return self.plan

    # --- tables and columns ---------------------------------------------------------------------------

    def _table(self, index: int, table: ImportTable, seen: set[tuple[str, str]]) -> None:
        location: Location = ("document", "tables", index)
        schema_name = table.schema_name or self.document.schema_name
        assert schema_name is not None  # guaranteed by SchemaImportDocument
        values = {
            "schema_name": schema_name,
            "table_name": table.table_name,
            "description": table.description,
            "business_purpose": table.business_purpose,
            "is_active": table.is_active,
        }
        name_key = (schema_name.lower(), table.table_name.lower())
        if name_key in seen:
            self.error(
                "duplicate_table",
                f"Table '{schema_name}.{table.table_name}' appears more than once in the document.",
                (*location, "table_name"),
            )
            return
        seen.add(name_key)
        planned = self.tables.get(name_key)
        if planned is None:
            planned = PlannedTable(key=f"new-table-{index}", existing_id=None, values=values, action=CREATE, location=location)
            self.tables[name_key] = planned
            self._columns(planned, table, location)
            return
        if self.strategy == "create_only":
            self.error(
                "table_exists",
                f"Table '{planned.name}' is already registered. Choose the merge or replace strategy to update it.",
                (*location, "table_name"),
            )
            return
        provided = {"schema_name", "table_name"} | (table.model_fields_set if not self.replace else set(TABLE_FIELDS))
        planned.changed_fields = [f for f in TABLE_FIELDS if f in provided and planned.values[f] != values[f]]
        for name in planned.changed_fields:
            planned.values[name] = values[name]
        planned.action = UPDATE if planned.changed_fields else UNCHANGED
        planned.location = location
        self._columns(planned, table, location)

    def _columns(self, planned: PlannedTable, table: ImportTable, table_location: Location) -> None:
        existing = {column.values["column_name"].lower(): column for column in planned.columns}
        next_position = max((c.ordinal_position for c in planned.columns), default=-1) + 1
        seen: set[str] = set()
        for index, column in enumerate(table.columns):
            location: Location = (*table_location, "columns", index)
            lowered = column.column_name.lower()
            if lowered in seen:
                self.error(
                    "duplicate_column",
                    f"Column '{column.column_name}' appears more than once in table '{planned.name}'.",
                    (*location, "column_name"),
                )
                continue
            seen.add(lowered)
            values = _column_values(column)
            match = existing.get(lowered)
            if match is None:
                planned.columns.append(
                    PlannedColumn(
                        key=f"{planned.key}-column-{index}", existing_id=None, values=values, action=CREATE,
                        ordinal_position=next_position, location=location,
                    )
                )
                next_position += 1
                continue
            provided = {"column_name"} | (column.model_fields_set if not self.replace else set(COLUMN_FIELDS))
            if values["is_primary_key"] and "is_primary_key" in provided and "is_nullable" not in column.model_fields_set:
                provided.add("is_nullable")  # a column made a primary key becomes NOT NULL unless told otherwise
            target = dict(match.values)
            for name in COLUMN_FIELDS:
                if name in provided:
                    target[name] = values[name]
            match.changed_fields = [f for f in COLUMN_FIELDS if target[f] != match.values[f]]
            match.values = target
            match.action = UPDATE if match.changed_fields else UNCHANGED
            match.location = location

    # --- relationships ----------------------------------------------------------------------------------

    def _live_tables(self) -> dict[tuple[str, str], PlannedTable]:
        return {k: t for k, t in self.tables.items() if t.action != REMOVE}

    def _relationships(self) -> None:
        live = self._live_tables()
        by_key: dict[str, PlannedTable] = {t.key: t for t in live.values()}
        live_columns: dict[str, PlannedColumn] = {
            c.key: c for t in live.values() for c in t.columns if c.action != REMOVE
        }
        planned: dict[tuple[str, str], PlannedRelationship] = {}
        for relationship in self.existing.relationships:
            keys = (str(relationship.source_table_id), str(relationship.source_column_id),
                    str(relationship.target_table_id), str(relationship.target_column_id))
            endpoints_live = keys[0] in by_key and keys[2] in by_key and keys[1] in live_columns and keys[3] in live_columns
            label = (
                _label(by_key[keys[0]], live_columns[keys[1]], by_key[keys[2]], live_columns[keys[3]])
                if endpoints_live
                else f"relationship {relationship.id}"
            )
            planned[(keys[1], keys[3])] = PlannedRelationship(
                key=str(relationship.id), existing_id=relationship.id,
                source_table_key=keys[0], source_column_key=keys[1], target_table_key=keys[2], target_column_key=keys[3],
                values=dict(relationship.values),
                # Replace removes what the document does not list; any strategy drops relationships
                # whose endpoints the import removes.
                action=REMOVE if (self.replace or not endpoints_live) else UNCHANGED,
                label=label,
            )
        seen: set[tuple[str, str]] = set()
        for index, item in enumerate(self.document.relationships):
            location: Location = ("document", "relationships", index)
            source = self._resolve(live, item.source_schema, item.source_table, item.source_column, location, "source")
            target = self._resolve(live, item.target_schema, item.target_table, item.target_column, location, "target")
            if source is None or target is None:
                continue
            (source_table, source_column), (target_table, target_column) = source, target
            pair = (source_column.key, target_column.key)
            label = _label(source_table, source_column, target_table, target_column)
            if pair in seen:
                self.error("duplicate_relationship", f"Relationship {label} appears more than once in the document.", location)
                continue
            seen.add(pair)
            values = {"relationship_type": item.relationship_type, "description": item.description, "is_active": item.is_active}
            match = planned.get(pair)
            if match is None or match.existing_id is None:
                planned[pair] = PlannedRelationship(
                    key=f"new-relationship-{index}", existing_id=None,
                    source_table_key=source_table.key, source_column_key=source_column.key,
                    target_table_key=target_table.key, target_column_key=target_column.key,
                    values=values, action=CREATE, label=label, location=location,
                )
                continue
            if self.strategy == "create_only":
                self.error(
                    "relationship_exists",
                    f"Relationship {label} is already registered. Choose the merge or replace strategy to update it.",
                    location,
                )
                continue
            provided = item.model_fields_set if not self.replace else set(RELATIONSHIP_FIELDS)
            original = dict(match.values)
            for name in RELATIONSHIP_FIELDS:
                if name in provided:
                    match.values[name] = values[name]
            match.changed_fields = [f for f in RELATIONSHIP_FIELDS if match.values[f] != original[f]]
            match.action = UPDATE if match.changed_fields else UNCHANGED
            match.location = location
            match.label = label
        self.plan.relationships = list(planned.values())

    def _resolve(
        self,
        live: dict[tuple[str, str], PlannedTable],
        schema_name: str | None,
        table_name: str,
        column_name: str,
        location: Location,
        side: str,
    ) -> tuple[PlannedTable, PlannedColumn] | None:
        schema = schema_name or self.document.schema_name or ""
        table = live.get((schema.lower(), table_name.lower()))
        if table is None:
            self.error(
                "dangling_relationship",
                f"The {side} table '{schema}.{table_name}' is neither in the document nor registered.",
                (*location, f"{side}_table"),
            )
            return None
        column = next(
            (c for c in table.columns if c.action != REMOVE and c.values["column_name"].lower() == column_name.lower()),
            None,
        )
        if column is None:
            self.error(
                "dangling_relationship",
                f"The {side} column '{column_name}' does not exist in table '{table.name}'.",
                (*location, f"{side}_column"),
            )
            return None
        return table, column

    # --- glossary ---------------------------------------------------------------------------------------

    def _glossary(self) -> None:
        terms = self.document.business_glossary
        if terms is None:
            return
        current = {str(t.get("term", "")).lower(): t for t in self.existing.glossary}
        incoming = [_term_dict(t) for t in terms]
        actions: list[tuple[str, str]] = []
        if self.strategy == "create_only":
            result = list(self.existing.glossary)
            for index, term in enumerate(incoming):
                if term["term"].lower() in current:
                    self.error(
                        "glossary_term_exists",
                        f"The glossary term '{term['term']}' already exists. Choose merge or replace to change it.",
                        ("document", "business_glossary", index, "term"),
                    )
                    continue
                result.append(term)
                actions.append((CREATE, term["term"]))
        elif self.strategy == "merge":
            merged = dict(current)
            for term in incoming:
                key = term["term"].lower()
                before = current.get(key)
                actions.append((CREATE if before is None else (UNCHANGED if before == term else UPDATE), term["term"]))
                merged[key] = term
            result = list(merged.values())
        else:
            incoming_keys = {t["term"].lower() for t in incoming}
            for term in incoming:
                before = current.get(term["term"].lower())
                actions.append((CREATE if before is None else (UNCHANGED if before == term else UPDATE), term["term"]))
            actions.extend((REMOVE, str(t.get("term"))) for key, t in current.items() if key not in incoming_keys)
            result = incoming
        self.plan.glossary = result
        self.plan.glossary_actions = actions

    # --- resulting state -----------------------------------------------------------------------------------

    def target_state(self) -> SchemaState:
        tables = [
            TableState(
                key=t.key,
                schema_name=t.values["schema_name"],
                table_name=t.values["table_name"],
                is_active=t.values["is_active"],
                location=t.location,
                columns=[
                    ColumnState(
                        key=c.key,
                        column_name=c.values["column_name"],
                        data_type=c.values["data_type"],
                        is_primary_key=c.values["is_primary_key"],
                        is_foreign_key=c.values["is_foreign_key"],
                        is_nullable=c.values["is_nullable"],
                        is_sensitive=c.values["is_sensitive"],
                        is_active=c.values["is_active"],
                        location=c.location,
                    )
                    for c in t.columns
                    if c.action != REMOVE
                ],
            )
            for t in self.plan.tables
            if t.action != REMOVE
        ]
        relationships = [
            RelationshipState(
                key=r.key,
                source_table_key=r.source_table_key,
                source_column_key=r.source_column_key,
                target_table_key=r.target_table_key,
                target_column_key=r.target_column_key,
                relationship_type=r.values["relationship_type"],
                is_active=r.values["is_active"],
                location=r.location or ("relationships", r.label),
            )
            for r in self.plan.relationships
            if r.action != REMOVE
        ]
        return SchemaState(tables=tables, relationships=relationships)


def _column_values(column: ImportColumn) -> dict[str, Any]:
    return {
        "column_name": column.column_name,
        "data_type": column.data_type,
        "description": column.description,
        "business_meaning": column.business_meaning,
        "is_primary_key": column.is_primary_key,
        "is_foreign_key": column.is_foreign_key,
        "is_nullable": (not column.is_primary_key) if column.is_nullable is None else column.is_nullable,
        "is_sensitive": column.is_sensitive,
        "is_active": column.is_active,
    }


def _term_dict(term: GlossaryTerm) -> dict[str, Any]:
    return {"term": term.term, "definition": term.definition, "synonyms": list(term.synonyms)}


def _label(source_table: PlannedTable, source_column: PlannedColumn, target_table: PlannedTable, target_column: PlannedColumn) -> str:
    return (
        f"{source_table.values['table_name']}.{source_column.values['column_name']} → "
        f"{target_table.values['table_name']}.{target_column.values['column_name']}"
    )

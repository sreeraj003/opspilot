"""Schema metadata validation, independent of the database and of HTTP.

Every write to the registry (a single table or column edit, a relationship, a
whole import) is validated the same way: the service builds a ``SchemaState``
describing what the schema source *would* look like after the change and
passes it to ``validate_schema_state``. The state holds live (not deleted)
items only; inactive items are included and checked too, because they can be
reactivated.

Issues are structured (``code``, ``message``, ``location``, ``severity``) so the
frontend can show them next to the field or table they concern. ``error``
issues block the change; ``warning`` issues are reported but allowed.
"""

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.models.schema_registry import RELATIONSHIP_TYPES
from app.schema_registry.data_types import (
    DataTypeError,
    compatible_for_relationship,
    data_type_category,
    is_identifier,
    parse_data_type,
)

ERROR = "error"
WARNING = "warning"

Location = tuple[str | int, ...]


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    code: str
    message: str
    location: Location = ()
    severity: str = ERROR

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "location": [str(part) for part in self.location],
            "severity": self.severity,
        }

    @property
    def identity(self) -> tuple[str, Location]:
        return self.code, self.location


@dataclass(slots=True)
class ValidationReport:
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == ERROR]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == WARNING]

    @property
    def valid(self) -> bool:
        return not self.errors

    def new_errors_since(self, before: "ValidationReport") -> list[ValidationIssue]:
        """Errors that ``before`` did not have: what a change introduced."""
        existing = {issue.identity for issue in before.errors}
        return [issue for issue in self.errors if issue.identity not in existing]


@dataclass(slots=True)
class ColumnState:
    key: str
    column_name: str
    data_type: str
    is_primary_key: bool = False
    is_foreign_key: bool = False
    is_nullable: bool = True
    is_sensitive: bool = False
    is_active: bool = True
    location: Location = ()


@dataclass(slots=True)
class TableState:
    key: str
    schema_name: str
    table_name: str
    is_active: bool = True
    columns: list[ColumnState] = field(default_factory=list)
    location: Location = ()

    @property
    def qualified_name(self) -> str:
        return f"{self.schema_name}.{self.table_name}"

    @property
    def name_key(self) -> tuple[str, str]:
        return self.schema_name.lower(), self.table_name.lower()


@dataclass(slots=True)
class RelationshipState:
    key: str
    source_table_key: str
    source_column_key: str
    target_table_key: str
    target_column_key: str
    relationship_type: str
    is_active: bool = True
    location: Location = ()


@dataclass(slots=True)
class SchemaState:
    tables: list[TableState] = field(default_factory=list)
    relationships: list[RelationshipState] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class SchemaLimits:
    max_tables: int = 2000
    max_columns_per_table: int = 1000
    max_relationships: int = 5000


def table_location(table: TableState) -> Location:
    return table.location or ("tables", table.qualified_name)


def column_location(table: TableState, column: ColumnState) -> Location:
    return column.location or (*table_location(table), "columns", column.column_name)


def validate_schema_state(state: SchemaState, limits: SchemaLimits | None = None) -> ValidationReport:
    return _Validator(state, limits or SchemaLimits()).run()


class _Validator:
    def __init__(self, state: SchemaState, limits: SchemaLimits) -> None:
        self.state = state
        self.limits = limits
        self.report = ValidationReport()
        self.tables = {t.key: t for t in state.tables}
        self.columns: dict[str, tuple[TableState, ColumnState]] = {
            c.key: (t, c) for t in state.tables for c in t.columns
        }

    def add(self, code: str, message: str, location: Location, severity: str = ERROR) -> None:
        self.report.issues.append(ValidationIssue(code, message, location, severity))

    def run(self) -> ValidationReport:
        self._tables()
        self._relationships()
        return self.report

    # --- tables and columns --------------------------------------------------------------------

    def _tables(self) -> None:
        if len(self.state.tables) > self.limits.max_tables:
            self.add(
                "limit_exceeded",
                f"A schema can hold at most {self.limits.max_tables} tables; this one would have {len(self.state.tables)}.",
                ("tables",),
            )
        seen: dict[tuple[str, str], TableState] = {}
        for table in self.state.tables:
            location = table_location(table)
            for field_name, value in (("schema_name", table.schema_name), ("table_name", table.table_name)):
                if not is_identifier(value):
                    self.add("invalid_identifier", _identifier_message(field_name, value), (*location, field_name))
            first = seen.get(table.name_key)
            if first is not None:
                self.add(
                    "duplicate_table",
                    f"Table '{table.qualified_name}' is defined more than once (names are not case-sensitive).",
                    (*location, "table_name"),
                )
            else:
                seen[table.name_key] = table
            self._columns(table, location)

    def _columns(self, table: TableState, location: Location) -> None:
        if len(table.columns) > self.limits.max_columns_per_table:
            self.add(
                "limit_exceeded",
                f"A table can hold at most {self.limits.max_columns_per_table} columns; "
                f"'{table.qualified_name}' would have {len(table.columns)}.",
                (*location, "columns"),
            )
        if not table.columns:
            self.add("table_without_columns", f"Table '{table.qualified_name}' has no columns.", location, WARNING)
        names: set[str] = set()
        for column in table.columns:
            column_loc = column_location(table, column)
            if not is_identifier(column.column_name):
                self.add(
                    "invalid_identifier", _identifier_message("column_name", column.column_name), (*column_loc, "column_name")
                )
            lowered = column.column_name.lower()
            if lowered in names:
                self.add(
                    "duplicate_column",
                    f"Column '{column.column_name}' is defined more than once in '{table.qualified_name}' "
                    "(names are not case-sensitive).",
                    (*column_loc, "column_name"),
                )
            names.add(lowered)
            try:
                parse_data_type(column.data_type)
            except DataTypeError as exc:
                self.add("invalid_data_type", str(exc), (*column_loc, "data_type"))
            if column.is_primary_key and column.is_nullable:
                self.add(
                    "primary_key_nullable",
                    f"Primary-key column '{table.table_name}.{column.column_name}' cannot be nullable.",
                    (*column_loc, "is_nullable"),
                )
            if column.is_primary_key and not column.is_active and table.is_active:
                self.add(
                    "primary_key_inactive",
                    f"Primary-key column '{table.table_name}.{column.column_name}' is inactive.",
                    (*column_loc, "is_active"),
                    WARNING,
                )
        if table.columns and table.is_active and not any(c.is_primary_key for c in table.columns):
            self.add(
                "no_primary_key",
                f"Table '{table.qualified_name}' has no primary key. Joins on it may be ambiguous.",
                location,
                WARNING,
            )

    # --- relationships ---------------------------------------------------------------------------

    def _relationships(self) -> None:
        relationships = self.state.relationships
        if len(relationships) > self.limits.max_relationships:
            self.add(
                "limit_exceeded",
                f"A schema can hold at most {self.limits.max_relationships} relationships; "
                f"this one would have {len(relationships)}.",
                ("relationships",),
            )
        pairs: Counter[frozenset[str]] = Counter()
        referenced_fk_columns: set[str] = set()
        for relationship in relationships:
            location = relationship.location or ("relationships", relationship.key)
            if relationship.relationship_type not in RELATIONSHIP_TYPES:
                self.add(
                    "invalid_relationship_type",
                    f"Relationship type must be one of: {', '.join(RELATIONSHIP_TYPES)}.",
                    (*location, "relationship_type"),
                )
            ends = self._endpoints(relationship, location)
            if ends is None:
                continue
            (source_table, source_column), (target_table, target_column) = ends
            referenced_fk_columns.update({source_column.key, target_column.key})
            label = (
                f"{source_table.table_name}.{source_column.column_name} → "
                f"{target_table.table_name}.{target_column.column_name}"
            )
            if source_column.key == target_column.key:
                self.add(
                    "self_referencing_column",
                    f"Relationship {label} connects a column to itself.",
                    (*location, "target_column"),
                )
                continue
            pair = frozenset((source_column.key, target_column.key))
            pairs[pair] += 1
            if pairs[pair] > 1:
                self.add(
                    "duplicate_relationship",
                    f"Relationship {label} is already defined (in either direction).",
                    location,
                )
            if not compatible_for_relationship(source_column.data_type, target_column.data_type):
                self.add(
                    "incompatible_relationship_types",
                    f"Relationship {label} joins a {data_type_category(source_column.data_type)} column "
                    f"({source_column.data_type}) to a {data_type_category(target_column.data_type)} column "
                    f"({target_column.data_type}).",
                    location,
                )
            if relationship.is_active and not all(
                (source_table.is_active, source_column.is_active, target_table.is_active, target_column.is_active)
            ):
                self.add(
                    "relationship_inactive_endpoint",
                    f"Relationship {label} uses an inactive table or column; it is ignored until they are active.",
                    location,
                    WARNING,
                )
            referenced = _referenced_side(relationship.relationship_type, source_column, target_column)
            if referenced is not None and not referenced.is_primary_key:
                self.add(
                    "referenced_column_not_primary_key",
                    f"Relationship {label}: the referenced column '{referenced.column_name}' is not a primary key.",
                    location,
                    WARNING,
                )
        for table in self.state.tables:
            for column in table.columns:
                if column.is_foreign_key and column.key not in referenced_fk_columns:
                    self.add(
                        "foreign_key_without_relationship",
                        f"Column '{table.table_name}.{column.column_name}' is marked as a foreign key, "
                        "but no relationship uses it.",
                        column_location(table, column),
                        WARNING,
                    )

    def _endpoints(
        self, relationship: RelationshipState, location: Location
    ) -> tuple[tuple[TableState, ColumnState], tuple[TableState, ColumnState]] | None:
        ends: list[tuple[TableState, ColumnState]] = []
        for side, table_key, column_key in (
            ("source", relationship.source_table_key, relationship.source_column_key),
            ("target", relationship.target_table_key, relationship.target_column_key),
        ):
            table = self.tables.get(table_key)
            found = self.columns.get(column_key)
            if table is None or found is None:
                self.add(
                    "dangling_relationship",
                    f"The relationship's {side} table or column is not registered in this schema.",
                    (*location, f"{side}_column"),
                )
                return None
            if found[0].key != table.key:
                self.add(
                    "relationship_column_mismatch",
                    f"Column '{found[1].column_name}' does not belong to table '{table.qualified_name}'.",
                    (*location, f"{side}_column"),
                )
                return None
            ends.append(found)
        return ends[0], ends[1]


def _referenced_side(relationship_type: str, source: ColumnState, target: ColumnState) -> ColumnState | None:
    """The column a foreign key points at, for the types where one side is clearly referenced."""
    if relationship_type in ("many_to_one", "one_to_one"):
        return target
    if relationship_type == "one_to_many":
        return source
    return None


def _identifier_message(field_name: str, value: str) -> str:
    shown = value if len(value) <= 70 else f"{value[:70]}…"
    return (
        f"{field_name.replace('_', ' ').capitalize()} '{shown}' is not a valid identifier: use letters, digits and "
        "underscores, start with a letter or underscore, at most 63 characters."
    )


def issues_as_dicts(issues: Iterable[ValidationIssue]) -> list[dict[str, Any]]:
    return [issue.as_dict() for issue in issues]


def sort_issues(issues: Sequence[ValidationIssue]) -> list[ValidationIssue]:
    return sorted(issues, key=lambda i: (i.severity != ERROR, [str(p) for p in i.location], i.code))

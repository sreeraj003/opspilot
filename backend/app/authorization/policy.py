"""Permission policy definitions: a constrained, versioned, structured format.

Administrators describe what a permission allows (or denies) as JSON data. It
is never code: there are no expressions, no SQL fragments and nothing is ever
evaluated. Every policy is validated here and stored in *normalised* form
(defaults filled in, columns qualified with their table, value types made
explicit), so the engine and later modules (SQL security, Module 11) read one
unambiguous shape. See ``docs/PERMISSION_POLICY.md`` for the full schema.

Validation is both syntactic (shape, types, identifier syntax, limits) and
semantic (operators valid for the value type, column references inside the
permission's tables, contradictory or duplicate conditions). Schema-aware
checks (does the table/column exist, what is its type) go through a
``SchemaCatalog``; until Module 06 registers schemas the ``UnknownSchemaCatalog``
is used and only the policy itself is checked.
"""

import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Protocol

POLICY_VERSION = 1
DATA_RESOURCE_TYPE = "database_data"

ALLOW = "allow"
DENY = "deny"

# Operations on data. OpsPilot only ever reads: write operations do not exist in this vocabulary.
OPERATION_READ = "read"  # retrieve individual rows
OPERATION_AGGREGATE = "aggregate"  # aggregate results only (COUNT, SUM, ...)
DATA_OPERATIONS = (OPERATION_AGGREGATE, OPERATION_READ)

OPERATORS = (
    "equals",
    "not_equals",
    "in",
    "not_in",
    "greater_than",
    "greater_than_or_equal",
    "less_than",
    "less_than_or_equal",
    "between",
    "is_null",
    "is_not_null",
)
_NULL_OPERATORS = frozenset({"is_null", "is_not_null"})
_LIST_OPERATORS = frozenset({"in", "not_in"})
_RANGE_OPERATORS = frozenset({"greater_than", "greater_than_or_equal", "less_than", "less_than_or_equal"})

VALUE_TYPES = ("string", "number", "boolean", "date", "datetime")
# Only operators that make sense for the value type are accepted.
OPERATORS_BY_TYPE: dict[str, frozenset[str]] = {
    "string": frozenset({"equals", "not_equals", "in", "not_in", "is_null", "is_not_null"}),
    "boolean": frozenset({"equals", "not_equals", "is_null", "is_not_null"}),
    "number": frozenset(OPERATORS),
    "date": frozenset(OPERATORS),
    "datetime": frozenset(OPERATORS),
}
LOGIC_OPERATORS = ("and", "or")

MAX_TABLES = 50
MAX_COLUMNS = 500
MAX_LIST_VALUES = 100
MAX_STRING_VALUE_LENGTH = 500
_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]{0,62}"
_IDENTIFIER_RE = re.compile(rf"^{_IDENTIFIER}$")
# ``table`` or ``schema.table``.
_TABLE_RE = re.compile(rf"^{_IDENTIFIER}(\.{_IDENTIFIER})?$")


# --- errors -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PolicyIssue:
    path: tuple[str | int, ...]
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {"location": ["policy_definition", *[str(p) for p in self.path]], "message": self.message}


class PolicyValidationError(ValueError):
    """The policy is invalid. ``issues`` lists every problem found, with its location."""

    def __init__(self, issues: Sequence[PolicyIssue]) -> None:
        self.issues = list(issues)
        super().__init__("; ".join(f"{'.'.join(map(str, i.path)) or '(root)'}: {i.message}" for i in self.issues))


# --- schema catalog (Module 06 plug-in point) -------------------------------------------------


class SchemaCatalog(Protocol):
    """What the registered data schemas say about tables and columns.

    ``None`` means "not known": the check is skipped rather than failed, so
    policies can be written before schemas are registered. Module 06 provides
    a catalog backed by registered schemas; Module 11 re-validates everything
    against it at query time regardless.
    """

    def has_table(self, table: str) -> bool | None: ...

    def column_type(self, table: str, column: str) -> str | None:
        """One of ``VALUE_TYPES``, ``"unknown"`` for an existing column of another type, or None."""
        ...


class UnknownSchemaCatalog:
    def has_table(self, table: str) -> bool | None:
        return None

    def column_type(self, table: str, column: str) -> str | None:
        return None


@dataclass(frozen=True, slots=True)
class PolicyLimits:
    max_depth: int = 4
    max_conditions: int = 50


# --- public API ----------------------------------------------------------------------------


def validate_policy(
    resource_type: str,
    raw: Any,
    *,
    limits: PolicyLimits | None = None,
    catalog: SchemaCatalog | None = None,
) -> dict[str, Any]:
    """Validate ``raw`` for ``resource_type`` and return its normalised form, or raise ``PolicyValidationError``."""
    validator = _Validator(limits or PolicyLimits(), catalog or UnknownSchemaCatalog())
    result = validator.policy(resource_type, raw)
    if validator.issues:
        raise PolicyValidationError(validator.issues)
    return result


def validate_override(
    base: dict[str, Any],
    raw: Any,
    *,
    limits: PolicyLimits | None = None,
    catalog: SchemaCatalog | None = None,
) -> dict[str, Any]:
    """Validate a per-user override of a normalised data ``base`` policy. It may only narrow it.

    Returns the normalised override (which ``merge_override`` applies).
    """
    validator = _Validator(limits or PolicyLimits(), catalog or UnknownSchemaCatalog())
    result = validator.override(base, raw)
    if validator.issues:
        raise PolicyValidationError(validator.issues)
    return result


def merge_override(base: dict[str, Any], override: dict[str, Any] | None) -> dict[str, Any]:
    """The effective policy of a direct grant: ``base`` narrowed by a validated ``override``."""
    if not override or base.get("effect") != ALLOW or "allowed_tables" not in base:
        return base
    # Every step intersects with the base, so the result can never be broader than
    # the permission, even if the permission was narrowed after the override was saved.
    merged = dict(base)
    tables = list(base["allowed_tables"])
    if "allowed_tables" in override:
        tables = [t for t in tables if t in set(override["allowed_tables"])]
    base_columns = set(base["allowed_columns"])

    def covered(column: str) -> bool:
        table = split_column(column)[0]
        return table in tables and (column in base_columns or f"{table}.*" in base_columns)

    columns = override.get("allowed_columns", base["allowed_columns"])
    merged["allowed_tables"] = tables
    merged["allowed_columns"] = [c for c in columns if covered(c)]
    merged["allowed_operations"] = [
        o for o in override.get("allowed_operations", base["allowed_operations"]) if o in base["allowed_operations"]
    ]
    merged["row_filters"] = [
        f for f in [*base["row_filters"], *override.get("row_filters", [])] if node_table(f) in tables
    ]
    for flag in ("allow_export", "allow_sql_view"):
        if flag in override:
            merged[flag] = merged[flag] and override[flag]
    return merged


def system_policy() -> dict[str, Any]:
    return {"version": POLICY_VERSION, "effect": ALLOW}


def split_column(qualified: str) -> tuple[str, str]:
    """``schema.table.column`` -> (``schema.table``, ``column``). Columns are always stored qualified."""
    table, _, column = qualified.rpartition(".")
    return table, column


def node_table(node: dict[str, Any]) -> str:
    """The single table a normalised row-filter node refers to."""
    if "column" in node:
        return split_column(node["column"])[0]
    return node_table(node["conditions"][0])


def iter_conditions(nodes: Iterable[dict[str, Any]]) -> Iterable[dict[str, Any]]:
    for node in nodes:
        if "column" in node:
            yield node
        else:
            yield from iter_conditions(node["conditions"])


# --- implementation ------------------------------------------------------------------------


@dataclass
class _Validator:
    limits: PolicyLimits
    catalog: SchemaCatalog
    issues: list[PolicyIssue] = field(default_factory=list)
    _condition_count: int = 0

    def fail(self, path: tuple[str | int, ...], message: str) -> None:
        self.issues.append(PolicyIssue(path, message))

    # --- top level ---------------------------------------------------------------------

    def policy(self, resource_type: str, raw: Any) -> dict[str, Any]:
        if not isinstance(raw, dict):
            self.fail((), "The policy must be a JSON object.")
            return {}
        version = raw.get("version", POLICY_VERSION)
        if version != POLICY_VERSION or isinstance(version, bool):
            self.fail(("version",), f"Unsupported policy version. The current version is {POLICY_VERSION}.")
        effect = raw.get("effect", ALLOW)
        if effect not in (ALLOW, DENY):
            self.fail(("effect",), "effect must be 'allow' or 'deny'.")
            return {}
        if resource_type == DATA_RESOURCE_TYPE:
            return self._data_allow(raw) if effect == ALLOW else self._data_deny(raw)
        return self._generic(raw, effect)

    def _known_keys(self, raw: dict[str, Any], allowed: Iterable[str], path: tuple[str | int, ...] = ()) -> None:
        allowed_set = set(allowed)
        for key in raw:
            if key not in allowed_set:
                self.fail((*path, str(key)), f"Unknown field '{key}' is not allowed here.")

    def _bool(self, raw: dict[str, Any], key: str, default: bool = False) -> bool:
        value = raw.get(key, default)
        if not isinstance(value, bool):
            self.fail((key,), f"{key} must be true or false.")
            return default
        return value

    def _generic(self, raw: dict[str, Any], effect: str) -> dict[str, Any]:
        if effect == ALLOW:
            self._known_keys(raw, ("version", "effect", "allow_export", "allow_sql_view"))
            return {
                "version": POLICY_VERSION,
                "effect": ALLOW,
                "allow_export": self._bool(raw, "allow_export"),
                "allow_sql_view": self._bool(raw, "allow_sql_view"),
            }
        self._known_keys(raw, ("version", "effect", "deny_all", "deny_export", "deny_sql_view"))
        result = {
            "version": POLICY_VERSION,
            "effect": DENY,
            "deny_all": self._bool(raw, "deny_all"),
            "deny_export": self._bool(raw, "deny_export"),
            "deny_sql_view": self._bool(raw, "deny_sql_view"),
        }
        if not (result["deny_all"] or result["deny_export"] or result["deny_sql_view"]):
            self.fail((), "A deny policy must deny something: set deny_all, deny_export or deny_sql_view.")
        return result

    # --- data: allow -------------------------------------------------------------------------

    def _data_allow(self, raw: dict[str, Any]) -> dict[str, Any]:
        self._known_keys(
            raw,
            (
                "version",
                "effect",
                "allowed_tables",
                "allowed_columns",
                "allowed_operations",
                "row_filters",
                "allow_export",
                "allow_sql_view",
            ),
        )
        tables = self._tables(raw.get("allowed_tables"), ("allowed_tables",), required=True)
        columns = self._allowed_columns(raw.get("allowed_columns"), tables, ("allowed_columns",))
        operations = self._operations(raw.get("allowed_operations", list(DATA_OPERATIONS)), ("allowed_operations",))
        filters = self._row_filters(raw.get("row_filters", []), tables, ("row_filters",))
        return {
            "version": POLICY_VERSION,
            "effect": ALLOW,
            "allowed_tables": tables,
            "allowed_columns": columns,
            "allowed_operations": operations,
            "row_filters": filters,
            "allow_export": self._bool(raw, "allow_export"),
            "allow_sql_view": self._bool(raw, "allow_sql_view"),
        }

    def _tables(self, value: Any, path: tuple[str | int, ...], *, required: bool) -> list[str]:
        if value is None and not required:
            return []
        if not isinstance(value, list) or (required and not value):
            self.fail(path, "List at least one table." if required else "Must be a list of table names.")
            return []
        if len(value) > MAX_TABLES:
            self.fail(path, f"At most {MAX_TABLES} tables are allowed.")
            return []
        tables: list[str] = []
        for index, table in enumerate(value):
            if not isinstance(table, str) or not _TABLE_RE.match(table):
                self.fail((*path, index), "Table names are identifiers such as 'sales' or 'finance.sales'.")
                continue
            if table in tables:
                self.fail((*path, index), f"Table '{table}' is listed twice.")
                continue
            if self.catalog.has_table(table) is False:
                self.fail((*path, index), f"Table '{table}' is not a registered table.")
            tables.append(table)
        return tables

    def _resolve_column(
        self, reference: Any, tables: list[str], path: tuple[str | int, ...], *, allow_star: bool
    ) -> str | None:
        """Qualify ``reference`` against ``tables``. Returns ``table.column`` / ``table.*`` or None (error recorded)."""
        if not isinstance(reference, str) or not reference:
            self.fail(path, "Column references are strings such as 'amount' or 'sales.amount'.")
            return None
        for table in sorted(tables, key=len, reverse=True):
            if reference.startswith(table + "."):
                column = reference[len(table) + 1 :]
                break
        else:
            if "." in reference:
                self.fail(path, f"'{reference}' refers to a table that is not listed in allowed_tables.")
                return None
            if len(tables) != 1:
                self.fail(path, f"Qualify '{reference}' with its table (for example 'table.{reference}').")
                return None
            table, column = tables[0], reference
        if column == "*":
            if not allow_star:
                self.fail(path, "'*' is not allowed here; name a column.")
                return None
            return f"{table}.*"
        if not _IDENTIFIER_RE.match(column):
            self.fail(path, f"'{column}' is not a valid column name.")
            return None
        if self.catalog.column_type(table, column) is None and self.catalog.has_table(table) is True:
            self.fail(path, f"Column '{column}' does not exist in table '{table}'.")
            return None
        return f"{table}.{column}"

    def _allowed_columns(self, value: Any, tables: list[str], path: tuple[str | int, ...]) -> list[str]:
        if not isinstance(value, list) or not value:
            self.fail(path, "List the columns this permission allows (use 'table.*' for every column of a table).")
            return []
        if len(value) > MAX_COLUMNS:
            self.fail(path, f"At most {MAX_COLUMNS} columns are allowed.")
            return []
        if not tables:
            return []
        resolved: list[str] = []
        for index, item in enumerate(value):
            if item == "*":
                resolved.extend(f"{t}.*" for t in tables)
                continue
            column = self._resolve_column(item, tables, (*path, index), allow_star=True)
            if column is None:
                continue
            if column in resolved:
                self.fail((*path, index), f"Column '{column}' is listed twice.")
                continue
            resolved.append(column)
        wildcard_tables = {split_column(c)[0] for c in resolved if c.endswith(".*")}
        result = sorted({c for c in resolved if c.endswith(".*") or split_column(c)[0] not in wildcard_tables})
        for table in tables:
            if not any(split_column(c)[0] == table for c in result):
                self.fail(path, f"No columns are allowed for table '{table}'. Add columns or '{table}.*'.")
        return result

    def _operations(self, value: Any, path: tuple[str | int, ...]) -> list[str]:
        if not isinstance(value, list) or not value:
            self.fail(path, f"List at least one operation: {', '.join(DATA_OPERATIONS)}.")
            return []
        operations: set[str] = set()
        for index, operation in enumerate(value):
            if operation not in DATA_OPERATIONS:
                self.fail((*path, index), f"Unsupported operation. Use one of: {', '.join(DATA_OPERATIONS)}.")
            else:
                operations.add(operation)
        return sorted(operations)

    # --- data: deny ------------------------------------------------------------------------

    def _data_deny(self, raw: dict[str, Any]) -> dict[str, Any]:
        self._known_keys(
            raw,
            (
                "version",
                "effect",
                "deny_all",
                "denied_tables",
                "denied_columns",
                "excluded_rows",
                "deny_export",
                "deny_sql_view",
            ),
        )
        denied_tables = self._tables(raw.get("denied_tables"), ("denied_tables",), required=False)
        denied_columns: list[str] = []
        columns_value = raw.get("denied_columns", [])
        if not isinstance(columns_value, list):
            self.fail(("denied_columns",), "Must be a list of 'table.column' references.")
            columns_value = []
        for index, reference in enumerate(columns_value):
            path = ("denied_columns", index)
            table, column = split_column(reference) if isinstance(reference, str) else ("", "")
            if not table or not _TABLE_RE.match(table):
                self.fail(path, "Denied columns are written as 'table.column'.")
                continue
            if column == "*":
                if table not in denied_tables:
                    denied_tables.append(table)
                continue
            if not _IDENTIFIER_RE.match(column):
                self.fail(path, f"'{column}' is not a valid column name.")
                continue
            if f"{table}.{column}" in denied_columns:
                self.fail(path, f"Column '{reference}' is listed twice.")
                continue
            denied_columns.append(f"{table}.{column}")
        excluded = self._exclusions(raw.get("excluded_rows", []), ("excluded_rows",))
        result = {
            "version": POLICY_VERSION,
            "effect": DENY,
            "deny_all": self._bool(raw, "deny_all"),
            "denied_tables": sorted(denied_tables),
            "denied_columns": sorted(c for c in denied_columns if split_column(c)[0] not in denied_tables),
            "excluded_rows": excluded,
            "deny_export": self._bool(raw, "deny_export"),
            "deny_sql_view": self._bool(raw, "deny_sql_view"),
        }
        if not (
            result["deny_all"]
            or result["denied_tables"]
            or result["denied_columns"]
            or result["excluded_rows"]
            or result["deny_export"]
            or result["deny_sql_view"]
        ):
            self.fail(
                (),
                "A deny policy must deny something: set deny_all, denied_tables, denied_columns, "
                "excluded_rows, deny_export or deny_sql_view.",
            )
        return result

    def _exclusions(self, value: Any, path: tuple[str | int, ...]) -> list[dict[str, Any]]:
        """Row exclusions: every column is written ``table.column``; each entry concerns one table."""
        if not isinstance(value, list):
            self.fail(path, "Must be a list of conditions.")
            return []
        tables = sorted(
            {
                split_column(c["column"])[0]
                for c in _raw_conditions(value)
                if isinstance(c.get("column"), str) and "." in c["column"]
            }
        )
        for condition_path, condition in _raw_conditions_with_path(value, path):
            column = condition.get("column")
            if not isinstance(column, str) or "." not in column:
                self.fail((*condition_path, "column"), "Excluded-row conditions name columns as 'table.column'.")
        return self._row_filters(value, tables, path)

    # --- row filters --------------------------------------------------------------------------

    def _row_filters(self, value: Any, tables: list[str], path: tuple[str | int, ...]) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            self.fail(path, "row_filters must be a list of conditions or groups (combined with AND).")
            return []
        self._condition_count = 0
        nodes = [self._node(item, tables, (*path, index), depth=1) for index, item in enumerate(value)]
        result = [n for n in nodes if n is not None]
        if self._condition_count > self.limits.max_conditions:
            self.fail(path, f"Too many conditions ({self._condition_count}); the limit is {self.limits.max_conditions}.")
        for index, node in enumerate(result):
            if len({split_column(c["column"])[0] for c in iter_conditions([node])}) > 1:
                self.fail((*path, index), "Each top-level filter must refer to columns of a single table.")
        self._check_conjunction(result, path)
        return result

    def _node(self, raw: Any, tables: list[str], path: tuple[str | int, ...], depth: int) -> dict[str, Any] | None:
        if not isinstance(raw, dict):
            self.fail(path, "Each filter is an object: a condition or a group.")
            return None
        if "logic" in raw or "conditions" in raw:
            return self._group(raw, tables, path, depth)
        return self._condition(raw, tables, path)

    def _group(self, raw: dict[str, Any], tables: list[str], path: tuple[str | int, ...], depth: int) -> dict[str, Any] | None:
        self._known_keys(raw, ("logic", "conditions"), path)
        if depth > self.limits.max_depth:
            self.fail(path, f"Groups are nested too deeply; the limit is {self.limits.max_depth} levels.")
            return None
        logic = raw.get("logic")
        if not isinstance(logic, str) or logic.lower() not in LOGIC_OPERATORS:
            self.fail((*path, "logic"), "logic must be 'and' or 'or'.")
            return None
        children_raw = raw.get("conditions")
        if not isinstance(children_raw, list) or not children_raw:
            self.fail((*path, "conditions"), "A group needs at least one condition.")
            return None
        children = [
            self._node(item, tables, (*path, "conditions", index), depth + 1) for index, item in enumerate(children_raw)
        ]
        if any(child is None for child in children):
            return None
        valid = [c for c in children if c is not None]
        if logic.lower() == "and":
            self._check_conjunction(valid, (*path, "conditions"))
        else:
            self._check_duplicates(valid, (*path, "conditions"))
        return {"logic": logic.lower(), "conditions": valid}

    def _condition(self, raw: dict[str, Any], tables: list[str], path: tuple[str | int, ...]) -> dict[str, Any] | None:
        self._known_keys(raw, ("column", "operator", "value", "value_type"), path)
        self._condition_count += 1
        column = self._resolve_column(raw.get("column"), tables, (*path, "column"), allow_star=False)
        operator = raw.get("operator")
        if operator not in OPERATORS:
            self.fail((*path, "operator"), f"Unsupported operator. Use one of: {', '.join(OPERATORS)}.")
            return None
        declared = raw.get("value_type")
        if declared is not None and declared not in VALUE_TYPES:
            self.fail((*path, "value_type"), f"value_type must be one of: {', '.join(VALUE_TYPES)}.")
            return None
        catalog_type = None
        if column is not None:
            table, name = split_column(column)
            catalog_type = self.catalog.column_type(table, name)
        if operator in _NULL_OPERATORS:
            if raw.get("value") is not None:
                self.fail((*path, "value"), f"'{operator}' takes no value.")
                return None
            if column is None:
                return None
            node: dict[str, Any] = {"column": column, "operator": operator}
            if declared or (catalog_type in VALUE_TYPES):
                node["value_type"] = declared or catalog_type
            return node
        if "value" not in raw:
            self.fail((*path, "value"), f"'{operator}' needs a value.")
            return None
        value_type, value = self._value(operator, raw["value"], declared, (*path, "value"))
        if value_type is None:
            return None
        if catalog_type is not None and catalog_type != value_type:
            self.fail(
                (*path, "value"),
                f"Column '{column}' holds {catalog_type} values; this condition compares it with a {value_type}.",
            )
            return None
        if operator not in OPERATORS_BY_TYPE[value_type]:
            allowed = ", ".join(o for o in OPERATORS if o in OPERATORS_BY_TYPE[value_type])
            self.fail((*path, "operator"), f"'{operator}' cannot be used with {value_type} values. Use one of: {allowed}.")
            return None
        if column is None:
            return None
        return {"column": column, "operator": operator, "value": value, "value_type": value_type}

    def _value(self, operator: str, value: Any, declared: str | None, path: tuple[str | int, ...]) -> tuple[str | None, Any]:
        if operator in _LIST_OPERATORS or operator == "between":
            if not isinstance(value, list):
                self.fail(path, f"'{operator}' needs a list of values.")
                return None, None
            if operator == "between" and len(value) != 2:
                self.fail(path, "'between' needs exactly two values: [low, high].")
                return None, None
            if not 1 <= len(value) <= MAX_LIST_VALUES:
                self.fail(path, f"Give between 1 and {MAX_LIST_VALUES} values.")
                return None, None
            typed = [self._scalar(item, declared, (*path, index)) for index, item in enumerate(value)]
            if any(t is None for t, _ in typed):
                return None, None
            types = {t for t, _ in typed}
            if len(types) != 1:
                self.fail(path, "All values must have the same type.")
                return None, None
            value_type = types.pop()
            values = [v for _, v in typed]
            if operator == "between":
                if value_type not in ("number", "date", "datetime"):
                    self.fail(path, "'between' compares numbers, dates or datetimes.")
                    return None, None
                if _comparable(value_type, values[0]) > _comparable(value_type, values[1]):
                    self.fail(path, "The first value of 'between' must not be greater than the second.")
                    return None, None
            elif len(set(map(repr, values))) != len(values):
                self.fail(path, "The list contains duplicate values.")
                return None, None
            return value_type, values
        if isinstance(value, list | dict):
            self.fail(path, f"'{operator}' needs a single value.")
            return None, None
        return self._scalar(value, declared, path)

    def _scalar(self, value: Any, declared: str | None, path: tuple[str | int, ...]) -> tuple[str | None, Any]:
        if value is None:
            self.fail(path, "Use is_null / is_not_null to compare with null.")
            return None, None
        if isinstance(value, bool):
            inferred = "boolean"
        elif isinstance(value, int | float):
            if isinstance(value, float) and not math.isfinite(value):
                self.fail(path, "Numbers must be finite.")
                return None, None
            inferred = "number"
        elif isinstance(value, str):
            if len(value) > MAX_STRING_VALUE_LENGTH or "\x00" in value:
                self.fail(path, f"Text values are at most {MAX_STRING_VALUE_LENGTH} characters.")
                return None, None
            inferred = "string"
        else:
            self.fail(path, "Values are text, numbers, true/false, dates or datetimes.")
            return None, None
        value_type = declared or inferred
        if value_type in ("date", "datetime"):
            if not isinstance(value, str) or _parse_temporal(value_type, value) is None:
                example = "2026-01-31" if value_type == "date" else "2026-01-31T09:00:00+00:00"
                self.fail(path, f"Write {value_type} values in ISO 8601 form, for example '{example}'.")
                return None, None
            return value_type, value
        if value_type != inferred:
            self.fail(path, f"The value is not a {value_type}.")
            return None, None
        return value_type, value

    # --- contradiction and duplicate detection ----------------------------------------------------

    def _check_duplicates(self, nodes: list[dict[str, Any]], path: tuple[str | int, ...]) -> None:
        seen: set[str] = set()
        for index, node in enumerate(nodes):
            signature = repr(_canonical(node))
            if signature in seen:
                self.fail((*path, index), "This condition duplicates another one in the same group.")
            seen.add(signature)

    def _check_conjunction(self, nodes: list[dict[str, Any]], path: tuple[str | int, ...]) -> None:
        """AND-combined siblings: duplicates and conditions that can never hold together."""
        self._check_duplicates(nodes, path)
        by_column: dict[str, list[dict[str, Any]]] = {}
        for node in nodes:
            if "column" in node:
                by_column.setdefault(node["column"], []).append(node)
        for column, conditions in by_column.items():
            problem = _contradiction(conditions)
            if problem:
                self.fail(path, f"Conditions on '{column}' contradict each other: {problem}")

    # --- overrides ------------------------------------------------------------------------------

    def override(self, base: dict[str, Any], raw: Any) -> dict[str, Any]:
        if not isinstance(raw, dict):
            self.fail((), "The override must be a JSON object.")
            return {}
        if base.get("effect") != ALLOW or "allowed_tables" not in base:
            self.fail((), "Only permissions that allow data access can be narrowed with an override.")
            return {}
        self._known_keys(
            raw, ("allowed_tables", "allowed_columns", "allowed_operations", "row_filters", "allow_export", "allow_sql_view")
        )
        result: dict[str, Any] = {}
        tables = list(base["allowed_tables"])
        if "allowed_tables" in raw:
            narrowed = self._tables(raw["allowed_tables"], ("allowed_tables",), required=True)
            for index, table in enumerate(narrowed):
                if table not in base["allowed_tables"]:
                    self.fail(("allowed_tables", index), f"'{table}' is not allowed by the permission itself.")
            tables = [t for t in narrowed if t in base["allowed_tables"]]
            result["allowed_tables"] = tables
        if "allowed_columns" in raw:
            columns = self._allowed_columns(raw["allowed_columns"], tables, ("allowed_columns",))
            for column in columns:
                table, name = split_column(column)
                if column not in base["allowed_columns"] and f"{table}.*" not in base["allowed_columns"]:
                    self.fail(("allowed_columns",), f"'{column}' is not allowed by the permission itself.")
                if name == "*" and column not in base["allowed_columns"]:
                    self.fail(("allowed_columns",), f"'{column}' is broader than the permission itself.")
            result["allowed_columns"] = columns
        if "allowed_operations" in raw:
            operations = self._operations(raw["allowed_operations"], ("allowed_operations",))
            for operation in operations:
                if operation not in base["allowed_operations"]:
                    self.fail(("allowed_operations",), f"'{operation}' is not allowed by the permission itself.")
            result["allowed_operations"] = operations
        if "row_filters" in raw:
            result["row_filters"] = self._row_filters(raw["row_filters"], tables, ("row_filters",))
            combined = [*[f for f in base["row_filters"] if node_table(f) in tables], *result["row_filters"]]
            self._check_conjunction(combined, ("row_filters",))
        for flag in ("allow_export", "allow_sql_view"):
            if flag in raw:
                value = self._bool(raw, flag)
                if value and not base[flag]:
                    self.fail((flag,), f"An override cannot turn on {flag}.")
                result[flag] = value
        if not result:
            self.fail((), "The override is empty.")
        return result


# --- helpers ----------------------------------------------------------------------------------


def _parse_temporal(value_type: str, value: str) -> date | datetime | None:
    try:
        if value_type == "date":
            return date.fromisoformat(value) if len(value) == 10 else None
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    # Datetimes must carry an offset: a naive timestamp is ambiguous.
    return parsed if parsed.tzinfo is not None else None


def _comparable(value_type: str, value: Any) -> Any:
    if value_type in ("date", "datetime"):
        return _parse_temporal(value_type, value)
    return value


def _canonical(node: dict[str, Any]) -> Any:
    if "column" in node:
        value = node.get("value")
        if node["operator"] in _LIST_OPERATORS and isinstance(value, list):
            value = sorted(value, key=repr)
        return ("c", node["column"], node["operator"], repr(value))
    return ("g", node["logic"], sorted((repr(_canonical(c)) for c in node["conditions"])))


def _contradiction(conditions: list[dict[str, Any]]) -> str | None:
    """A reason the AND of ``conditions`` (all on one column) can never be true, if detectable."""
    operators = {c["operator"] for c in conditions}
    if "is_null" in operators and len(operators) > 1:
        return "a column cannot be null and also compared with a value."
    types = {c["value_type"] for c in conditions if "value" in c}
    if len(types) > 1:
        return "the conditions compare the column with values of different types."
    value_type = types.pop() if types else None
    equals = [c["value"] for c in conditions if c["operator"] == "equals"]
    if len({repr(v) for v in equals}) > 1:
        return "it cannot equal two different values."
    for c in conditions:
        if c["operator"] == "not_equals" and repr(c["value"]) in {repr(v) for v in equals}:
            return "it cannot both equal and not equal the same value."
    allowed: set[str] | None = None
    for c in conditions:
        if c["operator"] == "in":
            values = {repr(v) for v in c["value"]}
            allowed = values if allowed is None else allowed & values
    excluded = {repr(v) for c in conditions if c["operator"] == "not_in" for v in c["value"]}
    excluded |= {repr(c["value"]) for c in conditions if c["operator"] == "not_equals"}
    if equals:
        allowed = {repr(equals[0])} if allowed is None else allowed & {repr(equals[0])}
    if allowed is not None and not (allowed - excluded):
        return "no value satisfies all of them."
    if value_type in ("number", "date", "datetime"):
        low: tuple[Any, bool] | None = None  # (bound, inclusive)
        high: tuple[Any, bool] | None = None

        def tighten_low(bound: Any, inclusive: bool) -> None:
            nonlocal low
            if low is None or bound > low[0] or (bound == low[0] and not inclusive):
                low = (bound, inclusive)

        def tighten_high(bound: Any, inclusive: bool) -> None:
            nonlocal high
            if high is None or bound < high[0] or (bound == high[0] and not inclusive):
                high = (bound, inclusive)

        for c in conditions:
            op = c["operator"]
            if op in _RANGE_OPERATORS or op in ("equals", "between"):
                if op == "between":
                    tighten_low(_comparable(value_type, c["value"][0]), True)
                    tighten_high(_comparable(value_type, c["value"][1]), True)
                    continue
                bound = _comparable(value_type, c["value"])
                if op in ("greater_than", "greater_than_or_equal", "equals"):
                    tighten_low(bound, op != "greater_than")
                if op in ("less_than", "less_than_or_equal", "equals"):
                    tighten_high(bound, op != "less_than")
        if low is not None and high is not None:
            if low[0] > high[0] or (low[0] == high[0] and not (low[1] and high[1])):
                return "the ranges do not overlap."
    return None


def _raw_conditions(nodes: list[Any]) -> Iterable[dict[str, Any]]:
    for _, condition in _raw_conditions_with_path(nodes, ()):
        yield condition


def _raw_conditions_with_path(nodes: Any, path: tuple[str | int, ...]) -> Iterable[tuple[tuple[str | int, ...], dict[str, Any]]]:
    if not isinstance(nodes, list):
        return
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            continue
        if "logic" in node or "conditions" in node:
            yield from _raw_conditions_with_path(node.get("conditions"), (*path, index, "conditions"))
        else:
            yield (*path, index), node

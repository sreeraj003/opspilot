"""Structural validation of a company API result (after Module 07's response contract accepted it).

Module 07 already guarantees a JSON array of JSON objects (or a reported
failure). This validator adds what a *table* needs, and rejects anything
else instead of repairing it:

* every row has exactly the same column names (rows are never padded and
  missing fields are never invented);
* column names are non-empty strings without control characters;
* at most ``max_columns`` columns;
* every value is JSON (null, boolean, number, string, array, object) with no
  NUL or other binary control characters in strings, strings no longer than
  ``max_cell_length`` and nesting no deeper than ``max_nesting_depth``;
* when the envelope's metadata describes the columns
  (``metadata.columns: [{"name": ..., "type": ...}]``), it is well formed and
  names exactly the returned columns.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.ai.query_execution.policies.failures import ExecutionErrorCategory
from app.ai.query_execution.policies.limits import ExecutionLimits

_MAX_COLUMN_NAME_LENGTH = 128
# Control characters other than tab, newline and carriage return mark binary data.
_BINARY_CHARS = frozenset(chr(c) for c in range(32) if chr(c) not in "\t\n\r") | {"\x7f"}


class ResultValidationError(Exception):
    """The result cannot be used as returned. ``message`` is safe to show and to store."""

    def __init__(self, category: ExecutionErrorCategory, message: str) -> None:
        super().__init__(message)
        self.category = category
        self.message = message


def _structure(message: str) -> ResultValidationError:
    return ResultValidationError(ExecutionErrorCategory.INVALID_RESPONSE_STRUCTURE, message)


def _too_large(message: str) -> ResultValidationError:
    return ResultValidationError(ExecutionErrorCategory.RESPONSE_TOO_LARGE, message)


@dataclass(frozen=True, slots=True)
class DeclaredColumn:
    name: str
    type: str | None


def _check_name(name: object) -> str:
    if not isinstance(name, str) or not name or len(name) > _MAX_COLUMN_NAME_LENGTH or set(name) & _BINARY_CHARS:
        raise _structure("The result has a column with an empty, too long or invalid name.")
    return name


def check_value(value: Any, limits: ExecutionLimits, depth: int = 0) -> None:
    if value is None or isinstance(value, (bool, int, Decimal)):
        return
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise _structure("The result contains a number that is not finite.")
        return
    if isinstance(value, str):
        if len(value) > limits.max_cell_length:
            raise _too_large(f"A value in the result is longer than the allowed {limits.max_cell_length} characters.")
        if set(value) & _BINARY_CHARS:
            raise ResultValidationError(
                ExecutionErrorCategory.NORMALIZATION_FAILED, "The result contains binary data, which is not supported."
            )
        return
    if isinstance(value, (list, dict)):
        if depth >= limits.max_nesting_depth:
            raise _structure(f"A value in the result is nested deeper than the allowed {limits.max_nesting_depth} levels.")
        items = value.values() if isinstance(value, dict) else value
        if isinstance(value, dict):
            for key in value:
                if not isinstance(key, str) or set(key) & _BINARY_CHARS:
                    raise _structure("A value in the result has an invalid object key.")
        for item in items:
            check_value(item, limits, depth + 1)
        return
    raise _structure("The result contains a value that is not JSON.")


def declared_columns(metadata: dict[str, Any] | None) -> list[DeclaredColumn] | None:
    """The column description in the envelope's metadata, if the company API sends one."""
    if not metadata or "columns" not in metadata:
        return None
    raw = metadata["columns"]
    if not isinstance(raw, list):
        raise _structure("The result's column metadata is not a list.")
    columns: list[DeclaredColumn] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict) or "name" not in item:
            raise _structure("The result's column metadata is not in the expected format.")
        name = _check_name(item["name"])
        declared = item.get("type", item.get("data_type"))
        if declared is not None and (not isinstance(declared, str) or len(declared) > 64):
            raise _structure("The result's column metadata has an invalid type.")
        if name in seen:
            raise _structure("The result's column metadata lists a column twice.")
        seen.add(name)
        columns.append(DeclaredColumn(name, declared))
    return columns


def validate_table(
    rows: list[dict[str, Any]], metadata: dict[str, Any] | None, limits: ExecutionLimits
) -> tuple[list[str], dict[str, str | None]]:
    """Return the column order and declared types; raise ``ResultValidationError`` for anything unusable."""
    declared = declared_columns(metadata)
    if rows:
        first = rows[0]
        columns = [_check_name(name) for name in first]
        expected = set(columns)
        for index, row in enumerate(rows[1:], start=2):
            if not isinstance(row, dict) or set(row) != expected:
                raise _structure(f"Result row {index} does not have the same columns as the first row.")
        if declared is not None:
            if {c.name for c in declared} != expected:
                raise _structure("The result's column metadata does not match the returned columns.")
            columns = [c.name for c in declared]
    else:
        columns = [c.name for c in declared] if declared is not None else []
    if len(columns) > limits.max_columns:
        raise _too_large(f"The result has {len(columns)} columns, more than the allowed {limits.max_columns}.")
    for row in rows:
        for value in row.values():
            check_value(value, limits)
    types = {c.name: c.type for c in declared} if declared is not None else {}
    return columns, types

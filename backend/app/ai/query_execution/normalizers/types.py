"""Predictable data type normalisation for result columns.

Types: ``string``, ``integer``, ``decimal``, ``float``, ``boolean``, ``date``,
``datetime``, ``json``, ``null`` (every value is null) and ``unknown`` (a
safe fallback: the column mixes kinds of values, which are passed through
unchanged).

How a column's type is chosen:

1. **Declared by the company API** (``metadata.columns[].type``): the
   declared type is mapped (for example ``numeric(12,2)`` -> decimal,
   ``timestamptz`` -> datetime) and every value must conform to it. A value
   that does not is an error; it is never coerced into something else.
   Binary types (``bytea``, ``blob``, ``varbinary``...) are rejected.
2. **Inferred** from *all* values of the column, never from one value:
   all integers -> integer; integers and non-integers -> decimal; all
   booleans -> boolean; strings become ``date`` / ``datetime`` only when
   every non-null value is a strict ISO 8601 date / date-time, otherwise
   they stay strings (``"2026-10"`` or ``"12345"`` are not reinterpreted).

Precision: JSON numbers are parsed as exact ``int`` / ``Decimal`` values.
In the normalised JSON, integers within +/-(2^53 - 1) are numbers and larger
ones are strings; decimals are always strings ("125000.10", trailing zeros
kept) so a browser never rounds money. Floats are numbers. Date-times with a
timezone are converted to UTC (``...Z``); date-times without one stay naive and
the column says so (``timezone: "naive"``).
"""

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.ai.query_execution.policies.failures import ExecutionErrorCategory
from app.ai.query_execution.validators.response import ResultValidationError

MAX_SAFE_INTEGER = 2**53 - 1

DataType = str  # one of TYPES
TYPES = ("string", "integer", "decimal", "float", "boolean", "date", "datetime", "json", "null", "unknown")

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d{1,6})?)?(Z|[+-]\d{2}:?\d{2})?$")
_INTEGER_TEXT = re.compile(r"^-?\d{1,4000}$")
_DECIMAL_TEXT = re.compile(r"^-?\d+(\.\d+)?([eE][+-]?\d+)?$")

_DECLARED: dict[str, DataType] = {}
for _name in ("int", "integer", "int2", "int4", "int8", "smallint", "bigint", "tinyint", "mediumint", "serial",
              "bigserial", "long"):
    _DECLARED[_name] = "integer"
for _name in ("numeric", "decimal", "number", "money", "smallmoney", "dec"):
    _DECLARED[_name] = "decimal"
for _name in ("float", "float4", "float8", "double", "double precision", "real"):
    _DECLARED[_name] = "float"
for _name in ("bool", "boolean", "bit"):
    _DECLARED[_name] = "boolean"
_DECLARED["date"] = "date"
for _name in ("timestamp", "timestamptz", "timestamp with time zone", "timestamp without time zone", "datetime",
              "datetime2", "datetimeoffset", "smalldatetime"):
    _DECLARED[_name] = "datetime"
for _name in ("string", "text", "varchar", "nvarchar", "char", "nchar", "character", "character varying", "uuid",
              "uniqueidentifier", "citext", "enum", "time", "interval"):
    _DECLARED[_name] = "string"
for _name in ("json", "jsonb", "array", "object"):
    _DECLARED[_name] = "json"
_BINARY = frozenset({"bytea", "blob", "binary", "varbinary", "longblob", "mediumblob", "tinyblob", "image", "bytes"})


def _failed(message: str) -> ResultValidationError:
    return ResultValidationError(ExecutionErrorCategory.NORMALIZATION_FAILED, message)


def declared_type(name: str | None) -> DataType | None:
    """Map a database type name to a normalised type; ``None`` when unknown (then the type is inferred)."""
    if not name:
        return None
    base = re.sub(r"\(.*\)", "", name.strip().lower()).strip()
    if base in _BINARY:
        raise _failed("The result contains binary data, which is not supported.")
    return _DECLARED.get(base)


@dataclass(frozen=True, slots=True)
class ColumnType:
    data_type: DataType
    timezone: str | None = None  # "utc", "naive" or "mixed" for datetime columns
    source: str = "inferred"  # "declared" or "inferred"


# --- value parsing -------------------------------------------------------------------------------


def _parse_date(value: str) -> date | None:
    if not _DATE.match(value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _parse_datetime(value: str) -> datetime | None:
    if not _DATETIME.match(value):
        return None
    text = value.replace(" ", "T", 1)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    elif re.search(r"[+-]\d{4}$", text):
        text = f"{text[:-2]}:{text[-2:]}"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _format_datetime(value: datetime) -> str:
    aware = value.tzinfo is not None
    if aware:
        value = value.astimezone(UTC)
    text = value.strftime("%Y-%m-%dT%H:%M:%S")
    if value.microsecond:
        text += f".{value.microsecond:06d}"
    return text + ("Z" if aware else "")


def _json_int(value: int) -> int | str:
    return value if -MAX_SAFE_INTEGER <= value <= MAX_SAFE_INTEGER else str(value)


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise _failed("The result contains a number that is not finite.")
    return str(value)


def json_safe(value: Any) -> Any:
    """A JSON value with exact numbers kept exact (used for ``json`` and ``unknown`` columns)."""
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, int):
        return _json_int(value)
    if isinstance(value, Decimal):
        text = _decimal_text(value)
        as_float = float(value)
        return as_float if Decimal(repr(as_float)) == value else text
    if isinstance(value, float):
        return value
    if isinstance(value, list):
        return [json_safe(v) for v in value]
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    raise _failed("The result contains a value that is not JSON.")


def _kind(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, Decimal):
        return "decimal"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "string"
    return "json"


# --- column typing -------------------------------------------------------------------------------


def infer_type(values: list[Any]) -> ColumnType:
    present = [v for v in values if v is not None]
    if not present:
        return ColumnType("null")
    kinds = {_kind(v) for v in present}
    if kinds == {"boolean"}:
        return ColumnType("boolean")
    if kinds == {"integer"}:
        return ColumnType("integer")
    if kinds <= {"integer", "decimal"}:
        return ColumnType("decimal")
    if kinds <= {"integer", "decimal", "float"}:
        return ColumnType("float")
    if kinds == {"json"}:
        return ColumnType("json")
    if kinds == {"string"}:
        if all(_parse_date(v) is not None for v in present):
            return ColumnType("date")
        parsed = [_parse_datetime(v) for v in present]
        if all(p is not None for p in parsed):
            return ColumnType("datetime", _timezone(parsed))  # type: ignore[arg-type]
        return ColumnType("string")
    return ColumnType("unknown")


def _timezone(values: list[datetime]) -> str:
    aware = {v.tzinfo is not None for v in values}
    return "mixed" if len(aware) > 1 else ("utc" if aware == {True} else "naive")


def column_type(declared: str | None, values: list[Any]) -> ColumnType:
    mapped = declared_type(declared)
    if mapped is None:
        return infer_type(values)
    if mapped == "datetime":
        parsed = [_parse_datetime(v) for v in values if isinstance(v, str)]
        return ColumnType("datetime", _timezone([p for p in parsed if p is not None]) if parsed else None, "declared")
    return ColumnType(mapped, None, "declared")


def normalize_value(value: Any, column: ColumnType, name: str) -> Any:
    """Return the JSON-safe normalised value, or raise if it does not conform to the column's type."""
    if value is None:
        return None
    kind = column.data_type

    def mismatch() -> ResultValidationError:
        return _failed(f"A value in column '{name}' does not match the column's type ({kind}).")

    if kind == "integer":
        if isinstance(value, int) and not isinstance(value, bool):
            return _json_int(value)
        if isinstance(value, str) and _INTEGER_TEXT.match(value) and column.source == "declared":
            return _json_int(int(value))
        if isinstance(value, Decimal) and value == value.to_integral_value() and column.source == "declared":
            return _json_int(int(value))
        raise mismatch()
    if kind == "decimal":
        if isinstance(value, bool):
            raise mismatch()
        if isinstance(value, int):
            return str(value)
        if isinstance(value, Decimal):
            return _decimal_text(value)
        if isinstance(value, str) and column.source == "declared" and _DECIMAL_TEXT.match(value):
            try:
                return _decimal_text(Decimal(value))
            except InvalidOperation:
                raise mismatch() from None
        raise mismatch()
    if kind == "float":
        if isinstance(value, bool):
            raise mismatch()
        if isinstance(value, (int, Decimal, float)):
            result = float(value)
            if result != result or result in (float("inf"), float("-inf")):
                raise _failed("The result contains a number that is not finite.")
            return result
        raise mismatch()
    if kind == "boolean":
        if isinstance(value, bool):
            return value
        raise mismatch()
    if kind == "date":
        if isinstance(value, str) and (parsed := _parse_date(value)) is not None:
            return parsed.isoformat()
        raise mismatch()
    if kind == "datetime":
        if isinstance(value, str) and (parsed_dt := _parse_datetime(value)) is not None:
            return _format_datetime(parsed_dt)
        raise mismatch()
    if kind == "string":
        if isinstance(value, str):
            return value
        raise mismatch()
    # json, unknown, null
    return json_safe(value)

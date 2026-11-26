"""Identifier rules and the supported column data types.

Imported metadata is untrusted. Names must be plain SQL identifiers (letters,
digits and underscores, not starting with a digit, at most 63 characters), the
same rule permission policies use. Quoted identifiers with spaces or other
characters are deliberately not supported: later modules generate SQL from
these names, and a name that needs quoting is a name that can break out of it.

Data types are checked against a vocabulary covering PostgreSQL, MySQL/MariaDB,
SQL Server, Oracle and SQLite. A type is a known base name, optionally with one
parameter list (``varchar(255)``, ``numeric(12,2)``, ``nvarchar(max)``,
``timestamp(3) with time zone``), an optional ``unsigned`` and optional array
brackets (``integer[]``). It is stored normalised: lower case, single spaces.
Each base type maps to a category, which policies and later SQL validation use.
"""

import re
from dataclasses import dataclass

IDENTIFIER_MAX_LENGTH = 63
IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
DATA_TYPE_MAX_LENGTH = 64

# Category names. The first five match the value types of permission policies
# (app.authorization.policy.VALUE_TYPES); "other" is anything they cannot compare.
STRING, NUMBER, BOOLEAN, DATE, DATETIME, OTHER = "string", "number", "boolean", "date", "datetime", "other"
TYPE_CATEGORIES = (STRING, NUMBER, BOOLEAN, DATE, DATETIME, OTHER)

_TYPES: dict[str, tuple[str, ...]] = {
    NUMBER: (
        "smallint", "integer", "int", "bigint", "tinyint", "mediumint", "int2", "int4", "int8",
        "serial", "smallserial", "bigserial", "serial4", "serial8",
        "decimal", "numeric", "dec", "number", "real", "float", "float4", "float8", "double",
        "double precision", "money", "smallmoney", "binary_float", "binary_double",
    ),
    STRING: (
        "char", "character", "varchar", "character varying", "nchar", "nvarchar", "national char",
        "national character", "national character varying", "varchar2", "nvarchar2",
        "text", "tinytext", "mediumtext", "longtext", "ntext", "string", "clob", "nclob", "citext",
        "uuid", "uniqueidentifier", "enum", "set", "name",
    ),
    BOOLEAN: ("boolean", "bool", "bit"),
    DATE: ("date",),
    DATETIME: (
        "timestamp", "timestamptz", "timestamp with time zone", "timestamp without time zone",
        "timestamp with local time zone", "datetime", "datetime2", "smalldatetime", "datetimeoffset",
    ),
    OTHER: (
        "time", "timetz", "time with time zone", "time without time zone", "interval", "year",
        "json", "jsonb", "xml", "bytea", "blob", "tinyblob", "mediumblob", "longblob", "binary",
        "varbinary", "raw", "long raw", "long", "image", "rowversion", "sql_variant", "hierarchyid",
        "inet", "cidr", "macaddr", "macaddr8", "geometry", "geography", "point", "line", "polygon",
        "hstore", "tsvector", "tsquery", "oid", "bit varying", "varbit", "rowid", "urowid", "bfile",
        "interval year to month", "interval day to second",
    ),
}
CATEGORY_BY_BASE_TYPE: dict[str, str] = {base: category for category, bases in _TYPES.items() for base in bases}
SUPPORTED_BASE_TYPES: tuple[str, ...] = tuple(sorted(CATEGORY_BY_BASE_TYPE))

# One parameter list: a length, precision/scale, or "max".
_PARAMETERS_RE = re.compile(r"\(\s*(?:\d{1,5}|max)\s*(?:,\s*-?\d{1,5}\s*)?\)")
_ALLOWED_CHARACTERS_RE = re.compile(r"^[a-z0-9_ (),\[\]-]+$")


@dataclass(frozen=True, slots=True)
class DataType:
    normalized: str
    base: str
    category: str
    is_array: bool


class DataTypeError(ValueError):
    pass


def is_identifier(value: str) -> bool:
    return bool(IDENTIFIER_RE.match(value))


def parse_data_type(raw: str) -> DataType:
    """Normalise and check a data type; raise ``DataTypeError`` with a readable message."""
    value = " ".join(raw.strip().lower().split())
    if not value:
        raise DataTypeError("Data type must not be empty.")
    if len(value) > DATA_TYPE_MAX_LENGTH:
        raise DataTypeError(f"Data type must be at most {DATA_TYPE_MAX_LENGTH} characters.")
    if not _ALLOWED_CHARACTERS_RE.match(value):
        raise DataTypeError(f"Data type '{raw.strip()[:40]}' contains characters that are not allowed.")
    parameter_lists = _PARAMETERS_RE.findall(value)
    if len(parameter_lists) > 1:
        raise DataTypeError("A data type can have at most one parameter list, such as (10,2).")
    remainder = _PARAMETERS_RE.sub(" ", value)
    if "(" in remainder or ")" in remainder:
        raise DataTypeError(f"Data type '{value}' has invalid parameters. Use a length or precision, such as (255) or (10,2).")
    remainder = " ".join(remainder.split())
    is_array = remainder.endswith("[]")
    while remainder.endswith("[]"):
        remainder = remainder[:-2].rstrip()
    if "[" in remainder or "]" in remainder:
        raise DataTypeError(f"Data type '{value}' has invalid array brackets.")
    if remainder.endswith(" unsigned"):
        remainder = remainder[: -len(" unsigned")]
    base = remainder.strip()
    category = CATEGORY_BY_BASE_TYPE.get(base)
    if category is None:
        raise DataTypeError(f"'{base[:40]}' is not a supported data type.")
    # Canonical spacing around parameters: "numeric(12,2)", "timestamp(3) with time zone".
    normalized = re.sub(r"\s*\(\s*", "(", value)
    normalized = re.sub(r"\s*,\s*", ",", normalized)
    normalized = re.sub(r"\s*\)", ")", normalized)
    normalized = re.sub(r"\s*\[\]", "[]", normalized)
    return DataType(normalized=normalized, base=base, category=OTHER if is_array else category, is_array=is_array)


def data_type_category(data_type: str) -> str:
    """Category of an already stored (normalised) type; ``other`` if it cannot be parsed."""
    try:
        return parse_data_type(data_type).category
    except DataTypeError:
        return OTHER


def compatible_for_relationship(first: str, second: str) -> bool:
    """Can a relationship join these two column types? Same category, or either is ``other``."""
    a, b = data_type_category(first), data_type_category(second)
    return a == b or OTHER in (a, b)

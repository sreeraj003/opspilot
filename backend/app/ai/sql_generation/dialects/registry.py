"""SQL dialects supported by generation, and how each one is written.

A registered schema source declares its ``database_type`` (Module 06). Each
supported type maps to one ``SQLDialect``: the SQLGlot dialect used to parse
and render the SQL, and the conventions the model is told to follow (quoting,
pagination, date arithmetic, date truncation). Anything else is rejected with
``UnsupportedDialectError`` before any model call: generation never falls back
to PostgreSQL syntax for a database that is not PostgreSQL.

``forbidden_tokens`` are SQLGlot token types that a lenient parser accepts but
that do not belong to the dialect (for example ``LIMIT`` in SQL Server);
``forbidden_quote_chars`` catches literals and identifiers quoted the wrong way
(``"x"`` in MySQL is a string, not an identifier); ``bracket_rule`` says what a
square bracket may be (an SQL Server identifier quote, a PostgreSQL ``ARRAY[...]``
constructor, nothing in MySQL). All three make preliminary validation reject
mixed-dialect SQL instead of silently rewriting it.
"""

from dataclasses import dataclass

from app.core.exceptions import InvalidInputError


class UnsupportedDialectError(InvalidInputError):
    code = "SQL_DIALECT_UNSUPPORTED"
    message = "SQL generation does not support this schema's database type."


@dataclass(frozen=True, slots=True)
class SQLDialect:
    key: str  # stored value, also the SQLGlot dialect name
    sqlglot_dialect: str
    display_name: str
    database_types: tuple[str, ...]
    identifier_quote: str  # how to quote an identifier that needs quoting
    string_quote: str
    pagination: str
    date_literal: str
    date_truncation: str
    date_arithmetic: str
    current_date: str
    forbidden_tokens: frozenset[str]
    forbidden_quote_chars: frozenset[str]
    bracket_rule: str  # "identifier", "array_only" or "forbidden"
    notes: tuple[str, ...] = ()

    def guidance(self) -> dict[str, object]:
        """The conventions given to the model."""
        return {
            "name": self.display_name,
            "identifier_quoting": self.identifier_quote,
            "string_literals": self.string_quote,
            "row_limit": self.pagination,
            "date_literal": self.date_literal,
            "date_truncation": self.date_truncation,
            "date_arithmetic": self.date_arithmetic,
            "current_date": self.current_date,
            "notes": list(self.notes),
        }


POSTGRESQL = SQLDialect(
    key="postgres",
    sqlglot_dialect="postgres",
    display_name="PostgreSQL",
    database_types=("postgresql",),
    identifier_quote='Double quotes, only when needed: "Order Items"',
    string_quote="Single quotes: 'Kerala'",
    pagination="LIMIT n at the end (LIMIT n OFFSET m for later pages). Never TOP or FETCH FIRST.",
    date_literal="DATE '2026-01-31' or TIMESTAMPTZ '2026-01-31T00:00:00+05:30'",
    date_truncation="DATE_TRUNC('month', column)",
    date_arithmetic="column + INTERVAL '1 day'",
    current_date="CURRENT_DATE",
    forbidden_tokens=frozenset({"TOP"}),
    forbidden_quote_chars=frozenset({"`"}),
    bracket_rule="array_only",
    notes=("Use ILIKE only when a case-insensitive match is requested.",),
)

MYSQL = SQLDialect(
    key="mysql",
    sqlglot_dialect="mysql",
    display_name="MySQL",
    database_types=("mysql", "mariadb"),
    identifier_quote="Backticks, only when needed: `order items`. Never double quotes for identifiers.",
    string_quote="Single quotes: 'Kerala'",
    pagination="LIMIT n at the end (LIMIT n OFFSET m for later pages). Never TOP or FETCH FIRST.",
    date_literal="'2026-01-31' or TIMESTAMP('2026-01-31 00:00:00')",
    date_truncation="DATE_FORMAT(column, '%Y-%m-01') for months, DATE(column) for days, YEAR(column) for years",
    date_arithmetic="DATE_ADD(column, INTERVAL 1 DAY)",
    current_date="CURRENT_DATE()",
    forbidden_tokens=frozenset({"TOP"}),
    forbidden_quote_chars=frozenset({'"'}),
    bracket_rule="forbidden",
    notes=("The schema name qualifies tables as database.table.",),
)

SQL_SERVER = SQLDialect(
    key="tsql",
    sqlglot_dialect="tsql",
    display_name="Microsoft SQL Server (T-SQL)",
    database_types=("sqlserver",),
    identifier_quote="Square brackets, only when needed: [Order Items]",
    string_quote="Single quotes: 'Kerala' (N'...' for Unicode)",
    pagination="SELECT TOP (n) ... ORDER BY ..., or ORDER BY ... OFFSET m ROWS FETCH NEXT n ROWS ONLY. Never LIMIT.",
    date_literal="CAST('2026-01-31' AS DATE) or CAST('2026-01-31T00:00:00' AS DATETIME2)",
    date_truncation="DATEFROMPARTS(YEAR(column), MONTH(column), 1) for months, CAST(column AS DATE) for days",
    date_arithmetic="DATEADD(day, 1, column)",
    current_date="CAST(GETDATE() AS DATE)",
    forbidden_tokens=frozenset({"LIMIT"}),
    forbidden_quote_chars=frozenset({"`"}),
    bracket_rule="identifier",
    notes=("Tables are written schema.table (for example sales.orders).",),
)

SUPPORTED_DIALECTS: tuple[SQLDialect, ...] = (POSTGRESQL, MYSQL, SQL_SERVER)
_BY_DATABASE_TYPE = {db_type: d for d in SUPPORTED_DIALECTS for db_type in d.database_types}
_BY_KEY = {d.key: d for d in SUPPORTED_DIALECTS}


def dialect_for_database_type(database_type: str) -> SQLDialect:
    """The dialect for a registered schema's ``database_type``, or ``UnsupportedDialectError``."""
    dialect = _BY_DATABASE_TYPE.get((database_type or "").strip().lower())
    if dialect is None:
        supported = ", ".join(sorted(_BY_DATABASE_TYPE))
        raise UnsupportedDialectError(
            f"SQL generation supports these database types: {supported}. "
            f"This schema is registered as '{database_type}'.",
            details={"database_type": database_type, "supported": sorted(_BY_DATABASE_TYPE)},
        )
    return dialect


def dialect_by_key(key: str) -> SQLDialect:
    dialect = _BY_KEY.get(key)
    if dialect is None:
        raise UnsupportedDialectError(f"Unknown SQL dialect '{key}'.")
    return dialect

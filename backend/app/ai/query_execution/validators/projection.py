"""The columns an approved query may return, read from the exact approved SQL.

A result may only contain the columns the approved query selects. The output
names come from SQLGlot's parse of the exact approved SQL (the same text the
approval's hash covers): aliases and plain column names. Expressions without a
name (``COUNT(*)`` with no alias) are counted, because their name is chosen by
the company database. The query's own row limit is read as well, so a result
that reaches it can be reported as possibly incomplete.
"""

from dataclasses import dataclass

import sqlglot
from sqlglot import exp

from app.ai.sql_security.rewriters.result_limit import _limit_count


@dataclass(frozen=True, slots=True)
class ApprovedProjection:
    names: frozenset[str]  # lower-case output names
    unnamed: int
    total: int
    row_limit: int | None

    def split(self, columns: list[str]) -> tuple[list[str], list[str]]:
        """(allowed, withheld). Unknown names are allowed only for unnamed expressions, never beyond the projection."""
        known = [c for c in columns if c.lower() in self.names]
        unknown = [c for c in columns if c.lower() not in self.names]
        if len(unknown) <= self.unnamed and len(columns) <= self.total:
            return columns, []
        return known, unknown


def approved_projection(exact_sql: str, dialect: str) -> ApprovedProjection | None:
    """``None`` when the SQL cannot be read (the caller then withholds nothing but trusts nothing either)."""
    try:
        statement = sqlglot.parse_one(exact_sql, read=dialect)
    except (sqlglot.errors.ParseError, sqlglot.errors.TokenError, ValueError):
        return None
    if not isinstance(statement, exp.Query):
        return None
    selects = statement.selects
    names = [n for n in statement.named_selects if n]
    limit: int | None = None
    _, count = _limit_count(statement)
    value = count.this if isinstance(count, exp.Paren) else count
    if isinstance(value, exp.Literal) and not value.is_string and str(value.this).isdigit():
        limit = int(value.this)
    return ApprovedProjection(
        names=frozenset(n.lower() for n in names), unnamed=max(0, len(selects) - len(names)), total=len(selects),
        row_limit=limit,
    )

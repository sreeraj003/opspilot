"""The result row limit (documented normalization policy).

* A query that returns exactly one row (an aggregate-only SELECT without
  GROUP BY) needs no limit.
* A top-level SELECT without a row limit gets one: ``AI_SQL_GUARD_MAX_RESULT_ROWS``
  is added (``LIMIT n``, or ``TOP n`` in T-SQL) and a warning tells the user.
  Adding a limit only caps how many rows are returned; it never changes which
  rows qualify.
* A literal limit above the maximum, or a limit that is not a literal whole
  number, is **rejected**, never lowered silently.
* A set operation (UNION ...) without its own limit, or ``WITH TIES`` /
  ``PERCENT`` limits, are rejected: the guard does not restructure them.
"""

from sqlglot import exp

from app.ai.sql_security.validators.violations import Category, GuardRejected, violation

APPLIED_LIMIT_WARNING = "No row limit was given, so at most {limit} rows will be returned."


def _limit_count(node: exp.Expression) -> tuple[exp.Expression | None, exp.Expression | None]:
    limit = node.args.get("limit")
    if isinstance(limit, exp.Limit):
        return limit, limit.expression
    if isinstance(limit, exp.Fetch):
        return limit, limit.args.get("count")
    fetch = node.args.get("fetch")
    if isinstance(fetch, exp.Fetch):
        return fetch, fetch.args.get("count")
    return None, None


def _single_aggregate_row(select: exp.Select) -> bool:
    if select.args.get("group"):
        return False
    projections = [p.unalias() for p in select.expressions]
    if not projections:
        return False
    for projection in projections:
        aggregates = [
            a for a in projection.find_all(exp.AggFunc)
            if a.find_ancestor(exp.Select) is select and a.find_ancestor(exp.Window) is None
        ]
        if not aggregates and not isinstance(projection, exp.Literal):
            return False
        for column in projection.find_all(exp.Column):
            agg = column.find_ancestor(exp.AggFunc)
            if agg is None or agg.find_ancestor(exp.Select) is not select:
                return False
    return True


def enforce_result_limit(statement: exp.Expression, max_rows: int) -> tuple[exp.Expression, list[str]]:
    """Returns the (possibly limited) statement and warnings. Raises ``GuardRejected``."""
    holder, count = _limit_count(statement)
    if holder is not None:
        options = holder.args.get("limit_options")
        if isinstance(options, exp.LimitOptions) and (options.args.get("percent") or options.args.get("with_ties")):
            raise GuardRejected([violation("row_limit_invalid", Category.RESULT_LIMIT,
                                           "Row limits must be a plain number of rows (no PERCENT or WITH TIES).")])
        value = count.this if isinstance(count, exp.Paren) else count
        if not (isinstance(value, exp.Literal) and not value.is_string and str(value.this).isdigit()):
            raise GuardRejected([violation("row_limit_invalid", Category.RESULT_LIMIT, "The row limit must be a whole number.")])
        rows = int(value.this)
        if rows < 1 or rows > max_rows:
            raise GuardRejected([violation("row_limit_too_large", Category.RESULT_LIMIT,
                                           f"The query may return at most {max_rows} rows.", f"limit {rows} > {max_rows}")])
        return statement, []
    if isinstance(statement, exp.Select):
        if _single_aggregate_row(statement):
            return statement, []
        if statement.args.get("offset") is not None:
            raise GuardRejected([violation("row_limit_required", Category.RESULT_LIMIT,
                                           f"A query with OFFSET needs a row limit of at most {max_rows}.")])
        return statement.limit(max_rows, copy=False), [APPLIED_LIMIT_WARNING.format(limit=max_rows)]
    raise GuardRejected([violation("row_limit_required", Category.RESULT_LIMIT,
                                   f"Combined queries need their own row limit of at most {max_rows}.")])

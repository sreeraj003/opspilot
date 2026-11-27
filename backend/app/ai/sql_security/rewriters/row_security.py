"""Row-level security enforcement by rewriting every access path.

Appending one WHERE clause to the outermost query is **not** row-level
security: a restricted table can also be read in a CTE, a derived table, an
``EXISTS`` / ``IN`` / scalar subquery, either side of a join or any branch of a
UNION. So the guard replaces **every** ``exp.Table`` node that references a
restricted table, wherever it is, with a filtered derived table that keeps the
alias the query uses::

    sales.orders AS o
    -> (SELECT o.total_amount, o.status FROM sales.orders AS o
        WHERE <restriction on o>) AS o

* The restriction comes from the permission engine's decision for this user
  and exactly these tables and columns (Module 04's DSL, converted by
  ``policy_dsl``), never from the model, the stored generation or the client.
* The wrapper projects only the columns the query itself references (all
  already authorized); restriction columns are used inside the wrapper but not
  exposed. A table read without columns (``COUNT(*)``) projects a constant.
* Outer joins keep their meaning: a restricted table on the nullable side
  contributes only permitted rows.
* Structures the guard cannot rewrite safely (a table alias with a column
  list, a restriction that cannot be translated) are rejected.

``verify_row_security`` then re-parses the final SQL and proves the
post-condition: every reference to a restricted table sits directly inside a
guard wrapper whose WHERE clause is exactly the expected restriction.
"""

from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from app.ai.sql_security.rewriters.policy_dsl import PolicyTranslationError, policy_to_expression
from app.ai.sql_security.validators.references import REF_KEY, QueryAnalysis, TableRef
from app.ai.sql_security.validators.violations import Category, GuardRejected, Violation, violation
from app.ai.understanding.services.schema_context import SchemaContext

ROW_MARKER_COLUMN = "_opspilot_row"

_UNENFORCEABLE = (
    "Your row-level data restrictions cannot be enforced safely for this query structure, so it was rejected."
)


def _unenforceable(internal: str) -> Violation:
    return violation("row_security_unenforceable", Category.ROW_SECURITY_UNENFORCEABLE, _UNENFORCEABLE, internal)


def _alias_identifier(node: exp.Table) -> exp.Identifier:
    alias = node.args.get("alias")
    if isinstance(alias, exp.TableAlias) and isinstance(alias.this, exp.Identifier):
        return alias.this.copy()
    return node.this.copy()


def restriction_expression(ref: TableRef, alias: exp.Identifier, policy: dict[str, Any], dialect: str) -> exp.Expression:
    try:
        return policy_to_expression(policy, ref.table, alias, dialect)
    except PolicyTranslationError as exc:
        raise GuardRejected([_unenforceable(f"{ref.table.qualified}: {exc}")]) from None


def apply_row_security(
    statement: exp.Expression, analysis: QueryAnalysis, restrictions: dict[str, dict[str, Any]], dialect: str
) -> tuple[exp.Expression, list[str]]:
    """Rewrite ``statement`` in place. ``restrictions`` maps lower-case qualified tables to policy DSL."""
    if not restrictions:
        return statement, []
    refs = {r.ref: r for r in analysis.tables}
    applied: set[str] = set()
    targets: list[tuple[exp.Table, TableRef, dict[str, Any]]] = []
    for node in list(statement.find_all(exp.Table)):
        ref = refs.get(node.meta.get(REF_KEY, -1))
        if ref is None:
            continue
        policy = restrictions.get(ref.table.qualified.lower())
        if policy is None:
            continue
        alias = node.args.get("alias")
        if isinstance(alias, exp.TableAlias) and alias.columns:
            raise GuardRejected([_unenforceable("table alias with a column list")])
        if not isinstance(node.parent, (exp.From, exp.Join)):
            raise GuardRejected([_unenforceable(f"table reference in {type(node.parent).__name__}")])
        targets.append((node, ref, policy))
    for node, ref, policy in targets:
        alias = _alias_identifier(node)
        columns = sorted(analysis.columns.get(ref.table.qualified, set()), key=str.lower)
        if columns:
            projections: list[exp.Expression] = [exp.Column(this=exp.to_identifier(c), table=alias.copy()) for c in columns]
        else:
            projections = [exp.alias_(exp.Literal.number(1), ROW_MARKER_COLUMN)]
        source = exp.Table(this=node.this.copy(), db=node.args["db"].copy(), alias=exp.TableAlias(this=alias.copy()))
        inner = exp.Select(expressions=projections).from_(source, copy=False)
        inner = inner.where(restriction_expression(ref, alias, policy, dialect), copy=False)
        node.replace(exp.Subquery(this=inner, alias=exp.TableAlias(this=alias.copy())))
        applied.add(ref.table.qualified)
    return statement, sorted(applied, key=str.lower)


def verify_row_security(
    sql: str, dialect: str, schema: SchemaContext, restrictions: dict[str, dict[str, Any]]
) -> None:
    """Re-parse the final SQL and check that every restricted table is wrapped by its exact restriction."""
    if not restrictions:
        return
    try:
        statement = sqlglot.parse_one(sql, read=dialect)
    except SqlglotError:
        raise GuardRejected([_unenforceable("rewritten SQL does not parse")]) from None
    for node in statement.find_all(exp.Table):
        if not node.db:
            continue
        table = schema.table(f"{node.db}.{node.name}")
        if table is None:
            raise GuardRejected([_unenforceable("rewritten SQL references an unknown table")])
        policy = restrictions.get(table.qualified.lower())
        if policy is None:
            continue
        select = node.parent.parent if isinstance(node.parent, exp.From) else None
        if not isinstance(select, exp.Select) or not isinstance(select.parent, exp.Subquery):
            raise GuardRejected([_unenforceable(f"{table.qualified} is not inside a restriction wrapper")])
        if any(select.args.get(k) for k in ("joins", "group", "having", "limit", "offset", "with", "distinct")):
            raise GuardRejected([_unenforceable(f"{table.qualified} wrapper was altered")])
        where = select.args.get("where")
        ref = TableRef(ref=-1, table=table, alias=node.alias_or_name)
        expected = restriction_expression(ref, _alias_identifier(node), policy, dialect)
        if where is None or where.this.sql(dialect=dialect) != expected.sql(dialect=dialect):
            raise GuardRejected([_unenforceable(f"{table.qualified} wrapper does not carry its restriction")])

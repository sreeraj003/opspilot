"""Query complexity limits, enforced through AST inspection.

Counted on the parsed statement before any rewriting: physical table
references, joins, CTEs, subqueries and derived tables, nesting depth (SELECTs
inside SELECTs) and the total number of AST nodes. SQL length is checked
before parsing. The result row limit is handled by ``rewriters.result_limit``.
These limits do not replace statement timeouts and resource limits in the
company's database: the external backend must enforce those too.
"""

from app.ai.sql_security.policies.limits import GuardLimits
from app.ai.sql_security.validators.references import QueryAnalysis
from app.ai.sql_security.validators.violations import Category, GuardRejected, Violation, violation


def check_complexity(analysis: QueryAnalysis, node_count: int, limits: GuardLimits) -> None:
    checks = (
        ("too_many_tables", len(analysis.tables), limits.max_tables, "table references"),
        ("too_many_joins", analysis.join_count, limits.max_joins, "joins"),
        ("too_many_ctes", analysis.ctes, limits.max_ctes, "CTEs"),
        ("too_many_subqueries", analysis.subqueries, limits.max_subqueries, "subqueries"),
        ("nesting_too_deep", analysis.nesting_depth, limits.max_nesting_depth, "levels of nesting"),
        ("too_many_expressions", node_count, limits.max_expressions, "expression nodes"),
    )
    problems: list[Violation] = []
    for code, value, maximum, label in checks:
        if value > maximum:
            problems.append(violation(code, Category.COMPLEXITY_LIMIT,
                                      f"The query is too complex: at most {maximum} {label} are allowed.",
                                      f"{value} {label} > {maximum}"))
    if problems:
        raise GuardRejected(problems)

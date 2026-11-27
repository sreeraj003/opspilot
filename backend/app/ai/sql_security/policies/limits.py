"""Configurable limits and switches of the SQL security guard.

Every limit is enforced on the parsed SQLGlot AST (``validators.complexity``),
never on the SQL text, and never by trusting the model to write an efficient
query. ``max_execution_seconds`` documents what the external API contract
supports: OpsPilot passes the connector's own timeout and warns when it is
longer, but the company backend must enforce statement timeouts and
database-level resource limits itself.
"""

from dataclasses import dataclass, field

from app.core.config import Settings


@dataclass(frozen=True, slots=True)
class GuardLimits:
    max_sql_length: int = 20000
    max_joins: int = 6
    max_tables: int = 8
    max_nesting_depth: int = 3
    max_ctes: int = 5
    max_subqueries: int = 6
    max_expressions: int = 600
    max_result_rows: int = 1000
    max_execution_seconds: int = 30


@dataclass(frozen=True, slots=True)
class GuardOptions:
    limits: GuardLimits = field(default_factory=GuardLimits)
    allow_window_functions: bool = False
    allow_set_operations: bool = False
    allow_cross_schema: bool = False
    # Lower-case schema namespaces queries may use; empty means every registered namespace.
    allowed_namespaces: frozenset[str] = frozenset()
    # "dialect:function" or "*:function" (SQLGlot function keys, lower case).
    extra_functions: frozenset[str] = frozenset()
    # Approved join rules beyond registered relationships, as unordered pairs of
    # lower-case ``schema.table.column`` names.
    approved_joins: frozenset[frozenset[str]] = frozenset()

    @classmethod
    def from_settings(cls, settings: Settings) -> "GuardOptions":
        joins: set[frozenset[str]] = set()
        for rule in settings.ai_sql_guard_approved_joins:
            left, sep, right = rule.partition("=")
            if sep and left.strip().count(".") == 2 and right.strip().count(".") == 2:
                joins.add(frozenset({left.strip().lower(), right.strip().lower()}))
        return cls(
            limits=GuardLimits(
                max_sql_length=settings.ai_sql_guard_max_sql_length,
                max_joins=settings.ai_sql_guard_max_joins,
                max_tables=settings.ai_sql_guard_max_tables,
                max_nesting_depth=settings.ai_sql_guard_max_nesting_depth,
                max_ctes=settings.ai_sql_guard_max_ctes,
                max_subqueries=settings.ai_sql_guard_max_subqueries,
                max_expressions=settings.ai_sql_guard_max_expressions,
                max_result_rows=settings.ai_sql_guard_max_result_rows,
                max_execution_seconds=settings.ai_sql_guard_max_execution_seconds,
            ),
            allow_window_functions=settings.ai_sql_guard_allow_window_functions,
            allow_set_operations=settings.ai_sql_guard_allow_set_operations,
            allow_cross_schema=settings.ai_sql_guard_allow_cross_schema,
            allowed_namespaces=frozenset(n.strip().lower() for n in settings.ai_sql_guard_allowed_namespaces if n.strip()),
            extra_functions=frozenset(f.strip().lower() for f in settings.ai_sql_guard_extra_functions if f.strip()),
            approved_joins=frozenset(joins),
        )

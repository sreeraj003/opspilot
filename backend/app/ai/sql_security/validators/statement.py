"""Statement and AST validation: every node of the parsed tree is inspected.

A query is not safe because its outermost node is a SELECT. The whole tree,
including CTE bodies, derived tables, ``IN`` / ``EXISTS`` subqueries and set
operation branches, is walked and every node must be of an **allowed node
type**; anything else is rejected, so constructs SQLGlot learns to parse in a
later version are refused until someone approves them here.

Rejected explicitly (with their own codes): DML, DDL, DCL, MERGE, EXEC/CALL,
transactions, administrative commands, SELECT INTO, locking clauses (``FOR
UPDATE``), table hints, table-valued functions, ``LATERAL``/``UNNEST``,
``VALUES`` lists, recursive CTEs, bind parameters and session variables,
``SELECT *`` and ``table.*``, and set operations unless
``AI_SQL_GUARD_ALLOW_SET_OPERATIONS`` is enabled. Window functions need
``AI_SQL_GUARD_ALLOW_WINDOW_FUNCTIONS``. Every function must be on the
allowlist for the dialect (``policies.functions``).
"""

from dataclasses import dataclass, field

from sqlglot import exp

from app.ai.sql_generation.validators.sql_validator import _FORBIDDEN_NODES, _SYSTEM_SCHEMAS
from app.ai.sql_security.policies.functions import STRUCTURAL_FUNCTION_TYPES, FunctionPolicy, display_name, function_key
from app.ai.sql_security.validators.violations import Category, GuardRejected, Violation, violation

_FORBIDDEN_TYPES = tuple(getattr(exp, name) for name in _FORBIDDEN_NODES if hasattr(exp, name))

ALLOWED_NODE_TYPES: tuple[type[exp.Expression], ...] = tuple(
    getattr(exp, name)
    for name in (
        "Select", "Union", "Intersect", "Except", "From", "Join", "Where", "Group", "Having", "Order", "Ordered",
        "Limit", "Offset", "Fetch", "LimitOptions", "With", "CTE", "TableAlias", "Subquery", "Table", "Column",
        "Identifier", "Alias", "Literal", "Boolean", "Null", "Paren", "And", "Or", "Not", "EQ", "NEQ", "GT", "GTE",
        "LT", "LTE", "Is", "In", "Between", "Like", "ILike", "Add", "Sub", "Mul", "Div", "Mod", "Neg", "DPipe",
        "Case", "If", "Cast", "DataType", "DataTypeParam", "Interval", "Var", "Distinct", "Exists", "Star", "Tuple",
        "Window", "WindowSpec",
    )
    if hasattr(exp, name)
)

_CONSTRUCT_NAMES: dict[str, str] = {
    "Lateral": "LATERAL", "Unnest": "UNNEST", "Values": "a VALUES list", "Hint": "a query or table hint",
    "Placeholder": "a bind parameter", "Parameter": "a parameter", "SessionParameter": "a session variable",
    "Qualify": "QUALIFY", "Pivot": "PIVOT", "TableSample": "TABLESAMPLE", "Dot": "member access",
    "Bracket": "subscripts", "Collate": "COLLATE", "Rollup": "ROLLUP", "Cube": "CUBE",
    "GroupingSets": "GROUPING SETS", "WithinGroup": "WITHIN GROUP", "Filter": "aggregate FILTER",
}


@dataclass
class StatementReport:
    functions: list[str] = field(default_factory=list)  # display names, sorted and unique
    uses_set_operation: bool = False
    uses_window: bool = False
    node_count: int = 0


def _reject(code: str, category: Category, message: str, internal: str | None = None) -> Violation:
    return violation(code, category, message, internal)


def inspect_statement(statement: exp.Expression, policy: FunctionPolicy, *, allow_set_operations: bool) -> StatementReport:
    report = StatementReport()
    problems: list[Violation] = []
    functions: set[str] = set()
    for node in statement.walk():
        report.node_count += 1
        name = type(node).__name__
        if isinstance(node, _FORBIDDEN_TYPES):
            label = _FORBIDDEN_NODES.get(name, name)
            category = Category.STATEMENT_NOT_ALLOWED if name not in ("Lock", "Into") else Category.UNSAFE_CONSTRUCT
            problems.append(_reject(
                "statement_not_allowed" if category == Category.STATEMENT_NOT_ALLOWED else "unsafe_construct",
                category, "Only a single read-only SELECT statement is allowed.", f"{label} in the query",
            ))
            continue
        if isinstance(node, exp.SetOperation):
            report.uses_set_operation = True
            if not allow_set_operations:
                problems.append(_reject("set_operation_not_allowed", Category.UNSAFE_CONSTRUCT,
                                        "UNION, INTERSECT and EXCEPT are not enabled for queries.", name))
            continue
        if isinstance(node, exp.With) and node.args.get("recursive"):
            problems.append(_reject("recursive_cte", Category.UNSAFE_CONSTRUCT, "Recursive queries are not allowed."))
            continue
        if isinstance(node, exp.Window):
            report.uses_window = True
            if not policy.allow_window_functions:
                problems.append(_reject("window_not_allowed", Category.FUNCTION_NOT_ALLOWED,
                                        "Window functions are not enabled for queries."))
            continue
        if isinstance(node, exp.Func) and not isinstance(node, STRUCTURAL_FUNCTION_TYPES):
            key = function_key(node)
            label = display_name(node)
            functions.add(label)
            if policy.is_denied(key):
                problems.append(_reject("unsafe_function", Category.UNSAFE_FUNCTION,
                                        f"The function {label} is not allowed.", f"denied function {key}"))
            elif policy.is_window(key) and not policy.allow_window_functions:
                problems.append(_reject("window_not_allowed", Category.FUNCTION_NOT_ALLOWED,
                                        "Window functions are not enabled for queries.", key))
            elif not policy.is_allowed(key):
                problems.append(_reject("function_not_allowed", Category.FUNCTION_NOT_ALLOWED,
                                        f"The function {label} is not approved for {policy.dialect} queries.",
                                        f"function {key} not on the allowlist"))
            continue
        if isinstance(node, exp.Func) and isinstance(node, STRUCTURAL_FUNCTION_TYPES):
            if isinstance(node, (exp.Case, exp.If, exp.Cast)):
                functions.add(display_name(node) if not isinstance(node, exp.If) else "CASE")
            continue
        if isinstance(node, exp.Table):
            problems.extend(_check_table_node(node))
            continue
        if isinstance(node, exp.Star):
            if not isinstance(node.parent, exp.Count):
                problems.append(_reject("select_star", Category.UNSAFE_CONSTRUCT,
                                        "SELECT * is not allowed; select explicit columns."))
            continue
        if isinstance(node, exp.Column) and isinstance(node.this, exp.Star):
            problems.append(_reject("select_star", Category.UNSAFE_CONSTRUCT,
                                    "SELECT * is not allowed; select explicit columns."))
            continue
        if isinstance(node, exp.Column) and (node.args.get("db") or node.args.get("catalog")):
            problems.append(_reject("column_qualification", Category.SCHEMA_REFERENCE,
                                    "Qualify columns with their table alias only (alias.column).",
                                    "schema-qualified column reference"))
            continue
        if isinstance(node, exp.Select):
            problems.extend(_check_select(node))
            continue
        if isinstance(node, exp.Join):
            problems.extend(_check_join(node))
            continue
        if not isinstance(node, ALLOWED_NODE_TYPES):
            label = _CONSTRUCT_NAMES.get(name, name)
            problems.append(_reject("unsupported_construct", Category.UNSAFE_CONSTRUCT,
                                    "The query uses SQL the security guard cannot validate.", f"node {label}"))
    report.functions = sorted(functions)
    if problems:
        raise GuardRejected(problems)
    return report


def _check_select(select: exp.Select) -> list[Violation]:
    problems: list[Violation] = []
    for key in ("hint", "into", "kind", "operation_modifiers", "qualify", "sample", "settings", "format"):
        if select.args.get(key):
            problems.append(_reject("unsupported_construct", Category.UNSAFE_CONSTRUCT,
                                    "The query uses SQL the security guard cannot validate.", f"SELECT {key}"))
    if select.args.get("locks"):
        problems.append(_reject("locking_not_allowed", Category.UNSAFE_CONSTRUCT, "Locking clauses are not allowed."))
    distinct = select.args.get("distinct")
    if isinstance(distinct, exp.Distinct) and distinct.args.get("on"):
        problems.append(_reject("unsupported_construct", Category.UNSAFE_CONSTRUCT,
                                "The query uses SQL the security guard cannot validate.", "DISTINCT ON"))
    return problems


def _check_join(join: exp.Join) -> list[Violation]:
    if join.args.get("method") or join.args.get("hint"):
        return [_reject("unsupported_construct", Category.UNSAFE_CONSTRUCT,
                        "The query uses SQL the security guard cannot validate.", "join hint or method")]
    return []


def _check_table_node(table: exp.Table) -> list[Violation]:
    if not isinstance(table.this, exp.Identifier):
        return [_reject("table_function_not_allowed", Category.UNSAFE_CONSTRUCT,
                        "Table-valued functions are not allowed; select from registered tables.",
                        f"table expression {type(table.this).__name__}")]
    for key in ("joins", "pivots", "laterals", "hints", "sample", "version", "when", "only", "partition", "changes"):
        if table.args.get(key):
            return [_reject("unsupported_construct", Category.UNSAFE_CONSTRUCT,
                            "The query uses SQL the security guard cannot validate.", f"table {key}")]
    parts = [p.lower() for p in (table.catalog, table.db, table.name) if p]
    if any(p in _SYSTEM_SCHEMAS for p in parts):
        return [_reject("system_catalog", Category.UNSAFE_CONSTRUCT, "System catalogs may not be queried.")]
    return []

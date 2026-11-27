"""Schema validation: every referenced object is extracted from the AST and checked
against Module 06's schema registry.

The table and column lists the model returned are never used. Instead:

1. Every ``exp.Table`` node is classified. A name without a schema that matches
   a CTE of the query is a CTE reference; every other table must be written
   ``schema.table`` (no catalog / database prefix), exist in the registered
   schema, be in an allowed namespace and, unless cross-schema access is
   configured, share one namespace with the other tables. Quoted identifiers
   must match the registered spelling exactly (identifier confusion); CTE names
   may not shadow a registered table name.
2. A copy of the statement is qualified by SQLGlot against **every registered
   column** of the schema, with validation on: unknown and ambiguous columns
   fail. Aliases and scopes are resolved by SQLGlot's scope analysis.
3. Every column of every scope (CTEs, derived tables, subqueries, correlated
   references to outer scopes) is resolved to its source. Physical columns are
   recorded per registered table; references to derived sources must exist in
   their projection.
4. Joins: no CROSS, NATURAL, USING or condition-less (comma) joins; the ON
   condition must be a conjunction; every predicate that compares columns of
   two different sources must be an equality that follows a registered
   relationship (or an approved join rule, or the same column of the same
   table), after tracing derived columns back
   to the physical column they project; at least one such equality is needed.
   Correlated subqueries and ``IN (SELECT ...)`` follow the same rule.

The result (``QueryAnalysis``) also carries the structural counts the
complexity limits use. Nothing here authorizes anything: permissions are
checked afterwards, on exactly these references.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field

from sqlglot import exp
from sqlglot.errors import OptimizeError, SqlglotError
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, traverse_scope
from sqlglot.schema import MappingSchema

from app.ai.sql_generation.context.builder import _SQLGLOT_TYPES
from app.ai.sql_generation.dialects.registry import SQLDialect
from app.ai.sql_security.policies.limits import GuardOptions
from app.ai.sql_security.validators.violations import (
    SAFE_REFERENCE_MESSAGE,
    Category,
    GuardRejected,
    Violation,
    violation,
)
from app.ai.understanding.services.schema_context import SchemaContext, TableInfo

REF_KEY = "opspilot_table_ref"


@dataclass
class TableRef:
    """One physical table reference (one ``exp.Table`` node) in the original statement."""

    ref: int
    table: TableInfo
    alias: str  # the alias the query uses for it (the table name when unaliased)


@dataclass
class JoinPair:
    left: str  # schema.table.column, registry spelling
    right: str
    via: str  # "relationship", "approved_rule" or "same_column"


@dataclass
class QueryAnalysis:
    tables: list[TableRef] = field(default_factory=list)
    # registry qualified table -> registry column names referenced anywhere in the query
    columns: dict[str, set[str]] = field(default_factory=dict)
    joins: list[JoinPair] = field(default_factory=list)
    aggregate_only: bool = False
    ctes: int = 0
    subqueries: int = 0
    join_count: int = 0
    nesting_depth: int = 0

    @property
    def qualified_tables(self) -> list[str]:
        return sorted({t.table.qualified for t in self.tables}, key=str.lower)

    @property
    def qualified_columns(self) -> list[str]:
        return sorted((f"{t}.{c}" for t, cols in self.columns.items() for c in cols), key=str.lower)

    def referenced_map(self) -> dict[str, set[str]]:
        result: dict[str, set[str]] = {t: set() for t in self.qualified_tables}
        for table, cols in self.columns.items():
            result.setdefault(table, set()).update(cols)
        return result

    @property
    def operation(self) -> str:
        return "aggregate" if self.aggregate_only else "read"


def _ref_violation(code: str, internal: str) -> Violation:
    return violation(code, Category.SCHEMA_REFERENCE, SAFE_REFERENCE_MESSAGE, internal)


def _join_violation(code: str, message: str, internal: str | None = None) -> Violation:
    return violation(code, Category.JOIN_NOT_ALLOWED, message, internal)


class ReferenceValidator:
    def __init__(self, schema: SchemaContext, dialect: SQLDialect, options: GuardOptions) -> None:
        self._schema = schema
        self._dialect = dialect
        self._options = options
        self._case_sensitive_quotes = dialect.key != "tsql"

    def analyse(self, statement: exp.Expression) -> QueryAnalysis:
        analysis = QueryAnalysis()
        cte_names = self._check_ctes(statement)
        self._check_join_shapes(statement)
        self._classify_tables(statement, cte_names, analysis)
        qualified = self._qualify(statement)
        self._check_quoted_columns(statement, analysis, cte_names)
        self._analyse_scopes(qualified, analysis)
        return analysis

    @staticmethod
    def _check_join_shapes(statement: exp.Expression) -> None:
        """Reject USING / NATURAL / CROSS joins on the original AST: qualify() would rewrite USING into ON."""
        problems: list[Violation] = []
        for join in statement.find_all(exp.Join):
            kind = (join.kind or "").upper()
            method = (join.method or "").upper()
            if kind == "CROSS":
                problems.append(_join_violation("cross_join", "CROSS JOIN is not allowed; join on a registered relationship."))
            elif method == "NATURAL":
                problems.append(_join_violation("natural_join", "NATURAL JOIN is not allowed; join on a registered relationship."))
            elif join.args.get("using"):
                problems.append(_join_violation("join_using", "Write joins as ON a.column = b.column using registered relationships."))
        if problems:
            raise GuardRejected(problems)

    # --- CTEs ---------------------------------------------------------------------------------------

    def _check_ctes(self, statement: exp.Expression) -> set[str]:
        names: list[str] = []
        problems: list[Violation] = []
        registered_names = {t.table_name.lower() for t in self._schema.tables.values()}
        for cte in statement.find_all(exp.CTE):
            name = cte.alias_or_name.lower()
            if not name:
                problems.append(_ref_violation("unnamed_cte", "CTE without a name"))
                continue
            if name in names:
                problems.append(violation("identifier_confusion", Category.SCHEMA_REFERENCE,
                                          "Each CTE in the query needs a distinct name.", f"duplicate CTE {name}"))
            if name in registered_names:
                problems.append(violation("identifier_confusion", Category.SCHEMA_REFERENCE,
                                          "A CTE may not use the name of a registered table.", f"CTE {name} shadows a table"))
            names.append(name)
        if problems:
            raise GuardRejected(problems)
        return set(names)

    # --- tables -------------------------------------------------------------------------------------

    def _classify_tables(self, statement: exp.Expression, cte_names: set[str], analysis: QueryAnalysis) -> None:
        problems: list[Violation] = []
        namespaces: set[str] = set()
        for index, node in enumerate(statement.find_all(exp.Table)):
            node.meta[REF_KEY] = index
            name, db = node.name, node.db
            if node.catalog:
                problems.append(violation("cross_database_reference", Category.CROSS_SCHEMA,
                                          "Tables must be written schema.table, without a database or server prefix.",
                                          f"catalog-qualified table {node.catalog}.{db}.{name}"))
                continue
            if not db:
                if name.lower() in cte_names:
                    continue
                problems.append(_ref_violation("unqualified_table", f"unqualified table {name}"))
                continue
            table = self._schema.table(f"{db}.{name}")
            if table is None:
                problems.append(_ref_violation("unknown_table", f"table {db}.{name} is not registered"))
                continue
            if self._case_sensitive_quotes and not self._spelling_matches(node, table):
                problems.append(violation("identifier_confusion", Category.SCHEMA_REFERENCE, SAFE_REFERENCE_MESSAGE,
                                          f"quoted identifier {db}.{name} differs from registered {table.qualified}"))
                continue
            namespace = table.schema_name.lower()
            if self._options.allowed_namespaces and namespace not in self._options.allowed_namespaces:
                problems.append(violation("namespace_not_allowed", Category.CROSS_SCHEMA, SAFE_REFERENCE_MESSAGE,
                                          f"namespace {namespace} is not allowed"))
                continue
            namespaces.add(namespace)
            analysis.tables.append(TableRef(ref=index, table=table, alias=node.alias_or_name))
        if len(namespaces) > 1 and not self._options.allow_cross_schema:
            problems.append(violation("cross_schema_not_allowed", Category.CROSS_SCHEMA,
                                      "Queries may not combine tables from different schema namespaces.",
                                      f"namespaces {sorted(namespaces)}"))
        if problems:
            raise GuardRejected(problems)

    @staticmethod
    def _spelling_matches(node: exp.Table, table: TableInfo) -> bool:
        db_id, name_id = node.args.get("db"), node.this
        if isinstance(db_id, exp.Identifier) and db_id.quoted and db_id.this != table.schema_name:
            return False
        if isinstance(name_id, exp.Identifier) and name_id.quoted and name_id.this != table.table_name:
            return False
        return True

    # --- columns ------------------------------------------------------------------------------------

    def _mapping(self) -> dict[str, dict[str, dict[str, str]]]:
        mapping: dict[str, dict[str, dict[str, str]]] = {}
        for table in self._schema.tables.values():
            mapping.setdefault(table.schema_name.lower(), {})[table.table_name.lower()] = {
                c.name.lower(): _SQLGLOT_TYPES.get(c.category, "TEXT") for c in table.columns.values()
            }
        return mapping

    def _qualify(self, statement: exp.Expression) -> exp.Expression:
        lowered = statement.copy()
        for identifier in lowered.find_all(exp.Identifier):
            identifier.set("this", identifier.this.lower())
            identifier.set("quoted", False)
        try:
            return qualify(
                lowered,
                schema=MappingSchema(self._mapping(), dialect=self._dialect.sqlglot_dialect, normalize=False),
                dialect=self._dialect.sqlglot_dialect,
                validate_qualify_columns=True,
                quote_identifiers=False,
                expand_stars=False,
                identify=False,
            )
        except OptimizeError as exc:
            raise GuardRejected([_ref_violation("unresolved_column", _short(str(exc)))]) from None
        except SqlglotError as exc:
            raise GuardRejected([_ref_violation("unresolved_identifier", type(exc).__name__)]) from None

    def _check_quoted_columns(self, statement: exp.Expression, analysis: QueryAnalysis, cte_names: set[str]) -> None:
        """A quoted column name must match a registered column (or a name the query itself defines) exactly."""
        if not self._case_sensitive_quotes:
            return
        known = {c.name for ref in analysis.tables for c in ref.table.columns.values()}
        known |= {a.alias for a in statement.find_all(exp.Alias) if a.alias}
        for alias in statement.find_all(exp.TableAlias):
            known |= {c.name for c in alias.columns}
        for column in statement.find_all(exp.Column):
            identifier = column.this
            if isinstance(identifier, exp.Identifier) and identifier.quoted and identifier.this not in known:
                raise GuardRejected([violation("identifier_confusion", Category.SCHEMA_REFERENCE, SAFE_REFERENCE_MESSAGE,
                                               f"quoted column {identifier.this!r} does not match a registered spelling")])

    # --- scopes -------------------------------------------------------------------------------------

    def _analyse_scopes(self, qualified: exp.Expression, analysis: QueryAnalysis) -> None:
        try:
            scopes = traverse_scope(qualified)
        except SqlglotError as exc:
            raise GuardRejected([_ref_violation("unresolved_identifier", type(exc).__name__)]) from None
        refs = {r.ref: r for r in analysis.tables}
        problems: list[Violation] = []
        for scope in scopes:
            if scope.is_cte:
                analysis.ctes += 1
            elif scope.is_subquery or scope.is_derived_table:
                analysis.subqueries += 1
            for column in scope.columns:
                problem = self._record_column(scope, column, refs, analysis)
                if problem is not None:
                    problems.append(problem)
            problems.extend(self._check_joins(scope, refs, analysis))
            problems.extend(self._check_correlation(scope, refs))
            problems.extend(self._check_in_subqueries(scope, refs))
        analysis.join_count = sum(1 for _ in qualified.find_all(exp.Join))
        analysis.nesting_depth = max(
            (_depth(select) for select in qualified.find_all(exp.Select)), default=0
        )
        analysis.aggregate_only = _aggregate_only(qualified)
        if problems:
            raise GuardRejected(problems)

    def _record_column(
        self, scope: Scope, column: exp.Column, refs: dict[int, TableRef], analysis: QueryAnalysis
    ) -> Violation | None:
        if isinstance(column.this, exp.Star):
            return violation("select_star", Category.UNSAFE_CONSTRUCT, "SELECT * is not allowed; select explicit columns.")
        if not column.table:
            if column.name in _projection_aliases(scope):
                return None
            return _ref_violation("unresolved_column", f"column {column.name} has no source")
        source = _source(scope, column.table)
        if source is None:
            return _ref_violation("unresolved_column", f"source {column.table} of {column.name} not found")
        if isinstance(source, exp.Table):
            ref = refs.get(source.meta.get(REF_KEY, -1))
            if ref is None:
                return _ref_violation("unresolved_column", f"table source of {column.name} is not registered")
            info = ref.table.column(column.name)
            if info is None:
                return _ref_violation("unknown_column", f"column {ref.table.qualified}.{column.name} is not registered")
            analysis.columns.setdefault(ref.table.qualified, set()).add(info.name)
            return None
        if isinstance(source, Scope):
            if column.name not in _output_names(source):
                return _ref_violation("unknown_column", f"derived column {column.table}.{column.name} not projected")
            return None
        return _ref_violation("unresolved_column", f"unsupported source for {column.name}")

    # --- joins --------------------------------------------------------------------------------------

    def _check_joins(self, scope: Scope, refs: dict[int, TableRef], analysis: QueryAnalysis) -> list[Violation]:
        select = scope.expression
        if not isinstance(select, exp.Select):
            return []
        problems: list[Violation] = []
        for join in select.args.get("joins") or []:
            kind = (join.kind or "").upper()
            if kind == "CROSS":
                problems.append(_join_violation("cross_join", "CROSS JOIN is not allowed; join on a registered relationship."))
                continue
            if join.args.get("using"):
                problems.append(_join_violation("join_using", "Write joins as ON a.column = b.column using registered relationships."))
                continue
            on = join.args.get("on")
            if on is None:
                problems.append(_join_violation("join_without_condition",
                                                "Every join needs an ON condition that follows a registered relationship."))
                continue
            problems.extend(self._check_condition(scope, refs, on, analysis, context="join"))
        return problems

    def _check_condition(
        self, scope: Scope, refs: dict[int, TableRef], condition: exp.Expression, analysis: QueryAnalysis, *, context: str
    ) -> list[Violation]:
        registered = 0
        for conjunct in _conjuncts(condition):
            sources = {c.table for c in conjunct.find_all(exp.Column)}
            if len(sources) <= 1:
                if isinstance(conjunct, exp.Or) or conjunct.find(exp.Or):
                    # A single-source OR is harmless, but it cannot be a relationship.
                    pass
                continue
            if not (isinstance(conjunct, exp.EQ) and isinstance(conjunct.left, exp.Column) and isinstance(conjunct.right, exp.Column)):
                return [_join_violation("join_condition_not_allowed",
                                        "Join conditions may only compare columns through registered relationships.",
                                        f"{context} predicate {type(conjunct).__name__} across sources")]
            left = self._physical(scope, conjunct.left, refs)
            right = self._physical(scope, conjunct.right, refs)
            if left is None or right is None:
                return [_join_violation("join_not_traceable",
                                        "A join compares computed values; join on registered relationship columns.",
                                        f"{context} column not traceable to a physical column")]
            via = self._relationship(left, right)
            if via is None:
                return [_join_violation("unregistered_join",
                                        "A join does not follow a registered relationship.",
                                        f"{context} {left} = {right} is not a registered relationship")]
            analysis.joins.append(JoinPair(left, right, via))
            registered += 1
        if registered == 0:
            return [_join_violation("join_without_relationship",
                                    "Every join must compare the columns of a registered relationship.",
                                    f"{context} without a relationship equality")]
        return []

    def _check_correlation(self, scope: Scope, refs: dict[int, TableRef]) -> list[Violation]:
        external = list(scope.external_columns)
        if not external:
            return []
        select = scope.expression
        where = select.args.get("where") if isinstance(select, exp.Select) else None
        for column in external:
            conjunct = _enclosing_conjunct(column, where)
            if conjunct is None:
                return [_join_violation("correlation_not_allowed",
                                        "Correlated subqueries may only match registered relationship columns in WHERE.",
                                        "outer reference outside a WHERE conjunct")]
        if where is None:
            return []
        dummy = QueryAnalysis()
        relevant = [c for c in _conjuncts(where.this) if any(col in external for col in c.find_all(exp.Column))]
        problems: list[Violation] = []
        for conjunct in relevant:
            problems.extend(self._check_condition(scope, refs, conjunct, dummy, context="correlated subquery"))
        return problems

    def _check_in_subqueries(self, scope: Scope, refs: dict[int, TableRef]) -> list[Violation]:
        select = scope.expression
        if not isinstance(select, exp.Select):
            return []
        problems: list[Violation] = []
        for node in _own_nodes(select, exp.In):
            query = node.args.get("query")
            if query is None:
                continue
            inner = query.unnest() if isinstance(query, exp.Subquery) else query
            if not isinstance(node.this, exp.Column) or not isinstance(inner, exp.Select) or len(inner.expressions) != 1:
                problems.append(_join_violation("subquery_join_not_allowed",
                                                "IN (SELECT ...) must compare one column with one registered related column."))
                continue
            projected = inner.expressions[0].unalias()
            inner_scope = next((s for s in scope.subquery_scopes if s.expression is inner), None)
            left = self._physical(scope, node.this, refs)
            right = self._physical(inner_scope, projected, refs) if inner_scope and isinstance(projected, exp.Column) else None
            if left is None or right is None or self._relationship(left, right) is None:
                problems.append(_join_violation("subquery_join_not_allowed",
                                                "IN (SELECT ...) must compare one column with one registered related column.",
                                                f"IN subquery {left} / {right}"))
        return problems

    def _physical(self, scope: Scope | None, column: exp.Expression, refs: dict[int, TableRef], depth: int = 0) -> str | None:
        """``schema.table.column`` (registry spelling) that ``column`` reads, through plain derived projections."""
        if scope is None or not isinstance(column, exp.Column) or depth > 20:
            return None
        source = _source(scope, column.table)
        if isinstance(source, exp.Table):
            ref = refs.get(source.meta.get(REF_KEY, -1))
            info = ref.table.column(column.name) if ref else None
            return f"{ref.table.qualified}.{info.name}" if ref and info else None
        if isinstance(source, Scope) and isinstance(source.expression, exp.Select):
            for projection in source.expression.expressions:
                if projection.alias_or_name == column.name:
                    inner = projection.unalias()
                    return self._physical(source, inner, refs, depth + 1) if isinstance(inner, exp.Column) else None
        return None

    def _relationship(self, left: str, right: str) -> str | None:
        a, b = left.lower(), right.lower()
        if a == b:
            return "same_column"  # a table matched with itself on the same column (for example IN on its key)
        for r in self._schema.relationships:
            if {a, b} == {f"{r.source_table}.{r.source_column}".lower(), f"{r.target_table}.{r.target_column}".lower()}:
                return "relationship"
        if frozenset({a, b}) in self._options.approved_joins:
            return "approved_rule"
        return None


# --- helpers --------------------------------------------------------------------------------------------


def _short(message: str) -> str:
    return message.split(". Line:")[0][:200]


def _source(scope: Scope, name: str) -> exp.Table | Scope | None:
    current: Scope | None = scope
    while current is not None:
        if name in current.sources:
            return current.sources[name]  # type: ignore[no-any-return]
        current = current.parent
    return None


def _output_names(scope: Scope) -> set[str]:
    expression = scope.expression
    selects = expression.selects if isinstance(expression, (exp.Select, exp.SetOperation)) else []
    return {s.alias_or_name for s in selects}


def _projection_aliases(scope: Scope) -> set[str]:
    expression = scope.expression
    if not isinstance(expression, exp.Select):
        return set()
    return {p.alias for p in expression.expressions if isinstance(p, exp.Alias)}


def _conjuncts(condition: exp.Expression) -> Iterable[exp.Expression]:
    node = condition
    while isinstance(node, exp.Paren):
        node = node.this
    if isinstance(node, exp.And):
        yield from _conjuncts(node.left)
        yield from _conjuncts(node.right)
    else:
        yield node


def _enclosing_conjunct(column: exp.Column, where: exp.Expression | None) -> exp.Expression | None:
    if where is None:
        return None
    node: exp.Expression = column
    while node.parent is not None and node.parent is not where:
        if isinstance(node.parent, exp.And):
            return node
        node = node.parent
    return node if node.parent is where else None


def _own_nodes(select: exp.Select, kind: type[exp.Expression]) -> Iterable[exp.Expression]:
    for node in select.find_all(kind):
        if node.find_ancestor(exp.Select) is select:
            yield node


def _depth(select: exp.Select) -> int:
    depth = 0
    node = select.parent
    while node is not None:
        if isinstance(node, (exp.Select, exp.SetOperation)):
            depth += 1
        node = node.parent
    return depth


def _aggregate_only(statement: exp.Expression) -> bool:
    """True when every top-level SELECT aggregates (Module 04's ``aggregate`` operation)."""
    root = statement
    selects = [root] if isinstance(root, exp.Select) else list(_leaf_selects(root))
    if not selects:
        return False
    for select in selects:
        if not isinstance(select, exp.Select):
            return False
        aggregated = any(
            a.find_ancestor(exp.Select) is select and a.find_ancestor(exp.Window) is None
            for p in select.expressions
            for a in p.find_all(exp.AggFunc)
        )
        if not aggregated:
            return False
    return True


def _leaf_selects(node: exp.Expression) -> Iterable[exp.Expression]:
    if isinstance(node, exp.SetOperation):
        yield from _leaf_selects(node.left)
        yield from _leaf_selects(node.right)
    elif isinstance(node, exp.Subquery):
        yield from _leaf_selects(node.this)
    else:
        yield node

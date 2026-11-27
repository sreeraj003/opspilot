"""Preliminary SQL validation with SQLGlot.

This rejects obviously invalid or unsupported SQL **before** a generated query
is returned. It is not the security guard: Module 11 parses the SQL again and
enforces table, column and row-level permissions, function allowlists,
complexity limits and the exact approved SQL identity. Passing this validator
never makes a query trusted or executable.

Checks, in order (SQLGlot's tokenizer and parser for the configured dialect;
regular expressions are not used to understand SQL):

1. Tokens: no comments, at most one trailing semicolon, no tokens or quoting
   from another dialect (``LIMIT`` in T-SQL, ``"x"`` in MySQL, brackets in
   MySQL or outside ``ARRAY[...]`` in PostgreSQL).
2. Parse: the SQL parses, exactly one statement exists, and it is a SELECT
   (optionally with ``WITH`` or a set operation of SELECTs).
3. Unsafe constructs anywhere in the tree: DML, DDL, DCL, MERGE, EXEC/CALL,
   commands the parser could not understand, SELECT INTO, locking clauses,
   transactions, SET, stored-procedure and dynamic-SQL functions, system
   catalogs. These are *blocking*: the query is discarded and never
   regenerated.
4. Tables: every table is qualified ``schema.table`` and is one of the tables
   in the generation context (permitted and relevant). Unknown, unregistered
   and restricted tables get the same generic message, so validation feedback
   never confirms that a restricted table exists.
5. Columns: SQLGlot's qualifier resolves every column against the context's
   *visible* columns only; an unknown, ambiguous or hidden column fails
   (never silently accepted). ``SELECT *`` and ``table.*`` are rejected.
6. Joins: equality join conditions must follow registered relationships.
7. Aggregation and grouping: SUM/AVG only over numeric columns; non-aggregated
   selected columns must be grouped.
8. Result size: anything but a single aggregate row needs a literal row limit
   no larger than ``AI_SQL_MAX_ROW_LIMIT`` (and the intent's limit).
9. Intent coverage: the intent's filters, time-range column, metrics and
   dimensions appear in the query.

Issues from steps 4 to 9 (and syntax or dialect errors) are *correctable*: the
service may ask the model again, with these backend-written messages only.
"""

from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.dialects.dialect import Dialect
from sqlglot.errors import OptimizeError, ParseError, SqlglotError, TokenError
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, traverse_scope
from sqlglot.schema import MappingSchema

from app.ai.sql_generation.context.builder import SQLGenerationContext
from app.ai.sql_generation.normalizers.sql_output import render_sql
from app.ai.sql_generation.schemas.result import JoinUsed
from app.models.sql_generation import SQL_MAX_LENGTH

# Statement and clause types that are never allowed (class name -> what to call it).
_FORBIDDEN_NODES: dict[str, str] = {
    "Insert": "INSERT",
    "Update": "UPDATE",
    "Delete": "DELETE",
    "Drop": "DROP",
    "Alter": "ALTER",
    "AlterTable": "ALTER",
    "Create": "CREATE",
    "TruncateTable": "TRUNCATE",
    "Grant": "GRANT",
    "Revoke": "REVOKE",
    "Merge": "MERGE",
    "Command": "an unsupported command",
    "Execute": "EXECUTE",
    "ExecuteSql": "EXECUTE",
    "Into": "SELECT INTO",
    "Lock": "a locking clause",
    "Transaction": "a transaction statement",
    "Commit": "a transaction statement",
    "Rollback": "a transaction statement",
    "Use": "USE",
    "Set": "SET",
    "Pragma": "PRAGMA",
    "Copy": "COPY",
    "Describe": "DESCRIBE",
    "Show": "SHOW",
    "Analyze": "ANALYZE",
    "Cache": "CACHE",
    "Uncache": "UNCACHE",
    "LoadData": "LOAD DATA",
    "Refresh": "REFRESH",
}
_FORBIDDEN_TYPES = tuple(getattr(exp, name) for name in _FORBIDDEN_NODES if hasattr(exp, name))

# Functions that execute dynamic SQL, reach outside the database, read files or stall the server.
_DANGEROUS_FUNCTIONS = frozenset(
    {
        "sp_executesql", "exec", "execute", "xp_cmdshell", "openrowset", "opendatasource", "openquery", "openxml",
        "dblink", "dblink_exec", "query_to_xml", "query_to_xml_and_xmlschema", "pg_sleep", "pg_sleep_for",
        "pg_sleep_until", "sleep", "benchmark", "waitfor", "load_file", "pg_read_file", "pg_read_binary_file",
        "pg_ls_dir", "pg_stat_file", "lo_import", "lo_export", "lo_get", "pg_terminate_backend", "pg_cancel_backend",
        "pg_reload_conf", "set_config", "current_setting", "sys_exec", "sys_eval", "xp_regread", "xp_dirtree",
        "sp_oacreate", "sp_oamethod", "get_lock", "release_lock", "copy", "prepare", "deallocate",
    }
)
_SYSTEM_SCHEMAS = frozenset(
    {"information_schema", "pg_catalog", "pg_toast", "sys", "mysql", "performance_schema", "master", "msdb", "tempdb"}
)
_NUMERIC = "number"


@dataclass(frozen=True, slots=True)
class SQLIssue:
    code: str
    message: str  # written by the backend; may name identifiers the model itself wrote
    blocking: bool = False  # unsafe: never regenerated

    def as_dict(self) -> dict[str, object]:
        return {"code": self.code, "message": self.message, "blocking": self.blocking}


@dataclass
class PreliminaryResult:
    issues: list[SQLIssue] = field(default_factory=list)
    sql: str | None = None  # normalized SQL (SQLGlot rendering in the same dialect)
    referenced_tables: list[str] = field(default_factory=list)  # registry spelling, schema.table
    referenced_columns: list[str] = field(default_factory=list)  # registry spelling, schema.table.column
    joins: list[JoinUsed] = field(default_factory=list)
    aggregating: bool = False
    # Tables referenced that exist in the registry but are outside the context (restricted or irrelevant).
    outside_context: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.issues

    @property
    def unsafe(self) -> bool:
        return any(i.blocking for i in self.issues)

    def referenced_map(self) -> dict[str, set[str]]:
        result: dict[str, set[str]] = {t: set() for t in self.referenced_tables}
        for column in self.referenced_columns:
            table, name = column.rsplit(".", 1)
            result.setdefault(table, set()).add(name)
        return result


class PreliminarySQLValidator:
    def __init__(self, context: SQLGenerationContext) -> None:
        self._context = context
        self._dialect = context.dialect
        self._read = context.dialect.sqlglot_dialect

    # =========================================================================================
    # entry point
    # =========================================================================================

    def validate(self, sql: str) -> PreliminaryResult:
        result = PreliminaryResult()
        text = sql.strip()
        if not text:
            result.issues.append(SQLIssue("empty_sql", "The query is empty."))
            return result
        if len(text) > SQL_MAX_LENGTH:
            result.issues.append(SQLIssue("sql_too_long", f"The query is longer than {SQL_MAX_LENGTH} characters."))
            return result
        if any(ord(ch) < 32 and ch not in "\t\r\n" for ch in text):
            result.issues.append(SQLIssue("control_characters", "The query contains control characters.", True))
            return result

        if not self._check_tokens(text, result):
            return result
        statement = self._parse(text, result)
        if statement is None:
            return result
        self._check_unsafe(statement, result)
        if result.unsafe:
            return result
        if not self._check_tables(statement, result):
            return result
        qualified = self._qualify(statement, result)
        if qualified is None:
            return result
        self._check_select_star(statement, result)
        self._analyse_scopes(qualified, result)
        self._check_row_limit(statement, result)
        self._check_intent_coverage(qualified, result)
        result.sql = render_sql(statement, self._read)
        return result

    # =========================================================================================
    # 1. tokens
    # =========================================================================================

    def _check_tokens(self, text: str, result: PreliminaryResult) -> bool:
        try:
            tokens = Dialect.get_or_raise(self._read).tokenize(text)
        except TokenError:
            result.issues.append(SQLIssue("invalid_syntax", f"The query is not valid {self._dialect.display_name} SQL."))
            return False
        if any(t.comments for t in tokens):
            result.issues.append(SQLIssue("comment_not_allowed", "Comments are not allowed in the query."))
        semicolons = [i for i, t in enumerate(tokens) if t.token_type.name == "SEMICOLON"]
        if semicolons and (len(semicolons) > 1 or semicolons[0] != len(tokens) - 1):
            result.issues.append(SQLIssue("multiple_statements", "Only one SQL statement is allowed.", True))
            return False
        for index, token in enumerate(tokens):
            name = token.token_type.name
            if name == "UNKNOWN":
                result.issues.append(
                    SQLIssue("dialect_mismatch", f"'{token.text}' is not valid {self._dialect.display_name} syntax.")
                )
            elif name in self._dialect.forbidden_tokens:
                result.issues.append(
                    SQLIssue(
                        "dialect_mismatch",
                        f"{token.text.upper()} is not {self._dialect.display_name} syntax. "
                        f"Row limits: {self._dialect.pagination}",
                    )
                )
            elif name in ("STRING", "IDENTIFIER") and text[token.start : token.start + 1] in self._dialect.forbidden_quote_chars:
                result.issues.append(
                    SQLIssue(
                        "dialect_mismatch",
                        f"{text[token.start]}...{text[token.start]} quoting is not {self._dialect.display_name} syntax. "
                        f"Identifiers: {self._dialect.identifier_quote}",
                    )
                )
            elif name == "L_BRACKET" and self._dialect.bracket_rule != "identifier":
                after_array = index > 0 and tokens[index - 1].token_type.name == "ARRAY"
                if self._dialect.bracket_rule == "forbidden" or not after_array:
                    result.issues.append(
                        SQLIssue(
                            "dialect_mismatch",
                            f"Square brackets are not identifier quotes in {self._dialect.display_name}. "
                            f"Identifiers: {self._dialect.identifier_quote}",
                        )
                    )
        if any(i.code == "dialect_mismatch" for i in result.issues):
            _dedupe(result)
            return False
        return True

    # =========================================================================================
    # 2. parse
    # =========================================================================================

    def _parse(self, text: str, result: PreliminaryResult) -> exp.Expression | None:
        try:
            statements = [s for s in sqlglot.parse(text, read=self._read) if s is not None]
        except (ParseError, TokenError):
            result.issues.append(SQLIssue("invalid_syntax", f"The query is not valid {self._dialect.display_name} SQL."))
            return None
        if not statements:
            result.issues.append(SQLIssue("empty_sql", "The query is empty."))
            return None
        if len(statements) > 1:
            result.issues.append(SQLIssue("multiple_statements", "Only one SQL statement is allowed.", True))
            return None
        statement = statements[0]
        root = statement.unnest() if isinstance(statement, exp.Subquery) else statement
        if not isinstance(root, (exp.Select, exp.SetOperation)):
            result.issues.append(
                SQLIssue("statement_not_allowed", f"Only SELECT queries are allowed, not {_describe(root)}.", True)
            )
            return None
        return statement

    # =========================================================================================
    # 3. unsafe constructs
    # =========================================================================================

    def _check_unsafe(self, statement: exp.Expression, result: PreliminaryResult) -> None:
        for node in statement.walk():
            if isinstance(node, _FORBIDDEN_TYPES):
                result.issues.append(
                    SQLIssue("statement_not_allowed", f"The query contains {_describe(node)}, which is not allowed.", True)
                )
            elif isinstance(node, exp.Func):
                name = _function_name(node)
                if name in _DANGEROUS_FUNCTIONS:
                    result.issues.append(
                        SQLIssue("dangerous_function", f"The function {name.upper()} is not allowed.", True)
                    )
            elif isinstance(node, exp.Table):
                if node.db and node.db.lower() in _SYSTEM_SCHEMAS or node.name.lower() in _SYSTEM_SCHEMAS:
                    result.issues.append(SQLIssue("system_catalog", "System catalogs may not be queried.", True))
        _dedupe(result)

    # =========================================================================================
    # 4. tables
    # =========================================================================================

    def _check_tables(self, statement: exp.Expression, result: PreliminaryResult) -> bool:
        cte_names = {cte.alias_or_name.lower() for cte in statement.find_all(exp.CTE)}
        found: list[str] = []
        ok = True
        for table in statement.find_all(exp.Table):
            if not isinstance(table.this, exp.Identifier):
                result.issues.append(
                    SQLIssue("table_function_not_allowed", "Table functions are not allowed; select from registered tables.")
                )
                ok = False
                continue
            name, db = table.name, table.db
            if not db and name.lower() in cte_names:
                continue
            if table.catalog:
                result.issues.append(
                    SQLIssue("cross_database_reference", f"Write table '{table.sql(dialect=self._read)}' as schema.table only.")
                )
                ok = False
                continue
            if not db:
                result.issues.append(SQLIssue("unqualified_table", f"Qualify table '{name}' as schema.table."))
                ok = False
                continue
            qualified = f"{db}.{name}"
            context_table = self._context.table(qualified)
            if context_table is None:
                if self._context.schema.table(qualified) is not None:
                    result.outside_context.append(qualified.lower())
                result.issues.append(
                    SQLIssue("unknown_table", f"Table '{qualified}' is not one of the supplied tables.")
                )
                ok = False
                continue
            found.append(context_table.table.qualified)
        result.referenced_tables = sorted(set(found), key=str.lower)
        _dedupe(result)
        return ok

    # =========================================================================================
    # 5. columns
    # =========================================================================================

    def _qualify(self, statement: exp.Expression, result: PreliminaryResult) -> exp.Expression | None:
        lowered = statement.copy()
        for identifier in lowered.find_all(exp.Identifier):
            identifier.set("this", identifier.this.lower())
            identifier.set("quoted", False)
        try:
            return qualify(
                lowered,
                schema=MappingSchema(self._context.sqlglot_mapping(), dialect=self._read, normalize=False),
                dialect=self._read,
                validate_qualify_columns=True,
                quote_identifiers=False,
                expand_stars=False,
                identify=False,
            )
        except OptimizeError as exc:
            result.issues.append(SQLIssue("unknown_column", _column_message(str(exc))))
        except SqlglotError:
            result.issues.append(SQLIssue("unresolved_identifier", "An identifier in the query could not be resolved."))
        return None

    def _check_select_star(self, statement: exp.Expression, result: PreliminaryResult) -> None:
        for select in statement.find_all(exp.Select):
            for projection in select.expressions:
                if isinstance(projection, exp.Star) or (
                    isinstance(projection, exp.Column) and isinstance(projection.this, exp.Star)
                ):
                    result.issues.append(SQLIssue("select_star", "SELECT * is not allowed; select explicit columns."))
                    return

    # =========================================================================================
    # 6-7. scopes: columns, joins, aggregation
    # =========================================================================================

    def _analyse_scopes(self, qualified: exp.Expression, result: PreliminaryResult) -> None:
        columns: set[str] = set()
        try:
            scopes = traverse_scope(qualified)
        except SqlglotError:
            result.issues.append(SQLIssue("unresolved_identifier", "An identifier in the query could not be resolved."))
            return
        for scope in scopes:
            for column in scope.columns:
                resolved = self._resolve(scope, column)
                if resolved is not None:
                    columns.add(resolved)
            self._check_joins(scope, result)
            self._check_aggregation(scope, result)
        result.referenced_columns = sorted(columns, key=str.lower)
        result.aggregating = any(True for _ in qualified.find_all(exp.AggFunc))
        _dedupe(result)

    def _resolve(self, scope: Scope, column: exp.Column) -> str | None:
        """``schema.table.column`` in registry spelling when the column comes from a registered table."""
        source = scope.sources.get(column.table)
        if not isinstance(source, exp.Table) or isinstance(column.this, exp.Star):
            return None
        context_table = self._context.table(f"{source.db}.{source.name}")
        if context_table is None:
            return None
        info = context_table.table.column(column.name)
        return f"{context_table.table.qualified}.{info.name if info else column.name}"

    def _check_joins(self, scope: Scope, result: PreliminaryResult) -> None:
        select = scope.expression
        if not isinstance(select, exp.Select):
            return
        for join in select.args.get("joins") or []:
            on = join.args.get("on")
            join_type = " ".join(p for p in (join.side, join.kind) if p).upper() or "INNER"
            if on is None:
                if join.args.get("using"):
                    result.issues.append(
                        SQLIssue("join_using_not_allowed", "Write joins as ON a.column = b.column using registered relationships.")
                    )
                else:
                    result.issues.append(
                        SQLIssue("join_without_condition", "Every join needs an ON condition from a registered relationship.")
                    )
                continue
            pairs = []
            for eq in on.find_all(exp.EQ):
                left, right = eq.left, eq.right
                if not (isinstance(left, exp.Column) and isinstance(right, exp.Column)):
                    continue
                a, b = self._resolve(scope, left), self._resolve(scope, right)
                if a is None or b is None:
                    # A derived table (CTE or subquery) on either side: its own scope was checked.
                    if a is not None or b is not None:
                        pairs.append(None)
                    continue
                if a.rsplit(".", 1)[0].lower() == b.rsplit(".", 1)[0].lower():
                    continue
                registered = self._context.relationship_between(a, b) is not None
                result.joins.append(JoinUsed(left=a, right=b, join_type=join_type, registered_relationship=registered))
                pairs.append(registered)
                if not registered:
                    result.issues.append(
                        SQLIssue(
                            "unregistered_join",
                            f"The join {a} = {b} does not follow a registered relationship. "
                            "Use only the supplied relationships.",
                        )
                    )
            if not pairs:
                result.issues.append(
                    SQLIssue("join_without_relationship", "A join does not compare columns of a registered relationship.")
                )

    def _check_aggregation(self, scope: Scope, result: PreliminaryResult) -> None:
        select = scope.expression
        if not isinstance(select, exp.Select):
            return
        for agg in select.find_all(exp.Sum, exp.Avg):
            if _scope_of(agg) is not select:
                continue
            target = agg.this
            if isinstance(target, exp.Distinct):
                target = target.expressions[0] if target.expressions else None
            if isinstance(target, exp.Column):
                resolved = self._resolve(scope, target)
                if resolved is not None:
                    found = self._context.schema.resolve_column(resolved)
                    if found is not None and found[1].category != _NUMERIC:
                        result.issues.append(
                            SQLIssue(
                                "invalid_aggregation",
                                f"{type(agg).__name__.upper()} cannot be applied to non-numeric column {resolved}.",
                            )
                        )
        projections = [p.unalias() for p in select.expressions]
        has_aggregate = any(
            any(_scope_of(a) is select and a.find_ancestor(exp.Window) is None for a in p.find_all(exp.AggFunc))
            for p in projections
        )
        if not has_aggregate:
            return
        group = select.args.get("group")
        group_exprs = {g.sql() for g in group.expressions} if group else set()
        for projection in projections:
            if projection.find(exp.AggFunc) or isinstance(projection, exp.Literal):
                continue
            if projection.sql() in group_exprs:
                continue
            for column in projection.find_all(exp.Column):
                if column.sql() not in group_exprs:
                    result.issues.append(
                        SQLIssue(
                            "missing_group_by",
                            f"Selected column {column.sql()} is neither aggregated nor in GROUP BY.",
                        )
                    )
                    return

    # =========================================================================================
    # 8. row limit
    # =========================================================================================

    def _check_row_limit(self, statement: exp.Expression, result: PreliminaryResult) -> None:
        root = statement.unnest() if isinstance(statement, exp.Subquery) else statement
        if isinstance(root, exp.Select) and not root.args.get("group") and _single_aggregate_row(root):
            return
        limit = root.args.get("limit")
        count: exp.Expression | None = None
        if isinstance(limit, exp.Limit):
            count = limit.expression
        elif isinstance(limit, exp.Fetch):
            count = limit.args.get("count")
        if count is None and isinstance(root.args.get("fetch"), exp.Fetch):
            count = root.args["fetch"].args.get("count")
        if count is None:
            result.issues.append(
                SQLIssue(
                    "unbounded_result",
                    f"The query must limit its rows (at most {self._context.max_row_limit}). "
                    f"Row limits: {self._dialect.pagination}",
                )
            )
            return
        if isinstance(count, exp.Paren):
            count = count.this
        if not (isinstance(count, exp.Literal) and not count.is_string and count.this.isdigit()):
            result.issues.append(SQLIssue("row_limit_invalid", "The row limit must be a whole number."))
            return
        value = int(count.this)
        maximum = self._context.max_row_limit
        intent_limit = self._context.intent.aggregation.limit
        if value < 1 or value > maximum:
            result.issues.append(SQLIssue("row_limit_too_large", f"The row limit must be between 1 and {maximum}."))
        elif intent_limit is not None and value > intent_limit:
            result.issues.append(
                SQLIssue("row_limit_exceeds_intent", f"The question asks for at most {intent_limit} rows.")
            )

    # =========================================================================================
    # 9. intent coverage
    # =========================================================================================

    def _check_intent_coverage(self, qualified: exp.Expression, result: PreliminaryResult) -> None:
        referenced = {c.lower() for c in result.referenced_columns}
        filtered = self._filtered_columns(qualified)
        intent = self._context.intent
        for item in intent.filters:
            if item.field.qualified.lower() not in filtered:
                result.issues.append(
                    SQLIssue("filter_missing", f"The filter on {item.field.qualified} from the business intent is missing.")
                )
        date_column = intent.time.date_column
        if intent.time.time_range is not None and date_column is not None and date_column.qualified.lower() not in filtered:
            result.issues.append(
                SQLIssue(
                    "time_filter_missing",
                    f"The time range ({intent.time.time_range.label}) must filter {date_column.qualified}.",
                )
            )
        for metric in intent.requested_metrics:
            if metric.column is not None and metric.column.qualified.lower() not in referenced:
                result.issues.append(
                    SQLIssue("metric_missing", f"The metric '{metric.name}' must use {metric.column.qualified}.")
                )
        for dimension in intent.requested_dimensions:
            if dimension.column.qualified.lower() not in referenced:
                result.issues.append(
                    SQLIssue("dimension_missing", f"The grouping by {dimension.column.qualified} is missing.")
                )

    def _filtered_columns(self, qualified: exp.Expression) -> set[str]:
        """Registered columns that appear in a WHERE or HAVING clause (any scope), lower case."""
        found: set[str] = set()
        for scope in traverse_scope(qualified):
            select = scope.expression
            if not isinstance(select, exp.Select):
                continue
            for clause in (select.args.get("where"), select.args.get("having")):
                if clause is None:
                    continue
                for column in clause.find_all(exp.Column):
                    resolved = self._resolve(scope, column)
                    if resolved is not None:
                        found.add(resolved.lower())
        return found


# --- helpers ------------------------------------------------------------------------------------------------


def _describe(node: exp.Expression) -> str:
    if isinstance(node, exp.Command):
        keyword = str(node.this or "").split()[0].upper() if node.this else "a command"
        return keyword or "a command"
    name = type(node).__name__
    if name in _FORBIDDEN_NODES:
        return _FORBIDDEN_NODES[name]
    key = getattr(node, "key", name).upper()
    return f"a {key} statement"


def _function_name(node: exp.Func) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.this).lower()
    return node.sql_name().lower()


def _scope_of(node: exp.Expression) -> exp.Expression | None:
    return node.find_ancestor(exp.Select)


def _single_aggregate_row(select: exp.Select) -> bool:
    """An aggregate-only SELECT without GROUP BY returns exactly one row."""
    projections = [p.unalias() for p in select.expressions]
    if not projections:
        return False
    for projection in projections:
        aggregates = [a for a in projection.find_all(exp.AggFunc) if _scope_of(a) is select and a.find_ancestor(exp.Window) is None]
        if not aggregates and not isinstance(projection, exp.Literal):
            return False
    return True


def _column_message(error: str) -> str:
    """SQLGlot's qualifier message, reduced to the identifier the model wrote (no positions or SQL)."""
    if error.startswith("Unknown column: "):
        name = error.removeprefix("Unknown column: ").strip().split()[0].strip("'\"`[]")
        return f"Column '{name}' is not one of the supplied columns of the referenced tables."
    marker = "Column '"
    if marker in error:
        name = error.split(marker, 1)[1].split("'", 1)[0]
        if "ambiguous" in error.lower():
            return f"Column '{name}' is ambiguous; qualify it with its table alias."
        return f"Column '{name}' is not one of the supplied columns of the referenced tables."
    if "ambiguous" in error.lower():
        return "A column is ambiguous; qualify every column with its table alias."
    return "A column could not be resolved against the supplied tables."


def _dedupe(result: PreliminaryResult) -> None:
    seen: set[tuple[str, str]] = set()
    unique: list[SQLIssue] = []
    for issue in result.issues:
        key = (issue.code, issue.message)
        if key not in seen:
            seen.add(key)
            unique.append(issue)
    result.issues = unique

"""The guard pipeline over one exact SQL statement (no database, no network).

``SQLSecurityInspector.inspect`` runs, in order, and stops at the first stage
that finds a violation:

1. parse the exact SQL with SQLGlot in the schema's dialect (``parsers``);
2. inspect the complete AST: statement type, allowed node types, functions
   (``validators.statement``);
3. validate tables, columns, aliases, scopes, joins and relationships against
   the registered schema (``validators.references``);
4. authorize exactly the referenced tables, columns and operation with the
   permission engine (the ``authorizer`` callback, which the validation
   service backs with Module 04's engine; deny by default);
5. inject row-level restrictions into every access path (``rewriters.row_security``);
6. enforce complexity limits (``validators.complexity``);
7. enforce the result limit (``rewriters.result_limit``);
8. render the exact SQL, re-validate the rewritten statement and prove the
   row-security post-condition, and hash it (SHA-256).

Nothing is executed. The outcome is data for ``SQLSecurityValidationService``,
which persists it and creates the approval.
"""

import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.ai.sql_generation.dialects.registry import SQLDialect
from app.ai.sql_security.parsers.sql_parser import parse_exact
from app.ai.sql_security.policies.functions import FunctionPolicy
from app.ai.sql_security.policies.limits import GuardOptions
from app.ai.sql_security.rewriters.result_limit import enforce_result_limit
from app.ai.sql_security.rewriters.row_security import apply_row_security, verify_row_security
from app.ai.sql_security.schemas.result import ValidationStatus
from app.ai.sql_security.validators.complexity import check_complexity
from app.ai.sql_security.validators.references import QueryAnalysis, ReferenceValidator
from app.ai.sql_security.validators.statement import inspect_statement
from app.ai.sql_security.validators.violations import (
    VALIDATION_FAILURE_CATEGORIES,
    Category,
    GuardRejected,
    Violation,
)
from app.ai.understanding.services.schema_context import SchemaContext


def sql_sha256(sql: str) -> str:
    """The canonical identity of an exact SQL statement: SHA-256 over its UTF-8 bytes."""
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class AccessDecision:
    """The permission engine's answer for exactly the referenced tables, columns and operation."""

    allowed: bool
    revision: int
    fingerprint: str | None = None
    # lower-case registry qualified table -> row-policy DSL
    row_filters: dict[str, dict[str, Any]] = field(default_factory=dict)
    sql_visible: bool = False
    internal_reason: str | None = None


Authorizer = Callable[[dict[str, set[str]], str], Awaitable[AccessDecision]]


@dataclass
class InspectionOutcome:
    status: ValidationStatus
    violations: list[Violation] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    referenced_tables: list[str] = field(default_factory=list)
    referenced_columns: list[str] = field(default_factory=list)
    functions: list[str] = field(default_factory=list)
    applied_row_restrictions: list[str] = field(default_factory=list)
    exact_sql: str | None = None
    query_hash: str | None = None
    access: AccessDecision | None = None

    @property
    def approved(self) -> bool:
        return self.status == ValidationStatus.APPROVED

    @property
    def category(self) -> Category | None:
        return self.violations[0].category if self.violations else None


def status_for(violations: list[Violation]) -> ValidationStatus:
    if any(v.category not in VALIDATION_FAILURE_CATEGORIES for v in violations):
        return ValidationStatus.REJECTED
    return ValidationStatus.VALIDATION_FAILED


class SQLSecurityInspector:
    def __init__(self, schema: SchemaContext, dialect: SQLDialect, options: GuardOptions) -> None:
        self._schema = schema
        self._dialect = dialect
        self._options = options
        self._functions = FunctionPolicy(
            dialect.key, allow_window_functions=options.allow_window_functions, extra=options.extra_functions
        )

    async def inspect(self, sql: str, authorizer: Authorizer) -> InspectionOutcome:
        outcome = InspectionOutcome(status=ValidationStatus.VALIDATION_FAILED)
        try:
            await self._run(sql, authorizer, outcome)
        except GuardRejected as rejected:
            outcome.violations = rejected.violations
            outcome.status = status_for(rejected.violations)
            outcome.exact_sql = None
            outcome.query_hash = None
            return outcome
        outcome.status = ValidationStatus.APPROVED
        return outcome

    async def _run(self, sql: str, authorizer: Authorizer, outcome: InspectionOutcome) -> None:
        read = self._dialect.sqlglot_dialect
        limits = self._options.limits
        # 1-2. parse and inspect the whole tree
        parsed = parse_exact(sql, self._dialect, max_length=limits.max_sql_length)
        statement = parsed.statement
        report = inspect_statement(statement, self._functions, allow_set_operations=self._options.allow_set_operations)
        outcome.functions = report.functions
        # 3. schema references (from the AST, never the model's lists)
        analysis = ReferenceValidator(self._schema, self._dialect, self._options).analyse(statement)
        outcome.referenced_tables = analysis.qualified_tables
        outcome.referenced_columns = analysis.qualified_columns
        if not analysis.tables:
            raise GuardRejected([_no_tables()])
        # 4. permissions for exactly these references
        access = await authorizer(analysis.referenced_map(), analysis.operation)
        outcome.access = access
        if not access.allowed:
            raise GuardRejected([_permission_denied(access.internal_reason)])
        # 5. row-level security on every access path
        statement, applied = apply_row_security(statement, analysis, access.row_filters, read)
        outcome.applied_row_restrictions = applied
        # 6. complexity (counted on the query as written, before the guard's own wrappers)
        check_complexity(analysis, report.node_count, limits)
        # 7. result limit
        statement, warnings = enforce_result_limit(statement, limits.max_result_rows)
        outcome.warnings.extend(warnings)
        # 8. the exact SQL, re-validated after rewriting
        exact_sql = statement.sql(dialect=read)
        self._revalidate(exact_sql, access)
        outcome.exact_sql = exact_sql
        outcome.query_hash = sql_sha256(exact_sql)

    def _revalidate(self, exact_sql: str, access: AccessDecision) -> None:
        reparsed = parse_exact(exact_sql, self._dialect, max_length=max(len(exact_sql) + 1, self._options.limits.max_sql_length))
        inspect_statement(reparsed.statement, self._functions, allow_set_operations=self._options.allow_set_operations)
        rewritten: QueryAnalysis = ReferenceValidator(self._schema, self._dialect, self._options).analyse(reparsed.statement)
        if not rewritten.tables:
            raise GuardRejected([_no_tables()])
        verify_row_security(exact_sql, self._dialect.sqlglot_dialect, self._schema, access.row_filters)


def _no_tables() -> Violation:
    return Violation("no_registered_table", Category.SCHEMA_REFERENCE,
                     "The query must read from at least one registered table.", "no physical table referenced")


def _permission_denied(internal: str | None) -> Violation:
    from app.ai.sql_security.validators.violations import SAFE_PERMISSION_MESSAGE

    return Violation("permission_denied", Category.PERMISSION_DENIED, SAFE_PERMISSION_MESSAGE,
                     internal or "the permission engine denied the request")

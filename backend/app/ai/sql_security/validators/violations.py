"""Violations found by the SQL security guard.

Each violation has a machine code, a rejection category (stored and audited),
a **safe** message for the user and a detailed internal reason for security
investigation. Safe messages never name tables or columns the user cannot
access: unknown and inaccessible objects produce the same message, so the
guard never confirms that a restricted object exists.
"""

from dataclasses import dataclass
from enum import StrEnum


class Category(StrEnum):
    GENERATION_NOT_READY = "generation_not_ready"
    SCHEMA_UNAVAILABLE = "schema_unavailable"
    SCHEMA_CHANGED = "schema_changed"
    CONNECTOR_UNAVAILABLE = "connector_unavailable"
    PARSE_ERROR = "parse_error"
    DIALECT_MISMATCH = "dialect_mismatch"
    STATEMENT_NOT_ALLOWED = "statement_not_allowed"
    MULTIPLE_STATEMENTS = "multiple_statements"
    UNSAFE_CONSTRUCT = "unsafe_construct"
    UNSAFE_FUNCTION = "unsafe_function"
    FUNCTION_NOT_ALLOWED = "function_not_allowed"
    SCHEMA_REFERENCE = "schema_reference"
    JOIN_NOT_ALLOWED = "join_not_allowed"
    CROSS_SCHEMA = "cross_schema"
    PERMISSION_DENIED = "permission_denied"
    ROW_SECURITY_UNENFORCEABLE = "row_security_unenforceable"
    COMPLEXITY_LIMIT = "complexity_limit"
    RESULT_LIMIT = "result_limit"
    INTEGRITY_FAILURE = "integrity_failure"


# Categories that mean "could not be validated" rather than "unsafe or unauthorized".
VALIDATION_FAILURE_CATEGORIES = frozenset(
    {
        Category.SCHEMA_UNAVAILABLE,
        Category.SCHEMA_CHANGED,
        Category.CONNECTOR_UNAVAILABLE,
        Category.PARSE_ERROR,
        Category.DIALECT_MISMATCH,
        Category.INTEGRITY_FAILURE,
    }
)

SAFE_REFERENCE_MESSAGE = (
    "The query references data that is not in the registered schema or that you are not permitted to query."
)
SAFE_PERMISSION_MESSAGE = "You are not permitted to query some of the data this query uses."


@dataclass(frozen=True, slots=True)
class Violation:
    code: str
    category: Category
    message: str  # safe for the user
    internal: str  # detailed, for audit only

    def user_view(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}

    def internal_view(self) -> dict[str, str]:
        return {"code": self.code, "category": self.category.value, "reason": self.internal}


class GuardRejected(Exception):
    """Raised by a pipeline stage. Carries every violation the stage found (at least one)."""

    def __init__(self, violations: list[Violation]) -> None:
        assert violations
        self.violations = _dedupe(violations)
        super().__init__("; ".join(v.code for v in self.violations))

    @property
    def category(self) -> Category:
        return self.violations[0].category


def _dedupe(violations: list[Violation]) -> list[Violation]:
    seen: set[tuple[str, str]] = set()
    unique: list[Violation] = []
    for violation in violations:
        key = (violation.code, violation.internal)
        if key not in seen:
            seen.add(key)
            unique.append(violation)
    return unique


def violation(code: str, category: Category, message: str, internal: str | None = None) -> Violation:
    return Violation(code, category, message, internal or message)

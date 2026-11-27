"""SQL generation contracts.

* ``LLMSQLOutput`` is what the model must return: one strict JSON object. The
  ``sql`` field is the only place SQL may appear; every prose field
  (``explanation``, ``assumptions``, ``clarification_question``) is checked by
  Module 09's executable-content detection, so SQL, code or tool instructions
  hidden in prose are rejected. Unknown fields, wrong types and bad identifiers
  are rejected too.
* ``SQLGenerationResult`` is what the backend returns: an **untrusted query
  artifact**. Its ``referenced_tables``, ``referenced_columns`` and
  ``joins_used`` come from parsing the SQL with SQLGlot, never from the model's
  own claims, and ``security_status`` is always
  ``awaiting_security_validation``: nothing in this module approves or runs SQL.
"""

import uuid
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.ai.understanding.schemas.intent import (
    QUALIFIED_COLUMN_PATTERN,
    QUALIFIED_TABLE_PATTERN,
    ParagraphText,
    SentenceText,
)
from app.models.sql_generation import SQL_MAX_LENGTH

_Table = Annotated[str, StringConstraints(pattern=QUALIFIED_TABLE_PATTERN)]
_Column = Annotated[str, StringConstraints(pattern=QUALIFIED_COLUMN_PATTERN)]


class GenerationStatus(StrEnum):
    GENERATED = "generated"
    CLARIFICATION_REQUIRED = "clarification_required"
    GENERATION_FAILED = "generation_failed"


class PreliminaryValidationStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    NOT_RUN = "not_run"


class FailureCategory(StrEnum):
    VALIDATION_FAILED = "validation_failed"  # still invalid after the bounded regenerations
    UNSAFE_SQL = "unsafe_sql"  # non-SELECT, several statements, procedures, dynamic SQL: never regenerated
    PERMISSION_DENIED = "permission_denied"
    MALFORMED_OUTPUT = "malformed_output"
    PROVIDER_ERROR = "provider_error"
    MODEL_DECLINED = "model_declined"


SECURITY_STATUS = "awaiting_security_validation"


# --- the model's reply ----------------------------------------------------------------------------------


class LLMJoin(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    from_column: _Column = Field(alias="from")
    to_column: _Column = Field(alias="to")


class LLMSQLOutput(BaseModel):
    """The JSON object the model must reply with (``prompts.sql_prompt`` shows it the shape)."""

    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)

    status: Literal["generated", "clarification_required"]
    sql: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=SQL_MAX_LENGTH)] | None = None
    referenced_tables: Annotated[list[_Table], Field(max_length=20)] = []
    referenced_columns: Annotated[list[_Column], Field(max_length=100)] = []
    joins_used: Annotated[list[LLMJoin], Field(max_length=10)] = []
    assumptions: Annotated[list[SentenceText], Field(max_length=10)] = []
    explanation: ParagraphText | None = None
    clarification_question: SentenceText | None = None

    @model_validator(mode="after")
    def _consistent(self) -> "LLMSQLOutput":
        if self.status == "generated" and not self.sql:
            raise ValueError("'sql' is required when status is 'generated'")
        if self.status == "clarification_required":
            if self.sql:
                raise ValueError("'sql' must be null when status is 'clarification_required'")
            if not self.clarification_question:
                raise ValueError("'clarification_question' is required when status is 'clarification_required'")
        return self


# --- the backend's result ---------------------------------------------------------------------------------


class JoinUsed(BaseModel):
    """One equality join condition found in the SQL, matched against registered relationships."""

    model_config = ConfigDict(extra="forbid")

    left: str  # schema.table.column
    right: str  # schema.table.column
    join_type: str  # INNER, LEFT, ...
    registered_relationship: bool


class ValidationIssueView(BaseModel):
    code: str
    message: str
    blocking: bool = False


class SQLGenerationResult(BaseModel):
    """An untrusted candidate query (or the reason there is none). Never an approval."""

    model_config = ConfigDict(extra="forbid")

    generation_id: uuid.UUID
    status: GenerationStatus
    sql: str | None = None
    # False when the user's data permissions do not allow viewing SQL (allow_sql_view).
    sql_visible: bool = False
    dialect: str
    dialect_name: str
    referenced_tables: list[str] = []
    referenced_columns: list[str] = []
    joins_used: list[JoinUsed] = []
    assumptions: list[str] = []
    explanation: str | None = None
    clarification_required: bool = False
    clarification_question: str | None = None
    permission_notes: list[str] = []
    generation_model: str | None = None
    provider: str | None = None
    schema_source_id: uuid.UUID | None
    schema_version: int
    intent_id: uuid.UUID
    validation_status: PreliminaryValidationStatus
    validation_issues: list[ValidationIssueView] = []
    failure_category: FailureCategory | None = None
    generation_attempts: int = 0
    prompt_version: str
    regenerated_from_id: uuid.UUID | None = None
    sql_sha256: str | None = None
    security_status: Literal["awaiting_security_validation"] = SECURITY_STATUS
    trust_level: Literal["untrusted"] = "untrusted"
    is_executable: Literal[False] = False
    latency_ms: int = 0

    @model_validator(mode="after")
    def _sql_matches_status(self) -> "SQLGenerationResult":
        if self.status != GenerationStatus.GENERATED and self.sql is not None:
            raise ValueError("SQL is only present for generated queries")
        if self.status == GenerationStatus.CLARIFICATION_REQUIRED and not self.clarification_question:
            raise ValueError("a clarification result needs its question")
        return self

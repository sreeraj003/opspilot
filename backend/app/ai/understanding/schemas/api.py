"""Request and response contracts for ``/api/v1/ai/understand``."""

import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.ai.understanding.schemas.intent import (
    CLARIFICATION_KEY_PATTERN,
    MAX_QUESTION_CHARS,
    BusinessIntent,
    ClarificationReason,
    MissingInformation,
)

UnderstandingStatus = Literal["understood", "needs_clarification", "access_denied", "rejected"]
ValidationStatus = Literal["passed", "corrected", "rejected", "not_run"]


class UnderstandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=MAX_QUESTION_CHARS)]
    schema_source_id: uuid.UUID
    # Keys of clarifications already asked for this question; none of them is asked again.
    answered_clarifications: Annotated[
        list[Annotated[str, StringConstraints(pattern=CLARIFICATION_KEY_PATTERN)]], Field(max_length=10)
    ] = []


class ClarificationView(BaseModel):
    requires_clarification: bool
    reason: ClarificationReason | None = None
    missing_information: list[MissingInformation] = []
    clarification_question: str | None = None
    # Send back in ``answered_clarifications`` so the same question is not asked twice.
    key: str | None = None


class ValidationIssueView(BaseModel):
    code: str
    message: str
    location: list[str] = []


class ValidationView(BaseModel):
    status: ValidationStatus
    correction_attempts: int = 0
    issues: list[ValidationIssueView] = []


class PermissionView(BaseModel):
    status: Literal["allowed", "denied", "no_data_access"]
    export_allowed: bool = False
    row_level_restrictions: bool = False
    # Generic and safe: never names a restricted table or column.
    reason: str | None = None


class UnderstandingResponse(BaseModel):
    request_id: str | None
    intent_id: uuid.UUID
    status: UnderstandingStatus
    schema_source_id: uuid.UUID
    schema_version: int
    confidence: float | None = None
    intent: BusinessIntent | None = None
    clarification: ClarificationView
    validation: ValidationView
    permission: PermissionView
    explanation: str
    provider: str | None = None
    model: str | None = None
    latency_ms: int


class TableScoreView(BaseModel):
    table: str
    score: int
    reasons: list[str]


class SchemaSelectionView(BaseModel):
    strategy: str
    selected_tables: list[str]
    scores: list[TableScoreView]
    truncated: bool


class MatchedTermView(BaseModel):
    id: uuid.UUID
    name: str
    matched_text: str


class PreviewDiagnostics(BaseModel):
    timezone: str
    timezone_source: str
    reference_time: str
    prompt_version: str
    schema_selection: SchemaSelectionView | None = None
    matched_terms: list[MatchedTermView] = []
    terms_in_context: int = 0
    prompt_chars: int = 0
    llm_attempts: int = 0


class PreviewResponse(UnderstandingResponse):
    diagnostics: PreviewDiagnostics


class UnderstandingSource(BaseModel):
    id: uuid.UUID
    name: str
    database_type: str
    schema_version: int


class UnderstandingOptions(BaseModel):
    timezone: str
    timezone_source: str
    timezone_setting_key: str
    max_question_chars: int
    sources: list[UnderstandingSource]

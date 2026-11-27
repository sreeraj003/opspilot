"""Request and response contracts for ``/api/v1/ai/sql``."""

import uuid

from pydantic import BaseModel, ConfigDict

from app.ai.sql_generation.schemas.result import SQLGenerationResult


class GenerateSQLRequest(BaseModel):
    """Only ids: the backend loads the stored, validated intent; a client-supplied intent is never trusted."""

    model_config = ConfigDict(extra="forbid")

    intent_id: uuid.UUID
    schema_source_id: uuid.UUID


class QuestionSummary(BaseModel):
    """What was asked and how it was understood (the user's own question, shown only to them)."""

    original_question: str
    normalized_question: str
    intent_type: str
    explanation: str
    schema_source_id: uuid.UUID | None
    schema_name: str | None = None
    metrics: list[str] = []
    dimensions: list[str] = []
    filters: list[str] = []
    time_range: str | None = None
    limit: int | None = None
    assumptions: list[str] = []


class SQLGenerationView(SQLGenerationResult):
    question: QuestionSummary | None = None
    regenerations_remaining: int = 0
    created_at: str | None = None

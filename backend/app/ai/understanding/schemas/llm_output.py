"""The structured output contract the model must follow (``LLMInterpretation``).

The model's reply is parsed with ``LLMInterpretation.model_validate_json`` in
strict mode: unknown fields, unknown enum values (for example an unsupported
intent type or operator), wrong types, out-of-range numbers and executable
content in any text field are all rejected here. Passing this model is only the
first step: ``app.ai.understanding.validators.intent_validator`` then checks
every identifier against the registered schema and the business terms, and the
permission engine decides access independently.
"""

from datetime import date
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.ai.understanding.schemas.intent import (
    AggregationType,
    ComparisonKind,
    FilterOperator,
    FilterValue,
    FilterValueType,
    IntentType,
    MissingInformation,
    ParagraphText,
    SentenceText,
    ShortText,
    SortDirection,
    TimeExpressionKind,
    TimeGranularity,
)

# Identifiers as the model must write them; anything else is rejected before lookup.
_TABLE = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,62}\.[A-Za-z_][A-Za-z0-9_]{0,62}$")]
_COLUMN = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")]

_CONFIG = ConfigDict(extra="forbid", strict=True)

MAX_OUTPUT_CHARS = 24_000


class LLMMetric(BaseModel):
    model_config = _CONFIG

    concept: ShortText
    # The exact name of a provided business term, when the metric is one.
    business_term: ShortText | None = None
    table: _TABLE | None = None
    column: _COLUMN | None = None
    aggregation: AggregationType | None = None


class LLMDimension(BaseModel):
    model_config = _CONFIG

    concept: ShortText
    business_term: ShortText | None = None
    table: _TABLE | None = None
    column: _COLUMN | None = None
    time_granularity: TimeGranularity | None = None


class LLMAttribute(BaseModel):
    model_config = _CONFIG

    table: _TABLE
    column: _COLUMN


class LLMFilter(BaseModel):
    model_config = _CONFIG

    table: _TABLE
    column: _COLUMN
    operator: FilterOperator
    value: FilterValue = None
    value_type: FilterValueType


class LLMTime(BaseModel):
    """The time expression the model found. The backend computes the dates."""

    model_config = _CONFIG

    # Copied verbatim from the question; checked against it.
    expression: ShortText
    kind: TimeExpressionKind
    quantity: Annotated[int | None, Field(ge=1, le=1000)] = None
    year: Annotated[int | None, Field(ge=1900, le=2200)] = None
    month: Annotated[int | None, Field(ge=1, le=12)] = None
    quarter: Annotated[int | None, Field(ge=1, le=4)] = None
    # Only for explicit_range: the dates the user wrote (end date inclusive, as people say it).
    start_date: date | None = None
    end_date: date | None = None
    date_table: _TABLE | None = None
    date_column: _COLUMN | None = None

    @model_validator(mode="after")
    def _shape(self) -> "LLMTime":
        kind = self.kind
        rolling = kind.value.startswith("last_n_")
        if rolling and self.quantity is None:
            raise ValueError("a rolling time expression needs a quantity")
        if not rolling and self.quantity is not None:
            raise ValueError("quantity is only allowed for rolling time expressions")
        if kind == TimeExpressionKind.EXPLICIT_RANGE:
            if self.start_date is None or self.end_date is None:
                raise ValueError("an explicit range needs start_date and end_date")
            if self.start_date > self.end_date:
                raise ValueError("start_date must not be after end_date")
        elif self.start_date is not None or self.end_date is not None:
            raise ValueError("start_date and end_date are only allowed for explicit_range")
        if kind == TimeExpressionKind.CALENDAR_MONTH and self.month is None:
            raise ValueError("calendar_month needs a month")
        if kind == TimeExpressionKind.CALENDAR_QUARTER and self.quarter is None:
            raise ValueError("calendar_quarter needs a quarter")
        if kind == TimeExpressionKind.CALENDAR_YEAR and self.year is None:
            raise ValueError("calendar_year needs a year")
        if self.month is not None and kind != TimeExpressionKind.CALENDAR_MONTH:
            raise ValueError("month is only allowed for calendar_month")
        if self.quarter is not None and kind != TimeExpressionKind.CALENDAR_QUARTER:
            raise ValueError("quarter is only allowed for calendar_quarter")
        if self.year is not None and kind not in (
            TimeExpressionKind.CALENDAR_MONTH, TimeExpressionKind.CALENDAR_QUARTER, TimeExpressionKind.CALENDAR_YEAR
        ):
            raise ValueError("year is only allowed for named calendar periods")
        if (self.date_table is None) != (self.date_column is None):
            raise ValueError("date_table and date_column go together")
        return self


class LLMSort(BaseModel):
    model_config = _CONFIG

    # A metric concept from "metrics", or a column name from the provided schema.
    target: ShortText
    direction: SortDirection


class LLMClarification(BaseModel):
    model_config = _CONFIG

    needed: bool
    missing_information: list[MissingInformation] = Field(default_factory=list, max_length=5)
    question: SentenceText | None = None


class LLMInterpretation(BaseModel):
    model_config = _CONFIG

    intent_type: IntentType
    normalized_question: ParagraphText
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    metrics: list[LLMMetric] = Field(default_factory=list, max_length=10)
    dimensions: list[LLMDimension] = Field(default_factory=list, max_length=10)
    attributes: list[LLMAttribute] = Field(default_factory=list, max_length=30)
    filters: list[LLMFilter] = Field(default_factory=list, max_length=20)
    time: LLMTime | None = None
    comparison: ComparisonKind | None = None
    time_granularity: TimeGranularity | None = None
    sorting: list[LLMSort] = Field(default_factory=list, max_length=5)
    limit: Annotated[int | None, Field(ge=1, le=1000)] = None
    # Business concepts in the question that match no provided term, table or column.
    unmapped_concepts: list[ShortText] = Field(default_factory=list, max_length=10)
    ambiguities: list[SentenceText] = Field(default_factory=list, max_length=10)
    assumptions: list[SentenceText] = Field(default_factory=list, max_length=10)
    business_context: ParagraphText | None = None
    clarification: LLMClarification | None = None

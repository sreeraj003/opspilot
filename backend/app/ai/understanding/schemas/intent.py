"""``BusinessIntent``: the structured, validated meaning of a business question.

It says *what* the user asks for (metrics, dimensions, filters, time context,
grouping, sorting, limit) in terms of registered schema identifiers and
administrator-defined business terms. It is data for later modules (SQL
generation, SQL security): it never contains SQL, expressions or code, and it
is never an authorization token.

Every model forbids unknown fields. Identifiers are plain SQL-style names
(letters, digits, underscores), so nothing in an intent can become an
executable fragment later. Free-text fields are checked by
``app.ai.understanding.validators.safety``.
"""

import uuid
from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

from app.ai.understanding.validators.safety import ensure_safe_text

IDENTIFIER_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]{0,62}$"
QUALIFIED_TABLE_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]{0,62}\.[A-Za-z_][A-Za-z0-9_]{0,62}$"
QUALIFIED_COLUMN_PATTERN = (
    r"^[A-Za-z_][A-Za-z0-9_]{0,62}\.[A-Za-z_][A-Za-z0-9_]{0,62}\.[A-Za-z_][A-Za-z0-9_]{0,62}$"
)
CLARIFICATION_KEY_PATTERN = r"^[a-z_]{1,40}(:[a-z0-9_ .\-]{1,100})?$"

MAX_QUESTION_CHARS = 1000

Identifier = Annotated[str, StringConstraints(pattern=IDENTIFIER_PATTERN)]
QualifiedTable = Annotated[str, StringConstraints(pattern=QUALIFIED_TABLE_PATTERN)]
QualifiedColumn = Annotated[str, StringConstraints(pattern=QUALIFIED_COLUMN_PATTERN)]
ShortText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120), AfterValidator(ensure_safe_text)
]
SentenceText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=400), AfterValidator(ensure_safe_text)
]
ParagraphText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=600), AfterValidator(ensure_safe_text)
]
FilterText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200), AfterValidator(ensure_safe_text)
]
FilterValue = FilterText | int | float | bool | list[FilterText | int | float] | None

_STRICT = ConfigDict(extra="forbid")


class IntentType(StrEnum):
    METRIC_LOOKUP = "metric_lookup"
    AGGREGATION = "aggregation"
    COMPARISON = "comparison"
    TREND_ANALYSIS = "trend_analysis"
    RANKING = "ranking"
    FILTERING = "filtering"
    DISTRIBUTION = "distribution"
    DETAIL_LOOKUP = "detail_lookup"
    UNKNOWN = "unknown"


class AggregationType(StrEnum):
    SUM = "sum"
    COUNT = "count"
    COUNT_DISTINCT = "count_distinct"
    AVERAGE = "average"
    MIN = "min"
    MAX = "max"
    NONE = "none"


class FilterOperator(StrEnum):
    """The same vocabulary as Module 04 permission policies."""

    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    IN = "in"
    NOT_IN = "not_in"
    GREATER_THAN = "greater_than"
    GREATER_THAN_OR_EQUAL = "greater_than_or_equal"
    LESS_THAN = "less_than"
    LESS_THAN_OR_EQUAL = "less_than_or_equal"
    BETWEEN = "between"
    IS_NULL = "is_null"
    IS_NOT_NULL = "is_not_null"


class FilterValueType(StrEnum):
    STRING = "string"
    NUMBER = "number"
    BOOLEAN = "boolean"
    DATE = "date"
    DATETIME = "datetime"


class SortDirection(StrEnum):
    ASC = "asc"
    DESC = "desc"


class TimeGranularity(StrEnum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    QUARTER = "quarter"
    YEAR = "year"


class TimeExpressionKind(StrEnum):
    # Calendar periods (whole periods in the business timezone).
    TODAY = "today"
    YESTERDAY = "yesterday"
    THIS_WEEK = "this_week"
    LAST_WEEK = "last_week"
    THIS_MONTH = "this_month"
    LAST_MONTH = "last_month"
    THIS_QUARTER = "this_quarter"
    LAST_QUARTER = "last_quarter"
    THIS_YEAR = "this_year"
    LAST_YEAR = "last_year"
    # Rolling windows ending with today: [tomorrow - N units, tomorrow).
    LAST_N_DAYS = "last_n_days"
    LAST_N_WEEKS = "last_n_weeks"
    LAST_N_MONTHS = "last_n_months"
    LAST_N_YEARS = "last_n_years"
    # Named periods ("March 2025", "Q1 2025", "2024") and explicit dates.
    CALENDAR_MONTH = "calendar_month"
    CALENDAR_QUARTER = "calendar_quarter"
    CALENDAR_YEAR = "calendar_year"
    EXPLICIT_RANGE = "explicit_range"


class ComparisonKind(StrEnum):
    PREVIOUS_PERIOD = "previous_period"
    SAME_PERIOD_LAST_YEAR = "same_period_last_year"


class TermKind(StrEnum):
    METRIC = "metric"
    DIMENSION = "dimension"
    ATTRIBUTE = "attribute"


class MissingInformation(StrEnum):
    METRIC_DEFINITION = "metric_definition"
    TERM_MEANING = "term_meaning"
    COMPARISON_PERIOD = "comparison_period"
    RANKING_METRIC = "ranking_metric"
    METRIC = "metric"
    TIME_RANGE = "time_range"
    SCHEMA_MAPPING = "schema_mapping"
    OTHER = "other"


class ClarificationReason(StrEnum):
    AMBIGUOUS_TERM = "ambiguous_term"
    MISSING_DEFINITION = "missing_definition"
    MISSING_COMPARISON_PERIOD = "missing_comparison_period"
    MISSING_RANKING_METRIC = "missing_ranking_metric"
    UNCLEAR_METRIC = "unclear_metric"
    UNMAPPED_CONCEPT = "unmapped_concept"
    MISSING_TIME_RANGE = "missing_time_range"
    MODEL_REQUESTED = "model_requested"
    UNRESOLVED_AFTER_CLARIFICATION = "unresolved_after_clarification"


# --- intent parts ---------------------------------------------------------------------------------------------


class FieldReference(BaseModel):
    """A registered column: ``table`` is ``schema.table``."""

    model_config = _STRICT

    table: QualifiedTable
    column: Identifier

    @property
    def qualified(self) -> str:
        return f"{self.table}.{self.column}"


class MetricRequest(BaseModel):
    model_config = _STRICT

    name: ShortText
    # Set when the metric is an administrator-defined business term.
    term_id: uuid.UUID | None = None
    term_name: ShortText | None = None
    column: FieldReference | None = None
    aggregation: AggregationType
    definition_source: Literal["business_term", "schema_column"]


class DimensionRequest(BaseModel):
    model_config = _STRICT

    name: ShortText
    term_id: uuid.UUID | None = None
    column: FieldReference
    time_granularity: TimeGranularity | None = None


class AttributeRequest(BaseModel):
    model_config = _STRICT

    column: FieldReference


class IntentFilter(BaseModel):
    """``field <operator> value``. Data only: the value is never an expression."""

    model_config = _STRICT

    field: FieldReference
    operator: FilterOperator
    value: FilterValue = None
    value_type: FilterValueType


class SortSpec(BaseModel):
    model_config = _STRICT

    # A requested metric's name, or a qualified column ``schema.table.column``.
    target: ShortText
    direction: SortDirection


class ResolvedPeriod(BaseModel):
    """A date range resolved by the backend in the business timezone.

    ``start_date`` is inclusive and ``end_date`` is **exclusive** (half-open
    ``[start, end)``); ``start_at``/``end_at`` are the same instants with the
    timezone's offset.
    """

    model_config = _STRICT

    label: str
    start_date: date
    end_date: date
    start_at: datetime
    end_at: datetime
    boundary: Literal["start_inclusive_end_exclusive"] = "start_inclusive_end_exclusive"


class TimeRange(ResolvedPeriod):
    # The time expression exactly as the user wrote it.
    expression: str
    kind: TimeExpressionKind
    quantity: int | None = None
    timezone: str
    # Rolling windows end with today; calendar periods are whole periods.
    is_rolling: bool
    # True when the period includes today (its data may still change).
    is_partial: bool


class ComparisonPeriod(ResolvedPeriod):
    kind: ComparisonKind


class TimeContext(BaseModel):
    model_config = _STRICT

    time_range: TimeRange | None = None
    comparison_period: ComparisonPeriod | None = None
    time_granularity: TimeGranularity | None = None
    # The registered date or timestamp column the period applies to, when known.
    date_column: FieldReference | None = None


class AggregationSpec(BaseModel):
    model_config = _STRICT

    aggregation_type: AggregationType | None = None
    grouping_dimensions: list[ShortText] = Field(default_factory=list, max_length=10)
    sorting: list[SortSpec] = Field(default_factory=list, max_length=5)
    limit: Annotated[int | None, Field(ge=1, le=1000)] = None


class TablePermission(BaseModel):
    """Row-level and operation context for one candidate table, from the permission engine."""

    model_config = _STRICT

    table: QualifiedTable
    operations: list[str]
    # Structured condition tree (Module 04 format) every row must satisfy; enforced in SQL later.
    row_filter: dict[str, Any] | None = None
    allow_export: bool


class PermissionContext(BaseModel):
    """What the backend permission engine decided. Never produced by the LLM."""

    model_config = _STRICT

    status: Literal["allowed", "denied", "no_data_access"]
    policy_revision: int
    tables: list[TablePermission] = Field(default_factory=list)
    export_allowed: bool = False
    row_level_restrictions: bool = False
    # Safe, generic explanation when something is restricted (names no restricted table or column).
    restriction_reason: str | None = None
    is_authorization_token: Literal[False] = False


class BusinessIntent(BaseModel):
    model_config = _STRICT

    # Request information
    intent_id: uuid.UUID
    original_question: str
    normalized_question: str
    intent_type: IntentType
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    requires_clarification: bool
    schema_source_id: uuid.UUID
    schema_version: int

    # Data requirements
    requested_metrics: list[MetricRequest] = Field(default_factory=list)
    requested_dimensions: list[DimensionRequest] = Field(default_factory=list)
    requested_attributes: list[AttributeRequest] = Field(default_factory=list)
    candidate_tables: list[QualifiedTable] = Field(default_factory=list)
    candidate_columns: list[QualifiedColumn] = Field(default_factory=list)

    # Filters, time and aggregation
    filters: list[IntentFilter] = Field(default_factory=list)
    time: TimeContext = Field(default_factory=TimeContext)
    aggregation: AggregationSpec = Field(default_factory=AggregationSpec)

    # Interpretation
    business_context: str | None = None
    assumptions: list[str] = Field(default_factory=list)
    ambiguities: list[str] = Field(default_factory=list)
    clarification_question: str | None = None

    # Backend-decided permission context (row-level policies are preserved for SQL enforcement)
    permission_context: PermissionContext

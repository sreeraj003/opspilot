"""Independent validation of the model's interpretation.

``LLMInterpretation`` has already passed strict Pydantic validation (shape,
enums, ranges, executable content). This module checks the *meaning* against
backend truth, without trusting anything the model says:

* every table and column must be an active registered field of the selected
  schema (``unknown_table`` / ``unknown_column``);
* a field that exists but the user may not query is ``restricted`` (never
  correctable: the answer is a safe denial, not a retry);
* business terms must be active terms of this schema; a term's registered
  aggregation and columns win over the model's guess;
* operators must suit the value type, values must match it, and the value type
  must match the column's registered type;
* aggregations must suit the column (no sum of text);
* the time expression must actually occur in the question, and the dates are
  computed here by ``TimeResolver``, never taken from the model;
* sort targets must be requested metrics or referenced columns.

Problems are returned as structured issues. ``correctable`` issues may be sent
back to the model once (bounded); the rest end the request with a controlled
result.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from app.ai.understanding.normalizers.text import contains_expression
from app.ai.understanding.normalizers.time_resolver import TimeResolutionError, TimeResolver
from app.ai.understanding.schemas.intent import (
    AggregationType,
    AttributeRequest,
    ComparisonPeriod,
    DimensionRequest,
    FieldReference,
    FilterOperator,
    FilterValueType,
    IntentFilter,
    IntentType,
    MetricRequest,
    SortDirection,
    SortSpec,
    TimeContext,
    TimeExpressionKind,
    TimeRange,
)
from app.ai.understanding.schemas.llm_output import LLMInterpretation
from app.ai.understanding.services.permission_view import PermissionView
from app.ai.understanding.services.schema_context import ColumnInfo, SchemaContext, TableInfo
from app.models.business_term import BusinessTerm

_ORDERED_TYPES = {FilterValueType.NUMBER, FilterValueType.DATE, FilterValueType.DATETIME}
_LIST_OPERATORS = {FilterOperator.IN, FilterOperator.NOT_IN}
_NULL_OPERATORS = {FilterOperator.IS_NULL, FilterOperator.IS_NOT_NULL}
_RANGE_OPERATORS = {
    FilterOperator.GREATER_THAN, FilterOperator.GREATER_THAN_OR_EQUAL,
    FilterOperator.LESS_THAN, FilterOperator.LESS_THAN_OR_EQUAL, FilterOperator.BETWEEN,
}
# Registered column category -> value types a filter on it may use.
_COMPATIBLE_TYPES: dict[str, set[FilterValueType]] = {
    "string": {FilterValueType.STRING},
    "number": {FilterValueType.NUMBER},
    "boolean": {FilterValueType.BOOLEAN},
    "date": {FilterValueType.DATE},
    "datetime": {FilterValueType.DATETIME, FilterValueType.DATE},
    "other": set(FilterValueType),
}
_NUMERIC_AGGREGATIONS = {AggregationType.SUM, AggregationType.AVERAGE}
_ORDERED_AGGREGATIONS = {AggregationType.MIN, AggregationType.MAX}


@dataclass(frozen=True, slots=True)
class Issue:
    code: str
    message: str
    location: tuple[str, ...] = ()
    correctable: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "location": list(self.location)}


@dataclass
class ValidatedInterpretation:
    interpretation: LLMInterpretation
    issues: list[Issue] = field(default_factory=list)
    metrics: list[MetricRequest] = field(default_factory=list)
    dimensions: list[DimensionRequest] = field(default_factory=list)
    attributes: list[AttributeRequest] = field(default_factory=list)
    filters: list[IntentFilter] = field(default_factory=list)
    time: TimeContext = field(default_factory=TimeContext)
    sorting: list[SortSpec] = field(default_factory=list)
    unmapped_concepts: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    terms_used: list[BusinessTerm] = field(default_factory=list)
    referenced: dict[str, set[str]] = field(default_factory=dict)  # qualified table -> column names

    @property
    def restricted(self) -> bool:
        return any(i.code == "restricted" for i in self.issues)

    @property
    def correctable_issues(self) -> list[Issue]:
        return [i for i in self.issues if i.correctable]

    @property
    def blocking_issues(self) -> list[Issue]:
        return [i for i in self.issues if not i.correctable]


class InterpretationValidator:
    def __init__(
        self,
        schema: SchemaContext,
        permissions: PermissionView,
        terms: Sequence[BusinessTerm],
        resolver: TimeResolver,
    ) -> None:
        self.schema = schema
        self.permissions = permissions
        self.terms = {t.name.lower(): t for t in terms}
        self.resolver = resolver

    def validate(self, question: str, output: LLMInterpretation) -> ValidatedInterpretation:
        result = ValidatedInterpretation(interpretation=output)
        for index, metric in enumerate(output.metrics):
            self._metric(result, index, metric)
        for index, dimension in enumerate(output.dimensions):
            self._dimension(result, index, dimension)
        for index, attribute in enumerate(output.attributes):
            ref = self._field(result, ("attributes", str(index)), attribute.table, attribute.column)
            if ref is not None:
                result.attributes.append(AttributeRequest(column=ref[0]))
        for index, item in enumerate(output.filters):
            self._filter(result, index, item)
        self._time(result, question, output)
        self._sorting(result, output)
        for concept in output.unmapped_concepts:
            if concept not in result.unmapped_concepts:
                result.unmapped_concepts.append(concept)
        return result

    # --- fields --------------------------------------------------------------------------------------------

    def _field(
        self, result: ValidatedInterpretation, location: tuple[str, ...], table_name: str, column_name: str
    ) -> tuple[FieldReference, ColumnInfo] | None:
        table = self.schema.table(table_name)
        if table is None:
            result.issues.append(Issue("unknown_table", f"Table '{table_name}' is not in the provided context.", location))
            return None
        column = table.column(column_name)
        if column is None:
            result.issues.append(
                Issue("unknown_column", f"Column '{column_name}' is not a column of '{table.qualified}' in the provided context.", location)
            )
            return None
        if not self.permissions.column_allowed(table.qualified, column.name):
            # Generic on purpose: names no restricted field.
            result.issues.append(Issue("restricted", "The question needs data you are not permitted to query.", location, False))
            return None
        result.referenced.setdefault(table.qualified, set()).add(column.name)
        return FieldReference(table=table.qualified, column=column.name), column

    def _term(self, result: ValidatedInterpretation, location: tuple[str, ...], name: str) -> BusinessTerm | None:
        term = self.terms.get(name.lower())
        if term is None:
            result.issues.append(Issue("unknown_business_term", f"'{name}' is not a provided business term.", location))
            return None
        if term not in result.terms_used:
            result.terms_used.append(term)
        return term

    def _term_columns(self, term: BusinessTerm) -> list[tuple[TableInfo, ColumnInfo]]:
        resolved = [self.schema.resolve_column(c) for c in term.related_columns]
        return [r for r in resolved if r is not None]

    # --- metrics and dimensions ----------------------------------------------------------------------------

    def _metric(self, result: ValidatedInterpretation, index: int, metric: Any) -> None:
        location = ("metrics", str(index))
        term = self._term(result, location, metric.business_term) if metric.business_term else None
        if metric.business_term and term is None:
            return
        ref: tuple[FieldReference, ColumnInfo] | None = None
        if metric.table and metric.column:
            ref = self._field(result, location, metric.table, metric.column)
            if ref is None:
                return
            if term is not None and term.related_columns and ref[0].qualified.lower() not in {c.lower() for c in term.related_columns}:
                result.issues.append(
                    Issue("column_not_in_term", f"Column '{ref[0].qualified}' is not associated with the term '{term.name}'.", location)
                )
                return
        elif metric.table or metric.column:
            result.issues.append(Issue("incomplete_field", "A metric needs both table and column, or neither.", location))
            return
        elif term is not None:
            columns = self._term_columns(term)
            if len(columns) == 1:
                table, column = columns[0]
                ref = self._field(result, location, table.qualified, column.name)
                if ref is None:
                    return
            else:
                for table in term.related_tables:
                    if self.schema.table(table) is not None and not self.permissions.table_allowed(table):
                        result.issues.append(Issue("restricted", "The question needs data you are not permitted to query.", location, False))
                        return
        else:
            if metric.concept not in result.unmapped_concepts:
                result.unmapped_concepts.append(metric.concept)
            return

        aggregation = metric.aggregation
        if term is not None and term.default_aggregation:
            registered = AggregationType(term.default_aggregation)
            if aggregation is not None and aggregation != registered:
                result.assumptions.append(f"Used the registered aggregation of '{term.name}' ({registered.value}).")
            aggregation = registered
        if aggregation is None:
            aggregation = AggregationType.COUNT if ref is None else (
                AggregationType.SUM if ref[1].category == "number" and not ref[1].is_primary_key else AggregationType.COUNT
            )
            result.assumptions.append(f"Assumed '{aggregation.value}' for '{metric.concept}'.")
        if ref is not None and not self._aggregation_fits(aggregation, ref[1]):
            result.issues.append(
                Issue("invalid_aggregation", f"'{aggregation.value}' does not suit column '{ref[0].qualified}' ({ref[1].category}).", location)
            )
            return
        result.metrics.append(
            MetricRequest(
                name=metric.concept,
                term_id=term.id if term else None,
                term_name=term.name if term else None,
                column=ref[0] if ref else None,
                aggregation=aggregation,
                definition_source="business_term" if term else "schema_column",
            )
        )

    @staticmethod
    def _aggregation_fits(aggregation: AggregationType, column: ColumnInfo) -> bool:
        if aggregation in _NUMERIC_AGGREGATIONS:
            return column.category in ("number", "other")
        if aggregation in _ORDERED_AGGREGATIONS:
            return column.category != "boolean"
        return True

    def _dimension(self, result: ValidatedInterpretation, index: int, dimension: Any) -> None:
        location = ("dimensions", str(index))
        term = self._term(result, location, dimension.business_term) if dimension.business_term else None
        if dimension.business_term and term is None:
            return
        table_name, column_name = dimension.table, dimension.column
        if not (table_name and column_name) and term is not None:
            columns = self._term_columns(term)
            if columns:
                table_name, column_name = columns[0][0].qualified, columns[0][1].name
        if not (table_name and column_name):
            if dimension.concept not in result.unmapped_concepts:
                result.unmapped_concepts.append(dimension.concept)
            return
        ref = self._field(result, location, table_name, column_name)
        if ref is None:
            return
        if dimension.time_granularity is not None and ref[1].category not in ("date", "datetime", "other"):
            result.issues.append(Issue("invalid_granularity", f"A time granularity needs a date column, not '{ref[0].qualified}'.", location))
            return
        result.dimensions.append(
            DimensionRequest(
                name=dimension.concept, term_id=term.id if term else None, column=ref[0], time_granularity=dimension.time_granularity
            )
        )

    # --- filters --------------------------------------------------------------------------------------------

    def _filter(self, result: ValidatedInterpretation, index: int, item: Any) -> None:
        location = ("filters", str(index))
        ref = self._field(result, location, item.table, item.column)
        if ref is None:
            return
        problem = _filter_problem(item.operator, item.value, item.value_type)
        if problem is None and item.value_type not in _COMPATIBLE_TYPES.get(ref[1].category, set(FilterValueType)):
            problem = f"value type '{item.value_type.value}' does not match column type '{ref[1].category}'"
        if problem is not None:
            result.issues.append(Issue("invalid_filter", f"Filter on '{ref[0].qualified}': {problem}.", location))
            return
        result.filters.append(IntentFilter(field=ref[0], operator=item.operator, value=item.value, value_type=item.value_type))

    # --- time -----------------------------------------------------------------------------------------------

    def _time(self, result: ValidatedInterpretation, question: str, output: LLMInterpretation) -> None:
        granularity = output.time_granularity
        if output.time is None:
            if output.comparison is not None:
                result.issues.append(
                    Issue("comparison_without_period", "A comparison period needs a time expression to compare against.", ("comparison",))
                )
            result.time = TimeContext(time_granularity=granularity)
            return
        time = output.time
        location = ("time",)
        if not contains_expression(time.expression, question):
            result.issues.append(Issue("time_expression_not_in_question", "The time expression must be copied from the question.", location))
            return
        date_ref: FieldReference | None = None
        if time.date_table and time.date_column:
            ref = self._field(result, location, time.date_table, time.date_column)
            if ref is None:
                return
            if ref[1].category not in ("date", "datetime", "other"):
                result.issues.append(Issue("invalid_date_column", f"'{ref[0].qualified}' is not a date or timestamp column.", location))
                return
            date_ref = ref[0]
        try:
            resolved = self.resolver.resolve(
                time.kind, quantity=time.quantity, year=time.year, month=time.month, quarter=time.quarter,
                start_date=time.start_date, end_date=time.end_date,
            )
        except TimeResolutionError as exc:
            result.issues.append(Issue("invalid_time", str(exc), location))
            return
        result.assumptions.extend(resolved.assumptions)
        period = resolved.period
        time_range = TimeRange(
            expression=time.expression,
            kind=time.kind,
            quantity=resolved.quantity,
            timezone=self.resolver.timezone,
            is_rolling=resolved.is_rolling,
            is_partial=resolved.is_partial,
            label=period.label,
            start_date=period.start,
            end_date=period.end,
            start_at=self.resolver.instant(period.start),
            end_at=self.resolver.instant(period.end),
        )
        comparison: ComparisonPeriod | None = None
        if output.comparison is not None:
            other = self.resolver.comparison(resolved, output.comparison)
            comparison = ComparisonPeriod(
                kind=output.comparison,
                label=other.label,
                start_date=other.start,
                end_date=other.end,
                start_at=self.resolver.instant(other.start),
                end_at=self.resolver.instant(other.end),
            )
        result.time = TimeContext(
            time_range=time_range, comparison_period=comparison, time_granularity=granularity, date_column=date_ref
        )

    # --- sorting --------------------------------------------------------------------------------------------

    def _sorting(self, result: ValidatedInterpretation, output: LLMInterpretation) -> None:
        metric_names = {m.name.lower(): m.name for m in result.metrics}
        for index, sort in enumerate(output.sorting):
            target = sort.target.strip()
            if target.lower() in metric_names:
                result.sorting.append(SortSpec(target=metric_names[target.lower()], direction=sort.direction))
                continue
            column = self._sort_column(result, target)
            if column is None:
                result.issues.append(
                    Issue("unknown_sort_target", f"Sort target '{target}' is neither a requested metric nor a referenced column.", ("sorting", str(index)))
                )
                continue
            result.sorting.append(SortSpec(target=column, direction=sort.direction))
        if output.intent_type == IntentType.RANKING and not result.sorting and result.metrics:
            result.sorting.append(SortSpec(target=result.metrics[0].name, direction=SortDirection.DESC))
            result.assumptions.append(f"Ranked by '{result.metrics[0].name}', highest first.")

    @staticmethod
    def _sort_column(result: ValidatedInterpretation, target: str) -> str | None:
        """A referenced column named by ``column``, ``table.column`` or ``schema.table.column``."""
        wanted = target.lower()
        for table, columns in result.referenced.items():
            for column in columns:
                qualified = f"{table}.{column}"
                if wanted in (column.lower(), qualified.lower(), f"{table.split('.')[1]}.{column}".lower()):
                    return qualified
        return None


def _filter_problem(operator: FilterOperator, value: Any, value_type: FilterValueType) -> str | None:
    if operator in _NULL_OPERATORS:
        return None if value is None else f"'{operator.value}' takes no value"
    if value is None:
        return f"'{operator.value}' needs a value"
    if operator in _RANGE_OPERATORS and value_type not in _ORDERED_TYPES:
        return f"'{operator.value}' needs a number, date or datetime"
    if operator in _LIST_OPERATORS or operator == FilterOperator.BETWEEN:
        if not isinstance(value, list):
            return f"'{operator.value}' needs a list of values"
        if operator == FilterOperator.BETWEEN and len(value) != 2:
            return "'between' needs exactly two values"
        if not 1 <= len(value) <= 100:
            return "a list needs 1 to 100 values"
        values = value
    else:
        if isinstance(value, list):
            return f"'{operator.value}' takes a single value"
        values = [value]
    for item in values:
        if not _value_matches(item, value_type):
            return f"a value does not match the value type '{value_type.value}'"
    if operator == FilterOperator.BETWEEN and _comparable(values[0], value_type) > _comparable(values[1], value_type):
        return "the first 'between' value must not exceed the second"
    return None


def _value_matches(value: Any, value_type: FilterValueType) -> bool:
    if value_type == FilterValueType.NUMBER:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if value_type == FilterValueType.BOOLEAN:
        return isinstance(value, bool)
    if value_type == FilterValueType.STRING:
        return isinstance(value, str)
    if not isinstance(value, str):
        return False
    try:
        if value_type == FilterValueType.DATE:
            date.fromisoformat(value)
        else:
            datetime.fromisoformat(value)
    except ValueError:
        return False
    return True


def _comparable(value: Any, value_type: FilterValueType) -> Any:
    if value_type == FilterValueType.DATE:
        return date.fromisoformat(value)
    if value_type == FilterValueType.DATETIME:
        parsed = datetime.fromisoformat(value)
        return parsed.replace(tzinfo=None) if parsed.tzinfo is None else parsed.astimezone().replace(tzinfo=None)
    return value


__all__ = ["InterpretationValidator", "Issue", "TimeExpressionKind", "ValidatedInterpretation"]

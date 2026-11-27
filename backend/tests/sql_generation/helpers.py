"""In-memory intents and contexts for pure SQL generation unit tests (no database, no LLM)."""

import uuid
from datetime import UTC, date, datetime
from typing import Any

from app.ai.sql_generation.context.builder import SQLGenerationContext, build_context
from app.ai.sql_generation.dialects.registry import dialect_for_database_type
from app.ai.understanding.schemas.intent import (
    AggregationSpec,
    AggregationType,
    AttributeRequest,
    BusinessIntent,
    DimensionRequest,
    FieldReference,
    FilterOperator,
    FilterValueType,
    IntentFilter,
    IntentType,
    MetricRequest,
    PermissionContext,
    SortDirection,
    SortSpec,
    TimeContext,
    TimeExpressionKind,
    TimeRange,
)
from app.ai.understanding.services.permission_view import PermissionView
from app.ai.understanding.services.schema_context import SchemaContext
from app.models.business_term import BusinessTerm
from tests.understanding.helpers import ai_context, grant, sales_schema, term


def ref(qualified: str) -> FieldReference:
    table, column = qualified.rsplit(".", 1)
    return FieldReference(table=table, column=column)


def revenue_metric(aggregation: AggregationType = AggregationType.SUM) -> MetricRequest:
    return MetricRequest(name="revenue", column=ref("sales.orders.total_amount"), aggregation=aggregation,
                         definition_source="schema_column")


def last_month() -> TimeContext:
    return TimeContext(
        time_range=TimeRange(
            label="Last month", start_date=date(2026, 2, 1), end_date=date(2026, 3, 1),
            start_at=datetime(2026, 2, 1, tzinfo=UTC), end_at=datetime(2026, 3, 1, tzinfo=UTC),
            expression="last month", kind=TimeExpressionKind.LAST_MONTH, timezone="UTC", is_rolling=False,
            is_partial=False,
        ),
        date_column=ref("sales.orders.placed_at"),
    )


def make_intent(**overrides: Any) -> BusinessIntent:
    values: dict[str, Any] = {
        "intent_id": uuid.uuid4(),
        "original_question": "What is total revenue?",
        "normalized_question": "What is the total revenue?",
        "intent_type": IntentType.METRIC_LOOKUP,
        "confidence": 0.9,
        "requires_clarification": False,
        "schema_source_id": uuid.UUID(int=1),
        "schema_version": 3,
        "requested_metrics": [revenue_metric()],
        "candidate_tables": ["sales.orders"],
        "permission_context": PermissionContext(status="allowed", policy_revision=7),
    }
    values.update(overrides)
    return BusinessIntent(**values)


def ranking_intent(limit: int = 5) -> BusinessIntent:
    return make_intent(
        intent_type=IntentType.RANKING,
        requested_dimensions=[DimensionRequest(name="region", column=ref("sales.customers.region"))],
        candidate_tables=["sales.orders", "sales.customers"],
        aggregation=AggregationSpec(
            aggregation_type=AggregationType.SUM, grouping_dimensions=["region"],
            sorting=[SortSpec(target="revenue", direction=SortDirection.DESC)], limit=limit,
        ),
    )


def filtered_intent() -> BusinessIntent:
    return make_intent(
        intent_type=IntentType.AGGREGATION,
        filters=[IntentFilter(field=ref("sales.orders.status"), operator=FilterOperator.EQUALS, value="paid",
                              value_type=FilterValueType.STRING)],
        time=last_month(),
    )


def detail_intent() -> BusinessIntent:
    return make_intent(
        intent_type=IntentType.DETAIL_LOOKUP, requested_metrics=[],
        requested_attributes=[AttributeRequest(column=ref("sales.customers.name")),
                              AttributeRequest(column=ref("sales.customers.region"))],
        candidate_tables=["sales.customers"],
    )


def schema_for(database_type: str = "postgresql") -> SchemaContext:
    base = sales_schema()
    return SchemaContext(
        source_id=base.source_id, source_name=base.source_name, schema_version=base.schema_version,
        database_type=database_type, dialect=database_type, is_active=True, tables=base.tables,
        relationships=base.relationships,
    )


def permissions(schema: SchemaContext, *, row_filter: dict[str, Any] | None = None, sql_view: bool = False) -> PermissionView:
    customers = grant("customers", denied=["customers.email"], row_filter=row_filter)
    entries = [grant("orders"), customers, grant("products")]
    for entry in entries:
        entry["allow_sql_view"] = sql_view
    return PermissionView(ai_context(*entries), schema)


def context(
    intent: BusinessIntent | None = None,
    database_type: str = "postgresql",
    *,
    terms: list[BusinessTerm] | None = None,
    perms: PermissionView | None = None,
    max_tables: int = 8,
    max_row_limit: int = 1000,
) -> SQLGenerationContext:
    schema = schema_for(database_type)
    return build_context(
        intent or make_intent(), schema, perms or permissions(schema), terms or [],
        dialect_for_database_type(database_type), max_tables=max_tables, max_row_limit=max_row_limit,
    )


__all__ = ["term"]

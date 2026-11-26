"""In-memory schema, permissions and terms for pure unit tests (no database)."""

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from app.ai.understanding.normalizers.time_resolver import TimeResolver
from app.ai.understanding.services.permission_view import PermissionView
from app.ai.understanding.services.schema_context import ColumnInfo, RelationshipInfo, SchemaContext, TableInfo
from app.models.business_term import BusinessTerm

NOW = datetime(2026, 3, 15, 12, tzinfo=UTC)


def column(name: str, data_type: str = "integer", category: str = "number", **flags: Any) -> ColumnInfo:
    return ColumnInfo(
        name=name, data_type=data_type, category=category, nullable=flags.get("nullable", True),
        description=flags.get("description"), business_meaning=flags.get("business_meaning"),
        is_sensitive=flags.get("sensitive", False), is_primary_key=flags.get("pk", False), is_foreign_key=flags.get("fk", False),
    )


def table(schema: str, name: str, columns: list[ColumnInfo], description: str | None = None) -> TableInfo:
    return TableInfo(schema, name, description, None, {c.name.lower(): c for c in columns})


def sales_schema(extra_tables: int = 0) -> SchemaContext:
    tables = [
        table("sales", "orders", [
            column("id", pk=True), column("customer_id", fk=True), column("product_id", fk=True),
            column("total_amount", "decimal(12,2)", description="Order value", business_meaning="Gross order value"),
            column("status", "varchar(20)", "string"),
            column("placed_at", "timestamp with time zone", "datetime"),
        ], "Customer orders"),
        table("sales", "customers", [column("id", pk=True), column("name", "varchar(200)", "string"),
                                     column("region", "varchar(50)", "string"), column("email", "varchar(320)", "string")]),
        table("sales", "products", [column("id", pk=True), column("name", "varchar(200)", "string"),
                                    column("category", "varchar(50)", "string")], "Products we sell"),
        table("hr", "payroll", [column("id", pk=True), column("salary", "decimal(12,2)")], "Employee salaries"),
    ]
    for index in range(extra_tables):
        tables.append(table("misc", f"archive_{index}", [column("id", pk=True), column("payload", "text", "string")]))
    relationships = (
        RelationshipInfo("sales.orders", "customer_id", "sales.customers", "id", "many_to_one", None),
        RelationshipInfo("sales.orders", "product_id", "sales.products", "id", "many_to_one", None),
    )
    return SchemaContext(
        source_id=uuid.UUID(int=1), source_name="Sales DB", schema_version=3, database_type="postgresql",
        dialect="postgresql", is_active=True, tables={t.qualified.lower(): t for t in tables}, relationships=relationships,
    )


def ai_context(*entries: dict[str, Any], revision: int = 7) -> dict[str, Any]:
    return {"policy_revision": revision, "data_access": [{"action": "view", "tables": list(entries)}] if entries else []}


def grant(table_name: str, columns: list[str] | None = None, *, denied: list[str] | None = None,
          row_filter: dict[str, Any] | None = None, operations: list[str] | None = None, export: bool = False) -> dict[str, Any]:
    return {
        "table": table_name,
        "columns": columns or [f"{table_name}.*"],
        "denied_columns": denied or [],
        "operations": operations or ["aggregate", "read"],
        "row_filter": row_filter,
        "allow_export": export,
        "allow_sql_view": False,
    }


def sales_permissions(schema: SchemaContext | None = None, **kwargs: Any) -> PermissionView:
    return PermissionView(
        ai_context(grant("orders", **kwargs), grant("customers", denied=["customers.email"]), grant("products")),
        schema or sales_schema(),
    )


def term(name: str, *, synonyms: list[str] | None = None, columns: list[str] | None = None,
         tables: list[str] | None = None, aggregation: str | None = "sum", kind: str = "metric") -> BusinessTerm:
    columns = columns or []
    return BusinessTerm(
        id=uuid.uuid4(), schema_source_id=uuid.UUID(int=1), name=name, description=f"{name} definition", kind=kind,
        synonyms=synonyms or [], related_columns=columns,
        related_tables=tables or sorted({c.rsplit(".", 1)[0] for c in columns}),
        default_aggregation=aggregation, aggregation_guidance=None, is_active=True,
    )


def resolver(tz: str = "UTC") -> TimeResolver:
    return TimeResolver(tz, NOW)


def interpretation(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "intent_type": "metric_lookup",
        "normalized_question": "What is the total revenue?",
        "confidence": 0.9,
        "metrics": [],
        "dimensions": [],
        "attributes": [],
        "filters": [],
        "time": None,
        "comparison": None,
        "time_granularity": None,
        "sorting": [],
        "limit": None,
        "unmapped_concepts": [],
        "ambiguities": [],
        "assumptions": [],
        "business_context": None,
        "clarification": {"needed": False, "missing_information": [], "question": None},
    }
    base.update(overrides)
    return base


def as_json(**overrides: Any) -> str:
    return json.dumps(interpretation(**overrides))

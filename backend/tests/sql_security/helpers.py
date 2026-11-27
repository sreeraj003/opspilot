"""In-memory guard runs for pure unit tests (no database, no network, no LLM)."""

import asyncio
from typing import Any

from app.ai.sql_generation.dialects.registry import MYSQL, POSTGRESQL, SQL_SERVER, SQLDialect
from app.ai.sql_security.policies.limits import GuardLimits, GuardOptions
from app.ai.sql_security.services.inspector import AccessDecision, InspectionOutcome, SQLSecurityInspector
from tests.understanding.helpers import sales_schema

DIALECTS: dict[str, SQLDialect] = {"postgres": POSTGRESQL, "mysql": MYSQL, "tsql": SQL_SERVER}

REGION_SOUTH = {"column": "customers.region", "operator": "equals", "value": "South", "value_type": "string"}
NOT_DELETED = {"column": "orders.status", "operator": "not_equals", "value": "deleted", "value_type": "string"}


class FakeAuthorizer:
    """Stands in for the permission engine: allowed tables/columns, row filters, and what was asked."""

    def __init__(self, *, filters: dict[str, Any] | None = None, denied_tables: set[str] | None = None,
                 denied_columns: set[str] | None = None, operations: set[str] | None = None) -> None:
        self.filters = {k.lower(): v for k, v in (filters or {}).items()}
        self.denied_tables = denied_tables or set()
        self.denied_columns = denied_columns or set()
        self.operations = operations or {"read", "aggregate"}
        self.calls: list[tuple[dict[str, set[str]], str]] = []

    async def __call__(self, referenced: dict[str, set[str]], operation: str) -> AccessDecision:
        self.calls.append((referenced, operation))
        if operation not in self.operations:
            return AccessDecision(False, 1, internal_reason=f"operation {operation}")
        for table, columns in referenced.items():
            if table in self.denied_tables:
                return AccessDecision(False, 1, internal_reason=f"table {table}")
            for column in columns:
                if f"{table}.{column}" in self.denied_columns:
                    return AccessDecision(False, 1, internal_reason=f"column {table}.{column}")
        filters = {t.lower(): f for t, f in self.filters.items() if t in {r.lower() for r in referenced}}
        return AccessDecision(True, 1, "a" * 64, filters, True)


def inspect_sql(sql: str, *, dialect: str = "postgres", authorizer: FakeAuthorizer | None = None,
                limits: GuardLimits | None = None, **options: Any) -> InspectionOutcome:
    opts = GuardOptions(limits=limits or GuardLimits(), **options)
    inspector = SQLSecurityInspector(sales_schema(), DIALECTS[dialect], opts)
    return asyncio.run(inspector.inspect(sql, authorizer or FakeAuthorizer()))


def codes(outcome: InspectionOutcome) -> set[str]:
    return {v.code for v in outcome.violations}

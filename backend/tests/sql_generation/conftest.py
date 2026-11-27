"""Fixtures for Module 10 API tests: the real app, PostgreSQL, permission engine, schema registry and Module 09.

The LLM is never called for real. ``FakeProvider`` answers through
``httpx.MockTransport``: first with the JSON interpretation Module 09 needs
(so a real, stored ``BusinessIntent`` exists), then with the SQL generation
replies a test wants, so the real ``LLMService``, parsing, SQLGlot validation,
regeneration and permission checks all run.
"""

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx import Response

from tests.ai.conftest import API_KEY, MODEL, FakeProvider, completion, provider_api  # noqa: F401  (fixtures)
from tests.auth.conftest import Api, api_factory  # noqa: F401  (fixtures)
from tests.understanding.conftest import SALES_POLICY, SCHEMA_DOCUMENT, Understanding, metric

pytestmark = pytest.mark.postgres

VIEW_SQL_POLICY: dict[str, Any] = {**SALES_POLICY, "allow_sql_view": True}

RANKING_INTERPRETATION: dict[str, Any] = {
    "intent_type": "ranking",
    "normalized_question": "Which regions have the highest revenue?",
    "metrics": [metric("revenue", table="sales.orders", column="total_amount", aggregation="sum")],
    "dimensions": [{"concept": "region", "business_term": None, "table": "sales.customers", "column": "region",
                    "time_granularity": None}],
    "sorting": [{"target": "revenue", "direction": "desc"}],
    "limit": 5,
}

RANKING_SQL = {
    "postgresql": "SELECT c.region, SUM(o.total_amount) AS revenue FROM sales.orders AS o "
                  "JOIN sales.customers AS c ON o.customer_id = c.id WHERE c.region = 'Kerala' "
                  "GROUP BY c.region ORDER BY revenue DESC LIMIT 5",
    "mysql": "SELECT c.region, SUM(o.total_amount) AS revenue FROM sales.orders AS o "
             "JOIN sales.customers AS c ON o.customer_id = c.id WHERE c.region = 'Kerala' "
             "GROUP BY c.region ORDER BY revenue DESC LIMIT 5",
    "sqlserver": "SELECT TOP (5) c.region, SUM(o.total_amount) AS revenue FROM sales.orders AS o "
                 "JOIN sales.customers AS c ON o.customer_id = c.id WHERE c.region = N'Kerala' "
                 "GROUP BY c.region ORDER BY revenue DESC",
}


def sql_reply(sql: str | None = RANKING_SQL["postgresql"], **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "status": "generated",
        "sql": sql,
        "referenced_tables": ["sales.orders", "sales.customers"],
        "referenced_columns": ["sales.orders.total_amount", "sales.customers.region"],
        "joins_used": [{"from": "sales.orders.customer_id", "to": "sales.customers.id"}],
        "assumptions": ["Revenue is the sum of order totals."],
        "explanation": "Sums order totals per region and keeps the five largest.",
        "clarification_question": None,
    }
    body.update(overrides)
    return completion(json.dumps(body))


class SQLGeneration(Understanding):
    def __init__(self, api: Api, provider: FakeProvider, database_type: str = "postgresql") -> None:
        super().__init__(api, provider)
        if database_type != "postgresql":
            schema = self.create_schema(f"{database_type} DB", database_type=database_type)
            self.schema_id = schema["id"]
            self.ok(self.import_document(self.schema_id, SCHEMA_DOCUMENT))

    def generator(self, *, policy: dict[str, Any] | None = SALES_POLICY, extra: tuple[str, ...] = ()) -> dict[str, str]:
        return self.analyst("ai.question.understand", "ai.sql.generate", *extra, policy=policy)

    def intent(self, headers: dict[str, str], question: str = "Which regions have the highest revenue?",
               **interpretation: Any) -> str:
        self.model_says(**{**RANKING_INTERPRETATION, **interpretation})
        body = self.ok(self.understand(question, headers))
        assert body["status"] == "understood", body
        return str(body["intent_id"])

    def model_sql(self, sql: str | None = RANKING_SQL["postgresql"], **overrides: Any) -> "SQLGeneration":
        self._fresh_queue()
        self.provider.reply(200, sql_reply(sql, **overrides))
        return self

    def model_sql_raw(self, content: str) -> "SQLGeneration":
        self._fresh_queue()
        self.provider.reply(200, completion(content))
        return self

    def generate(self, intent_id: str, headers: dict[str, str], schema_id: str | None = None, **extra: Any) -> Response:
        return self.post("/ai/sql/generate", {"intent_id": intent_id, "schema_source_id": schema_id or self.schema_id,
                                              **extra}, headers=headers)

    def regenerate(self, generation_id: str, headers: dict[str, str]) -> Response:
        return self.post(f"/ai/sql/generations/{generation_id}/regenerate", {}, headers=headers)

    def records(self) -> list[Any]:
        return self.api.sql("SELECT * FROM sql_generations ORDER BY created_at")

    def sql_events(self) -> list[Any]:
        return self.api.sql("SELECT * FROM sql_generation_events ORDER BY created_at")

    def sql_prompts(self) -> list[str]:
        """Prompts of the SQL generation requests (system prompt starts with the SQL component)."""
        prompts = []
        for request in self.provider.requests:
            if not request.url.path.endswith("/chat/completions"):
                continue
            body = json.loads(request.content)
            text = "\n".join(m["content"] for m in body["messages"])
            if "SQL generation component" in text:
                prompts.append(text)
        return prompts


@pytest.fixture
def sql_factory(api_factory: Callable[..., Api], provider_api: FakeProvider) -> Callable[..., SQLGeneration]:  # noqa: F811
    def factory(database_type: str = "postgresql", **overrides: Any) -> SQLGeneration:
        def prepare(app: FastAPI) -> None:
            app.state.ai_transport = httpx.MockTransport(provider_api)

        values = {
            "nvidia_api_key": API_KEY, "nvidia_model": MODEL, "ai_max_retries": 0, "ai_retry_max_wait_seconds": 0,
            **overrides,
        }
        return SQLGeneration(api_factory(prepare=prepare, **values), provider_api, database_type)

    return factory


@pytest.fixture
def g(sql_factory: Callable[..., SQLGeneration]) -> SQLGeneration:
    return sql_factory()

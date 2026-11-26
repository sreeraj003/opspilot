"""Fixtures for Module 09 API tests: the real app, PostgreSQL, permission engine and schema registry.

The LLM is never called for real. ``FakeProvider`` (Module 08 tests) answers
through ``httpx.MockTransport`` with queued chat completions whose content is
the JSON interpretation a test wants the model to return, so the real
``LLMService``, adapter, parsing, validation and permission checks all run.
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
from tests.schema_registry.conftest import Registry
from tests.understanding.helpers import interpretation

pytestmark = pytest.mark.postgres

SCHEMA_DOCUMENT: dict[str, Any] = {
    "tables": [
        {
            "schema_name": "sales",
            "table_name": "customers",
            "description": "People and companies that buy from us",
            "columns": [
                {"column_name": "id", "data_type": "integer", "is_primary_key": True, "is_nullable": False},
                {"column_name": "name", "data_type": "varchar(200)"},
                {"column_name": "region", "data_type": "varchar(50)", "description": "Sales region"},
                {"column_name": "email", "data_type": "varchar(320)"},
            ],
        },
        {
            "schema_name": "sales",
            "table_name": "products",
            "description": "Products we sell",
            "columns": [
                {"column_name": "id", "data_type": "integer", "is_primary_key": True, "is_nullable": False},
                {"column_name": "name", "data_type": "varchar(200)"},
                {"column_name": "category", "data_type": "varchar(50)"},
            ],
        },
        {
            "schema_name": "sales",
            "table_name": "orders",
            "description": "Customer orders",
            "columns": [
                {"column_name": "id", "data_type": "integer", "is_primary_key": True, "is_nullable": False},
                {"column_name": "customer_id", "data_type": "integer", "is_foreign_key": True},
                {"column_name": "product_id", "data_type": "integer", "is_foreign_key": True},
                {"column_name": "total_amount", "data_type": "decimal(12,2)", "business_meaning": "Order value incl. tax"},
                {"column_name": "status", "data_type": "varchar(20)"},
                {"column_name": "placed_at", "data_type": "timestamp with time zone"},
            ],
        },
        {
            "schema_name": "hr",
            "table_name": "payroll",
            "description": "Employee salaries",
            "columns": [
                {"column_name": "id", "data_type": "integer", "is_primary_key": True, "is_nullable": False},
                {"column_name": "salary", "data_type": "decimal(12,2)"},
            ],
        },
    ],
    "relationships": [
        {"source_schema": "sales", "source_table": "orders", "source_column": "customer_id", "target_schema": "sales",
         "target_table": "customers", "target_column": "id", "relationship_type": "many_to_one"},
        {"source_schema": "sales", "source_table": "orders", "source_column": "product_id", "target_schema": "sales",
         "target_table": "products", "target_column": "id", "relationship_type": "many_to_one"},
    ],
}

SALES_POLICY: dict[str, Any] = {
    "allowed_tables": ["orders", "customers", "products"],
    "allowed_columns": ["orders.*", "customers.id", "customers.name", "customers.region", "products.*"],
    "allowed_operations": ["aggregate", "read"],
    "row_filters": [{"column": "customers.region", "operator": "equals", "value": "Kerala"}],
    "allow_export": False,
    "allow_sql_view": False,
}


def llm_reply(**overrides: Any) -> dict[str, Any]:
    return completion(json.dumps(interpretation(**overrides)))


def metric(concept: str, term: str | None = None, table: str | None = None, column: str | None = None,
           aggregation: str | None = None) -> dict[str, Any]:
    return {"concept": concept, "business_term": term, "table": table, "column": column, "aggregation": aggregation}


class Understanding(Registry):
    def __init__(self, api: Api, provider: FakeProvider) -> None:
        super().__init__(api)
        self.provider = provider
        self._requests_seen = len(provider.requests)
        schema = self.create_schema("Sales DB")
        self.schema_id: str = schema["id"]
        self.ok(self.import_document(self.schema_id, SCHEMA_DOCUMENT))

    # --- model replies --------------------------------------------------------------------------------

    def _fresh_queue(self) -> None:
        # FakeProvider repeats its last reply; replies queued after a request start a new script.
        if len(self.provider.requests) > self._requests_seen:
            self.provider.queue.clear()
        self._requests_seen = len(self.provider.requests)

    def model_says(self, **overrides: Any) -> "Understanding":
        self._fresh_queue()
        self.provider.reply(200, llm_reply(**overrides))
        return self

    def model_raw(self, content: str) -> "Understanding":
        self._fresh_queue()
        self.provider.reply(200, completion(content))
        return self

    def prompt(self, index: int = -1) -> str:
        """Everything sent to the model in one request (system + user messages)."""
        body = json.loads(self.provider.requests[index].content)
        return "\n".join(m["content"] for m in body["messages"])

    # --- users and terms ---------------------------------------------------------------------------------

    def analyst(self, *system_keys: str, policy: dict[str, Any] | None = SALES_POLICY, key: str = "sales.view") -> dict[str, str]:
        user_id, headers = self.user("analyst")
        permission_ids = [self.system_permission_id(k) for k in (system_keys or ("ai.question.understand",))]
        if policy is not None:
            permission_ids.append(self.create_permission(f"{key}.{self._users}", policy))
        self.assign_role(user_id, self.role_with(f"analyst_{self._users}", *permission_ids))
        return headers

    def create_term(self, name: str, *, headers: dict[str, str] | None = None, **fields: Any) -> dict[str, Any]:
        body = {"schema_source_id": self.schema_id, "name": name, "description": f"{name} as defined by finance", **fields}
        return self.ok(self.post("/business-terms", body, headers=headers), 201)

    def understand(self, question: str, headers: dict[str, str], *, path: str = "/ai/understand", **extra: Any) -> Response:
        return self.post(path, {"question": question, "schema_source_id": self.schema_id, **extra}, headers=headers)

    def events(self) -> list[Any]:
        return self.api.sql("SELECT * FROM question_understanding_events ORDER BY created_at")


@pytest.fixture
def understanding_factory(api_factory: Callable[..., Api], provider_api: FakeProvider) -> Callable[..., Understanding]:  # noqa: F811
    def factory(**overrides: Any) -> Understanding:
        def prepare(app: FastAPI) -> None:
            app.state.ai_transport = httpx.MockTransport(provider_api)

        values = {
            "nvidia_api_key": API_KEY, "nvidia_model": MODEL, "ai_max_retries": 0, "ai_retry_max_wait_seconds": 0,
            **overrides,
        }
        return Understanding(api_factory(prepare=prepare, **values), provider_api)

    return factory


@pytest.fixture
def u(understanding_factory: Callable[..., Understanding]) -> Understanding:
    return understanding_factory()

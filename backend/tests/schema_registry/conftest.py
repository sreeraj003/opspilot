"""Fixtures for Module 06 tests: the real app, PostgreSQL, login and permission engine.

``Registry`` builds on the Module 04 ``Rbac`` harness and adds helpers that call
the schema registry API as the installation administrator (or any user).
"""

import copy
from typing import Any

import pytest
from httpx import Response

from tests.auth.conftest import Api, api, api_factory  # noqa: F401  (fixtures)
from tests.rbac.conftest import Rbac

pytestmark = pytest.mark.postgres

ORDERS_DOCUMENT: dict[str, Any] = {
    "schema_name": "sales",
    "tables": [
        {
            "table_name": "customers",
            "description": "People and companies that buy from us",
            "columns": [
                {"column_name": "id", "data_type": "integer", "is_primary_key": True, "description": "Customer id"},
                {"column_name": "name", "data_type": "varchar(200)", "is_nullable": False},
                {"column_name": "email", "data_type": "varchar(320)", "is_sensitive": True},
            ],
        },
        {
            "table_name": "orders",
            "description": "Customer orders",
            "business_purpose": "One row per order placed",
            "columns": [
                {"column_name": "id", "data_type": "integer", "is_primary_key": True, "is_nullable": False,
                 "description": "Order identifier"},
                {"column_name": "customer_id", "data_type": "integer", "is_foreign_key": True, "is_nullable": False},
                {"column_name": "total_amount", "data_type": "decimal(12,2)", "is_nullable": False,
                 "description": "Total order value", "business_meaning": "Gross order value incl. tax"},
                {"column_name": "placed_at", "data_type": "timestamp with time zone"},
            ],
        },
    ],
    "relationships": [
        {"source_table": "orders", "source_column": "customer_id", "target_table": "customers", "target_column": "id",
         "relationship_type": "many_to_one", "description": "Who placed the order"},
    ],
    "business_glossary": [{"term": "GMV", "definition": "Gross merchandise value: sum of total_amount", "synonyms": ["gross sales"]}],
}


def orders_document() -> dict[str, Any]:
    return copy.deepcopy(ORDERS_DOCUMENT)


class Registry(Rbac):
    def create_schema(self, name: str = "Sales DB", headers: dict[str, str] | None = None, **extra: Any) -> dict[str, Any]:
        body = {"name": name, "database_type": "postgresql", **extra}
        return self.ok(self.post("/schemas", body, headers=headers), 201)

    def create_table(self, schema_id: str, table_name: str, columns: list[dict[str, Any]] | None = None,
                     schema_name: str = "public", **extra: Any) -> dict[str, Any]:
        body = {"schema_name": schema_name, "table_name": table_name, "columns": columns or [], **extra}
        return self.ok(self.post(f"/schemas/{schema_id}/tables", body), 201)

    def create_column(self, table_id: str, column_name: str, data_type: str = "integer", **extra: Any) -> dict[str, Any]:
        return self.ok(self.post(f"/tables/{table_id}/columns", {"column_name": column_name, "data_type": data_type, **extra}), 201)

    def import_document(self, schema_id: str, document: dict[str, Any], *, strategy: str = "create_only",
                        dry_run: bool = False, headers: dict[str, str] | None = None, **extra: Any) -> Response:
        body = {"strategy": strategy, "dry_run": dry_run, "document": document, **extra}
        return self.post(f"/schemas/{schema_id}/import", body, headers=headers)

    def imported_schema(self, name: str = "Sales DB") -> dict[str, Any]:
        schema = self.create_schema(name)
        self.ok(self.import_document(schema["id"], orders_document()))
        return self.ok(self.get(f"/schemas/{schema['id']}"))

    def version(self, schema_id: str) -> int:
        return self.ok(self.get(f"/schemas/{schema_id}/version"))["schema_version"]

    def outline(self, schema_id: str) -> dict[str, dict[str, Any]]:
        """``{"sales.orders": {"id": ..., "columns": {"id": {...}}}}``"""
        data = self.ok(self.get(f"/schemas/{schema_id}/outline"))
        return {
            f"{t['schema_name']}.{t['table_name']}": {**t, "columns": {c["column_name"]: c for c in t["columns"]}}
            for t in data["tables"]
        }

    def history(self, schema_id: str) -> list[dict[str, Any]]:
        return self.ok(self.get(f"/schemas/{schema_id}/history?limit=100"))["items"]

    def user_with(self, *permission_keys: str, name: str = "member") -> dict[str, str]:
        """A user holding exactly these system permissions through a custom role."""
        user_id, headers = self.user(name)
        if permission_keys:
            ids = [self.system_permission_id(key) for key in permission_keys]
            role_id = self.role_with(f"role_{name}_{self._users}", *ids)
            self.assign_role(user_id, role_id)
        return headers


@pytest.fixture
def registry(api: Api) -> Registry:  # noqa: F811
    return Registry(api)

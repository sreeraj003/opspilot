"""AI-ready representation, the policy SchemaCatalog, versioning and the audit trail."""

from typing import Any

import pytest

from tests.schema_registry.conftest import Registry, orders_document

pytestmark = pytest.mark.postgres


def _representation(registry: Registry, schema_id: str, query: str = "") -> dict[str, Any]:
    return registry.ok(registry.get(f"/schemas/{schema_id}/representation{query}"))


def test_representation_is_structured_and_excludes_internal_data(registry: Registry) -> None:
    schema_id = registry.imported_schema()["id"]
    data = _representation(registry, schema_id)

    assert data["schema_version"] == 2
    assert data["database_type"] == "postgresql" and data["schema_source"] == "Sales DB"
    tables = {t["table"]: t for t in data["tables"]}
    assert set(tables) == {"customers", "orders"}
    orders = tables["orders"]
    assert orders["schema"] == "sales" and orders["description"] == "Customer orders"
    columns = {c["name"]: c for c in orders["columns"]}
    assert columns["id"] == {"name": "id", "type": "integer", "nullable": False, "description": "Order identifier",
                             "primary_key": True}
    assert columns["total_amount"]["business_meaning"] == "Gross order value incl. tax"
    assert columns["customer_id"]["foreign_key"] is True
    assert orders["relationships"] == [{
        "type": "many_to_one", "column": "customer_id",
        "references": {"schema": "sales", "table": "customers", "column": "id"}, "description": "Who placed the order",
    }]
    assert data["relationships"][0]["source"] == {"schema": "sales", "table": "orders", "column": "customer_id"}
    assert data["business_terms"] == [
        {"term": "GMV", "definition": "Gross merchandise value: sum of total_amount", "synonyms": ["gross sales"]}
    ]
    # Sensitive columns are left out by default; no ids, timestamps or users anywhere.
    assert "email" not in {c["name"] for c in tables["customers"]["columns"]}
    text = str(data)
    for internal in ("created_at", "updated_at", "created_by", "table_id", "is_active", "deleted_at"):
        assert internal not in text
    assert str(registry.admin_id) not in text

    with_sensitive = _representation(registry, schema_id, "?include_sensitive=true")
    customers = next(t for t in with_sensitive["tables"] if t["table"] == "customers")
    assert next(c for c in customers["columns"] if c["name"] == "email")["sensitive"] is True


def test_representation_excludes_inactive_metadata(registry: Registry) -> None:
    schema_id = registry.imported_schema()["id"]
    outline = registry.outline(schema_id)
    registry.ok(registry.patch(f"/columns/{outline['sales.orders']['columns']['placed_at']['id']}", {"is_active": False}))
    registry.ok(registry.patch(f"/tables/{outline['sales.customers']['id']}", {"is_active": False}))

    data = _representation(registry, schema_id)
    assert [t["table"] for t in data["tables"]] == ["orders"]
    orders = data["tables"][0]
    assert "placed_at" not in {c["name"] for c in orders["columns"]}
    assert orders["relationships"] == [] and data["relationships"] == []  # its other end is inactive

    relationship_id = registry.ok(registry.get(f"/schemas/{schema_id}/relationships"))["items"][0]["id"]
    registry.ok(registry.patch(f"/tables/{outline['sales.customers']['id']}", {"is_active": True}))
    assert len(_representation(registry, schema_id)["relationships"]) == 1
    registry.ok(registry.patch(f"/relationships/{relationship_id}", {"is_active": False}))
    assert _representation(registry, schema_id)["relationships"] == []


def test_representation_filters(registry: Registry) -> None:
    schema_id = registry.imported_schema()["id"]
    registry.create_table(schema_id, "ledger", schema_name="finance",
                          columns=[{"column_name": "id", "data_type": "integer", "is_primary_key": True}])

    only_orders = _representation(registry, schema_id, "?table=orders")
    assert [t["table"] for t in only_orders["tables"]] == ["orders"]
    assert only_orders["tables"][0]["relationships"] == []  # the other end is not selected

    related = _representation(registry, schema_id, "?table=sales.orders&include_related=true")
    assert [t["table"] for t in related["tables"]] == ["customers", "orders"]
    assert len(related["relationships"]) == 1

    finance = _representation(registry, schema_id, "?schema_name=finance")
    assert [t["table"] for t in finance["tables"]] == ["ledger"]

    bare = _representation(registry, schema_id, "?include_descriptions=false&include_business_terms=false")
    assert bare["business_terms"] == []
    assert all("description" not in t for t in bare["tables"])


def test_permission_policies_are_checked_against_registered_schemas(registry: Registry) -> None:
    policy = {"allowed_tables": ["invoices"], "allowed_columns": ["invoices.*"]}
    # Nothing registered yet: table names cannot be checked, so the policy is accepted.
    registry.create_permission("invoices.view", policy)

    registry.imported_schema()
    unknown = registry.post("/permissions", {
        "key": "invoices.view2", "name": "Invoices", "resource_type": "database_data", "action": "view",
        "policy_definition": policy,
    })
    assert unknown.status_code == 422
    assert "not a registered table" in str(unknown.json()["error"]["details"])

    registry.create_permission("orders.view", {
        "allowed_tables": ["sales.orders"],
        "allowed_columns": ["sales.orders.id", "sales.orders.total_amount"],
        "row_filters": [{"column": "sales.orders.total_amount", "operator": "greater_than", "value": 10}],
    })
    wrong_column = registry.post("/permissions", {
        "key": "orders.view2", "name": "Orders", "resource_type": "database_data", "action": "view",
        "policy_definition": {"allowed_tables": ["orders"], "allowed_columns": ["orders.missing"]},
    })
    assert wrong_column.status_code == 422
    wrong_type = registry.post("/permissions", {
        "key": "orders.view3", "name": "Orders", "resource_type": "database_data", "action": "view",
        "policy_definition": {
            "allowed_tables": ["orders"], "allowed_columns": ["orders.*"],
            "row_filters": [{"column": "total_amount", "operator": "equals", "value": "a lot"}],
        },
    })
    assert wrong_type.status_code == 422


def test_versions_increase_once_per_change_and_never_on_reads(registry: Registry) -> None:
    schema_id = registry.create_schema()["id"]
    assert registry.version(schema_id) == 1
    table = registry.create_table(schema_id, "orders", columns=[{"column_name": "id", "data_type": "int", "is_primary_key": True}])
    assert registry.version(schema_id) == 2
    column = registry.create_column(table["id"], "total", "numeric(10,2)")
    assert registry.version(schema_id) == 3
    registry.ok(registry.patch(f"/columns/{column['id']}", {"description": "Order total"}))
    assert registry.version(schema_id) == 4

    for path in ("", "/tables", "/summary", "/outline", "/representation", "/history", "/relationships", "/export"):
        registry.ok(registry.get(f"/schemas/{schema_id}{path}"))
    registry.ok(registry.get(f"/tables/{table['id']}/columns"))
    assert registry.version(schema_id) == 4

    version = registry.ok(registry.get(f"/schemas/{schema_id}/version"))
    assert version["last_change"]["event_type"] == "column_updated"
    assert version["last_change"]["schema_version"] == 4
    assert version["last_change"]["actor_name"] == "Installation Admin"


def test_every_change_is_audited_with_a_summary(registry: Registry) -> None:
    schema_id = registry.create_schema()["id"]
    table = registry.create_table(schema_id, "orders", schema_name="sales",
                                  columns=[{"column_name": "id", "data_type": "int", "is_primary_key": True}])
    column = registry.create_column(table["id"], "amount", "numeric")
    registry.ok(registry.patch(f"/tables/{table['id']}", {"description": "Orders"}))
    registry.ok(registry.patch(f"/columns/{column['id']}", {"data_type": "numeric(10,2)"}))
    registry.ok(registry.delete(f"/columns/{column['id']}"), 204)
    registry.ok(registry.import_document(schema_id, {
        "schema_name": "sales", "tables": [{"table_name": "customers", "columns": [
            {"column_name": "id", "data_type": "int", "is_primary_key": True}]}],
    }))

    events = registry.history(schema_id)
    assert [e["event_type"] for e in events] == [
        "schema_imported", "column_deleted", "column_updated", "table_updated", "column_created", "table_created",
        "schema_created",
    ]
    assert [e["schema_version"] for e in events] == [7, 6, 5, 4, 3, 2, 1]
    by_type = {e["event_type"]: e for e in events}
    assert by_type["table_created"]["summary"] == "Added table sales.orders with 1 column"
    assert by_type["column_updated"]["details"]["changes"]["data_type"] == {"from": "numeric", "to": "numeric(10,2)"}
    assert by_type["column_deleted"]["summary"] == "Deleted column sales.orders.amount"
    assert all(e["actor_user_id"] == str(registry.admin_id) for e in events)
    assert registry.ok(registry.get(f"/schemas/{schema_id}/history?limit=2"))["total"] == 7

    _, reader = registry.user("auditor")
    assert registry.get(f"/schemas/{schema_id}/history", headers=reader).status_code == 403


def test_import_document_with_unregistered_namespace_defaults(registry: Registry) -> None:
    schema_id = registry.create_schema()["id"]
    document = orders_document()
    document.pop("schema_name")
    preview = registry.ok(registry.import_document(schema_id, document, dry_run=True))
    assert preview["valid"] is False
    assert "schema_name" in preview["errors"][0]["message"]

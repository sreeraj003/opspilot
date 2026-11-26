"""Tables, columns and relationships through the HTTP API, with validation and versioning."""

from typing import Any

import pytest

from tests.schema_registry.conftest import Registry

pytestmark = pytest.mark.postgres

ID = {"column_name": "id", "data_type": "bigint", "is_primary_key": True}


def _codes(response: Any) -> list[str]:
    return [issue["code"] for issue in response.json()["error"].get("details") or []]


# --- tables -----------------------------------------------------------------------------------------


def test_create_table_with_columns_and_read_it_back(registry: Registry) -> None:
    schema_id = registry.create_schema()["id"]
    table = registry.create_table(
        schema_id, "orders", schema_name="sales", description="Customer orders", business_purpose="Revenue",
        columns=[ID, {"column_name": "Total_Amount", "data_type": " NUMERIC( 12 , 2 ) ", "is_nullable": False}],
    )
    assert table["qualified_name"] == "sales.orders"
    assert table["column_count"] == 2
    id_column, amount = table["columns"]
    assert id_column["is_nullable"] is False  # primary keys default to NOT NULL
    assert amount["data_type"] == "numeric(12,2)" and amount["data_type_category"] == "number"
    assert [c["ordinal_position"] for c in table["columns"]] == [0, 1]

    fetched = registry.ok(registry.get(f"/tables/{table['id']}"))
    assert fetched["description"] == "Customer orders" and len(fetched["columns"]) == 2
    assert registry.version(schema_id) == 2


def test_table_names_are_unique_per_namespace_case_insensitively(registry: Registry) -> None:
    schema_id = registry.create_schema()["id"]
    registry.create_table(schema_id, "orders", schema_name="sales")
    registry.create_table(schema_id, "orders", schema_name="archive")  # another namespace is fine

    duplicate = registry.post(f"/schemas/{schema_id}/tables", {"schema_name": "SALES", "table_name": "Orders"})
    assert duplicate.status_code == 422
    assert _codes(duplicate) == ["duplicate_table"]
    assert duplicate.json()["error"]["code"] == "SCHEMA_VALIDATION_FAILED"

    # A different schema source may register the same name.
    other = registry.create_schema("Other")["id"]
    registry.create_table(other, "orders", schema_name="sales")


def test_invalid_identifiers_and_data_types_are_rejected(registry: Registry) -> None:
    schema_id = registry.create_schema()["id"]
    for name in ("1orders", "orders;drop", "order items", "a" * 64, "orders\"", ""):
        response = registry.post(f"/schemas/{schema_id}/tables", {"schema_name": "public", "table_name": name})
        assert response.status_code == 422, name
    table_id = registry.create_table(schema_id, "orders", columns=[ID])["id"]
    for data_type in ("varchar(255", "money(1,2,3)", "integer; drop table x", "blob blob", "notatype", "int(1)(2)"):
        response = registry.post(f"/tables/{table_id}/columns", {"column_name": "x", "data_type": data_type})
        assert response.status_code == 422, data_type
    for data_type, normalized in (("VARCHAR(255)", "varchar(255)"), ("nvarchar(max)", "nvarchar(max)"),
                                  ("int unsigned", "int unsigned"), ("Integer[]", "integer[]"),
                                  ("timestamp(3)  with time zone", "timestamp(3) with time zone")):
        column = registry.create_column(table_id, f"c_{len(normalized)}_{normalized[:3]}", data_type)
        assert column["data_type"] == normalized


def test_update_table_and_no_op_updates_do_not_bump_the_version(registry: Registry) -> None:
    schema_id = registry.create_schema()["id"]
    table = registry.create_table(schema_id, "orders", columns=[ID])
    version = registry.version(schema_id)

    same = registry.ok(registry.patch(f"/tables/{table['id']}", {"table_name": "orders", "description": None}))
    assert same["table_name"] == "orders"
    assert registry.version(schema_id) == version

    updated = registry.ok(registry.patch(f"/tables/{table['id']}", {"table_name": "sales_orders", "description": "Orders"}))
    assert updated["table_name"] == "sales_orders" and updated["description"] == "Orders"
    assert registry.version(schema_id) == version + 1

    registry.create_table(schema_id, "orders")
    clash = registry.patch(f"/tables/{table['id']}", {"table_name": "ORDERS"})
    assert clash.status_code == 422 and _codes(clash) == ["duplicate_table"]
    assert registry.patch(f"/tables/{table['id']}", {"table_name": None}).status_code == 422


def test_list_tables_with_search_filters_sort_and_pagination(registry: Registry) -> None:
    schema_id = registry.create_schema()["id"]
    registry.create_table(schema_id, "orders", schema_name="sales", description="Customer orders")
    registry.create_table(schema_id, "customers", schema_name="sales", columns=[ID])
    registry.create_table(schema_id, "ledger", schema_name="finance", is_active=False, business_purpose="orders paid")

    names = lambda query: [t["table_name"] for t in registry.ok(registry.get(f"/schemas/{schema_id}/tables{query}"))["items"]]  # noqa: E731
    assert names("") == ["customers", "ledger", "orders"]
    assert names("?search=order") == ["ledger", "orders"]  # name, description or purpose
    assert names("?schema_name=finance") == ["ledger"]
    assert names("?status=active") == ["customers", "orders"]
    assert names("?sort=table_name&order=desc&limit=2") == ["orders", "ledger"]
    assert names("?sort=schema_name") == ["ledger", "customers", "orders"]
    page = registry.ok(registry.get(f"/schemas/{schema_id}/tables?offset=1&limit=1"))
    assert page["total"] == 3 and [t["table_name"] for t in page["items"]] == ["ledger"]
    customers = registry.ok(registry.get(f"/schemas/{schema_id}/tables?search=customers"))["items"][0]
    assert customers["column_count"] == 1


# --- columns -----------------------------------------------------------------------------------------


def test_columns_are_unique_per_table_and_primary_keys_are_not_nullable(registry: Registry) -> None:
    schema_id = registry.create_schema()["id"]
    table_id = registry.create_table(schema_id, "orders", columns=[ID])["id"]

    duplicate = registry.post(f"/tables/{table_id}/columns", {"column_name": "ID", "data_type": "integer"})
    assert duplicate.status_code == 422 and _codes(duplicate) == ["duplicate_column"]

    nullable_pk = registry.post(
        f"/tables/{table_id}/columns", {"column_name": "code", "data_type": "text", "is_primary_key": True, "is_nullable": True}
    )
    assert nullable_pk.status_code == 422 and _codes(nullable_pk) == ["primary_key_nullable"]

    composite = registry.create_column(table_id, "line_no", "integer", is_primary_key=True)
    assert composite["is_nullable"] is False and composite["ordinal_position"] == 1

    duplicate_in_create = registry.post(
        f"/schemas/{schema_id}/tables",
        {"schema_name": "public", "table_name": "items", "columns": [ID, {"column_name": "Id", "data_type": "int"}]},
    )
    assert duplicate_in_create.status_code == 422 and "duplicate_column" in _codes(duplicate_in_create)


def test_update_list_and_delete_columns(registry: Registry) -> None:
    schema_id = registry.create_schema()["id"]
    table_id = registry.create_table(schema_id, "customers", columns=[ID])["id"]
    email = registry.create_column(table_id, "email", "varchar(320)", is_sensitive=True, description="Login email")
    registry.create_column(table_id, "name", "text", business_meaning="Display name")
    version = registry.version(schema_id)

    updated = registry.ok(registry.patch(f"/columns/{email['id']}", {"is_primary_key": True}))
    assert updated["is_primary_key"] is True and updated["is_nullable"] is False
    assert registry.version(schema_id) == version + 1
    assert registry.patch(f"/columns/{email['id']}", {"is_nullable": True}).status_code == 422
    assert registry.patch(f"/columns/{email['id']}", {"data_type": "nonsense"}).status_code == 422
    assert registry.patch(f"/columns/{email['id']}", {"column_name": None}).status_code == 422

    listed = lambda query: [c["column_name"] for c in registry.ok(registry.get(f"/tables/{table_id}/columns{query}"))["items"]]  # noqa: E731
    assert listed("") == ["id", "email", "name"]
    assert listed("?sensitive=true") == ["email"]
    assert listed("?primary_key=true") == ["id", "email"]
    assert listed("?search=display") == ["name"]
    assert listed("?sort=column_name") == ["email", "id", "name"]

    assert registry.delete(f"/columns/{email['id']}").status_code == 204
    assert listed("") == ["id", "name"]
    assert registry.patch(f"/columns/{email['id']}", {"description": "x"}).status_code == 404


# --- relationships ------------------------------------------------------------------------------------------


def _orders_schema(registry: Registry) -> tuple[str, dict[str, Any], dict[str, Any]]:
    schema_id = registry.create_schema()["id"]
    customers = registry.create_table(schema_id, "customers", schema_name="sales",
                                      columns=[ID, {"column_name": "name", "data_type": "text"}])
    orders = registry.create_table(
        schema_id, "orders", schema_name="sales",
        columns=[ID, {"column_name": "customer_id", "data_type": "integer", "is_foreign_key": True},
                 {"column_name": "note", "data_type": "text"}, {"column_name": "manager_order_id", "data_type": "bigint"}],
    )
    return schema_id, customers, orders


def _column(table: dict[str, Any], name: str) -> str:
    return next(c["id"] for c in table["columns"] if c["column_name"] == name)


def _relationship(source: dict[str, Any], source_column: str, target: dict[str, Any], target_column: str,
                  relationship_type: str = "many_to_one", **extra: Any) -> dict[str, Any]:
    return {
        "source_table_id": source["id"], "source_column_id": _column(source, source_column),
        "target_table_id": target["id"], "target_column_id": _column(target, target_column),
        "relationship_type": relationship_type, **extra,
    }


def test_create_list_update_and_delete_relationships(registry: Registry) -> None:
    schema_id, customers, orders = _orders_schema(registry)
    version = registry.version(schema_id)

    created = registry.ok(
        registry.post(f"/schemas/{schema_id}/relationships",
                      _relationship(orders, "customer_id", customers, "id", description="Who ordered")), 201
    )
    assert created["source"]["table_name"] == "orders" and created["source"]["column_name"] == "customer_id"
    assert created["target"]["table_name"] == "customers" and created["relationship_type"] == "many_to_one"
    assert registry.version(schema_id) == version + 1

    listed = registry.ok(registry.get(f"/schemas/{schema_id}/relationships"))
    assert listed["total"] == 1 and listed["items"][0]["description"] == "Who ordered"
    assert registry.ok(registry.get(f"/schemas/{schema_id}/relationships?table_id={customers['id']}"))["total"] == 1
    assert registry.ok(registry.get(f"/schemas/{schema_id}/relationships?relationship_type=one_to_one"))["total"] == 0
    assert registry.ok(registry.get(f"/schemas/{schema_id}/relationships?search=customer_id"))["total"] == 1

    updated = registry.ok(registry.patch(f"/relationships/{created['id']}", {"relationship_type": "one_to_one"}))
    assert updated["relationship_type"] == "one_to_one"
    assert registry.patch(f"/relationships/{created['id']}", {"relationship_type": "sideways"}).status_code == 422

    assert registry.delete(f"/relationships/{created['id']}").status_code == 204
    assert registry.ok(registry.get(f"/schemas/{schema_id}/relationships"))["total"] == 0
    assert registry.delete(f"/relationships/{created['id']}").status_code == 404


def test_invalid_relationship_endpoints_are_rejected(registry: Registry) -> None:
    schema_id, customers, orders = _orders_schema(registry)
    path = f"/schemas/{schema_id}/relationships"

    mismatched = _relationship(orders, "customer_id", customers, "id")
    mismatched["target_column_id"] = _column(orders, "id")  # a column of another table
    response = registry.post(path, mismatched)
    assert response.status_code == 422 and _codes(response) == ["relationship_column_mismatch"]

    same_column = _relationship(orders, "customer_id", orders, "customer_id")
    response = registry.post(path, same_column)
    assert response.status_code == 422 and "self_referencing_column" in _codes(response)

    incompatible = _relationship(orders, "note", customers, "id")
    response = registry.post(path, incompatible)
    assert response.status_code == 422 and _codes(response) == ["incompatible_relationship_types"]

    other_schema = registry.create_schema("Other")["id"]
    foreign = registry.create_table(other_schema, "accounts", columns=[ID])
    cross = _relationship(orders, "customer_id", foreign, "id")
    response = registry.post(path, cross)
    assert response.status_code == 422 and _codes(response) == ["relationship_endpoint_outside_schema"]

    registry.ok(registry.patch(f"/tables/{customers['id']}", {"is_active": False}))
    response = registry.post(path, _relationship(orders, "customer_id", customers, "id"))
    assert response.status_code == 422 and _codes(response) == ["relationship_inactive_endpoint"]


def test_self_referencing_tables_are_supported_but_duplicates_are_not(registry: Registry) -> None:
    schema_id, customers, orders = _orders_schema(registry)
    path = f"/schemas/{schema_id}/relationships"

    registry.ok(registry.post(path, _relationship(orders, "manager_order_id", orders, "id")), 201)
    registry.ok(registry.post(path, _relationship(orders, "customer_id", customers, "id")), 201)

    again = registry.post(path, _relationship(orders, "customer_id", customers, "id"))
    assert again.status_code == 422 and _codes(again) == ["duplicate_relationship"]
    reversed_pair = registry.post(path, _relationship(customers, "id", orders, "customer_id", "one_to_many"))
    assert reversed_pair.status_code == 422 and _codes(reversed_pair) == ["duplicate_relationship"]


def test_tables_and_columns_used_by_relationships_need_an_explicit_cascade(registry: Registry) -> None:
    schema_id, customers, orders = _orders_schema(registry)
    relationship = registry.ok(
        registry.post(f"/schemas/{schema_id}/relationships", _relationship(orders, "customer_id", customers, "id")), 201
    )

    blocked = registry.delete(f"/tables/{customers['id']}")
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "IN_USE"
    assert blocked.json()["error"]["details"]["relationship_ids"] == [relationship["id"]]
    column_blocked = registry.delete(f"/columns/{_column(orders, 'customer_id')}")
    assert column_blocked.status_code == 409

    version = registry.version(schema_id)
    assert registry.delete(f"/columns/{_column(orders, 'customer_id')}?cascade_relationships=true").status_code == 204
    assert registry.ok(registry.get(f"/schemas/{schema_id}/relationships"))["total"] == 0
    assert registry.version(schema_id) == version + 1  # one change, one version
    assert registry.delete(f"/tables/{customers['id']}").status_code == 204


def test_summary_counts_and_reports_current_issues(registry: Registry) -> None:
    schema_id, customers, orders = _orders_schema(registry)
    registry.create_table(schema_id, "audit_log", schema_name="ops")  # no columns: a warning
    summary = registry.ok(registry.get(f"/schemas/{schema_id}/summary"))
    counts = summary["counts"]
    assert counts["tables"] == 3 and counts["columns"] == 6
    assert counts["primary_key_columns"] == 2 and counts["foreign_key_columns"] == 1
    assert counts["schema_namespaces"] == ["ops", "sales"]
    codes = {issue["code"] for issue in summary["issues"]}
    assert {"table_without_columns", "foreign_key_without_relationship"} <= codes
    assert all(issue["severity"] == "warning" for issue in summary["issues"])

"""``/api/v1/business-terms`` against the real app, PostgreSQL and permission engine."""

import pytest

from tests.understanding.conftest import Understanding

pytestmark = pytest.mark.postgres


def test_create_list_get_update_delete(u: Understanding) -> None:
    created = u.create_term(
        "Revenue",
        synonyms=["turnover", "sales"],
        related_columns=["SALES.ORDERS.TOTAL_AMOUNT"],
        default_aggregation="sum",
        aggregation_guidance="Sum of order totals excluding cancelled orders.",
    )
    assert created["kind"] == "metric" and created["is_active"] is True
    # Canonical spelling; a column's table is always related too.
    assert created["related_columns"] == ["sales.orders.total_amount"] and created["related_tables"] == ["sales.orders"]
    assert created["schema_source_name"] == "Sales DB" and created["stale_references"] == []

    u.create_term("Customer count", kind="metric", related_columns=["sales.customers.id"], default_aggregation="count_distinct")
    u.create_term("Product category", kind="dimension", related_columns=["sales.products.category"], is_active=False)

    page = u.ok(u.get(f"/business-terms?schema_source_id={u.schema_id}"))
    assert page["total"] == 3 and [t["name"] for t in page["items"]] == ["Customer count", "Product category", "Revenue"]
    assert [t["name"] for t in u.ok(u.get("/business-terms?search=turnover"))["items"]] == ["Revenue"]
    assert [t["name"] for t in u.ok(u.get("/business-terms?kind=dimension"))["items"]] == ["Product category"]
    assert u.ok(u.get("/business-terms?is_active=false"))["total"] == 1
    assert u.ok(u.get(f"/business-terms/{created['id']}"))["name"] == "Revenue"

    updated = u.ok(u.patch(f"/business-terms/{created['id']}", {"description": "Net of discounts", "synonyms": ["turnover"],
                                                                 "is_active": False, "default_aggregation": None}))
    assert updated["description"] == "Net of discounts" and updated["synonyms"] == ["turnover"]
    assert updated["is_active"] is False and updated["default_aggregation"] is None
    assert updated["related_columns"] == ["sales.orders.total_amount"]  # untouched

    associated = u.ok(u.patch(f"/business-terms/{created['id']}", {"related_columns": ["sales.orders.total_amount", "sales.orders.status"]}))
    assert associated["related_columns"] == ["sales.orders.total_amount", "sales.orders.status"]

    assert u.delete(f"/business-terms/{created['id']}").status_code == 204
    assert u.get(f"/business-terms/{created['id']}").status_code == 404
    assert u.ok(u.get("/business-terms"))["total"] == 2
    # The name can be reused after deletion.
    u.create_term("Revenue", related_columns=["sales.orders.total_amount"])


def test_terms_must_reference_registered_fields(u: Understanding) -> None:
    response = u.post("/business-terms", {
        "schema_source_id": u.schema_id, "name": "Profit", "description": "Revenue minus cost",
        "related_columns": ["sales.orders.cost", "sales.invoices.amount"], "related_tables": ["finance.ledger"],
    })
    assert response.status_code == 422 and response.json()["error"]["code"] == "BUSINESS_TERM_INVALID"
    messages = [d["message"] for d in response.json()["error"]["details"]]
    assert len(messages) == 3 and any("sales.orders.cost" in m for m in messages)

    no_fields = u.post("/business-terms", {"schema_source_id": u.schema_id, "name": "Profit", "description": "x"})
    assert no_fields.status_code == 422

    sql = u.post("/business-terms", {"schema_source_id": u.schema_id, "name": "Revenue", "description": "x",
                                     "related_columns": ["sales.orders.total_amount"],
                                     "aggregation_guidance": "SELECT SUM(total_amount) FROM sales.orders"})
    assert sql.status_code == 422

    bad_aggregation = u.post("/business-terms", {"schema_source_id": u.schema_id, "name": "Revenue", "description": "x",
                                                 "related_columns": ["sales.orders.total_amount"], "default_aggregation": "median"})
    assert bad_aggregation.status_code == 422


def test_name_and_synonym_conflicts(u: Understanding) -> None:
    u.create_term("Revenue", synonyms=["turnover"], related_columns=["sales.orders.total_amount"])
    duplicate = u.post("/business-terms", {"schema_source_id": u.schema_id, "name": "revenue", "description": "x",
                                           "related_columns": ["sales.orders.total_amount"]})
    assert duplicate.status_code == 409
    name_is_synonym = u.post("/business-terms", {"schema_source_id": u.schema_id, "name": "Turnover", "description": "x",
                                                 "related_columns": ["sales.orders.total_amount"]})
    assert name_is_synonym.status_code == 409
    self_synonym = u.post("/business-terms", {"schema_source_id": u.schema_id, "name": "Net", "description": "x",
                                              "synonyms": ["net"], "related_columns": ["sales.orders.total_amount"]})
    assert self_synonym.status_code == 422
    # A shared synonym is allowed: it records that a word has several meanings.
    u.create_term("Gross sales", synonyms=["sales"], related_columns=["sales.orders.total_amount"])
    u.create_term("Net sales", synonyms=["sales"], related_columns=["sales.orders.total_amount"])


def test_stale_references_after_schema_changes(u: Understanding) -> None:
    term = u.create_term("Order status", kind="dimension", related_columns=["sales.orders.status"])
    tables = u.ok(u.get(f"/schemas/{u.schema_id}/tables?search=orders"))["items"]
    orders = next(t for t in tables if t["table_name"] == "orders")
    columns = u.ok(u.get(f"/tables/{orders['id']}/columns?search=status"))["items"]
    status = next(c for c in columns if c["column_name"] == "status")
    u.ok(u.patch(f"/columns/{status['id']}", {"is_active": False}))
    assert u.ok(u.get(f"/business-terms/{term['id']}"))["stale_references"] == ["sales.orders.status"]


def test_schema_fields_for_association(u: Understanding) -> None:
    fields = u.ok(u.get(f"/business-terms/schema-fields?schema_source_id={u.schema_id}"))
    assert fields["schema_source_name"] == "Sales DB"
    tables = {t["table"]: [c["name"] for c in t["columns"]] for t in fields["tables"]}
    assert tables["sales.orders"][:2] == ["id", "customer_id"] and "hr.payroll" in tables


def test_permissions(u: Understanding) -> None:
    created = u.create_term("Revenue", related_columns=["sales.orders.total_amount"])
    _, nobody = u.user()
    assert u.get("/business-terms", nobody).status_code == 403

    reader = u.analyst("business_term.read", policy=None)
    assert u.ok(u.get("/business-terms", reader))["total"] == 1
    assert u.get(f"/business-terms/schema-fields?schema_source_id={u.schema_id}", reader).status_code == 403
    assert u.post("/business-terms", {"schema_source_id": u.schema_id, "name": "X", "description": "x",
                                      "related_columns": ["sales.orders.id"]}, headers=reader).status_code == 403
    assert u.patch(f"/business-terms/{created['id']}", {"is_active": False}, headers=reader).status_code == 403
    assert u.delete(f"/business-terms/{created['id']}", headers=reader).status_code == 403

    manager = u.analyst("business_term.read", "business_term.manage", policy=None)
    assert u.ok(u.patch(f"/business-terms/{created['id']}", {"is_active": False}, headers=manager))["is_active"] is False


def test_unknown_schema_and_term(u: Understanding) -> None:
    missing = "00000000-0000-0000-0000-000000000000"
    assert u.post("/business-terms", {"schema_source_id": missing, "name": "X", "description": "x",
                                      "related_columns": ["sales.orders.id"]}).status_code == 404
    assert u.get(f"/business-terms/{missing}").status_code == 404
    assert u.patch(f"/business-terms/{missing}", {"is_active": False}).status_code == 404

"""JSON schema import (create_only, merge, replace, dry runs, limits, rollback) and export."""

import json
from typing import Any

import pytest

from app.services.schema_registry_service import SchemaRegistryService
from tests.schema_registry.conftest import Registry, orders_document

pytestmark = pytest.mark.postgres


def _codes(issues: list[dict[str, Any]]) -> set[str]:
    return {issue["code"] for issue in issues}


def test_import_creates_tables_columns_relationships_and_glossary(registry: Registry) -> None:
    schema_id = registry.create_schema()["id"]
    result = registry.ok(registry.import_document(schema_id, orders_document()))

    assert result["applied"] is True and result["valid"] is True and result["dry_run"] is False
    assert result["counts"]["tables"]["created"] == 2
    assert result["counts"]["columns"]["created"] == 7
    assert result["counts"]["relationships"]["created"] == 1
    assert result["counts"]["glossary_terms"]["created"] == 1
    assert (result["schema_version_before"], result["schema_version_after"]) == (1, 2)
    assert result["version_incremented"] is True
    assert {"action": "create", "entity_type": "table", "name": "sales.orders", "fields": []} in result["changes"]

    outline = registry.outline(schema_id)
    assert set(outline) == {"sales.customers", "sales.orders"}
    assert outline["sales.customers"]["columns"]["id"]["is_nullable"] is False
    assert outline["sales.customers"]["columns"]["email"]["is_sensitive"] is True
    assert outline["sales.orders"]["columns"]["total_amount"]["data_type"] == "decimal(12,2)"
    schema = registry.ok(registry.get(f"/schemas/{schema_id}"))
    assert schema["business_glossary"][0]["term"] == "GMV"
    events = registry.history(schema_id)
    assert events[0]["event_type"] == "schema_imported" and events[0]["version_incremented"] is True
    assert "2 tables created" in events[0]["summary"]


def test_dry_run_previews_without_changing_anything(registry: Registry) -> None:
    schema_id = registry.create_schema()["id"]
    preview = registry.ok(registry.import_document(schema_id, orders_document(), dry_run=True))
    assert preview["dry_run"] is True and preview["applied"] is False and preview["valid"] is True
    assert preview["counts"]["tables"]["created"] == 2
    assert preview["schema_version_after"] == preview["schema_version_before"] == 1
    assert registry.outline(schema_id) == {}
    assert registry.version(schema_id) == 1
    assert [e["event_type"] for e in registry.history(schema_id)] == ["schema_created"]


def test_invalid_documents_are_reported_in_a_dry_run_and_rejected_otherwise(registry: Registry) -> None:
    schema_id = registry.create_schema()["id"]
    document = orders_document()
    document["tables"][0]["table_name"] = "customers; DROP TABLE users"
    document["tables"][1]["columns"][2]["data_type"] = "dollars"
    document["tables"][1]["columns"].append({"column_name": "ID", "data_type": "int"})
    document["tables"][1]["unexpected"] = True

    preview = registry.ok(registry.import_document(schema_id, document, dry_run=True))
    assert preview["valid"] is False and preview["applied"] is False
    locations = {tuple(issue["location"]) for issue in preview["errors"]}
    assert ("document", "tables", "0", "table_name") in locations
    assert ("document", "tables", "1", "columns", "2", "data_type") in locations
    assert ("document", "tables", "1", "unexpected") in locations

    rejected = registry.import_document(schema_id, document)
    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "SCHEMA_VALIDATION_FAILED"
    assert registry.outline(schema_id) == {}


def test_semantic_errors_reject_the_whole_import(registry: Registry) -> None:
    schema_id = registry.create_schema()["id"]
    document = orders_document()
    document["tables"].append({"table_name": "Orders", "columns": []})  # duplicate, different case
    document["tables"][1]["columns"].append({"column_name": "Total_Amount", "data_type": "money"})
    document["relationships"].append(
        {"source_table": "orders", "source_column": "nope", "target_table": "customers", "target_column": "id",
         "relationship_type": "many_to_one"}
    )
    document["relationships"].append(
        {"source_table": "orders", "source_column": "placed_at", "target_table": "customers", "target_column": "id",
         "relationship_type": "many_to_one"}
    )
    preview = registry.ok(registry.import_document(schema_id, document, dry_run=True))
    assert {"duplicate_table", "duplicate_column", "dangling_relationship"} <= _codes(preview["errors"])

    response = registry.import_document(schema_id, document)
    assert response.status_code == 422
    assert {"duplicate_table", "duplicate_column", "dangling_relationship"} <= {
        d["code"] for d in response.json()["error"]["details"]
    }
    assert registry.outline(schema_id) == {} and registry.version(schema_id) == 1

    incompatible = orders_document()
    incompatible["relationships"][0]["source_column"] = "placed_at"
    preview = registry.ok(registry.import_document(schema_id, incompatible, dry_run=True))
    assert _codes(preview["errors"]) == {"incompatible_relationship_types"}


def test_malformed_requests_and_limits(registry: Registry, monkeypatch: pytest.MonkeyPatch) -> None:
    schema_id = registry.create_schema()["id"]
    path = f"/api/v1/schemas/{schema_id}/import"
    headers = {**registry.admin, "content-type": "application/json"}

    not_json = registry.client.post(path, content=b'{"document": {"tables": [', headers=headers)
    assert not_json.status_code == 422 and not_json.json()["error"]["code"] == "INVALID_JSON"

    deep: Any = {"x": 1}
    for _ in range(12):
        deep = {"nested": deep}
    too_deep = registry.client.post(path, content=json.dumps({"document": deep}).encode(), headers=headers)
    assert too_deep.status_code == 422 and too_deep.json()["error"]["code"] == "JSON_TOO_DEEP"

    bad_strategy = registry.import_document(schema_id, orders_document(), strategy="overwrite")
    assert bad_strategy.status_code == 422

    big = registry.client.post(path, content=b" " * (5 * 1024 * 1024 + 1), headers=headers)
    assert big.status_code == 413 and big.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"


def test_table_limit_is_enforced(api_factory: Any) -> None:
    registry = Registry(api_factory(schema_max_tables=1))
    schema_id = registry.create_schema()["id"]
    preview = registry.ok(registry.import_document(schema_id, orders_document(), dry_run=True))
    assert _codes(preview["errors"]) == {"limit_exceeded"}


def test_create_only_refuses_to_touch_registered_tables(registry: Registry) -> None:
    schema_id = registry.imported_schema()["id"]
    document = orders_document()
    document["tables"] = [document["tables"][1]]  # orders, already registered
    document["relationships"] = []
    document["business_glossary"] = None
    response = registry.import_document(schema_id, document)
    assert response.status_code == 422
    assert {d["code"] for d in response.json()["error"]["details"]} == {"table_exists"}

    # New tables may reference registered ones.
    addition = {
        "schema_name": "sales",
        "tables": [{"table_name": "refunds", "columns": [
            {"column_name": "id", "data_type": "integer", "is_primary_key": True},
            {"column_name": "order_id", "data_type": "integer", "is_foreign_key": True},
        ]}],
        "relationships": [{"source_table": "refunds", "source_column": "order_id", "target_table": "orders",
                           "target_column": "id", "relationship_type": "many_to_one"}],
    }
    result = registry.ok(registry.import_document(schema_id, addition))
    assert result["counts"]["tables"] == {"created": 1, "updated": 0, "unchanged": 2, "removed": 0}


def test_merge_updates_only_the_fields_the_document_contains(registry: Registry) -> None:
    schema_id = registry.imported_schema()["id"]
    version = registry.version(schema_id)
    document = {
        "schema_name": "sales",
        "tables": [
            {"table_name": "orders", "description": "All orders, including cancelled",
             "columns": [{"column_name": "total_amount", "data_type": "numeric(14,2)"},
                         {"column_name": "channel", "data_type": "varchar(20)"}]},
            {"table_name": "products", "columns": [{"column_name": "sku", "data_type": "text", "is_primary_key": True}]},
        ],
        "business_glossary": [{"term": "gmv", "definition": "Gross merchandise value"},
                              {"term": "AOV", "definition": "Average order value"}],
    }
    preview = registry.ok(registry.import_document(schema_id, document, strategy="merge", dry_run=True))
    assert preview["valid"] is True
    changes = {(c["action"], c["entity_type"], c["name"]): c["fields"] for c in preview["changes"]}
    assert changes[("update", "table", "sales.orders")] == ["description"]
    assert changes[("update", "column", "sales.orders.total_amount")] == ["data_type"]
    assert ("create", "column", "sales.orders.channel") in changes
    assert ("create", "table", "sales.products") in changes
    assert preview["counts"]["glossary_terms"] == {"created": 1, "updated": 1, "unchanged": 0, "removed": 0}

    result = registry.ok(registry.import_document(schema_id, document, strategy="merge",
                                                  expected_schema_version=version))
    assert result["schema_version_after"] == version + 1
    outline = registry.outline(schema_id)
    orders = outline["sales.orders"]["columns"]
    assert orders["total_amount"]["data_type"] == "numeric(14,2)"
    assert orders["total_amount"]["is_nullable"] is False  # not in the document: kept
    assert {"id", "customer_id", "placed_at", "channel"} <= set(orders)  # omitted columns are kept
    assert "sales.customers" in outline  # omitted tables are kept
    table = registry.ok(registry.get(f"/tables/{outline['sales.orders']['id']}"))
    assert table["business_purpose"] == "One row per order placed"
    assert registry.ok(registry.get(f"/schemas/{schema_id}/relationships"))["total"] == 1

    again = registry.ok(registry.import_document(schema_id, document, strategy="merge"))
    assert again["version_incremented"] is False and again["changes"] == []


def test_replace_needs_confirmation_and_removes_what_the_document_omits(registry: Registry) -> None:
    schema_id = registry.imported_schema()["id"]
    document = {
        "schema_name": "sales",
        "tables": [{"table_name": "orders", "columns": [
            {"column_name": "id", "data_type": "integer", "is_primary_key": True},
            {"column_name": "total_amount", "data_type": "decimal(12,2)"},
        ]}],
        "business_glossary": [],
    }
    refused = registry.import_document(schema_id, document, strategy="replace")
    assert refused.status_code == 422 and "confirm" in refused.json()["error"]["message"].lower()

    preview = registry.ok(registry.import_document(schema_id, document, strategy="replace", dry_run=True))
    assert preview["counts"]["tables"]["removed"] == 1
    assert preview["counts"]["columns"]["removed"] == 5  # 3 customer columns + customer_id + placed_at
    assert preview["counts"]["relationships"]["removed"] == 1
    assert preview["counts"]["glossary_terms"]["removed"] == 1
    total = next(c for c in preview["changes"] if c["name"] == "sales.orders.total_amount")
    # Replace resets omitted fields: description, business meaning and NOT NULL go back to defaults.
    assert set(total["fields"]) == {"description", "business_meaning", "is_nullable"}

    result = registry.ok(registry.import_document(schema_id, document, strategy="replace", confirm_replace=True))
    assert result["applied"] is True
    outline = registry.outline(schema_id)
    assert set(outline) == {"sales.orders"}
    assert set(outline["sales.orders"]["columns"]) == {"id", "total_amount"}
    assert registry.ok(registry.get(f"/schemas/{schema_id}/relationships"))["total"] == 0
    assert registry.ok(registry.get(f"/schemas/{schema_id}"))["business_glossary"] == []


def test_import_refuses_a_stale_preview(registry: Registry) -> None:
    schema_id = registry.create_schema()["id"]
    preview = registry.ok(registry.import_document(schema_id, orders_document(), dry_run=True))
    registry.ok(registry.patch(f"/schemas/{schema_id}", {"dialect": "postgresql-16"}))  # someone else changed it
    stale = registry.import_document(schema_id, orders_document(), expected_schema_version=preview["schema_version_before"])
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "SCHEMA_VERSION_CONFLICT"
    assert registry.outline(schema_id) == {}


def test_a_failure_while_applying_rolls_back_everything(registry: Registry, monkeypatch: pytest.MonkeyPatch) -> None:
    schema_id = registry.create_schema()["id"]
    original = SchemaRegistryService._apply

    async def failing_apply(self: SchemaRegistryService, *args: Any) -> None:
        await original(self, *args)  # every row is written (flushed)...
        raise RuntimeError("simulated failure after writing")  # ...then the import fails

    monkeypatch.setattr(SchemaRegistryService, "_apply", failing_apply)
    with pytest.raises(RuntimeError):
        registry.import_document(schema_id, orders_document())
    monkeypatch.undo()

    assert registry.outline(schema_id) == {}
    assert registry.version(schema_id) == 1
    assert registry.api.sql("SELECT count(*) FROM schema_tables")[0][0] == 0
    assert registry.api.sql("SELECT count(*) FROM schema_columns")[0][0] == 0
    assert [e["event_type"] for e in registry.history(schema_id)] == ["schema_created"]
    registry.ok(registry.import_document(schema_id, orders_document()))  # and works afterwards


def test_export_round_trips_and_is_audited(registry: Registry) -> None:
    schema_id = registry.imported_schema()["id"]
    exported = registry.ok(registry.get(f"/schemas/{schema_id}/export"))
    assert exported["format_version"] == 1 and exported["schema_version"] == 2
    assert exported["source"] == {"name": "Sales DB", "description": None, "database_type": "postgresql",
                                  "dialect": "postgresql"}
    assert exported["schema_name"] == "sales"
    assert [t["table_name"] for t in exported["tables"]] == ["customers", "orders"]
    assert exported["relationships"][0]["target_table"] == "customers"
    assert "id" not in exported["tables"][0]  # names only, no internal ids
    assert registry.version(schema_id) == 2  # exporting does not change the version
    events = registry.history(schema_id)
    assert events[0]["event_type"] == "schema_exported" and events[0]["version_incremented"] is False

    unchanged = registry.ok(registry.import_document(schema_id, exported, strategy="merge"))
    assert unchanged["changes"] == [] and unchanged["version_incremented"] is False

    copy_id = registry.create_schema("Copy")["id"]
    registry.ok(registry.import_document(copy_id, exported))
    copy = registry.ok(registry.get(f"/schemas/{copy_id}/export"))
    for key in ("tables", "relationships", "business_glossary"):
        assert copy[key] == exported[key]


def test_the_documented_example_imports_cleanly(registry: Registry) -> None:
    """docs/SCHEMA_IMPORT_FORMAT.md's complete example must stay valid."""
    import re
    from pathlib import Path

    path = Path(__file__).resolve().parents[3] / "docs" / "SCHEMA_IMPORT_FORMAT.md"
    if not path.is_file():  # e.g. inside the backend container, which holds only backend/
        pytest.skip("docs/ is not available here")
    text = path.read_text(encoding="utf-8")
    match = re.search(r"```json\n(.*?)```", text, re.S)
    assert match is not None
    document = json.loads(match.group(1))
    schema_id = registry.create_schema("Docs example")["id"]
    result = registry.ok(registry.import_document(schema_id, document))
    assert result["valid"] is True and result["errors"] == [] and result["warnings"] == []
    assert result["counts"]["tables"]["created"] == 3
    assert result["counts"]["columns"]["created"] == 14
    assert result["counts"]["relationships"]["created"] == 2
    assert result["counts"]["glossary_terms"]["created"] == 2

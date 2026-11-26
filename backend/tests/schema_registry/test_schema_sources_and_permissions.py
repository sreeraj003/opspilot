"""Schema sources through the HTTP API, and permission enforcement on every schema endpoint."""

import uuid

import pytest

from tests.schema_registry.conftest import Registry, orders_document

pytestmark = pytest.mark.postgres


def test_create_get_and_list_schema_sources(registry: Registry) -> None:
    created = registry.create_schema("  Sales   Warehouse ", description=" Orders and customers ", database_type="mysql")
    assert created["name"] == "Sales Warehouse"
    assert created["description"] == "Orders and customers"
    assert created["dialect"] == "mysql"  # defaults to the database type
    assert created["schema_version"] == 1 and created["is_active"] is True
    assert created["table_count"] == 0 and created["column_count"] == 0
    assert created["created_by"] == str(registry.admin_id)

    registry.create_schema("Finance", database_type="sqlserver", dialect="tsql", is_active=False)
    registry.create_schema("HR", database_type="postgresql")

    fetched = registry.ok(registry.get(f"/schemas/{created['id']}"))
    assert fetched["name"] == "Sales Warehouse"

    page = registry.ok(registry.get("/schemas"))
    assert [s["name"] for s in page["items"]] == ["Finance", "HR", "Sales Warehouse"]
    assert page["total"] == 3

    assert [s["name"] for s in registry.ok(registry.get("/schemas?search=ware"))["items"]] == ["Sales Warehouse"]
    assert [s["name"] for s in registry.ok(registry.get("/schemas?status=inactive"))["items"]] == ["Finance"]
    assert [s["name"] for s in registry.ok(registry.get("/schemas?database_type=postgresql"))["items"]] == ["HR"]
    descending = registry.ok(registry.get("/schemas?sort=name&order=desc&limit=2"))
    assert [s["name"] for s in descending["items"]] == ["Sales Warehouse", "HR"] and descending["total"] == 3
    second_page = registry.ok(registry.get("/schemas?offset=2&limit=2"))
    assert [s["name"] for s in second_page["items"]] == ["Sales Warehouse"]


def test_schema_counts_reflect_registered_metadata(registry: Registry) -> None:
    schema = registry.imported_schema()
    assert (schema["table_count"], schema["column_count"], schema["relationship_count"]) == (2, 7, 1)
    listed = registry.ok(registry.get("/schemas"))["items"][0]
    assert (listed["table_count"], listed["column_count"]) == (2, 7)


def test_schema_source_validation_and_duplicates(registry: Registry) -> None:
    registry.create_schema("Sales")
    duplicate = registry.post("/schemas", {"name": "sales", "database_type": "postgresql"})
    assert duplicate.status_code == 409

    for body in (
        {"name": "X", "database_type": "mongodb"},
        {"name": "", "database_type": "postgresql"},
        {"name": "X", "database_type": "postgresql", "dialect": "Bad Dialect!"},
        {"name": "X", "database_type": "postgresql", "connection_string": "postgres://user:pw@host/db"},
        {"name": "X", "database_type": "postgresql", "password": "secret"},
    ):
        response = registry.post("/schemas", body)
        assert response.status_code == 422, body
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_updating_a_source_versions_only_meaningful_changes(registry: Registry) -> None:
    schema = registry.create_schema("Sales")
    schema_id = schema["id"]

    renamed = registry.ok(registry.patch(f"/schemas/{schema_id}", {"name": "Sales DB", "description": "Main store"}))
    assert renamed["name"] == "Sales DB" and renamed["schema_version"] == 1

    unchanged = registry.ok(registry.patch(f"/schemas/{schema_id}", {"name": "Sales DB"}))
    assert unchanged["schema_version"] == 1

    redialected = registry.ok(registry.patch(f"/schemas/{schema_id}", {"dialect": "postgresql-16"}))
    assert redialected["schema_version"] == 2

    glossary = [{"term": "AOV", "definition": "Average order value", "synonyms": ["basket size"]}]
    with_terms = registry.ok(registry.patch(f"/schemas/{schema_id}", {"business_glossary": glossary}))
    assert with_terms["schema_version"] == 3 and with_terms["business_glossary"][0]["term"] == "AOV"

    duplicate_terms = registry.patch(
        f"/schemas/{schema_id}",
        {"business_glossary": [{"term": "AOV", "definition": "a"}, {"term": "aov", "definition": "b"}]},
    )
    assert duplicate_terms.status_code == 422
    assert registry.patch(f"/schemas/{schema_id}", {"name": None}).status_code == 422

    events = [e["event_type"] for e in registry.history(schema_id)]
    assert events == ["schema_updated", "schema_updated", "schema_updated", "schema_created"]


def test_deleting_a_source_hides_it_and_its_metadata(registry: Registry) -> None:
    schema = registry.imported_schema()
    table_id = registry.outline(schema["id"])["sales.orders"]["id"]

    assert registry.delete(f"/schemas/{schema['id']}").status_code == 204
    assert registry.get(f"/schemas/{schema['id']}").status_code == 404
    assert registry.get(f"/tables/{table_id}").status_code == 404
    assert registry.ok(registry.get("/schemas"))["total"] == 0
    # Soft delete: rows and history are kept, and the name can be reused.
    assert registry.api.sql("SELECT count(*) FROM schema_tables WHERE deleted_at IS NOT NULL")[0][0] == 2
    assert registry.api.sql("SELECT count(*) FROM schema_columns WHERE deleted_at IS NULL")[0][0] == 0
    assert registry.api.sql(
        "SELECT event_type FROM schema_change_events WHERE event_type = 'schema_deleted'"
    )[0][0] == "schema_deleted"
    registry.create_schema("Sales DB")


def test_unknown_ids_are_404(registry: Registry) -> None:
    missing = uuid.uuid4()
    for path in (f"/schemas/{missing}", f"/tables/{missing}", f"/schemas/{missing}/tables", f"/schemas/{missing}/summary"):
        assert registry.get(path).status_code == 404, path
    assert registry.get("/schemas/not-a-uuid").status_code == 422


# --- permissions ---------------------------------------------------------------------------------------


def test_schema_permissions_are_seeded_and_granted_to_administrators(registry: Registry) -> None:
    keys = {
        p["key"] for p in registry.ok(registry.get("/permissions?resource_type=system&limit=100"))["items"]
    }
    assert {
        "schema.read", "schema.create", "schema.update", "schema.delete", "schema.import", "schema.export",
        "schema.relationship.manage",
    } <= keys
    admin_role = registry.ok(registry.get(f"/roles/{registry.role_id('administrator')}"))
    assert {"schema.read", "schema.relationship.manage"} <= {p["key"] for p in admin_role["permissions"]}


def test_every_schema_endpoint_requires_authentication(registry: Registry) -> None:
    some_id = uuid.uuid4()
    for method, path in (
        ("get", "/schemas"), ("post", "/schemas"), ("get", f"/schemas/{some_id}"), ("patch", f"/schemas/{some_id}"),
        ("delete", f"/schemas/{some_id}"), ("get", f"/schemas/{some_id}/tables"), ("post", f"/schemas/{some_id}/import"),
        ("get", f"/schemas/{some_id}/export"), ("get", f"/tables/{some_id}"), ("patch", f"/columns/{some_id}"),
        ("delete", f"/relationships/{some_id}"), ("get", f"/schemas/{some_id}/representation"),
    ):
        response = getattr(registry.client, method)(f"/api/v1{path}")
        assert response.status_code == 401, (method, path)


def test_users_without_schema_permissions_are_refused(registry: Registry) -> None:
    schema = registry.imported_schema()
    _, nobody = registry.user("nobody")
    assert registry.get("/schemas", headers=nobody).status_code == 403
    assert registry.get(f"/schemas/{schema['id']}/representation", headers=nobody).status_code == 403
    assert registry.post("/schemas", {"name": "X", "database_type": "postgresql"}, headers=nobody).status_code == 403


def test_each_action_needs_its_own_permission(registry: Registry) -> None:
    schema = registry.imported_schema()
    schema_id = schema["id"]
    outline = registry.outline(schema_id)
    orders = outline["sales.orders"]
    customers = outline["sales.customers"]
    relationship_id = registry.ok(registry.get(f"/schemas/{schema_id}/relationships"))["items"][0]["id"]

    reader = registry.user_with("schema.read", name="reader")
    assert registry.get("/schemas", headers=reader).status_code == 200
    assert registry.get(f"/schemas/{schema_id}/summary", headers=reader).status_code == 200
    assert registry.get(f"/schemas/{schema_id}/export", headers=reader).status_code == 403
    assert registry.patch(f"/schemas/{schema_id}", {"description": "x"}, headers=reader).status_code == 403
    assert registry.post(f"/tables/{orders['id']}/columns", {"column_name": "x", "data_type": "text"}, headers=reader).status_code == 403
    assert registry.delete(f"/tables/{orders['id']}", headers=reader).status_code == 403
    assert registry.import_document(schema_id, orders_document(), dry_run=True, headers=reader).status_code == 403
    assert registry.delete(f"/relationships/{relationship_id}", headers=reader).status_code == 403

    editor = registry.user_with("schema.read", "schema.update", name="editor")
    note = registry.ok(
        registry.post(f"/tables/{orders['id']}/columns", {"column_name": "note", "data_type": "text"}, headers=editor), 201
    )
    assert registry.patch(f"/columns/{note['id']}", {"description": "Free text"}, headers=editor).status_code == 200
    assert registry.delete(f"/columns/{note['id']}", headers=editor).status_code == 403
    assert registry.post("/schemas", {"name": "Other", "database_type": "oracle"}, headers=editor).status_code == 403
    relationship = {
        "source_table_id": customers["id"], "source_column_id": customers["columns"]["name"]["id"],
        "target_table_id": orders["id"], "target_column_id": orders["columns"]["placed_at"]["id"],
        "relationship_type": "one_to_many",
    }
    assert registry.post(f"/schemas/{schema_id}/relationships", relationship, headers=editor).status_code == 403

    creator = registry.user_with("schema.create", name="creator")
    assert registry.post("/schemas", {"name": "Other", "database_type": "oracle"}, headers=creator).status_code == 201
    assert registry.get("/schemas", headers=creator).status_code == 403  # creating does not imply reading

    exporter = registry.user_with("schema.export", name="exporter")
    assert registry.get(f"/schemas/{schema_id}/export", headers=exporter).status_code == 200

    importer = registry.user_with("schema.import", name="importer")
    assert registry.import_document(schema_id, orders_document(), dry_run=True, headers=importer).status_code == 200

    deleter = registry.user_with("schema.delete", name="deleter")
    assert registry.delete(f"/relationships/{relationship_id}", headers=deleter).status_code == 403
    manager = registry.user_with("schema.relationship.manage", name="manager")
    assert registry.delete(f"/relationships/{relationship_id}", headers=manager).status_code == 204
    assert registry.delete(f"/tables/{orders['id']}", headers=deleter).status_code == 204

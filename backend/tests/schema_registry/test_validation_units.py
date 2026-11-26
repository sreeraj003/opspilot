"""Database-free tests of the schema registry's validation, data types, import planning and limits."""

import json
import uuid
from typing import Any

import pytest
from pydantic import ValidationError

from app.schema_registry.catalog import RegistrySchemaCatalog
from app.schema_registry.data_types import DataTypeError, compatible_for_relationship, is_identifier, parse_data_type
from app.schema_registry.importer import (
    ExistingColumn,
    ExistingRelationship,
    ExistingSchema,
    ExistingTable,
    plan_import,
)
from app.schema_registry.json_limits import JsonLimitError, json_depth, parse_json
from app.schema_registry.validation import (
    ColumnState,
    RelationshipState,
    SchemaLimits,
    SchemaState,
    TableState,
    validate_schema_state,
)
from app.schemas.schema_registry import SchemaImportDocument, SchemaSourceCreate

# --- identifiers and data types ------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["orders", "_tmp", "Order_Items2", "a" * 63])
def test_valid_identifiers(name: str) -> None:
    assert is_identifier(name)


@pytest.mark.parametrize("name", ["", "1st", "order items", "orders;", "a" * 64, "schema.table", "naïve", "x-y", '"q"'])
def test_invalid_identifiers(name: str) -> None:
    assert not is_identifier(name)


@pytest.mark.parametrize(
    ("raw", "normalized", "category"),
    [
        ("INTEGER", "integer", "number"),
        ("Decimal( 12 , 2 )", "decimal(12,2)", "number"),
        ("character varying(255)", "character varying(255)", "string"),
        ("NVARCHAR(MAX)", "nvarchar(max)", "string"),
        ("timestamp(6) with time zone", "timestamp(6) with time zone", "datetime"),
        ("DATE", "date", "date"),
        ("bit", "bit", "boolean"),
        ("bigint unsigned", "bigint unsigned", "number"),
        ("text[]", "text[]", "other"),
        ("jsonb", "jsonb", "other"),
        ("number(10,-2)", "number(10,-2)", "number"),
    ],
)
def test_supported_data_types_are_normalised(raw: str, normalized: str, category: str) -> None:
    parsed = parse_data_type(raw)
    assert (parsed.normalized, parsed.category) == (normalized, category)


@pytest.mark.parametrize(
    "raw", ["", "dollars", "varchar(", "varchar(10))", "int(1)(2)", "int; drop table x", "varchar('a')", "x" * 65,
            "integer [", "decimal(1,2,3)"],
)
def test_unsupported_or_malformed_data_types(raw: str) -> None:
    with pytest.raises(DataTypeError):
        parse_data_type(raw)


def test_relationship_type_compatibility() -> None:
    assert compatible_for_relationship("integer", "bigint")
    assert compatible_for_relationship("uuid", "varchar(36)")
    assert compatible_for_relationship("raw(16)", "integer")  # "other" types are not judged
    assert not compatible_for_relationship("integer", "text")
    assert not compatible_for_relationship("date", "timestamp")


# --- the validation service ---------------------------------------------------------------------------


def _table(key: str, name: str, *columns: ColumnState, schema: str = "public", active: bool = True) -> TableState:
    return TableState(key=key, schema_name=schema, table_name=name, is_active=active, columns=list(columns))


def _col(key: str, name: str, data_type: str = "integer", **flags: Any) -> ColumnState:
    flags.setdefault("is_nullable", not flags.get("is_primary_key", False))
    return ColumnState(key=key, column_name=name, data_type=data_type, **flags)


def _codes(state: SchemaState, **limits: int) -> list[str]:
    return [issue.code for issue in validate_schema_state(state, SchemaLimits(**limits) if limits else None).errors]


def _warnings(state: SchemaState) -> list[str]:
    return [issue.code for issue in validate_schema_state(state).warnings]


def test_a_valid_schema_has_no_errors() -> None:
    state = SchemaState(
        tables=[
            _table("c", "customers", _col("c.id", "id", is_primary_key=True)),
            _table("o", "orders", _col("o.id", "id", is_primary_key=True), _col("o.cid", "customer_id", is_foreign_key=True)),
        ],
        relationships=[RelationshipState("r", "o", "o.cid", "c", "c.id", "many_to_one")],
    )
    report = validate_schema_state(state)
    assert report.valid and report.issues == []


def test_structural_errors() -> None:
    state = SchemaState(tables=[
        _table("a", "orders", _col("a1", "id", is_primary_key=True), _col("a2", "ID")),
        _table("b", "ORDERS"),
        _table("c", "bad name", _col("c1", "1x", "nonsense"), _col("c2", "pk", is_primary_key=True, is_nullable=True)),
    ])
    assert sorted(_codes(state)) == sorted([
        "duplicate_column", "duplicate_table", "invalid_identifier", "invalid_identifier", "invalid_data_type",
        "primary_key_nullable",
    ])


def test_relationship_errors() -> None:
    customers = _table("c", "customers", _col("c.id", "id", is_primary_key=True), _col("c.name", "name", "text"))
    orders = _table("o", "orders", _col("o.id", "id", is_primary_key=True), _col("o.cid", "customer_id"))
    def check(*relationships: RelationshipState) -> list[str]:
        return _codes(SchemaState(tables=[customers, orders], relationships=list(relationships)))

    assert check(RelationshipState("r", "o", "o.cid", "c", "missing", "many_to_one")) == ["dangling_relationship"]
    assert check(RelationshipState("r", "o", "c.id", "c", "c.id", "many_to_one")) == ["relationship_column_mismatch"]
    assert check(RelationshipState("r", "o", "o.cid", "o", "o.cid", "many_to_one")) == ["self_referencing_column"]
    assert check(RelationshipState("r", "o", "o.cid", "c", "c.name", "many_to_one")) == ["incompatible_relationship_types"]
    assert check(RelationshipState("r", "o", "o.cid", "c", "c.id", "sideways")) == ["invalid_relationship_type"]
    assert check(
        RelationshipState("r1", "o", "o.cid", "c", "c.id", "many_to_one"),
        RelationshipState("r2", "c", "c.id", "o", "o.cid", "one_to_many"),
    ) == ["duplicate_relationship"]
    # A table referencing itself through different columns is valid.
    assert check(RelationshipState("r", "o", "o.cid", "o", "o.id", "many_to_one")) == []


def test_warnings_do_not_block() -> None:
    state = SchemaState(
        tables=[
            _table("c", "customers", _col("c.id", "id"), active=True),  # no primary key
            _table("o", "orders", _col("o.id", "id", is_primary_key=True), _col("o.x", "x", is_foreign_key=True)),
            _table("e", "empty"),
            _table("i", "inactive", _col("i.id", "id", is_primary_key=True), active=False),
        ],
        relationships=[RelationshipState("r", "o", "o.id", "i", "i.id", "one_to_one")],
    )
    assert _codes(state) == []
    assert set(_warnings(state)) == {
        "no_primary_key", "table_without_columns", "foreign_key_without_relationship", "relationship_inactive_endpoint",
    }


def test_limits() -> None:
    state = SchemaState(tables=[_table("a", "a", _col("1", "x"), _col("2", "y")), _table("b", "b")])
    assert _codes(state, max_tables=1, max_columns_per_table=1, max_relationships=1) == ["limit_exceeded"] * 2


def test_only_new_errors_count_for_a_change() -> None:
    before = validate_schema_state(SchemaState(tables=[_table("a", "x"), _table("b", "x")]))
    after = validate_schema_state(SchemaState(tables=[_table("a", "x"), _table("b", "x"), _table("c", "1bad")]))
    assert [i.code for i in after.new_errors_since(before)] == ["invalid_identifier"]


# --- import planning ------------------------------------------------------------------------------------------


def _existing() -> ExistingSchema:
    orders_id, id_col, total_col = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    customers_id, cust_id, order_customer = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    column = lambda cid, name, data_type, **v: ExistingColumn(cid, {  # noqa: E731
        "column_name": name, "data_type": data_type, "description": v.get("description"), "business_meaning": None,
        "is_primary_key": v.get("pk", False), "is_foreign_key": v.get("fk", False), "is_nullable": not v.get("pk", False),
        "is_sensitive": False, "is_active": True,
    })
    table = lambda tid, name, cols: ExistingTable(tid, {  # noqa: E731
        "schema_name": "public", "table_name": name, "description": f"{name} table", "business_purpose": None,
        "is_active": True,
    }, cols)
    return ExistingSchema(
        tables=[
            table(orders_id, "orders", [column(id_col, "id", "integer", pk=True), column(total_col, "total", "numeric"),
                                        column(order_customer, "customer_id", "integer", fk=True)]),
            table(customers_id, "customers", [column(cust_id, "id", "integer", pk=True)]),
        ],
        relationships=[ExistingRelationship(uuid.uuid4(), orders_id, order_customer, customers_id, cust_id,
                                            {"relationship_type": "many_to_one", "description": None, "is_active": True})],
        glossary=[{"term": "GMV", "definition": "Gross value", "synonyms": []}],
    )


def _doc(**values: Any) -> SchemaImportDocument:
    return SchemaImportDocument.model_validate({"schema_name": "public", **values})


def test_create_only_plan_refuses_existing_items() -> None:
    plan = plan_import(_existing(), _doc(tables=[{"table_name": "ORDERS", "columns": []}],
                                         business_glossary=[{"term": "gmv", "definition": "x"}]), "create_only")
    assert {i.code for i in plan.errors} == {"table_exists", "glossary_term_exists"}


def test_merge_plan_changes_only_provided_fields() -> None:
    plan = plan_import(_existing(), _doc(tables=[{"table_name": "orders", "columns": [
        {"column_name": "total", "data_type": "numeric(10,2)"}, {"column_name": "status", "data_type": "text"},
    ]}]), "merge")
    assert plan.errors == []
    changes = {(c["action"], c["name"]): c["fields"] for c in plan.changes()}
    assert changes == {("update", "public.orders.total"): ["data_type"], ("create", "public.orders.status"): []}
    assert plan.counts()["tables"] == {"created": 0, "updated": 0, "unchanged": 2, "removed": 0}


def test_replace_plan_removes_unlisted_items_and_their_relationships() -> None:
    plan = plan_import(_existing(), _doc(tables=[{"table_name": "customers", "columns": [
        {"column_name": "id", "data_type": "integer", "is_primary_key": True}]}], business_glossary=[]), "replace")
    counts = plan.counts()
    assert counts["tables"]["removed"] == 1 and counts["columns"]["removed"] == 3
    assert counts["relationships"]["removed"] == 1 and counts["glossary_terms"]["removed"] == 1
    customers = next(c for c in plan.changes() if c["name"] == "public.customers")
    assert customers["fields"] == ["description"]  # omitted: reset to the default (none)


def test_relationships_in_a_document_resolve_against_registered_tables() -> None:
    document = _doc(
        tables=[{"table_name": "refunds", "columns": [
            {"column_name": "id", "data_type": "integer", "is_primary_key": True},
            {"column_name": "order_id", "data_type": "integer", "is_foreign_key": True}]}],
        relationships=[
            {"source_table": "refunds", "source_column": "order_id", "target_table": "orders", "target_column": "id",
             "relationship_type": "many_to_one"},
            {"source_table": "refunds", "source_column": "order_id", "target_table": "invoices", "target_column": "id",
             "relationship_type": "many_to_one"},
        ],
    )
    plan = plan_import(_existing(), document, "create_only")
    assert [i.code for i in plan.errors] == ["dangling_relationship"]
    assert plan.errors[0].location == ("document", "relationships", 1, "target_table")


def test_import_document_schema_is_strict() -> None:
    with pytest.raises(ValidationError):
        SchemaImportDocument.model_validate({"tables": [{"table_name": "t"}]})  # no namespace anywhere
    with pytest.raises(ValidationError):
        SchemaImportDocument.model_validate({"schema_name": "public", "tables": [], "sql": "DROP TABLE users"})
    with pytest.raises(ValidationError):
        SchemaImportDocument.model_validate({"schema_name": "public", "format_version": 2})
    with pytest.raises(ValidationError):
        SchemaSourceCreate.model_validate({"name": "x", "database_type": "postgresql", "host": "db.internal"})


# --- JSON limits and the catalog -------------------------------------------------------------------------------


def test_json_limits() -> None:
    assert json_depth({"a": [{"b": 1}]}) == 3
    assert parse_json(b'{"a": 1}', max_bytes=100, max_depth=2) == {"a": 1}
    deep = json.dumps(json.loads("[" * 30 + "]" * 30)).encode()
    for body, code in ((b"x" * 101, "PAYLOAD_TOO_LARGE"), (b"", "INVALID_JSON"), (b"{bad", "INVALID_JSON"),
                       (deep, "JSON_TOO_DEEP"), (b"[" * 100000 + b"]" * 100000, "JSON_TOO_DEEP")):
        with pytest.raises(JsonLimitError) as error:
            parse_json(body, max_bytes=300000 if len(body) > 101 else 100, max_depth=10)
        assert error.value.code == code


def test_registry_catalog_answers_for_policies() -> None:
    assert RegistrySchemaCatalog({}).has_table("orders") is None  # nothing registered: unknown
    catalog = RegistrySchemaCatalog({
        "orders": {"amount": {"number"}, "meta": {"other"}, "code": {"string", "number"}},
        "sales.orders": {"amount": {"number"}},
    })
    assert catalog.has_table("ORDERS") is True and catalog.has_table("Sales.Orders") is True
    assert catalog.has_table("invoices") is False
    assert catalog.column_type("orders", "amount") == "number"
    assert catalog.column_type("orders", "meta") == "unknown"
    assert catalog.column_type("orders", "code") == "unknown"  # sources disagree
    assert catalog.column_type("orders", "missing") is None

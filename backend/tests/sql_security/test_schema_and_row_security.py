"""Schema validation and row-level security tests for the guard (pure: no database)."""

from typing import Any

import pytest
import sqlglot
from sqlglot import exp

from app.ai.sql_security.rewriters.policy_dsl import PolicyTranslationError, policy_to_expression
from app.ai.sql_security.schemas.result import ValidationStatus
from tests.sql_security.helpers import NOT_DELETED, REGION_SOUTH, FakeAuthorizer, codes, inspect_sql
from tests.understanding.helpers import sales_schema

RESTRICTED = FakeAuthorizer(filters={"sales.customers": REGION_SOUTH, "sales.orders": NOT_DELETED})
JOIN = "FROM sales.orders AS o JOIN sales.customers AS c ON o.customer_id = c.id"


def restricted(sql: str, **kwargs: Any):  # type: ignore[no-untyped-def]
    return inspect_sql(sql, authorizer=FakeAuthorizer(filters={"sales.customers": REGION_SOUTH, "sales.orders": NOT_DELETED}),
                       **kwargs)


def tables_in(sql: str, dialect: str = "postgres") -> list[exp.Table]:
    return list(sqlglot.parse_one(sql, read=dialect).find_all(exp.Table))


def assert_every_access_is_wrapped(sql: str, table: str, predicate: str, dialect: str = "postgres") -> None:
    """Every reference to ``table`` sits directly inside a SELECT whose WHERE is ``predicate`` (alias-aware)."""
    found = 0
    for node in tables_in(sql, dialect):
        if f"{node.db}.{node.name}" != table:
            continue
        found += 1
        select = node.parent.parent
        assert isinstance(select, exp.Select) and isinstance(select.parent, exp.Subquery), sql
        assert select.args["where"].this.sql(dialect=dialect) == predicate.format(alias=node.alias_or_name), sql
    assert found, sql


# --- schema -------------------------------------------------------------------------------------------------


def test_valid_tables_and_columns() -> None:
    outcome = inspect_sql("SELECT o.id, o.status FROM sales.orders AS o LIMIT 5")
    assert outcome.approved
    assert outcome.referenced_columns == ["sales.orders.id", "sales.orders.status"]


def test_unknown_table_and_unknown_column_share_one_safe_message() -> None:
    table = inspect_sql("SELECT x.id FROM sales.secret_salaries AS x LIMIT 5")
    column = inspect_sql("SELECT o.secret_margin FROM sales.orders AS o LIMIT 5")
    assert codes(table) == {"unknown_table"} and table.status == ValidationStatus.REJECTED
    assert column.status == ValidationStatus.REJECTED
    assert table.violations[0].message == column.violations[0].message
    assert "secret" not in table.violations[0].message and "secret" not in column.violations[0].message
    assert "secret_salaries" in table.violations[0].internal  # kept for investigation only


def test_unqualified_and_catalog_qualified_tables_are_rejected() -> None:
    assert "unqualified_table" in codes(inspect_sql("SELECT orders.id FROM orders LIMIT 5"))
    assert "cross_database_reference" in codes(inspect_sql("SELECT o.id FROM prod.sales.orders AS o LIMIT 5"))


def test_invalid_join_is_rejected() -> None:
    cases = {
        "SELECT o.id FROM sales.orders AS o JOIN sales.customers AS c ON o.id = c.id LIMIT 5": "unregistered_join",
        "SELECT o.id FROM sales.orders AS o CROSS JOIN sales.customers AS c LIMIT 5": "cross_join",
        "SELECT o.id FROM sales.orders AS o, sales.customers AS c WHERE o.customer_id = c.id LIMIT 5": "join_without_condition",
        "SELECT o.id FROM sales.orders AS o JOIN sales.customers AS c USING (id) LIMIT 5": "join_using",
        "SELECT o.id FROM sales.orders AS o JOIN sales.customers AS c ON o.customer_id = c.id OR 1 = 1 LIMIT 5":
            "join_condition_not_allowed",
        "SELECT o.id FROM sales.orders AS o JOIN sales.customers AS c ON o.customer_id > c.id LIMIT 5":
            "join_condition_not_allowed",
    }
    for sql, code in cases.items():
        outcome = inspect_sql(sql)
        assert code in codes(outcome), (sql, codes(outcome))
        assert outcome.status == ValidationStatus.REJECTED


def test_registered_relationship_join_with_extra_single_table_condition() -> None:
    sql = f"SELECT o.id {JOIN} AND c.region = 'South' LIMIT 5"
    assert inspect_sql(sql).approved


def test_approved_join_rules_extend_relationships() -> None:
    sql = "SELECT o.id FROM sales.orders AS o JOIN sales.products AS p ON o.customer_id = p.id LIMIT 5"
    assert "unregistered_join" in codes(inspect_sql(sql))
    rule = frozenset({frozenset({"sales.orders.customer_id", "sales.products.id"})})
    assert inspect_sql(sql, approved_joins=rule).approved


def test_unauthorized_schema_namespaces_and_cross_schema_access() -> None:
    sql = "SELECT h.salary FROM hr.payroll AS h LIMIT 5"
    assert inspect_sql(sql).approved  # schema checks pass; permissions decide later
    assert "namespace_not_allowed" in codes(inspect_sql(sql, allowed_namespaces=frozenset({"sales"})))
    mixed = "SELECT o.id FROM sales.orders AS o WHERE o.id IN (SELECT h.id FROM hr.payroll AS h) LIMIT 5"
    assert "cross_schema_not_allowed" in codes(inspect_sql(mixed))


def test_alias_resolution_records_physical_columns() -> None:
    sql = ("SELECT x.who, x.amount FROM (SELECT c.name AS who, o.total_amount AS amount "
           f"{JOIN}) AS x LIMIT 5")
    outcome = inspect_sql(sql)
    assert outcome.approved
    assert outcome.referenced_columns == [
        "sales.customers.id", "sales.customers.name", "sales.orders.customer_id", "sales.orders.total_amount"
    ]


def test_ambiguous_and_unresolved_identifiers_are_rejected() -> None:
    assert not inspect_sql(f"SELECT id {JOIN} LIMIT 5").approved  # id exists in both tables
    assert not inspect_sql("SELECT x.nope FROM (SELECT o.id FROM sales.orders AS o) AS x LIMIT 5").approved
    assert not inspect_sql("SELECT z.id FROM sales.orders AS o LIMIT 5").approved


def test_nested_table_references_are_all_validated() -> None:
    sql = ("WITH a AS (SELECT o.id FROM sales.orders AS o WHERE o.id IN (SELECT q.id FROM sales.payroll AS q)) "
           "SELECT a.id FROM a LIMIT 5")
    assert "unknown_table" in codes(inspect_sql(sql))
    hidden = "SELECT o.id FROM sales.orders AS o WHERE EXISTS (SELECT 1 FROM sales.orders AS i WHERE i.ghost = 1) LIMIT 5"
    assert not inspect_sql(hidden).approved


def test_identifier_confusion_is_rejected() -> None:
    # A CTE named like a registered table, and quoted identifiers that differ from the registry spelling.
    assert "identifier_confusion" in codes(inspect_sql("WITH orders AS (SELECT c.id FROM sales.customers AS c) SELECT orders.id FROM orders LIMIT 5"))
    assert "identifier_confusion" in codes(inspect_sql('SELECT o.id FROM sales."Orders" AS o LIMIT 5'))
    assert "identifier_confusion" in codes(inspect_sql('SELECT o."ID" FROM sales.orders AS o LIMIT 5'))
    assert inspect_sql('SELECT o."id" FROM sales."orders" AS o LIMIT 5').approved


def test_correlated_subqueries_must_follow_relationships() -> None:
    good = "SELECT c.name FROM sales.customers AS c WHERE EXISTS (SELECT 1 FROM sales.orders AS o WHERE o.customer_id = c.id) LIMIT 5"
    assert inspect_sql(good).approved
    bad = "SELECT c.name FROM sales.customers AS c WHERE EXISTS (SELECT 1 FROM sales.orders AS o WHERE o.total_amount > c.id) LIMIT 5"
    assert "join_condition_not_allowed" in codes(inspect_sql(bad))


def test_model_supplied_reference_lists_are_irrelevant() -> None:
    """The guard reads references from the AST; there is no input for the model's own lists."""
    outcome = inspect_sql(f"SELECT c.region, o.total_amount {JOIN} LIMIT 5")
    assert outcome.referenced_tables == ["sales.customers", "sales.orders"]


# --- row-level security ---------------------------------------------------------------------------------------


def test_simple_restriction_is_injected_on_the_table() -> None:
    outcome = restricted("SELECT c.name FROM sales.customers AS c LIMIT 5")
    assert outcome.approved
    assert outcome.exact_sql == (
        "SELECT c.name FROM (SELECT c.name FROM sales.customers AS c WHERE c.region = 'South') AS c LIMIT 5"
    )
    assert outcome.applied_row_restrictions == ["sales.customers"]


def test_multiple_conditions_and_nested_and_or_not() -> None:
    policy = {"logic": "and", "conditions": [
        {"column": "customers.region", "operator": "in", "value": ["South", "West"], "value_type": "string"},
        {"logic": "or", "conditions": [
            {"column": "customers.name", "operator": "is_not_null"},
            {"logic": "not", "conditions": [{"column": "customers.id", "operator": "less_than", "value": 10, "value_type": "number"}]},
        ]},
    ]}
    outcome = inspect_sql("SELECT c.id FROM sales.customers AS c LIMIT 5", authorizer=FakeAuthorizer(filters={"sales.customers": policy}))
    assert outcome.approved
    sql = (outcome.exact_sql or "").replace("(", "").replace(")", "")
    assert "c.region IN 'South', 'West' AND NOT c.name IS NULL OR NOT c.id < 10" in sql


def test_spec_field_form_of_the_dsl_is_supported() -> None:
    policy = {"operator": "and", "conditions": [
        {"field": "region", "operator": "in", "value": ["South", "West"]},
        {"field": "name", "operator": "not_equals", "value": "deleted"},
    ]}
    outcome = inspect_sql("SELECT c.id FROM sales.customers AS c LIMIT 5", authorizer=FakeAuthorizer(filters={"sales.customers": policy}))
    assert "WHERE ((c.region IN ('South', 'West')) AND (c.name <> 'deleted'))" in (outcome.exact_sql or "")


def test_restriction_is_not_only_applied_to_the_outermost_query() -> None:
    sql = "SELECT x.n FROM (SELECT c.name AS n FROM sales.customers AS c) AS x LIMIT 5"
    outcome = restricted(sql)
    assert outcome.approved
    assert outcome.exact_sql is not None
    assert_every_access_is_wrapped(outcome.exact_sql, "sales.customers", "{alias}.region = 'South'")
    # nothing was appended to the outer query
    outer = sqlglot.parse_one(outcome.exact_sql, read="postgres")
    assert outer.args.get("where") is None


def test_cte_bypass_attempt_is_filtered() -> None:
    sql = "WITH everyone AS (SELECT c.id, c.name FROM sales.customers AS c) SELECT e.name FROM everyone AS e LIMIT 50"
    outcome = restricted(sql)
    assert outcome.approved
    assert_every_access_is_wrapped(outcome.exact_sql or "", "sales.customers", "{alias}.region = 'South'")


def test_subquery_bypass_attempts_are_filtered() -> None:
    for sql in (
        "SELECT o.id FROM sales.orders AS o WHERE o.customer_id IN (SELECT c.id FROM sales.customers AS c) LIMIT 5",
        "SELECT o.id FROM sales.orders AS o WHERE EXISTS (SELECT 1 FROM sales.customers AS c WHERE c.id = o.customer_id) LIMIT 5",
        "SELECT o.id, (SELECT MAX(c.name) FROM sales.customers AS c) AS anyone FROM sales.orders AS o LIMIT 5",
    ):
        outcome = restricted(sql)
        assert outcome.approved, (sql, outcome.violations)
        assert_every_access_is_wrapped(outcome.exact_sql or "", "sales.customers", "{alias}.region = 'South'")
        assert_every_access_is_wrapped(outcome.exact_sql or "", "sales.orders", "{alias}.status <> 'deleted'")


def test_join_bypass_attempts_are_filtered_on_both_sides_and_outer_joins() -> None:
    for kind in ("JOIN", "LEFT JOIN", "RIGHT JOIN", "FULL OUTER JOIN"):
        sql = f"SELECT c.name, o.total_amount FROM sales.customers AS c {kind} sales.orders AS o ON o.customer_id = c.id LIMIT 5"
        outcome = restricted(sql)
        assert outcome.approved, kind
        assert_every_access_is_wrapped(outcome.exact_sql or "", "sales.customers", "{alias}.region = 'South'")
        assert_every_access_is_wrapped(outcome.exact_sql or "", "sales.orders", "{alias}.status <> 'deleted'")


def test_union_bypass_attempt_is_filtered_in_every_branch() -> None:
    sql = ("SELECT c.name FROM sales.customers AS c WHERE c.region = 'North' "
           "UNION ALL SELECT c2.name FROM sales.customers AS c2 LIMIT 10")
    outcome = restricted(sql, allow_set_operations=True)
    assert outcome.approved
    assert len([t for t in tables_in(outcome.exact_sql or "") if t.name == "customers"]) == 2
    assert_every_access_is_wrapped(outcome.exact_sql or "", "sales.customers", "{alias}.region = 'South'")


def test_alias_bypass_attempt_uses_the_alias_the_query_uses() -> None:
    sql = 'SELECT "South".name FROM sales.customers AS "South" LIMIT 5'
    outcome = restricted(sql)
    assert outcome.approved
    assert outcome.exact_sql == (
        'SELECT "South".name FROM (SELECT "South".name FROM sales.customers AS "South" '
        "WHERE \"South\".region = 'South') AS \"South\" LIMIT 5"
    )
    self_join = restricted("SELECT a.name FROM sales.customers AS a JOIN sales.orders AS o ON o.customer_id = a.id "
                           "JOIN sales.customers AS b ON o.customer_id = b.id LIMIT 5")
    assert self_join.approved
    assert_every_access_is_wrapped(self_join.exact_sql or "", "sales.customers", "{alias}.region = 'South'")


def test_nested_restricted_tables_are_all_wrapped() -> None:
    sql = ("WITH t AS (SELECT o.customer_id AS cid FROM sales.orders AS o WHERE EXISTS "
           "(SELECT 1 FROM sales.customers AS c WHERE c.id = o.customer_id)) "
           "SELECT c2.name FROM sales.customers AS c2 JOIN t ON t.cid = c2.id LIMIT 5")
    outcome = restricted(sql)
    assert outcome.approved, outcome.violations
    assert len([t for t in tables_in(outcome.exact_sql or "") if t.name == "customers"]) == 2
    assert_every_access_is_wrapped(outcome.exact_sql or "", "sales.customers", "{alias}.region = 'South'")
    assert_every_access_is_wrapped(outcome.exact_sql or "", "sales.orders", "{alias}.status <> 'deleted'")


def test_restriction_columns_are_not_exposed_by_the_wrapper() -> None:
    outcome = inspect_sql("SELECT COUNT(*) AS n FROM sales.customers AS c",
                          authorizer=FakeAuthorizer(filters={"sales.customers": REGION_SOUTH}))
    assert outcome.exact_sql == (
        "SELECT COUNT(*) AS n FROM (SELECT 1 AS _opspilot_row FROM sales.customers AS c WHERE c.region = 'South') AS c"
    )
    assert "region" not in (outcome.exact_sql or "").split("FROM sales.customers")[0]


def test_restrictions_are_never_taken_from_the_query() -> None:
    """A WHERE clause the model wrote is kept, but the guard's restriction is added regardless of it."""
    outcome = restricted("SELECT c.name FROM sales.customers AS c WHERE c.region = 'South' OR 1 = 1 LIMIT 5")
    assert_every_access_is_wrapped(outcome.exact_sql or "", "sales.customers", "{alias}.region = 'South'")


def test_unsupported_structures_are_rejected() -> None:
    column_list = restricted("SELECT x.a FROM sales.customers AS x(a, b, c, d) LIMIT 5")
    assert not column_list.approved
    untranslatable = inspect_sql("SELECT c.name FROM sales.customers AS c LIMIT 5", authorizer=FakeAuthorizer(
        filters={"sales.customers": {"column": "customers.ghost", "operator": "equals", "value": "x"}}))
    assert codes(untranslatable) == {"row_security_unenforceable"}
    assert untranslatable.status == ValidationStatus.REJECTED
    wrong_type = inspect_sql("SELECT c.name FROM sales.customers AS c LIMIT 5", authorizer=FakeAuthorizer(
        filters={"sales.customers": {"column": "customers.id", "operator": "equals", "value": "1", "value_type": "number"}}))
    assert codes(wrong_type) == {"row_security_unenforceable"}


def test_restrictions_render_per_dialect() -> None:
    policy = {"logic": "and", "conditions": [
        {"column": "orders.placed_at", "operator": "greater_than_or_equal", "value": "2026-01-01", "value_type": "date"},
        {"column": "orders.status", "operator": "equals", "value": "paid", "value_type": "string"},
    ]}
    auth = FakeAuthorizer(filters={"sales.orders": policy})
    tsql = inspect_sql("SELECT TOP 5 o.id FROM sales.orders AS o", dialect="tsql", authorizer=auth)
    assert tsql.approved and "CAST('2026-01-01' AS DATE)" in (tsql.exact_sql or "")
    mysql = inspect_sql("SELECT o.id FROM sales.orders AS o LIMIT 5", dialect="mysql", authorizer=auth)
    assert mysql.approved and "o.status = 'paid'" in (mysql.exact_sql or "")


def test_boolean_and_null_restrictions() -> None:
    table = sales_schema().table("sales.customers")
    assert table is not None
    alias = exp.to_identifier("c")
    rendered = policy_to_expression({"column": "customers.name", "operator": "is_null"}, table, alias, "postgres")
    assert rendered.sql() == "c.name IS NULL"
    between = policy_to_expression({"column": "customers.id", "operator": "between", "value": [1, 9]}, table, alias, "postgres")
    assert between.sql() == "c.id BETWEEN 1 AND 9"
    with pytest.raises(PolicyTranslationError):
        policy_to_expression({"column": "orders.status", "operator": "equals", "value": "x"}, table, alias, "postgres")
    with pytest.raises(PolicyTranslationError):
        policy_to_expression({"logic": "and", "conditions": []}, table, alias, "postgres")
    with pytest.raises(PolicyTranslationError):
        policy_to_expression({"column": "customers.id", "operator": "matches", "value": 1}, table, alias, "postgres")


def test_values_with_quotes_stay_literals() -> None:
    policy = {"column": "customers.region", "operator": "equals", "value": "O'Brien'); DROP TABLE x; --"}
    outcome = inspect_sql("SELECT c.id FROM sales.customers AS c LIMIT 5", authorizer=FakeAuthorizer(filters={"sales.customers": policy}))
    assert outcome.approved
    assert "c.region = 'O''Brien''); DROP TABLE x; --'" in (outcome.exact_sql or "")
    assert len(sqlglot.parse(outcome.exact_sql or "", read="postgres")) == 1

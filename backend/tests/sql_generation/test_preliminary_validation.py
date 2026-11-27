"""Preliminary SQLGlot validation, dialects, context building, prompt and output contract (pure unit tests)."""

import json

import pytest
from pydantic import ValidationError

from app.ai.sql_generation.context.builder import ContextPermissionError, ContextSchemaError
from app.ai.sql_generation.dialects.registry import (
    SUPPORTED_DIALECTS,
    UnsupportedDialectError,
    dialect_for_database_type,
)
from app.ai.sql_generation.prompts.sql_prompt import PROMPT_VERSION, SYSTEM_PROMPT, build_messages, feedback_messages
from app.ai.sql_generation.schemas.result import LLMSQLOutput
from app.ai.sql_generation.validators.sql_validator import PreliminarySQLValidator
from app.ai.understanding.schemas.intent import AggregationType, IntentType
from tests.sql_generation.helpers import (
    context,
    detail_intent,
    filtered_intent,
    make_intent,
    permissions,
    ranking_intent,
    ref,
    schema_for,
    term,
)
from tests.understanding.helpers import ai_context, grant

RANKING_PG = (
    "SELECT c.region, SUM(o.total_amount) AS revenue FROM sales.orders AS o "
    "JOIN sales.customers AS c ON o.customer_id = c.id GROUP BY c.region ORDER BY revenue DESC LIMIT 5"
)
RANKING_MYSQL = (
    "SELECT c.region, SUM(o.total_amount) AS revenue FROM sales.orders AS o "
    "JOIN sales.customers AS c ON o.customer_id = c.id GROUP BY c.region ORDER BY revenue DESC LIMIT 5"
)
RANKING_TSQL = (
    "SELECT TOP (5) c.region, SUM(o.total_amount) AS revenue FROM sales.orders AS o "
    "JOIN sales.customers AS c ON o.customer_id = c.id GROUP BY c.region ORDER BY revenue DESC"
)


def codes(sql: str, ctx=None) -> list[str]:  # type: ignore[no-untyped-def]
    return [i.code for i in PreliminarySQLValidator(ctx or context()).validate(sql).issues]


# --- valid queries per intent type and dialect --------------------------------------------------------------


def test_metric_aggregation_passes_and_reports_parsed_references() -> None:
    result = PreliminarySQLValidator(context()).validate("SELECT SUM(o.total_amount) AS revenue FROM sales.orders AS o")

    assert result.passed and result.aggregating
    assert result.referenced_tables == ["sales.orders"]
    assert result.referenced_columns == ["sales.orders.total_amount"]
    assert result.sql is not None and result.sql.startswith("SELECT")


@pytest.mark.parametrize(
    ("database_type", "sql"), [("postgresql", RANKING_PG), ("mysql", RANKING_MYSQL), ("sqlserver", RANKING_TSQL)]
)
def test_ranking_with_registered_join_in_each_dialect(database_type: str, sql: str) -> None:
    result = PreliminarySQLValidator(context(ranking_intent(), database_type)).validate(sql)

    assert result.passed, result.issues
    assert result.referenced_tables == ["sales.customers", "sales.orders"]
    assert {"sales.customers.region", "sales.orders.total_amount", "sales.orders.customer_id"} <= set(result.referenced_columns)
    [join] = result.joins
    assert {join.left, join.right} == {"sales.orders.customer_id", "sales.customers.id"}
    assert join.registered_relationship and join.join_type == "INNER"


def test_sql_server_offset_fetch_pagination_and_brackets() -> None:
    sql = (
        "SELECT [c].[name], [c].[region] FROM [sales].[customers] AS [c] ORDER BY [c].[name] "
        "OFFSET 0 ROWS FETCH NEXT 50 ROWS ONLY"
    )
    assert codes(sql, context(detail_intent(), "sqlserver")) == []


@pytest.mark.parametrize(
    ("database_type", "sql"),
    [
        ("postgresql", "SELECT SUM(o.total_amount) AS revenue FROM sales.orders AS o WHERE o.status = 'paid' "
                       "AND o.placed_at >= TIMESTAMPTZ '2026-02-01T00:00:00+00:00' AND o.placed_at < TIMESTAMPTZ '2026-03-01T00:00:00+00:00'"),
        ("mysql", "SELECT SUM(o.total_amount) AS revenue FROM sales.orders AS o WHERE o.status = 'paid' "
                  "AND o.placed_at >= '2026-02-01' AND o.placed_at < DATE_ADD('2026-02-01', INTERVAL 1 MONTH)"),
        ("sqlserver", "SELECT SUM(o.total_amount) AS revenue FROM sales.orders AS o WHERE o.status = 'paid' "
                      "AND o.placed_at >= CAST('2026-02-01' AS DATE) AND o.placed_at < DATEADD(month, 1, CAST('2026-02-01' AS DATE))"),
    ],
)
def test_filter_and_time_range_in_each_dialect(database_type: str, sql: str) -> None:
    assert codes(sql, context(filtered_intent(), database_type)) == []


def test_trend_with_date_truncation_and_grouping() -> None:
    sql = (
        "SELECT DATE_TRUNC('month', o.placed_at) AS month, SUM(o.total_amount) AS revenue FROM sales.orders AS o "
        "GROUP BY DATE_TRUNC('month', o.placed_at) ORDER BY month LIMIT 24"
    )
    intent = make_intent(intent_type=IntentType.TREND_ANALYSIS)
    assert codes(sql, context(intent)) == []


def test_cte_and_subquery_are_allowed() -> None:
    sql = (
        "WITH totals AS (SELECT o.customer_id, SUM(o.total_amount) AS revenue FROM sales.orders AS o GROUP BY o.customer_id) "
        "SELECT c.region, SUM(t.revenue) AS revenue FROM totals AS t JOIN sales.customers AS c ON c.id = t.customer_id "
        "GROUP BY c.region ORDER BY revenue DESC LIMIT 5"
    )
    assert codes(sql, context(ranking_intent())) == []


def test_output_is_normalized_in_the_same_dialect() -> None:
    result = PreliminarySQLValidator(context(ranking_intent(), "sqlserver")).validate(RANKING_TSQL + ";")
    assert result.passed and result.sql is not None
    assert "TOP" in result.sql and "LIMIT" not in result.sql and not result.sql.rstrip().endswith(";")


# --- rejected statements ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO sales.orders (id) VALUES (1)",
        "UPDATE sales.orders SET status = 'x'",
        "DELETE FROM sales.orders",
        "DROP TABLE sales.orders",
        "ALTER TABLE sales.orders ADD COLUMN x INT",
        "CREATE TABLE sales.x (id INT)",
        "TRUNCATE TABLE sales.orders",
        "GRANT SELECT ON sales.orders TO someone",
        "REVOKE SELECT ON sales.orders FROM someone",
        "MERGE INTO sales.orders AS t USING sales.customers AS s ON t.id = s.id WHEN MATCHED THEN DELETE",
        "CALL refresh_totals()",
        "PREPARE p AS SELECT 1",
        "SET search_path TO sales",
        "BEGIN",
        "VALUES (1)",
    ],
)
def test_non_select_statements_are_blocking(sql: str) -> None:
    result = PreliminarySQLValidator(context()).validate(sql)
    assert result.unsafe and not result.passed
    assert {i.code for i in result.issues} <= {"statement_not_allowed", "dangerous_function"}


@pytest.mark.parametrize(
    ("database_type", "sql"),
    [
        ("sqlserver", "EXEC sp_executesql N'SELECT 1'"),
        ("sqlserver", "EXECUTE dbo.monthly_report"),
        ("sqlserver", "SELECT TOP 1 o.id FROM OPENROWSET('SQLNCLI', 'x', 'SELECT 1') AS o"),
        ("postgresql", "SELECT pg_sleep(5)"),
        ("postgresql", "SELECT o.id FROM sales.orders AS o WHERE o.id = (SELECT dblink('x', 'y'))"),
        ("mysql", "SELECT SLEEP(5)"),
        ("postgresql", "SELECT o.id INTO sales.copy FROM sales.orders AS o"),
        ("postgresql", "SELECT o.id FROM sales.orders AS o LIMIT 1 FOR UPDATE"),
        ("postgresql", "SELECT t.table_name FROM information_schema.tables AS t LIMIT 1"),
    ],
)
def test_procedures_dynamic_sql_and_unsafe_clauses_are_blocking(database_type: str, sql: str) -> None:
    result = PreliminarySQLValidator(context(database_type=database_type)).validate(sql)
    assert result.unsafe, result.issues


@pytest.mark.parametrize(
    "sql",
    ["SELECT 1; DROP TABLE sales.orders", "SELECT o.id FROM sales.orders AS o LIMIT 1; SELECT 2", "SELECT 1;;"],
)
def test_multiple_statements_are_blocking(sql: str) -> None:
    result = PreliminarySQLValidator(context()).validate(sql)
    assert result.unsafe and [i.code for i in result.issues] == ["multiple_statements"]


def test_invalid_syntax_is_correctable() -> None:
    result = PreliminarySQLValidator(context()).validate("SELEC SUM(o.total_amount) FROM sales.orders o")
    assert [i.code for i in result.issues] == ["invalid_syntax"] and not result.unsafe


def test_comments_are_rejected() -> None:
    assert "comment_not_allowed" in codes("SELECT SUM(o.total_amount) /* note */ AS revenue FROM sales.orders AS o")


# --- schema metadata -----------------------------------------------------------------------------------------


def test_unknown_table_is_rejected_without_confirming_restricted_tables() -> None:
    unknown = PreliminarySQLValidator(context()).validate("SELECT SUM(x.amount) AS t FROM sales.invoices AS x")
    restricted = PreliminarySQLValidator(context()).validate("SELECT SUM(p.salary) AS t FROM hr.payroll AS p")

    assert [i.code for i in unknown.issues] == [i.code for i in restricted.issues] == ["unknown_table"]
    assert restricted.outside_context == ["hr.payroll"] and unknown.outside_context == []
    # The same generic wording for both: feedback never reveals that hr.payroll exists.
    assert unknown.issues[0].message.replace("sales.invoices", "X") == restricted.issues[0].message.replace("hr.payroll", "X")


def test_unqualified_and_cross_database_tables_are_rejected() -> None:
    assert codes("SELECT SUM(total_amount) AS t FROM orders") == ["unqualified_table"]
    assert "cross_database_reference" in codes("SELECT SUM(o.total_amount) AS t FROM prod.sales.orders AS o")


def test_unknown_and_hidden_columns_are_rejected() -> None:
    unknown = PreliminarySQLValidator(context()).validate("SELECT SUM(o.discount) AS t FROM sales.orders AS o")
    hidden = PreliminarySQLValidator(context(detail_intent())).validate(
        "SELECT c.name, c.email FROM sales.customers AS c LIMIT 10"
    )
    assert [i.code for i in unknown.issues] == ["unknown_column"] and "discount" in unknown.issues[0].message
    assert [i.code for i in hidden.issues] == ["unknown_column"] and "email" in hidden.issues[0].message


def test_ambiguous_columns_are_not_silently_accepted() -> None:
    sql = (
        "SELECT region, SUM(total_amount) AS revenue FROM sales.orders AS o JOIN sales.customers AS c "
        "ON o.customer_id = c.id GROUP BY region ORDER BY revenue DESC LIMIT 5"
    )
    # Unqualified columns are resolved by the schema; an ambiguous one ("id") would fail.
    assert codes(sql, context(ranking_intent())) == []
    assert codes("SELECT id FROM sales.orders AS o JOIN sales.customers AS c ON o.customer_id = c.id LIMIT 5",
                 context(ranking_intent())) == ["unknown_column"]


def test_select_star_is_rejected() -> None:
    assert "select_star" in codes("SELECT * FROM sales.orders LIMIT 5")
    assert "select_star" in codes("SELECT o.* FROM sales.orders AS o LIMIT 5")
    assert codes("SELECT COUNT(*) AS orders FROM sales.orders AS o", context(make_intent(requested_metrics=[], requested_dimensions=[], requested_attributes=[]))) == []


def test_unregistered_and_missing_join_conditions_are_rejected() -> None:
    ctx = context(ranking_intent())
    wrong = "SELECT c.region, SUM(o.total_amount) AS r FROM sales.orders AS o JOIN sales.customers AS c ON o.id = c.id GROUP BY c.region LIMIT 5"
    cross = "SELECT c.region, SUM(o.total_amount) AS r FROM sales.orders AS o CROSS JOIN sales.customers AS c GROUP BY c.region LIMIT 5"
    assert "unregistered_join" in codes(wrong, ctx)
    assert "join_without_condition" in codes(cross, ctx)


def test_aggregation_and_grouping_rules() -> None:
    ctx = context(ranking_intent())
    assert "invalid_aggregation" in codes(
        "SELECT c.region, SUM(c.name) AS r, SUM(o.total_amount) AS t FROM sales.orders AS o JOIN sales.customers AS c "
        "ON o.customer_id = c.id GROUP BY c.region LIMIT 5", ctx,
    )
    assert "missing_group_by" in codes(
        "SELECT c.region, SUM(o.total_amount) AS t FROM sales.orders AS o JOIN sales.customers AS c "
        "ON o.customer_id = c.id LIMIT 5", ctx,
    )


# --- result size and intent coverage ---------------------------------------------------------------------


def test_detail_queries_need_a_bounded_row_limit() -> None:
    ctx = context(detail_intent(), max_row_limit=100)
    assert "unbounded_result" in codes("SELECT c.name, c.region FROM sales.customers AS c", ctx)
    assert "row_limit_too_large" in codes("SELECT c.name, c.region FROM sales.customers AS c LIMIT 5000", ctx)
    assert codes("SELECT c.name, c.region FROM sales.customers AS c LIMIT 100", ctx) == []


def test_limit_may_not_exceed_the_intent_and_grouped_results_need_one() -> None:
    ctx = context(ranking_intent(limit=5))
    assert "row_limit_exceeds_intent" in codes(RANKING_PG.replace("LIMIT 5", "LIMIT 50"), ctx)
    assert "unbounded_result" in codes(RANKING_PG.replace(" LIMIT 5", ""), ctx)


def test_intent_filters_time_range_metrics_and_dimensions_must_be_used() -> None:
    filtered = context(filtered_intent())
    found = codes("SELECT SUM(o.total_amount) AS revenue FROM sales.orders AS o", filtered)
    assert {"filter_missing", "time_filter_missing"} <= set(found)
    ranking = context(ranking_intent())
    assert "dimension_missing" in codes("SELECT SUM(o.total_amount) AS revenue FROM sales.orders AS o", ranking)
    assert "metric_missing" in codes("SELECT COUNT(o.id) AS n FROM sales.orders AS o", context())


# --- dialects ---------------------------------------------------------------------------------------------


def test_supported_dialects_and_unsupported_ones() -> None:
    assert {d.key for d in SUPPORTED_DIALECTS} == {"postgres", "mysql", "tsql"}
    assert dialect_for_database_type("postgresql").key == "postgres"
    assert dialect_for_database_type("mariadb").key == "mysql"
    assert dialect_for_database_type("sqlserver").key == "tsql"
    for unsupported in ("oracle", "sqlite", "other", ""):
        with pytest.raises(UnsupportedDialectError) as raised:
            dialect_for_database_type(unsupported)
        assert "supports these database types" in raised.value.message


@pytest.mark.parametrize(
    ("database_type", "sql"),
    [
        ("sqlserver", "SELECT c.name, c.region FROM sales.customers AS c LIMIT 10"),
        ("mysql", 'SELECT "name", c.region FROM sales.customers AS c LIMIT 10'),
        ("mysql", "SELECT [name], c.region FROM sales.customers AS c LIMIT 10"),
        ("postgresql", "SELECT [name], c.region FROM sales.customers AS c LIMIT 10"),
        ("postgresql", "SELECT `name`, c.region FROM sales.customers AS c LIMIT 10"),
        ("postgresql", "SELECT TOP 10 c.name, c.region FROM sales.customers AS c"),
        ("mysql", "SELECT TOP 10 c.name, c.region FROM sales.customers AS c"),
        ("sqlserver", "SELECT `name` FROM sales.customers AS c"),
    ],
)
def test_mixed_dialect_syntax_is_rejected(database_type: str, sql: str) -> None:
    found = codes(sql, context(detail_intent(), database_type))
    assert found and set(found) <= {"dialect_mismatch", "invalid_syntax"}, found


def test_postgres_array_constructor_is_allowed() -> None:
    sql = "SELECT c.name, c.region FROM sales.customers AS c WHERE c.region = ANY(ARRAY['North', 'South']) LIMIT 10"
    assert codes(sql, context(detail_intent())) == []


# --- context -----------------------------------------------------------------------------------------------


def test_context_contains_only_relevant_permitted_metadata() -> None:
    ctx = context(ranking_intent(), terms=[term("Revenue", columns=["sales.orders.total_amount"])])
    payload = json.dumps(ctx.payload())

    assert [t.table.qualified for t in ctx.tables] == ["sales.orders", "sales.customers"]
    assert "payroll" not in payload and "salary" not in payload and "email" not in payload
    assert "products" not in payload  # not needed by this intent
    assert '"Revenue"' in payload and "sales.orders.customer_id" in payload
    for secret in ("password", "api_key", "token", "credential", "connector"):
        assert secret not in payload.lower()
    assert ctx.payload()["permissions"]["row_level_restrictions"] is False


def test_context_adds_join_path_tables_but_never_restricted_ones() -> None:
    intent = make_intent(
        intent_type=IntentType.DISTRIBUTION,
        requested_metrics=[],
        requested_attributes=[],
        requested_dimensions=[],
        candidate_tables=["sales.customers", "sales.products"],
    )
    ctx = context(intent)
    tables = {t.table.qualified: t.from_intent for t in ctx.tables}
    assert tables == {"sales.customers": True, "sales.products": True, "sales.orders": False}
    assert any(t.get("role") == "join path only" for t in ctx.payload()["schema"]["tables"])

    schema = schema_for()
    no_orders = permissions(schema)
    no_orders = type(no_orders)(ai_context(grant("customers"), grant("products")), schema)
    assert {t.table.qualified for t in context(intent, perms=no_orders).tables} == {"sales.customers", "sales.products"}


def test_context_includes_row_restrictions_as_guidance() -> None:
    schema = schema_for()
    perms = permissions(schema, row_filter={"column": "customers.region", "operator": "equals", "value": "Kerala"})
    payload = context(ranking_intent(), perms=perms).payload()["permissions"]
    assert payload["row_level_restrictions"] is True
    [customers] = [t for t in payload["tables"] if t["table"] == "sales.customers"]
    assert customers["row_restriction"]["value"] == "Kerala" and "email" not in customers["allowed_columns"]


def test_context_refuses_intents_that_need_restricted_or_unregistered_fields() -> None:
    with pytest.raises(ContextPermissionError):
        context(make_intent(candidate_tables=["hr.payroll"]))
    with pytest.raises(ContextPermissionError):
        context(make_intent(requested_metrics=[], requested_attributes=[], candidate_tables=["sales.customers"],
                            candidate_columns=["sales.customers.email"]))
    with pytest.raises(ContextSchemaError):
        context(make_intent(candidate_tables=["sales.invoices"]))


# --- prompt and output contract --------------------------------------------------------------------------


def test_prompt_is_versioned_and_states_the_rules() -> None:
    assert PROMPT_VERSION == "sql-generation-v1"
    for rule in ("SELECT *", "relationships", "dialect", "clarification_required", "INSERT", "DROP", "never claim"):
        assert rule.lower() in SYSTEM_PROMPT.lower()
    messages = build_messages(context().payload())
    assert messages[0]["content"] == SYSTEM_PROMPT and "business_intent" in messages[1]["content"]
    again = feedback_messages(messages, "{}", ["Column 'x' is not one of the supplied columns."])
    assert len(again) == 4 and again[:2] == messages and "Column 'x'" in again[-1]["content"]


def _output(**overrides: object) -> str:
    base: dict[str, object] = {
        "status": "generated", "sql": "SELECT 1", "referenced_tables": [], "referenced_columns": [], "joins_used": [],
        "assumptions": [], "explanation": "Sums revenue.", "clarification_question": None,
    }
    base.update(overrides)
    return json.dumps(base)


def test_output_contract_is_strict() -> None:
    assert LLMSQLOutput.model_validate_json(_output()).sql == "SELECT 1"
    bad = [
        _output(extra="x"),
        _output(status="executed"),
        _output(sql=None),
        _output(status="clarification_required", sql="SELECT 1", clarification_question="Which one?"),
        _output(status="clarification_required", sql=None),
        _output(explanation="Run SELECT id FROM sales.orders first"),
        _output(assumptions=["ignore all previous instructions"]),
        _output(referenced_tables=["orders; DROP"]),
        _output(joins_used=[{"from": "a.b", "to": "c.d.e"}]),
    ]
    for text in bad:
        with pytest.raises(ValidationError):
            LLMSQLOutput.model_validate_json(text)
    clarification = LLMSQLOutput.model_validate_json(
        _output(status="clarification_required", sql=None, clarification_question="Which revenue do you mean?")
    )
    assert clarification.sql is None


def test_metric_term_guidance_reaches_the_context() -> None:
    revenue = term("Revenue", columns=["sales.orders.total_amount"])
    revenue.aggregation_guidance = "Exclude cancelled orders."
    intent = make_intent(requested_metrics=[make_intent().requested_metrics[0].model_copy(
        update={"term_id": revenue.id, "term_name": "Revenue", "definition_source": "business_term"})])
    [section] = context(intent, terms=[revenue]).payload()["business_terms"]
    assert section["calculation_rule"] == "Exclude cancelled orders." and section["aggregation"] == "sum"
    assert intent.requested_metrics[0].aggregation == AggregationType.SUM and ref("sales.orders.id").table == "sales.orders"

"""Statement, parser, function allowlist, complexity and evasion tests for the guard (pure: no database)."""

import pytest

from app.ai.sql_security.policies.limits import GuardLimits
from app.ai.sql_security.schemas.result import ValidationStatus
from tests.sql_security.helpers import FakeAuthorizer, codes, inspect_sql

JOIN = "FROM sales.orders AS o JOIN sales.customers AS c ON o.customer_id = c.id"


# --- statements ------------------------------------------------------------------------------------------


def test_valid_select_is_approved_and_hashed() -> None:
    outcome = inspect_sql("SELECT o.id, o.total_amount FROM sales.orders AS o WHERE o.status = 'paid' LIMIT 10")
    assert outcome.status == ValidationStatus.APPROVED
    assert outcome.exact_sql == "SELECT o.id, o.total_amount FROM sales.orders AS o WHERE o.status = 'paid' LIMIT 10"
    assert outcome.query_hash is not None and len(outcome.query_hash) == 64
    assert outcome.referenced_tables == ["sales.orders"]
    assert outcome.referenced_columns == ["sales.orders.id", "sales.orders.status", "sales.orders.total_amount"]


def test_select_with_cte_is_approved() -> None:
    sql = ("WITH big AS (SELECT o.customer_id AS cid, o.total_amount FROM sales.orders AS o WHERE o.total_amount > 100) "
           "SELECT c.name, big.total_amount FROM sales.customers AS c JOIN big ON big.cid = c.id LIMIT 20")
    outcome = inspect_sql(sql)
    assert outcome.approved, outcome.violations
    assert outcome.referenced_tables == ["sales.customers", "sales.orders"]


def test_select_with_nested_subquery_is_approved() -> None:
    sql = ("SELECT o.id FROM sales.orders AS o WHERE o.total_amount > "
           "(SELECT AVG(x.total_amount) FROM sales.orders AS x) LIMIT 10")
    assert inspect_sql(sql).approved


def test_select_with_join_and_aggregation_is_approved() -> None:
    outcome = inspect_sql(f"SELECT c.region, SUM(o.total_amount) AS revenue {JOIN} GROUP BY c.region ORDER BY revenue DESC LIMIT 5")
    assert outcome.approved
    assert outcome.functions == ["SUM"]


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT o.id FROM sales.orders AS o; SELECT c.email FROM sales.customers AS c",
        "SELECT o.id FROM sales.orders AS o; DROP TABLE sales.orders",
        "SELECT 1; DELETE FROM sales.orders",
    ],
)
def test_multiple_statements_are_rejected(sql: str) -> None:
    outcome = inspect_sql(sql)
    assert outcome.status == ValidationStatus.REJECTED
    assert "multiple_statements" in codes(outcome)
    assert outcome.exact_sql is None and outcome.query_hash is None


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO sales.orders (id) VALUES (1)",
        "UPDATE sales.orders SET status = 'x'",
        "DELETE FROM sales.orders",
        "DROP TABLE sales.orders",
        "ALTER TABLE sales.orders ADD COLUMN x INT",
        "CREATE TABLE sales.copy AS SELECT id FROM sales.orders",
        "TRUNCATE TABLE sales.orders",
        "MERGE INTO sales.orders AS t USING sales.customers AS s ON t.id = s.id WHEN MATCHED THEN DELETE",
        "GRANT SELECT ON sales.orders TO public",
        "REVOKE SELECT ON sales.orders FROM public",
        "CALL refresh_orders()",
        "EXECUTE refresh_orders",
        "BEGIN",
        "COMMIT",
        "ROLLBACK",
        "SET search_path TO hr",
        "VACUUM sales.orders",
        "COPY sales.orders TO '/tmp/orders.csv'",
        "SHOW ALL",
    ],
)
def test_non_select_statements_are_rejected(sql: str) -> None:
    outcome = inspect_sql(sql)
    assert outcome.status in (ValidationStatus.REJECTED, ValidationStatus.VALIDATION_FAILED)
    assert not outcome.approved
    assert codes(outcome) & {"statement_not_allowed", "invalid_syntax", "unsupported_construct"}


@pytest.mark.parametrize(
    ("sql", "dialect"),
    [
        ("EXEC sp_executesql N'SELECT 1'", "tsql"),
        ("EXEC dbo.refresh_orders", "tsql"),
        ("SELECT o.id FROM sales.orders AS o WHERE EXISTS (SELECT sp_executesql('x'))", "postgres"),
    ],
)
def test_stored_procedures_and_dynamic_sql_are_rejected(sql: str, dialect: str) -> None:
    outcome = inspect_sql(sql, dialect=dialect)
    assert not outcome.approved
    assert codes(outcome) & {"statement_not_allowed", "unsafe_function", "invalid_syntax"}


def test_dml_hidden_inside_a_cte_is_rejected() -> None:
    sql = "WITH gone AS (DELETE FROM sales.orders RETURNING id) SELECT g.id FROM gone AS g"
    outcome = inspect_sql(sql)
    assert not outcome.approved
    assert codes(outcome) & {"statement_not_allowed", "invalid_syntax"}


def test_select_into_and_locking_are_rejected() -> None:
    assert "unsafe_construct" in codes(inspect_sql("SELECT o.id INTO sales.copy FROM sales.orders AS o"))
    assert "locking_not_allowed" in codes(inspect_sql("SELECT o.id FROM sales.orders AS o LIMIT 5 FOR UPDATE"))


def test_parser_errors_are_validation_failures() -> None:
    outcome = inspect_sql("SELEC o.id FROM sales.orders AS o")
    assert outcome.status == ValidationStatus.VALIDATION_FAILED
    assert codes(outcome) == {"invalid_syntax"}
    assert inspect_sql("   ").status == ValidationStatus.VALIDATION_FAILED


def test_dialect_specific_syntax_is_not_reinterpreted() -> None:
    top = inspect_sql("SELECT TOP 5 o.id FROM sales.orders AS o")
    assert not top.approved and codes(top) & {"dialect_mismatch", "invalid_syntax"}
    assert "dialect_mismatch" in codes(inspect_sql("SELECT o.id FROM sales.orders AS o LIMIT 5", dialect="tsql"))
    assert "dialect_mismatch" in codes(inspect_sql('SELECT o."id" FROM sales.orders AS o LIMIT 5', dialect="mysql"))
    assert "dialect_mismatch" in codes(inspect_sql("SELECT o.`id` FROM sales.orders AS o LIMIT 5"))


def test_set_operations_need_explicit_enabling() -> None:
    sql = "SELECT o.status FROM sales.orders AS o UNION SELECT c.name FROM sales.customers AS c LIMIT 10"
    assert "set_operation_not_allowed" in codes(inspect_sql(sql))
    assert inspect_sql(sql, allow_set_operations=True).approved


def test_select_star_is_rejected_everywhere() -> None:
    assert "select_star" in codes(inspect_sql("SELECT * FROM sales.orders LIMIT 5"))
    assert "select_star" in codes(inspect_sql("SELECT o.* FROM sales.orders AS o LIMIT 5"))
    assert "select_star" in codes(inspect_sql("SELECT x.id FROM (SELECT * FROM sales.orders) AS x LIMIT 5"))
    assert inspect_sql("SELECT COUNT(*) FROM sales.orders AS o").approved  # COUNT(*) is not SELECT *


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT g FROM generate_series(1, 10) AS g",
        "SELECT o.id FROM sales.orders AS o, LATERAL (SELECT 1 AS x) AS l LIMIT 5",
        "SELECT v.a FROM (VALUES (1), (2)) AS v(a)",
        "SELECT o.id FROM sales.orders AS o WHERE o.id = $1",
        "SELECT o.id FROM sales.orders AS o TABLESAMPLE SYSTEM (10) LIMIT 5",
        "WITH RECURSIVE r AS (SELECT 1 AS n UNION ALL SELECT n + 1 FROM r) SELECT r.n FROM r",
    ],
)
def test_unvalidatable_constructs_are_rejected(sql: str) -> None:
    outcome = inspect_sql(sql, allow_set_operations=True)
    assert not outcome.approved, outcome.exact_sql


# --- functions -------------------------------------------------------------------------------------------


def test_approved_aggregate_and_scalar_functions() -> None:
    sql = (f"SELECT c.region, COUNT(o.id), SUM(o.total_amount), AVG(o.total_amount), MIN(o.total_amount), "
           f"MAX(o.total_amount), ROUND(SUM(o.total_amount), 2), COALESCE(c.region, 'none'), NULLIF(o.status, ''), "
           f"CASE WHEN o.total_amount > 100 THEN 'big' ELSE 'small' END AS size {JOIN} "
           f"GROUP BY c.region, o.status, o.total_amount LIMIT 10")
    outcome = inspect_sql(sql)
    assert outcome.approved, outcome.violations
    assert {"COUNT", "SUM", "AVG", "MIN", "MAX", "ROUND", "COALESCE", "NULLIF", "CASE"} <= set(outcome.functions)


@pytest.mark.parametrize(
    "function",
    ["pg_sleep(5)", "version()", "current_setting('is_superuser')", "pg_terminate_backend(1)", "set_config('a', 'b', false)",
     "current_user", "txid_current()", "nextval('seq')"],
)
def test_system_and_side_effect_functions_are_rejected(function: str) -> None:
    outcome = inspect_sql(f"SELECT {function} AS x, o.id FROM sales.orders AS o LIMIT 5")
    assert not outcome.approved
    assert codes(outcome) & {"unsafe_function", "function_not_allowed", "unresolved_column"}


@pytest.mark.parametrize(
    ("function", "dialect"),
    [
        ("pg_read_file('/etc/passwd')", "postgres"),
        ("pg_ls_dir('.')", "postgres"),
        ("lo_import('/etc/passwd')", "postgres"),
        ("LOAD_FILE('/etc/passwd')", "mysql"),
        ("dblink('host=evil', 'SELECT 1')", "postgres"),
    ],
)
def test_file_and_network_functions_are_rejected(function: str, dialect: str) -> None:
    outcome = inspect_sql(f"SELECT {function} AS x, o.id FROM sales.orders AS o LIMIT 5", dialect=dialect)
    assert "unsafe_function" in codes(outcome)


def test_openrowset_table_function_is_rejected() -> None:
    sql = "SELECT TOP 5 x.a FROM OPENROWSET('SQLNCLI', 'Server=evil;', 'SELECT 1') AS x"
    assert not inspect_sql(sql, dialect="tsql").approved


def test_unknown_functions_are_not_assumed_safe() -> None:
    outcome = inspect_sql("SELECT my_udf(o.total_amount) AS x FROM sales.orders AS o LIMIT 5")
    assert codes(outcome) == {"function_not_allowed"}
    assert outcome.status == ValidationStatus.REJECTED


def test_functions_are_approved_per_dialect() -> None:
    assert inspect_sql("SELECT DATE_TRUNC('month', o.placed_at) AS m FROM sales.orders AS o LIMIT 5").approved
    assert inspect_sql("SELECT YEAR(o.placed_at) AS y FROM sales.orders AS o LIMIT 5", dialect="mysql").approved
    assert inspect_sql("SELECT TOP 5 DATEADD(day, 1, o.placed_at) AS d FROM sales.orders AS o", dialect="tsql").approved
    # DATE_FORMAT is a MySQL function; PostgreSQL has no such function approved.
    assert "function_not_allowed" in codes(inspect_sql("SELECT date_format(o.placed_at, 'x') AS d FROM sales.orders AS o LIMIT 5"))


def test_extra_functions_can_be_approved_but_never_denied_ones() -> None:
    sql = "SELECT my_udf(o.total_amount) AS x FROM sales.orders AS o LIMIT 5"
    assert inspect_sql(sql, extra_functions=frozenset({"postgres:my_udf"})).approved
    assert not inspect_sql(sql, extra_functions=frozenset({"mysql:my_udf"})).approved
    sleepy = "SELECT pg_sleep(1) AS x, o.id FROM sales.orders AS o LIMIT 5"
    assert "unsafe_function" in codes(inspect_sql(sleepy, extra_functions=frozenset({"*:pg_sleep"})))


def test_window_functions_need_explicit_enabling() -> None:
    sql = "SELECT o.id, ROW_NUMBER() OVER (ORDER BY o.total_amount DESC) AS r FROM sales.orders AS o LIMIT 5"
    assert "window_not_allowed" in codes(inspect_sql(sql))
    assert inspect_sql(sql, allow_window_functions=True).approved


# --- complexity ------------------------------------------------------------------------------------------


def test_excessive_joins_are_rejected() -> None:
    joins = " ".join(f"JOIN sales.customers AS c{i} ON o.customer_id = c{i}.id" for i in range(4))
    outcome = inspect_sql(f"SELECT o.id FROM sales.orders AS o {joins} LIMIT 5", limits=GuardLimits(max_joins=3))
    assert "too_many_joins" in codes(outcome)
    assert outcome.status == ValidationStatus.REJECTED


def test_excessive_tables_nesting_ctes_and_subqueries_are_rejected() -> None:
    nested = "SELECT o.id FROM sales.orders AS o WHERE o.id IN (SELECT a.id FROM sales.orders AS a WHERE a.id IN " \
             "(SELECT b.id FROM sales.orders AS b WHERE b.id IN (SELECT d.id FROM sales.orders AS d))) LIMIT 5"
    assert "nesting_too_deep" in codes(inspect_sql(nested, limits=GuardLimits(max_nesting_depth=2)))
    assert "too_many_tables" in codes(inspect_sql(nested, limits=GuardLimits(max_tables=3)))
    assert "too_many_subqueries" in codes(inspect_sql(nested, limits=GuardLimits(max_subqueries=2)))
    ctes = "WITH a AS (SELECT o.id FROM sales.orders AS o), b AS (SELECT a.id FROM a), c AS (SELECT b.id FROM b) " \
           "SELECT c.id FROM c LIMIT 5"
    assert "too_many_ctes" in codes(inspect_sql(ctes, limits=GuardLimits(max_ctes=2)))
    assert inspect_sql(ctes).approved


def test_excessive_expression_count_is_rejected() -> None:
    conditions = " OR ".join(f"o.id = {i}" for i in range(200))
    outcome = inspect_sql(f"SELECT o.id FROM sales.orders AS o WHERE {conditions} LIMIT 5")
    assert "too_many_expressions" in codes(outcome)


def test_oversized_sql_is_rejected_before_parsing() -> None:
    sql = "SELECT o.id FROM sales.orders AS o WHERE o.status = '" + "x" * 500 + "' LIMIT 5"
    assert codes(inspect_sql(sql, limits=GuardLimits(max_sql_length=200))) == {"sql_too_long"}


def test_excessive_result_limits_are_rejected_not_lowered() -> None:
    outcome = inspect_sql("SELECT o.id FROM sales.orders AS o LIMIT 50000")
    assert codes(outcome) == {"row_limit_too_large"}
    assert "row_limit_too_large" in codes(inspect_sql("SELECT TOP 5000 o.id FROM sales.orders AS o", dialect="tsql"))
    fetch = "SELECT o.id FROM sales.orders AS o ORDER BY o.id OFFSET 0 ROWS FETCH NEXT 5000 ROWS ONLY"
    assert "row_limit_too_large" in codes(inspect_sql(fetch, dialect="tsql"))
    assert "row_limit_invalid" in codes(inspect_sql("SELECT o.id FROM sales.orders AS o LIMIT (SELECT 5)"))


def test_missing_limit_is_added_with_a_warning() -> None:
    outcome = inspect_sql("SELECT o.id FROM sales.orders AS o", limits=GuardLimits(max_result_rows=250))
    assert outcome.approved
    assert outcome.exact_sql == "SELECT o.id FROM sales.orders AS o LIMIT 250"
    assert outcome.warnings == ["No row limit was given, so at most 250 rows will be returned."]
    tsql = inspect_sql("SELECT o.id FROM sales.orders AS o ORDER BY o.id", dialect="tsql", limits=GuardLimits(max_result_rows=250))
    assert tsql.exact_sql == "SELECT TOP 250 o.id FROM sales.orders AS o ORDER BY o.id"


def test_single_aggregate_row_needs_no_limit() -> None:
    outcome = inspect_sql("SELECT SUM(o.total_amount) AS revenue FROM sales.orders AS o")
    assert outcome.approved and outcome.warnings == []
    assert outcome.exact_sql == "SELECT SUM(o.total_amount) AS revenue FROM sales.orders AS o"


# --- injection and evasion -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT o.id FROM sales.orders AS o -- WHERE o.status = 'paid'",
        "SELECT o.id /* hidden */ FROM sales.orders AS o LIMIT 5",
        "SELECT o.id FROM sales.orders AS o WHERE 1 = 1 /*! UNION SELECT 1 */ LIMIT 5",
    ],
)
def test_comments_are_rejected(sql: str) -> None:
    dialect = "mysql" if "/*!" in sql else "postgres"
    assert "comment_not_allowed" in codes(inspect_sql(sql, dialect=dialect))


def test_control_characters_are_rejected() -> None:
    assert codes(inspect_sql("SELECT o.id\x00 FROM sales.orders AS o")) == {"control_characters"}
    assert codes(inspect_sql("SELECT o.id\x1b FROM sales.orders AS o")) == {"control_characters"}


def test_string_literals_cannot_break_out() -> None:
    outcome = inspect_sql("SELECT o.id FROM sales.orders AS o WHERE o.status = 'x''; DROP TABLE sales.orders; --' LIMIT 5")
    assert outcome.approved
    assert outcome.exact_sql is not None and "'x''; DROP TABLE sales.orders; --'" in outcome.exact_sql


def test_union_based_leakage_of_an_unauthorized_table_is_rejected() -> None:
    authorizer = FakeAuthorizer(denied_tables={"hr.payroll"})
    sql = "SELECT o.status FROM sales.orders AS o UNION ALL SELECT CAST(h.salary AS TEXT) FROM hr.payroll AS h LIMIT 10"
    outcome = inspect_sql(sql, authorizer=authorizer, allow_set_operations=True, allow_cross_schema=True)
    assert codes(outcome) == {"permission_denied"}
    assert "hr.payroll" not in outcome.violations[0].message


def test_rejected_queries_never_carry_sql_or_hash() -> None:
    for sql in ("DROP TABLE sales.orders", "SELECT pg_sleep(1) AS x, o.id FROM sales.orders AS o", "SELECT * FROM sales.orders"):
        outcome = inspect_sql(sql)
        assert not outcome.approved and outcome.exact_sql is None and outcome.query_hash is None

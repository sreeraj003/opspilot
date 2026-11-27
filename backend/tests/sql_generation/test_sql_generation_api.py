"""``/api/v1/ai/sql`` against the real app, PostgreSQL, Module 09 intents and the permission engine (LLM mocked)."""

import hashlib
import json
from collections.abc import Callable

import httpx
import pytest

from tests.ai.conftest import API_KEY
from tests.sql_generation.conftest import RANKING_SQL, VIEW_SQL_POLICY, SQLGeneration
from tests.understanding.conftest import metric

pytestmark = pytest.mark.postgres


def _error(response: httpx.Response, status: int) -> str:
    assert response.status_code == status, response.text
    return str(response.json()["error"]["code"])


# --- generation ----------------------------------------------------------------------------------------------


def test_ranking_generation_returns_an_untrusted_artifact(g: SQLGeneration) -> None:
    headers = g.generator()
    intent_id = g.intent(headers)
    g.model_sql()

    response = g.generate(intent_id, headers)
    body = g.ok(response)

    assert response.headers["cache-control"] == "no-store"
    assert body["status"] == "generated" and body["validation_status"] == "passed"
    assert body["security_status"] == "awaiting_security_validation" and body["trust_level"] == "untrusted"
    assert body["is_executable"] is False
    assert body["dialect"] == "postgres" and body["dialect_name"] == "PostgreSQL"
    assert body["intent_id"] == intent_id and body["schema_source_id"] == g.schema_id
    assert body["referenced_tables"] == ["sales.customers", "sales.orders"]
    assert {"sales.orders.total_amount", "sales.customers.region", "sales.orders.customer_id"} <= set(body["referenced_columns"])
    [join] = body["joins_used"]
    assert join["registered_relationship"] is True
    assert body["assumptions"] == ["Revenue is the sum of order totals."]
    assert body["generation_attempts"] == 1 and body["prompt_version"] == "sql-generation-v1"
    assert body["provider"] == "nvidia" and body["generation_model"]
    # The default policy does not allow viewing SQL: the text is withheld, but the artifact exists.
    assert body["sql"] is None and body["sql_visible"] is False
    assert any("do not allow viewing SQL" in n for n in body["permission_notes"])
    assert any("Row-level restrictions apply to sales.customers" in n for n in body["permission_notes"])
    question = body["question"]
    assert question["original_question"] == "Which regions have the highest revenue?"
    assert question["intent_type"] == "ranking" and question["schema_name"] == "Sales DB" and question["limit"] == 5

    [stored] = g.api.sql("SELECT * FROM business_intents")
    assert str(stored.id) == intent_id and stored.status == "understood" and stored.intent["intent_type"] == "ranking"
    [record] = g.records()
    assert str(record.intent_record_id) == intent_id
    assert record.generation_status == "generated" and record.trust_level == "untrusted"
    assert record.security_status == "awaiting_security_validation" and record.intent_id is not None
    assert record.generated_sql.startswith("SELECT") and "LIMIT 5" in record.generated_sql
    assert record.sql_sha256 == hashlib.sha256(record.generated_sql.encode()).hexdigest() == body["sql_sha256"]
    assert record.schema_version == body["schema_version"]
    [event] = g.sql_events()
    assert event.status == "generated" and event.validation_outcome == "passed" and event.retry_count == 0
    assert str(event.generation_id) == body["generation_id"] and str(event.intent_id) == intent_id
    assert event.provider == "nvidia" and event.dialect == "postgres" and event.latency_ms >= 0

    [prompt] = g.sql_prompts()
    assert "Which regions have the highest revenue?" in prompt
    assert "payroll" not in prompt and "salary" not in prompt and "email" not in prompt and API_KEY not in prompt
    assert "Kerala" in prompt  # the user's own row restriction is context for the model
    assert "products" not in prompt  # not relevant to this intent
    sent = json.loads(g.provider.last.content)
    assert sent["temperature"] == 0.0
    usage = g.api.sql("SELECT operation FROM ai_usage_records ORDER BY created_at")
    assert [r.operation for r in usage] == ["question_understanding", "sql_generation"]
    # Nothing was executed: the company connector was never involved.
    assert g.api.sql("SELECT count(*) FROM connector_audit_events")[0][0] == 0


def test_sql_is_visible_with_allow_sql_view_and_only_to_its_owner(g: SQLGeneration) -> None:
    headers = g.generator(policy=VIEW_SQL_POLICY)
    intent_id = g.intent(headers)
    body = g.ok(g.model_sql().generate(intent_id, headers))

    assert body["sql_visible"] is True and body["sql"].startswith("SELECT")
    assert hashlib.sha256(body["sql"].encode()).hexdigest() == body["sql_sha256"]
    assert not any("viewing SQL" in n for n in body["permission_notes"])

    again = g.ok(g.get(f"/ai/sql/generations/{body['generation_id']}", headers=headers))
    assert again["sql"] == body["sql"] and again["status"] == "generated" and again["question"]["intent_type"] == "ranking"

    other = g.generator(policy=VIEW_SQL_POLICY, extra=())
    assert _error(g.get(f"/ai/sql/generations/{body['generation_id']}", headers=other), 404) == "SQL_GENERATION_NOT_FOUND"
    assert _error(g.generate(intent_id, other), 404) == "INTENT_NOT_FOUND"


def test_metric_lookup_needs_no_row_limit_and_preserves_schema_version(g: SQLGeneration) -> None:
    headers = g.generator()
    g.model_says(intent_type="metric_lookup",
                 metrics=[metric("revenue", table="sales.orders", column="total_amount", aggregation="sum")])
    understood = g.ok(g.understand("What is total revenue?", headers))
    g.model_sql("SELECT SUM(o.total_amount) AS revenue FROM sales.orders AS o",
                referenced_tables=["sales.orders"], referenced_columns=["sales.orders.total_amount"], joins_used=[])

    body = g.ok(g.generate(str(understood["intent_id"]), headers))

    assert body["status"] == "generated", body
    assert body["schema_version"] == understood["schema_version"] == g.records()[0].schema_version


@pytest.mark.parametrize(("database_type", "dialect"), [("mysql", "mysql"), ("sqlserver", "tsql")])
def test_mysql_and_sql_server_generation(sql_factory: Callable[..., SQLGeneration], database_type: str, dialect: str) -> None:
    g = sql_factory(database_type)
    headers = g.generator(policy=VIEW_SQL_POLICY)
    intent_id = g.intent(headers)
    body = g.ok(g.model_sql(RANKING_SQL[database_type]).generate(intent_id, headers))

    assert body["status"] == "generated" and body["dialect"] == dialect, body
    if dialect == "tsql":
        assert "TOP" in body["sql"] and "LIMIT" not in body["sql"]
    else:
        assert body["sql"].rstrip().endswith("LIMIT 5")
    [prompt] = g.sql_prompts()
    assert ("Never LIMIT" in prompt) == (dialect == "tsql")


def test_wrong_dialect_syntax_is_regenerated_with_feedback(sql_factory: Callable[..., SQLGeneration]) -> None:
    g = sql_factory("sqlserver")
    headers = g.generator(policy=VIEW_SQL_POLICY)
    intent_id = g.intent(headers)
    g.model_sql(RANKING_SQL["postgresql"]).model_sql(RANKING_SQL["sqlserver"])

    body = g.ok(g.generate(intent_id, headers))

    assert body["status"] == "generated" and body["generation_attempts"] == 2 and "TOP" in body["sql"]
    second = g.sql_prompts()[1]
    assert "LIMIT is not Microsoft SQL Server (T-SQL) syntax" in second
    assert g.sql_events()[0].retry_count == 1


def test_unsupported_dialect_is_rejected_before_the_model(sql_factory: Callable[..., SQLGeneration]) -> None:
    g = sql_factory("oracle")
    headers = g.generator()
    intent_id = g.intent(headers)
    requests = len(g.provider.requests)

    code = _error(g.generate(intent_id, headers), 422)

    assert code == "SQL_DIALECT_UNSUPPORTED" and len(g.provider.requests) == requests
    assert g.records() == []


# --- clarification and failures ------------------------------------------------------------------------------


def test_model_clarification_returns_no_sql(g: SQLGeneration) -> None:
    headers = g.generator(policy=VIEW_SQL_POLICY)
    intent_id = g.intent(headers)
    g.model_sql(None, status="clarification_required", clarification_question="Should revenue include tax?",
                referenced_tables=[], referenced_columns=[], joins_used=[])

    body = g.ok(g.generate(intent_id, headers))

    assert body["status"] == "clarification_required" and body["clarification_required"] is True
    assert body["sql"] is None and body["clarification_question"] == "Should revenue include tax?"
    [record] = g.records()
    assert record.generated_sql is None and record.generation_status == "clarification_required"


def test_malformed_json_is_regenerated(g: SQLGeneration) -> None:
    headers = g.generator()
    intent_id = g.intent(headers)
    g.model_sql_raw("Here is your query: SELECT 1").model_sql()

    body = g.ok(g.generate(intent_id, headers))

    assert body["status"] == "generated" and body["generation_attempts"] == 2
    assert "could not be accepted" in g.sql_prompts()[1]


def test_unknown_columns_are_regenerated_until_the_limit(sql_factory: Callable[..., SQLGeneration]) -> None:
    g = sql_factory(ai_sql_max_regenerations=1)
    headers = g.generator()
    intent_id = g.intent(headers)
    bad = RANKING_SQL["postgresql"].replace("SUM(o.total_amount)", "SUM(o.discount)")
    g.model_sql(bad).model_sql(bad).model_sql(bad)

    body = g.ok(g.generate(intent_id, headers))

    assert body["status"] == "generation_failed" and body["failure_category"] == "validation_failed"
    assert body["validation_status"] == "failed" and body["generation_attempts"] == 2 and body["sql"] is None
    assert "unknown_column" in {i["code"] for i in body["validation_issues"]}
    prompts = g.sql_prompts()
    assert len(prompts) == 2 and "Column 'discount' is not one of the supplied columns" in prompts[1]
    # Regeneration never widens the context.
    assert "payroll" not in prompts[1] and "email" not in prompts[1]
    [record] = g.records()
    assert record.generation_status == "generation_failed" and record.generated_sql is None
    [event] = g.sql_events()
    assert event.failure_category == "validation_failed" and event.retry_count == 1
    assert event.details["issue_codes"] and "discount" not in json.dumps(event.details)


@pytest.mark.parametrize(
    "sql",
    ["DELETE FROM sales.orders", "SELECT c.region FROM sales.customers AS c LIMIT 5; DROP TABLE sales.orders",
     "EXECUTE refresh_totals"],
)
def test_unsafe_sql_is_discarded_without_regeneration(g: SQLGeneration, sql: str) -> None:
    headers = g.generator(policy=VIEW_SQL_POLICY)
    intent_id = g.intent(headers)
    g.model_sql(sql).model_sql()

    body = g.ok(g.generate(intent_id, headers))

    assert body["status"] == "generation_failed" and body["failure_category"] == "unsafe_sql"
    assert body["generation_attempts"] == 1 and body["sql"] is None
    assert any(i["blocking"] for i in body["validation_issues"])
    assert g.records()[0].generated_sql is None


def test_restricted_table_in_sql_is_reported_generically(sql_factory: Callable[..., SQLGeneration]) -> None:
    g = sql_factory(ai_sql_max_regenerations=0)
    headers = g.generator()
    intent_id = g.intent(headers)
    g.model_sql("SELECT p.id, SUM(p.salary) AS total FROM hr.payroll AS p GROUP BY p.id LIMIT 5")

    body = g.ok(g.generate(intent_id, headers))

    assert body["status"] == "generation_failed" and body["failure_category"] == "validation_failed"
    messages = " ".join(i["message"] for i in body["validation_issues"])
    assert "not one of the supplied tables" in messages and "permi" not in messages.lower()


def test_unauthorized_columns_in_model_lists_are_rejected(sql_factory: Callable[..., SQLGeneration]) -> None:
    g = sql_factory(ai_sql_max_regenerations=0)
    headers = g.generator()
    intent_id = g.intent(headers)
    g.model_sql(referenced_columns=["sales.customers.email"])

    body = g.ok(g.generate(intent_id, headers))

    assert body["status"] == "generation_failed"
    assert "declared_unknown_column" in {i["code"] for i in body["validation_issues"]}


def test_prose_with_sql_is_not_accepted(sql_factory: Callable[..., SQLGeneration]) -> None:
    g = sql_factory(ai_sql_max_regenerations=0)
    headers = g.generator()
    intent_id = g.intent(headers)
    g.model_sql(explanation="Then run DELETE FROM sales.orders to tidy up.")

    body = g.ok(g.generate(intent_id, headers))

    assert body["status"] == "generation_failed" and body["failure_category"] == "malformed_output"
    assert "executable_content_in_text" in {i["code"] for i in body["validation_issues"]}


@pytest.mark.parametrize(
    ("configure", "status", "code"),
    [
        (lambda f: f.fail(httpx.ReadTimeout("slow")), 504, "AI_PROVIDER_TIMEOUT"),
        (lambda f: f.reply(503, {"error": "down"}), 503, "AI_PROVIDER_UNAVAILABLE"),
    ],
)
def test_provider_timeout_and_failure(g: SQLGeneration, configure, status: int, code: str) -> None:  # type: ignore[no-untyped-def]
    headers = g.generator()
    intent_id = g.intent(headers)
    g._fresh_queue()
    configure(g.provider)

    assert _error(g.generate(intent_id, headers), status) == code
    assert g.records() == []
    [event] = g.sql_events()
    assert event.status == "generation_failed" and event.failure_category == "provider_error"
    assert event.generation_id is None


# --- intents ---------------------------------------------------------------------------------------------


def test_client_supplied_intents_are_not_accepted(g: SQLGeneration) -> None:
    headers = g.generator()
    response = g.post("/ai/sql/generate", {"intent": {"intent_type": "ranking"}, "schema_source_id": g.schema_id},
                      headers=headers)
    assert response.status_code == 422
    assert _error(g.generate("00000000-0000-0000-0000-000000000001", headers), 404) == "INTENT_NOT_FOUND"


def test_intent_needing_clarification_is_refused(g: SQLGeneration) -> None:
    headers = g.generator()
    g.model_says(**{"intent_type": "aggregation", "metrics": [],
                    "clarification": {"needed": True, "missing_information": ["metric"], "question": "Which measure?"}})
    body = g.ok(g.understand("How are we doing?", headers))
    assert body["status"] == "needs_clarification"

    assert _error(g.generate(str(body["intent_id"]), headers), 409) == "INTENT_NOT_READY"


def test_schema_mismatch_change_and_expiry(g: SQLGeneration) -> None:
    headers = g.generator()
    intent_id = g.intent(headers)
    other = g.create_schema("Other DB")
    assert _error(g.generate(intent_id, headers, schema_id=other["id"]), 422) == "INTENT_SCHEMA_MISMATCH"

    g.api.sql("UPDATE business_intents SET created_at = now() - interval '2 hours' WHERE id = :i", i=intent_id)
    assert _error(g.generate(intent_id, headers), 409) == "INTENT_EXPIRED"

    fresh = g.intent(headers)
    g.api.sql("UPDATE schema_sources SET schema_version = schema_version + 1 WHERE id = :s", s=g.schema_id)
    assert _error(g.generate(fresh, headers), 409) == "INTENT_SCHEMA_CHANGED"


def test_permissions_are_checked_again_at_generation_time(g: SQLGeneration) -> None:
    user_id, headers = g.user("analyst")
    system_role = g.role_with("sql_system", g.system_permission_id("ai.question.understand"),
                              g.system_permission_id("ai.sql.generate"))
    data_role = g.role_with("sql_data", g.create_permission("sales.view.late", {
        "allowed_tables": ["orders", "customers"], "allowed_columns": ["orders.*", "customers.id", "customers.region"],
        "allowed_operations": ["aggregate", "read"],
    }))
    g.assign_role(user_id, system_role)
    g.assign_role(user_id, data_role)
    intent_id = g.intent(headers)
    g.ok(g.delete(f"/users/{user_id}/roles/{data_role}"), 204)
    requests = len(g.provider.requests)

    body = g.ok(g.generate(intent_id, headers))

    assert body["status"] == "generation_failed" and body["failure_category"] == "permission_denied"
    assert len(g.provider.requests) == requests  # no model call
    assert body["sql"] is None and "not permitted" in body["explanation"]


def test_generation_requires_its_permission(g: SQLGeneration) -> None:
    headers = g.analyst("ai.question.understand")
    intent_id = g.intent(headers)
    assert g.generate(intent_id, headers).status_code == 403
    g.api.clear_cookies()
    assert g.api.client.post("/api/v1/ai/sql/generate",
                             json={"intent_id": intent_id, "schema_source_id": g.schema_id}).status_code == 401


# --- regeneration and limits --------------------------------------------------------------------------------


def test_regeneration_keeps_the_intent_and_is_bounded(sql_factory: Callable[..., SQLGeneration]) -> None:
    g = sql_factory(ai_sql_max_manual_regenerations=1)
    headers = g.generator(policy=VIEW_SQL_POLICY)
    intent_id = g.intent(headers)
    first = g.ok(g.model_sql().generate(intent_id, headers))
    assert first["regenerations_remaining"] == 1

    second = g.ok(g.model_sql().regenerate(first["generation_id"], headers))

    assert second["generation_id"] != first["generation_id"] and second["regenerated_from_id"] == first["generation_id"]
    assert second["intent_id"] == intent_id and second["regenerations_remaining"] == 0
    assert _error(g.regenerate(first["generation_id"], headers), 409) == "SQL_REGENERATION_LIMIT"
    assert [e.is_regeneration for e in g.sql_events()] == [False, True]
    other = g.generator()
    assert _error(g.regenerate(first["generation_id"], other), 404) == "SQL_GENERATION_NOT_FOUND"


def test_rate_limit(sql_factory: Callable[..., SQLGeneration]) -> None:
    g = sql_factory(ai_sql_rate_limit_per_user=1)
    headers = g.generator()
    intent_id = g.intent(headers)
    assert g.model_sql().generate(intent_id, headers).status_code == 200
    limited = g.generate(intent_id, headers)
    assert limited.status_code == 429 and int(limited.headers["retry-after"]) >= 1


def test_there_is_no_execution_endpoint(g: SQLGeneration) -> None:
    paths = set(g.api.client.app.openapi()["paths"])  # type: ignore[attr-defined]
    sql_paths = {p for p in paths if p.startswith("/api/v1/ai/sql")}
    assert sql_paths == {
        "/api/v1/ai/sql/generate",
        "/api/v1/ai/sql/generations/{generation_id}",
        "/api/v1/ai/sql/generations/{generation_id}/regenerate",
    }
    assert not any("execute" in p for p in sql_paths)

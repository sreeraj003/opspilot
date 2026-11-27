"""``/api/v1/ai/understand`` against the real app, PostgreSQL, schema registry and permission engine (LLM mocked)."""

import json
from collections.abc import Callable
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest

from tests.ai.conftest import API_KEY
from tests.understanding.conftest import SALES_POLICY, Understanding, metric

pytestmark = pytest.mark.postgres


def today(tz: str = "UTC") -> date:
    return datetime.now(ZoneInfo(tz)).date()


def first_of_month(day: date) -> date:
    return day.replace(day=1)


def previous_month_start(day: date) -> date:
    start = first_of_month(day)
    return first_of_month(start - timedelta(days=1))


def _passed(body: dict) -> dict:  # type: ignore[type-arg]
    assert body["status"] == "understood" and body["validation"]["status"] == "passed", body
    return body["intent"]  # type: ignore[no-any-return]


# --- interpretation of the common intent types --------------------------------------------------------------


def test_metric_lookup_with_registered_term(u: Understanding) -> None:
    u.create_term("Revenue", synonyms=["turnover"], related_columns=["sales.orders.total_amount"], default_aggregation="sum",
                  aggregation_guidance="Exclude cancelled orders.")
    headers = u.analyst()
    u.model_says(metrics=[metric("revenue", "Revenue")], business_context="Company revenue")

    response = u.understand("What is our total revenue?", headers)
    body = u.ok(response)

    assert response.headers["cache-control"] == "no-store"
    assert body["status"] == "understood" and body["validation"]["status"] == "passed"
    intent = body["intent"]
    assert intent["intent_type"] == "metric_lookup" and intent["original_question"] == "What is our total revenue?"
    assert intent["schema_source_id"] == u.schema_id and body["schema_version"] == intent["schema_version"] >= 1
    revenue = intent["requested_metrics"][0]
    assert revenue["term_name"] == "Revenue" and revenue["aggregation"] == "sum"
    assert revenue["column"] == {"table": "sales.orders", "column": "total_amount"}
    assert intent["candidate_tables"] == ["sales.orders"]
    assert intent["candidate_columns"] == ["sales.orders.total_amount"]
    assert intent["permission_context"]["status"] == "allowed" and intent["permission_context"]["is_authorization_token"] is False
    assert body["permission"] == {"status": "allowed", "export_allowed": False, "row_level_restrictions": False, "reason": None}
    assert body["provider"] == "nvidia" and body["confidence"] == 0.9
    assert "sql" not in json.dumps(intent).lower()

    prompt = u.prompt()
    assert "What is our total revenue?" in prompt and "Exclude cancelled orders." in prompt
    # Only what the user may see; no restricted table, denied column, row filter or credential.
    assert "payroll" not in prompt and "salary" not in prompt and "email" not in prompt
    assert "Kerala" not in prompt and API_KEY not in prompt
    body_sent = json.loads(u.provider.last.content)
    assert body_sent["temperature"] == 0.0 and "response_format" not in body_sent

    [event] = u.events()
    assert event.status == "understood" and event.intent_type == "metric_lookup" and event.validation_outcome == "passed"
    assert event.provider == "nvidia" and event.question_chars == len("What is our total revenue?")
    assert event.schema_version == body["schema_version"] and event.is_preview is False
    usage = u.api.sql("SELECT operation, status FROM ai_usage_records")
    assert [(r.operation, r.status) for r in usage] == [("question_understanding", "success")]


def test_json_response_format_is_requested_when_enabled(understanding_factory: Callable[..., Understanding]) -> None:
    u = understanding_factory(ai_json_response_format=True)
    headers = u.analyst()
    u.model_says(intent_type="detail_lookup", attributes=[{"table": "sales.customers", "column": "name"}])
    assert u.ok(u.understand("List customer names", headers))["status"] == "understood"
    assert json.loads(u.provider.last.content)["response_format"] == {"type": "json_object"}


def test_aggregation_last_month_resolves_dates_in_business_timezone(u: Understanding) -> None:
    u.create_term("Order count", related_columns=["sales.orders.id"], default_aggregation="count")
    u.ok(u.put("/settings/business.timezone", {"value": "Asia/Kolkata"}), 201)
    invalid = u.put("/settings/business.timezone", {"value": "Mars/Olympus"})
    assert invalid.status_code == 422
    try:
        _check_kolkata_last_month(u)
    finally:
        u.ok(u.delete("/settings/business.timezone"), 204)


def _check_kolkata_last_month(u: Understanding) -> None:
    headers = u.analyst()
    u.model_says(
        intent_type="aggregation",
        metrics=[metric("orders", "Order count")],
        time={"expression": "last month", "kind": "last_month", "date_table": "sales.orders", "date_column": "placed_at"},
    )

    intent = u.ok(u.understand("How many orders did we receive last month?", headers))["intent"]

    time_range = intent["time"]["time_range"]
    local_today = today("Asia/Kolkata")
    assert time_range["timezone"] == "Asia/Kolkata" and time_range["expression"] == "last month"
    assert time_range["start_date"] == previous_month_start(local_today).isoformat()
    assert time_range["end_date"] == first_of_month(local_today).isoformat()
    assert time_range["boundary"] == "start_inclusive_end_exclusive" and time_range["start_at"].endswith("+05:30")
    assert intent["time"]["date_column"] == {"table": "sales.orders", "column": "placed_at"}
    assert intent["requested_metrics"][0]["aggregation"] == "count"
    assert intent["aggregation"]["aggregation_type"] == "count"


def test_comparison_trend_and_ranking(u: Understanding) -> None:
    u.create_term("Revenue", synonyms=["sales"], related_columns=["sales.orders.total_amount"], default_aggregation="sum")
    headers = u.analyst()

    u.model_says(intent_type="comparison", metrics=[metric("sales", "Revenue")],
                 time={"expression": "this month", "kind": "this_month"}, comparison="previous_period")
    comparison = _passed(u.ok(u.understand("Compare this month's sales with last month.", headers)))
    assert comparison["intent_type"] == "comparison"
    assert comparison["time"]["comparison_period"]["start_date"] == previous_month_start(today()).isoformat()
    assert comparison["time"]["comparison_period"]["kind"] == "previous_period"

    u.model_says(intent_type="trend_analysis", metrics=[metric("revenue", "Revenue")],
                 time={"expression": "last 12 months", "kind": "last_n_months", "quantity": 12}, time_granularity="month",
                 dimensions=[{"concept": "month", "business_term": None, "table": "sales.orders", "column": "placed_at",
                              "time_granularity": "month"}])
    trend = _passed(u.ok(u.understand("Show monthly revenue for the last 12 months.", headers)))
    assert trend["time"]["time_granularity"] == "month"
    assert trend["time"]["time_range"]["is_rolling"] is True and trend["time"]["time_range"]["quantity"] == 12
    assert trend["time"]["time_range"]["end_date"] == (today() + timedelta(days=1)).isoformat()

    u.model_says(intent_type="ranking", metrics=[metric("revenue", "Revenue")], limit=5,
                 dimensions=[{"concept": "product", "business_term": None, "table": "sales.products", "column": "name",
                              "time_granularity": None}])
    ranking = _passed(u.ok(u.understand("Show the top five products by revenue.", headers)))
    assert ranking["aggregation"]["limit"] == 5
    assert ranking["aggregation"]["sorting"] == [{"target": "revenue", "direction": "desc"}]
    assert ranking["aggregation"]["grouping_dimensions"] == ["product"]
    assert set(ranking["candidate_tables"]) == {"sales.orders", "sales.products"}


def test_filters_and_row_level_context_are_preserved(u: Understanding) -> None:
    headers = u.analyst()
    u.model_says(intent_type="filtering", attributes=[{"table": "sales.customers", "column": "name"}],
                 filters=[{"table": "sales.customers", "column": "region", "operator": "equals", "value": "Kerala",
                           "value_type": "string"}])
    body = u.ok(u.understand("List customers in Kerala", headers))
    intent = body["intent"]
    assert intent["filters"][0]["field"] == {"table": "sales.customers", "column": "region"}
    [table] = intent["permission_context"]["tables"]
    assert table["table"] == "sales.customers" and table["row_filter"]["value"] == "Kerala"
    assert intent["permission_context"]["row_level_restrictions"] is True and body["permission"]["row_level_restrictions"] is True


# --- clarification ------------------------------------------------------------------------------------------


def test_ambiguous_terminology_needs_clarification_once(u: Understanding) -> None:
    u.create_term("Gross sales", synonyms=["sales"], related_columns=["sales.orders.total_amount"], default_aggregation="sum")
    u.create_term("Net sales", synonyms=["sales"], related_columns=["sales.orders.total_amount"], default_aggregation="sum")
    headers = u.analyst()
    u.model_says(metrics=[metric("sales", "Gross sales")])

    body = u.ok(u.understand("Show me sales", headers))
    assert body["status"] == "needs_clarification" and body["intent"]["requires_clarification"] is True
    clarification = body["clarification"]
    assert clarification["reason"] == "ambiguous_term" and clarification["key"] == "ambiguous_term:sales"
    assert clarification["clarification_question"] == "'sales' can mean Gross sales or Net sales. Which one do you mean?"
    assert body["confidence"] <= 0.5 and body["intent"]["original_question"] == "Show me sales"
    assert u.events()[-1].requires_clarification is True

    u.model_says(metrics=[metric("sales", "Gross sales")])
    again = u.ok(u.understand("Show me sales", headers, answered_clarifications=["ambiguous_term:sales"]))
    assert again["clarification"]["reason"] == "unresolved_after_clarification"
    assert again["clarification"]["clarification_question"] is None


def test_missing_business_definition_is_not_invented(u: Understanding) -> None:
    headers = u.analyst()
    u.model_says(intent_type="metric_lookup", unmapped_concepts=["profit"])
    body = u.ok(u.understand("What is our profit?", headers))
    assert body["status"] == "needs_clarification"
    assert body["clarification"]["reason"] == "missing_definition"
    assert body["clarification"]["missing_information"] == ["metric_definition"]
    assert body["intent"]["requested_metrics"] == []


def test_inactive_terms_are_not_used(u: Understanding) -> None:
    u.create_term("Revenue", related_columns=["sales.orders.total_amount"], is_active=False)
    headers = u.analyst()
    u.model_says(metrics=[metric("revenue", "Revenue")])
    u.model_says(metrics=[metric("revenue", "Revenue")])
    body = u.ok(u.understand("What is our revenue?", headers))
    assert "Revenue as defined by finance" not in u.prompt(0)
    assert body["status"] == "rejected" and body["validation"]["issues"][0]["code"] == "unknown_business_term"


# --- validation and correction ---------------------------------------------------------------------------------


def test_unknown_identifiers_get_one_bounded_correction(u: Understanding) -> None:
    headers = u.analyst()
    u.model_says(attributes=[{"table": "sales.invoices", "column": "id"}], intent_type="detail_lookup")
    u.model_says(attributes=[{"table": "sales.orders", "column": "id"}], intent_type="detail_lookup")

    body = u.ok(u.understand("List order ids", headers))
    assert body["status"] == "understood" and body["validation"]["status"] == "corrected"
    assert body["validation"]["correction_attempts"] == 1 and body["confidence"] <= 0.8
    assert len(u.provider.requests) == 2
    assert "Table 'sales.invoices' is not in the provided context." in u.prompt(1)


def test_invalid_output_after_corrections_is_rejected(u: Understanding) -> None:
    headers = u.analyst()
    u.model_says(attributes=[{"table": "sales.orders", "column": "margin"}], intent_type="detail_lookup")
    body = u.ok(u.understand("List order margins", headers))
    assert body["status"] == "rejected" and body["intent"] is None
    assert body["validation"]["status"] == "rejected" and body["validation"]["issues"][0]["code"] == "unknown_column"
    assert len(u.provider.requests) == 2  # one answer, one bounded correction
    assert u.events()[-1].validation_outcome == "rejected"


def test_malformed_json_is_corrected_or_rejected(u: Understanding) -> None:
    headers = u.analyst()
    u.model_raw("Sure! Here is the interpretation: {not json}")
    u.model_says(intent_type="detail_lookup", attributes=[{"table": "sales.products", "column": "name"}])
    body = u.ok(u.understand("List product names", headers))
    assert body["status"] == "understood" and body["validation"]["status"] == "corrected"

    u.model_raw("{\"intent_type\": ")
    body = u.ok(u.understand("List product names", headers))
    assert body["status"] == "rejected" and body["validation"]["issues"][0]["code"] == "malformed_json"


def test_unknown_intent_type_and_unsupported_operator_are_rejected(u: Understanding) -> None:
    headers = u.analyst()
    u.model_says(intent_type="forecasting")
    body = u.ok(u.understand("Forecast next year", headers))
    assert body["status"] == "rejected" and body["validation"]["issues"][0]["code"] == "unsupported_value"


def test_sql_in_model_output_is_never_corrected_or_trusted(u: Understanding) -> None:
    headers = u.analyst()
    u.model_says(normalized_question="SELECT * FROM hr.payroll", intent_type="detail_lookup")
    body = u.ok(u.understand("Show order details", headers))
    assert body["status"] == "rejected" and body["validation"]["issues"][0]["code"] == "executable_content"
    assert len(u.provider.requests) == 1 and "payroll" not in json.dumps(body)


def test_invalid_questions_are_refused_before_the_model(u: Understanding) -> None:
    headers = u.analyst()
    response = u.understand("SELECT salary FROM hr.payroll", headers)
    assert response.status_code == 422 and response.json()["error"]["code"] == "QUESTION_INVALID"
    assert u.understand("hi", headers).status_code == 422
    assert u.understand("x" * 1001, headers).status_code == 422
    unknown = u.post("/ai/understand", {"question": "Revenue please", "schema_source_id": "00000000-0000-0000-0000-000000000000"}, headers=headers)
    assert unknown.status_code == 404
    assert u.provider.requests == []
    assert u.events()[0].status == "rejected" and u.events()[0].error_category == "invalid_question"


# --- permissions -----------------------------------------------------------------------------------------


def test_unauthorized_table_and_column_requests_are_denied_safely(u: Understanding) -> None:
    headers = u.analyst()
    u.model_says(intent_type="detail_lookup", attributes=[{"table": "hr.payroll", "column": "salary"}])
    body = u.ok(u.understand("Show salaries", headers))
    assert body["status"] == "access_denied" and body["intent"] is None
    assert body["permission"]["status"] == "denied"
    assert "payroll" not in json.dumps(body) and "salary" not in json.dumps(body).replace("Show salaries", "")

    u.model_says(intent_type="detail_lookup", attributes=[{"table": "sales.customers", "column": "email"}])
    body = u.ok(u.understand("List customer emails", headers))
    assert body["status"] == "access_denied" and "email" not in body["explanation"]
    assert len(u.provider.requests) == 2  # no correction for restricted data


def test_unmapped_concept_naming_restricted_data_is_a_denial(u: Understanding) -> None:
    headers = u.analyst()
    u.model_says(unmapped_concepts=["payroll"])
    body = u.ok(u.understand("What is the total payroll?", headers))
    assert body["status"] == "access_denied"


def test_restricted_business_term_is_denied_without_calling_the_model(u: Understanding) -> None:
    u.create_term("Payroll cost", related_columns=["hr.payroll.salary"], default_aggregation="sum")
    headers = u.analyst()
    body = u.ok(u.understand("What was our payroll cost last month?", headers))
    assert body["status"] == "access_denied" and u.provider.requests == []
    assert "Payroll cost" not in body["explanation"]


def test_no_data_permissions_means_no_model_call(u: Understanding) -> None:
    headers = u.analyst(policy=None)
    body = u.ok(u.understand("What is our revenue?", headers))
    assert body["status"] == "access_denied" and body["permission"]["status"] == "no_data_access"
    assert u.provider.requests == [] and u.events()[-1].status == "access_denied"


def test_engine_reauthorizes_the_operation(u: Understanding) -> None:
    read_only = {**SALES_POLICY, "allowed_operations": ["read"]}
    headers = u.analyst(policy=read_only)
    u.model_says(metrics=[metric("total order value", None, "sales.orders", "total_amount", "sum")])
    body = u.ok(u.understand("What is the total order value?", headers))
    assert body["status"] == "access_denied"  # aggregate is not permitted, only row reads

    u.model_says(intent_type="detail_lookup", attributes=[{"table": "sales.orders", "column": "total_amount"}])
    assert u.ok(u.understand("List order values", headers))["status"] == "understood"


def test_system_permissions_gate_the_endpoints(u: Understanding) -> None:
    _, nobody = u.user()
    assert u.understand("What is our revenue?", nobody).status_code == 403
    assert u.get("/ai/understand/options", nobody).status_code == 403
    understand_only = u.analyst()
    assert u.understand("What is our revenue?", understand_only, path="/ai/understand/preview").status_code == 403
    assert u.client.post("/api/v1/ai/understand", json={"question": "x y z", "schema_source_id": u.schema_id}).status_code == 401

    options = u.ok(u.get("/ai/understand/options", understand_only))
    assert options["timezone"] == "UTC" and options["timezone_source"] == "default"
    assert [s["name"] for s in options["sources"]] == ["Sales DB"] and options["timezone_setting_key"] == "business.timezone"


def test_preview_adds_diagnostics_without_secrets(u: Understanding) -> None:
    u.create_term("Revenue", related_columns=["sales.orders.total_amount"], default_aggregation="sum")
    headers = u.analyst("ai.question.preview")
    u.model_says(metrics=[metric("revenue", "Revenue")])
    body = u.ok(u.understand("What is our revenue?", headers, path="/ai/understand/preview"))
    diagnostics = body["diagnostics"]
    assert body["status"] == "understood" and diagnostics["llm_attempts"] == 1
    assert diagnostics["matched_terms"][0]["name"] == "Revenue" and diagnostics["prompt_chars"] > 0
    assert diagnostics["schema_selection"]["selected_tables"][0] == "sales.orders"
    assert "hr.payroll" not in json.dumps(diagnostics)  # restricted tables are not scored or shown
    assert API_KEY not in json.dumps(body) and "You are the question-understanding component" not in json.dumps(body)
    assert u.events()[-1].is_preview is True


def test_rate_limit(understanding_factory: Callable[..., Understanding]) -> None:
    u = understanding_factory(ai_understand_rate_limit_per_user=1)
    headers = u.analyst()
    u.model_says(intent_type="detail_lookup", attributes=[{"table": "sales.products", "column": "name"}])
    assert u.understand("List product names", headers).status_code == 200
    limited = u.understand("List product names", headers)
    assert limited.status_code == 429 and int(limited.headers["retry-after"]) >= 1


def test_inactive_schema_is_refused(u: Understanding) -> None:
    headers = u.analyst()
    u.ok(u.patch(f"/schemas/{u.schema_id}", {"is_active": False}))
    response = u.understand("What is our revenue?", headers)
    assert response.status_code == 409 and response.json()["error"]["code"] == "SCHEMA_NOT_AVAILABLE"


# --- provider failures -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("configure", "status", "code", "category"),
    [
        (lambda f: f.fail(httpx.ReadTimeout("slow")), 504, "AI_PROVIDER_TIMEOUT", "timeout"),
        (lambda f: f.reply(503, {"error": "down"}), 503, "AI_PROVIDER_UNAVAILABLE", "unavailable"),
        (lambda f: f.reply(401, {"error": f"bad key {API_KEY}"}), 502, "AI_PROVIDER_AUTHENTICATION_FAILED", "authentication"),
    ],
)
def test_provider_failures_are_controlled(u: Understanding, configure, status: int, code: str, category: str) -> None:  # type: ignore[no-untyped-def]
    headers = u.analyst()
    configure(u.provider)
    response = u.understand("How many orders did we receive?", headers)
    assert response.status_code == status and response.json()["error"]["code"] == code
    assert API_KEY not in response.text
    [event] = u.events()
    assert event.status == "failed" and event.error_category == category and event.validation_outcome == "not_run"


def test_unconfigured_provider(understanding_factory: Callable[..., Understanding]) -> None:
    u = understanding_factory(nvidia_api_key=None, nvidia_model=None)
    headers = u.analyst()
    response = u.understand("How many orders did we receive?", headers)
    assert response.status_code == 503 and response.json()["error"]["code"] == "AI_PROVIDER_NOT_CONFIGURED"

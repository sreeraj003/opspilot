"""Schema relevance, term matching, permission view and clarification rules (pure)."""

from app.ai.understanding.prompts.understanding_prompt import build_messages, context_payload
from app.ai.understanding.schemas.intent import ClarificationReason, MissingInformation
from app.ai.understanding.schemas.llm_output import LLMInterpretation
from app.ai.understanding.services.clarification import ClarificationEngine
from app.ai.understanding.services.permission_view import PermissionView
from app.ai.understanding.services.schema_relevance import KeywordSchemaRelevanceSelector, restricted_matches
from app.ai.understanding.services.terminology_service import ambiguous_phrases, term_matches
from app.ai.understanding.validators.intent_validator import InterpretationValidator
from tests.understanding.helpers import ai_context, as_json, grant, resolver, sales_permissions, sales_schema, term

GROSS = term("Gross sales", synonyms=["sales"], columns=["sales.orders.total_amount"])
NET = term("Net sales", synonyms=["sales"], columns=["sales.orders.total_amount"])
REVENUE = term("Revenue", columns=["sales.orders.total_amount"])


# --- relevance ----------------------------------------------------------------------------------------------


def test_small_schema_is_sent_whole_with_best_tables_first() -> None:
    schema = sales_schema()
    result = KeywordSchemaRelevanceSelector().select(
        "Top five products by revenue", schema, candidates=list(schema.tables), max_tables=10
    )
    assert result.strategy == "all_tables" and result.tables[0] == "sales.products"
    assert set(result.tables) == {t.qualified for t in schema.tables.values()}


def test_large_schema_is_narrowed_by_keywords_and_relationships() -> None:
    schema = sales_schema(extra_tables=30)
    result = KeywordSchemaRelevanceSelector().select(
        "How many orders did we receive last month?", schema, candidates=list(schema.tables), max_tables=3
    )
    assert result.strategy == "keyword"
    assert result.tables[0] == "sales.orders"
    # Related tables are added to keep join paths, archive tables are not sent.
    assert set(result.tables) == {"sales.orders", "sales.customers", "sales.products"}
    assert not any(t.startswith("misc.") for t in result.tables)


def test_terms_point_relevance_at_their_tables() -> None:
    schema = sales_schema(extra_tables=30)
    result = KeywordSchemaRelevanceSelector().select(
        "What is our turnover?", schema, candidates=list(schema.tables), term_tables={"sales.orders"}, max_tables=2
    )
    assert result.tables[0] == "sales.orders"
    assert any("business term" in s.reasons for s in result.scores)


def test_only_permitted_candidates_are_considered() -> None:
    schema = sales_schema()
    permitted = [k for k in schema.tables if k != "hr.payroll"]
    result = KeywordSchemaRelevanceSelector().select("payroll salary", schema, candidates=permitted, max_tables=10)
    assert "hr.payroll" not in result.tables
    assert restricted_matches("show payroll", schema, ["hr.payroll"]) == ["hr.payroll"]


# --- permission view ------------------------------------------------------------------------------------------


def test_permission_view_maps_policies_to_registered_tables() -> None:
    schema = sales_schema()
    view = PermissionView(
        ai_context(
            grant("orders", ["orders.id", "orders.total_amount"], row_filter={"column": "orders.status", "operator": "equals", "value": "paid"}),
            grant("sales.customers", denied=["sales.customers.email"], export=True),
        ),
        schema,
    )
    assert view.table_allowed("sales.orders") and view.table_allowed("SALES.CUSTOMERS")
    assert not view.table_allowed("sales.products") and not view.table_allowed("hr.payroll")
    assert view.column_allowed("sales.orders", "total_amount") and not view.column_allowed("sales.orders", "status")
    assert view.column_allowed("sales.customers", "region") and not view.column_allowed("sales.customers", "email")
    assert view.row_filter("sales.orders") == {"column": "orders.status", "operator": "equals", "value": "paid"}
    assert view.export_allowed("sales.customers") and not view.export_allowed("sales.orders")
    assert view.policy_column("sales.orders", "view", "total_amount") == "orders.total_amount"
    assert not PermissionView(ai_context(), schema).has_any_data_access


def test_prompt_contains_only_permitted_fields_and_no_policies() -> None:
    schema = sales_schema()
    permissions = sales_permissions(schema, row_filter={"column": "orders.status", "operator": "equals", "value": "secret-region"})
    payload = context_payload(
        "revenue by region", schema, ["sales.orders", "sales.customers"], permissions, [REVENUE], today="2026-03-15", timezone="UTC"
    )
    text = build_messages(payload)[1]["content"]
    assert "total_amount" in text and "Revenue" in text
    assert "email" not in text  # denied column
    assert "payroll" not in text and "salary" not in text  # restricted table never selected
    assert "secret-region" not in text and "row_filter" not in text  # policies stay in the backend


# --- terms ----------------------------------------------------------------------------------------------------


def test_term_matching_prefers_longer_phrases() -> None:
    matches = term_matches("Show me net sales for last month", [GROSS, NET, REVENUE])
    assert [m.term.name for m in matches] == ["Net sales"]
    assert ambiguous_phrases(matches) == {}

    shared = term_matches("Show me sales", [GROSS, NET])
    assert {m.term.name for m in shared} == {"Gross sales", "Net sales"}
    assert set(ambiguous_phrases(shared)) == {"sales"}

    assert [m.term.name for m in term_matches("What were our revenues?", [REVENUE])] == ["Revenue"]
    assert term_matches("prerevenue figures", [REVENUE]) == []  # whole words only


# --- clarification --------------------------------------------------------------------------------------------


def clarify(question: str, terms: list, answered: tuple[str, ...] = (), **output: object):  # type: ignore[no-untyped-def]
    schema = sales_schema()
    parsed = LLMInterpretation.model_validate_json(as_json(**output))
    validated = InterpretationValidator(schema, sales_permissions(schema), terms, resolver()).validate(question, parsed)
    return ClarificationEngine().evaluate(parsed, validated, term_matches(question, terms), answered=answered)


def test_ambiguous_terminology_asks_which_definition() -> None:
    result = clarify("Show me sales", [GROSS, NET], metrics=[
        {"concept": "sales", "business_term": "Gross sales", "table": None, "column": None, "aggregation": None}])
    assert result.requires_clarification and result.reason == ClarificationReason.AMBIGUOUS_TERM
    assert result.clarification_question == "'sales' can mean Gross sales or Net sales. Which one do you mean?"
    assert result.key == "ambiguous_term:sales"


def test_missing_definition_is_not_invented() -> None:
    result = clarify("Show performance", [REVENUE], intent_type="unknown", unmapped_concepts=["performance"])
    assert result.reason == ClarificationReason.MISSING_DEFINITION
    assert result.missing_information == (MissingInformation.METRIC_DEFINITION,)
    assert "'performance' has no registered business definition" in (result.clarification_question or "")


def test_comparison_without_period_and_ranking_without_metric() -> None:
    metric = [{"concept": "revenue", "business_term": "Revenue", "table": None, "column": None, "aggregation": None}]
    result = clarify("Compare revenue", [REVENUE], intent_type="comparison", metrics=metric)
    assert result.reason == ClarificationReason.MISSING_COMPARISON_PERIOD

    result = clarify("Show top customers", [REVENUE], intent_type="ranking",
                     dimensions=[{"concept": "customer", "business_term": None, "table": "sales.customers", "column": "name", "time_granularity": None}])
    assert result.reason == ClarificationReason.MISSING_RANKING_METRIC
    assert "ranking" in (result.clarification_question or "")


def test_complete_questions_need_no_clarification() -> None:
    result = clarify(
        "What is our total revenue?", [REVENUE],
        metrics=[{"concept": "revenue", "business_term": "Revenue", "table": None, "column": None, "aggregation": "sum"}],
        # The model asks anyway, about something the backend already knows.
        clarification={"needed": True, "missing_information": ["metric_definition"], "question": "Which revenue definition?"},
    )
    assert not result.requires_clarification


def test_one_question_at_a_time_and_never_repeated() -> None:
    kwargs = {"intent_type": "unknown", "unmapped_concepts": ["profit", "margin"]}
    first = clarify("profit and margin", [REVENUE], **kwargs)
    assert first.key == "missing_definition:profit" and len(first.deferred) == 1
    second = clarify("profit and margin", [REVENUE], answered=("missing_definition:profit",), **kwargs)
    assert second.key == "missing_definition:margin"
    exhausted = clarify("profit and margin", [REVENUE], answered=("missing_definition:profit", "missing_definition:margin"), **kwargs)
    assert exhausted.requires_clarification and exhausted.clarification_question is None
    assert exhausted.reason == ClarificationReason.UNRESOLVED_AFTER_CLARIFICATION

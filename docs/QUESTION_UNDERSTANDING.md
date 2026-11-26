# Question understanding and business intent (Module 09)

Module 09 turns a business question ("Show the top 5 products by revenue last month") into a validated, structured
**BusinessIntent**. It is the first step of the future question pipeline. It does **not** generate or execute SQL,
does not call the company connector and is not a chatbot: one question in, one structured interpretation out.

Design notes live in ARCHITECTURE.md section 10. This guide covers the contract, the rules and how to configure them.

## 1. How a question is processed

```
question + schema_source_id
  1. Validate the question          plain language, ≤ 1000 characters, no SQL statements or code
  2. Load the registered schema     Module 06 (inactive or empty schema → 409)
  3. Load the user's permissions    Module 04 AI authorization context (no data access → stop, no model call)
  4. Match business terms           active terms of that schema; a term on restricted data → safe denial
  5. Select relevant tables         deterministic keyword relevance over permitted tables only
  6. Ask the model                  Module 08 LLMService, temperature 0, JSON only, constrained prompt
  7. Parse and validate             strict JSON contract; every identifier and value checked; dates resolved
     (bounded correction)           correctable problems get at most AI_UNDERSTAND_MAX_CORRECTIONS retries
  8. Clarification                  one question at a time, never repeated
  9. Re-authorize                   the permission engine checks the intent's tables, columns and operation
 10. Respond + audit                BusinessIntent, status, validation and permission views; audit row; log event
```

Steps 1–5 and 7–10 are backend code. The model (step 6) only proposes an interpretation; it never decides
permissions, never computes dates and its output is never trusted until validated.

## 2. API

All routes are under `/api/v1`, require a signed-in user and return the standard error envelope.

| Method and path | Permission | Purpose |
| --- | --- | --- |
| `POST /ai/understand` | `ai.question.understand` | Interpret a question |
| `POST /ai/understand/preview` | `ai.question.preview` | Same pipeline plus diagnostics (administrators) |
| `GET /ai/understand/options` | either of the above | Schemas available for interpretation, business timezone |
| `GET /business-terms` | `business_term.read` | List terms (`schema_source_id`, `search`, `kind`, `is_active`, `offset`, `limit`) |
| `GET /business-terms/{id}` | `business_term.read` | One term |
| `GET /business-terms/schema-fields?schema_source_id=` | `business_term.manage` | Tables and columns a term can be associated with |
| `POST /business-terms` | `business_term.manage` | Create (201) |
| `PATCH /business-terms/{id}` | `business_term.manage` | Partial update |
| `DELETE /business-terms/{id}` | `business_term.manage` | Delete (204, soft delete; the name can be reused) |

The four permissions are delegable system permissions, granted to Administrator by migration `0007`. Give analysts
`ai.question.understand` plus a data-access permission (a Module 04 policy) for the tables they may query.

Interpretation calls are rate-limited per user (`AI_UNDERSTAND_RATE_LIMIT_PER_USER` per minute, shared by both
endpoints; 429 with `Retry-After`). Responses carry `Cache-Control: no-store`.

### Errors

| Status | Code | When |
| --- | --- | --- |
| 422 | `QUESTION_INVALID` | Question too short or long, contains SQL statements, code, markup or prompt-injection phrases |
| 404 | `NOT_FOUND` / `BUSINESS_TERM_NOT_FOUND` | Unknown schema or term |
| 409 | `SCHEMA_NOT_AVAILABLE` | Schema inactive or with no tables |
| 409 | `BUSINESS_TERM_CONFLICT` | Name already used, or a name equal to another term's synonym |
| 422 | `BUSINESS_TERM_INVALID` | Unregistered table or column, no fields, SQL in guidance, name used as its own synonym |
| 502/503/504 | `AI_PROVIDER_*` | Module 08 provider errors (not configured, unavailable, timeout, ...) |

An interpretation that is restricted, unsafe or unclear is **not** an HTTP error: it is a 200 response whose `status`
is `access_denied`, `rejected` or `needs_clarification`.

### Request

```json
POST /api/v1/ai/understand
{
  "question": "Show the top 5 products by revenue last month",
  "schema_source_id": "d8f3a1ed-8562-436c-b1b7-5586205a8fbc",
  "answered_clarifications": []
}
```

`answered_clarifications` holds the `clarification.key` values of questions already asked for this question (see
section 6). There is no conversation memory: the client sends them back.

### Response (understood)

Real output from the test suite (mocked model), shortened only in `permission_context.tables`:

```json
{
  "request_id": "d373c83645404446aae93c9ac8beb8e0",
  "intent_id": "73c85675-cfda-41b5-b839-74d62ee996b9",
  "status": "understood",
  "schema_source_id": "d8f3a1ed-8562-436c-b1b7-5586205a8fbc",
  "schema_version": 2,
  "confidence": 0.9,
  "intent": {
    "intent_id": "73c85675-cfda-41b5-b839-74d62ee996b9",
    "original_question": "Show the top 5 products by revenue last month",
    "normalized_question": "Top 5 products by revenue last month",
    "intent_type": "ranking",
    "confidence": 0.9,
    "requires_clarification": false,
    "schema_source_id": "d8f3a1ed-8562-436c-b1b7-5586205a8fbc",
    "schema_version": 2,
    "requested_metrics": [
      {
        "name": "revenue",
        "term_id": "dc688bb8-1f82-40b4-a6cc-95513a3dfd75",
        "term_name": "Revenue",
        "column": { "table": "sales.orders", "column": "total_amount" },
        "aggregation": "sum",
        "definition_source": "business_term"
      }
    ],
    "requested_dimensions": [
      { "name": "product", "term_id": null, "column": { "table": "sales.products", "column": "name" }, "time_granularity": null }
    ],
    "requested_attributes": [],
    "candidate_tables": ["sales.orders", "sales.products"],
    "candidate_columns": ["sales.orders.placed_at", "sales.orders.total_amount", "sales.products.name"],
    "filters": [],
    "time": {
      "time_range": {
        "label": "Last month",
        "start_date": "2026-10-01",
        "end_date": "2026-11-01",
        "start_at": "2026-10-01T00:00:00Z",
        "end_at": "2026-11-01T00:00:00Z",
        "boundary": "start_inclusive_end_exclusive",
        "expression": "last month",
        "kind": "last_month",
        "quantity": null,
        "timezone": "UTC",
        "is_rolling": false,
        "is_partial": false
      },
      "comparison_period": null,
      "time_granularity": null,
      "date_column": { "table": "sales.orders", "column": "placed_at" }
    },
    "aggregation": {
      "aggregation_type": "sum",
      "grouping_dimensions": ["product"],
      "sorting": [{ "target": "revenue", "direction": "desc" }],
      "limit": 5
    },
    "business_context": null,
    "assumptions": ["Ranked by 'revenue', highest first."],
    "ambiguities": [],
    "clarification_question": null,
    "permission_context": {
      "status": "allowed",
      "policy_revision": 10,
      "tables": [
        { "table": "sales.orders", "operations": ["aggregate", "read"], "row_filter": null, "allow_export": false }
      ],
      "export_allowed": false,
      "row_level_restrictions": false,
      "restriction_reason": null,
      "is_authorization_token": false
    }
  },
  "clarification": { "requires_clarification": false, "reason": null, "missing_information": [], "clarification_question": null, "key": null },
  "validation": { "status": "passed", "correction_attempts": 0, "issues": [] },
  "permission": { "status": "allowed", "export_allowed": false, "row_level_restrictions": false, "reason": null },
  "explanation": "Understood as ranking of revenue by product for last month.",
  "provider": "nvidia",
  "model": "meta/llama-3.1-8b-instruct",
  "latency_ms": 26
}
```

### Response (needs clarification)

```json
{
  "status": "needs_clarification",
  "confidence": 0.5,
  "clarification": {
    "requires_clarification": true,
    "reason": "ambiguous_term",
    "missing_information": ["term_meaning"],
    "clarification_question": "'sales' can mean Gross sales or Net sales. Which one do you mean?",
    "key": "ambiguous_term:sales"
  },
  "validation": { "status": "passed", "correction_attempts": 0, "issues": [] },
  "...": "intent, permission, explanation as above"
}
```

### Response (access denied)

```json
{
  "status": "access_denied",
  "intent": null,
  "permission": {
    "status": "denied",
    "export_allowed": false,
    "row_level_restrictions": false,
    "reason": "The question needs data you are not permitted to use."
  },
  "validation": { "status": "rejected", "correction_attempts": 0,
                  "issues": [{ "code": "restricted", "message": "A requested field is not available to you.", "location": [] }] }
}
```

The denial never names the restricted table or column.

### Preview diagnostics

`/ai/understand/preview` returns the same fields plus `diagnostics`: business timezone and its source, reference
time, prompt version, schema selection (strategy, selected tables, scores and reasons), matched terms, number of
terms in context, prompt size in characters and model attempts. It never returns the prompt, the model's raw
output, provider keys or anyone's permission policies, and it uses the caller's own permissions.

## 3. BusinessIntent

| Field | Meaning |
| --- | --- |
| `intent_id` | New UUID per interpretation (also in the audit row) |
| `original_question` / `normalized_question` | As asked / restated plainly by the model (safety-checked) |
| `intent_type` | See section 4 |
| `confidence` | 0–1. Capped at 0.5 when clarification is needed and 0.8 after a correction turn |
| `requires_clarification`, `clarification_question` | See section 6 |
| `schema_source_id`, `schema_version` | The schema and version the interpretation was validated against |
| `requested_metrics` | Measures: term or column, aggregation, and whether the definition came from a business term |
| `requested_dimensions` | Group-by fields, with optional time granularity |
| `requested_attributes` | Plain columns to show (detail lookups) |
| `candidate_tables`, `candidate_columns` | Every registered field the intent uses (all permission-checked) |
| `filters` | Field, operator, value, value type (type-checked against the column) |
| `time` | Resolved `time_range`, optional `comparison_period`, `time_granularity`, `date_column` |
| `aggregation` | Overall aggregation type, grouping dimensions, sorting, limit |
| `business_context`, `assumptions`, `ambiguities` | Notes the backend made explicit |
| `permission_context` | Tables with allowed operations, row filters, export flag. **Not an authorization token** (`is_authorization_token` is always `false`): later modules re-authorize |

All models are strict Pydantic models (`app/ai/understanding/schemas/intent.py`).

## 4. Intent types

| Type | Example | Typical shape |
| --- | --- | --- |
| `metric_lookup` | "What is our revenue?" | One metric |
| `aggregation` | "How many orders did we receive last month?" | Metric + time range |
| `comparison` | "Compare this month's sales with last month" | Metric + time range + comparison period (or dimension groups) |
| `trend_analysis` | "Revenue by month this year" | Metric + time granularity |
| `ranking` | "Top 5 products by revenue" | Metric + dimension + sort + limit (default sort: highest first) |
| `filtering` | "Orders over 10,000 in Kerala" | Filters |
| `distribution` | "Orders by region" | Metric + categorical dimension |
| `detail_lookup` | "Show the email of customer 42" | Attributes + filters |
| `unknown` | — | Leads to clarification |

No question is hard-coded; the type comes from the model and is validated (unknown values are rejected).

## 5. Business terminology

A **business term** is a registered definition the model must use. Fields: schema source, name, description, kind
(`metric`, `dimension`, `attribute`), synonyms, related tables and columns, default aggregation (`sum`, `count`,
`count_distinct`, `average`, `min`, `max`, `none`), aggregation guidance (plain language) and active status.

Rules enforced by the backend:

- Every related table and column must be registered in that schema; names are stored canonically
  (`schema.table`, `schema.table.column`) and a column's table is always added to related tables.
- At least one table or column is required. A term cannot change schema.
- Names are unique per schema (case-insensitive); a name may not equal another term's synonym or its own synonym.
- A synonym **may** be shared by several terms: this records that a word has several meanings, and the AI then asks
  which one is meant.
- Guidance and descriptions refuse SQL, code and markup.
- Inactive terms are kept but never sent to the model.
- When a schema change removes or deactivates a related field, the term reports it in `stale_references` (shown as
  a warning in the UI) and the stale field is ignored.

At question time, terms are matched on whole words, singular and plural ("revenues" matches "Revenue"), and the
longest phrase wins ("net sales" matches only *Net sales*, not the shared synonym "sales"). A term's registered
aggregation overrides whatever the model chose, and the model may only use columns inside a term's definition.
A concept that has no term and no clearly matching column is never mapped: the AI asks for a definition.

Manage terms under **Administration → Business terms** (`/admin/business-terms`).

## 6. Clarification rules

The backend decides whether to ask, using this priority order; only the first open question is asked:

1. **Ambiguous term** (`ambiguous_term:<phrase>`): a phrase matches several terms, unless the user named one
   exactly.
2. **Missing definition** (`missing_definition:<concept>`): a concept has no registered definition.
3. **Comparison without a period** (`comparison_period`): "compare revenue" with no periods, or a period without
   "compared with what".
4. **Ranking without a measure** (`ranking_metric`): "top customers" with nothing to rank by.
5. **Unclear request** (`unclear_metric`): unknown intent or nothing to show.
6. **The model's own question** (`model:<question>`): only if none of the above applies and the backend cannot
   answer it itself (for example the model asking which revenue definition to use when a term already defines it).

Other open points are listed in `ambiguities`. The response's `clarification.key` identifies the question; when the
client sends it back in `answered_clarifications`, that question is never asked again for the same question text.
When every open point has been asked, the result is `unresolved_after_clarification` without a new question. The
original question is always preserved. There is no conversational memory: the user rephrases the question with the
missing detail.

## 7. Time behaviour

The model only names the expression ("last month") and its kind. The backend resolves it with
`app/ai/understanding/normalizers/time_resolver.py`, in the **business timezone**, relative to the request time.
The server's local timezone is never used.

- Every range is half-open: `start_date` inclusive, `end_date` **exclusive** (`boundary:
  start_inclusive_end_exclusive`). `start_at`/`end_at` are the same instants in the business timezone.
- Calendar periods are whole periods: `today`, `yesterday`, `this_week`/`last_week` (ISO weeks, Monday to Sunday),
  `this_month`/`last_month`, `this_quarter`/`last_quarter` (Q1 = January–March), `this_year`/`last_year`.
  `is_partial` is true when the period contains today.
- Rolling windows include today: `last_n_days`, `last_n_weeks`, `last_n_months`, `last_n_years` resolve to
  `[tomorrow − N units, tomorrow)` (`is_rolling` true). Month and year steps keep the day, clamped to the month's
  last day (29 February steps to 28 February in a common year).
- Named periods: `calendar_month` ("March 2025"), `calendar_quarter` ("Q1 2025"), `calendar_year` ("2024"). A month
  or quarter without a year means its most recent occurrence that has started (reported as an assumption).
- `explicit_range`: dates the user wrote, end date inclusive in the question, stored exclusive.
- Comparisons: `previous_period` (same kind and length immediately before) or `same_period_last_year`.
- Year transitions and leap years are handled (January's "last month" is December of the previous year; "last
  quarter" in Q1 is Q4 of the previous year).
- The expression must appear in the question; a time range the user did not ask for is rejected
  (`time_expression_not_in_question`). The date column must be a date or datetime column.

**Business timezone.** Default `UTC`. Precedence: the application setting `business.timezone` (changed by an
administrator with `settings:manage` on the Question preview page, or `PUT /api/v1/settings/business.timezone`),
then the `BUSINESS_TIMEZONE` environment variable, then `UTC`. Values must be IANA names (`Asia/Kolkata`); invalid
values are refused.

## 8. LLM structured output contract

The system prompt (`prompts/understanding_prompt.py`, version `understanding-v1`) lists the rules, the allowed
values of every enum and the exact JSON shape. The user message is a JSON document with the question (marked as
data), today's date and timezone, and only the tables, columns, terms, relationships and glossary entries the user
is permitted to see. No credentials, connector details, permission policies, row filters or sample data are sent.

```json
{
  "intent_type": "ranking",
  "normalized_question": "Top 5 products by revenue last month",
  "confidence": 0.9,
  "metrics": [{ "concept": "revenue", "business_term": "Revenue", "table": null, "column": null, "aggregation": null }],
  "dimensions": [{ "concept": "product", "business_term": null, "table": "sales.products", "column": "name", "time_granularity": null }],
  "attributes": [],
  "filters": [],
  "time": { "expression": "last month", "kind": "last_month", "quantity": null, "year": null, "month": null, "quarter": null,
            "start_date": null, "end_date": null, "date_table": "sales.orders", "date_column": "placed_at" },
  "comparison": null,
  "time_granularity": null,
  "sorting": [],
  "limit": 5,
  "unmapped_concepts": [],
  "ambiguities": [],
  "assumptions": [],
  "business_context": null,
  "clarification": { "needed": false, "missing_information": [], "question": null }
}
```

Validation (`schemas/llm_output.py`, `validators/`):

- **Parsing.** One JSON object only (a single Markdown fence is tolerated); no prose, arrays, trailing text or
  output over 24,000 characters. Strict types (`"0.9"` is not a number), unknown fields rejected, enums checked,
  confidence 0–1, limit 1–1000, dates `YYYY-MM-DD`, time fields consistent with the kind.
- **Safety.** Every text field is checked for SQL statements and clauses, SQL comments and function calls, code,
  template syntax, markup, tool-call phrases and instruction-override phrases. Unsafe output is **rejected without a
  correction turn** and its content is never echoed back.
- **Semantics.** Tables, columns and terms must exist in the schema/terms sent; columns must be inside a term's
  definition; aggregations must suit the column type (no `sum` of text); filters are type-checked (operator, value
  type, list for `in`, ordered pair for `between`, no value for `is_null`); granularity needs a date column; sort
  targets must be requested metrics or dimensions; restricted fields are blocking.
- **Correction.** Correctable problems (malformed JSON, unknown identifiers, invalid values) are sent back once (by
  default) with generic messages; the second answer is validated from scratch. Invalid output is never used.

## 9. Permissions

The model never authorizes. The backend:

- builds the prompt only from tables and columns the user may see (Module 04 AI authorization context);
- denies before calling the model when the user has no data access or a matched term relies on restricted data;
- treats any restricted field in the model's output as blocking (`access_denied`, generic message);
- re-authorizes the final tables, columns and operation (`aggregate` or `read`) with the permission engine;
- preserves row-level restrictions and export flags in `permission_context` so later modules apply them.

## 10. Observability

Each interpretation writes one `question_understanding_events` row (request id, intent id, user, schema source and
version, preview flag, provider, model, latency, model attempts, question length, intent type, confidence, status,
clarification flag, validation outcome, error category, and the validation issue codes and correction count) and logs `ai.understanding.completed`. The
question text, prompts, model output, keys and permission policies are never stored or logged. Model calls are also
recorded by Module 08 in `ai_usage_records` with the operation `question_understanding`.

## 11. Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `BUSINESS_TIMEZONE` | `UTC` | Fallback business timezone (an administrator setting overrides it) |
| `AI_UNDERSTAND_RATE_LIMIT_PER_USER` | `20` | Interpretations per user per minute |
| `AI_UNDERSTAND_MAX_SCHEMA_TABLES` | `12` | Most tables sent to the model per question |
| `AI_UNDERSTAND_MAX_CORRECTIONS` | `1` | Correction turns for invalid output (0–3) |
| `AI_UNDERSTAND_MAX_OUTPUT_TOKENS` | `1500` | Output token ceiling per interpretation |
| `AI_JSON_RESPONSE_FORMAT` | `false` | Send `response_format: {"type": "json_object"}` to the provider (only if the model supports it) |

## 12. Testing

Backend: `backend/tests/understanding/` (time resolution, parsing and validation, relevance, term matching,
clarification, permission view, prompt contents, the full API with a mocked provider, terms CRUD). The model is
never called for real: tests answer through `httpx.MockTransport` with queued JSON completions, so the real
`LLMService`, parsing, validation and permission checks run. Frontend: `BusinessTermsPage.test.tsx` and
`QuestionPreviewPage.test.tsx`.

# SQL generation (Module 10)

OpsPilot turns a validated **business intent** (Module 09) into one **untrusted candidate SQL query** for the
registered schema's database. Generated SQL is never executed by this module, never sent to the company GET API
and never approved here. Every result is `awaiting_security_validation`; the SQL security guard (Module 11) must
parse, authorize and approve it independently before anything can run.

## Workflow

```
Question ──► Module 09: POST /ai/understand ──► validated BusinessIntent (stored in business_intents)
                                                     │
                     POST /ai/sql/generate {intent_id, schema_source_id}
                                                     ▼
 1. load the caller's own stored intent (never a client-supplied object); must be "understood", recent, same schema
 2. load the registered schema (Module 06); reject unsupported database types
 3. load active business terms (Module 09)
 4. load the caller's CURRENT data permissions (Module 04) and re-authorize what the intent needs
 5. build the constrained context (intent tables + join paths, visible columns, terms, permission context)
 6. build the versioned prompt (sql-generation-v1)
 7. ask the model through LLMService (JSON only, temperature 0)
 8. parse the reply strictly (LLMSQLOutput)
 9. preliminary validation with SQLGlot; bounded regeneration with backend-written feedback
10. authorize the tables and columns the SQL ACTUALLY references; decide SQL visibility (allow_sql_view)
11. store the untrusted artifact (sql_generations) and an audit event (sql_generation_events)
```

Only step 7 is non-deterministic. Code lives in `backend/app/ai/sql_generation/` (`schemas/`, `services/`,
`prompts/`, `dialects/`, `context/`, `normalizers/`, `validators/`); tests in `backend/tests/sql_generation/`.

## API (under `/api/v1`, permission `ai.sql.generate`)

| Endpoint | Purpose |
| --- | --- |
| `POST /ai/sql/generate` | Body `{"intent_id": "...", "schema_source_id": "..."}`. Generates SQL for your stored intent. |
| `GET /ai/sql/generations/{generation_id}` | One of your generations (SQL text re-checked against current permissions). |
| `POST /ai/sql/generations/{generation_id}/regenerate` | A new generation for the same intent (bounded). |

There is **no execution endpoint**. All three require authentication and `ai.sql.generate`; a generation is visible
only to the user who created it (others get 404). Generate and regenerate share a per-user rate limit
(`AI_SQL_RATE_LIMIT_PER_USER` per minute). Responses carry `Cache-Control: no-store`. Prompts, provider keys and
connector credentials are never returned.

Errors: `INTENT_NOT_FOUND` (404), `INTENT_NOT_READY` (409, needs clarification), `INTENT_EXPIRED` (409, older than
`AI_SQL_INTENT_MAX_AGE_MINUTES`), `INTENT_SCHEMA_CHANGED` (409, the schema version or a referenced field changed),
`INTENT_SCHEMA_MISMATCH` (422), `INTENT_INVALID` (422), `SQL_DIALECT_UNSUPPORTED` (422), `SQL_REGENERATION_LIMIT` (409),
`SQL_GENERATION_NOT_FOUND` (404), and the Module 08 provider errors (502/503/504).

## Response contract (`SQLGenerationResult`)

| Field | Meaning |
| --- | --- |
| `generation_id` | Id of the stored artifact |
| `status` | `generated`, `clarification_required` or `generation_failed` |
| `sql` | The normalized SQL, only when generated **and** the caller's data permissions allow viewing SQL (`allow_sql_view`); otherwise `null` |
| `sql_visible` | Whether `sql` is shown |
| `dialect`, `dialect_name` | `postgres`, `mysql` or `tsql` |
| `referenced_tables`, `referenced_columns` | Parsed from the SQL with SQLGlot (never the model's own lists) |
| `joins_used` | Equality join conditions found in the SQL and whether each follows a registered relationship |
| `assumptions`, `explanation` | Plain sentences from the model (checked for SQL, code and tool instructions) |
| `clarification_required`, `clarification_question` | Set when the model needs more information; `sql` is then absent |
| `permission_notes` | Row-level restrictions that apply, hidden SQL, truncated join paths |
| `generation_model`, `provider`, `prompt_version` | What produced it |
| `schema_source_id`, `schema_version`, `intent_id` | Provenance; the intent's schema version is preserved |
| `validation_status`, `validation_issues` | Preliminary validation outcome (`passed`, `failed`, `not_run`) and issue codes |
| `failure_category` | `validation_failed`, `unsafe_sql`, `permission_denied`, `malformed_output`, `provider_error`, `model_declined` |
| `generation_attempts` | Model requests used, including automatic regenerations |
| `sql_sha256` | SHA-256 of the stored SQL: the exact identity Module 11 must approve |
| `security_status`, `trust_level`, `is_executable` | Always `awaiting_security_validation`, `untrusted`, `false` |

The API response adds `question` (original question, interpretation, schema name) and `regenerations_remaining`.

### The model's output contract (`LLMSQLOutput`)

```json
{
  "status": "generated",
  "sql": "SELECT c.region, SUM(o.total_amount) AS revenue FROM sales.orders AS o JOIN sales.customers AS c ON o.customer_id = c.id GROUP BY c.region ORDER BY revenue DESC LIMIT 5",
  "referenced_tables": ["sales.orders", "sales.customers"],
  "referenced_columns": ["sales.orders.total_amount", "sales.customers.region"],
  "joins_used": [{"from": "sales.orders.customer_id", "to": "sales.customers.id"}],
  "assumptions": ["Revenue is the sum of order totals."],
  "explanation": "Sums order totals per region and keeps the five largest.",
  "clarification_question": null
}
```

Strict: unknown fields, wrong types, bad identifiers, `sql` with a clarification (or no `sql` when generated) and
SQL or code inside prose fields are rejected. The JSON must be the whole reply (one optional Markdown fence).

## Supported dialects

| `database_type` (registry) | Dialect | Row limit | Identifier quoting | Dates |
| --- | --- | --- | --- | --- |
| `postgresql` | PostgreSQL (`postgres`) | `LIMIT n` | `"name"` | `DATE_TRUNC('month', c)`, `c + INTERVAL '1 day'` |
| `mysql`, `mariadb` | MySQL (`mysql`) | `LIMIT n` | `` `name` `` | `DATE_FORMAT(c, '%Y-%m-01')`, `DATE_ADD(c, INTERVAL 1 DAY)` |
| `sqlserver` | SQL Server (`tsql`) | `TOP (n)` or `OFFSET … FETCH NEXT n ROWS ONLY` | `[name]` | `DATEFROMPARTS(...)`, `DATEADD(day, 1, c)` |

Any other type (`oracle`, `sqlite`, `other`) is refused with `SQL_DIALECT_UNSUPPORTED` before any model call. The
dialect's conventions are given to the model, SQLGlot parses with the dialect, and syntax from another dialect is
rejected rather than rewritten: `LIMIT` in T-SQL, `TOP` in PostgreSQL or MySQL, `"x"` in MySQL (a string there),
backticks outside MySQL, square brackets outside SQL Server (PostgreSQL `ARRAY[...]` is allowed). The returned SQL
is SQLGlot's rendering in the same dialect, with any trailing semicolon removed.

## Context and permissions

The context contains: the intent (type, metrics, dimensions, attributes, filters, resolved time range with the
half-open boundaries, grouping, sorting, limit, assumptions, ambiguities); the intent's tables plus tables on the
shortest registered relationship paths between them (permitted tables only, at most `AI_SQL_MAX_SCHEMA_TABLES`;
bridge tables are marked "join path only"); only columns the user may see, with types, keys, descriptions and
business meaning; registered relationships between those tables; business terms the intent uses or that describe
those tables (definition, synonyms, related fields, aggregation, calculation rule); the permission context
(allowed tables and columns, operations, the user's own row restrictions, data scope); the dialect conventions and
the maximum row limit. It never contains credentials, connector details, other users' permissions, tables outside
the relevant set or hidden columns.

The LLM is not the security authority. Permission data in the prompt is guidance. The backend re-authorizes the
intent with the permission engine before the model call (no data access or a revoked table ends with
`permission_denied` and no model call), and authorizes the tables and columns the SQL actually references after
validation. Module 11 must still enforce table, column and row-level permissions, function allowlists, complexity
limits and the exact approved SQL identity. The generator never approves its own output.

## Preliminary validation (SQLGlot)

| Check | Result |
| --- | --- |
| Tokens: comments, extra semicolons, other-dialect tokens or quoting | `comment_not_allowed`, `multiple_statements` (blocking), `dialect_mismatch` |
| Parses in the dialect; exactly one statement; SELECT (WITH and set operations allowed) | `invalid_syntax`, `multiple_statements`, `statement_not_allowed` (blocking) |
| INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, TRUNCATE, GRANT, REVOKE, MERGE, EXEC/EXECUTE, CALL, PREPARE, SET, transactions, SELECT INTO, FOR UPDATE/SHARE, unparsed commands | `statement_not_allowed` (blocking) |
| Dynamic SQL and dangerous functions (`sp_executesql`, `xp_cmdshell`, `OPENROWSET`, `dblink`, `pg_sleep`, `SLEEP`, `load_file`, ...); system catalogs | `dangerous_function`, `system_catalog` (blocking) |
| Tables qualified `schema.table` and in the context | `unqualified_table`, `cross_database_reference`, `unknown_table`, `table_function_not_allowed` |
| Columns resolved by SQLGlot's qualifier against visible columns only (unknown, hidden and ambiguous columns fail) | `unknown_column` |
| No `SELECT *` / `t.*` (`COUNT(*)` is fine) | `select_star` |
| Joins: `ON a.x = b.y` following a registered relationship | `unregistered_join`, `join_without_condition`, `join_without_relationship`, `join_using_not_allowed` |
| `SUM`/`AVG` over numeric columns; non-aggregated selected columns grouped | `invalid_aggregation`, `missing_group_by` |
| Row limit: required unless the query returns one aggregate row; literal, at most `AI_SQL_MAX_ROW_LIMIT` and the intent's limit | `unbounded_result`, `row_limit_invalid`, `row_limit_too_large`, `row_limit_exceeds_intent` |
| The intent's filters, time-range column, metric columns and dimension columns appear | `filter_missing`, `time_filter_missing`, `metric_missing`, `dimension_missing` |
| The model's own reference lists name only supplied tables and visible columns | `declared_unknown_table`, `declared_unknown_column` |

Unknown, unregistered and restricted tables get the same generic message, so feedback never confirms that a
restricted table exists. Regular expressions are not used to understand SQL (the SQL display in the browser uses a
regex tokenizer for colours only).

**Limitations.** This is a preliminary check, not the security guard. It does not enforce row-level restrictions
(it passes them to the model as guidance and reports them in `permission_notes`), does not apply a function
allowlist (only a denylist of dangerous functions), does not measure query cost or complexity, does not verify the
semantic correctness of the computation beyond the coverage checks above, and does not check that literal values
match column types. Join checks consider equality conditions between registered tables; joins to CTEs or derived
tables are validated inside their own scope only.

## Controlled regeneration

Correctable issues (syntax, dialect, unknown tables or columns, joins, aggregation, grouping, limits, coverage,
malformed JSON, SQL in prose) trigger up to `AI_SQL_MAX_REGENERATIONS` extra model requests. The feedback is the
backend's own issue messages plus the previous reply; the context, the intent and the permissions never change, so
regeneration cannot widen access. Blocking issues (unsafe statements, several statements, dangerous functions,
system catalogs) are never regenerated: the query is discarded with `unsafe_sql`. When attempts run out the result
is `generation_failed` / `validation_failed` (or `malformed_output`). Manual regeneration
(`POST .../regenerate`) creates a new generation for the same stored intent, at most
`AI_SQL_MAX_MANUAL_REGENERATIONS` times per intent.

## Prompt versioning

`app/ai/sql_generation/prompts/sql_prompt.py` holds `PROMPT_VERSION` (`sql-generation-v1`), the system prompt and
the feedback wording. Every generation record and audit event stores the version. Change the version whenever the
prompt, the payload shape or the feedback changes. The prompt is never stored or logged; only its size is logged.

## Persistence and audit

* `business_intents` (migration `0008`): the validated intent from Module 09, keyed by `intent_id`, owned by the
  asking user, with schema version, status and policy revision. It includes the user's own question text, which is
  needed to show the question summary; it is only readable by that user through these APIs.
* `sql_generations`: the untrusted artifact. Check constraints pin `trust_level = 'untrusted'` and
  `security_status = 'awaiting_security_validation'`, require SQL exactly when the status is `generated` and store
  its SHA-256. Persisting a query never makes it executable. The full SQL is stored even when the user may not view
  it, so Module 11 can validate it. No prompt is stored.
* `sql_generation_events`: one row per request (generation, user, intent, schema and version, dialect, provider,
  model, latency, retry count, preliminary validation outcome, failure category, issue codes, regeneration flag).
  No SQL, question, prompt or credentials. The log event `ai.sql_generation.completed` carries the same fields.
  The model call is also recorded by Module 08 in `ai_usage_records` under the operation `sql_generation`.

## Frontend

**Administration → SQL generation** (`/admin/sql-generation`, needs `ai.sql.generate`; interpreting the question
also needs `ai.question.understand`): choose a schema, ask a question, and the page interprets it (Module 09) and
then generates SQL from the stored intent. It shows the question summary (original question, interpreted intent,
schema), the status **Generated — Awaiting Security Validation**, the SQL with syntax highlighting (only with SQL
view permission, with **Copy SQL**), dialect, referenced tables and columns, joins, assumptions, permission notes
and validation issues, **Regenerate SQL** and **View generation details** (re-read from the stored record). There is
no Execute button.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `AI_SQL_RATE_LIMIT_PER_USER` | `10` | Generate + regenerate requests per user per minute |
| `AI_SQL_MAX_REGENERATIONS` | `2` | Automatic regenerations after failed preliminary validation (0 to 3) |
| `AI_SQL_MAX_MANUAL_REGENERATIONS` | `3` | Manual regenerations per intent |
| `AI_SQL_MAX_OUTPUT_TOKENS` | `1500` | Output token ceiling per request |
| `AI_SQL_MAX_SCHEMA_TABLES` | `8` | Tables in the SQL context |
| `AI_SQL_MAX_ROW_LIMIT` | `1000` | Largest row limit a query may use |
| `AI_SQL_INTENT_MAX_AGE_MINUTES` | `60` | How long an intent may be used |

New dependency: `sqlglot==30.35.0` (pure Python).

## Testing

```bash
cd backend
pytest -q tests/sql_generation/test_preliminary_validation.py      # no database needed
TEST_DATABASE_URL=postgresql://... pytest -q tests/sql_generation  # API tests on PostgreSQL
cd ../frontend && npx vitest run src/pages/SqlGenerationPage.test.tsx
```

All model replies are mocked (`FakeProvider` over `httpx.MockTransport`); no NVIDIA key is needed. With a real key,
use the SQL generation page to check how a real model behaves.

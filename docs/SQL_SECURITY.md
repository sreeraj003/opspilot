# SQL security and execution guard (Module 11)

Module 10 produces **untrusted** SQL. Module 11 is the only component that can approve a query, and the only issuer
of the trusted request that Module 07's connector boundary will send to the company API. It is deterministic: no LLM
takes part in any decision here, and nothing the model said about its own SQL is trusted.

Code: `backend/app/ai/sql_security/` (parsers, validators, policies, rewriters, approvals, services, schemas).
Tests: `backend/tests/sql_security/`. Migration: `0009_create_sql_security_tables`.

## 1. Trust boundary

```
Module 10 (untrusted SQL, stored)        client (generation id only, never SQL)
        │                                        │
        ▼                                        ▼
POST /ai/sql/generations/{id}/validate ──► SQLSecurityValidationService
                                               │ 17-step pipeline (section 2)
                                               ▼
                         sql_security_validations (append-only)  +  sql_execution_approvals (one, short-lived)
                                               │
          internal call only (Module 12) ──► SQLExecutionGuard.authorize_execution
                                               │ lock, check binding, re-validate now, consume once
                                               ▼
                         ValidatedQueryRequest (HMAC-sealed, issuer sql_security_guard, exact SQL + hash)
                                               │
                                               ▼
                         ConnectorService.execute (Module 07): verify seal + hash, claim once, then HTTPX
```

- There is **no public endpoint that accepts SQL** and no execute endpoint. The four Module 11 routes validate a
  stored generation by id, read results, read an approval and revoke one.
- The frontend never decides anything: approval state shown in the browser is the server's answer, and there is no
  execute control.
- `ConnectorService.execute` accepts only two issuers: the fixed connector test (Module 07) and the guard. Every
  other issuer, a forged or altered seal, a hash that does not match the SQL, a missing binding field or an expired
  request is rejected before any HTTP request.

## 2. Validation pipeline

`SQLSecurityValidationService.validate` runs these steps in order and stops at the first that fails:

1. Load the **persisted** generation by id (never SQL from the request).
2. It must belong to the caller (otherwise 404, the same as a missing id).
3. The caller must still be able to use the schema source (Module 04 `PermissionView`).
4. The schema version must equal the generation's version (otherwise `validation_failed` / `schema_changed`).
5. Integrity: the stored SQL's SHA-256 must equal the stored fingerprint, and the generation must be `generated`.
6. Parse with SQLGlot in the generation's dialect (`parse_exact`): length limit, no control characters, no
   comments, at most one trailing semicolon, exactly one statement, no `Command` fallback.
7. Dialect: tokens of other dialects (`TOP` outside T-SQL, backticks outside MySQL, brackets outside T-SQL) fail.
8. Full AST inspection on an **allowlist** of node types (section 3), every scope, CTE and subquery included.
9. Statement type: the root must be one `SELECT` (or an allowed set operation of SELECTs).
10. Tables and columns: every table `schema.table`, registered, in an allowed namespace; every column resolved by
    SQLGlot's qualifier against the registry (unknown or ambiguous fail); quoted identifiers must match exactly.
11. Joins: each join's ON condition must contain at least one `a.col = b.col` that follows a registered relationship
    or an approved rule (`AI_SQL_GUARD_APPROVED_JOINS`); no CROSS, NATURAL, USING or condition-less joins; columns
    are traced through CTE and subquery projections to their physical source.
12. Functions: per-dialect allowlist, plus a denylist that can never be re-enabled (section 4).
13. Dynamic permissions: the exact referenced tables, columns and operation (`read` or `aggregate`) are authorized
    by Module 04's `AuthorizationEngine` **without its cache**, so a grant revoked a second ago is already gone.
14. Row-level restrictions are compiled from the policy DSL and applied to every access path (section 5).
15. Complexity limits (section 6).
16. Result limit: an existing `LIMIT`/`TOP`/`FETCH` must be a literal between 1 and the maximum; a non-aggregate
    query without one gets `LIMIT <max>` and a warning.
17. The rewritten SQL is rendered, **re-parsed and re-validated from scratch** (statement, references and the
    row-security post-condition), hashed, stored, and bound to a new single-use approval.

The result (`SQLSecurityValidationResult`) has `validation_id`, `generation_id`, `approved`, `status`,
`validation_errors`, `warnings`, `referenced_tables`, `referenced_columns`, `detected_functions`,
`applied_row_restrictions`, `schema_version`, `permission_version`, `query_hash` and `validated_at`. Statuses:
`approved`, `rejected`, `clarification_required`, `validation_failed`, `approval_expired`.

**Errors.** Users see short, fixed messages per category. Messages never name a table or column the user cannot
access (an inaccessible table and a non-existent table give the same message). The precise internal reason is
stored in `sql_security_validations.internal_reasons` and is returned only to holders of `ai.sql.security.audit`.

## 3. AST rules

Rejected anywhere in the tree (outer query, CTEs, subqueries, joins, set-operation branches):

- DML, DDL, DCL and TCL: `INSERT`, `UPDATE`, `DELETE`, `MERGE`, `CREATE`, `DROP`, `ALTER`, `TRUNCATE`, `GRANT`,
  `REVOKE`, `COMMIT`, `ROLLBACK`, `SET`, `USE`, `EXEC`/`CALL`, `COPY`, any parser `Command` fallback.
- `SELECT INTO`, locking (`FOR UPDATE`, `FOR SHARE`, `WITH (NOLOCK)` and other table hints), query hints, `DISTINCT
  ON`, `QUALIFY`, operation modifiers.
- Table-valued functions, `VALUES` sources, `LATERAL`, `UNNEST`, system schemas (`pg_catalog`,
  `information_schema`, `sys`, `mysql`, `performance_schema`), catalog- or database-qualified names.
- Recursive CTEs, CTE names that shadow a registered table, duplicate CTE names.
- `SELECT *` (only `COUNT(*)` is allowed).
- Window functions and set operations (`UNION`, `INTERSECT`, `EXCEPT`) unless enabled.
- Comments of any kind, stacked statements, control characters.

Anything not on the allowlist of node types is rejected as an unsafe construct, so new SQLGlot syntax fails closed.
Regex is never the primary parser; the token pass only catches comments and dialect markers before parsing.

## 4. Function allowlist

| Set | Functions |
| --- | --- |
| Every dialect | COUNT, SUM, AVG, MIN, MAX, ROUND, COALESCE, NULLIF, ABS, FLOOR, CEIL, GREATEST, LEAST |
| PostgreSQL | DATE_TRUNC, EXTRACT, CURRENT_DATE, CURRENT_TIMESTAMP, DATE, LOWER, UPPER, LENGTH, TRIM, SUBSTRING, CONCAT, YEAR, MONTH, DAY, QUARTER |
| MySQL | DATE_FORMAT, DATE, YEAR, MONTH, DAY, QUARTER, DATE_ADD, DATE_SUB, DATEDIFF, CURRENT_DATE, CURRENT_TIMESTAMP, EXTRACT, LOWER, UPPER, LENGTH, TRIM, SUBSTRING, CONCAT |
| SQL Server | YEAR, MONTH, DAY, DATEADD, DATEDIFF, DATEFROMPARTS, CURRENT_DATE, CURRENT_TIMESTAMP, LOWER, UPPER, LEN, TRIM, SUBSTRING, CONCAT |
| Window (opt-in) | ROW_NUMBER, RANK, DENSE_RANK, LAG, LEAD, PERCENT_RANK, CUME_DIST, FIRST_VALUE, LAST_VALUE |

Functions are identified by SQLGlot's parsed class (or the raw name for functions SQLGlot does not model), not by
text. `AI_SQL_GUARD_EXTRA_FUNCTIONS=postgres:date_part,*:sign` adds functions. The denylist (dynamic SQL, file and
network access, system and administrative functions, sleeps, locks, sequences, randomness, `version()`,
`current_user` and similar) always wins over the allowlist and over configuration.

## 5. Permissions and row-level security

Module 04 decides; the guard asks with the exact references of the parsed query. Explicit deny wins, expired and
revoked grants are ignored, no grant means deny, and the result is fresh for every validation and again at
execution time. A **permission fingerprint** (SHA-256 of the canonical decision: allowed columns, row filters, SQL
visibility, revision) is stored with the approval; any change in permissions changes it and invalidates the
approval at the execution boundary.

Row filters use the Module 04 DSL: `{column, operator, value, value_type}` conditions and `{logic: and|or|not,
conditions: [...]}` groups. Operators: `equals`, `not_equals`, `greater_than`, `greater_than_or_equal`,
`less_than`, `less_than_or_equal`, `in`, `not_in`, `between`, `is_null`, `is_not_null`. Values are built as SQLGlot
literals (never string concatenation); dates become typed casts, booleans become `1`/`0` in T-SQL.

**Enforcement on every access path.** Every reference to a restricted physical table, wherever it appears (outer
query, CTE body, subquery, `IN`/`EXISTS` subquery, either side of a join, each set-operation branch), is replaced by
a filtered derived table that keeps the query's alias:

```sql
-- generated
SELECT c.region, SUM(o.total_amount) FROM sales.orders AS o JOIN sales.customers AS c ON o.customer_id = c.id ...
-- approved
SELECT c.region, SUM(o.total_amount) FROM (SELECT o.customer_id, o.status, o.total_amount FROM sales.orders AS o
  WHERE o.status <> 'deleted') AS o JOIN (SELECT c.id, c.region FROM sales.customers AS c
  WHERE c.region = 'South') AS c ON o.customer_id = c.id ...
```

The filter therefore applies before any join, aggregation, `OR` in the outer `WHERE`, or outer join can widen the
rows. After rendering, the SQL is re-parsed and the guard verifies that every reference to every restricted table is
inside a wrapper carrying exactly its restriction. A filter that cannot be compiled (unknown column or operator,
wrong value type, a table alias with a column list) rejects the query (`row_security_unenforceable`); it is never
dropped.

## 6. Complexity and result limits

| Setting | Default | Meaning |
| --- | --- | --- |
| `AI_SQL_GUARD_MAX_SQL_LENGTH` | 20000 | characters of SQL parsed |
| `AI_SQL_GUARD_MAX_JOINS` | 6 | joins across all scopes |
| `AI_SQL_GUARD_MAX_TABLES` | 8 | physical table references |
| `AI_SQL_GUARD_MAX_NESTING_DEPTH` | 3 | subquery / CTE nesting |
| `AI_SQL_GUARD_MAX_CTES` | 5 | CTEs |
| `AI_SQL_GUARD_MAX_SUBQUERIES` | 6 | subqueries |
| `AI_SQL_GUARD_MAX_EXPRESSIONS` | 600 | AST nodes |
| `AI_SQL_GUARD_MAX_RESULT_ROWS` | 1000 | the largest allowed row limit |
| `AI_SQL_GUARD_MAX_EXECUTION_SECONDS` | 30 | longest acceptable connector timeout; approvals warn when the connector's own (Module 07) timeout is higher |

**Limit policy.** A query above any limit is rejected, never trimmed. A query returning one aggregate row needs no
limit. Any other query must have a literal limit of at most `MAX_RESULT_ROWS`; when it has none, the guard adds
`LIMIT <max>` (`TOP` is never added to non-T-SQL) and warns. `PERCENT`, `WITH TIES`, a non-literal limit, `OFFSET`
without a limit, or a limit above the maximum is rejected. The limit rewrite happens before hashing, so the approval
covers the limited SQL.

## 7. Approval lifecycle

```
            validate                    authorize_execution                ConnectorService.execute
 (none) ───────────────► approved ─────────────────────────► consumed ────────────────────────► executed
                           │ │ │
                  revoke   │ │ └─ re-validation differs ──► invalidated
                           │ └─── newer validation ───────► invalidated (superseded)
                           └───── TTL passes ─────────────► expired
```

- `sql_execution_approvals` stores the exact SQL, its SHA-256, user, connector, schema source and version,
  permission version and fingerprint, dialect, validation, generation and expiry
  (`AI_SQL_APPROVAL_TTL_SECONDS`, 300 s).
- A database trigger makes every bound field immutable and allows only the transitions above.
  `sql_security_validations` is append-only.
- `authorize_execution` locks the row (`SELECT ... FOR UPDATE`), checks user, connector, status, expiry and the
  caller's expected SQL hash, then **re-runs the whole pipeline now**. A different outcome, schema version,
  connector, permission fingerprint or SQL invalidates the approval. Otherwise a conditional `UPDATE` consumes it and
  the guard seals the request.
- The connector service claims the approval (`consumed → executed`) with a conditional `UPDATE` that also matches
  the execution reference, hash, user, connector and generation, **before** the HTTP request. Replays, concurrent
  attempts and copies of a used request match nothing and are rejected.

## 8. Audit

`sql_security_events` records `validation_completed`, `approval_created`, `approval_superseded`,
`approval_revoked`, `approval_expired`, `approval_invalidated`, `execution_authorized`, `execution_rejected` and
`execution_claimed`, with ids, hashes, schema and permission versions, status, category and violation codes.
**No SQL text, literals, question text or credentials** are written to events or logs. The exact SQL is kept only in
the approval row, which only the guard reads.

## 9. API

All under `/api/v1`, authenticated, rate limited (`AI_SQL_VALIDATION_RATE_LIMIT_PER_USER`, 30/min for validation)
and audited.

| Method and path | Permission | Purpose |
| --- | --- | --- |
| `POST /ai/sql/generations/{id}/validate` | `ai.sql.validate` | Run the guard on your stored generation; returns the result and, when approved, the approval |
| `GET /ai/sql/generations/{id}/validation` | `ai.sql.validate` | Latest validation of your generation |
| `GET /ai/sql/validations/{id}` | `ai.sql.validate` (owner) or `ai.sql.security.audit` | One validation; auditors also get internal reasons |
| `GET /ai/sql/approvals/{id}` | `ai.sql.validate` (owner) | Approval status, expiry and usability |
| `POST /ai/sql/approvals/{id}/revoke` | `ai.sql.validate` (owner) | Revoke an unused approval (409 otherwise) |

The approved SQL is returned only when the user's data permission allows SQL view.

## 10. What the company API must still do

The guard reduces risk; it cannot replace controls on the database side. The company GET API should:

- run queries with a **read-only database account** that can see only the intended schemas;
- enforce its own statement timeout (`AI_SQL_GUARD_MAX_EXECUTION_SECONDS`) and row/size limits;
- reject multiple statements and anything other than `SELECT` itself;
- authenticate OpsPilot with the connector credential and accept requests only from the backend.

## 11. Test coverage

`backend/tests/sql_security/`:

- `test_statements_and_functions.py`: every rejected statement type, multiple statements, comments, dialect
  confusion, locking, hints, `SELECT INTO`, system catalogs, table functions, recursive CTEs, function allowlist and
  denylist per dialect, window and set-operation switches, result limits and injection/evasion strings.
- `test_schema_and_row_security.py`: unknown and inaccessible tables and columns (same message), quoting tricks,
  CTE shadowing, cross-schema, invalid joins, nested references, RLS on the outer query, CTEs, subqueries, both join
  sides, `UNION` branches, `OR` bypass attempts, nested DSL, typed values, and unenforceable filters.
- `test_permissions_and_approvals.py`: API result, explicit deny, expired and revoked grants, deny by default,
  permission and row-filter changes after approval, ownership, SQL visibility, rate limit, auditor reasons, approval
  revoke, expiry, supersession and database immutability.
- `test_execution_boundary.py`: approved SQL reaches the mocked company API exactly once, replay, modified SQL,
  different user or connector, schema change, deactivated connector, expired approval, concurrent double
  consumption, tampered and forged trusted requests, raw SQL, no public execution endpoint, and the full Module 10 →
  Module 11 → connector flow.

All external calls go to `httpx.MockTransport`; no test touches a real database or company API.

## 12. Known limitations

- **No execution workflow yet.** `SQLExecutionGuard.execute` is an internal service; Module 12 will call it. The UI
  shows approvals but cannot run them.
- The guard validates SQL text, not runtime cost. Complexity limits are structural. The connector's HTTPX timeout
  (Module 07) bounds how long OpsPilot waits, but only the company API can stop a running query, so it must enforce
  its own statement timeout.
- Row filters on a table are wrapped as derived tables; databases with unusual optimizers may plan these queries
  differently than the original. Filters on columns of other tables (cross-table policies) are not supported and
  reject the query.
- Joins are accepted only through registered relationships or configured rules; legitimate non-equality joins
  (date ranges) are rejected.
- The function allowlist is intentionally small. Add functions per dialect with `AI_SQL_GUARD_EXTRA_FUNCTIONS` after
  reviewing them.
- T-SQL quoted identifiers are compared case-insensitively (SQL Server's default collation); other dialects compare
  them exactly.
- Rate limits are per backend process, like the other modules.
- Approval expiry is checked when an approval is read or used; there is no background job marking old rows
  `expired`.

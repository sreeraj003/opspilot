# Permission policy format (version 1)

A permission's `policy_definition` describes **what** it allows or denies, as structured JSON data. It is never
code: there are no expressions, no SQL fragments, and nothing in it is evaluated. Administrators normally build
policies with the form on **Permissions → Create permission**; this document is the reference for the stored
format, for API clients and for later modules (SQL security reads the normalized form).

The server validates every policy (`POST /api/v1/permissions`, `PATCH /api/v1/permissions/{id}` and
`POST /api/v1/permissions/validate-policy`) and stores it **normalized**: defaults filled in, columns qualified with
their table, value types explicit. The validator lives in `backend/app/authorization/policy.py`.

## Shapes by resource type and effect

| Resource type | Effect | Fields |
| --- | --- | --- |
| `database_data` | `allow` | `allowed_tables`, `allowed_columns`, `allowed_operations`, `row_filters`, `allow_export`, `allow_sql_view` |
| `database_data` | `deny` | `deny_all`, `denied_tables`, `denied_columns`, `excluded_rows`, `deny_export`, `deny_sql_view` |
| any other type (`reports`, `executive_reports`, …) | `allow` | `allow_export`, `allow_sql_view` |
| any other type | `deny` | `deny_all`, `deny_export`, `deny_sql_view` |
| `system` | n/a | Reserved for the ten built-in administration permissions; cannot be created or changed through the API. |

Every policy has `"version": 1` and `"effect": "allow" | "deny"` (default `allow`). Unknown fields are rejected.

### Allow (data)

```json
{
  "version": 1,
  "effect": "allow",
  "allowed_tables": ["sales"],
  "allowed_columns": ["sales.*"],
  "allowed_operations": ["aggregate", "read"],
  "row_filters": [
    { "column": "sales.region", "operator": "equals", "value": "Karnataka", "value_type": "string" }
  ],
  "allow_export": false,
  "allow_sql_view": false
}
```

- `allowed_tables`: 1 to 50 identifiers, `table` or `schema.table`, no duplicates.
- `allowed_columns`: `table.column` or `table.*` (every column). With a single table, an unqualified `amount` is
  accepted and normalized to `sales.amount`; with several tables each column must be qualified. Every allowed table
  needs at least one column.
- `allowed_operations`: `read` (individual rows) and/or `aggregate` (aggregate results only). OpsPilot never writes
  data, so there are no write operations.
- `row_filters`: conditions that **every** visible row must satisfy (AND-combined at the top level). Each top-level
  entry must concern a single table; use a group to allow any of several values.
- `allow_export`, `allow_sql_view`: default `false`.

### Deny (data)

```json
{
  "version": 1,
  "effect": "deny",
  "deny_all": false,
  "denied_tables": ["payroll"],
  "denied_columns": ["sales.customer_email"],
  "excluded_rows": [{ "column": "sales.region", "operator": "equals", "value": "Restricted" }],
  "deny_export": true,
  "deny_sql_view": false
}
```

A deny policy must deny something. `deny_all: true` denies the action on the resource entirely. Excluded-row
conditions always name columns as `table.column`.

## Conditions and groups

A **condition** is `{ "column", "operator", "value", "value_type" }`:

| Operator | Value | Value types |
| --- | --- | --- |
| `equals`, `not_equals` | one value | all |
| `in`, `not_in` | list of 1–100 values of one type, no duplicates | string, number, date, datetime |
| `greater_than`, `greater_than_or_equal`, `less_than`, `less_than_or_equal` | one value | number, date, datetime |
| `between` | `[low, high]`, low ≤ high (inclusive) | number, date, datetime |
| `is_null`, `is_not_null` | none | all |

Value types are `string`, `number`, `boolean`, `date` (`2026-01-31`) and `datetime` (ISO 8601). The type is inferred
from JSON for strings, numbers and booleans and must be given for dates. Text values are at most 500 characters;
numbers must be finite; `null` is not a value (use `is_null`).

A **group** is `{ "logic": "and" | "or", "conditions": [ ... ] }` and may contain conditions or further groups:

```json
{ "logic": "or", "conditions": [
  { "column": "sales.region", "operator": "equals", "value": "Karnataka" },
  { "column": "sales.region", "operator": "equals", "value": "Kerala" }
] }
```

Limits (configurable): nesting depth `AUTHORIZATION_POLICY_MAX_DEPTH` (default 4) and total conditions
`AUTHORIZATION_POLICY_MAX_CONDITIONS` (default 50).

## Semantic checks

Besides shape and types, the validator rejects:

- operators that do not suit the value type (for example `greater_than` on text);
- columns outside the permission's tables, and unqualified columns where qualification is required;
- duplicate tables, columns, list values, or duplicate conditions in the same group;
- **contradictions** among AND-combined conditions on one column, for example `region = 'A' AND region = 'B'`,
  `amount > 100 AND amount < 50`, or `x is_null AND x equals 1`;
- tables and columns unknown to the schema catalog. Until schema registration exists (a later module) the catalog
  knows no schemas and this check is skipped; the code path (`SchemaCatalog`) is in place.

Errors are returned with code `INVALID_POLICY` and a list of `{ "location": [...], "message": "..." }` details, for
example `["policy_definition", "row_filters", "0", "operator"]`.

## How policies combine (the engine)

`backend/app/authorization/engine.py` evaluates access with these rules, in order:

1. Inactive, deleted or unknown users are denied everything.
2. No matching permission means **deny** (deny by default).
3. Expired or revoked assignments, inactive or deleted roles and permissions, and expired permissions are ignored.
4. A **direct deny assignment** of a permission removes that permission key even when a role grants it.
5. Allow grants for the same resource and action are combined per table: a request is allowed when one grant's
   scope covers every requested column and operation. Row filters of different grants are OR-ed (each grant is an
   alternative scope); filters within one grant are AND-ed.
6. Deny-effect policies are applied after all allows and always win: `deny_all` removes the action, denied tables
   and columns are removed, `excluded_rows` are added as `NOT (...)` conditions to every affected table, and
   `deny_export` / `deny_sql_view` switch those flags off.
7. Administrator-only system permissions (`users:manage`, `settings:manage`, `roles:manage`, `permissions:manage`,
   `access:assign`) count only through the built-in **Administrator** role. Administrators get **no implicit data
   access**: reading business data always needs an explicit data permission.

The result is a structured `AuthorizationDecision` (`allowed`, `decision_reason`, `allowed_tables`,
`allowed_columns`, `denied_columns`, `allowed_operations`, `row_filters`, export and SQL-view flags, the policy
revision and evaluation time). It is never SQL: turning row filters into
SQL is the job of the SQL security module.

## Direct grants with an override

A permission assigned directly to a user may carry a `policy_override` that **narrows** a data allow policy (fewer
tables, columns or operations, extra row filters, flags switched off). An override can never widen access; it is
validated the same way and intersected with the base policy.

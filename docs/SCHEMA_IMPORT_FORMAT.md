# Schema import format (Module 06)

OpsPilot keeps a registry of **metadata** about your business databases: tables, columns, how they relate, and what
they mean. It never connects to those databases. You describe them in a JSON document and import it, either in the web
app (**Schemas → a schema → Import / Export**) or with `POST /api/v1/schemas/{id}/import`.

The same format is produced by **Export**, so the usual workflow is: export, edit the file, validate, import.

Imported text is stored as metadata only. Nothing in a document is ever executed: names must be plain identifiers,
data types must come from a fixed vocabulary, and descriptions are kept as text.

## A complete example

A small sales database in PostgreSQL, in the `sales` namespace:

```json
{
  "format_version": 1,
  "schema_name": "sales",
  "tables": [
    {
      "table_name": "customers",
      "description": "People and companies that buy from us",
      "business_purpose": "One row per billing account",
      "columns": [
        { "column_name": "id", "data_type": "integer", "is_primary_key": true, "description": "Customer identifier" },
        { "column_name": "name", "data_type": "varchar(200)", "is_nullable": false },
        { "column_name": "email", "data_type": "varchar(255)", "is_sensitive": true, "description": "Billing contact" },
        { "column_name": "region", "data_type": "varchar(50)", "business_meaning": "Sales region, e.g. Karnataka" },
        { "column_name": "created_at", "data_type": "timestamptz", "is_nullable": false }
      ]
    },
    {
      "table_name": "orders",
      "description": "Customer orders",
      "columns": [
        { "column_name": "id", "data_type": "bigint", "is_primary_key": true },
        { "column_name": "customer_id", "data_type": "integer", "is_foreign_key": true, "is_nullable": false },
        { "column_name": "status", "data_type": "varchar(20)", "business_meaning": "placed, shipped, delivered or cancelled" },
        { "column_name": "total_amount", "data_type": "numeric(12,2)", "business_meaning": "Gross order value incl. tax" },
        { "column_name": "placed_at", "data_type": "timestamp with time zone" }
      ]
    },
    {
      "schema_name": "finance",
      "table_name": "invoices",
      "columns": [
        { "column_name": "id", "data_type": "bigint", "is_primary_key": true },
        { "column_name": "order_id", "data_type": "bigint", "is_foreign_key": true },
        { "column_name": "issued_on", "data_type": "date" },
        { "column_name": "paid", "data_type": "boolean", "is_nullable": false }
      ]
    }
  ],
  "relationships": [
    {
      "source_table": "orders",
      "source_column": "customer_id",
      "target_table": "customers",
      "target_column": "id",
      "relationship_type": "many_to_one",
      "description": "Who placed the order"
    },
    {
      "source_schema": "finance",
      "source_table": "invoices",
      "source_column": "order_id",
      "target_table": "orders",
      "target_column": "id",
      "relationship_type": "one_to_one"
    }
  ],
  "business_glossary": [
    { "term": "GMV", "definition": "Gross merchandise value: the sum of orders.total_amount", "synonyms": ["gross sales"] },
    { "term": "Active customer", "definition": "A customer with at least one order in the last 90 days" }
  ]
}
```

## Top level

| Field | Required | Meaning |
| --- | --- | --- |
| `format_version` | no (default `1`) | Must be `1`. |
| `schema_name` | when a table or relationship does not name its own | Default namespace (PostgreSQL schema, SQL Server schema, MySQL database) for tables and relationship ends. |
| `tables` | no | List of tables, each with its columns. |
| `relationships` | no | List of relationships between registered columns. |
| `business_glossary` | no | Terms the AI should understand. **Omitted: the glossary is left as it is.** With `replace`, an empty list clears it. |
| `source`, `schema_version`, `exported_at` | no | Written by export for your information. Import ignores them; it never renames the schema or changes its database type. |

Unknown fields are rejected everywhere (a typo such as `"is_primary"` is an error, not silently ignored).

## Tables

| Field | Default | Rules |
| --- | --- | --- |
| `table_name` | required | Identifier (see below). Unique per namespace, case-insensitively. |
| `schema_name` | the document's `schema_name` | Identifier. |
| `description` | none | Up to 4000 characters. |
| `business_purpose` | none | Up to 4000 characters: what the business uses the table for. |
| `is_active` | `true` | Inactive tables are kept but left out of the AI's schema context. |
| `columns` | `[]` | List of columns. A table without columns is allowed (a warning). |

## Columns

| Field | Default | Rules |
| --- | --- | --- |
| `column_name` | required | Identifier, unique within its table. |
| `data_type` | required | A supported type (see below). Stored normalised: `VARCHAR(255)` becomes `varchar(255)`. |
| `description`, `business_meaning` | none | Up to 4000 characters each. |
| `is_primary_key` | `false` | A primary-key column cannot be nullable. Composite keys: mark several columns. |
| `is_foreign_key` | `false` | Informational. Declare the link itself under `relationships`. |
| `is_nullable` | `false` for primary keys, otherwise `true` | |
| `is_sensitive` | `false` | Sensitive columns (personal data, secrets) are left out of the AI representation unless explicitly requested. |
| `is_active` | `true` | |

## Relationships

| Field | Default | Rules |
| --- | --- | --- |
| `source_table`, `source_column` | required | The column holding the reference, e.g. `orders.customer_id`. |
| `target_table`, `target_column` | required | The referenced column, usually a primary key, e.g. `customers.id`. |
| `source_schema`, `target_schema` | the document's `schema_name` | |
| `relationship_type` | required | `one_to_one`, `one_to_many`, `many_to_one` or `many_to_many`, read from the source's side. |
| `description` | none | Up to 4000 characters. |
| `is_active` | `true` | |

Both ends must exist, either already registered or in the same document. A column cannot reference itself (a table can:
`employees.manager_id → employees.id` is fine). The two ends must have compatible types (both numbers, both text, …;
types in the `other` category are not compared). The same pair may be declared only once, in either direction.

## Names and data types

**Identifiers** (`schema_name`, `table_name`, `column_name`): letters, digits and underscores, starting with a letter or
underscore, at most 63 characters. Quoted names with spaces or punctuation are not supported, because later modules
generate SQL from these names. Names are matched case-insensitively.

**Data types**: a base type, optionally with one parameter list, `unsigned` and array brackets. Examples:
`integer`, `bigint unsigned`, `numeric(12,2)`, `varchar(255)`, `nvarchar(max)`, `text`, `uuid`, `boolean`, `date`,
`timestamp(3) with time zone`, `timestamptz`, `jsonb`, `integer[]`. The vocabulary covers PostgreSQL, MySQL/MariaDB,
SQL Server, Oracle and SQLite; the full list is in `backend/app/schema_registry/data_types.py`. Each type belongs to a
category (`number`, `string`, `boolean`, `date`, `datetime`, `other`) used for relationship and permission-policy
checks.

## Strategies

Nothing is ever overwritten silently. Tables match on `(schema_name, table_name)`, columns on `column_name` within
their table, relationships on their two end columns, and glossary terms on `term`, all case-insensitively.

| Strategy | Effect |
| --- | --- |
| `create_only` (API default) | Adds new tables with their columns, new relationships and new glossary terms. If the document lists anything already registered, the whole import is refused. |
| `merge` (web app default) | Adds what is new and updates what matches, changing **only the fields present in the document**. Registered items the document does not mention are kept. |
| `replace` | Makes the registry match the document: matched items take the document's values, omitted fields take their defaults, and registered items the document does not list are deleted (soft-deleted, history kept). Requires `confirm_replace: true`. |

Example: the registry has `orders.total_amount` with a description. Importing `{ "column_name": "total_amount",
"data_type": "numeric(14,2)" }` with `merge` changes the type and keeps the description; with `replace` it changes the
type and clears the description.

## The import request

```http
POST /api/v1/schemas/{schema_id}/import
Content-Type: application/json
Authorization: Bearer …

{
  "strategy": "merge",
  "dry_run": true,
  "confirm_replace": false,
  "expected_schema_version": 7,
  "document": { "schema_name": "sales", "tables": [ … ] }
}
```

- `dry_run: true` validates and plans without saving. The response says `valid`, lists every error and warning with
  its location (for example `["document", "tables", "1", "columns", "2", "data_type"]`), and counts what would be
  created, updated, left unchanged and removed. The web app always previews first.
- `dry_run: false` applies the plan in one transaction. An invalid document is refused with `422
  SCHEMA_VALIDATION_FAILED` and nothing changes.
- `expected_schema_version` (optional) refuses the import with `409 SCHEMA_VERSION_CONFLICT` if someone changed the
  schema after your preview.
- A successful import that changes something increases the schema version by one and records one history entry.
  Importing a file that matches the registry changes nothing and keeps the version.

Requires the `schema.import` permission. Export (`GET /api/v1/schemas/{id}/export`) requires `schema.export`.

## Limits

| Limit | Default | Setting |
| --- | --- | --- |
| Request size | 5 MiB | `SCHEMA_IMPORT_MAX_BYTES` (larger requests get `413 PAYLOAD_TOO_LARGE`) |
| JSON nesting depth | 10 | `SCHEMA_IMPORT_MAX_DEPTH` (`422 JSON_TOO_DEEP`) |
| Tables per schema | 2000 | `SCHEMA_MAX_TABLES` |
| Columns per table | 1000 | `SCHEMA_MAX_COLUMNS_PER_TABLE` |
| Relationships per schema | 5000 | `SCHEMA_MAX_RELATIONSHIPS` |
| Glossary terms | 500 | fixed |

Limits count the result after the import (registered plus new), so they also apply to tables added one by one.

## Validation messages

Errors block a change; warnings never do.

| Code | Severity | Meaning |
| --- | --- | --- |
| `invalid_identifier` | error | A name is not a plain identifier. |
| `invalid_data_type` | error | The data type is not in the vocabulary. |
| `duplicate_table`, `duplicate_column` | error | The same name twice (case-insensitive). |
| `primary_key_nullable` | error | A primary-key column marked nullable. |
| `dangling_relationship` | error | A relationship end that is neither registered nor in the document. |
| `self_referencing_column` | error | A column referencing itself. |
| `duplicate_relationship` | error | The same pair of columns twice, in either direction. |
| `incompatible_relationship_types` | error | For example `integer` → `varchar(255)`. |
| `limit_exceeded` | error | One of the limits above. |
| `table_without_columns` | warning | |
| `no_primary_key` | warning | |
| `referenced_column_not_primary_key` | warning | The target column is not a primary key. |
| `foreign_key_without_relationship` | warning | `is_foreign_key` is set but no relationship uses the column. |
| `relationship_inactive_endpoint` | warning | The relationship is active but one end is inactive (it is left out of the AI context). |
| `primary_key_inactive` | warning | |

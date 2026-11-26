import type {
  OutlineTable,
  SchemaChange,
  SchemaColumn,
  SchemaImportResult,
  SchemaOutline,
  SchemaRelationship,
  SchemaSource,
  SchemaSummary,
  SchemaTable,
  SchemaTableDetail,
} from "@/types/schemaRegistry";

export const SCHEMA_ID = "11111111-1111-4111-8111-111111111111";
export const ORDERS_ID = "22222222-2222-4222-8222-222222222222";
export const CUSTOMERS_ID = "33333333-3333-4333-8333-333333333333";
export const ORDER_PK = "44444444-4444-4444-8444-444444444441";
export const ORDER_CUSTOMER = "44444444-4444-4444-8444-444444444442";
export const ORDER_PLACED = "44444444-4444-4444-8444-444444444443";
export const CUSTOMER_PK = "55555555-5555-4555-8555-555555555551";
export const CUSTOMER_EMAIL = "55555555-5555-4555-8555-555555555552";
export const RELATIONSHIP_ID = "66666666-6666-4666-8666-666666666666";

const at = "2026-11-20T08:00:00Z";

export function schemaPage<T>(items: T[], total = items.length) {
  return { items, total, offset: 0, limit: 25 };
}

export function makeSchema(overrides: Partial<SchemaSource> = {}): SchemaSource {
  return {
    id: SCHEMA_ID,
    name: "Sales DB",
    description: "Orders and customers",
    database_type: "postgresql",
    dialect: "postgresql",
    schema_version: 3,
    metadata_updated_at: at,
    is_active: true,
    business_glossary: [{ term: "GMV", definition: "Gross merchandise value", synonyms: ["gross sales"] }],
    created_by: null,
    created_at: at,
    updated_at: at,
    table_count: 2,
    column_count: 5,
    relationship_count: 1,
    ...overrides,
  };
}

export function makeTable(overrides: Partial<SchemaTable> = {}): SchemaTable {
  return {
    id: ORDERS_ID,
    schema_source_id: SCHEMA_ID,
    schema_name: "sales",
    table_name: "orders",
    qualified_name: "sales.orders",
    description: "Customer orders",
    business_purpose: null,
    is_active: true,
    created_at: at,
    updated_at: at,
    column_count: 3,
    relationship_count: 1,
    ...overrides,
  };
}

export function makeColumn(overrides: Partial<SchemaColumn> = {}): SchemaColumn {
  return {
    id: ORDER_PK,
    table_id: ORDERS_ID,
    column_name: "id",
    data_type: "integer",
    data_type_category: "number",
    description: "Order identifier",
    business_meaning: null,
    is_primary_key: true,
    is_foreign_key: false,
    is_nullable: false,
    is_sensitive: false,
    is_active: true,
    ordinal_position: 1,
    created_at: at,
    updated_at: at,
    ...overrides,
  };
}

export function makeOrdersDetail(): SchemaTableDetail {
  return {
    ...makeTable(),
    columns: [
      makeColumn(),
      makeColumn({
        id: ORDER_CUSTOMER,
        column_name: "customer_id",
        is_primary_key: false,
        is_foreign_key: true,
        description: null,
        ordinal_position: 2,
      }),
      makeColumn({
        id: ORDER_PLACED,
        column_name: "placed_at",
        data_type: "timestamptz",
        data_type_category: "datetime",
        is_primary_key: false,
        is_nullable: true,
        is_sensitive: true,
        description: null,
        ordinal_position: 3,
      }),
    ],
  };
}

export function makeSummary(overrides: Partial<SchemaSummary> = {}): SchemaSummary {
  return {
    schema_id: SCHEMA_ID,
    name: "Sales DB",
    database_type: "postgresql",
    dialect: "postgresql",
    is_active: true,
    schema_version: 3,
    metadata_updated_at: at,
    counts: {
      tables: 2,
      active_tables: 2,
      columns: 5,
      active_columns: 5,
      primary_key_columns: 2,
      foreign_key_columns: 1,
      sensitive_columns: 1,
      relationships: 1,
      active_relationships: 1,
      schema_namespaces: ["sales"],
      glossary_terms: 1,
    },
    issues: [],
    ...overrides,
  };
}

export const OUTLINE_TABLES: OutlineTable[] = [
  {
    id: CUSTOMERS_ID,
    schema_name: "sales",
    table_name: "customers",
    is_active: true,
    columns: [
      {
        id: CUSTOMER_PK,
        column_name: "id",
        data_type: "integer",
        data_type_category: "number",
        is_primary_key: true,
        is_foreign_key: false,
        is_nullable: false,
        is_sensitive: false,
        is_active: true,
      },
      {
        id: CUSTOMER_EMAIL,
        column_name: "email",
        data_type: "varchar(255)",
        data_type_category: "string",
        is_primary_key: false,
        is_foreign_key: false,
        is_nullable: true,
        is_sensitive: true,
        is_active: true,
      },
    ],
  },
  {
    id: ORDERS_ID,
    schema_name: "sales",
    table_name: "orders",
    is_active: true,
    columns: [
      {
        id: ORDER_PK,
        column_name: "id",
        data_type: "integer",
        data_type_category: "number",
        is_primary_key: true,
        is_foreign_key: false,
        is_nullable: false,
        is_sensitive: false,
        is_active: true,
      },
      {
        id: ORDER_CUSTOMER,
        column_name: "customer_id",
        data_type: "integer",
        data_type_category: "number",
        is_primary_key: false,
        is_foreign_key: true,
        is_nullable: false,
        is_sensitive: false,
        is_active: true,
      },
      {
        id: ORDER_PLACED,
        column_name: "placed_at",
        data_type: "timestamptz",
        data_type_category: "datetime",
        is_primary_key: false,
        is_foreign_key: false,
        is_nullable: true,
        is_sensitive: false,
        is_active: false,
      },
    ],
  },
];

export function makeOutline(): SchemaOutline {
  return { schema_id: SCHEMA_ID, schema_version: 3, tables: OUTLINE_TABLES };
}

export function makeRelationship(overrides: Partial<SchemaRelationship> = {}): SchemaRelationship {
  return {
    id: RELATIONSHIP_ID,
    schema_source_id: SCHEMA_ID,
    source: {
      table_id: ORDERS_ID,
      column_id: ORDER_CUSTOMER,
      schema_name: "sales",
      table_name: "orders",
      column_name: "customer_id",
      data_type: "integer",
      is_active: true,
    },
    target: {
      table_id: CUSTOMERS_ID,
      column_id: CUSTOMER_PK,
      schema_name: "sales",
      table_name: "customers",
      column_name: "id",
      data_type: "integer",
      is_active: true,
    },
    relationship_type: "many_to_one",
    description: "Who placed the order",
    is_active: true,
    created_at: at,
    updated_at: at,
    ...overrides,
  };
}

export function makeChange(overrides: Partial<SchemaChange> = {}): SchemaChange {
  return {
    id: "77777777-7777-4777-8777-777777777777",
    event_type: "table_created",
    entity_type: "table",
    entity_id: ORDERS_ID,
    schema_version: 2,
    version_incremented: true,
    summary: "Added table sales.orders with 3 columns",
    details: {},
    actor_user_id: null,
    actor_name: "Installation Admin",
    created_at: at,
    ...overrides,
  };
}

const zero = { created: 0, updated: 0, unchanged: 0, removed: 0 };

export function makeImportResult(overrides: Partial<SchemaImportResult> = {}): SchemaImportResult {
  return {
    strategy: "merge",
    dry_run: true,
    applied: false,
    valid: true,
    schema_version_before: 3,
    schema_version_after: 3,
    version_incremented: false,
    counts: { tables: { ...zero, created: 1 }, columns: { ...zero, created: 2 }, relationships: zero, glossary_terms: zero },
    changes: [{ action: "create", entity_type: "table", name: "sales.invoices", fields: [] }],
    changes_truncated: false,
    errors: [],
    warnings: [],
    ...overrides,
  };
}

/** The standard error envelope with validation details. */
export function validationErrorResponse(status: number, code: string, message: string, details: unknown): Response {
  return new Response(JSON.stringify({ success: false, error: { code, message, details, request_id: "req-test" } }), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

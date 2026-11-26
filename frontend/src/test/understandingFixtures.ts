import { SCHEMA_ID } from "@/test/schemaFixtures";
import type { BusinessIntent, BusinessTerm, PreviewResponse, SchemaFields, UnderstandingOptions } from "@/types/understanding";

export const TERM_ID = "77777777-7777-4777-8777-777777777777";
export const INTENT_ID = "88888888-8888-4888-8888-888888888888";

const at = "2026-11-20T08:00:00Z";

export function makeTerm(overrides: Partial<BusinessTerm> = {}): BusinessTerm {
  return {
    id: TERM_ID,
    schema_source_id: SCHEMA_ID,
    schema_source_name: "Sales DB",
    name: "Revenue",
    description: "Sum of order totals excluding cancelled orders",
    kind: "metric",
    synonyms: ["turnover"],
    related_tables: ["sales.orders"],
    related_columns: ["sales.orders.total_amount"],
    default_aggregation: "sum",
    aggregation_guidance: null,
    is_active: true,
    stale_references: [],
    created_at: at,
    updated_at: at,
    ...overrides,
  };
}

export function makeSchemaFields(): SchemaFields {
  return {
    schema_source_id: SCHEMA_ID,
    schema_source_name: "Sales DB",
    schema_version: 3,
    tables: [
      {
        table: "sales.orders",
        description: "Customer orders",
        columns: [
          {
            name: "id",
            data_type: "integer",
            description: null,
            sensitive: false,
          },
          {
            name: "total_amount",
            data_type: "decimal(12,2)",
            description: "Order value",
            sensitive: false,
          },
          {
            name: "status",
            data_type: "varchar(20)",
            description: null,
            sensitive: false,
          },
        ],
      },
      {
        table: "sales.customers",
        description: null,
        columns: [
          {
            name: "region",
            data_type: "varchar(50)",
            description: null,
            sensitive: false,
          },
        ],
      },
    ],
  };
}

export function makeOptions(overrides: Partial<UnderstandingOptions> = {}): UnderstandingOptions {
  return {
    timezone: "UTC",
    timezone_source: "default",
    timezone_setting_key: "business.timezone",
    max_question_chars: 1000,
    sources: [
      {
        id: SCHEMA_ID,
        name: "Sales DB",
        database_type: "postgresql",
        schema_version: 3,
      },
    ],
    ...overrides,
  };
}

export function makeIntent(overrides: Partial<BusinessIntent> = {}): BusinessIntent {
  return {
    intent_id: INTENT_ID,
    original_question: "Top five products by revenue last month",
    normalized_question: "top five products by revenue last month",
    intent_type: "ranking",
    confidence: 0.9,
    requires_clarification: false,
    schema_source_id: SCHEMA_ID,
    schema_version: 3,
    requested_metrics: [
      {
        name: "revenue",
        term_id: TERM_ID,
        term_name: "Revenue",
        column: { table: "sales.orders", column: "total_amount" },
        aggregation: "sum",
        definition_source: "business_term",
      },
    ],
    requested_dimensions: [
      {
        name: "product",
        term_id: null,
        column: { table: "sales.products", column: "name" },
        time_granularity: null,
      },
    ],
    requested_attributes: [],
    candidate_tables: ["sales.orders", "sales.products"],
    candidate_columns: ["sales.orders.total_amount", "sales.products.name"],
    filters: [
      {
        field: { table: "sales.orders", column: "status" },
        operator: "equals",
        value: "paid",
        value_type: "string",
      },
    ],
    time: {
      time_range: {
        label: "last month",
        start_date: "2026-10-01",
        end_date: "2026-11-01",
        start_at: "2026-10-01T00:00:00Z",
        end_at: "2026-11-01T00:00:00Z",
        boundary: "start_inclusive_end_exclusive",
        expression: "last month",
        kind: "last_month",
        quantity: null,
        timezone: "UTC",
        is_rolling: false,
        is_partial: false,
      },
      comparison_period: null,
      time_granularity: null,
      date_column: { table: "sales.orders", column: "placed_at" },
    },
    aggregation: {
      aggregation_type: "sum",
      grouping_dimensions: ["product"],
      sorting: [{ target: "revenue", direction: "desc" }],
      limit: 5,
    },
    business_context: null,
    assumptions: ["Revenue uses its registered aggregation: sum."],
    ambiguities: [],
    clarification_question: null,
    permission_context: {
      status: "allowed",
      policy_revision: 4,
      tables: [],
      export_allowed: false,
      row_level_restrictions: true,
      restriction_reason: null,
      is_authorization_token: false,
    },
    ...overrides,
  };
}

export function makePreview(overrides: Partial<PreviewResponse> = {}): PreviewResponse {
  return {
    request_id: "req-1",
    intent_id: INTENT_ID,
    status: "understood",
    schema_source_id: SCHEMA_ID,
    schema_version: 3,
    confidence: 0.9,
    intent: makeIntent(),
    clarification: {
      requires_clarification: false,
      reason: null,
      missing_information: [],
      clarification_question: null,
      key: null,
    },
    validation: { status: "passed", correction_attempts: 0, issues: [] },
    permission: {
      status: "allowed",
      export_allowed: false,
      row_level_restrictions: true,
      reason: null,
    },
    explanation: "Ranking of revenue by product for last month.",
    provider: "nvidia",
    model: "test-model",
    latency_ms: 420,
    diagnostics: {
      timezone: "UTC",
      timezone_source: "default",
      reference_time: "2026-11-26T10:00:00Z",
      prompt_version: "understanding-v1",
      schema_selection: {
        strategy: "all_tables",
        selected_tables: ["sales.orders", "sales.products"],
        scores: [],
        truncated: false,
      },
      matched_terms: [{ id: TERM_ID, name: "Revenue", matched_text: "revenue" }],
      terms_in_context: 1,
      prompt_chars: 2400,
      llm_attempts: 1,
    },
    ...overrides,
  };
}

export function clarificationPreview(): PreviewResponse {
  return makePreview({
    status: "needs_clarification",
    confidence: 0.4,
    intent: makeIntent({
      requires_clarification: true,
      clarification_question: "'sales' can mean Gross sales or Net sales. Which one do you mean?",
    }),
    clarification: {
      requires_clarification: true,
      reason: "ambiguous_term",
      missing_information: ["term_meaning"],
      clarification_question: "'sales' can mean Gross sales or Net sales. Which one do you mean?",
      key: "ambiguous_term:sales",
    },
    explanation: "The question needs one more detail before it can be interpreted.",
  });
}

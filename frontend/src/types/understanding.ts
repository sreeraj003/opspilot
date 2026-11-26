/** Module 09: business terminology and question understanding (mirrors the backend contracts). */

export type TermKind = "metric" | "dimension" | "attribute";
export type AggregationType = "sum" | "count" | "count_distinct" | "average" | "min" | "max" | "none";

export interface BusinessTerm {
  id: string;
  schema_source_id: string;
  schema_source_name: string | null;
  name: string;
  description: string;
  kind: TermKind;
  synonyms: string[];
  related_tables: string[];
  related_columns: string[];
  default_aggregation: AggregationType | null;
  aggregation_guidance: string | null;
  is_active: boolean;
  stale_references: string[];
  created_at: string;
  updated_at: string;
}

export interface BusinessTermPage {
  items: BusinessTerm[];
  total: number;
  offset: number;
  limit: number;
}

export interface BusinessTermListParams {
  offset: number;
  limit: number;
  schema_source_id?: string;
  search?: string;
  kind?: TermKind | "";
  is_active?: "true" | "false" | "";
}

export interface BusinessTermInput {
  schema_source_id: string;
  name: string;
  description: string;
  kind: TermKind;
  synonyms: string[];
  related_tables: string[];
  related_columns: string[];
  default_aggregation: AggregationType | null;
  aggregation_guidance: string | null;
  is_active: boolean;
}

export type BusinessTermUpdate = Partial<Omit<BusinessTermInput, "schema_source_id">>;

export interface SchemaFieldColumn {
  name: string;
  data_type: string;
  description: string | null;
  sensitive: boolean;
}

export interface SchemaFieldTable {
  table: string;
  description: string | null;
  columns: SchemaFieldColumn[];
}

export interface SchemaFields {
  schema_source_id: string;
  schema_source_name: string;
  schema_version: number;
  tables: SchemaFieldTable[];
}

// --- question understanding ---------------------------------------------------------------------------------

export type IntentType =
  | "metric_lookup"
  | "aggregation"
  | "comparison"
  | "trend_analysis"
  | "ranking"
  | "filtering"
  | "distribution"
  | "detail_lookup"
  | "unknown";

export interface FieldReference {
  table: string;
  column: string;
}

export interface MetricRequest {
  name: string;
  term_id: string | null;
  term_name: string | null;
  column: FieldReference | null;
  aggregation: AggregationType;
  definition_source: "business_term" | "schema_column";
}

export interface DimensionRequest {
  name: string;
  term_id: string | null;
  column: FieldReference;
  time_granularity: string | null;
}

export interface IntentFilter {
  field: FieldReference;
  operator: string;
  value: string | number | boolean | (string | number)[] | null;
  value_type: string;
}

export interface ResolvedPeriod {
  label: string;
  start_date: string;
  end_date: string;
  start_at: string;
  end_at: string;
  boundary: "start_inclusive_end_exclusive";
}

export interface TimeRange extends ResolvedPeriod {
  expression: string;
  kind: string;
  quantity: number | null;
  timezone: string;
  is_rolling: boolean;
  is_partial: boolean;
}

export interface ComparisonPeriod extends ResolvedPeriod {
  kind: string;
}

export interface TablePermission {
  table: string;
  operations: string[];
  row_filter: Record<string, unknown> | null;
  allow_export: boolean;
}

export interface BusinessIntent {
  intent_id: string;
  original_question: string;
  normalized_question: string;
  intent_type: IntentType;
  confidence: number;
  requires_clarification: boolean;
  schema_source_id: string;
  schema_version: number;
  requested_metrics: MetricRequest[];
  requested_dimensions: DimensionRequest[];
  requested_attributes: { column: FieldReference }[];
  candidate_tables: string[];
  candidate_columns: string[];
  filters: IntentFilter[];
  time: {
    time_range: TimeRange | null;
    comparison_period: ComparisonPeriod | null;
    time_granularity: string | null;
    date_column: FieldReference | null;
  };
  aggregation: {
    aggregation_type: AggregationType | null;
    grouping_dimensions: string[];
    sorting: { target: string; direction: "asc" | "desc" }[];
    limit: number | null;
  };
  business_context: string | null;
  assumptions: string[];
  ambiguities: string[];
  clarification_question: string | null;
  permission_context: {
    status: "allowed" | "denied" | "no_data_access";
    policy_revision: number;
    tables: TablePermission[];
    export_allowed: boolean;
    row_level_restrictions: boolean;
    restriction_reason: string | null;
    is_authorization_token: false;
  };
}

export type UnderstandingStatus = "understood" | "needs_clarification" | "access_denied" | "rejected";
export type ValidationStatus = "passed" | "corrected" | "rejected" | "not_run";

export interface UnderstandingResponse {
  request_id: string | null;
  intent_id: string;
  status: UnderstandingStatus;
  schema_source_id: string;
  schema_version: number;
  confidence: number | null;
  intent: BusinessIntent | null;
  clarification: {
    requires_clarification: boolean;
    reason: string | null;
    missing_information: string[];
    clarification_question: string | null;
    key: string | null;
  };
  validation: {
    status: ValidationStatus;
    correction_attempts: number;
    issues: { code: string; message: string; location: string[] }[];
  };
  permission: {
    status: "allowed" | "denied" | "no_data_access";
    export_allowed: boolean;
    row_level_restrictions: boolean;
    reason: string | null;
  };
  explanation: string;
  provider: string | null;
  model: string | null;
  latency_ms: number;
}

export interface PreviewResponse extends UnderstandingResponse {
  diagnostics: {
    timezone: string;
    timezone_source: string;
    reference_time: string;
    prompt_version: string;
    schema_selection: {
      strategy: string;
      selected_tables: string[];
      scores: { table: string; score: number; reasons: string[] }[];
      truncated: boolean;
    } | null;
    matched_terms: { id: string; name: string; matched_text: string }[];
    terms_in_context: number;
    prompt_chars: number;
    llm_attempts: number;
  };
}

export interface UnderstandRequest {
  question: string;
  schema_source_id: string;
  answered_clarifications?: string[];
}

export interface UnderstandingOptions {
  timezone: string;
  timezone_source: "setting" | "environment" | "default";
  timezone_setting_key: string;
  max_question_chars: number;
  sources: {
    id: string;
    name: string;
    database_type: string;
    schema_version: number;
  }[];
}

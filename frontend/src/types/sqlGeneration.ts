/** Module 10: SQL generation (mirrors the backend contracts). Generated SQL is untrusted and never executed here. */

export type GenerationStatus = "generated" | "clarification_required" | "generation_failed";
export type PreliminaryValidationStatus = "passed" | "failed" | "not_run";
export type FailureCategory =
  | "validation_failed"
  | "unsafe_sql"
  | "permission_denied"
  | "malformed_output"
  | "provider_error"
  | "model_declined";

export interface JoinUsed {
  left: string;
  right: string;
  join_type: string;
  registered_relationship: boolean;
}

export interface SqlValidationIssue {
  code: string;
  message: string;
  blocking: boolean;
}

export interface QuestionSummary {
  original_question: string;
  normalized_question: string;
  intent_type: string;
  explanation: string;
  schema_source_id: string | null;
  schema_name: string | null;
  metrics: string[];
  dimensions: string[];
  filters: string[];
  time_range: string | null;
  limit: number | null;
  assumptions: string[];
}

export interface SqlGeneration {
  generation_id: string;
  status: GenerationStatus;
  /** Null unless generated and the user's data permissions allow viewing SQL. */
  sql: string | null;
  sql_visible: boolean;
  dialect: "postgres" | "mysql" | "tsql";
  dialect_name: string;
  referenced_tables: string[];
  referenced_columns: string[];
  joins_used: JoinUsed[];
  assumptions: string[];
  explanation: string | null;
  clarification_required: boolean;
  clarification_question: string | null;
  permission_notes: string[];
  generation_model: string | null;
  provider: string | null;
  schema_source_id: string | null;
  schema_version: number;
  intent_id: string;
  validation_status: PreliminaryValidationStatus;
  validation_issues: SqlValidationIssue[];
  failure_category: FailureCategory | null;
  generation_attempts: number;
  prompt_version: string;
  regenerated_from_id: string | null;
  sql_sha256: string | null;
  security_status: "awaiting_security_validation";
  trust_level: "untrusted";
  is_executable: false;
  latency_ms: number;
  question: QuestionSummary | null;
  regenerations_remaining: number;
  created_at: string | null;
}

export interface GenerateSqlRequest {
  intent_id: string;
  schema_source_id: string;
}

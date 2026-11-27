/** Module 11: SQL security guard results. The server decides; nothing here grants execution. */

export type ValidationStatus = "approved" | "rejected" | "clarification_required" | "validation_failed" | "approval_expired";

export type ApprovalStatus = "approved" | "consumed" | "executed" | "revoked" | "expired" | "invalidated";

export interface ValidationMessage {
  code: string;
  message: string;
}

export interface InternalReason {
  code: string;
  category: string;
  reason: string;
}

export interface SqlApproval {
  approval_id: string;
  validation_id: string;
  generation_id: string;
  connector_id: string;
  schema_source_id: string;
  schema_version: number;
  permission_version: number;
  exact_sql_hash: string;
  dialect: string;
  status: ApprovalStatus;
  approved_at: string;
  expires_at: string;
  consumed_at: string | null;
  revoked_at: string | null;
  expires_in_seconds: number;
  is_usable: boolean;
}

export interface SqlSecurityValidation {
  validation_id: string;
  generation_id: string;
  approved: boolean;
  status: ValidationStatus;
  validation_errors: ValidationMessage[];
  warnings: string[];
  referenced_tables: string[];
  referenced_columns: string[];
  detected_functions: string[];
  applied_row_restrictions: string[];
  schema_version: number | null;
  permission_version: number | null;
  query_hash: string | null;
  validated_at: string;
  rejection_category: string | null;
  summary: string;
  dialect: string | null;
  connector_id: string | null;
  /** The exact SQL the approval covers (row restrictions included), only when the user may view SQL. */
  approved_sql: string | null;
  sql_visible: boolean;
  approval: SqlApproval | null;
  /** Always false in Module 11: no execution workflow exists yet. */
  execution_available: boolean;
  /** Only for security auditors. */
  internal_reasons: InternalReason[] | null;
}

import { SCHEMA_ID } from "@/test/schemaFixtures";
import { GENERATION_ID } from "@/test/sqlGenerationFixtures";
import type { SqlApproval, SqlSecurityValidation } from "@/types/sqlSecurity";

export const VALIDATION_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
export const APPROVAL_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
export const CONNECTOR_ID = "dddddddd-dddd-4ddd-8ddd-dddddddddddd";
export const QUERY_HASH = "e".repeat(64);

export const APPROVED_SQL =
  "SELECT p.name, SUM(o.total_amount) AS revenue FROM (SELECT o.product_id, o.status, o.total_amount FROM sales.orders AS o " +
  "WHERE o.status <> 'deleted') AS o JOIN sales.products AS p ON o.product_id = p.id WHERE o.status = 'paid' " +
  "GROUP BY p.name ORDER BY revenue DESC LIMIT 5";

export function makeApproval(overrides: Partial<SqlApproval> = {}): SqlApproval {
  return {
    approval_id: APPROVAL_ID,
    validation_id: VALIDATION_ID,
    generation_id: GENERATION_ID,
    connector_id: CONNECTOR_ID,
    schema_source_id: SCHEMA_ID,
    schema_version: 3,
    permission_version: 7,
    exact_sql_hash: QUERY_HASH,
    dialect: "postgres",
    status: "approved",
    approved_at: "2026-11-27T10:00:00Z",
    expires_at: "2026-11-27T10:05:00Z",
    consumed_at: null,
    revoked_at: null,
    expires_in_seconds: 300,
    is_usable: true,
    ...overrides,
  };
}

export function makeValidation(overrides: Partial<SqlSecurityValidation> = {}): SqlSecurityValidation {
  return {
    validation_id: VALIDATION_ID,
    generation_id: GENERATION_ID,
    approved: true,
    status: "approved",
    validation_errors: [],
    warnings: [],
    referenced_tables: ["sales.orders", "sales.products"],
    referenced_columns: ["sales.orders.total_amount", "sales.products.name"],
    detected_functions: ["SUM"],
    applied_row_restrictions: ["sales.orders"],
    schema_version: 3,
    permission_version: 7,
    query_hash: QUERY_HASH,
    validated_at: "2026-11-27T10:00:00Z",
    rejection_category: null,
    summary: "The query passed every security check and is approved for one execution.",
    dialect: "postgres",
    connector_id: CONNECTOR_ID,
    approved_sql: APPROVED_SQL,
    sql_visible: true,
    approval: makeApproval(),
    execution_available: false,
    internal_reasons: null,
    ...overrides,
  };
}

export function makeRejection(overrides: Partial<SqlSecurityValidation> = {}): SqlSecurityValidation {
  return makeValidation({
    approved: false,
    status: "rejected",
    validation_errors: [{ code: "statement_not_allowed", message: "Only a single read-only SELECT query is allowed." }],
    applied_row_restrictions: [],
    query_hash: null,
    rejection_category: "statement",
    summary: "The query was rejected by the security guard.",
    approved_sql: null,
    approval: null,
    ...overrides,
  });
}

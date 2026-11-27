import { SCHEMA_ID } from "@/test/schemaFixtures";
import { INTENT_ID, makePreview } from "@/test/understandingFixtures";
import type { SqlGeneration } from "@/types/sqlGeneration";
import type { UnderstandingResponse } from "@/types/understanding";

export const GENERATION_ID = "99999999-9999-4999-8999-999999999999";
export const REGENERATION_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";

export const RANKING_SQL = `SELECT
  p.name,
  SUM(o.total_amount) AS revenue
FROM sales.orders AS o
JOIN sales.products AS p
  ON o.product_id = p.id
WHERE o.status = 'paid'
GROUP BY p.name
ORDER BY revenue DESC
LIMIT 5`;

export function makeUnderstanding(overrides: Partial<UnderstandingResponse> = {}): UnderstandingResponse {
  const { diagnostics: _diagnostics, ...response } = makePreview();
  return { ...response, ...overrides };
}

export function makeGeneration(overrides: Partial<SqlGeneration> = {}): SqlGeneration {
  return {
    generation_id: GENERATION_ID,
    status: "generated",
    sql: RANKING_SQL,
    sql_visible: true,
    dialect: "postgres",
    dialect_name: "PostgreSQL",
    referenced_tables: ["sales.orders", "sales.products"],
    referenced_columns: ["sales.orders.product_id", "sales.orders.status", "sales.orders.total_amount", "sales.products.id", "sales.products.name"],
    joins_used: [{ left: "sales.orders.product_id", right: "sales.products.id", join_type: "INNER", registered_relationship: true }],
    assumptions: ["Revenue is the sum of paid order totals."],
    explanation: "Sums paid order totals per product and keeps the five largest.",
    clarification_required: false,
    clarification_question: null,
    permission_notes: [],
    generation_model: "test-model",
    provider: "nvidia",
    schema_source_id: SCHEMA_ID,
    schema_version: 3,
    intent_id: INTENT_ID,
    validation_status: "passed",
    validation_issues: [],
    failure_category: null,
    generation_attempts: 1,
    prompt_version: "sql-generation-v1",
    regenerated_from_id: null,
    sql_sha256: "ab".repeat(32),
    security_status: "awaiting_security_validation",
    trust_level: "untrusted",
    is_executable: false,
    latency_ms: 840,
    question: {
      original_question: "Top five products by revenue last month",
      normalized_question: "top five products by revenue last month",
      intent_type: "ranking",
      explanation: "Ranking of revenue by product for last month.",
      schema_source_id: SCHEMA_ID,
      schema_name: "Sales DB",
      metrics: ["revenue (sum)"],
      dimensions: ["product"],
      filters: ["sales.orders.status equals paid"],
      time_range: "last month (2026-10-01 to before 2026-11-01)",
      limit: 5,
      assumptions: [],
    },
    regenerations_remaining: 3,
    created_at: "2026-11-27T08:00:00Z",
    ...overrides,
  };
}

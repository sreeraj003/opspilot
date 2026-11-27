import type { StatusTone } from "@/components/ui/StatusIndicator";
import type { AggregationType, IntentType, TermKind, UnderstandingStatus, ValidationStatus } from "@/types/understanding";

export const KIND_LABELS: Record<TermKind, string> = {
  metric: "Metric",
  dimension: "Dimension",
  attribute: "Attribute",
};

export const AGGREGATION_LABELS: Record<AggregationType, string> = {
  sum: "Sum",
  count: "Count",
  count_distinct: "Count distinct",
  average: "Average",
  min: "Minimum",
  max: "Maximum",
  none: "None",
};

export const INTENT_LABELS: Record<IntentType, string> = {
  metric_lookup: "Metric lookup",
  aggregation: "Aggregation",
  comparison: "Comparison",
  trend_analysis: "Trend analysis",
  ranking: "Ranking",
  filtering: "Filtering",
  distribution: "Distribution",
  detail_lookup: "Detail lookup",
  unknown: "Unknown",
};

export const STATUS: Record<UnderstandingStatus, { tone: StatusTone; label: string }> = {
  understood: { tone: "success", label: "Understood" },
  needs_clarification: { tone: "warning", label: "Needs clarification" },
  access_denied: { tone: "danger", label: "Access denied" },
  rejected: { tone: "danger", label: "Rejected" },
};

export const VALIDATION: Record<ValidationStatus, { tone: StatusTone; label: string }> = {
  passed: { tone: "success", label: "Validation passed" },
  corrected: { tone: "info", label: "Passed after correction" },
  rejected: { tone: "danger", label: "Validation failed" },
  not_run: { tone: "neutral", label: "Not validated" },
};

export const PERMISSION: Record<"allowed" | "denied" | "no_data_access", { tone: StatusTone; label: string }> = {
  allowed: { tone: "success", label: "Permitted" },
  denied: { tone: "danger", label: "Restricted" },
  no_data_access: { tone: "neutral", label: "No data access" },
};

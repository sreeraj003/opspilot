/** Contracts of the Module 08 AI administration API. None of them can carry an API key. */

export interface AIProviderCredential {
  /** Whether a key is set on the server. The key itself is never sent to the browser. */
  configured: boolean;
  source: "environment";
  /** The environment variable that holds the key, e.g. NVIDIA_API_KEY. */
  reference: string;
}

export interface AIProvider {
  name: string;
  display_name: string;
  model: string | null;
  base_url: string | null;
  is_active: boolean;
  configured: boolean;
  missing_settings: string[];
  credential: AIProviderCredential;
  temperature: number;
  max_output_tokens: number;
  timeout_seconds: number;
  max_retries: number;
  retry_max_wait_seconds: number;
}

export interface AIProviderList {
  active_provider: string;
  items: AIProvider[];
}

export type AIHealthStatus = "healthy" | "unhealthy" | "not_configured";

export interface AIProviderHealth {
  provider: string;
  model: string | null;
  status: AIHealthStatus;
  latency_ms: number | null;
  model_available: boolean | null;
  error_category: string | null;
  message: string;
  checked_at: string;
}

export interface AIProviderTestResult {
  status: "passed" | "failed";
  provider: string;
  model: string | null;
  message: string;
  latency_ms: number | null;
  attempts: number | null;
  finish_reason: string | null;
  usage_available: boolean;
  input_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
  request_id: string | null;
  error_category: string | null;
  error_code: string | null;
  response_preview: string | null;
  tested_at: string;
}

export interface AIUsageRecord {
  id: string;
  provider: string;
  model: string;
  operation: string;
  status: "success" | "failure";
  error_category: string | null;
  finish_reason: string | null;
  usage_available: boolean;
  input_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
  latency_ms: number;
  attempts: number;
  request_id: string;
  correlation_id: string | null;
  user_id: string | null;
  created_at: string;
}

export interface AIUsageSummary {
  requests: number;
  successful: number;
  failed: number;
  requests_with_usage: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
}

export interface AIUsagePage {
  items: AIUsageRecord[];
  total: number;
  offset: number;
  limit: number;
  summary: AIUsageSummary;
}

export interface AIUsageParams {
  offset: number;
  limit: number;
  status?: "success" | "failure" | "all";
}

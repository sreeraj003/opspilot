import type { AIProvider, AIProviderHealth, AIProviderList, AIProviderTestResult, AIUsagePage, AIUsageRecord } from "@/types/ai";

const at = "2026-11-26T08:00:00Z";

export function makeAIProvider(overrides: Partial<AIProvider> = {}): AIProvider {
  return {
    name: "nvidia",
    display_name: "NVIDIA",
    model: "meta/llama-3.1-70b-instruct",
    base_url: "https://integrate.api.nvidia.com/v1",
    is_active: true,
    configured: true,
    missing_settings: [],
    credential: { configured: true, source: "environment", reference: "NVIDIA_API_KEY" },
    temperature: 0.2,
    max_output_tokens: 1024,
    timeout_seconds: 60,
    max_retries: 2,
    retry_max_wait_seconds: 20,
    ...overrides,
  };
}

export function makeAIProviderList(active: Partial<AIProvider> = {}): AIProviderList {
  return {
    active_provider: "nvidia",
    items: [
      makeAIProvider(active),
      makeAIProvider({
        name: "openai_compatible",
        display_name: "OpenAI-compatible",
        model: null,
        base_url: null,
        is_active: false,
        configured: false,
        missing_settings: ["OPENAI_COMPATIBLE_BASE_URL", "OPENAI_COMPATIBLE_API_KEY", "OPENAI_COMPATIBLE_MODEL"],
        credential: { configured: false, source: "environment", reference: "OPENAI_COMPATIBLE_API_KEY" },
      }),
    ],
  };
}

export function makeAIHealth(overrides: Partial<AIProviderHealth> = {}): AIProviderHealth {
  return {
    provider: "nvidia",
    model: "meta/llama-3.1-70b-instruct",
    status: "healthy",
    latency_ms: 142,
    model_available: true,
    error_category: null,
    message: "The provider is reachable.",
    checked_at: at,
    ...overrides,
  };
}

export function makeAITestResult(overrides: Partial<AIProviderTestResult> = {}): AIProviderTestResult {
  return {
    status: "passed",
    provider: "nvidia",
    model: "meta/llama-3.1-70b-instruct",
    message: "The provider answered the test prompt.",
    latency_ms: 812,
    attempts: 1,
    finish_reason: "stop",
    usage_available: true,
    input_tokens: 31,
    output_tokens: 2,
    total_tokens: 33,
    request_id: "llm_0123456789abcdef",
    error_category: null,
    error_code: null,
    response_preview: "OK",
    tested_at: at,
    ...overrides,
  };
}

export function makeAIUsageRecord(overrides: Partial<AIUsageRecord> = {}): AIUsageRecord {
  return {
    id: "99999999-9999-4999-8999-999999999999",
    provider: "nvidia",
    model: "meta/llama-3.1-70b-instruct",
    operation: "provider_test",
    status: "success",
    error_category: null,
    finish_reason: "stop",
    usage_available: true,
    input_tokens: 31,
    output_tokens: 2,
    total_tokens: 33,
    latency_ms: 812,
    attempts: 1,
    request_id: "llm_0123456789abcdef",
    correlation_id: "req-1",
    user_id: null,
    created_at: at,
    ...overrides,
  };
}

export function makeAIUsagePage(items: AIUsageRecord[] = [makeAIUsageRecord()]): AIUsagePage {
  const withUsage = items.filter((i) => i.usage_available);
  return {
    items,
    total: items.length,
    offset: 0,
    limit: 10,
    summary: {
      requests: items.length,
      successful: items.filter((i) => i.status === "success").length,
      failed: items.filter((i) => i.status === "failure").length,
      requests_with_usage: withUsage.length,
      input_tokens: withUsage.reduce((sum, i) => sum + (i.input_tokens ?? 0), 0),
      output_tokens: withUsage.reduce((sum, i) => sum + (i.output_tokens ?? 0), 0),
      total_tokens: withUsage.reduce((sum, i) => sum + (i.total_tokens ?? 0), 0),
    },
  };
}

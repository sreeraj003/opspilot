import type { ApiClient } from "@/services/apiClient";
import type { AIProvider, AIProviderHealth, AIProviderList, AIProviderTestResult, AIUsagePage, AIUsageParams } from "@/types/ai";

/**
 * Typed calls to the Module 08 AI administration API. The server authorizes every call and never returns an
 * API key. There is deliberately no call that sends text to the model: the provider test uses a fixed prompt
 * chosen by the server.
 */

function query(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === "" || value === "all") continue;
    search.set(key, String(value));
  }
  return search.toString();
}

export function listAIProviders(client: ApiClient, signal?: AbortSignal): Promise<AIProviderList> {
  return client.get("/api/v1/ai/providers", { signal });
}

export function getActiveAIProvider(client: ApiClient, signal?: AbortSignal): Promise<AIProvider> {
  return client.get("/api/v1/ai/providers/active", { signal });
}

export function getAIProviderHealth(client: ApiClient, signal?: AbortSignal): Promise<AIProviderHealth> {
  return client.get("/api/v1/ai/providers/health", { signal });
}

export function testAIProvider(client: ApiClient): Promise<AIProviderTestResult> {
  return client.post("/api/v1/ai/providers/test");
}

export function listAIUsage(client: ApiClient, params: AIUsageParams, signal?: AbortSignal): Promise<AIUsagePage> {
  return client.get(`/api/v1/ai/usage?${query({ ...params })}`, { signal });
}

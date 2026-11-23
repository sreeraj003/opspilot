import type { ApiClient } from "@/services/apiClient";
import type { HealthResponse, ReadinessResponse } from "@/types/api";

export function fetchHealth(client: ApiClient, signal?: AbortSignal): Promise<HealthResponse> {
  return client.get<HealthResponse>("/health", { signal });
}

/** 503 is a valid readiness answer ("not_ready"), so it resolves instead of throwing. */
export function fetchReadiness(client: ApiClient, signal?: AbortSignal): Promise<ReadinessResponse> {
  return client.get<ReadinessResponse>("/ready", { signal, acceptStatuses: [503] });
}

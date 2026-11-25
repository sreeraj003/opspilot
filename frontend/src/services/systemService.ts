import type { ApiClient } from "@/services/apiClient";
import type { AppInfoResponse, DatabaseHealthResponse, HealthResponse } from "@/types/api";

/** Typed calls to the backend's application information and health endpoints. */

export function fetchAppInfo(client: ApiClient, signal?: AbortSignal): Promise<AppInfoResponse> {
  return client.get<AppInfoResponse>("/", { signal });
}

export function fetchHealth(client: ApiClient, signal?: AbortSignal): Promise<HealthResponse> {
  return client.get<HealthResponse>("/health", { signal });
}

/** 503 is a valid answer here (database unavailable or not configured), so it resolves instead of throwing. */
export function fetchDatabaseHealth(client: ApiClient, signal?: AbortSignal): Promise<DatabaseHealthResponse> {
  return client.get<DatabaseHealthResponse>("/health/database", { signal, acceptStatuses: [503] });
}

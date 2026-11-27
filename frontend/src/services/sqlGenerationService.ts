import type { ApiClient } from "@/services/apiClient";
import type { GenerateSqlRequest, SqlGeneration } from "@/types/sqlGeneration";
import type { UnderstandingResponse, UnderstandRequest } from "@/types/understanding";

/**
 * Typed calls to the Module 10 APIs. The server loads the stored intent by id (a client intent is never sent),
 * authorizes every call and returns untrusted SQL only. There is no execution call.
 */

const enc = encodeURIComponent;

/** Module 09: interpret the question and store the validated intent this page generates SQL from. */
export function understandQuestion(client: ApiClient, input: UnderstandRequest): Promise<UnderstandingResponse> {
  return client.post("/api/v1/ai/understand", input);
}

export function generateSql(client: ApiClient, input: GenerateSqlRequest): Promise<SqlGeneration> {
  return client.post("/api/v1/ai/sql/generate", input);
}

export function getSqlGeneration(client: ApiClient, generationId: string, signal?: AbortSignal): Promise<SqlGeneration> {
  return client.get(`/api/v1/ai/sql/generations/${enc(generationId)}`, { signal });
}

export function regenerateSql(client: ApiClient, generationId: string): Promise<SqlGeneration> {
  return client.post(`/api/v1/ai/sql/generations/${enc(generationId)}/regenerate`, {});
}

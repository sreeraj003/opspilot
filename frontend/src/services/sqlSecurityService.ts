import type { ApiClient } from "@/services/apiClient";
import type { SqlApproval, SqlSecurityValidation } from "@/types/sqlSecurity";

/**
 * Typed calls to the Module 11 APIs. The client never sends SQL: the server validates the stored generation by id.
 * There is no execution call; an approval shown here is only the server's record of it.
 */

const enc = encodeURIComponent;

export function validateGeneration(client: ApiClient, generationId: string): Promise<SqlSecurityValidation> {
  return client.post(`/api/v1/ai/sql/generations/${enc(generationId)}/validate`, {});
}

export function getLatestValidation(client: ApiClient, generationId: string, signal?: AbortSignal): Promise<SqlSecurityValidation> {
  return client.get(`/api/v1/ai/sql/generations/${enc(generationId)}/validation`, { signal });
}

export function getValidation(client: ApiClient, validationId: string, signal?: AbortSignal): Promise<SqlSecurityValidation> {
  return client.get(`/api/v1/ai/sql/validations/${enc(validationId)}`, { signal });
}

export function getApproval(client: ApiClient, approvalId: string, signal?: AbortSignal): Promise<SqlApproval> {
  return client.get(`/api/v1/ai/sql/approvals/${enc(approvalId)}`, { signal });
}

export function revokeApproval(client: ApiClient, approvalId: string): Promise<SqlApproval> {
  return client.post(`/api/v1/ai/sql/approvals/${enc(approvalId)}/revoke`, {});
}

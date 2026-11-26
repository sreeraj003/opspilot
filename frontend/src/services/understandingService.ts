import type { ApiClient } from "@/services/apiClient";
import type {
  BusinessTerm,
  BusinessTermInput,
  BusinessTermListParams,
  BusinessTermPage,
  BusinessTermUpdate,
  PreviewResponse,
  SchemaFields,
  UnderstandingOptions,
  UnderstandRequest,
} from "@/types/understanding";

/** Typed calls to the Module 09 APIs. The server authorizes every call and applies the caller's own data permissions. */

const enc = encodeURIComponent;

function query(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === "") continue;
    search.set(key, String(value));
  }
  return search.toString();
}

export function listBusinessTerms(client: ApiClient, params: BusinessTermListParams, signal?: AbortSignal): Promise<BusinessTermPage> {
  return client.get(`/api/v1/business-terms?${query({ ...params })}`, {
    signal,
  });
}

export function getSchemaFields(client: ApiClient, schemaSourceId: string, signal?: AbortSignal): Promise<SchemaFields> {
  return client.get(`/api/v1/business-terms/schema-fields?${query({ schema_source_id: schemaSourceId })}`, { signal });
}

export function createBusinessTerm(client: ApiClient, input: BusinessTermInput): Promise<BusinessTerm> {
  return client.post("/api/v1/business-terms", input);
}

export function updateBusinessTerm(client: ApiClient, id: string, input: BusinessTermUpdate): Promise<BusinessTerm> {
  return client.patch(`/api/v1/business-terms/${enc(id)}`, input);
}

export function deleteBusinessTerm(client: ApiClient, id: string): Promise<void> {
  return client.delete(`/api/v1/business-terms/${enc(id)}`);
}

export function getUnderstandingOptions(client: ApiClient, signal?: AbortSignal): Promise<UnderstandingOptions> {
  return client.get("/api/v1/ai/understand/options", { signal });
}

export function previewUnderstanding(client: ApiClient, input: UnderstandRequest): Promise<PreviewResponse> {
  return client.post("/api/v1/ai/understand/preview", input);
}

/** Administrators with settings:manage change the business timezone through the settings API. */
export function setBusinessTimezone(client: ApiClient, key: string, timezone: string): Promise<unknown> {
  return client.request(`/api/v1/settings/${enc(key)}`, {
    method: "PUT",
    body: {
      value: timezone,
      description: "Business timezone for resolving relative dates in questions.",
    },
  });
}

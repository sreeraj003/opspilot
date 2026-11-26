import type { ApiClient } from "@/services/apiClient";
import type { Page } from "@/types/rbac";
import type {
  SchemaChange,
  SchemaColumn,
  SchemaColumnInput,
  SchemaImportRequest,
  SchemaImportResult,
  SchemaListParams,
  SchemaOutline,
  SchemaRelationship,
  SchemaRelationshipInput,
  SchemaSource,
  SchemaSourceInput,
  SchemaSummary,
  SchemaTable,
  SchemaTableDetail,
  SchemaTableInput,
  TableListParams,
} from "@/types/schemaRegistry";

/** Typed calls to the Module 06 schema registry. The server authorizes and validates every one of them. */

const enc = encodeURIComponent;

function query(params: Record<string, string | number | boolean | undefined | null>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "" || value === "all") continue;
    search.set(key, String(value));
  }
  return search.toString();
}

// --- schema sources ------------------------------------------------------------------------------

export function listSchemas(client: ApiClient, params: SchemaListParams, signal?: AbortSignal): Promise<Page<SchemaSource>> {
  return client.get(`/api/v1/schemas?${query({ ...params })}`, { signal });
}

export function getSchema(client: ApiClient, id: string, signal?: AbortSignal): Promise<SchemaSource> {
  return client.get(`/api/v1/schemas/${enc(id)}`, { signal });
}

export function createSchema(client: ApiClient, input: SchemaSourceInput): Promise<SchemaSource> {
  return client.post("/api/v1/schemas", input);
}

export function updateSchema(client: ApiClient, id: string, input: Partial<SchemaSourceInput>): Promise<SchemaSource> {
  return client.patch(`/api/v1/schemas/${enc(id)}`, input);
}

export function deleteSchema(client: ApiClient, id: string): Promise<void> {
  return client.delete(`/api/v1/schemas/${enc(id)}`);
}

export function getSummary(client: ApiClient, id: string, signal?: AbortSignal): Promise<SchemaSummary> {
  return client.get(`/api/v1/schemas/${enc(id)}/summary`, { signal });
}

export function getOutline(client: ApiClient, id: string, signal?: AbortSignal): Promise<SchemaOutline> {
  return client.get(`/api/v1/schemas/${enc(id)}/outline`, { signal });
}

export function getHistory(
  client: ApiClient,
  id: string,
  params: { offset: number; limit: number },
  signal?: AbortSignal,
): Promise<Page<SchemaChange>> {
  return client.get(`/api/v1/schemas/${enc(id)}/history?${query(params)}`, { signal });
}

// --- tables and columns --------------------------------------------------------------------------------

export function listTables(client: ApiClient, schemaId: string, params: TableListParams, signal?: AbortSignal): Promise<Page<SchemaTable>> {
  return client.get(`/api/v1/schemas/${enc(schemaId)}/tables?${query({ ...params })}`, { signal });
}

export function getTable(client: ApiClient, id: string, signal?: AbortSignal): Promise<SchemaTableDetail> {
  return client.get(`/api/v1/tables/${enc(id)}`, { signal });
}

export function createTable(client: ApiClient, schemaId: string, input: SchemaTableInput): Promise<SchemaTableDetail> {
  return client.post(`/api/v1/schemas/${enc(schemaId)}/tables`, input);
}

export function updateTable(client: ApiClient, id: string, input: Partial<SchemaTableInput>): Promise<SchemaTableDetail> {
  return client.patch(`/api/v1/tables/${enc(id)}`, input);
}

export function deleteTable(client: ApiClient, id: string, cascadeRelationships = false): Promise<void> {
  return client.delete(`/api/v1/tables/${enc(id)}${cascadeRelationships ? "?cascade_relationships=true" : ""}`);
}

export function createColumn(client: ApiClient, tableId: string, input: SchemaColumnInput): Promise<SchemaColumn> {
  return client.post(`/api/v1/tables/${enc(tableId)}/columns`, input);
}

export function updateColumn(client: ApiClient, id: string, input: Partial<SchemaColumnInput>): Promise<SchemaColumn> {
  return client.patch(`/api/v1/columns/${enc(id)}`, input);
}

export function deleteColumn(client: ApiClient, id: string, cascadeRelationships = false): Promise<void> {
  return client.delete(`/api/v1/columns/${enc(id)}${cascadeRelationships ? "?cascade_relationships=true" : ""}`);
}

// --- relationships ---------------------------------------------------------------------------------------

export function listRelationships(
  client: ApiClient,
  schemaId: string,
  params: { offset: number; limit: number; search?: string; table_id?: string },
  signal?: AbortSignal,
): Promise<Page<SchemaRelationship>> {
  return client.get(`/api/v1/schemas/${enc(schemaId)}/relationships?${query(params)}`, { signal });
}

export function createRelationship(client: ApiClient, schemaId: string, input: SchemaRelationshipInput): Promise<SchemaRelationship> {
  return client.post(`/api/v1/schemas/${enc(schemaId)}/relationships`, input);
}

export function updateRelationship(client: ApiClient, id: string, input: Partial<SchemaRelationshipInput>): Promise<SchemaRelationship> {
  return client.patch(`/api/v1/relationships/${enc(id)}`, input);
}

export function deleteRelationship(client: ApiClient, id: string): Promise<void> {
  return client.delete(`/api/v1/relationships/${enc(id)}`);
}

// --- import and export -----------------------------------------------------------------------------------

export function importSchema(client: ApiClient, schemaId: string, request: SchemaImportRequest): Promise<SchemaImportResult> {
  return client.post(`/api/v1/schemas/${enc(schemaId)}/import`, request);
}

export function exportSchema(client: ApiClient, schemaId: string): Promise<Record<string, unknown>> {
  return client.get(`/api/v1/schemas/${enc(schemaId)}/export`);
}

import type { ApiClient } from "@/services/apiClient";
import type {
  Connector,
  ConnectorAuditEvent,
  ConnectorEnvironment,
  ConnectorHealth,
  ConnectorInput,
  ConnectorListParams,
  ConnectorTestResult,
} from "@/types/connector";
import type { Page } from "@/types/rbac";

/**
 * Typed calls to the Module 07 connector API. The server authorizes and validates every one of them,
 * never returns credentials, and accepts no SQL: the connection test runs a fixed system query.
 */

const enc = encodeURIComponent;

function query(params: Record<string, string | number | boolean | undefined | null>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "" || value === "all") continue;
    search.set(key, String(value));
  }
  return search.toString();
}

export function listConnectors(client: ApiClient, params: ConnectorListParams, signal?: AbortSignal): Promise<Page<Connector>> {
  return client.get(`/api/v1/connectors?${query({ ...params })}`, { signal });
}

export function getConnector(client: ApiClient, id: string, signal?: AbortSignal): Promise<Connector> {
  return client.get(`/api/v1/connectors/${enc(id)}`, { signal });
}

export function getConnectorEnvironment(client: ApiClient, signal?: AbortSignal): Promise<ConnectorEnvironment> {
  return client.get("/api/v1/connectors/environment", { signal });
}

export function createConnector(client: ApiClient, input: ConnectorInput): Promise<Connector> {
  return client.post("/api/v1/connectors", input);
}

export function updateConnector(client: ApiClient, id: string, input: Partial<ConnectorInput>): Promise<Connector> {
  return client.patch(`/api/v1/connectors/${enc(id)}`, input);
}

export function deleteConnector(client: ApiClient, id: string): Promise<void> {
  return client.delete(`/api/v1/connectors/${enc(id)}`);
}

export function setConnectorStatus(client: ApiClient, id: string, isActive: boolean): Promise<Connector> {
  return client.patch(`/api/v1/connectors/${enc(id)}/status`, { is_active: isActive });
}

export function testConnector(client: ApiClient, id: string): Promise<ConnectorTestResult> {
  return client.post(`/api/v1/connectors/${enc(id)}/test`);
}

export function getConnectorHealth(client: ApiClient, id: string, signal?: AbortSignal): Promise<ConnectorHealth> {
  return client.get(`/api/v1/connectors/${enc(id)}/health`, { signal });
}

export function getConnectorAudit(
  client: ApiClient,
  id: string,
  params: { offset: number; limit: number },
  signal?: AbortSignal,
): Promise<Page<ConnectorAuditEvent>> {
  return client.get(`/api/v1/connectors/${enc(id)}/audit?${query(params)}`, { signal });
}

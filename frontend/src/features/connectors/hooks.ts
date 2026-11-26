import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { useApiClient } from "@/hooks/useApiClient";
import * as api from "@/services/connectorService";
import type { ConnectorInput, ConnectorListParams } from "@/types/connector";

export const connectorKeys = {
  all: ["connectors"] as const,
  list: (params: ConnectorListParams) => ["connectors", "list", params] as const,
  environment: ["connectors", "environment"] as const,
  detail: (id: string) => ["connectors", "detail", id] as const,
  health: (id: string) => ["connectors", "detail", id, "health"] as const,
  audit: (id: string, params: object) => ["connectors", "detail", id, "audit", params] as const,
};

export function useConnectorList(params: ConnectorListParams) {
  const client = useApiClient();
  return useQuery({
    queryKey: connectorKeys.list(params),
    queryFn: ({ signal }) => api.listConnectors(client, params, signal),
    placeholderData: keepPreviousData,
  });
}

export function useConnector(id: string) {
  const client = useApiClient();
  return useQuery({ queryKey: connectorKeys.detail(id), queryFn: ({ signal }) => api.getConnector(client, id, signal) });
}

export function useConnectorEnvironment(enabled = true) {
  const client = useApiClient();
  return useQuery({
    queryKey: connectorKeys.environment,
    queryFn: ({ signal }) => api.getConnectorEnvironment(client, signal),
    enabled,
    staleTime: 60_000,
  });
}

export function useConnectorHealth(id: string) {
  const client = useApiClient();
  return useQuery({ queryKey: connectorKeys.health(id), queryFn: ({ signal }) => api.getConnectorHealth(client, id, signal) });
}

export function useConnectorAudit(id: string, params: { offset: number; limit: number }) {
  const client = useApiClient();
  return useQuery({
    queryKey: connectorKeys.audit(id, params),
    queryFn: ({ signal }) => api.getConnectorAudit(client, id, params, signal),
    placeholderData: keepPreviousData,
  });
}

function useInvalidateConnectors() {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: connectorKeys.all });
}

export function useCreateConnector() {
  const client = useApiClient();
  const invalidate = useInvalidateConnectors();
  return useMutation({ mutationFn: (input: ConnectorInput) => api.createConnector(client, input), onSuccess: invalidate });
}

export function useUpdateConnector() {
  const client = useApiClient();
  const invalidate = useInvalidateConnectors();
  return useMutation({
    mutationFn: ({ id, input }: { id: string; input: Partial<ConnectorInput> }) => api.updateConnector(client, id, input),
    onSuccess: invalidate,
  });
}

export function useDeleteConnector() {
  const client = useApiClient();
  const invalidate = useInvalidateConnectors();
  return useMutation({ mutationFn: (id: string) => api.deleteConnector(client, id), onSuccess: invalidate });
}

export function useSetConnectorStatus() {
  const client = useApiClient();
  const invalidate = useInvalidateConnectors();
  return useMutation({
    mutationFn: ({ id, active }: { id: string; active: boolean }) => api.setConnectorStatus(client, id, active),
    onSuccess: invalidate,
  });
}

export function useTestConnector() {
  const client = useApiClient();
  const invalidate = useInvalidateConnectors();
  return useMutation({ mutationFn: (id: string) => api.testConnector(client, id), onSuccess: invalidate });
}

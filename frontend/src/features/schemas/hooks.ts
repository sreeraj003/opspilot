import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { useApiClient } from "@/hooks/useApiClient";
import * as api from "@/services/schemaService";
import type {
  SchemaColumnInput,
  SchemaImportRequest,
  SchemaListParams,
  SchemaRelationshipInput,
  SchemaSourceInput,
  SchemaTableInput,
  TableListParams,
} from "@/types/schemaRegistry";

export const schemaKeys = {
  all: ["schemas"] as const,
  list: (params: SchemaListParams) => ["schemas", "list", params] as const,
  detail: (id: string) => ["schemas", "detail", id] as const,
  /** Everything that belongs to one schema (tables, columns, relationships, summary, history). */
  scope: (id: string) => ["schemas", "scope", id] as const,
  summary: (id: string) => ["schemas", "scope", id, "summary"] as const,
  outline: (id: string) => ["schemas", "scope", id, "outline"] as const,
  tables: (id: string, params: TableListParams) => ["schemas", "scope", id, "tables", params] as const,
  table: (schemaId: string, tableId: string) => ["schemas", "scope", schemaId, "table", tableId] as const,
  relationships: (id: string, params: object) => ["schemas", "scope", id, "relationships", params] as const,
  history: (id: string, params: object) => ["schemas", "scope", id, "history", params] as const,
};

export function useSchemaList(params: SchemaListParams) {
  const client = useApiClient();
  return useQuery({
    queryKey: schemaKeys.list(params),
    queryFn: ({ signal }) => api.listSchemas(client, params, signal),
    placeholderData: keepPreviousData,
  });
}

export function useSchema(id: string) {
  const client = useApiClient();
  return useQuery({
    queryKey: schemaKeys.detail(id),
    queryFn: ({ signal }) => api.getSchema(client, id, signal),
  });
}

export function useSchemaSummary(id: string) {
  const client = useApiClient();
  return useQuery({
    queryKey: schemaKeys.summary(id),
    queryFn: ({ signal }) => api.getSummary(client, id, signal),
  });
}

export function useSchemaOutline(id: string, enabled = true) {
  const client = useApiClient();
  return useQuery({
    queryKey: schemaKeys.outline(id),
    queryFn: ({ signal }) => api.getOutline(client, id, signal),
    enabled,
  });
}

export function useTableList(schemaId: string, params: TableListParams) {
  const client = useApiClient();
  return useQuery({
    queryKey: schemaKeys.tables(schemaId, params),
    queryFn: ({ signal }) => api.listTables(client, schemaId, params, signal),
    placeholderData: keepPreviousData,
  });
}

export function useTable(schemaId: string, tableId: string | null) {
  const client = useApiClient();
  return useQuery({
    queryKey: schemaKeys.table(schemaId, tableId ?? ""),
    queryFn: ({ signal }) => api.getTable(client, tableId!, signal),
    enabled: tableId !== null,
  });
}

export function useRelationshipList(schemaId: string, params: { offset: number; limit: number; search?: string; table_id?: string }) {
  const client = useApiClient();
  return useQuery({
    queryKey: schemaKeys.relationships(schemaId, params),
    queryFn: ({ signal }) => api.listRelationships(client, schemaId, params, signal),
    placeholderData: keepPreviousData,
  });
}

export function useSchemaHistory(schemaId: string, params: { offset: number; limit: number }) {
  const client = useApiClient();
  return useQuery({
    queryKey: schemaKeys.history(schemaId, params),
    queryFn: ({ signal }) => api.getHistory(client, schemaId, params, signal),
    placeholderData: keepPreviousData,
  });
}

/** After any change: the schema's own data, its counts in lists, and its version. */
function useInvalidateSchema() {
  const queryClient = useQueryClient();
  return (schemaId?: string) => {
    void queryClient.invalidateQueries({ queryKey: ["schemas", "list"] });
    if (schemaId) {
      void queryClient.invalidateQueries({
        queryKey: schemaKeys.detail(schemaId),
      });
      void queryClient.invalidateQueries({
        queryKey: schemaKeys.scope(schemaId),
      });
    }
  };
}

export function useCreateSchema() {
  const client = useApiClient();
  const invalidate = useInvalidateSchema();
  return useMutation({
    mutationFn: (input: SchemaSourceInput) => api.createSchema(client, input),
    onSuccess: () => invalidate(),
  });
}

export function useUpdateSchema() {
  const client = useApiClient();
  const invalidate = useInvalidateSchema();
  return useMutation({
    mutationFn: ({ id, input }: { id: string; input: Partial<SchemaSourceInput> }) => api.updateSchema(client, id, input),
    onSuccess: (_, { id }) => invalidate(id),
  });
}

export function useDeleteSchema() {
  const client = useApiClient();
  const invalidate = useInvalidateSchema();
  return useMutation({
    mutationFn: (id: string) => api.deleteSchema(client, id),
    onSuccess: () => invalidate(),
  });
}

export function useSaveTable(schemaId: string) {
  const client = useApiClient();
  const invalidate = useInvalidateSchema();
  return useMutation({
    mutationFn: ({ id, input }: { id?: string; input: SchemaTableInput }) =>
      id ? api.updateTable(client, id, input) : api.createTable(client, schemaId, input),
    onSuccess: () => invalidate(schemaId),
  });
}

export function useDeleteTable(schemaId: string) {
  const client = useApiClient();
  const invalidate = useInvalidateSchema();
  return useMutation({
    mutationFn: ({ id, cascade }: { id: string; cascade: boolean }) => api.deleteTable(client, id, cascade),
    onSuccess: () => invalidate(schemaId),
  });
}

export function useSaveColumn(schemaId: string, tableId: string) {
  const client = useApiClient();
  const invalidate = useInvalidateSchema();
  return useMutation({
    mutationFn: ({ id, input }: { id?: string; input: SchemaColumnInput }) =>
      id ? api.updateColumn(client, id, input) : api.createColumn(client, tableId, input),
    onSuccess: () => invalidate(schemaId),
  });
}

export function useDeleteColumn(schemaId: string) {
  const client = useApiClient();
  const invalidate = useInvalidateSchema();
  return useMutation({
    mutationFn: ({ id, cascade }: { id: string; cascade: boolean }) => api.deleteColumn(client, id, cascade),
    onSuccess: () => invalidate(schemaId),
  });
}

export function useSaveRelationship(schemaId: string) {
  const client = useApiClient();
  const invalidate = useInvalidateSchema();
  return useMutation({
    mutationFn: ({ id, input }: { id?: string; input: SchemaRelationshipInput }) =>
      id ? api.updateRelationship(client, id, input) : api.createRelationship(client, schemaId, input),
    onSuccess: () => invalidate(schemaId),
  });
}

export function useDeleteRelationship(schemaId: string) {
  const client = useApiClient();
  const invalidate = useInvalidateSchema();
  return useMutation({
    mutationFn: (id: string) => api.deleteRelationship(client, id),
    onSuccess: () => invalidate(schemaId),
  });
}

export function useImportSchema(schemaId: string) {
  const client = useApiClient();
  const invalidate = useInvalidateSchema();
  return useMutation({
    mutationFn: (request: SchemaImportRequest) => api.importSchema(client, schemaId, request),
    onSuccess: (result) => {
      if (result.applied) invalidate(schemaId);
    },
  });
}

export function useExportSchema(schemaId: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.exportSchema(client, schemaId),
    // An export is audited, so the history changes.
    onSuccess: () =>
      void queryClient.invalidateQueries({
        queryKey: ["schemas", "scope", schemaId, "history"],
      }),
  });
}

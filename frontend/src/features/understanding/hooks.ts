import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { useApiClient } from "@/hooks/useApiClient";
import * as api from "@/services/understandingService";
import type { BusinessTermInput, BusinessTermListParams, BusinessTermUpdate, UnderstandRequest } from "@/types/understanding";

export const understandingKeys = {
  terms: ["business-terms"] as const,
  termList: (params: BusinessTermListParams) => ["business-terms", "list", params] as const,
  fields: (schemaSourceId: string) => ["business-terms", "fields", schemaSourceId] as const,
  options: ["ai", "understand", "options"] as const,
};

export function useBusinessTerms(params: BusinessTermListParams) {
  const client = useApiClient();
  return useQuery({
    queryKey: understandingKeys.termList(params),
    queryFn: ({ signal }) => api.listBusinessTerms(client, params, signal),
    placeholderData: keepPreviousData,
  });
}

export function useSchemaFields(schemaSourceId: string | null) {
  const client = useApiClient();
  return useQuery({
    queryKey: understandingKeys.fields(schemaSourceId ?? ""),
    queryFn: ({ signal }) => api.getSchemaFields(client, schemaSourceId!, signal),
    enabled: Boolean(schemaSourceId),
  });
}

export function useUnderstandingOptions(enabled = true) {
  const client = useApiClient();
  return useQuery({
    queryKey: understandingKeys.options,
    queryFn: ({ signal }) => api.getUnderstandingOptions(client, signal),
    enabled,
  });
}

function useInvalidateTerms() {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: understandingKeys.terms });
}

export function useCreateBusinessTerm() {
  const client = useApiClient();
  const invalidate = useInvalidateTerms();
  return useMutation({
    mutationFn: (input: BusinessTermInput) => api.createBusinessTerm(client, input),
    onSuccess: invalidate,
  });
}

export function useUpdateBusinessTerm() {
  const client = useApiClient();
  const invalidate = useInvalidateTerms();
  return useMutation({
    mutationFn: ({ id, input }: { id: string; input: BusinessTermUpdate }) => api.updateBusinessTerm(client, id, input),
    onSuccess: invalidate,
  });
}

export function useDeleteBusinessTerm() {
  const client = useApiClient();
  const invalidate = useInvalidateTerms();
  return useMutation({
    mutationFn: (id: string) => api.deleteBusinessTerm(client, id),
    onSuccess: invalidate,
  });
}

/** Each preview is one LLM request; it runs only when the user submits. */
export function usePreviewUnderstanding() {
  const client = useApiClient();
  return useMutation({
    mutationFn: (input: UnderstandRequest) => api.previewUnderstanding(client, input),
  });
}

export function useSetBusinessTimezone() {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ key, timezone }: { key: string; timezone: string }) => api.setBusinessTimezone(client, key, timezone),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: understandingKeys.options }),
  });
}

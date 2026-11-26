import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { useApiClient } from "@/hooks/useApiClient";
import * as api from "@/services/aiService";
import type { AIUsageParams } from "@/types/ai";

export const aiKeys = {
  all: ["ai"] as const,
  providers: ["ai", "providers"] as const,
  health: ["ai", "providers", "health"] as const,
  usage: (params: AIUsageParams) => ["ai", "usage", params] as const,
};

export function useAIProviders(enabled = true) {
  const client = useApiClient();
  return useQuery({ queryKey: aiKeys.providers, queryFn: ({ signal }) => api.listAIProviders(client, signal), enabled });
}

/** Health makes one cheap request to the provider (its model list); it never generates text. */
export function useAIProviderHealth(enabled = true) {
  const client = useApiClient();
  return useQuery({
    queryKey: aiKeys.health,
    queryFn: ({ signal }) => api.getAIProviderHealth(client, signal),
    enabled,
    staleTime: 30_000,
    refetchOnWindowFocus: false,
    retry: false,
  });
}

export function useAIUsage(params: AIUsageParams, enabled = true) {
  const client = useApiClient();
  return useQuery({
    queryKey: aiKeys.usage(params),
    queryFn: ({ signal }) => api.listAIUsage(client, params, signal),
    enabled,
    placeholderData: keepPreviousData,
  });
}

export function useTestAIProvider() {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.testAIProvider(client),
    // A test run adds a usage record.
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["ai", "usage"] }),
  });
}

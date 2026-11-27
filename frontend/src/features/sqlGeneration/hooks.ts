import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { useApiClient } from "@/hooks/useApiClient";
import * as api from "@/services/sqlGenerationService";
import type { GenerateSqlRequest, SqlGeneration } from "@/types/sqlGeneration";
import type { UnderstandRequest } from "@/types/understanding";

export const sqlGenerationKeys = {
  generation: (id: string) => ["ai", "sql", "generation", id] as const,
};

/** One AI request; runs only when the user submits. */
export function useUnderstandQuestion() {
  const client = useApiClient();
  return useMutation({ mutationFn: (input: UnderstandRequest) => api.understandQuestion(client, input) });
}

function useRemember() {
  const queryClient = useQueryClient();
  return (result: SqlGeneration) => queryClient.setQueryData(sqlGenerationKeys.generation(result.generation_id), result);
}

/** One AI request (plus bounded server-side regenerations); runs only on demand. */
export function useGenerateSql() {
  const client = useApiClient();
  const remember = useRemember();
  return useMutation({ mutationFn: (input: GenerateSqlRequest) => api.generateSql(client, input), onSuccess: remember });
}

export function useRegenerateSql() {
  const client = useApiClient();
  const remember = useRemember();
  return useMutation({ mutationFn: (generationId: string) => api.regenerateSql(client, generationId), onSuccess: remember });
}

/** Re-reads a stored generation (for example after permissions change); never re-runs the model. */
export function useSqlGeneration(generationId: string | null, enabled: boolean) {
  const client = useApiClient();
  return useQuery({
    queryKey: sqlGenerationKeys.generation(generationId ?? ""),
    queryFn: ({ signal }) => api.getSqlGeneration(client, generationId!, signal),
    enabled: Boolean(generationId) && enabled,
  });
}

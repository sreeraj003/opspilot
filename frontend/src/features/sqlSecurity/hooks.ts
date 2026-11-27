import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { useApiClient } from "@/hooks/useApiClient";
import * as api from "@/services/sqlSecurityService";
import type { SqlApproval, SqlSecurityValidation } from "@/types/sqlSecurity";

export const sqlSecurityKeys = {
  validation: (generationId: string) => ["ai", "sql", "security", "validation", generationId] as const,
  approval: (approvalId: string) => ["ai", "sql", "security", "approval", approvalId] as const,
};

/** Runs the server-side guard on demand; the result is whatever the server decided. */
export function useValidateGeneration(generationId: string) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.validateGeneration(client, generationId),
    onSuccess: (result: SqlSecurityValidation) => {
      queryClient.setQueryData(sqlSecurityKeys.validation(generationId), result);
      if (result.approval) queryClient.setQueryData(sqlSecurityKeys.approval(result.approval.approval_id), result.approval);
    },
  });
}

/**
 * The approval's current server state. Re-read on an interval while it is usable, so expiry and revocation are
 * shown from the server, never computed and trusted in the browser.
 */
export function useApproval(approvalId: string | null, initial: SqlApproval | null) {
  const client = useApiClient();
  return useQuery({
    queryKey: sqlSecurityKeys.approval(approvalId ?? ""),
    queryFn: ({ signal }) => api.getApproval(client, approvalId!, signal),
    enabled: Boolean(approvalId),
    initialData: initial ?? undefined,
    refetchInterval: (query) => (query.state.data?.is_usable ? 30_000 : false),
  });
}

export function useRevokeApproval() {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (approvalId: string) => api.revokeApproval(client, approvalId),
    onSuccess: (approval: SqlApproval) => queryClient.setQueryData(sqlSecurityKeys.approval(approval.approval_id), approval),
  });
}

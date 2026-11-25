import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useMemo, type ReactNode } from "react";

import { useAuth } from "@/features/auth/authContext";
import { PermissionsContext, type PermissionsContextValue } from "@/features/permissions/permissionsContext";
import { useApiClient } from "@/hooks/useApiClient";
import { getMyPermissions } from "@/services/rbacService";

export const permissionKeys = {
  all: ["authorization"] as const,
  me: (userId: string) => ["authorization", "me", userId] as const,
};

const STALE_MS = 30_000;
const REFRESH_INTERVAL_MS = 5 * 60_000;

/**
 * Loads the signed-in user's effective permissions from ``/api/v1/authorization/me``.
 *
 * - Keyed by user id, so a different sign-in never sees the previous user's data.
 * - Cleared on sign-out and session expiry (AuthProvider clears the query cache).
 * - Refetched on window focus, every few minutes, and after any role or
 *   permission change made in this app (those mutations invalidate ``authorization``).
 * - Until the first answer arrives, the permissions from the sign-in response are used.
 */
export function PermissionsProvider({ children }: { children: ReactNode }) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  const { status, user } = useAuth();
  const userId = user?.id ?? "";
  const query = useQuery({
    queryKey: permissionKeys.me(userId),
    queryFn: ({ signal }) => getMyPermissions(client, signal),
    enabled: status === "authenticated" && userId !== "",
    staleTime: STALE_MS,
    refetchInterval: REFRESH_INTERVAL_MS,
    refetchOnWindowFocus: true,
  });
  const effective = status === "authenticated" && query.data?.user_id === userId ? query.data : null;

  const refresh = useCallback(async () => {
    await queryClient.invalidateQueries({ queryKey: permissionKeys.all });
  }, [queryClient]);

  const value = useMemo<PermissionsContextValue>(() => {
    const keys = new Set<string>(effective ? [...effective.system_permissions, ...effective.permission_keys] : (user?.permissions ?? []));
    return {
      hasPermission: (key) => status === "authenticated" && keys.has(key),
      canAccessResource: (resourceType, action) => {
        if (!effective || status !== "authenticated") return false;
        if (resourceType === "system") return keys.has(action);
        return (
          effective.resources.some((r) => r.resource_type === resourceType && r.action === action) ||
          (resourceType === "database_data" && effective.data_access.some((d) => d.action === action && d.tables.length > 0))
        );
      },
      effectivePermissions: effective,
      isLoading: status === "authenticated" && query.isPending,
      refresh,
    };
  }, [effective, user, status, query.isPending, refresh]);

  return <PermissionsContext.Provider value={value}>{children}</PermissionsContext.Provider>;
}

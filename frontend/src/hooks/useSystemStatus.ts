import { useQuery } from "@tanstack/react-query";

import { useApiClient } from "@/hooks/useApiClient";
import { fetchDatabaseHealth, fetchHealth } from "@/services/systemService";

export const systemStatusKeys = {
  all: ["system-status"] as const,
  health: () => [...systemStatusKeys.all, "health"] as const,
  database: () => [...systemStatusKeys.all, "database"] as const,
};

const REFRESH_INTERVAL_MS = 30_000;

export function useBackendHealth() {
  const client = useApiClient();
  return useQuery({
    queryKey: systemStatusKeys.health(),
    queryFn: ({ signal }) => fetchHealth(client, signal),
    refetchInterval: REFRESH_INTERVAL_MS,
  });
}

export function useDatabaseHealth() {
  const client = useApiClient();
  return useQuery({
    queryKey: systemStatusKeys.database(),
    queryFn: ({ signal }) => fetchDatabaseHealth(client, signal),
    refetchInterval: REFRESH_INTERVAL_MS,
  });
}

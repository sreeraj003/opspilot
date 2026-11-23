import { useQuery } from "@tanstack/react-query";

import { useApiClient } from "@/hooks/useApiClient";

import { fetchHealth, fetchReadiness } from "./api";

export const systemStatusKeys = {
  all: ["system-status"] as const,
  health: () => [...systemStatusKeys.all, "health"] as const,
  readiness: () => [...systemStatusKeys.all, "readiness"] as const,
};

const REFRESH_INTERVAL_MS = 30_000;

export function useHealth() {
  const client = useApiClient();
  return useQuery({
    queryKey: systemStatusKeys.health(),
    queryFn: ({ signal }) => fetchHealth(client, signal),
    refetchInterval: REFRESH_INTERVAL_MS,
  });
}

export function useReadiness() {
  const client = useApiClient();
  return useQuery({
    queryKey: systemStatusKeys.readiness(),
    queryFn: ({ signal }) => fetchReadiness(client, signal),
    refetchInterval: REFRESH_INTERVAL_MS,
  });
}

import { QueryClientProvider, type QueryClient } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";

import { createQueryClient } from "@/app/queryClient";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { ApiClientContext } from "@/hooks/apiClientContext";
import { apiClient as defaultApiClient, type ApiClient } from "@/services/apiClient";

interface AppProvidersProps {
  children: ReactNode;
  queryClient?: QueryClient;
  apiClient?: ApiClient;
}

export function AppProviders({ children, queryClient, apiClient = defaultApiClient }: AppProvidersProps) {
  const [client] = useState(() => queryClient ?? createQueryClient());
  return (
    <ErrorBoundary>
      <ApiClientContext.Provider value={apiClient}>
        <QueryClientProvider client={client}>{children}</QueryClientProvider>
      </ApiClientContext.Provider>
    </ErrorBoundary>
  );
}

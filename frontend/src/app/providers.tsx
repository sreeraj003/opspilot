import { QueryClientProvider, type QueryClient } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";

import { createQueryClient } from "@/app/queryClient";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { AuthProvider } from "@/features/auth/AuthProvider";
import { ApiClientContext } from "@/hooks/apiClientContext";
import { apiClient as defaultApiClient, type ApiClient } from "@/services/apiClient";
import type { AuthSession } from "@/services/authSession";
import { authSession as defaultAuthSession } from "@/services/session";

interface AppProvidersProps {
  children: ReactNode;
  queryClient?: QueryClient;
  apiClient?: ApiClient;
  /** Must be bound to ``apiClient`` (tests pass both). */
  authSession?: AuthSession;
}

export function AppProviders({
  children,
  queryClient,
  apiClient = defaultApiClient,
  authSession = defaultAuthSession,
}: AppProvidersProps) {
  const [client] = useState(() => queryClient ?? createQueryClient());
  return (
    <ErrorBoundary>
      <ApiClientContext.Provider value={apiClient}>
        <QueryClientProvider client={client}>
          <AuthProvider session={authSession}>{children}</AuthProvider>
        </QueryClientProvider>
      </ApiClientContext.Provider>
    </ErrorBoundary>
  );
}

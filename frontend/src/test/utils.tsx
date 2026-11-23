import { QueryClient } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { createMemoryRouter, RouterProvider } from "react-router";

import { AppProviders } from "@/app/providers";
import { routes } from "@/app/routes";
import { ApiClient } from "@/services/apiClient";

export function jsonResponse(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

export function createTestQueryClient(): QueryClient {
  return new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
}

export function createTestApiClient(fetchImpl: typeof fetch): ApiClient {
  return new ApiClient({ baseUrl: "http://api.test/api/v1", timeoutMs: 1_000, fetchImpl });
}

export function renderWithProviders(ui: ReactElement, fetchImpl: typeof fetch) {
  return render(
    <AppProviders queryClient={createTestQueryClient()} apiClient={createTestApiClient(fetchImpl)}>
      {ui}
    </AppProviders>,
  );
}

export function renderRoute(path: string, fetchImpl: typeof fetch) {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  return renderWithProviders(<RouterProvider router={router} />, fetchImpl);
}

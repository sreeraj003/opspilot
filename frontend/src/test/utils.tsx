import { QueryClient } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { vi } from "vitest";

import { AppProviders } from "@/app/providers";
import { routes } from "@/app/routes";
import { ApiClient } from "@/services/apiClient";

export function jsonResponse(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

/** A fetch mock that answers by URL suffix and fails loudly on unexpected requests. */
export function routeFetch(responses: Record<string, () => Response | Promise<Response>>) {
  return vi.fn<typeof fetch>(async (input) => {
    const url = String(input);
    // Longest suffix first so "/health/database" is not matched by "/health".
    const match = Object.keys(responses)
      .sort((a, b) => b.length - a.length)
      .find((path) => url.endsWith(path));
    if (!match) throw new Error(`Unexpected request: ${url}`);
    return responses[match]!();
  });
}

export const neverResolvingFetch = vi.fn<typeof fetch>(() => new Promise<Response>(() => {}));

function createTestQueryClient(): QueryClient {
  return new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
}

function createTestApiClient(fetchImpl: typeof fetch): ApiClient {
  return new ApiClient({ baseUrl: "http://api.test", timeoutMs: 1_000, fetchImpl });
}

export function renderWithProviders(ui: ReactElement, fetchImpl: typeof fetch) {
  return render(
    <AppProviders queryClient={createTestQueryClient()} apiClient={createTestApiClient(fetchImpl)}>
      {ui}
    </AppProviders>,
  );
}

export function renderRoute(path: string, fetchImpl: typeof fetch = neverResolvingFetch) {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  return renderWithProviders(<RouterProvider router={router} />, fetchImpl);
}

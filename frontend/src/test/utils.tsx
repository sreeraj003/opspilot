import { QueryClient } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { vi } from "vitest";

import { AppProviders } from "@/app/providers";
import { routes } from "@/app/routes";
import { ApiClient } from "@/services/apiClient";
import { AuthSession } from "@/services/authSession";
import type { CurrentUser, Permission, TokenResponse, User } from "@/types/auth";

export function jsonResponse(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

export function errorResponse(status: number, code: string, message: string): Response {
  return jsonResponse({ success: false, error: { code, message, request_id: "req-test" } }, status);
}

type Handler = (init: RequestInit | undefined, url: string) => Response | Promise<Response>;

/**
 * A fetch mock that answers by "METHOD path" or by path suffix and fails loudly
 * on unexpected requests. Query strings are ignored when matching.
 */
export function routeFetch(responses: Record<string, Handler>) {
  return vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input);
    const path = url.split("?")[0]!;
    const method = (init?.method ?? "GET").toUpperCase();
    // Longest key first so "/health/database" is not matched by "/health".
    const keys = Object.keys(responses).sort((a, b) => b.length - a.length);
    const match = keys.find((key) => {
      const [keyMethod, keyPath] = key.includes(" ") ? key.split(" ") : [undefined, key];
      return (keyMethod === undefined || keyMethod === method) && path.endsWith(keyPath!);
    });
    // The permissions provider asks for /authorization/me on every signed-in render. Tests that
    // do not answer it keep using the permissions from the sign-in response.
    if (!match && method === "GET" && path.endsWith("/api/v1/authorization/me")) return new Promise<Response>(() => {});
    if (!match) throw new Error(`Unexpected request: ${method} ${url}`);
    return responses[match]!(init, url);
  });
}

export const neverResolvingFetch = vi.fn<typeof fetch>(() => new Promise<Response>(() => {}));

export function makeUser(overrides: Partial<User> = {}): User {
  return {
    id: "11111111-1111-4111-8111-111111111111",
    email: "ada@example.com",
    username: null,
    full_name: "Ada Lovelace",
    is_active: true,
    is_verified: false,
    is_superuser: false,
    is_locked: false,
    locked_until: null,
    last_login_at: "2026-11-25T08:00:00Z",
    created_at: "2026-11-01T08:00:00Z",
    updated_at: "2026-11-01T08:00:00Z",
    ...overrides,
  };
}

export const ADMIN_PERMISSIONS: Permission[] = [
  "access:assign",
  "access:review",
  "permissions:manage",
  "permissions:read",
  "roles:manage",
  "roles:read",
  "settings:manage",
  "settings:read",
  "users:manage",
  "users:read",
  "schema.read",
  "schema.create",
  "schema.update",
  "schema.delete",
  "schema.import",
  "schema.export",
  "schema.relationship.manage",
  "connector.read",
  "connector.create",
  "connector.update",
  "connector.delete",
  "connector.test",
  "connector.activate",
];

export function makeCurrentUser(overrides: Partial<CurrentUser> = {}): CurrentUser {
  return { ...makeUser(), permissions: [], ...overrides };
}

export function makeAdmin(): CurrentUser {
  return makeCurrentUser({
    id: "00000000-0000-4000-8000-000000000001",
    email: "admin@example.com",
    full_name: "Installation Admin",
    is_superuser: true,
    permissions: ADMIN_PERMISSIONS,
  });
}

export function tokenResponse(user: CurrentUser, accessToken = "access-token-1"): TokenResponse {
  return { access_token: accessToken, token_type: "bearer", expires_in: 900, user };
}

function createTestQueryClient(): QueryClient {
  return new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
}

export interface TestContext {
  apiClient: ApiClient;
  authSession: AuthSession;
}

export function createTestContext(fetchImpl: typeof fetch, user?: CurrentUser | null): TestContext {
  const apiClient = new ApiClient({ baseUrl: "http://api.test", timeoutMs: 1_000, fetchImpl });
  const authSession = new AuthSession(apiClient, { broadcast: false });
  if (user) authSession.establish(tokenResponse(user));
  return { apiClient, authSession };
}

export function renderWithProviders(ui: ReactElement, fetchImpl: typeof fetch, user: CurrentUser | null = makeCurrentUser()) {
  const context = createTestContext(fetchImpl, user);
  const result = render(
    <AppProviders queryClient={createTestQueryClient()} apiClient={context.apiClient} authSession={context.authSession}>
      {ui}
    </AppProviders>,
  );
  return { ...result, ...context };
}

/**
 * Render the real route table at ``path``. With a ``user`` the session starts
 * signed in; with ``null`` the app restores the session through the mocked
 * /auth/refresh endpoint like it does in the browser.
 */
export function renderRoute(path: string, fetchImpl: typeof fetch = neverResolvingFetch, user: CurrentUser | null = makeCurrentUser()) {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  return { router, ...renderWithProviders(<RouterProvider router={router} />, fetchImpl, user) };
}

import { describe, expect, it, vi } from "vitest";

import { ApiClient } from "@/services/apiClient";
import { AuthSession, type SessionEvent } from "@/services/authSession";
import { errorResponse, jsonResponse, makeCurrentUser, routeFetch, tokenResponse } from "@/test/utils";

function setup(fetchImpl: typeof fetch) {
  const client = new ApiClient({ baseUrl: "http://api.test", timeoutMs: 1_000, fetchImpl });
  const session = new AuthSession(client, { broadcast: false });
  const events: SessionEvent[] = [];
  session.subscribe((event) => events.push(event));
  return { client, session, events };
}

function headerOf(init: RequestInit | undefined, name: string): string | undefined {
  return (init?.headers as Record<string, string> | undefined)?.[name];
}

describe("AuthSession", () => {
  it("refreshes an expired access token once and retries the request", async () => {
    let meCalls = 0;
    const fetchImpl = routeFetch({
      "POST /api/v1/auth/refresh": () => jsonResponse(tokenResponse(makeCurrentUser(), "fresh-token")),
      "GET /api/v1/auth/me": (init) => {
        meCalls += 1;
        return headerOf(init, "Authorization") === "Bearer fresh-token"
          ? jsonResponse(makeCurrentUser())
          : errorResponse(401, "UNAUTHORIZED", "Authentication is required.");
      },
    });
    const { client, session } = setup(fetchImpl);
    session.establish(tokenResponse(makeCurrentUser(), "stale-token"));

    await expect(client.get("/api/v1/auth/me")).resolves.toMatchObject({ email: "ada@example.com" });

    expect(meCalls).toBe(2);
    const refresh = fetchImpl.mock.calls.find(([url]) => String(url).endsWith("/auth/refresh"))!;
    expect(refresh[1]?.credentials).toBe("include");
    expect(headerOf(refresh[1], "X-OpsPilot-CSRF")).toBe("1");
    expect(headerOf(refresh[1], "Authorization")).toBeUndefined();
  });

  it("shares one refresh between concurrent 401s so the token rotates once", async () => {
    let refreshes = 0;
    const fetchImpl = routeFetch({
      "POST /api/v1/auth/refresh": async () => {
        refreshes += 1;
        await new Promise((resolve) => setTimeout(resolve, 10));
        return jsonResponse(tokenResponse(makeCurrentUser(), "fresh-token"));
      },
      "/api/v1/users": (init) =>
        headerOf(init, "Authorization") === "Bearer fresh-token"
          ? jsonResponse({ items: [], total: 0, offset: 0, limit: 20 })
          : errorResponse(401, "UNAUTHORIZED", "Authentication is required."),
    });
    const { client, session } = setup(fetchImpl);
    session.establish(tokenResponse(makeCurrentUser(), "stale-token"));

    await Promise.all([client.get("/api/v1/users"), client.get("/api/v1/users"), client.get("/api/v1/users")]);

    expect(refreshes).toBe(1);
  });

  it("retries once when another tab rotated the token at the same moment (409)", async () => {
    const responses = [
      () => errorResponse(409, "REFRESH_CONFLICT", "Retry."),
      () => jsonResponse(tokenResponse(makeCurrentUser(), "after-conflict")),
    ];
    const fetchImpl = routeFetch({ "POST /api/v1/auth/refresh": () => responses.shift()!() });
    const { session } = setup(fetchImpl);

    await expect(session.refresh()).resolves.toBe(true);
    expect(fetchImpl).toHaveBeenCalledTimes(2);
  });

  it("ends the session when refresh fails, without retry loops", async () => {
    const fetchImpl = routeFetch({
      "POST /api/v1/auth/refresh": () => errorResponse(401, "UNAUTHORIZED", "Your session has expired. Sign in again."),
      "GET /api/v1/auth/me": () => errorResponse(401, "UNAUTHORIZED", "Authentication is required."),
    });
    const { client, session, events } = setup(fetchImpl);
    session.establish(tokenResponse(makeCurrentUser()));

    await expect(client.get("/api/v1/auth/me")).rejects.toMatchObject({ status: 401 });

    expect(fetchImpl).toHaveBeenCalledTimes(2); // the request once, the refresh once
    expect(session.isAuthenticated).toBe(false);
    expect(events.at(-1)).toEqual({ type: "signed-out", reason: "expired" });
  });

  it("keeps tokens out of storage", async () => {
    const { session } = setup(routeFetch({}));
    session.establish(tokenResponse(makeCurrentUser(), "secret-access-token"));

    expect(JSON.stringify({ ...localStorage })).not.toContain("secret-access-token");
    expect(JSON.stringify({ ...sessionStorage })).not.toContain("secret-access-token");
    expect(document.cookie).not.toContain("secret-access-token");
  });

  it("signs out on the server and locally even if the server call fails", async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockRejectedValue(new TypeError("Failed to fetch"));
    const { session, events } = setup(fetchImpl);
    session.establish(tokenResponse(makeCurrentUser()));

    await session.signOut();

    expect(String(fetchImpl.mock.calls[0]![0])).toContain("/api/v1/auth/logout");
    expect(session.isAuthenticated).toBe(false);
    expect(events.at(-1)).toEqual({ type: "signed-out", reason: "logout" });
  });
});

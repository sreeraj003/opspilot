import { describe, expect, it, vi } from "vitest";

import { jsonResponse } from "@/test/utils";

import { ApiClient, ApiError, type HeadersProvider } from "./apiClient";

function client(fetchImpl: typeof fetch, timeoutMs = 1_000, headersProvider?: HeadersProvider) {
  return new ApiClient({ baseUrl: "http://api.test/", timeoutMs, fetchImpl, headersProvider });
}

describe("ApiClient", () => {
  it("joins the base URL and path and returns parsed JSON", async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ status: "ok" }));

    await expect(client(fetchImpl).get("/health")).resolves.toEqual({ status: "ok" });

    const [url, init] = fetchImpl.mock.calls[0]!;
    expect(url).toBe("http://api.test/health");
    expect(init?.method).toBe("GET");
    expect((init?.headers as Record<string, string>).Accept).toBe("application/json");
  });

  it("converts the backend error envelope into an ApiError", async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(
      jsonResponse(
        { success: false, error: { code: "NOT_FOUND", message: "Missing.", request_id: "req-9" } },
        404,
      ),
    );

    const error = await client(fetchImpl).get("/x").catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 404, code: "NOT_FOUND", message: "Missing.", requestId: "req-9" });
  });

  it("falls back to the X-Request-ID header when the envelope has no request id", async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(
      jsonResponse({ success: false, error: { code: "INTERNAL_SERVER_ERROR", message: "Oops." } }, 500, {
        "X-Request-ID": "hdr-1",
      }),
    );

    await expect(client(fetchImpl).get("/x")).rejects.toMatchObject({ status: 500, requestId: "hdr-1" });
  });

  it("handles non-JSON error responses", async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(new Response("Bad gateway", { status: 502 }));

    await expect(client(fetchImpl).get("/x")).rejects.toMatchObject({ status: 502, code: "http_error" });
  });

  it("reports malformed JSON as invalid_response", async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(
      new Response("{not json", { status: 200, headers: { "Content-Type": "application/json" } }),
    );

    await expect(client(fetchImpl).get("/x")).rejects.toMatchObject({ code: "invalid_response" });
  });

  it("resolves accepted non-2xx statuses with the body", async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ status: "unavailable" }, 503));

    await expect(client(fetchImpl).get("/health/database", { acceptStatuses: [503] })).resolves.toEqual({
      status: "unavailable",
    });
  });

  it("reports network failures as network_error", async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockRejectedValue(new TypeError("Failed to fetch"));

    await expect(client(fetchImpl).get("/x")).rejects.toMatchObject({ status: 0, code: "network_error" });
  });

  it("aborts slow requests with a timeout error", async () => {
    const fetchImpl: typeof fetch = (_input, init) =>
      new Promise((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
      });

    await expect(client(fetchImpl, 20).get("/slow")).rejects.toMatchObject({ code: "timeout" });
  });

  it("rethrows caller cancellation instead of wrapping it", async () => {
    const controller = new AbortController();
    const fetchImpl: typeof fetch = (_input, init) =>
      new Promise((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
      });

    const pending = client(fetchImpl).get("/x", { signal: controller.signal });
    controller.abort();

    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
  });

  it("sends JSON bodies with a content type", async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(new Response(null, { status: 204 }));

    await expect(client(fetchImpl).request("/items", { method: "POST", body: { a: 1 } })).resolves.toBeUndefined();

    const init = fetchImpl.mock.calls[0]![1]!;
    expect(init.body).toBe('{"a":1}');
    expect((init.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
  });

  it("adds headers from the headers provider (reserved for authentication)", async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({}));

    await client(fetchImpl, 1_000, () => ({ Authorization: "Bearer test-token" })).get("/x");

    const init = fetchImpl.mock.calls[0]![1]!;
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer test-token");
  });

  it("sends cookies only when asked to", async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockImplementation(async () => jsonResponse({}));

    await client(fetchImpl).get("/x");
    await client(fetchImpl).post("/auth/refresh", undefined, { withCredentials: true });

    expect(fetchImpl.mock.calls[0]![1]!.credentials).toBe("same-origin");
    expect(fetchImpl.mock.calls[1]![1]!.credentials).toBe("include");
  });

  it("retries a 401 once after the unauthorized handler succeeds, and never for anonymous calls", async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockImplementation(async () =>
      jsonResponse({ success: false, error: { code: "UNAUTHORIZED", message: "No." } }, 401),
    );
    const api = client(fetchImpl);
    const handler = vi.fn().mockResolvedValue(true);
    api.useAuthentication(() => ({ Authorization: "Bearer t" }), handler);

    await expect(api.get("/x")).rejects.toMatchObject({ status: 401 });
    expect(fetchImpl).toHaveBeenCalledTimes(2);
    expect(handler).toHaveBeenCalledTimes(1);

    await expect(api.post("/auth/login", {}, { anonymous: true })).rejects.toMatchObject({ status: 401 });
    expect(fetchImpl).toHaveBeenCalledTimes(3);
    expect(handler).toHaveBeenCalledTimes(1);
    expect((fetchImpl.mock.calls[2]![1]!.headers as Record<string, string>).Authorization).toBeUndefined();
  });
});

import { describe, expect, it, vi } from "vitest";

import { jsonResponse } from "@/test/utils";

import { ApiClient, ApiError } from "./apiClient";

function client(fetchImpl: typeof fetch, timeoutMs = 1_000) {
  return new ApiClient({ baseUrl: "http://api.test/api/v1/", timeoutMs, fetchImpl });
}

describe("ApiClient", () => {
  it("joins the base URL and path and returns parsed JSON", async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ status: "ok" }));

    await expect(client(fetchImpl).get("/health")).resolves.toEqual({ status: "ok" });

    const [url, init] = fetchImpl.mock.calls[0]!;
    expect(url).toBe("http://api.test/api/v1/health");
    expect(init?.method).toBe("GET");
  });

  it("converts the backend error envelope into an ApiError", async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(
      jsonResponse({ error: { code: "not_found", message: "Missing.", request_id: "req-9" } }, 404),
    );

    const error = await client(fetchImpl).get("/x").catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 404, code: "not_found", message: "Missing.", requestId: "req-9" });
  });

  it("handles non-JSON error responses", async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(new Response("Bad gateway", { status: 502 }));

    await expect(client(fetchImpl).get("/x")).rejects.toMatchObject({ status: 502, code: "http_error" });
  });

  it("resolves accepted non-2xx statuses with the body", async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ status: "not_ready" }, 503));

    await expect(client(fetchImpl).get("/ready", { acceptStatuses: [503] })).resolves.toEqual({
      status: "not_ready",
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

  it("sends JSON bodies with a content type", async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockResolvedValue(new Response(null, { status: 204 }));

    await expect(client(fetchImpl).request("/items", { method: "POST", body: { a: 1 } })).resolves.toBeUndefined();

    const init = fetchImpl.mock.calls[0]![1]!;
    expect(init.body).toBe('{"a":1}');
    expect((init.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
  });
});

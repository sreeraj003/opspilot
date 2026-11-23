import { describe, expect, it } from "vitest";

import { ApiError } from "@/services/apiClient";

import { shouldRetry } from "./queryClient";

describe("shouldRetry", () => {
  it("does not retry client errors", () => {
    expect(shouldRetry(0, new ApiError("nope", { status: 404, code: "not_found" }))).toBe(false);
  });

  it("retries server and network errors up to the limit", () => {
    const serverError = new ApiError("down", { status: 503, code: "service_unavailable" });
    expect(shouldRetry(0, serverError)).toBe(true);
    expect(shouldRetry(1, new ApiError("net", { status: 0, code: "network_error" }))).toBe(true);
    expect(shouldRetry(2, serverError)).toBe(false);
  });
});

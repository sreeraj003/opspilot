import { describe, expect, it } from "vitest";

import { parseConfig } from "./config";

describe("parseConfig", () => {
  it("uses defaults when variables are not set", () => {
    expect(parseConfig({})).toEqual({ apiBaseUrl: "http://localhost:8000", apiTimeoutMs: 10_000 });
  });

  it("reads and normalises configured values", () => {
    expect(parseConfig({ VITE_API_BASE_URL: "https://bi.acme.example/api/", VITE_API_TIMEOUT_MS: "5000" })).toEqual({
      apiBaseUrl: "https://bi.acme.example/api",
      apiTimeoutMs: 5_000,
    });
  });

  it("accepts a relative base path for reverse-proxy deployments", () => {
    expect(parseConfig({ VITE_API_BASE_URL: "/api/" }).apiBaseUrl).toBe("/api");
  });

  it.each([
    [{ VITE_API_BASE_URL: "not a url" }, /VITE_API_BASE_URL/],
    [{ VITE_API_BASE_URL: "ftp://files.example" }, /VITE_API_BASE_URL/],
    [{ VITE_API_TIMEOUT_MS: "-1" }, /VITE_API_TIMEOUT_MS/],
    [{ VITE_API_TIMEOUT_MS: "abc" }, /VITE_API_TIMEOUT_MS/],
  ])("rejects invalid configuration %o", (env, message) => {
    expect(() => parseConfig(env)).toThrow(message);
  });
});

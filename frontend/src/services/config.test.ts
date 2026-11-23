import { describe, expect, it } from "vitest";

import { parseConfig } from "./config";

describe("parseConfig", () => {
  it("uses defaults when variables are absent", () => {
    expect(parseConfig({})).toEqual({ apiBaseUrl: "http://localhost:8000/api/v1", apiTimeoutMs: 10_000 });
  });

  it("reads and normalises configured values", () => {
    expect(parseConfig({ VITE_API_BASE_URL: "https://ops.example.com/api/v1/", VITE_API_TIMEOUT_MS: "2500" })).toEqual({
      apiBaseUrl: "https://ops.example.com/api/v1",
      apiTimeoutMs: 2500,
    });
  });

  it("supports a relative base path for same-origin deployments", () => {
    expect(parseConfig({ VITE_API_BASE_URL: "/api/v1" }).apiBaseUrl).toBe("/api/v1");
  });

  it("rejects an invalid timeout", () => {
    expect(() => parseConfig({ VITE_API_TIMEOUT_MS: "soon" })).toThrow(/VITE_API_TIMEOUT_MS/);
  });
});

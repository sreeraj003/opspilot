import { screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { jsonResponse, renderWithProviders } from "@/test/utils";
import type { HealthResponse, ReadinessResponse } from "@/types/api";

import { SystemStatusPanel } from "./SystemStatusPanel";

const health: HealthResponse = {
  status: "ok",
  service: "OpsPilot AI API",
  version: "0.1.0",
  environment: "development",
  timestamp: "2026-11-23T09:00:00Z",
};

function readiness(overrides: Partial<ReadinessResponse> = {}): ReadinessResponse {
  return {
    status: "ready",
    timestamp: "2026-11-23T09:00:00Z",
    checks: [
      { name: "application", status: "ok", required: true, latency_ms: null, detail: null },
      { name: "database", status: "not_configured", required: false, latency_ms: null, detail: "DATABASE_URL is not set." },
    ],
    ...overrides,
  };
}

function routeFetch(responses: Record<string, () => Response | Promise<Response>>) {
  return vi.fn<typeof fetch>(async (input) => {
    const url = String(input);
    const match = Object.keys(responses).find((path) => url.endsWith(path));
    if (!match) throw new Error(`Unexpected request: ${url}`);
    return responses[match]!();
  });
}

describe("SystemStatusPanel", () => {
  it("shows backend health and readiness checks", async () => {
    const fetchImpl = routeFetch({ "/health": () => jsonResponse(health), "/ready": () => jsonResponse(readiness()) });

    renderWithProviders(<SystemStatusPanel />, fetchImpl);

    expect(await screen.findByText("Online")).toBeInTheDocument();
    expect(screen.getByText("0.1.0")).toBeInTheDocument();
    expect(screen.getByText("Development")).toBeInTheDocument();
    expect(await screen.findByText("Ready")).toBeInTheDocument();
    const database = screen.getByText("Database").closest("li")!;
    expect(within(database).getByText("Not configured")).toBeInTheDocument();
    expect(within(database).getByText("DATABASE_URL is not set.")).toBeInTheDocument();
  });

  it("shows not ready when the backend answers 503", async () => {
    const notReady = readiness({
      status: "not_ready",
      checks: [{ name: "database", status: "error", required: true, latency_ms: 4, detail: "Database unreachable." }],
    });
    const fetchImpl = routeFetch({
      "/health": () => jsonResponse(health),
      "/ready": () => jsonResponse(notReady, 503),
    });

    renderWithProviders(<SystemStatusPanel />, fetchImpl);

    expect(await screen.findByText("Not ready")).toBeInTheDocument();
    expect(screen.getByText("Database unreachable.")).toBeInTheDocument();
  });

  it("shows an unreachable state when the backend cannot be contacted", async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockRejectedValue(new TypeError("Failed to fetch"));

    renderWithProviders(<SystemStatusPanel />, fetchImpl);

    expect(await screen.findByText("Unreachable")).toBeInTheDocument();
    expect(screen.getAllByText("Unable to reach the OpsPilot AI API.").length).toBeGreaterThan(0);
  });
});

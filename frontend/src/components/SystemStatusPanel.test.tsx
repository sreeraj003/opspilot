import { screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { jsonResponse, renderWithProviders, routeFetch } from "@/test/utils";
import type { DatabaseHealthResponse, HealthResponse } from "@/types/api";

import { SystemStatusPanel } from "./SystemStatusPanel";

const health: HealthResponse = {
  status: "ok",
  service: "OpsPilot AI",
  version: "1.0.0",
  environment: "development",
  timestamp: "2026-11-25T09:00:00Z",
};

function databaseHealth(overrides: Partial<DatabaseHealthResponse> = {}): DatabaseHealthResponse {
  return {
    status: "ok",
    database: "postgresql",
    latency_ms: 1.5,
    detail: null,
    timestamp: "2026-11-25T09:00:00Z",
    ...overrides,
  };
}

describe("SystemStatusPanel", () => {
  it("shows a connected backend and database", async () => {
    const fetchImpl = routeFetch({
      "/health": () => jsonResponse(health),
      "/health/database": () => jsonResponse(databaseHealth()),
    });

    renderWithProviders(<SystemStatusPanel />, fetchImpl);

    const backend = screen.getByRole("region", { name: "Backend API" });
    expect(await within(backend).findByText("Connected")).toBeInTheDocument();
    expect(within(backend).getByText("1.0.0")).toBeInTheDocument();
    expect(within(backend).getByText("Development")).toBeInTheDocument();

    const database = screen.getByRole("region", { name: "Database" });
    expect(await within(database).findByText("Connected")).toBeInTheDocument();
    expect(within(database).getByText("1.5 ms")).toBeInTheDocument();
    expect(within(database).getByText("PostgreSQL")).toBeInTheDocument();
  });

  it("shows an unavailable database when the backend answers 503", async () => {
    const fetchImpl = routeFetch({
      "/health": () => jsonResponse(health),
      "/health/database": () =>
        jsonResponse(
          databaseHealth({ status: "unavailable", detail: "The application database is not reachable." }),
          503,
        ),
    });

    renderWithProviders(<SystemStatusPanel />, fetchImpl);

    const database = screen.getByRole("region", { name: "Database" });
    expect(await within(database).findByText("Unavailable")).toBeInTheDocument();
    expect(within(database).getByText("The application database is not reachable.")).toBeInTheDocument();
  });

  it("shows an unreachable backend when it cannot be contacted", async () => {
    const fetchImpl = vi.fn<typeof fetch>().mockRejectedValue(new TypeError("Failed to fetch"));

    renderWithProviders(<SystemStatusPanel />, fetchImpl);

    const backend = screen.getByRole("region", { name: "Backend API" });
    expect(await within(backend).findByText("Unreachable")).toBeInTheDocument();
    expect(within(backend).getByRole("alert")).toHaveTextContent("Unable to reach the OpsPilot AI server.");
    expect(await within(screen.getByRole("region", { name: "Database" })).findByText("Unknown")).toBeInTheDocument();
  });
});

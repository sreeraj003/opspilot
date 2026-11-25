import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "@/app/App";
import { jsonResponse, makeCurrentUser, tokenResponse } from "@/test/utils";

describe("App", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("restores the session from the refresh cookie and renders the home page", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) =>
      String(input).endsWith("/api/v1/auth/refresh")
        ? Promise.resolve(jsonResponse(tokenResponse(makeCurrentUser())))
        : new Promise<Response>(() => {}),
    );
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    expect(await screen.findByRole("heading", { level: 1, name: "OpsPilot AI" })).toBeInTheDocument();
    expect(screen.getByLabelText("OpsPilot AI home")).toBeInTheDocument();
    expect(screen.getByRole("contentinfo")).toHaveTextContent("AI-powered Business Intelligence Assistant");
    const refreshCalls = fetchMock.mock.calls.filter(([input]) => String(input).endsWith("/auth/refresh"));
    expect(refreshCalls).toHaveLength(1); // StrictMode double effects share one refresh
  });
});

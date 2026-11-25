import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "@/app/App";

describe("App", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders the application shell with the home page", () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => {})));

    render(<App />);

    expect(screen.getByLabelText("OpsPilot AI home")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1, name: "OpsPilot AI" })).toBeInTheDocument();
    expect(screen.getByRole("contentinfo")).toHaveTextContent("AI-powered Business Intelligence Assistant");
  });
});

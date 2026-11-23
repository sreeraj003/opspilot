import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { renderRoute } from "@/test/utils";

const neverResolves = vi.fn<typeof fetch>(() => new Promise<Response>(() => {}));

describe("application shell and routing", () => {
  it("renders the OpsPilot AI home screen", () => {
    renderRoute("/", neverResolves);

    expect(screen.getByRole("heading", { level: 1, name: "OpsPilot AI" })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Main" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Backend connection" })).toBeInTheDocument();
    expect(document.title).toBe("OpsPilot AI");
  });

  it("renders a not-found page for unknown routes inside the shell", () => {
    renderRoute("/nowhere", neverResolves);

    expect(screen.getByRole("heading", { name: "Page not found" })).toBeInTheDocument();
    expect(screen.getByLabelText("OpsPilot AI home")).toBeInTheDocument();
  });
});

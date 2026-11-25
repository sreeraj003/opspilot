import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { renderRoute } from "@/test/utils";

describe("HomePage", () => {
  it("shows the product name, description and status panel", () => {
    renderRoute("/");

    expect(screen.getByRole("heading", { level: 1, name: "OpsPilot AI" })).toBeInTheDocument();
    expect(screen.getByText(/Ask questions about your business in natural language/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "System status" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Application" })).toHaveTextContent("Running");
    expect(screen.getByRole("region", { name: "Backend API" })).toHaveTextContent("Checking");
    expect(document.title).toBe("OpsPilot AI");
  });
});

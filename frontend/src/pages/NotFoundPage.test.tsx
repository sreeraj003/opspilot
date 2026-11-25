import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { renderRoute } from "@/test/utils";

describe("NotFoundPage", () => {
  it("renders for unknown routes inside the application shell", () => {
    renderRoute("/does/not/exist");

    expect(screen.getByRole("heading", { name: "Page not found" })).toBeInTheDocument();
    expect(screen.getByText("404")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back to home" })).toHaveAttribute("href", "/");
    expect(screen.getByLabelText("OpsPilot AI home")).toBeInTheDocument();
    expect(document.title).toBe("Page not found · OpsPilot AI");
  });
});

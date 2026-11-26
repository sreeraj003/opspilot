import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { errorResponse, neverResolvingFetch, renderRoute, routeFetch } from "@/test/utils";

describe("authentication and public layouts", () => {
  it("shows the sign-in page in the authentication layout", async () => {
    renderRoute("/login", routeFetch({ "POST /api/v1/auth/refresh": () => errorResponse(401, "UNAUTHORIZED", "No session") }), null);
    const main = await screen.findByRole("main");
    expect(within(main).getByRole("heading", { level: 1, name: "Sign in" })).toBeInTheDocument();
    expect(within(main).getByLabelText("Email")).toHaveFocus();
    expect(screen.getByRole("complementary", { name: "About OpsPilot AI" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Theme/ })).toBeInTheDocument();
    // The authenticated shell is not rendered.
    expect(screen.queryByRole("navigation", { name: "Main" })).not.toBeInTheDocument();
  });

  it("shows a branded loading state while the session is checked, without the login form", () => {
    renderRoute("/login", neverResolvingFetch, null);
    expect(screen.getByRole("status")).toHaveTextContent("Checking your session");
    expect(screen.queryByRole("heading", { name: "Sign in" })).not.toBeInTheDocument();
  });

  it("renders the password reset page in the public layout without waiting for a session", () => {
    window.history.replaceState(null, "", "/reset-password");
    renderRoute("/reset-password", neverResolvingFetch, null);
    expect(screen.getByRole("heading", { name: "Link invalid or expired" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "OpsPilot AI home" })).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Main" })).not.toBeInTheDocument();
  });

  it("sends a signed-in user away from the sign-in page", async () => {
    const { router } = renderRoute("/login", routeFetch({ "GET /health": () => new Promise(() => {}) }));
    expect(await screen.findByRole("navigation", { name: "Main" })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/");
  });
});

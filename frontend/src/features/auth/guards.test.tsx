import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import {
  errorResponse,
  jsonResponse,
  makeAdmin,
  makeCurrentUser,
  neverResolvingFetch,
  renderRoute,
  routeFetch,
  tokenResponse,
} from "@/test/utils";

describe("protected routes", () => {
  it("shows a loading state while the session is being restored", () => {
    renderRoute("/", neverResolvingFetch, null);

    expect(screen.getByRole("status")).toHaveTextContent("Checking your session");
  });

  it("redirects to the login page when there is no session", async () => {
    const { router } = renderRoute(
      "/admin/users?page=2",
      routeFetch({ "POST /api/v1/auth/refresh": () => errorResponse(401, "UNAUTHORIZED", "Expired.") }),
      null,
    );

    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/login");
    expect(router.state.location.state).toEqual({ from: "/admin/users?page=2" });
    // A missing session on first load is not reported as an expired one.
    expect(screen.queryByText(/session has ended/)).not.toBeInTheDocument();
  });

  it("restores the session from the refresh cookie", async () => {
    renderRoute(
      "/account",
      routeFetch({ "POST /api/v1/auth/refresh": () => jsonResponse(tokenResponse(makeCurrentUser())) }),
      null,
    );

    expect(await screen.findByRole("heading", { name: "Your account" })).toBeInTheDocument();
    expect(within(screen.getByRole("main")).getByText("ada@example.com")).toBeInTheDocument();
  });

  it("hides administrator pages from ordinary users", async () => {
    renderRoute("/admin/users", neverResolvingFetch, makeCurrentUser());

    expect(await screen.findByRole("heading", { name: "Not available" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Users" })).not.toBeInTheDocument();
  });

  it("shows the Users navigation to administrators", async () => {
    renderRoute("/", neverResolvingFetch, makeAdmin());

    const nav = await screen.findByRole("navigation", { name: "Main" });
    expect(within(nav).getByRole("link", { name: "Users" })).toBeInTheDocument();
  });

  it("returns to the login page with a notice when the session expires", async () => {
    const user = userEvent.setup();
    const fetchImpl = routeFetch({
      "GET /api/v1/users": () => errorResponse(401, "UNAUTHORIZED", "Authentication is required."),
      "POST /api/v1/auth/refresh": () => errorResponse(401, "UNAUTHORIZED", "Your session has expired. Sign in again."),
    });
    const { router } = renderRoute("/admin/users", fetchImpl, makeAdmin());

    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/login");
    expect(screen.getByRole("status")).toHaveTextContent("Your session has ended");
    expect(fetchImpl.mock.calls.filter(([url]) => String(url).endsWith("/auth/refresh"))).toHaveLength(1);
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("Enter your email address.")).toBeInTheDocument();
  });

  it("signs out, clears user data and returns to the login page", async () => {
    const user = userEvent.setup();
    const fetchImpl = routeFetch({
      "POST /api/v1/auth/logout": () => new Response(null, { status: 204 }),
      "GET /health": () => new Promise(() => {}),
    });
    const { router } = renderRoute("/", fetchImpl, makeCurrentUser());

    await user.click(await screen.findByRole("button", { name: "Sign out" }));
    // A deliberate sign-out is confirmed first; cancelling keeps the session.
    const confirm = screen.getByRole("alertdialog", { name: "Sign out?" });
    expect(within(confirm).getByRole("button", { name: "Cancel" })).toHaveFocus();
    await user.click(within(confirm).getByRole("button", { name: "Sign out" }));

    await waitFor(() => expect(router.state.location.pathname).toBe("/login"));
    const logout = fetchImpl.mock.calls.find(([url]) => String(url).endsWith("/auth/logout"))!;
    expect(logout[1]?.credentials).toBe("include");
    expect((logout[1]?.headers as Record<string, string>)["X-OpsPilot-CSRF"]).toBe("1");
    expect(screen.queryByText(/session has ended/)).not.toBeInTheDocument();
  });
});

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { errorResponse, jsonResponse, makeAdmin, makeCurrentUser, renderRoute, routeFetch, tokenResponse } from "@/test/utils";

const noSession = () => errorResponse(401, "UNAUTHORIZED", "Your session has expired. Sign in again.");

describe("LoginPage", () => {
  it("validates the form before contacting the server", async () => {
    const user = userEvent.setup();
    const fetchImpl = routeFetch({ "POST /api/v1/auth/refresh": noSession });
    renderRoute("/login", fetchImpl, null);

    await user.click(await screen.findByRole("button", { name: "Sign in" }));

    expect(await screen.findByText("Enter your email address.")).toBeInTheDocument();
    expect(screen.getByText("Enter your password.")).toBeInTheDocument();
    await user.type(screen.getByLabelText("Email"), "not-an-email");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("Enter a valid email address.")).toBeInTheDocument();
    expect(fetchImpl.mock.calls.some(([url]) => String(url).endsWith("/auth/login"))).toBe(false);
  });

  it("toggles password visibility", async () => {
    const user = userEvent.setup();
    renderRoute("/login", routeFetch({ "POST /api/v1/auth/refresh": noSession }), null);

    const password = await screen.findByLabelText("Password");
    expect(password).toHaveAttribute("type", "password");
    await user.click(screen.getByRole("button", { name: "Show password" }));
    expect(password).toHaveAttribute("type", "text");
    await user.click(screen.getByRole("button", { name: "Hide password" }));
    expect(password).toHaveAttribute("type", "password");
  });

  it("signs in and returns to the page the user wanted", async () => {
    const user = userEvent.setup();
    const admin = makeAdmin();
    const fetchImpl = routeFetch({
      "POST /api/v1/auth/refresh": noSession,
      "POST /api/v1/auth/login": () => jsonResponse(tokenResponse(admin)),
      "GET /api/v1/users": () => jsonResponse({ items: [admin], total: 1, offset: 0, limit: 20 }),
    });
    const { router } = renderRoute("/admin/users", fetchImpl, null);

    await user.type(await screen.findByLabelText("Email"), "admin@example.com");
    await user.type(screen.getByLabelText("Password"), "correct horse battery staple");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("heading", { name: "Users" })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/admin/users");
    const login = fetchImpl.mock.calls.find(([url]) => String(url).endsWith("/auth/login"))!;
    expect(login[1]?.credentials).toBe("include");
    expect(JSON.parse(String(login[1]?.body))).toEqual({
      email: "admin@example.com",
      password: "correct horse battery staple",
    });
  });

  it("shows the server's generic error and clears the password", async () => {
    const user = userEvent.setup();
    const fetchImpl = routeFetch({
      "POST /api/v1/auth/refresh": noSession,
      "POST /api/v1/auth/login": () => errorResponse(401, "INVALID_CREDENTIALS", "Invalid email or password."),
    });
    renderRoute("/login", fetchImpl, null);

    await user.type(await screen.findByLabelText("Email"), "ada@example.com");
    await user.type(screen.getByLabelText("Password"), "wrong password!!");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid email or password.");
    await waitFor(() => expect(screen.getByLabelText("Password")).toHaveValue(""));
    expect(screen.getByLabelText("Email")).toHaveValue("ada@example.com");
  });

  it("explains rate limiting", async () => {
    const user = userEvent.setup();
    renderRoute(
      "/login",
      routeFetch({
        "POST /api/v1/auth/refresh": noSession,
        "POST /api/v1/auth/login": () => errorResponse(429, "TOO_MANY_REQUESTS", "Too many requests. Try again later."),
      }),
      null,
    );

    await user.type(await screen.findByLabelText("Email"), "ada@example.com");
    await user.type(screen.getByLabelText("Password"), "whatever password");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Too many attempts");
  });

  it("sends a signed-in user away from the login page", async () => {
    const { router } = renderRoute("/login", routeFetch({ "GET /health": () => new Promise(() => {}) }), makeCurrentUser());

    await waitFor(() => expect(router.state.location.pathname).toBe("/"));
  });
});

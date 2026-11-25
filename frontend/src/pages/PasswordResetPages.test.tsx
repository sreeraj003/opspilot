import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { errorResponse, jsonResponse, renderRoute, routeFetch } from "@/test/utils";

const noSession = () => errorResponse(401, "UNAUTHORIZED", "Expired.");
const GENERIC = "If an active account exists for that email address, password reset instructions have been sent.";

describe("ForgotPasswordPage", () => {
  it("shows the same generic confirmation for any email", async () => {
    const user = userEvent.setup();
    const fetchImpl = routeFetch({
      "POST /api/v1/auth/refresh": noSession,
      "POST /api/v1/auth/forgot-password": () => jsonResponse({ message: GENERIC }, 202),
    });
    renderRoute("/forgot-password", fetchImpl, null);

    await user.type(await screen.findByLabelText("Email"), "maybe@example.com");
    await user.click(screen.getByRole("button", { name: "Send reset instructions" }));

    expect(await screen.findByRole("status")).toHaveTextContent(GENERIC);
    const call = fetchImpl.mock.calls.find(([url]) => String(url).endsWith("/forgot-password"))!;
    expect((call[1]?.headers as Record<string, string>).Authorization).toBeUndefined();
  });
});

describe("ResetPasswordPage", () => {
  afterEach(() => {
    window.history.replaceState(null, "", "/");
  });

  it("resets the password with the token from the URL fragment and removes it from the address bar", async () => {
    const user = userEvent.setup();
    window.history.replaceState(null, "", "/reset-password#token=reset-token-abcdefghijklmnop");
    const fetchImpl = routeFetch({
      "POST /api/v1/auth/refresh": noSession,
      "POST /api/v1/auth/reset-password": () => jsonResponse({ message: "ok" }),
    });
    renderRoute("/reset-password", fetchImpl, null);

    expect(window.location.hash).toBe("");
    await user.type(await screen.findByLabelText("New password"), "my new passphrase");
    await user.type(screen.getByLabelText("Confirm new password"), "my new passphrase!");
    await user.click(screen.getByRole("button", { name: "Reset password" }));
    expect(await screen.findByText("The passwords do not match.")).toBeInTheDocument();

    await user.clear(screen.getByLabelText("Confirm new password"));
    await user.type(screen.getByLabelText("Confirm new password"), "my new passphrase");
    await user.click(screen.getByRole("button", { name: "Reset password" }));

    expect(await screen.findByRole("heading", { name: "Password updated" })).toBeInTheDocument();
    const call = fetchImpl.mock.calls.find(([url]) => String(url).endsWith("/reset-password"))!;
    expect(JSON.parse(String(call[1]?.body))).toEqual({
      token: "reset-token-abcdefghijklmnop",
      new_password: "my new passphrase",
    });
  });

  it("shows the invalid-link state for an expired or used token", async () => {
    const user = userEvent.setup();
    window.history.replaceState(null, "", "/reset-password#token=expired-token-abcdefghijkl");
    renderRoute(
      "/reset-password",
      routeFetch({
        "POST /api/v1/auth/refresh": noSession,
        "POST /api/v1/auth/reset-password": () => errorResponse(400, "INVALID_RESET_TOKEN", "Invalid."),
      }),
      null,
    );

    await user.type(await screen.findByLabelText("New password"), "my new passphrase");
    await user.type(screen.getByLabelText("Confirm new password"), "my new passphrase");
    await user.click(screen.getByRole("button", { name: "Reset password" }));

    expect(await screen.findByRole("heading", { name: "Link invalid or expired" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Request a new link" })).toBeInTheDocument();
  });

  it("shows the invalid-link state when there is no token", async () => {
    renderRoute("/reset-password", routeFetch({ "POST /api/v1/auth/refresh": noSession }), null);

    expect(await screen.findByRole("heading", { name: "Link invalid or expired" })).toBeInTheDocument();
  });
});

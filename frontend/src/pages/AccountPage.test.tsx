import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { errorResponse, jsonResponse, makeCurrentUser, renderRoute, routeFetch, tokenResponse } from "@/test/utils";

describe("AccountPage", () => {
  it("changes the password and adopts the new session", async () => {
    const user = userEvent.setup();
    const fetchImpl = routeFetch({
      "POST /api/v1/auth/change-password": () => jsonResponse(tokenResponse(makeCurrentUser(), "after-change")),
    });
    renderRoute("/account", fetchImpl, makeCurrentUser());

    await user.type(await screen.findByLabelText("Current password"), "old passphrase here");
    await user.type(screen.getByLabelText("New password"), "brand new passphrase");
    await user.type(screen.getByLabelText("Confirm new password"), "brand new passphrase");
    await user.click(screen.getByRole("button", { name: "Change password" }));

    expect(await screen.findByText(/Password changed/)).toBeInTheDocument();
    const call = fetchImpl.mock.calls.find(([url]) => String(url).endsWith("/api/v1/auth/change-password"))!;
    expect(call[1]?.credentials).toBe("include");
    expect(JSON.parse(String(call[1]?.body))).toEqual({
      current_password: "old passphrase here",
      new_password: "brand new passphrase",
    });
  });

  it("shows a wrong current password", async () => {
    const user = userEvent.setup();
    renderRoute(
      "/account",
      routeFetch({
        "POST /api/v1/auth/change-password": () => errorResponse(422, "VALIDATION_ERROR", "The current password is incorrect."),
      }),
      makeCurrentUser(),
    );

    await user.type(await screen.findByLabelText("Current password"), "wrong passphrase");
    await user.type(screen.getByLabelText("New password"), "brand new passphrase");
    await user.type(screen.getByLabelText("Confirm new password"), "brand new passphrase");
    await user.click(screen.getByRole("button", { name: "Change password" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("The current password is incorrect.");
  });

  it("signs out everywhere after confirmation", async () => {
    const user = userEvent.setup();
    const fetchImpl = routeFetch({ "POST /api/v1/auth/logout-all": () => new Response(null, { status: 204 }) });
    const { router } = renderRoute("/account", fetchImpl, makeCurrentUser());

    await user.click(await screen.findByRole("button", { name: "Sign out everywhere" }));
    await user.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Sign out everywhere" }));

    await waitFor(() => expect(router.state.location.pathname).toBe("/login"));
    const call = fetchImpl.mock.calls[0]!;
    expect((call[1]?.headers as Record<string, string>).Authorization).toBe("Bearer access-token-1");
  });
});

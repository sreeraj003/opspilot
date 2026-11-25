import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { jsonResponse, makeAdmin, makeUser, renderRoute, routeFetch } from "@/test/utils";

const admin = makeAdmin();
const grace = makeUser({ id: "22222222-2222-4222-8222-222222222222", email: "grace@example.com", full_name: "Grace Hopper" });
const BASE = `/api/v1/users/${grace.id}`;

function routes(extra: Record<string, () => Response> = {}) {
  return routeFetch({
    [`GET ${BASE}`]: () => jsonResponse({ ...grace, active_session_count: 2 }),
    [`GET ${BASE}/sessions`]: () =>
      jsonResponse([
        {
          id: "s1",
          created_at: "2026-11-25T08:00:00Z",
          last_used_at: "2026-11-25T09:00:00Z",
          expires_at: "2026-12-02T09:00:00Z",
          user_agent: "Firefox on Windows",
          ip_address: "10.0.0.5",
        },
      ]),
    ...extra,
  });
}

describe("UserDetailPage", () => {
  it("shows account details and sessions without credential data", async () => {
    renderRoute(`/admin/users/${grace.id}`, routes(), admin);

    expect(await screen.findByRole("heading", { name: "Grace Hopper" })).toBeInTheDocument();
    expect(await screen.findByText("Firefox on Windows")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/password_hash|argon2/i);
  });

  it("updates permitted fields in the edit dialog", async () => {
    const user = userEvent.setup();
    const fetchImpl = routes({ [`PATCH ${BASE}`]: () => jsonResponse({ ...grace, full_name: "Admiral Hopper" }) });
    renderRoute(`/admin/users/${grace.id}`, fetchImpl, admin);

    await user.click(await screen.findByRole("button", { name: "Edit details" }));
    const dialog = screen.getByRole("dialog", { name: "Edit user" });
    const save = within(dialog).getByRole("button", { name: "Save changes" });
    expect(save).toBeDisabled();
    const name = within(dialog).getByLabelText(/Full name/);
    expect(name).toHaveFocus();
    await user.clear(name);
    await user.type(name, "Admiral Hopper");
    await user.click(save);

    await waitFor(() => expect(fetchImpl.mock.calls.some(([, init]) => init?.method === "PATCH")).toBe(true));
    const patch = fetchImpl.mock.calls.find(([, init]) => init?.method === "PATCH")!;
    expect(JSON.parse(String(patch[1]?.body))).toEqual({ full_name: "Admiral Hopper", email: "grace@example.com", username: null });
    expect(await screen.findByText("Changes saved.")).toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "Edit user" })).not.toBeInTheDocument();
  });

  it("validates the edit form before saving", async () => {
    const user = userEvent.setup();
    const fetchImpl = routes();
    renderRoute(`/admin/users/${grace.id}`, fetchImpl, admin);

    await user.click(await screen.findByRole("button", { name: "Edit details" }));
    const dialog = screen.getByRole("dialog", { name: "Edit user" });
    await user.clear(within(dialog).getByLabelText(/Email/));
    await user.type(within(dialog).getByLabelText(/Email/), "not-an-email");
    await user.click(within(dialog).getByRole("button", { name: "Save changes" }));

    const email = within(dialog).getByLabelText(/Email/);
    expect(await within(dialog).findByText("Enter a valid email address.")).toBeInTheDocument();
    expect(email).toHaveAttribute("aria-invalid", "true");
    expect(email.getAttribute("aria-describedby")).toContain(within(dialog).getByText("Enter a valid email address.").id);
    expect(fetchImpl.mock.calls.some(([, init]) => init?.method === "PATCH")).toBe(false);
  });

  it("revokes sessions after confirmation", async () => {
    const user = userEvent.setup();
    const fetchImpl = routes({ [`POST ${BASE}/revoke-sessions`]: () => jsonResponse({ revoked_sessions: 2 }) });
    renderRoute(`/admin/users/${grace.id}`, fetchImpl, admin);

    await user.click(await screen.findByRole("button", { name: "Sign out all sessions" }));
    const dialog = screen.getByRole("alertdialog", { name: "Sign out all sessions?" });
    await user.click(within(dialog).getByRole("button", { name: "Sign out sessions" }));

    expect(await screen.findByText("2 sessions signed out.")).toBeInTheDocument();
  });

  it("creates a one-time reset link and shows it once", async () => {
    const user = userEvent.setup();
    const link = "http://localhost:5173/reset-password#token=abc";
    renderRoute(
      `/admin/users/${grace.id}`,
      routes({ [`POST ${BASE}/password-reset`]: () => jsonResponse({ reset_url: link, expires_at: "2026-11-25T10:00:00Z" }) }),
      admin,
    );

    await user.click(await screen.findByRole("button", { name: "Create password reset link" }));
    await user.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Create link" }));

    const dialog = await screen.findByRole("dialog", { name: "One-time password reset link" });
    expect(within(dialog).getByLabelText("Password reset link")).toHaveValue(link);
    await user.click(within(dialog).getByRole("button", { name: "Close" }));
    expect(screen.queryByDisplayValue(link)).not.toBeInTheDocument();
  });
});

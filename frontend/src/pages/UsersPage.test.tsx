import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { errorResponse, jsonResponse, makeAdmin, makeUser, renderRoute, routeFetch } from "@/test/utils";
import type { User } from "@/types/auth";

const admin = makeAdmin();
const grace = makeUser({ id: "22222222-2222-4222-8222-222222222222", email: "grace@example.com", full_name: "Grace Hopper" });
const alan = makeUser({
  id: "33333333-3333-4333-8333-333333333333",
  email: "alan@example.com",
  full_name: "Alan Turing",
  is_active: false,
  last_login_at: null,
});

function page(items: User[], total = items.length, offset = 0) {
  return jsonResponse({ items, total, offset, limit: 20 });
}

describe("UsersPage", () => {
  it("lists users with status, last sign-in and actions", async () => {
    renderRoute("/admin/users", routeFetch({ "GET /api/v1/users": () => page([admin, grace, alan]) }), admin);

    const table = await screen.findByRole("table");
    const rows = within(table).getAllByRole("row");
    expect(rows).toHaveLength(4);
    expect(within(rows[2]!).getByText("Grace Hopper")).toBeInTheDocument();
    expect(within(rows[2]!).getByText("grace@example.com")).toBeInTheDocument();
    expect(within(rows[2]!).getByText("Active")).toBeInTheDocument();
    expect(within(rows[3]!).getByText("Inactive")).toBeInTheDocument();
    expect(within(rows[3]!).getByText("Never")).toBeInTheDocument();
    expect(within(rows[2]!).getByRole("button", { name: "Deactivate Grace Hopper" })).toBeInTheDocument();
    expect(within(rows[3]!).getByRole("button", { name: "Activate Alan Turing" })).toBeInTheDocument();
    // The administrator's own account cannot be deactivated from the list.
    expect(within(rows[1]!).queryByRole("button", { name: /Deactivate/ })).not.toBeInTheDocument();
    expect(screen.getByText("Showing 1–3 of 3")).toBeInTheDocument();
  });

  it("searches, filters and paginates through the API", async () => {
    const user = userEvent.setup();
    const fetchImpl = routeFetch({
      "GET /api/v1/users": (_init, url) => {
        const query = new URL(url).searchParams;
        if (query.get("search") === "grace") return page([grace]);
        if (query.get("offset") === "20") return page([alan], 21, 20);
        return page(Array.from({ length: 20 }, (_, i) => makeUser({ id: `id-${i}`, email: `u${i}@example.com`, full_name: `User ${i}` })), 21);
      },
    });
    renderRoute("/admin/users", fetchImpl, admin);

    expect(await screen.findByText("Showing 1–20 of 21")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Next/ }));
    expect(await screen.findByText("Alan Turing")).toBeInTheDocument();
    expect(screen.getByText("Showing 21–21 of 21")).toBeInTheDocument();

    await user.type(screen.getByRole("searchbox", { name: "Search users" }), "grace");
    expect(await screen.findByText("Grace Hopper")).toBeInTheDocument();

    await user.selectOptions(screen.getByRole("combobox", { name: "Filter by status" }), "inactive");
    await waitFor(() =>
      expect(fetchImpl.mock.calls.some(([url]) => new URL(String(url)).searchParams.get("status") === "inactive")).toBe(true),
    );
    const searched = fetchImpl.mock.calls.map(([url]) => new URL(String(url)).searchParams).find((q) => q.get("search"));
    expect(searched?.get("offset")).toBe("0");
  });

  it("shows empty and error states", async () => {
    const user = userEvent.setup();
    let fail = true;
    renderRoute(
      "/admin/users",
      routeFetch({
        "GET /api/v1/users": () => (fail ? errorResponse(500, "INTERNAL_SERVER_ERROR", "An unexpected error occurred.") : page([])),
      }),
      admin,
    );

    expect(await screen.findByRole("alert")).toHaveTextContent("An unexpected error occurred.");
    fail = false;
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("No users found")).toBeInTheDocument();
  });

  it("creates a user", async () => {
    const user = userEvent.setup();
    const created = makeUser({ id: "44444444-4444-4444-8444-444444444444", email: "new@example.com", full_name: "New Person" });
    const fetchImpl = routeFetch({
      "GET /api/v1/users": () => page([admin]),
      "POST /api/v1/users": () => jsonResponse(created, 201),
      [`GET /api/v1/users/${created.id}`]: () => jsonResponse({ ...created, active_session_count: 0 }),
      [`GET /api/v1/users/${created.id}/sessions`]: () => jsonResponse([]),
    });
    const { router } = renderRoute("/admin/users", fetchImpl, admin);

    await user.click(await screen.findByRole("button", { name: "Create user" }));
    const dialog = screen.getByRole("dialog", { name: "Create user" });
    await user.click(within(dialog).getByRole("button", { name: "Create user" }));
    expect(await within(dialog).findByText("Enter the person's name.")).toBeInTheDocument();
    expect(within(dialog).getByText("Use at least 12 characters.")).toBeInTheDocument();

    await user.type(within(dialog).getByLabelText("Full name"), "New Person");
    await user.type(within(dialog).getByLabelText("Email"), "new@example.com");
    await user.type(within(dialog).getByLabelText("Initial password"), "a long initial passphrase");
    await user.click(within(dialog).getByRole("button", { name: "Create user" }));

    await waitFor(() => expect(router.state.location.pathname).toBe(`/admin/users/${created.id}`));
    const post = fetchImpl.mock.calls.find(([, init]) => init?.method === "POST")!;
    expect(JSON.parse(String(post[1]?.body))).toEqual({
      full_name: "New Person",
      email: "new@example.com",
      username: null,
      password: "a long initial passphrase",
      is_active: true,
    });
    expect((post[1]?.headers as Record<string, string>).Authorization).toBe("Bearer access-token-1");
  });

  it("shows a server conflict when creating a duplicate user", async () => {
    const user = userEvent.setup();
    renderRoute(
      "/admin/users",
      routeFetch({
        "GET /api/v1/users": () => page([admin]),
        "POST /api/v1/users": () => errorResponse(409, "CONFLICT", "A user with this email address already exists."),
      }),
      admin,
    );

    await user.click(await screen.findByRole("button", { name: "Create user" }));
    const dialog = screen.getByRole("dialog", { name: "Create user" });
    await user.type(within(dialog).getByLabelText("Full name"), "Dup");
    await user.type(within(dialog).getByLabelText("Email"), "admin@example.com");
    await user.type(within(dialog).getByLabelText("Initial password"), "a long initial passphrase");
    await user.click(within(dialog).getByRole("button", { name: "Create user" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent("already exists");
  });

  it("asks for confirmation before deactivating a user", async () => {
    const user = userEvent.setup();
    let graceActive = true;
    const fetchImpl = routeFetch({
      "GET /api/v1/users": () => page([admin, { ...grace, is_active: graceActive }]),
      [`POST /api/v1/users/${grace.id}/deactivate`]: () => {
        graceActive = false;
        return jsonResponse({ ...grace, is_active: false });
      },
    });
    renderRoute("/admin/users", fetchImpl, admin);

    await user.click(await screen.findByRole("button", { name: "Deactivate Grace Hopper" }));
    const confirm = screen.getByRole("alertdialog", { name: "Deactivate user?" });
    expect(confirm).toHaveTextContent("signed out everywhere");

    await user.click(within(confirm).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(fetchImpl.mock.calls.some(([url]) => String(url).endsWith("/deactivate"))).toBe(false);

    await user.click(screen.getByRole("button", { name: "Deactivate Grace Hopper" }));
    await user.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Deactivate" }));

    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(await screen.findByRole("button", { name: "Activate Grace Hopper" })).toBeInTheDocument();
    expect(within(screen.getByRole("list", { name: "Notifications" })).getByText("User deactivated")).toBeInTheDocument();
  });
});

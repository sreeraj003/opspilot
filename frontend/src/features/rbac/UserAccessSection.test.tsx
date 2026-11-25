import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { makeEffective, makeRole, page, ROLE_ID } from "@/test/rbacFixtures";
import { jsonResponse, makeAdmin, makeCurrentUser, makeUser, renderRoute, routeFetch } from "@/test/utils";

const admin = makeAdmin();
const grace = makeUser({ id: "22222222-2222-4222-8222-222222222222", email: "grace@example.com", full_name: "Grace Hopper" });
const BASE = `/api/v1/users/${grace.id}`;
const analyst = makeRole({ id: "ffffffff-ffff-4fff-8fff-ffffffffffff", key: "analyst", name: "Analyst" });

const effective = makeEffective({
  roles: [{ id: ROLE_ID, key: "regional_manager", name: "Regional Manager", expires_at: null }],
  permission_keys: ["sales.karnataka.view"],
  revoked_permission_keys: ["profit.view"],
  data_access: [
    {
      action: "view",
      tables: [
        {
          table: "sales",
          columns: ["amount", "region"],
          denied_columns: ["customer_email"],
          operations: ["aggregate", "read"],
          row_filter: { column: "sales.region", operator: "equals", value: "Karnataka" },
          allow_export: false,
          allow_sql_view: false,
        },
      ],
    },
  ],
});

function routes(extra: Record<string, (init: RequestInit | undefined) => Response> = {}) {
  return routeFetch({
    [`GET ${BASE}`]: () => jsonResponse({ ...grace, active_session_count: 0 }),
    [`GET ${BASE}/sessions`]: () => jsonResponse([]),
    [`GET ${BASE}/roles`]: () =>
      jsonResponse([{ role: makeRole(), assigned_at: "2026-11-20T08:00:00Z", assigned_by: null, expires_at: null, is_expired: false }]),
    [`GET ${BASE}/permissions`]: () => jsonResponse([]),
    [`GET ${BASE}/effective-permissions`]: () => jsonResponse(effective),
    "GET /api/v1/roles": () => jsonResponse(page([makeRole(), analyst])),
    "GET /api/v1/permissions": () => jsonResponse(page([])),
    ...extra,
  });
}

describe("user access on the user page", () => {
  it("shows the user's effective permissions and data restrictions", async () => {
    renderRoute(`/admin/users/${grace.id}`, routes(), admin);

    const keys = await screen.findByRole("list", { name: "Effective permissions" });
    expect(keys).toHaveTextContent("sales.karnataka.view");
    expect(screen.getByText("Denied directly: profit.view")).toBeInTheDocument();
    const data = screen.getByRole("list", { name: "Data access" });
    expect(data).toHaveTextContent("sales · view");
    expect(data).toHaveTextContent("Columns: amount, region");
    expect(data).toHaveTextContent("Hidden columns: customer_email");
    expect(data).toHaveTextContent("Rows: sales.region equals Karnataka");
    expect(within(screen.getByRole("list", { name: "Assigned roles" })).getByText("Regional Manager")).toBeInTheDocument();
  });

  it("assigns a role with an optional expiry", async () => {
    const user = userEvent.setup();
    const fetchImpl = routes({
      [`POST ${BASE}/roles`]: () =>
        jsonResponse({ role: analyst, assigned_at: "2026-11-25T08:00:00Z", assigned_by: admin.id, expires_at: null, is_expired: false }, 201),
    });
    renderRoute(`/admin/users/${grace.id}`, fetchImpl, admin);

    const select = await screen.findByLabelText("Role to assign");
    // Roles the user already has are not offered again.
    await waitFor(() => expect(within(select).getByRole("option", { name: "Analyst" })).toBeInTheDocument());
    expect(within(select).queryByRole("option", { name: "Regional Manager" })).not.toBeInTheDocument();
    await user.selectOptions(select, analyst.id);
    await user.type(screen.getByLabelText("Role expires on (optional)"), "2026-12-31");
    await user.click(screen.getByRole("button", { name: "Assign role" }));

    await waitFor(() => expect(fetchImpl.mock.calls.some(([, init]) => init?.method === "POST")).toBe(true));
    const post = fetchImpl.mock.calls.find(([, init]) => init?.method === "POST")!;
    const body = JSON.parse(String(post[1]?.body));
    expect(body.role_id).toBe(analyst.id);
    expect(new Date(body.expires_at).getFullYear()).toBe(2026);
  });

  it("removes a role after confirmation", async () => {
    const user = userEvent.setup();
    const fetchImpl = routes({ [`DELETE ${BASE}/roles/${ROLE_ID}`]: () => new Response(null, { status: 204 }) });
    renderRoute(`/admin/users/${grace.id}`, fetchImpl, admin);

    await user.click(await screen.findByRole("button", { name: "Remove role Regional Manager" }));
    await user.click(within(screen.getByRole("alertdialog", { name: "Remove role?" })).getByRole("button", { name: "Remove" }));

    await waitFor(() => expect(fetchImpl.mock.calls.some(([, init]) => init?.method === "DELETE")).toBe(true));
  });

  it("lets reviewers see access but not change it", async () => {
    const reviewer = makeCurrentUser({ id: "33333333-3333-4333-8333-333333333333", permissions: ["users:read", "access:review"] });
    renderRoute(`/admin/users/${grace.id}`, routes(), reviewer);

    expect(await screen.findByRole("list", { name: "Effective permissions" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Role to assign")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Remove role/ })).not.toBeInTheDocument();
  });

  it("hides the access section without access:review", async () => {
    const fetchImpl = routes();
    renderRoute(`/admin/users/${grace.id}`, fetchImpl, makeCurrentUser({ id: "33333333-3333-4333-8333-333333333333", permissions: ["users:read"] }));

    expect(await screen.findByRole("heading", { name: "Grace Hopper" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Effective access" })).not.toBeInTheDocument();
    expect(fetchImpl.mock.calls.some(([url]) => String(url).includes("effective-permissions"))).toBe(false);
  });
});

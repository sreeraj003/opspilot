import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { makePermission, makeRoleDetail, page, ROLE_ID } from "@/test/rbacFixtures";
import { jsonResponse, makeAdmin, makeCurrentUser, renderRoute, routeFetch } from "@/test/utils";

const admin = makeAdmin();
const EXPORT_ID = "dddddddd-dddd-4ddd-8ddd-dddddddddddd";
const exportPermission = makePermission({ id: EXPORT_ID, key: "reports.export", name: "Export reports", resource_type: "reports", action: "export" });
const reserved = makePermission({ id: "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee", key: "roles:manage", name: "Manage roles", resource_type: "system", is_system: true });

describe("RoleDetailPage", () => {
  it("assigns permissions to a role after reviewing the change", async () => {
    const user = userEvent.setup();
    const fetchImpl = routeFetch({
      [`GET /api/v1/roles/${ROLE_ID}`]: () => jsonResponse(makeRoleDetail()),
      "GET /api/v1/permissions": () => jsonResponse(page([makePermission(), exportPermission, reserved])),
      [`PUT /api/v1/roles/${ROLE_ID}/permissions`]: () => jsonResponse({ added: 1, removed: 1 }),
    });
    renderRoute(`/admin/roles/${ROLE_ID}`, fetchImpl, admin);

    expect(await screen.findByRole("list", { name: "Assigned permissions" })).toHaveTextContent("View Karnataka sales");
    await user.click(screen.getByRole("button", { name: "Edit permissions" }));
    await user.click(await screen.findByRole("checkbox", { name: "Export reports" }));
    await user.click(screen.getByRole("checkbox", { name: "View Karnataka sales" }));
    // Administrator-only permissions cannot be given to other roles.
    expect(screen.getByRole("checkbox", { name: "Manage roles" })).toBeDisabled();
    expect(screen.getByText("1 to add, 1 to remove")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Review changes" }));
    const dialog = screen.getByRole("dialog", { name: "Review permission changes" });
    expect(within(dialog).getByRole("list", { name: "Permissions to add" })).toHaveTextContent("Export reports");
    expect(within(dialog).getByRole("list", { name: "Permissions to remove" })).toHaveTextContent("View Karnataka sales");
    expect(within(dialog).getByText("Affects 2 users.")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    const put = fetchImpl.mock.calls.find(([, init]) => init?.method === "PUT")!;
    expect(JSON.parse(String(put[1]?.body))).toEqual({ permission_ids: [EXPORT_ID] });
  });

  it("keeps system roles fixed", async () => {
    const fetchImpl = routeFetch({
      [`GET /api/v1/roles/${ROLE_ID}`]: () => jsonResponse(makeRoleDetail({ key: "administrator", name: "Administrator", is_system: true })),
    });
    renderRoute(`/admin/roles/${ROLE_ID}`, fetchImpl, admin);

    expect(await screen.findByText("The permissions of system roles are fixed.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit permissions" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("Name")).toBeDisabled();
  });

  it("shows the role read-only without roles:manage", async () => {
    const fetchImpl = routeFetch({ [`GET /api/v1/roles/${ROLE_ID}`]: () => jsonResponse(makeRoleDetail()) });
    renderRoute(`/admin/roles/${ROLE_ID}`, fetchImpl, makeCurrentUser({ permissions: ["roles:read"] }));

    expect(await screen.findByRole("heading", { name: "Regional Manager" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit permissions" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Deactivate" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save changes" })).not.toBeInTheDocument();
  });
});

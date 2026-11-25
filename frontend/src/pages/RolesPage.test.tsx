import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { makeRole, makeRoleDetail, page, ROLE_ID } from "@/test/rbacFixtures";
import { errorResponse, jsonResponse, makeAdmin, makeCurrentUser, renderRoute, routeFetch } from "@/test/utils";

const admin = makeAdmin();
const reviewer = makeCurrentUser({ permissions: ["roles:read", "permissions:read"] });
const systemRole = makeRole({ id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc", key: "administrator", name: "Administrator", is_system: true, description: null });

describe("RolesPage", () => {
  it("lists roles with their status and counts", async () => {
    const fetchImpl = routeFetch({ "GET /api/v1/roles": () => jsonResponse(page([systemRole, makeRole()])) });
    renderRoute("/admin/roles", fetchImpl, admin);

    const row = (await screen.findByRole("link", { name: "Regional Manager" })).closest("tr")!;
    expect(within(row).getByText("regional_manager")).toBeInTheDocument();
    expect(within(row).getByText("Active")).toBeInTheDocument();
    expect(screen.getByText("System")).toBeInTheDocument();
    // Built-in roles cannot be deactivated or deleted.
    expect(screen.queryByRole("button", { name: "Delete Administrator" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete Regional Manager" })).toBeInTheDocument();
  });

  it("searches and filters through the API", async () => {
    const user = userEvent.setup();
    const fetchImpl = routeFetch({ "GET /api/v1/roles": () => jsonResponse(page([makeRole()])) });
    renderRoute("/admin/roles", fetchImpl, admin);
    await screen.findByRole("link", { name: "Regional Manager" });

    await user.type(screen.getByLabelText("Search roles"), "region");
    await user.selectOptions(screen.getByLabelText("Filter by status"), "inactive");

    await waitFor(() => {
      const urls = fetchImpl.mock.calls.map(([url]) => String(url));
      expect(urls.some((url) => url.includes("search=region") && url.includes("status=inactive"))).toBe(true);
    });
  });

  it("creates a role with a key suggested from its name", async () => {
    const user = userEvent.setup();
    const created = makeRole({ id: ROLE_ID, name: "Finance Analyst", key: "finance_analyst" });
    const fetchImpl = routeFetch({
      "GET /api/v1/roles": () => jsonResponse(page([])),
      "POST /api/v1/roles": () => jsonResponse(created, 201),
      [`GET /api/v1/roles/${ROLE_ID}`]: () => jsonResponse(makeRoleDetail({ ...created, permissions: [] })),
    });
    const { router } = renderRoute("/admin/roles", fetchImpl, admin);

    await user.click(await screen.findByRole("button", { name: "Create role" }));
    const dialog = screen.getByRole("dialog", { name: "Create role" });
    await user.type(within(dialog).getByLabelText("Name"), "Finance Analyst");
    expect(within(dialog).getByLabelText("Key")).toHaveValue("finance_analyst");
    await user.click(within(dialog).getByRole("button", { name: "Create role" }));

    await waitFor(() => expect(router.state.location.pathname).toBe(`/admin/roles/${ROLE_ID}`));
    const post = fetchImpl.mock.calls.find(([, init]) => init?.method === "POST")!;
    expect(JSON.parse(String(post[1]?.body))).toEqual({ name: "Finance Analyst", key: "finance_analyst", description: null });
  });

  it("shows the server's reason when a role cannot be created", async () => {
    const user = userEvent.setup();
    const fetchImpl = routeFetch({
      "GET /api/v1/roles": () => jsonResponse(page([])),
      "POST /api/v1/roles": () => errorResponse(409, "CONFLICT", "A role with this key or name already exists."),
    });
    renderRoute("/admin/roles", fetchImpl, admin);

    await user.click(await screen.findByRole("button", { name: "Create role" }));
    const dialog = screen.getByRole("dialog", { name: "Create role" });
    await user.type(within(dialog).getByLabelText("Name"), "Analyst");
    await user.click(within(dialog).getByRole("button", { name: "Create role" }));

    expect(await within(dialog).findByText("A role with this key or name already exists.")).toBeInTheDocument();
  });

  it("hides management actions from users who may only read roles", async () => {
    const fetchImpl = routeFetch({ "GET /api/v1/roles": () => jsonResponse(page([makeRole()])) });
    renderRoute("/admin/roles", fetchImpl, reviewer);

    await screen.findByRole("link", { name: "Regional Manager" });
    expect(screen.queryByRole("button", { name: "Create role" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Delete/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Deactivate/ })).not.toBeInTheDocument();
  });

  it("is not reachable without roles:read", async () => {
    renderRoute("/admin/roles", routeFetch({}), makeCurrentUser({ permissions: [] }));

    expect(await screen.findByRole("heading", { name: "Not available" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Roles" })).not.toBeInTheDocument();
  });
});

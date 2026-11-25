import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { makePermission, makePermissionDetail, page, PERMISSION_ID } from "@/test/rbacFixtures";
import { jsonResponse, makeAdmin, makeCurrentUser, renderRoute, routeFetch } from "@/test/utils";

const admin = makeAdmin();

function echoValidation(init: RequestInit | undefined) {
  const body = JSON.parse(String(init?.body));
  return jsonResponse({ valid: true, policy_definition: body.policy_definition });
}

function bodyOf(fetchImpl: ReturnType<typeof routeFetch>, method: string, path: string) {
  const call = fetchImpl.mock.calls.find(([url, init]) => init?.method === method && String(url).endsWith(path));
  return call ? JSON.parse(String(call[1]?.body)) : undefined;
}

describe("PermissionEditorPage", () => {
  it("creates a row-restricted data permission without writing JSON", async () => {
    const user = userEvent.setup();
    const fetchImpl = routeFetch({
      "POST /api/v1/permissions/validate-policy": (init) => echoValidation(init),
      "POST /api/v1/permissions": () => jsonResponse(makePermission(), 201),
      [`GET /api/v1/permissions/${PERMISSION_ID}`]: () => jsonResponse(makePermissionDetail()),
    });
    const { router } = renderRoute("/admin/permissions/new", fetchImpl, admin);

    await user.type(await screen.findByLabelText("Name"), "View Karnataka sales");
    const key = screen.getByLabelText("Key");
    await user.clear(key);
    await user.type(key, "sales.karnataka.view");
    await user.type(screen.getByLabelText("Allowed tables"), "sales{Enter}");
    await user.click(screen.getByLabelText("All columns of sales"));
    await user.click(screen.getByRole("button", { name: "Add row filter" }));
    await user.type(screen.getByLabelText("Row filter 1 column"), "region");
    await user.type(screen.getByLabelText("Row filter 1 value"), "Karnataka");
    await user.click(screen.getByLabelText("Aggregates only (totals, counts, averages)"));

    await user.click(screen.getByRole("button", { name: "Review" }));

    // The review shows a summary and the exact policy that will be saved.
    const summary = await screen.findByRole("list", { name: "Policy summary" });
    expect(within(summary).getByText("Only rows where region equals Karnataka.")).toBeInTheDocument();
    expect(within(summary).getByText("Only individual rows are allowed.")).toBeInTheDocument();
    expect(screen.getByLabelText("Policy JSON")).toHaveTextContent('"allowed_tables": [ "sales" ]');
    expect(bodyOf(fetchImpl, "POST", "/validate-policy").resource_type).toBe("database_data");

    await user.click(screen.getByRole("button", { name: "Create permission" }));
    await waitFor(() => expect(router.state.location.pathname).toBe(`/admin/permissions/${PERMISSION_ID}`));
    expect(bodyOf(fetchImpl, "POST", "/api/v1/permissions")).toEqual({
      key: "sales.karnataka.view",
      name: "View Karnataka sales",
      description: null,
      resource_type: "database_data",
      action: "view",
      is_active: true,
      expires_at: null,
      policy_definition: {
        version: 1,
        effect: "allow",
        allowed_tables: ["sales"],
        allowed_columns: ["sales.*"],
        allowed_operations: ["read"],
        row_filters: [{ column: "region", operator: "equals", value: "Karnataka" }],
        allow_export: false,
        allow_sql_view: false,
      },
    });
  });

  it("supports OR groups for several allowed values", async () => {
    const user = userEvent.setup();
    const fetchImpl = routeFetch({ "POST /api/v1/permissions/validate-policy": (init) => echoValidation(init) });
    renderRoute("/admin/permissions/new", fetchImpl, admin);

    await user.type(await screen.findByLabelText("Name"), "South sales");
    await user.type(screen.getByLabelText("Allowed tables"), "sales{Enter}");
    await user.click(screen.getByLabelText("All columns of sales"));
    await user.click(screen.getByRole("button", { name: "Add group" }));
    await user.type(screen.getByLabelText("Row filter 1 › 1 column"), "region");
    await user.type(screen.getByLabelText("Row filter 1 › 1 value"), "Karnataka");
    await user.click(within(screen.getByRole("group", { name: "Row filter 1 (group)" })).getByRole("button", { name: "Add condition" }));
    await user.type(screen.getByLabelText("Row filter 1 › 2 column"), "region");
    await user.type(screen.getByLabelText("Row filter 1 › 2 value"), "Kerala");
    await user.click(screen.getByRole("button", { name: "Review" }));

    expect(await screen.findByText("Only rows where (region equals Karnataka OR region equals Kerala).")).toBeInTheDocument();
    expect(bodyOf(fetchImpl, "POST", "/validate-policy").policy_definition.row_filters).toEqual([
      {
        logic: "or",
        conditions: [
          { column: "region", operator: "equals", value: "Karnataka" },
          { column: "region", operator: "equals", value: "Kerala" },
        ],
      },
    ]);
  });

  it("explains incomplete rules before contacting the server", async () => {
    const user = userEvent.setup();
    const fetchImpl = routeFetch({});
    renderRoute("/admin/permissions/new", fetchImpl, admin);

    await user.click(await screen.findByRole("button", { name: "Review" }));

    const problems = await screen.findByRole("list", { name: "Problems" });
    expect(within(problems).getByText("Enter a name.")).toBeInTheDocument();
    expect(within(problems).getByText("Add at least one table.")).toBeInTheDocument();
    expect(fetchImpl.mock.calls.some(([url]) => String(url).includes("validate-policy"))).toBe(false);
  });

  it("shows the server's validation details", async () => {
    const user = userEvent.setup();
    const fetchImpl = routeFetch({
      "POST /api/v1/permissions/validate-policy": () =>
        jsonResponse(
          {
            success: false,
            error: {
              code: "INVALID_POLICY",
              message: "The policy is invalid.",
              details: [{ location: ["policy_definition", "row_filters", "0"], message: "The conditions contradict each other." }],
              request_id: "r",
            },
          },
          422,
        ),
    });
    renderRoute("/admin/permissions/new", fetchImpl, admin);

    await user.type(await screen.findByLabelText("Name"), "Sales");
    await user.type(screen.getByLabelText("Allowed tables"), "sales{Enter}");
    await user.click(screen.getByLabelText("All columns of sales"));
    await user.click(screen.getByRole("button", { name: "Review" }));

    expect(await screen.findByText("The server rejected this policy:")).toBeInTheDocument();
    expect(screen.getByText("The conditions contradict each other.")).toBeInTheDocument();
    expect(screen.getByText("row_filters › 0:")).toBeInTheDocument();
  });

  it("loads an existing permission into the builder and saves changes", async () => {
    const user = userEvent.setup();
    const fetchImpl = routeFetch({
      [`GET /api/v1/permissions/${PERMISSION_ID}`]: () => jsonResponse(makePermissionDetail()),
      "POST /api/v1/permissions/validate-policy": (init) => echoValidation(init),
      [`PATCH /api/v1/permissions/${PERMISSION_ID}`]: () => jsonResponse(makePermission()),
    });
    renderRoute(`/admin/permissions/${PERMISSION_ID}`, fetchImpl, admin);

    expect(await screen.findByLabelText("Row filter 1 value")).toHaveValue("Karnataka");
    expect(screen.getByLabelText("Key")).toBeDisabled();
    await user.click(screen.getByLabelText("Allow exporting results"));
    await user.click(screen.getByRole("button", { name: "Review" }));
    await user.click(await screen.findByRole("button", { name: "Save permission" }));

    expect(await screen.findByText(/Permission saved/)).toBeInTheDocument();
    const patch = bodyOf(fetchImpl, "PATCH", `/permissions/${PERMISSION_ID}`);
    expect(patch.key).toBeUndefined();
    expect(patch.policy_definition.allow_export).toBe(true);
    expect(patch.policy_definition.row_filters).toEqual([{ column: "sales.region", operator: "equals", value: "Karnataka" }]);
  });

  it("is read-only for users who may only read permissions", async () => {
    const fetchImpl = routeFetch({
      "GET /api/v1/permissions": () => jsonResponse(page([makePermission()])),
      [`GET /api/v1/permissions/${PERMISSION_ID}`]: () => jsonResponse(makePermissionDetail()),
    });
    renderRoute(`/admin/permissions/${PERMISSION_ID}`, fetchImpl, makeCurrentUser({ permissions: ["permissions:read"] }));

    expect(await screen.findByLabelText("Policy JSON")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Review" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Name")).not.toBeInTheDocument();
  });

  it("hides create, edit and delete actions on the list without permissions:manage", async () => {
    const fetchImpl = routeFetch({ "GET /api/v1/permissions": () => jsonResponse(page([makePermission()])) });
    renderRoute("/admin/permissions", fetchImpl, makeCurrentUser({ permissions: ["permissions:read"] }));

    expect(await screen.findByRole("link", { name: "View Karnataka sales" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Create permission" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Delete/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "View View Karnataka sales" })).toBeInTheDocument();
  });
});


import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { usePermissions } from "@/features/permissions/permissionsContext";
import { makeEffective } from "@/test/rbacFixtures";
import { jsonResponse, makeCurrentUser, neverResolvingFetch, renderRoute, renderWithProviders, routeFetch } from "@/test/utils";

const me = makeCurrentUser({ permissions: ["users:read"] });

function Probe() {
  const { hasPermission, canAccessResource, effectivePermissions } = usePermissions();
  return (
    <ul>
      <li>users:read={String(hasPermission("users:read"))}</li>
      <li>roles:read={String(hasPermission("roles:read"))}</li>
      <li>sales={String(hasPermission("sales.karnataka.view"))}</li>
      <li>data={String(canAccessResource("database_data", "view"))}</li>
      <li>reports={String(canAccessResource("executive_reports", "view"))}</li>
      <li>loaded={String(effectivePermissions !== null)}</li>
    </ul>
  );
}

const serverAnswer = makeEffective({
  user_id: me.id,
  system_permissions: ["roles:read"],
  permission_keys: ["sales.karnataka.view"],
  data_access: [
    {
      action: "view",
      tables: [{ table: "sales", columns: ["*"], denied_columns: [], operations: ["read"], row_filter: null, allow_export: false, allow_sql_view: false }],
    },
  ],
});

describe("usePermissions", () => {
  it("uses the sign-in permissions until the server answers", () => {
    renderWithProviders(<Probe />, neverResolvingFetch, me);

    expect(screen.getByText("users:read=true")).toBeInTheDocument();
    expect(screen.getByText("roles:read=false")).toBeInTheDocument();
    expect(screen.getByText("data=false")).toBeInTheDocument();
    expect(screen.getByText("loaded=false")).toBeInTheDocument();
  });

  it("follows the server's effective permissions once loaded", async () => {
    renderWithProviders(<Probe />, routeFetch({ "GET /api/v1/authorization/me": () => jsonResponse(serverAnswer) }), me);

    expect(await screen.findByText("loaded=true")).toBeInTheDocument();
    // users:read was revoked since sign-in; roles:read was granted.
    expect(screen.getByText("users:read=false")).toBeInTheDocument();
    expect(screen.getByText("roles:read=true")).toBeInTheDocument();
    expect(screen.getByText("sales=true")).toBeInTheDocument();
    expect(screen.getByText("data=true")).toBeInTheDocument();
    expect(screen.getByText("reports=false")).toBeInTheDocument();
  });

  it("ignores an answer that belongs to another user", async () => {
    const other = { ...serverAnswer, user_id: "99999999-9999-4999-8999-999999999999" };
    const fetchImpl = routeFetch({ "GET /api/v1/authorization/me": () => jsonResponse(other) });
    renderWithProviders(<Probe />, fetchImpl, me);

    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(fetchImpl).toHaveBeenCalled();
    expect(screen.getByText("loaded=false")).toBeInTheDocument();
    expect(screen.getByText("roles:read=false")).toBeInTheDocument();
  });

  it("shows only the admin navigation the user may open", async () => {
    renderRoute("/account", routeFetch({ "GET /api/v1/authorization/me": () => jsonResponse(serverAnswer) }), me);

    expect((await screen.findAllByRole("link", { name: "Roles" })).length).toBeGreaterThan(0);
    expect(screen.queryAllByRole("link", { name: "Permissions" })).toHaveLength(0);
    expect(screen.queryAllByRole("link", { name: "Users" })).toHaveLength(0);
  });
});

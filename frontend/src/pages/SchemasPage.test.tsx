import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { makeSchema, makeSummary, SCHEMA_ID, schemaPage } from "@/test/schemaFixtures";
import { errorResponse, jsonResponse, makeAdmin, makeCurrentUser, neverResolvingFetch, renderRoute, routeFetch } from "@/test/utils";

const admin = makeAdmin();
const reader = makeCurrentUser({ permissions: ["schema.read"] });

describe("SchemasPage", () => {
  it("lists registered schemas with their real counts and version", async () => {
    const fetchImpl = routeFetch({ "GET /api/v1/schemas": () => jsonResponse(schemaPage([makeSchema()])) });
    renderRoute("/admin/schemas", fetchImpl, admin);

    const row = (await screen.findByRole("link", { name: "Sales DB" })).closest("tr")!;
    expect(within(row).getByText("PostgreSQL")).toBeInTheDocument();
    expect(within(row).getByText("2")).toBeInTheDocument();
    expect(within(row).getByText("5")).toBeInTheDocument();
    expect(within(row).getByText("v3")).toBeInTheDocument();
    expect(within(row).getByText("Active")).toBeInTheDocument();
  });

  it("searches and filters through the API", async () => {
    const user = userEvent.setup();
    const fetchImpl = routeFetch({ "GET /api/v1/schemas": () => jsonResponse(schemaPage([makeSchema()])) });
    renderRoute("/admin/schemas", fetchImpl, admin);
    await screen.findByRole("link", { name: "Sales DB" });

    await user.type(screen.getByLabelText("Search schemas"), "sales");
    await user.selectOptions(screen.getByLabelText("Filter by database type"), "mysql");
    await user.selectOptions(screen.getByLabelText("Filter by status"), "inactive");

    await waitFor(() => {
      const urls = fetchImpl.mock.calls.map(([url]) => String(url));
      expect(
        urls.some((url) => url.includes("search=sales") && url.includes("database_type=mysql") && url.includes("status=inactive")),
      ).toBe(true);
    });
  });

  it("shows management actions only with the matching permissions", async () => {
    const fetchImpl = routeFetch({ "GET /api/v1/schemas": () => jsonResponse(schemaPage([makeSchema()])) });
    renderRoute("/admin/schemas", fetchImpl, reader);

    await screen.findByRole("link", { name: "Sales DB" });
    expect(screen.queryByRole("button", { name: "Register schema" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit Sales DB" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete Sales DB" })).not.toBeInTheDocument();
  });

  it("registers a schema and opens it", async () => {
    const user = userEvent.setup();
    const created = makeSchema({ table_count: 0, column_count: 0, relationship_count: 0, schema_version: 1 });
    const fetchImpl = routeFetch({
      "GET /api/v1/schemas": () => jsonResponse(schemaPage([])),
      "POST /api/v1/schemas": () => jsonResponse(created, 201),
      [`GET /api/v1/schemas/${SCHEMA_ID}`]: () => jsonResponse(created),
      [`GET /api/v1/schemas/${SCHEMA_ID}/summary`]: () => jsonResponse(makeSummary()),
    });
    const { router } = renderRoute("/admin/schemas", fetchImpl, admin);

    expect(await screen.findByText("No schemas registered yet")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Register schema" }));
    const dialog = screen.getByRole("dialog", { name: "Register schema" });
    await user.type(within(dialog).getByLabelText("Name"), "Sales DB");
    await user.click(within(dialog).getByRole("button", { name: "Register schema" }));

    await waitFor(() => expect(router.state.location.pathname).toBe(`/admin/schemas/${SCHEMA_ID}`));
    const post = fetchImpl.mock.calls.find(([, init]) => init?.method === "POST")!;
    expect(JSON.parse(String(post[1]?.body))).toMatchObject({ name: "Sales DB", database_type: "postgresql" });
  });

  it("shows a loading state", async () => {
    renderRoute("/admin/schemas", neverResolvingFetch, admin);
    expect(await screen.findByRole("status", { name: "Loading schemas" })).toBeInTheDocument();
  });

  it("shows the server's error with a retry", async () => {
    const fetchImpl = routeFetch({ "GET /api/v1/schemas": () => errorResponse(500, "INTERNAL_ERROR", "Something broke.") });
    renderRoute("/admin/schemas", fetchImpl, admin);
    expect(await screen.findByText("Something broke.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /retry|try again/i })).toBeInTheDocument();
  });

  it("is not reachable without schema.read", async () => {
    const fetchImpl = routeFetch({});
    renderRoute("/admin/schemas", fetchImpl, makeCurrentUser({ permissions: ["users:read"] }));
    expect(await screen.findByText("Your account does not have access to this page.")).toBeInTheDocument();
  });
});

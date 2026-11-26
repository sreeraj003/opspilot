import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { CONNECTOR_ID, CONNECTOR_SCHEMA_ID, makeConnector, makeEnvironment, makeHealth } from "@/test/connectorFixtures";
import { makeSchema, schemaPage } from "@/test/schemaFixtures";
import { errorResponse, jsonResponse, makeAdmin, makeCurrentUser, neverResolvingFetch, renderRoute, routeFetch } from "@/test/utils";

const admin = makeAdmin();
const reader = makeCurrentUser({ permissions: ["connector.read"] });

function body(fetchImpl: ReturnType<typeof routeFetch>, method: string): Record<string, unknown> {
  const call = fetchImpl.mock.calls.find(([, init]) => init?.method === method)!;
  return JSON.parse(String(call[1]?.body));
}

describe("ConnectorsPage", () => {
  it("lists connectors with schema, host, authentication and test status", async () => {
    const fetchImpl = routeFetch({
      "GET /api/v1/connectors": () =>
        jsonResponse(schemaPage([makeConnector({ is_active: true, last_test_status: "passed", last_tested_at: "2026-11-21T09:00:00Z" })])),
    });
    renderRoute("/admin/connectors", fetchImpl, admin);

    const row = (await screen.findByRole("link", { name: "Company SQL API" })).closest("tr")!;
    expect(within(row).getByText("Sales DB")).toBeInTheDocument();
    expect(within(row).getByText("company-backend.example.com")).toBeInTheDocument();
    expect(within(row).getByText("API key")).toBeInTheDocument();
    expect(within(row).getByText("Active")).toBeInTheDocument();
    expect(within(row).getByText("Test passed")).toBeInTheDocument();
    // Only the masked host is shown; no credential or header value appears.
    expect(screen.queryByText(/acme/)).not.toBeInTheDocument();
  });

  it("searches, filters and paginates through the API", async () => {
    const user = userEvent.setup();
    const fetchImpl = routeFetch({ "GET /api/v1/connectors": () => jsonResponse(schemaPage([makeConnector()], 45)) });
    renderRoute("/admin/connectors", fetchImpl, admin);
    await screen.findByRole("link", { name: "Company SQL API" });

    await user.type(screen.getByLabelText("Search connectors"), "company");
    await user.selectOptions(screen.getByLabelText("Filter by status"), "active");
    await user.selectOptions(screen.getByLabelText("Filter by authentication"), "bearer_token");
    await user.selectOptions(screen.getByLabelText("Filter by last test"), "failed");
    await waitFor(() => {
      const urls = fetchImpl.mock.calls.map(([url]) => String(url));
      expect(
        urls.some(
          (url) =>
            url.includes("search=company") &&
            url.includes("status=active") &&
            url.includes("authentication_type=bearer_token") &&
            url.includes("last_test_status=failed"),
        ),
      ).toBe(true);
    });
    await user.click(screen.getByRole("button", { name: /next/i }));
    await waitFor(() => expect(fetchImpl.mock.calls.some(([url]) => String(url).includes("offset=20"))).toBe(true));
  });

  it("shows the create action only with connector.create", async () => {
    const fetchImpl = routeFetch({ "GET /api/v1/connectors": () => jsonResponse(schemaPage([makeConnector()])) });
    renderRoute("/admin/connectors", fetchImpl, reader);
    await screen.findByRole("link", { name: "Company SQL API" });
    expect(screen.queryByRole("button", { name: "New connector" })).not.toBeInTheDocument();
  });

  it("shows loading, empty and error states", async () => {
    const { unmount } = renderRoute("/admin/connectors", neverResolvingFetch, admin);
    expect(await screen.findByRole("status", { name: "Loading connectors" })).toBeInTheDocument();
    unmount();

    const { unmount: unmountEmpty } = renderRoute(
      "/admin/connectors",
      routeFetch({ "GET /api/v1/connectors": () => jsonResponse(schemaPage([])) }),
      admin,
    );
    expect(await screen.findByText("No connectors yet")).toBeInTheDocument();
    unmountEmpty();

    renderRoute(
      "/admin/connectors",
      routeFetch({ "GET /api/v1/connectors": () => errorResponse(500, "INTERNAL_SERVER_ERROR", "Something broke.") }),
      admin,
    );
    expect(await screen.findByText("Something broke.")).toBeInTheDocument();
  });

  it("is not reachable without connector.read", async () => {
    renderRoute("/admin/connectors", routeFetch({}), makeCurrentUser({ permissions: ["schema.read"] }));
    expect(await screen.findByText("Your account does not have access to this page.")).toBeInTheDocument();
  });

  it("creates a connector with a masked API key and opens it", async () => {
    const user = userEvent.setup();
    const created = makeConnector();
    const fetchImpl = routeFetch({
      "GET /api/v1/connectors": () => jsonResponse(schemaPage([])),
      "GET /api/v1/connectors/environment": () => jsonResponse(makeEnvironment()),
      "GET /api/v1/schemas": () => jsonResponse(schemaPage([makeSchema()])),
      "POST /api/v1/connectors": () => jsonResponse(created, 201),
      [`GET /api/v1/connectors/${CONNECTOR_ID}`]: () => jsonResponse(created),
      [`GET /api/v1/connectors/${CONNECTOR_ID}/health`]: () => jsonResponse(makeHealth()),
      [`GET /api/v1/connectors/${CONNECTOR_ID}/audit`]: () => jsonResponse(schemaPage([])),
    });
    const { router } = renderRoute("/admin/connectors", fetchImpl, admin);

    await user.click(await screen.findByRole("button", { name: "New connector" }));
    const dialog = screen.getByRole("dialog", { name: "New connector" });
    expect(within(dialog).getByText("SQL travels in the URL")).toBeInTheDocument();
    expect(await within(dialog).findByText(/Approved hosts on this server: company-backend.example.com/)).toBeInTheDocument();

    await user.type(within(dialog).getByLabelText("Connector name"), "Company SQL API");
    await within(dialog).findByRole("option", { name: "Sales DB" });
    await user.selectOptions(within(dialog).getByLabelText("Associated schema"), CONNECTOR_SCHEMA_ID);
    await user.type(within(dialog).getByLabelText("Base URL"), "https://company-backend.example.com/");
    const key = within(dialog).getByLabelText("API key");
    expect(key).toHaveAttribute("type", "password");
    await user.type(key, "sk_live_secret");
    await user.click(within(dialog).getByRole("button", { name: "Create connector" }));

    await waitFor(() => expect(router.state.location.pathname).toBe(`/admin/connectors/${CONNECTOR_ID}`));
    expect(body(fetchImpl, "POST")).toMatchObject({
      name: "Company SQL API",
      schema_source_id: CONNECTOR_SCHEMA_ID,
      base_url: "https://company-backend.example.com",
      endpoint_path: "/api/sql/execute",
      sql_parameter_name: "sql",
      authentication_type: "api_key",
      auth_header_name: null,
      credential: "sk_live_secret",
      max_response_size_bytes: 5 * 1024 * 1024,
    });
  });

  it("validates the form before sending and configures bearer and custom header authentication", async () => {
    const user = userEvent.setup();
    const fetchImpl = routeFetch({
      "GET /api/v1/connectors": () => jsonResponse(schemaPage([])),
      "GET /api/v1/connectors/environment": () => jsonResponse(makeEnvironment({ encryption_configured: false })),
      "GET /api/v1/schemas": () => jsonResponse(schemaPage([makeSchema()])),
      "POST /api/v1/connectors": () => errorResponse(422, "CONNECTOR_DESTINATION_REJECTED", "This host is not on the server's list."),
    });
    renderRoute("/admin/connectors", fetchImpl, admin);
    await user.click(await screen.findByRole("button", { name: "New connector" }));
    const dialog = screen.getByRole("dialog", { name: "New connector" });
    expect(await within(dialog).findByText("Credential encryption is not configured")).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "Create connector" }));
    expect(await within(dialog).findByText("Enter a name.")).toBeInTheDocument();
    expect(within(dialog).getByText("Choose the schema this connector serves.")).toBeInTheDocument();
    expect(within(dialog).getByText("Enter the credential.")).toBeInTheDocument();
    expect(fetchImpl.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);

    await user.selectOptions(within(dialog).getByLabelText("Authentication type"), "custom_header");
    expect(within(dialog).getByLabelText("Header name")).toBeInTheDocument();
    await user.selectOptions(within(dialog).getByLabelText("Authentication type"), "bearer_token");
    expect(within(dialog).queryByLabelText("Header name")).not.toBeInTheDocument();
    expect(within(dialog).getByLabelText("Bearer token")).toHaveAttribute("type", "password");

    await user.type(within(dialog).getByLabelText("Connector name"), "API");
    await within(dialog).findByRole("option", { name: "Sales DB" });
    await user.selectOptions(within(dialog).getByLabelText("Associated schema"), CONNECTOR_SCHEMA_ID);
    await user.type(within(dialog).getByLabelText("Base URL"), "https://elsewhere.example.org");
    await user.type(within(dialog).getByLabelText("Bearer token"), "tok");
    await user.click(within(dialog).getByRole("button", { name: "Add header" }));
    await user.type(within(dialog).getByLabelText("Header 1 name"), "X-Signature");
    await user.click(within(dialog).getByLabelText("Secret"));
    expect(within(dialog).getByLabelText("Header 1 value")).toHaveAttribute("type", "password");
    await user.type(within(dialog).getByLabelText("Header 1 value"), "sig");
    await user.click(within(dialog).getByRole("button", { name: "Create connector" }));

    expect(await within(dialog).findByText("This host is not on the server's list.")).toBeInTheDocument();
    expect(body(fetchImpl, "POST")).toMatchObject({
      authentication_type: "bearer_token",
      credential: "tok",
      custom_headers: [{ name: "X-Signature", secret: true, value: "sig" }],
    });
  });
});

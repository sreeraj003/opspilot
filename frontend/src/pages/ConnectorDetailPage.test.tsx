import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import {
  CONNECTOR_ID,
  makeAuditEvent,
  makeConnector,
  makeEnvironment,
  makeHealth,
  makeTestResult,
} from "@/test/connectorFixtures";
import { makeSchema, schemaPage } from "@/test/schemaFixtures";
import { errorResponse, jsonResponse, makeAdmin, makeCurrentUser, renderRoute, routeFetch } from "@/test/utils";

const admin = makeAdmin();
const path = `/admin/connectors/${CONNECTOR_ID}`;

function routes(overrides: Record<string, () => Response> = {}) {
  return routeFetch({
    [`GET /api/v1/connectors/${CONNECTOR_ID}`]: () => jsonResponse(makeConnector()),
    [`GET /api/v1/connectors/${CONNECTOR_ID}/health`]: () => jsonResponse(makeHealth()),
    [`GET /api/v1/connectors/${CONNECTOR_ID}/audit`]: () => jsonResponse(schemaPage([makeAuditEvent()])),
    "GET /api/v1/connectors/environment": () => jsonResponse(makeEnvironment()),
    "GET /api/v1/schemas": () => jsonResponse(schemaPage([makeSchema()])),
    ...overrides,
  });
}

function sent(fetchImpl: ReturnType<typeof routeFetch>, method: string, suffix: string): Record<string, unknown> | null {
  const call = fetchImpl.mock.calls.find(([url, init]) => init?.method === method && String(url).split("?")[0]!.endsWith(suffix));
  return call ? (call[1]?.body ? JSON.parse(String(call[1].body)) : {}) : null;
}

describe("ConnectorDetailPage", () => {
  it("shows the masked configuration, status and activity", async () => {
    renderRoute(path, routes(), admin);

    expect(await screen.findByRole("heading", { name: "Company SQL API" })).toBeInTheDocument();
    const configuration = screen.getByRole("region", { name: "Configuration" });
    expect(within(configuration).getByText("company-backend.example.com")).toBeInTheDocument();
    expect(within(configuration).getByText("GET /api/sql/execute?sql=…")).toBeInTheDocument();
    expect(within(configuration).getByText("Stored, hidden")).toBeInTheDocument();
    expect(within(configuration).getByText("X-Signature: hidden")).toBeInTheDocument();
    expect(within(configuration).getByText("X-Tenant: acme")).toBeInTheDocument();
    const status = screen.getByRole("region", { name: "Connection status" });
    expect((await within(status).findAllByText("Inactive")).length).toBeGreaterThan(0);
    expect(await screen.findByText("Dana Operator")).toBeInTheDocument();
    expect(screen.getByText("SQL travels in the URL")).toBeInTheDocument();
  });

  it("runs the connection test and shows each step", async () => {
    const user = userEvent.setup();
    const fetchImpl = routes({ [`POST /api/v1/connectors/${CONNECTOR_ID}/test`]: () => jsonResponse(makeTestResult()) });
    renderRoute(path, fetchImpl, admin);

    await user.click(await screen.findByRole("button", { name: "Test connection" }));
    const result = await screen.findByLabelText("Connection test result");
    expect(within(result).getByText("Connection test passed")).toBeInTheDocument();
    expect(within(result).getByText("Connection test passed: the test query returned 1 row.")).toBeInTheDocument();
    expect(within(result).getByText("Authentication accepted")).toBeInTheDocument();
    // The test sends no SQL from the browser.
    expect(sent(fetchImpl, "POST", "/test")).toEqual({});
  });

  it("shows a failed test with the failing step and skipped steps", async () => {
    const user = userEvent.setup();
    renderRoute(
      path,
      routes({ [`POST /api/v1/connectors/${CONNECTOR_ID}/test`]: () => jsonResponse(makeTestResult("authentication")) }),
      admin,
    );
    await user.click(await screen.findByRole("button", { name: "Test connection" }));
    const result = await screen.findByLabelText("Connection test result");
    expect(within(result).getByText("Connection test failed")).toBeInTheDocument();
    expect(within(result).getByText("The company API rejected the connector's credentials.")).toBeInTheDocument();
    expect(within(result).getByText(": failed")).toBeInTheDocument();
    expect(within(result).getByText(": skipped")).toBeInTheDocument();
  });

  it("shows the last stored test error", async () => {
    renderRoute(
      path,
      routes({
        [`GET /api/v1/connectors/${CONNECTOR_ID}`]: () =>
          jsonResponse(
            makeConnector({
              last_test_status: "failed",
              last_error_category: "timeout",
              last_error_summary: "The company API did not respond in time.",
            }),
          ),
        [`GET /api/v1/connectors/${CONNECTOR_ID}/health`]: () => jsonResponse(makeHealth({ status: "failing" })),
      }),
      admin,
    );
    expect(await screen.findByText("Timed out")).toBeInTheDocument();
    expect(screen.getByText("The company API did not respond in time.")).toBeInTheDocument();
  });

  it("shows a server error when the test cannot run", async () => {
    const user = userEvent.setup();
    renderRoute(
      path,
      routes({ [`POST /api/v1/connectors/${CONNECTOR_ID}/test`]: () => errorResponse(403, "FORBIDDEN", "You do not have permission.") }),
      admin,
    );
    await user.click(await screen.findByRole("button", { name: "Test connection" }));
    expect(await screen.findByText("You do not have permission.")).toBeInTheDocument();
  });

  it("edits without prefilling secrets and keeps them when left empty", async () => {
    const user = userEvent.setup();
    const fetchImpl = routes({ [`PATCH /api/v1/connectors/${CONNECTOR_ID}`]: () => jsonResponse(makeConnector()) });
    renderRoute(path, fetchImpl, admin);

    await user.click(await screen.findByRole("button", { name: "Edit" }));
    const dialog = screen.getByRole("dialog", { name: "Edit connector" });
    const key = within(dialog).getByLabelText("API key");
    expect(key).toHaveValue("");
    expect(key).toHaveAttribute("type", "password");
    expect(within(dialog).getByText(/A credential is stored \(hidden\)/)).toBeInTheDocument();
    expect(within(dialog).getByLabelText("Header 2 value")).toHaveValue("");
    await user.clear(within(dialog).getByLabelText("Connector name"));
    await user.type(within(dialog).getByLabelText("Connector name"), "Renamed API");
    await user.click(within(dialog).getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(sent(fetchImpl, "PATCH", CONNECTOR_ID)).not.toBeNull());
    const patch = sent(fetchImpl, "PATCH", CONNECTOR_ID)!;
    expect(patch.name).toBe("Renamed API");
    expect(patch).not.toHaveProperty("credential");
    expect(patch.custom_headers).toEqual([
      { name: "X-Tenant", secret: false, value: "acme" },
      { name: "X-Signature", secret: true },
    ]);
  });

  it("replaces the credential when a new one is entered", async () => {
    const user = userEvent.setup();
    const fetchImpl = routes({ [`PATCH /api/v1/connectors/${CONNECTOR_ID}`]: () => jsonResponse(makeConnector()) });
    renderRoute(path, fetchImpl, admin);
    await user.click(await screen.findByRole("button", { name: "Edit" }));
    const dialog = screen.getByRole("dialog", { name: "Edit connector" });
    await user.type(within(dialog).getByLabelText("API key"), "rotated-key");
    await user.click(within(dialog).getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(sent(fetchImpl, "PATCH", CONNECTOR_ID)).toMatchObject({ credential: "rotated-key" }));
  });

  it("activates and deletes after confirmation", async () => {
    const user = userEvent.setup();
    const fetchImpl = routes({
      [`PATCH /api/v1/connectors/${CONNECTOR_ID}/status`]: () => jsonResponse(makeConnector({ is_active: true })),
      [`DELETE /api/v1/connectors/${CONNECTOR_ID}`]: () => new Response(null, { status: 204 }),
      "GET /api/v1/connectors": () => jsonResponse(schemaPage([])),
    });
    const { router } = renderRoute(path, fetchImpl, admin);

    await user.click(await screen.findByRole("button", { name: "Activate" }));
    await waitFor(() => expect(sent(fetchImpl, "PATCH", "/status")).toEqual({ is_active: true }));

    await user.click(screen.getByRole("button", { name: "Delete" }));
    const confirm = screen.getByRole("alertdialog", { name: "Delete connector?" });
    expect(within(confirm).getByText(/stored credentials are erased/)).toBeInTheDocument();
    await user.click(within(confirm).getByRole("button", { name: "Delete connector" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/admin/connectors"));
  });

  it("shows each action only with its permission", async () => {
    renderRoute(path, routes(), makeCurrentUser({ permissions: ["connector.read"] }));
    await screen.findByRole("heading", { name: "Company SQL API" });
    for (const name of ["Test connection", "Activate", "Edit", "Delete"]) {
      expect(screen.queryByRole("button", { name })).not.toBeInTheDocument();
    }
  });

  it("shows only the test action to a tester", async () => {
    renderRoute(path, routes(), makeCurrentUser({ permissions: ["connector.read", "connector.test"] }));
    expect(await screen.findByRole("button", { name: "Test connection" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
  });

  it("explains a missing connector", async () => {
    renderRoute(
      path,
      routes({ [`GET /api/v1/connectors/${CONNECTOR_ID}`]: () => errorResponse(404, "NOT_FOUND", "The connector was not found.") }),
      admin,
    );
    expect(await screen.findByText("This connector does not exist or was deleted.")).toBeInTheDocument();
  });
});

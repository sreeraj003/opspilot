import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { makeSchema, schemaPage, SCHEMA_ID } from "@/test/schemaFixtures";
import { makeSchemaFields, makeTerm, TERM_ID } from "@/test/understandingFixtures";
import { errorResponse, jsonResponse, makeAdmin, makeCurrentUser, neverResolvingFetch, renderRoute, routeFetch } from "@/test/utils";

const admin = makeAdmin();
const reader = makeCurrentUser({ permissions: ["business_term.read"] });

function body(fetchImpl: ReturnType<typeof routeFetch>, method: string): Record<string, unknown> {
  const call = fetchImpl.mock.calls.find(([, init]) => init?.method === method)!;
  return JSON.parse(String(call[1]?.body));
}

const base = {
  "GET /api/v1/schemas": () => jsonResponse(schemaPage([makeSchema()])),
  "GET /api/v1/business-terms/schema-fields": () => jsonResponse(makeSchemaFields()),
};

describe("BusinessTermsPage", () => {
  it("lists terms with synonyms, schema fields, aggregation and status", async () => {
    const fetchImpl = routeFetch({
      ...base,
      "GET /api/v1/business-terms": () =>
        jsonResponse(
          schemaPage([
            makeTerm(),
            makeTerm({
              id: "t2",
              name: "Order status",
              kind: "dimension",
              is_active: false,
              related_columns: ["sales.orders.status"],
              stale_references: ["sales.orders.status"],
              default_aggregation: null,
            }),
          ]),
        ),
    });
    renderRoute("/admin/business-terms", fetchImpl, admin);

    const row = (await screen.findByText("Revenue")).closest("tr")!;
    expect(within(row).getByText(/turnover/)).toBeInTheDocument();
    expect(within(row).getByText("sales.orders.total_amount")).toBeInTheDocument();
    expect(within(row).getByText("Sum")).toBeInTheDocument();
    expect(within(row).getByText("Active")).toBeInTheDocument();
    const stale = screen.getByText("Order status").closest("tr")!;
    expect(within(stale).getByText("Inactive")).toBeInTheDocument();
    expect(within(stale).getByText("1 field is no longer in the schema")).toBeInTheDocument();
  });

  it("searches and filters through the API", async () => {
    const user = userEvent.setup();
    const fetchImpl = routeFetch({
      ...base,
      "GET /api/v1/business-terms": () => jsonResponse(schemaPage([makeTerm()], 45)),
    });
    renderRoute("/admin/business-terms", fetchImpl, admin);
    await screen.findByText("Revenue");

    await user.type(screen.getByLabelText("Search terms"), "turn");
    await screen.findByRole("option", { name: "Sales DB" });
    await user.selectOptions(screen.getByLabelText("Filter by schema"), SCHEMA_ID);
    await user.selectOptions(screen.getByLabelText("Filter by kind"), "metric");
    await user.selectOptions(screen.getByLabelText("Filter by status"), "false");
    await waitFor(() => {
      const urls = fetchImpl.mock.calls.map(([url]) => String(url));
      expect(
        urls.some(
          (url) =>
            url.includes("search=turn") &&
            url.includes(`schema_source_id=${SCHEMA_ID}`) &&
            url.includes("kind=metric") &&
            url.includes("is_active=false"),
        ),
      ).toBe(true);
    });
    await user.click(screen.getByRole("button", { name: /next/i }));
    await waitFor(() => expect(fetchImpl.mock.calls.some(([url]) => String(url).includes("offset=20"))).toBe(true));
  });

  it("creates a term with synonyms, associated schema fields and aggregation guidance", async () => {
    const user = userEvent.setup();
    const fetchImpl = routeFetch({
      ...base,
      "GET /api/v1/business-terms": () => jsonResponse(schemaPage([])),
      "POST /api/v1/business-terms": () => jsonResponse(makeTerm(), 201),
    });
    renderRoute("/admin/business-terms", fetchImpl, admin);

    await user.click(await screen.findByRole("button", { name: "New term" }));
    const dialog = screen.getByRole("dialog", { name: "New business term" });
    // Nothing is sent until required fields and at least one schema field are present.
    await user.click(within(dialog).getByRole("button", { name: "Create term" }));
    expect(within(dialog).getByText("Enter the business name.")).toBeInTheDocument();
    expect(within(dialog).getByText(/Associate at least one table or column/)).toBeInTheDocument();
    expect(fetchImpl.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);

    await within(dialog).findByRole("option", { name: "Sales DB" });
    await user.selectOptions(within(dialog).getByLabelText(/^Schema/), SCHEMA_ID);
    await user.type(within(dialog).getByLabelText(/Business name/), "Revenue");
    await user.type(within(dialog).getByLabelText(/^Description/), "Order totals");
    await user.type(within(dialog).getByLabelText("Synonyms"), "turnover{Enter}");
    await within(dialog).findByRole("option", { name: "sales.orders" });
    await user.selectOptions(within(dialog).getByLabelText("Table"), "sales.orders");
    await user.click(within(dialog).getByRole("checkbox", { name: /total_amount/ }));
    await user.selectOptions(within(dialog).getByLabelText("Default aggregation"), "sum");
    await user.type(within(dialog).getByLabelText(/Aggregation guidance/), "Exclude cancelled orders");
    await user.click(within(dialog).getByRole("button", { name: "Create term" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(body(fetchImpl, "POST")).toEqual({
      schema_source_id: SCHEMA_ID,
      name: "Revenue",
      description: "Order totals",
      kind: "metric",
      synonyms: ["turnover"],
      related_tables: [],
      related_columns: ["sales.orders.total_amount"],
      default_aggregation: "sum",
      aggregation_guidance: "Exclude cancelled orders",
      is_active: true,
    });
  });

  it("shows the server's reasons when references are invalid", async () => {
    const user = userEvent.setup();
    const fetchImpl = routeFetch({
      ...base,
      "GET /api/v1/business-terms": () => jsonResponse(schemaPage([makeTerm()])),
      [`PATCH /api/v1/business-terms/${TERM_ID}`]: () =>
        jsonResponse(
          {
            success: false,
            error: {
              code: "BUSINESS_TERM_INVALID",
              message: "The term references fields that are not registered.",
              details: [{ message: "Column sales.orders.margin is not registered." }],
              request_id: "r",
            },
          },
          422,
        ),
    });
    renderRoute("/admin/business-terms", fetchImpl, admin);

    await user.click(await screen.findByRole("button", { name: "Edit Revenue" }));
    const dialog = screen.getByRole("dialog", { name: "Edit business term" });
    expect(within(dialog).getByLabelText(/^Schema/)).toBeDisabled();
    const description = within(dialog).getByLabelText(/^Description/);
    await user.clear(description);
    await user.type(description, "Net of discounts");
    await user.click(within(dialog).getByRole("button", { name: "Save changes" }));

    expect(await within(dialog).findByText("Column sales.orders.margin is not registered.")).toBeInTheDocument();
    const sent = body(fetchImpl, "PATCH");
    expect(sent.description).toBe("Net of discounts");
    expect(sent).not.toHaveProperty("schema_source_id");
  });

  it("activates, deactivates and deletes terms", async () => {
    const user = userEvent.setup();
    const fetchImpl = routeFetch({
      ...base,
      "GET /api/v1/business-terms": () => jsonResponse(schemaPage([makeTerm()])),
      [`PATCH /api/v1/business-terms/${TERM_ID}`]: () => jsonResponse(makeTerm({ is_active: false })),
      [`DELETE /api/v1/business-terms/${TERM_ID}`]: () => new Response(null, { status: 204 }),
    });
    renderRoute("/admin/business-terms", fetchImpl, admin);

    await user.click(await screen.findByRole("button", { name: "Deactivate Revenue" }));
    await waitFor(() => expect(body(fetchImpl, "PATCH")).toEqual({ is_active: false }));

    await user.click(screen.getByRole("button", { name: "Delete Revenue" }));
    const confirm = screen.getByRole("alertdialog", {
      name: "Delete business term?",
    });
    await user.click(within(confirm).getByRole("button", { name: "Delete term" }));
    await waitFor(() => expect(fetchImpl.mock.calls.some(([, init]) => init?.method === "DELETE")).toBe(true));
  });

  it("hides management controls without business_term.manage", async () => {
    const fetchImpl = routeFetch({
      "GET /api/v1/business-terms": () => jsonResponse(schemaPage([makeTerm()])),
    });
    renderRoute("/admin/business-terms", fetchImpl, reader);
    await screen.findByText("Revenue");
    expect(screen.queryByRole("button", { name: "New term" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Edit Revenue" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete Revenue" })).not.toBeInTheDocument();
    // Without schema.read the schema filter is not offered.
    expect(screen.queryByLabelText("Filter by schema")).not.toBeInTheDocument();
  });

  it("shows loading, empty and error states", async () => {
    const { unmount } = renderRoute("/admin/business-terms", neverResolvingFetch, admin);
    expect(await screen.findByRole("status", { name: "Loading business terms" })).toBeInTheDocument();
    unmount();

    const { unmount: unmountEmpty } = renderRoute(
      "/admin/business-terms",
      routeFetch({
        ...base,
        "GET /api/v1/business-terms": () => jsonResponse(schemaPage([])),
      }),
      admin,
    );
    expect(await screen.findByText("No business terms yet")).toBeInTheDocument();
    unmountEmpty();

    renderRoute(
      "/admin/business-terms",
      routeFetch({
        ...base,
        "GET /api/v1/business-terms": () => errorResponse(500, "INTERNAL_SERVER_ERROR", "Something broke."),
      }),
      admin,
    );
    expect(await screen.findByText("Something broke.")).toBeInTheDocument();
  });

  it("is not reachable without business_term.read", async () => {
    renderRoute("/admin/business-terms", routeFetch({}), makeCurrentUser({ permissions: ["schema.read"] }));
    expect(await screen.findByText("Your account does not have access to this page.")).toBeInTheDocument();
  });
});

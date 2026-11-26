import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import {
  CUSTOMER_EMAIL,
  CUSTOMERS_ID,
  makeChange,
  makeImportResult,
  makeOrdersDetail,
  makeOutline,
  makeRelationship,
  makeSchema,
  makeSummary,
  makeTable,
  ORDER_CUSTOMER,
  ORDERS_ID,
  SCHEMA_ID,
  schemaPage,
  validationErrorResponse,
} from "@/test/schemaFixtures";
import { errorResponse, jsonResponse, makeAdmin, makeCurrentUser, renderRoute, routeFetch } from "@/test/utils";

const admin = makeAdmin();
const reader = makeCurrentUser({ permissions: ["schema.read"] });
const base = `/api/v1/schemas/${SCHEMA_ID}`;

function handlers(extra: Parameters<typeof routeFetch>[0] = {}) {
  return routeFetch({
    [`GET ${base}`]: () => jsonResponse(makeSchema()),
    [`GET ${base}/summary`]: () => jsonResponse(makeSummary()),
    [`GET ${base}/tables`]: () =>
      jsonResponse(
        schemaPage([
          makeTable(),
          makeTable({ id: CUSTOMERS_ID, table_name: "customers", qualified_name: "sales.customers", description: null }),
        ]),
      ),
    [`GET /api/v1/tables/${ORDERS_ID}`]: () => jsonResponse(makeOrdersDetail()),
    [`GET ${base}/relationships`]: () => jsonResponse(schemaPage([makeRelationship()])),
    [`GET ${base}/outline`]: () => jsonResponse(makeOutline()),
    [`GET ${base}/history`]: () => jsonResponse(schemaPage([makeChange()])),
    ...extra,
  });
}

async function openTab(name: string) {
  const user = userEvent.setup();
  await user.click(await screen.findByRole("tab", { name }));
  return user;
}

describe("SchemaDetailPage", () => {
  it("shows the overview with real counts, checks and the glossary", async () => {
    const summary = makeSummary({
      issues: [
        { code: "no_primary_key", message: "sales.events has no primary key.", location: ["tables", "sales.events"], severity: "warning" },
      ],
    });
    renderRoute(`/admin/schemas/${SCHEMA_ID}`, handlers({ [`GET ${base}/summary`]: () => jsonResponse(summary) }), admin);

    expect(await screen.findByRole("heading", { name: "Sales DB" })).toBeInTheDocument();
    expect(await screen.findByText("2 active")).toBeInTheDocument();
    expect(screen.getByText("sales.events has no primary key.")).toBeInTheDocument();
    expect(screen.getByText("GMV")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Import / Export" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Schema History" })).toBeInTheDocument();
  });

  it("hides editing and import/export from read-only users", async () => {
    renderRoute(`/admin/schemas/${SCHEMA_ID}`, handlers(), reader);
    await screen.findByRole("heading", { name: "Sales DB" });
    expect(screen.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete" })).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Import / Export" })).not.toBeInTheDocument();

    await openTab("Tables");
    expect(await screen.findByRole("button", { name: /sales\.orders/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add table" })).not.toBeInTheDocument();
  });

  it("explores a table's columns with key and sensitive indicators", async () => {
    renderRoute(`/admin/schemas/${SCHEMA_ID}`, handlers(), admin);
    const user = await openTab("Tables");
    expect(screen.getByText("Select a table")).toBeInTheDocument();

    await user.click(await screen.findByRole("button", { name: /sales\.orders/ }));
    const columns = await screen.findByRole("table", { name: "Columns of sales.orders" });
    const idRow = within(columns).getByText("id").closest("tr")!;
    expect(within(idRow).getByText("PK")).toBeInTheDocument();
    expect(within(idRow).getByText("not null")).toBeInTheDocument();
    expect(within(within(columns).getByText("customer_id").closest("tr")!).getByText("FK")).toBeInTheDocument();
    expect(within(within(columns).getByText("placed_at").closest("tr")!).getByText("Sensitive")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add column" })).toBeInTheDocument();
  });

  it("validates a column before saving it", async () => {
    renderRoute(`/admin/schemas/${SCHEMA_ID}`, handlers(), admin);
    const user = await openTab("Tables");
    await user.click(await screen.findByRole("button", { name: /sales\.orders/ }));
    await user.click(await screen.findByRole("button", { name: "Add column" }));
    const dialog = screen.getByRole("dialog", { name: "Add column" });
    await user.type(within(dialog).getByLabelText("Column name"), "1bad");
    await user.type(within(dialog).getByLabelText("Data type"), "whatever");
    await user.click(within(dialog).getByRole("button", { name: "Add column" }));
    expect(await within(dialog).findByText(/Use letters, digits and underscores/)).toBeInTheDocument();
    expect(within(dialog).getByText(/Use a supported data type/)).toBeInTheDocument();
  });

  it("asks before deleting a table that relationships use, then deletes them together", async () => {
    let calls = 0;
    const fetchImpl = handlers({
      [`DELETE /api/v1/tables/${ORDERS_ID}`]: () => {
        calls += 1;
        return calls === 1
          ? validationErrorResponse(409, "IN_USE", "This table is used by 1 relationship.", { relationship_ids: ["r"] })
          : new Response(null, { status: 204 });
      },
    });
    renderRoute(`/admin/schemas/${SCHEMA_ID}`, fetchImpl, admin);
    const user = await openTab("Tables");
    await user.click(await screen.findByRole("button", { name: /sales\.orders/ }));
    await user.click(await screen.findByRole("button", { name: "Delete table" }));
    const dialog = screen.getByRole("alertdialog", { name: "Delete table?" });
    await user.click(within(dialog).getByRole("button", { name: "Delete" }));
    expect(await within(dialog).findByText("This table is used by 1 relationship.")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Delete with relationships" }));

    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    const urls = fetchImpl.mock.calls.filter(([, init]) => init?.method === "DELETE").map(([url]) => String(url));
    expect(urls[0]).not.toContain("cascade_relationships=true");
    expect(urls[1]).toContain("cascade_relationships=true");
  });

  it("lists relationships and validates a new one before sending it", async () => {
    const fetchImpl = handlers();
    renderRoute(`/admin/schemas/${SCHEMA_ID}`, fetchImpl, admin);
    const user = await openTab("Relationships");
    const table = await screen.findByRole("table", { name: "Relationships between registered tables" });
    expect(within(table).getByText("customer_id")).toBeInTheDocument();
    expect(within(table).getByText("Many to one")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Add relationship" }));
    const dialog = screen.getByRole("dialog", { name: "Add relationship" });
    await user.click(within(dialog).getByRole("button", { name: "Add relationship" }));
    expect(within(dialog).getAllByText("Choose the source table.")).toHaveLength(1);
    expect(within(dialog).getByText("Choose the target table.")).toBeInTheDocument();

    // The outline loads when the dialog opens.
    await waitFor(() => expect(within(dialog).getAllByRole("option", { name: "sales.orders" }).length).toBeGreaterThan(0));
    await user.selectOptions(within(dialog).getByLabelText("Source table"), ORDERS_ID);
    await user.selectOptions(within(dialog).getByLabelText("Source column"), ORDER_CUSTOMER);
    await user.selectOptions(within(dialog).getByLabelText("Target table"), CUSTOMERS_ID);
    await user.selectOptions(within(dialog).getByLabelText("Target column"), CUSTOMER_EMAIL);
    expect(within(dialog).getByText("Types do not match: integer and varchar(255).")).toBeInTheDocument();

    await user.selectOptions(within(dialog).getByLabelText("Target column"), "55555555-5555-4555-8555-555555555551");
    expect(within(dialog).getByText("This relationship already exists.")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Add relationship" }));
    expect(fetchImpl.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  });

  it("previews an import, shows validation errors and blocks importing an invalid file", async () => {
    const invalid = makeImportResult({
      valid: false,
      errors: [
        {
          code: "invalid_data_type",
          message: "Unsupported data type 'whatever'.",
          location: ["document", "tables", "0", "columns", "1", "data_type"],
          severity: "error",
        },
      ],
    });
    const fetchImpl = handlers({ [`POST ${base}/import`]: () => jsonResponse(invalid) });
    renderRoute(`/admin/schemas/${SCHEMA_ID}`, fetchImpl, admin);
    const user = await openTab("Import / Export");

    const file = new File(
      [JSON.stringify({ schema_name: "sales", tables: [{ table_name: "invoices", columns: [{}, {}] }] })],
      "invoices.json",
      {
        type: "application/json",
      },
    );
    await user.upload(screen.getByLabelText("JSON file"), file);
    expect(await screen.findByText("1 table(s), 2 column(s), 0 relationship(s)")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Validate and preview" }));
    const preview = await screen.findByRole("region", { name: "Import preview" });
    expect(within(preview).getByText("The file cannot be imported")).toBeInTheDocument();
    expect(within(preview).getByText("Unsupported data type 'whatever'.")).toBeInTheDocument();
    expect(within(preview).getByText("tables[0] › columns[1] › data_type")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Import" })).toBeDisabled();

    const body = JSON.parse(String(fetchImpl.mock.calls.find(([, init]) => init?.method === "POST")![1]?.body));
    expect(body).toMatchObject({ strategy: "merge", dry_run: true });
  });

  it("imports a valid file after confirmation and reports the result", async () => {
    const fetchImpl = handlers({
      [`POST ${base}/import`]: (init) => {
        const body = JSON.parse(String(init?.body));
        return jsonResponse(
          body.dry_run
            ? makeImportResult({ strategy: "replace" })
            : makeImportResult({ strategy: "replace", dry_run: false, applied: true, schema_version_after: 4, version_incremented: true }),
        );
      },
    });
    renderRoute(`/admin/schemas/${SCHEMA_ID}`, fetchImpl, admin);
    const user = await openTab("Import / Export");
    await user.selectOptions(screen.getByLabelText("Import strategy"), "replace");
    await user.upload(screen.getByLabelText("JSON file"), new File(['{"tables": []}'], "empty.json", { type: "application/json" }));
    await screen.findByText("empty.json");
    await user.click(screen.getByRole("button", { name: "Validate and preview" }));
    expect(await screen.findByText("The file is valid")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Import" }));
    const confirm = screen.getByRole("alertdialog", { name: "Replace registered metadata?" });
    await user.click(within(confirm).getByRole("button", { name: "Replace" }));
    expect(await screen.findByText("Imported. The schema is now at version 4.")).toBeInTheDocument();

    const posts = fetchImpl.mock.calls.filter(([, init]) => init?.method === "POST").map(([, init]) => JSON.parse(String(init?.body)));
    expect(posts[1]).toMatchObject({ strategy: "replace", dry_run: false, confirm_replace: true, expected_schema_version: 3 });
  });

  it("rejects a file that is not JSON without calling the API", async () => {
    const fetchImpl = handlers();
    renderRoute(`/admin/schemas/${SCHEMA_ID}`, fetchImpl, admin);
    await openTab("Import / Export");
    const input = screen.getByLabelText("JSON file");
    fireEvent.change(input, { target: { files: [new File(["{not json"], "broken.json", { type: "application/json" })] } });
    expect(await screen.findByText("The file is not valid JSON.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Validate and preview" })).toBeDisabled();
  });

  it("shows the change history", async () => {
    renderRoute(`/admin/schemas/${SCHEMA_ID}`, handlers(), admin);
    await openTab("Schema History");
    const row = (await screen.findByText("Added table sales.orders with 3 columns")).closest("tr")!;
    expect(within(row).getByText("Installation Admin")).toBeInTheDocument();
    expect(within(row).getByText("v2")).toBeInTheDocument();
  });

  it("explains a missing schema", async () => {
    renderRoute(`/admin/schemas/${SCHEMA_ID}`, handlers({ [`GET ${base}`]: () => errorResponse(404, "NOT_FOUND", "Not found") }), admin);
    expect(await screen.findByText("This schema does not exist or was deleted.")).toBeInTheDocument();
  });
});

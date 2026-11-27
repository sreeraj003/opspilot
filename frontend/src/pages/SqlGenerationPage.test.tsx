import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { SCHEMA_ID } from "@/test/schemaFixtures";
import { GENERATION_ID, REGENERATION_ID, makeGeneration, makeUnderstanding } from "@/test/sqlGenerationFixtures";
import { INTENT_ID, makeOptions } from "@/test/understandingFixtures";
import { errorResponse, jsonResponse, makeCurrentUser, neverResolvingFetch, renderRoute, routeFetch } from "@/test/utils";

const generator = makeCurrentUser({ permissions: ["ai.question.understand", "ai.sql.generate"] });

function posted(fetchImpl: ReturnType<typeof routeFetch>, suffix: string): Record<string, unknown>[] {
  return fetchImpl.mock.calls
    .filter(([input, init]) => init?.method === "POST" && String(input).endsWith(suffix))
    .map(([, init]) => JSON.parse(String(init?.body)));
}

async function ask(question = "Top five products by revenue last month") {
  const user = userEvent.setup();
  await user.type(await screen.findByLabelText(/Business question/), question);
  await user.click(screen.getByRole("button", { name: "Generate SQL" }));
  return user;
}

function api(overrides: Record<string, () => Response | Promise<Response>> = {}) {
  return routeFetch({
    "GET /api/v1/ai/understand/options": () => jsonResponse(makeOptions()),
    "POST /api/v1/ai/understand": () => jsonResponse(makeUnderstanding()),
    "POST /api/v1/ai/sql/generate": () => jsonResponse(makeGeneration()),
    ...overrides,
  });
}

describe("SqlGenerationPage", () => {
  it("interprets the question, requests SQL by intent id and renders the result", async () => {
    const fetchImpl = api();
    renderRoute("/admin/sql-generation", fetchImpl, generator);
    await ask();

    const result = await screen.findByRole("region", { name: "SQL generation result" });
    expect(within(result).getByText("Generated — Awaiting Security Validation")).toBeInTheDocument();
    expect(within(result).getByText("PostgreSQL")).toBeInTheDocument();
    expect(within(result).getByText("Preliminary checks passed")).toBeInTheDocument();
    const code = within(result).getByLabelText("Generated SQL");
    expect(code.textContent).toContain("SUM(o.total_amount) AS revenue");
    // Keywords are highlighted, the text stays exactly as returned.
    expect(within(code).getAllByText("SELECT")[0]).toHaveClass("font-semibold");
    expect(code.textContent).toBe(makeGeneration().sql);
    expect(within(within(result).getByRole("list", { name: "Referenced tables" })).getByText("sales.products")).toBeInTheDocument();
    expect(within(within(result).getByRole("list", { name: "Referenced columns" })).getByText("sales.orders.total_amount")).toBeInTheDocument();
    const joins = within(result).getByRole("list", { name: "Joins" });
    expect(within(joins).getByText("INNER sales.orders.product_id = sales.products.id")).toBeInTheDocument();
    expect(within(joins).getByText("Registered relationship")).toBeInTheDocument();
    expect(within(within(result).getByRole("list", { name: "Assumptions" })).getByText(/paid order totals/)).toBeInTheDocument();

    const summary = screen.getByRole("region", { name: "Question summary" });
    expect(within(summary).getByText("Top five products by revenue last month")).toBeInTheDocument();
    expect(within(summary).getByText("Ranking")).toBeInTheDocument();
    expect(within(summary).getByText("Sales DB (version 3)")).toBeInTheDocument();

    // No execute action exists, and nothing claims the query ran or was approved.
    expect(screen.queryByRole("button", { name: /execute|run/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/executed successfully|approved for execution|query results/i)).not.toBeInTheDocument();

    expect(posted(fetchImpl, "/api/v1/ai/understand")).toEqual([
      { question: "Top five products by revenue last month", schema_source_id: SCHEMA_ID },
    ]);
    expect(posted(fetchImpl, "/api/v1/ai/sql/generate")).toEqual([{ intent_id: INTENT_ID, schema_source_id: SCHEMA_ID }]);
  });

  it("copies SQL only when the user may view it", async () => {
    const { unmount } = renderRoute("/admin/sql-generation", api(), generator);
    const user = await ask();
    // user-event installs its own clipboard stub during setup; spy on it.
    const writeText = vi.spyOn(navigator.clipboard, "writeText");
    await user.click(await screen.findByRole("button", { name: "Copy SQL" }));
    expect(writeText).toHaveBeenCalledWith(makeGeneration().sql);
    expect(await screen.findByRole("button", { name: "Copied" })).toBeInTheDocument();
    unmount();

    renderRoute(
      "/admin/sql-generation",
      api({
        "POST /api/v1/ai/sql/generate": () =>
          jsonResponse(makeGeneration({ sql: null, sql_visible: false, permission_notes: ["Your data permissions do not allow viewing SQL text for these tables."] })),
      }),
      generator,
    );
    await ask();
    expect(await screen.findByText("SQL text hidden")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Copy SQL" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Generated SQL")).not.toBeInTheDocument();
    expect(screen.getByText("Generated — Awaiting Security Validation")).toBeInTheDocument();
  });

  it("shows a clarification response without SQL", async () => {
    renderRoute(
      "/admin/sql-generation",
      api({
        "POST /api/v1/ai/sql/generate": () =>
          jsonResponse(
            makeGeneration({
              status: "clarification_required",
              sql: null,
              sql_visible: false,
              clarification_required: true,
              clarification_question: "Should revenue include tax?",
              referenced_tables: [],
              referenced_columns: [],
              joins_used: [],
              validation_status: "not_run",
            }),
          ),
      }),
      generator,
    );
    await ask();
    const result = await screen.findByRole("region", { name: "SQL generation result" });
    expect(within(result).getAllByText("Clarification required").length).toBeGreaterThan(0);
    expect(within(result).getByText("Should revenue include tax?")).toBeInTheDocument();
    expect(within(result).queryByLabelText("Generated SQL")).not.toBeInTheDocument();
  });

  it("does not generate SQL when the question needs clarification", async () => {
    const fetchImpl = api({
      "POST /api/v1/ai/understand": () =>
        jsonResponse(
          makeUnderstanding({
            status: "needs_clarification",
            clarification: { requires_clarification: true, reason: "unclear_metric", missing_information: ["metric"], clarification_question: "Which measure do you mean?", key: "unclear_metric" },
          }),
        ),
    });
    renderRoute("/admin/sql-generation", fetchImpl, generator);
    await ask("How are we doing?");
    expect(await screen.findByText("Which measure do you mean?")).toBeInTheDocument();
    expect(posted(fetchImpl, "/api/v1/ai/sql/generate")).toEqual([]);
  });

  it("renders failures with validation issues", async () => {
    renderRoute(
      "/admin/sql-generation",
      api({
        "POST /api/v1/ai/sql/generate": () =>
          jsonResponse(
            makeGeneration({
              status: "generation_failed",
              sql: null,
              sql_visible: false,
              failure_category: "unsafe_sql",
              validation_status: "failed",
              explanation: "The generated query was not a single read-only SELECT statement, so it was discarded.",
              validation_issues: [{ code: "statement_not_allowed", message: "Only SELECT queries are allowed, not DELETE.", blocking: true }],
            }),
          ),
      }),
      generator,
    );
    await ask();
    expect(await screen.findByText("The query was not a single read-only SELECT and was discarded")).toBeInTheDocument();
    const issues = screen.getByRole("list", { name: "Validation issues" });
    expect(within(issues).getByText("statement_not_allowed")).toBeInTheDocument();
    expect(screen.getByText("Preliminary checks failed")).toBeInTheDocument();
  });

  it("regenerates SQL for the same generation and disables the action when none are left", async () => {
    const fetchImpl = api({
      [`POST /api/v1/ai/sql/generations/${GENERATION_ID}/regenerate`]: () =>
        jsonResponse(
          makeGeneration({
            generation_id: REGENERATION_ID,
            regenerated_from_id: GENERATION_ID,
            regenerations_remaining: 0,
            sql: "SELECT\n  SUM(o.total_amount) AS revenue\nFROM sales.orders AS o",
          }),
        ),
    });
    renderRoute("/admin/sql-generation", fetchImpl, generator);
    const user = await ask();
    await user.click(await screen.findByRole("button", { name: "Regenerate SQL" }));

    await waitFor(() => expect(screen.getByLabelText("Generated SQL").textContent).toContain("FROM sales.orders AS o"));
    expect(screen.getByText("0 regenerations left")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Regenerate SQL" })).toBeDisabled();
    expect(posted(fetchImpl, "/regenerate")).toEqual([{}]);
  });

  it("shows generation details from the stored record", async () => {
    const fetchImpl = api({
      [`GET /api/v1/ai/sql/generations/${GENERATION_ID}`]: () => jsonResponse(makeGeneration({ generation_attempts: 2 })),
    });
    renderRoute("/admin/sql-generation", fetchImpl, generator);
    const user = await ask();
    await user.click(await screen.findByRole("button", { name: "View generation details" }));
    const dialog = await screen.findByRole("dialog", { name: "Generation details" });
    expect(within(dialog).getByText("sql-generation-v1")).toBeInTheDocument();
    expect(within(dialog).getByText("Awaiting security validation (not approved, not executed)")).toBeInTheDocument();
    expect(within(dialog).getByText(GENERATION_ID)).toBeInTheDocument();
  });

  it("shows loading and error states", async () => {
    const { unmount } = renderRoute(
      "/admin/sql-generation",
      api({ "POST /api/v1/ai/sql/generate": () => new Promise<Response>(() => {}) }),
      generator,
    );
    await ask();
    expect(await screen.findByRole("button", { name: "Generating SQL…" })).toBeDisabled();
    unmount();

    const second = renderRoute(
      "/admin/sql-generation",
      api({ "POST /api/v1/ai/sql/generate": () => errorResponse(504, "AI_PROVIDER_TIMEOUT", "The AI provider did not answer in time.") }),
      generator,
    );
    await ask();
    expect(await screen.findByText("The AI provider did not answer in time.")).toBeInTheDocument();
    second.unmount();

    renderRoute("/admin/sql-generation", neverResolvingFetch, generator);
    expect(await screen.findByRole("heading", { name: "SQL generation" })).toBeInTheDocument();
  });

  it("validates input and is not reachable without ai.sql.generate", async () => {
    const fetchImpl = api();
    const { unmount } = renderRoute("/admin/sql-generation", fetchImpl, generator);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Generate SQL" }));
    expect(await screen.findByText("Enter a business question.")).toBeInTheDocument();
    expect(posted(fetchImpl, "/api/v1/ai/understand")).toEqual([]);
    unmount();

    renderRoute("/admin/sql-generation", routeFetch({}), makeCurrentUser({ permissions: ["ai.question.understand"] }));
    expect(await screen.findByText("Your account does not have access to this page.")).toBeInTheDocument();
  });
});

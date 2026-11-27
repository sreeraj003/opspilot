import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { APPROVAL_ID, APPROVED_SQL, makeApproval, makeRejection, makeValidation } from "@/test/sqlSecurityFixtures";
import { GENERATION_ID, makeGeneration, makeUnderstanding } from "@/test/sqlGenerationFixtures";
import { makeOptions } from "@/test/understandingFixtures";
import { errorResponse, jsonResponse, makeCurrentUser, renderRoute, routeFetch } from "@/test/utils";

const validator = makeCurrentUser({ permissions: ["ai.question.understand", "ai.sql.generate", "ai.sql.validate"] });
const generatorOnly = makeCurrentUser({ permissions: ["ai.question.understand", "ai.sql.generate"] });

const VALIDATE = `POST /api/v1/ai/sql/generations/${GENERATION_ID}/validate`;
const APPROVAL = `GET /api/v1/ai/sql/approvals/${APPROVAL_ID}`;
const REVOKE = `POST /api/v1/ai/sql/approvals/${APPROVAL_ID}/revoke`;

function api(overrides: Record<string, () => Response | Promise<Response>> = {}) {
  return routeFetch({
    "GET /api/v1/ai/understand/options": () => jsonResponse(makeOptions()),
    "POST /api/v1/ai/understand": () => jsonResponse(makeUnderstanding()),
    "POST /api/v1/ai/sql/generate": () => jsonResponse(makeGeneration()),
    [APPROVAL]: () => jsonResponse(makeApproval()),
    ...overrides,
  });
}

function calls(fetchImpl: ReturnType<typeof routeFetch>, method: string, suffix: string) {
  return fetchImpl.mock.calls.filter(([input, init]) => (init?.method ?? "GET").toUpperCase() === method && String(input).endsWith(suffix));
}

async function generate() {
  const user = userEvent.setup();
  await user.type(await screen.findByLabelText(/Business question/), "Top five products by revenue last month");
  await user.click(screen.getByRole("button", { name: "Generate SQL" }));
  return { user, panel: await screen.findByRole("region", { name: "Security validation" }) };
}

describe("SQL security validation panel", () => {
  it("starts as awaiting validation and approves only from the server's answer", async () => {
    const fetchImpl = api({ [VALIDATE]: () => jsonResponse(makeValidation()) });
    renderRoute("/admin/sql-generation", fetchImpl, validator);
    const { user, panel } = await generate();

    expect(within(panel).getByText("Awaiting validation")).toBeInTheDocument();
    expect(within(panel).queryByText("Approved")).not.toBeInTheDocument();
    await user.click(within(panel).getByRole("button", { name: "Validate SQL" }));

    expect(await within(panel).findByText("Approved")).toBeInTheDocument();
    expect(within(panel).getByText("Schema version 3")).toBeInTheDocument();
    expect(within(panel).getByText(/approved for one execution/)).toBeInTheDocument();
    expect(within(within(panel).getByRole("list", { name: "Row restrictions" })).getByText("sales.orders")).toBeInTheDocument();
    expect(within(panel).getByLabelText("Approved SQL").textContent).toBe(APPROVED_SQL);
    const details = within(panel).getByLabelText("Approval details");
    expect(within(details).getByText("e".repeat(64))).toBeInTheDocument();

    // The client sends no SQL, and there is no way to execute.
    const [, init] = calls(fetchImpl, "POST", `/generations/${GENERATION_ID}/validate`)[0]!;
    expect(JSON.parse(String(init?.body))).toEqual({});
    expect(screen.queryByRole("button", { name: /execute|run query|run sql/i })).not.toBeInTheDocument();
    expect(within(panel).getByText(/Running queries is not available yet/)).toBeInTheDocument();
  });

  it("shows rejections with safe errors and no approval", async () => {
    renderRoute("/admin/sql-generation", api({ [VALIDATE]: () => jsonResponse(makeRejection()) }), validator);
    const { user, panel } = await generate();
    await user.click(within(panel).getByRole("button", { name: "Validate SQL" }));

    expect(await within(panel).findByText("Rejected")).toBeInTheDocument();
    expect(within(within(panel).getByRole("list", { name: "Security errors" })).getByText(/single read-only SELECT/)).toBeInTheDocument();
    expect(within(panel).queryByLabelText("Approval details")).not.toBeInTheDocument();
    expect(within(panel).queryByRole("button", { name: "Revoke approval" })).not.toBeInTheDocument();
    expect(within(panel).getByRole("button", { name: "Validate again" })).toBeInTheDocument();
  });

  it("shows warnings and validation failures", async () => {
    const failed = makeRejection({ status: "validation_failed", summary: "The schema changed since the SQL was generated." });
    renderRoute("/admin/sql-generation", api({ [VALIDATE]: () => jsonResponse(failed) }), validator);
    const { user, panel } = await generate();
    await user.click(within(panel).getByRole("button", { name: "Validate SQL" }));
    expect(await within(panel).findByText("Validation failed")).toBeInTheDocument();
    expect(within(panel).getByText(/schema changed/)).toBeInTheDocument();
  });

  it("shows warnings from an approval", async () => {
    const warned = makeValidation({ warnings: ["No row limit was given, so at most 1000 rows will be returned."] });
    renderRoute("/admin/sql-generation", api({ [VALIDATE]: () => jsonResponse(warned) }), validator);
    const { user, panel } = await generate();
    await user.click(within(panel).getByRole("button", { name: "Validate SQL" }));
    expect(await within(within(panel).getByRole("list", { name: "Security warnings" }).parentElement!).findByText(/at most 1000 rows/)).toBeInTheDocument();
  });

  it("shows an expired approval as the server reports it", async () => {
    const expired = makeApproval({ status: "expired", is_usable: false, expires_in_seconds: 0 });
    renderRoute(
      "/admin/sql-generation",
      api({ [VALIDATE]: () => jsonResponse(makeValidation({ approval: expired })), [APPROVAL]: () => jsonResponse(expired) }),
      validator,
    );
    const { user, panel } = await generate();
    await user.click(within(panel).getByRole("button", { name: "Validate SQL" }));
    expect(await within(panel).findByText("Approval expired")).toBeInTheDocument();
    expect(within(panel).queryByRole("button", { name: "Revoke approval" })).not.toBeInTheDocument();
  });

  it("revokes an approval through the server", async () => {
    const fetchImpl = api({
      [VALIDATE]: () => jsonResponse(makeValidation()),
      [REVOKE]: () => jsonResponse(makeApproval({ status: "revoked", is_usable: false, revoked_at: "2026-11-27T10:01:00Z" })),
    });
    renderRoute("/admin/sql-generation", fetchImpl, validator);
    const { user, panel } = await generate();
    await user.click(within(panel).getByRole("button", { name: "Validate SQL" }));
    await user.click(await within(panel).findByRole("button", { name: "Revoke approval" }));

    expect(await within(panel).findByText("Revoked")).toBeInTheDocument();
    expect(within(panel).queryByRole("button", { name: "Revoke approval" })).not.toBeInTheDocument();
    expect(calls(fetchImpl, "POST", `/approvals/${APPROVAL_ID}/revoke`)).toHaveLength(1);
  });

  it("hides the approved SQL when the user may not view SQL", async () => {
    const hidden = makeValidation({ sql_visible: false, approved_sql: null });
    renderRoute("/admin/sql-generation", api({ [VALIDATE]: () => jsonResponse(hidden) }), validator);
    const { user, panel } = await generate();
    await user.click(within(panel).getByRole("button", { name: "Validate SQL" }));
    expect(await within(panel).findByText("Approved")).toBeInTheDocument();
    expect(within(panel).queryByLabelText("Approved SQL")).not.toBeInTheDocument();
  });

  it("shows internal reasons only when the server includes them", async () => {
    const audited = makeRejection({ internal_reasons: [{ code: "forbidden_node", category: "statement", reason: "Delete node" }] });
    renderRoute("/admin/sql-generation", api({ [VALIDATE]: () => jsonResponse(audited) }), validator);
    const { user, panel } = await generate();
    await user.click(within(panel).getByRole("button", { name: "Validate SQL" }));
    expect(await within(panel).findByRole("list", { name: "Internal reasons" })).toBeInTheDocument();
  });

  it("shows server errors", async () => {
    renderRoute(
      "/admin/sql-generation",
      api({ [VALIDATE]: () => errorResponse(429, "RATE_LIMITED", "Too many validation requests. Try again shortly.") }),
      validator,
    );
    const { user, panel } = await generate();
    await user.click(within(panel).getByRole("button", { name: "Validate SQL" }));
    await waitFor(() => expect(within(panel).getByRole("alert")).toHaveTextContent(/Too many attempts/));
    expect(within(panel).getByText("Awaiting validation")).toBeInTheDocument();
  });

  it("offers no validate action without ai.sql.validate", async () => {
    const fetchImpl = api();
    renderRoute("/admin/sql-generation", fetchImpl, generatorOnly);
    const { panel } = await generate();
    expect(within(panel).queryByRole("button", { name: "Validate SQL" })).not.toBeInTheDocument();
    expect(within(panel).getByText(/do not have permission to run security validation/)).toBeInTheDocument();
    expect(calls(fetchImpl, "POST", "/validate")).toHaveLength(0);
  });
});

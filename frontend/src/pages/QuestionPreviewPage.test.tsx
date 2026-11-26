import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { SCHEMA_ID } from "@/test/schemaFixtures";
import { clarificationPreview, makeOptions, makePreview } from "@/test/understandingFixtures";
import { errorResponse, jsonResponse, makeCurrentUser, neverResolvingFetch, renderRoute, routeFetch } from "@/test/utils";

const previewer = makeCurrentUser({ permissions: ["ai.question.preview"] });
const settingsAdmin = makeCurrentUser({
  permissions: ["ai.question.preview", "settings:manage"],
});

function bodies(fetchImpl: ReturnType<typeof routeFetch>, method: string): Record<string, unknown>[] {
  return fetchImpl.mock.calls.filter(([, init]) => init?.method === method).map(([, init]) => JSON.parse(String(init?.body)));
}

async function ask(question: string) {
  const user = userEvent.setup();
  await user.type(await screen.findByLabelText(/Business question/), question);
  await user.click(screen.getByRole("button", { name: "Interpret question" }));
  return user;
}

describe("QuestionPreviewPage", () => {
  it("submits a question and renders the structured intent", async () => {
    const fetchImpl = routeFetch({
      "GET /api/v1/ai/understand/options": () => jsonResponse(makeOptions()),
      "POST /api/v1/ai/understand/preview": () => jsonResponse(makePreview()),
    });
    renderRoute("/admin/question-preview", fetchImpl, previewer);
    await ask("Top five products by revenue last month");

    const interpretation = await screen.findByRole("region", {
      name: "Interpretation",
    });
    expect(within(interpretation).getByText("Understood")).toBeInTheDocument();
    expect(within(interpretation).getByText("Validation passed")).toBeInTheDocument();
    expect(within(interpretation).getByText("Permitted")).toBeInTheDocument();
    expect(within(interpretation).getByText("Row-level restrictions apply")).toBeInTheDocument();
    expect(within(interpretation).getByText("Confidence 90%")).toBeInTheDocument();

    const intent = screen.getByRole("region", { name: "Business intent" });
    expect(within(intent).getByText("Ranking")).toBeInTheDocument();
    const metrics = within(intent).getByRole("list", { name: "Metrics" });
    expect(within(metrics).getByText("revenue")).toBeInTheDocument();
    expect(within(metrics).getByText("Business term: Revenue")).toBeInTheDocument();
    expect(within(metrics).getByText("sales.orders.total_amount")).toBeInTheDocument();
    expect(within(within(intent).getByRole("list", { name: "Dimensions" })).getByText("sales.products.name")).toBeInTheDocument();
    expect(within(within(intent).getByRole("list", { name: "Filters" })).getByText("paid")).toBeInTheDocument();
    expect(within(intent).getByText("2026-10-01 to 2026-11-01 (end excluded)")).toBeInTheDocument();
    expect(within(intent).getByText("revenue descending")).toBeInTheDocument();
    expect(within(within(intent).getByRole("list", { name: "Assumptions" })).getByText(/registered aggregation/)).toBeInTheDocument();

    expect(bodies(fetchImpl, "POST")[0]).toEqual({
      question: "Top five products by revenue last month",
      schema_source_id: SCHEMA_ID,
      answered_clarifications: [],
    });
  });

  it("renders a clarification and never asks the same one twice", async () => {
    let call = 0;
    const fetchImpl = routeFetch({
      "GET /api/v1/ai/understand/options": () => jsonResponse(makeOptions()),
      "POST /api/v1/ai/understand/preview": () => jsonResponse(call++ === 0 ? clarificationPreview() : makePreview()),
    });
    renderRoute("/admin/question-preview", fetchImpl, previewer);
    const user = await ask("Show me sales");

    expect(await screen.findByText("Needs clarification")).toBeInTheDocument();
    expect(screen.getByText("'sales' can mean Gross sales or Net sales. Which one do you mean?")).toBeInTheDocument();
    expect(screen.getByText("Missing: Term meaning")).toBeInTheDocument();

    await user.click(
      screen.getByRole("button", {
        name: "Mark as answered and interpret again",
      }),
    );
    await waitFor(() => expect(bodies(fetchImpl, "POST")).toHaveLength(2));
    expect(bodies(fetchImpl, "POST")[1]!.answered_clarifications).toEqual(["ambiguous_term:sales"]);
    expect(await screen.findByText("Understood")).toBeInTheDocument();
  });

  it("shows validation issues and a safe access-denied reason", async () => {
    const fetchImpl = routeFetch({
      "GET /api/v1/ai/understand/options": () => jsonResponse(makeOptions()),
      "POST /api/v1/ai/understand/preview": () =>
        jsonResponse(
          makePreview({
            status: "access_denied",
            intent: null,
            confidence: null,
            validation: {
              status: "rejected",
              correction_attempts: 1,
              issues: [
                {
                  code: "restricted",
                  message: "A requested field is not available to you.",
                  location: [],
                },
              ],
            },
            permission: {
              status: "denied",
              export_allowed: false,
              row_level_restrictions: false,
              reason: "The question needs data you are not permitted to use.",
            },
            explanation: "The question cannot be interpreted with your data permissions.",
          }),
        ),
    });
    renderRoute("/admin/question-preview", fetchImpl, previewer);
    await ask("Show salaries");

    expect(await screen.findByText("Access denied")).toBeInTheDocument();
    expect(screen.getByText("Validation failed")).toBeInTheDocument();
    expect(screen.getByText("The question needs data you are not permitted to use.")).toBeInTheDocument();
    expect(within(screen.getByRole("list", { name: "Validation issues" })).getByText("restricted")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Business intent" })).not.toBeInTheDocument();
  });

  it("validates the question before sending and shows API errors", async () => {
    const fetchImpl = routeFetch({
      "GET /api/v1/ai/understand/options": () => jsonResponse(makeOptions()),
      "POST /api/v1/ai/understand/preview": () => errorResponse(504, "AI_PROVIDER_TIMEOUT", "The AI provider did not answer in time."),
    });
    renderRoute("/admin/question-preview", fetchImpl, previewer);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Interpret question" }));
    expect(screen.getByText("Enter a business question.")).toBeInTheDocument();
    expect(bodies(fetchImpl, "POST")).toHaveLength(0);

    await ask("Revenue last month");
    expect(await screen.findByText("The AI provider did not answer in time.")).toBeInTheDocument();
  });

  it("shows loading, empty and error states for options", async () => {
    const { unmount } = renderRoute("/admin/question-preview", neverResolvingFetch, previewer);
    expect(await screen.findByRole("heading", { name: "Question preview" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Interpret question" })).not.toBeInTheDocument();
    unmount();

    const { unmount: unmountEmpty } = renderRoute(
      "/admin/question-preview",
      routeFetch({
        "GET /api/v1/ai/understand/options": () => jsonResponse(makeOptions({ sources: [] })),
      }),
      previewer,
    );
    expect(await screen.findByText("No schemas to interpret against")).toBeInTheDocument();
    unmountEmpty();

    renderRoute(
      "/admin/question-preview",
      routeFetch({
        "GET /api/v1/ai/understand/options": () => errorResponse(500, "INTERNAL_SERVER_ERROR", "Something broke."),
      }),
      previewer,
    );
    expect(await screen.findByText("Something broke.")).toBeInTheDocument();
  });

  it("lets settings managers change the business timezone", async () => {
    const user = userEvent.setup();
    const fetchImpl = routeFetch({
      "GET /api/v1/ai/understand/options": () => jsonResponse(makeOptions()),
      "PUT /api/v1/settings/business.timezone": () => jsonResponse({ key: "business.timezone", value: "Asia/Kolkata" }),
    });
    renderRoute("/admin/question-preview", fetchImpl, settingsAdmin);
    const input = await screen.findByLabelText("IANA timezone");
    await user.clear(input);
    await user.type(input, "Asia/Kolkata");
    await user.click(screen.getByRole("button", { name: "Save timezone" }));
    await waitFor(() =>
      expect(bodies(fetchImpl, "PUT")[0]).toMatchObject({
        value: "Asia/Kolkata",
      }),
    );
  });

  it("shows the timezone read-only without settings:manage, and is not reachable without ai.question.preview", async () => {
    const { unmount } = renderRoute(
      "/admin/question-preview",
      routeFetch({
        "GET /api/v1/ai/understand/options": () => jsonResponse(makeOptions()),
      }),
      previewer,
    );
    expect(await screen.findByRole("region", { name: "Business timezone" })).toBeInTheDocument();
    expect(screen.queryByLabelText("IANA timezone")).not.toBeInTheDocument();
    unmount();

    renderRoute("/admin/question-preview", routeFetch({}), makeCurrentUser({ permissions: ["ai.question.understand"] }));
    expect(await screen.findByText("Your account does not have access to this page.")).toBeInTheDocument();
  });
});

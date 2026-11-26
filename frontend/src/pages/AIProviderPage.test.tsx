import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { makeAIHealth, makeAIProviderList, makeAITestResult, makeAIUsagePage, makeAIUsageRecord } from "@/test/aiFixtures";
import { errorResponse, jsonResponse, makeAdmin, makeCurrentUser, neverResolvingFetch, renderRoute, routeFetch } from "@/test/utils";

const admin = makeAdmin();
const path = "/admin/ai";

function routes(overrides: Record<string, () => Response> = {}) {
  return routeFetch({
    "GET /api/v1/ai/providers": () => jsonResponse(makeAIProviderList()),
    "GET /api/v1/ai/providers/health": () => jsonResponse(makeAIHealth()),
    "GET /api/v1/ai/usage": () => jsonResponse(makeAIUsagePage()),
    ...overrides,
  });
}

function called(fetchImpl: ReturnType<typeof routeFetch>, method: string, suffix: string) {
  return fetchImpl.mock.calls.filter(([url, init]) => (init?.method ?? "GET") === method && String(url).split("?")[0]!.endsWith(suffix));
}

describe("AIProviderPage", () => {
  it("shows the configured provider and model without any API key", async () => {
    const { container } = renderRoute(path, routes(), admin);

    expect(await screen.findByRole("heading", { name: "AI provider", level: 1 })).toBeInTheDocument();
    const configuration = await screen.findByRole("region", { name: "Configuration" });
    expect(within(configuration).getByText("meta/llama-3.1-70b-instruct")).toBeInTheDocument();
    expect(within(configuration).getByText("https://integrate.api.nvidia.com/v1")).toBeInTheDocument();
    expect(within(configuration).getByText("Set on server, hidden")).toBeInTheDocument();
    expect(within(configuration).getByText("NVIDIA_API_KEY")).toBeInTheDocument();
    expect(within(configuration).getByText("OpenAI-compatible: not configured")).toBeInTheDocument();
    expect(container.textContent).not.toMatch(/nvapi-/);
  });

  it("explains what to set when the provider is not configured", async () => {
    renderRoute(
      path,
      routes({
        "GET /api/v1/ai/providers": () =>
          jsonResponse(
            makeAIProviderList({
              configured: false,
              model: null,
              missing_settings: ["NVIDIA_API_KEY", "NVIDIA_MODEL"],
              credential: { configured: false, source: "environment", reference: "NVIDIA_API_KEY" },
            }),
          ),
        "GET /api/v1/ai/providers/health": () =>
          jsonResponse(makeAIHealth({ status: "not_configured", latency_ms: null, model_available: null, message: "Not configured." })),
      }),
      admin,
    );
    expect(await screen.findByText(/Set NVIDIA_API_KEY and NVIDIA_MODEL/)).toBeInTheDocument();
    expect(screen.getByText("Missing")).toBeInTheDocument();
    expect(await screen.findByText("Not configured")).toBeInTheDocument();
  });

  it("shows provider health and checks again on request", async () => {
    const user = userEvent.setup();
    const fetchImpl = routes();
    renderRoute(path, fetchImpl, admin);

    const health = await screen.findByRole("group", { name: "Provider health" });
    expect(within(health).getByText("Reachable")).toBeInTheDocument();
    expect(within(health).getByText("142 ms")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Check health" }));
    await waitFor(() => expect(called(fetchImpl, "GET", "/ai/providers/health")).toHaveLength(2));
  });

  it("shows an unhealthy provider with its problem", async () => {
    renderRoute(
      path,
      routes({
        "GET /api/v1/ai/providers/health": () =>
          jsonResponse(
            makeAIHealth({ status: "unhealthy", error_category: "authentication", message: "The AI provider rejected the configured credentials." }),
          ),
      }),
      admin,
    );
    const health = await screen.findByRole("group", { name: "Provider health" });
    expect(within(health).getByText("Unreachable")).toBeInTheDocument();
    expect(within(health).getByText("Authentication rejected")).toBeInTheDocument();
  });

  it("runs the fixed test without sending any prompt and shows latency and usage", async () => {
    const user = userEvent.setup();
    const fetchImpl = routes({ "POST /api/v1/ai/providers/test": () => jsonResponse(makeAITestResult()) });
    renderRoute(path, fetchImpl, admin);

    await user.click(await screen.findByRole("button", { name: "Run test" }));
    const result = await screen.findByRole("group", { name: "Provider test result" });
    expect(within(result).getByText("Test passed")).toBeInTheDocument();
    expect(within(result).getByText("812 ms")).toBeInTheDocument();
    expect(within(result).getByText("31 in, 2 out, 33 total")).toBeInTheDocument();
    expect(within(result).getByText("OK")).toBeInTheDocument();
    const [call] = called(fetchImpl, "POST", "/ai/providers/test");
    expect(call?.[1]?.body ?? null).toBeNull();
  });

  it("does not invent usage when the provider reported none", async () => {
    const user = userEvent.setup();
    renderRoute(
      path,
      routes({
        "POST /api/v1/ai/providers/test": () =>
          jsonResponse(makeAITestResult({ usage_available: false, input_tokens: null, output_tokens: null, total_tokens: null })),
      }),
      admin,
    );
    await user.click(await screen.findByRole("button", { name: "Run test" }));
    const result = await screen.findByRole("group", { name: "Provider test result" });
    expect(within(result).getByText("Not reported by the provider")).toBeInTheDocument();
  });

  it("shows a loading state and then a failed test", async () => {
    const user = userEvent.setup();
    let release: (response: Response) => void = () => {};
    renderRoute(
      path,
      routes({
        "POST /api/v1/ai/providers/test": () =>
          new Promise<Response>((resolve) => {
            release = resolve;
          }) as unknown as Response,
      }),
      admin,
    );
    await user.click(await screen.findByRole("button", { name: "Run test" }));
    expect(await screen.findByText("Waiting for the provider…")).toBeInTheDocument();
    release(
      jsonResponse(
        makeAITestResult({
          status: "failed",
          message: "The AI provider is rate limiting requests. Try again later.",
          error_category: "rate_limited",
          error_code: "AI_PROVIDER_RATE_LIMITED",
          latency_ms: null,
          usage_available: false,
          response_preview: null,
          request_id: null,
        }),
      ),
    );
    const result = await screen.findByRole("group", { name: "Provider test result" });
    expect(within(result).getByText("Test failed")).toBeInTheDocument();
    expect(within(result).getByText("Rate limited")).toBeInTheDocument();
    expect(within(result).queryByText(/Token usage/)).not.toBeInTheDocument();
  });

  it("shows an error when the test request itself is refused", async () => {
    const user = userEvent.setup();
    renderRoute(
      path,
      routes({ "POST /api/v1/ai/providers/test": () => errorResponse(429, "TOO_MANY_REQUESTS", "Too many requests. Try again later.") }),
      admin,
    );
    await user.click(await screen.findByRole("button", { name: "Run test" }));
    expect(await screen.findByText("Too many attempts. Wait a few minutes and try again.")).toBeInTheDocument();
  });

  it("lists usage with reported tokens and marks requests without usage", async () => {
    renderRoute(
      path,
      routes({
        "GET /api/v1/ai/usage": () =>
          jsonResponse(
            makeAIUsagePage([
              makeAIUsageRecord(),
              makeAIUsageRecord({
                id: "88888888-8888-4888-8888-888888888888",
                status: "failure",
                error_category: "timeout",
                usage_available: false,
                input_tokens: null,
                output_tokens: null,
                total_tokens: null,
              }),
            ]),
          ),
      }),
      admin,
    );
    const usage = await screen.findByRole("region", { name: "Usage" });
    expect(await within(usage).findByText("31 / 2 / 33")).toBeInTheDocument();
    expect(within(usage).getByText("Not reported")).toBeInTheDocument();
    expect(within(usage).getByText("Timed out")).toBeInTheDocument();
    const summary = within(usage).getByLabelText("Usage summary");
    expect(within(summary).getByText("1 of 2")).toBeInTheDocument();
    expect(within(summary).getByText("33")).toBeInTheDocument();
  });

  it("shows an empty usage state without a summary", async () => {
    renderRoute(path, routes({ "GET /api/v1/ai/usage": () => jsonResponse(makeAIUsagePage([])) }), admin);
    expect(await screen.findByText("No AI requests yet")).toBeInTheDocument();
    expect(screen.queryByLabelText("Usage summary")).not.toBeInTheDocument();
  });

  it("shows loading and error states for the configuration", async () => {
    const { unmount } = renderRoute(path, neverResolvingFetch, admin);
    expect(await screen.findByRole("status", { name: "Loading usage" })).toBeInTheDocument();
    unmount();

    renderRoute(
      path,
      routes({ "GET /api/v1/ai/providers": () => errorResponse(500, "INTERNAL_SERVER_ERROR", "Something broke.") }),
      admin,
    );
    expect(await screen.findByText("Something broke.")).toBeInTheDocument();
  });

  it("shows only what each permission allows", async () => {
    const fetchImpl = routes();
    const { unmount } = renderRoute(path, fetchImpl, makeCurrentUser({ permissions: ["ai.provider.read"] }));
    expect(await screen.findByRole("region", { name: "Configuration" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Run test" })).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Usage" })).not.toBeInTheDocument();
    expect(called(fetchImpl, "GET", "/ai/usage")).toHaveLength(0);
    unmount();

    const usageOnly = routes();
    renderRoute(path, usageOnly, makeCurrentUser({ permissions: ["ai.usage.read"] }));
    expect(await screen.findByRole("region", { name: "Usage" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Configuration" })).not.toBeInTheDocument();
    expect(called(usageOnly, "GET", "/ai/providers")).toHaveLength(0);
  });

  it("is not reachable without any AI permission and is listed in navigation only with one", async () => {
    const { unmount } = renderRoute(path, routeFetch({}), makeCurrentUser({ permissions: ["schema.read"] }));
    expect(await screen.findByText("Your account does not have access to this page.")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "AI provider" })).not.toBeInTheDocument();
    unmount();

    renderRoute("/", routes(), admin);
    expect((await screen.findAllByRole("link", { name: "AI provider" })).length).toBeGreaterThan(0);
  });
});

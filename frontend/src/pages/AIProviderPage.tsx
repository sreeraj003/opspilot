import { Activity, BrainCircuit, RefreshCw } from "lucide-react";
import { useState, type ReactNode } from "react";

import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { DataTable, type DataTableColumn } from "@/components/ui/DataTable";
import { PageHeader } from "@/components/ui/Layout";
import { SkeletonText } from "@/components/ui/Skeleton";
import { StatusIndicator, type StatusTone } from "@/components/ui/StatusIndicator";
import { useAIProviderHealth, useAIProviders, useAIUsage, useTestAIProvider } from "@/features/ai/hooks";
import { usePermissions } from "@/features/permissions/permissionsContext";
import { Pagination } from "@/features/rbac/components";
import { useDocumentTitle } from "@/hooks/useDocumentTitle";
import type { AIHealthStatus, AIProvider, AIProviderTestResult, AIUsageRecord } from "@/types/ai";
import { errorMessage } from "@/utils/errors";
import { formatDateTime, humanize } from "@/utils/format";

const USAGE_PAGE_SIZE = 10;

const HEALTH: Record<AIHealthStatus, { tone: StatusTone; label: string }> = {
  healthy: { tone: "success", label: "Reachable" },
  unhealthy: { tone: "danger", label: "Unreachable" },
  not_configured: { tone: "neutral", label: "Not configured" },
};

const CATEGORY_LABELS: Record<string, string> = {
  configuration: "Configuration",
  authentication: "Authentication rejected",
  rate_limited: "Rate limited",
  timeout: "Timed out",
  connection: "Connection failed",
  invalid_request: "Request rejected",
  malformed_response: "Unusable response",
  unavailable: "Provider unavailable",
};

function categoryLabel(category: string | null): string {
  if (!category) return "";
  return CATEGORY_LABELS[category] ?? humanize(category);
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5 sm:flex-row sm:justify-between sm:gap-4">
      <dt className="text-fg-muted">{label}</dt>
      <dd className="min-w-0 break-words text-fg sm:text-right">{children}</dd>
    </div>
  );
}

function tokens(value: number | null): string {
  return value === null ? "—" : value.toLocaleString();
}

function ConfigurationCard({ provider, others }: { provider: AIProvider; others: AIProvider[] }) {
  return (
    <Card title="Configuration" description="Set in the server environment (.env). It cannot be changed from the browser.">
      {!provider.configured && (
        <Alert tone="warning" className="mb-4">
          {provider.display_name} is not configured. Set {provider.missing_settings.join(" and ")} in the server&apos;s .env file and
          restart the backend. Until then AI features are unavailable; everything else keeps working.
        </Alert>
      )}
      <dl className="space-y-2 text-sm">
        <Row label="Provider">
          {provider.display_name} <Badge tone="info">Active</Badge>
        </Row>
        <Row label="Model">{provider.model ? <span className="font-mono">{provider.model}</span> : <span className="text-fg-muted">Not set</span>}</Row>
        <Row label="Endpoint">
          {provider.base_url ? <span className="font-mono text-xs">{provider.base_url}</span> : <span className="text-fg-muted">Not set</span>}
        </Row>
        <Row label="API key">
          {provider.credential.configured ? <Badge>Set on server, hidden</Badge> : <Badge tone="danger">Missing</Badge>}
          <span className="block font-mono text-xs text-fg-muted">{provider.credential.reference}</span>
        </Row>
        <Row label="Temperature">{provider.temperature}</Row>
        <Row label="Max output tokens">{provider.max_output_tokens.toLocaleString()}</Row>
        <Row label="Timeout">{provider.timeout_seconds} s per request</Row>
        <Row label="Retries">
          {provider.max_retries === 0 ? "Off" : `Up to ${provider.max_retries}, transient failures only`}
        </Row>
        {others.length > 0 && (
          <Row label="Other providers">
            {others.map((other) => (
              <span key={other.name} className="block">
                {other.display_name}: {other.configured ? "configured" : "not configured"}
              </span>
            ))}
          </Row>
        )}
      </dl>
    </Card>
  );
}

function HealthCard() {
  const health = useAIProviderHealth();
  return (
    <Card
      title="Health"
      description="Asks the provider for its model list. No text is generated and no tokens are used."
      actions={
        <Button variant="secondary" size="sm" onClick={() => void health.refetch()} loading={health.isFetching}>
          <RefreshCw aria-hidden="true" className="h-4 w-4" />
          Check health
        </Button>
      }
    >
      {health.isPending ? (
        <SkeletonText lines={3} />
      ) : health.isError ? (
        <Alert tone="error">{errorMessage(health.error, "The provider health could not be checked.")}</Alert>
      ) : (
        <div className="space-y-3 text-sm" aria-label="Provider health" role="group">
          <div className="flex flex-wrap items-center gap-2">
            <StatusIndicator tone={HEALTH[health.data.status].tone} label={HEALTH[health.data.status].label} />
            <span className="text-fg-muted">{health.data.message}</span>
          </div>
          <dl className="space-y-2">
            {health.data.error_category && <Row label="Problem">{categoryLabel(health.data.error_category)}</Row>}
            {health.data.latency_ms !== null && <Row label="Latency">{health.data.latency_ms} ms</Row>}
            {health.data.model_available !== null && (
              <Row label="Model listed">{health.data.model_available ? "Yes" : "No: check the model name"}</Row>
            )}
            <Row label="Checked">{formatDateTime(health.data.checked_at)}</Row>
          </dl>
        </div>
      )}
    </Card>
  );
}

function TestResult({ result }: { result: AIProviderTestResult }) {
  const passed = result.status === "passed";
  return (
    <div className="space-y-3 text-sm" role="group" aria-label="Provider test result">
      <div className="flex flex-wrap items-center gap-2">
        <StatusIndicator tone={passed ? "success" : "danger"} label={passed ? "Test passed" : "Test failed"} />
        <span className="text-fg-muted">{result.message}</span>
      </div>
      <dl className="space-y-2">
        {result.error_category && <Row label="Problem">{categoryLabel(result.error_category)}</Row>}
        {result.model && (
          <Row label="Model">
            <span className="font-mono">{result.model}</span>
          </Row>
        )}
        {result.latency_ms !== null && <Row label="Latency">{result.latency_ms} ms</Row>}
        {result.attempts !== null && result.attempts > 1 && <Row label="Attempts">{result.attempts}</Row>}
        {result.finish_reason && <Row label="Finish reason">{result.finish_reason}</Row>}
        {passed && (
          <Row label="Token usage">
            {result.usage_available ? (
              `${tokens(result.input_tokens)} in, ${tokens(result.output_tokens)} out, ${tokens(result.total_tokens)} total`
            ) : (
              <span className="text-fg-muted">Not reported by the provider</span>
            )}
          </Row>
        )}
        {result.response_preview && (
          <Row label="Reply">
            <span className="font-mono text-xs">{result.response_preview}</span>
          </Row>
        )}
        {result.request_id && (
          <Row label="Request id">
            <span className="font-mono text-xs">{result.request_id}</span>
          </Row>
        )}
        <Row label="Tested">{formatDateTime(result.tested_at)}</Row>
      </dl>
    </div>
  );
}

function TestCard() {
  const test = useTestAIProvider();
  return (
    <Card
      title="Connection test"
      description="Sends a fixed test prompt chosen by the server (“Reply with OK.”) and shows the outcome. Each run uses a few tokens."
      actions={
        <Button size="sm" onClick={() => test.mutate()} loading={test.isPending}>
          <Activity aria-hidden="true" className="h-4 w-4" />
          Run test
        </Button>
      }
    >
      {test.isPending ? (
        <p className="text-sm text-fg-muted" role="status">
          Waiting for the provider…
        </p>
      ) : test.isError ? (
        <Alert tone="error">{errorMessage(test.error, "The test could not be run.")}</Alert>
      ) : test.data ? (
        <TestResult result={test.data} />
      ) : (
        <p className="text-sm text-fg-muted">No test run yet in this session.</p>
      )}
    </Card>
  );
}

function UsageCard() {
  const [page, setPage] = useState(0);
  const usage = useAIUsage({ offset: page * USAGE_PAGE_SIZE, limit: USAGE_PAGE_SIZE });
  const summary = usage.data?.summary;
  const columns: DataTableColumn<AIUsageRecord>[] = [
    { id: "when", header: "When", cell: (r) => <span className="whitespace-nowrap">{formatDateTime(r.created_at)}</span> },
    { id: "operation", header: "Operation", hideBelow: "sm", cell: (r) => humanize(r.operation) },
    {
      id: "status",
      header: "Result",
      cell: (r) => (
        <span>
          <StatusIndicator variant="dot" tone={r.status === "success" ? "success" : "danger"} label={r.status === "success" ? "Success" : "Failed"} />
          {r.error_category && <span className="block text-xs text-fg-muted">{categoryLabel(r.error_category)}</span>}
        </span>
      ),
    },
    { id: "model", header: "Model", hideBelow: "md", cell: (r) => <span className="font-mono text-xs">{r.model}</span> },
    { id: "latency", header: "Latency", hideBelow: "sm", align: "right", cell: (r) => `${r.latency_ms} ms` },
    {
      id: "tokens",
      header: "Tokens (in / out / total)",
      align: "right",
      cell: (r) =>
        r.usage_available ? (
          `${tokens(r.input_tokens)} / ${tokens(r.output_tokens)} / ${tokens(r.total_tokens)}`
        ) : (
          <span className="text-fg-muted">Not reported</span>
        ),
    },
  ];
  return (
    <Card
      title="Usage"
      description="Every AI request, with the token counts the provider reported. Counts are never estimated, and no costs are calculated."
      padding="none"
    >
      {summary && summary.requests > 0 && (
        <dl className="grid grid-cols-2 gap-4 border-b border-border p-4 text-sm sm:grid-cols-4" aria-label="Usage summary">
          <div>
            <dt className="text-fg-muted">Requests</dt>
            <dd className="text-lg font-semibold text-fg">{summary.requests.toLocaleString()}</dd>
          </div>
          <div>
            <dt className="text-fg-muted">Failed</dt>
            <dd className="text-lg font-semibold text-fg">{summary.failed.toLocaleString()}</dd>
          </div>
          <div>
            <dt className="text-fg-muted">Reported tokens</dt>
            <dd className="text-lg font-semibold text-fg">
              {summary.requests_with_usage > 0 ? summary.total_tokens.toLocaleString() : "—"}
            </dd>
          </div>
          <div>
            <dt className="text-fg-muted">Requests with usage</dt>
            <dd className="text-lg font-semibold text-fg">
              {summary.requests_with_usage.toLocaleString()} of {summary.requests.toLocaleString()}
            </dd>
          </div>
        </dl>
      )}
      <DataTable
        caption="AI requests, newest first"
        columns={columns}
        rows={usage.data?.items}
        getRowId={(r) => r.id}
        loading={usage.isPending}
        loadingLabel="Loading usage"
        error={usage.isError ? errorMessage(usage.error, "Usage could not be loaded.") : null}
        onRetry={() => void usage.refetch()}
        retrying={usage.isFetching}
        empty={{ title: "No AI requests yet", description: "Requests appear here once the provider has been used." }}
        footer={
          usage.data && usage.data.total > USAGE_PAGE_SIZE ? (
            <Pagination page={page} pageSize={USAGE_PAGE_SIZE} total={usage.data.total} onPage={setPage} itemLabel="Requests" />
          ) : undefined
        }
      />
    </Card>
  );
}

function ProviderSection({ canTest }: { canTest: boolean }) {
  const providers = useAIProviders();
  if (providers.isPending) return <SkeletonText lines={6} />;
  if (providers.isError) return <Alert tone="error">{errorMessage(providers.error, "The AI provider configuration could not be loaded.")}</Alert>;
  const active = providers.data.items.find((p) => p.is_active) ?? providers.data.items[0];
  if (!active) return <Alert tone="error">No AI provider is defined on the server.</Alert>;
  const others = providers.data.items.filter((p) => p.name !== active.name);
  return (
    <>
      <div className="grid gap-section lg:grid-cols-2">
        <ConfigurationCard provider={active} others={others} />
        <HealthCard />
      </div>
      {canTest && <TestCard />}
    </>
  );
}

export function AIProviderPage() {
  useDocumentTitle("AI provider");
  const { hasPermission } = usePermissions();
  const canRead = hasPermission("ai.provider.read");
  const canTest = hasPermission("ai.provider.test");
  const canReadUsage = hasPermission("ai.usage.read");

  return (
    <div className="space-y-section">
      <PageHeader
        title="AI provider"
        description="The language model OpsPilot uses. API keys stay on the server and are never shown here."
        meta={<BrainCircuit aria-hidden="true" className="h-5 w-5 text-fg-subtle" />}
      />
      {canRead ? (
        <ProviderSection canTest={canTest} />
      ) : canTest ? (
        <TestCard />
      ) : null}
      {canReadUsage && <UsageCard />}
    </div>
  );
}

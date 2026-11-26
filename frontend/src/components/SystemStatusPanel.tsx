import { Database, MonitorSmartphone, RefreshCw, Server } from "lucide-react";

import { DetailList } from "@/components/DetailList";
import { StatusCard } from "@/components/StatusCard";
import { Button } from "@/components/ui/Button";
import { SectionHeader } from "@/components/ui/Layout";
import type { StatusTone } from "@/components/ui/StatusIndicator";
import { useBackendHealth, useDatabaseHealth } from "@/hooks/useSystemStatus";
import { ApiError } from "@/services/apiClient";
import { appConfig } from "@/services/config";
import type { DatabaseStatus } from "@/types/api";
import { cn } from "@/utils/cn";
import { formatTime, humanize } from "@/utils/format";

const DATABASE_STATUS: Record<DatabaseStatus, { tone: StatusTone; label: string }> = {
  ok: { tone: "success", label: "Connected" },
  unavailable: { tone: "danger", label: "Unavailable" },
  not_configured: { tone: "neutral", label: "Not configured" },
};

const DATABASE_ENGINES: Record<string, string> = { postgresql: "PostgreSQL" };

function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : "Unexpected error while contacting the server.";
}

/** Live application, backend connectivity and database status, refreshed every 30 seconds. */
export function SystemStatusPanel() {
  const health = useBackendHealth();
  const database = useDatabaseHealth();
  const refreshing = health.isFetching || database.isFetching;

  return (
    <section aria-labelledby="system-status-heading">
      <div className="mb-4">
        <SectionHeader
          id="system-status-heading"
          title="System status"
          description="Live connectivity between this application and its services."
          actions={
            <Button
              variant="secondary"
              size="sm"
              onClick={() => {
                void health.refetch();
                void database.refetch();
              }}
              disabled={refreshing}
            >
              <RefreshCw aria-hidden="true" className={cn("h-4 w-4", refreshing && "animate-spin")} />
              Refresh
            </Button>
          }
        />
      </div>

      <div className="grid gap-4 md:grid-cols-3">
        <StatusCard title="Application" icon={MonitorSmartphone} tone="success" statusLabel="Running">
          <DetailList
            items={[
              { label: "Build", value: import.meta.env.DEV ? "Development server" : "Production build" },
              { label: "API endpoint", value: appConfig.apiBaseUrl },
            ]}
          />
        </StatusCard>

        {health.isPending ? (
          <StatusCard title="Backend API" icon={Server} tone="neutral" statusLabel="Checking" pending>
            Contacting the backend…
          </StatusCard>
        ) : health.isError ? (
          <StatusCard title="Backend API" icon={Server} tone="danger" statusLabel="Unreachable">
            <p role="alert">{errorMessage(health.error)}</p>
          </StatusCard>
        ) : (
          <StatusCard title="Backend API" icon={Server} tone="success" statusLabel="Connected">
            <DetailList
              items={[
                { label: "Version", value: health.data.version },
                { label: "Environment", value: humanize(health.data.environment) },
                { label: "Last checked", value: formatTime(health.data.timestamp) },
              ]}
            />
          </StatusCard>
        )}

        {database.isPending ? (
          <StatusCard title="Database" icon={Database} tone="neutral" statusLabel="Checking" pending>
            Checking the application database…
          </StatusCard>
        ) : database.isError ? (
          <StatusCard title="Database" icon={Database} tone="neutral" statusLabel="Unknown">
            <p>Database status is unavailable while the backend cannot be reached.</p>
          </StatusCard>
        ) : (
          <StatusCard
            title="Database"
            icon={Database}
            tone={DATABASE_STATUS[database.data.status].tone}
            statusLabel={DATABASE_STATUS[database.data.status].label}
          >
            <DetailList
              items={[
                { label: "Engine", value: DATABASE_ENGINES[database.data.database] ?? database.data.database },
                {
                  label: "Latency",
                  value: database.data.latency_ms === null ? "Not measured" : `${database.data.latency_ms} ms`,
                },
              ]}
            />
            {database.data.detail && <p className="mt-3 text-fg-muted">{database.data.detail}</p>}
          </StatusCard>
        )}
      </div>
    </section>
  );
}

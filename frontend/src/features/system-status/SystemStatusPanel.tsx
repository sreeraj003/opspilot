import { StatusBadge } from "@/components/StatusBadge";
import { ApiError } from "@/services/apiClient";
import { formatDateTime, humanize } from "@/utils/format";

import { useHealth, useReadiness } from "./hooks";

function describeError(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return "Unexpected error while contacting the API.";
}

export function SystemStatusPanel() {
  const health = useHealth();
  const readiness = useReadiness();

  return (
    <section aria-labelledby="system-status-heading" className="rounded-xl border border-slate-200 bg-white shadow-sm">
      <header className="flex items-center justify-between border-b border-slate-200 px-5 py-4">
        <div>
          <h2 id="system-status-heading" className="text-base font-semibold">
            Backend connection
          </h2>
          <p className="text-sm text-slate-500">Live status of the OpsPilot AI API.</p>
        </div>
        <button
          type="button"
          onClick={() => {
            void health.refetch();
            void readiness.refetch();
          }}
          disabled={health.isFetching || readiness.isFetching}
          className="rounded-md border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
        >
          Refresh
        </button>
      </header>

      <div className="px-5 py-4">
        {health.isPending ? (
          <p className="text-sm text-slate-500">Checking API health…</p>
        ) : health.isError ? (
          <div role="alert" className="flex items-center gap-3">
            <StatusBadge status="error" label="Unreachable" />
            <p className="text-sm text-slate-600">{describeError(health.error)}</p>
          </div>
        ) : (
          <dl className="grid grid-cols-1 gap-4 text-sm sm:grid-cols-4">
            <div>
              <dt className="text-slate-500">API status</dt>
              <dd className="mt-1">
                <StatusBadge status={health.data.status} label="Online" />
              </dd>
            </div>
            <div>
              <dt className="text-slate-500">Version</dt>
              <dd className="mt-1 font-medium">{health.data.version}</dd>
            </div>
            <div>
              <dt className="text-slate-500">Environment</dt>
              <dd className="mt-1 font-medium">{humanize(health.data.environment)}</dd>
            </div>
            <div>
              <dt className="text-slate-500">Last checked</dt>
              <dd className="mt-1 font-medium">{formatDateTime(health.data.timestamp)}</dd>
            </div>
          </dl>
        )}
      </div>

      <div className="border-t border-slate-200 px-5 py-4">
        <div className="flex items-center gap-2">
          <h3 className="text-sm font-semibold">Readiness</h3>
          {readiness.data && <StatusBadge status={readiness.data.status} />}
        </div>
        {readiness.isPending ? (
          <p className="mt-2 text-sm text-slate-500">Checking readiness…</p>
        ) : readiness.isError ? (
          <p role="alert" className="mt-2 text-sm text-rose-700">
            {describeError(readiness.error)}
          </p>
        ) : (
          <ul className="mt-3 divide-y divide-slate-100">
            {readiness.data.checks.map((check) => (
              <li key={check.name} className="flex items-center justify-between py-2 text-sm">
                <div>
                  <span className="font-medium">{humanize(check.name)}</span>
                  {check.detail && <span className="ml-2 text-slate-500">{check.detail}</span>}
                </div>
                <StatusBadge status={check.status} />
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

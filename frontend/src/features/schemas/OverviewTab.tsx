import { CheckCircle2 } from "lucide-react";

import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { ErrorState } from "@/components/ui/ErrorState";
import { SkeletonText } from "@/components/ui/Skeleton";
import { DatabaseTypeLabel, IssueList } from "@/features/schemas/components";
import { useSchemaSummary } from "@/features/schemas/hooks";
import type { SchemaSource } from "@/types/schemaRegistry";
import { errorMessage } from "@/utils/errors";
import { formatDateTime } from "@/utils/format";

function Stat({ label, value, detail }: { label: string; value: number | string; detail?: string }) {
  return (
    <div className="rounded-md border border-line bg-surface p-4">
      <dt className="text-xs font-medium uppercase tracking-wide text-fg-muted">{label}</dt>
      <dd className="mt-1 text-2xl font-semibold text-fg">{value}</dd>
      {detail && <dd className="mt-0.5 text-xs text-fg-muted">{detail}</dd>}
    </div>
  );
}

export function OverviewTab({ schema }: { schema: SchemaSource }) {
  const summary = useSchemaSummary(schema.id);

  if (summary.isPending) return <SkeletonText lines={6} />;
  if (summary.isError) {
    return (
      <ErrorState
        message={errorMessage(summary.error, "The schema summary could not be loaded.")}
        onRetry={() => void summary.refetch()}
        retrying={summary.isFetching}
      />
    );
  }
  const { counts, issues } = summary.data;
  const errors = issues.filter((issue) => issue.severity === "error");
  return (
    <div className="space-y-section">
      <dl className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Tables" value={counts.tables} detail={`${counts.active_tables} active`} />
        <Stat label="Columns" value={counts.columns} detail={`${counts.active_columns} active`} />
        <Stat label="Relationships" value={counts.relationships} detail={`${counts.active_relationships} active`} />
        <Stat
          label="Schema version"
          value={`v${summary.data.schema_version}`}
          detail={`Updated ${formatDateTime(summary.data.metadata_updated_at)}`}
        />
      </dl>

      <div className="grid gap-section lg:grid-cols-2">
        <Card title="Details">
          <dl className="space-y-2 text-sm">
            <div className="flex justify-between gap-4">
              <dt className="text-fg-muted">Database</dt>
              <dd className="text-right font-medium text-fg">
                <DatabaseTypeLabel type={schema.database_type} dialect={schema.dialect} />
              </dd>
            </div>
            <div className="flex justify-between gap-4">
              <dt className="text-fg-muted">Namespaces</dt>
              <dd className="flex flex-wrap justify-end gap-1">
                {counts.schema_namespaces.length === 0 ? (
                  <span className="text-fg-muted">None yet</span>
                ) : (
                  counts.schema_namespaces.map((name) => (
                    <Badge key={name} mono>
                      {name}
                    </Badge>
                  ))
                )}
              </dd>
            </div>
            <div className="flex justify-between gap-4">
              <dt className="text-fg-muted">Key columns</dt>
              <dd className="text-right text-fg">
                {counts.primary_key_columns} primary, {counts.foreign_key_columns} foreign
              </dd>
            </div>
            <div className="flex justify-between gap-4">
              <dt className="text-fg-muted">Sensitive columns</dt>
              <dd className="text-right text-fg">{counts.sensitive_columns}</dd>
            </div>
            <div className="flex justify-between gap-4">
              <dt className="text-fg-muted">Registered</dt>
              <dd className="text-right text-fg">{formatDateTime(schema.created_at)}</dd>
            </div>
          </dl>
          {schema.description && <p className="mt-4 text-sm text-fg-secondary">{schema.description}</p>}
        </Card>

        <Card title="Metadata checks" description="The registered metadata as the validation service sees it now.">
          {issues.length === 0 ? (
            <p className="flex items-center gap-2 text-sm text-success-text">
              <CheckCircle2 aria-hidden="true" className="h-4 w-4" /> No problems found.
            </p>
          ) : (
            <div className="space-y-3">
              <p className="text-sm text-fg-secondary">
                {errors.length > 0 ? `${errors.length} error(s) and ` : ""}
                {issues.length - errors.length} warning(s). Warnings do not block changes.
              </p>
              <IssueList issues={issues} max={20} />
            </div>
          )}
        </Card>
      </div>

      <Card
        title="Business glossary"
        description="Terms the AI should understand. Maintain them through a JSON import (business_glossary)."
      >
        {schema.business_glossary.length === 0 ? (
          <p className="text-sm text-fg-muted">No terms defined.</p>
        ) : (
          <dl className="divide-y divide-line text-sm">
            {schema.business_glossary.map((term) => (
              <div key={term.term} className="py-2 first:pt-0 last:pb-0">
                <dt className="font-medium text-fg">
                  {term.term}
                  {term.synonyms.length > 0 && (
                    <span className="ml-2 text-xs font-normal text-fg-muted">also: {term.synonyms.join(", ")}</span>
                  )}
                </dt>
                <dd className="text-fg-secondary">{term.definition}</dd>
              </div>
            ))}
          </dl>
        )}
      </Card>
    </div>
  );
}

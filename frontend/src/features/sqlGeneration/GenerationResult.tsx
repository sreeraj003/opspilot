import { Check, Copy, RefreshCw } from "lucide-react";
import { useState, type ReactNode } from "react";

import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Dialog } from "@/components/ui/Dialog";
import { SkeletonText } from "@/components/ui/Skeleton";
import { StatusIndicator, type StatusTone } from "@/components/ui/StatusIndicator";
import { useSqlGeneration } from "@/features/sqlGeneration/hooks";
import { SqlCode } from "@/features/sqlGeneration/SqlCode";
import type { FailureCategory, GenerationStatus, SqlGeneration } from "@/types/sqlGeneration";
import { errorMessage } from "@/utils/errors";
import { humanize } from "@/utils/format";

export const AWAITING_VALIDATION = "Generated — Awaiting Security Validation";

const STATUS: Record<GenerationStatus, { tone: StatusTone; label: string }> = {
  generated: { tone: "info", label: AWAITING_VALIDATION },
  clarification_required: { tone: "warning", label: "Clarification required" },
  generation_failed: { tone: "danger", label: "Generation failed" },
};

const FAILURES: Record<FailureCategory, string> = {
  validation_failed: "The query did not pass preliminary validation",
  unsafe_sql: "The query was not a single read-only SELECT and was discarded",
  permission_denied: "The question needs data you are not permitted to query",
  malformed_output: "The AI reply could not be read",
  provider_error: "The AI provider failed",
  model_declined: "The AI declined to generate a query",
};

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5 sm:flex-row sm:justify-between sm:gap-4">
      <dt className="text-fg-muted">{label}</dt>
      <dd className="min-w-0 break-words text-fg sm:text-right">{children}</dd>
    </div>
  );
}

function Section({ title, empty, children }: { title: string; empty: boolean; children: ReactNode }) {
  return (
    <div>
      <h3 className="mb-2 text-sm font-semibold text-fg">{title}</h3>
      {empty ? <p className="text-sm text-fg-muted">None</p> : children}
    </div>
  );
}

function Mono({ items, label }: { items: string[]; label: string }) {
  return (
    <ul aria-label={label} className="flex flex-wrap gap-1.5">
      {items.map((item) => (
        <li key={item}>
          <Badge mono>{item}</Badge>
        </li>
      ))}
    </ul>
  );
}

function CopyButton({ sql }: { sql: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <Button
      size="sm"
      variant="secondary"
      onClick={async () => {
        try {
          await navigator.clipboard?.writeText(sql);
          setCopied(true);
        } catch {
          setCopied(false);
        }
      }}
    >
      {copied ? <Check aria-hidden="true" className="h-4 w-4" /> : <Copy aria-hidden="true" className="h-4 w-4" />}
      {copied ? "Copied" : "Copy SQL"}
    </Button>
  );
}

function DetailsDialog({ generationId, open, onClose }: { generationId: string; open: boolean; onClose: () => void }) {
  const details = useSqlGeneration(generationId, open);
  const data = details.data;
  return (
    <Dialog open={open} onClose={onClose} title="Generation details" description="Stored metadata for this untrusted query." size="lg">
      {details.isPending ? (
        <SkeletonText lines={6} />
      ) : details.isError ? (
        <Alert tone="error">{errorMessage(details.error, "The generation could not be loaded.")}</Alert>
      ) : data ? (
        <dl className="space-y-2 text-sm">
          <Row label="Generation">
            <span className="font-mono">{data.generation_id}</span>
          </Row>
          <Row label="Intent">
            <span className="font-mono">{data.intent_id}</span>
          </Row>
          <Row label="Schema version">{data.schema_version}</Row>
          <Row label="Dialect">{data.dialect_name}</Row>
          <Row label="Provider and model">{[data.provider, data.generation_model].filter(Boolean).join(" · ") || "—"}</Row>
          <Row label="Model requests">{data.generation_attempts}</Row>
          <Row label="Prompt version">
            <span className="font-mono">{data.prompt_version}</span>
          </Row>
          <Row label="Preliminary validation">{humanize(data.validation_status)}</Row>
          <Row label="Security status">Awaiting security validation (not approved, not executed)</Row>
          <Row label="SQL fingerprint">
            <span className="font-mono break-all">{data.sql_sha256 ?? "—"}</span>
          </Row>
          {data.regenerated_from_id && (
            <Row label="Regenerated from">
              <span className="font-mono">{data.regenerated_from_id}</span>
            </Row>
          )}
          <Row label="Created">{data.created_at ?? "—"}</Row>
        </dl>
      ) : null}
    </Dialog>
  );
}

export function GenerationResult({
  result,
  onRegenerate,
  regenerating,
  regenerateError,
}: {
  result: SqlGeneration;
  onRegenerate: () => void;
  regenerating: boolean;
  regenerateError?: string | null;
}) {
  const [details, setDetails] = useState(false);
  const status = STATUS[result.status];
  const canRegenerate = result.regenerations_remaining > 0;

  return (
    <Card
      title="SQL generation result"
      description="Generated SQL is untrusted. It has not been approved or executed; the SQL security guard validates it separately."
      actions={
        <>
          <Button size="sm" variant="ghost" onClick={() => setDetails(true)}>
            View generation details
          </Button>
          <Button
            size="sm"
            variant="secondary"
            onClick={onRegenerate}
            loading={regenerating}
            disabled={!canRegenerate}
            title={canRegenerate ? undefined : "No regenerations left for this question"}
          >
            {!regenerating && <RefreshCw aria-hidden="true" className="h-4 w-4" />}
            Regenerate SQL
          </Button>
        </>
      }
    >
      <div className="space-y-5">
        <div className="flex flex-wrap items-center gap-2" aria-label="Generation status">
          <StatusIndicator tone={status.tone} label={status.label} />
          <Badge>{result.dialect_name}</Badge>
          <Badge tone={result.validation_status === "passed" ? "success" : result.validation_status === "failed" ? "danger" : "neutral"}>
            {result.validation_status === "passed"
              ? "Preliminary checks passed"
              : result.validation_status === "failed"
                ? "Preliminary checks failed"
                : "Preliminary checks not run"}
          </Badge>
          <span className="text-xs text-fg-muted">
            {result.regenerations_remaining} regeneration{result.regenerations_remaining === 1 ? "" : "s"} left
          </span>
        </div>

        {regenerateError && <Alert tone="error">{regenerateError}</Alert>}

        {result.status === "clarification_required" && (
          <Alert tone="warning" title="Clarification required">
            <p className="text-base font-medium">{result.clarification_question}</p>
            <p className="mt-1 text-xs">No SQL was generated. Rephrase the question with this detail and interpret it again.</p>
          </Alert>
        )}

        {result.status === "generation_failed" && (
          <Alert tone="error" title={result.failure_category ? FAILURES[result.failure_category] : "Generation failed"}>
            {result.explanation}
          </Alert>
        )}

        {result.status === "generated" && (
          <div className="space-y-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h3 className="text-sm font-semibold text-fg">Generated SQL</h3>
              {result.sql_visible && result.sql && <CopyButton sql={result.sql} />}
            </div>
            {result.sql_visible && result.sql ? (
              <SqlCode sql={result.sql} />
            ) : (
              <Alert tone="info" title="SQL text hidden">
                Your data permissions do not allow viewing SQL for these tables. The query was still generated and is awaiting
                security validation.
              </Alert>
            )}
            {result.explanation && <p className="text-sm text-fg-secondary">{result.explanation}</p>}
          </div>
        )}

        <div className="grid gap-5 md:grid-cols-2">
          <Section title="Referenced tables" empty={result.referenced_tables.length === 0}>
            <Mono items={result.referenced_tables} label="Referenced tables" />
          </Section>
          <Section title="Referenced columns" empty={result.referenced_columns.length === 0}>
            <Mono items={result.referenced_columns} label="Referenced columns" />
          </Section>
        </div>

        <Section title="Joins" empty={result.joins_used.length === 0}>
          <ul aria-label="Joins" className="space-y-1 text-sm text-fg-secondary">
            {result.joins_used.map((join) => (
              <li key={`${join.left}-${join.right}`} className="flex flex-wrap items-center gap-2">
                <span className="font-mono">
                  {join.join_type} {join.left} = {join.right}
                </span>
                <Badge tone={join.registered_relationship ? "success" : "danger"}>
                  {join.registered_relationship ? "Registered relationship" : "Not registered"}
                </Badge>
              </li>
            ))}
          </ul>
        </Section>

        <Section title="Assumptions" empty={result.assumptions.length === 0}>
          <ul aria-label="Assumptions" className="list-disc space-y-1 pl-5 text-sm text-fg-secondary">
            {result.assumptions.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </Section>

        {result.permission_notes.length > 0 && (
          <Section title="Permission notes" empty={false}>
            <ul aria-label="Permission notes" className="list-disc space-y-1 pl-5 text-sm text-fg-secondary">
              {result.permission_notes.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          </Section>
        )}

        {result.validation_issues.length > 0 && (
          <Section title="Validation issues" empty={false}>
            <ul aria-label="Validation issues" className="space-y-1 text-sm text-fg-secondary">
              {result.validation_issues.map((issue) => (
                <li key={`${issue.code}-${issue.message}`}>
                  <Badge mono tone={issue.blocking ? "danger" : "warning"}>
                    {issue.code}
                  </Badge>{" "}
                  {issue.message}
                </li>
              ))}
            </ul>
          </Section>
        )}
      </div>
      <DetailsDialog generationId={result.generation_id} open={details} onClose={() => setDetails(false)} />
    </Card>
  );
}

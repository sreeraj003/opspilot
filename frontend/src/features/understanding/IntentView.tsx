import type { ReactNode } from "react";

import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { StatusIndicator } from "@/components/ui/StatusIndicator";
import { AGGREGATION_LABELS, INTENT_LABELS, PERMISSION, STATUS, VALIDATION } from "@/features/understanding/labels";
import type { BusinessIntent, FieldReference, IntentFilter, PreviewResponse } from "@/types/understanding";
import { humanize } from "@/utils/format";

function field(ref: FieldReference | null): string {
  return ref ? `${ref.table}.${ref.column}` : "—";
}

function filterValue(filter: IntentFilter): string {
  if (filter.value === null || filter.value === undefined) return "";
  return Array.isArray(filter.value) ? filter.value.join(", ") : String(filter.value);
}

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

function List({ items, label }: { items: string[]; label: string }) {
  return (
    <ul aria-label={label} className="list-disc space-y-1 pl-5 text-sm text-fg-secondary">
      {items.map((item) => (
        <li key={item}>{item}</li>
      ))}
    </ul>
  );
}

export function ClarificationCard({ result, onSkip, pending }: { result: PreviewResponse; onSkip?: () => void; pending?: boolean }) {
  const clarification = result.clarification;
  if (!clarification.requires_clarification) return null;
  return (
    <Alert tone="warning" title="Clarification needed">
      {clarification.clarification_question ? (
        <p className="text-base font-medium">{clarification.clarification_question}</p>
      ) : (
        <p>Every clarification has been asked once. Rephrase the question with the missing details.</p>
      )}
      {clarification.missing_information.length > 0 && (
        <p className="mt-1 text-xs">Missing: {clarification.missing_information.map(humanize).join(", ")}</p>
      )}
      {clarification.key && onSkip && (
        <div className="mt-2">
          <Button size="sm" variant="secondary" onClick={onSkip} loading={pending}>
            Mark as answered and interpret again
          </Button>
        </div>
      )}
    </Alert>
  );
}

function IntentDetails({ intent }: { intent: BusinessIntent }) {
  const time = intent.time;
  const aggregation = intent.aggregation;
  return (
    <Card title="Business intent" description={`Intent ${intent.intent_id}`}>
      <div className="space-y-5">
        <dl className="space-y-2 text-sm">
          <Row label="Intent type">{INTENT_LABELS[intent.intent_type]}</Row>
          <Row label="Normalized question">{intent.normalized_question}</Row>
          {intent.business_context && <Row label="Business context">{intent.business_context}</Row>}
        </dl>

        <Section title="Metrics" empty={intent.requested_metrics.length === 0}>
          <ul aria-label="Metrics" className="space-y-1.5 text-sm">
            {intent.requested_metrics.map((metric) => (
              <li key={metric.name} className="flex flex-wrap items-center gap-2">
                <span className="font-medium text-fg">{metric.name}</span>
                <Badge tone="accent">{AGGREGATION_LABELS[metric.aggregation]}</Badge>
                <Badge mono>{field(metric.column)}</Badge>
                <span className="text-xs text-fg-muted">
                  {metric.definition_source === "business_term" ? `Business term: ${metric.term_name}` : "Schema column"}
                </span>
              </li>
            ))}
          </ul>
        </Section>

        <Section title="Dimensions" empty={intent.requested_dimensions.length === 0}>
          <ul aria-label="Dimensions" className="space-y-1.5 text-sm">
            {intent.requested_dimensions.map((dimension) => (
              <li key={dimension.name} className="flex flex-wrap items-center gap-2">
                <span className="font-medium text-fg">{dimension.name}</span>
                <Badge mono>{field(dimension.column)}</Badge>
                {dimension.time_granularity && <Badge>{humanize(dimension.time_granularity)}</Badge>}
              </li>
            ))}
          </ul>
        </Section>

        {intent.requested_attributes.length > 0 && (
          <Section title="Attributes" empty={false}>
            <div className="flex flex-wrap gap-1.5">
              {intent.requested_attributes.map((attribute) => (
                <Badge key={field(attribute.column)} mono>
                  {field(attribute.column)}
                </Badge>
              ))}
            </div>
          </Section>
        )}

        <Section title="Filters" empty={intent.filters.length === 0}>
          <ul aria-label="Filters" className="space-y-1.5 text-sm">
            {intent.filters.map((filter, index) => (
              <li key={index} className="flex flex-wrap items-center gap-2">
                <Badge mono>{field(filter.field)}</Badge>
                <span className="text-fg-secondary">{humanize(filter.operator)}</span>
                <span className="font-medium text-fg">{filterValue(filter)}</span>
              </li>
            ))}
          </ul>
        </Section>

        <Section title="Time" empty={!time.time_range && !time.time_granularity}>
          <dl className="space-y-2 text-sm">
            {time.time_range && (
              <>
                <Row label="Expression">“{time.time_range.expression}”</Row>
                <Row label="Period">
                  {time.time_range.start_date} to {time.time_range.end_date} (end excluded)
                </Row>
                <Row label="Kind">
                  {time.time_range.is_rolling ? "Rolling" : "Calendar"}
                  {time.time_range.is_partial ? ", in progress" : ""} · {time.time_range.timezone}
                </Row>
              </>
            )}
            {time.comparison_period && (
              <Row label="Compared with">
                {time.comparison_period.start_date} to {time.comparison_period.end_date} ({humanize(time.comparison_period.kind)})
              </Row>
            )}
            {time.time_granularity && <Row label="Granularity">{humanize(time.time_granularity)}</Row>}
            {time.date_column && <Row label="Date column">{field(time.date_column)}</Row>}
          </dl>
        </Section>

        {(aggregation.grouping_dimensions.length > 0 || aggregation.sorting.length > 0 || aggregation.limit) && (
          <Section title="Grouping and sorting" empty={false}>
            <dl className="space-y-2 text-sm">
              {aggregation.grouping_dimensions.length > 0 && <Row label="Group by">{aggregation.grouping_dimensions.join(", ")}</Row>}
              {aggregation.sorting.length > 0 && (
                <Row label="Sort">
                  {aggregation.sorting.map((s) => `${s.target} ${s.direction === "desc" ? "descending" : "ascending"}`).join(", ")}
                </Row>
              )}
              {aggregation.limit && <Row label="Limit">{aggregation.limit}</Row>}
            </dl>
          </Section>
        )}

        <Section title="Candidate tables" empty={intent.candidate_tables.length === 0}>
          <div className="flex flex-wrap gap-1.5">
            {intent.candidate_tables.map((table) => (
              <Badge key={table} mono>
                {table}
              </Badge>
            ))}
          </div>
        </Section>

        {intent.assumptions.length > 0 && (
          <Section title="Assumptions" empty={false}>
            <List items={intent.assumptions} label="Assumptions" />
          </Section>
        )}
        {intent.ambiguities.length > 0 && (
          <Section title="Ambiguities" empty={false}>
            <List items={intent.ambiguities} label="Ambiguities" />
          </Section>
        )}
      </div>
    </Card>
  );
}

export function InterpretationResult({
  result,
  onSkipClarification,
  pending,
}: {
  result: PreviewResponse;
  onSkipClarification?: () => void;
  pending?: boolean;
}) {
  const status = STATUS[result.status];
  const validation = VALIDATION[result.validation.status];
  const permission = PERMISSION[result.permission.status];
  const diagnostics = result.diagnostics;
  return (
    <div className="space-y-section">
      <Card title="Interpretation" actions={<StatusIndicator tone={status.tone} label={status.label} />}>
        <div className="space-y-4">
          <p className="text-sm text-fg-secondary">{result.explanation}</p>
          <div className="flex flex-wrap gap-2">
            <StatusIndicator tone={validation.tone} label={validation.label} />
            <StatusIndicator tone={permission.tone} label={permission.label} />
            {result.permission.row_level_restrictions && <Badge tone="info">Row-level restrictions apply</Badge>}
            {result.confidence !== null && <Badge>Confidence {Math.round(result.confidence * 100)}%</Badge>}
          </div>
          {result.permission.reason && <Alert tone="error">{result.permission.reason}</Alert>}
          <ClarificationCard result={result} onSkip={onSkipClarification} pending={pending} />
          {result.validation.issues.length > 0 && (
            <div>
              <h3 className="mb-2 text-sm font-semibold text-fg">Validation issues</h3>
              <ul aria-label="Validation issues" className="space-y-1 text-sm">
                {result.validation.issues.map((issue, index) => (
                  <li key={index} className="flex flex-wrap gap-2">
                    <Badge mono tone="danger">
                      {issue.code}
                    </Badge>
                    <span className="text-fg-secondary">{issue.message}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </Card>

      {result.intent && <IntentDetails intent={result.intent} />}

      <Card title="Diagnostics" description="How the question was processed. Prompts and model output are never stored or shown.">
        <dl className="space-y-2 text-sm">
          <Row label="Schema version">{result.schema_version}</Row>
          <Row label="Timezone">
            {diagnostics.timezone} ({diagnostics.timezone_source})
          </Row>
          <Row label="Reference time">{diagnostics.reference_time}</Row>
          {diagnostics.schema_selection && (
            <Row label="Schema selection">
              {humanize(diagnostics.schema_selection.strategy)}: {diagnostics.schema_selection.selected_tables.join(", ") || "none"}
            </Row>
          )}
          <Row label="Matched terms">{diagnostics.matched_terms.map((t) => `${t.name} (“${t.matched_text}”)`).join(", ") || "None"}</Row>
          <Row label="Terms in context">{diagnostics.terms_in_context}</Row>
          <Row label="Model requests">{diagnostics.llm_attempts}</Row>
          <Row label="Correction attempts">{result.validation.correction_attempts}</Row>
          <Row label="Provider">{result.provider ? `${result.provider} · ${result.model ?? ""}` : "Not called"}</Row>
          <Row label="Latency">{result.latency_ms} ms</Row>
          <Row label="Prompt version">{diagnostics.prompt_version}</Row>
          <Row label="Request ID">{result.request_id ?? "—"}</Row>
        </dl>
      </Card>
    </div>
  );
}

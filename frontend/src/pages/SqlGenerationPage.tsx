import { FileCode2 } from "lucide-react";
import { useState, type FormEvent, type ReactNode } from "react";

import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { TextareaField } from "@/components/ui/Field";
import { FormField } from "@/components/ui/FormField";
import { Select } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/Layout";
import { SkeletonText } from "@/components/ui/Skeleton";
import { StatusIndicator } from "@/components/ui/StatusIndicator";
import { GenerationResult } from "@/features/sqlGeneration/GenerationResult";
import { useGenerateSql, useRegenerateSql, useUnderstandQuestion } from "@/features/sqlGeneration/hooks";
import { INTENT_LABELS, STATUS } from "@/features/understanding/labels";
import { useUnderstandingOptions } from "@/features/understanding/hooks";
import { useDocumentTitle } from "@/hooks/useDocumentTitle";
import type { SqlGeneration } from "@/types/sqlGeneration";
import type { IntentType, UnderstandingResponse } from "@/types/understanding";
import { errorMessage } from "@/utils/errors";

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5 sm:flex-row sm:justify-between sm:gap-4">
      <dt className="text-fg-muted">{label}</dt>
      <dd className="min-w-0 break-words text-fg sm:text-right">{children}</dd>
    </div>
  );
}

function QuestionSummaryCard({
  understanding,
  generation,
  schemaName,
}: {
  understanding: UnderstandingResponse;
  generation: SqlGeneration | null;
  schemaName: string;
}) {
  const summary = generation?.question;
  const intent = understanding.intent;
  const status = STATUS[understanding.status];
  const intentType = (summary?.intent_type ?? intent?.intent_type) as IntentType | undefined;
  return (
    <Card title="Question summary" description="What was asked, how it was understood and which schema it is answered from.">
      <dl className="space-y-2 text-sm">
        <Row label="Original question">{summary?.original_question ?? intent?.original_question ?? "—"}</Row>
        <Row label="Interpretation">
          <StatusIndicator tone={status.tone} label={status.label} variant="dot" />
        </Row>
        {intentType && <Row label="Intent type">{INTENT_LABELS[intentType] ?? intentType}</Row>}
        <Row label="Interpreted as">{summary?.explanation ?? understanding.explanation}</Row>
        {summary && summary.metrics.length > 0 && <Row label="Metrics">{summary.metrics.join(", ")}</Row>}
        {summary && summary.dimensions.length > 0 && <Row label="Grouped by">{summary.dimensions.join(", ")}</Row>}
        {summary && summary.filters.length > 0 && <Row label="Filters">{summary.filters.join("; ")}</Row>}
        {summary?.time_range && <Row label="Time range">{summary.time_range}</Row>}
        {summary?.limit && <Row label="Limit">{summary.limit}</Row>}
        <Row label="Schema">
          {summary?.schema_name ?? schemaName} (version {generation?.schema_version ?? understanding.schema_version})
        </Row>
      </dl>
    </Card>
  );
}

export function SqlGenerationPage() {
  useDocumentTitle("SQL generation");
  const options = useUnderstandingOptions();
  const understand = useUnderstandQuestion();
  const generate = useGenerateSql();
  const regenerate = useRegenerateSql();
  const [schemaId, setSchemaId] = useState("");
  const [question, setQuestion] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [current, setCurrent] = useState<SqlGeneration | null>(null);
  const sources = options.data?.sources ?? [];
  const selected = schemaId || sources[0]?.id || "";
  const selectedName = sources.find((s) => s.id === selected)?.name ?? "";
  const maxChars = options.data?.max_question_chars ?? 1000;
  const understanding = understand.data;
  const busy = understand.isPending || generate.isPending;

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    if (!selected) return setError("Choose a schema.");
    if (question.trim().length < 3) return setError("Enter a business question.");
    setError(null);
    setCurrent(null);
    generate.reset();
    regenerate.reset();
    understand.mutate(
      { question: question.trim(), schema_source_id: selected },
      {
        onSuccess: (result) => {
          // SQL is generated only from an intent the backend understood and stored; never from raw text.
          if (result.status !== "understood") return;
          generate.mutate(
            { intent_id: result.intent_id, schema_source_id: result.schema_source_id },
            { onSuccess: setCurrent },
          );
        },
      },
    );
  };

  const onRegenerate = () => {
    if (!current) return;
    regenerate.mutate(current.generation_id, { onSuccess: setCurrent });
  };

  return (
    <div className="space-y-section">
      <PageHeader
        title="SQL generation"
        description="Turn a business question into candidate SQL for the selected schema. Generated SQL is untrusted: it is never executed here and must pass the SQL security guard first."
      />

      {options.isPending ? (
        <Card>
          <SkeletonText lines={4} />
        </Card>
      ) : options.isError ? (
        <Alert tone="error">{errorMessage(options.error, "Schemas could not be loaded.")}</Alert>
      ) : sources.length === 0 ? (
        <EmptyState
          icon={FileCode2}
          title="No schemas to generate SQL for"
          description="Register and import an active schema first. SQL is generated only against registered tables and columns."
        />
      ) : (
        <div className="space-y-section">
          <Card title="Ask a question" description="Interpreted and generated with your own data permissions. This uses two or more AI requests.">
            <form onSubmit={onSubmit} noValidate className="space-y-4">
              <FormField label="Schema" required>
                <Select value={selected} onChange={(event) => setSchemaId(event.target.value)}>
                  {sources.map((source) => (
                    <option key={source.id} value={source.id}>
                      {source.name} ({source.database_type}, version {source.schema_version})
                    </option>
                  ))}
                </Select>
              </FormField>
              <TextareaField
                label="Business question"
                required
                rows={3}
                maxLength={maxChars}
                value={question}
                placeholder="Top five regions by revenue last month"
                error={error ?? undefined}
                hint={`${question.length}/${maxChars} characters`}
                onChange={(event) => setQuestion(event.target.value)}
              />
              <div className="flex justify-end">
                <Button type="submit" loading={busy}>
                  {understand.isPending ? "Interpreting…" : generate.isPending ? "Generating SQL…" : "Generate SQL"}
                </Button>
              </div>
            </form>
          </Card>

          {understand.isError && (
            <Alert tone="error">{errorMessage(understand.error, "The question could not be interpreted.")}</Alert>
          )}
          {understanding && understanding.status !== "understood" && (
            <Alert
              tone={understanding.status === "needs_clarification" ? "warning" : "error"}
              title={understanding.status === "needs_clarification" ? "The question needs clarification" : "No SQL was generated"}
            >
              <p>{understanding.clarification.clarification_question ?? understanding.explanation}</p>
              <p className="mt-1 text-xs">SQL is generated only after the question is fully understood. Rephrase it and try again.</p>
            </Alert>
          )}
          {understanding && <QuestionSummaryCard understanding={understanding} generation={current} schemaName={selectedName} />}
          {generate.isPending && (
            <Card title="SQL generation result" aria-busy="true">
              <SkeletonText lines={5} />
            </Card>
          )}
          {generate.isError && <Alert tone="error">{errorMessage(generate.error, "SQL could not be generated.")}</Alert>}
          {current && (
            <GenerationResult
              result={current}
              onRegenerate={onRegenerate}
              regenerating={regenerate.isPending}
              regenerateError={regenerate.isError ? errorMessage(regenerate.error, "SQL could not be regenerated.") : null}
            />
          )}
        </div>
      )}
    </div>
  );
}

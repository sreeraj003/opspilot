import { MessageSquareText } from "lucide-react";
import { useState, type FormEvent } from "react";

import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { TextareaField } from "@/components/ui/Field";
import { FormField } from "@/components/ui/FormField";
import { Input, Select } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/Layout";
import { SkeletonText } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/toastContext";
import { usePermissions } from "@/features/permissions/permissionsContext";
import { usePreviewUnderstanding, useSetBusinessTimezone, useUnderstandingOptions } from "@/features/understanding/hooks";
import { InterpretationResult } from "@/features/understanding/IntentView";
import { useDocumentTitle } from "@/hooks/useDocumentTitle";
import type { UnderstandingOptions } from "@/types/understanding";
import { errorMessage } from "@/utils/errors";

const SOURCE_LABELS: Record<string, string> = {
  setting: "set by an administrator",
  environment: "from the server environment",
  default: "default",
};

function TimezoneCard({ options }: { options: UnderstandingOptions }) {
  const { hasPermission } = usePermissions();
  const canEdit = hasPermission("settings:manage");
  const toast = useToast();
  const save = useSetBusinessTimezone();
  const [value, setValue] = useState(options.timezone);

  return (
    <Card
      title="Business timezone"
      description="Relative dates such as “yesterday” or “last month” are resolved in this timezone, on the server."
    >
      <p className="text-sm text-fg">
        <span className="font-mono">{options.timezone}</span>{" "}
        <span className="text-fg-muted">({SOURCE_LABELS[options.timezone_source] ?? options.timezone_source})</span>
      </p>
      {canEdit && (
        <form
          className="mt-4 flex flex-col gap-2 sm:flex-row sm:items-end"
          onSubmit={(event) => {
            event.preventDefault();
            save.mutate(
              { key: options.timezone_setting_key, timezone: value.trim() },
              {
                onSuccess: () =>
                  toast.notify({
                    title: "Business timezone saved",
                    description: value.trim(),
                  }),
              },
            );
          }}
        >
          <FormField
            label="IANA timezone"
            className="flex-1"
            error={save.isError ? errorMessage(save.error, "The timezone could not be saved.") : undefined}
          >
            <Input value={value} onChange={(event) => setValue(event.target.value)} placeholder="Asia/Kolkata" className="font-mono" />
          </FormField>
          <Button type="submit" variant="secondary" loading={save.isPending} disabled={!value.trim() || value.trim() === options.timezone}>
            Save timezone
          </Button>
        </form>
      )}
    </Card>
  );
}

export function QuestionPreviewPage() {
  useDocumentTitle("Question preview");
  const options = useUnderstandingOptions();
  const preview = usePreviewUnderstanding();
  const [schemaId, setSchemaId] = useState("");
  const [question, setQuestion] = useState("");
  const [answered, setAnswered] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const sources = options.data?.sources ?? [];
  const selected = schemaId || sources[0]?.id || "";
  const maxChars = options.data?.max_question_chars ?? 1000;

  const run = (answeredKeys: string[]) => {
    preview.mutate({
      question: question.trim(),
      schema_source_id: selected,
      answered_clarifications: answeredKeys,
    });
  };

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    if (!selected) return setError("Choose a schema.");
    if (question.trim().length < 3) return setError("Enter a business question.");
    setError(null);
    // A new submission starts a new clarification sequence.
    setAnswered([]);
    run([]);
  };

  const skipClarification = () => {
    const key = preview.data?.clarification.key;
    if (!key) return;
    const next = [...answered, key];
    setAnswered(next);
    run(next);
  };

  return (
    <div className="space-y-section">
      <PageHeader
        title="Question preview"
        description="See how OpsPilot interprets a business question: the structured intent, how it was validated and what is still unclear. No SQL is generated or run."
      />

      {options.isPending ? (
        <Card>
          <SkeletonText lines={4} />
        </Card>
      ) : options.isError ? (
        <Alert tone="error">{errorMessage(options.error, "Preview options could not be loaded.")}</Alert>
      ) : sources.length === 0 ? (
        <EmptyState
          icon={MessageSquareText}
          title="No schemas to interpret against"
          description="Register and import an active schema first. Questions are interpreted only against registered tables and columns."
        />
      ) : (
        <div className="grid gap-section lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
          <div className="space-y-section">
            <Card title="Ask a question" description="Interpreted with your own data permissions. Each preview is one AI request.">
              <form onSubmit={onSubmit} noValidate className="space-y-4">
                <FormField label="Schema" required>
                  <Select
                    value={selected}
                    onChange={(event) => {
                      setSchemaId(event.target.value);
                      setAnswered([]);
                    }}
                  >
                    {sources.map((source) => (
                      <option key={source.id} value={source.id}>
                        {source.name} (version {source.schema_version})
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
                  placeholder="Show the top five products by revenue last month"
                  error={error ?? undefined}
                  hint={`${question.length}/${maxChars} characters`}
                  onChange={(event) => {
                    setQuestion(event.target.value);
                    setAnswered([]);
                  }}
                />
                <div className="flex justify-end">
                  <Button type="submit" loading={preview.isPending}>
                    Interpret question
                  </Button>
                </div>
              </form>
            </Card>

            {preview.isError && <Alert tone="error">{errorMessage(preview.error, "The question could not be interpreted.")}</Alert>}
            {preview.data && (
              <InterpretationResult result={preview.data} onSkipClarification={skipClarification} pending={preview.isPending} />
            )}
          </div>
          {options.data && <TimezoneCard key={options.data.timezone} options={options.data} />}
        </div>
      )}
    </div>
  );
}

import { X } from "lucide-react";
import { useState, type FormEvent } from "react";

import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Checkbox } from "@/components/ui/Checkbox";
import { Dialog, DialogActions } from "@/components/ui/Dialog";
import { TextField, TextareaField } from "@/components/ui/Field";
import { FormField } from "@/components/ui/FormField";
import { Select } from "@/components/ui/Input";
import { TagInput } from "@/features/rbac/components";
import { useSchemaList } from "@/features/schemas/hooks";
import { useCreateBusinessTerm, useSchemaFields, useUpdateBusinessTerm } from "@/features/understanding/hooks";
import { AGGREGATION_LABELS, KIND_LABELS } from "@/features/understanding/labels";
import { ApiError } from "@/services/apiClient";
import type { AggregationType, BusinessTerm, BusinessTermInput, TermKind } from "@/types/understanding";
import { errorMessage } from "@/utils/errors";

interface FormState {
  schema_source_id: string;
  name: string;
  description: string;
  kind: TermKind;
  synonyms: string[];
  related_tables: string[];
  related_columns: string[];
  default_aggregation: AggregationType | "";
  aggregation_guidance: string;
  is_active: boolean;
}

function initialState(term: BusinessTerm | null, schemaSourceId: string): FormState {
  return {
    schema_source_id: term?.schema_source_id ?? schemaSourceId,
    name: term?.name ?? "",
    description: term?.description ?? "",
    kind: term?.kind ?? "metric",
    synonyms: term?.synonyms ?? [],
    related_tables: term?.related_tables ?? [],
    related_columns: term?.related_columns ?? [],
    default_aggregation: term?.default_aggregation ?? "",
    aggregation_guidance: term?.aggregation_guidance ?? "",
    is_active: term?.is_active ?? true,
  };
}

type Errors = Partial<Record<"schema_source_id" | "name" | "description" | "fields", string>>;

function validate(state: FormState): Errors {
  const errors: Errors = {};
  if (!state.schema_source_id) errors.schema_source_id = "Choose the schema this term belongs to.";
  if (!state.name.trim()) errors.name = "Enter the business name.";
  if (!state.description.trim()) errors.description = "Describe what the term means in your business.";
  if (state.related_tables.length === 0 && state.related_columns.length === 0)
    errors.fields = "Associate at least one table or column, so the term never needs a guessed definition.";
  return errors;
}

/** Messages from a 422 with per-field details (unknown tables or columns, conflicts). */
function detailMessages(error: unknown): string[] {
  if (!(error instanceof ApiError) || !Array.isArray(error.details)) return [];
  return error.details
    .map((detail) => (detail && typeof detail === "object" && "message" in detail ? String(detail.message) : null))
    .filter((message): message is string => Boolean(message));
}

/** Choose tables and columns from the registered schema; nothing outside it can be added. */
function FieldPicker({
  schemaSourceId,
  tables,
  columns,
  onChange,
  error,
}: {
  schemaSourceId: string;
  tables: string[];
  columns: string[];
  onChange: (tables: string[], columns: string[]) => void;
  error?: string;
}) {
  const fields = useSchemaFields(schemaSourceId || null);
  const [table, setTable] = useState("");
  const current = fields.data?.tables.find((t) => t.table === table);
  const toggleColumn = (qualified: string, checked: boolean) =>
    onChange(tables, checked ? [...columns, qualified] : columns.filter((c) => c !== qualified));
  const chips = [
    ...tables.map((t) => ({ value: t, kind: "table" as const })),
    ...columns.map((c) => ({ value: c, kind: "column" as const })),
  ];

  return (
    <fieldset className="space-y-3">
      <legend className="text-sm font-semibold text-fg">Schema fields</legend>
      <p className="text-xs text-fg-muted">The tables and columns that define this term. Only registered fields can be chosen.</p>
      {chips.length > 0 && (
        <ul className="flex flex-wrap gap-1.5" aria-label="Associated fields">
          {chips.map((chip) => (
            <li
              key={chip.value}
              className="inline-flex items-center gap-1 rounded-sm bg-surface-strong py-0.5 pr-1 pl-2 font-mono text-xs text-fg"
            >
              {chip.value}
              <button
                type="button"
                aria-label={`Remove ${chip.value}`}
                onClick={() =>
                  chip.kind === "table"
                    ? onChange(
                        tables.filter((t) => t !== chip.value),
                        columns,
                      )
                    : onChange(
                        tables,
                        columns.filter((c) => c !== chip.value),
                      )
                }
                className="rounded-xs p-0.5 text-fg-muted hover:bg-line-strong hover:text-fg"
              >
                <X aria-hidden="true" className="h-3 w-3" />
              </button>
            </li>
          ))}
        </ul>
      )}
      {!schemaSourceId ? (
        <p className="text-sm text-fg-muted">Choose a schema first.</p>
      ) : fields.isError ? (
        <Alert tone="error">{errorMessage(fields.error, "The schema's fields could not be loaded.")}</Alert>
      ) : (
        <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
          <FormField label="Table" className="flex-1">
            <Select value={table} onChange={(event) => setTable(event.target.value)} disabled={fields.isPending}>
              <option value="">{fields.isPending ? "Loading tables…" : "Choose a table"}</option>
              {fields.data?.tables.map((t) => (
                <option key={t.table} value={t.table}>
                  {t.table}
                </option>
              ))}
            </Select>
          </FormField>
          <Button variant="secondary" disabled={!table || tables.includes(table)} onClick={() => onChange([...tables, table], columns)}>
            Add whole table
          </Button>
        </div>
      )}
      {current && (
        <div
          className="max-h-56 space-y-2 overflow-y-auto rounded-control border border-line p-3"
          role="group"
          aria-label={`Columns of ${current.table}`}
        >
          {current.columns.map((column) => {
            const qualified = `${current.table}.${column.name}`;
            return (
              <Checkbox
                key={qualified}
                label={column.name}
                description={[column.data_type, column.sensitive ? "sensitive" : null, column.description].filter(Boolean).join(" · ")}
                checked={columns.includes(qualified)}
                onChange={(event) => toggleColumn(qualified, event.target.checked)}
              />
            );
          })}
        </div>
      )}
      {error && <p className="text-xs font-medium text-danger-text">{error}</p>}
    </fieldset>
  );
}

/** Mount it per opening (keyed by term) so the form starts from the term's current values. */
export function TermFormDialog({
  open,
  term,
  defaultSchemaId = "",
  onClose,
  onSaved,
}: {
  open: boolean;
  term?: BusinessTerm | null;
  defaultSchemaId?: string;
  onClose: () => void;
  onSaved: (term: BusinessTerm) => void;
}) {
  const create = useCreateBusinessTerm();
  const update = useUpdateBusinessTerm();
  const mutation = term ? update : create;
  const schemas = useSchemaList({
    offset: 0,
    limit: 100,
    sort: "name",
    order: "asc",
  });
  const [state, setState] = useState<FormState>(() => initialState(term ?? null, defaultSchemaId));
  const [errors, setErrors] = useState<Errors>({});

  const set = <K extends keyof FormState>(key: K, value: FormState[K]) => setState((s) => ({ ...s, [key]: value }));
  const close = () => {
    create.reset();
    update.reset();
    onClose();
  };

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    const found = validate(state);
    setErrors(found);
    if (Object.keys(found).length > 0) return;
    const input: BusinessTermInput = {
      schema_source_id: state.schema_source_id,
      name: state.name.trim(),
      description: state.description.trim(),
      kind: state.kind,
      synonyms: state.synonyms,
      related_tables: state.related_tables,
      related_columns: state.related_columns,
      default_aggregation: state.default_aggregation || null,
      aggregation_guidance: state.aggregation_guidance.trim() || null,
      is_active: state.is_active,
    };
    try {
      if (term) {
        const { schema_source_id: _ignored, ...changes } = input;
        void _ignored;
        onSaved(await update.mutateAsync({ id: term.id, input: changes }));
      } else {
        onSaved(await create.mutateAsync(input));
      }
    } catch {
      // Shown from the mutation state.
    }
  };

  const details = detailMessages(mutation.error);

  return (
    <Dialog
      open={open}
      onClose={close}
      size="lg"
      title={term ? "Edit business term" : "New business term"}
      description="A registered definition the AI must use. It describes business meaning only, never SQL."
    >
      <form onSubmit={onSubmit} noValidate className="space-y-5">
        {mutation.isError && (
          <Alert tone="error" title={errorMessage(mutation.error, "The term could not be saved.")}>
            {details.length > 0 && (
              <ul className="list-disc pl-4">
                {details.map((message) => (
                  <li key={message}>{message}</li>
                ))}
              </ul>
            )}
          </Alert>
        )}
        <fieldset className="space-y-4">
          <legend className="text-sm font-semibold text-fg">Definition</legend>
          <FormField
            label="Schema"
            required
            error={errors.schema_source_id}
            description={term ? "A term's schema cannot change. Delete it and create a new one instead." : undefined}
          >
            <Select
              value={state.schema_source_id}
              disabled={Boolean(term)}
              onChange={(event) =>
                setState((s) => ({
                  ...s,
                  schema_source_id: event.target.value,
                  related_tables: [],
                  related_columns: [],
                }))
              }
            >
              <option value="">Choose a schema</option>
              {term && !schemas.data?.items.some((s) => s.id === term.schema_source_id) && (
                <option value={term.schema_source_id}>{term.schema_source_name ?? "Current schema"}</option>
              )}
              {schemas.data?.items.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </Select>
          </FormField>
          <TextField label="Business name" required value={state.name} error={errors.name} onChange={(e) => set("name", e.target.value)} />
          <TextareaField
            label="Description"
            required
            rows={2}
            value={state.description}
            error={errors.description}
            onChange={(e) => set("description", e.target.value)}
          />
          <FormField label="Kind">
            <Select value={state.kind} onChange={(e) => set("kind", e.target.value as TermKind)}>
              {Object.entries(KIND_LABELS).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </Select>
          </FormField>
          <TagInput
            label="Synonyms"
            values={state.synonyms}
            onChange={(values) => set("synonyms", values)}
            placeholder="turnover, sales"
            hint="Other words people use for this term. A synonym shared by two terms makes the AI ask which one is meant."
          />
        </fieldset>

        <FieldPicker
          schemaSourceId={state.schema_source_id}
          tables={state.related_tables}
          columns={state.related_columns}
          error={errors.fields}
          onChange={(tables, columns) =>
            setState((s) => ({
              ...s,
              related_tables: tables,
              related_columns: columns,
            }))
          }
        />

        <fieldset className="space-y-4">
          <legend className="text-sm font-semibold text-fg">Aggregation</legend>
          <FormField label="Default aggregation" description="Used whenever a question asks for this metric.">
            <Select value={state.default_aggregation} onChange={(e) => set("default_aggregation", e.target.value as AggregationType | "")}>
              <option value="">No default</option>
              {Object.entries(AGGREGATION_LABELS).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </Select>
          </FormField>
          <TextareaField
            label="Aggregation guidance (optional)"
            rows={2}
            value={state.aggregation_guidance}
            hint="Plain language, for example: sum of order totals excluding cancelled orders. SQL is refused."
            onChange={(e) => set("aggregation_guidance", e.target.value)}
          />
          <Checkbox
            label="Active"
            description="Inactive terms are kept but never sent to the AI."
            checked={state.is_active}
            onChange={(e) => set("is_active", e.target.checked)}
          />
        </fieldset>

        <DialogActions>
          <Button variant="secondary" onClick={close}>
            Cancel
          </Button>
          <Button type="submit" loading={mutation.isPending}>
            {term ? "Save changes" : "Create term"}
          </Button>
        </DialogActions>
      </form>
    </Dialog>
  );
}

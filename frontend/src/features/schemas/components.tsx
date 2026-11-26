import { zodResolver } from "@hookform/resolvers/zod";
import { useEffect } from "react";
import { useForm, useWatch } from "react-hook-form";
import { z } from "zod";

import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Checkbox } from "@/components/ui/Checkbox";
import { Dialog, DialogActions } from "@/components/ui/Dialog";
import { TextareaField, TextField } from "@/components/ui/Field";
import { FormField } from "@/components/ui/FormField";
import { Select } from "@/components/ui/Input";
import { useToast } from "@/components/ui/toastContext";
import { useCreateSchema, useSaveColumn, useSaveTable, useUpdateSchema } from "@/features/schemas/hooks";
import {
  COMMON_DATA_TYPES,
  DATABASE_TYPE_LABELS,
  dataTypeError,
  IDENTIFIER_MESSAGE,
  IDENTIFIER_PATTERN,
  issueLocation,
  issuesFromError,
} from "@/features/schemas/validation";
import { ApiError } from "@/services/apiClient";
import type { DatabaseType, OutlineColumn, SchemaColumn, SchemaSource, SchemaTable, ValidationIssue } from "@/types/schemaRegistry";
import { errorMessage } from "@/utils/errors";

// --- indicators ----------------------------------------------------------------------------------

/** Primary-key, foreign-key, sensitive and inactive markers for a column. */
export function ColumnFlags({
  column,
}: {
  column: Pick<SchemaColumn | OutlineColumn, "is_primary_key" | "is_foreign_key" | "is_sensitive" | "is_active">;
}) {
  return (
    <span className="inline-flex flex-wrap gap-1">
      {column.is_primary_key && (
        <Badge tone="accent" title="Primary key">
          PK
        </Badge>
      )}
      {column.is_foreign_key && (
        <Badge tone="info" title="Foreign key">
          FK
        </Badge>
      )}
      {column.is_sensitive && (
        <Badge tone="warning" title="Sensitive data: excluded from AI context by default">
          Sensitive
        </Badge>
      )}
      {!column.is_active && <Badge title="Ignored until activated">Inactive</Badge>}
    </span>
  );
}

export function DatabaseTypeLabel({ type, dialect }: { type: DatabaseType; dialect?: string }) {
  const label = DATABASE_TYPE_LABELS[type] ?? type;
  return (
    <span>
      {label}
      {dialect && dialect !== type && <span className="ml-1 font-mono text-xs text-fg-muted">({dialect})</span>}
    </span>
  );
}

/** Structured validation issues from the backend or from a preview. */
export function IssueList({ issues, max = 50 }: { issues: ValidationIssue[]; max?: number }) {
  if (issues.length === 0) return null;
  return (
    <ul className="space-y-1.5 text-sm" aria-label="Validation issues">
      {issues.slice(0, max).map((issue, index) => (
        <li key={`${issue.code}-${index}`} className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
          <Badge tone={issue.severity === "error" ? "danger" : "warning"}>{issue.severity === "error" ? "Error" : "Warning"}</Badge>
          <span className="text-fg">{issue.message}</span>
          {issue.location.length > 0 && <span className="font-mono text-xs text-fg-muted">{issueLocation(issue)}</span>}
        </li>
      ))}
      {issues.length > max && <li className="text-fg-muted">and {issues.length - max} more</li>}
    </ul>
  );
}

/** The server's message for a failed save, with its validation issues when it sent any. */
export function MutationError({ error, fallback }: { error: unknown; fallback: string }) {
  const issues = error instanceof ApiError ? issuesFromError(error.details) : [];
  return (
    <Alert tone="error">
      <p>{errorMessage(error, fallback)}</p>
      {issues.length > 1 && (
        <div className="mt-2">
          <IssueList issues={issues} />
        </div>
      )}
    </Alert>
  );
}

const optionalText = (max: number) => z.string().trim().max(max, `Use at most ${max} characters.`);
const identifier = z.string().trim().min(1, "Required.").regex(IDENTIFIER_PATTERN, IDENTIFIER_MESSAGE);
const blankToNull = (value: string) => (value.trim() ? value.trim() : null);

// --- schema source form ------------------------------------------------------------------------------

const schemaSchema = z.object({
  name: z.string().trim().min(1, "Enter a name.").max(150, "Use at most 150 characters."),
  description: optionalText(4000),
  database_type: z.enum(["postgresql", "mysql", "mariadb", "sqlserver", "oracle", "sqlite", "other"]),
  dialect: z
    .string()
    .trim()
    .max(64, "Use at most 64 characters.")
    .regex(/^([a-z0-9][a-z0-9_.+ -]*)?$/, "Use lowercase letters, digits, dots, dashes or underscores."),
  is_active: z.boolean(),
});
type SchemaValues = z.infer<typeof schemaSchema>;

export function SchemaFormDialog({
  open,
  schema,
  onClose,
  onSaved,
}: {
  open: boolean;
  schema?: SchemaSource | null;
  onClose: () => void;
  onSaved: (schema: SchemaSource) => void;
}) {
  const create = useCreateSchema();
  const update = useUpdateSchema();
  const mutation = schema ? update : create;
  const defaults: SchemaValues = {
    name: schema?.name ?? "",
    description: schema?.description ?? "",
    database_type: schema?.database_type ?? "postgresql",
    dialect: schema && schema.dialect !== schema.database_type ? schema.dialect : "",
    is_active: schema?.is_active ?? true,
  };
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<SchemaValues>({
    resolver: zodResolver(schemaSchema),
    defaultValues: defaults,
  });
  useEffect(() => {
    if (open) reset(defaults);
    // Reset only when the dialog opens or switches to another schema.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, schema?.id]);

  const close = () => {
    create.reset();
    update.reset();
    onClose();
  };

  const onSubmit = handleSubmit(async (values) => {
    const input = {
      name: values.name,
      description: blankToNull(values.description),
      database_type: values.database_type,
      dialect: blankToNull(values.dialect) ?? values.database_type,
      is_active: values.is_active,
    };
    try {
      const saved = schema ? await update.mutateAsync({ id: schema.id, input }) : await create.mutateAsync(input);
      onSaved(saved);
    } catch {
      // Shown from the mutation state.
    }
  });

  return (
    <Dialog
      open={open}
      onClose={close}
      title={schema ? "Edit schema" : "Register schema"}
      description="Describe an external database schema. Only metadata is stored: never connection details or credentials."
    >
      <form onSubmit={onSubmit} noValidate className="space-y-4">
        {mutation.isError && <MutationError error={mutation.error} fallback="The schema could not be saved." />}
        <TextField label="Name" error={errors.name?.message} {...register("name")} />
        <FormField label="Database type" error={errors.database_type?.message}>
          <Select {...register("database_type")}>
            {Object.entries(DATABASE_TYPE_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </Select>
        </FormField>
        <TextField
          label="Dialect (optional)"
          className="font-mono"
          hint="A more specific SQL dialect, such as postgresql-16 or tsql. Defaults to the database type."
          error={errors.dialect?.message}
          {...register("dialect")}
        />
        <TextareaField label="Description (optional)" rows={3} error={errors.description?.message} {...register("description")} />
        {schema && (
          <Checkbox
            label="Active"
            description="Inactive schemas stay registered but are not offered to the AI."
            {...register("is_active")}
          />
        )}
        <DialogActions>
          <Button variant="secondary" onClick={close}>
            Cancel
          </Button>
          <Button type="submit" loading={isSubmitting}>
            {schema ? "Save changes" : "Register schema"}
          </Button>
        </DialogActions>
      </form>
    </Dialog>
  );
}

// --- table form ------------------------------------------------------------------------------------------

const tableSchema = z.object({
  schema_name: identifier,
  table_name: identifier,
  description: optionalText(4000),
  business_purpose: optionalText(4000),
  is_active: z.boolean(),
});
type TableValues = z.infer<typeof tableSchema>;

export function TableFormDialog({
  open,
  schemaId,
  table,
  defaultNamespace = "public",
  onClose,
}: {
  open: boolean;
  schemaId: string;
  table?: SchemaTable | null;
  defaultNamespace?: string;
  onClose: () => void;
}) {
  const save = useSaveTable(schemaId);
  const toast = useToast();
  const defaults: TableValues = {
    schema_name: table?.schema_name ?? defaultNamespace,
    table_name: table?.table_name ?? "",
    description: table?.description ?? "",
    business_purpose: table?.business_purpose ?? "",
    is_active: table?.is_active ?? true,
  };
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<TableValues>({
    resolver: zodResolver(tableSchema),
    defaultValues: defaults,
  });
  useEffect(() => {
    if (open) reset(defaults);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, table?.id]);

  const close = () => {
    save.reset();
    onClose();
  };
  const onSubmit = handleSubmit(async (values) => {
    try {
      const saved = await save.mutateAsync({
        id: table?.id,
        input: {
          schema_name: values.schema_name,
          table_name: values.table_name,
          description: blankToNull(values.description),
          business_purpose: blankToNull(values.business_purpose),
          is_active: values.is_active,
        },
      });
      toast.notify({
        title: table ? "Table updated" : "Table added",
        description: saved.qualified_name,
      });
      close();
    } catch {
      // Shown from the mutation state.
    }
  });

  return (
    <Dialog open={open} onClose={close} title={table ? "Edit table" : "Add table"} description="Names must match the external database.">
      <form onSubmit={onSubmit} noValidate className="space-y-4">
        {save.isError && <MutationError error={save.error} fallback="The table could not be saved." />}
        <div className="grid gap-4 sm:grid-cols-2">
          <TextField label="Schema namespace" className="font-mono" error={errors.schema_name?.message} {...register("schema_name")} />
          <TextField label="Table name" className="font-mono" error={errors.table_name?.message} {...register("table_name")} />
        </div>
        <TextareaField label="Description (optional)" rows={2} error={errors.description?.message} {...register("description")} />
        <TextareaField
          label="Business purpose (optional)"
          rows={2}
          hint="What the business uses this table for, in plain words."
          error={errors.business_purpose?.message}
          {...register("business_purpose")}
        />
        <Checkbox
          label="Active"
          description="Inactive tables are kept but left out of the AI's schema context."
          {...register("is_active")}
        />
        <DialogActions>
          <Button variant="secondary" onClick={close}>
            Cancel
          </Button>
          <Button type="submit" loading={isSubmitting}>
            {table ? "Save changes" : "Add table"}
          </Button>
        </DialogActions>
      </form>
    </Dialog>
  );
}

// --- column form -------------------------------------------------------------------------------------------

const columnSchema = z
  .object({
    column_name: identifier,
    data_type: z
      .string()
      .trim()
      .min(1, "Enter a data type.")
      .superRefine((value, context) => {
        const problem = dataTypeError(value);
        if (problem) context.addIssue({ code: "custom", message: problem });
      }),
    description: optionalText(4000),
    business_meaning: optionalText(4000),
    is_primary_key: z.boolean(),
    is_foreign_key: z.boolean(),
    is_nullable: z.boolean(),
    is_sensitive: z.boolean(),
    is_active: z.boolean(),
  })
  .refine((values) => !(values.is_primary_key && values.is_nullable), {
    path: ["is_nullable"],
    message: "A primary-key column cannot be nullable.",
  });
type ColumnValues = z.infer<typeof columnSchema>;

export function ColumnFormDialog({
  open,
  schemaId,
  tableId,
  tableName,
  column,
  onClose,
}: {
  open: boolean;
  schemaId: string;
  tableId: string;
  tableName: string;
  column?: SchemaColumn | null;
  onClose: () => void;
}) {
  const save = useSaveColumn(schemaId, tableId);
  const toast = useToast();
  const defaults: ColumnValues = {
    column_name: column?.column_name ?? "",
    data_type: column?.data_type ?? "",
    description: column?.description ?? "",
    business_meaning: column?.business_meaning ?? "",
    is_primary_key: column?.is_primary_key ?? false,
    is_foreign_key: column?.is_foreign_key ?? false,
    is_nullable: column?.is_nullable ?? true,
    is_sensitive: column?.is_sensitive ?? false,
    is_active: column?.is_active ?? true,
  };
  const {
    register,
    handleSubmit,
    reset,
    control,
    setValue,
    formState: { errors, isSubmitting },
  } = useForm<ColumnValues>({
    resolver: zodResolver(columnSchema),
    defaultValues: defaults,
  });
  useEffect(() => {
    if (open) reset(defaults);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, column?.id]);
  const isPrimaryKey = useWatch({ control, name: "is_primary_key" });

  const close = () => {
    save.reset();
    onClose();
  };
  const onSubmit = handleSubmit(async (values) => {
    try {
      const saved = await save.mutateAsync({
        id: column?.id,
        input: {
          ...values,
          description: blankToNull(values.description),
          business_meaning: blankToNull(values.business_meaning),
        },
      });
      toast.notify({
        title: column ? "Column updated" : "Column added",
        description: `${tableName}.${saved.column_name}`,
      });
      close();
    } catch {
      // Shown from the mutation state.
    }
  });

  const primaryKey = register("is_primary_key");
  const listId = `data-types-${tableId}`;
  return (
    <Dialog open={open} onClose={close} title={column ? "Edit column" : "Add column"} description={`Table ${tableName}`} size="lg">
      <form onSubmit={onSubmit} noValidate className="space-y-4">
        {save.isError && <MutationError error={save.error} fallback="The column could not be saved." />}
        <div className="grid gap-4 sm:grid-cols-2">
          <TextField label="Column name" className="font-mono" error={errors.column_name?.message} {...register("column_name")} />
          <TextField
            label="Data type"
            className="font-mono"
            list={listId}
            hint="As declared in the database, e.g. varchar(255)."
            error={errors.data_type?.message}
            {...register("data_type")}
          />
          <datalist id={listId}>
            {COMMON_DATA_TYPES.map((type) => (
              <option key={type} value={type} />
            ))}
          </datalist>
        </div>
        <TextareaField label="Description (optional)" rows={2} error={errors.description?.message} {...register("description")} />
        <TextareaField
          label="Business meaning (optional)"
          rows={2}
          hint="What the value means to the business, e.g. “order value including tax”."
          error={errors.business_meaning?.message}
          {...register("business_meaning")}
        />
        <fieldset className="grid gap-3 sm:grid-cols-2">
          <legend className="sr-only">Column properties</legend>
          <Checkbox
            label="Primary key"
            {...primaryKey}
            onChange={(event) => {
              void primaryKey.onChange(event);
              if (event.target.checked) setValue("is_nullable", false);
            }}
          />
          <Checkbox label="Foreign key" description="Describe the reference under Relationships." {...register("is_foreign_key")} />
          <div>
            <Checkbox
              label="Nullable"
              description={isPrimaryKey ? "Primary keys are never nullable." : undefined}
              {...register("is_nullable")}
            />
            {errors.is_nullable && <p className="mt-1 text-xs text-danger-text">{errors.is_nullable.message}</p>}
          </div>
          <Checkbox
            label="Sensitive"
            description="Personal or confidential data. Left out of AI context by default."
            {...register("is_sensitive")}
          />
          <Checkbox label="Active" {...register("is_active")} />
        </fieldset>
        <DialogActions>
          <Button variant="secondary" onClick={close}>
            Cancel
          </Button>
          <Button type="submit" loading={isSubmitting}>
            {column ? "Save changes" : "Add column"}
          </Button>
        </DialogActions>
      </form>
    </Dialog>
  );
}

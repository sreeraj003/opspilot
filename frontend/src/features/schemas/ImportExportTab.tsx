import { CheckCircle2, Download, FileJson, Upload } from "lucide-react";
import { useId, useRef, useState } from "react";

import { Alert } from "@/components/ui/Alert";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { ConfirmDialog } from "@/components/ui/Dialog";
import { FormField } from "@/components/ui/FormField";
import { Select } from "@/components/ui/Input";
import { useToast } from "@/components/ui/toastContext";
import { usePermissions } from "@/features/permissions/permissionsContext";
import { IssueList, MutationError } from "@/features/schemas/components";
import { useExportSchema, useImportSchema } from "@/features/schemas/hooks";
import { importRequest, MAX_IMPORT_FILE_BYTES, parseImportText } from "@/features/schemas/validation";
import type { ImportCount, ImportStrategy, SchemaImportResult, SchemaSource } from "@/types/schemaRegistry";
import { errorMessage } from "@/utils/errors";

const STRATEGIES: Record<ImportStrategy, { label: string; description: string }> = {
  create_only: {
    label: "Add new items only",
    description: "Adds new tables with their columns, new relationships and new glossary terms. If the file lists anything already registered, nothing is imported.",
  },
  merge: {
    label: "Merge",
    description: "Adds new items and updates the fields the file sets on existing ones. Nothing is removed.",
  },
  replace: {
    label: "Replace",
    description: "Makes the registry match the file: fields the file leaves out are reset and items it does not list are deleted.",
  },
};

interface LoadedFile {
  name: string;
  document: Record<string, unknown>;
  tables: number;
  columns: number;
  relationships: number;
}

function CountRow({ label, count }: { label: string; count: ImportCount }) {
  return (
    <tr className="border-t border-line-subtle">
      <th scope="row" className="py-1.5 pr-4 text-left font-medium text-fg">
        {label}
      </th>
      <td className="py-1.5 pr-4 text-right tabular-nums">{count.created}</td>
      <td className="py-1.5 pr-4 text-right tabular-nums">{count.updated}</td>
      <td className="py-1.5 pr-4 text-right tabular-nums">{count.unchanged}</td>
      <td className="py-1.5 text-right tabular-nums">{count.removed}</td>
    </tr>
  );
}

/** What an import would change (dry run) or did change. */
export function ImportResultSummary({ result }: { result: SchemaImportResult }) {
  const { counts } = result;
  return (
    <div className="space-y-4">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <caption className="sr-only">{result.applied ? "Imported changes" : "Changes the import would make"}</caption>
          <thead>
            <tr className="text-xs text-fg-muted">
              <th scope="col" className="pb-1 text-left font-medium">
                Item
              </th>
              <th scope="col" className="pr-4 pb-1 text-right font-medium">
                Created
              </th>
              <th scope="col" className="pr-4 pb-1 text-right font-medium">
                Updated
              </th>
              <th scope="col" className="pr-4 pb-1 text-right font-medium">
                Unchanged
              </th>
              <th scope="col" className="pb-1 text-right font-medium">
                Removed
              </th>
            </tr>
          </thead>
          <tbody>
            <CountRow label="Tables" count={counts.tables} />
            <CountRow label="Columns" count={counts.columns} />
            <CountRow label="Relationships" count={counts.relationships} />
            <CountRow label="Glossary terms" count={counts.glossary_terms} />
          </tbody>
        </table>
      </div>
      {result.changes.length > 0 && (
        <details className="text-sm">
          <summary className="cursor-pointer font-medium text-fg">
            {result.changes.length}
            {result.changes_truncated ? "+" : ""} individual change(s)
          </summary>
          <ul className="mt-2 max-h-64 space-y-1 overflow-y-auto">
            {result.changes.map((change, index) => (
              <li key={index} className="flex flex-wrap items-baseline gap-2">
                <Badge tone={change.action === "remove" ? "danger" : change.action === "create" ? "success" : "info"}>
                  {change.action}
                </Badge>
                <span className="text-fg-muted">{change.entity_type.replace("_", " ")}</span>
                <span className="font-mono text-xs text-fg">{change.name}</span>
                {change.fields.length > 0 && <span className="text-xs text-fg-muted">{change.fields.join(", ")}</span>}
              </li>
            ))}
          </ul>
        </details>
      )}
      {result.warnings.length > 0 && (
        <div className="space-y-2">
          <p className="text-sm font-medium text-fg">Warnings (these do not block the import)</p>
          <IssueList issues={result.warnings} max={20} />
        </div>
      )}
    </div>
  );
}

function ImportCard({ schema }: { schema: SchemaSource }) {
  const inputId = useId();
  const fileInput = useRef<HTMLInputElement>(null);
  const toast = useToast();
  const [file, setFile] = useState<LoadedFile | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [strategy, setStrategy] = useState<ImportStrategy>("merge");
  const [preview, setPreview] = useState<SchemaImportResult | null>(null);
  const [done, setDone] = useState<SchemaImportResult | null>(null);
  const [confirming, setConfirming] = useState(false);
  const validate = useImportSchema(schema.id);
  const apply = useImportSchema(schema.id);

  const resetResults = () => {
    setPreview(null);
    setDone(null);
    validate.reset();
    apply.reset();
  };

  const onFile = async (selected: File | undefined) => {
    resetResults();
    setFile(null);
    setFileError(null);
    if (!selected) return;
    // Check the size before reading the file into memory.
    const parsed = parseImportText(selected.size > MAX_IMPORT_FILE_BYTES ? "" : await selected.text(), selected.size);
    if (!parsed.ok) {
      setFileError(parsed.error);
      return;
    }
    setFile({ name: selected.name, ...parsed });
  };

  const runPreview = () => {
    if (!file) return;
    resetResults();
    validate.mutate(importRequest(file.document, strategy, true), {
      onSuccess: setPreview,
    });
  };

  const runImport = () => {
    if (!file || !preview) return;
    apply.mutate(importRequest(file.document, strategy, false, preview.schema_version_before), {
      onSuccess: (result) => {
        setConfirming(false);
        setDone(result);
        setPreview(null);
        toast.notify({
          title: "Import complete",
          description: `Schema version ${result.schema_version_after}`,
        });
      },
      onError: () => setConfirming(false),
    });
  };

  const removes = preview ? Object.values(preview.counts).reduce((sum, count) => sum + count.removed, 0) : 0;

  return (
    <Card
      title="Import from JSON"
      description="Validate a file first to see exactly what would change. Nothing is saved until you confirm. Imported text is stored as metadata only and never run."
    >
      <div className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <FormField label="JSON file" id={inputId} description="Up to 5 MB, in the schema import format.">
            <input
              ref={fileInput}
              id={inputId}
              type="file"
              accept="application/json,.json"
              className="block w-full text-sm text-fg-secondary file:mr-3 file:rounded-control file:border file:border-line file:bg-surface-muted file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-fg"
              onChange={(event) => void onFile(event.target.files?.[0])}
            />
          </FormField>
          <FormField label="Import strategy" description={STRATEGIES[strategy].description}>
            <Select
              value={strategy}
              onChange={(event) => {
                setStrategy(event.target.value as ImportStrategy);
                resetResults();
              }}
            >
              {(Object.keys(STRATEGIES) as ImportStrategy[]).map((key) => (
                <option key={key} value={key}>
                  {STRATEGIES[key].label}
                </option>
              ))}
            </Select>
          </FormField>
        </div>

        {fileError && <Alert tone="error">{fileError}</Alert>}
        {file && (
          <p className="flex flex-wrap items-center gap-2 text-sm text-fg-secondary">
            <FileJson aria-hidden="true" className="h-4 w-4 text-fg-muted" />
            <span className="font-medium text-fg">{file.name}</span>
            <span>
              {file.tables} table(s), {file.columns} column(s), {file.relationships} relationship(s)
            </span>
          </p>
        )}

        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" disabled={!file} loading={validate.isPending} onClick={runPreview}>
            Validate and preview
          </Button>
          <Button
            disabled={!preview || !preview.valid}
            loading={apply.isPending}
            variant={strategy === "replace" ? "danger" : "primary"}
            onClick={() => setConfirming(true)}
          >
            <Upload aria-hidden="true" className="h-4 w-4" /> Import
          </Button>
        </div>

        {validate.isError && <MutationError error={validate.error} fallback="The file could not be validated." />}
        {apply.isError && <MutationError error={apply.error} fallback="The import failed. Nothing was changed." />}

        {preview && (
          <section aria-label="Import preview" className="space-y-3 rounded-md border border-line p-4">
            {preview.valid ? (
              <Alert tone={removes > 0 ? "warning" : "info"} title="The file is valid">
                {removes > 0
                  ? `Importing will remove ${removes} registered item(s). Review the changes below.`
                  : "Review what will change, then import."}
              </Alert>
            ) : (
              <Alert tone="error" title="The file cannot be imported">
                Fix these {preview.errors.length} problem(s) and choose the file again. Nothing was changed.
              </Alert>
            )}
            {preview.errors.length > 0 && <IssueList issues={preview.errors} max={50} />}
            <ImportResultSummary result={preview} />
          </section>
        )}

        {done && (
          <section aria-label="Import result" className="space-y-3 rounded-md border border-success-line p-4">
            <p className="flex items-center gap-2 text-sm font-medium text-success-text">
              <CheckCircle2 aria-hidden="true" className="h-4 w-4" />
              {done.version_incremented
                ? `Imported. The schema is now at version ${done.schema_version_after}.`
                : "Imported. The file matched the registry, so nothing changed."}
            </p>
            <ImportResultSummary result={done} />
          </section>
        )}
      </div>

      <ConfirmDialog
        open={confirming}
        title={strategy === "replace" ? "Replace registered metadata?" : "Import this file?"}
        tone={strategy === "replace" ? "danger" : "primary"}
        description={
          strategy === "replace"
            ? `The registry will be made to match ${file?.name ?? "the file"}. ${removes} item(s) will be removed. This creates a new schema version; history is kept.`
            : `The changes from ${file?.name ?? "the file"} are saved as a new schema version.`
        }
        confirmLabel={strategy === "replace" ? "Replace" : "Import"}
        pending={apply.isPending}
        onCancel={() => setConfirming(false)}
        onConfirm={runImport}
      />
    </Card>
  );
}

function ExportCard({ schema }: { schema: SchemaSource }) {
  const exporter = useExportSchema(schema.id);
  const download = () =>
    exporter.mutate(undefined, {
      onSuccess: (document) => {
        const blob = new Blob([JSON.stringify(document, null, 2)], {
          type: "application/json",
        });
        const url = URL.createObjectURL(blob);
        const link = window.document.createElement("a");
        const safeName = schema.name.replace(/[^A-Za-z0-9_-]+/g, "-").replace(/^-|-$/g, "") || "schema";
        link.href = url;
        link.download = `${safeName}-v${schema.schema_version}.json`;
        link.click();
        URL.revokeObjectURL(url);
      },
    });
  return (
    <Card
      title="Export as JSON"
      description="Downloads the registered tables, columns, relationships and glossary in the import format, so it can be edited and imported again."
    >
      <div className="space-y-3">
        {exporter.isError && <Alert tone="error">{errorMessage(exporter.error, "The export failed.")}</Alert>}
        <Button variant="secondary" loading={exporter.isPending} onClick={download}>
          <Download aria-hidden="true" className="h-4 w-4" /> Download JSON
        </Button>
      </div>
    </Card>
  );
}

export function ImportExportTab({ schema }: { schema: SchemaSource }) {
  const { hasPermission } = usePermissions();
  const canImport = hasPermission("schema.import");
  const canExport = hasPermission("schema.export");
  if (!canImport && !canExport) {
    return <Alert tone="info">You can view this schema but not import or export it.</Alert>;
  }
  return (
    <div className="space-y-section">
      {canImport && <ImportCard schema={schema} />}
      {canExport && <ExportCard schema={schema} />}
    </div>
  );
}

import { ArrowRight, GitFork, Plus } from "lucide-react";
import { useMemo, useState } from "react";

import { Button } from "@/components/ui/Button";
import { DataTable, type DataTableColumn } from "@/components/ui/DataTable";
import { ConfirmDialog, Dialog, DialogActions } from "@/components/ui/Dialog";
import { TextareaField } from "@/components/ui/Field";
import { FormField } from "@/components/ui/FormField";
import { Select } from "@/components/ui/Input";
import { Checkbox } from "@/components/ui/Checkbox";
import { Alert } from "@/components/ui/Alert";
import { useToast } from "@/components/ui/toastContext";
import { usePermissions } from "@/features/permissions/permissionsContext";
import { ActiveBadge, Pagination, SearchInput } from "@/features/rbac/components";
import { MutationError } from "@/features/schemas/components";
import { useDeleteRelationship, useRelationshipList, useSaveRelationship, useSchemaOutline } from "@/features/schemas/hooks";
import { RELATIONSHIP_LABELS, validateRelationshipDraft, type ExistingPair, type RelationshipDraft } from "@/features/schemas/validation";
import { useDebounced } from "@/hooks/useDebounced";
import type { OutlineTable, RelationshipEndpoint, RelationshipType, SchemaRelationship } from "@/types/schemaRegistry";
import { errorMessage } from "@/utils/errors";

export const RELATIONSHIPS_PAGE_SIZE = 25;

function Endpoint({ endpoint }: { endpoint: RelationshipEndpoint }) {
  return (
    <span className="font-mono text-xs">
      <span className="text-fg-muted">{endpoint.schema_name}.</span>
      <span className="text-fg">{endpoint.table_name}</span>
      <span className="text-fg-muted">.</span>
      <span className="font-medium text-fg">{endpoint.column_name}</span>
      {!endpoint.is_active && <span className="ml-1 text-warning-text">(inactive)</span>}
    </span>
  );
}

const EMPTY_DRAFT: RelationshipDraft = {
  sourceTableId: "",
  sourceColumnId: "",
  targetTableId: "",
  targetColumnId: "",
  relationshipType: "many_to_one",
};

function tableLabel(table: OutlineTable): string {
  return `${table.schema_name}.${table.table_name}${table.is_active ? "" : " (inactive)"}`;
}

interface RelationshipFormProps {
  schemaId: string;
  relationship: SchemaRelationship | null;
  tables: OutlineTable[];
  existing: ExistingPair[];
  onClose: () => void;
}

/** Add or edit a relationship. The column pickers come from the outline; the draft is checked before it is sent. */
export function RelationshipFormDialog({ open, ...props }: RelationshipFormProps & { open: boolean }) {
  return (
    <Dialog
      open={open}
      onClose={props.onClose}
      size="lg"
      title={props.relationship ? "Edit relationship" : "Add relationship"}
      description="The source column holds the reference; the target is usually the referenced table's primary key."
    >
      {/* Mounted only while open, so each opening starts from the relationship's current values. */}
      <RelationshipForm {...props} />
    </Dialog>
  );
}

function RelationshipForm({ schemaId, relationship, tables, existing, onClose }: RelationshipFormProps) {
  const save = useSaveRelationship(schemaId);
  const toast = useToast();
  const [draft, setDraft] = useState<RelationshipDraft>(() =>
    relationship
      ? {
          sourceTableId: relationship.source.table_id,
          sourceColumnId: relationship.source.column_id,
          targetTableId: relationship.target.table_id,
          targetColumnId: relationship.target.column_id,
          relationshipType: relationship.relationship_type,
        }
      : EMPTY_DRAFT,
  );
  const [description, setDescription] = useState(relationship?.description ?? "");
  const [active, setActive] = useState(relationship?.is_active ?? true);
  const [submitted, setSubmitted] = useState(false);

  const errors = validateRelationshipDraft(draft, tables, existing, relationship?.id);
  const shown = submitted ? errors : {};
  const sourceTable = tables.find((t) => t.id === draft.sourceTableId);
  const targetTable = tables.find((t) => t.id === draft.targetTableId);

  const close = () => {
    save.reset();
    onClose();
  };
  const submit = () => {
    setSubmitted(true);
    if (Object.keys(errors).length > 0 || !draft.relationshipType) return;
    save.mutate(
      {
        id: relationship?.id,
        input: {
          source_table_id: draft.sourceTableId,
          source_column_id: draft.sourceColumnId,
          target_table_id: draft.targetTableId,
          target_column_id: draft.targetColumnId,
          relationship_type: draft.relationshipType,
          description: description.trim() || null,
          is_active: active,
        },
      },
      {
        onSuccess: () => {
          toast.notify({
            title: relationship ? "Relationship updated" : "Relationship added",
          });
          close();
        },
      },
    );
  };

  const columnSelect = (side: "source" | "target", table: OutlineTable | undefined, value: string, error: string | undefined) => (
    <FormField label={side === "source" ? "Source column" : "Target column"} error={error} required>
      <Select
        value={value}
        disabled={!table}
        onChange={(event) =>
          setDraft((d) => ({
            ...d,
            [side === "source" ? "sourceColumnId" : "targetColumnId"]: event.target.value,
          }))
        }
      >
        <option value="">{table ? "Choose a column" : "Choose a table first"}</option>
        {table?.columns.map((column) => (
          <option key={column.id} value={column.id}>
            {column.column_name} ({column.data_type}){column.is_primary_key ? " PK" : ""}
            {column.is_active ? "" : " (inactive)"}
          </option>
        ))}
      </Select>
    </FormField>
  );

  return (
    <form
      noValidate
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      {save.isError && <MutationError error={save.error} fallback="The relationship could not be saved." />}
      {tables.length === 0 && <Alert tone="info">Add tables and columns before defining relationships.</Alert>}
      <div className="grid gap-4 sm:grid-cols-2">
        <FormField label="Source table" error={shown.sourceTableId} required>
          <Select
            value={draft.sourceTableId}
            onChange={(event) =>
              setDraft((d) => ({
                ...d,
                sourceTableId: event.target.value,
                sourceColumnId: "",
              }))
            }
          >
            <option value="">Choose a table</option>
            {tables.map((table) => (
              <option key={table.id} value={table.id}>
                {tableLabel(table)}
              </option>
            ))}
          </Select>
        </FormField>
        {columnSelect("source", sourceTable, draft.sourceColumnId, shown.sourceColumnId)}
        <FormField label="Target table" error={shown.targetTableId} required>
          <Select
            value={draft.targetTableId}
            onChange={(event) =>
              setDraft((d) => ({
                ...d,
                targetTableId: event.target.value,
                targetColumnId: "",
              }))
            }
          >
            <option value="">Choose a table</option>
            {tables.map((table) => (
              <option key={table.id} value={table.id}>
                {tableLabel(table)}
              </option>
            ))}
          </Select>
        </FormField>
        {columnSelect("target", targetTable, draft.targetColumnId, shown.targetColumnId)}
        <FormField label="Relationship type" error={shown.relationshipType} required>
          <Select
            value={draft.relationshipType}
            onChange={(event) =>
              setDraft((d) => ({
                ...d,
                relationshipType: event.target.value as RelationshipType | "",
              }))
            }
          >
            <option value="">Choose a type</option>
            {(Object.keys(RELATIONSHIP_LABELS) as RelationshipType[]).map((type) => (
              <option key={type} value={type}>
                {RELATIONSHIP_LABELS[type]}
              </option>
            ))}
          </Select>
        </FormField>
      </div>
      <TextareaField
        label="Description (optional)"
        rows={2}
        value={description}
        maxLength={4000}
        onChange={(event) => setDescription(event.target.value)}
      />
      <Checkbox
        label="Active"
        description="Inactive relationships are kept but left out of the AI's schema context."
        checked={active}
        onChange={(event) => setActive(event.target.checked)}
      />
      <DialogActions>
        <Button variant="secondary" onClick={close}>
          Cancel
        </Button>
        <Button type="submit" loading={save.isPending}>
          {relationship ? "Save changes" : "Add relationship"}
        </Button>
      </DialogActions>
    </form>
  );
}

export function RelationshipsTab({ schemaId }: { schemaId: string }) {
  const { hasPermission } = usePermissions();
  const canManage = hasPermission("schema.relationship.manage");
  const toast = useToast();
  const [searchInput, setSearchInput] = useState("");
  const search = useDebounced(searchInput.trim());
  const [page, setPage] = useState(0);
  const list = useRelationshipList(schemaId, {
    offset: page * RELATIONSHIPS_PAGE_SIZE,
    limit: RELATIONSHIPS_PAGE_SIZE,
    search: search || undefined,
  });
  const [form, setForm] = useState<{
    open: boolean;
    relationship: SchemaRelationship | null;
  }>({ open: false, relationship: null });
  const outline = useSchemaOutline(schemaId, canManage && form.open);
  // Pairs for the early duplicate check (the page size cap is 100; the server checks every pair anyway).
  const allPairs = useRelationshipList(schemaId, { offset: 0, limit: 100 });
  const existing = useMemo<ExistingPair[]>(
    () =>
      (allPairs.data?.items ?? []).map((r) => ({
        id: r.id,
        sourceColumnId: r.source.column_id,
        targetColumnId: r.target.column_id,
      })),
    [allPairs.data],
  );
  const [pendingDelete, setPendingDelete] = useState<SchemaRelationship | null>(null);
  const remove = useDeleteRelationship(schemaId);

  const columns: DataTableColumn<SchemaRelationship>[] = [
    {
      id: "relationship",
      header: "Relationship",
      cell: (r) => (
        <div className="flex min-w-0 flex-wrap items-center gap-1.5">
          <Endpoint endpoint={r.source} />
          <ArrowRight aria-label="references" className="h-3.5 w-3.5 shrink-0 text-fg-subtle" />
          <Endpoint endpoint={r.target} />
          {r.description && <p className="w-full text-xs text-fg-muted">{r.description}</p>}
        </div>
      ),
    },
    {
      id: "type",
      header: "Type",
      cell: (r) => <span className="text-sm whitespace-nowrap">{RELATIONSHIP_LABELS[r.relationship_type]}</span>,
    },
    {
      id: "status",
      header: "Status",
      hideBelow: "sm",
      cell: (r) => <ActiveBadge active={r.is_active} />,
    },
    {
      id: "actions",
      header: <span className="sr-only">Actions</span>,
      align: "right",
      cell: (r) =>
        canManage && (
          <div className="flex justify-end gap-1">
            <Button
              variant="ghost"
              size="sm"
              aria-label={`Edit relationship ${r.source.table_name}.${r.source.column_name}`}
              onClick={() => setForm({ open: true, relationship: r })}
            >
              Edit
            </Button>
            <Button
              variant="danger-ghost"
              size="sm"
              aria-label={`Delete relationship ${r.source.table_name}.${r.source.column_name}`}
              onClick={() => setPendingDelete(r)}
            >
              Delete
            </Button>
          </div>
        ),
    },
  ];

  return (
    <section aria-label="Relationships" className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <SearchInput
          label="Search relationships"
          placeholder="Table, column or description"
          value={searchInput}
          onChange={(value) => {
            setSearchInput(value);
            setPage(0);
          }}
        />
        {canManage && (
          <Button size="sm" onClick={() => setForm({ open: true, relationship: null })}>
            <Plus aria-hidden="true" className="h-4 w-4" /> Add relationship
          </Button>
        )}
      </div>
      <DataTable
        caption="Relationships between registered tables"
        columns={columns}
        rows={list.data?.items}
        getRowId={(r) => r.id}
        loading={list.isPending}
        loadingLabel="Loading relationships"
        error={list.isError ? errorMessage(list.error, "Relationships could not be loaded.") : null}
        onRetry={() => void list.refetch()}
        retrying={list.isFetching}
        empty={{
          icon: GitFork,
          title: search ? "No relationships match" : "No relationships yet",
          description: search ? "Try a different search." : "Relationships tell the AI how tables join.",
        }}
        footer={
          list.data && (
            <Pagination page={page} pageSize={RELATIONSHIPS_PAGE_SIZE} total={list.data.total} onPage={setPage} itemLabel="Relationships" />
          )
        }
      />

      {canManage && (
        <RelationshipFormDialog
          open={form.open}
          schemaId={schemaId}
          relationship={form.relationship}
          tables={outline.data?.tables ?? []}
          existing={existing}
          onClose={() => setForm({ open: false, relationship: null })}
        />
      )}
      <ConfirmDialog
        open={pendingDelete !== null}
        title="Delete relationship?"
        description={
          pendingDelete && (
            <>
              The link from <Endpoint endpoint={pendingDelete.source} /> to <Endpoint endpoint={pendingDelete.target} /> is removed. The
              columns stay.
            </>
          )
        }
        confirmLabel="Delete"
        pending={remove.isPending}
        error={remove.isError ? errorMessage(remove.error) : null}
        onCancel={() => {
          setPendingDelete(null);
          remove.reset();
        }}
        onConfirm={() =>
          pendingDelete &&
          remove.mutate(pendingDelete.id, {
            onSuccess: () => {
              toast.notify({ title: "Relationship deleted" });
              setPendingDelete(null);
            },
          })
        }
      />
    </section>
  );
}

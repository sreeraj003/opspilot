import { BookOpenText, Plus } from "lucide-react";
import { useState } from "react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { DataTable, type DataTableColumn } from "@/components/ui/DataTable";
import { ConfirmDialog } from "@/components/ui/Dialog";
import { Select } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/Layout";
import { useToast } from "@/components/ui/toastContext";
import { usePermissions } from "@/features/permissions/permissionsContext";
import { ActiveBadge, Pagination, SearchInput } from "@/features/rbac/components";
import { useSchemaList } from "@/features/schemas/hooks";
import { useBusinessTerms, useDeleteBusinessTerm, useUpdateBusinessTerm } from "@/features/understanding/hooks";
import { AGGREGATION_LABELS, KIND_LABELS } from "@/features/understanding/labels";
import { TermFormDialog } from "@/features/understanding/TermFormDialog";
import { useDebounced } from "@/hooks/useDebounced";
import { useDocumentTitle } from "@/hooks/useDocumentTitle";
import type { BusinessTerm, TermKind } from "@/types/understanding";
import { errorMessage } from "@/utils/errors";

export const TERMS_PAGE_SIZE = 20;

export function BusinessTermsPage() {
  useDocumentTitle("Business terms");
  const toast = useToast();
  const { hasPermission } = usePermissions();
  const canManage = hasPermission("business_term.manage");
  const canReadSchemas = hasPermission("schema.read");
  const [searchInput, setSearchInput] = useState("");
  const search = useDebounced(searchInput.trim());
  const [schemaId, setSchemaId] = useState("");
  const [kind, setKind] = useState<TermKind | "">("");
  const [active, setActive] = useState<"true" | "false" | "">("");
  const [page, setPage] = useState(0);
  const [editing, setEditing] = useState<BusinessTerm | null>(null);
  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState<BusinessTerm | null>(null);
  const schemas = useSchemaList({
    offset: 0,
    limit: 100,
    sort: "name",
    order: "asc",
  });
  const update = useUpdateBusinessTerm();
  const remove = useDeleteBusinessTerm();
  const list = useBusinessTerms({
    offset: page * TERMS_PAGE_SIZE,
    limit: TERMS_PAGE_SIZE,
    search: search || undefined,
    schema_source_id: schemaId || undefined,
    kind,
    is_active: active,
  });
  const filtered = Boolean(search) || schemaId !== "" || kind !== "" || active !== "";
  const resetPage = () => setPage(0);

  const toggleActive = async (term: BusinessTerm) => {
    try {
      await update.mutateAsync({
        id: term.id,
        input: { is_active: !term.is_active },
      });
      toast.notify({
        title: term.is_active ? "Term deactivated" : "Term activated",
        description: term.name,
      });
    } catch (error) {
      toast.notify({
        title: "The term could not be changed",
        description: errorMessage(error),
        tone: "error",
      });
    }
  };

  const columns: DataTableColumn<BusinessTerm>[] = [
    {
      id: "term",
      header: "Term",
      cell: (term) => (
        <div className="min-w-0">
          <p className="font-medium text-fg">{term.name}</p>
          <p className="mt-0.5 line-clamp-2 max-w-md text-xs text-fg-muted">{term.description}</p>
          {term.synonyms.length > 0 && (
            <p className="mt-1 text-xs text-fg-muted">
              <span className="sr-only">Synonyms: </span>Also: {term.synonyms.join(", ")}
            </p>
          )}
        </div>
      ),
    },
    {
      id: "kind",
      header: "Kind",
      hideBelow: "sm",
      cell: (term) => <Badge>{KIND_LABELS[term.kind]}</Badge>,
    },
    {
      id: "fields",
      header: "Schema fields",
      hideBelow: "md",
      cell: (term) => (
        <div className="space-y-1">
          <p className="text-xs text-fg-muted">{term.schema_source_name ?? "Schema deleted"}</p>
          <div className="flex max-w-xs flex-wrap gap-1">
            {[...term.related_columns, ...term.related_tables.filter((t) => !term.related_columns.some((c) => c.startsWith(`${t}.`)))].map(
              (field) => (
                <Badge key={field} mono tone={term.stale_references.includes(field) ? "warning" : "neutral"}>
                  {field}
                </Badge>
              ),
            )}
          </div>
          {term.stale_references.length > 0 && (
            <p className="text-xs font-medium text-warning-text">
              {term.stale_references.length === 1 ? "1 field is" : `${term.stale_references.length} fields are`} no longer in the schema
            </p>
          )}
        </div>
      ),
    },
    {
      id: "aggregation",
      header: "Aggregation",
      hideBelow: "lg",
      cell: (term) => (term.default_aggregation ? AGGREGATION_LABELS[term.default_aggregation] : <span className="text-fg-muted">—</span>),
    },
    {
      id: "status",
      header: "Status",
      cell: (term) => <ActiveBadge active={term.is_active} />,
    },
    ...(canManage
      ? [
          {
            id: "actions",
            header: <span className="sr-only">Actions</span>,
            cell: (term: BusinessTerm) => (
              <div className="flex flex-wrap justify-end gap-1.5">
                <Button size="sm" variant="secondary" onClick={() => setEditing(term)} aria-label={`Edit ${term.name}`}>
                  Edit
                </Button>
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() => void toggleActive(term)}
                  aria-label={`${term.is_active ? "Deactivate" : "Activate"} ${term.name}`}
                >
                  {term.is_active ? "Deactivate" : "Activate"}
                </Button>
                <Button size="sm" variant="danger-ghost" onClick={() => setDeleting(term)} aria-label={`Delete ${term.name}`}>
                  Delete
                </Button>
              </div>
            ),
          },
        ]
      : []),
  ];

  return (
    <div className="space-y-section">
      <PageHeader
        title="Business terms"
        description="Your company's definitions of metrics and dimensions. The AI uses only these definitions and asks when a term is missing or ambiguous."
        actions={
          canManage && (
            <Button onClick={() => setCreating(true)}>
              <Plus aria-hidden="true" className="h-4 w-4" />
              New term
            </Button>
          )
        }
      />

      <div className="flex flex-col gap-3 sm:flex-row sm:flex-wrap">
        <SearchInput
          label="Search terms"
          placeholder="Search by name, synonym or description"
          value={searchInput}
          onChange={(value) => {
            setSearchInput(value);
            resetPage();
          }}
        />
        {canReadSchemas && (
          <Select
            aria-label="Filter by schema"
            value={schemaId}
            onChange={(event) => {
              setSchemaId(event.target.value);
              resetPage();
            }}
            className="sm:w-48"
          >
            <option value="">All schemas</option>
            {schemas.data?.items.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </Select>
        )}
        <Select
          aria-label="Filter by kind"
          value={kind}
          onChange={(event) => {
            setKind(event.target.value as TermKind | "");
            resetPage();
          }}
          className="sm:w-40"
        >
          <option value="">All kinds</option>
          {Object.entries(KIND_LABELS).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </Select>
        <Select
          aria-label="Filter by status"
          value={active}
          onChange={(event) => {
            setActive(event.target.value as "true" | "false" | "");
            resetPage();
          }}
          className="sm:w-40"
        >
          <option value="">All statuses</option>
          <option value="true">Active</option>
          <option value="false">Inactive</option>
        </Select>
      </div>

      <DataTable
        caption="Business terms"
        columns={columns}
        rows={list.data?.items}
        getRowId={(term) => term.id}
        loading={list.isPending}
        loadingLabel="Loading business terms"
        error={list.isError ? errorMessage(list.error, "Business terms could not be loaded.") : null}
        onRetry={() => void list.refetch()}
        retrying={list.isFetching}
        empty={{
          icon: BookOpenText,
          title: filtered ? "No terms match" : "No business terms yet",
          description: filtered
            ? "Try a different search or filter."
            : canManage
              ? "Define terms such as Revenue or Active customer so the AI never has to guess what they mean."
              : "Ask an administrator to define your company's business terms.",
        }}
        footer={
          list.data && <Pagination page={page} pageSize={TERMS_PAGE_SIZE} total={list.data.total} onPage={setPage} itemLabel="Terms" />
        }
      />

      {(creating || editing !== null) && (
        <TermFormDialog
          key={editing?.id ?? "new"}
          open
          term={editing}
          defaultSchemaId={schemaId}
          onClose={() => {
            setCreating(false);
            setEditing(null);
          }}
          onSaved={(term) => {
            toast.notify({
              title: editing ? "Term saved" : "Term created",
              description: term.name,
            });
            setCreating(false);
            setEditing(null);
          }}
        />
      )}

      <ConfirmDialog
        open={deleting !== null}
        title="Delete business term?"
        description={
          <>
            <strong>{deleting?.name}</strong> will no longer be used to interpret questions. To keep it for later, deactivate it instead.
          </>
        }
        confirmLabel="Delete term"
        pending={remove.isPending}
        error={remove.isError ? errorMessage(remove.error, "The term could not be deleted.") : null}
        onCancel={() => {
          remove.reset();
          setDeleting(null);
        }}
        onConfirm={() => {
          if (!deleting) return;
          remove.mutate(deleting.id, {
            onSuccess: () => {
              toast.notify({
                title: "Term deleted",
                description: deleting.name,
              });
              setDeleting(null);
            },
          });
        }}
      />
    </div>
  );
}

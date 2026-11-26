import { Database, Plus } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router";

import { Button } from "@/components/ui/Button";
import { DataTable, type DataTableColumn } from "@/components/ui/DataTable";
import { ConfirmDialog } from "@/components/ui/Dialog";
import { Select } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/Layout";
import { useToast } from "@/components/ui/toastContext";
import { usePermissions } from "@/features/permissions/permissionsContext";
import { ActiveBadge, Pagination, SearchInput } from "@/features/rbac/components";
import { DatabaseTypeLabel, SchemaFormDialog } from "@/features/schemas/components";
import { useDeleteSchema, useSchemaList } from "@/features/schemas/hooks";
import { DATABASE_TYPE_LABELS } from "@/features/schemas/validation";
import { useDebounced } from "@/hooks/useDebounced";
import { useDocumentTitle } from "@/hooks/useDocumentTitle";
import type { DatabaseType, SchemaListParams, SchemaSource, SchemaStatusFilter } from "@/types/schemaRegistry";
import { errorMessage } from "@/utils/errors";
import { formatDateTime } from "@/utils/format";

export const SCHEMAS_PAGE_SIZE = 20;

type Sort = NonNullable<SchemaListParams["sort"]>;

export function SchemasPage() {
  useDocumentTitle("Schemas");
  const navigate = useNavigate();
  const toast = useToast();
  const { hasPermission } = usePermissions();
  const canCreate = hasPermission("schema.create");
  const canUpdate = hasPermission("schema.update");
  const canDelete = hasPermission("schema.delete");
  const [searchInput, setSearchInput] = useState("");
  const search = useDebounced(searchInput.trim());
  const [status, setStatus] = useState<SchemaStatusFilter>("all");
  const [databaseType, setDatabaseType] = useState<DatabaseType | "">("");
  const [sort, setSort] = useState<Sort>("name");
  const [page, setPage] = useState(0);
  const list = useSchemaList({
    offset: page * SCHEMAS_PAGE_SIZE,
    limit: SCHEMAS_PAGE_SIZE,
    search: search || undefined,
    status,
    database_type: databaseType,
    sort,
    order: sort === "name" ? "asc" : "desc",
  });
  const remove = useDeleteSchema();
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<SchemaSource | null>(null);
  const [toDelete, setToDelete] = useState<SchemaSource | null>(null);
  const filtered = Boolean(search) || status !== "all" || databaseType !== "";

  const columns: DataTableColumn<SchemaSource>[] = [
    {
      id: "schema",
      header: "Schema",
      cell: (schema) => (
        <div className="min-w-0">
          <Link to={`/admin/schemas/${schema.id}`} className="font-medium text-fg hover:underline">
            {schema.name}
          </Link>
          {schema.description && <p className="mt-0.5 line-clamp-2 max-w-md text-xs text-fg-muted">{schema.description}</p>}
        </div>
      ),
    },
    {
      id: "type",
      header: "Database",
      cell: (schema) => <DatabaseTypeLabel type={schema.database_type} dialect={schema.dialect} />,
      hideBelow: "md",
    },
    {
      id: "tables",
      header: "Tables",
      align: "right",
      cell: (schema) => schema.table_count,
      hideBelow: "sm",
    },
    {
      id: "columns",
      header: "Columns",
      align: "right",
      cell: (schema) => schema.column_count,
      hideBelow: "sm",
    },
    {
      id: "version",
      header: "Version",
      align: "right",
      cell: (schema) => <span className="font-mono">v{schema.schema_version}</span>,
    },
    {
      id: "updated",
      header: "Last updated",
      cell: (schema) => formatDateTime(schema.metadata_updated_at),
      hideBelow: "lg",
    },
    {
      id: "status",
      header: "Status",
      cell: (schema) => <ActiveBadge active={schema.is_active} />,
      hideBelow: "sm",
    },
    {
      id: "actions",
      header: <span className="sr-only">Actions</span>,
      align: "right",
      cell: (schema) => (
        <div className="flex justify-end gap-1">
          {canUpdate && (
            <Button variant="ghost" size="sm" aria-label={`Edit ${schema.name}`} onClick={() => setEditing(schema)}>
              Edit
            </Button>
          )}
          {canDelete && (
            <Button variant="danger-ghost" size="sm" aria-label={`Delete ${schema.name}`} onClick={() => setToDelete(schema)}>
              Delete
            </Button>
          )}
        </div>
      ),
    },
  ];

  const resetPage = () => setPage(0);
  return (
    <div className="space-y-section">
      <PageHeader
        title="Schemas"
        description="Metadata about your business databases: tables, columns and how they relate. OpsPilot never connects to these databases."
        actions={
          canCreate && (
            <Button onClick={() => setCreating(true)}>
              <Plus aria-hidden="true" className="h-4 w-4" />
              Register schema
            </Button>
          )
        }
      />

      <div className="flex flex-col gap-3 sm:flex-row sm:flex-wrap">
        <SearchInput
          label="Search schemas"
          placeholder="Search by name or description"
          value={searchInput}
          onChange={(value) => {
            setSearchInput(value);
            resetPage();
          }}
        />
        <Select
          aria-label="Filter by database type"
          value={databaseType}
          onChange={(event) => {
            setDatabaseType(event.target.value as DatabaseType | "");
            resetPage();
          }}
          className="sm:w-44"
        >
          <option value="">All databases</option>
          {Object.entries(DATABASE_TYPE_LABELS).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </Select>
        <Select
          aria-label="Filter by status"
          value={status}
          onChange={(event) => {
            setStatus(event.target.value as SchemaStatusFilter);
            resetPage();
          }}
          className="sm:w-40"
        >
          <option value="all">All statuses</option>
          <option value="active">Active</option>
          <option value="inactive">Inactive</option>
        </Select>
        <Select aria-label="Sort by" value={sort} onChange={(event) => setSort(event.target.value as Sort)} className="sm:w-48">
          <option value="name">Name</option>
          <option value="updated_at">Recently updated</option>
          <option value="created_at">Recently registered</option>
          <option value="schema_version">Highest version</option>
        </Select>
      </div>

      <DataTable
        caption="Schemas"
        columns={columns}
        rows={list.data?.items}
        getRowId={(schema) => schema.id}
        loading={list.isPending}
        loadingLabel="Loading schemas"
        error={list.isError ? errorMessage(list.error, "Schemas could not be loaded.") : null}
        onRetry={() => void list.refetch()}
        retrying={list.isFetching}
        empty={{
          icon: Database,
          title: filtered ? "No schemas match" : "No schemas registered yet",
          description: filtered
            ? "Try a different search or filter."
            : canCreate
              ? "Register a schema, then add its tables or import them from JSON."
              : "Ask an administrator to register your company's database schemas.",
        }}
        footer={
          list.data && <Pagination page={page} pageSize={SCHEMAS_PAGE_SIZE} total={list.data.total} onPage={setPage} itemLabel="Schemas" />
        }
      />

      <SchemaFormDialog
        open={creating}
        onClose={() => setCreating(false)}
        onSaved={(schema) => {
          setCreating(false);
          toast.notify({
            title: "Schema registered",
            description: schema.name,
          });
          navigate(`/admin/schemas/${schema.id}`);
        }}
      />
      <SchemaFormDialog
        open={editing !== null}
        schema={editing}
        onClose={() => setEditing(null)}
        onSaved={(schema) => {
          setEditing(null);
          toast.notify({ title: "Schema updated", description: schema.name });
        }}
      />
      <ConfirmDialog
        open={toDelete !== null}
        title="Delete schema?"
        description={
          toDelete && (
            <>
              <strong className="font-medium text-fg">{toDelete.name}</strong> and its {toDelete.table_count} tables,{" "}
              {toDelete.column_count} columns and {toDelete.relationship_count} relationships are removed from the registry. The change
              history is kept for auditing.
            </>
          )
        }
        confirmLabel="Delete schema"
        pending={remove.isPending}
        error={remove.isError ? errorMessage(remove.error) : null}
        onCancel={() => {
          setToDelete(null);
          remove.reset();
        }}
        onConfirm={() =>
          toDelete &&
          remove.mutate(toDelete.id, {
            onSuccess: () => {
              toast.notify({
                title: "Schema deleted",
                description: toDelete.name,
              });
              setToDelete(null);
            },
          })
        }
      />
    </div>
  );
}

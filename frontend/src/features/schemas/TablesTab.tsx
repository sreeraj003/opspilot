import { Columns3, Pencil, Plus, Table2, Trash2 } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { DataTable, type DataTableColumn } from "@/components/ui/DataTable";
import { ConfirmDialog } from "@/components/ui/Dialog";
import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorState } from "@/components/ui/ErrorState";
import { Select } from "@/components/ui/Input";
import { SkeletonText } from "@/components/ui/Skeleton";
import { useToast } from "@/components/ui/toastContext";
import { usePermissions } from "@/features/permissions/permissionsContext";
import { ActiveBadge, Pagination, SearchInput } from "@/features/rbac/components";
import { ColumnFlags, ColumnFormDialog, TableFormDialog } from "@/features/schemas/components";
import { useDeleteColumn, useDeleteTable, useSchemaSummary, useTable, useTableList } from "@/features/schemas/hooks";
import { useDebounced } from "@/hooks/useDebounced";
import { ApiError } from "@/services/apiClient";
import type { SchemaColumn, SchemaStatusFilter, SchemaTable } from "@/types/schemaRegistry";
import { cn } from "@/utils/cn";
import { errorMessage } from "@/utils/errors";

export const TABLES_PAGE_SIZE = 25;

type PendingDelete = { kind: "table"; item: SchemaTable } | { kind: "column"; item: SchemaColumn } | null;

/** A delete blocked by relationships answers 409 IN_USE; the user may then delete them together. */
function isInUse(error: unknown): boolean {
  return error instanceof ApiError && error.code === "IN_USE";
}

export function TablesTab({ schemaId }: { schemaId: string }) {
  const { hasPermission } = usePermissions();
  const canEdit = hasPermission("schema.update");
  const canDelete = hasPermission("schema.delete");
  const toast = useToast();
  const [searchInput, setSearchInput] = useState("");
  const search = useDebounced(searchInput.trim());
  const [namespace, setNamespace] = useState("");
  const [status, setStatus] = useState<SchemaStatusFilter>("all");
  const [page, setPage] = useState(0);
  const summary = useSchemaSummary(schemaId);
  const list = useTableList(schemaId, {
    offset: page * TABLES_PAGE_SIZE,
    limit: TABLES_PAGE_SIZE,
    search: search || undefined,
    schema_name: namespace || undefined,
    status,
  });
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected = useTable(schemaId, selectedId);
  const [tableForm, setTableForm] = useState<{
    open: boolean;
    table: SchemaTable | null;
  }>({ open: false, table: null });
  const [columnForm, setColumnForm] = useState<{
    open: boolean;
    column: SchemaColumn | null;
  }>({ open: false, column: null });
  const [pendingDelete, setPendingDelete] = useState<PendingDelete>(null);
  const deleteTable = useDeleteTable(schemaId);
  const deleteColumn = useDeleteColumn(schemaId);
  const deletion = pendingDelete?.kind === "column" ? deleteColumn : deleteTable;
  const needsCascade = isInUse(deletion.error);
  const namespaces = summary.data?.counts.schema_namespaces ?? [];

  const confirmDelete = () => {
    if (!pendingDelete) return;
    const cascade = needsCascade;
    const done = (title: string, description: string) => () => {
      toast.notify({ title, description });
      if (pendingDelete.kind === "table" && pendingDelete.item.id === selectedId) setSelectedId(null);
      setPendingDelete(null);
      deleteTable.reset();
      deleteColumn.reset();
    };
    if (pendingDelete.kind === "table") {
      deleteTable.mutate({ id: pendingDelete.item.id, cascade }, { onSuccess: done("Table deleted", pendingDelete.item.qualified_name) });
    } else {
      deleteColumn.mutate({ id: pendingDelete.item.id, cascade }, { onSuccess: done("Column deleted", pendingDelete.item.column_name) });
    }
  };

  const tableColumns: DataTableColumn<SchemaTable>[] = [
    {
      id: "table",
      header: "Table",
      cell: (table) => (
        <button
          type="button"
          onClick={() => setSelectedId(table.id)}
          aria-pressed={table.id === selectedId}
          className={cn("min-w-0 rounded-xs text-left", table.id === selectedId && "text-accent-text")}
        >
          <span className="font-mono text-sm font-medium hover:underline">{table.qualified_name}</span>
          {table.description && <span className="mt-0.5 line-clamp-1 block max-w-xs text-xs text-fg-muted">{table.description}</span>}
        </button>
      ),
    },
    {
      id: "columns",
      header: "Columns",
      align: "right",
      cell: (table) => table.column_count,
    },
    {
      id: "status",
      header: "Status",
      cell: (table) => <ActiveBadge active={table.is_active} />,
      hideBelow: "sm",
    },
  ];

  const detail = selected.data;
  const columnColumns: DataTableColumn<SchemaColumn>[] = [
    {
      id: "name",
      header: "Column",
      cell: (column) => (
        <div className="min-w-0">
          <span className="font-mono text-sm font-medium text-fg">{column.column_name}</span>
          <div className="mt-1">
            <ColumnFlags column={column} />
          </div>
        </div>
      ),
    },
    {
      id: "type",
      header: "Data type",
      cell: (column) => (
        <span className="font-mono text-xs">
          {column.data_type}
          {!column.is_nullable && <span className="ml-1 text-fg-muted">not null</span>}
        </span>
      ),
    },
    {
      id: "meaning",
      header: "Description",
      hideBelow: "md",
      cell: (column) => (
        <div className="max-w-sm text-xs">
          {column.description && <p className="text-fg-secondary">{column.description}</p>}
          {column.business_meaning && <p className="text-fg-muted">Business: {column.business_meaning}</p>}
          {!column.description && !column.business_meaning && <span className="text-fg-subtle">—</span>}
        </div>
      ),
    },
    {
      id: "actions",
      header: <span className="sr-only">Actions</span>,
      align: "right",
      cell: (column) => (
        <div className="flex justify-end gap-1">
          {canEdit && (
            <Button
              variant="ghost"
              size="sm"
              aria-label={`Edit column ${column.column_name}`}
              onClick={() => setColumnForm({ open: true, column })}
            >
              Edit
            </Button>
          )}
          {canDelete && (
            <Button
              variant="danger-ghost"
              size="sm"
              aria-label={`Delete column ${column.column_name}`}
              onClick={() => setPendingDelete({ kind: "column", item: column })}
            >
              Delete
            </Button>
          )}
        </div>
      ),
    },
  ];

  return (
    <div className="grid gap-section xl:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
      <section aria-label="Tables" className="min-w-0 space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-base font-semibold text-fg">Tables</h2>
          {canEdit && (
            <Button size="sm" onClick={() => setTableForm({ open: true, table: null })}>
              <Plus aria-hidden="true" className="h-4 w-4" /> Add table
            </Button>
          )}
        </div>
        <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap">
          <SearchInput
            label="Search tables"
            placeholder="Name, description or purpose"
            value={searchInput}
            onChange={(value) => {
              setSearchInput(value);
              setPage(0);
            }}
          />
          {namespaces.length > 1 && (
            <Select
              aria-label="Filter by namespace"
              value={namespace}
              onChange={(event) => {
                setNamespace(event.target.value);
                setPage(0);
              }}
              className="sm:w-40"
            >
              <option value="">All namespaces</option>
              {namespaces.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </Select>
          )}
          <Select
            aria-label="Filter tables by status"
            value={status}
            onChange={(event) => {
              setStatus(event.target.value as SchemaStatusFilter);
              setPage(0);
            }}
            className="sm:w-36"
          >
            <option value="all">All statuses</option>
            <option value="active">Active</option>
            <option value="inactive">Inactive</option>
          </Select>
        </div>
        <DataTable
          caption="Registered tables"
          columns={tableColumns}
          rows={list.data?.items}
          getRowId={(table) => table.id}
          loading={list.isPending}
          loadingLabel="Loading tables"
          error={list.isError ? errorMessage(list.error, "Tables could not be loaded.") : null}
          onRetry={() => void list.refetch()}
          retrying={list.isFetching}
          empty={{
            icon: Table2,
            title: search || namespace || status !== "all" ? "No tables match" : "No tables yet",
            description:
              search || namespace || status !== "all" ? "Try a different search or filter." : "Add a table or import the schema from JSON.",
          }}
          footer={
            list.data && <Pagination page={page} pageSize={TABLES_PAGE_SIZE} total={list.data.total} onPage={setPage} itemLabel="Tables" />
          }
        />
      </section>

      <section aria-label="Column explorer" className="min-w-0">
        {selectedId === null ? (
          <Card>
            <EmptyState icon={Columns3} title="Select a table" description="Its columns, data types and keys appear here." />
          </Card>
        ) : selected.isPending ? (
          <Card>
            <SkeletonText lines={6} />
          </Card>
        ) : selected.isError ? (
          <ErrorState message={errorMessage(selected.error, "The table could not be loaded.")} onRetry={() => void selected.refetch()} />
        ) : (
          detail && (
            <Card
              title={<span className="font-mono">{detail.qualified_name}</span>}
              description={detail.description ?? undefined}
              actions={
                <div className="flex flex-wrap gap-1">
                  {canEdit && (
                    <Button variant="secondary" size="sm" onClick={() => setTableForm({ open: true, table: detail })}>
                      <Pencil aria-hidden="true" className="h-3.5 w-3.5" /> Edit table
                    </Button>
                  )}
                  {canDelete && (
                    <Button variant="danger-ghost" size="sm" onClick={() => setPendingDelete({ kind: "table", item: detail })}>
                      <Trash2 aria-hidden="true" className="h-3.5 w-3.5" /> Delete table
                    </Button>
                  )}
                </div>
              }
            >
              <div className="space-y-4">
                <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
                  <ActiveBadge active={detail.is_active} />
                  <span className="text-fg-muted">
                    {detail.columns.length} columns · {detail.relationship_count} relationships
                  </span>
                </div>
                {detail.business_purpose && (
                  <p className="text-sm text-fg-secondary">
                    <span className="font-medium text-fg">Business purpose: </span>
                    {detail.business_purpose}
                  </p>
                )}
                <div className="flex items-center justify-between gap-2">
                  <h3 className="text-sm font-semibold text-fg">Columns</h3>
                  {canEdit && (
                    <Button size="sm" variant="secondary" onClick={() => setColumnForm({ open: true, column: null })}>
                      <Plus aria-hidden="true" className="h-4 w-4" /> Add column
                    </Button>
                  )}
                </div>
                <DataTable
                  caption={`Columns of ${detail.qualified_name}`}
                  columns={columnColumns}
                  rows={detail.columns}
                  getRowId={(column) => column.id}
                  empty={{
                    icon: Columns3,
                    title: "No columns yet",
                    description: canEdit ? "Add the table's columns." : undefined,
                  }}
                />
              </div>
            </Card>
          )
        )}
      </section>

      <TableFormDialog
        open={tableForm.open}
        schemaId={schemaId}
        table={tableForm.table}
        defaultNamespace={namespaces[0] ?? "public"}
        onClose={() => setTableForm({ open: false, table: null })}
      />
      {detail && (
        <ColumnFormDialog
          open={columnForm.open}
          schemaId={schemaId}
          tableId={detail.id}
          tableName={detail.qualified_name}
          column={columnForm.column}
          onClose={() => setColumnForm({ open: false, column: null })}
        />
      )}
      <ConfirmDialog
        open={pendingDelete !== null}
        title={pendingDelete?.kind === "column" ? "Delete column?" : "Delete table?"}
        description={
          pendingDelete && (
            <>
              <strong className="font-mono font-medium text-fg">
                {pendingDelete.kind === "table" ? pendingDelete.item.qualified_name : pendingDelete.item.column_name}
              </strong>{" "}
              {pendingDelete.kind === "table" ? "and its columns are" : "is"} removed from the registry. History is kept.
              {needsCascade && " It is used by relationships: confirm again to delete them as well."}
            </>
          )
        }
        confirmLabel={needsCascade ? "Delete with relationships" : "Delete"}
        pending={deletion.isPending}
        error={deletion.isError ? errorMessage(deletion.error) : null}
        onCancel={() => {
          setPendingDelete(null);
          deleteTable.reset();
          deleteColumn.reset();
        }}
        onConfirm={confirmDelete}
      />
    </div>
  );
}

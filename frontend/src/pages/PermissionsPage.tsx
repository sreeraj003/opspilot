import { KeyRound, Plus } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router";

import { Button, ButtonLink } from "@/components/ui/Button";
import { DataTable, type DataTableColumn } from "@/components/ui/DataTable";
import { ConfirmDialog } from "@/components/ui/Dialog";
import { Select } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/Layout";
import { useToast } from "@/components/ui/toastContext";
import { usePermissions } from "@/features/permissions/permissionsContext";
import { ActiveBadge, Pagination, SearchInput, SystemTag } from "@/features/rbac/components";
import { useDebounced } from "@/hooks/useDebounced";
import { useDeletePermission, usePermissionList, useUpdatePermission } from "@/features/rbac/hooks";
import { useDocumentTitle } from "@/hooks/useDocumentTitle";
import type { PermissionDefinition, StatusFilter } from "@/types/rbac";
import { errorMessage } from "@/utils/errors";
import { formatDate } from "@/utils/format";

export const PERMISSIONS_PAGE_SIZE = 20;

function ActiveToggle({ permission }: { permission: PermissionDefinition }) {
  const update = useUpdatePermission(permission.id);
  const toast = useToast();
  return (
    <Button
      variant="ghost"
      size="sm"
      aria-label={`${permission.is_active ? "Deactivate" : "Activate"} ${permission.name}`}
      loading={update.isPending}
      onClick={() =>
        update.mutate(
          { is_active: !permission.is_active },
          {
            onSuccess: () => toast.notify({ title: permission.is_active ? "Permission deactivated" : "Permission activated", description: permission.name }),
            onError: (error) => toast.notify({ tone: "error", title: "Could not change the permission", description: errorMessage(error) }),
          },
        )
      }
    >
      {permission.is_active ? "Deactivate" : "Activate"}
    </Button>
  );
}

export function PermissionsPage() {
  useDocumentTitle("Permissions");
  const navigate = useNavigate();
  const { hasPermission } = usePermissions();
  const canManage = hasPermission("permissions:manage");
  const [searchInput, setSearchInput] = useState("");
  const search = useDebounced(searchInput.trim());
  const [status, setStatus] = useState<StatusFilter>("all");
  const [resourceType, setResourceType] = useState("");
  const [page, setPage] = useState(0);
  const list = usePermissionList({
    offset: page * PERMISSIONS_PAGE_SIZE,
    limit: PERMISSIONS_PAGE_SIZE,
    search: search || undefined,
    status,
    resource_type: resourceType || undefined,
  });
  const remove = useDeletePermission();
  const [toDelete, setToDelete] = useState<PermissionDefinition | null>(null);
  const toast = useToast();

  const columns: DataTableColumn<PermissionDefinition>[] = [
    {
      id: "permission",
      header: "Permission",
      cell: (permission) => (
        <div className="min-w-0">
          <Link to={`/admin/permissions/${permission.id}`} className="font-medium text-fg hover:underline">
            {permission.name}
          </Link>
          {permission.is_system && <SystemTag />}
          <p className="font-mono text-xs text-fg-muted">{permission.key}</p>
        </div>
      ),
    },
    { id: "resource", header: "Resource", cell: (permission) => <span className="font-mono text-xs">{permission.resource_type}</span>, hideBelow: "md" },
    { id: "action", header: "Action", cell: (permission) => <span className="font-mono text-xs">{permission.action}</span>, hideBelow: "lg" },
    {
      id: "status",
      header: "Status",
      cell: (permission) => (
        <>
          <ActiveBadge active={permission.is_active} />
          {permission.expires_at && <p className="mt-1 text-xs text-fg-muted">Expires {formatDate(permission.expires_at)}</p>}
        </>
      ),
    },
    { id: "roles", header: "Roles", align: "right", cell: (permission) => permission.role_count, hideBelow: "sm" },
    {
      id: "actions",
      header: <span className="sr-only">Actions</span>,
      align: "right",
      cell: (permission) => (
        <div className="flex justify-end gap-1">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => navigate(`/admin/permissions/${permission.id}`)}
            aria-label={`${canManage && !permission.is_system ? "Edit" : "View"} ${permission.name}`}
          >
            {canManage && !permission.is_system ? "Edit" : "View"}
          </Button>
          {canManage && !permission.is_system && (
            <>
              <ActiveToggle permission={permission} />
              <Button variant="danger-ghost" size="sm" aria-label={`Delete ${permission.name}`} onClick={() => setToDelete(permission)}>
                Delete
              </Button>
            </>
          )}
        </div>
      ),
    },
  ];

  return (
    <div className="space-y-section">
      <PageHeader
        title="Permissions"
        description="Each permission states what may be done, and on which data. Assign them to roles."
        actions={
          canManage && (
            <ButtonLink to="/admin/permissions/new" variant="primary">
              <Plus aria-hidden="true" className="h-4 w-4" />
              Create permission
            </ButtonLink>
          )
        }
      />

      <div className="flex flex-col gap-3 sm:flex-row sm:flex-wrap">
        <SearchInput
          label="Search permissions"
          placeholder="Search by name or key"
          value={searchInput}
          onChange={(value) => {
            setSearchInput(value);
            setPage(0);
          }}
        />
        <Select
          aria-label="Filter by resource"
          value={resourceType}
          onChange={(event) => {
            setResourceType(event.target.value);
            setPage(0);
          }}
          className="sm:w-52"
        >
          <option value="">All resources</option>
          <option value="database_data">Database data</option>
          <option value="system">System administration</option>
        </Select>
        <Select
          aria-label="Filter by status"
          value={status}
          onChange={(event) => {
            setStatus(event.target.value as StatusFilter);
            setPage(0);
          }}
          className="sm:w-44"
        >
          <option value="all">All statuses</option>
          <option value="active">Active</option>
          <option value="inactive">Inactive</option>
        </Select>
      </div>

      <DataTable
        caption="Permissions"
        columns={columns}
        rows={list.data?.items}
        getRowId={(permission) => permission.id}
        loading={list.isPending}
        loadingLabel="Loading permissions"
        error={list.isError ? errorMessage(list.error, "Permissions could not be loaded.") : null}
        onRetry={() => void list.refetch()}
        retrying={list.isFetching}
        empty={{ icon: KeyRound, title: "No permissions found", description: "Try a different search or filter." }}
        footer={
          list.data && <Pagination page={page} pageSize={PERMISSIONS_PAGE_SIZE} total={list.data.total} onPage={setPage} itemLabel="Permissions" />
        }
      />

      <ConfirmDialog
        open={toDelete !== null}
        title="Delete permission?"
        description={toDelete && `${toDelete.name} will be deleted. Its history is kept for auditing.`}
        confirmLabel="Delete permission"
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
              toast.notify({ title: "Permission deleted", description: toDelete.name });
              setToDelete(null);
            },
          })
        }
      />
    </div>
  );
}

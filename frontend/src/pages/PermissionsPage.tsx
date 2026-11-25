import { KeyRound, Plus } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router";

import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/Dialog";
import { Spinner } from "@/components/ui/Spinner";
import { usePermissions } from "@/features/permissions/permissionsContext";
import { ActiveBadge, Pagination, SearchInput, SystemTag } from "@/features/rbac/components";
import { SELECT_CLASSES } from "@/features/rbac/fieldClasses";
import { useDebounced } from "@/hooks/useDebounced";
import { useDeletePermission, usePermissionList, useUpdatePermission } from "@/features/rbac/hooks";
import { useDocumentTitle } from "@/hooks/useDocumentTitle";
import type { PermissionDefinition, StatusFilter } from "@/types/rbac";
import { errorMessage } from "@/utils/errors";
import { formatDate } from "@/utils/format";

export const PERMISSIONS_PAGE_SIZE = 20;

function ActiveToggle({ permission, onError }: { permission: PermissionDefinition; onError: (message: string) => void }) {
  const update = useUpdatePermission(permission.id);
  return (
    <Button
      variant="ghost"
      aria-label={`${permission.is_active ? "Deactivate" : "Activate"} ${permission.name}`}
      loading={update.isPending}
      onClick={() => update.mutate({ is_active: !permission.is_active }, { onError: (error) => onError(errorMessage(error)) })}
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
  const [actionError, setActionError] = useState<string | null>(null);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-stone-900">Permissions</h1>
          <p className="mt-1 text-sm text-stone-500">Each permission states what may be done, and on which data. Assign them to roles.</p>
        </div>
        {canManage && (
          <Button onClick={() => navigate("/admin/permissions/new")}>
            <Plus aria-hidden="true" className="h-4 w-4" />
            Create permission
          </Button>
        )}
      </div>

      <div className="flex flex-wrap gap-3">
        <SearchInput
          label="Search permissions"
          placeholder="Search by name or key"
          value={searchInput}
          onChange={(value) => {
            setSearchInput(value);
            setPage(0);
          }}
        />
        <select
          aria-label="Filter by resource"
          value={resourceType}
          onChange={(event) => {
            setResourceType(event.target.value);
            setPage(0);
          }}
          className={SELECT_CLASSES}
        >
          <option value="">All resources</option>
          <option value="database_data">Database data</option>
          <option value="system">System administration</option>
        </select>
        <select
          aria-label="Filter by status"
          value={status}
          onChange={(event) => {
            setStatus(event.target.value as StatusFilter);
            setPage(0);
          }}
          className={SELECT_CLASSES}
        >
          <option value="all">All statuses</option>
          <option value="active">Active</option>
          <option value="inactive">Inactive</option>
        </select>
      </div>

      {actionError && <Alert tone="error">{actionError}</Alert>}

      <div className="overflow-hidden rounded-2xl border border-stone-200 bg-white">
        {list.isPending ? (
          <Spinner label="Loading permissions" />
        ) : list.isError ? (
          <div className="p-6">
            <Alert tone="error">{errorMessage(list.error, "Permissions could not be loaded.")}</Alert>
          </div>
        ) : list.data.items.length === 0 ? (
          <div className="flex flex-col items-center px-6 py-14 text-center">
            <KeyRound aria-hidden="true" className="h-8 w-8 text-stone-300" strokeWidth={1.5} />
            <p className="mt-3 text-sm font-medium text-stone-900">No permissions found</p>
            <p className="mt-1 text-sm text-stone-500">Try a different search or filter.</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-stone-200 text-sm">
              <thead className="bg-stone-50 text-left text-xs font-medium uppercase tracking-wide text-stone-500">
                <tr>
                  <th scope="col" className="px-4 py-3">Permission</th>
                  <th scope="col" className="px-4 py-3">Resource</th>
                  <th scope="col" className="px-4 py-3">Action</th>
                  <th scope="col" className="px-4 py-3">Status</th>
                  <th scope="col" className="px-4 py-3 text-right">Roles</th>
                  <th scope="col" className="px-4 py-3 text-right">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-stone-100">
                {list.data.items.map((permission) => (
                  <tr key={permission.id} className="hover:bg-stone-50/60">
                    <td className="px-4 py-3">
                      <Link to={`/admin/permissions/${permission.id}`} className="font-medium text-stone-900 hover:underline">
                        {permission.name}
                      </Link>
                      {permission.is_system && <SystemTag />}
                      <p className="font-mono text-xs text-stone-500">{permission.key}</p>
                    </td>
                    <td className="px-4 py-3 font-mono text-xs text-stone-600">{permission.resource_type}</td>
                    <td className="px-4 py-3 font-mono text-xs text-stone-600">{permission.action}</td>
                    <td className="px-4 py-3">
                      <ActiveBadge active={permission.is_active} />
                      {permission.expires_at && <p className="mt-1 text-xs text-stone-500">Expires {formatDate(permission.expires_at)}</p>}
                    </td>
                    <td className="px-4 py-3 text-right text-stone-600">{permission.role_count}</td>
                    <td className="px-4 py-3 text-right">
                      <div className="flex justify-end gap-1">
                        <Button
                          variant="ghost"
                          onClick={() => navigate(`/admin/permissions/${permission.id}`)}
                          aria-label={`${canManage && !permission.is_system ? "Edit" : "View"} ${permission.name}`}
                        >
                          {canManage && !permission.is_system ? "Edit" : "View"}
                        </Button>
                        {canManage && !permission.is_system && (
                          <>
                            <ActiveToggle permission={permission} onError={setActionError} />
                            <Button variant="ghost" aria-label={`Delete ${permission.name}`} onClick={() => setToDelete(permission)}>
                              Delete
                            </Button>
                          </>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {list.data && <Pagination page={page} pageSize={PERMISSIONS_PAGE_SIZE} total={list.data.total} onPage={setPage} />}

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
        onConfirm={() => toDelete && remove.mutate(toDelete.id, { onSuccess: () => setToDelete(null) })}
      />
    </div>
  );
}

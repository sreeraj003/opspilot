import { zodResolver } from "@hookform/resolvers/zod";
import { Plus, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { Link, useNavigate } from "react-router";
import { z } from "zod";

import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog, Dialog } from "@/components/ui/Dialog";
import { TextField } from "@/components/ui/Field";
import { Spinner } from "@/components/ui/Spinner";
import { usePermissions } from "@/features/permissions/permissionsContext";
import { ActiveBadge, Pagination, SearchInput, SystemTag } from "@/features/rbac/components";
import { SELECT_CLASSES } from "@/features/rbac/fieldClasses";
import { useDebounced } from "@/hooks/useDebounced";
import { KEY_PATTERN, slugify } from "@/features/rbac/keys";
import { useCreateRole, useDeleteRole, useRoleList, useSetRoleActive } from "@/features/rbac/hooks";
import { useDocumentTitle } from "@/hooks/useDocumentTitle";
import type { Role, StatusFilter } from "@/types/rbac";
import { errorMessage } from "@/utils/errors";

export const ROLES_PAGE_SIZE = 20;

const roleSchema = z.object({
  name: z.string().trim().min(1, "Enter a name.").max(120, "Use at most 120 characters."),
  key: z
    .string()
    .trim()
    .min(2, "Use at least 2 characters.")
    .max(100, "Use at most 100 characters.")
    .regex(KEY_PATTERN, "Use lowercase letters, digits and underscores, such as regional_manager."),
  description: z.string().trim().max(1000, "Use at most 1000 characters."),
});
type RoleValues = z.infer<typeof roleSchema>;

function CreateRoleDialog({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: (role: Role) => void }) {
  const create = useCreateRole();
  const [keyEdited, setKeyEdited] = useState(false);
  const {
    register,
    handleSubmit,
    setValue,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<RoleValues>({ resolver: zodResolver(roleSchema), defaultValues: { name: "", key: "", description: "" } });

  const close = () => {
    reset();
    create.reset();
    setKeyEdited(false);
    onClose();
  };

  const onSubmit = handleSubmit(async (values) => {
    try {
      const role = await create.mutateAsync({ ...values, description: values.description || null });
      reset();
      setKeyEdited(false);
      onCreated(role);
    } catch {
      // Shown from the mutation state.
    }
  });

  const name = register("name");
  const key = register("key");
  return (
    <Dialog open={open} onClose={close} title="Create role" description="A role groups permissions. Assign it to users afterwards.">
      <form onSubmit={onSubmit} noValidate className="space-y-4">
        {create.isError && <Alert tone="error">{errorMessage(create.error, "The role could not be created.")}</Alert>}
        <TextField
          label="Name"
          error={errors.name?.message}
          {...name}
          onChange={(event) => {
            void name.onChange(event);
            if (!keyEdited) setValue("key", slugify(event.target.value));
          }}
        />
        <TextField
          label="Key"
          className="font-mono"
          hint="A stable identifier. It cannot be changed later."
          error={errors.key?.message}
          {...key}
          onChange={(event) => {
            setKeyEdited(true);
            void key.onChange(event);
          }}
        />
        <TextField label="Description (optional)" error={errors.description?.message} {...register("description")} />
        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={close}>
            Cancel
          </Button>
          <Button type="submit" loading={isSubmitting}>
            Create role
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

export function RolesPage() {
  useDocumentTitle("Roles");
  const navigate = useNavigate();
  const { hasPermission } = usePermissions();
  const canManage = hasPermission("roles:manage");
  const [searchInput, setSearchInput] = useState("");
  const search = useDebounced(searchInput.trim());
  const [status, setStatus] = useState<StatusFilter>("all");
  const [page, setPage] = useState(0);
  const list = useRoleList({ offset: page * ROLES_PAGE_SIZE, limit: ROLES_PAGE_SIZE, search: search || undefined, status });
  const setActive = useSetRoleActive();
  const remove = useDeleteRole();
  const [creating, setCreating] = useState(false);
  const [toDelete, setToDelete] = useState<Role | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-stone-900">Roles</h1>
          <p className="mt-1 text-sm text-stone-500">Roles group permissions. A user's access is the sum of their roles and direct permissions.</p>
        </div>
        {canManage && (
          <Button onClick={() => setCreating(true)}>
            <Plus aria-hidden="true" className="h-4 w-4" />
            Create role
          </Button>
        )}
      </div>

      <div className="flex flex-wrap gap-3">
        <SearchInput
          label="Search roles"
          placeholder="Search by name or key"
          value={searchInput}
          onChange={(value) => {
            setSearchInput(value);
            setPage(0);
          }}
        />
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
          <Spinner label="Loading roles" />
        ) : list.isError ? (
          <div className="p-6">
            <Alert tone="error">{errorMessage(list.error, "Roles could not be loaded.")}</Alert>
          </div>
        ) : list.data.items.length === 0 ? (
          <div className="flex flex-col items-center px-6 py-14 text-center">
            <ShieldCheck aria-hidden="true" className="h-8 w-8 text-stone-300" strokeWidth={1.5} />
            <p className="mt-3 text-sm font-medium text-stone-900">No roles found</p>
            <p className="mt-1 text-sm text-stone-500">{search || status !== "all" ? "Try a different search or filter." : "Create a role to get started."}</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-stone-200 text-sm">
              <thead className="bg-stone-50 text-left text-xs font-medium uppercase tracking-wide text-stone-500">
                <tr>
                  <th scope="col" className="px-4 py-3">Role</th>
                  <th scope="col" className="px-4 py-3">Key</th>
                  <th scope="col" className="px-4 py-3">Status</th>
                  <th scope="col" className="px-4 py-3 text-right">Permissions</th>
                  <th scope="col" className="px-4 py-3 text-right">Users</th>
                  <th scope="col" className="px-4 py-3 text-right">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-stone-100">
                {list.data.items.map((role) => (
                  <tr key={role.id} className="hover:bg-stone-50/60">
                    <td className="px-4 py-3">
                      <Link to={`/admin/roles/${role.id}`} className="font-medium text-stone-900 hover:underline">
                        {role.name}
                      </Link>
                      {role.is_system && <SystemTag />}
                      {role.description && <p className="mt-0.5 text-xs text-stone-500">{role.description}</p>}
                    </td>
                    <td className="px-4 py-3 font-mono text-xs text-stone-600">{role.key}</td>
                    <td className="px-4 py-3">
                      <ActiveBadge active={role.is_active} />
                    </td>
                    <td className="px-4 py-3 text-right text-stone-600">{role.permission_count}</td>
                    <td className="px-4 py-3 text-right text-stone-600">{role.user_count}</td>
                    <td className="px-4 py-3 text-right">
                      <div className="flex justify-end gap-1">
                        <Button variant="ghost" onClick={() => navigate(`/admin/roles/${role.id}`)} aria-label={`View ${role.name}`}>
                          View
                        </Button>
                        {canManage && !role.is_system && (
                          <>
                            <Button
                              variant="ghost"
                              aria-label={`${role.is_active ? "Deactivate" : "Activate"} ${role.name}`}
                              loading={setActive.isPending && setActive.variables?.id === role.id}
                              onClick={() => {
                                setActionError(null);
                                setActive.mutate({ id: role.id, active: !role.is_active }, { onError: (error) => setActionError(errorMessage(error)) });
                              }}
                            >
                              {role.is_active ? "Deactivate" : "Activate"}
                            </Button>
                            <Button variant="ghost" aria-label={`Delete ${role.name}`} onClick={() => setToDelete(role)}>
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

      {list.data && <Pagination page={page} pageSize={ROLES_PAGE_SIZE} total={list.data.total} onPage={setPage} />}

      <CreateRoleDialog
        open={creating}
        onClose={() => setCreating(false)}
        onCreated={(role) => {
          setCreating(false);
          navigate(`/admin/roles/${role.id}`);
        }}
      />

      <ConfirmDialog
        open={toDelete !== null}
        title="Delete role?"
        description={
          toDelete && (
            <>
              <strong className="font-medium text-stone-900">{toDelete.name}</strong> is removed from{" "}
              {toDelete.user_count === 1 ? "1 user" : `${toDelete.user_count} users`} immediately. Its history is kept for auditing.
            </>
          )
        }
        confirmLabel="Delete role"
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

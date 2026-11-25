import { zodResolver } from "@hookform/resolvers/zod";
import { Plus, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { Link, useNavigate } from "react-router";
import { z } from "zod";

import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { DataTable, type DataTableColumn } from "@/components/ui/DataTable";
import { ConfirmDialog, Dialog, DialogActions } from "@/components/ui/Dialog";
import { TextField } from "@/components/ui/Field";
import { Select } from "@/components/ui/Input";
import { PageHeader } from "@/components/ui/Layout";
import { useToast } from "@/components/ui/toastContext";
import { usePermissions } from "@/features/permissions/permissionsContext";
import { ActiveBadge, Pagination, SearchInput, SystemTag } from "@/features/rbac/components";
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
        <DialogActions>
          <Button variant="secondary" onClick={close}>
            Cancel
          </Button>
          <Button type="submit" loading={isSubmitting}>
            Create role
          </Button>
        </DialogActions>
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
  const toast = useToast();

  const columns: DataTableColumn<Role>[] = [
    {
      id: "role",
      header: "Role",
      cell: (role) => (
        <div className="min-w-0">
          <Link to={`/admin/roles/${role.id}`} className="font-medium text-fg hover:underline">
            {role.name}
          </Link>
          {role.is_system && <SystemTag />}
          {role.description && <p className="mt-0.5 line-clamp-2 max-w-md text-xs text-fg-muted">{role.description}</p>}
        </div>
      ),
    },
    { id: "key", header: "Key", cell: (role) => <span className="font-mono text-xs">{role.key}</span>, hideBelow: "md" },
    { id: "status", header: "Status", cell: (role) => <ActiveBadge active={role.is_active} /> },
    { id: "permissions", header: "Permissions", align: "right", cell: (role) => role.permission_count, hideBelow: "sm" },
    { id: "users", header: "Users", align: "right", cell: (role) => role.user_count, hideBelow: "sm" },
    {
      id: "actions",
      header: <span className="sr-only">Actions</span>,
      align: "right",
      cell: (role) => (
        <div className="flex justify-end gap-1">
          <Button variant="ghost" size="sm" onClick={() => navigate(`/admin/roles/${role.id}`)} aria-label={`View ${role.name}`}>
            View
          </Button>
          {canManage && !role.is_system && (
            <>
              <Button
                variant="ghost"
                size="sm"
                aria-label={`${role.is_active ? "Deactivate" : "Activate"} ${role.name}`}
                loading={setActive.isPending && setActive.variables?.id === role.id}
                onClick={() => {
                  setActive.mutate(
                    { id: role.id, active: !role.is_active },
                    {
                      onSuccess: () => toast.notify({ title: role.is_active ? "Role deactivated" : "Role activated", description: role.name }),
                      onError: (error) => toast.notify({ tone: "error", title: "Could not change the role", description: errorMessage(error) }),
                    },
                  );
                }}
              >
                {role.is_active ? "Deactivate" : "Activate"}
              </Button>
              <Button variant="danger-ghost" size="sm" aria-label={`Delete ${role.name}`} onClick={() => setToDelete(role)}>
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
        title="Roles"
        description="Roles group permissions. A user's access is the sum of their roles and direct permissions."
        actions={
          canManage && (
            <Button onClick={() => setCreating(true)}>
              <Plus aria-hidden="true" className="h-4 w-4" />
              Create role
            </Button>
          )
        }
      />

      <div className="flex flex-col gap-3 sm:flex-row sm:flex-wrap">
        <SearchInput
          label="Search roles"
          placeholder="Search by name or key"
          value={searchInput}
          onChange={(value) => {
            setSearchInput(value);
            setPage(0);
          }}
        />
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
        caption="Roles"
        columns={columns}
        rows={list.data?.items}
        getRowId={(role) => role.id}
        loading={list.isPending}
        loadingLabel="Loading roles"
        error={list.isError ? errorMessage(list.error, "Roles could not be loaded.") : null}
        onRetry={() => void list.refetch()}
        retrying={list.isFetching}
        empty={{
          icon: ShieldCheck,
          title: "No roles found",
          description: search || status !== "all" ? "Try a different search or filter." : "Create a role to get started.",
        }}
        footer={list.data && <Pagination page={page} pageSize={ROLES_PAGE_SIZE} total={list.data.total} onPage={setPage} itemLabel="Roles" />}
      />

      <CreateRoleDialog
        open={creating}
        onClose={() => setCreating(false)}
        onCreated={(role) => {
          setCreating(false);
          toast.notify({ title: "Role created", description: role.name });
          navigate(`/admin/roles/${role.id}`);
        }}
      />

      <ConfirmDialog
        open={toDelete !== null}
        title="Delete role?"
        description={
          toDelete && (
            <>
              <strong className="font-medium text-fg">{toDelete.name}</strong> is removed from{" "}
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
        onConfirm={() =>
          toDelete &&
          remove.mutate(toDelete.id, {
            onSuccess: () => {
              toast.notify({ title: "Role deleted", description: toDelete.name });
              setToDelete(null);
            },
          })
        }
      />
    </div>
  );
}

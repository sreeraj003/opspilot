import { ArrowLeft } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog, Dialog } from "@/components/ui/Dialog";
import { TextField } from "@/components/ui/Field";
import { Spinner } from "@/components/ui/Spinner";
import { usePermissions } from "@/features/permissions/permissionsContext";
import { ActiveBadge, Card, SearchInput, SystemTag } from "@/features/rbac/components";
import { useDebounced } from "@/hooks/useDebounced";
import { formatEventType } from "@/utils/format";
import { ADMINISTRATOR_ONLY } from "@/features/rbac/keys";
import { usePermissionList, useRole, useSetRoleActive, useDeleteRole, useSetRolePermissions, useUpdateRole } from "@/features/rbac/hooks";
import { useDocumentTitle } from "@/hooks/useDocumentTitle";
import { ApiError } from "@/services/apiClient";
import type { PermissionSummary, RoleDetail } from "@/types/rbac";
import { errorMessage } from "@/utils/errors";
import { formatDate, formatDateTime } from "@/utils/format";

function RoleDetailsForm({ role, canManage }: { role: RoleDetail; canManage: boolean }) {
  const update = useUpdateRole(role.id);
  const [name, setName] = useState(role.name);
  const [description, setDescription] = useState(role.description ?? "");
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    setName(role.name);
    setDescription(role.description ?? "");
  }, [role]);
  const dirty = name.trim() !== role.name || description.trim() !== (role.description ?? "");

  if (!canManage) {
    return <p className="text-sm text-stone-600">{role.description || "No description."}</p>;
  }
  return (
    <form
      noValidate
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault();
        setSaved(false);
        const changes = role.is_system ? { description: description.trim() || null } : { name: name.trim(), description: description.trim() || null };
        update.mutate(changes, { onSuccess: () => setSaved(true) });
      }}
    >
      {saved && !dirty && <Alert tone="success">Changes saved.</Alert>}
      {update.isError && <Alert tone="error">{errorMessage(update.error, "Changes could not be saved.")}</Alert>}
      <div className="grid gap-4 sm:grid-cols-2">
        <TextField
          label="Name"
          value={name}
          disabled={role.is_system}
          hint={role.is_system ? "System roles cannot be renamed." : undefined}
          error={!name.trim() ? "Enter a name." : undefined}
          onChange={(event) => setName(event.target.value)}
        />
        <TextField label="Description" value={description} onChange={(event) => setDescription(event.target.value)} />
      </div>
      <Button type="submit" loading={update.isPending} disabled={!dirty || !name.trim()}>
        Save changes
      </Button>
    </form>
  );
}

/** Pick the role's permissions, review the difference, then save it in one request. */
export function RolePermissionsEditor({ role, canManage }: { role: RoleDetail; canManage: boolean }) {
  const current = useMemo(() => new Set(role.permissions.map((p) => p.id)), [role.permissions]);
  const [selected, setSelected] = useState<Set<string>>(current);
  const [editing, setEditing] = useState(false);
  const [reviewing, setReviewing] = useState(false);
  const [searchInput, setSearchInput] = useState("");
  const search = useDebounced(searchInput.trim());
  const catalog = usePermissionList({ offset: 0, limit: 100, search: search || undefined, status: "active" }, editing);
  const save = useSetRolePermissions(role.id);
  const [known, setKnown] = useState<Map<string, PermissionSummary>>(new Map());

  useEffect(() => setSelected(current), [current]);
  useEffect(() => {
    if (!catalog.data) return;
    setKnown((previous) => {
      const next = new Map(previous);
      catalog.data.items.forEach((p) => next.set(p.id, p));
      return next;
    });
  }, [catalog.data]);

  const byId = useMemo(() => {
    const map = new Map<string, PermissionSummary>(known);
    role.permissions.forEach((p) => map.set(p.id, p));
    return map;
  }, [known, role.permissions]);
  const added = [...selected].filter((id) => !current.has(id)).map((id) => byId.get(id)!);
  const removed = [...current].filter((id) => !selected.has(id)).map((id) => byId.get(id)!);
  const changed = added.length + removed.length > 0;

  const toggle = (id: string) =>
    setSelected((previous) => {
      const next = new Set(previous);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const editable = canManage && !role.is_system;

  return (
    <Card
      title="Permissions"
      description={role.is_system ? "The permissions of system roles are fixed." : "Everything a user with this role may do."}
      actions={
        editable &&
        !editing && (
          <Button variant="secondary" onClick={() => setEditing(true)}>
            Edit permissions
          </Button>
        )
      }
    >
      {!editing ? (
        role.permissions.length === 0 ? (
          <p className="text-sm text-stone-500">This role has no permissions yet.</p>
        ) : (
          <ul className="divide-y divide-stone-100 text-sm" aria-label="Assigned permissions">
            {role.permissions.map((p) => (
              <li key={p.id} className="flex items-start justify-between gap-4 py-2.5">
                <div>
                  <Link to={`/admin/permissions/${p.id}`} className="font-medium text-stone-900 hover:underline">
                    {p.name}
                  </Link>
                  <p className="font-mono text-xs text-stone-500">{p.key}</p>
                </div>
                {!p.is_active && <ActiveBadge active={false} />}
              </li>
            ))}
          </ul>
        )
      ) : (
        <div className="space-y-4">
          <SearchInput label="Search permissions" placeholder="Search by name or key" value={searchInput} onChange={setSearchInput} />
          {catalog.isPending ? (
            <Spinner label="Loading permissions" />
          ) : catalog.isError ? (
            <Alert tone="error">{errorMessage(catalog.error)}</Alert>
          ) : catalog.data.items.length === 0 ? (
            <p className="text-sm text-stone-500">No permissions match.</p>
          ) : (
            <ul className="max-h-96 divide-y divide-stone-100 overflow-y-auto rounded-lg border border-stone-200 text-sm">
              {catalog.data.items.map((p) => {
                const reserved = ADMINISTRATOR_ONLY.has(p.key);
                return (
                  <li key={p.id} className="px-3 py-2">
                    <label className="flex items-start gap-3">
                      <input
                        type="checkbox"
                        className="mt-1 h-4 w-4"
                        checked={selected.has(p.id)}
                        disabled={reserved}
                        onChange={() => toggle(p.id)}
                        aria-label={p.name}
                      />
                      <span>
                        <span className="font-medium text-stone-900">{p.name}</span>
                        <span className="block font-mono text-xs text-stone-500">{p.key}</span>
                        {reserved && <span className="block text-xs text-stone-500">Only the Administrator role grants this.</span>}
                      </span>
                    </label>
                  </li>
                );
              })}
            </ul>
          )}
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-sm text-stone-600">
              {changed ? `${added.length} to add, ${removed.length} to remove` : "No changes yet."}
            </p>
            <div className="flex gap-2">
              <Button
                variant="secondary"
                onClick={() => {
                  setSelected(current);
                  setEditing(false);
                  save.reset();
                }}
              >
                Cancel
              </Button>
              <Button disabled={!changed} onClick={() => setReviewing(true)}>
                Review changes
              </Button>
            </div>
          </div>
        </div>
      )}

      <Dialog open={reviewing} onClose={() => setReviewing(false)} title="Review permission changes" description={`Users with the role ${role.name} gain or lose this access immediately.`}>
        <div className="space-y-4 text-sm">
          {added.length > 0 && (
            <div>
              <h3 className="font-medium text-emerald-700">Add ({added.length})</h3>
              <ul className="mt-1 list-inside list-disc text-stone-700" aria-label="Permissions to add">
                {added.map((p) => (
                  <li key={p.id}>
                    {p.name} <span className="font-mono text-xs text-stone-500">{p.key}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {removed.length > 0 && (
            <div>
              <h3 className="font-medium text-rose-700">Remove ({removed.length})</h3>
              <ul className="mt-1 list-inside list-disc text-stone-700" aria-label="Permissions to remove">
                {removed.map((p) => (
                  <li key={p.id}>
                    {p.name} <span className="font-mono text-xs text-stone-500">{p.key}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          <p className="text-stone-600">Affects {role.user_count === 1 ? "1 user" : `${role.user_count} users`}.</p>
          {save.isError && (
            <p role="alert" className="font-medium text-rose-600">
              {errorMessage(save.error, "The changes could not be saved.")}
            </p>
          )}
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setReviewing(false)}>
              Back
            </Button>
            <Button
              loading={save.isPending}
              onClick={() =>
                save.mutate([...selected], {
                  onSuccess: () => {
                    setReviewing(false);
                    setEditing(false);
                  },
                })
              }
            >
              Save changes
            </Button>
          </div>
        </div>
      </Dialog>
    </Card>
  );
}

export function RoleDetailPage() {
  const { roleId = "" } = useParams();
  const navigate = useNavigate();
  const { hasPermission } = usePermissions();
  const canManage = hasPermission("roles:manage");
  const detail = useRole(roleId);
  const setActive = useSetRoleActive();
  const remove = useDeleteRole();
  const [confirm, setConfirm] = useState<"delete" | "deactivate" | null>(null);
  useDocumentTitle(detail.data?.name ?? "Role");

  const back = (
    <Link to="/admin/roles" className="inline-flex items-center gap-1.5 text-sm text-stone-600 hover:text-stone-900">
      <ArrowLeft aria-hidden="true" className="h-4 w-4" /> All roles
    </Link>
  );

  if (detail.isPending) return <Spinner label="Loading role" />;
  if (detail.isError) {
    const missing = detail.error instanceof ApiError && detail.error.status === 404;
    return (
      <div className="space-y-4">
        {back}
        <Alert tone="error">{missing ? "This role does not exist or was deleted." : errorMessage(detail.error)}</Alert>
      </div>
    );
  }
  const role = detail.data;
  const mutation = confirm === "delete" ? remove : setActive;

  return (
    <div className="space-y-6">
      {back}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-stone-900">
            {role.name}
            {role.is_system && <SystemTag />}
          </h1>
          <p className="mt-1 font-mono text-sm text-stone-500">{role.key}</p>
        </div>
        <div className="flex items-center gap-2">
          <ActiveBadge active={role.is_active} />
          {canManage && !role.is_system && (
            <>
              {role.is_active ? (
                <Button variant="secondary" onClick={() => setConfirm("deactivate")}>
                  Deactivate
                </Button>
              ) : (
                <Button variant="secondary" loading={setActive.isPending} onClick={() => setActive.mutate({ id: role.id, active: true })}>
                  Activate
                </Button>
              )}
              <Button variant="ghost" className="text-rose-600 hover:bg-rose-50" onClick={() => setConfirm("delete")}>
                Delete
              </Button>
            </>
          )}
        </div>
      </div>

      {setActive.isError && !confirm && <Alert tone="error">{errorMessage(setActive.error)}</Alert>}

      <div className="grid gap-6 lg:grid-cols-3">
        <div className="space-y-6 lg:col-span-2">
          <Card title="Details">
            <RoleDetailsForm role={role} canManage={canManage} />
          </Card>
          <RolePermissionsEditor role={role} canManage={canManage} />
        </div>
        <div className="space-y-6">
          <Card title="Users" description={role.users.length ? undefined : "No one has this role."}>
            {role.users.length > 0 && (
              <ul className="divide-y divide-stone-100 text-sm" aria-label="Users with this role">
                {role.users.map((u) => (
                  <li key={u.id} className="py-2">
                    <Link to={`/admin/users/${u.id}`} className="font-medium text-stone-900 hover:underline">
                      {u.full_name}
                    </Link>
                    <p className="text-xs text-stone-500">
                      {u.email}
                      {u.expires_at ? ` · until ${formatDate(u.expires_at)}` : ""}
                      {!u.is_active ? " · inactive" : ""}
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </Card>
          <Card title="History">
            {role.history.length === 0 ? (
              <p className="text-sm text-stone-500">No changes recorded.</p>
            ) : (
              <ol className="space-y-2 text-sm">
                {role.history.map((event) => (
                  <li key={event.id}>
                    <p className="text-stone-800">{formatEventType(event.event_type)}</p>
                    <p className="text-xs text-stone-500">{formatDateTime(event.created_at)}</p>
                  </li>
                ))}
              </ol>
            )}
          </Card>
        </div>
      </div>

      <ConfirmDialog
        open={confirm !== null}
        title={confirm === "delete" ? "Delete role?" : "Deactivate role?"}
        description={
          confirm === "delete"
            ? `${role.name} will be deleted. Its history is kept for auditing.`
            : `Users with ${role.name} lose the access it grants immediately, until it is activated again.`
        }
        confirmLabel={confirm === "delete" ? "Delete role" : "Deactivate"}
        pending={mutation.isPending}
        error={mutation.isError ? errorMessage(mutation.error) : null}
        onCancel={() => {
          setConfirm(null);
          remove.reset();
          setActive.reset();
        }}
        onConfirm={() => {
          if (confirm === "delete") remove.mutate(role.id, { onSuccess: () => navigate("/admin/roles", { replace: true }) });
          else setActive.mutate({ id: role.id, active: false }, { onSuccess: () => setConfirm(null) });
        }}
      />
    </div>
  );
}

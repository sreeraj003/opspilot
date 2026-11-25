import { Trash2 } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";

import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/Dialog";
import { Spinner } from "@/components/ui/Spinner";
import { useAuth } from "@/features/auth/authContext";
import { usePermissions } from "@/features/permissions/permissionsContext";
import { Card, INPUT_CLASSES, SELECT_CLASSES } from "@/features/rbac/components";
import {
  useAssignUserPermission,
  useAssignUserRole,
  useEffectivePermissions,
  usePermissionList,
  useRevokeUserPermission,
  useRevokeUserRole,
  useRoleList,
  useUserPermissions,
  useUserRoles,
} from "@/features/rbac/hooks";
import { ADMINISTRATOR_ONLY } from "@/features/rbac/keys";
import { describeNode } from "@/features/rbac/policyBuilder";
import type { EffectivePermissions } from "@/types/rbac";
import { errorMessage } from "@/utils/errors";
import { formatDate } from "@/utils/format";

function endOfDay(date: string): string | null {
  return date ? new Date(`${date}T23:59:59`).toISOString() : null;
}

function AssignRoleForm({ userId, assigned }: { userId: string; assigned: Set<string> }) {
  const roles = useRoleList({ offset: 0, limit: 100, status: "active" });
  const assign = useAssignUserRole(userId);
  const [roleId, setRoleId] = useState("");
  const [expires, setExpires] = useState("");
  const options = roles.data?.items.filter((role) => !assigned.has(role.id)) ?? [];
  return (
    <form
      className="mt-4 space-y-2 border-t border-stone-100 pt-4"
      onSubmit={(event) => {
        event.preventDefault();
        if (!roleId) return;
        assign.mutate({ roleId, expiresAt: endOfDay(expires) }, { onSuccess: () => (setRoleId(""), setExpires("")) });
      }}
    >
      <div className="flex flex-wrap items-end gap-2">
        <select aria-label="Role to assign" value={roleId} onChange={(event) => setRoleId(event.target.value)} className={`${SELECT_CLASSES} min-w-48 flex-1`}>
          <option value="">{roles.isPending ? "Loading roles…" : "Choose a role…"}</option>
          {options.map((role) => (
            <option key={role.id} value={role.id}>
              {role.name}
            </option>
          ))}
        </select>
        <input type="date" aria-label="Role expires on (optional)" value={expires} onChange={(event) => setExpires(event.target.value)} className={`${INPUT_CLASSES} w-44`} />
        <Button type="submit" disabled={!roleId} loading={assign.isPending}>
          Assign role
        </Button>
      </div>
      {assign.isError && (
        <p role="alert" className="text-sm font-medium text-rose-600">
          {errorMessage(assign.error, "The role could not be assigned.")}
        </p>
      )}
    </form>
  );
}

function AssignPermissionForm({ userId, assigned }: { userId: string; assigned: Set<string> }) {
  const permissions = usePermissionList({ offset: 0, limit: 100, status: "active" });
  const assign = useAssignUserPermission(userId);
  const [permissionId, setPermissionId] = useState("");
  const [type, setType] = useState<"grant" | "deny">("grant");
  const [expires, setExpires] = useState("");
  const options = permissions.data?.items.filter((p) => !assigned.has(p.id) && !ADMINISTRATOR_ONLY.has(p.key)) ?? [];
  return (
    <form
      className="mt-4 space-y-2 border-t border-stone-100 pt-4"
      onSubmit={(event) => {
        event.preventDefault();
        if (!permissionId) return;
        assign.mutate(
          { permission_id: permissionId, assignment_type: type, expires_at: endOfDay(expires) },
          { onSuccess: () => (setPermissionId(""), setExpires("")) },
        );
      }}
    >
      <div className="flex flex-wrap items-end gap-2">
        <select aria-label="Permission" value={permissionId} onChange={(event) => setPermissionId(event.target.value)} className={`${SELECT_CLASSES} min-w-48 flex-1`}>
          <option value="">{permissions.isPending ? "Loading permissions…" : "Choose a permission…"}</option>
          {options.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name} ({p.key})
            </option>
          ))}
        </select>
        <select aria-label="Grant or deny" value={type} onChange={(event) => setType(event.target.value as "grant" | "deny")} className={SELECT_CLASSES}>
          <option value="grant">Grant</option>
          <option value="deny">Deny</option>
        </select>
        <input type="date" aria-label="Permission expires on (optional)" value={expires} onChange={(event) => setExpires(event.target.value)} className={`${INPUT_CLASSES} w-44`} />
        <Button type="submit" disabled={!permissionId} loading={assign.isPending}>
          Add
        </Button>
      </div>
      <p className="text-xs text-stone-500">Deny removes this permission even when one of the user's roles grants it.</p>
      {assign.isError && (
        <p role="alert" className="text-sm font-medium text-rose-600">
          {errorMessage(assign.error, "The permission could not be assigned.")}
        </p>
      )}
    </form>
  );
}

export function EffectivePermissionsView({ effective }: { effective: EffectivePermissions }) {
  if (!effective.is_active) return <p className="text-sm text-stone-600">This account is inactive, so it has no access.</p>;
  const data = effective.data_access.flatMap((entry) => entry.tables.map((table) => ({ ...table, action: entry.action })));
  return (
    <div className="space-y-4 text-sm">
      {effective.is_administrator && <p className="text-stone-700">Administrator: can manage users, roles and permissions. Data access still needs explicit permissions.</p>}
      <div>
        <h3 className="font-medium text-stone-700">Permissions</h3>
        {effective.permission_keys.length === 0 ? (
          <p className="mt-1 text-stone-500">None. Without permissions, every request is denied.</p>
        ) : (
          <ul className="mt-1 flex flex-wrap gap-1.5" aria-label="Effective permissions">
            {effective.permission_keys.map((key) => (
              <li key={key} className="rounded-md bg-stone-100 px-2 py-0.5 font-mono text-xs text-stone-800">
                {key}
              </li>
            ))}
          </ul>
        )}
        {effective.revoked_permission_keys.length > 0 && (
          <p className="mt-2 text-xs text-stone-500">Denied directly: {effective.revoked_permission_keys.join(", ")}</p>
        )}
      </div>
      {data.length > 0 && (
        <div>
          <h3 className="font-medium text-stone-700">Data access</h3>
          <ul className="mt-1 space-y-2" aria-label="Data access">
            {data.map((table) => (
              <li key={`${table.action}:${table.table}`} className="rounded-lg border border-stone-200 p-3">
                <p className="font-mono text-xs font-medium text-stone-900">
                  {table.table} · {table.action}
                </p>
                <p className="mt-1 text-xs text-stone-600">Columns: {table.columns.includes("*") ? "all" : table.columns.join(", ")}</p>
                {table.denied_columns.length > 0 && <p className="text-xs text-stone-600">Hidden columns: {table.denied_columns.join(", ")}</p>}
                <p className="text-xs text-stone-600">Operations: {table.operations.join(", ")}</p>
                <p className="text-xs text-stone-600">Rows: {table.row_filter ? describeNode(table.row_filter) : "all"}</p>
                <p className="text-xs text-stone-600">
                  Export {table.allow_export ? "allowed" : "not allowed"} · SQL view {table.allow_sql_view ? "allowed" : "not allowed"}
                </p>
              </li>
            ))}
          </ul>
        </div>
      )}
      {effective.restrictions.length > 0 && (
        <div>
          <h3 className="font-medium text-stone-700">Restrictions</h3>
          <ul className="mt-1 list-inside list-disc text-stone-600">
            {effective.restrictions.map((r) => (
              <li key={`${r.key}:${r.source}`}>{r.name}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

/** Roles, direct permissions and effective access of one user, on the user's page. */
export function UserAccessSection({ userId }: { userId: string }) {
  const { user: currentUser } = useAuth();
  const { hasPermission } = usePermissions();
  const canReview = hasPermission("access:review");
  const isSelf = currentUser?.id === userId;
  const canAssign = hasPermission("access:assign") && (!isSelf || currentUser?.is_superuser === true);
  const roles = useUserRoles(userId, canReview);
  const direct = useUserPermissions(userId, canReview);
  const effective = useEffectivePermissions(userId, canReview);
  const revokeRole = useRevokeUserRole(userId);
  const revokePermission = useRevokeUserPermission(userId);
  const [toRevoke, setToRevoke] = useState<{ kind: "role" | "permission"; id: string; name: string } | null>(null);

  if (!canReview) return null;
  const revoking = toRevoke?.kind === "role" ? revokeRole : revokePermission;

  return (
    <>
      <Card title="Roles" description={isSelf && !canAssign ? "You cannot change your own access." : undefined}>
        {roles.isPending ? (
          <Spinner label="Loading roles" />
        ) : roles.isError ? (
          <Alert tone="error">{errorMessage(roles.error)}</Alert>
        ) : roles.data.length === 0 ? (
          <p className="text-sm text-stone-500">No roles assigned.</p>
        ) : (
          <ul className="divide-y divide-stone-100 text-sm" aria-label="Assigned roles">
            {roles.data.map((assignment) => (
              <li key={assignment.role.id} className="flex items-center justify-between gap-3 py-2">
                <div>
                  <Link to={`/admin/roles/${assignment.role.id}`} className="font-medium text-stone-900 hover:underline">
                    {assignment.role.name}
                  </Link>
                  <p className="text-xs text-stone-500">
                    {assignment.is_expired ? "Expired" : assignment.expires_at ? `Until ${formatDate(assignment.expires_at)}` : "No expiry"}
                    {!assignment.role.is_active ? " · role inactive" : ""}
                  </p>
                </div>
                {canAssign && (
                  <Button
                    variant="ghost"
                    aria-label={`Remove role ${assignment.role.name}`}
                    onClick={() => setToRevoke({ kind: "role", id: assignment.role.id, name: assignment.role.name })}
                  >
                    <Trash2 aria-hidden="true" className="h-4 w-4" />
                  </Button>
                )}
              </li>
            ))}
          </ul>
        )}
        {canAssign && roles.data && <AssignRoleForm userId={userId} assigned={new Set(roles.data.map((a) => a.role.id))} />}
      </Card>

      <Card title="Direct permissions" description="Granted or denied to this user only, in addition to their roles.">
        {direct.isPending ? (
          <Spinner label="Loading permissions" />
        ) : direct.isError ? (
          <Alert tone="error">{errorMessage(direct.error)}</Alert>
        ) : direct.data.length === 0 ? (
          <p className="text-sm text-stone-500">None.</p>
        ) : (
          <ul className="divide-y divide-stone-100 text-sm" aria-label="Direct permissions">
            {direct.data.map((assignment) => (
              <li key={assignment.permission.id} className="flex items-center justify-between gap-3 py-2">
                <div>
                  <p className="font-medium text-stone-900">
                    {assignment.assignment_type === "deny" ? "Denied: " : ""}
                    {assignment.permission.name}
                  </p>
                  <p className="font-mono text-xs text-stone-500">
                    {assignment.permission.key}
                    {assignment.expires_at ? ` · until ${formatDate(assignment.expires_at)}` : ""}
                  </p>
                </div>
                {canAssign && (
                  <Button
                    variant="ghost"
                    aria-label={`Remove permission ${assignment.permission.name}`}
                    onClick={() => setToRevoke({ kind: "permission", id: assignment.permission.id, name: assignment.permission.name })}
                  >
                    <Trash2 aria-hidden="true" className="h-4 w-4" />
                  </Button>
                )}
              </li>
            ))}
          </ul>
        )}
        {canAssign && direct.data && <AssignPermissionForm userId={userId} assigned={new Set(direct.data.map((a) => a.permission.id))} />}
      </Card>

      <Card title="Effective access" description="What this user can do right now, after combining roles, direct grants, denials and expiry.">
        {effective.isPending ? (
          <Spinner label="Loading effective access" />
        ) : effective.isError ? (
          <Alert tone="error">{errorMessage(effective.error)}</Alert>
        ) : (
          <EffectivePermissionsView effective={effective.data} />
        )}
      </Card>

      <ConfirmDialog
        open={toRevoke !== null}
        title={toRevoke?.kind === "role" ? "Remove role?" : "Remove permission?"}
        description={toRevoke && `${toRevoke.name} is removed from this user immediately.`}
        confirmLabel="Remove"
        pending={revoking.isPending}
        error={revoking.isError ? errorMessage(revoking.error) : null}
        onCancel={() => {
          setToRevoke(null);
          revokeRole.reset();
          revokePermission.reset();
        }}
        onConfirm={() => {
          if (!toRevoke) return;
          const done = { onSuccess: () => setToRevoke(null) };
          if (toRevoke.kind === "role") revokeRole.mutate(toRevoke.id, done);
          else revokePermission.mutate(toRevoke.id, done);
        }}
      />
    </>
  );
}

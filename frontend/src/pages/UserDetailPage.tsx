import { ArrowLeft, Copy, KeyRound, LogOut, Monitor, Pencil, Power, Trash2 } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { DetailList } from "@/components/DetailList";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { ConfirmDialog, Dialog } from "@/components/ui/Dialog";
import { ErrorState } from "@/components/ui/ErrorState";
import { controlClasses } from "@/components/ui/controlStyles";
import { PageHeader } from "@/components/ui/Layout";
import { SkeletonText } from "@/components/ui/Skeleton";
import { Spinner } from "@/components/ui/Spinner";
import { useToast } from "@/components/ui/toastContext";
import { useAuth } from "@/features/auth/authContext";
import { usePermissions } from "@/features/permissions/permissionsContext";
import { UserAccessSection } from "@/features/rbac/UserAccessSection";
import { EditUserDialog } from "@/features/users/EditUserDialog";
import {
  useDeleteUser,
  useRevokeUserSessions,
  useSetUserActive,
  useStartPasswordReset,
  useUserDetail,
  useUserSessions,
} from "@/features/users/hooks";
import { UserStatusBadge } from "@/features/users/UserStatusBadge";
import { useDocumentTitle } from "@/hooks/useDocumentTitle";
import { ApiError } from "@/services/apiClient";
import type { AdminPasswordResetResponse } from "@/types/auth";
import { errorMessage } from "@/utils/errors";
import { formatDateTime } from "@/utils/format";

function BackLink() {
  return (
    <Link to="/admin/users" className="inline-flex items-center gap-1.5 rounded-xs text-sm text-fg-muted hover:text-fg">
      <ArrowLeft aria-hidden="true" className="h-4 w-4" /> All users
    </Link>
  );
}

type PendingAction = "deactivate" | "activate" | "revoke" | "reset" | "delete" | null;

function ResetLinkDialog({ result, onClose }: { result: AdminPasswordResetResponse | null; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  return (
    <Dialog
      open={result !== null}
      onClose={() => {
        setCopied(false);
        onClose();
      }}
      title="One-time password reset link"
      description="Give this link to the user over a trusted channel. It works once and expires; it will not be shown again."
    >
      {result?.reset_url && (
        <div className="space-y-3">
          <input
            readOnly
            aria-label="Password reset link"
            value={result.reset_url}
            onFocus={(event) => event.currentTarget.select()}
            className={controlClasses({ className: "bg-surface-muted font-mono text-xs" })}
          />
          <p className="text-xs text-fg-muted">Expires {formatDateTime(result.expires_at)}.</p>
          <Button
            variant="secondary"
            onClick={async () => {
              await navigator.clipboard?.writeText(result.reset_url ?? "");
              setCopied(true);
            }}
          >
            <Copy aria-hidden="true" className="h-4 w-4" />
            {copied ? "Copied" : "Copy link"}
          </Button>
        </div>
      )}
    </Dialog>
  );
}

export function UserDetailPage() {
  const { userId = "" } = useParams();
  const navigate = useNavigate();
  const { user: currentUser } = useAuth();
  const { hasPermission } = usePermissions();
  const canManage = hasPermission("users:manage");
  const detail = useUserDetail(userId);
  const sessions = useUserSessions(userId);
  const setActive = useSetUserActive();
  const revoke = useRevokeUserSessions();
  const resetLink = useStartPasswordReset();
  const remove = useDeleteUser();
  const [pending, setPending] = useState<PendingAction>(null);
  const [editing, setEditing] = useState(false);
  const toast = useToast();
  const [resetResult, setResetResult] = useState<AdminPasswordResetResponse | null>(null);
  useDocumentTitle(detail.data?.full_name ?? "User");

  if (detail.isPending) return <Spinner label="Loading user" />;
  if (detail.isError) {
    const missing = detail.error instanceof ApiError && detail.error.status === 404;
    return (
      <div className="space-y-4">
        <BackLink />
        <Card padding="none">
          <ErrorState
            title={missing ? "User not found" : "The user could not be loaded"}
            message={missing ? "This user does not exist or was deleted." : errorMessage(detail.error)}
            onRetry={missing ? undefined : () => void detail.refetch()}
          />
        </Card>
      </div>
    );
  }

  const user = detail.data;
  const protectedAccount = user.id === currentUser?.id || user.is_superuser;
  const actionError = [setActive, revoke, resetLink, remove].find((mutation) => mutation.isError)?.error;

  const run = async (action: Exclude<PendingAction, null>) => {
    try {
      if (action === "activate" || action === "deactivate") {
        await setActive.mutateAsync({ id: user.id, active: action === "activate" });
        toast.notify({ title: action === "activate" ? "Account activated." : "Account deactivated and signed out everywhere." });
      } else if (action === "revoke") {
        const { revoked_sessions } = await revoke.mutateAsync(user.id);
        toast.notify({ title: `${revoked_sessions} session${revoked_sessions === 1 ? "" : "s"} signed out.` });
      } else if (action === "reset") {
        setResetResult(await resetLink.mutateAsync(user.id));
      } else {
        await remove.mutateAsync(user.id);
        toast.notify({ title: "User deleted", description: `${user.full_name} was removed.` });
        navigate("/admin/users", { replace: true });
        return;
      }
      setPending(null);
    } catch {
      // The dialog shows the error from the mutation state.
    }
  };

  const dialogs: Record<Exclude<PendingAction, null>, { title: string; description: ReactNode; confirm: string; tone?: "primary" }> = {
    deactivate: {
      title: "Deactivate user?",
      description: `${user.full_name} will be signed out everywhere immediately and cannot sign in until reactivated.`,
      confirm: "Deactivate",
    },
    activate: {
      title: "Activate user?",
      description: `${user.full_name} will be able to sign in again. Any temporary lockout is cleared.`,
      confirm: "Activate",
      tone: "primary",
    },
    revoke: {
      title: "Sign out all sessions?",
      description: `Every active session of ${user.full_name} ends now. They can sign in again with their password.`,
      confirm: "Sign out sessions",
    },
    reset: {
      title: "Create a password reset link?",
      description: "Any earlier unused link stops working. When the user sets a new password, all of their sessions end.",
      confirm: "Create link",
      tone: "primary",
    },
    delete: {
      title: "Delete user?",
      description: `${user.full_name} is removed from the user list and signed out everywhere. Their history is kept for auditing.`,
      confirm: "Delete user",
    },
  };
  const dialog = pending ? dialogs[pending] : null;

  return (
    <div className="space-y-section">
      <PageHeader
        eyebrow={<BackLink />}
        title={user.full_name}
        description={user.email}
        meta={<UserStatusBadge user={user} />}
        actions={
          canManage && (
            <Button variant="secondary" onClick={() => setEditing(true)}>
              <Pencil aria-hidden="true" className="h-4 w-4" />
              Edit details
            </Button>
          )
        }
      />

      <div className="grid gap-section lg:grid-cols-3">
        <div className="min-w-0 space-y-section lg:col-span-2">
          <Card title="Active sessions" description="Browsers where this user is currently signed in.">
            {sessions.isPending ? (
              <SkeletonText lines={2} />
            ) : sessions.isError ? (
              <Alert tone="error">{errorMessage(sessions.error)}</Alert>
            ) : sessions.data.length === 0 ? (
              <p className="text-sm text-fg-muted">No active sessions.</p>
            ) : (
              <ul className="-my-3 divide-y divide-line-subtle text-sm">
                {sessions.data.map((session) => (
                  <li key={session.id} className="flex items-start gap-3 py-3">
                    <Monitor aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0 text-fg-subtle" />
                    <div className="min-w-0">
                      <p className="truncate font-medium text-fg">{session.user_agent ?? "Unknown browser"}</p>
                      <p className="mt-0.5 text-xs text-fg-muted">
                        Signed in {formatDateTime(session.created_at)} · last active {formatDateTime(session.last_used_at)}
                        {session.ip_address ? ` · ${session.ip_address}` : ""}
                      </p>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <UserAccessSection userId={user.id} />
        </div>

        <div className="min-w-0 space-y-section">
          <Card title="Account">
            <div className="text-sm">
              <DetailList
                items={[
                  { label: "Account type", value: user.is_superuser ? "Installation administrator" : "User" },
                  { label: "Username", value: user.username ?? "Not set" },
                  { label: "Last sign-in", value: formatDateTime(user.last_login_at) },
                  { label: "Active sessions", value: String(user.active_session_count) },
                  { label: "Created", value: formatDateTime(user.created_at) },
                  ...(user.is_locked ? [{ label: "Locked until", value: formatDateTime(user.locked_until) }] : []),
                ]}
              />
            </div>
          </Card>

          {canManage && (
            <Card title="Security actions">
              <div className="flex flex-col gap-2">
                {user.is_active ? (
                  !protectedAccount && (
                    <Button variant="secondary" className="justify-start" onClick={() => setPending("deactivate")}>
                      <Power aria-hidden="true" className="h-4 w-4" /> Deactivate account
                    </Button>
                  )
                ) : (
                  <Button variant="secondary" className="justify-start" onClick={() => setPending("activate")}>
                    <Power aria-hidden="true" className="h-4 w-4" /> Activate account
                  </Button>
                )}
                {user.is_active && user.is_locked && (
                  <Button variant="secondary" className="justify-start" onClick={() => setPending("activate")}>
                    <Power aria-hidden="true" className="h-4 w-4" /> Clear lockout
                  </Button>
                )}
                <Button variant="secondary" className="justify-start" onClick={() => setPending("revoke")}>
                  <LogOut aria-hidden="true" className="h-4 w-4" /> Sign out all sessions
                </Button>
                {user.is_active && (
                  <Button variant="secondary" className="justify-start" onClick={() => setPending("reset")}>
                    <KeyRound aria-hidden="true" className="h-4 w-4" /> Create password reset link
                  </Button>
                )}
                {!protectedAccount && (
                  <Button variant="danger-ghost" className="justify-start" onClick={() => setPending("delete")}>
                    <Trash2 aria-hidden="true" className="h-4 w-4" /> Delete user
                  </Button>
                )}
              </div>
            </Card>
          )}
        </div>
      </div>

      {canManage && (
        <EditUserDialog
          user={user}
          open={editing}
          onClose={() => setEditing(false)}
          onSaved={() => {
            setEditing(false);
            toast.notify({ title: "Changes saved." });
          }}
        />
      )}

      {dialog && pending && (
        <ConfirmDialog
          open
          title={dialog.title}
          description={dialog.description}
          confirmLabel={dialog.confirm}
          tone={dialog.tone ?? "danger"}
          pending={setActive.isPending || revoke.isPending || resetLink.isPending || remove.isPending}
          error={actionError ? errorMessage(actionError) : null}
          onCancel={() => {
            setPending(null);
            [setActive, revoke, resetLink, remove].forEach((mutation) => mutation.reset());
          }}
          onConfirm={() => void run(pending)}
        />
      )}

      <ResetLinkDialog result={resetResult} onClose={() => setResetResult(null)} />
    </div>
  );
}

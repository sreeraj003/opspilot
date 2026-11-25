import { zodResolver } from "@hookform/resolvers/zod";
import { ArrowLeft, Copy, KeyRound, LogOut, Power, Trash2 } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { useForm } from "react-hook-form";
import { Link, useNavigate, useParams } from "react-router";
import { z } from "zod";

import { DetailList } from "@/components/DetailList";
import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog, Dialog } from "@/components/ui/Dialog";
import { TextField } from "@/components/ui/Field";
import { Spinner } from "@/components/ui/Spinner";
import { useAuth } from "@/features/auth/authContext";
import { emailSchema } from "@/features/auth/validation";
import { usernameSchema } from "@/features/users/CreateUserDialog";
import {
  useDeleteUser,
  useRevokeUserSessions,
  useSetUserActive,
  useStartPasswordReset,
  useUpdateUser,
  useUserDetail,
  useUserSessions,
} from "@/features/users/hooks";
import { UserStatusBadge } from "@/features/users/UserStatusBadge";
import { useDocumentTitle } from "@/hooks/useDocumentTitle";
import { ApiError } from "@/services/apiClient";
import type { AdminPasswordResetResponse, UserDetail } from "@/types/auth";
import { errorMessage } from "@/utils/errors";
import { formatDateTime } from "@/utils/format";

const editSchema = z.object({
  full_name: z.string().trim().min(1, "Enter the person's name.").max(200, "Use at most 200 characters."),
  email: emailSchema,
  username: usernameSchema,
});

type EditValues = z.infer<typeof editSchema>;
type PendingAction = "deactivate" | "activate" | "revoke" | "reset" | "delete" | null;

function Section({ title, description, children }: { title: string; description?: string; children: ReactNode }) {
  const id = title.toLowerCase().replace(/\W+/g, "-");
  return (
    <section aria-labelledby={id} className="rounded-2xl border border-stone-200 bg-white p-6">
      <h2 id={id} className="text-base font-semibold text-stone-900">
        {title}
      </h2>
      {description && <p className="mt-1 text-sm text-stone-500">{description}</p>}
      <div className="mt-5">{children}</div>
    </section>
  );
}

function EditUserForm({ user }: { user: UserDetail }) {
  const update = useUpdateUser(user.id);
  const [saved, setSaved] = useState(false);
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting, isDirty },
  } = useForm<EditValues>({
    resolver: zodResolver(editSchema),
    defaultValues: { full_name: user.full_name, email: user.email, username: user.username ?? "" },
  });

  useEffect(() => {
    reset({ full_name: user.full_name, email: user.email, username: user.username ?? "" });
  }, [user, reset]);

  const onSubmit = handleSubmit(async (values) => {
    setSaved(false);
    try {
      await update.mutateAsync({ ...values, username: values.username || null });
      setSaved(true);
    } catch {
      // Shown below from the mutation state.
    }
  });

  return (
    <form onSubmit={onSubmit} noValidate className="space-y-4">
      {saved && !isDirty && <Alert tone="success">Changes saved.</Alert>}
      {update.isError && <Alert tone="error">{errorMessage(update.error, "Changes could not be saved.")}</Alert>}
      <div className="grid gap-4 sm:grid-cols-2">
        <TextField label="Full name" error={errors.full_name?.message} {...register("full_name")} />
        <TextField label="Email" type="email" error={errors.email?.message} {...register("email")} />
        <TextField label="Username (optional)" error={errors.username?.message} {...register("username")} />
      </div>
      <Button type="submit" loading={isSubmitting} disabled={!isDirty}>
        Save changes
      </Button>
    </form>
  );
}

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
            className="w-full rounded-lg border border-stone-200 bg-stone-50 px-3 py-2 font-mono text-xs text-stone-800"
          />
          <p className="text-xs text-stone-500">Expires {formatDateTime(result.expires_at)}.</p>
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
  const { user: currentUser, hasPermission } = useAuth();
  const canManage = hasPermission("users:manage");
  const detail = useUserDetail(userId);
  const sessions = useUserSessions(userId);
  const setActive = useSetUserActive();
  const revoke = useRevokeUserSessions();
  const resetLink = useStartPasswordReset();
  const remove = useDeleteUser();
  const [pending, setPending] = useState<PendingAction>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [resetResult, setResetResult] = useState<AdminPasswordResetResponse | null>(null);
  useDocumentTitle(detail.data?.full_name ?? "User");

  if (detail.isPending) return <Spinner label="Loading user" />;
  if (detail.isError) {
    const missing = detail.error instanceof ApiError && detail.error.status === 404;
    return (
      <div className="space-y-4">
        <Link to="/admin/users" className="inline-flex items-center gap-1.5 text-sm text-stone-600 hover:text-stone-900">
          <ArrowLeft aria-hidden="true" className="h-4 w-4" /> All users
        </Link>
        <Alert tone="error">{missing ? "This user does not exist or was deleted." : errorMessage(detail.error)}</Alert>
      </div>
    );
  }

  const user = detail.data;
  const protectedAccount = user.id === currentUser?.id || user.is_superuser;
  const actionError = [setActive, revoke, resetLink, remove].find((mutation) => mutation.isError)?.error;

  const run = async (action: Exclude<PendingAction, null>) => {
    setNotice(null);
    try {
      if (action === "activate" || action === "deactivate") {
        await setActive.mutateAsync({ id: user.id, active: action === "activate" });
        setNotice(action === "activate" ? "Account activated." : "Account deactivated and signed out everywhere.");
      } else if (action === "revoke") {
        const { revoked_sessions } = await revoke.mutateAsync(user.id);
        setNotice(`${revoked_sessions} session${revoked_sessions === 1 ? "" : "s"} signed out.`);
      } else if (action === "reset") {
        setResetResult(await resetLink.mutateAsync(user.id));
      } else {
        await remove.mutateAsync(user.id);
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
    <div className="space-y-6">
      <Link to="/admin/users" className="inline-flex items-center gap-1.5 text-sm text-stone-600 hover:text-stone-900">
        <ArrowLeft aria-hidden="true" className="h-4 w-4" /> All users
      </Link>

      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-stone-900">{user.full_name}</h1>
          <p className="mt-1 text-sm text-stone-500">{user.email}</p>
        </div>
        <UserStatusBadge user={user} />
      </div>

      {notice && <Alert tone="success">{notice}</Alert>}

      <div className="grid gap-6 lg:grid-cols-3">
        <div className="space-y-6 lg:col-span-2">
          {canManage && (
            <Section title="Details" description="Status and privileges are changed with the actions on this page only.">
              <EditUserForm user={user} />
            </Section>
          )}

          <Section title="Active sessions" description="Browsers where this user is currently signed in.">
            {sessions.isPending ? (
              <Spinner label="Loading sessions" />
            ) : sessions.isError ? (
              <Alert tone="error">{errorMessage(sessions.error)}</Alert>
            ) : sessions.data.length === 0 ? (
              <p className="text-sm text-stone-500">No active sessions.</p>
            ) : (
              <ul className="divide-y divide-stone-100 text-sm">
                {sessions.data.map((session) => (
                  <li key={session.id} className="py-3">
                    <p className="truncate font-medium text-stone-800">{session.user_agent ?? "Unknown browser"}</p>
                    <p className="mt-0.5 text-xs text-stone-500">
                      Signed in {formatDateTime(session.created_at)} · last active {formatDateTime(session.last_used_at)}
                      {session.ip_address ? ` · ${session.ip_address}` : ""}
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </Section>
        </div>

        <div className="space-y-6">
          <Section title="Account">
            <div className="text-sm">
              <DetailList
                items={[
                  { label: "Role", value: user.is_superuser ? "Administrator" : "User" },
                  { label: "Username", value: user.username ?? "Not set" },
                  { label: "Last sign-in", value: formatDateTime(user.last_login_at) },
                  { label: "Active sessions", value: String(user.active_session_count) },
                  { label: "Created", value: formatDateTime(user.created_at) },
                  ...(user.is_locked ? [{ label: "Locked until", value: formatDateTime(user.locked_until) }] : []),
                ]}
              />
            </div>
          </Section>

          {canManage && (
            <Section title="Security actions">
              <div className="flex flex-col gap-2">
                {user.is_active ? (
                  !protectedAccount && (
                    <Button variant="secondary" onClick={() => setPending("deactivate")}>
                      <Power aria-hidden="true" className="h-4 w-4" /> Deactivate account
                    </Button>
                  )
                ) : (
                  <Button variant="secondary" onClick={() => setPending("activate")}>
                    <Power aria-hidden="true" className="h-4 w-4" /> Activate account
                  </Button>
                )}
                {user.is_active && user.is_locked && (
                  <Button variant="secondary" onClick={() => setPending("activate")}>
                    <Power aria-hidden="true" className="h-4 w-4" /> Clear lockout
                  </Button>
                )}
                <Button variant="secondary" onClick={() => setPending("revoke")}>
                  <LogOut aria-hidden="true" className="h-4 w-4" /> Sign out all sessions
                </Button>
                {user.is_active && (
                  <Button variant="secondary" onClick={() => setPending("reset")}>
                    <KeyRound aria-hidden="true" className="h-4 w-4" /> Create password reset link
                  </Button>
                )}
                {!protectedAccount && (
                  <Button variant="ghost" className="text-rose-600 hover:bg-rose-50 hover:text-rose-700" onClick={() => setPending("delete")}>
                    <Trash2 aria-hidden="true" className="h-4 w-4" /> Delete user
                  </Button>
                )}
              </div>
            </Section>
          )}
        </div>
      </div>

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

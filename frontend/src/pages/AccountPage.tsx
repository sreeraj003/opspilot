import { zodResolver } from "@hookform/resolvers/zod";
import { LogOut } from "lucide-react";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/Dialog";
import { PasswordField } from "@/components/ui/Field";
import { DetailList } from "@/components/DetailList";
import { useAuth } from "@/features/auth/authContext";
import { newPasswordSchema, PASSWORD_HINT } from "@/features/auth/validation";
import { useDocumentTitle } from "@/hooks/useDocumentTitle";
import { errorMessage } from "@/utils/errors";
import { formatDateTime } from "@/utils/format";

const schema = z
  .object({ current: z.string().min(1, "Enter your current password."), password: newPasswordSchema, confirm: z.string() })
  .refine((values) => values.password === values.confirm, { path: ["confirm"], message: "The passwords do not match." });

export function AccountPage() {
  useDocumentTitle("Your account");
  const { user, changePassword, logoutEverywhere } = useAuth();
  const [changed, setChanged] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [confirmingSignOut, setConfirmingSignOut] = useState(false);
  const [signingOut, setSigningOut] = useState(false);
  const [signOutError, setSignOutError] = useState<string | null>(null);
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<z.infer<typeof schema>>({
    resolver: zodResolver(schema),
    defaultValues: { current: "", password: "", confirm: "" },
  });

  if (!user) return null;

  const onSubmit = handleSubmit(async ({ current, password }) => {
    setFailure(null);
    setChanged(false);
    try {
      await changePassword(current, password);
      reset();
      setChanged(true);
    } catch (error) {
      setFailure(errorMessage(error));
    }
  });

  return (
    <div className="mx-auto max-w-2xl space-y-8">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-stone-900">Your account</h1>
        <p className="mt-1 text-sm text-stone-500">Profile details and sign-in security.</p>
      </div>

      <section aria-labelledby="profile-heading" className="rounded-2xl border border-stone-200 bg-white p-6 text-sm">
        <h2 id="profile-heading" className="mb-4 text-base font-semibold text-stone-900">
          Profile
        </h2>
        <DetailList
          items={[
            { label: "Name", value: user.full_name },
            { label: "Email", value: user.email },
            { label: "Username", value: user.username ?? "Not set" },
            { label: "Role", value: user.is_superuser ? "Installation administrator" : "User" },
            { label: "Last sign-in", value: formatDateTime(user.last_login_at) },
          ]}
        />
      </section>

      <section aria-labelledby="password-heading" className="rounded-2xl border border-stone-200 bg-white p-6">
        <h2 id="password-heading" className="text-base font-semibold text-stone-900">
          Change password
        </h2>
        <p className="mt-1 text-sm text-stone-500">Changing your password signs out every other session.</p>
        <form onSubmit={onSubmit} noValidate className="mt-5 space-y-4">
          {changed && <Alert tone="success">Password changed. Your other sessions have been signed out.</Alert>}
          {failure && <Alert tone="error">{failure}</Alert>}
          <PasswordField label="Current password" autoComplete="current-password" error={errors.current?.message} {...register("current")} />
          <PasswordField
            label="New password"
            autoComplete="new-password"
            hint={PASSWORD_HINT}
            error={errors.password?.message}
            {...register("password")}
          />
          <PasswordField label="Confirm new password" autoComplete="new-password" error={errors.confirm?.message} {...register("confirm")} />
          <Button type="submit" loading={isSubmitting}>
            Change password
          </Button>
        </form>
      </section>

      <section aria-labelledby="sessions-heading" className="rounded-2xl border border-stone-200 bg-white p-6">
        <h2 id="sessions-heading" className="text-base font-semibold text-stone-900">
          Sessions
        </h2>
        <p className="mt-1 text-sm text-stone-500">Sign out of OpsPilot AI on every device and browser, including this one.</p>
        <Button variant="secondary" className="mt-4" onClick={() => setConfirmingSignOut(true)}>
          <LogOut aria-hidden="true" className="h-4 w-4" />
          Sign out everywhere
        </Button>
      </section>

      <ConfirmDialog
        open={confirmingSignOut}
        title="Sign out everywhere?"
        description="Every session of your account will end, including this one."
        confirmLabel="Sign out everywhere"
        pending={signingOut}
        error={signOutError}
        onCancel={() => setConfirmingSignOut(false)}
        onConfirm={async () => {
          setSigningOut(true);
          setSignOutError(null);
          try {
            await logoutEverywhere();
          } catch (error) {
            setSignOutError(errorMessage(error));
            setSigningOut(false);
          }
        }}
      />
    </div>
  );
}

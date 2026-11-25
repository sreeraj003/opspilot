import { zodResolver } from "@hookform/resolvers/zod";
import { LogOut } from "lucide-react";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { useSearchParams } from "react-router";
import { z } from "zod";

import { Alert } from "@/components/ui/Alert";
import { Avatar } from "@/components/ui/Avatar";
import { Card } from "@/components/ui/Card";
import { Divider, PageHeader, ResponsiveContainer } from "@/components/ui/Layout";
import { Switch } from "@/components/ui/Switch";
import { Tabs } from "@/components/ui/Tabs";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/Dialog";
import { PasswordField } from "@/components/ui/Field";
import { DetailList } from "@/components/DetailList";
import { useAuth } from "@/features/auth/authContext";
import { newPasswordSchema, PASSWORD_HINT } from "@/features/auth/validation";
import { useTheme } from "@/features/theme/themeContext";
import { useDocumentTitle } from "@/hooks/useDocumentTitle";
import { errorMessage } from "@/utils/errors";
import { formatDateTime } from "@/utils/format";

const schema = z
  .object({ current: z.string().min(1, "Enter your current password."), password: newPasswordSchema, confirm: z.string() })
  .refine((values) => values.password === values.confirm, { path: ["confirm"], message: "The passwords do not match." });

export function AccountPage() {
  useDocumentTitle("Your account");
  const { user, changePassword, logoutEverywhere } = useAuth();
  const { preference, theme, setPreference } = useTheme();
  const [params, setParams] = useSearchParams();
  const requested = params.get("tab");
  const tab = requested === "security" || requested === "appearance" ? requested : "profile";
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

  const profile = (
    <Card title="Profile" description="Your details are managed by an administrator.">
      <div className="mb-5 flex items-center gap-3">
        <Avatar name={user.full_name} size="lg" />
        <div className="min-w-0">
          <p className="truncate font-medium text-fg">{user.full_name}</p>
          <p className="truncate text-sm text-fg-muted">{user.email}</p>
        </div>
      </div>
      <div className="text-sm">
        <DetailList
          items={[
            { label: "Username", value: user.username ?? "Not set" },
            { label: "Account type", value: user.is_superuser ? "Installation administrator" : "User" },
            { label: "Last sign-in", value: formatDateTime(user.last_login_at) },
          ]}
        />
      </div>
    </Card>
  );

  const security = (
    <div className="space-y-section">
      <Card title="Change password" description="Changing your password signs out every other session.">
        <form onSubmit={onSubmit} noValidate className="space-y-4">
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
      </Card>

      <Card title="Sessions" description="Sign out of OpsPilot AI on every device and browser, including this one.">
        <Button variant="secondary" onClick={() => setConfirmingSignOut(true)}>
          <LogOut aria-hidden="true" className="h-4 w-4" />
          Sign out everywhere
        </Button>
      </Card>
    </div>
  );

  const appearance = (
    <Card title="Appearance" description="Saved in this browser only.">
      <div className="space-y-5">
        <Switch
          label="Match the operating system"
          description="Follow your device's light or dark setting."
          checked={preference === "system"}
          onCheckedChange={(checked) => setPreference(checked ? "system" : theme)}
        />
        <Divider />
        <fieldset disabled={preference === "system"} className="disabled:opacity-60">
          <legend className="mb-2 text-sm font-medium text-fg">Theme</legend>
          <div className="grid grid-cols-2 gap-3 sm:max-w-sm">
            {(["light", "dark"] as const).map((option) => (
              <label
                key={option}
                className="flex cursor-pointer items-center gap-2.5 rounded-control border border-line bg-surface px-3 py-2.5 text-sm text-fg-secondary has-checked:border-accent has-checked:bg-accent-subtle has-checked:text-accent-text"
              >
                <input
                  type="radio"
                  name="theme"
                  value={option}
                  checked={theme === option}
                  onChange={() => setPreference(option)}
                  className="accent-accent"
                />
                {option === "light" ? "Light" : "Dark"}
              </label>
            ))}
          </div>
        </fieldset>
      </div>
    </Card>
  );

  return (
    <ResponsiveContainer width="narrow" className="space-y-section">
      <PageHeader title="Your account" description="Profile details, sign-in security and appearance." />
      <Tabs
        label="Account sections"
        value={tab}
        onValueChange={(next) => setParams(next === "profile" ? {} : { tab: next }, { replace: true })}
        items={[
          { id: "profile", label: "Profile", content: profile },
          { id: "security", label: "Security", content: security },
          { id: "appearance", label: "Appearance", content: appearance },
        ]}
      />

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
    </ResponsiveContainer>
  );
}

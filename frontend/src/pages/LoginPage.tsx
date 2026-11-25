import { zodResolver } from "@hookform/resolvers/zod";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { Link, useLocation, useNavigate } from "react-router";
import { z } from "zod";

import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { PasswordField, TextField } from "@/components/ui/Field";
import { AuthCard } from "@/features/auth/AuthCard";
import { useAuth } from "@/features/auth/authContext";
import type { LoginRedirectState } from "@/features/auth/guards";
import { emailSchema } from "@/features/auth/validation";
import { useDocumentTitle } from "@/hooks/useDocumentTitle";
import { errorMessage } from "@/utils/errors";

const loginSchema = z.object({
  email: emailSchema,
  // Any non-empty password is submitted: the policy applies when passwords are set, not here.
  password: z.string().min(1, "Enter your password."),
});

type LoginValues = z.infer<typeof loginSchema>;

function safeRedirect(from: string | undefined): string {
  return from && from.startsWith("/") && !from.startsWith("//") && !from.startsWith("/login") ? from : "/";
}

export function LoginPage() {
  useDocumentTitle("Sign in");
  const { login, sessionExpired } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [failure, setFailure] = useState<string | null>(null);
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
    resetField,
  } = useForm<LoginValues>({ resolver: zodResolver(loginSchema), defaultValues: { email: "", password: "" } });

  const onSubmit = handleSubmit(async ({ email, password }) => {
    setFailure(null);
    try {
      await login(email, password);
      navigate(safeRedirect((location.state as LoginRedirectState | null)?.from), { replace: true });
    } catch (error) {
      resetField("password");
      setFailure(errorMessage(error, "Unable to sign in. Try again."));
    }
  });

  return (
    <AuthCard title="Sign in" subtitle="Use the account your administrator created for you.">
      <form onSubmit={onSubmit} noValidate className="space-y-4">
        {sessionExpired && !failure && <Alert tone="info">Your session has ended. Sign in again to continue.</Alert>}
        {failure && <Alert tone="error">{failure}</Alert>}
        <TextField
          label="Email"
          type="email"
          autoComplete="username"
          inputMode="email"
          autoFocus
          error={errors.email?.message}
          {...register("email")}
        />
        <PasswordField
          label="Password"
          autoComplete="current-password"
          error={errors.password?.message}
          {...register("password")}
        />
        <div className="flex justify-end">
          <Link to="/forgot-password" className="text-sm font-medium text-stone-600 underline-offset-4 hover:text-stone-900 hover:underline">
            Forgot password?
          </Link>
        </div>
        <Button type="submit" loading={isSubmitting} className="w-full">
          {isSubmitting ? "Signing in" : "Sign in"}
        </Button>
      </form>
    </AuthCard>
  );
}

import { zodResolver } from "@hookform/resolvers/zod";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { Link } from "react-router";
import { z } from "zod";

import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { PasswordField } from "@/components/ui/Field";
import { AuthCard } from "@/features/auth/AuthCard";
import { newPasswordSchema, PASSWORD_HINT } from "@/features/auth/validation";
import { useApiClient } from "@/hooks/useApiClient";
import { useDocumentTitle } from "@/hooks/useDocumentTitle";
import { ApiError } from "@/services/apiClient";
import { resetPassword } from "@/services/authService";
import { errorMessage } from "@/utils/errors";

const schema = z
  .object({ password: newPasswordSchema, confirm: z.string() })
  .refine((values) => values.password === values.confirm, { path: ["confirm"], message: "The passwords do not match." });

/**
 * Reads the token from the URL fragment (#token=...), which browsers never send
 * to servers, then removes it from the address bar and history.
 */
function takeTokenFromUrl(): string | null {
  const token = new URLSearchParams(window.location.hash.replace(/^#/, "")).get("token");
  if (token) window.history.replaceState(window.history.state, "", window.location.pathname);
  return token;
}

export function ResetPasswordPage() {
  useDocumentTitle("Choose a new password");
  const client = useApiClient();
  const [token] = useState(takeTokenFromUrl);
  const [result, setResult] = useState<"done" | "invalid" | null>(token ? null : "invalid");
  const [failure, setFailure] = useState<string | null>(null);
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<z.infer<typeof schema>>({ resolver: zodResolver(schema), defaultValues: { password: "", confirm: "" } });

  const onSubmit = handleSubmit(async ({ password }) => {
    if (!token) return;
    setFailure(null);
    try {
      await resetPassword(client, token, password);
      setResult("done");
    } catch (error) {
      if (error instanceof ApiError && error.code === "INVALID_RESET_TOKEN") setResult("invalid");
      else setFailure(errorMessage(error));
    }
  });

  if (result === "invalid") {
    return (
      <AuthCard title="Link invalid or expired">
        <Alert tone="error">This password reset link is invalid, has already been used, or has expired.</Alert>
        <Link to="/forgot-password" className="mt-6 inline-flex text-sm font-medium text-stone-900 underline underline-offset-4">
          Request a new link
        </Link>
      </AuthCard>
    );
  }

  if (result === "done") {
    return (
      <AuthCard title="Password updated">
        <Alert tone="success">Your password has been reset and all your sessions were signed out.</Alert>
        <Link
          to="/login"
          className="mt-6 inline-flex w-full items-center justify-center rounded-lg bg-stone-900 px-3.5 py-2 text-sm font-medium text-white hover:bg-stone-700"
        >
          Sign in
        </Link>
      </AuthCard>
    );
  }

  return (
    <AuthCard title="Choose a new password">
      <form onSubmit={onSubmit} noValidate className="space-y-4">
        {failure && <Alert tone="error">{failure}</Alert>}
        <PasswordField
          label="New password"
          autoComplete="new-password"
          autoFocus
          hint={PASSWORD_HINT}
          error={errors.password?.message}
          {...register("password")}
        />
        <PasswordField
          label="Confirm new password"
          autoComplete="new-password"
          error={errors.confirm?.message}
          {...register("confirm")}
        />
        <Button type="submit" loading={isSubmitting} className="w-full">
          Reset password
        </Button>
      </form>
    </AuthCard>
  );
}

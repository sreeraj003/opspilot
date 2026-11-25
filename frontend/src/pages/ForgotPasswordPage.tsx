import { zodResolver } from "@hookform/resolvers/zod";
import { ArrowLeft } from "lucide-react";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { Link } from "react-router";
import { z } from "zod";

import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { TextField } from "@/components/ui/Field";
import { AuthLayout } from "@/features/auth/AuthLayout";
import { emailSchema } from "@/features/auth/validation";
import { useApiClient } from "@/hooks/useApiClient";
import { useDocumentTitle } from "@/hooks/useDocumentTitle";
import { requestPasswordReset } from "@/services/authService";
import { errorMessage } from "@/utils/errors";

const schema = z.object({ email: emailSchema });

export function ForgotPasswordPage() {
  useDocumentTitle("Reset your password");
  const client = useApiClient();
  const [confirmation, setConfirmation] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<z.infer<typeof schema>>({ resolver: zodResolver(schema), defaultValues: { email: "" } });

  const onSubmit = handleSubmit(async ({ email }) => {
    setFailure(null);
    try {
      // The answer is identical whether or not the account exists.
      setConfirmation((await requestPasswordReset(client, email)).message);
    } catch (error) {
      setFailure(errorMessage(error));
    }
  });

  return (
    <AuthLayout
      title="Reset your password"
      subtitle="Enter your account's email address. If you cannot receive a reset link, ask your administrator for one."
    >
      {confirmation ? (
        <Alert tone="success">{confirmation}</Alert>
      ) : (
        <form onSubmit={onSubmit} noValidate className="space-y-4">
          {failure && <Alert tone="error">{failure}</Alert>}
          <TextField label="Email" type="email" autoComplete="email" autoFocus error={errors.email?.message} {...register("email")} />
          <Button type="submit" loading={isSubmitting} className="w-full">
            Send reset instructions
          </Button>
        </form>
      )}
      <Link to="/login" className="mt-6 inline-flex items-center gap-1.5 text-sm font-medium text-stone-600 hover:text-stone-900">
        <ArrowLeft aria-hidden="true" className="h-4 w-4" />
        Back to sign in
      </Link>
    </AuthLayout>
  );
}

import { zodResolver } from "@hookform/resolvers/zod";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { PasswordField, TextField } from "@/components/ui/Field";
import { emailSchema, newPasswordSchema, PASSWORD_HINT } from "@/features/auth/validation";
import { useCreateUser } from "@/features/users/hooks";
import type { User } from "@/types/auth";
import { errorMessage } from "@/utils/errors";

export const usernameSchema = z
  .string()
  .trim()
  .refine((value) => value === "" || /^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$/.test(value), {
    message: "3-64 letters, digits, '.', '_' or '-', starting with a letter or digit.",
  });

const schema = z.object({
  full_name: z.string().trim().min(1, "Enter the person's name.").max(200, "Use at most 200 characters."),
  email: emailSchema,
  username: usernameSchema,
  password: newPasswordSchema,
  is_active: z.boolean(),
});

type Values = z.infer<typeof schema>;

const DEFAULTS: Values = { full_name: "", email: "", username: "", password: "", is_active: true };

export function CreateUserDialog({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (user: User) => void;
}) {
  const createUser = useCreateUser();
  const [failure, setFailure] = useState<string | null>(null);
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting },
  } = useForm<Values>({ resolver: zodResolver(schema), defaultValues: DEFAULTS });

  const close = () => {
    reset(DEFAULTS);
    setFailure(null);
    onClose();
  };

  const onSubmit = handleSubmit(async (values) => {
    setFailure(null);
    try {
      const user = await createUser.mutateAsync({ ...values, username: values.username || null });
      reset(DEFAULTS);
      onCreated(user);
    } catch (error) {
      setFailure(errorMessage(error, "The user could not be created."));
    }
  });

  return (
    <Dialog
      open={open}
      onClose={close}
      title="Create user"
      description="Choose an initial password and share it with the person over a trusted channel. They can change it after signing in."
    >
      <form onSubmit={onSubmit} noValidate className="space-y-4">
        {failure && <Alert tone="error">{failure}</Alert>}
        <TextField label="Full name" autoComplete="off" error={errors.full_name?.message} {...register("full_name")} />
        <TextField label="Email" type="email" autoComplete="off" error={errors.email?.message} {...register("email")} />
        <TextField
          label="Username (optional)"
          autoComplete="off"
          error={errors.username?.message}
          {...register("username")}
        />
        <PasswordField
          label="Initial password"
          autoComplete="new-password"
          hint={PASSWORD_HINT}
          error={errors.password?.message}
          {...register("password")}
        />
        <label className="flex items-center gap-2 text-sm text-stone-700">
          <input type="checkbox" className="h-4 w-4 rounded border-stone-300" {...register("is_active")} />
          Account is active (the user can sign in)
        </label>
        <div className="flex justify-end gap-2 pt-2">
          <Button variant="secondary" onClick={close} disabled={isSubmitting}>
            Cancel
          </Button>
          <Button type="submit" loading={isSubmitting}>
            Create user
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

import { zodResolver } from "@hookform/resolvers/zod";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Checkbox } from "@/components/ui/Checkbox";
import { Dialog, DialogActions } from "@/components/ui/Dialog";
import { PasswordField, TextField } from "@/components/ui/Field";
import { emailSchema, newPasswordSchema, PASSWORD_HINT } from "@/features/auth/validation";
import { useCreateUser } from "@/features/users/hooks";
import { fullNameSchema, usernameSchema } from "@/features/users/validation";
import type { User } from "@/types/auth";
import { errorMessage } from "@/utils/errors";

const schema = z.object({
  full_name: fullNameSchema,
  email: emailSchema,
  username: usernameSchema,
  password: newPasswordSchema,
  is_active: z.boolean(),
});

type Values = z.infer<typeof schema>;

const DEFAULTS: Values = { full_name: "", email: "", username: "", password: "", is_active: true };

export function CreateUserDialog({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: (user: User) => void }) {
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
        <TextField label="Full name" autoComplete="off" required error={errors.full_name?.message} {...register("full_name")} />
        <TextField label="Email" type="email" autoComplete="off" required error={errors.email?.message} {...register("email")} />
        <TextField label="Username (optional)" autoComplete="off" error={errors.username?.message} {...register("username")} />
        <PasswordField
          label="Initial password"
          autoComplete="new-password"
          required
          hint={PASSWORD_HINT}
          error={errors.password?.message}
          {...register("password")}
        />
        <Checkbox label="Account is active (the user can sign in)" {...register("is_active")} />
        <DialogActions>
          <Button variant="secondary" onClick={close} disabled={isSubmitting}>
            Cancel
          </Button>
          <Button type="submit" loading={isSubmitting}>
            Create user
          </Button>
        </DialogActions>
      </form>
    </Dialog>
  );
}

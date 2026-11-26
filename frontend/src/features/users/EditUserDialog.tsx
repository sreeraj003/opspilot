import { zodResolver } from "@hookform/resolvers/zod";
import { useEffect } from "react";
import { useForm } from "react-hook-form";

import { Alert } from "@/components/ui/Alert";
import { Button } from "@/components/ui/Button";
import { Dialog, DialogActions } from "@/components/ui/Dialog";
import { TextField } from "@/components/ui/Field";
import { useUpdateUser } from "@/features/users/hooks";
import { userDetailsSchema, type UserDetailsValues } from "@/features/users/validation";
import type { UserDetail } from "@/types/auth";
import { errorMessage } from "@/utils/errors";

function valuesOf(user: UserDetail): UserDetailsValues {
  return { full_name: user.full_name, email: user.email, username: user.username ?? "" };
}

/** Edits a user's name, email and username. Status and access are changed elsewhere. */
export function EditUserDialog({ user, open, onClose, onSaved }: { user: UserDetail; open: boolean; onClose: () => void; onSaved: () => void }) {
  const update = useUpdateUser(user.id);
  const {
    register,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting, isDirty },
  } = useForm<UserDetailsValues>({ resolver: zodResolver(userDetailsSchema), defaultValues: valuesOf(user) });

  useEffect(() => {
    if (open) reset(valuesOf(user));
  }, [open, user, reset]);

  const close = () => {
    update.reset();
    onClose();
  };

  const onSubmit = handleSubmit(async (values) => {
    try {
      await update.mutateAsync({ ...values, username: values.username || null });
      onSaved();
    } catch {
      // Shown from the mutation state.
    }
  });

  return (
    <Dialog open={open} onClose={close} title="Edit user" description="Status and access are changed with the actions on the user's page.">
      <form onSubmit={onSubmit} noValidate className="space-y-4">
        {update.isError && <Alert tone="error">{errorMessage(update.error, "Changes could not be saved.")}</Alert>}
        <TextField label="Full name" required error={errors.full_name?.message} {...register("full_name")} />
        <TextField label="Email" type="email" required error={errors.email?.message} {...register("email")} />
        <TextField label="Username (optional)" error={errors.username?.message} {...register("username")} />
        <DialogActions>
          <Button variant="secondary" onClick={close} disabled={isSubmitting}>
            Cancel
          </Button>
          <Button type="submit" loading={isSubmitting} disabled={!isDirty}>
            Save changes
          </Button>
        </DialogActions>
      </form>
    </Dialog>
  );
}

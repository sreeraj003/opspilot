import { useState } from "react";

import { ConfirmDialog } from "@/components/ui/Dialog";
import { useAuth } from "@/features/auth/authContext";

/** Confirms a deliberate sign-out of this browser. */
export function SignOutDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { logout } = useAuth();
  const [pending, setPending] = useState(false);
  return (
    <ConfirmDialog
      open={open}
      title="Sign out?"
      description="You will be signed out of OpsPilot AI in this browser. Sessions on other devices stay signed in."
      confirmLabel="Sign out"
      tone="primary"
      pending={pending}
      onCancel={onClose}
      onConfirm={async () => {
        setPending(true);
        // Sign-out always ends the local session, even if the server cannot be reached.
        await logout();
      }}
    />
  );
}

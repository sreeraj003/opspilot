import { StatusBadge } from "@/components/StatusBadge";
import type { User } from "@/types/auth";

export function UserStatusBadge({ user }: { user: Pick<User, "is_active" | "is_locked"> }) {
  if (!user.is_active) return <StatusBadge tone="neutral" label="Inactive" />;
  if (user.is_locked) return <StatusBadge tone="warning" label="Locked" />;
  return <StatusBadge tone="success" label="Active" />;
}

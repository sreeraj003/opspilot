import { StatusIndicator } from "@/components/ui/StatusIndicator";
import type { User } from "@/types/auth";

export function UserStatusBadge({ user }: { user: Pick<User, "is_active" | "is_locked"> }) {
  if (!user.is_active) return <StatusIndicator tone="neutral" label="Inactive" />;
  if (user.is_locked) return <StatusIndicator tone="warning" label="Locked" />;
  return <StatusIndicator tone="success" label="Active" />;
}

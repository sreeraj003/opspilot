import { ChevronDown, LogOut, UserRound } from "lucide-react";

import { Avatar } from "@/components/ui/Avatar";
import { DropdownMenu } from "@/components/ui/DropdownMenu";
import { useAuth } from "@/features/auth/authContext";

/** Account menu in the top bar: who is signed in, the account page, and sign out. */
export function UserMenu({ onSignOut }: { onSignOut: () => void }) {
  const { user } = useAuth();
  if (!user) return null;
  return (
    <DropdownMenu
      label="Account"
      trigger={(props) => (
        <button
          type="button"
          {...props}
          aria-label="Account menu"
          className="flex h-9 items-center gap-2 rounded-control pr-1.5 pl-1 text-sm font-medium text-fg-secondary hover:bg-surface-muted"
        >
          <Avatar name={user.full_name} size="sm" />
          <span className="hidden max-w-40 truncate md:inline">{user.full_name}</span>
          <ChevronDown aria-hidden="true" className="hidden h-3.5 w-3.5 text-fg-subtle md:block" />
        </button>
      )}
      items={[
        {
          type: "label",
          id: "who",
          label: (
            <span className="block max-w-56">
              <span className="block truncate text-sm font-medium text-fg">{user.full_name}</span>
              <span className="block truncate text-xs text-fg-muted">{user.email}</span>
            </span>
          ),
        },
        { type: "separator", id: "sep-1" },
        { type: "link", id: "account", label: "Your account", to: "/account", icon: <UserRound aria-hidden="true" className="h-4 w-4" /> },
        { type: "separator", id: "sep-2" },
        { id: "sign-out", label: "Sign out", tone: "danger", icon: <LogOut aria-hidden="true" className="h-4 w-4" />, onSelect: onSignOut },
      ]}
    />
  );
}

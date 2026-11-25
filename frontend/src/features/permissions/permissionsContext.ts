import { createContext, useContext } from "react";

import type { EffectivePermissions } from "@/types/rbac";

export interface PermissionsContextValue {
  /** True when the server currently grants ``key`` (a system permission such as "roles:manage" or a permission key). */
  hasPermission: (key: string) => boolean;
  /** True when the user may perform ``action`` on ``resourceType`` (for example "database_data" / "view"). */
  canAccessResource: (resourceType: string, action: string) => boolean;
  /** The server's latest answer, or null before it arrives (then the sign-in response is used). */
  effectivePermissions: EffectivePermissions | null;
  isLoading: boolean;
  /** Re-read permissions from the server now. */
  refresh: () => Promise<void>;
}

export const PermissionsContext = createContext<PermissionsContextValue | null>(null);

/**
 * Permission checks for the UI. They only decide what to show: the server
 * enforces every permission on every request regardless of this state.
 */
export function usePermissions(): PermissionsContextValue {
  const value = useContext(PermissionsContext);
  if (!value) throw new Error("usePermissions must be used inside <PermissionsProvider>");
  return value;
}

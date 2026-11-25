import { Navigate, Outlet } from "react-router";

import { canSeeItem, sectionItems } from "@/app/navigation";
import { ForbiddenPage } from "@/features/auth/guards";
import { usePermissions } from "@/features/permissions/permissionsContext";

/**
 * Gate for everything under /admin: shown to users who can open at least one
 * administration page in the navigation registry. Each page also has its own
 * <RequirePermission>, and the server authorizes every request.
 */
export function AdminLayout() {
  const { hasPermission } = usePermissions();
  const allowed = sectionItems("administration").some((item) => canSeeItem(item, hasPermission));
  return allowed ? <Outlet /> : <ForbiddenPage />;
}

/** /admin itself has no page: go to the first administration page this user may open. */
export function AdminIndexRedirect() {
  const { hasPermission } = usePermissions();
  const first = sectionItems("administration").find((item) => canSeeItem(item, hasPermission));
  return first ? <Navigate to={first.to} replace /> : <ForbiddenPage />;
}

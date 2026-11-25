import { ShieldAlert } from "lucide-react";
import { Navigate, Outlet, useLocation } from "react-router";

import { FullPageSpinner } from "@/components/ui/Spinner";
import { useAuth } from "@/features/auth/authContext";
import { usePermissions } from "@/features/permissions/permissionsContext";
import type { Permission } from "@/types/auth";

export interface LoginRedirectState {
  from?: string;
}

/**
 * Renders child routes only for a signed-in user. This is a convenience for
 * the UI, not a security boundary: every API endpoint checks authentication itself.
 */
export function RequireAuth() {
  const { status } = useAuth();
  const location = useLocation();
  if (status === "loading") return <FullPageSpinner label="Checking your session" />;
  if (status === "unauthenticated") {
    const state: LoginRedirectState = { from: `${location.pathname}${location.search}` };
    return <Navigate to="/login" replace state={state} />;
  }
  return <Outlet />;
}

/** Child routes for users the server says hold ``permission``; others see a 403 page. */
export function RequirePermission({ permission }: { permission: Permission }) {
  const { hasPermission } = usePermissions();
  return hasPermission(permission) ? <Outlet /> : <ForbiddenPage />;
}

/** Public pages (login, password reset) that a signed-in user does not need. */
export function RedirectIfAuthenticated() {
  const { status } = useAuth();
  const location = useLocation();
  if (status === "loading") return <FullPageSpinner label="Checking your session" />;
  if (status === "authenticated") {
    const from = (location.state as LoginRedirectState | null)?.from;
    return <Navigate to={from && from.startsWith("/") && !from.startsWith("//") ? from : "/"} replace />;
  }
  return <Outlet />;
}

export function ForbiddenPage() {
  return (
    <div className="mx-auto max-w-md py-16 text-center">
      <span className="mx-auto grid h-12 w-12 place-items-center rounded-2xl bg-stone-100 text-stone-600">
        <ShieldAlert aria-hidden="true" className="h-6 w-6" strokeWidth={1.75} />
      </span>
      <p className="mt-6 text-sm font-semibold text-stone-500">403</p>
      <h1 className="mt-2 text-2xl font-semibold tracking-tight text-stone-900">Not available</h1>
      <p className="mt-2 text-stone-600">Your account does not have access to this page.</p>
    </div>
  );
}

import { ShieldAlert, Sparkles } from "lucide-react";
import { Navigate, Outlet, useLocation } from "react-router";

import { ButtonLink } from "@/components/ui/Button";
import { LoadingSpinner } from "@/components/ui/Spinner";
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
  if (status === "loading") return <SessionCheck />;
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

/** Child routes for users who hold at least one of ``permissions`` (pages whose sections need different keys). */
export function RequireAnyPermission({ permissions }: { permissions: Permission[] }) {
  const { hasPermission } = usePermissions();
  return permissions.some(hasPermission) ? <Outlet /> : <ForbiddenPage />;
}

/** Public pages (login, password reset) that a signed-in user does not need. */
export function RedirectIfAuthenticated() {
  const { status } = useAuth();
  const location = useLocation();
  if (status === "loading") return <SessionCheck />;
  if (status === "authenticated") {
    const from = (location.state as LoginRedirectState | null)?.from;
    return <Navigate to={from && from.startsWith("/") && !from.startsWith("//") ? from : "/"} replace />;
  }
  return <Outlet />;
}

/** Shown while the session is restored from the refresh cookie (one request), so pages never flash. */
function SessionCheck() {
  return (
    <div className="grid min-h-full place-items-center bg-canvas">
      <div role="status" className="flex flex-col items-center gap-4 text-sm text-fg-muted">
        <span aria-hidden="true" className="grid h-10 w-10 place-items-center rounded-card bg-accent text-accent-fg shadow-card">
          <Sparkles className="h-5 w-5" />
        </span>
        <span className="inline-flex items-center gap-2">
          <LoadingSpinner size="sm" />
          Checking your session…
        </span>
      </div>
    </div>
  );
}

export function ForbiddenPage() {
  return (
    <div className="mx-auto max-w-md py-16 text-center">
      <span className="mx-auto grid h-12 w-12 place-items-center rounded-card bg-surface-strong text-fg-secondary">
        <ShieldAlert aria-hidden="true" className="h-6 w-6" strokeWidth={1.75} />
      </span>
      <p className="mt-6 text-sm font-semibold text-fg-muted">403</p>
      <h1 className="mt-2 text-page-title text-fg">Not available</h1>
      <p className="mt-2 text-fg-secondary">Your account does not have access to this page.</p>
      <ButtonLink to="/" variant="secondary" className="mt-8">
        Back to home
      </ButtonLink>
    </div>
  );
}

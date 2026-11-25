import { Suspense, useEffect, useState } from "react";
import { Outlet, useLocation } from "react-router";

import { ErrorBoundary } from "@/components/ErrorBoundary";
import { Drawer } from "@/components/ui/Drawer";
import { ResponsiveContainer } from "@/components/ui/Layout";
import { Spinner } from "@/components/ui/Spinner";
import { minWidth, SIDEBAR_DOCK_BREAKPOINT } from "@/design/tokens";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { usePersistentBoolean } from "@/hooks/usePersistentState";
import { SidebarContent } from "@/layouts/shell/Sidebar";
import { SignOutDialog } from "@/layouts/shell/SignOutDialog";
import { Topbar } from "@/layouts/shell/Topbar";
import { cn } from "@/utils/cn";

const COLLAPSED_KEY = "opspilot.sidebar.collapsed";

/**
 * The authenticated application shell: docked sidebar on large screens
 * (collapsible to an icon rail), a navigation drawer on smaller ones, a sticky
 * top bar, and the page content inside error and loading boundaries.
 * Rendered under <RequireAuth>, so a user is always signed in here.
 */
export function AppLayout() {
  const location = useLocation();
  const [collapsed, setCollapsed] = usePersistentBoolean(COLLAPSED_KEY, false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [signingOut, setSigningOut] = useState(false);
  const docked = useMediaQuery(minWidth(SIDEBAR_DOCK_BREAKPOINT));

  // The drawer is for small screens only: close it when the viewport grows.
  useEffect(() => {
    if (docked) setDrawerOpen(false); // eslint-disable-line react-hooks/set-state-in-effect -- reacting to an external media query
  }, [docked]);

  return (
    <div className="min-h-full bg-canvas">
      <a
        href="#main-content"
        className="sr-only z-(--z-tooltip) rounded-control bg-surface px-3 py-2 text-sm font-medium text-fg shadow-popover focus:not-sr-only focus:fixed focus:top-3 focus:left-3"
      >
        Skip to content
      </a>

      <aside
        aria-label="Sidebar"
        className={cn(
          "fixed inset-y-0 left-0 z-(--z-sidebar) hidden border-r border-line bg-surface transition-[width] duration-200 lg:block",
          collapsed ? "w-sidebar-collapsed" : "w-sidebar",
        )}
      >
        <SidebarContent
          collapsed={collapsed}
          sidebarId="sidebar-navigation"
          onToggleCollapsed={() => setCollapsed(!collapsed)}
          onSignOut={() => setSigningOut(true)}
        />
      </aside>

      <Drawer open={drawerOpen} onClose={() => setDrawerOpen(false)} title="Navigation">
        <SidebarContent
          onNavigate={() => setDrawerOpen(false)}
          onSignOut={() => {
            setDrawerOpen(false);
            setSigningOut(true);
          }}
        />
      </Drawer>

      <div className={cn("flex min-h-full min-w-0 flex-col transition-[padding] duration-200", collapsed ? "lg:pl-sidebar-collapsed" : "lg:pl-sidebar")}>
        <Topbar onOpenNavigation={() => setDrawerOpen(true)} navigationOpen={drawerOpen} onSignOut={() => setSigningOut(true)} />
        <main id="main-content" tabIndex={-1} className="flex-1 px-gutter py-6 outline-none sm:py-8 lg:px-gutter-lg">
          <ResponsiveContainer>
            {/* Keyed by path so an error on one page does not stick after navigating away. */}
            <ErrorBoundary key={location.pathname}>
              <Suspense fallback={<Spinner label="Loading page" />}>
                <Outlet />
              </Suspense>
            </ErrorBoundary>
          </ResponsiveContainer>
        </main>
        <footer className="border-t border-line px-gutter py-4 text-xs text-fg-muted lg:px-gutter-lg">
          <ResponsiveContainer>OpsPilot AI · AI-powered Business Intelligence Assistant</ResponsiveContainer>
        </footer>
      </div>

      <SignOutDialog open={signingOut} onClose={() => setSigningOut(false)} />
    </div>
  );
}

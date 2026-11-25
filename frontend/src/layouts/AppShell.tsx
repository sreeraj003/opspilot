import { LogOut, UserRound } from "lucide-react";
import { useState } from "react";
import { NavLink, Outlet } from "react-router";

import { BrandMark } from "@/components/BrandMark";
import { useAuth } from "@/features/auth/authContext";
import { usePermissions } from "@/features/permissions/permissionsContext";
import { cn } from "@/utils/cn";

function navClass({ isActive }: { isActive: boolean }): string {
  return cn(
    "rounded-lg px-3 py-1.5 text-sm font-medium transition focus-visible:outline-2 focus-visible:outline-stone-900",
    isActive ? "bg-stone-100 text-stone-900" : "text-stone-600 hover:text-stone-900",
  );
}

export function AppShell() {
  const { user, logout } = useAuth();
  const { hasPermission } = usePermissions();
  const links = [
    { to: "/admin/users", label: "Users", permission: "users:read" },
    { to: "/admin/roles", label: "Roles", permission: "roles:read" },
    { to: "/admin/permissions", label: "Permissions", permission: "permissions:read" },
  ].filter((link) => hasPermission(link.permission));
  const [signingOut, setSigningOut] = useState(false);

  return (
    <div className="flex min-h-full flex-col">
      <header className="sticky top-0 z-10 border-b border-stone-200/80 bg-white/80 backdrop-blur">
        <div className="mx-auto flex h-16 max-w-6xl items-center justify-between gap-4 px-4 sm:px-6">
          <div className="flex items-center gap-6">
            <BrandMark />
            <nav aria-label="Main" className="hidden items-center gap-1 sm:flex">
              <NavLink to="/" end className={navClass}>
                Home
              </NavLink>
              {links.map((link) => (
                <NavLink key={link.to} to={link.to} className={navClass}>
                  {link.label}
                </NavLink>
              ))}
            </nav>
          </div>
          {user && (
            <div className="flex items-center gap-1">
              <NavLink to="/account" className={navClass} aria-label="Your account">
                <span className="inline-flex items-center gap-2">
                  <UserRound aria-hidden="true" className="h-4 w-4" />
                  <span className="hidden max-w-40 truncate md:inline">{user.full_name}</span>
                </span>
              </NavLink>
              <button
                type="button"
                disabled={signingOut}
                onClick={async () => {
                  setSigningOut(true);
                  await logout();
                }}
                className="inline-flex items-center gap-2 rounded-lg px-3 py-1.5 text-sm font-medium text-stone-600 hover:text-stone-900 focus-visible:outline-2 focus-visible:outline-stone-900 disabled:opacity-60"
              >
                <LogOut aria-hidden="true" className="h-4 w-4" />
                Sign out
              </button>
            </div>
          )}
        </div>
        {links.length > 0 && (
          <nav aria-label="Main (compact)" className="flex gap-1 overflow-x-auto px-4 pb-2 sm:hidden">
            <NavLink to="/" end className={navClass}>
              Home
            </NavLink>
            {links.map((link) => (
              <NavLink key={link.to} to={link.to} className={navClass}>
                {link.label}
              </NavLink>
            ))}
          </nav>
        )}
      </header>
      <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-10 sm:px-6 sm:py-14">
        <Outlet />
      </main>
      <footer className="border-t border-stone-200">
        <div className="mx-auto max-w-6xl px-4 py-5 text-xs text-stone-500 sm:px-6">
          OpsPilot AI · AI-powered Business Intelligence Assistant
        </div>
      </footer>
    </div>
  );
}

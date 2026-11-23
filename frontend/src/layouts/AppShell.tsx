import { NavLink, Outlet } from "react-router";

import { cn } from "@/utils/cn";

const NAV_ITEMS = [{ to: "/", label: "Overview", end: true }];

export function AppShell() {
  return (
    <div className="flex min-h-full flex-col">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex h-14 max-w-6xl items-center gap-8 px-4 sm:px-6">
          <NavLink to="/" className="flex items-center gap-2" aria-label="OpsPilot AI home">
            <span aria-hidden="true" className="grid h-7 w-7 place-items-center rounded-md bg-indigo-600 text-xs font-bold text-white">
              OP
            </span>
            <span className="text-base font-semibold tracking-tight">OpsPilot AI</span>
          </NavLink>
          <nav aria-label="Main" className="flex gap-1">
            {NAV_ITEMS.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={({ isActive }) =>
                  cn(
                    "rounded-md px-3 py-1.5 text-sm font-medium",
                    isActive ? "bg-slate-100 text-slate-900" : "text-slate-600 hover:text-slate-900",
                  )
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>
        </div>
      </header>
      <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-8 sm:px-6">
        <Outlet />
      </main>
      <footer className="border-t border-slate-200 py-4 text-center text-xs text-slate-500">
        OpsPilot AI · Enterprise Operations Intelligence Platform
      </footer>
    </div>
  );
}

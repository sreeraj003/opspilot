import { Outlet } from "react-router";

import { BrandMark } from "@/components/BrandMark";

export function AppShell() {
  return (
    <div className="flex min-h-full flex-col">
      <header className="sticky top-0 z-10 border-b border-stone-200/80 bg-white/80 backdrop-blur">
        <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-4 sm:px-6">
          <BrandMark />
          <span className="rounded-full border border-stone-200 px-2.5 py-0.5 text-xs font-medium text-stone-500">
            Foundation preview
          </span>
        </div>
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

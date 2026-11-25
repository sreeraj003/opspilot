import type { ReactNode } from "react";
import { Outlet } from "react-router";

import { BrandMark } from "@/components/BrandMark";
import { ThemeToggle } from "@/features/theme/ThemeToggle";

/** Chrome for pages anyone can open (no session check): brand, theme picker, centred content. */
export function PublicLayout({ children }: { children?: ReactNode }) {
  return (
    <div className="flex min-h-full flex-col bg-canvas">
      <header className="flex h-topbar shrink-0 items-center justify-between px-gutter lg:px-gutter-lg">
        <BrandMark />
        <ThemeToggle />
      </header>
      <main id="main-content" className="flex flex-1 flex-col items-center justify-center px-gutter py-10">
        {children ?? <Outlet />}
      </main>
      <footer className="px-gutter py-5 text-center text-xs text-fg-muted">OpsPilot AI · AI-powered Business Intelligence Assistant</footer>
    </div>
  );
}

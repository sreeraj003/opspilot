import { Sparkles } from "lucide-react";
import { Outlet } from "react-router";

import { BrandMark } from "@/components/BrandMark";
import { PRODUCT_PRINCIPLES } from "@/features/product/principles";
import { ThemeToggle } from "@/features/theme/ThemeToggle";

/**
 * Layout of the sign-in and password pages: the form on the left and, on large
 * screens, a short product description on the right. Mounted under
 * <RedirectIfAuthenticated>, so signed-in users never see it.
 */
export function AuthLayout() {
  return (
    <div className="grid min-h-full bg-canvas lg:grid-cols-[minmax(0,1fr)_minmax(0,30rem)] xl:grid-cols-[minmax(0,1fr)_minmax(0,36rem)]">
      <div className="flex min-h-full flex-col">
        <header className="flex h-topbar shrink-0 items-center justify-between px-gutter lg:px-gutter-lg">
          <BrandMark />
          <ThemeToggle />
        </header>
        <main id="main-content" className="flex flex-1 flex-col items-center justify-center px-gutter py-10">
          <Outlet />
        </main>
        <footer className="px-gutter py-5 text-center text-xs text-fg-muted">OpsPilot AI · AI-powered Business Intelligence Assistant</footer>
      </div>
      <aside aria-label="About OpsPilot AI" className="relative hidden overflow-hidden border-l border-line bg-surface lg:flex lg:flex-col lg:justify-center lg:px-12">
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_at_top_right,var(--color-accent-subtle),transparent_60%)]"
        />
        <div className="relative max-w-sm">
          <span className="grid h-10 w-10 place-items-center rounded-card bg-accent text-accent-fg">
            <Sparkles aria-hidden="true" className="h-5 w-5" />
          </span>
          <p className="mt-6 text-2xl font-semibold tracking-tight text-fg">Business answers from your own data.</p>
          <p className="mt-3 text-sm leading-relaxed text-fg-muted">
            OpsPilot AI is a business intelligence assistant that runs inside your organisation, with access decided by
            your administrators.
          </p>
          <ul className="mt-8 space-y-5">
            {PRODUCT_PRINCIPLES.map(({ icon: Icon, title, text }) => (
              <li key={title} className="flex gap-3">
                <span className="grid h-8 w-8 shrink-0 place-items-center rounded-control border border-line bg-surface-muted text-fg-secondary">
                  <Icon aria-hidden="true" className="h-4 w-4" strokeWidth={1.75} />
                </span>
                <div>
                  <p className="text-sm font-medium text-fg">{title}</p>
                  <p className="mt-0.5 text-sm text-fg-muted">{text}</p>
                </div>
              </li>
            ))}
          </ul>
        </div>
      </aside>
    </div>
  );
}

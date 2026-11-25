import type { ReactNode } from "react";

/** The card holding an authentication form (sign in, password reset). */
export function AuthCard({ title, subtitle, children }: { title: string; subtitle?: ReactNode; children: ReactNode }) {
  return (
    <div className="w-full max-w-sm">
      <div className="rounded-overlay border border-line bg-surface p-6 shadow-card sm:p-8">
        <h1 className="text-xl font-semibold tracking-tight text-fg">{title}</h1>
        {subtitle && <p className="mt-1.5 text-sm text-fg-muted">{subtitle}</p>}
        <div className="mt-6">{children}</div>
      </div>
    </div>
  );
}

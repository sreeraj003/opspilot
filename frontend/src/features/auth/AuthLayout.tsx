import type { ReactNode } from "react";

import { BrandMark } from "@/components/BrandMark";

/** Centred card used by the public authentication pages. */
export function AuthLayout({ title, subtitle, children }: { title: string; subtitle?: ReactNode; children: ReactNode }) {
  return (
    <div className="flex min-h-full flex-col items-center justify-center px-4 py-12">
      <BrandMark />
      <div className="mt-8 w-full max-w-sm rounded-2xl border border-stone-200 bg-white p-6 shadow-[0_1px_2px_rgba(28,25,23,0.04)] sm:p-8">
        <h1 className="text-xl font-semibold tracking-tight text-stone-900">{title}</h1>
        {subtitle && <p className="mt-1.5 text-sm text-stone-500">{subtitle}</p>}
        <div className="mt-6">{children}</div>
      </div>
    </div>
  );
}

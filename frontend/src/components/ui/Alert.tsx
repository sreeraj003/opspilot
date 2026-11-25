import { AlertCircle, CheckCircle2, Info } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/utils/cn";

type Tone = "error" | "success" | "info";

const STYLES: Record<Tone, { box: string; icon: typeof Info }> = {
  error: { box: "border-rose-200 bg-rose-50 text-rose-800", icon: AlertCircle },
  success: { box: "border-emerald-200 bg-emerald-50 text-emerald-800", icon: CheckCircle2 },
  info: { box: "border-stone-200 bg-stone-50 text-stone-700", icon: Info },
};

export function Alert({ tone = "info", children, className }: { tone?: Tone; children: ReactNode; className?: string }) {
  const { box, icon: Icon } = STYLES[tone];
  return (
    <div
      role={tone === "error" ? "alert" : "status"}
      className={cn("flex items-start gap-2.5 rounded-lg border px-3.5 py-3 text-sm", box, className)}
    >
      <Icon aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0" />
      <div className="min-w-0">{children}</div>
    </div>
  );
}

import { cn } from "@/utils/cn";

export type StatusTone = "success" | "warning" | "danger" | "neutral";

const TONE_CLASSES: Record<StatusTone, string> = {
  success: "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
  warning: "bg-amber-50 text-amber-800 ring-amber-600/20",
  danger: "bg-rose-50 text-rose-700 ring-rose-600/20",
  neutral: "bg-stone-100 text-stone-600 ring-stone-500/20",
};

const DOT_CLASSES: Record<StatusTone, string> = {
  success: "bg-emerald-500",
  warning: "bg-amber-500",
  danger: "bg-rose-500",
  neutral: "bg-stone-400",
};

interface StatusBadgeProps {
  tone: StatusTone;
  label: string;
  pulse?: boolean;
}

export function StatusBadge({ tone, label, pulse = false }: StatusBadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset",
        TONE_CLASSES[tone],
      )}
    >
      <span aria-hidden="true" className={cn("h-1.5 w-1.5 rounded-full", DOT_CLASSES[tone], pulse && "animate-pulse")} />
      {label}
    </span>
  );
}

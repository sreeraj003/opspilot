import { TONE_CLASSES, type Tone } from "@/components/ui/tones";
import { cn } from "@/utils/cn";

export type StatusTone = Extract<Tone, "success" | "warning" | "danger" | "info" | "neutral">;

export interface StatusIndicatorProps {
  tone: StatusTone;
  label: string;
  /** Animate the dot (for checks in progress). Respects reduced motion. */
  pulse?: boolean;
  /** "pill" for tables and headers, "dot" for dense inline text. */
  variant?: "pill" | "dot";
}

/** A state such as Active / Inactive / Locked. The label carries the meaning, never the colour alone. */
export function StatusIndicator({ tone, label, pulse = false, variant = "pill" }: StatusIndicatorProps) {
  const classes = TONE_CLASSES[tone];
  const dot = <span aria-hidden="true" className={cn("h-1.5 w-1.5 shrink-0 rounded-full", classes.solid, pulse && "animate-pulse")} />;
  if (variant === "dot") {
    return (
      <span className="inline-flex items-center gap-1.5 text-sm text-fg-secondary">
        {dot}
        {label}
      </span>
    );
  }
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium whitespace-nowrap ring-1 ring-inset",
        classes.subtle,
        classes.text,
        classes.line,
      )}
    >
      {dot}
      {label}
    </span>
  );
}

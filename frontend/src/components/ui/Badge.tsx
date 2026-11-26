import type { ReactNode } from "react";

import { TONE_CLASSES, type Tone } from "@/components/ui/tones";
import { cn } from "@/utils/cn";

export type { Tone };

export interface BadgeProps {
  tone?: Tone;
  children: ReactNode;
  /** Monospace, for identifiers and keys. */
  mono?: boolean;
  title?: string;
  className?: string;
}

/** A small label for categories and counts (use StatusIndicator for states). */
export function Badge({ tone = "neutral", mono = false, title, className, children }: BadgeProps) {
  const classes = TONE_CLASSES[tone];
  return (
    <span
      title={title}
      className={cn(
        "inline-flex items-center gap-1 rounded-sm px-1.5 py-0.5 text-xs font-medium ring-1 ring-inset",
        classes.subtle,
        classes.text,
        classes.line,
        mono && "font-mono",
        className,
      )}
    >
      {children}
    </span>
  );
}

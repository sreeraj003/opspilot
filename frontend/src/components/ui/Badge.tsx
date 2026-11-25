import type { ReactNode } from "react";

import { cn } from "@/utils/cn";

export type Tone = "neutral" | "accent" | "success" | "warning" | "danger" | "info";

/** Background, text and ring classes per tone; shared by Badge, StatusIndicator and Alert. */
export const TONE_CLASSES: Record<Tone, { subtle: string; text: string; line: string; solid: string }> = {
  neutral: { subtle: "bg-surface-muted", text: "text-fg-secondary", line: "ring-line-strong/70", solid: "bg-fg-subtle" },
  accent: { subtle: "bg-accent-subtle", text: "text-accent-text", line: "ring-accent/25", solid: "bg-accent" },
  success: { subtle: "bg-success-subtle", text: "text-success-text", line: "ring-success-line", solid: "bg-success" },
  warning: { subtle: "bg-warning-subtle", text: "text-warning-text", line: "ring-warning-line", solid: "bg-warning" },
  danger: { subtle: "bg-danger-subtle", text: "text-danger-text", line: "ring-danger-line", solid: "bg-danger" },
  info: { subtle: "bg-info-subtle", text: "text-info-text", line: "ring-info-line", solid: "bg-info" },
};

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

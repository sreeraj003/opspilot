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

import { cn } from "@/utils/cn";

export type ControlSize = "sm" | "md";

/** Shared look of text-like controls. */
export function controlClasses({ size = "md", className }: { size?: ControlSize; className?: string } = {}): string {
  return cn(
    "block w-full rounded-control border border-line bg-surface text-fg shadow-xs transition-colors",
    "placeholder:text-fg-subtle hover:border-line-strong",
    "focus-visible:border-accent focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/25",
    "aria-invalid:border-danger aria-invalid:focus-visible:ring-danger/20",
    "disabled:cursor-not-allowed disabled:bg-surface-muted disabled:text-fg-muted",
    size === "sm" ? "h-8 px-2.5 text-xs" : "h-9 px-3 text-sm",
    className,
  );
}

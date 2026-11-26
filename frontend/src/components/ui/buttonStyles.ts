import { cn } from "@/utils/cn";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger" | "danger-ghost";
export type ButtonSize = "sm" | "md" | "lg" | "icon" | "icon-sm";

const VARIANTS: Record<ButtonVariant, string> = {
  primary: "bg-accent text-accent-fg shadow-xs hover:bg-accent-hover",
  secondary: "border border-line bg-surface text-fg-secondary shadow-xs hover:bg-surface-muted hover:text-fg",
  ghost: "text-fg-secondary hover:bg-surface-muted hover:text-fg",
  danger: "bg-danger text-accent-fg shadow-xs hover:bg-danger-hover",
  "danger-ghost": "text-danger-text hover:bg-danger-subtle",
};

const SIZES: Record<ButtonSize, string> = {
  sm: "h-8 gap-1.5 px-2.5 text-xs",
  md: "h-9 gap-2 px-3.5 text-sm",
  lg: "h-10 gap-2 px-4 text-sm",
  icon: "h-9 w-9",
  "icon-sm": "h-8 w-8",
};

export interface ButtonStyleOptions {
  variant?: ButtonVariant;
  size?: ButtonSize;
  fullWidth?: boolean;
  className?: string;
}

/** Class names for anything that should look like a button (links, menu triggers). */
export function buttonClasses({ variant = "primary", size = "md", fullWidth = false, className }: ButtonStyleOptions = {}): string {
  return cn(
    "inline-flex shrink-0 items-center justify-center rounded-control font-medium whitespace-nowrap transition-colors",
    "disabled:pointer-events-none disabled:opacity-55 aria-disabled:pointer-events-none aria-disabled:opacity-55",
    VARIANTS[variant],
    SIZES[size],
    fullWidth && "w-full",
    className,
  );
}

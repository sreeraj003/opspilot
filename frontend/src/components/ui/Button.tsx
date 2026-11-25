import { Loader2 } from "lucide-react";
import type { ButtonHTMLAttributes } from "react";

import { cn } from "@/utils/cn";

type Variant = "primary" | "secondary" | "danger" | "ghost";

const VARIANTS: Record<Variant, string> = {
  primary: "bg-stone-900 text-white hover:bg-stone-700 disabled:bg-stone-400",
  secondary: "border border-stone-200 bg-white text-stone-700 shadow-sm hover:bg-stone-50",
  danger: "bg-rose-600 text-white hover:bg-rose-500 disabled:bg-rose-300",
  ghost: "text-stone-600 hover:bg-stone-100 hover:text-stone-900",
};

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  loading?: boolean;
}

export function Button({ variant = "primary", loading = false, className, children, disabled, type, ...props }: ButtonProps) {
  return (
    <button
      type={type ?? "button"}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={cn(
        "inline-flex items-center justify-center gap-2 rounded-lg px-3.5 py-2 text-sm font-medium transition focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-stone-900 disabled:cursor-not-allowed disabled:opacity-70",
        VARIANTS[variant],
        className,
      )}
      {...props}
    >
      {loading && <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" />}
      {children}
    </button>
  );
}

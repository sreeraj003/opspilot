import { useId, type ReactNode } from "react";

import { cn } from "@/utils/cn";

export interface SwitchProps {
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
  label: ReactNode;
  description?: ReactNode;
  disabled?: boolean;
  /** Visually hide the label (it stays the switch's accessible name). */
  hideLabel?: boolean;
  className?: string;
}

/** An on/off setting that applies immediately (use a Checkbox inside forms that are submitted). */
export function Switch({ checked, onCheckedChange, label, description, disabled = false, hideLabel = false, className }: SwitchProps) {
  const id = useId();
  return (
    <div className={cn("flex items-start justify-between gap-4", className)}>
      <div className={cn("min-w-0 text-sm", hideLabel && "sr-only")}>
        <span id={`${id}-label`} className="font-medium text-fg">
          {label}
        </span>
        {description && (
          <p id={`${id}-description`} className="text-xs text-fg-muted">
            {description}
          </p>
        )}
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-labelledby={`${id}-label`}
        aria-describedby={description ? `${id}-description` : undefined}
        disabled={disabled}
        onClick={() => onCheckedChange(!checked)}
        className={cn(
          "relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors disabled:cursor-not-allowed disabled:opacity-55",
          checked ? "bg-accent" : "bg-line-strong",
        )}
      >
        <span
          aria-hidden="true"
          className={cn(
            "inline-block h-4 w-4 rounded-full bg-surface shadow-xs transition-transform",
            checked ? "translate-x-4.5" : "translate-x-0.5",
          )}
        />
      </button>
    </div>
  );
}

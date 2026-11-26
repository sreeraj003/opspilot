import { useId, type ComponentPropsWithRef, type ReactNode } from "react";

import { cn } from "@/utils/cn";

export interface CheckboxProps extends Omit<ComponentPropsWithRef<"input">, "type"> {
  label: ReactNode;
  description?: ReactNode;
}

/** Native checkbox with a label and optional description (works with react-hook-form's register). */
export function Checkbox({ label, description, className, id, disabled, ...props }: CheckboxProps) {
  const generated = useId();
  const inputId = id ?? generated;
  const descriptionId = description ? `${inputId}-description` : undefined;
  return (
    <div className={cn("flex items-start gap-2.5", className)}>
      <input
        id={inputId}
        type="checkbox"
        disabled={disabled}
        aria-describedby={descriptionId}
        className="mt-0.5 h-4 w-4 shrink-0 cursor-pointer rounded-xs border-line-strong accent-accent disabled:cursor-not-allowed"
        {...props}
      />
      <div className="min-w-0 text-sm">
        <label htmlFor={inputId} className={cn("text-fg-secondary", disabled ? "cursor-not-allowed opacity-60" : "cursor-pointer")}>
          {label}
        </label>
        {description && (
          <p id={descriptionId} className="text-xs text-fg-muted">
            {description}
          </p>
        )}
      </div>
    </div>
  );
}

import { useId, type ReactNode } from "react";

import { FormFieldContext } from "@/components/ui/formFieldContext";

import { cn } from "@/utils/cn";

export interface FormFieldProps {
  label: ReactNode;
  /** Help text shown under the control while there is no error. */
  description?: ReactNode;
  /** Field-level error message, announced to assistive technology. */
  error?: string;
  required?: boolean;
  /** Visually hide the label (it stays available to screen readers). */
  hideLabel?: boolean;
  id?: string;
  className?: string;
  children: ReactNode;
}

/** Label, control, help text and error message with consistent spacing and ARIA wiring. */
export function FormField({ label, description, error, required = false, hideLabel = false, id, className, children }: FormFieldProps) {
  const generated = useId();
  const controlId = id ?? generated;
  const descriptionId = description && !error ? `${controlId}-description` : undefined;
  const errorId = error ? `${controlId}-error` : undefined;
  const describedBy = [errorId, descriptionId].filter(Boolean).join(" ") || undefined;
  return (
    <div className={className}>
      {/* The required mark is CSS-generated so it is not part of the label text; the control gets aria-required. */}
      <label
        htmlFor={controlId}
        className={cn(
          "mb-1.5 block text-sm font-medium text-fg-secondary",
          required && "after:ml-0.5 after:text-danger-text after:content-['*']",
          hideLabel && "sr-only",
        )}
      >
        {label}
      </label>
      <FormFieldContext.Provider value={{ id: controlId, describedBy, invalid: Boolean(error), required }}>
        {children}
      </FormFieldContext.Provider>
      {descriptionId && (
        <p id={descriptionId} className="mt-1.5 text-xs text-fg-muted">
          {description}
        </p>
      )}
      {errorId && (
        <p id={errorId} className="mt-1.5 text-xs font-medium text-danger-text">
          {error}
        </p>
      )}
    </div>
  );
}

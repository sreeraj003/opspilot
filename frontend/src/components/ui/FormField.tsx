import { createContext, useContext, useId, type ReactNode } from "react";

import { cn } from "@/utils/cn";

interface FormFieldContextValue {
  id: string;
  describedBy: string | undefined;
  invalid: boolean;
  required: boolean;
}

const FormFieldContext = createContext<FormFieldContextValue | null>(null);

/**
 * The id, description and error wiring of the surrounding <FormField>, if any.
 * Controls (Input, Textarea, Select) read it so labels and messages are always associated.
 */
export function useFormFieldControl<P extends { id?: string; "aria-describedby"?: string; "aria-invalid"?: unknown; required?: boolean }>(
  props: P,
): P {
  const field = useContext(FormFieldContext);
  if (!field) return props;
  return {
    ...props,
    id: props.id ?? field.id,
    "aria-describedby": [field.describedBy, props["aria-describedby"]].filter(Boolean).join(" ") || undefined,
    "aria-invalid": props["aria-invalid"] ?? (field.invalid || undefined),
    required: props.required ?? (field.required || undefined),
  };
}

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
      <label htmlFor={controlId} className={cn("mb-1.5 block text-sm font-medium text-fg-secondary", hideLabel && "sr-only")}>
        {label}
        {required && (
          <span aria-hidden="true" className="ml-0.5 text-danger-text">
            *
          </span>
        )}
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

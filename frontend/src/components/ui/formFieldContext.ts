import { createContext, useContext } from "react";

export interface FormFieldContextValue {
  id: string;
  describedBy: string | undefined;
  invalid: boolean;
  required: boolean;
}

export const FormFieldContext = createContext<FormFieldContextValue | null>(null);

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

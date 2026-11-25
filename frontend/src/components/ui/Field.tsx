import type { ComponentPropsWithRef, ReactNode } from "react";

import { FormField } from "@/components/ui/FormField";
import { Input, PasswordInput, Textarea, type InputProps } from "@/components/ui/Input";

/**
 * Convenience fields: a <FormField> around one control. They take the props
 * of the control plus label/hint/error, so `{...register("name")}` just works.
 */
interface FieldProps extends InputProps {
  label: string;
  error?: string;
  hint?: ReactNode;
}

export function TextField({ label, error, hint, className, id, required, ...props }: FieldProps) {
  return (
    <FormField label={label} description={hint} error={error} id={id} required={required} className={className}>
      <Input {...props} />
    </FormField>
  );
}

export function PasswordField({ label, error, hint, className, id, required, ...props }: FieldProps) {
  return (
    <FormField label={label} description={hint} error={error} id={id} required={required} className={className}>
      <PasswordInput revealLabel={label.toLowerCase()} {...props} />
    </FormField>
  );
}

interface TextareaFieldProps extends ComponentPropsWithRef<"textarea"> {
  label: string;
  error?: string;
  hint?: ReactNode;
}

export function TextareaField({ label, error, hint, className, id, required, ...props }: TextareaFieldProps) {
  return (
    <FormField label={label} description={hint} error={error} id={id} required={required} className={className}>
      <Textarea {...props} />
    </FormField>
  );
}

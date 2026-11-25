import { Loader2 } from "lucide-react";
import type { ComponentPropsWithRef } from "react";
import { Link, type LinkProps } from "react-router";

import { buttonClasses, type ButtonStyleOptions } from "@/components/ui/buttonStyles";

export type { ButtonSize, ButtonStyleOptions, ButtonVariant } from "@/components/ui/buttonStyles";

export interface ButtonProps extends ComponentPropsWithRef<"button">, Omit<ButtonStyleOptions, "className"> {
  /** Shows a spinner and disables the button while an action runs. */
  loading?: boolean;
}

export function Button({
  variant,
  size,
  fullWidth,
  loading = false,
  className,
  children,
  disabled,
  type,
  ...props
}: ButtonProps) {
  return (
    <button
      type={type ?? "button"}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={buttonClasses({ variant, size, fullWidth, className })}
      {...props}
    >
      {loading && <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" />}
      {children}
    </button>
  );
}

export type ButtonLinkProps = LinkProps & Omit<ButtonStyleOptions, "className">;

/** A router link styled as a button, for navigation that looks like an action. */
export function ButtonLink({ variant = "secondary", size, fullWidth, className, ...props }: ButtonLinkProps) {
  return <Link className={buttonClasses({ variant, size, fullWidth, className: typeof className === "string" ? className : undefined })} {...props} />;
}

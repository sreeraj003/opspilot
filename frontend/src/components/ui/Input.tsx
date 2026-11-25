import { ChevronDown, Eye, EyeOff, Search, X } from "lucide-react";
import { useState, type ComponentPropsWithRef } from "react";

import { controlClasses, type ControlSize } from "@/components/ui/controlStyles";
import { useFormFieldControl } from "@/components/ui/formFieldContext";

import { cn } from "@/utils/cn";

export interface InputProps extends Omit<ComponentPropsWithRef<"input">, "size"> {
  size?: ControlSize;
}

export function Input({ size, className, ...props }: InputProps) {
  const control = useFormFieldControl(props);
  return <input {...control} className={controlClasses({ size, className })} />;
}

export function Textarea({ className, rows = 3, ...props }: ComponentPropsWithRef<"textarea">) {
  const control = useFormFieldControl(props);
  return <textarea rows={rows} {...control} className={controlClasses({ className: cn("h-auto py-2 leading-relaxed", className) })} />;
}

export interface SelectProps extends Omit<ComponentPropsWithRef<"select">, "size"> {
  size?: ControlSize;
}

/** Native select (best keyboard and mobile support) with the design-system look. */
export function Select({ size, className, children, ...props }: SelectProps) {
  const control = useFormFieldControl(props);
  return (
    <div className={cn("relative", className)}>
      <select {...control} className={controlClasses({ size, className: "appearance-none pr-9" })}>
        {children}
      </select>
      <ChevronDown aria-hidden="true" className="pointer-events-none absolute top-1/2 right-3 h-4 w-4 -translate-y-1/2 text-fg-subtle" />
    </div>
  );
}

export interface SearchInputProps extends Omit<InputProps, "type" | "onChange" | "value"> {
  /** Accessible name; also used as the placeholder when none is given. */
  label: string;
  value: string;
  onValueChange: (value: string) => void;
}

/** Search box with an icon and a clear button. */
export function SearchInput({ label, value, onValueChange, placeholder, className, size, ...props }: SearchInputProps) {
  return (
    <div className={cn("relative", className)}>
      <Search aria-hidden="true" className="pointer-events-none absolute top-1/2 left-3 h-4 w-4 -translate-y-1/2 text-fg-subtle" />
      <Input
        type="search"
        aria-label={label}
        placeholder={placeholder ?? label}
        value={value}
        size={size}
        onChange={(event) => onValueChange(event.target.value)}
        className="pr-9 pl-9 [&::-webkit-search-cancel-button]:hidden"
        {...props}
      />
      {value && (
        <button
          type="button"
          onClick={() => onValueChange("")}
          aria-label="Clear search"
          className="absolute top-1/2 right-2 grid h-6 w-6 -translate-y-1/2 place-items-center rounded-sm text-fg-subtle hover:bg-surface-muted hover:text-fg"
        >
          <X aria-hidden="true" className="h-3.5 w-3.5" />
        </button>
      )}
    </div>
  );
}

export interface PasswordInputProps extends Omit<InputProps, "type"> {
  /** Used for the show/hide button's accessible name ("Show password"). */
  revealLabel?: string;
}

/** Password input with a show/hide toggle. Paste and password managers work normally. */
export function PasswordInput({ revealLabel = "password", className, ...props }: PasswordInputProps) {
  const [visible, setVisible] = useState(false);
  return (
    <div className={cn("relative", className)}>
      <Input type={visible ? "text" : "password"} spellCheck={false} autoCapitalize="none" className="pr-10" {...props} />
      <button
        type="button"
        onClick={() => setVisible((value) => !value)}
        aria-label={visible ? `Hide ${revealLabel}` : `Show ${revealLabel}`}
        aria-pressed={visible}
        className="absolute inset-y-0 right-0 grid w-10 place-items-center rounded-r-control text-fg-subtle hover:text-fg"
      >
        {visible ? <EyeOff aria-hidden="true" className="h-4 w-4" /> : <Eye aria-hidden="true" className="h-4 w-4" />}
      </button>
    </div>
  );
}

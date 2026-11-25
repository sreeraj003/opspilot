import { Plus, X } from "lucide-react";
import { useId, useState, type ReactNode } from "react";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Checkbox as BaseCheckbox } from "@/components/ui/Checkbox";
import { controlClasses, SearchInput as BaseSearchInput } from "@/components/ui/Input";
import { StatusIndicator } from "@/components/ui/StatusIndicator";

export { Card } from "@/components/ui/Card";
export { Pagination } from "@/components/ui/Pagination";

export function ActiveBadge({ active }: { active: boolean }) {
  return active ? <StatusIndicator tone="success" label="Active" /> : <StatusIndicator tone="neutral" label="Inactive" />;
}

export function SystemTag() {
  return (
    <Badge className="ml-2" title="Built in and protected">
      System
    </Badge>
  );
}

export function SearchInput({ label, value, onChange, placeholder }: { label: string; value: string; onChange: (v: string) => void; placeholder?: string }) {
  return <BaseSearchInput label={label} value={value} onValueChange={onChange} placeholder={placeholder} className="min-w-56 flex-1" />;
}

/** A list of short identifiers (tables, columns) entered one at a time. */
export function TagInput({
  label,
  values,
  onChange,
  placeholder,
  validate,
  hint,
}: {
  label: string;
  values: string[];
  onChange: (values: string[]) => void;
  placeholder?: string;
  validate?: (value: string) => string | null;
  hint?: ReactNode;
}) {
  const id = useId();
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const add = () => {
    const parts = text
      .split(",")
      .map((part) => part.trim())
      .filter(Boolean);
    if (parts.length === 0) return;
    for (const part of parts) {
      const problem = validate?.(part) ?? null;
      if (problem) {
        setError(problem);
        return;
      }
    }
    onChange([...values, ...parts.filter((part) => !values.includes(part))]);
    setText("");
    setError(null);
  };
  return (
    <div>
      <label htmlFor={id} className="mb-1.5 block text-sm font-medium text-fg-secondary">
        {label}
      </label>
      {values.length > 0 && (
        <ul className="mb-2 flex flex-wrap gap-1.5" aria-label={`${label} added`}>
          {values.map((value) => (
            <li key={value} className="inline-flex items-center gap-1 rounded-sm bg-surface-strong py-0.5 pr-1 pl-2 font-mono text-xs text-fg">
              {value}
              <button
                type="button"
                aria-label={`Remove ${value}`}
                onClick={() => onChange(values.filter((v) => v !== value))}
                className="rounded-xs p-0.5 text-fg-muted hover:bg-line-strong hover:text-fg"
              >
                <X aria-hidden="true" className="h-3 w-3" />
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="flex gap-2">
        <input
          id={id}
          value={text}
          placeholder={placeholder}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? `${id}-error` : hint ? `${id}-hint` : undefined}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              add();
            }
          }}
          className={controlClasses({ className: "font-mono" })}
        />
        <Button variant="secondary" onClick={add} aria-label={`Add to ${label.toLowerCase()}`}>
          <Plus aria-hidden="true" className="h-4 w-4" />
          Add
        </Button>
      </div>
      {error ? (
        <p id={`${id}-error`} className="mt-1.5 text-xs font-medium text-danger-text">
          {error}
        </p>
      ) : (
        hint && (
          <p id={`${id}-hint`} className="mt-1.5 text-xs text-fg-muted">
            {hint}
          </p>
        )
      )}
    </div>
  );
}

export function Checkbox({ label, checked, onChange, description }: { label: string; checked: boolean; onChange: (checked: boolean) => void; description?: string }) {
  return <BaseCheckbox label={label} description={description} checked={checked} onChange={(event) => onChange(event.target.checked)} />;
}

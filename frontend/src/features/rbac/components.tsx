import { ChevronLeft, ChevronRight, Plus, Search, X } from "lucide-react";
import { useEffect, useId, useState, type ReactNode } from "react";

import { StatusBadge } from "@/components/StatusBadge";
import { Button } from "@/components/ui/Button";

export const SELECT_CLASSES =
  "rounded-lg border border-stone-200 bg-white px-3 py-2 text-sm text-stone-700 shadow-sm focus:border-stone-500 focus:outline-none focus:ring-2 focus:ring-stone-900/10";
/** Input styling without a width, for inputs sized by their container (inline rows). */
export const FIELD_CLASSES =
  "rounded-lg border border-stone-200 bg-white px-3 py-2 text-sm text-stone-900 shadow-sm focus:border-stone-500 focus:outline-none focus:ring-2 focus:ring-stone-900/10";
export const INPUT_CLASSES = `w-full ${FIELD_CLASSES}`;

export function ActiveBadge({ active }: { active: boolean }) {
  return active ? <StatusBadge tone="success" label="Active" /> : <StatusBadge tone="neutral" label="Inactive" />;
}

export function SystemTag() {
  return (
    <span className="ml-2 rounded-md bg-stone-100 px-1.5 py-0.5 text-xs font-medium text-stone-600" title="Built in and protected">
      System
    </span>
  );
}

export function useDebounced<T>(value: T, delayMs = 300): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(timer);
  }, [value, delayMs]);
  return debounced;
}

export function SearchInput({ label, value, onChange, placeholder }: { label: string; value: string; onChange: (v: string) => void; placeholder?: string }) {
  return (
    <div className="relative min-w-56 flex-1">
      <Search aria-hidden="true" className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-stone-400" />
      <input
        type="search"
        aria-label={label}
        placeholder={placeholder}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className={`${INPUT_CLASSES} pl-9`}
      />
    </div>
  );
}

export function Pagination({ page, pageSize, total, onPage }: { page: number; pageSize: number; total: number; onPage: (page: number) => void }) {
  if (total === 0) return null;
  const lastPage = Math.max(0, Math.ceil(total / pageSize) - 1);
  return (
    <nav aria-label="Pagination" className="flex items-center justify-between text-sm text-stone-600">
      <p>
        Showing {page * pageSize + 1}–{Math.min(total, (page + 1) * pageSize)} of {total}
      </p>
      <div className="flex gap-2">
        <Button variant="secondary" disabled={page === 0} onClick={() => onPage(page - 1)}>
          <ChevronLeft aria-hidden="true" className="h-4 w-4" />
          Previous
        </Button>
        <Button variant="secondary" disabled={page >= lastPage} onClick={() => onPage(page + 1)}>
          Next
          <ChevronRight aria-hidden="true" className="h-4 w-4" />
        </Button>
      </div>
    </nav>
  );
}

export function Card({ title, description, actions, children }: { title: string; description?: ReactNode; actions?: ReactNode; children: ReactNode }) {
  const id = useId();
  return (
    <section aria-labelledby={id} className="rounded-2xl border border-stone-200 bg-white p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 id={id} className="text-base font-semibold text-stone-900">
            {title}
          </h2>
          {description && <p className="mt-1 text-sm text-stone-500">{description}</p>}
        </div>
        {actions}
      </div>
      <div className="mt-5">{children}</div>
    </section>
  );
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
      <label htmlFor={id} className="mb-1.5 block text-sm font-medium text-stone-700">
        {label}
      </label>
      {values.length > 0 && (
        <ul className="mb-2 flex flex-wrap gap-1.5" aria-label={`${label} added`}>
          {values.map((value) => (
            <li key={value} className="inline-flex items-center gap-1 rounded-md bg-stone-100 py-0.5 pl-2 pr-1 font-mono text-xs text-stone-800">
              {value}
              <button
                type="button"
                aria-label={`Remove ${value}`}
                onClick={() => onChange(values.filter((v) => v !== value))}
                className="rounded p-0.5 text-stone-500 hover:bg-stone-200 hover:text-stone-900"
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
          className={`${INPUT_CLASSES} font-mono`}
        />
        <Button variant="secondary" onClick={add} aria-label={`Add to ${label.toLowerCase()}`}>
          <Plus aria-hidden="true" className="h-4 w-4" />
          Add
        </Button>
      </div>
      {error ? (
        <p id={`${id}-error`} className="mt-1.5 text-xs font-medium text-rose-600">
          {error}
        </p>
      ) : (
        hint && (
          <p id={`${id}-hint`} className="mt-1.5 text-xs text-stone-500">
            {hint}
          </p>
        )
      )}
    </div>
  );
}

export function Checkbox({ label, checked, onChange, description }: { label: string; checked: boolean; onChange: (checked: boolean) => void; description?: string }) {
  return (
    <label className="flex items-start gap-2 text-sm text-stone-700">
      <input type="checkbox" className="mt-0.5 h-4 w-4 rounded border-stone-300" checked={checked} onChange={(event) => onChange(event.target.checked)} />
      <span>
        {label}
        {description && <span className="block text-xs text-stone-500">{description}</span>}
      </span>
    </label>
  );
}

export function formatEventType(type: string): string {
  const text = type.replace(/_/g, " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

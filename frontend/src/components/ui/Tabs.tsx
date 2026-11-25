import { useId, useRef, type KeyboardEvent, type ReactNode } from "react";

import { cn } from "@/utils/cn";

export interface TabItem {
  id: string;
  label: ReactNode;
  content: ReactNode;
  disabled?: boolean;
}

export interface TabsProps {
  items: TabItem[];
  value: string;
  onValueChange: (id: string) => void;
  /** Accessible name of the tab list. */
  label: string;
  className?: string;
}

/**
 * Controlled tabs (WAI-ARIA tabs pattern with automatic activation):
 * arrow keys, Home and End move between tabs; only the active tab is in the Tab order.
 */
export function Tabs({ items, value, onValueChange, label, className }: TabsProps) {
  const baseId = useId();
  const listRef = useRef<HTMLDivElement>(null);
  const enabled = items.filter((item) => !item.disabled);

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const index = enabled.findIndex((item) => item.id === value);
    let next: number | null = null;
    if (event.key === "ArrowRight") next = (index + 1) % enabled.length;
    else if (event.key === "ArrowLeft") next = (index - 1 + enabled.length) % enabled.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = enabled.length - 1;
    if (next === null) return;
    event.preventDefault();
    const target = enabled[next]!;
    onValueChange(target.id);
    listRef.current?.querySelector<HTMLElement>(`[data-tab-id="${target.id}"]`)?.focus();
  };

  return (
    <div className={className}>
      <div
        ref={listRef}
        role="tablist"
        aria-label={label}
        onKeyDown={onKeyDown}
        className="-mx-1 flex gap-1 overflow-x-auto border-b border-line px-1"
      >
        {items.map((item) => {
          const selected = item.id === value;
          return (
            <button
              key={item.id}
              type="button"
              role="tab"
              id={`${baseId}-tab-${item.id}`}
              data-tab-id={item.id}
              aria-selected={selected}
              aria-controls={`${baseId}-panel-${item.id}`}
              tabIndex={selected ? 0 : -1}
              disabled={item.disabled}
              onClick={() => onValueChange(item.id)}
              className={cn(
                "-mb-px border-b-2 px-3 py-2.5 text-sm font-medium whitespace-nowrap transition-colors disabled:opacity-50",
                selected ? "border-accent text-fg" : "border-transparent text-fg-muted hover:border-line-strong hover:text-fg",
              )}
            >
              {item.label}
            </button>
          );
        })}
      </div>
      {items.map((item) =>
        item.id === value ? (
          <div
            key={item.id}
            role="tabpanel"
            id={`${baseId}-panel-${item.id}`}
            aria-labelledby={`${baseId}-tab-${item.id}`}
            tabIndex={0}
            className="pt-6 focus-visible:outline-offset-4"
          >
            {item.content}
          </div>
        ) : null,
      )}
    </div>
  );
}

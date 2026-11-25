import { Check } from "lucide-react";
import { useId, useRef, useState, type KeyboardEvent, type ReactNode, type RefObject } from "react";
import { Link } from "react-router";

import { useDismiss } from "@/components/ui/useOverlay";
import { cn } from "@/utils/cn";

export type DropdownMenuItem =
  | {
      type?: "action";
      id: string;
      label: ReactNode;
      icon?: ReactNode;
      onSelect: () => void;
      tone?: "default" | "danger";
      disabled?: boolean;
      /** Makes the item a radio choice (role="menuitemradio") showing whether it is selected. */
      checked?: boolean;
    }
  | { type: "link"; id: string; label: ReactNode; icon?: ReactNode; to: string }
  | { type: "separator"; id: string }
  | { type: "label"; id: string; label: ReactNode };

export interface DropdownMenuProps {
  /** Renders the trigger button; spread the given props onto a <button>. */
  trigger: (props: {
    ref: RefObject<HTMLButtonElement | null>;
    id: string;
    "aria-haspopup": "menu";
    "aria-expanded": boolean;
    "aria-controls": string | undefined;
    onClick: () => void;
    onKeyDown: (event: KeyboardEvent<HTMLButtonElement>) => void;
  }) => ReactNode;
  items: DropdownMenuItem[];
  align?: "start" | "end";
  /** Open upwards (for triggers at the bottom of the screen). */
  side?: "bottom" | "top";
  label?: string;
  className?: string;
}

/**
 * Menu button (WAI-ARIA menu pattern): Enter/Space/ArrowDown open it on the first
 * item, arrows/Home/End move, Escape or a click outside closes and refocuses the trigger.
 */
export function DropdownMenu({ trigger, items, align = "end", side = "bottom", label, className }: DropdownMenuProps) {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const triggerId = useId();
  const menuId = useId();

  const menuItems = () => Array.from(menuRef.current?.querySelectorAll<HTMLElement>('[role^="menuitem"]:not([aria-disabled="true"])') ?? []);
  const focusItem = (index: number) => {
    const all = menuItems();
    if (all.length === 0) return;
    all[(index + all.length) % all.length]!.focus();
  };
  const close = (refocus = true) => {
    setOpen(false);
    if (refocus) triggerRef.current?.focus();
  };
  const openAt = (index: number) => {
    setOpen(true);
    requestAnimationFrame(() => focusItem(index));
  };

  useDismiss(open, [triggerRef, menuRef], () => close(false));

  const onMenuKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const all = menuItems();
    const index = all.indexOf(document.activeElement as HTMLElement);
    if (event.key === "ArrowDown") focusItem(index + 1);
    else if (event.key === "ArrowUp") focusItem(index - 1);
    else if (event.key === "Home") focusItem(0);
    else if (event.key === "End") focusItem(all.length - 1);
    else if (event.key === "Escape") close();
    else if (event.key === "Tab") close(false);
    else return;
    if (event.key !== "Tab") event.preventDefault();
  };

  const itemClass = (tone: "default" | "danger" = "default") =>
    cn(
      "flex w-full items-center gap-2.5 rounded-sm px-2.5 py-2 text-left text-sm outline-none",
      "focus-visible:outline-none aria-disabled:opacity-50",
      tone === "danger" ? "text-danger-text hover:bg-danger-subtle focus:bg-danger-subtle" : "text-fg-secondary hover:bg-surface-muted hover:text-fg focus:bg-surface-muted focus:text-fg",
    );

  return (
    <div className={cn("relative", className)}>
      {trigger({
        ref: triggerRef,
        id: triggerId,
        "aria-haspopup": "menu",
        "aria-expanded": open,
        "aria-controls": open ? menuId : undefined,
        onClick: () => (open ? close() : openAt(0)),
        onKeyDown: (event) => {
          if (event.key === "ArrowDown") {
            event.preventDefault();
            openAt(0);
          } else if (event.key === "ArrowUp") {
            event.preventDefault();
            openAt(-1);
          }
        },
      })}
      {open && (
        <div
          ref={menuRef}
          id={menuId}
          role="menu"
          aria-label={label}
          aria-labelledby={label ? undefined : triggerId}
          tabIndex={-1}
          onKeyDown={onMenuKeyDown}
          className={cn(
            "absolute z-(--z-popover) min-w-52 animate-scale-in rounded-card border border-line bg-surface p-1 shadow-popover",
            align === "end" ? "right-0" : "left-0",
            side === "bottom" ? "top-full mt-1.5" : "bottom-full mb-1.5",
          )}
        >
          {items.map((item) => {
            if (item.type === "separator") return <div key={item.id} role="separator" className="my-1 h-px bg-line-subtle" />;
            if (item.type === "label")
              return (
                <div key={item.id} role="presentation" className="px-2.5 py-2">
                  {item.label}
                </div>
              );
            if (item.type === "link")
              return (
                <Link key={item.id} role="menuitem" tabIndex={-1} to={item.to} className={itemClass()} onClick={() => close(false)}>
                  {item.icon}
                  {item.label}
                </Link>
              );
            return (
              <button
                key={item.id}
                type="button"
                role={item.checked === undefined ? "menuitem" : "menuitemradio"}
                aria-checked={item.checked}
                tabIndex={-1}
                aria-disabled={item.disabled || undefined}
                className={itemClass(item.tone)}
                onClick={() => {
                  if (item.disabled) return;
                  close();
                  item.onSelect();
                }}
              >
                {item.icon}
                <span className="flex-1">{item.label}</span>
                {item.checked && <Check aria-hidden="true" className="h-4 w-4 text-accent-text" />}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

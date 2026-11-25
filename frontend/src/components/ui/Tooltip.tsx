import { cloneElement, useEffect, useId, useRef, useState, type ReactElement, type ReactNode } from "react";

import { durations } from "@/design/tokens";
import { cn } from "@/utils/cn";

export interface TooltipProps {
  content: ReactNode;
  /** One focusable element; it is described by the tooltip. */
  children: ReactElement<{ "aria-describedby"?: string }>;
  side?: "top" | "right" | "bottom";
  /** Render nothing extra (for example when the label is already visible). */
  disabled?: boolean;
  className?: string;
}

const SIDES = {
  top: "bottom-full left-1/2 mb-2 -translate-x-1/2",
  right: "left-full top-1/2 ml-2 -translate-y-1/2",
  bottom: "top-full left-1/2 mt-2 -translate-x-1/2",
};

/**
 * Short supplementary text shown on hover and keyboard focus (WCAG 1.4.13:
 * dismissible with Escape, stays while hovered). Never put essential information
 * only in a tooltip; give icon-only buttons an aria-label as well.
 */
export function Tooltip({ content, children, side = "top", disabled = false, className }: TooltipProps) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);

  useEffect(() => () => clearTimeout(timer.current), []);
  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open]);

  if (disabled) return children;

  const show = (delay: number) => {
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setOpen(true), delay);
  };
  const hide = () => {
    clearTimeout(timer.current);
    setOpen(false);
  };

  return (
    <span
      className={cn("relative inline-flex", className)}
      onMouseEnter={() => show(durations.tooltipDelay)}
      onMouseLeave={hide}
      onFocus={() => show(0)}
      onBlur={hide}
    >
      {cloneElement(children, { "aria-describedby": open ? id : children.props["aria-describedby"] })}
      {open && (
        <span
          id={id}
          role="tooltip"
          className={cn(
            "pointer-events-none absolute z-(--z-tooltip) w-max max-w-64 animate-fade-in rounded-sm bg-inverse px-2 py-1 text-xs font-medium text-inverse-fg shadow-popover",
            SIDES[side],
          )}
        >
          {content}
        </span>
      )}
    </span>
  );
}

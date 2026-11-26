import { X } from "lucide-react";
import { useId, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";

import { useModalOverlay } from "@/components/ui/useOverlay";
import { cn } from "@/utils/cn";

export interface DrawerProps {
  open: boolean;
  onClose: () => void;
  /** Accessible name of the drawer. */
  title: string;
  /** Show the title as a visible header (otherwise it is only announced). */
  showTitle?: boolean;
  side?: "left" | "right";
  children: ReactNode;
  className?: string;
}

/** A modal panel that slides in from the edge; used for mobile navigation. */
export function Drawer({ open, onClose, title, showTitle = false, side = "left", children, className }: DrawerProps) {
  const titleId = useId();
  const panelRef = useRef<HTMLDivElement>(null);
  useModalOverlay(open, panelRef, onClose);
  if (!open) return null;
  return createPortal(
    <div className="fixed inset-0 z-(--z-drawer)">
      <div aria-hidden="true" className="absolute inset-0 animate-fade-in bg-overlay" onClick={onClose} />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className={cn(
          "absolute inset-y-0 flex w-[min(20rem,86vw)] flex-col border-line bg-surface shadow-overlay outline-none",
          side === "left" ? "left-0 animate-slide-in-left border-r" : "right-0 border-l",
          className,
        )}
      >
        <div className={cn("flex items-center justify-between gap-3 px-4 pt-4", !showTitle && "absolute top-0 right-0 z-10")}>
          <h2 id={titleId} className={cn("text-section-title text-fg", !showTitle && "sr-only")}>
            {title}
          </h2>
          <button
            type="button"
            data-focus-skip
            onClick={onClose}
            aria-label="Close navigation"
            className="grid h-8 w-8 place-items-center rounded-control text-fg-subtle hover:bg-surface-muted hover:text-fg"
          >
            <X aria-hidden="true" className="h-4 w-4" />
          </button>
        </div>
        <div className="flex min-h-0 flex-1 flex-col">{children}</div>
      </div>
    </div>,
    document.body,
  );
}

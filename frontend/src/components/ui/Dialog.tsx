import { X } from "lucide-react";
import { useId, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";

import { Button } from "@/components/ui/Button";
import { useModalOverlay } from "@/components/ui/useOverlay";
import { cn } from "@/utils/cn";

type DialogSize = "sm" | "md" | "lg";
const SIZES: Record<DialogSize, string> = { sm: "sm:max-w-md", md: "sm:max-w-lg", lg: "sm:max-w-2xl" };

export interface DialogProps {
  open: boolean;
  title: string;
  description?: ReactNode;
  onClose: () => void;
  children?: ReactNode;
  /** Buttons pinned to the bottom of the dialog. */
  footer?: ReactNode;
  role?: "dialog" | "alertdialog";
  size?: DialogSize;
}

/**
 * Accessible modal (also used as "Modal"): focus moves in and is trapped,
 * Escape and the close button close it, page scroll is locked and focus returns
 * to the opener. On small screens it becomes a bottom sheet that scrolls.
 */
export function Dialog({ open, title, description, onClose, children, footer, role = "dialog", size = "md" }: DialogProps) {
  const titleId = useId();
  const descriptionId = useId();
  const panelRef = useRef<HTMLDivElement>(null);
  useModalOverlay(open, panelRef, onClose);

  if (!open) return null;
  return createPortal(
    <div className="fixed inset-0 z-(--z-modal) flex items-end justify-center sm:items-center sm:p-4">
      <div aria-hidden="true" className="absolute inset-0 animate-fade-in bg-overlay" onClick={onClose} />
      <div
        ref={panelRef}
        role={role}
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description ? descriptionId : undefined}
        tabIndex={-1}
        className={cn(
          "relative flex max-h-[92dvh] w-full animate-scale-in flex-col rounded-t-overlay border border-line bg-surface shadow-overlay outline-none sm:rounded-overlay",
          SIZES[size],
        )}
      >
        <div className="flex items-start justify-between gap-4 px-5 pt-5 sm:px-6">
          <h2 id={titleId} className="text-section-title text-fg">
            {title}
          </h2>
          <button
            type="button"
            data-focus-skip
            onClick={onClose}
            aria-label="Close"
            className="-mt-1 -mr-1 grid h-8 w-8 shrink-0 place-items-center rounded-control text-fg-subtle hover:bg-surface-muted hover:text-fg"
          >
            <X aria-hidden="true" className="h-4 w-4" />
          </button>
        </div>
        <div className="min-h-0 overflow-y-auto px-5 pb-5 sm:px-6 sm:pb-6">
          {description && (
            <div id={descriptionId} className="mt-1.5 text-sm text-fg-muted">
              {description}
            </div>
          )}
          {children && <div className="mt-5">{children}</div>}
        </div>
        {footer && (
          <div className="flex flex-col-reverse gap-2 border-t border-line-subtle px-5 py-4 sm:flex-row sm:justify-end sm:px-6">{footer}</div>
        )}
      </div>
    </div>,
    document.body,
  );
}

/** Alias: the spec's "Modal" is the same component. */
export const Modal = Dialog;

/** Footer row for forms inside a dialog: stacked full-width on phones, right-aligned otherwise. */
export function DialogActions({ children }: { children: ReactNode }) {
  return <div className="flex flex-col-reverse gap-2 pt-2 sm:flex-row sm:justify-end">{children}</div>;
}

export interface ConfirmDialogProps {
  open: boolean;
  title: string;
  description: ReactNode;
  confirmLabel: string;
  cancelLabel?: string;
  tone?: "danger" | "primary";
  pending?: boolean;
  error?: string | null;
  onConfirm: () => void;
  onCancel: () => void;
}

/** Confirmation for destructive or security-sensitive actions. Focus starts on Cancel. */
export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel,
  cancelLabel = "Cancel",
  tone = "danger",
  pending = false,
  error,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  return (
    <Dialog
      open={open}
      title={title}
      description={description}
      onClose={pending ? () => undefined : onCancel}
      role="alertdialog"
      size="sm"
    >
      {error && (
        <p role="alert" className="mb-4 rounded-control border border-danger-line bg-danger-subtle px-3 py-2 text-sm font-medium text-danger-text">
          {error}
        </p>
      )}
      <DialogActions>
        <Button variant="secondary" onClick={onCancel} disabled={pending} data-autofocus>
          {cancelLabel}
        </Button>
        <Button variant={tone === "danger" ? "danger" : "primary"} onClick={onConfirm} loading={pending}>
          {confirmLabel}
        </Button>
      </DialogActions>
    </Dialog>
  );
}

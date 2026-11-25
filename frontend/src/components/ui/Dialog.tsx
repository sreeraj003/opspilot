import { X } from "lucide-react";
import { useEffect, useId, useRef, type ReactNode } from "react";

import { Button } from "@/components/ui/Button";

interface DialogProps {
  open: boolean;
  title: string;
  description?: ReactNode;
  onClose: () => void;
  children?: ReactNode;
  role?: "dialog" | "alertdialog";
}

/** Accessible modal: focus moves in, Escape closes, focus returns to the opener. */
export function Dialog({ open, title, description, onClose, children, role = "dialog" }: DialogProps) {
  const titleId = useId();
  const descriptionId = useId();
  const panelRef = useRef<HTMLDivElement>(null);
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  });

  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    const focusable = panelRef.current?.querySelector<HTMLElement>(
      "input, select, textarea, button:not([data-dialog-close])",
    );
    (focusable ?? panelRef.current)?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onCloseRef.current();
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      previous?.focus?.();
    };
  }, [open]);

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-stone-900/40 p-4 sm:items-center">
      <div
        ref={panelRef}
        role={role}
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description ? descriptionId : undefined}
        tabIndex={-1}
        className="w-full max-w-lg rounded-2xl border border-stone-200 bg-white p-6 shadow-xl outline-none"
      >
        <div className="flex items-start justify-between gap-4">
          <h2 id={titleId} className="text-base font-semibold text-stone-900">
            {title}
          </h2>
          <button
            type="button"
            data-dialog-close
            onClick={onClose}
            aria-label="Close"
            className="rounded-md p-1 text-stone-400 hover:bg-stone-100 hover:text-stone-700"
          >
            <X aria-hidden="true" className="h-4 w-4" />
          </button>
        </div>
        {description && (
          <div id={descriptionId} className="mt-2 text-sm text-stone-600">
            {description}
          </div>
        )}
        {children && <div className="mt-5">{children}</div>}
      </div>
    </div>
  );
}

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  description: ReactNode;
  confirmLabel: string;
  tone?: "danger" | "primary";
  pending?: boolean;
  error?: string | null;
  onConfirm: () => void;
  onCancel: () => void;
}

/** Confirmation for destructive or security-sensitive actions. */
export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel,
  tone = "danger",
  pending = false,
  error,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  return (
    <Dialog open={open} title={title} description={description} onClose={onCancel} role="alertdialog">
      {error && (
        <p role="alert" className="mb-4 text-sm font-medium text-rose-600">
          {error}
        </p>
      )}
      <div className="flex justify-end gap-2">
        <Button variant="secondary" onClick={onCancel} disabled={pending}>
          Cancel
        </Button>
        <Button variant={tone === "danger" ? "danger" : "primary"} onClick={onConfirm} loading={pending}>
          {confirmLabel}
        </Button>
      </div>
    </Dialog>
  );
}

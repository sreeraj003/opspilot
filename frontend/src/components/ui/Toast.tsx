import { AlertCircle, CheckCircle2, Info, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { ToastContext, type ToastContextValue, type ToastOptions, type ToastTone } from "@/components/ui/toastContext";
import { durations } from "@/design/tokens";
import { cn } from "@/utils/cn";

interface ToastItem extends Required<Omit<ToastOptions, "description">> {
  id: string;
  description?: string;
}

const ICONS: Record<ToastTone, { icon: typeof Info; className: string }> = {
  success: { icon: CheckCircle2, className: "text-success-text" },
  error: { icon: AlertCircle, className: "text-danger-text" },
  info: { icon: Info, className: "text-info-text" },
};

const MAX_TOASTS = 4;
let counter = 0;

function ToastView({ toast, onDismiss }: { toast: ToastItem; onDismiss: (id: string) => void }) {
  const { icon: Icon, className } = ICONS[toast.tone];
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  const start = useCallback(() => {
    if (toast.durationMs > 0) timer.current = setTimeout(() => onDismiss(toast.id), toast.durationMs);
  }, [toast, onDismiss]);
  useEffect(() => {
    start();
    return () => clearTimeout(timer.current);
  }, [start]);
  return (
    <li
      // Pausing on hover/focus gives people time to read (WCAG 2.2.1).
      onMouseEnter={() => clearTimeout(timer.current)}
      onMouseLeave={start}
      className="pointer-events-auto flex w-full animate-slide-in-up items-start gap-3 rounded-card border border-line bg-surface p-3.5 shadow-popover"
    >
      <Icon aria-hidden="true" className={cn("mt-0.5 h-4 w-4 shrink-0", className)} />
      <div className="min-w-0 flex-1 text-sm">
        <p className="font-medium text-fg">{toast.title}</p>
        {toast.description && <p className="mt-0.5 text-fg-muted">{toast.description}</p>}
      </div>
      <button
        type="button"
        onClick={() => onDismiss(toast.id)}
        aria-label="Dismiss notification"
        className="-m-1 grid h-7 w-7 shrink-0 place-items-center rounded-sm text-fg-subtle hover:bg-surface-muted hover:text-fg"
      >
        <X aria-hidden="true" className="h-3.5 w-3.5" />
      </button>
    </li>
  );
}

/** Hosts toasts in two live regions: errors are assertive, everything else polite. */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const dismiss = useCallback((id: string) => setToasts((current) => current.filter((toast) => toast.id !== id)), []);
  const notify = useCallback((options: ToastOptions) => {
    counter += 1;
    const id = `toast-${counter}`;
    const tone = options.tone ?? "success";
    const item: ToastItem = {
      id,
      tone,
      title: options.title,
      description: options.description,
      durationMs: options.durationMs ?? (tone === "error" ? 0 : durations.toast),
    };
    setToasts((current) => [...current, item].slice(-MAX_TOASTS));
    return id;
  }, []);
  const value = useMemo<ToastContextValue>(() => ({ notify, dismiss }), [notify, dismiss]);
  const polite = toasts.filter((toast) => toast.tone !== "error");
  const assertive = toasts.filter((toast) => toast.tone === "error");

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="pointer-events-none fixed inset-x-0 bottom-0 z-(--z-toast) flex flex-col items-center gap-2 p-4 sm:items-end">
        <ol aria-live="assertive" aria-label="Error notifications" className="flex w-full max-w-sm flex-col gap-2">
          {assertive.map((toast) => (
            <ToastView key={toast.id} toast={toast} onDismiss={dismiss} />
          ))}
        </ol>
        <ol aria-live="polite" aria-label="Notifications" className="flex w-full max-w-sm flex-col gap-2">
          {polite.map((toast) => (
            <ToastView key={toast.id} toast={toast} onDismiss={dismiss} />
          ))}
        </ol>
      </div>
    </ToastContext.Provider>
  );
}

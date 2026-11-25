import { createContext, useContext } from "react";

export type ToastTone = "success" | "error" | "info";

export interface ToastOptions {
  tone?: ToastTone;
  title: string;
  description?: string;
  /** Milliseconds before it disappears; errors stay until dismissed when 0. */
  durationMs?: number;
}

export interface ToastContextValue {
  notify: (options: ToastOptions) => string;
  dismiss: (id: string) => void;
}

export const ToastContext = createContext<ToastContextValue | null>(null);

/** Show short, non-blocking confirmations ("User activated"). Use an Alert for messages that must stay in place. */
export function useToast(): ToastContextValue {
  const value = useContext(ToastContext);
  if (!value) throw new Error("useToast must be used inside <ToastProvider>");
  return value;
}

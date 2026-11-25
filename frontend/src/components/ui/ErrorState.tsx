import { AlertTriangle, RotateCw } from "lucide-react";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/Button";
import { cn } from "@/utils/cn";

export interface ErrorStateProps {
  title?: string;
  /** What went wrong, in words the user can act on. */
  message: ReactNode;
  onRetry?: () => void;
  retrying?: boolean;
  className?: string;
}

/** A failed load, announced as an alert, with an optional retry. */
export function ErrorState({ title = "Something went wrong", message, onRetry, retrying = false, className }: ErrorStateProps) {
  return (
    <div role="alert" className={cn("flex flex-col items-center px-6 py-12 text-center", className)}>
      <span className="grid h-11 w-11 place-items-center rounded-card bg-danger-subtle text-danger-text">
        <AlertTriangle aria-hidden="true" className="h-5 w-5" strokeWidth={1.75} />
      </span>
      <p className="mt-3 text-sm font-medium text-fg">{title}</p>
      <p className="mt-1 max-w-md text-sm text-fg-muted">{message}</p>
      {onRetry && (
        <Button variant="secondary" size="sm" className="mt-4" onClick={onRetry} loading={retrying}>
          {!retrying && <RotateCw aria-hidden="true" className="h-3.5 w-3.5" />}
          Try again
        </Button>
      )}
    </div>
  );
}

import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/utils/cn";

export interface EmptyStateProps {
  title: string;
  description?: ReactNode;
  icon?: LucideIcon;
  action?: ReactNode;
  className?: string;
}

/** Shown when a list or section has nothing to display. */
export function EmptyState({ title, description, icon: Icon, action, className }: EmptyStateProps) {
  return (
    <div className={cn("flex flex-col items-center px-6 py-12 text-center", className)}>
      {Icon && (
        <span className="grid h-11 w-11 place-items-center rounded-card bg-surface-muted text-fg-subtle">
          <Icon aria-hidden="true" className="h-5 w-5" strokeWidth={1.75} />
        </span>
      )}
      <p className={cn("text-sm font-medium text-fg", Icon && "mt-3")}>{title}</p>
      {description && <p className="mt-1 max-w-sm text-sm text-fg-muted">{description}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

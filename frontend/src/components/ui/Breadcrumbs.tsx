import { ChevronRight } from "lucide-react";
import { Link } from "react-router";

import { cn } from "@/utils/cn";

export interface BreadcrumbItem {
  label: string;
  /** Omit for the current page or for a grouping without its own page. */
  to?: string;
}

/** Trail of the current location. The last item is the current page (aria-current). */
export function Breadcrumbs({ items, className }: { items: BreadcrumbItem[]; className?: string }) {
  if (items.length === 0) return null;
  return (
    <nav aria-label="Breadcrumb" className={cn("min-w-0", className)}>
      <ol className="flex min-w-0 items-center gap-1.5 text-sm">
        {items.map((item, index) => {
          const last = index === items.length - 1;
          return (
            <li key={`${item.label}-${index}`} className={cn("flex min-w-0 items-center gap-1.5", !last && "shrink-0")}>
              {index > 0 && <ChevronRight aria-hidden="true" className="h-3.5 w-3.5 shrink-0 text-fg-subtle" />}
              {last ? (
                <span aria-current="page" className="truncate font-medium text-fg">
                  {item.label}
                </span>
              ) : item.to ? (
                <Link to={item.to} className="truncate rounded-xs text-fg-muted hover:text-fg">
                  {item.label}
                </Link>
              ) : (
                <span className="truncate text-fg-muted">{item.label}</span>
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

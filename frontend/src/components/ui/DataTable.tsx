import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { EmptyState } from "@/components/ui/EmptyState";
import { ErrorState } from "@/components/ui/ErrorState";
import { Skeleton } from "@/components/ui/Skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeaderCell, TableRow } from "@/components/ui/Table";
import { cn } from "@/utils/cn";

export interface DataTableColumn<T> {
  id: string;
  header: ReactNode;
  cell: (row: T) => ReactNode;
  align?: "left" | "right";
  /** Hide on narrow screens; the column's data must also be reachable elsewhere (the detail page). */
  hideBelow?: "sm" | "md" | "lg";
  className?: string;
}

const HIDE: Record<NonNullable<DataTableColumn<unknown>["hideBelow"]>, string> = {
  sm: "hidden sm:table-cell",
  md: "hidden md:table-cell",
  lg: "hidden lg:table-cell",
};

export interface DataTableProps<T> {
  columns: DataTableColumn<T>[];
  rows: T[] | undefined;
  getRowId: (row: T) => string;
  /** Accessible description of the table (visually hidden). */
  caption: string;
  loading?: boolean;
  loadingLabel?: string;
  /** A load error message; shown with a retry button when ``onRetry`` is given. */
  error?: string | null;
  onRetry?: () => void;
  retrying?: boolean;
  empty: { title: string; description?: ReactNode; icon?: LucideIcon; action?: ReactNode };
  /** Shown under the table (pagination). */
  footer?: ReactNode;
}

/**
 * A list of records in a card: loading skeleton, error with retry, empty state,
 * responsive columns, and horizontal scrolling inside the card (never the page).
 */
export function DataTable<T>({
  columns,
  rows,
  getRowId,
  caption,
  loading = false,
  loadingLabel = "Loading",
  error,
  onRetry,
  retrying,
  empty,
  footer,
}: DataTableProps<T>) {
  let body: ReactNode;
  if (loading && !rows) {
    body = (
      <div role="status" aria-label={loadingLabel} className="divide-y divide-line-subtle">
        <span className="sr-only">{loadingLabel}…</span>
        {Array.from({ length: 5 }, (_, index) => (
          <div key={index} className="flex items-center gap-4 px-4 py-4">
            <Skeleton className="h-4 w-1/4" />
            <Skeleton className="h-4 w-1/3" />
            <Skeleton className="ml-auto h-4 w-16" />
          </div>
        ))}
      </div>
    );
  } else if (error) {
    body = <ErrorState message={error} onRetry={onRetry} retrying={retrying} />;
  } else if (!rows || rows.length === 0) {
    body = <EmptyState {...empty} />;
  } else {
    body = (
      // "relative" contains sr-only text (absolutely positioned) so it cannot widen the page.
      <div className="relative overflow-x-auto">
        <Table>
          <caption className="sr-only">{caption}</caption>
          <TableHead>
            <tr>
              {columns.map((column) => (
                <TableHeaderCell key={column.id} className={cn(column.align === "right" && "text-right", column.hideBelow && HIDE[column.hideBelow])}>
                  {column.header}
                </TableHeaderCell>
              ))}
            </tr>
          </TableHead>
          <TableBody>
            {rows.map((row) => (
              <TableRow key={getRowId(row)}>
                {columns.map((column) => (
                  <TableCell
                    key={column.id}
                    className={cn(column.align === "right" && "text-right", column.hideBelow && HIDE[column.hideBelow], column.className)}
                  >
                    {column.cell(row)}
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    );
  }
  return (
    <div className="space-y-4">
      <div className="relative overflow-hidden rounded-card border border-line bg-surface shadow-card" aria-busy={loading || undefined}>
        {body}
      </div>
      {footer}
    </div>
  );
}

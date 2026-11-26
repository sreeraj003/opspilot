import { ChevronLeft, ChevronRight } from "lucide-react";

import { Button } from "@/components/ui/Button";

export interface PaginationProps {
  /** Zero-based page index. */
  page: number;
  pageSize: number;
  total: number;
  onPage: (page: number) => void;
  /** What is being counted, for the screen-reader label ("users"). */
  itemLabel?: string;
}

/** Previous/next pagination with a range summary. Hidden when there is nothing to page. */
export function Pagination({ page, pageSize, total, onPage, itemLabel }: PaginationProps) {
  if (total === 0) return null;
  const lastPage = Math.max(0, Math.ceil(total / pageSize) - 1);
  return (
    <nav aria-label={itemLabel ? `${itemLabel} pagination` : "Pagination"} className="flex flex-wrap items-center justify-between gap-3 text-sm text-fg-muted">
      <p aria-live="polite">
        Showing {page * pageSize + 1}–{Math.min(total, (page + 1) * pageSize)} of {total}
      </p>
      <div className="flex gap-2">
        <Button variant="secondary" size="sm" disabled={page === 0} onClick={() => onPage(page - 1)}>
          <ChevronLeft aria-hidden="true" className="h-4 w-4" />
          Previous
        </Button>
        <Button variant="secondary" size="sm" disabled={page >= lastPage} onClick={() => onPage(page + 1)}>
          Next
          <ChevronRight aria-hidden="true" className="h-4 w-4" />
        </Button>
      </div>
    </nav>
  );
}

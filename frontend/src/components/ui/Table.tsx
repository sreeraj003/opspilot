import type { ComponentPropsWithoutRef } from "react";

import { cn } from "@/utils/cn";

/**
 * Table primitives with the design-system look. Wrap in a scroll container
 * (DataTable does) so wide tables never overflow the page.
 */
export function Table({ className, ...props }: ComponentPropsWithoutRef<"table">) {
  return <table className={cn("min-w-full border-collapse text-sm", className)} {...props} />;
}

export function TableHead({ className, ...props }: ComponentPropsWithoutRef<"thead">) {
  return <thead className={cn("border-b border-line bg-surface-muted text-left text-xs font-medium text-fg-muted", className)} {...props} />;
}

export function TableBody({ className, ...props }: ComponentPropsWithoutRef<"tbody">) {
  return <tbody className={cn("divide-y divide-line-subtle", className)} {...props} />;
}

export function TableRow({ className, ...props }: ComponentPropsWithoutRef<"tr">) {
  return <tr className={cn("transition-colors hover:bg-surface-muted/60", className)} {...props} />;
}

export function TableHeaderCell({ className, scope = "col", ...props }: ComponentPropsWithoutRef<"th">) {
  return <th scope={scope} className={cn("px-4 py-2.5 font-medium whitespace-nowrap", className)} {...props} />;
}

export function TableCell({ className, ...props }: ComponentPropsWithoutRef<"td">) {
  return <td className={cn("px-4 py-3 align-middle text-fg-secondary", className)} {...props} />;
}

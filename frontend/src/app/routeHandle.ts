import { useMatches, type Params, type UIMatch } from "react-router";

import type { BreadcrumbItem } from "@/components/ui/Breadcrumbs";

/**
 * Optional metadata on a route (``handle``). ``crumb`` adds a breadcrumb for the
 * route; ``link: false`` shows it without a link (a grouping with no page).
 */
export interface RouteHandle {
  crumb?: string | ((params: Params) => string);
  link?: boolean;
}

/** Breadcrumbs for the current location, from the ``handle.crumb`` of each matched route. */
export function useBreadcrumbs(): BreadcrumbItem[] {
  const matches = useMatches() as UIMatch<unknown, RouteHandle | undefined>[];
  const crumbs: BreadcrumbItem[] = [];
  for (const match of matches) {
    const crumb = match.handle?.crumb;
    if (!crumb) continue;
    const label = typeof crumb === "function" ? crumb(match.params) : crumb;
    crumbs.push({ label, to: match.handle?.link === false ? undefined : match.pathname });
  }
  return crumbs;
}

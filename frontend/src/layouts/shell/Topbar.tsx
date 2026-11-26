import { Menu } from "lucide-react";

import { useBreadcrumbs } from "@/app/routeHandle";
import { Breadcrumbs } from "@/components/ui/Breadcrumbs";
import { ThemeToggle } from "@/features/theme/ThemeToggle";
import { UserMenu } from "@/layouts/shell/UserMenu";

export interface TopbarProps {
  onOpenNavigation: () => void;
  navigationOpen: boolean;
  onSignOut: () => void;
}

/**
 * Sticky top bar: mobile menu trigger, page title (small screens) or breadcrumbs,
 * theme picker and account menu. There is no notification area: nothing in the
 * product produces notifications yet.
 */
export function Topbar({ onOpenNavigation, navigationOpen, onSignOut }: TopbarProps) {
  const crumbs = useBreadcrumbs();
  const title = crumbs[crumbs.length - 1]?.label ?? "OpsPilot AI";
  return (
    <header className="sticky top-0 z-(--z-sticky) flex h-topbar shrink-0 items-center gap-3 border-b border-line bg-surface/85 px-gutter backdrop-blur-md lg:px-gutter-lg">
      <button
        type="button"
        onClick={onOpenNavigation}
        aria-label="Open navigation"
        aria-expanded={navigationOpen}
        aria-haspopup="dialog"
        className="-ml-1.5 grid h-9 w-9 shrink-0 place-items-center rounded-control text-fg-secondary hover:bg-surface-muted lg:hidden"
      >
        <Menu aria-hidden="true" className="h-5 w-5" />
      </button>
      <p className="min-w-0 truncate text-sm font-semibold text-fg md:hidden">{title}</p>
      <Breadcrumbs items={crumbs} className="hidden md:block" />
      <div className="ml-auto flex items-center gap-1">
        <ThemeToggle />
        <UserMenu onSignOut={onSignOut} />
      </div>
    </header>
  );
}

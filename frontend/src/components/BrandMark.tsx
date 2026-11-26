import { Sparkles } from "lucide-react";
import { Link } from "react-router";

import { cn } from "@/utils/cn";

/** OpsPilot AI logo and name. ``compact`` shows the mark only (collapsed sidebar). */
export function BrandMark({ compact = false, className }: { compact?: boolean; className?: string }) {
  return (
    <Link to="/" aria-label="OpsPilot AI home" className={cn("flex min-w-0 items-center gap-2.5 rounded-control", className)}>
      <span aria-hidden="true" className="grid h-8 w-8 shrink-0 place-items-center rounded-control bg-accent text-accent-fg shadow-xs">
        <Sparkles className="h-4 w-4" strokeWidth={2} />
      </span>
      {!compact && <span className="truncate text-[15px] font-semibold tracking-tight text-fg">OpsPilot AI</span>}
    </Link>
  );
}

import { Loader2 } from "lucide-react";

import { cn } from "@/utils/cn";

const SIZES = { sm: "h-3.5 w-3.5", md: "h-4 w-4", lg: "h-6 w-6" };

/** The spinning icon alone (decorative); pair it with visible or sr-only text. */
export function LoadingSpinner({ size = "md", className }: { size?: keyof typeof SIZES; className?: string }) {
  return <Loader2 aria-hidden="true" className={cn("animate-spin text-fg-subtle", SIZES[size], className)} />;
}

/** A labelled loading state for a region, announced politely. */
export function Spinner({ label = "Loading", className }: { label?: string; className?: string }) {
  return (
    <div role="status" className={cn("flex items-center justify-center gap-2 py-10 text-sm text-fg-muted", className)}>
      <LoadingSpinner />
      {label}…
    </div>
  );
}

/** Whole-screen loading state (session checks, first load). */
export function FullPageSpinner({ label = "Loading" }: { label?: string }) {
  return (
    <div className="grid min-h-full place-items-center bg-canvas">
      <Spinner label={label} />
    </div>
  );
}

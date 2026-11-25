import { cn } from "@/utils/cn";

/** A placeholder block while content loads. Decorative: announce loading with a Spinner or sr-only text. */
export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden="true" className={cn("animate-pulse rounded-sm bg-surface-strong", className)} />;
}

export function SkeletonText({ lines = 3, className }: { lines?: number; className?: string }) {
  return (
    <div aria-hidden="true" className={cn("space-y-2", className)}>
      {Array.from({ length: lines }, (_, index) => (
        <Skeleton key={index} className={cn("h-3.5", index === lines - 1 ? "w-3/5" : "w-full")} />
      ))}
    </div>
  );
}

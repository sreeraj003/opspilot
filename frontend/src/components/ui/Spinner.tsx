import { Loader2 } from "lucide-react";

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <div role="status" className="flex items-center justify-center gap-2 py-10 text-sm text-stone-500">
      <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" />
      {label}…
    </div>
  );
}

export function FullPageSpinner({ label = "Loading" }: { label?: string }) {
  return (
    <div className="grid min-h-full place-items-center">
      <Spinner label={label} />
    </div>
  );
}

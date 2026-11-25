import { Sparkles } from "lucide-react";
import { Link } from "react-router";

export function BrandMark() {
  return (
    <Link
      to="/"
      aria-label="OpsPilot AI home"
      className="flex items-center gap-2.5 rounded-lg focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-stone-900"
    >
      <span aria-hidden="true" className="grid h-8 w-8 place-items-center rounded-lg bg-stone-900 text-white">
        <Sparkles className="h-4 w-4" strokeWidth={2} />
      </span>
      <span className="text-[15px] font-semibold tracking-tight text-stone-900">OpsPilot AI</span>
    </Link>
  );
}

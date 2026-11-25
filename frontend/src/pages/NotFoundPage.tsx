import { ArrowLeft, Compass } from "lucide-react";
import { Link } from "react-router";

import { useDocumentTitle } from "@/hooks/useDocumentTitle";

export function NotFoundPage() {
  useDocumentTitle("Page not found");
  return (
    <div className="mx-auto max-w-md py-16 text-center">
      <span className="mx-auto grid h-12 w-12 place-items-center rounded-2xl bg-stone-100 text-stone-600">
        <Compass aria-hidden="true" className="h-6 w-6" strokeWidth={1.75} />
      </span>
      <p className="mt-6 text-sm font-semibold text-stone-500">404</p>
      <h1 className="mt-2 text-2xl font-semibold tracking-tight text-stone-900">Page not found</h1>
      <p className="mt-2 text-stone-600">The page you are looking for does not exist or has moved.</p>
      <Link
        to="/"
        className="mt-8 inline-flex items-center gap-2 rounded-lg bg-stone-900 px-4 py-2 text-sm font-medium text-white transition hover:bg-stone-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-stone-900"
      >
        <ArrowLeft aria-hidden="true" className="h-4 w-4" />
        Back to home
      </Link>
    </div>
  );
}

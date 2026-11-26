import { ArrowLeft, Compass } from "lucide-react";

import { ButtonLink } from "@/components/ui/Button";
import { useDocumentTitle } from "@/hooks/useDocumentTitle";

export function NotFoundPage() {
  useDocumentTitle("Page not found");
  return (
    <div className="mx-auto max-w-md py-16 text-center">
      <span className="mx-auto grid h-12 w-12 place-items-center rounded-card bg-surface-strong text-fg-secondary">
        <Compass aria-hidden="true" className="h-6 w-6" strokeWidth={1.75} />
      </span>
      <p className="mt-6 text-sm font-semibold text-fg-muted">404</p>
      <h1 className="mt-2 text-page-title text-fg">Page not found</h1>
      <p className="mt-2 text-fg-secondary">The page you are looking for does not exist or has moved.</p>
      <ButtonLink to="/" variant="primary" className="mt-8">
        <ArrowLeft aria-hidden="true" className="h-4 w-4" />
        Back to home
      </ButtonLink>
    </div>
  );
}

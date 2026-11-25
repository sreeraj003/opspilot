import { SystemStatusPanel } from "@/components/SystemStatusPanel";
import { useAuth } from "@/features/auth/authContext";
import { PRODUCT_PRINCIPLES } from "@/features/product/principles";
import { useDocumentTitle } from "@/hooks/useDocumentTitle";

export function HomePage() {
  useDocumentTitle();
  const { user } = useAuth();
  const firstName = user?.full_name.trim().split(/\s+/)[0];
  return (
    <div className="space-y-10">
      <section className="rounded-card border border-line bg-surface p-6 shadow-card sm:p-8">
        <p className="text-sm font-medium text-accent-text">{firstName ? `Welcome, ${firstName}` : "Business Intelligence Assistant"}</p>
        <h1 className="mt-2 text-3xl font-semibold tracking-tight text-fg sm:text-4xl">OpsPilot AI</h1>
        <p className="mt-3 max-w-2xl text-base leading-relaxed text-fg-secondary">
          An AI-powered business intelligence assistant. Ask questions about your business in natural language and
          get answers grounded in your company&apos;s real data.
        </p>
        <ul className="mt-8 grid gap-5 border-t border-line-subtle pt-6 sm:grid-cols-3">
          {PRODUCT_PRINCIPLES.map(({ icon: Icon, title, text }) => (
            <li key={title} className="flex gap-3">
              <span className="grid h-8 w-8 shrink-0 place-items-center rounded-control bg-surface-muted text-fg-secondary">
                <Icon aria-hidden="true" className="h-4 w-4" strokeWidth={1.75} />
              </span>
              <div>
                <p className="text-sm font-medium text-fg">{title}</p>
                <p className="mt-0.5 text-sm text-fg-muted">{text}</p>
              </div>
            </li>
          ))}
        </ul>
      </section>

      <SystemStatusPanel />
    </div>
  );
}

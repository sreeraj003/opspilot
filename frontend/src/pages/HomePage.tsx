import { MessageSquareText, ShieldCheck, Workflow } from "lucide-react";

import { SystemStatusPanel } from "@/components/SystemStatusPanel";
import { useDocumentTitle } from "@/hooks/useDocumentTitle";

const PRINCIPLES = [
  { icon: MessageSquareText, title: "Ask in plain language", text: "Business questions answered from your own data." },
  { icon: ShieldCheck, title: "Guarded by design", text: "Every generated query passes an independent security layer." },
  { icon: Workflow, title: "Your installation", text: "Runs on your infrastructure, against your existing API." },
];

export function HomePage() {
  useDocumentTitle();
  return (
    <div className="space-y-12">
      <section className="max-w-3xl">
        <p className="text-sm font-medium tracking-wide text-stone-500">Business Intelligence Assistant</p>
        <h1 className="mt-3 text-4xl font-semibold tracking-tight text-stone-900 sm:text-5xl">OpsPilot AI</h1>
        <p className="mt-4 text-lg leading-relaxed text-stone-600">
          An AI-powered business intelligence assistant. Ask questions about your business in natural language and
          get answers grounded in your company&apos;s real data.
        </p>
        <ul className="mt-8 grid gap-4 sm:grid-cols-3">
          {PRINCIPLES.map(({ icon: Icon, title, text }) => (
            <li key={title} className="flex gap-3">
              <Icon aria-hidden="true" className="mt-0.5 h-5 w-5 shrink-0 text-stone-400" strokeWidth={1.75} />
              <div>
                <p className="text-sm font-medium text-stone-900">{title}</p>
                <p className="mt-0.5 text-sm text-stone-500">{text}</p>
              </div>
            </li>
          ))}
        </ul>
      </section>

      <SystemStatusPanel />
    </div>
  );
}

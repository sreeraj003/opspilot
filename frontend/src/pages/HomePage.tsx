import { SystemStatusPanel } from "@/features/system-status";
import { useDocumentTitle } from "@/hooks/useDocumentTitle";

export function HomePage() {
  useDocumentTitle();
  return (
    <div className="space-y-8">
      <div>
        <p className="text-sm font-medium text-indigo-600">Operations control centre</p>
        <h1 className="mt-1 text-3xl font-bold tracking-tight">OpsPilot AI</h1>
        <p className="mt-2 max-w-2xl text-slate-600">
          Enterprise operations intelligence and automation. Business intelligence, knowledge
          retrieval, incident investigation and approved workflow automation in one place.
        </p>
      </div>
      <SystemStatusPanel />
    </div>
  );
}

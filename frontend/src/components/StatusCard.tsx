import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { StatusIndicator, type StatusTone } from "@/components/ui/StatusIndicator";

interface StatusCardProps {
  title: string;
  icon: LucideIcon;
  tone: StatusTone;
  statusLabel: string;
  pending?: boolean;
  children?: ReactNode;
}

/** A titled card summarising the state of one part of the system. */
export function StatusCard({ title, icon: Icon, tone, statusLabel, pending = false, children }: StatusCardProps) {
  return (
    <section
      aria-label={title}
      className="flex flex-col rounded-card border border-line bg-surface p-5 shadow-card"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-3">
          <span className="grid h-9 w-9 place-items-center rounded-card bg-surface-strong text-fg-secondary">
            <Icon aria-hidden="true" className="h-[18px] w-[18px]" strokeWidth={1.75} />
          </span>
          <h3 className="text-sm font-semibold text-fg">{title}</h3>
        </div>
        <StatusIndicator tone={tone} label={statusLabel} pulse={pending} />
      </div>
      {children && <div className="mt-4 text-sm text-fg-secondary">{children}</div>}
    </section>
  );
}

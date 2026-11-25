import { AlertCircle, AlertTriangle, CheckCircle2, Info } from "lucide-react";
import type { ReactNode } from "react";

import { TONE_CLASSES } from "@/components/ui/Badge";
import { cn } from "@/utils/cn";

type AlertTone = "error" | "success" | "info" | "warning";

const TONES: Record<AlertTone, { tone: keyof typeof TONE_CLASSES; border: string; icon: typeof Info }> = {
  error: { tone: "danger", border: "border-danger-line", icon: AlertCircle },
  success: { tone: "success", border: "border-success-line", icon: CheckCircle2 },
  warning: { tone: "warning", border: "border-warning-line", icon: AlertTriangle },
  info: { tone: "info", border: "border-info-line", icon: Info },
};

export interface AlertProps {
  tone?: AlertTone;
  title?: ReactNode;
  children: ReactNode;
  className?: string;
}

/** Inline message. Errors use role="alert" (announced immediately); others role="status". */
export function Alert({ tone = "info", title, children, className }: AlertProps) {
  const { tone: key, border, icon: Icon } = TONES[tone];
  const classes = TONE_CLASSES[key];
  return (
    <div
      role={tone === "error" ? "alert" : "status"}
      className={cn("flex items-start gap-2.5 rounded-control border px-3.5 py-3 text-sm", classes.subtle, classes.text, border, className)}
    >
      <Icon aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0" />
      <div className="min-w-0">
        {title && <p className="font-medium">{title}</p>}
        <div className={cn(Boolean(title) && "mt-0.5 opacity-90")}>{children}</div>
      </div>
    </div>
  );
}

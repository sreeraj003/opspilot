import type { ComponentPropsWithoutRef, ElementType, ReactNode } from "react";

import { cn } from "@/utils/cn";

export interface PageHeaderProps {
  title: ReactNode;
  description?: ReactNode;
  /** Primary actions for the page, shown at the right (wraps under the title on small screens). */
  actions?: ReactNode;
  /** Small content above the title, such as a back link. */
  eyebrow?: ReactNode;
  /** Content beside the title, such as a status badge. */
  meta?: ReactNode;
}

/** The single <h1> of a page with its description and actions. */
export function PageHeader({ title, description, actions, eyebrow, meta }: PageHeaderProps) {
  return (
    <header className="space-y-3">
      {eyebrow}
      <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-4">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <h1 className="text-page-title break-words text-fg">{title}</h1>
            {meta}
          </div>
          {description && <p className="mt-1 max-w-3xl text-sm text-fg-muted">{description}</p>}
        </div>
        {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
      </div>
    </header>
  );
}

export interface SectionHeaderProps {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  id?: string;
  /** Heading level; defaults to h2. */
  as?: "h2" | "h3";
}

/** Heading for a section inside a page. */
export function SectionHeader({ title, description, actions, id, as: Heading = "h2" }: SectionHeaderProps) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        <Heading id={id} className="text-section-title text-fg">
          {title}
        </Heading>
        {description && <p className="mt-1 text-sm text-fg-muted">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  );
}

export function Divider({ className, label }: { className?: string; label?: string }) {
  if (label) {
    return (
      <div role="separator" aria-label={label} className={cn("flex items-center gap-3 text-xs text-fg-muted", className)}>
        <span className="h-px flex-1 bg-line" />
        {label}
        <span className="h-px flex-1 bg-line" />
      </div>
    );
  }
  return <hr className={cn("border-0 border-t border-line", className)} />;
}

type Gap = "xs" | "sm" | "md" | "lg" | "section";
const GAPS: Record<Gap, string> = { xs: "gap-1", sm: "gap-2", md: "gap-4", lg: "gap-6", section: "gap-section" };

export interface StackProps extends ComponentPropsWithoutRef<"div"> {
  direction?: "vertical" | "horizontal";
  gap?: Gap;
  align?: "start" | "center" | "end" | "stretch";
  wrap?: boolean;
  as?: ElementType;
}

/** Flex layout with consistent gaps from the spacing scale. */
export function Stack({ direction = "vertical", gap = "md", align, wrap = false, as: Component = "div", className, ...props }: StackProps) {
  return (
    <Component
      className={cn(
        "flex",
        direction === "vertical" ? "flex-col" : "flex-row",
        GAPS[gap],
        align && { start: "items-start", center: "items-center", end: "items-end", stretch: "items-stretch" }[align],
        wrap && "flex-wrap",
        className,
      )}
      {...props}
    />
  );
}

type Width = "narrow" | "default" | "wide" | "full";
const WIDTHS: Record<Width, string> = { narrow: "max-w-2xl", default: "max-w-content", wide: "max-w-(--breakpoint-2xl)", full: "max-w-none" };

/** Centres content with the standard maximum width for its kind of page. */
export function ResponsiveContainer({ width = "default", className, ...props }: ComponentPropsWithoutRef<"div"> & { width?: Width }) {
  return <div className={cn("mx-auto w-full min-w-0", WIDTHS[width], className)} {...props} />;
}

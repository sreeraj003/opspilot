import { useId, type ComponentPropsWithoutRef, type ReactNode } from "react";

import { cn } from "@/utils/cn";

type Padding = "none" | "sm" | "md";

const PADDING: Record<Padding, string> = { none: "", sm: "p-4", md: "p-5 sm:p-6" };

export interface CardProps extends Omit<ComponentPropsWithoutRef<"section">, "title"> {
  /** When given, the card is a labelled region with a header. */
  title?: ReactNode;
  description?: ReactNode;
  /** Buttons or links shown at the right of the header. */
  actions?: ReactNode;
  padding?: Padding;
}

/** The standard surface for grouped content. */
export function Card({ title, description, actions, padding = "md", className, children, ...props }: CardProps) {
  const headingId = useId();
  return (
    <section
      aria-labelledby={title ? headingId : undefined}
      className={cn("rounded-card border border-line bg-surface shadow-card", PADDING[padding], className)}
      {...props}
    >
      {title && (
        <div className={cn("flex flex-wrap items-start justify-between gap-3", padding === "none" && "px-5 pt-5 sm:px-6")}>
          <div className="min-w-0">
            <h2 id={headingId} className="text-section-title text-fg">
              {title}
            </h2>
            {description && <p className="mt-1 text-sm text-fg-muted">{description}</p>}
          </div>
          {actions && <div className="flex shrink-0 flex-wrap gap-2">{actions}</div>}
        </div>
      )}
      {title ? <div className={cn(padding === "none" ? "mt-4" : "mt-5")}>{children}</div> : children}
    </section>
  );
}

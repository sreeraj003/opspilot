/**
 * Design tokens that JavaScript needs. The visual tokens (colours, radius,
 * shadows, typography, motion) live in src/styles/tokens.css and reach
 * components as Tailwind utilities; this file mirrors only what code reads.
 */

/** Tailwind breakpoints in pixels (16px root), identical to --breakpoint-* in tokens.css. */
export const breakpoints = { sm: 640, md: 768, lg: 1024, xl: 1280, "2xl": 1536 } as const;
export type Breakpoint = keyof typeof breakpoints;

/** Media query for "at least this breakpoint". */
export function minWidth(breakpoint: Breakpoint): string {
  return `(min-width: ${breakpoints[breakpoint]}px)`;
}

/** Stacking layers, identical to --z-* in tokens.css. Use the CSS variables in classes: z-(--z-modal). */
export const zIndex = {
  base: 0,
  sticky: 20,
  sidebar: 30,
  drawer: 40,
  modal: 50,
  popover: 60,
  toast: 70,
  tooltip: 80,
} as const;

/** Durations in milliseconds for behaviour that waits on motion (toasts, tooltips). */
export const durations = { tooltipDelay: 350, toast: 5000 } as const;

/** The breakpoint at which the sidebar is docked instead of shown as a drawer. */
export const SIDEBAR_DOCK_BREAKPOINT: Breakpoint = "lg";

import { AlertTriangle, RotateCw } from "lucide-react";
import { Component, type ErrorInfo, type ReactNode } from "react";

import { Button } from "@/components/ui/Button";

interface ErrorBoundaryProps {
  children: ReactNode;
  fallback?: (error: Error, reset: () => void) => ReactNode;
}

interface ErrorBoundaryState {
  error: Error | null;
}

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("Unhandled UI error", error, info.componentStack);
  }

  reset = (): void => {
    this.setState({ error: null });
  };

  render(): ReactNode {
    const { error } = this.state;
    if (!error) return this.props.children;
    if (this.props.fallback) return this.props.fallback(error, this.reset);
    return <ErrorFallback error={error} onReset={this.reset} />;
  }
}

export function ErrorFallback({ error, onReset }: { error: Error; onReset?: () => void }) {
  return (
    <div role="alert" className="mx-auto my-16 max-w-lg rounded-card border border-line bg-surface p-6 shadow-card">
      <div className="flex items-start gap-3">
        <span className="grid h-9 w-9 shrink-0 place-items-center rounded-control bg-danger-subtle text-danger-text">
          <AlertTriangle aria-hidden="true" className="h-4 w-4" />
        </span>
        <div className="min-w-0">
          <h2 className="text-section-title text-fg">Something went wrong</h2>
          <p className="mt-1 text-sm text-fg-muted">An unexpected error occurred while rendering this page.</p>
          {import.meta.env.DEV && (
            <pre className="mt-3 overflow-auto rounded-sm bg-surface-muted p-3 text-xs text-fg-secondary">{error.message}</pre>
          )}
          {onReset && (
            <Button variant="secondary" size="sm" className="mt-4" onClick={onReset}>
              <RotateCw aria-hidden="true" className="h-3.5 w-3.5" />
              Try again
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}

/** Router-level fallback when a route fails to render or load. */
export function RouteErrorFallback() {
  return <ErrorFallback error={new Error("This page failed to load.")} onReset={() => window.location.reload()} />;
}

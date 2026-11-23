import { Component, type ErrorInfo, type ReactNode } from "react";

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
    <div role="alert" className="mx-auto mt-24 max-w-lg rounded-xl border border-rose-200 bg-white p-6 shadow-sm">
      <h2 className="text-lg font-semibold text-rose-700">Something went wrong</h2>
      <p className="mt-2 text-sm text-slate-600">
        An unexpected error occurred while rendering this page.
      </p>
      {import.meta.env.DEV && (
        <pre className="mt-3 overflow-auto rounded bg-slate-50 p-3 text-xs text-slate-700">{error.message}</pre>
      )}
      {onReset && (
        <button
          type="button"
          onClick={onReset}
          className="mt-4 rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-500 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-600"
        >
          Try again
        </button>
      )}
    </div>
  );
}

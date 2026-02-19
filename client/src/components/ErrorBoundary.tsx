/**
 * Generic error boundary for lazy-loaded components.
 *
 * When a chunk fails to load (network issue, deploy-time cache bust, etc.)
 * this shows a friendly fallback instead of crashing the whole app.
 */
import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
  /** Optional fallback — defaults to a "Retry" card */
  fallback?: ReactNode;
}

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("[ErrorBoundary]", error, info.componentStack);
  }

  handleRetry = () => {
    this.setState({ error: null });
  };

  render() {
    if (this.state.error) {
      if (this.props.fallback) return this.props.fallback;

      return (
        <div className="flex flex-col items-center justify-center gap-3 p-6 text-center rounded-lg border border-border bg-surface">
          <p className="text-sm text-foreground font-medium">
            Something went wrong
          </p>
          <p className="text-xs text-muted max-w-xs">
            {this.state.error.message || "Failed to load this section."}
          </p>
          <button
            onClick={this.handleRetry}
            className="px-3 py-1.5 text-xs font-medium rounded-lg bg-primary text-primary-foreground hover:opacity-90 transition-opacity"
          >
            Try Again
          </button>
        </div>
      );
    }

    return this.props.children;
  }
}

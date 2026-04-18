"use client";

/**
 * Consistent error banner for Smart Money mutations/queries.
 * Shows the real error message + an optional retry button.
 * Use below the triggering button so users see *why* a click did nothing.
 */
export function ErrorBanner({
  error,
  onRetry,
  title = "Request failed",
}: {
  error: unknown;
  onRetry?: () => void;
  title?: string;
}) {
  const msg = (error as Error)?.message ?? (typeof error === "string" ? error : "unknown error");
  return (
    <div className="card border-loss/30 bg-loss-bg text-loss text-sm flex items-center justify-between gap-3">
      <span>
        <strong>{title}:</strong> {msg}
      </span>
      {onRetry && (
        <button
          onClick={onRetry}
          className="px-3 py-1 text-xs font-semibold rounded border border-loss/40 hover:bg-loss/10 shrink-0"
        >
          Retry
        </button>
      )}
    </div>
  );
}

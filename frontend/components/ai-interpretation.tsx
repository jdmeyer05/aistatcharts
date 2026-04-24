"use client";

import { useEffect } from "react";
import { useMutation } from "@tanstack/react-query";
import { AIMarkdown } from "@/components/ai-markdown";
import { fetchInterpretation } from "@/lib/api";

interface Props {
  /** Page identifier. Must match a key in api/routes/ai.py:PAGE_CONTEXT. */
  page: string;
  /** Compact JSON summary of what's currently rendered. Keep under ~10KB for quick responses. */
  data: unknown;
  /** Optional: ticker / fund / politician name for additional context */
  subject?: string;
  /** Optional: disable if data isn't ready yet */
  disabled?: boolean;
  /** Optional: button copy override */
  buttonLabel?: string;
}

/**
 * Claude Opus 4.7 interpretation panel — embed at the bottom of any data-rich
 * page. Collapsed-by-default: user clicks to pay for a Claude call. Result
 * caches for the page's lifetime (same data won't retrigger until the user
 * re-clicks).
 */
export function AIInterpretation({ page, data, subject, disabled, buttonLabel }: Props) {
  const m = useMutation({
    mutationFn: () => fetchInterpretation({ page, data, subject }),
  });

  // Clear stale output when `page` changes — React can reuse this component
  // instance across e.g. options-analysis tabs (same JSX tree position,
  // different `page` prop). Without this reset, Tab 0's interpretation
  // lingers when the user lands on Tab 1.
  useEffect(() => {
    m.reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page]);

  const isDisabled = disabled || m.isPending || !data;

  return (
    <div className="card border-l-4 border-l-accent">
      <div className="flex items-baseline justify-between gap-2 flex-wrap mb-2">
        <div>
          <div className="text-sm font-bold flex items-center gap-2">
            <span>AI Interpretation</span>
            <span className="px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider rounded bg-accent/20 text-accent">
              Claude Opus 4.7
            </span>
          </div>
          <div className="text-xs text-text-muted">
            What does this data mean? Claude reads the current view and gives you a trader-facing take.
          </div>
        </div>
        <button
          onClick={() => m.mutate()}
          disabled={isDisabled}
          className="px-4 py-1.5 bg-accent text-white font-semibold rounded-lg hover:bg-accent-hover disabled:opacity-50 text-xs"
        >
          {m.isPending ? "Analyzing…" : m.data ? "Regenerate" : (buttonLabel ?? "Interpret")}
        </button>
      </div>

      {m.isPending && (
        <div className="text-center py-6">
          <div className="inline-block w-5 h-5 border-2 border-accent border-t-transparent rounded-full animate-spin" />
          <div className="text-xs text-text-muted mt-2">
            Claude is reading the data… typically 4-8 seconds.
          </div>
        </div>
      )}

      {m.isError && (() => {
        const msg = (m.error as Error)?.message ?? "unknown error";
        const isAuth = /\b401\b|unauthor/i.test(msg);
        const isQuota = /\b429\b|rate.?limit/i.test(msg);
        return (
          <div className="text-sm text-loss py-3">
            {isAuth
              ? "Sign in required to use AI interpretation."
              : isQuota
                ? "Claude rate-limited — try again in a few seconds."
                : `Interpretation failed: ${msg}`}
          </div>
        );
      })()}

      {m.data?.interpretation && (
        <div className="mt-2">
          <AIMarkdown text={m.data.interpretation} className="text-sm leading-relaxed markdown-body" />
          <div className="mt-3 pt-2 border-t border-border/50 text-[10px] text-text-muted flex flex-wrap gap-x-4 gap-y-1 items-center">
            <span>Model: {m.data.model}</span>
            {m.data.grounding && (() => {
              const g = m.data.grounding;
              const total = g.grounded_count + g.unverified_count;
              const pct = total > 0 ? Math.round((g.grounded_count / total) * 100) : 100;
              const color = g.unverified_count === 0 ? "text-gain" : pct >= 80 ? "text-warn" : "text-loss";
              return (
                <span
                  className={`${color} font-semibold`}
                  title={
                    g.unverified_count > 0
                      ? `Unverified numeric claims: ${g.unverified_tokens.join(", ")}`
                      : "Every numeric claim in the interpretation matches a value in the data."
                  }
                >
                  {g.unverified_count === 0 ? "✓" : "⚠"} Grounded {g.grounded_count}/{total}
                  {g.unverified_count > 0 && ` (${g.unverified_count} flagged)`}
                </span>
              );
            })()}
            <span>In: {m.data.input_tokens} tok</span>
            <span>Out: {m.data.output_tokens} tok</span>
            {m.data.cache_read_tokens ? (
              <span className="text-gain">Cache hit: {m.data.cache_read_tokens} tok</span>
            ) : m.data.cache_creation_tokens ? (
              <span>Cache primed: {m.data.cache_creation_tokens} tok</span>
            ) : null}
          </div>
          {m.data.grounding && m.data.grounding.unverified_count > 0 && (
            <details className="mt-2 text-[11px]">
              <summary className="cursor-pointer text-warn font-semibold">
                Flagged numeric claims ({m.data.grounding.unverified_count})
              </summary>
              <div className="mt-1 text-text-muted">
                These numbers don&apos;t appear in the data payload and aren&apos;t obvious derivations.
                They may be rounded approximations, rough computations, or — flag carefully — fabrications:
                <ul className="mt-1 list-disc pl-5">
                  {m.data.grounding.unverified_tokens.map((tok, i) => (
                    <li key={i} className="font-data">{tok}</li>
                  ))}
                </ul>
              </div>
            </details>
          )}
        </div>
      )}
    </div>
  );
}

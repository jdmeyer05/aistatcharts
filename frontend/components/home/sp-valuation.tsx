"use client";

/**
 * S&P valuation strip — six multiples against their own long-run history.
 *
 * A strip, not a card, on purpose: six numbers with context don't justify a
 * full panel, and the home page has more competing for vertical space than it
 * has genuinely full-width content.
 *
 * LEVEL treatment, deliberately not folded into the macro pressure scorecard.
 * That board scores the z-score of recent CHANGE, which is right for rates,
 * credit and vol. Valuation inverts the assumption — CAPE sits at an extreme
 * for years — so scored on change it would read neutral almost permanently.
 */

import { useQuery } from "@tanstack/react-query";
import { fetchSpValuation, type SpValuation } from "@/lib/api";

function fmt(v: number, unit: string): string {
  return unit === "pct" ? `${v.toFixed(2)}%` : `${v.toFixed(1)}×`;
}

export default function SpValuationStrip() {
  const q = useQuery<SpValuation>({
    // Monthly-cadence data behind an HTML scrape; the server holds it 6h.
    queryKey: ["sp-valuation"],
    queryFn: fetchSpValuation,
    refetchInterval: 60 * 60_000,
    staleTime: 55 * 60_000,
  });
  const d = q.data;

  return (
    <div className="card card-compact">
      <div className="flex flex-wrap items-baseline gap-x-5 gap-y-2">
        <div className="flex items-baseline gap-2">
          <span className="text-[0.6rem] font-bold uppercase tracking-wider text-accent">
            S&amp;P Valuation
          </span>
          {typeof d?.median_premium_pct === "number" && (
            <span
              className={`text-[0.6rem] font-bold px-1.5 py-0.5 rounded ${
                d.median_premium_pct > 25 ? "bg-loss/15 text-loss"
                  : d.median_premium_pct < -25 ? "bg-gain/15 text-gain"
                  : "bg-border text-text-muted"
              }`}
              title="Median premium across the six multiples vs each one's own long-run median"
            >
              {d.median_premium_pct > 0 ? "+" : ""}{d.median_premium_pct}% vs history
            </span>
          )}
        </div>

        {q.isLoading && <span className="text-[0.65rem] text-text-muted">loading…</span>}
        {!q.isLoading && !d?.available && (
          <span className="text-[0.65rem] text-text-muted">valuation unavailable</span>
        )}

        {d?.available && d.rows?.map((r) => (
          <div key={r.key} className="flex items-baseline gap-1.5 min-w-0" title={r.why}>
            <span className="text-[0.6rem] uppercase tracking-wider text-text-muted">{r.label}</span>
            <span className="text-sm font-semibold tabular-nums text-text">{fmt(r.value, r.unit)}</span>
            {/* Against its own median, which is the only comparison that makes
                CAPE and dividend yield readable on the same row. */}
            {typeof r.premium_to_median_pct === "number" && (
              <span
                className={`text-[0.62rem] tabular-nums ${
                  r.premium_to_median_pct > 0 ? "text-loss" : "text-gain"
                }`}
                title={`Long-run median ${r.median ?? "—"}`}
              >
                {r.premium_to_median_pct > 0 ? "+" : ""}{Math.round(r.premium_to_median_pct)}%
              </span>
            )}
          </div>
        ))}

        {d?.available && (
          <span className="ml-auto text-[0.55rem] text-text-muted">
            vs long-run median · {d.source} · not a timing signal
          </span>
        )}
      </div>
    </div>
  );
}

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
import { ordinal } from "@/lib/home-constants";

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

      {/* The multiples above are a slow state variable and say nothing about
          today. This row is the part that moves daily. The two halves are
          reported side by side but NOT joined: the obvious link between them
          was tested and rejected — see SpRateContext in lib/api.ts. */}
      {d?.available && d.rate_context && rc(d.rate_context)}
    </div>
  );
}

function rc(c: NonNullable<SpValuation["rate_context"]>) {
  const hasErp = typeof c.erp_pct === "number";
  const hasBeta = typeof c.move_per_10bp_pct === "number";
  if (!hasErp && !hasBeta) return null;
  return (
    <div className="mt-2 pt-2 border-t border-border flex flex-wrap items-baseline gap-x-5 gap-y-1">
      {hasErp && (
        <div className="flex items-baseline gap-1.5 min-w-0">
          <span className="text-[0.6rem] uppercase tracking-wider text-text-muted">
            Earnings yield vs 10y
          </span>
          <span className="text-sm font-semibold tabular-nums text-text">
            {c.erp_pct! > 0 ? "+" : ""}{c.erp_pct!.toFixed(2)}pp
          </span>
          <span className="text-[0.6rem] text-text-muted tabular-nums">
            {c.earnings_yield_pct?.toFixed(2)}% − {c.ten_year_pct?.toFixed(2)}%
          </span>
          {typeof c.erp_pctile === "number" && (
            <span className="text-[0.6rem] text-text-muted tabular-nums">
              · {ordinal(c.erp_pctile)} pctile of {c.erp_n_months} months
            </span>
          )}
        </div>
      )}

      {hasBeta && (
        <div className="flex items-baseline gap-1.5 min-w-0">
          <span className="text-[0.6rem] uppercase tracking-wider text-text-muted">
            10bp on the 10y
          </span>
          <span className={`text-sm font-semibold tabular-nums ${
            c.move_per_10bp_pct! < 0 ? "text-loss" : "text-gain"}`}>
            {c.move_per_10bp_pct! > 0 ? "+" : ""}{c.move_per_10bp_pct!.toFixed(2)}% SPX
          </span>
          <span className="text-[0.6rem] text-text-muted tabular-nums">
            {c.beta_window_days}d
            {typeof c.beta_pctile === "number" && `, ${ordinal(c.beta_pctile)} pctile of ${c.beta_pctile_years}y`}
            {typeof c.rates_r2 === "number" && `, R² ${c.rates_r2.toFixed(2)}`}
          </span>
        </div>
      )}

      {/* Caveats as text, not tooltips. */}
      <span className="basis-full text-[0.55rem] text-text-muted leading-snug">
        {typeof c.erp_streak_months === "number" && typeof c.erp_negative_share_pct === "number" && (
          <>
            The index has yielded {c.erp_streak_is_negative ? "less" : "more"} than the 10-year for{" "}
            <span className="text-text tabular-nums">{c.erp_streak_months} straight months</span>;
            that sign has held in {c.erp_negative_share_pct.toFixed(0)}% of months since 1986, so it is
            the run rather than the sign that is unusual.{" "}
          </>
        )}
        {typeof c.rates_r2 === "number" && (
          <>
            R² is how much of the index&apos;s daily move rates explain at all — a large sensitivity with a
            low R² is not currently carrying the tape.{" "}
          </>
        )}
        Measured, not predicted: the risk premium does not condition this sensitivity once the level of
        rates is controlled for.
      </span>
    </div>
  );
}

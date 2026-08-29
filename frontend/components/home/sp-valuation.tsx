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

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchSpValuation, type SpValuation } from "@/lib/api";
import { ordinal } from "@/lib/home-constants";
import { Takeaway } from "@/components/home/primitives";

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

  // THE TAKEAWAY, and the one this strip most needed: six multiples and two
  // rate readings, all of which have sat at an extreme for years, invite
  // exactly the wrong inference — that an expensive market is a short. It is
  // not, and the strip's own footer already said "not a timing signal" in
  // 0.55rem grey. Say it as the reading rather than as fine print, and give
  // the number that makes it concrete: how long the current state has already
  // persisted.
  const read = useMemo(() => {
    if (!d?.available) return null;
    const prem = d.median_premium_pct;
    const rc = d.rate_context;
    const rich = typeof prem === "number" && prem > 25;
    const cheap = typeof prem === "number" && prem < -25;

    const headline =
      typeof prem !== "number"
        ? "The multiples loaded, but no median premium could be computed against their own history."
        : rich
          ? `The index sits ${prem > 0 ? "+" : ""}${prem}% above the median of its own long-run range across the six multiples.`
          : cheap
            ? `The index sits ${prem}% below the median of its own long-run range across the six multiples.`
            : `The index sits ${prem > 0 ? "+" : ""}${prem}% against the median of its own long-run range — inside the ordinary band.`;

    const parts: string[] = [];
    if (rc?.erp_streak_months != null && rc.erp_negative_share_pct != null) {
      parts.push(
        `The index has yielded ${rc.erp_streak_is_negative ? "less" : "more"} than the 10-year for ` +
        `${rc.erp_streak_months} straight months, and that sign has held in ` +
        `${rc.erp_negative_share_pct.toFixed(0)}% of months since 1986 — the run is the unusual part, not the sign.`
      );
    }
    if (rc?.erp_pctile != null && rc.erp_n_months != null) {
      parts.push(
        `The risk premium is at the ${ordinal(rc.erp_pctile)} percentile of ${rc.erp_n_months} months.`
      );
    }
    if (rc?.rates_r2 != null && rc.move_per_10bp_pct != null) {
      parts.push(
        `Rates currently explain R² ${rc.rates_r2.toFixed(2)} of the index's daily variance, so the ` +
        `${rc.move_per_10bp_pct > 0 ? "+" : ""}${rc.move_per_10bp_pct.toFixed(2)}% per 10bp sensitivity ` +
        `${rc.rates_r2 < 0.2 ? "is not currently carrying the tape" : "is being expressed in the tape"}.`
      );
    }
    parts.push(
      "This is a state variable, not a timing input: valuation has sat at extremes for years at a " +
      "time and nothing on this strip has been tested as a forecast of the next session, week or quarter."
    );

    return { headline, detail: parts.join(" ") };
  }, [d]);

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
          /* The one card on this page with no data timestamp at all. Every
             other board says how old its numbers are; this one refreshes hourly
             against a monthly-cadence scrape behind a six-hour server cache, so
             a stale read looks identical to a live one. `asof` was in the
             payload the whole time. */
          <span className="ml-auto text-[0.55rem] text-text-muted">
            vs long-run median · {d.source}
            {d.asof ? ` · as of ${d.asof}` : ""} · not a timing signal
          </span>
        )}
      </div>

      {/* The multiples above are a slow state variable and say nothing about
          today. This row is the part that moves daily. The two halves are
          reported side by side but NOT joined: the obvious link between them
          was tested and rejected — see SpRateContext in lib/api.ts. */}
      {d?.available && d.rate_context && rc(d.rate_context)}

      {read && (
        <div className="mt-2">
          <Takeaway headline={read.headline} detail={read.detail} />
        </div>
      )}
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

"use client";

/**
 * State of the 12-month time-series momentum book.
 *
 * WHY THIS IS ON A PAGE THAT DOES NOT GIVE SIGNALS. It doesn't give one. Every
 * other block here describes the market; this describes a position book that
 * has already been committed to, and it answers the three bookkeeping
 * questions running that system forces on you anyway:
 *
 *      what am I supposed to be holding?          (set at the last month-end)
 *      what would the rule say if I rebalanced?   (today)
 *      when do those get reconciled?              (the next month-end)
 *
 * THE HELD/LIVE SPLIT IS THE WHOLE CARD. The rule rebalances MONTHLY and holds
 * weights constant in between. Showing only today's raw target would invite
 * trading the drift, which is a different and worse system — daily rebalancing
 * backtests at Sharpe 0.62 against monthly's 0.72-0.74. So the held book leads
 * and the live column is explicitly labelled as what the rule WOULD say, not as
 * something to do today.
 *
 * IT QUOTES THE POSTERIOR, NOT THE BACKTEST. The raw backtest Sharpe is 0.72;
 * the Bayesian posterior across the parameter grid is 0.51, CI [0.29, 0.73].
 * The headline number here is 0.51, because the 0.72 is the best cell of a grid
 * that was searched and the 0.51 is what survives being honest about that.
 */

import { useQuery } from "@tanstack/react-query";
import { fetchTsmomBook, type TsmomBook, type TsmomRow } from "@/lib/api";
import { CardHeader, Takeaway } from "@/components/home/primitives";

function sideClass(side: string): string {
  return side === "long" ? "text-gain" : side === "short" ? "text-loss" : "text-text-muted";
}

function Row({ r }: { r: TsmomRow }) {
  return (
    <div className="flex items-baseline gap-2 text-[0.65rem] tabular-nums">
      <span className="w-11 font-semibold shrink-0">{r.ticker}</span>
      <span className="w-24 text-text-muted truncate shrink-0" title={r.asset_class}>
        {r.asset_class}
      </span>
      <span className={`w-12 text-right ${r.return_12m_pct >= 0 ? "text-gain" : "text-loss"}`}>
        {r.return_12m_pct > 0 ? "+" : ""}{r.return_12m_pct.toFixed(1)}%
      </span>
      <span className="w-12 text-right text-text-muted">
        {r.ann_vol_pct == null ? "—" : `${r.ann_vol_pct.toFixed(1)}%`}
      </span>
      <span className={`w-11 text-right font-semibold uppercase text-[0.55rem] ${sideClass(r.side)}`}>
        {r.side}
      </span>
      <span className="w-14 text-right font-semibold">
        {r.weight_pct > 0 ? "+" : ""}{r.weight_pct.toFixed(1)}%
      </span>
      {/* The median split on trend strength is the one refinement that beat a
          purpose-built classifier. Flagged, never applied — the production rule
          does not include it and this card reports the rule as run. */}
      <span
        className={`w-5 text-center text-[0.55rem] ${
          r.above_strength_median ? "text-accent" : "text-text-muted/40"}`}
        title={
          r.trend_strength == null
            ? "No trend-strength reading."
            : `Trend strength ${r.trend_strength.toFixed(2)} (|12m return| / annualised vol) — ${
                r.above_strength_median ? "above" : "below"
              } today's cross-sectional median. A median split on this measured Sharpe 0.62 → 0.68 and maxDD −23.6% → −18.2%, but it is not part of the shipped rule.`
        }
      >
        {r.above_strength_median == null ? "" : r.above_strength_median ? "●" : "○"}
      </span>
    </div>
  );
}

export default function TsmomBookCard() {
  const q = useQuery<TsmomBook>({
    // The rule rebalances monthly and the prices behind it move daily. The
    // server holds this 12h; polling faster would buy nothing.
    queryKey: ["tsmom-book"],
    queryFn: fetchTsmomBook,
    refetchInterval: 6 * 60 * 60_000,
    staleTime: 5 * 60 * 60_000,
  });
  const d = q.data;

  const held = d?.held;
  const live = d?.live;
  const flips = d?.flips_since_rebalance ?? [];
  const next = d?.next_rebalance;
  const res = d?.research;

  // Sorted by size of position: the book's risk is concentrated in the low-vol
  // legs by construction, and a ticker-alphabetical list hides that.
  const heldRows = [...(held?.rows ?? [])]
    .filter((r) => r.side !== "flat")
    .sort((a, b) => Math.abs(b.weight_pct) - Math.abs(a.weight_pct));
  const flat = (held?.rows ?? []).filter((r) => r.side === "flat").map((r) => r.ticker);

  return (
    <div className="card space-y-3">
      <CardHeader
        title="Trend Book — 12-month TSMOM"
        size="md"
        asOf={d?.asof}
        staleAfterMin={60 * 24 * 4}
        right={
          <button
            type="button"
            onClick={() => q.refetch()}
            disabled={q.isFetching}
            className="text-[0.65rem] px-2 py-1 rounded border border-border hover:bg-surface-alt disabled:opacity-50"
            title="Recompute the book"
          >
            {q.isFetching ? "…" : "Refresh"}
          </button>
        }
      />
      <div className="text-[0.6rem] text-text-muted -mt-2">
        Bookkeeping for a committed system — no signal, no forecast. Rebalances monthly.
      </div>

      {q.isLoading && (
        <div className="py-6 text-center">
          <div className="inline-block w-5 h-5 border-2 border-accent border-t-transparent rounded-full animate-spin" />
          <p className="text-xs text-text-muted mt-2">Building the book…</p>
        </div>
      )}

      {!q.isLoading && !d?.available && (
        <div className="py-3 flex items-baseline gap-2 flex-wrap">
          <p className="text-xs text-text-muted">
            {q.isError
              ? "Couldn't load the trend book."
              : `Book unavailable${d?.reason ? ` — ${d.reason}` : ""}.`}
          </p>
          <button
            type="button"
            onClick={() => q.refetch()}
            disabled={q.isFetching}
            className="text-[0.65rem] text-accent hover:underline disabled:opacity-50"
          >
            {q.isFetching ? "Retrying…" : "Retry"}
          </button>
        </div>
      )}

      {d?.available && held && live && (
        <>
          {/* THE TAKEAWAY. Held vs live vs when — the three questions, in one
              sentence each, before any table. */}
          <Takeaway
            tone={flips.length > 0 ? "warn" : "neutral"}
            headline={
              flips.length === 0
                ? `The book is ${held.exposure.n_long} long / ${held.exposure.n_short} short at ${held.exposure.total_gross_pct.toFixed(0)}% gross, and no market has changed sign since it was set.`
                : `${flips.length} market${flips.length === 1 ? " has" : "s have"} changed sign since the book was set: ${flips.map((f) => `${f.ticker} ${f.from}→${f.to}`).join(", ")}.`
            }
            detail={
              `Held weights were set ${d.last_rebalance ?? "at the last month-end"} at ${d.portfolio_scale_held?.toFixed(2)}× portfolio scale; ` +
              `the rule today would run ${d.portfolio_scale?.toFixed(2)}× and ${live.exposure.total_gross_pct.toFixed(0)}% gross. ` +
              `Those reconcile at the next rebalance, about ${next?.sessions_away ?? "—"} session${next?.sessions_away === 1 ? "" : "s"} away (${next?.estimated_date ?? "—"}). ` +
              `Nothing here is a position to take today — the drift between the two columns is the system working as specified, not a trade.`
            }
          />

          {/* Flips get their own line when the signal behind one is marginal —
              "TLT short → long" reads as conviction until you see it flipped on
              a twelve-month return of +0.0%. */}
          {flips.some((f) => (f.trend_strength ?? 1) < 0.15) && (
            <p className="text-[0.6rem] text-amber-400/90 leading-snug">
              {flips
                .filter((f) => (f.trend_strength ?? 1) < 0.15)
                .map((f) => `${f.ticker} flipped on a 12-month return of ${f.return_12m_pct > 0 ? "+" : ""}${f.return_12m_pct.toFixed(1)}% (trend strength ${f.trend_strength?.toFixed(2)})`)
                .join("; ")}
              {" "}— a signal sitting on its own zero line, not a trend change. It can flip back before the rebalance.
            </p>
          )}

          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
            {[
              { k: "Gross", v: `${held.exposure.total_gross_pct.toFixed(0)}%`, n: "of NAV — margin account assumed" },
              { k: "Net", v: `${held.exposure.net_pct > 0 ? "+" : ""}${held.exposure.net_pct.toFixed(0)}%`, n: "long minus short" },
              { k: "Positions", v: `${held.exposure.n_long}L / ${held.exposure.n_short}S`, n: `${held.exposure.n_flat} flat of ${d.n_markets}` },
              { k: "Next rebalance", v: `${next?.sessions_away ?? "—"}d`, n: next?.estimated_date ?? "" },
            ].map((s) => (
              <div key={s.k} className="border border-border rounded p-2">
                <div className="text-[0.5rem] uppercase tracking-wider text-text-muted">{s.k}</div>
                <div className="text-sm font-bold font-data tabular-nums mt-0.5">{s.v}</div>
                <div className="text-[0.5rem] text-text-muted leading-tight mt-0.5">{s.n}</div>
              </div>
            ))}
          </div>

          <div className="space-y-1">
            <div className="flex items-baseline gap-2">
              <h4 className="text-[0.6rem] font-bold uppercase tracking-wider text-text-muted">
                Held book — set {d.last_rebalance ?? "at the last month-end"}
              </h4>
              <span className="text-[0.55rem] text-text-muted/70">
                what should be in the account now
              </span>
            </div>
            <div className="flex items-baseline gap-2 text-[0.5rem] uppercase tracking-wider text-text-muted">
              <span className="w-11 shrink-0">etf</span>
              <span className="w-24 shrink-0">class</span>
              <span className="w-12 text-right">12m</span>
              <span className="w-12 text-right">vol</span>
              <span className="w-11 text-right">side</span>
              <span className="w-14 text-right">weight</span>
              <span className="w-5" />
            </div>
            {heldRows.map((r) => <Row key={r.ticker} r={r} />)}
            {flat.length > 0 && (
              <div className="text-[0.6rem] text-text-muted pt-1">
                Flat ({flat.length}): {flat.join(", ")}
              </div>
            )}
          </div>

          <details className="group">
            <summary className="text-[0.62rem] text-text-muted hover:text-accent cursor-pointer select-none list-none flex items-center gap-1">
              <span className="transition-transform group-open:rotate-90">▸</span>
              What to expect from it, and where it breaks
            </summary>
            <div className="text-[0.65rem] text-text-muted leading-relaxed mt-2 space-y-1.5 pl-3 border-l border-border">
              {res && (
                <p>
                  <span className="text-text font-semibold">Size for 0.5, not 0.7.</span> The raw
                  backtest is Sharpe {res.sharpe_backtest.toFixed(2)} ({res.ann_return_pct.toFixed(1)}%
                  annual, {res.ann_vol_pct.toFixed(1)}% vol, {res.max_drawdown_pct.toFixed(1)}% max
                  drawdown, against SPY&apos;s {res.spy_sharpe.toFixed(2)} and{" "}
                  {res.spy_max_drawdown_pct.toFixed(1)}%). But that is the chosen cell of a
                  32-cell parameter grid; the Bayesian posterior across the whole grid is{" "}
                  <span className="text-text font-semibold">{res.sharpe_posterior.toFixed(2)}</span>,
                  95% credible interval [{res.sharpe_posterior_ci95[0].toFixed(2)},{" "}
                  {res.sharpe_posterior_ci95[1].toFixed(2)}]. Positive in {res.eras_positive}.
                </p>
              )}
              {res && (
                <p>
                  <span className="text-text font-semibold">The weakness to expect.</span>{" "}
                  {res.worst_episode}. Trend protects in slow bears and gets hurt in fast crashes;
                  no parameter choice fixes that.
                </p>
              )}
              {(d.caveats ?? []).map((c, i) => (
                <p key={i}>{c}</p>
              ))}
              {res && (
                <p className="text-text-muted/70">
                  Rule and figures: {res.source}. Turnover ~{res.turnover_per_year}/year.
                </p>
              )}
            </div>
          </details>
        </>
      )}
    </div>
  );
}

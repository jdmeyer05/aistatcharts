"use client";

import { useState, useMemo } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { useTheme } from "next-themes";
import { Plot } from "@/components/plot";
import { fetchShortInterest, fetchShortsWatchlist } from "@/lib/api";
import { getChartTheme, getBaseLayout, CHART_HEIGHT } from "@/lib/chart-theme";
import { Metric } from "@/components/ui/metric";
import { AIInterpretation } from "@/components/ai-interpretation";
import { fmtBn } from "../_shared/utils";


/**
 * Composite squeeze score 0-100 from short-side structural factors.
 * Higher = stronger squeeze setup, not a price forecast.
 */
function squeezeScore(pctFloat: number | null | undefined, daysToCover: number | null | undefined, deltaShort: number): number {
  let score = 0;
  if (pctFloat != null) {
    // Above 20% of float is historically where squeezes develop.
    score += Math.min(40, pctFloat * 100 * 2);
  }
  if (daysToCover != null) {
    // 5+ days = significant cover pressure.
    score += Math.min(30, daysToCover * 4);
  }
  // Recent INCREASE in short interest adds pressure; decrease relieves.
  score += Math.max(-15, Math.min(30, deltaShort * 100));
  return Math.max(0, Math.min(100, Math.round(score)));
}

export default function ShortsPage() {
  const { resolvedTheme } = useTheme();
  const t = getChartTheme(resolvedTheme === "dark");
  const L = getBaseLayout(t);

  const [ticker, setTicker] = useState("");

  const lookup = useMutation({
    mutationFn: (tk: string) => fetchShortInterest(tk.toUpperCase()),
  });

  const watchQ = useQuery({
    queryKey: ["shorts-watchlist"],
    queryFn: fetchShortsWatchlist,
    staleTime: 30 * 60_000,
  });

  const ranked = useMemo(() => {
    if (!watchQ.data) return [];
    return [...watchQ.data.data]
      .filter((r) => r.short_pct_float != null)
      .map((r) => {
        const prior = r.shares_short_prior ?? 0;
        const cur = r.shares_short ?? 0;
        const delta = prior > 0 ? (cur - prior) / prior : 0;
        return {
          ...r,
          deltaShort: delta,
          squeeze: squeezeScore(r.short_pct_float, r.short_ratio, delta),
        };
      })
      .sort((a, b) => b.squeeze - a.squeeze);
  }, [watchQ.data]);

  const result = lookup.data;
  const resultSqueeze = useMemo(() => {
    if (!result || !result.ok) return null;
    const prior = result.shares_short_prior ?? 0;
    const cur = result.shares_short ?? 0;
    const delta = prior > 0 ? (cur - prior) / prior : 0;
    return {
      delta,
      score: squeezeScore(result.short_pct_float, result.short_ratio, delta),
    };
  }, [result]);

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Short Side</h1>
        <p className="text-text-secondary text-sm mt-1">
          Short interest, days-to-cover, and composite squeeze setups. Data sourced from Yahoo Finance&apos;s feed of
          FINRA biweekly short-interest reports.
        </p>
      </div>

      <div className="card border-l-4 border-l-warn">
        <div className="text-xs font-bold uppercase tracking-wider text-warn mb-1">How to read this</div>
        <p className="text-sm">
          Heavy short interest alone is noise — most crowded shorts are correctly bearish. Setups worth attention
          combine <strong>high % of float short</strong> (&gt;20%), <strong>elevated days-to-cover</strong> (&gt;5),
          AND <strong>recent price momentum</strong>. The squeeze score here only captures the structural factors;
          pair it with the chart and a catalyst before acting.
        </p>
      </div>

      {/* Per-ticker lookup */}
      <div className="card card-compact">
        <div className="flex items-end gap-3 flex-wrap">
          <div className="flex-1 min-w-[240px]">
            <label className="metric-label">Ticker</label>
            <input
              value={ticker}
              onChange={(e) => setTicker(e.target.value.toUpperCase())}
              onKeyDown={(e) => e.key === "Enter" && ticker && lookup.mutate(ticker)}
              placeholder="GME, AMC, TSLA"
              className="mt-0.5 w-full px-2 py-1.5 border border-border rounded text-sm bg-surface font-data"
            />
          </div>
          <button
            onClick={() => ticker && lookup.mutate(ticker)}
            disabled={!ticker || lookup.isPending}
            className="px-5 py-2 bg-accent text-white font-semibold rounded-lg hover:bg-accent-hover disabled:opacity-50 text-sm"
          >
            {lookup.isPending ? "Loading…" : "Look up short interest"}
          </button>
        </div>
      </div>

      {lookup.isPending && (
        <div className="card text-center py-8">
          <div className="inline-block w-5 h-5 border-2 border-accent border-t-transparent rounded-full animate-spin" />
        </div>
      )}

      {result && !result.ok && (
        <div className="card text-sm text-loss">Lookup failed: {result.error ?? "unknown error"}</div>
      )}

      {result && result.ok && resultSqueeze && (
        <div className="card">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {/* Squeeze score */}
            <div
              className="rounded-lg p-5 text-center"
              style={{
                border: `2px solid ${resultSqueeze.score >= 60 ? t.loss : resultSqueeze.score >= 35 ? t.spot : t.muted}`,
                background: `${resultSqueeze.score >= 60 ? t.loss : resultSqueeze.score >= 35 ? t.spot : t.muted}15`,
              }}
            >
              <div className="text-xs font-bold uppercase tracking-wider text-text-muted">Squeeze Score</div>
              <div
                className="text-5xl font-bold my-1"
                style={{ color: resultSqueeze.score >= 60 ? t.loss : resultSqueeze.score >= 35 ? t.spot : t.muted }}
              >
                {resultSqueeze.score}
              </div>
              <div className="text-xs text-text-muted">
                {resultSqueeze.score >= 60 ? "Strong setup" : resultSqueeze.score >= 35 ? "Moderate" : "Quiet"}
              </div>
            </div>

            <div className="md:col-span-2 rounded-lg p-4 border border-border space-y-2.5 text-sm">
              <div className="font-semibold">{result.name ?? result.ticker}</div>
              <div className="flex flex-wrap gap-x-6 gap-y-2 text-xs">
                <Metric label="Price" value={result.price != null ? `$${result.price.toFixed(2)}` : "—"} />
                <Metric label="Mkt cap" value={result.market_cap != null ? fmtBn(result.market_cap) : "—"} />
                <Metric
                  label="% of float short"
                  value={result.short_pct_float != null ? `${(result.short_pct_float * 100).toFixed(1)}%` : "—"}
                  deltaType={result.short_pct_float != null && result.short_pct_float > 0.2 ? "loss" : "neutral"}
                />
                <Metric
                  label="Days to cover"
                  value={result.short_ratio != null ? result.short_ratio.toFixed(1) : "—"}
                  deltaType={result.short_ratio != null && result.short_ratio > 5 ? "loss" : "neutral"}
                />
                <Metric label="Shares short" value={result.shares_short != null ? fmtBn(result.shares_short) : "—"} />
                <Metric
                  label="Δ vs prior month"
                  value={`${resultSqueeze.delta >= 0 ? "+" : ""}${(resultSqueeze.delta * 100).toFixed(1)}%`}
                  deltaType={resultSqueeze.delta > 0.05 ? "loss" : resultSqueeze.delta < -0.05 ? "gain" : "neutral"}
                />
              </div>
              <div className="text-[11px] text-text-muted pt-2 border-t border-border">
                Float shares: {result.float_shares != null ? result.float_shares.toLocaleString() : "—"} ·
                10-day avg volume: {result.avg_volume_10d != null ? result.avg_volume_10d.toLocaleString() : "—"}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Watchlist leaderboard */}
      <div className="card">
        <div className="flex items-baseline justify-between mb-2">
          <div>
            <div className="text-sm font-bold">Watchlist — ranked by squeeze score</div>
            <div className="text-xs text-text-muted">
              25 frequently-squeezed or heavily-shorted names. Short leaders rotate — worth reviewing quarterly.
            </div>
          </div>
        </div>

        {watchQ.isPending && (
          <div className="text-center py-6">
            <div className="inline-block w-5 h-5 border-2 border-accent border-t-transparent rounded-full animate-spin" />
            <div className="text-xs text-text-muted mt-2">Fetching 25 tickers in parallel…</div>
          </div>
        )}

        {watchQ.isError && (
          <div className="text-sm text-loss py-4">Watchlist fetch failed.</div>
        )}

        {ranked.length > 0 && (
          <>
            <Plot
              data={[
                {
                  type: "bar" as const,
                  orientation: "h" as const,
                  y: ranked.slice(0, 15).map((r) => r.ticker),
                  x: ranked.slice(0, 15).map((r) => (r.short_pct_float ?? 0) * 100),
                  marker: {
                    color: ranked.slice(0, 15).map((r) =>
                      r.squeeze >= 60 ? t.loss : r.squeeze >= 35 ? t.spot : t.muted,
                    ),
                  },
                  text: ranked.slice(0, 15).map((r) =>
                    `${((r.short_pct_float ?? 0) * 100).toFixed(1)}% · ${(r.short_ratio ?? 0).toFixed(1)}d · squeeze ${r.squeeze}`,
                  ),
                  textposition: "outside" as const,
                },
              ]}
              layout={{
                ...L,
                height: CHART_HEIGHT.tall,
                title: { text: "Top 15 squeeze candidates — % of float short", font: { size: 13, color: t.text } },
                xaxis: { title: { text: "% of float short" }, gridcolor: t.grid },
                yaxis: { gridcolor: t.grid, autorange: "reversed" },
                margin: { l: 70, r: 260, t: 40, b: 40 },
              }}
              config={{ displayModeBar: false, responsive: true }}
              style={{ width: "100%" }}
            />

            <div className="overflow-x-auto max-h-[480px] mt-3">
              <table className="w-full text-xs font-data">
                <thead className="border-b border-border text-text-muted sticky top-0 bg-surface">
                  <tr>
                    <th className="text-left py-1.5 px-2">#</th>
                    <th className="text-left py-1.5 px-2">Ticker</th>
                    <th className="text-left py-1.5 px-2">Name</th>
                    <th className="text-right py-1.5 px-2">Price</th>
                    <th className="text-right py-1.5 px-2">% Float</th>
                    <th className="text-right py-1.5 px-2">D2C</th>
                    <th className="text-right py-1.5 px-2">Δ Mo/Mo</th>
                    <th className="text-right py-1.5 px-2">Score</th>
                  </tr>
                </thead>
                <tbody>
                  {ranked.map((r, i) => (
                    <tr key={r.ticker} className="border-b border-border/50 hover:bg-surface-alt">
                      <td className="py-1 px-2 text-text-muted">{i + 1}</td>
                      <td className="py-1 px-2 font-bold">{r.ticker}</td>
                      <td className="py-1 px-2 text-text-muted truncate max-w-[220px]">{r.name ?? "—"}</td>
                      <td className="py-1 px-2 text-right">{r.price != null ? `$${r.price.toFixed(2)}` : "—"}</td>
                      <td className={`py-1 px-2 text-right ${(r.short_pct_float ?? 0) > 0.2 ? "text-loss font-semibold" : ""}`}>
                        {r.short_pct_float != null ? `${(r.short_pct_float * 100).toFixed(1)}%` : "—"}
                      </td>
                      <td className={`py-1 px-2 text-right ${(r.short_ratio ?? 0) > 5 ? "text-loss font-semibold" : ""}`}>
                        {r.short_ratio != null ? r.short_ratio.toFixed(1) : "—"}
                      </td>
                      <td className={`py-1 px-2 text-right ${r.deltaShort > 0.05 ? "text-loss" : r.deltaShort < -0.05 ? "text-gain" : "text-text-muted"}`}>
                        {r.deltaShort >= 0 ? "+" : ""}{(r.deltaShort * 100).toFixed(1)}%
                      </td>
                      <td className="py-1 px-2 text-right">
                        <span
                          className="px-1.5 py-0.5 rounded text-[10px] font-bold"
                          style={{
                            background: r.squeeze >= 60 ? t.loss : r.squeeze >= 35 ? t.spot : t.muted,
                            color: "#000",
                          }}
                        >
                          {r.squeeze}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>

      {(result?.ok || ranked.length > 0) && (
        <AIInterpretation
          page="shorts"
          subject={result?.ok ? result.ticker : "watchlist"}
          data={{
            lookup: result?.ok && resultSqueeze ? {
              ticker: result.ticker,
              name: result.name,
              short_pct_float: result.short_pct_float,
              days_to_cover: result.short_ratio,
              shares_short: result.shares_short,
              delta_month_over_month: resultSqueeze.delta,
              squeeze_score: resultSqueeze.score,
              price: result.price,
              market_cap: result.market_cap,
            } : null,
            watchlist_top10: ranked.slice(0, 10).map((r) => ({
              ticker: r.ticker,
              short_pct_float: r.short_pct_float,
              days_to_cover: r.short_ratio,
              delta: r.deltaShort,
              squeeze: r.squeeze,
            })),
          }}
        />
      )}

      <div className="card card-compact text-[11px] text-text-muted">
        <strong>Data source:</strong> Yahoo Finance (proxy for FINRA biweekly short interest reports). Data is
        latest available — typical lag 5-10 business days from the FINRA settlement date. Changes month-over-month
        compare current <em>sharesShort</em> vs <em>sharesShortPriorMonth</em>.
      </div>
    </div>
  );
}

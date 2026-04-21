"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useTheme } from "next-themes";
import { Plot } from "@/components/plot";
import {
  fetchCongressionalTrades,
  fetchPriceHistoryBatch,
  type CongressionalTrade,
} from "@/lib/api";
import { getChartTheme, getBaseLayout, CHART_HEIGHT } from "@/lib/chart-theme";
import { Metric } from "@/components/ui/metric";
import { AIInterpretation } from "@/components/ai-interpretation";
import { ErrorBanner } from "../_shared/error-banner";


/** Rough midpoint of an amount range like "$1,001 - $15,000". */
function parseAmountMidpoint(raw: string): number {
  if (!raw) return 0;
  const nums = raw.match(/\$?[\d,]+/g);
  if (!nums || nums.length === 0) return 0;
  const vals = nums.map((s) => parseInt(s.replace(/[$,]/g, ""), 10)).filter((n) => Number.isFinite(n));
  if (vals.length === 0) return 0;
  if (vals.length === 1) return vals[0];
  return (vals[0] + vals[vals.length - 1]) / 2;
}

/** Find the close price on the first trading day ON OR AFTER the given ISO date.
 *  Returns null if the nearest match is more than `toleranceDays` after the
 *  requested date — guards against scoring trades whose true date falls before
 *  our price window (e.g., a 3-year-old trade with 2-year history would
 *  otherwise silently match the first bar in our window and produce bogus
 *  alpha). Assumes `bars` is chronologically ascending. */
function priceOnOrAfter(
  bars: { Date: string; Close: number }[],
  isoDate: string,
  toleranceDays = 30,
): number | null {
  if (!bars || bars.length === 0 || !isoDate) return null;
  let lo = 0, hi = bars.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (bars[mid].Date < isoDate) lo = mid + 1;
    else hi = mid;
  }
  if (bars[lo].Date < isoDate) return null;
  const matched = bars[lo].Date.slice(0, 10);
  const gap =
    (new Date(matched + "T00:00:00").getTime() - new Date(isoDate.slice(0, 10) + "T00:00:00").getTime()) / 86400000;
  if (gap > toleranceDays) return null;
  return bars[lo].Close;
}

export default function PoliticalPage() {
  const { resolvedTheme } = useTheme();
  const t = getChartTheme(resolvedTheme === "dark");
  const L = getBaseLayout(t);

  const [year, setYear] = useState(2026);
  const [maxFilings, setMaxFilings] = useState(50);
  const [typeFilter, setTypeFilter] = useState<Set<string>>(new Set(["Purchase", "Sale"]));
  const [memberFilter, setMemberFilter] = useState<Set<string>>(new Set());
  const [tickerSearch, setTickerSearch] = useState("");
  const [selectedMember, setSelectedMember] = useState<string | null>(null);

  const load = useMutation({
    mutationFn: () => fetchCongressionalTrades({ year, maxFilings }),
  });

  const trades = load.data?.data ?? [];

  const { buys, sells, topBought, topSold, topMembers, members } = useMemo(() => {
    const buys = trades.filter((tr) => tr.type === "Purchase");
    const sells = trades.filter((tr) => tr.type === "Sale");
    const count = (arr: CongressionalTrade[], key: keyof CongressionalTrade) => {
      const m = new Map<string, number>();
      for (const row of arr) {
        const v = row[key];
        if (!v) continue;
        const s = String(v);
        m.set(s, (m.get(s) ?? 0) + 1);
      }
      return [...m.entries()].sort((a, b) => b[1] - a[1]);
    };
    const topBought = count(buys, "ticker").slice(0, 12);
    const topSold = count(sells, "ticker").slice(0, 12);
    const topMembers = count(trades, "member").slice(0, 10);
    const members = [...new Set(trades.map((tr) => tr.member))].sort();
    return { buys, sells, topBought, topSold, topMembers, members };
  }, [trades]);

  const leaderboard = useMemo(() => {
    const m = new Map<string, { buys: number; sells: number; volume: number; state: string }>();
    for (const tr of trades) {
      if (!tr.member) continue;
      const rec = m.get(tr.member) ?? { buys: 0, sells: 0, volume: 0, state: tr.state ?? "" };
      if (tr.type === "Purchase") rec.buys++;
      if (tr.type === "Sale") rec.sells++;
      rec.volume += parseAmountMidpoint(tr.amount);
      rec.state = rec.state || tr.state || "";
      m.set(tr.member, rec);
    }
    return [...m.entries()]
      .map(([member, r]) => ({ member, ...r, total: r.buys + r.sells }))
      .sort((a, b) => b.volume - a.volume)
      .slice(0, 25);
  }, [trades]);

  const memberTrades = useMemo(() => {
    if (!selectedMember) return [];
    return trades.filter((tr) => tr.member === selectedMember);
  }, [trades, selectedMember]);

  // Unique tickers bought (Purchase trades) for performance tracking. Limit
  // to the 80 most-traded so the batch fetch stays fast (~4-5s for 80 tickers).
  const trackedTickers = useMemo(() => {
    const freq = new Map<string, number>();
    for (const tr of trades) {
      if (tr.type !== "Purchase" || !tr.ticker || !tr.date || tr.date === "NaT") continue;
      freq.set(tr.ticker, (freq.get(tr.ticker) ?? 0) + 1);
    }
    const sorted = [...freq.entries()].sort((a, b) => b[1] - a[1]).slice(0, 80).map(([tk]) => tk);
    return sorted;
  }, [trades]);

  // Fetch price history for the tracked tickers + SPY. Enabled only when we
  // have trades to score. 2y lookback covers typical PTR windows. Dedupe
  // against SPY in case a politician traded SPY itself.
  const priceBatchQ = useQuery({
    queryKey: ["political-prices", [...trackedTickers].sort().join(",")],
    queryFn: () => {
      const set = new Set(trackedTickers);
      set.add("SPY");
      return fetchPriceHistoryBatch([...set], 504);
    },
    enabled: trackedTickers.length > 0,
    staleTime: 30 * 60_000,
  });

  // Per-politician performance vs SPY: for each Purchase, compare stock return
  // since trade date to SPY return over the same window. Alpha = stock − SPY.
  // Only count politicians with >= 3 scored trades so stats are meaningful.
  const performance = useMemo(() => {
    if (!priceBatchQ.data) return null;
    const priceMap = priceBatchQ.data;
    const spyBars = priceMap["SPY"];
    if (!spyBars || spyBars.length === 0) return null;
    const spyCurrent = spyBars[spyBars.length - 1].Close;

    type Rec = { trades: number; scored: number; winners: number; totalReturn: number; totalAlpha: number; totalVolume: number };
    const byPol = new Map<string, Rec>();

    for (const tr of trades) {
      if (tr.type !== "Purchase" || !tr.ticker || !tr.date || tr.date === "NaT") continue;
      if (!tr.member) continue;
      const rec = byPol.get(tr.member) ?? { trades: 0, scored: 0, winners: 0, totalReturn: 0, totalAlpha: 0, totalVolume: 0 };
      rec.trades++;
      rec.totalVolume += parseAmountMidpoint(tr.amount);

      const tickerBars = priceMap[tr.ticker];
      const tradeIso = tr.date.slice(0, 10);
      const buyPrice = tickerBars ? priceOnOrAfter(tickerBars, tradeIso) : null;
      const spyBuy = priceOnOrAfter(spyBars, tradeIso);
      if (buyPrice != null && buyPrice > 0 && spyBuy != null && spyBuy > 0 && tickerBars && tickerBars.length > 0) {
        const current = tickerBars[tickerBars.length - 1].Close;
        const stockRet = (current - buyPrice) / buyPrice;
        const spyRet = (spyCurrent - spyBuy) / spyBuy;
        const alpha = stockRet - spyRet;
        rec.scored++;
        if (alpha > 0) rec.winners++;
        rec.totalReturn += stockRet;
        rec.totalAlpha += alpha;
      }
      byPol.set(tr.member, rec);
    }

    const rows = [...byPol.entries()]
      .map(([member, r]) => ({
        member,
        trades: r.trades,
        scored: r.scored,
        winRate: r.scored > 0 ? r.winners / r.scored : 0,
        avgReturn: r.scored > 0 ? r.totalReturn / r.scored : 0,
        avgAlpha: r.scored > 0 ? r.totalAlpha / r.scored : 0,
        volume: r.totalVolume,
      }))
      .filter((r) => r.scored >= 3)
      .sort((a, b) => b.avgAlpha - a.avgAlpha);

    const totalScored = rows.reduce((s, r) => s + r.scored, 0);
    const totalPurchases = trades.filter((tr) => tr.type === "Purchase" && tr.ticker && tr.date && tr.date !== "NaT").length;
    return { rows, totalScored, totalPurchases };
  }, [trades, priceBatchQ.data]);

  // Relative performance curves — equal-weighted "politician portfolio" vs SPY.
  // For each date in the SPY timeline, each scored trade contributes its cumulative
  // return since trade date. Portfolio value at T = average(1 + return_i(T)) across
  // all of the politician's Purchase trades that are in-window.
  const perfSeries = useMemo(() => {
    if (!performance || performance.rows.length === 0 || !priceBatchQ.data) return null;
    const priceMap = priceBatchQ.data;
    const spyBars = priceMap["SPY"];
    if (!spyBars || spyBars.length < 2) return null;

    // Timeline from SPY (shared grid).
    const dates = spyBars.map((b) => b.Date.slice(0, 10));
    const spyValues = spyBars.map((b) => b.Close);

    // Top 6 by alpha for the chart — any more and it becomes unreadable.
    const top = performance.rows.slice(0, 6);

    const series = top.map((pol) => {
      const polTrades = trades.filter(
        (tr) =>
          tr.member === pol.member &&
          tr.type === "Purchase" &&
          tr.ticker &&
          tr.date &&
          tr.date !== "NaT",
      );

      // For each trade: precompute entry bar index on SPY timeline and the entry
      // price on the ticker itself.
      const legs: { entryIdx: number; entryPrice: number; tickerBars: { Date: string; Close: number }[] }[] = [];
      for (const tr of polTrades) {
        const tickerBars = priceMap[tr.ticker];
        if (!tickerBars || tickerBars.length === 0) continue;
        const tradeIso = tr.date!.slice(0, 10);
        const entryPrice = priceOnOrAfter(tickerBars, tradeIso);
        if (entryPrice == null || entryPrice <= 0) continue;
        // Find entry index on the SPY timeline so all legs share the same grid.
        let idx = 0;
        for (let k = 0; k < dates.length; k++) {
          if (dates[k] >= tradeIso) { idx = k; break; }
          idx = k;
        }
        legs.push({ entryIdx: idx, entryPrice, tickerBars });
      }

      if (legs.length === 0) return null;
      const values: (number | null)[] = new Array(dates.length).fill(null);
      // Cache: for each leg, map SPY-date-index → ticker close via searching forward.
      for (let i = 0; i < dates.length; i++) {
        const active = legs.filter((l) => l.entryIdx <= i);
        if (active.length === 0) continue;
        let accum = 0;
        let ok = 0;
        for (const leg of active) {
          const tBars = leg.tickerBars;
          // Find ticker bar at or before dates[i] — most recent close up to that point.
          const target = dates[i];
          let lo = 0, hi = tBars.length - 1;
          while (lo < hi) {
            const mid = (lo + hi + 1) >> 1;
            if (tBars[mid].Date.slice(0, 10) <= target) lo = mid;
            else hi = mid - 1;
          }
          const close = tBars[lo]?.Close;
          if (close != null && close > 0) {
            accum += close / leg.entryPrice; // growth factor
            ok++;
          }
        }
        if (ok > 0) values[i] = (accum / ok) * 100; // index 100 at entry
      }
      return { member: pol.member, values };
    }).filter((s): s is { member: string; values: (number | null)[] } => s !== null);

    if (series.length === 0) return null;

    // Find the earliest date any politician's portfolio started.
    let firstActive = dates.length;
    for (const s of series) {
      for (let i = 0; i < s.values.length; i++) {
        if (s.values[i] != null) { firstActive = Math.min(firstActive, i); break; }
      }
    }
    if (firstActive >= dates.length - 1) return null;

    // SPY normalized to same start.
    const spyBase = spyValues[firstActive];
    const spyIndex = spyValues.map((v, i) => (i < firstActive ? null : (v / spyBase) * 100));

    return {
      dates: dates.slice(firstActive),
      spy: spyIndex.slice(firstActive),
      series: series.map((s) => ({ member: s.member, values: s.values.slice(firstActive) })),
    };
  }, [performance, priceBatchQ.data, trades]);

  const filtered = useMemo(() => {
    const searchTickers = tickerSearch
      ? tickerSearch.split(",").map((s) => s.trim().toUpperCase()).filter(Boolean)
      : [];
    return trades.filter((tr) => {
      if (!typeFilter.has(tr.type)) return false;
      if (memberFilter.size > 0 && !memberFilter.has(tr.member)) return false;
      if (searchTickers.length && !searchTickers.includes(tr.ticker)) return false;
      return true;
    });
  }, [trades, typeFilter, memberFilter, tickerSearch]);

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Congressional & Political Trades</h1>
        <p className="text-text-secondary text-sm mt-1">
          House members must disclose trades within 45 days under the STOCK Act. Data parsed from clerk.house.gov PTR filings.
        </p>
      </div>

      <div className="card card-compact">
        <div className="flex items-end gap-3 flex-wrap">
          <div>
            <label className="metric-label">Year</label>
            <select
              value={year}
              onChange={(e) => setYear(parseInt(e.target.value))}
              className="mt-0.5 px-2 py-1.5 border border-border rounded text-sm bg-surface"
            >
              {[2026, 2025, 2024].map((y) => (
                <option key={y} value={y}>
                  {y}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="metric-label">Filings to parse</label>
            <select
              value={maxFilings}
              onChange={(e) => setMaxFilings(parseInt(e.target.value))}
              className="mt-0.5 px-2 py-1.5 border border-border rounded text-sm bg-surface"
            >
              {[25, 50, 100, 200].map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </div>
          <button
            onClick={() => load.mutate()}
            disabled={load.isPending}
            className="px-5 py-2 bg-accent text-white font-semibold rounded-lg hover:bg-accent-hover disabled:opacity-50 text-sm"
          >
            {load.isPending ? "Parsing…" : "Analyze Trades"}
          </button>
        </div>
      </div>

      {load.isError && (
        <ErrorBanner title="Congressional trade parse failed" error={load.error} onRetry={() => load.mutate()} />
      )}

      {load.isPending && (
        <div className="card text-center py-10">
          <div className="inline-block w-6 h-6 border-2 border-accent border-t-transparent rounded-full animate-spin" />
          <div className="text-xs text-text-muted mt-3">Parsing PTR PDFs — this can take 30–90 seconds.</div>
        </div>
      )}

      {load.data && trades.length === 0 && !load.isPending && (
        <div className="card text-sm text-text-muted py-6 px-5">No trade data could be parsed for {year}.</div>
      )}

      {trades.length > 0 && (
        <>
          <AIInterpretation
            page="political"
            subject={`Congressional trades ${year}`}
            data={{
              year,
              total_trades: trades.length,
              unique_tickers: new Set(trades.map((tr) => tr.ticker).filter(Boolean)).size,
              purchases: buys.length,
              sales: sells.length,
              top_bought: topBought.slice(0, 8).map(([tk, n]) => ({ ticker: tk, count: n })),
              top_sold: topSold.slice(0, 8).map(([tk, n]) => ({ ticker: tk, count: n })),
              top_politicians_by_volume: leaderboard.slice(0, 8).map((r) => ({
                member: r.member,
                state: r.state,
                buys: r.buys,
                sells: r.sells,
                estimated_volume_usd: r.volume,
              })),
              performance_vs_spy: performance ? {
                scored_trades: performance.totalScored,
                total_purchases: performance.totalPurchases,
                top_alpha: performance.rows.slice(0, 8).map((r) => ({
                  member: r.member,
                  scored_trades: r.scored,
                  win_rate: r.winRate,
                  avg_return: r.avgReturn,
                  avg_alpha: r.avgAlpha,
                })),
                bottom_alpha: performance.rows.slice(-5).map((r) => ({
                  member: r.member,
                  avg_alpha: r.avgAlpha,
                  win_rate: r.winRate,
                })),
              } : null,
            }}
          />
          <div className="card card-compact">
            <div className="flex flex-wrap gap-6">
              <Metric label="Total Trades" value={trades.length.toLocaleString()} />
              <Metric label="Unique Tickers" value={String(new Set(trades.map((tr) => tr.ticker).filter(Boolean)).size)} />
              <Metric
                label="Purchases"
                value={String(buys.length)}
                delta={`${((buys.length / Math.max(trades.length, 1)) * 100).toFixed(0)}%`}
                deltaType="gain"
              />
              <Metric
                label="Sales"
                value={String(sells.length)}
                delta={`${((sells.length / Math.max(trades.length, 1)) * 100).toFixed(0)}%`}
                deltaType="loss"
              />
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {topBought.length > 0 && (
              <div className="card">
                <Plot
                  data={[
                    {
                      type: "bar" as const,
                      orientation: "h" as const,
                      y: topBought.map(([tk]) => tk),
                      x: topBought.map(([, n]) => n),
                      marker: { color: t.accent },
                      text: topBought.map(([, n]) => String(n)),
                      textposition: "outside" as const,
                    },
                  ]}
                  layout={{
                    ...L,
                    height: CHART_HEIGHT.normal,
                    title: { text: "Most Purchased Tickers", font: { size: 13, color: t.text } },
                    xaxis: { title: { text: "# Buy Transactions" }, gridcolor: t.grid },
                    yaxis: { gridcolor: t.grid, autorange: "reversed" },
                    margin: { l: 60, r: 40, t: 40, b: 40 },
                  }}
                  config={{ displayModeBar: false, responsive: true }}
                  style={{ width: "100%" }}
                />
              </div>
            )}
            {topSold.length > 0 && (
              <div className="card">
                <Plot
                  data={[
                    {
                      type: "bar" as const,
                      orientation: "h" as const,
                      y: topSold.map(([tk]) => tk),
                      x: topSold.map(([, n]) => n),
                      marker: { color: t.loss },
                      text: topSold.map(([, n]) => String(n)),
                      textposition: "outside" as const,
                    },
                  ]}
                  layout={{
                    ...L,
                    height: CHART_HEIGHT.normal,
                    title: { text: "Most Sold Tickers", font: { size: 13, color: t.text } },
                    xaxis: { title: { text: "# Sell Transactions" }, gridcolor: t.grid },
                    yaxis: { gridcolor: t.grid, autorange: "reversed" },
                    margin: { l: 60, r: 40, t: 40, b: 40 },
                  }}
                  config={{ displayModeBar: false, responsive: true }}
                  style={{ width: "100%" }}
                />
              </div>
            )}
          </div>

          {/* Politician Leaderboard */}
          <div className="card">
            <div className="font-semibold text-sm mb-2">Politician Leaderboard — by estimated $ volume</div>
            <div className="text-xs text-text-muted mb-3">
              Volume estimated from the midpoint of each filing&apos;s amount range (PTRs disclose ranges, not exact values).
            </div>
            <div className="overflow-x-auto max-h-[420px]">
              <table className="w-full text-xs font-data">
                <thead className="border-b border-border text-text-muted sticky top-0 bg-surface">
                  <tr>
                    <th className="text-left py-1.5 px-2">#</th>
                    <th className="text-left py-1.5 px-2">Member</th>
                    <th className="text-left py-1.5 px-2">State</th>
                    <th className="text-right py-1.5 px-2">Buys</th>
                    <th className="text-right py-1.5 px-2">Sells</th>
                    <th className="text-right py-1.5 px-2">Total Trades</th>
                    <th className="text-right py-1.5 px-2">Est. Volume</th>
                    <th className="text-left py-1.5 px-2">Drill-down</th>
                  </tr>
                </thead>
                <tbody>
                  {leaderboard.map((row, i) => (
                    <tr key={row.member} className="border-b border-border/50 hover:bg-surface-alt">
                      <td className="py-1 px-2 text-text-muted">{i + 1}</td>
                      <td className="py-1 px-2 font-semibold">{row.member}</td>
                      <td className="py-1 px-2 text-text-muted">{row.state}</td>
                      <td className="py-1 px-2 text-right text-gain">{row.buys}</td>
                      <td className="py-1 px-2 text-right text-loss">{row.sells}</td>
                      <td className="py-1 px-2 text-right">{row.total}</td>
                      <td className="py-1 px-2 text-right font-semibold">
                        ${(row.volume / 1000).toLocaleString(undefined, { maximumFractionDigits: 0 })}K
                      </td>
                      <td className="py-1 px-2">
                        <button
                          onClick={() => setSelectedMember(row.member === selectedMember ? null : row.member)}
                          className="px-2 py-0.5 text-[10px] rounded border border-border hover:bg-accent hover:text-white hover:border-accent"
                        >
                          {selectedMember === row.member ? "Close" : "View"}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {selectedMember && memberTrades.length > 0 && (
              <div className="mt-4 border-t border-border pt-4">
                <div className="text-sm font-semibold mb-2">
                  {selectedMember} — all disclosed trades ({memberTrades.length})
                </div>
                <div className="overflow-x-auto max-h-[320px]">
                  <table className="w-full text-xs font-data">
                    <thead className="border-b border-border text-text-muted sticky top-0 bg-surface">
                      <tr>
                        <th className="text-left py-1.5 px-2">Date</th>
                        <th className="text-left py-1.5 px-2">Ticker</th>
                        <th className="text-left py-1.5 px-2">Type</th>
                        <th className="text-left py-1.5 px-2">Amount</th>
                      </tr>
                    </thead>
                    <tbody>
                      {memberTrades.map((tr, i) => (
                        <tr key={i} className="border-b border-border/50">
                          <td className="py-1 px-2">{tr.date && tr.date !== "NaT" ? tr.date.slice(0, 10) : "—"}</td>
                          <td className="py-1 px-2 font-bold">{tr.ticker}</td>
                          <td className="py-1 px-2">
                            <span
                              className={
                                tr.type === "Purchase"
                                  ? "text-gain"
                                  : tr.type === "Sale"
                                    ? "text-loss"
                                    : "text-text-muted"
                              }
                            >
                              {tr.type}
                            </span>
                          </td>
                          <td className="py-1 px-2">{tr.amount}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </div>

          {/* Performance vs SPY */}
          <div className="card">
            <div className="flex items-baseline justify-between flex-wrap gap-2 mb-2">
              <div>
                <div className="font-semibold text-sm">Performance vs SPY</div>
                <div className="text-xs text-text-muted">
                  For each Purchase, compares the stock&apos;s return from trade date to today against SPY over the
                  same window. Alpha = stock return − SPY return. Only politicians with ≥ 3 scored trades shown.
                </div>
              </div>
              {performance && (
                <div className="text-[11px] text-text-muted">
                  {performance.totalScored.toLocaleString()} / {performance.totalPurchases.toLocaleString()} purchases scored
                </div>
              )}
            </div>

            {priceBatchQ.isPending && (
              <div className="text-center py-6">
                <div className="inline-block w-5 h-5 border-2 border-accent border-t-transparent rounded-full animate-spin" />
                <div className="text-xs text-text-muted mt-2">Fetching 2-year price history for top {trackedTickers.length} tickers + SPY…</div>
              </div>
            )}

            {priceBatchQ.isError && (
              <div className="text-sm text-loss py-4">
                Price batch failed: {(priceBatchQ.error as Error)?.message ?? "unknown error"}
              </div>
            )}

            {performance && performance.rows.length > 0 && (
              <>
                {/* Relative performance — equal-weighted portfolio vs SPY */}
                {perfSeries && (
                  <Plot
                    data={[
                      ...perfSeries.series.map((s, i) => ({
                        type: "scatter" as const,
                        mode: "lines" as const,
                        name: s.member,
                        x: perfSeries.dates,
                        y: s.values,
                        line: {
                          color: [t.accent, t.gain, t.spot, t.hv20, t.hv60, t.loss][i % 6],
                          width: 2,
                        },
                        connectgaps: false,
                      })),
                      {
                        type: "scatter" as const,
                        mode: "lines" as const,
                        name: "SPY",
                        x: perfSeries.dates,
                        y: perfSeries.spy,
                        line: { color: t.muted, width: 2.5, dash: "dash" as const },
                      },
                    ]}
                    layout={{
                      ...L,
                      height: CHART_HEIGHT.tall,
                      title: { text: "Portfolio value — each politician's Purchase trades vs SPY (indexed to 100 at first trade)", font: { size: 12, color: t.text } },
                      yaxis: { title: { text: "Indexed value" }, gridcolor: t.grid },
                      xaxis: { gridcolor: t.grid },
                      hovermode: "x unified" as const,
                      legend: { orientation: "h" as const, y: -0.18 },
                      margin: { l: 60, r: 20, t: 50, b: 60 },
                      shapes: [
                        { type: "line", x0: 0, x1: 1, xref: "paper", y0: 100, y1: 100, line: { color: t.muted, width: 0.5, dash: "dot" as const } },
                      ],
                    }}
                    config={{ displayModeBar: false, responsive: true }}
                    style={{ width: "100%" }}
                  />
                )}

                <Plot
                  data={[
                    {
                      type: "bar" as const,
                      orientation: "h" as const,
                      y: performance.rows.slice(0, 20).map((r) => r.member),
                      x: performance.rows.slice(0, 20).map((r) => r.avgAlpha * 100),
                      marker: {
                        color: performance.rows.slice(0, 20).map((r) => (r.avgAlpha > 0 ? t.gain : t.loss)),
                      },
                      text: performance.rows.slice(0, 20).map((r) => `${r.avgAlpha >= 0 ? "+" : ""}${(r.avgAlpha * 100).toFixed(1)}%`),
                      textposition: "outside" as const,
                      hovertemplate: "%{y}<br>Alpha: %{x:.1f}%<extra></extra>",
                    },
                  ]}
                  layout={{
                    ...L,
                    height: CHART_HEIGHT.tall,
                    title: { text: "Average alpha per trade — top 20 politicians", font: { size: 12, color: t.text } },
                    xaxis: { title: { text: "Average alpha per trade (%)" }, gridcolor: t.grid, zeroline: true, zerolinecolor: t.muted },
                    yaxis: { gridcolor: t.grid, autorange: "reversed" },
                    margin: { l: 200, r: 80, t: 50, b: 40 },
                  }}
                  config={{ displayModeBar: false, responsive: true }}
                  style={{ width: "100%" }}
                />
                <div className="overflow-x-auto max-h-[400px] mt-3">
                  <table className="w-full text-xs font-data">
                    <thead className="border-b border-border text-text-muted sticky top-0 bg-surface">
                      <tr>
                        <th className="text-left py-1.5 px-2">#</th>
                        <th className="text-left py-1.5 px-2">Member</th>
                        <th className="text-right py-1.5 px-2">Scored</th>
                        <th className="text-right py-1.5 px-2">Win rate</th>
                        <th className="text-right py-1.5 px-2">Avg return</th>
                        <th className="text-right py-1.5 px-2">Avg alpha vs SPY</th>
                      </tr>
                    </thead>
                    <tbody>
                      {performance.rows.map((r, i) => (
                        <tr key={r.member} className="border-b border-border/50 hover:bg-surface-alt">
                          <td className="py-1 px-2 text-text-muted">{i + 1}</td>
                          <td className="py-1 px-2 font-semibold">{r.member}</td>
                          <td className="py-1 px-2 text-right">
                            {r.scored}{r.scored < r.trades && <span className="text-text-muted"> / {r.trades}</span>}
                          </td>
                          <td className="py-1 px-2 text-right">{(r.winRate * 100).toFixed(0)}%</td>
                          <td className={`py-1 px-2 text-right ${r.avgReturn >= 0 ? "text-gain" : "text-loss"}`}>
                            {r.avgReturn >= 0 ? "+" : ""}{(r.avgReturn * 100).toFixed(1)}%
                          </td>
                          <td className={`py-1 px-2 text-right font-semibold ${r.avgAlpha >= 0 ? "text-gain" : "text-loss"}`}>
                            {r.avgAlpha >= 0 ? "+" : ""}{(r.avgAlpha * 100).toFixed(1)}%
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="text-[11px] text-text-muted mt-2">
                  <strong>Caveat:</strong> we score only Purchase trades where the ticker is in the top {trackedTickers.length} most-traded
                  names AND the trade date is within our 2-year price window. Return is cumulative from trade date to today — a trade made
                  2 years ago carries more weight than one made last week. Treat directionally, not as risk-adjusted performance.
                </div>
              </>
            )}

            {performance && performance.rows.length === 0 && !priceBatchQ.isPending && (
              <div className="text-xs text-text-muted py-4">
                No politician had ≥ 3 scored Purchase trades. Try loading more filings.
              </div>
            )}
          </div>

          {topMembers.length > 0 && (
            <div className="card">
              <Plot
                data={[
                  {
                    type: "bar" as const,
                    orientation: "h" as const,
                    y: topMembers.map(([m]) => m),
                    x: topMembers.map(([, n]) => n),
                    marker: { color: t.spot },
                    text: topMembers.map(([, n]) => String(n)),
                    textposition: "outside" as const,
                  },
                ]}
                layout={{
                  ...L,
                  height: CHART_HEIGHT.normal,
                  title: { text: "Most Active Members (# Trades)", font: { size: 13, color: t.text } },
                  xaxis: { title: { text: "# Trades" }, gridcolor: t.grid },
                  yaxis: { gridcolor: t.grid, autorange: "reversed" },
                  margin: { l: 200, r: 40, t: 40, b: 40 },
                }}
                config={{ displayModeBar: false, responsive: true }}
                style={{ width: "100%" }}
              />
            </div>
          )}

          <div className="card">
            <div className="font-semibold text-sm mb-2">Trade Details</div>
            <div className="flex flex-wrap items-end gap-3 mb-3">
              <div>
                <label className="metric-label">Type</label>
                <div className="flex gap-1 mt-0.5">
                  {["Purchase", "Sale"].map((kind) => (
                    <button
                      key={kind}
                      onClick={() =>
                        setTypeFilter((prev) => {
                          const n = new Set(prev);
                          if (n.has(kind)) n.delete(kind);
                          else n.add(kind);
                          return n;
                        })
                      }
                      className={`px-2 py-1 text-xs rounded border ${
                        typeFilter.has(kind)
                          ? "bg-accent text-white border-accent"
                          : "border-border text-text-muted hover:bg-surface-alt"
                      }`}
                    >
                      {kind}
                    </button>
                  ))}
                </div>
              </div>
              <div className="flex-1 min-w-[220px]">
                <label className="metric-label">Member filter ({memberFilter.size})</label>
                <select
                  multiple
                  value={[...memberFilter]}
                  onChange={(e) => {
                    const selected = new Set(Array.from(e.target.selectedOptions).map((o) => o.value));
                    setMemberFilter(selected);
                  }}
                  className="mt-0.5 w-full h-[80px] px-2 py-1 border border-border rounded text-xs bg-surface font-data"
                >
                  {members.map((m) => (
                    <option key={m} value={m}>
                      {m}
                    </option>
                  ))}
                </select>
              </div>
              <div className="flex-1 min-w-[200px]">
                <label className="metric-label">Ticker search</label>
                <input
                  type="text"
                  value={tickerSearch}
                  onChange={(e) => setTickerSearch(e.target.value.toUpperCase())}
                  placeholder="AAPL, TSLA"
                  className="mt-0.5 w-full px-2 py-1.5 border border-border rounded text-sm bg-surface font-data"
                />
              </div>
            </div>
            <div className="overflow-x-auto max-h-[500px]">
              <table className="w-full text-xs font-data">
                <thead className="border-b border-border text-text-muted sticky top-0 bg-surface">
                  <tr>
                    <th className="text-left py-1.5 px-2">Member</th>
                    <th className="text-left py-1.5 px-2">State</th>
                    <th className="text-left py-1.5 px-2">Ticker</th>
                    <th className="text-left py-1.5 px-2">Type</th>
                    <th className="text-left py-1.5 px-2">Trade Date</th>
                    <th className="text-left py-1.5 px-2">Amount</th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.slice(0, 500).map((tr, i) => (
                    <tr key={i} className="border-b border-border/50 hover:bg-surface-alt">
                      <td className="py-1 px-2 font-semibold">{tr.member ?? "—"}</td>
                      <td className="py-1 px-2 text-text-muted">{tr.state ?? ""}</td>
                      <td className="py-1 px-2">{tr.ticker}</td>
                      <td className="py-1 px-2">
                        <span
                          className={
                            tr.type === "Purchase"
                              ? "text-gain"
                              : tr.type === "Sale"
                                ? "text-loss"
                                : "text-text-muted"
                          }
                        >
                          {tr.type}
                        </span>
                      </td>
                      <td className="py-1 px-2">{tr.date && tr.date !== "NaT" ? tr.date.slice(0, 10) : "—"}</td>
                      <td className="py-1 px-2">{tr.amount}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {filtered.length > 500 && (
                <div className="text-xs text-text-muted p-2">
                  Showing first 500 of {filtered.length.toLocaleString()} filtered rows.
                </div>
              )}
            </div>
          </div>

        </>
      )}

      <div className="text-xs text-text-muted px-3 py-2 border border-border rounded">
        <b>Note:</b> Senate trades are filed separately via the{" "}
        <a
          className="text-accent hover:underline"
          href="https://efdsearch.senate.gov/search/"
          target="_blank"
          rel="noreferrer"
        >
          Senate eFD portal
        </a>
        . House data shown here is parsed directly from official PTR filings.
      </div>
    </div>
  );
}

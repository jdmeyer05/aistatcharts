"use client";

import { useMemo, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTheme } from "next-themes";
import dynamic from "next/dynamic";
import {
  fetchCongressionalTrades,
  type CongressionalTrade,
} from "@/lib/api";
import { getChartTheme, getBaseLayout, CHART_HEIGHT } from "@/lib/chart-theme";
import { Metric } from "@/components/ui/metric";

const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

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

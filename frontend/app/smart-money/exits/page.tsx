"use client";

import { useMemo, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTheme } from "next-themes";
import dynamic from "next/dynamic";
import {
  fetchCongressionalTrades,
  fetchRecent13D,
  fetchInsiderTransactions,
  type CongressionalTrade,
} from "@/lib/api";
import { getChartTheme, getBaseLayout, CHART_HEIGHT } from "@/lib/chart-theme";
import { Metric } from "@/components/ui/metric";
import { AIInterpretation } from "@/components/ai-interpretation";
import { fmtBn, shortDate } from "../_shared/utils";
import { ErrorBanner } from "../_shared/error-banner";

const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

function netCongressionalByTicker(trades: CongressionalTrade[]) {
  const m = new Map<string, { buys: number; sells: number; members: Set<string> }>();
  for (const tr of trades) {
    if (!tr.ticker) continue;
    const rec = m.get(tr.ticker) ?? { buys: 0, sells: 0, members: new Set<string>() };
    if (tr.type === "Purchase") rec.buys++;
    else if (tr.type === "Sale") rec.sells++;
    rec.members.add(tr.member ?? "");
    m.set(tr.ticker, rec);
  }
  return [...m.entries()]
    .map(([ticker, r]) => ({
      ticker,
      buys: r.buys,
      sells: r.sells,
      net: r.buys - r.sells,
      distinctMembers: r.members.size,
    }))
    .filter((r) => r.sells > 0);
}

export default function ExitsPage() {
  const { resolvedTheme } = useTheme();
  const t = getChartTheme(resolvedTheme === "dark");
  const L = getBaseLayout(t);

  const [year, setYear] = useState(2026);
  const [days13d, setDays13d] = useState(180);
  const [insiderTicker, setInsiderTicker] = useState("");

  const congressLoad = useMutation({
    mutationFn: () => fetchCongressionalTrades({ year, maxFilings: 100 }),
  });

  const activistLoad = useMutation({
    mutationFn: (d: number) => fetchRecent13D(d),
  });

  const insiderLoad = useMutation({
    mutationFn: () => fetchInsiderTransactions(insiderTicker.toUpperCase()),
  });

  const congressTrades = congressLoad.data?.data ?? [];
  const activistFilings = activistLoad.data?.data ?? [];

  const congressionalExits = useMemo(() => {
    const all = netCongressionalByTicker(congressTrades);
    return all
      .filter((r) => r.net < 0) // more sells than buys
      .sort((a, b) => a.net - b.net)
      .slice(0, 15);
  }, [congressTrades]);

  const activistExitCandidates = useMemo(() => {
    // 13D amendments often = stake changes. Without parsed text, surface
    // amendments as the "watch these" list. Filter for amendments only.
    return activistFilings
      .filter((f) => !f.is_new)
      .sort((a, b) => b.filed.localeCompare(a.filed))
      .slice(0, 20);
  }, [activistFilings]);

  const insiderExits = useMemo(() => {
    if (!insiderLoad.data) return { sells: [], netValue: 0, sellCount: 0, buyCount: 0 };
    let buyCount = 0;
    let sellCount = 0;
    let netValue = 0;
    const sells: {
      date: string;
      insider: string;
      position: string;
      shares: number;
      value: number;
    }[] = [];
    for (const raw of insiderLoad.data.data) {
      const r = raw as Record<string, unknown>;
      // yfinance puts the action in Text, leaving Transaction blank — match both.
      const haystack = `${String(r.Transaction ?? "")} ${String(r.Text ?? "")}`.toLowerCase();
      const value = Number(r.Value ?? 0) || 0;
      const date = String(r["Start Date"] ?? "").slice(0, 10);
      const insider = String(r.Insider ?? "");
      const position = String(r.Position ?? "");
      const shares = Number(r.Shares ?? 0) || 0;
      const isBuy = haystack.includes("purchase") || haystack.includes("acquire");
      const isSell = haystack.includes("sale") || haystack.includes("dispose");
      if (isBuy) {
        buyCount++;
        netValue += value;
      }
      if (isSell) {
        sellCount++;
        netValue -= value;
        if (date) sells.push({ date, insider, position, shares, value });
      }
    }
    return { sells: sells.sort((a, b) => b.date.localeCompare(a.date)), netValue, sellCount, buyCount };
  }, [insiderLoad.data]);

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Exit Signals</h1>
        <p className="text-text-secondary text-sm mt-1">
          The inverse tracker — where smart money is <em>leaving</em>. Heavy insider selling, net congressional
          selling, and 13D amendments often signal top-of-cycle distribution before a downward move.
        </p>
      </div>

      <div className="card border-l-4 border-l-warn">
        <div className="text-xs font-bold uppercase tracking-wider text-warn mb-1">Read the signal carefully</div>
        <p className="text-sm">
          Not every insider sale is bearish — options exercises, 10b5-1 plans, and estate planning all produce routine
          sells. The tells are: <strong>cluster sells</strong> by multiple execs, sales with no offsetting buys over
          multiple quarters, and sells coinciding with negative congressional flow. Any single dimension is noise.
        </p>
      </div>

      {/* Congressional exits */}
      <div className="card card-compact">
        <div className="flex items-end gap-3 flex-wrap">
          <div>
            <label className="metric-label">Congressional year</label>
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
          <button
            onClick={() => congressLoad.mutate()}
            disabled={congressLoad.isPending}
            className="px-4 py-2 bg-accent text-white font-semibold rounded-lg hover:bg-accent-hover disabled:opacity-50 text-sm"
          >
            {congressLoad.isPending ? "Parsing…" : "Load Congressional"}
          </button>
        </div>
      </div>

      {congressLoad.isError && (
        <ErrorBanner title="Congressional load failed" error={congressLoad.error} onRetry={() => congressLoad.mutate()} />
      )}

      {congressLoad.isPending && (
        <div className="card text-center py-6">
          <div className="inline-block w-5 h-5 border-2 border-accent border-t-transparent rounded-full animate-spin" />
          <div className="text-xs text-text-muted mt-2">Parsing PTR PDFs — 30-90s.</div>
        </div>
      )}

      {congressionalExits.length > 0 && (
        <div className="card">
          <Plot
            data={[
              {
                type: "bar" as const,
                orientation: "h" as const,
                y: congressionalExits.map((r) => r.ticker),
                x: congressionalExits.map((r) => r.net),
                marker: { color: t.loss },
                text: congressionalExits.map((r) => `${r.buys}B / ${r.sells}S · ${r.distinctMembers} members`),
                textposition: "outside" as const,
              },
            ]}
            layout={{
              ...L,
              height: CHART_HEIGHT.tall,
              title: {
                text: `Congressional Exit Pressure — most-sold tickers ${year}`,
                font: { size: 13, color: t.text },
              },
              xaxis: { title: { text: "Net (buys − sells, negative = exit pressure)" }, gridcolor: t.grid },
              yaxis: { gridcolor: t.grid, autorange: "reversed" },
              margin: { l: 70, r: 180, t: 40, b: 40 },
            }}
            config={{ displayModeBar: false, responsive: true }}
            style={{ width: "100%" }}
          />
        </div>
      )}

      {/* Activist exits (13D amendments) */}
      <div className="card card-compact">
        <div className="flex items-end gap-3 flex-wrap">
          <div className="min-w-[200px]">
            <label className="metric-label">13D lookback: {days13d} days</label>
            <input
              type="range"
              min={30}
              max={365}
              value={days13d}
              onChange={(e) => setDays13d(parseInt(e.target.value))}
              className="w-full mt-1"
            />
          </div>
          <button
            onClick={() => activistLoad.mutate(days13d)}
            disabled={activistLoad.isPending}
            className="px-4 py-2 bg-accent text-white font-semibold rounded-lg hover:bg-accent-hover disabled:opacity-50 text-sm"
          >
            {activistLoad.isPending ? "Loading…" : "Load 13D Amendments"}
          </button>
        </div>
      </div>

      {activistLoad.isError && (
        <ErrorBanner title="13D load failed" error={activistLoad.error} onRetry={() => activistLoad.mutate(days13d)} />
      )}

      {activistExitCandidates.length > 0 && (
        <div className="card">
          <div className="font-semibold text-sm mb-2">
            Recent 13D Amendments (watch for stake reductions)
          </div>
          <div className="text-xs text-text-muted mb-3">
            Amendments update existing 13D positions — they can signal stake increases OR exits. The filing URL
            contains the direction; review each for stake change language.
          </div>
          <div className="overflow-x-auto max-h-[400px]">
            <table className="w-full text-xs font-data">
              <thead className="border-b border-border text-text-muted sticky top-0 bg-surface">
                <tr>
                  <th className="text-left py-1.5 px-2">Filed</th>
                  <th className="text-left py-1.5 px-2">Ticker</th>
                  <th className="text-left py-1.5 px-2">Target</th>
                  <th className="text-left py-1.5 px-2">Activist</th>
                  <th className="text-left py-1.5 px-2">Filing</th>
                </tr>
              </thead>
              <tbody>
                {activistExitCandidates.map((row, i) => (
                  <tr key={i} className="border-b border-border/50 hover:bg-surface-alt">
                    <td className="py-1 px-2">{shortDate(row.filed)}</td>
                    <td className="py-1 px-2 font-bold">{row.ticker}</td>
                    <td className="py-1 px-2">{row.target}</td>
                    <td className="py-1 px-2">{row.activist}</td>
                    <td className="py-1 px-2">
                      {row.url ? (
                        <a href={row.url} target="_blank" rel="noreferrer" className="text-accent hover:underline">
                          View
                        </a>
                      ) : (
                        "—"
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Insider exits per-ticker */}
      <div className="card card-compact">
        <div className="flex items-end gap-3 flex-wrap">
          <div className="flex-1 min-w-[240px]">
            <label className="metric-label">Per-ticker insider exits</label>
            <input
              value={insiderTicker}
              onChange={(e) => setInsiderTicker(e.target.value.toUpperCase())}
              onKeyDown={(e) => e.key === "Enter" && insiderTicker && insiderLoad.mutate()}
              placeholder="Ticker"
              className="mt-0.5 w-full px-2 py-1.5 border border-border rounded text-sm bg-surface font-data"
            />
          </div>
          <button
            onClick={() => insiderTicker && insiderLoad.mutate()}
            disabled={!insiderTicker || insiderLoad.isPending}
            className="px-4 py-2 bg-accent text-white font-semibold rounded-lg hover:bg-accent-hover disabled:opacity-50 text-sm"
          >
            {insiderLoad.isPending ? "Loading…" : "Check Insider Flow"}
          </button>
        </div>
      </div>

      {insiderLoad.isError && (
        <ErrorBanner title="Insider flow fetch failed" error={insiderLoad.error} onRetry={() => insiderLoad.mutate()} />
      )}

      {insiderLoad.isSuccess && insiderLoad.data && (
        <div className="card">
          <div className="font-semibold text-sm mb-2">
            Insider Exit Flow — {insiderTicker.toUpperCase()}
          </div>
          <div className="flex flex-wrap gap-6 mb-3">
            <Metric label="Insider Buys" value={String(insiderExits.buyCount)} deltaType="gain" />
            <Metric label="Insider Sells" value={String(insiderExits.sellCount)} deltaType="loss" />
            <Metric
              label="Net $"
              value={fmtBn(insiderExits.netValue)}
              deltaType={insiderExits.netValue > 0 ? "gain" : insiderExits.netValue < 0 ? "loss" : "neutral"}
            />
            <Metric
              label="Exit Pressure"
              value={
                insiderExits.netValue < -1e7
                  ? "Strong"
                  : insiderExits.netValue < 0
                    ? "Moderate"
                    : "None"
              }
              deltaType={insiderExits.netValue < 0 ? "loss" : "neutral"}
            />
          </div>
          {insiderExits.sells.length > 0 ? (
            <div className="overflow-x-auto max-h-[360px]">
              <table className="w-full text-xs font-data">
                <thead className="border-b border-border text-text-muted sticky top-0 bg-surface">
                  <tr>
                    <th className="text-left py-1.5 px-2">Date</th>
                    <th className="text-left py-1.5 px-2">Insider</th>
                    <th className="text-left py-1.5 px-2">Position</th>
                    <th className="text-right py-1.5 px-2">Shares Sold</th>
                    <th className="text-right py-1.5 px-2">Value</th>
                  </tr>
                </thead>
                <tbody>
                  {insiderExits.sells.slice(0, 50).map((r, i) => (
                    <tr key={i} className="border-b border-border/50 hover:bg-surface-alt">
                      <td className="py-1 px-2">{r.date}</td>
                      <td className="py-1 px-2 font-semibold">{r.insider || "—"}</td>
                      <td className="py-1 px-2 text-text-muted">{r.position || "—"}</td>
                      <td className="py-1 px-2 text-right">{r.shares ? r.shares.toLocaleString() : "—"}</td>
                      <td className="py-1 px-2 text-right text-loss">{fmtBn(r.value)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="text-xs text-text-muted">No insider sells for this ticker.</div>
          )}
        </div>
      )}

      {(congressionalExits.length > 0 || activistExitCandidates.length > 0 || insiderLoad.isSuccess) && (
        <AIInterpretation
          page="exits"
          data={{
            congressional_exits: congressionalExits.slice(0, 15).map((r) => ({
              ticker: r.ticker,
              buys: r.buys,
              sells: r.sells,
              net: r.net,
              distinct_members: r.distinctMembers,
            })),
            recent_13d_amendments: activistExitCandidates.slice(0, 12).map((row) => ({
              filed: row.filed,
              ticker: row.ticker,
              target: row.target,
              activist: row.activist,
            })),
            per_ticker_insider: insiderLoad.isSuccess && insiderTicker ? {
              ticker: insiderTicker.toUpperCase(),
              buys: insiderExits.buyCount,
              sells: insiderExits.sellCount,
              net_value: insiderExits.netValue,
              recent_sells: insiderExits.sells.slice(0, 6),
            } : null,
          }}
        />
      )}

      <div className="card card-compact text-xs text-text-muted">
        <strong>Coming next:</strong> 13F removals (fund liquidations q/q), retail-sentiment overlay (dumb money
        buying what smart money exits), and cluster-exit scoring. These require the 13F history worker and retail
        sentiment pipeline — both scaffolded for Phase 2.
      </div>
    </div>
  );
}

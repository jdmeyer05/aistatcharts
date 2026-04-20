"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTheme } from "next-themes";
import { Plot } from "@/components/plot";
import { fetchBuybacks } from "@/lib/api";
import { getChartTheme, getBaseLayout, CHART_HEIGHT } from "@/lib/chart-theme";
import { Metric } from "@/components/ui/metric";
import { AIInterpretation } from "@/components/ai-interpretation";
import { fmtBn } from "../_shared/utils";


export default function BuybacksPage() {
  const { resolvedTheme } = useTheme();
  const t = getChartTheme(resolvedTheme === "dark");
  const L = getBaseLayout(t);

  const [ticker, setTicker] = useState("");
  const load = useMutation({
    mutationFn: (tk: string) => fetchBuybacks(tk.toUpperCase()),
  });

  const d = load.data;

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Buybacks & Capital Returns</h1>
        <p className="text-text-secondary text-sm mt-1">
          Share repurchases and dividends returned to shareholders — derived from the company&apos;s 10-Q/10-K
          cashflow statements. The purest smart-money signal: the people with the most information about the
          business are allocating its capital into that business.
        </p>
      </div>

      <div className="card card-compact">
        <div className="flex items-end gap-3 flex-wrap">
          <div className="flex-1 min-w-[240px]">
            <label className="metric-label">Ticker</label>
            <input
              value={ticker}
              onChange={(e) => setTicker(e.target.value.toUpperCase())}
              onKeyDown={(e) => e.key === "Enter" && ticker && load.mutate(ticker)}
              placeholder="AAPL, MSFT, META"
              className="mt-0.5 w-full px-2 py-1.5 border border-border rounded text-sm bg-surface font-data"
            />
          </div>
          <button
            onClick={() => ticker && load.mutate(ticker)}
            disabled={!ticker || load.isPending}
            className="px-5 py-2 bg-accent text-white font-semibold rounded-lg hover:bg-accent-hover disabled:opacity-50 text-sm"
          >
            {load.isPending ? "Loading…" : "Load capital returns"}
          </button>
        </div>
      </div>

      {load.isPending && (
        <div className="card text-center py-8">
          <div className="inline-block w-5 h-5 border-2 border-accent border-t-transparent rounded-full animate-spin" />
        </div>
      )}

      {d && !d.ok && (
        <div className="card text-sm text-loss">Lookup failed: {d.error ?? "unknown error"}</div>
      )}

      {d && d.ok && (
        <>
          <div className="card card-compact">
            <div className="flex flex-wrap gap-6 items-center">
              <div>
                <div className="text-sm font-bold">{d.name ?? d.ticker}</div>
                <div className="text-xs text-text-muted">
                  Market cap {d.market_cap != null ? fmtBn(d.market_cap) : "—"}
                </div>
              </div>
              <Metric
                label="TTM Buybacks"
                value={d.ttm_repurchase != null ? fmtBn(d.ttm_repurchase) : "—"}
                deltaType={d.ttm_repurchase && d.ttm_repurchase > 0 ? "gain" : "neutral"}
              />
              <Metric
                label="TTM Dividends"
                value={d.ttm_dividend != null ? fmtBn(d.ttm_dividend) : "—"}
              />
              <Metric
                label="Buyback yield"
                value={d.buyback_yield != null ? `${(d.buyback_yield * 100).toFixed(2)}%` : "—"}
                deltaType={d.buyback_yield && d.buyback_yield > 0.02 ? "gain" : "neutral"}
              />
              <Metric
                label="Dividend yield"
                value={d.dividend_yield != null ? `${(d.dividend_yield * 100).toFixed(2)}%` : "—"}
              />
              <Metric
                label="Total shareholder yield"
                value={d.total_shareholder_yield != null ? `${(d.total_shareholder_yield * 100).toFixed(2)}%` : "—"}
                deltaType={d.total_shareholder_yield && d.total_shareholder_yield > 0.04 ? "gain" : "neutral"}
              />
            </div>
          </div>

          {/* Annual trend */}
          {d.annual && d.annual.length > 0 && (
            <div className="card">
              <div className="text-sm font-semibold mb-2">Annual capital returned</div>
              <Plot
                data={[
                  {
                    type: "bar" as const,
                    name: "Repurchases",
                    x: [...d.annual].reverse().map((p) => p.period),
                    y: [...d.annual].reverse().map((p) => (p.repurchase ?? 0) / 1e9),
                    marker: { color: t.accent },
                    text: [...d.annual].reverse().map((p) => (p.repurchase != null ? fmtBn(p.repurchase) : "—")),
                    textposition: "outside" as const,
                  },
                  {
                    type: "bar" as const,
                    name: "Dividends",
                    x: [...d.annual].reverse().map((p) => p.period),
                    y: [...d.annual].reverse().map((p) => (p.dividend ?? 0) / 1e9),
                    marker: { color: t.gain },
                    text: [...d.annual].reverse().map((p) => (p.dividend != null ? fmtBn(p.dividend) : "—")),
                    textposition: "outside" as const,
                  },
                ]}
                layout={{
                  ...L,
                  height: CHART_HEIGHT.normal,
                  barmode: "group" as const,
                  yaxis: { title: { text: "$B" }, gridcolor: t.grid },
                  xaxis: { gridcolor: t.grid },
                  legend: { orientation: "h" as const, y: -0.18 },
                  margin: { l: 50, r: 20, t: 10, b: 50 },
                }}
                config={{ displayModeBar: false, responsive: true }}
                style={{ width: "100%" }}
              />
            </div>
          )}

          {/* Quarterly trend */}
          {d.quarterly && d.quarterly.length > 0 && (
            <div className="card">
              <div className="text-sm font-semibold mb-2">Quarterly capital returned</div>
              <Plot
                data={[
                  {
                    type: "bar" as const,
                    name: "Repurchases",
                    x: [...d.quarterly].reverse().map((p) => p.period),
                    y: [...d.quarterly].reverse().map((p) => (p.repurchase ?? 0) / 1e9),
                    marker: { color: t.accent },
                  },
                  {
                    type: "bar" as const,
                    name: "Dividends",
                    x: [...d.quarterly].reverse().map((p) => p.period),
                    y: [...d.quarterly].reverse().map((p) => (p.dividend ?? 0) / 1e9),
                    marker: { color: t.gain },
                  },
                ]}
                layout={{
                  ...L,
                  height: CHART_HEIGHT.normal,
                  barmode: "stack" as const,
                  yaxis: { title: { text: "$B" }, gridcolor: t.grid },
                  xaxis: { gridcolor: t.grid },
                  legend: { orientation: "h" as const, y: -0.18 },
                  margin: { l: 50, r: 20, t: 10, b: 50 },
                }}
                config={{ displayModeBar: false, responsive: true }}
                style={{ width: "100%" }}
              />
            </div>
          )}

          {/* Detail tables */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="card">
              <div className="text-sm font-semibold mb-2">Annual detail</div>
              <table className="w-full text-xs font-data">
                <thead className="border-b border-border text-text-muted">
                  <tr>
                    <th className="text-left py-1.5 px-2">Period</th>
                    <th className="text-right py-1.5 px-2">Repurchases</th>
                    <th className="text-right py-1.5 px-2">Dividends</th>
                  </tr>
                </thead>
                <tbody>
                  {(d.annual ?? []).map((p) => (
                    <tr key={p.period} className="border-b border-border/50">
                      <td className="py-1 px-2">{p.period}</td>
                      <td className="py-1 px-2 text-right">{p.repurchase != null ? fmtBn(p.repurchase) : "—"}</td>
                      <td className="py-1 px-2 text-right">{p.dividend != null ? fmtBn(p.dividend) : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="card">
              <div className="text-sm font-semibold mb-2">Quarterly detail</div>
              <table className="w-full text-xs font-data">
                <thead className="border-b border-border text-text-muted">
                  <tr>
                    <th className="text-left py-1.5 px-2">Period</th>
                    <th className="text-right py-1.5 px-2">Repurchases</th>
                    <th className="text-right py-1.5 px-2">Dividends</th>
                  </tr>
                </thead>
                <tbody>
                  {(d.quarterly ?? []).map((p) => (
                    <tr key={p.period} className="border-b border-border/50">
                      <td className="py-1 px-2">{p.period}</td>
                      <td className="py-1 px-2 text-right">{p.repurchase != null ? fmtBn(p.repurchase) : "—"}</td>
                      <td className="py-1 px-2 text-right">{p.dividend != null ? fmtBn(p.dividend) : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <AIInterpretation
            page="buybacks"
            subject={d.ticker}
            data={{
              ticker: d.ticker,
              name: d.name,
              market_cap: d.market_cap,
              ttm_buyback: d.ttm_repurchase,
              ttm_dividend: d.ttm_dividend,
              buyback_yield: d.buyback_yield,
              dividend_yield: d.dividend_yield,
              total_shareholder_yield: d.total_shareholder_yield,
              quarterly: (d.quarterly ?? []).slice(0, 8),
              annual: (d.annual ?? []).slice(0, 5),
            }}
          />
        </>
      )}

      <div className="card card-compact text-[11px] text-text-muted">
        <strong>Data source:</strong> Yahoo Finance cashflow statement — &ldquo;Repurchase Of Capital Stock&rdquo;
        and &ldquo;Cash Dividends Paid&rdquo; lines aggregated from 10-Q/10-K filings. Buyback yield = TTM
        repurchases ÷ current market cap. A total shareholder yield above 4% is generous; above 8% is exceptional.
      </div>
    </div>
  );
}

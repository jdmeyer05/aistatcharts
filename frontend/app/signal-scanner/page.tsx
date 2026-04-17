"use client";

import { useState, useMemo } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTheme } from "next-themes";
import { fetchPriceHistoryBatch, fetchSnapshot } from "@/lib/api";
import { getChartTheme, getBaseLayout } from "@/lib/chart-theme";
import { Metric } from "@/components/ui/metric";
import dynamic from "next/dynamic";

const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

const TABS = ["Momentum", "Mean Reversion", "Composite Ranking"];

const UNIVERSES: Record<string, string[]> = {
  "Sectors": ["XLK", "XLF", "XLE", "XLV", "XLI", "XLU", "XLP", "XLY", "XLC", "XLB", "XLRE"],
  "Mega Caps": ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "JPM", "V", "UNH"],
  "Cross-Asset": ["SPY", "QQQ", "IWM", "TLT", "GLD", "USO", "UNG", "EFA", "EEM", "HYG"],
};

interface ScanResult {
  ticker: string; mom12m: number; mom1m: number; rsi14: number; bbPct: number;
  zScore: number; compositeScore: number;
}

function computeRSI(closes: number[], period = 14): number {
  if (closes.length < period + 1) return 50;
  let gainSum = 0, lossSum = 0;
  for (let i = closes.length - period; i < closes.length; i++) {
    const change = closes[i] - closes[i - 1];
    if (change > 0) gainSum += change; else lossSum -= change;
  }
  const avgGain = gainSum / period;
  const avgLoss = lossSum / period;
  if (avgLoss === 0) return 100;
  const rs = avgGain / avgLoss;
  return 100 - 100 / (1 + rs);
}

function computeBBPct(closes: number[], period = 20): number {
  if (closes.length < period) return 50;
  const recent = closes.slice(-period);
  const mean = recent.reduce((s, v) => s + v, 0) / period;
  const std = Math.sqrt(recent.reduce((s, v) => s + (v - mean) ** 2, 0) / period);
  if (std === 0) return 50;
  const upper = mean + 2 * std;
  const lower = mean - 2 * std;
  return ((closes[closes.length - 1] - lower) / (upper - lower)) * 100;
}

export default function SignalScannerPage() {
  const { resolvedTheme } = useTheme();
  const t = getChartTheme(resolvedTheme === "dark");
  const L = getBaseLayout(t);
  const [activeTab, setActiveTab] = useState(0);
  const [universe, setUniverse] = useState("Sectors");
  const [results, setResults] = useState<ScanResult[]>([]);

  const tickers = UNIVERSES[universe] ?? [];

  const scan = useMutation({
    mutationFn: async () => {
      const data = await fetchPriceHistoryBatch(tickers, 260);
      return data;
    },
    onSuccess: (data) => {
      const rows: ScanResult[] = [];
      for (const tk of tickers) {
        const hist = data[tk] ?? [];
        if (hist.length < 30) continue;
        const closes = hist.map(d => d.Close);

        // Momentum
        const mom12m = closes.length >= 252 ? (closes[closes.length - 1] / closes[closes.length - 252] - 1) * 100 : 0;
        const mom1m = closes.length >= 21 ? (closes[closes.length - 1] / closes[closes.length - 21] - 1) * 100 : 0;

        // Mean reversion
        const rsi14 = computeRSI(closes);
        const bbPct = computeBBPct(closes);
        const recent20 = closes.slice(-20);
        const mean20 = recent20.reduce((s, v) => s + v, 0) / 20;
        const std20 = Math.sqrt(recent20.reduce((s, v) => s + (v - mean20) ** 2, 0) / 20);
        const zScore = std20 > 0 ? (closes[closes.length - 1] - mean20) / std20 : 0;

        // Composite: normalize and blend
        const momScore = (mom12m + mom1m * 2) / 3; // weight recent momentum more
        const mrScore = Math.abs(rsi14 - 50) + Math.abs(bbPct - 50); // reward extremes (mean-reversion candidates)
        const compositeScore = momScore * 0.6 + mrScore * 0.02; // simplified

        rows.push({ ticker: tk, mom12m, mom1m, rsi14, bbPct, zScore, compositeScore });
      }
      rows.sort((a, b) => b.compositeScore - a.compositeScore);
      setResults(rows);
    },
  });

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Signal Scanner</h1>
        <p className="text-text-secondary text-sm mt-1">Cross-sectional momentum, mean reversion, and composite ranking.</p>
      </div>

      <div className="card card-compact">
        <div className="flex items-center gap-3 flex-wrap">
          <div className="flex gap-1">
            {Object.keys(UNIVERSES).map(u => (
              <button key={u} onClick={() => setUniverse(u)}
                className={`px-2 py-1 text-xs rounded ${universe === u ? "bg-accent text-white" : "text-text-muted hover:bg-surface-alt"}`}>{u}</button>
            ))}
          </div>
          <button onClick={() => scan.mutate()} disabled={scan.isPending}
            className="px-6 py-2 bg-accent text-white font-semibold rounded-lg hover:bg-accent-hover disabled:opacity-50 text-sm">
            {scan.isPending ? `Scanning ${tickers.length} tickers...` : "Run Scan"}
          </button>
        </div>
      </div>

      {scan.isPending && (
        <div className="card text-center py-12">
          <div className="inline-block w-6 h-6 border-2 border-accent border-t-transparent rounded-full animate-spin" />
          <p className="text-sm text-text-muted mt-3">Fetching {tickers.length} tickers and computing signals...</p>
        </div>
      )}

      {results.length > 0 && (
        <>
          <div className="card card-compact">
            <div className="flex flex-wrap gap-6">
              <Metric label="Universe" value={universe} />
              <Metric label="Tickers Scanned" value={String(results.length)} />
              <Metric label="Top Signal" value={results[0].ticker} delta={`Score: ${results[0].compositeScore.toFixed(1)}`} />
            </div>
          </div>

          <div className="flex gap-1 border-b border-border pb-1">
            {TABS.map((tab, i) => (
              <button key={tab} onClick={() => setActiveTab(i)}
                className={`px-3 py-1.5 text-xs font-semibold rounded-t-md transition-colors whitespace-nowrap ${
                  activeTab === i ? "bg-accent text-white" : "text-text-muted hover:text-text hover:bg-surface-alt"}`}>
                {tab}
              </button>
            ))}
          </div>

          {/* Tab 0: Momentum */}
          {activeTab === 0 && (
            <div className="card space-y-4">
              <Plot data={[{
                x: results.map(r => r.ticker),
                y: results.map(r => r.mom12m),
                type: "bar" as const,
                marker: { color: results.map(r => r.mom12m > 0 ? t.gain : t.loss) },
                text: results.map(r => `${r.mom12m > 0 ? "+" : ""}${r.mom12m.toFixed(1)}%`),
                textposition: "outside" as const,
                textfont: { size: 9, color: t.text },
                hovertemplate: "%{x}: %{y:.1f}%<extra></extra>",
              }]} layout={{ height: 350, ...L, yaxis: { title: "12M Momentum (%)", gridcolor: t.grid }, xaxis: { gridcolor: t.grid } }}
                config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />

              <Plot data={[{
                x: results.map(r => r.ticker),
                y: results.map(r => r.mom1m),
                type: "bar" as const,
                marker: { color: results.map(r => r.mom1m > 0 ? t.gain : t.loss) },
                hovertemplate: "%{x}: %{y:.1f}%<extra></extra>",
              }]} layout={{ height: 250, ...L, yaxis: { title: "1M Momentum (%)", gridcolor: t.grid }, xaxis: { gridcolor: t.grid } }}
                config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
            </div>
          )}

          {/* Tab 1: Mean Reversion */}
          {activeTab === 1 && (
            <div className="card space-y-4">
              <Plot data={[{
                x: results.map(r => r.ticker),
                y: results.map(r => r.rsi14),
                type: "bar" as const,
                marker: { color: results.map(r => r.rsi14 > 70 ? t.loss : r.rsi14 < 30 ? t.gain : t.muted) },
                hovertemplate: "%{x}: RSI %{y:.1f}<extra></extra>",
              }]} layout={{ height: 300, ...L, yaxis: { title: "RSI(14)", gridcolor: t.grid, range: [0, 100] },
                shapes: [
                  { type: "line", y0: 70, y1: 70, x0: 0, x1: 1, xref: "paper", line: { color: t.loss, width: 1, dash: "dot" } },
                  { type: "line", y0: 30, y1: 30, x0: 0, x1: 1, xref: "paper", line: { color: t.gain, width: 1, dash: "dot" } },
                ] }}
                config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />

              <Plot data={[{
                x: results.map(r => r.ticker),
                y: results.map(r => r.zScore),
                type: "bar" as const,
                marker: { color: results.map(r => r.zScore > 2 ? t.loss : r.zScore < -2 ? t.gain : t.accent) },
                hovertemplate: "%{x}: Z=%{y:.2f}<extra></extra>",
              }]} layout={{ height: 250, ...L, yaxis: { title: "Z-Score (20d)", gridcolor: t.grid },
                shapes: [{ type: "line", y0: 0, y1: 0, x0: 0, x1: 1, xref: "paper", line: { color: t.muted, width: 1 } }] }}
                config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
            </div>
          )}

          {/* Tab 2: Composite Ranking */}
          {activeTab === 2 && (
            <div className="card">
              <div className="overflow-x-auto">
                <table className="data-table text-xs">
                  <thead><tr><th>Rank</th><th>Ticker</th><th>12M Mom</th><th>1M Mom</th><th>RSI(14)</th><th>BB%</th><th>Z-Score</th><th>Composite</th></tr></thead>
                  <tbody>
                    {results.map((r, i) => (
                      <tr key={r.ticker}>
                        <td className="font-data">{i + 1}</td>
                        <td className="font-semibold">{r.ticker}</td>
                        <td className={`font-data ${r.mom12m > 0 ? "text-gain" : "text-loss"}`}>{r.mom12m > 0 ? "+" : ""}{r.mom12m.toFixed(1)}%</td>
                        <td className={`font-data ${r.mom1m > 0 ? "text-gain" : "text-loss"}`}>{r.mom1m > 0 ? "+" : ""}{r.mom1m.toFixed(1)}%</td>
                        <td className={`font-data ${r.rsi14 > 70 ? "text-loss" : r.rsi14 < 30 ? "text-gain" : ""}`}>{r.rsi14.toFixed(1)}</td>
                        <td className="font-data">{r.bbPct.toFixed(1)}</td>
                        <td className={`font-data ${Math.abs(r.zScore) > 2 ? "text-loss font-semibold" : ""}`}>{r.zScore.toFixed(2)}</td>
                        <td className="font-data font-semibold">{r.compositeScore.toFixed(1)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

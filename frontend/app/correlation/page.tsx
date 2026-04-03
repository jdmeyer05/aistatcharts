"use client";

import { useState, useMemo } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTheme } from "next-themes";
import { fetchPriceHistoryBatch } from "@/lib/api";
import { getChartTheme, getBaseLayout } from "@/lib/chart-theme";
import { Metric } from "@/components/ui/metric";
import dynamic from "next/dynamic";

const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

const ASSET_CLASSES: Record<string, Record<string, string>> = {
  "US Equities": { SPY: "S&P 500", QQQ: "Nasdaq", IWM: "Russell", DIA: "Dow" },
  Sectors: { XLK: "Tech", XLF: "Financials", XLE: "Energy", XLV: "Health", XLI: "Industrials", XLU: "Utilities" },
  "Fixed Income": { TLT: "20Y Trsy", IEF: "7-10Y", HYG: "High Yield", LQD: "IG Corp", TIP: "TIPS" },
  Commodities: { GLD: "Gold", SLV: "Silver", USO: "Crude", UNG: "NatGas", DBA: "Agriculture" },
  International: { EFA: "Dev Intl", EEM: "Emerging", FXI: "China", EWJ: "Japan" },
};

const ALL_TICKERS = Object.values(ASSET_CLASSES).flatMap(cls => Object.keys(cls));
const TICKER_NAMES: Record<string, string> = {};
const TICKER_CLASS: Record<string, string> = {};
for (const [cls, tickers] of Object.entries(ASSET_CLASSES)) {
  for (const [tk, name] of Object.entries(tickers)) { TICKER_NAMES[tk] = name; TICKER_CLASS[tk] = cls; }
}

const CLASS_COLORS: Record<string, string> = {
  "US Equities": "#00d1ff", Sectors: "#3fb950", "Fixed Income": "#f59e0b",
  Commodities: "#a78bfa", International: "#f85149",
};

const TABS = ["Correlation Matrix", "Rolling Correlation", "Regime Analysis"];

function computeReturns(closes: number[]): number[] {
  return closes.slice(1).map((c, i) => closes[i] > 0 ? (c - closes[i]) / closes[i] : 0);
}

function pearsonCorr(a: number[], b: number[]): number {
  const n = Math.min(a.length, b.length);
  if (n < 10) return 0;
  const xa = a.slice(-n), xb = b.slice(-n);
  const ma = xa.reduce((s, v) => s + v, 0) / n;
  const mb = xb.reduce((s, v) => s + v, 0) / n;
  let num = 0, da = 0, db = 0;
  for (let i = 0; i < n; i++) {
    const ai = xa[i] - ma, bi = xb[i] - mb;
    num += ai * bi; da += ai * ai; db += bi * bi;
  }
  return da > 0 && db > 0 ? num / Math.sqrt(da * db) : 0;
}

export default function CorrelationPage() {
  const { resolvedTheme } = useTheme();
  const t = getChartTheme(resolvedTheme === "dark");
  const L = getBaseLayout(t);
  const [activeTab, setActiveTab] = useState(0);
  const [period, setPeriod] = useState(60);
  const [returns, setReturns] = useState<Record<string, number[]>>({});
  const [dates, setDates] = useState<string[]>([]);

  const load = useMutation({
    mutationFn: async () => {
      const data = await fetchPriceHistoryBatch(ALL_TICKERS, period + 10);
      return data;
    },
    onSuccess: (data) => {
      const ret: Record<string, number[]> = {};
      let maxLen = 0;
      for (const tk of ALL_TICKERS) {
        const closes = (data[tk] || []).map(d => d.Close);
        if (closes.length > 10) {
          ret[tk] = computeReturns(closes);
          maxLen = Math.max(maxLen, ret[tk].length);
        }
      }
      setReturns(ret);
      const firstTk = Object.keys(data)[0];
      if (firstTk) setDates((data[firstTk] || []).slice(1).map(d => d.Date));
    },
  });

  const activeTickers = useMemo(() => ALL_TICKERS.filter(tk => returns[tk]?.length > 0), [returns]);

  // Correlation matrix
  const corrMatrix = useMemo(() => {
    if (activeTickers.length < 2) return null;
    const n = activeTickers.length;
    const matrix: number[][] = Array.from({ length: n }, () => Array(n).fill(0));
    for (let i = 0; i < n; i++) {
      matrix[i][i] = 1;
      for (let j = i + 1; j < n; j++) {
        const c = pearsonCorr(returns[activeTickers[i]], returns[activeTickers[j]]);
        matrix[i][j] = c;
        matrix[j][i] = c;
      }
    }
    return matrix;
  }, [activeTickers, returns]);

  // Summary stats
  const avgCorr = useMemo(() => {
    if (!corrMatrix || activeTickers.length < 2) return 0;
    let sum = 0, count = 0;
    for (let i = 0; i < activeTickers.length; i++)
      for (let j = i + 1; j < activeTickers.length; j++) { sum += corrMatrix[i][j]; count++; }
    return count > 0 ? sum / count : 0;
  }, [corrMatrix, activeTickers]);

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Cross-Asset Correlation</h1>
        <p className="text-text-secondary text-sm mt-1">Correlation matrix, rolling analysis, and regime breakdown across {ALL_TICKERS.length} assets.</p>
      </div>

      <div className="card card-compact">
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2">
            <span className="text-xs font-semibold text-text-muted">Period:</span>
            {[30, 60, 90, 180, 252].map(d => (
              <button key={d} onClick={() => setPeriod(d)}
                className={`px-2 py-1 text-xs rounded ${period === d ? "bg-accent text-white" : "text-text-muted hover:bg-surface-alt"}`}>
                {d}d
              </button>
            ))}
          </div>
          <button onClick={() => load.mutate()} disabled={load.isPending}
            className="px-6 py-2 bg-accent text-white font-semibold rounded-lg hover:bg-accent-hover disabled:opacity-50 transition-colors text-sm">
            {load.isPending ? "Loading..." : "Compute"}
          </button>
        </div>
      </div>

      {load.isPending && (
        <div className="card text-center py-12">
          <div className="inline-block w-6 h-6 border-2 border-accent border-t-transparent rounded-full animate-spin" />
          <p className="text-sm text-text-muted mt-3">Fetching {ALL_TICKERS.length} tickers...</p>
        </div>
      )}

      {corrMatrix && activeTickers.length > 2 && (
        <>
          <div className="card card-compact">
            <div className="flex flex-wrap gap-6">
              <Metric label="Assets" value={String(activeTickers.length)} />
              <Metric label="Avg Correlation" value={avgCorr.toFixed(2)} />
              <Metric label="Period" value={`${period} days`} />
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

          {/* Tab 0: Matrix */}
          {activeTab === 0 && (
            <div className="card">
              <Plot data={[{
                type: "heatmap" as const,
                z: corrMatrix,
                x: activeTickers.map(tk => TICKER_NAMES[tk] || tk),
                y: activeTickers.map(tk => TICKER_NAMES[tk] || tk),
                colorscale: [[0, t.loss], [0.5, t.grid], [1, t.gain]],
                zmid: 0, zmin: -1, zmax: 1,
                colorbar: { title: { text: "Corr", font: { size: 9 } }, thickness: 12 },
                hovertemplate: "%{x} vs %{y}: %{z:.2f}<extra></extra>",
                text: corrMatrix.map(row => row.map(v => v.toFixed(2))),
                texttemplate: "%{text}",
                textfont: { size: 8 },
              }]}
                layout={{
                  height: Math.max(500, activeTickers.length * 22),
                  ...L,
                  margin: { l: 80, r: 20, t: 10, b: 80 },
                  xaxis: { tickangle: -45, tickfont: { size: 8 }, gridcolor: t.grid },
                  yaxis: { autorange: "reversed", tickfont: { size: 8 }, gridcolor: t.grid },
                }}
                config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
            </div>
          )}

          {/* Tab 1: Rolling Correlation */}
          {activeTab === 1 && (
            <div className="card space-y-4">
              <p className="text-xs text-text-muted">20-day rolling correlation of each asset vs SPY.</p>
              {(() => {
                const spyRet = returns["SPY"];
                if (!spyRet || spyRet.length < 20) return <p className="text-sm text-text-muted">Need SPY data.</p>;
                const window = 20;
                const tickers = activeTickers.filter(tk => tk !== "SPY" && returns[tk]?.length >= window);
                const rollingData = tickers.slice(0, 8).map(tk => {
                  const ret = returns[tk];
                  const n = Math.min(ret.length, spyRet.length);
                  const rolling: number[] = [];
                  for (let i = window; i <= n; i++) {
                    rolling.push(pearsonCorr(spyRet.slice(i - window, i), ret.slice(i - window, i)));
                  }
                  return { ticker: tk, rolling };
                });
                const rollingDates = dates.slice(window);
                return (
                  <Plot data={rollingData.map(d => ({
                    x: rollingDates.slice(-d.rolling.length),
                    y: d.rolling,
                    type: "scatter" as const, mode: "lines" as const,
                    name: TICKER_NAMES[d.ticker] || d.ticker,
                    line: { width: 1.5 },
                  }))}
                    layout={{ height: 400, ...L, yaxis: { title: "Correlation vs SPY", gridcolor: t.grid, range: [-1, 1] }, xaxis: { gridcolor: t.grid }, hovermode: "x unified",
                      shapes: [{ type: "line", y0: 0, y1: 0, x0: 0, x1: 1, xref: "paper", line: { color: t.muted, width: 1 } }] }}
                    config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
                );
              })()}
            </div>
          )}

          {/* Tab 2: Regime Analysis */}
          {activeTab === 2 && (
            <div className="card space-y-4">
              <p className="text-xs text-text-muted">Average correlation by asset class — high-vol vs low-vol days.</p>
              {(() => {
                const spyRet = returns["SPY"];
                if (!spyRet) return null;
                const absRet = spyRet.map(r => Math.abs(r));
                const median = [...absRet].sort((a, b) => a - b)[Math.floor(absRet.length / 2)];

                const classCorrs: { cls: string; calm: number; stress: number }[] = [];
                for (const cls of Object.keys(ASSET_CLASSES)) {
                  const tickers = Object.keys(ASSET_CLASSES[cls]).filter(tk => returns[tk]?.length > 0 && tk !== "SPY");
                  if (tickers.length === 0) continue;
                  let calmSum = 0, stressSum = 0, calmN = 0, stressN = 0;
                  for (const tk of tickers) {
                    const ret = returns[tk];
                    const n = Math.min(ret.length, spyRet.length);
                    const calmA: number[] = [], calmB: number[] = [], stressA: number[] = [], stressB: number[] = [];
                    for (let i = 0; i < n; i++) {
                      if (absRet[i] <= median) { calmA.push(spyRet[i]); calmB.push(ret[i]); }
                      else { stressA.push(spyRet[i]); stressB.push(ret[i]); }
                    }
                    if (calmA.length > 5) { calmSum += pearsonCorr(calmA, calmB); calmN++; }
                    if (stressA.length > 5) { stressSum += pearsonCorr(stressA, stressB); stressN++; }
                  }
                  classCorrs.push({
                    cls,
                    calm: calmN > 0 ? calmSum / calmN : 0,
                    stress: stressN > 0 ? stressSum / stressN : 0,
                  });
                }

                return (
                  <Plot data={[
                    { x: classCorrs.map(c => c.cls), y: classCorrs.map(c => c.calm), type: "bar" as const, name: "Calm Days", marker: { color: t.gain } },
                    { x: classCorrs.map(c => c.cls), y: classCorrs.map(c => c.stress), type: "bar" as const, name: "Stress Days", marker: { color: t.loss } },
                  ]}
                    layout={{ height: 350, ...L, barmode: "group", yaxis: { title: "Avg Corr vs SPY", gridcolor: t.grid, range: [-0.5, 1] }, xaxis: { gridcolor: t.grid } }}
                    config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
                );
              })()}
            </div>
          )}
        </>
      )}
    </div>
  );
}

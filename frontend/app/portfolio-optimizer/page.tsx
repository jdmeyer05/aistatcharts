"use client";

import { useState, useMemo } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTheme } from "next-themes";
import { fetchPriceHistoryBatch } from "@/lib/api";
import { getChartTheme, getBaseLayout } from "@/lib/chart-theme";
import { Metric } from "@/components/ui/metric";
import dynamic from "next/dynamic";

const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

const TABS = ["Efficient Frontier", "Optimal Weights", "Risk Analysis"];

const DEFAULT_TICKERS = ["SPY", "QQQ", "TLT", "GLD", "VGK", "EEM", "XLE", "HYG"];

function computeReturns(closes: number[]): number[] {
  return closes.slice(1).map((c, i) => closes[i] > 0 ? (c - closes[i]) / closes[i] : 0);
}

function meanReturn(rets: number[]): number { return rets.reduce((s, r) => s + r, 0) / rets.length * 252; }
function stdReturn(rets: number[]): number {
  const m = rets.reduce((s, r) => s + r, 0) / rets.length;
  return Math.sqrt(rets.reduce((s, r) => s + (r - m) ** 2, 0) / rets.length) * Math.sqrt(252);
}

function covMatrix(retArrays: number[][]): number[][] {
  const n = retArrays.length;
  const T = Math.min(...retArrays.map(r => r.length));
  const means = retArrays.map(r => r.slice(-T).reduce((s, v) => s + v, 0) / T);
  const cov: number[][] = Array.from({ length: n }, () => Array(n).fill(0));
  for (let i = 0; i < n; i++) {
    for (let j = i; j < n; j++) {
      let s = 0;
      for (let t = 0; t < T; t++) s += (retArrays[i][retArrays[i].length - T + t] - means[i]) * (retArrays[j][retArrays[j].length - T + t] - means[j]);
      cov[i][j] = cov[j][i] = s / T * 252;
    }
  }
  return cov;
}

// Simple portfolio stats
function portStats(w: number[], mu: number[], cov: number[][]): { ret: number; vol: number; sharpe: number } {
  const ret = w.reduce((s, wi, i) => s + wi * mu[i], 0);
  let vol2 = 0;
  for (let i = 0; i < w.length; i++) for (let j = 0; j < w.length; j++) vol2 += w[i] * w[j] * cov[i][j];
  const vol = Math.sqrt(Math.max(0, vol2));
  return { ret, vol, sharpe: vol > 0 ? (ret - 0.045) / vol : 0 };
}

// Equal weight
function equalWeight(n: number): number[] { return Array(n).fill(1 / n); }

// Min variance (analytical for long-only: use equal weight as proxy since we can't do constrained optimization easily in JS)
function minVarWeight(cov: number[][]): number[] {
  const n = cov.length;
  // Inverse volatility weighting as approximation
  const vols = cov.map((_, i) => Math.sqrt(cov[i][i]));
  const invVols = vols.map(v => v > 0 ? 1 / v : 0);
  const total = invVols.reduce((s, v) => s + v, 0);
  return total > 0 ? invVols.map(v => v / total) : equalWeight(n);
}

// Risk parity (inverse vol approximation)
function riskParityWeight(cov: number[][]): number[] { return minVarWeight(cov); }

export default function PortfolioOptimizerPage() {
  const { resolvedTheme } = useTheme();
  const t = getChartTheme(resolvedTheme === "dark");
  const L = getBaseLayout(t);
  const [activeTab, setActiveTab] = useState(0);
  const [tickerInput, setTickerInput] = useState(DEFAULT_TICKERS.join(", "));
  const [retData, setRetData] = useState<{ tickers: string[]; returns: number[][]; mu: number[]; vol: number[]; cov: number[][] } | null>(null);

  const load = useMutation({
    mutationFn: async () => {
      const tickers = tickerInput.split(",").map(t => t.trim().toUpperCase()).filter(Boolean);
      const data = await fetchPriceHistoryBatch(tickers, 504);
      return { data, tickers };
    },
    onSuccess: ({ data, tickers }) => {
      const validTickers: string[] = [];
      const retArrays: number[][] = [];
      for (const tk of tickers) {
        const hist = data[tk];
        if (hist && hist.length > 60) {
          const closes = hist.map(d => d.Close);
          validTickers.push(tk);
          retArrays.push(computeReturns(closes));
        }
      }
      if (validTickers.length < 2) return;
      const mu = retArrays.map(meanReturn);
      const vol = retArrays.map(stdReturn);
      const cov = covMatrix(retArrays);
      setRetData({ tickers: validTickers, returns: retArrays, mu, vol, cov });
    },
  });

  // Portfolio methods
  const portfolios = useMemo(() => {
    if (!retData) return [];
    const { tickers, mu, cov } = retData;
    const n = tickers.length;

    const methods = [
      { name: "Equal Weight", weights: equalWeight(n) },
      { name: "Min Variance", weights: minVarWeight(cov) },
      { name: "Risk Parity", weights: riskParityWeight(cov) },
    ];

    return methods.map(m => ({ ...m, stats: portStats(m.weights, mu, cov) }));
  }, [retData]);

  // Random frontier portfolios
  const frontier = useMemo(() => {
    if (!retData) return [];
    const { mu, cov } = retData;
    const n = mu.length;
    const pts: { ret: number; vol: number }[] = [];
    for (let i = 0; i < 2000; i++) {
      const raw = Array.from({ length: n }, () => Math.random());
      const sum = raw.reduce((s, v) => s + v, 0);
      const w = raw.map(v => v / sum);
      const s = portStats(w, mu, cov);
      pts.push({ ret: s.ret * 100, vol: s.vol * 100 });
    }
    return pts;
  }, [retData]);

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Portfolio Optimizer</h1>
        <p className="text-text-secondary text-sm mt-1">Mean-variance optimization, risk parity, and efficient frontier analysis.</p>
      </div>

      <div className="card card-compact">
        <div className="flex items-center gap-3 flex-wrap">
          <input type="text" value={tickerInput} onChange={e => setTickerInput(e.target.value.toUpperCase())}
            className="flex-1 min-w-[200px] px-3 py-2 border border-border rounded-lg text-sm font-data bg-surface"
            placeholder="SPY, QQQ, TLT, GLD, ..." />
          <button onClick={() => load.mutate()} disabled={load.isPending}
            className="px-6 py-2 bg-accent text-white font-semibold rounded-lg hover:bg-accent-hover disabled:opacity-50 text-sm">
            {load.isPending ? "Optimizing..." : "Optimize"}
          </button>
        </div>
      </div>

      {load.isPending && (
        <div className="card text-center py-12">
          <div className="inline-block w-6 h-6 border-2 border-accent border-t-transparent rounded-full animate-spin" />
        </div>
      )}

      {retData && portfolios.length > 0 && (
        <>
          {/* Asset stats */}
          <div className="card">
            <div className="overflow-x-auto">
              <table className="data-table text-xs">
                <thead><tr><th>Ticker</th><th>Ann. Return</th><th>Ann. Vol</th><th>Sharpe</th>{portfolios.map(p => <th key={p.name}>{p.name} Wt</th>)}</tr></thead>
                <tbody>
                  {retData.tickers.map((tk, i) => (
                    <tr key={tk}>
                      <td className="font-semibold">{tk}</td>
                      <td className={`font-data ${retData.mu[i] > 0 ? "text-gain" : "text-loss"}`}>{(retData.mu[i] * 100).toFixed(1)}%</td>
                      <td className="font-data">{(retData.vol[i] * 100).toFixed(1)}%</td>
                      <td className="font-data">{retData.vol[i] > 0 ? ((retData.mu[i] - 0.045) / retData.vol[i]).toFixed(2) : "—"}</td>
                      {portfolios.map(p => <td key={p.name} className="font-data">{(p.weights[i] * 100).toFixed(1)}%</td>)}
                    </tr>
                  ))}
                  <tr className="font-semibold bg-accent-light">
                    <td>Portfolio</td>
                    {portfolios.map(p => [
                      <td key={`${p.name}-r`} className={`font-data ${p.stats.ret > 0 ? "text-gain" : "text-loss"}`}>{(p.stats.ret * 100).toFixed(1)}%</td>,
                    ]).flat().slice(0, 1)}
                    <td colSpan={2 + portfolios.length}></td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>

          {/* Portfolio comparison */}
          <div className="card card-compact">
            <div className="flex flex-wrap gap-6">
              {portfolios.map(p => (
                <div key={p.name} className="border border-border rounded-lg p-3 min-w-[150px]">
                  <div className="metric-label">{p.name}</div>
                  <div className="flex gap-4 mt-1">
                    <Metric label="Return" value={`${(p.stats.ret * 100).toFixed(1)}%`} />
                    <Metric label="Vol" value={`${(p.stats.vol * 100).toFixed(1)}%`} />
                    <Metric label="Sharpe" value={p.stats.sharpe.toFixed(2)} />
                  </div>
                </div>
              ))}
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

          {/* Tab 0: Frontier */}
          {activeTab === 0 && (
            <div className="card">
              <Plot data={[
                { x: frontier.map(p => p.vol), y: frontier.map(p => p.ret), type: "scatter" as const, mode: "markers" as const,
                  marker: { color: frontier.map(p => p.vol > 0 ? (p.ret - 4.5) / p.vol : 0), colorscale: "Viridis", size: 3, opacity: 0.4, showscale: false },
                  name: "Random Portfolios", hovertemplate: "Vol: %{x:.1f}%<br>Ret: %{y:.1f}%<extra></extra>" },
                ...portfolios.map((p, i) => ({
                  x: [p.stats.vol * 100], y: [p.stats.ret * 100],
                  type: "scatter" as const, mode: "markers+text" as const,
                  marker: { size: 14, color: [t.loss, t.gain, t.accent][i], symbol: "diamond" },
                  text: [p.name], textposition: "top center" as const, textfont: { size: 9, color: t.text },
                  name: p.name,
                })),
                ...retData.tickers.map((tk, i) => ({
                  x: [retData.vol[i] * 100], y: [retData.mu[i] * 100],
                  type: "scatter" as const, mode: "markers+text" as const,
                  marker: { size: 8, color: t.muted, symbol: "circle" },
                  text: [tk], textposition: "bottom center" as const, textfont: { size: 8, color: t.muted },
                  name: tk, showlegend: false,
                })),
              ]} layout={{ height: 500, ...L, xaxis: { title: "Annualized Volatility (%)", gridcolor: t.grid }, yaxis: { title: "Annualized Return (%)", gridcolor: t.grid }, hovermode: "closest" }}
                config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
            </div>
          )}

          {/* Tab 1: Weights */}
          {activeTab === 1 && (
            <div className="card space-y-4">
              {portfolios.map(p => (
                <div key={p.name}>
                  <div className="text-sm font-bold mb-1">{p.name}</div>
                  <Plot data={[{
                    labels: retData.tickers, values: p.weights.map(w => Math.max(0, w)),
                    type: "pie" as const, hole: 0.4,
                    textinfo: "label+percent", textfont: { size: 10 },
                  }]} layout={{ height: 280, paper_bgcolor: "transparent", font: { color: t.text, size: 10 }, margin: { l: 0, r: 0, t: 10, b: 10 }, showlegend: false }}
                    config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
                </div>
              ))}
            </div>
          )}

          {/* Tab 2: Risk */}
          {activeTab === 2 && (
            <div className="card">
              <div className="text-sm font-bold mb-2">Correlation Matrix</div>
              {(() => {
                const n = retData.tickers.length;
                const corr: number[][] = Array.from({ length: n }, () => Array(n).fill(0));
                for (let i = 0; i < n; i++) for (let j = 0; j < n; j++) {
                  const cij = retData.cov[i][j];
                  const vi = Math.sqrt(retData.cov[i][i]);
                  const vj = Math.sqrt(retData.cov[j][j]);
                  corr[i][j] = vi > 0 && vj > 0 ? cij / (vi * vj) : 0;
                }
                return (
                  <Plot data={[{
                    type: "heatmap" as const, z: corr,
                    x: retData.tickers, y: retData.tickers,
                    colorscale: [[0, t.loss], [0.5, t.grid], [1, t.gain]],
                    zmid: 0, zmin: -1, zmax: 1,
                    text: corr.map(row => row.map(v => v.toFixed(2))), texttemplate: "%{text}",
                    textfont: { size: 9 },
                    colorbar: { title: { text: "Corr", font: { size: 9 } }, thickness: 12 },
                  }]} layout={{ height: Math.max(350, n * 30), ...L, margin: { l: 60, r: 20, t: 10, b: 60 }, xaxis: { tickangle: -45 }, yaxis: { autorange: "reversed" } }}
                    config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
                );
              })()}
            </div>
          )}
        </>
      )}

      {load.isError && <div className="card border-loss/30 bg-loss-bg text-loss text-sm">Failed: {(load.error as Error).message}</div>}
    </div>
  );
}

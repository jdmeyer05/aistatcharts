"use client";

import { useState, useMemo } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTheme } from "next-themes";
import { fetchPriceHistoryBatch } from "@/lib/api";
import { getChartTheme, getBaseLayout, CHART_HEIGHT, heatmapTrace, heatmapHeight, type ChartTheme } from "@/lib/chart-theme";
import { Metric } from "@/components/ui/metric";
import dynamic from "next/dynamic";

const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

const TABS = ["Efficient Frontier", "Optimal Weights", "Walk-Forward Backtest", "Risk Analysis", "Black-Litterman"];

const DEFAULT_TICKERS = ["SPY", "QQQ", "TLT", "GLD", "VGK", "EEM", "XLE", "HYG"];

const METHOD_COLORS: Record<string, string> = {
  "Equal Weight": "#888888",
  "Min Variance": "#00ff88",
  "Risk Parity": "#ffaa00",
  "Tangency (Max Sharpe)": "#00d1ff",
};

// ─── Math helpers ───────────────────────────────────────────

function computeReturns(closes: number[]): number[] {
  return closes.slice(1).map((c, i) => closes[i] > 0 ? (c - closes[i]) / closes[i] : 0);
}

function mean(xs: number[]): number {
  if (xs.length === 0) return 0;
  return xs.reduce((s, v) => s + v, 0) / xs.length;
}

function std(xs: number[]): number {
  if (xs.length < 2) return 0;
  const m = mean(xs);
  return Math.sqrt(xs.reduce((s, v) => s + (v - m) ** 2, 0) / (xs.length - 1));
}

function annualizedReturn(rets: number[]): number {
  return mean(rets) * 252;
}

function annualizedVol(rets: number[]): number {
  return std(rets) * Math.sqrt(252);
}

// Build covariance matrix from return arrays (annualized)
function annualCov(retArrays: number[][]): number[][] {
  const n = retArrays.length;
  const T = Math.min(...retArrays.map(r => r.length));
  const means = retArrays.map(r => mean(r.slice(-T)));
  const cov: number[][] = Array.from({ length: n }, () => Array(n).fill(0));
  for (let i = 0; i < n; i++) {
    for (let j = i; j < n; j++) {
      let s = 0;
      for (let k = 0; k < T; k++) {
        s += (retArrays[i][retArrays[i].length - T + k] - means[i]) *
             (retArrays[j][retArrays[j].length - T + k] - means[j]);
      }
      cov[i][j] = cov[j][i] = (s / (T - 1)) * 252;
    }
  }
  return cov;
}

// Portfolio stats
function portStats(w: number[], mu: number[], cov: number[][], rf = 0.045): { ret: number; vol: number; sharpe: number } {
  const ret = w.reduce((s, wi, i) => s + wi * mu[i], 0);
  let vol2 = 0;
  for (let i = 0; i < w.length; i++) for (let j = 0; j < w.length; j++) vol2 += w[i] * w[j] * cov[i][j];
  const vol = Math.sqrt(Math.max(0, vol2));
  return { ret, vol, sharpe: vol > 0 ? (ret - rf) / vol : 0 };
}

function equalWeight(n: number): number[] { return Array(n).fill(1 / n); }

// Inverse-volatility "min variance" proxy (long-only, analytical closed form is ill-behaved without constraints)
function minVarWeight(cov: number[][]): number[] {
  const n = cov.length;
  const vols = cov.map((_, i) => Math.sqrt(Math.max(0, cov[i][i])));
  const invVols = vols.map(v => v > 0 ? 1 / v : 0);
  const total = invVols.reduce((s, v) => s + v, 0);
  return total > 0 ? invVols.map(v => v / total) : equalWeight(n);
}

// Risk parity via equal-risk-contribution iterative solver (Newton-style)
function riskParityWeight(cov: number[][]): number[] {
  const n = cov.length;
  let w = equalWeight(n);
  for (let iter = 0; iter < 50; iter++) {
    const mrc = matVecMul(cov, w);
    let portVar = 0;
    for (let i = 0; i < n; i++) portVar += w[i] * mrc[i];
    const portVol = Math.sqrt(Math.max(1e-12, portVar));
    // risk contribution target = portVol / n
    const target = portVol / n;
    // gradient step
    const newW: number[] = [];
    for (let i = 0; i < n; i++) {
      const rc = w[i] * mrc[i] / portVol;
      const adj = rc > 0 ? Math.pow(target / rc, 0.3) : 1;
      newW.push(Math.max(1e-6, w[i] * adj));
    }
    const total = newW.reduce((s, v) => s + v, 0);
    w = newW.map(v => v / total);
  }
  return w;
}

// Tangency portfolio (max Sharpe, unconstrained). w = cov^-1 * (mu - rf) / sum
function tangencyPortfolio(mu: number[], cov: number[][], rf = 0.045): number[] {
  const n = mu.length;
  const excess = mu.map(m => m - rf);
  const covInv = invMatrix(cov);
  if (!covInv) return equalWeight(n);
  const raw = matVecMul(covInv, excess);
  const total = raw.reduce((s, v) => s + v, 0);
  if (Math.abs(total) < 1e-9) return equalWeight(n);
  const w = raw.map(v => v / total);
  // If fully unconstrained yields short positions, clamp + renormalize
  const hasNeg = w.some(v => v < 0);
  if (hasNeg) {
    const clamped = w.map(v => Math.max(0, v));
    const s = clamped.reduce((a, b) => a + b, 0);
    return s > 0 ? clamped.map(v => v / s) : equalWeight(n);
  }
  return w;
}

// ── Matrix ops ────

function matVecMul(A: number[][], x: number[]): number[] {
  return A.map(row => row.reduce((s, v, i) => s + v * x[i], 0));
}

function matMul(A: number[][], B: number[][]): number[][] {
  const n = A.length, p = B[0].length, m = B.length;
  const C: number[][] = Array.from({ length: n }, () => Array(p).fill(0));
  for (let i = 0; i < n; i++) {
    for (let j = 0; j < p; j++) {
      let s = 0;
      for (let k = 0; k < m; k++) s += A[i][k] * B[k][j];
      C[i][j] = s;
    }
  }
  return C;
}

function matAdd(A: number[][], B: number[][]): number[][] {
  return A.map((row, i) => row.map((v, j) => v + B[i][j]));
}

function transpose(A: number[][]): number[][] {
  const n = A.length, m = A[0].length;
  const T: number[][] = Array.from({ length: m }, () => Array(n).fill(0));
  for (let i = 0; i < n; i++) for (let j = 0; j < m; j++) T[j][i] = A[i][j];
  return T;
}

function invMatrix(A: number[][]): number[][] | null {
  const n = A.length;
  // Build augmented [A | I]
  const M: number[][] = A.map((row, i) => {
    const r = [...row];
    for (let j = 0; j < n; j++) r.push(i === j ? 1 : 0);
    return r;
  });
  for (let col = 0; col < n; col++) {
    let piv = col;
    for (let r = col + 1; r < n; r++) if (Math.abs(M[r][col]) > Math.abs(M[piv][col])) piv = r;
    if (piv !== col) [M[col], M[piv]] = [M[piv], M[col]];
    const d = M[col][col];
    if (Math.abs(d) < 1e-12) return null;
    for (let j = 0; j < 2 * n; j++) M[col][j] /= d;
    for (let r = 0; r < n; r++) {
      if (r === col) continue;
      const f = M[r][col];
      for (let j = 0; j < 2 * n; j++) M[r][j] -= f * M[col][j];
    }
  }
  return M.map(row => row.slice(n));
}

// Portfolio metrics for walk-forward OOS
function computeOosMetrics(oosRets: number[], rf = 0.045) {
  if (oosRets.length === 0) return { annRet: 0, annVol: 0, sharpe: 0, maxDd: 0, totalRet: 0 };
  const ann = mean(oosRets) * 252;
  const vol = std(oosRets) * Math.sqrt(252);
  const sharpe = vol > 0 ? (ann - rf) / vol : 0;
  let equity = 1;
  let peak = 1;
  let maxDd = 0;
  for (const r of oosRets) {
    equity *= (1 + r);
    peak = Math.max(peak, equity);
    const dd = (equity - peak) / peak;
    if (dd < maxDd) maxDd = dd;
  }
  return { annRet: ann * 100, annVol: vol * 100, sharpe, maxDd: maxDd * 100, totalRet: (equity - 1) * 100 };
}

// ═══════════════════════════════════════════════

export default function PortfolioOptimizerPage() {
  const { resolvedTheme } = useTheme();
  const t = getChartTheme(resolvedTheme === "dark");
  const L = getBaseLayout(t);
  const [activeTab, setActiveTab] = useState(0);
  const [tickerInput, setTickerInput] = useState(DEFAULT_TICKERS.join(", "));
  const [retData, setRetData] = useState<{
    tickers: string[];
    returns: number[][];  // aligned, same length per ticker
    dates: string[];       // T-1 return dates
    mu: number[];
    vol: number[];
    cov: number[][];
  } | null>(null);

  const load = useMutation({
    mutationFn: async () => {
      const tickers = tickerInput.split(",").map(t => t.trim().toUpperCase()).filter(Boolean);
      const data = await fetchPriceHistoryBatch(tickers, 1008);
      return { data, tickers };
    },
    onSuccess: ({ data, tickers }) => {
      const validTickers: string[] = [];
      const retArraysRaw: number[][] = [];
      const dateArraysRaw: string[][] = [];
      for (const tk of tickers) {
        const hist = data[tk];
        if (hist && hist.length > 60) {
          const closes = hist.map(d => d.Close);
          const dates = hist.slice(1).map(d => d.Date);
          validTickers.push(tk);
          retArraysRaw.push(computeReturns(closes));
          dateArraysRaw.push(dates);
        }
      }
      if (validTickers.length < 2) return;
      // Align to a common date index (intersection)
      const commonDates = dateArraysRaw.reduce<Set<string>>((acc, arr, idx) => {
        const s = new Set(arr);
        return idx === 0 ? s : new Set([...acc].filter(d => s.has(d)));
      }, new Set<string>());
      const sortedDates = Array.from(commonDates).sort();
      const dateToIdx = sortedDates.map((d, i) => ({ d, i }));
      const alignedReturns: number[][] = validTickers.map((_, t) => {
        const map = new Map(dateArraysRaw[t].map((d, i) => [d, retArraysRaw[t][i]]));
        return dateToIdx.map(({ d }) => map.get(d) ?? 0);
      });
      const mu = alignedReturns.map(annualizedReturn);
      const vol = alignedReturns.map(annualizedVol);
      const cov = annualCov(alignedReturns);
      setRetData({ tickers: validTickers, returns: alignedReturns, dates: sortedDates, mu, vol, cov });
    },
  });

  const portfolios = useMemo(() => {
    if (!retData) return [];
    const { mu, cov } = retData;
    const n = mu.length;
    const methods = [
      { name: "Equal Weight", weights: equalWeight(n) },
      { name: "Min Variance", weights: minVarWeight(cov) },
      { name: "Risk Parity", weights: riskParityWeight(cov) },
      { name: "Tangency (Max Sharpe)", weights: tangencyPortfolio(mu, cov) },
    ];
    return methods.map(m => ({ ...m, stats: portStats(m.weights, mu, cov) }));
  }, [retData]);

  const frontier = useMemo(() => {
    if (!retData) return [];
    const { mu, cov } = retData;
    const n = mu.length;
    const pts: { ret: number; vol: number }[] = [];
    for (let i = 0; i < 2000; i++) {
      const raw = Array.from({ length: n }, () => Math.random());
      const s = raw.reduce((a, b) => a + b, 0);
      const w = raw.map(v => v / s);
      const stats = portStats(w, mu, cov);
      pts.push({ ret: stats.ret * 100, vol: stats.vol * 100 });
    }
    return pts;
  }, [retData]);

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Portfolio Optimizer</h1>
        <p className="text-text-secondary text-sm mt-1">Mean-variance optimization, risk parity, walk-forward backtests, and Black-Litterman view blending.</p>
      </div>

      <div className="card card-compact">
        <div className="flex items-center gap-3 flex-wrap">
          <input type="text" value={tickerInput} onChange={e => setTickerInput(e.target.value.toUpperCase())}
            className="flex-1 min-w-[200px] px-3 py-2 border border-border rounded-lg text-sm font-data bg-surface"
            placeholder="SPY, QQQ, TLT, GLD, ..." />
          <button onClick={() => load.mutate()} disabled={load.isPending}
            className="px-6 py-2 bg-accent text-white font-semibold rounded-lg hover:bg-accent-hover disabled:opacity-50 text-sm">
            {load.isPending ? "Loading..." : "Optimize"}
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
          {/* Asset stats table */}
          <div className="card">
            <div className="overflow-x-auto">
              <table className="w-full text-xs font-data">
                <thead className="border-b border-border text-text-muted">
                  <tr>
                    <th className="text-left py-1.5 px-2">Ticker</th>
                    <th className="text-right py-1.5 px-2">Ann. Return</th>
                    <th className="text-right py-1.5 px-2">Ann. Vol</th>
                    <th className="text-right py-1.5 px-2">Sharpe</th>
                    {portfolios.map(p => <th key={p.name} className="text-right py-1.5 px-2">{p.name} Wt</th>)}
                  </tr>
                </thead>
                <tbody>
                  {retData.tickers.map((tk, i) => (
                    <tr key={tk} className="border-b border-border/50 hover:bg-surface-alt">
                      <td className="py-1 px-2 font-semibold">{tk}</td>
                      <td className={`py-1 px-2 text-right ${retData.mu[i] > 0 ? "text-gain" : "text-loss"}`}>
                        {(retData.mu[i] * 100).toFixed(1)}%
                      </td>
                      <td className="py-1 px-2 text-right">{(retData.vol[i] * 100).toFixed(1)}%</td>
                      <td className="py-1 px-2 text-right">{retData.vol[i] > 0 ? ((retData.mu[i] - 0.045) / retData.vol[i]).toFixed(2) : "—"}</td>
                      {portfolios.map(p => <td key={p.name} className="py-1 px-2 text-right">{(p.weights[i] * 100).toFixed(1)}%</td>)}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* Portfolio comparison */}
          <div className="card card-compact">
            <div className="flex flex-wrap gap-3">
              {portfolios.map(p => (
                <div key={p.name} className="border border-border rounded-lg p-3 min-w-[170px]">
                  <div className="metric-label" style={{ color: METHOD_COLORS[p.name] ?? t.text }}>{p.name}</div>
                  <div className="flex gap-4 mt-1">
                    <Metric label="Return" value={`${(p.stats.ret * 100).toFixed(1)}%`} />
                    <Metric label="Vol" value={`${(p.stats.vol * 100).toFixed(1)}%`} />
                    <Metric label="Sharpe" value={p.stats.sharpe.toFixed(2)} />
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div className="flex gap-1 border-b border-border pb-1 overflow-x-auto">
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
                ...portfolios.map(p => ({
                  x: [p.stats.vol * 100], y: [p.stats.ret * 100],
                  type: "scatter" as const, mode: "markers+text" as const,
                  marker: { size: 14, color: METHOD_COLORS[p.name] ?? t.accent, symbol: "diamond" },
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
              ]} layout={{ height: CHART_HEIGHT.tall + 40, ...L, xaxis: { title: { text: "Annualized Volatility (%)" }, gridcolor: t.grid }, yaxis: { title: { text: "Annualized Return (%)" }, gridcolor: t.grid }, hovermode: "closest" }}
                config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
            </div>
          )}

          {/* Tab 1: Weights */}
          {activeTab === 1 && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {portfolios.map(p => (
                <div key={p.name} className="card">
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

          {/* Tab 2: Walk-Forward */}
          {activeTab === 2 && (
            <WalkForwardTab retData={retData} t={t} L={L} />
          )}

          {/* Tab 3: Risk Analysis */}
          {activeTab === 3 && (
            <div className="card">
              <div className="text-sm font-bold mb-2">Correlation Matrix</div>
              {(() => {
                const n = retData.tickers.length;
                const corr: number[][] = Array.from({ length: n }, () => Array(n).fill(0));
                for (let i = 0; i < n; i++) for (let j = 0; j < n; j++) {
                  const cij = retData.cov[i][j];
                  const vi = Math.sqrt(Math.max(0, retData.cov[i][i]));
                  const vj = Math.sqrt(Math.max(0, retData.cov[j][j]));
                  corr[i][j] = vi > 0 && vj > 0 ? cij / (vi * vj) : 0;
                }
                return (
                  <Plot data={[{
                    ...heatmapTrace(t, "correlation", { colorbarTitle: "Corr" }),
                    z: corr,
                    x: retData.tickers, y: retData.tickers,
                    zmid: 0, zmin: -1, zmax: 1,
                    text: corr.map(row => row.map(v => v.toFixed(2))),
                  }]} layout={{ height: heatmapHeight(n), ...L, margin: { l: 60, r: 20, t: 10, b: 60 }, xaxis: { tickangle: -45 }, yaxis: { autorange: "reversed" } }}
                    config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
                );
              })()}
            </div>
          )}

          {/* Tab 4: Black-Litterman */}
          {activeTab === 4 && (
            <BlackLittermanTab retData={retData} portfolios={portfolios} t={t} L={L} />
          )}
        </>
      )}

      {load.isError && <div className="card border-loss/30 bg-loss-bg text-loss text-sm">Failed: {(load.error as Error).message}</div>}
    </div>
  );
}

// ═══════════════════════════════════════════════
// Tab 2 — Walk-Forward Backtest
// ═══════════════════════════════════════════════

function WalkForwardTab({
  retData, t, L,
}: {
  retData: { tickers: string[]; returns: number[][]; dates: string[]; mu: number[]; vol: number[]; cov: number[][] };
  t: ChartTheme;
  L: ReturnType<typeof getBaseLayout>;
}) {
  const [rebal, setRebal] = useState<"Monthly" | "Quarterly">("Monthly");
  const [estWindow, setEstWindow] = useState(252);

  const nTotal = retData.dates.length;
  const maxEst = Math.max(63, nTotal - 60);

  const wf = useMemo(() => {
    const { returns, tickers, dates } = retData;
    const n = tickers.length;
    const rebalEvery = rebal === "Monthly" ? 21 : 63;

    // Build rebalance indices: every `rebalEvery` days, starting from estWindow
    const rebalIdx: number[] = [];
    for (let i = estWindow; i < nTotal; i += rebalEvery) rebalIdx.push(i);
    if (rebalIdx.length < 4) return null;

    const methodRets: Record<string, number[]> = {
      "Equal Weight": [],
      "Min Variance": [],
      "Risk Parity": [],
      "Tangency (Max Sharpe)": [],
    };
    const outDates: string[] = [];

    for (let r = 0; r < rebalIdx.length; r++) {
      const idx = rebalIdx[r];
      // Estimation window
      const estRet: number[][] = returns.map(arr => arr.slice(idx - estWindow, idx));
      const estMu = estRet.map(annualizedReturn);
      const estCov = annualCov(estRet);

      const weights: Record<string, number[]> = {
        "Equal Weight": equalWeight(n),
        "Min Variance": minVarWeight(estCov),
        "Risk Parity": riskParityWeight(estCov),
        "Tangency (Max Sharpe)": tangencyPortfolio(estMu, estCov),
      };

      const nextIdx = r < rebalIdx.length - 1 ? rebalIdx[r + 1] : nTotal;
      for (let k = idx; k < nextIdx; k++) {
        outDates.push(dates[k]);
        for (const method of Object.keys(weights)) {
          const w = weights[method];
          let portRet = 0;
          for (let j = 0; j < n; j++) portRet += w[j] * returns[j][k];
          methodRets[method].push(portRet);
        }
      }
    }

    // Cumulative series
    const series = Object.fromEntries(
      Object.entries(methodRets).map(([method, rets]) => {
        const cum: number[] = [100];
        for (const r of rets) cum.push(cum[cum.length - 1] * (1 + r));
        return [method, cum];
      }),
    );

    const metrics = Object.entries(methodRets).map(([method, rets]) => ({
      method, ...computeOosMetrics(rets),
    }));

    return { methodRets, dates: outDates, series, metrics, rebalIdx };
  }, [retData, rebal, estWindow, nTotal]);

  if (!wf) {
    return (
      <div className="card text-sm text-text-muted py-6">
        Not enough data for walk-forward with this window. Try a shorter estimation window.
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="card card-compact">
        <p className="text-xs text-text-muted mb-3">
          Walks forward: optimize weights using only past {estWindow}D, hold until next rebalance. Eliminates look-ahead bias.
        </p>
        <div className="flex items-end gap-4 flex-wrap">
          <div>
            <label className="metric-label">Rebalance</label>
            <div className="flex gap-1 mt-0.5">
              {(["Monthly", "Quarterly"] as const).map(r => (
                <button
                  key={r}
                  onClick={() => setRebal(r)}
                  className={`px-2 py-1 text-xs rounded ${rebal === r ? "bg-accent text-white" : "text-text-muted hover:bg-surface-alt"}`}
                >
                  {r}
                </button>
              ))}
            </div>
          </div>
          <div className="flex-1 min-w-[220px]">
            <label className="metric-label">Estimation window: <b className="text-text">{estWindow} days</b></label>
            <input
              type="range" min={63} max={maxEst} step={21}
              value={estWindow}
              onChange={e => setEstWindow(parseInt(e.target.value))}
              className="w-full mt-1"
            />
          </div>
          <div className="text-xs text-text-muted">
            {nTotal} trading days total · {wf.rebalIdx.length} rebalances
          </div>
        </div>
      </div>

      <div className="card">
        <Plot
          data={Object.entries(wf.series).map(([method, cum]) => ({
            x: wf.dates.slice(0, cum.length - 1),
            y: cum.slice(1),
            type: "scatter", mode: "lines",
            name: method,
            line: { color: METHOD_COLORS[method] ?? t.muted, width: method === "Tangency (Max Sharpe)" ? 2.5 : 1.5 },
          }))}
          layout={{
            height: CHART_HEIGHT.tall + 40, ...L,
            title: { text: `Walk-Forward Backtest (${rebal}, ${estWindow}D Window, base=100)`, font: { size: 14, color: t.text } },
            yaxis: { title: { text: "Portfolio Value" }, gridcolor: t.grid },
            xaxis: { gridcolor: t.grid },
            legend: { orientation: "h", y: -0.15 },
            margin: { l: 60, r: 20, t: 40, b: 60 },
            shapes: [{ type: "line", y0: 100, y1: 100, x0: 0, x1: 1, xref: "paper", line: { color: t.muted, dash: "dash" } }],
          }}
          config={{ displayModeBar: false, responsive: true }}
          style={{ width: "100%" }}
        />
      </div>

      <div className="card">
        <div className="font-semibold text-sm mb-2">Out-of-Sample Performance</div>
        <div className="overflow-x-auto">
          <table className="w-full text-xs font-data">
            <thead className="border-b border-border text-text-muted">
              <tr>
                <th className="text-left py-1.5 px-2">Method</th>
                <th className="text-right py-1.5 px-2">Total Return</th>
                <th className="text-right py-1.5 px-2">Ann. Return</th>
                <th className="text-right py-1.5 px-2">Ann. Vol</th>
                <th className="text-right py-1.5 px-2">Sharpe</th>
                <th className="text-right py-1.5 px-2">Max DD</th>
              </tr>
            </thead>
            <tbody>
              {wf.metrics.map(m => (
                <tr key={m.method} className="border-b border-border/50 hover:bg-surface-alt">
                  <td className="py-1 px-2 font-semibold" style={{ color: METHOD_COLORS[m.method] ?? t.text }}>{m.method}</td>
                  <td className={`py-1 px-2 text-right ${m.totalRet > 0 ? "text-gain" : "text-loss"}`}>
                    {m.totalRet >= 0 ? "+" : ""}{m.totalRet.toFixed(1)}%
                  </td>
                  <td className="py-1 px-2 text-right">{m.annRet.toFixed(1)}%</td>
                  <td className="py-1 px-2 text-right">{m.annVol.toFixed(1)}%</td>
                  <td className="py-1 px-2 text-right">{m.sharpe.toFixed(2)}</td>
                  <td className="py-1 px-2 text-right text-loss">{m.maxDd.toFixed(1)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════
// Tab 4 — Black-Litterman
// ═══════════════════════════════════════════════

interface View {
  ticker: string;        // "" = unused slot
  annReturn: number;     // %
  confidence: "Low" | "Medium" | "High";
}

const CONF_SCALE: Record<View["confidence"], number> = { Low: 3.0, Medium: 1.0, High: 0.3 };

function BlackLittermanTab({
  retData, portfolios, t, L,
}: {
  retData: { tickers: string[]; returns: number[][]; dates: string[]; mu: number[]; vol: number[]; cov: number[][] };
  portfolios: { name: string; weights: number[]; stats: { ret: number; vol: number; sharpe: number } }[];
  t: ChartTheme;
  L: ReturnType<typeof getBaseLayout>;
}) {
  const [riskAversion, setRiskAversion] = useState(2.5);
  const [tau, setTau] = useState(0.05);
  const [views, setViews] = useState<View[]>(
    Array.from({ length: 5 }, () => ({ ticker: "", annReturn: 0, confidence: "Medium" as const })),
  );

  const { tickers, cov: annCov } = retData;
  const n = tickers.length;
  const wEqual = equalWeight(n);

  // Implied equilibrium returns: pi = delta * cov * w_equal
  const pi = useMemo(() => {
    const prod = matVecMul(annCov, wEqual);
    return prod.map(v => riskAversion * v);
  }, [annCov, riskAversion]);

  const wTangency = useMemo(() => portfolios.find(p => p.name === "Tangency (Max Sharpe)")?.weights ?? tangencyPortfolio(retData.mu, annCov), [portfolios, retData.mu, annCov]);

  const activeViews = views.filter(v => v.ticker && v.annReturn !== 0 && tickers.includes(v.ticker));

  const bl = useMemo(() => {
    if (activeViews.length === 0) return null;
    const P: number[][] = activeViews.map(v => {
      const row = Array(n).fill(0);
      row[tickers.indexOf(v.ticker)] = 1;
      return row;
    });
    const Q = activeViews.map(v => v.annReturn / 100);
    // base Omega = diag(diag(tau * P * Sigma * P'))
    const tauCov: number[][] = annCov.map(row => row.map(v => v * tau));
    const PtauCov = matMul(P, tauCov);
    const PSigmaPt = matMul(PtauCov, transpose(P));
    const baseOmega: number[][] = PSigmaPt.map((row, i) => row.map((v, j) => i === j ? v : 0));
    const omega: number[][] = baseOmega.map((row, i) => row.map((v, j) => i === j ? v * CONF_SCALE[activeViews[i].confidence] : 0));

    const invTauCov = invMatrix(tauCov);
    const invOmega = invMatrix(omega);
    if (!invTauCov || !invOmega) return { singular: true, pi, bl_mu: pi, w_bl: wTangency, shift: pi.map(() => 0) };

    const Pt = transpose(P);
    // posterior covariance of returns: inv(invTauCov + P' * invOmega * P)
    const middle = matAdd(invTauCov, matMul(matMul(Pt, invOmega), P));
    const blCovPost = invMatrix(middle);
    if (!blCovPost) return { singular: true, pi, bl_mu: pi, w_bl: wTangency, shift: pi.map(() => 0) };

    // posterior mean
    const rhs1 = matVecMul(invTauCov, pi);
    const rhs2 = matVecMul(matMul(Pt, invOmega), Q);
    const rhs = rhs1.map((v, i) => v + rhs2[i]);
    const bl_mu = matVecMul(blCovPost, rhs);

    // tangency with bl_mu and Sigma + bl_cov
    const combinedCov = matAdd(annCov, blCovPost);
    const w_bl = tangencyPortfolio(bl_mu, combinedCov);

    return {
      singular: false,
      pi, bl_mu, w_bl,
      shift: bl_mu.map((v, i) => v - pi[i]),
    };
  }, [activeViews, n, tickers, annCov, tau, pi, wTangency]);

  const piSorted = [...tickers.map((tk, i) => ({ tk, v: pi[i] * 100 }))].sort((a, b) => a.v - b.v);

  const updateView = (i: number, patch: Partial<View>) => {
    setViews(prev => prev.map((v, idx) => idx === i ? { ...v, ...patch } : v));
  };

  return (
    <div className="space-y-4">
      <div className="card card-compact">
        <div className="text-sm font-semibold mb-1">Black-Litterman: blend market equilibrium with your views</div>
        <div className="text-xs text-text-muted">
          Start from the market&apos;s implied equilibrium returns, apply confident views, get a posterior that tilts toward your beliefs without over-concentrating.
        </div>
      </div>

      <div className="card">
        <div className="font-semibold text-sm mb-2">Step 1 — Market Equilibrium</div>
        <div className="flex items-end gap-4 flex-wrap mb-3">
          <div className="min-w-[220px]">
            <label className="metric-label">Risk aversion (δ): <b className="text-text">{riskAversion.toFixed(1)}</b></label>
            <input type="range" min={1} max={5} step={0.5} value={riskAversion}
              onChange={e => setRiskAversion(parseFloat(e.target.value))}
              className="w-full mt-1" />
          </div>
          <div className="min-w-[220px]">
            <label className="metric-label">View confidence (τ): <b className="text-text">{tau.toFixed(2)}</b></label>
            <input type="range" min={0.01} max={0.5} step={0.01} value={tau}
              onChange={e => setTau(parseFloat(e.target.value))}
              className="w-full mt-1" />
          </div>
        </div>
        <Plot
          data={[{
            type: "bar", orientation: "h",
            y: piSorted.map(p => p.tk),
            x: piSorted.map(p => p.v),
            marker: { color: piSorted.map(p => p.v >= 0 ? t.accent : t.loss) },
            text: piSorted.map(p => `${p.v >= 0 ? "+" : ""}${p.v.toFixed(1)}%`),
            textposition: "outside",
          }]}
          layout={{
            height: Math.max(250, n * 30), ...L,
            title: { text: "Implied Equilibrium Returns (Annualized %)", font: { size: 13, color: t.text } },
            xaxis: { title: { text: "Expected Return (%)" }, gridcolor: t.grid },
            yaxis: { gridcolor: t.grid },
            margin: { l: 60, r: 60, t: 40, b: 40 },
            shapes: [{ type: "line", x0: 0, x1: 0, y0: 0, y1: 1, yref: "paper", line: { color: t.muted, dash: "dash" } }],
          }}
          config={{ displayModeBar: false, responsive: true }}
          style={{ width: "100%" }}
        />
      </div>

      <div className="card">
        <div className="font-semibold text-sm mb-1">Step 2 — Your Views</div>
        <div className="text-xs text-text-muted mb-3">
          Enter up to 5 individual-asset views. Skip unused slots (leave ticker blank or return at 0).
        </div>
        <div className="space-y-2">
          {views.map((v, i) => (
            <div key={i} className="flex gap-2 items-center flex-wrap">
              <select value={v.ticker} onChange={e => updateView(i, { ticker: e.target.value })}
                className="px-2 py-1 text-xs border border-border rounded bg-surface min-w-[100px] font-data">
                <option value="">—</option>
                {tickers.map(tk => <option key={tk} value={tk}>{tk}</option>)}
              </select>
              <input type="number" step="0.5" value={v.annReturn} onChange={e => updateView(i, { annReturn: parseFloat(e.target.value) || 0 })}
                placeholder="Ann. Return %" className="w-28 px-2 py-1 text-xs border border-border rounded bg-surface font-data" />
              <span className="text-xs text-text-muted">% ann.</span>
              <select value={v.confidence} onChange={e => updateView(i, { confidence: e.target.value as View["confidence"] })}
                className="px-2 py-1 text-xs border border-border rounded bg-surface">
                <option value="Low">Low</option>
                <option value="Medium">Medium</option>
                <option value="High">High</option>
              </select>
            </div>
          ))}
        </div>
      </div>

      {!bl && (
        <div className="card text-xs text-text-muted py-4">
          Enter at least one view above to see how Black-Litterman blends your beliefs with the market equilibrium.
        </div>
      )}

      {bl && bl.singular && (
        <div className="card text-xs text-loss py-4">
          Black-Litterman posterior is singular — views may be inconsistent. Try reducing confidence or removing conflicting views.
        </div>
      )}

      {bl && !bl.singular && (
        <>
          <div className="card">
            <div className="font-semibold text-sm mb-1">Step 3 — Blended Returns & Weights</div>
            <div className="text-xs text-text-muted mb-2">
              How your views shifted the expected returns from equilibrium. Large shifts on viewed assets, small ripple effects on correlated assets.
            </div>
            <Plot
              data={[
                {
                  type: "bar", x: tickers, y: pi.map(v => v * 100),
                  name: "Equilibrium", marker: { color: t.muted },
                },
                {
                  type: "bar", x: tickers, y: bl.bl_mu.map(v => v * 100),
                  name: "BL Posterior", marker: { color: t.accent },
                },
              ]}
              layout={{
                height: 380, ...L, barmode: "group",
                title: { text: "Expected Returns: Equilibrium → BL Posterior", font: { size: 13, color: t.text } },
                yaxis: { title: { text: "Ann. Return (%)" }, gridcolor: t.grid },
                xaxis: { gridcolor: t.grid },
                legend: { orientation: "h", y: -0.18 },
                margin: { l: 60, r: 20, t: 40, b: 60 },
                annotations: bl.shift.map((s, i) => (Math.abs(s) > 0.001 ? {
                  x: tickers[i], y: Math.max(pi[i] * 100, bl.bl_mu[i] * 100) + 0.5,
                  text: `${s * 100 >= 0 ? "+" : ""}${(s * 100).toFixed(1)}%`,
                  showarrow: false, font: { size: 9, color: t.spot },
                } : null)).filter((x): x is NonNullable<typeof x> => x !== null),
              }}
              config={{ displayModeBar: false, responsive: true }}
              style={{ width: "100%" }}
            />
          </div>

          <div className="card">
            <div className="font-semibold text-sm mb-1">Portfolio Weights</div>
            <div className="text-xs text-text-muted mb-2">BL tilts toward viewed assets while maintaining diversification.</div>
            <Plot
              data={[
                { type: "bar", x: tickers, y: bl.w_bl.map(v => v * 100), name: "Black-Litterman", marker: { color: t.loss } },
                { type: "bar", x: tickers, y: wTangency.map(v => v * 100), name: "Tangency (no views)", marker: { color: t.accent } },
                { type: "bar", x: tickers, y: wEqual.map(v => v * 100), name: "Equal Weight", marker: { color: t.muted } },
              ]}
              layout={{
                height: 380, ...L, barmode: "group",
                yaxis: { title: { text: "Weight (%)" }, gridcolor: t.grid },
                xaxis: { gridcolor: t.grid },
                legend: { orientation: "h", y: -0.18 },
                margin: { l: 60, r: 20, t: 10, b: 60 },
              }}
              config={{ displayModeBar: false, responsive: true }}
              style={{ width: "100%" }}
            />
            {(() => {
              const stats = portStats(bl.w_bl, retData.mu, annCov);
              const hhi = bl.w_bl.reduce((s, v) => s + v * v, 0);
              return (
                <div className="flex flex-wrap gap-6 mt-3">
                  <Metric label="BL Exp. Return" value={`${(stats.ret * 100).toFixed(1)}%`} />
                  <Metric label="BL Exp. Vol" value={`${(stats.vol * 100).toFixed(1)}%`} />
                  <Metric label="BL Sharpe" value={stats.sharpe.toFixed(2)} />
                  <Metric label="Positions > 1%" value={`${bl.w_bl.filter(v => v > 0.01).length} / ${n}`} />
                  <Metric label="Effective N" value={hhi > 0 ? (1 / hhi).toFixed(1) : "—"} />
                </div>
              );
            })()}
          </div>

          <div className="card">
            <div className="font-semibold text-sm mb-1">View Impact Summary</div>
            <div className="overflow-x-auto">
              <table className="w-full text-xs font-data">
                <thead className="border-b border-border text-text-muted">
                  <tr>
                    <th className="text-left py-1.5 px-2">Asset</th>
                    <th className="text-right py-1.5 px-2">Your View</th>
                    <th className="text-left py-1.5 px-2">Confidence</th>
                    <th className="text-right py-1.5 px-2">Equilibrium</th>
                    <th className="text-right py-1.5 px-2">BL Posterior</th>
                    <th className="text-right py-1.5 px-2">Return Shift</th>
                    <th className="text-right py-1.5 px-2">Weight Shift</th>
                  </tr>
                </thead>
                <tbody>
                  {activeViews.map(v => {
                    const idx = tickers.indexOf(v.ticker);
                    const eqRet = pi[idx] * 100;
                    const blRet = bl.bl_mu[idx] * 100;
                    const eqWt = wEqual[idx] * 100;
                    const blWt = bl.w_bl[idx] * 100;
                    return (
                      <tr key={v.ticker} className="border-b border-border/50 hover:bg-surface-alt">
                        <td className="py-1 px-2 font-semibold">{v.ticker}</td>
                        <td className="py-1 px-2 text-right">{v.annReturn >= 0 ? "+" : ""}{v.annReturn.toFixed(1)}%</td>
                        <td className="py-1 px-2">{v.confidence}</td>
                        <td className="py-1 px-2 text-right">{eqRet.toFixed(1)}%</td>
                        <td className="py-1 px-2 text-right">{blRet.toFixed(1)}%</td>
                        <td className="py-1 px-2 text-right" style={{ color: blRet - eqRet >= 0 ? t.gain : t.loss }}>
                          {blRet - eqRet >= 0 ? "+" : ""}{(blRet - eqRet).toFixed(1)}%
                        </td>
                        <td className="py-1 px-2 text-right" style={{ color: blWt - eqWt >= 0 ? t.gain : t.loss }}>
                          {blWt - eqWt >= 0 ? "+" : ""}{(blWt - eqWt).toFixed(1)}%
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

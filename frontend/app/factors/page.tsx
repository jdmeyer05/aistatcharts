"use client";

import { useState, useMemo } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTheme } from "next-themes";
import { fetchFamaFrench, fetchPriceHistory, type FFRecord } from "@/lib/api";
import { getChartTheme, getBaseLayout } from "@/lib/chart-theme";
import { Metric } from "@/components/ui/metric";
import dynamic from "next/dynamic";

const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

const TABS = ["Factor Returns", "Factor Exposure", "Alpha Attribution", "Rolling Style"];

const FACTOR_COLORS: Record<string, string> = {
  "Mkt-RF": "#00d1ff", SMB: "#3fb950", HML: "#f59e0b", RMW: "#f85149", CMA: "#a78bfa",
};
const FACTOR_NAMES: Record<string, string> = {
  "Mkt-RF": "Market", SMB: "Size", HML: "Value", RMW: "Profitability", CMA: "Investment",
};
const FACTORS = ["Mkt-RF", "SMB", "HML", "RMW", "CMA"] as const;

// Simple OLS regression: y = Xb + e
function ols(y: number[], X: number[][]): { betas: number[]; r2: number; alpha: number } {
  const n = y.length;
  const k = X[0].length;
  if (n < k + 5) return { betas: Array(k).fill(0), r2: 0, alpha: 0 };

  // Add intercept
  const Xa = X.map(row => [1, ...row]);
  const kk = k + 1;

  // X'X
  const XtX: number[][] = Array.from({ length: kk }, () => Array(kk).fill(0));
  const Xty: number[] = Array(kk).fill(0);
  for (let i = 0; i < n; i++) {
    for (let j = 0; j < kk; j++) {
      Xty[j] += Xa[i][j] * y[i];
      for (let m = 0; m < kk; m++) XtX[j][m] += Xa[i][j] * Xa[i][m];
    }
  }

  // Solve via Gaussian elimination
  const aug = XtX.map((row, i) => [...row, Xty[i]]);
  for (let col = 0; col < kk; col++) {
    let maxRow = col;
    for (let row = col + 1; row < kk; row++) if (Math.abs(aug[row][col]) > Math.abs(aug[maxRow][col])) maxRow = row;
    [aug[col], aug[maxRow]] = [aug[maxRow], aug[col]];
    if (Math.abs(aug[col][col]) < 1e-12) continue;
    for (let row = col + 1; row < kk; row++) {
      const f = aug[row][col] / aug[col][col];
      for (let j = col; j <= kk; j++) aug[row][j] -= f * aug[col][j];
    }
  }
  const b = Array(kk).fill(0);
  for (let i = kk - 1; i >= 0; i--) {
    b[i] = aug[i][kk];
    for (let j = i + 1; j < kk; j++) b[i] -= aug[i][j] * b[j];
    b[i] /= aug[i][i] || 1;
  }

  // R²
  const yMean = y.reduce((s, v) => s + v, 0) / n;
  let ssTot = 0, ssRes = 0;
  for (let i = 0; i < n; i++) {
    let yHat = 0;
    for (let j = 0; j < kk; j++) yHat += Xa[i][j] * b[j];
    ssRes += (y[i] - yHat) ** 2;
    ssTot += (y[i] - yMean) ** 2;
  }
  const r2 = ssTot > 0 ? 1 - ssRes / ssTot : 0;

  return { betas: b.slice(1), r2, alpha: b[0] * 252 * 100 }; // annualized alpha in %
}

export default function FactorDecomposition() {
  const { resolvedTheme } = useTheme();
  const t = getChartTheme(resolvedTheme === "dark");
  const L = getBaseLayout(t);
  const [activeTab, setActiveTab] = useState(0);
  const [ticker, setTicker] = useState("SPY");
  const [factors, setFactors] = useState<FFRecord[]>([]);
  const [stockReturns, setStockReturns] = useState<{ date: string; ret: number }[]>([]);

  const load = useMutation({
    mutationFn: async (tk: string) => {
      const [ff, hist] = await Promise.all([fetchFamaFrench(504), fetchPriceHistory(tk, 504)]);
      return { ff: ff.factors, hist: hist.data };
    },
    onSuccess: (d) => {
      setFactors(d.ff);
      const closes = d.hist;
      const rets = closes.slice(1).map((bar, i) => ({
        date: bar.Date,
        ret: closes[i].Close > 0 ? (bar.Close - closes[i].Close) / closes[i].Close : 0,
      }));
      setStockReturns(rets);
    },
  });

  // Match dates between stock returns and factor data
  const matched = useMemo(() => {
    if (factors.length === 0 || stockReturns.length === 0) return null;
    const ffMap = new Map(factors.map(f => [f.date, f]));
    const rows: { date: string; stockRet: number; ff: FFRecord }[] = [];
    for (const sr of stockReturns) {
      const ff = ffMap.get(sr.date);
      if (ff) rows.push({ date: sr.date, stockRet: sr.ret, ff });
    }
    if (rows.length < 30) return null;

    const y = rows.map(r => r.stockRet - r.ff.RF);
    const X = rows.map(r => FACTORS.map(f => r.ff[f]));
    const reg = ols(y, X);

    return { rows, y, X, reg, n: rows.length };
  }, [factors, stockReturns]);

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Factor Decomposition</h1>
        <p className="text-text-secondary text-sm mt-1">Fama-French 5-factor regression: exposure betas, alpha attribution, rolling style drift.</p>
      </div>

      <div className="card card-compact">
        <div className="flex items-center gap-3">
          <input type="text" value={ticker} onChange={e => setTicker(e.target.value.toUpperCase())}
            onKeyDown={e => e.key === "Enter" && load.mutate(ticker)}
            placeholder="SPY" className="w-32 px-3 py-2 border border-border rounded-lg text-sm font-data bg-surface" />
          <button onClick={() => load.mutate(ticker)} disabled={load.isPending}
            className="px-6 py-2 bg-accent text-white font-semibold rounded-lg hover:bg-accent-hover disabled:opacity-50 text-sm">
            {load.isPending ? "Loading..." : "Decompose"}
          </button>
        </div>
      </div>

      {load.isPending && (
        <div className="card text-center py-12">
          <div className="inline-block w-6 h-6 border-2 border-accent border-t-transparent rounded-full animate-spin" />
          <p className="text-sm text-text-muted mt-3">Fetching FF factors + price history...</p>
        </div>
      )}

      {matched && (
        <>
          <div className="card card-compact">
            <div className="flex flex-wrap gap-6">
              <Metric label="Observations" value={String(matched.n)} />
              <Metric label="R²" value={`${(matched.reg.r2 * 100).toFixed(1)}%`} />
              <Metric label="Ann. Alpha" value={`${matched.reg.alpha > 0 ? "+" : ""}${matched.reg.alpha.toFixed(2)}%`}
                deltaType={matched.reg.alpha > 0 ? "gain" : "loss"} />
              {FACTORS.map((f, i) => (
                <Metric key={f} label={`β ${FACTOR_NAMES[f]}`} value={matched.reg.betas[i].toFixed(3)} />
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

          {/* Tab 0: Factor Returns */}
          {activeTab === 0 && (
            <div className="card">
              <Plot data={FACTORS.map(f => {
                const cumRet: number[] = [];
                let cum = 1;
                for (const row of matched.rows) { cum *= (1 + row.ff[f]); cumRet.push((cum - 1) * 100); }
                return { x: matched.rows.map(r => r.date), y: cumRet, type: "scatter" as const, mode: "lines" as const,
                  name: FACTOR_NAMES[f], line: { color: FACTOR_COLORS[f], width: 1.5 } };
              })}
                layout={{ height: 400, ...L, yaxis: { title: "Cumulative Return (%)", gridcolor: t.grid }, xaxis: { gridcolor: t.grid }, hovermode: "x unified",
                  legend: { x: 0.01, y: 0.99, bgcolor: "transparent" } }}
                config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
            </div>
          )}

          {/* Tab 1: Factor Exposure — betas bar chart */}
          {activeTab === 1 && (
            <div className="card">
              <Plot data={[{
                x: FACTORS.map(f => FACTOR_NAMES[f]),
                y: matched.reg.betas,
                type: "bar" as const,
                marker: { color: FACTORS.map(f => FACTOR_COLORS[f]) },
                text: matched.reg.betas.map(b => b.toFixed(3)),
                textposition: "outside" as const,
                textfont: { size: 11, color: t.text },
              }]} layout={{ height: 350, ...L, yaxis: { title: "Beta", gridcolor: t.grid },
                shapes: [{ type: "line", y0: 0, y1: 0, x0: 0, x1: 1, xref: "paper", line: { color: t.muted, width: 1 } }] }}
                config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
            </div>
          )}

          {/* Tab 2: Alpha Attribution — waterfall */}
          {activeTab === 2 && (
            <div className="card space-y-4">
              {(() => {
                const totalRet = matched.rows.reduce((s, r) => s + r.stockRet, 0) * 252 / matched.n * 100;
                const rf = matched.rows.reduce((s, r) => s + r.ff.RF, 0) * 252 / matched.n * 100;
                const factorContribs = FACTORS.map((f, i) => ({
                  name: FACTOR_NAMES[f],
                  value: matched.reg.betas[i] * matched.rows.reduce((s, r) => s + r.ff[f], 0) * 252 / matched.n * 100,
                  color: FACTOR_COLORS[f],
                }));
                const explained = factorContribs.reduce((s, c) => s + c.value, 0);

                return (<>
                  <div className="flex gap-6">
                    <Metric label={`${ticker} Ann. Return`} value={`${totalRet.toFixed(1)}%`} />
                    <Metric label="Risk-Free" value={`${rf.toFixed(1)}%`} />
                    <Metric label="Factor-Explained" value={`${explained.toFixed(1)}%`} />
                    <Metric label="Alpha (Unexplained)" value={`${matched.reg.alpha.toFixed(2)}%`} deltaType={matched.reg.alpha > 0 ? "gain" : "loss"} />
                  </div>
                  <Plot data={[{
                    x: [...factorContribs.map(c => c.name), "Alpha"],
                    y: [...factorContribs.map(c => c.value), matched.reg.alpha],
                    type: "bar" as const,
                    marker: { color: [...factorContribs.map(c => c.color), matched.reg.alpha > 0 ? t.gain : t.loss] },
                    text: [...factorContribs.map(c => `${c.value > 0 ? "+" : ""}${c.value.toFixed(1)}%`), `${matched.reg.alpha > 0 ? "+" : ""}${matched.reg.alpha.toFixed(2)}%`],
                    textposition: "outside" as const, textfont: { size: 10, color: t.text },
                  }]} layout={{ height: 350, ...L, yaxis: { title: "Ann. Return Attribution (%)", gridcolor: t.grid },
                    shapes: [{ type: "line", y0: 0, y1: 0, x0: 0, x1: 1, xref: "paper", line: { color: t.muted, width: 1 } }] }}
                    config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
                </>);
              })()}
            </div>
          )}

          {/* Tab 3: Rolling Style — 60-day rolling betas */}
          {activeTab === 3 && (
            <div className="card">
              {(() => {
                const window = 60;
                if (matched.n < window + 10) return <p className="text-sm text-text-muted">Need more data for rolling analysis.</p>;
                const rollingBetas: Record<string, number[]> = {};
                FACTORS.forEach(f => { rollingBetas[f] = []; });
                const rollingDates: string[] = [];

                for (let end = window; end <= matched.n; end++) {
                  const ySlice = matched.y.slice(end - window, end);
                  const xSlice = matched.X.slice(end - window, end);
                  const reg = ols(ySlice, xSlice);
                  rollingDates.push(matched.rows[end - 1].date);
                  FACTORS.forEach((f, i) => rollingBetas[f].push(reg.betas[i]));
                }

                return (
                  <Plot data={FACTORS.map(f => ({
                    x: rollingDates, y: rollingBetas[f],
                    type: "scatter" as const, mode: "lines" as const,
                    name: FACTOR_NAMES[f], line: { color: FACTOR_COLORS[f], width: 1.5 },
                  }))}
                    layout={{ height: 400, ...L, yaxis: { title: "Rolling 60d Beta", gridcolor: t.grid }, xaxis: { gridcolor: t.grid }, hovermode: "x unified",
                      shapes: [{ type: "line", y0: 0, y1: 0, x0: 0, x1: 1, xref: "paper", line: { color: t.muted, width: 1 } }],
                      legend: { x: 0.01, y: 0.99, bgcolor: "transparent" } }}
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

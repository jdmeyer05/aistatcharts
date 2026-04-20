"use client";

import { useState, useMemo } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useTheme } from "next-themes";
import { fetchFamaFrench, fetchPriceHistory, type FFRecord } from "@/lib/api";
import { getChartTheme, getBaseLayout } from "@/lib/chart-theme";
import { Metric } from "@/components/ui/metric";
import { AIInterpretation } from "@/components/ai-interpretation";
import { Plot } from "@/components/plot";


const TABS = ["Factor Returns", "Factor Exposure", "Alpha Attribution", "Factor Timing", "Risk Decomposition"];

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
  const k = X[0]?.length ?? 0;
  if (k === 0 || n < k + 5) return { betas: Array(k).fill(0), r2: 0, alpha: 0 };

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
  const [stockReturns, setStockReturns] = useState<{ date: string; ret: number }[]>([]);

  // FF factor data is the same regardless of ticker — fetch once, reuse.
  // staleTime 24h: FF releases daily with a lag; no need to refetch within a session.
  const ffQ = useQuery({
    queryKey: ["ff-factors", 504],
    queryFn: () => fetchFamaFrench(504),
    staleTime: 24 * 60 * 60 * 1000,
  });
  const factors: FFRecord[] = ffQ.data?.factors ?? [];

  const load = useMutation({
    mutationFn: async (tk: string) => {
      const hist = await fetchPriceHistory(tk, 504);
      return { hist: hist.data };
    },
    onSuccess: (d) => {
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

  // Tab 0: precompute cumulative factor returns once per matched dataset.
  const cumFactorReturns = useMemo(() => {
    if (!matched) return null;
    const xs = matched.rows.map(r => r.date);
    const series = FACTORS.map(f => {
      const y: number[] = [];
      let cum = 1;
      for (const row of matched.rows) { cum *= (1 + row.ff[f]); y.push((cum - 1) * 100); }
      return { factor: f, y };
    });
    return { xs, series };
  }, [matched]);

  // Tab 2: precompute alpha attribution once per matched dataset.
  const attribution = useMemo(() => {
    if (!matched) return null;
    const n = matched.n;
    const totalRet = matched.rows.reduce((s, r) => s + r.stockRet, 0) * 252 / n * 100;
    const rf = matched.rows.reduce((s, r) => s + r.ff.RF, 0) * 252 / n * 100;
    const factorContribs = FACTORS.map((f, i) => ({
      name: FACTOR_NAMES[f],
      value: matched.reg.betas[i] * matched.rows.reduce((s, r) => s + r.ff[f], 0) * 252 / n * 100,
      color: FACTOR_COLORS[f],
    }));
    const explained = factorContribs.reduce((s, c) => s + c.value, 0);
    return { totalRet, rf, factorContribs, explained };
  }, [matched]);

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Factor Decomposition</h1>
        <p className="text-text-secondary text-sm mt-1">Fama-French 5-factor regression: exposure betas, alpha attribution, rolling style drift.</p>
      </div>

      <div className="card card-compact">
        <div className="flex items-center gap-3">
          <input type="text" value={ticker} onChange={e => setTicker(e.target.value.toUpperCase())}
            onKeyDown={e => {
              if (e.key === "Enter" && !load.isPending && ticker && factors.length > 0) load.mutate(ticker);
            }}
            placeholder="SPY" className="w-32 px-3 py-2 border border-border rounded-lg text-sm font-data bg-surface" />
          <button onClick={() => load.mutate(ticker)} disabled={load.isPending || !ticker || factors.length === 0}
            className="px-6 py-2 bg-accent text-white font-semibold rounded-lg hover:bg-accent-hover disabled:opacity-50 text-sm">
            {load.isPending ? "Loading..." : factors.length === 0 ? "Loading factors..." : "Decompose"}
          </button>
          <div className="text-[11px] text-text-muted ml-auto">
            Fama-French 5-factor model: Market, Size, Value, Profitability, Investment. 504 trading days (~2y).
          </div>
        </div>
      </div>

      {ffQ.isError && (
        <div className="card border-loss/30 bg-loss-bg text-loss text-sm flex items-center justify-between">
          <span>Failed to load Fama-French factor data: {(ffQ.error as Error)?.message ?? "unknown error"}.</span>
          <button onClick={() => ffQ.refetch()} className="px-3 py-1 text-xs rounded border border-loss hover:bg-loss/10">Retry</button>
        </div>
      )}

      {load.isPending && (
        <div className="card text-center py-12">
          <div className="inline-block w-6 h-6 border-2 border-accent border-t-transparent rounded-full animate-spin" />
          <p className="text-sm text-text-muted mt-3">Fetching {ticker} price history…</p>
        </div>
      )}

      {matched && (
        <>
          <div className="card card-compact">
            <div className="flex flex-wrap gap-6 items-center">
              <Metric label="Observations" value={String(matched.n)} />
              <div>
                <div className="metric-label">R²</div>
                <div className="text-xl font-bold" style={{ color: matched.reg.r2 > 0.7 ? t.gain : matched.reg.r2 > 0.4 ? t.hv20 : t.loss }}>
                  {(matched.reg.r2 * 100).toFixed(1)}%
                </div>
                <div className="w-24 h-1 bg-surface-alt rounded-full overflow-hidden mt-1">
                  <div
                    className="h-full rounded-full transition-all"
                    style={{
                      width: `${Math.min(100, Math.max(0, matched.reg.r2 * 100))}%`,
                      background: matched.reg.r2 > 0.7 ? t.gain : matched.reg.r2 > 0.4 ? t.hv20 : t.loss,
                    }}
                  />
                </div>
              </div>
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
          {activeTab === 0 && cumFactorReturns && (
            <div className="card">
              <Plot data={cumFactorReturns.series.map(s => ({
                x: cumFactorReturns.xs, y: s.y,
                type: "scatter" as const, mode: "lines" as const,
                name: FACTOR_NAMES[s.factor], line: { color: FACTOR_COLORS[s.factor], width: 1.5 },
              }))}
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
          {activeTab === 2 && attribution && (
            <div className="card space-y-4">
              <div className="flex gap-6 flex-wrap">
                <Metric label={`${ticker} Ann. Return`} value={`${attribution.totalRet.toFixed(1)}%`} />
                <Metric label="Risk-Free" value={`${attribution.rf.toFixed(1)}%`} />
                <Metric label="Factor-Explained" value={`${attribution.explained.toFixed(1)}%`} />
                <Metric label="Alpha (Unexplained)" value={`${matched.reg.alpha.toFixed(2)}%`} deltaType={matched.reg.alpha > 0 ? "gain" : "loss"} />
              </div>
              <Plot data={[{
                x: [...attribution.factorContribs.map(c => c.name), "Alpha"],
                y: [...attribution.factorContribs.map(c => c.value), matched.reg.alpha],
                type: "bar" as const,
                marker: { color: [...attribution.factorContribs.map(c => c.color), matched.reg.alpha > 0 ? t.gain : t.loss] },
                text: [...attribution.factorContribs.map(c => `${c.value > 0 ? "+" : ""}${c.value.toFixed(1)}%`), `${matched.reg.alpha > 0 ? "+" : ""}${matched.reg.alpha.toFixed(2)}%`],
                textposition: "outside" as const, textfont: { size: 10, color: t.text },
              }]} layout={{ height: 350, ...L, yaxis: { title: "Ann. Return Attribution (%)", gridcolor: t.grid },
                shapes: [{ type: "line", y0: 0, y1: 0, x0: 0, x1: 1, xref: "paper", line: { color: t.muted, width: 1 } }] }}
                config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
            </div>
          )}

          {/* Tab 3: Factor Timing — rolling betas/alpha/R2 + style drift */}
          {activeTab === 3 && (
            <FactorTimingTab matched={matched} fullAlpha={matched.reg.alpha} fullR2={matched.reg.r2} t={t} L={L} />
          )}

          {/* Tab 4: Risk Decomposition — variance attribution */}
          {activeTab === 4 && (
            <RiskDecompositionTab matched={matched} t={t} L={L} />
          )}

          <AIInterpretation
            page="factors"
            subject={ticker}
            data={{
              ticker,
              observations: matched.n,
              r_squared: matched.reg.r2,
              annualized_alpha_pct: matched.reg.alpha,
              betas: {
                market: matched.reg.betas[0],
                size: matched.reg.betas[1],
                value: matched.reg.betas[2],
                profitability: matched.reg.betas[3],
                investment: matched.reg.betas[4],
              },
              attribution: attribution ? {
                ticker_annual_return_pct: attribution.totalRet,
                risk_free_pct: attribution.rf,
                factor_explained_pct: attribution.explained,
                alpha_pct: matched.reg.alpha,
                factor_contribs: attribution.factorContribs.map((c) => ({
                  factor: c.name,
                  contribution_pct: c.value,
                })),
              } : null,
            }}
          />
        </>
      )}

      {load.isError && <div className="card border-loss/30 bg-loss-bg text-loss text-sm">Failed: {(load.error as Error).message}</div>}
    </div>
  );
}

// ═══════════════════════════════════════════════
// Tab 3 — Factor Timing
// ═══════════════════════════════════════════════

interface MatchedRegression {
  rows: { date: string; stockRet: number; ff: FFRecord }[];
  y: number[];
  X: number[][];
  reg: { betas: number[]; r2: number; alpha: number };
  n: number;
}

function FactorTimingTab({
  matched, fullAlpha, fullR2, t, L,
}: {
  matched: MatchedRegression;
  fullAlpha: number;
  fullR2: number;
  t: ReturnType<typeof getChartTheme>;
  L: ReturnType<typeof getBaseLayout>;
}) {
  const [windowDays, setWindowDays] = useState<63 | 126 | 252>(126);

  const rolling = useMemo(() => {
    if (matched.n < windowDays + 30) return null;
    const betas: Record<string, number[]> = {};
    FACTORS.forEach(f => { betas[f] = []; });
    const alpha: number[] = [];
    const r2: number[] = [];
    const dates: string[] = [];
    for (let end = windowDays; end <= matched.n; end++) {
      const y = matched.y.slice(end - windowDays, end);
      const X = matched.X.slice(end - windowDays, end);
      const reg = ols(y, X);
      dates.push(matched.rows[end - 1].date);
      FACTORS.forEach((f, i) => betas[f].push(reg.betas[i]));
      alpha.push(reg.alpha);
      r2.push(reg.r2);
    }
    return { betas, alpha, r2, dates };
  }, [matched, windowDays]);

  const drift = useMemo(() => {
    if (!rolling) return [];
    const mid = Math.floor(rolling.dates.length / 2);
    return FACTORS.map(f => {
      const firstHalf = rolling.betas[f].slice(0, mid);
      const secondHalf = rolling.betas[f].slice(mid);
      const mean = (xs: number[]) => xs.length ? xs.reduce((s, v) => s + v, 0) / xs.length : 0;
      const fh = mean(firstHalf);
      const sh = mean(secondHalf);
      const change = sh - fh;
      const level = Math.abs(change) > 0.15 ? "HIGH" : Math.abs(change) > 0.05 ? "MODERATE" : "LOW";
      return { factor: FACTOR_NAMES[f], code: f, fh, sh, change, level };
    });
  }, [rolling]);

  if (!rolling) {
    return (
      <div className="card text-sm text-text-muted py-6">
        Need at least {windowDays + 30} observations for rolling analysis.
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="card card-compact">
        <p className="text-xs text-text-muted mb-2">
          Rolling factor betas show how exposure evolves over time. Stable betas = consistent style. Volatile betas = style drift or tactical allocation.
        </p>
        <div className="flex items-center gap-2">
          <span className="text-xs text-text-muted">Rolling window:</span>
          {[63, 126, 252].map(w => (
            <button
              key={w}
              onClick={() => setWindowDays(w as 63 | 126 | 252)}
              className={`px-2 py-1 text-xs rounded ${windowDays === w ? "bg-accent text-white" : "text-text-muted hover:bg-surface-alt"}`}
            >
              {w === 63 ? "63D (3M)" : w === 126 ? "126D (6M)" : "252D (1Y)"}
            </button>
          ))}
        </div>
      </div>

      <div className="card">
        <Plot
          data={FACTORS.map(f => ({
            x: rolling.dates, y: rolling.betas[f],
            type: "scatter" as const, mode: "lines" as const,
            name: FACTOR_NAMES[f], line: { color: FACTOR_COLORS[f], width: 2 },
          }))}
          layout={{
            height: 420, ...L,
            title: { text: `Rolling Factor Betas (${windowDays}D window)`, font: { size: 14, color: t.text } },
            yaxis: { title: { text: "Beta" }, gridcolor: t.grid },
            xaxis: { gridcolor: t.grid },
            legend: { orientation: "h", y: -0.15 },
            margin: { l: 60, r: 20, t: 40, b: 60 },
            shapes: [{ type: "line", y0: 0, y1: 0, x0: 0, x1: 1, xref: "paper", line: { color: t.muted, width: 1, dash: "dash" } }],
          }}
          config={{ displayModeBar: false, responsive: true }}
          style={{ width: "100%" }}
        />
      </div>

      <div className="card">
        <div className="font-semibold text-sm mb-1">Style Drift Detection</div>
        <div className="text-xs text-text-muted mb-2">
          First-half vs second-half average betas. Large changes indicate the factor profile has shifted.
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-xs font-data">
            <thead className="border-b border-border text-text-muted">
              <tr>
                <th className="text-left py-1.5 px-2">Factor</th>
                <th className="text-right py-1.5 px-2">First Half β</th>
                <th className="text-right py-1.5 px-2">Second Half β</th>
                <th className="text-right py-1.5 px-2">Change</th>
                <th className="text-left py-1.5 px-2">Drift</th>
              </tr>
            </thead>
            <tbody>
              {drift.map(d => (
                <tr key={d.code} className="border-b border-border/50 hover:bg-surface-alt">
                  <td className="py-1 px-2 font-semibold">{d.factor}</td>
                  <td className="py-1 px-2 text-right">{d.fh.toFixed(3)}</td>
                  <td className="py-1 px-2 text-right">{d.sh.toFixed(3)}</td>
                  <td className="py-1 px-2 text-right">{d.change >= 0 ? "+" : ""}{d.change.toFixed(3)}</td>
                  <td className="py-1 px-2">
                    <span style={{ color: d.level === "HIGH" ? t.loss : d.level === "MODERATE" ? t.spot : t.muted }}>
                      {d.level}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="card">
        <div className="font-semibold text-sm mb-1">Rolling Alpha</div>
        <div className="text-xs text-text-muted mb-2">
          Annualized alpha over rolling windows. Persistently positive = genuine skill. Mean-reverting around zero = no edge.
        </div>
        <Plot
          data={[{
            x: rolling.dates, y: rolling.alpha, type: "scatter", mode: "lines",
            line: { color: t.gain, width: 2 }, name: "Rolling Alpha (ann. %)",
          }]}
          layout={{
            height: 320, ...L,
            yaxis: { title: { text: "Alpha (ann. %)" }, gridcolor: t.grid },
            xaxis: { gridcolor: t.grid },
            margin: { l: 60, r: 20, t: 20, b: 40 },
            shapes: [
              { type: "line", y0: 0, y1: 0, x0: 0, x1: 1, xref: "paper", line: { color: t.muted, dash: "dash" } },
              { type: "line", y0: fullAlpha, y1: fullAlpha, x0: 0, x1: 1, xref: "paper", line: { color: t.spot, dash: "dot" } },
            ],
            annotations: [{
              xref: "paper", x: 1.0, y: fullAlpha, text: `Full-period: ${fullAlpha >= 0 ? "+" : ""}${fullAlpha.toFixed(1)}%`,
              showarrow: false, font: { size: 9, color: t.spot }, xanchor: "right", yshift: 10,
            }],
          }}
          config={{ displayModeBar: false, responsive: true }}
          style={{ width: "100%" }}
        />
      </div>

      <div className="card">
        <div className="font-semibold text-sm mb-1">Rolling R²</div>
        <Plot
          data={[{
            x: rolling.dates, y: rolling.r2, type: "scatter", mode: "lines",
            line: { color: t.accent, width: 2 }, name: "Rolling R²",
          }]}
          layout={{
            height: 280, ...L,
            yaxis: { title: { text: "R²" }, gridcolor: t.grid, range: [0, 1.05] },
            xaxis: { gridcolor: t.grid },
            margin: { l: 60, r: 20, t: 20, b: 40 },
            shapes: [{ type: "line", y0: fullR2, y1: fullR2, x0: 0, x1: 1, xref: "paper", line: { color: t.spot, dash: "dot" } }],
            annotations: [{
              xref: "paper", x: 1.0, y: fullR2, text: `Full-period: ${fullR2.toFixed(3)}`,
              showarrow: false, font: { size: 9, color: t.spot }, xanchor: "right", yshift: 10,
            }],
          }}
          config={{ displayModeBar: false, responsive: true }}
          style={{ width: "100%" }}
        />
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════
// Tab 4 — Risk Decomposition
// ═══════════════════════════════════════════════

function RiskDecompositionTab({
  matched, t, L,
}: {
  matched: MatchedRegression;
  t: ReturnType<typeof getChartTheme>;
  L: ReturnType<typeof getBaseLayout>;
}) {
  const decomp = useMemo(() => {
    const n = matched.n;
    // Re-compute predicted/residual per observation
    const yHat: number[] = [];
    const resid: number[] = [];
    for (let i = 0; i < n; i++) {
      let p = matched.reg.alpha / (252 * 100); // alpha is ann %, convert back to daily decimal
      for (let f = 0; f < FACTORS.length; f++) p += matched.reg.betas[f] * matched.X[i][f];
      yHat.push(p);
      resid.push(matched.y[i] - p);
    }

    const variance = (xs: number[]) => {
      const m = xs.reduce((s, v) => s + v, 0) / xs.length;
      return xs.reduce((s, v) => s + (v - m) ** 2, 0) / (xs.length - 1);
    };

    const totalVar = variance(matched.y);
    const factorVar = variance(yHat);
    const idioVar = variance(resid);

    // Factor covariance matrix
    const fMeans = FACTORS.map((_, i) => {
      let s = 0;
      for (let k = 0; k < n; k++) s += matched.X[k][i];
      return s / n;
    });
    const fCov: number[][] = FACTORS.map(() => FACTORS.map(() => 0));
    for (let i = 0; i < FACTORS.length; i++) {
      for (let j = i; j < FACTORS.length; j++) {
        let s = 0;
        for (let k = 0; k < n; k++) s += (matched.X[k][i] - fMeans[i]) * (matched.X[k][j] - fMeans[j]);
        const c = s / (n - 1);
        fCov[i][j] = c; fCov[j][i] = c;
      }
    }

    // Per-factor variance contribution: sum_j (beta_i * beta_j * cov(f_i, f_j))
    const factorContribs = FACTORS.map((_, i) => {
      let mc = 0;
      for (let j = 0; j < FACTORS.length; j++) mc += matched.reg.betas[i] * matched.reg.betas[j] * fCov[i][j];
      return mc;
    });

    const pctSystematic = totalVar > 1e-10 ? (factorVar / totalVar) * 100 : 0;
    const pctIdio = totalVar > 1e-10 ? (idioVar / totalVar) * 100 : 0;

    // Annualized vol — keep the base consistent with totalVar (which is computed
    // on y = excess return). Previously this used stockRet variance which mixes
    // two different bases when computing volContrib = (factorContrib/totalVar) * totalVol.
    const totalVol = Math.sqrt(totalVar * 252) * 100;
    const mrc = FACTORS.map((f, i) => ({
      factor: FACTOR_NAMES[f],
      code: f,
      pctOfVar: totalVar > 0 ? (factorContribs[i] / totalVar) * 100 : 0,
      volContrib: totalVar > 0 ? (factorContribs[i] / totalVar) * totalVol : 0,
    }));

    return { totalVar, factorVar, idioVar, pctSystematic, pctIdio, totalVol, mrc };
  }, [matched]);

  // Sort mrc by vol contribution ascending for the bar chart
  const mrcSorted = [...decomp.mrc].sort((a, b) => a.volContrib - b.volContrib);

  // Pie chart data
  const pieLabels = [...decomp.mrc.map(m => m.factor), "Idiosyncratic"];
  const pieValues = [...decomp.mrc.map(m => Math.max(0, m.pctOfVar)), decomp.pctIdio];
  const pieColors = [...FACTORS.map(f => FACTOR_COLORS[f]), t.muted];

  return (
    <div className="space-y-4">
      <div className="card card-compact">
        <p className="text-xs text-text-muted">
          Decomposes total variance into factor-explained (systematic — compensated via risk premia) and idiosyncratic (unexplained — diversifiable, not compensated).
        </p>
      </div>

      <div className="card card-compact">
        <div className="flex flex-wrap gap-6">
          <Metric label="Systematic Risk" value={`${decomp.pctSystematic.toFixed(1)}%`} />
          <Metric label="Idiosyncratic Risk" value={`${decomp.pctIdio.toFixed(1)}%`} />
          <Metric label="Total Ann. Vol" value={`${decomp.totalVol.toFixed(1)}%`} />
        </div>
      </div>

      <div className="card">
        <Plot
          data={[{
            type: "pie",
            labels: pieLabels,
            values: pieValues,
            marker: { colors: pieColors },
            hole: 0.45,
            textinfo: "label+percent",
            textfont: { size: 11 },
          }]}
          layout={{
            height: 440, ...L,
            title: { text: "Variance Decomposition", font: { size: 14, color: t.text } },
            margin: { l: 20, r: 20, t: 40, b: 20 },
            showlegend: true,
            legend: { orientation: "h", y: -0.05 },
          }}
          config={{ displayModeBar: false, responsive: true }}
          style={{ width: "100%" }}
        />
      </div>

      <div className="card">
        <div className="font-semibold text-sm mb-1">Marginal Risk Contribution</div>
        <div className="text-xs text-text-muted mb-2">
          How much each factor contributes to total portfolio volatility. Hedge the largest contributors first.
        </div>
        <Plot
          data={[{
            type: "bar", orientation: "h",
            y: mrcSorted.map(m => m.factor),
            x: mrcSorted.map(m => m.volContrib),
            marker: { color: mrcSorted.map(m => FACTOR_COLORS[m.code]) },
            text: mrcSorted.map(m => `${m.volContrib >= 0 ? "+" : ""}${m.volContrib.toFixed(1)}%`),
            textposition: "outside",
          }]}
          layout={{
            height: 320, ...L,
            title: { text: "Marginal Volatility Contribution by Factor", font: { size: 13, color: t.text } },
            xaxis: { title: { text: "Vol Contribution (%)" }, gridcolor: t.grid },
            yaxis: { gridcolor: t.grid },
            margin: { l: 100, r: 60, t: 40, b: 40 },
          }}
          config={{ displayModeBar: false, responsive: true }}
          style={{ width: "100%" }}
        />
      </div>
    </div>
  );
}

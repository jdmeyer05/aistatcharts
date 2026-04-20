"use client";

import { useEffect, useMemo, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTheme } from "next-themes";
import { Plot } from "@/components/plot";
import {
  fetchQuantLabAnalyze,
  fetchQuantLabHrp,
  type QuantLabAnalyzeResponse,
  type QuantLabHrpResponse,
} from "@/lib/api";
import { getChartTheme, getBaseLayout, heatmapTrace, heatmapHeight } from "@/lib/chart-theme";
import { Metric } from "@/components/ui/metric";


const TABS = [
  "Frac. Diff.",
  "Structural Breaks",
  "Triple Barrier",
  "Sample Weights",
  "Feature Importance",
  "HRP",
  "Microstructure",
  "Entropy",
];

function fmtPct(v: number, digits = 1): string {
  return `${v >= 0 ? "+" : ""}${v.toFixed(digits)}%`;
}

// ─── Math helpers (client-side) ─────────────────────────────────────

function mean(x: number[]): number { return x.length > 0 ? x.reduce((s, v) => s + v, 0) / x.length : 0; }
function stddev(x: number[]): number {
  if (x.length < 2) return 0;
  const m = mean(x);
  return Math.sqrt(x.reduce((s, v) => s + (v - m) ** 2, 0) / x.length);
}
function cumProd(rets: number[], start = 100): number[] {
  const out = new Array(rets.length + 1);
  out[0] = start;
  for (let i = 0; i < rets.length; i++) out[i + 1] = out[i] * (1 + rets[i]);
  return out;
}

// ─── CUSUM ───
function cusumFilter(returns: number[], hThresh: number): Array<{ idx: number; type: "Up" | "Down"; value: number; sPos: number[]; sNeg: number[] }> {
  const sPos: number[] = [];
  const sNeg: number[] = [];
  const events: Array<{ idx: number; type: "Up" | "Down"; value: number }> = [];
  let sp = 0, sn = 0;
  for (let i = 0; i < returns.length; i++) {
    sp = Math.max(0, sp + returns[i]);
    sn = Math.min(0, sn + returns[i]);
    sPos.push(sp);
    sNeg.push(sn);
    if (sp > hThresh) { events.push({ idx: i, type: "Up", value: sp }); sp = 0; }
    else if (sn < -hThresh) { events.push({ idx: i, type: "Down", value: sn }); sn = 0; }
  }
  return events.map((e) => ({ ...e, sPos, sNeg }));
}

// ─── ATR ───
function atrSeries(high: number[], low: number[], close: number[], n = 14): number[] {
  const tr: number[] = [];
  for (let i = 0; i < close.length; i++) {
    if (i === 0) { tr.push(high[i] - low[i]); continue; }
    const a = high[i] - low[i];
    const b = Math.abs(high[i] - close[i - 1]);
    const c = Math.abs(low[i] - close[i - 1]);
    tr.push(Math.max(a, b, c));
  }
  // rolling mean
  const atr: number[] = new Array(tr.length).fill(NaN);
  for (let i = n - 1; i < tr.length; i++) {
    let s = 0;
    for (let j = i - n + 1; j <= i; j++) s += tr[j];
    atr[i] = s / n;
  }
  return atr;
}

// ─── Triple Barrier ───
interface TripleBarrierLabel {
  entry_idx: number;
  exit_idx: number;
  entry_price: number;
  exit_price: number;
  label: -1 | 0 | 1;
  return_pct: number;
  hold_days: number;
}
function tripleBarrier(close: number[], atr: number[], ptMult: number, slMult: number, maxHold: number): TripleBarrierLabel[] {
  const out: TripleBarrierLabel[] = [];
  for (let i = 0; i < close.length - maxHold; i++) {
    const a = atr[i];
    if (!Number.isFinite(a) || a <= 0) continue;
    const entry = close[i];
    const upper = entry + ptMult * a;
    const lower = entry - slMult * a;
    let label: -1 | 0 | 1 = 0;
    let exit_idx = Math.min(i + maxHold, close.length - 1);
    for (let j = i + 1; j <= Math.min(i + maxHold, close.length - 1); j++) {
      if (close[j] >= upper) { label = 1; exit_idx = j; break; }
      if (close[j] <= lower) { label = -1; exit_idx = j; break; }
    }
    const exit_price = close[exit_idx];
    const return_pct = ((exit_price / entry) - 1) * 100;
    out.push({ entry_idx: i, exit_idx, entry_price: entry, exit_price, label, return_pct, hold_days: exit_idx - i });
  }
  return out;
}

// ─── Uniqueness + Bootstrap ───
function avgUniqueness(n: number, window: number): number[] {
  const u: number[] = new Array(n);
  for (let i = 0; i < n; i++) {
    const start = Math.max(0, i - window + 1);
    const end = Math.min(n, i + window);
    u[i] = 1 / (end - start);
  }
  return u;
}

function standardBootstrapSharpe(rets: number[], nBoot: number, seed = 42): number[] {
  const sqrt252 = Math.sqrt(252);
  const sharpes: number[] = [];
  // simple LCG for reproducibility
  let state = seed;
  const rand = () => {
    state = (state * 1664525 + 1013904223) >>> 0;
    return state / 0x100000000;
  };
  for (let b = 0; b < nBoot; b++) {
    const sample: number[] = new Array(rets.length);
    for (let i = 0; i < rets.length; i++) {
      sample[i] = rets[Math.floor(rand() * rets.length)];
    }
    const sd = stddev(sample);
    if (sd > 0) sharpes.push(mean(sample) / sd * sqrt252);
  }
  return sharpes;
}

function sequentialBootstrapSharpe(rets: number[], probs: number[], nBoot: number, seed = 43): number[] {
  const sqrt252 = Math.sqrt(252);
  const sharpes: number[] = [];
  const cdf: number[] = new Array(probs.length);
  let s = 0;
  for (let i = 0; i < probs.length; i++) { s += probs[i]; cdf[i] = s; }
  let state = seed;
  const rand = () => { state = (state * 1664525 + 1013904223) >>> 0; return state / 0x100000000; };
  // sample with probability proportional to uniqueness (inverse-CDF lookup)
  for (let b = 0; b < nBoot; b++) {
    const sample: number[] = new Array(rets.length);
    for (let i = 0; i < rets.length; i++) {
      const r = rand() * s;
      // binary search
      let lo = 0, hi = cdf.length - 1;
      while (lo < hi) {
        const m = (lo + hi) >>> 1;
        if (cdf[m] < r) lo = m + 1; else hi = m;
      }
      sample[i] = rets[lo];
    }
    const sd = stddev(sample);
    if (sd > 0) sharpes.push(mean(sample) / sd * sqrt252);
  }
  return sharpes;
}

// ─── Entropy ───
function quantileBins(xs: number[], nBins: number): { bins: number[]; counts: number[] } {
  const valid = xs.filter((v) => Number.isFinite(v));
  const sorted = [...valid].sort((a, b) => a - b);
  const edges: number[] = [];
  for (let i = 1; i < nBins; i++) {
    const q = (i / nBins) * (sorted.length - 1);
    const lo = Math.floor(q);
    const hi = Math.ceil(q);
    const frac = q - lo;
    edges.push(sorted[lo] * (1 - frac) + sorted[hi] * frac);
  }
  // assign bin per value
  const bins: number[] = new Array(valid.length);
  const counts: number[] = new Array(nBins).fill(0);
  for (let i = 0; i < valid.length; i++) {
    let b = 0;
    for (let j = 0; j < edges.length; j++) { if (valid[i] > edges[j]) b = j + 1; else break; }
    bins[i] = b;
    counts[b]++;
  }
  return { bins, counts };
}

function shannonEntropy(counts: number[]): number {
  const total = counts.reduce((s, v) => s + v, 0);
  if (total === 0) return 0;
  let h = 0;
  for (const c of counts) {
    if (c > 0) {
      const p = c / total;
      h -= p * Math.log2(p);
    }
  }
  return h;
}

function lempelZiv(sequence: number[]): number {
  const s = sequence.map((v) => (v > 0 ? "1" : "0")).join("");
  const n = s.length;
  if (n === 0) return 0;
  const words = new Set<string>();
  let w = "", complexity = 0;
  for (const c of s) {
    const wc = w + c;
    if (!words.has(wc)) { words.add(wc); complexity++; w = ""; }
    else { w = wc; }
  }
  if (w) complexity++;
  const maxC = n / Math.log2(Math.max(n, 2));
  return maxC > 0 ? complexity / maxC : 0;
}

function transitionMatrix(bins: number[], nStates: number): number[][] {
  const m: number[][] = Array.from({ length: nStates }, () => new Array(nStates).fill(0));
  for (let i = 1; i < bins.length; i++) {
    const prev = bins[i - 1];
    const cur = bins[i];
    if (prev < nStates && cur < nStates) m[prev][cur]++;
  }
  // normalize rows
  return m.map((row) => {
    const total = row.reduce((s, v) => s + v, 0);
    return total > 0 ? row.map((v) => v / total) : row;
  });
}

// ═══════════════════════════════════════════════════════════════
// PAGE
// ═══════════════════════════════════════════════════════════════

export default function QuantLabPage() {
  const { resolvedTheme } = useTheme();
  const t = getChartTheme(resolvedTheme === "dark");
  const L = getBaseLayout(t);

  const [ticker, setTicker] = useState("SPY");
  const [lookback, setLookback] = useState(756);
  const [activeTab, setActiveTab] = useState(0);

  const analyze = useMutation({
    mutationFn: () => fetchQuantLabAnalyze(ticker, lookback),
  });
  const data: QuantLabAnalyzeResponse | null = analyze.data && !analyze.data.error ? analyze.data : null;

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Quant Lab</h1>
        <p className="text-text-secondary text-sm mt-1">
          Institutional-grade quantitative methods from Lopez de Prado&apos;s <em>Advances in Financial Machine Learning</em> and <em>Machine Learning for Asset Managers</em>.
        </p>
      </div>

      <div className="card card-compact">
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <label className="metric-label">Ticker</label>
            <input
              value={ticker}
              onChange={(e) => setTicker(e.target.value.toUpperCase())}
              onKeyDown={(e) => e.key === "Enter" && analyze.mutate()}
              className="mt-0.5 w-28 px-3 py-1.5 border border-border rounded text-sm font-data bg-surface"
            />
          </div>
          <div>
            <label className="metric-label">Lookback (days)</label>
            <div className="flex gap-1 mt-0.5">
              {[504, 756, 1260, 2520].map((d) => (
                <button
                  key={d}
                  onClick={() => setLookback(d)}
                  className={`px-3 py-1.5 text-xs rounded font-data ${lookback === d ? "bg-accent text-white" : "bg-surface-alt text-text-muted"}`}
                >
                  {d / 252}Y
                </button>
              ))}
            </div>
          </div>
          <button
            onClick={() => analyze.mutate()}
            disabled={analyze.isPending}
            className="px-6 py-2 bg-accent text-white font-semibold rounded-lg hover:bg-accent-hover disabled:opacity-50 text-sm"
          >
            {analyze.isPending ? "Analyzing…" : "Run Analysis"}
          </button>
        </div>
      </div>

      {analyze.isPending && (
        <div className="card text-center py-12">
          <div className="inline-block w-6 h-6 border-2 border-accent border-t-transparent rounded-full animate-spin" />
          <div className="text-xs text-text-muted mt-2">Running ADF scan, SADF, Chow, feature importance…</div>
          <ElapsedCounter />
        </div>
      )}

      {analyze.data?.error && <div className="card border-loss text-loss text-sm">{analyze.data.error}</div>}

      {!data && !analyze.isPending && !analyze.data?.error && (
        <div className="card text-center py-10 text-text-muted text-sm">
          <div className="font-semibold text-text mb-1">Quant Lab is idle</div>
          Enter a ticker and lookback, then click <span className="text-accent font-semibold">Run Analysis</span> to scan the ADF p-value
          curve for fractional-differencing stationarity, detect structural breaks, run feature importance, and more.
        </div>
      )}

      {data && (
        <>
          <SummaryBar data={data} t={t} />

          <div className="flex gap-1 border-b border-border pb-1 overflow-x-auto">
            {TABS.map((tab, i) => (
              <button
                key={tab}
                onClick={() => setActiveTab(i)}
                className={`px-3 py-1.5 text-xs font-semibold rounded-t-md whitespace-nowrap ${
                  activeTab === i ? "bg-accent text-white" : "text-text-muted hover:text-text hover:bg-surface-alt"
                }`}
              >
                {tab}
              </button>
            ))}
          </div>

          {activeTab === 0 && <FracDiffTab data={data} t={t} L={L} />}
          {activeTab === 1 && <StructuralBreaksTab data={data} t={t} L={L} />}
          {activeTab === 2 && <TripleBarrierTab data={data} t={t} L={L} />}
          {activeTab === 3 && <SampleWeightsTab data={data} t={t} L={L} />}
          {activeTab === 4 && <FeatureImportanceTab data={data} t={t} L={L} />}
          {activeTab === 5 && <HrpTab t={t} L={L} />}
          {activeTab === 6 && <MicrostructureTab data={data} t={t} L={L} />}
          {activeTab === 7 && <EntropyTab data={data} t={t} L={L} />}
        </>
      )}
    </div>
  );
}

function ElapsedCounter() {
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    const start = Date.now();
    const id = window.setInterval(() => setSeconds(Math.floor((Date.now() - start) / 1000)), 1000);
    return () => window.clearInterval(id);
  }, []);
  const label = seconds < 10
    ? "starting…"
    : seconds < 30
      ? `${seconds}s elapsed — ADF and SADF are the slow steps`
      : seconds < 60
        ? `${seconds}s elapsed — still working (long lookbacks can take 30–60s)`
        : `${seconds}s elapsed — if this runs past 2 minutes, try a shorter lookback`;
  return <div className="text-[10px] text-text-muted mt-1 font-data">{label}</div>;
}

function SummaryBar({ data, t }: { data: QuantLabAnalyzeResponse; t: ReturnType<typeof getChartTheme> }) {
  return (
    <div className="card card-compact flex flex-wrap gap-6">
      <Metric label="Ticker" value={data.ticker} />
      <Metric label="Observations" value={String(data.n_obs)} />
      <div>
        <div className="metric-label">Date range</div>
        <div className="text-sm font-data">{data.date_start} → {data.date_end}</div>
      </div>
      <Metric label="Ann. Return" value={fmtPct(data.ann_return)} deltaType={data.ann_return >= 0 ? "gain" : "loss"} />
      <Metric label="Ann. Vol" value={`${data.ann_vol.toFixed(1)}%`} />
      <Metric label="Min d (stationary)" value={data.min_d.toFixed(2)} />
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════
// TAB 1: FRACTIONAL DIFFERENTIATION
// ═══════════════════════════════════════════════════════════════

function FracDiffTab({ data, t, L }: { data: QuantLabAnalyzeResponse; t: ReturnType<typeof getChartTheme>; L: ReturnType<typeof getBaseLayout> }) {
  const adf = data.adf_scan;
  return (
    <div className="space-y-4">
      <div className="card card-compact text-xs text-text-muted">
        <strong className="text-sm text-text">Fractional differencing</strong> (AFML Ch. 5) — find the smallest <em>d</em> that makes the log-price series stationary while preserving maximum memory. ADF p-value below 5% = stationary. Correlation with the original series measures memory retention.
      </div>
      <div className="card">
        <div className="text-sm font-semibold mb-2">Stationarity vs memory preservation</div>
        <Plot
          data={[
            {
              x: adf.map((r) => r.d),
              y: adf.map((r) => r.pvalue),
              type: "scatter" as const,
              mode: "lines+markers" as const,
              name: "ADF p-value",
              line: { color: t.loss, width: 2 },
              marker: { size: 6 },
            },
            {
              x: adf.map((r) => r.d),
              y: adf.map((r) => r.corr),
              type: "scatter" as const,
              mode: "lines+markers" as const,
              name: "Correlation with original",
              line: { color: t.accent, width: 2 },
              marker: { size: 6 },
              yaxis: "y2" as const,
            },
          ]}
          layout={{
            height: 420,
            ...L,
            xaxis: { title: "d (differencing order)", gridcolor: t.grid },
            yaxis: { title: "ADF p-value (log)", type: "log" as const, gridcolor: t.grid },
            yaxis2: { title: "Correlation", overlaying: "y" as const, side: "right" as const, range: [0, 1.05], gridcolor: t.grid },
            legend: { orientation: "h", y: -0.18 },
            shapes: [
              { type: "line", yref: "y" as const, xref: "paper" as const, x0: 0, x1: 1, y0: 0.05, y1: 0.05, line: { color: t.spot, dash: "dash", width: 1 } },
              { type: "line", xref: "x" as const, yref: "paper" as const, x0: data.min_d, x1: data.min_d, y0: 0, y1: 1, line: { color: t.gain, dash: "dash", width: 1 } },
            ],
            annotations: [
              { xref: "paper" as const, yref: "y" as const, x: 1, y: 0.05, xanchor: "right" as const, text: "5% signif.", showarrow: false, font: { color: t.spot, size: 10 } },
              { xref: "x" as const, yref: "paper" as const, x: data.min_d, y: 1, yanchor: "bottom" as const, text: `Min d = ${data.min_d.toFixed(2)}`, showarrow: false, font: { color: t.gain, size: 10 } },
            ],
          }}
          config={{ displayModeBar: false, responsive: true }}
          style={{ width: "100%" }}
        />
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div className="card">
          <div className="text-sm font-semibold mb-1">Original (d=0, non-stationary)</div>
          <Plot
            data={[{ x: data.ohlcv.dates, y: data.ohlcv.log_prices, type: "scatter" as const, mode: "lines" as const, line: { color: t.muted, width: 1 } }]}
            layout={{ height: 260, ...L, yaxis: { gridcolor: t.grid }, xaxis: { gridcolor: t.grid } }}
            config={{ displayModeBar: false, responsive: true }}
            style={{ width: "100%" }}
          />
        </div>
        <div className="card">
          <div className="text-sm font-semibold mb-1">Frac. diff. d={data.fd_optimal.d.toFixed(2)} (stationary + memory)</div>
          <Plot
            data={[{ x: data.fd_optimal.dates, y: data.fd_optimal.values, type: "scatter" as const, mode: "lines" as const, line: { color: t.accent, width: 1 } }]}
            layout={{ height: 260, ...L, yaxis: { gridcolor: t.grid }, xaxis: { gridcolor: t.grid } }}
            config={{ displayModeBar: false, responsive: true }}
            style={{ width: "100%" }}
          />
        </div>
      </div>

      <div className="card">
        <div className="text-sm font-semibold mb-2">Full ADF scan</div>
        <table className="data-table text-xs">
          <thead>
            <tr><th>d</th><th>ADF stat</th><th>p-value</th><th>Correlation</th><th>Stationary?</th></tr>
          </thead>
          <tbody>
            {adf.map((r) => (
              <tr key={r.d}>
                <td className="font-data">{r.d.toFixed(2)}</td>
                <td className="font-data">{r.adf_stat !== null ? r.adf_stat.toFixed(3) : "—"}</td>
                <td className="font-data">{r.pvalue.toFixed(4)}</td>
                <td className="font-data">{r.corr.toFixed(3)}</td>
                <td className={r.pvalue < 0.05 ? "text-gain" : "text-text-muted"}>{r.pvalue < 0.05 ? "Yes" : "No"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════
// TAB 2: STRUCTURAL BREAKS
// ═══════════════════════════════════════════════════════════════

function StructuralBreaksTab({ data, t, L }: { data: QuantLabAnalyzeResponse; t: ReturnType<typeof getChartTheme>; L: ReturnType<typeof getBaseLayout> }) {
  const [hMult, setHMult] = useState(2.0);
  const returns = data.ohlcv.log_returns.slice(1);
  const dates = data.ohlcv.dates.slice(1);
  const sigma = stddev(returns);
  const h = hMult * sigma;
  const cusumResult = cusumFilter(returns, h);
  const events = cusumResult;
  const sPos = cusumResult.length > 0 ? cusumResult[0].sPos : [];
  const sNeg = cusumResult.length > 0 ? cusumResult[0].sNeg : [];
  const compression = returns.length > 0 ? (events.length / returns.length) * 100 : 0;

  const isBubble = data.sadf.max > data.sadf.cv_95;

  // Top 5 chow breakpoints
  const chowSig = data.chow.f_stats
    .map((f, i) => ({ date: data.chow.dates[i], f }))
    .filter((x) => x.f > data.chow.cv_99)
    .sort((a, b) => b.f - a.f)
    .slice(0, 5);

  return (
    <div className="space-y-4">
      <div className="card">
        <div className="text-sm font-semibold mb-2">CUSUM filter</div>
        <div className="text-xs text-text-muted mb-3">Event-driven sampling (AFML Ch. 17). Only samples when cumulative returns exceed threshold — creates meaningful observation points.</div>
        <div className="flex items-center gap-3 mb-3">
          <label className="metric-label">Threshold (σ)</label>
          <input type="range" value={hMult} onChange={(e) => setHMult(Number(e.target.value))} min={0.5} max={5} step={0.25} className="flex-1 max-w-[260px] accent-accent" />
          <span className="font-data text-xs">{hMult.toFixed(2)}σ</span>
          <Metric label="Events" value={String(events.length)} />
          <Metric label="Compression" value={`${compression.toFixed(1)}%`} />
          <Metric label="h" value={h.toFixed(4)} />
        </div>
        <Plot
          data={[
            { x: data.ohlcv.dates, y: data.ohlcv.close, type: "scatter" as const, mode: "lines" as const, line: { color: t.muted, width: 1 }, name: "Price" },
            {
              x: events.filter((e) => e.type === "Up").map((e) => dates[e.idx]),
              y: events.filter((e) => e.type === "Up").map((e) => data.ohlcv.close[e.idx + 1] ?? data.ohlcv.close[e.idx]),
              type: "scatter" as const,
              mode: "markers" as const,
              marker: { color: t.gain, size: 6, symbol: "triangle-up" },
              name: `Up (${events.filter((e) => e.type === "Up").length})`,
            },
            {
              x: events.filter((e) => e.type === "Down").map((e) => dates[e.idx]),
              y: events.filter((e) => e.type === "Down").map((e) => data.ohlcv.close[e.idx + 1] ?? data.ohlcv.close[e.idx]),
              type: "scatter" as const,
              mode: "markers" as const,
              marker: { color: t.loss, size: 6, symbol: "triangle-down" },
              name: `Down (${events.filter((e) => e.type === "Down").length})`,
            },
          ]}
          layout={{ height: 340, ...L, yaxis: { title: "Price", gridcolor: t.grid }, xaxis: { gridcolor: t.grid }, legend: { orientation: "h", y: -0.18 } }}
          config={{ displayModeBar: false, responsive: true }}
          style={{ width: "100%" }}
        />
        <Plot
          data={[
            { x: dates, y: sPos, type: "scatter" as const, mode: "lines" as const, line: { color: t.gain, width: 1 }, name: "S+" },
            { x: dates, y: sNeg, type: "scatter" as const, mode: "lines" as const, line: { color: t.loss, width: 1 }, name: "S−" },
          ]}
          layout={{
            height: 220,
            ...L,
            yaxis: { title: "CUSUM", gridcolor: t.grid },
            xaxis: { gridcolor: t.grid },
            shapes: [
              { type: "line", xref: "paper" as const, x0: 0, x1: 1, y0: h, y1: h, line: { color: t.spot, dash: "dash", width: 1 } },
              { type: "line", xref: "paper" as const, x0: 0, x1: 1, y0: -h, y1: -h, line: { color: t.spot, dash: "dash", width: 1 } },
            ],
          }}
          config={{ displayModeBar: false, responsive: true }}
          style={{ width: "100%" }}
        />
      </div>

      <div className="card">
        <div className="text-sm font-semibold mb-2">SADF bubble detection</div>
        <div className="text-xs text-text-muted mb-3">Supremum ADF (Phillips, Shi &amp; Yu 2015). Detects explosive (bubble-like) behavior in prices.</div>
        <div className="flex flex-wrap gap-6 mb-3">
          <Metric label="Max SADF" value={data.sadf.max.toFixed(3)} deltaType={isBubble ? "loss" : "gain"} delta={isBubble ? "BUBBLE" : "No bubble"} />
          <Metric label="95% CV" value={data.sadf.cv_95.toFixed(2)} />
        </div>
        <Plot
          data={[
            { x: data.ohlcv.dates, y: data.ohlcv.close, type: "scatter" as const, mode: "lines" as const, line: { color: t.muted, width: 1 }, name: "Price", xaxis: "x", yaxis: "y" },
            { x: data.sadf.dates, y: data.sadf.values, type: "scatter" as const, mode: "lines" as const, line: { color: t.accent, width: 2 }, name: "ADF stat", xaxis: "x2", yaxis: "y2" },
          ]}
          layout={{
            height: 460,
            ...L,
            grid: { rows: 2, columns: 1, pattern: "independent" as const, ygap: 0.1 },
            xaxis: { gridcolor: t.grid, anchor: "y" as const },
            yaxis: { title: "Price", gridcolor: t.grid, domain: [0.55, 1] },
            xaxis2: { gridcolor: t.grid, anchor: "y2" as const },
            yaxis2: { title: "ADF stat", gridcolor: t.grid, domain: [0, 0.45] },
            shapes: [{ type: "line", xref: "x2" as const, yref: "y2" as const, x0: data.sadf.dates[0] ?? "", x1: data.sadf.dates[data.sadf.dates.length - 1] ?? "", y0: data.sadf.cv_95, y1: data.sadf.cv_95, line: { color: t.loss, dash: "dash", width: 1 } }],
          }}
          config={{ displayModeBar: false, responsive: true }}
          style={{ width: "100%" }}
        />
      </div>

      <div className="card">
        <div className="text-sm font-semibold mb-2">Chow breakpoint test</div>
        <div className="text-xs text-text-muted mb-3">F-statistic across candidate breakpoints. Red dashed = 99% critical value; peaks above = structural regime shifts.</div>
        <Plot
          data={[{ x: data.chow.dates, y: data.chow.f_stats, type: "scatter" as const, mode: "lines" as const, line: { color: t.accent, width: 2 } }]}
          layout={{
            height: 320,
            ...L,
            yaxis: { title: "F-stat", gridcolor: t.grid },
            xaxis: { gridcolor: t.grid },
            shapes: [{ type: "line", xref: "paper" as const, x0: 0, x1: 1, y0: data.chow.cv_99, y1: data.chow.cv_99, line: { color: t.loss, dash: "dash", width: 1 } }],
            annotations: [{ xref: "paper" as const, yref: "y" as const, x: 1, y: data.chow.cv_99, xanchor: "right" as const, text: `99% CV (${data.chow.cv_99.toFixed(2)})`, showarrow: false, font: { color: t.loss, size: 10 } }],
          }}
          config={{ displayModeBar: false, responsive: true }}
          style={{ width: "100%" }}
        />
        {chowSig.length > 0 && (
          <div className="mt-2 text-xs">
            <div className="metric-label mb-1">Top 5 significant breakpoints</div>
            <ul className="space-y-0.5">
              {chowSig.map((b) => <li key={b.date}><span className="font-data">{b.date}</span> — F = <span className="font-data text-loss">{b.f.toFixed(2)}</span></li>)}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════
// TAB 3: TRIPLE BARRIER
// ═══════════════════════════════════════════════════════════════

function TripleBarrierTab({ data, t, L }: { data: QuantLabAnalyzeResponse; t: ReturnType<typeof getChartTheme>; L: ReturnType<typeof getBaseLayout> }) {
  const [ptMult, setPtMult] = useState(2.0);
  const [slMult, setSlMult] = useState(2.0);
  const [maxHold, setMaxHold] = useState(20);
  const [sizing, setSizing] = useState<"equal" | "meta">("equal");

  const atr = useMemo(() => atrSeries(data.ohlcv.high, data.ohlcv.low, data.ohlcv.close, 14), [data]);
  const labels = useMemo(() => tripleBarrier(data.ohlcv.close, atr, ptMult, slMult, maxHold), [data, atr, ptMult, slMult, maxHold]);

  const n = labels.length;
  const wins = labels.filter((l) => l.label === 1).length;
  const losses = labels.filter((l) => l.label === -1).length;
  const timeouts = labels.filter((l) => l.label === 0).length;
  const avgRet = n > 0 ? mean(labels.map((l) => l.return_pct)) : 0;
  const avgWin = wins > 0 ? mean(labels.filter((l) => l.label === 1).map((l) => l.return_pct)) : 0;
  const avgLoss = losses > 0 ? Math.abs(mean(labels.filter((l) => l.label === -1).map((l) => l.return_pct))) : 1;
  const b = avgLoss > 0 ? avgWin / avgLoss : 1;
  const p = n > 0 ? wins / n : 0;
  const kelly = b > 0 ? Math.max(0, p - (1 - p) / b) : 0;
  const halfKelly = kelly / 2;

  // rolling win rate as meta-label
  const rollingWin = useMemo(() => {
    const w: number[] = new Array(labels.length).fill(NaN);
    const winArr = labels.map((l) => l.label === 1 ? 1 : 0);
    const k = 50;
    for (let i = 0; i < labels.length; i++) {
      const start = Math.max(0, i - k + 1);
      const slice = winArr.slice(start, i + 1);
      if (slice.length >= 10) w[i] = mean(slice);
    }
    return w;
  }, [labels]);

  // equity curves
  const stratRets: number[] = labels.map((l, i) => {
    if (l.label === 0) return 0;
    const size = sizing === "meta" ? (Number.isFinite(rollingWin[i]) ? rollingWin[i] : 0.5) : 1;
    return (l.return_pct / 100) * size;
  });
  const cumStrat = cumProd(stratRets, 100);

  // Buy & hold
  const firstIdx = labels.length > 0 ? labels[0].entry_idx : 0;
  const lastIdx = labels.length > 0 ? labels[labels.length - 1].entry_idx : data.ohlcv.close.length - 1;
  const bhStart = data.ohlcv.close[firstIdx] || 1;
  const bh = data.ohlcv.close.slice(firstIdx, lastIdx + 1).map((c) => (c / bhStart) * 100);
  const bhDates = data.ohlcv.dates.slice(firstIdx, lastIdx + 1);

  const dates = labels.map((l) => data.ohlcv.dates[l.entry_idx]);

  // metrics
  const cumStratRet = cumStrat[cumStrat.length - 1] / 100 - 1;
  const annRet = n > 0 ? mean(stratRets) * 252 * 100 : 0;
  const annVol = stddev(stratRets) * Math.sqrt(252) * 100;
  const sharpe = annVol > 0 ? annRet / annVol : 0;
  let peak = 100, maxDD = 0;
  for (const v of cumStrat) { peak = Math.max(peak, v); maxDD = Math.min(maxDD, v / peak - 1); }

  return (
    <div className="space-y-4">
      <div className="card card-compact">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <div>
            <label className="metric-label">Profit-take (ATR)</label>
            <input type="range" value={ptMult} onChange={(e) => setPtMult(Number(e.target.value))} min={0.5} max={5} step={0.25} className="w-full accent-accent" />
            <div className="font-data text-xs text-center">{ptMult.toFixed(2)}</div>
          </div>
          <div>
            <label className="metric-label">Stop-loss (ATR)</label>
            <input type="range" value={slMult} onChange={(e) => setSlMult(Number(e.target.value))} min={0.5} max={5} step={0.25} className="w-full accent-accent" />
            <div className="font-data text-xs text-center">{slMult.toFixed(2)}</div>
          </div>
          <div>
            <label className="metric-label">Max hold (days)</label>
            <input type="range" value={maxHold} onChange={(e) => setMaxHold(Number(e.target.value))} min={5} max={60} step={5} className="w-full accent-accent" />
            <div className="font-data text-xs text-center">{maxHold}D</div>
          </div>
          <div>
            <label className="metric-label">Position sizing</label>
            <div className="flex gap-1 mt-0.5">
              {(["equal", "meta"] as const).map((s) => (
                <button key={s} onClick={() => setSizing(s)} className={`px-2 py-1 text-xs rounded ${sizing === s ? "bg-accent text-white" : "bg-surface-alt text-text-muted"}`}>
                  {s === "equal" ? "Equal" : "Meta-label"}
                </button>
              ))}
            </div>
          </div>
        </div>
      </div>

      <div className="card card-compact flex flex-wrap gap-6">
        <Metric label="Events" value={String(n)} />
        <Metric label="Profit-Take" value={`${wins} (${n > 0 ? ((wins / n) * 100).toFixed(0) : 0}%)`} deltaType="gain" />
        <Metric label="Stop-Loss" value={`${losses} (${n > 0 ? ((losses / n) * 100).toFixed(0) : 0}%)`} deltaType="loss" />
        <Metric label="Time Expiry" value={`${timeouts} (${n > 0 ? ((timeouts / n) * 100).toFixed(0) : 0}%)`} />
        <Metric label="Avg return" value={fmtPct(avgRet, 2)} deltaType={avgRet >= 0 ? "gain" : "loss"} />
        <Metric label="Full Kelly" value={`${(kelly * 100).toFixed(1)}%`} />
        <Metric label="Half Kelly" value={`${(halfKelly * 100).toFixed(1)}%`} />
        <Metric label="Win/Loss ratio" value={`${b.toFixed(2)}x`} />
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div className="card">
          <div className="text-sm font-semibold mb-1">Return distribution by barrier</div>
          <Plot
            data={[
              { type: "histogram" as const, x: labels.filter((l) => l.label === 1).map((l) => l.return_pct), name: "Profit-Take", marker: { color: t.gain }, opacity: 0.6, nbinsx: 40 },
              { type: "histogram" as const, x: labels.filter((l) => l.label === -1).map((l) => l.return_pct), name: "Stop-Loss", marker: { color: t.loss }, opacity: 0.6, nbinsx: 40 },
              { type: "histogram" as const, x: labels.filter((l) => l.label === 0).map((l) => l.return_pct), name: "Time Expiry", marker: { color: t.spot }, opacity: 0.6, nbinsx: 40 },
            ]}
            layout={{ height: 320, ...L, barmode: "overlay" as const, yaxis: { title: "Count", gridcolor: t.grid }, xaxis: { title: "Return (%)", gridcolor: t.grid }, legend: { orientation: "h", y: -0.2 } }}
            config={{ displayModeBar: false, responsive: true }}
            style={{ width: "100%" }}
          />
        </div>
        <div className="card">
          <div className="text-sm font-semibold mb-1">Holding period by barrier</div>
          <Plot
            data={[
              { type: "histogram" as const, x: labels.filter((l) => l.label === 1).map((l) => l.hold_days), name: "Profit-Take", marker: { color: t.gain }, opacity: 0.6, nbinsx: maxHold },
              { type: "histogram" as const, x: labels.filter((l) => l.label === -1).map((l) => l.hold_days), name: "Stop-Loss", marker: { color: t.loss }, opacity: 0.6, nbinsx: maxHold },
              { type: "histogram" as const, x: labels.filter((l) => l.label === 0).map((l) => l.hold_days), name: "Time Expiry", marker: { color: t.spot }, opacity: 0.6, nbinsx: maxHold },
            ]}
            layout={{ height: 320, ...L, barmode: "overlay" as const, yaxis: { title: "Count", gridcolor: t.grid }, xaxis: { title: "Days held", gridcolor: t.grid }, legend: { orientation: "h", y: -0.2 } }}
            config={{ displayModeBar: false, responsive: true }}
            style={{ width: "100%" }}
          />
        </div>
      </div>

      <div className="card">
        <div className="text-sm font-semibold mb-1">Meta-label bet sizing (rolling 50-event win rate)</div>
        <Plot
          data={[{ x: dates, y: Array.from(rollingWin), type: "scatter" as const, mode: "lines" as const, fill: "tozeroy" as const, fillcolor: t.accent + "30", line: { color: t.accent, width: 2 } }]}
          layout={{ height: 240, ...L, yaxis: { title: "Bet size (0-1)", range: [0, 1.05], gridcolor: t.grid }, xaxis: { gridcolor: t.grid }, shapes: [{ type: "line", xref: "paper" as const, x0: 0, x1: 1, y0: halfKelly, y1: halfKelly, line: { color: t.gain, dash: "dash", width: 1 } }], annotations: [{ xref: "paper" as const, yref: "y" as const, x: 1, y: halfKelly, xanchor: "right" as const, text: `Half Kelly: ${(halfKelly * 100).toFixed(1)}%`, showarrow: false, font: { color: t.gain, size: 10 } }] }}
          config={{ displayModeBar: false, responsive: true }}
          style={{ width: "100%" }}
        />
      </div>

      <div className="card">
        <div className="text-sm font-semibold mb-1">Strategy equity curve vs buy &amp; hold</div>
        <Plot
          data={[
            { x: dates, y: cumStrat.slice(1), type: "scatter" as const, mode: "lines" as const, name: `Triple Barrier (${sizing})`, line: { color: t.accent, width: 2 } },
            { x: bhDates, y: bh, type: "scatter" as const, mode: "lines" as const, name: "Buy & Hold", line: { color: t.muted, width: 1 } },
          ]}
          layout={{ height: 340, ...L, yaxis: { title: "Value ($100 start)", gridcolor: t.grid }, xaxis: { gridcolor: t.grid }, legend: { orientation: "h", y: -0.18 }, shapes: [{ type: "line", xref: "paper" as const, x0: 0, x1: 1, y0: 100, y1: 100, line: { color: t.muted, dash: "dash", width: 1 } }] }}
          config={{ displayModeBar: false, responsive: true }}
          style={{ width: "100%" }}
        />
        <div className="flex flex-wrap gap-6 mt-3">
          <Metric label="Cumulative return" value={`${(cumStratRet * 100).toFixed(1)}%`} deltaType={cumStratRet >= 0 ? "gain" : "loss"} />
          <Metric label="Ann. return" value={fmtPct(annRet)} />
          <Metric label="Ann. vol" value={`${annVol.toFixed(1)}%`} />
          <Metric label="Sharpe" value={sharpe.toFixed(2)} />
          <Metric label="Max drawdown" value={`${(maxDD * 100).toFixed(1)}%`} deltaType="loss" />
        </div>
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════
// TAB 4: SAMPLE WEIGHTS & BOOTSTRAP
// ═══════════════════════════════════════════════════════════════

function SampleWeightsTab({ data, t, L }: { data: QuantLabAnalyzeResponse; t: ReturnType<typeof getChartTheme>; L: ReturnType<typeof getBaseLayout> }) {
  const [window, setWindow] = useState(20);
  const [nBoot, setNBoot] = useState(1000);
  const returns = data.ohlcv.log_returns.slice(1);
  const dates = data.ohlcv.dates.slice(1);

  const uniqueness = useMemo(() => avgUniqueness(returns.length, window), [returns, window]);
  const avgU = mean(uniqueness);
  const minU = uniqueness.length > 0 ? Math.min(...uniqueness) : 0;
  const effSamples = uniqueness.reduce((s, v) => s + v, 0);

  const totalU = uniqueness.reduce((s, v) => s + v, 0);
  const probs = totalU > 0 ? uniqueness.map((u) => u / totalU) : uniqueness.map(() => 1 / uniqueness.length);

  const { standard, sequential } = useMemo(() => {
    const std = standardBootstrapSharpe(returns, nBoot);
    const seq = sequentialBootstrapSharpe(returns, probs, nBoot);
    return { standard: std, sequential: seq };
  }, [returns, probs, nBoot]);

  return (
    <div className="space-y-4">
      <div className="card card-compact">
        <div className="flex flex-wrap gap-4 items-center">
          <div>
            <label className="metric-label">Overlap window (days)</label>
            <input type="range" value={window} onChange={(e) => setWindow(Number(e.target.value))} min={5} max={60} step={5} className="w-48 accent-accent" />
            <span className="font-data text-xs ml-2">{window}D</span>
          </div>
          <div>
            <label className="metric-label">Bootstrap iterations</label>
            <input type="range" value={nBoot} onChange={(e) => setNBoot(Number(e.target.value))} min={500} max={5000} step={500} className="w-48 accent-accent" />
            <span className="font-data text-xs ml-2">{nBoot}</span>
          </div>
        </div>
      </div>

      <div className="card card-compact flex flex-wrap gap-6">
        <Metric label="Avg uniqueness" value={avgU.toFixed(3)} />
        <Metric label="Min uniqueness" value={minU.toFixed(3)} />
        <Metric label="Effective samples" value={effSamples.toFixed(0)} />
        <Metric label="Total obs" value={String(returns.length)} />
      </div>

      <div className="card">
        <div className="text-sm font-semibold mb-1">Average uniqueness (window={window}D)</div>
        <Plot
          data={[{ x: dates, y: uniqueness, type: "scatter" as const, mode: "lines" as const, line: { color: t.accent, width: 1 } }]}
          layout={{ height: 240, ...L, yaxis: { title: "Uniqueness", gridcolor: t.grid }, xaxis: { gridcolor: t.grid }, shapes: [{ type: "line", xref: "paper" as const, x0: 0, x1: 1, y0: avgU, y1: avgU, line: { color: t.spot, dash: "dash", width: 1 } }], annotations: [{ xref: "paper" as const, yref: "y" as const, x: 1, y: avgU, xanchor: "right" as const, text: `Mean: ${avgU.toFixed(3)}`, showarrow: false, font: { color: t.spot, size: 10 } }] }}
          config={{ displayModeBar: false, responsive: true }}
          style={{ width: "100%" }}
        />
      </div>

      <div className="card">
        <div className="text-sm font-semibold mb-1">Standard vs sequential bootstrap Sharpe distribution</div>
        <div className="text-xs text-text-muted mb-2">Sequential bootstrap weights by uniqueness — produces wider, more honest confidence intervals.</div>
        <Plot
          data={[
            { type: "histogram" as const, x: standard, name: "Standard", marker: { color: t.loss }, opacity: 0.6, nbinsx: 50 },
            { type: "histogram" as const, x: sequential, name: "Sequential", marker: { color: t.accent }, opacity: 0.6, nbinsx: 50 },
          ]}
          layout={{ height: 340, ...L, barmode: "overlay" as const, xaxis: { title: "Annualized Sharpe", gridcolor: t.grid }, yaxis: { gridcolor: t.grid }, legend: { orientation: "h", y: -0.2 } }}
          config={{ displayModeBar: false, responsive: true }}
          style={{ width: "100%" }}
        />
        <div className="flex flex-wrap gap-6 mt-3">
          <Metric label="Standard mean SR" value={mean(standard).toFixed(3)} />
          <Metric label="Sequential mean SR" value={mean(sequential).toFixed(3)} />
          <Metric label="Standard std" value={stddev(standard).toFixed(3)} />
          <Metric label="Sequential std" value={stddev(sequential).toFixed(3)} />
        </div>
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════
// TAB 5: FEATURE IMPORTANCE
// ═══════════════════════════════════════════════════════════════

function FeatureImportanceTab({ data, t, L }: { data: QuantLabAnalyzeResponse; t: ReturnType<typeof getChartTheme>; L: ReturnType<typeof getBaseLayout> }) {
  const fi = data.feature_importance;
  if (!fi) {
    return <div className="card text-xs text-text-muted">Insufficient data for feature importance analysis.</div>;
  }

  const features = fi.features;
  const mdiVals = features.map((f) => fi.mdi[f] ?? 0);
  const mdaVals = features.map((f) => fi.mda[f] ?? 0);

  // normalize each to 0-1
  const nMdi = normalize(mdiVals);
  const nMda = normalize(mdaVals);
  const avg = features.map((_, i) => (nMdi[i] + nMda[i]) / 2);
  const order = [...features.map((f, i) => ({ f, i, avg: avg[i] }))].sort((a, b) => b.avg - a.avg);
  const sortedFeat = order.map((o) => o.f);
  const z = order.map((o) => [nMdi[o.i], nMda[o.i]]);

  return (
    <div className="space-y-4">
      <div className="card card-compact flex flex-wrap gap-6">
        <Metric label="OOS accuracy (Random Forest)" value={`${(fi.oos_accuracy * 100).toFixed(1)}%`} />
        <Metric label="Features" value={String(features.length)} />
      </div>

      <div className="card">
        <div className="text-sm font-semibold mb-1">Feature importance — MDI vs MDA (normalized 0-1)</div>
        <div className="text-xs text-text-muted mb-2">MDI: in-sample impurity reduction. MDA: out-of-sample permutation importance (more honest).</div>
        <Plot
          data={[{
            ...heatmapTrace(t, "sequential", { colorbarTitle: "Normalized" }),
            z,
            x: ["MDI (Impurity)", "MDA (Accuracy)"],
            y: sortedFeat,
            zmin: 0, zmax: 1,
            text: z.map((row) => row.map((v) => v.toFixed(2))),
          }]}
          layout={{ height: heatmapHeight(features.length), ...L }}
          config={{ displayModeBar: false, responsive: true }}
          style={{ width: "100%" }}
        />
      </div>

      <div className="card">
        <div className="text-sm font-semibold mb-2">Raw importance values</div>
        <table className="data-table text-xs">
          <thead>
            <tr><th>Feature</th><th>MDI (raw)</th><th>MDA (raw)</th><th>Avg rank</th></tr>
          </thead>
          <tbody>
            {order.map((o, rank) => (
              <tr key={o.f}>
                <td className="font-semibold">{o.f}</td>
                <td className="font-data">{fi.mdi[o.f].toFixed(4)}</td>
                <td className="font-data">{fi.mda[o.f].toFixed(4)}</td>
                <td className="font-data">{rank + 1}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function normalize(xs: number[]): number[] {
  if (xs.length === 0) return [];
  const finite = xs.filter((v) => Number.isFinite(v));
  if (finite.length === 0) return xs.map(() => 0.5);
  const min = Math.min(...finite);
  const max = Math.max(...finite);
  const range = max - min;
  return range > 0 ? xs.map((x) => Number.isFinite(x) ? (x - min) / range : 0.5) : xs.map(() => 0.5);
}

// ═══════════════════════════════════════════════════════════════
// TAB 6: HRP (separate ticker list, new API call)
// ═══════════════════════════════════════════════════════════════

function HrpTab({ t, L }: { t: ReturnType<typeof getChartTheme>; L: ReturnType<typeof getBaseLayout> }) {
  const [input, setInput] = useState("SPY,TLT,GLD,EFA,IWM,USO,HYG,XLK");
  const [rebal, setRebal] = useState<"Monthly" | "Quarterly">("Monthly");
  const run = useMutation({
    mutationFn: () => fetchQuantLabHrp({
      tickers: input.split(",").map((s) => s.trim().toUpperCase()).filter(Boolean),
      lookback: 504,
      rebalance: rebal,
      estimation_window: 252,
    }),
  });
  const data: QuantLabHrpResponse | null = run.data && !run.data.error ? run.data : null;

  return (
    <div className="space-y-4">
      <div className="card card-compact">
        <div className="flex flex-wrap items-end gap-3">
          <div className="flex-1 min-w-[260px]">
            <label className="metric-label">Portfolio tickers (comma-sep)</label>
            <input value={input} onChange={(e) => setInput(e.target.value)} className="mt-0.5 w-full px-3 py-1.5 border border-border rounded text-sm bg-surface font-data" />
          </div>
          <div>
            <label className="metric-label">Rebalance</label>
            <div className="flex gap-1 mt-0.5">
              {(["Monthly", "Quarterly"] as const).map((r) => (
                <button key={r} onClick={() => setRebal(r)} className={`px-2.5 py-1.5 text-xs rounded ${rebal === r ? "bg-accent text-white" : "bg-surface-alt text-text-muted"}`}>{r}</button>
              ))}
            </div>
          </div>
          <button onClick={() => run.mutate()} disabled={run.isPending} className="px-5 py-2 bg-accent text-white font-semibold rounded-lg hover:bg-accent-hover disabled:opacity-50 text-sm">
            {run.isPending ? "Allocating…" : "Run HRP"}
          </button>
        </div>
      </div>

      {run.isPending && <div className="card text-center py-8"><div className="inline-block w-6 h-6 border-2 border-accent border-t-transparent rounded-full animate-spin" /></div>}

      {run.data?.error && <div className="card border-loss text-loss text-sm">{run.data.error}</div>}

      {data && (
        <>
          <div className="card">
            <div className="text-sm font-semibold mb-1">Portfolio weights: HRP vs Equal Weight vs Inverse Vol</div>
            <Plot
              data={[
                { type: "bar" as const, x: data.tickers, y: data.tickers.map((tk) => (data.weights.hrp[tk] ?? 0) * 100), name: "HRP", marker: { color: t.accent } },
                { type: "bar" as const, x: data.tickers, y: data.tickers.map((tk) => (data.weights.equal[tk] ?? 0) * 100), name: "Equal Weight", marker: { color: t.muted } },
                { type: "bar" as const, x: data.tickers, y: data.tickers.map((tk) => (data.weights.inverse_vol[tk] ?? 0) * 100), name: "Inverse Vol", marker: { color: t.spot } },
              ]}
              layout={{ height: 340, ...L, barmode: "group" as const, yaxis: { title: "Weight (%)", gridcolor: t.grid }, xaxis: { gridcolor: t.grid }, legend: { orientation: "h", y: -0.2 } }}
              config={{ displayModeBar: false, responsive: true }}
              style={{ width: "100%" }}
            />
          </div>

          <div className="card">
            <div className="text-sm font-semibold mb-1">Static backtest (base=100)</div>
            <Plot
              data={[
                { x: data.dates, y: data.cum_hrp, type: "scatter" as const, mode: "lines" as const, name: "HRP", line: { color: t.accent, width: 3 } },
                { x: data.dates, y: data.cum_eq, type: "scatter" as const, mode: "lines" as const, name: "Equal Weight", line: { color: t.muted, width: 1 } },
                { x: data.dates, y: data.cum_iv, type: "scatter" as const, mode: "lines" as const, name: "Inverse Vol", line: { color: t.spot, width: 1, dash: "dash" as const } },
              ]}
              layout={{ height: 380, ...L, yaxis: { title: "Value", gridcolor: t.grid }, xaxis: { gridcolor: t.grid }, legend: { orientation: "h", y: -0.18 }, shapes: [{ type: "line", xref: "paper" as const, x0: 0, x1: 1, y0: 100, y1: 100, line: { color: t.muted, dash: "dash", width: 1 } }] }}
              config={{ displayModeBar: false, responsive: true }}
              style={{ width: "100%" }}
            />
            <table className="data-table text-xs mt-3">
              <thead>
                <tr><th>Method</th><th>Ann. Return</th><th>Ann. Vol</th><th>Sharpe</th><th>Max DD</th></tr>
              </thead>
              <tbody>
                {([
                  { name: "HRP", m: data.static_metrics.hrp },
                  { name: "Equal Weight", m: data.static_metrics.equal },
                  { name: "Inverse Vol", m: data.static_metrics.inverse_vol },
                ] as const).map(({ name, m }) => (
                  <tr key={name}>
                    <td className="font-semibold">{name}</td>
                    <td className={`font-data ${m.ann_return >= 0 ? "text-gain" : "text-loss"}`}>{fmtPct(m.ann_return)}</td>
                    <td className="font-data">{m.ann_vol.toFixed(1)}%</td>
                    <td className="font-data">{m.sharpe.toFixed(2)}</td>
                    <td className="font-data text-loss">{m.max_dd.toFixed(1)}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="card">
            <div className="text-sm font-semibold mb-1">Walk-forward HRP ({data.walk_forward.rebalance}, 252D window)</div>
            <Plot
              data={[
                { x: data.walk_forward.dates, y: data.walk_forward.cum, type: "scatter" as const, mode: "lines" as const, name: `HRP WF`, line: { color: t.gain, width: 3 } },
                { x: data.dates, y: data.cum_hrp, type: "scatter" as const, mode: "lines" as const, name: "HRP Static", line: { color: t.accent, width: 1, dash: "dash" as const } },
                { x: data.dates, y: data.cum_eq, type: "scatter" as const, mode: "lines" as const, name: "Equal Weight", line: { color: t.muted, width: 1 } },
              ]}
              layout={{ height: 380, ...L, yaxis: { title: "Value", gridcolor: t.grid }, xaxis: { gridcolor: t.grid }, legend: { orientation: "h", y: -0.18 }, shapes: [{ type: "line", xref: "paper" as const, x0: 0, x1: 1, y0: 100, y1: 100, line: { color: t.muted, dash: "dash", width: 1 } }] }}
              config={{ displayModeBar: false, responsive: true }}
              style={{ width: "100%" }}
            />
            <div className="flex flex-wrap gap-6 mt-3">
              <Metric label="WF Ann Return" value={fmtPct(data.walk_forward.metrics.ann_return)} deltaType={data.walk_forward.metrics.ann_return >= 0 ? "gain" : "loss"} />
              <Metric label="WF Ann Vol" value={`${data.walk_forward.metrics.ann_vol.toFixed(1)}%`} />
              <Metric label="WF Sharpe" value={data.walk_forward.metrics.sharpe.toFixed(2)} />
              <Metric label="WF Max DD" value={`${data.walk_forward.metrics.max_dd.toFixed(1)}%`} deltaType="loss" />
              <Metric label="Rebalances" value={String(data.walk_forward.weight_history.length)} />
            </div>
          </div>

          <div className="card">
            <div className="text-sm font-semibold mb-1">HRP weight evolution</div>
            <Plot
              data={data.tickers.map((tk, i) => ({
                x: data.walk_forward.weight_history.map((h) => h.date),
                y: data.walk_forward.weight_history.map((h) => (h.weights[tk] ?? 0) * 100),
                type: "scatter" as const,
                mode: "lines" as const,
                name: tk,
                stackgroup: "one" as const,
              }))}
              layout={{ height: 340, ...L, yaxis: { title: "Weight (%)", gridcolor: t.grid, range: [0, 100] }, xaxis: { gridcolor: t.grid }, legend: { orientation: "h", y: -0.18 } }}
              config={{ displayModeBar: false, responsive: true }}
              style={{ width: "100%" }}
            />
          </div>
        </>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════
// TAB 7: MICROSTRUCTURE (client-side)
// ═══════════════════════════════════════════════════════════════

function MicrostructureTab({ data, t, L }: { data: QuantLabAnalyzeResponse; t: ReturnType<typeof getChartTheme>; L: ReturnType<typeof getBaseLayout> }) {
  const { close, high, low, volume, log_returns, dates } = data.ohlcv;
  const hasVolume = volume.length > 0 && volume.some((v) => v > 0);

  // Amihud illiquidity = |return| / dollar volume, rolling 20D mean
  const amihud20 = useMemo(() => {
    if (!hasVolume) return [];
    const raw: number[] = log_returns.map((r, i) => {
      const dv = volume[i] * close[i];
      return dv > 0 ? Math.abs(r) / dv : 0;
    });
    return rollingMean(raw, 20);
  }, [log_returns, volume, close, hasVolume]);

  // Kyle's Lambda: rolling 60 regression slope of |return| on volume
  const kyleDates: string[] = [];
  const kyleVals: number[] = [];
  if (hasVolume) {
    for (let i = 60; i < close.length; i++) {
      const wRet = log_returns.slice(i - 60, i).map(Math.abs);
      const wVol = volume.slice(i - 60, i);
      const varV = variance(wVol);
      if (varV > 0) {
        const beta = covariance(wRet, wVol) / varV;
        kyleDates.push(dates[i]);
        kyleVals.push(Math.abs(beta));
      }
    }
  }

  // VPIN: (|sum(signed*vol)| / total_vol) over rolling window
  const vpin: number[] = [];
  if (hasVolume) {
    const vpinWindow = 50;
    const signs = log_returns.map((r) => (r > 0 ? 1 : r < 0 ? -1 : 0));
    // forward-fill zeros
    for (let i = 1; i < signs.length; i++) if (signs[i] === 0) signs[i] = signs[i - 1];
    for (let i = vpinWindow - 1; i < log_returns.length; i++) {
      let buyVol = 0, sellVol = 0, totalVol = 0;
      for (let j = i - vpinWindow + 1; j <= i; j++) {
        const v = volume[j];
        totalVol += v;
        if (signs[j] >= 0) buyVol += v; else sellVol += v;
      }
      vpin.push(totalVol > 0 ? Math.abs(buyVol - sellVol) / totalVol : NaN);
    }
  }

  // Corwin-Schultz spread estimator (needs High/Low)
  const cs: number[] = [];
  const csDates: string[] = [];
  if (high.length > 0 && low.length > 0) {
    for (let i = 1; i < close.length; i++) {
      if (high[i] <= 0 || low[i] <= 0 || high[i - 1] <= 0 || low[i - 1] <= 0) { cs.push(NaN); csDates.push(dates[i]); continue; }
      const beta = Math.pow(Math.log(high[i - 1] / low[i - 1]), 2) + Math.pow(Math.log(high[i] / low[i]), 2);
      const hi2 = Math.max(high[i - 1], high[i]);
      const lo2 = Math.min(low[i - 1], low[i]);
      const gamma = Math.pow(Math.log(hi2 / lo2), 2);
      const denom = 3 - 2 * Math.sqrt(2);
      const alpha = (Math.sqrt(2 * beta) - Math.sqrt(beta)) / denom - Math.sqrt(gamma / denom);
      const s = 2 * (Math.exp(alpha) - 1) / (1 + Math.exp(alpha));
      cs.push(Math.max(0, Number.isFinite(s) ? s : NaN));
      csDates.push(dates[i]);
    }
  }
  const cs20 = rollingMean(cs, 20);

  const lastAmihud = lastFinite(amihud20) * 1e6;
  const lastVpin = lastFinite(vpin);
  const lastKyle = lastFinite(kyleVals) * 1e6;
  const lastCs = lastFinite(cs20) * 100;

  return (
    <div className="space-y-4">
      {!hasVolume && <div className="card text-sm text-text-muted">No volume data available — microstructure requires volume.</div>}

      <div className="card card-compact flex flex-wrap gap-6">
        <Metric label="Amihud (×10⁶)" value={lastAmihud.toFixed(2)} />
        <Metric label="VPIN" value={lastVpin.toFixed(3)} />
        <Metric label="Kyle's Lambda (×10⁶)" value={lastKyle.toFixed(2)} />
        {Number.isFinite(lastCs) && <Metric label="CS spread (20D, %)" value={`${lastCs.toFixed(3)}%`} />}
      </div>

      {hasVolume && (
        <>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div className="card">
              <div className="text-sm font-semibold mb-1">Amihud illiquidity (×10⁶)</div>
              <Plot
                data={[{ x: dates.slice(19), y: amihud20.slice(19).map((v) => v * 1e6), type: "scatter" as const, mode: "lines" as const, line: { color: t.accent, width: 2 } }]}
                layout={{ height: 260, ...L, yaxis: { gridcolor: t.grid }, xaxis: { gridcolor: t.grid } }}
                config={{ displayModeBar: false, responsive: true }}
                style={{ width: "100%" }}
              />
            </div>
            <div className="card">
              <div className="text-sm font-semibold mb-1">VPIN (flow toxicity)</div>
              <Plot
                data={[{ x: dates.slice(49), y: vpin, type: "scatter" as const, mode: "lines" as const, line: { color: t.loss, width: 2 } }]}
                layout={{ height: 260, ...L, yaxis: { range: [0, 1.05], gridcolor: t.grid }, xaxis: { gridcolor: t.grid }, shapes: [{ type: "line", xref: "paper" as const, x0: 0, x1: 1, y0: 0.5, y1: 0.5, line: { color: t.spot, dash: "dash", width: 1 } }] }}
                config={{ displayModeBar: false, responsive: true }}
                style={{ width: "100%" }}
              />
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div className="card">
              <div className="text-sm font-semibold mb-1">Kyle&apos;s Lambda (×10⁶)</div>
              <Plot
                data={[{ x: kyleDates, y: kyleVals.map((v) => v * 1e6), type: "scatter" as const, mode: "lines" as const, line: { color: t.spot, width: 2 } }]}
                layout={{ height: 260, ...L, yaxis: { gridcolor: t.grid }, xaxis: { gridcolor: t.grid } }}
                config={{ displayModeBar: false, responsive: true }}
                style={{ width: "100%" }}
              />
            </div>
            {cs20.length > 0 && (
              <div className="card">
                <div className="text-sm font-semibold mb-1">Corwin-Schultz effective spread (20D, %)</div>
                <Plot
                  data={[{ x: csDates.slice(19), y: cs20.slice(19).map((v) => v * 100), type: "scatter" as const, mode: "lines" as const, line: { color: t.loss, width: 2 } }]}
                  layout={{ height: 260, ...L, yaxis: { gridcolor: t.grid }, xaxis: { gridcolor: t.grid } }}
                  config={{ displayModeBar: false, responsive: true }}
                  style={{ width: "100%" }}
                />
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}

function rollingMean(xs: number[], n: number): number[] {
  const out: number[] = new Array(xs.length).fill(NaN);
  let sum = 0, count = 0;
  for (let i = 0; i < xs.length; i++) {
    if (Number.isFinite(xs[i])) { sum += xs[i]; count++; }
    if (i >= n) {
      const drop = xs[i - n];
      if (Number.isFinite(drop)) { sum -= drop; count--; }
    }
    if (i >= n - 1 && count > 0) out[i] = sum / count;
  }
  return out;
}

function variance(xs: number[]): number {
  const m = mean(xs);
  return xs.reduce((s, v) => s + (v - m) ** 2, 0) / xs.length;
}

function covariance(xs: number[], ys: number[]): number {
  const mx = mean(xs), my = mean(ys);
  let s = 0;
  for (let i = 0; i < xs.length; i++) s += (xs[i] - mx) * (ys[i] - my);
  return s / xs.length;
}

function lastFinite(xs: number[]): number {
  for (let i = xs.length - 1; i >= 0; i--) if (Number.isFinite(xs[i])) return xs[i];
  return NaN;
}

// ═══════════════════════════════════════════════════════════════
// TAB 8: ENTROPY (client-side)
// ═══════════════════════════════════════════════════════════════

function EntropyTab({ data, t, L }: { data: QuantLabAnalyzeResponse; t: ReturnType<typeof getChartTheme>; L: ReturnType<typeof getBaseLayout> }) {
  const [nBins, setNBins] = useState(10);
  const returns = data.ohlcv.log_returns.slice(1);
  const dates = data.ohlcv.dates.slice(1);

  const { bins, counts } = useMemo(() => quantileBins(returns, nBins), [returns, nBins]);
  const shannon = shannonEntropy(counts);
  const maxEntropy = Math.log2(nBins);
  const normalized = maxEntropy > 0 ? shannon / maxEntropy : 0;
  const nObs = returns.length;
  const biasCorrection = (nBins - 1) / (2 * nObs * Math.log(2));
  const plugin = shannon + biasCorrection;
  const lz = lempelZiv(returns);

  // rolling 63D normalized entropy
  const rolling: { date: string; entropy: number }[] = [];
  const w = 63;
  for (let i = w; i < returns.length; i++) {
    const slice = returns.slice(i - w, i);
    const { counts: wc } = quantileBins(slice, nBins);
    const h = shannonEntropy(wc);
    rolling.push({ date: dates[i], entropy: maxEntropy > 0 ? h / maxEntropy : 0 });
  }

  // Timeframe comparison: daily / weekly / monthly (sum returns)
  const weeklyReturns = aggregateReturns(returns, 5);
  const monthlyReturns = aggregateReturns(returns, 21);
  const timeframes = [
    { name: "Daily", data: returns },
    { name: "Weekly", data: weeklyReturns },
    { name: "Monthly", data: monthlyReturns },
  ];
  const tfResults = timeframes.filter((tf) => tf.data.length >= 30).map((tf) => {
    const { counts: c } = quantileBins(tf.data, Math.min(nBins, 20));
    const h = shannonEntropy(c);
    const maxH = Math.log2(Math.max(c.filter((x) => x > 0).length, 2));
    return { name: tf.name, obs: tf.data.length, shannon: h, normalized: maxH > 0 ? h / maxH : 0 };
  });

  // Conditional entropy / Markov
  const nStates = Math.min(nBins, 5);
  const { bins: stateBins } = useMemo(() => quantileBins(returns, nStates), [returns, nStates]);
  const transMat = useMemo(() => transitionMatrix(stateBins, nStates), [stateBins, nStates]);
  const { marginal, hUnc, hCond } = useMemo(() => {
    const marg: number[] = new Array(nStates).fill(0);
    stateBins.forEach((b) => marg[b]++);
    const total = marg.reduce((s, v) => s + v, 0);
    const mNorm = total > 0 ? marg.map((v) => v / total) : marg.map(() => 0);
    const hU = shannonEntropy(marg);
    // H(X|Y) = sum over y of P(y) * H(X|Y=y). Need ≥2 bins for joint pairs.
    let hC = 0;
    const pairCount = stateBins.length - 1;
    if (pairCount > 0) {
      const joint: number[][] = Array.from({ length: nStates }, () => new Array(nStates).fill(0));
      for (let i = 1; i < stateBins.length; i++) joint[stateBins[i - 1]][stateBins[i]]++;
      for (let y = 0; y < nStates; y++) {
        const ySum = joint[y].reduce((s, v) => s + v, 0);
        if (ySum === 0) continue;
        const pY = ySum / pairCount;
        let h = 0;
        for (let x = 0; x < nStates; x++) {
          const p = joint[y][x] / ySum;
          if (p > 0) h -= p * Math.log2(p);
        }
        hC += pY * h;
      }
    }
    return { marginal: mNorm, hUnc: hU, hCond: hC };
  }, [stateBins, nStates]);
  const mi = hUnc - hCond;

  const uniformProb = 1 / nBins;

  return (
    <div className="space-y-4">
      <div className="card card-compact">
        <div className="flex items-center gap-3">
          <label className="metric-label">Discretization bins</label>
          <input type="range" value={nBins} onChange={(e) => setNBins(Number(e.target.value))} min={3} max={20} step={1} className="flex-1 max-w-[260px] accent-accent" />
          <span className="font-data text-xs">{nBins}</span>
        </div>
      </div>

      <div className="card card-compact flex flex-wrap gap-6">
        <Metric label="Shannon" value={`${shannon.toFixed(3)} bits`} />
        <Metric label="Normalized" value={normalized.toFixed(3)} />
        <Metric label="Plugin" value={`${plugin.toFixed(3)} bits`} />
        <Metric label="Lempel-Ziv" value={lz.toFixed(3)} />
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div className="card">
          <div className="text-sm font-semibold mb-1">Return bin distribution</div>
          <Plot
            data={[{ type: "bar" as const, x: counts.map((_, i) => `Bin ${i}`), y: counts.map((c) => c / counts.reduce((s, v) => s + v, 0)), marker: { color: t.accent } }]}
            layout={{ height: 280, ...L, yaxis: { title: "Probability", gridcolor: t.grid }, xaxis: { gridcolor: t.grid }, shapes: [{ type: "line", xref: "paper" as const, x0: 0, x1: 1, y0: uniformProb, y1: uniformProb, line: { color: t.spot, dash: "dash", width: 1 } }] }}
            config={{ displayModeBar: false, responsive: true }}
            style={{ width: "100%" }}
          />
        </div>
        <div className="card">
          <div className="text-sm font-semibold mb-1">Rolling normalized entropy (63D)</div>
          <Plot
            data={[{ x: rolling.map((r) => r.date), y: rolling.map((r) => r.entropy), type: "scatter" as const, mode: "lines" as const, line: { color: t.accent, width: 2 } }]}
            layout={{ height: 280, ...L, yaxis: { range: [0.5, 1.05], gridcolor: t.grid }, xaxis: { gridcolor: t.grid }, shapes: [{ type: "line", xref: "paper" as const, x0: 0, x1: 1, y0: 1.0, y1: 1.0, line: { color: t.muted, dash: "dash", width: 1 } }, { type: "line", xref: "paper" as const, x0: 0, x1: 1, y0: 0.85, y1: 0.85, line: { color: t.spot, dash: "dash", width: 1 } }] }}
            config={{ displayModeBar: false, responsive: true }}
            style={{ width: "100%" }}
          />
        </div>
      </div>

      <div className="card">
        <div className="text-sm font-semibold mb-2">Entropy across timeframes</div>
        <table className="data-table text-xs">
          <thead>
            <tr><th>Timeframe</th><th>Obs</th><th>Shannon (bits)</th><th>Normalized</th></tr>
          </thead>
          <tbody>
            {tfResults.map((tf) => (
              <tr key={tf.name}>
                <td className="font-semibold">{tf.name}</td>
                <td className="font-data">{tf.obs}</td>
                <td className="font-data">{tf.shannon.toFixed(3)}</td>
                <td className="font-data">{tf.normalized.toFixed(3)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card">
        <div className="text-sm font-semibold mb-2">Transition matrix P(today | yesterday)</div>
        <div className="flex flex-wrap gap-6 mb-2">
          <Metric label="H(X)" value={`${hUnc.toFixed(3)} bits`} />
          <Metric label="H(X|X_prev)" value={`${hCond.toFixed(3)} bits`} />
          <Metric label="Mutual Info" value={`${mi.toFixed(4)} bits`} deltaType={mi > 0.01 ? "gain" : "neutral"} />
        </div>
        <Plot
          data={[{
            ...heatmapTrace(t, "intensity", { colorbarTitle: "P(X|Y)" }),
            z: transMat,
            x: Array.from({ length: nStates }, (_, i) => `Bin ${i}`),
            y: Array.from({ length: nStates }, (_, i) => `Lag ${i}`),
            text: transMat.map((row) => row.map((v) => v.toFixed(2))),
          }]}
          layout={{ height: 340, ...L }}
          config={{ displayModeBar: false, responsive: true }}
          style={{ width: "100%" }}
        />
      </div>
    </div>
  );
}

function aggregateReturns(rets: number[], k: number): number[] {
  const out: number[] = [];
  for (let i = 0; i + k <= rets.length; i += k) {
    let s = 0;
    for (let j = i; j < i + k; j++) s += rets[j];
    out.push(s);
  }
  return out;
}

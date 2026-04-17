"use client";

import { useState, useMemo } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTheme } from "next-themes";
import {
  fetchSignalBundle,
  type SignalScanBundle,
} from "@/lib/api";
import { getChartTheme, getBaseLayout } from "@/lib/chart-theme";
import { Metric } from "@/components/ui/metric";
import dynamic from "next/dynamic";

const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

const TABS = ["Dashboard", "Momentum", "Mean Reversion", "Value & Quality", "Earnings & Sentiment", "Regime & Micro", "Factor Correlation", "Composite"];

const UNIVERSES: Record<string, string[]> = {
  "S&P Sectors": ["XLK", "XLF", "XLE", "XLV", "XLI", "XLU", "XLP", "XLY", "XLC", "XLB", "XLRE"],
  "Mega Caps": ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "BRK-B", "JPM", "V", "UNH", "LLY", "XOM", "JNJ", "PG", "MA", "HD", "COST", "ABBV", "MRK"],
  "Growth vs Value": ["VUG", "VTV", "IWF", "IWD", "SPYG", "SPYV", "QQQ", "SCHD", "MGK", "RPV", "MTUM", "VLUE", "QUAL", "SIZE", "USMV"],
  "Multi-Asset": ["SPY", "QQQ", "IWM", "EFA", "EEM", "TLT", "IEF", "LQD", "HYG", "GLD", "SLV", "USO", "UNG", "DBA", "VNQ", "VIXY"],
};

// ─── Math helpers ──────────────────────────────────────────────────
function returns(closes: number[]): number[] {
  const r: number[] = [];
  for (let i = 1; i < closes.length; i++) {
    const base = closes[i - 1];
    r.push(base > 0 ? (closes[i] / base - 1) : 0);
  }
  return r;
}
function stddev(xs: number[]): number {
  if (xs.length < 2) return 0;
  const m = xs.reduce((s, v) => s + v, 0) / xs.length;
  return Math.sqrt(xs.reduce((s, v) => s + (v - m) ** 2, 0) / xs.length);
}
function rsi14(closes: number[]): number {
  if (closes.length < 15) return NaN;
  let gains = 0, losses = 0;
  for (let i = 1; i <= 14; i++) {
    const d = closes[i] - closes[i - 1];
    if (d > 0) gains += d; else losses -= d;
  }
  let avgG = gains / 14, avgL = losses / 14;
  for (let i = 15; i < closes.length; i++) {
    const d = closes[i] - closes[i - 1];
    avgG = (avgG * 13 + (d > 0 ? d : 0)) / 14;
    avgL = (avgL * 13 + (d < 0 ? -d : 0)) / 14;
  }
  if (avgL === 0) return 100;
  const rs = avgG / avgL;
  return 100 - 100 / (1 + rs);
}
function bollingerPct(closes: number[], n = 20): number {
  if (closes.length < n) return 50;
  const last = closes.slice(-n);
  const mean = last.reduce((s, v) => s + v, 0) / n;
  const sd = stddev(last);
  if (sd === 0) return 50;
  const upper = mean + 2 * sd;
  const lower = mean - 2 * sd;
  const pct = ((closes[closes.length - 1] - lower) / (upper - lower)) * 100;
  return Math.max(0, Math.min(100, pct));
}
function zScore63(closes: number[]): number {
  if (closes.length < 63) return NaN;
  const last = closes.slice(-63);
  const mean = last.reduce((s, v) => s + v, 0) / 63;
  const sd = stddev(last);
  return sd > 0 ? (closes[closes.length - 1] - mean) / sd : NaN;
}
function pctRank(values: Array<number | null>): Array<number | null> {
  const valid = values.map((v, i) => ({ v, i })).filter(x => x.v !== null && Number.isFinite(x.v as number));
  const n = valid.length;
  if (n === 0) return values.map(() => null);
  valid.sort((a, b) => (a.v as number) - (b.v as number));
  const rankMap = new Map<number, number>();
  valid.forEach((x, k) => rankMap.set(x.i, ((k + 1) / n) * 100));
  return values.map((v, i) => v === null || !Number.isFinite(v as number) ? null : (rankMap.get(i) ?? null));
}
function entropy(xs: number[], bins = 10): number {
  if (xs.length < 2) return NaN;
  const min = Math.min(...xs), max = Math.max(...xs);
  if (min === max) return 0;
  const counts = new Array(bins).fill(0);
  const w = (max - min) / bins;
  for (const x of xs) {
    const b = Math.min(bins - 1, Math.floor((x - min) / w));
    counts[b] += 1;
  }
  const total = xs.length;
  let H = 0;
  for (const c of counts) {
    if (c === 0) continue;
    const p = c / total;
    H -= p * Math.log2(p);
  }
  return H / Math.log2(bins); // normalize to 0-1
}
function vpinLike(volumes: number[], rets: number[], window = 50): number {
  // Simplified VPIN: |buy - sell| / (buy + sell) using sign of return as proxy
  if (volumes.length < window || rets.length < window) return NaN;
  const buys: number[] = [], sells: number[] = [];
  for (let i = 0; i < rets.length; i++) {
    const r = rets[i], v = volumes[i + 1] ?? volumes[i]; // volume at time t corresponds to return
    if (r > 0) { buys.push(v); sells.push(0); }
    else if (r < 0) { buys.push(0); sells.push(v); }
    else { buys.push(v / 2); sells.push(v / 2); }
  }
  const lastBuys = buys.slice(-window);
  const lastSells = sells.slice(-window);
  const totB = lastBuys.reduce((s, v) => s + v, 0);
  const totS = lastSells.reduce((s, v) => s + v, 0);
  const total = totB + totS;
  return total > 0 ? Math.abs(totB - totS) / total : NaN;
}

// Factor signal computation
type SignalRow = {
  ticker: string;
  closes: number[];
  volumes: number[];
  rets: number[];
  Mom_1M?: number; Mom_3M?: number; Mom_6M?: number; Mom_12M?: number;
  Mom_12_1?: number; Mom_Accel?: number; Mom_Consistency?: number; Mom_RiskAdj?: number;
  RSI_14?: number; BB_Position?: number; Z_Score_63D?: number; MR_Confluence?: number;
  Vol_20D?: number; Vol_Ratio?: number; Drawdown?: number;
  VPIN?: number; Entropy?: number;
};

export default function SignalScanner() {
  const { resolvedTheme } = useTheme();
  const t = getChartTheme(resolvedTheme === "dark");
  const L = getBaseLayout(t);

  const [universe, setUniverse] = useState("Mega Caps");
  const [customTickers, setCustomTickers] = useState("");
  const [lookback, setLookback] = useState<"6mo" | "1y" | "2y">("1y");
  const [activeTab, setActiveTab] = useState(0);

  const tickers = universe === "Custom"
    ? customTickers.split(",").map(s => s.trim().toUpperCase()).filter(Boolean)
    : UNIVERSES[universe];

  const [bundle, setBundle] = useState<SignalScanBundle | null>(null);
  const scan = useMutation({
    mutationFn: () => fetchSignalBundle(tickers, lookback),
    onSuccess: d => setBundle(d),
  });

  // Build SignalRow[] from bundle
  const rows: SignalRow[] = useMemo(() => {
    if (!bundle) return [];
    const out: SignalRow[] = [];
    for (const [tk, series] of Object.entries(bundle.prices)) {
      if (!series || series.length < 30) continue;
      const closes = series.map(r => r.Close);
      const volumes = series.map(r => r.Volume);
      const rets = returns(closes);
      const row: SignalRow = { ticker: tk, closes, volumes, rets };

      const len = closes.length;
      const pctAgo = (d: number) => len >= d ? ((closes[len - 1] / closes[len - d]) - 1) * 100 : undefined;
      row.Mom_1M = pctAgo(21);
      row.Mom_3M = pctAgo(63);
      row.Mom_6M = pctAgo(126);
      row.Mom_12M = pctAgo(252);
      if (len >= 252) {
        row.Mom_12_1 = (closes[len - 21] / closes[len - 252] - 1) * 100;
      }
      if (row.Mom_3M !== undefined && row.Mom_12M !== undefined) {
        row.Mom_Accel = row.Mom_3M * 4 - row.Mom_12M;
      }
      const momVals = [row.Mom_1M, row.Mom_3M, row.Mom_6M, row.Mom_12M].filter((v): v is number => v !== undefined);
      if (momVals.length > 0) {
        row.Mom_Consistency = momVals.filter(v => v > 0).length / momVals.length * 100;
      }
      // Risk-adjusted: 6m return / annualized vol from last 126 returns
      if (row.Mom_6M !== undefined && rets.length >= 63) {
        const vol = stddev(rets.slice(-Math.min(126, rets.length))) * Math.sqrt(252) * 100;
        row.Mom_RiskAdj = vol > 0 ? row.Mom_6M / vol : undefined;
      }

      row.RSI_14 = rsi14(closes);
      row.BB_Position = bollingerPct(closes, 20);
      row.Z_Score_63D = zScore63(closes);
      let os = 0, ob = 0;
      if (Number.isFinite(row.RSI_14)) { if (row.RSI_14! < 30) os++; else if (row.RSI_14! > 70) ob++; }
      if (row.BB_Position < 20) os++; else if (row.BB_Position! > 80) ob++;
      if (Number.isFinite(row.Z_Score_63D)) { if (row.Z_Score_63D! < -1.5) os++; else if (row.Z_Score_63D! > 1.5) ob++; }
      row.MR_Confluence = ob - os;

      if (rets.length >= 20) row.Vol_20D = stddev(rets.slice(-20)) * Math.sqrt(252) * 100;
      if (rets.length >= 63) {
        const v20 = stddev(rets.slice(-20));
        const v63 = stddev(rets.slice(-63));
        row.Vol_Ratio = v63 > 0 ? v20 / v63 : 1;
      }
      const peak = closes.reduce((m, v) => Math.max(m, v), -Infinity);
      row.Drawdown = ((closes[len - 1] / peak) - 1) * 100;

      if (volumes.length >= 63 && rets.length >= 63) {
        row.VPIN = vpinLike(volumes, rets, 50);
        row.Entropy = entropy(rets.slice(-63), 10);
      }

      out.push(row);
    }
    return out;
  }, [bundle]);

  const fundMap = useMemo(() => {
    const m = new Map<string, (typeof bundle extends null ? never : NonNullable<typeof bundle>)["fundamentals"][number]>();
    if (bundle) for (const f of bundle.fundamentals) m.set(f.ticker, f);
    return m;
  }, [bundle]);

  const epsMap = useMemo(() => {
    const m = new Map<string, (typeof bundle extends null ? never : NonNullable<typeof bundle>)["eps_revisions"][number]>();
    if (bundle) for (const e of bundle.eps_revisions) m.set(e.ticker, e);
    return m;
  }, [bundle]);

  const insiderMap = useMemo(() => {
    const m = new Map<string, (typeof bundle extends null ? never : NonNullable<typeof bundle>)["insider"][number]>();
    if (bundle) for (const i of bundle.insider) m.set(i.ticker, i);
    return m;
  }, [bundle]);

  // ─── Ranks (cross-sectional percentile) ─────────────────────────
  const ranks = useMemo(() => {
    if (rows.length === 0) return null;
    const tks = rows.map(r => r.ticker);
    const get = (key: keyof SignalRow): Array<number | null> => rows.map(r => {
      const v = r[key];
      return typeof v === "number" && Number.isFinite(v) ? v : null;
    });
    const invert = (arr: Array<number | null>) => arr.map(v => v === null ? null : -v);

    const out: Record<string, Array<number | null>> = {};
    // Momentum: higher = better
    out["Mom_1M"] = pctRank(get("Mom_1M"));
    out["Mom_3M"] = pctRank(get("Mom_3M"));
    out["Mom_6M"] = pctRank(get("Mom_6M"));
    out["Mom_12M"] = pctRank(get("Mom_12M"));
    if (rows.some(r => r.Mom_12_1 !== undefined)) out["Mom_12-1"] = pctRank(get("Mom_12_1"));
    if (rows.some(r => r.Mom_RiskAdj !== undefined)) out["Mom_RiskAdj"] = pctRank(get("Mom_RiskAdj"));
    if (rows.some(r => r.Mom_Consistency !== undefined)) out["Mom_Consistency"] = pctRank(get("Mom_Consistency"));
    // Mean reversion
    out["RSI_Signal"] = rows.map(r => Number.isFinite(r.RSI_14) ? (r.RSI_14! < 30 ? 100 : r.RSI_14! > 70 ? 0 : 50) : null);
    out["MeanRev"] = pctRank(invert(get("Z_Score_63D")));
    // Value (invert so lower = better)
    const fundGet = (key: keyof NonNullable<typeof bundle>["fundamentals"][number]): Array<number | null> => rows.map(r => {
      const f = fundMap.get(r.ticker);
      const v = f?.[key];
      return typeof v === "number" && Number.isFinite(v) ? v : null;
    });
    const hasFund = rows.some(r => fundMap.has(r.ticker));
    if (hasFund) {
      out["Value_PE"] = pctRank(invert(fundGet("forward_pe")));
      out["Value_PB"] = pctRank(invert(fundGet("price_to_book")));
      out["Value_EVEBITDA"] = pctRank(invert(fundGet("ev_ebitda")));
      out["Value_FCF"] = pctRank(fundGet("fcf_yield"));
      out["Carry"] = pctRank(fundGet("dividend_yield"));
      out["Quality_ROE"] = pctRank(fundGet("roe"));
      out["Quality_Margin"] = pctRank(fundGet("profit_margin"));
      out["Growth"] = pctRank(fundGet("revenue_growth"));
    }
    // EPS + insider
    if (rows.some(r => epsMap.has(r.ticker))) {
      const epsRatios = rows.map(r => {
        const e = epsMap.get(r.ticker);
        if (!e) return null;
        const tot = e.up_30d + e.down_30d;
        return tot > 0 ? (e.up_30d / tot) * 100 : null;
      });
      out["EPS_Mom"] = pctRank(epsRatios);
    }
    if (rows.some(r => insiderMap.has(r.ticker))) {
      const insiderNets = rows.map(r => insiderMap.get(r.ticker)?.net_value ?? null);
      out["Insider"] = pctRank(insiderNets);
    }
    // Low-vol: lower = better
    out["LowVol"] = pctRank(invert(get("Vol_20D")));

    // Composite
    const keys = Object.keys(out);
    const composite: Array<number | null> = tks.map((_, i) => {
      const vals = keys.map(k => out[k][i]).filter((v): v is number => v !== null);
      return vals.length > 0 ? vals.reduce((s, v) => s + v, 0) / vals.length : null;
    });
    out["Composite"] = composite;

    return { tickers: tks, ranks: out };
  }, [rows, fundMap, epsMap, insiderMap]);

  // ─── Render ────────────────────────────────────────────────────
  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Signal Scanner</h1>
        <p className="text-text-secondary text-sm mt-1">Cross-sectional factor analysis: momentum, mean reversion, value, quality, earnings, microstructure.</p>
      </div>

      {/* Controls */}
      <div className="card card-compact">
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <label className="metric-label">Universe</label>
            <select value={universe} onChange={e => setUniverse(e.target.value)} className="mt-0.5 px-3 py-1.5 border border-border rounded text-sm bg-surface min-w-[180px]">
              {Object.keys(UNIVERSES).map(u => <option key={u} value={u}>{u}</option>)}
              <option value="Custom">Custom</option>
            </select>
          </div>
          {universe === "Custom" && (
            <div className="flex-1 min-w-[300px]">
              <label className="metric-label">Tickers (comma-sep)</label>
              <input value={customTickers} onChange={e => setCustomTickers(e.target.value)} placeholder="AAPL,MSFT,NVDA" className="mt-0.5 w-full px-3 py-1.5 border border-border rounded text-sm bg-surface font-data" />
            </div>
          )}
          <div>
            <label className="metric-label">Lookback</label>
            <div className="flex gap-1 mt-0.5">
              {(["6mo", "1y", "2y"] as const).map(l => (
                <button key={l} onClick={() => setLookback(l)}
                  className={`px-3 py-1.5 text-xs rounded ${lookback === l ? "bg-accent text-white" : "bg-surface-alt text-text-muted"}`}>{l}</button>
              ))}
            </div>
          </div>
          <button onClick={() => scan.mutate()} disabled={scan.isPending || tickers.length < 3}
            className="px-6 py-2 bg-accent text-white font-semibold rounded-lg hover:bg-accent-hover disabled:opacity-50 text-sm">
            {scan.isPending ? `Scanning ${tickers.length}…` : `Scan Signals (${tickers.length})`}
          </button>
        </div>
      </div>

      {scan.isPending && <Spinner />}

      {bundle && rows.length > 0 && ranks && (<>
        <div className="flex gap-1 border-b border-border pb-1 overflow-x-auto">
          {TABS.map((tab, i) => (
            <button key={tab} onClick={() => setActiveTab(i)}
              className={`px-3 py-1.5 text-xs font-semibold rounded-t-md whitespace-nowrap ${activeTab === i ? "bg-accent text-white" : "text-text-muted hover:text-text hover:bg-surface-alt"}`}>{tab}</button>
          ))}
        </div>

        {/* Tab 0: Dashboard */}
        {activeTab === 0 && <DashboardTab rows={rows} ranks={ranks} t={t} L={L} />}
        {activeTab === 1 && <MomentumTab rows={rows} ranks={ranks} t={t} L={L} />}
        {activeTab === 2 && <MeanReversionTab rows={rows} t={t} L={L} />}
        {activeTab === 3 && <ValueQualityTab rows={rows} fundMap={fundMap} ranks={ranks} t={t} L={L} />}
        {activeTab === 4 && <EarningsTab rows={rows} epsMap={epsMap} insiderMap={insiderMap} t={t} L={L} />}
        {activeTab === 5 && <RegimeTab rows={rows} t={t} L={L} />}
        {activeTab === 6 && <FactorCorrelationTab ranks={ranks} t={t} L={L} />}
        {activeTab === 7 && <CompositeTab rows={rows} ranks={ranks} t={t} L={L} />}
      </>)}

      {!bundle && !scan.isPending && (
        <div className="card text-center py-10 text-sm text-text-muted">Pick a universe and click Scan Signals.</div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────
// Tab 0: Dashboard
// ─────────────────────────────────────────────────────────────────
type Ranks = { tickers: string[]; ranks: Record<string, Array<number | null>> };

function DashboardTab({ rows, ranks, t, L }: { rows: SignalRow[]; ranks: Ranks; t: ReturnType<typeof getChartTheme>; L: ReturnType<typeof getBaseLayout> }) {
  const rsiValid = rows.map(r => r.RSI_14).filter((v): v is number => Number.isFinite(v));
  const momValid = rows.map(r => r.Mom_3M).filter((v): v is number => v !== undefined);
  const volValid = rows.map(r => r.Vol_20D).filter((v): v is number => v !== undefined);
  const avgRsi = rsiValid.length ? rsiValid.reduce((s, v) => s + v, 0) / rsiValid.length : NaN;
  const avgMom = momValid.length ? momValid.reduce((s, v) => s + v, 0) / momValid.length : NaN;
  const avgVol = volValid.length ? volValid.reduce((s, v) => s + v, 0) / volValid.length : NaN;

  let regime = "Mixed", regimeColor = t.muted;
  if (Number.isFinite(avgRsi) && Number.isFinite(avgMom)) {
    if (avgRsi > 60 && avgMom > 5) { regime = "Bullish — broad momentum"; regimeColor = t.gain; }
    else if (avgRsi < 40 && avgMom < -5) { regime = "Bearish — broad weakness"; regimeColor = t.loss; }
    else if (Number.isFinite(avgVol) && avgVol > 30) { regime = "High volatility — risk off"; regimeColor = t.hv20; }
    else { regime = "Neutral — mixed signals"; regimeColor = t.muted; }
  }

  // Factor spreads (top-quintile rank mean − bottom-quintile mean)
  const FACTOR_GROUPS: Record<string, string[]> = {
    Momentum: ["Mom_1M", "Mom_3M", "Mom_6M", "Mom_12M", "Mom_12-1", "Mom_RiskAdj", "Mom_Consistency"],
    "Mean Reversion": ["RSI_Signal", "MeanRev"],
    Value: ["Value_PE", "Value_PB", "Value_EVEBITDA", "Value_FCF", "Carry"],
    Quality: ["Quality_ROE", "Quality_Margin", "Growth"],
    Earnings: ["EPS_Mom", "Insider"],
    Risk: ["LowVol"],
  };
  const spreads: Record<string, number> = {};
  const n = ranks.tickers.length;
  const qSize = Math.max(1, Math.floor(n / 5));
  for (const [gname, cols] of Object.entries(FACTOR_GROUPS)) {
    const active = cols.filter(c => ranks.ranks[c]);
    if (active.length === 0) continue;
    const avgs: number[] = [];
    for (let i = 0; i < n; i++) {
      const vals = active.map(c => ranks.ranks[c][i]).filter((v): v is number => v !== null);
      if (vals.length > 0) avgs.push(vals.reduce((s, v) => s + v, 0) / vals.length);
    }
    if (avgs.length < 2 * qSize) continue;
    const sorted = [...avgs].sort((a, b) => b - a);
    const top = sorted.slice(0, qSize).reduce((s, v) => s + v, 0) / qSize;
    const bot = sorted.slice(-qSize).reduce((s, v) => s + v, 0) / qSize;
    spreads[gname] = top - bot;
  }

  // Top + bottom composite picks
  const comp = ranks.ranks["Composite"];
  const compRanked = ranks.tickers.map((tk, i) => ({ tk, score: comp[i] })).filter(x => x.score !== null).sort((a, b) => (b.score as number) - (a.score as number));
  const top5 = compRanked.slice(0, 5);
  const bot5 = compRanked.slice(-5).reverse();

  // Heatmap
  const columns = Object.keys(ranks.ranks);
  const nonComp = columns.filter(c => c !== "Composite");
  const orderedCols = [...nonComp, "Composite"];
  const sortedIdx = [...ranks.tickers.keys()].sort((a, b) => {
    const av = ranks.ranks["Composite"][a] ?? -1;
    const bv = ranks.ranks["Composite"][b] ?? -1;
    return bv - av;
  });
  const z = sortedIdx.map(i => orderedCols.map(c => ranks.ranks[c][i] ?? null));
  const yLabels = sortedIdx.map(i => ranks.tickers[i]);

  return (
    <div className="space-y-4">
      <div className="rounded-lg border px-4 py-3" style={{ borderColor: regimeColor }}>
        <span className="font-bold text-sm" style={{ color: regimeColor }}>MARKET REGIME: {regime}</span>
        <span className="text-xs text-text-muted ml-4">Avg RSI: {Number.isFinite(avgRsi) ? avgRsi.toFixed(0) : "—"} · Avg 3M Mom: {Number.isFinite(avgMom) ? avgMom.toFixed(1) + "%" : "—"} · Avg Vol: {Number.isFinite(avgVol) ? avgVol.toFixed(0) + "%" : "—"}</span>
      </div>

      <div className="card card-compact">
        <div className="flex flex-wrap gap-6">
          {Object.entries(spreads).map(([g, s]) => (
            <Metric key={g} label={g} value={`${s.toFixed(0)}pt spread`} deltaType={s > 40 ? "gain" : s > 25 ? "neutral" : "loss"} />
          ))}
        </div>
        <div className="text-[11px] text-text-muted mt-2">Top-quintile minus bottom-quintile rank. Higher = stronger signal differentiation.</div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="card">
          <div className="text-sm font-semibold mb-2">Top Picks</div>
          {top5.map(p => {
            const row = rows.find(r => r.ticker === p.tk);
            return (
              <div key={p.tk} className="flex justify-between text-xs py-1 border-b border-border last:border-0">
                <span className="font-semibold">{p.tk}</span>
                <span className="font-data">Composite {(p.score as number).toFixed(0)}</span>
                <span className="text-text-muted">Mom 12-1: {row?.Mom_12_1 !== undefined ? row.Mom_12_1.toFixed(1) + "%" : "—"}</span>
                <span className="text-text-muted">RSI: {Number.isFinite(row?.RSI_14) ? (row!.RSI_14!).toFixed(0) : "—"}</span>
              </div>
            );
          })}
        </div>
        <div className="card">
          <div className="text-sm font-semibold mb-2">Bottom Concerns</div>
          {bot5.map(p => {
            const row = rows.find(r => r.ticker === p.tk);
            return (
              <div key={p.tk} className="flex justify-between text-xs py-1 border-b border-border last:border-0">
                <span className="font-semibold">{p.tk}</span>
                <span className="font-data">Composite {(p.score as number).toFixed(0)}</span>
                <span className="text-text-muted">Mom 12-1: {row?.Mom_12_1 !== undefined ? row.Mom_12_1.toFixed(1) + "%" : "—"}</span>
                <span className="text-loss">DD: {row?.Drawdown !== undefined ? row.Drawdown.toFixed(1) + "%" : "—"}</span>
              </div>
            );
          })}
        </div>
      </div>

      <div className="card">
        <div className="text-sm font-semibold mb-2">Multi-Factor Signal Heatmap ({ranks.tickers.length} assets)</div>
        <Plot
          data={[{
            type: "heatmap" as const,
            z, x: orderedCols, y: yLabels,
            colorscale: [[0, t.loss], [0.5, t.grid], [1, t.gain]], zmid: 50, zmin: 0, zmax: 100,
            text: z.map(row => row.map(v => v === null ? "" : v.toFixed(0))), texttemplate: "%{text}", textfont: { size: 9 },
            colorbar: { title: { text: "Rank", font: { size: 10 } }, thickness: 12 },
            hovertemplate: "%{y} · %{x}: %{z:.0f}th pctl<extra></extra>",
          }]}
          layout={{ height: Math.max(400, ranks.tickers.length * 22), ...L, margin: { l: 80, r: 40, t: 10, b: 80 }, xaxis: { tickangle: -45 } }}
          config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }}
        />
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────
// Tab 1: Momentum
// ─────────────────────────────────────────────────────────────────
function MomentumTab({ rows, ranks, t, L }: { rows: SignalRow[]; ranks: Ranks; t: ReturnType<typeof getChartTheme>; L: ReturnType<typeof getBaseLayout> }) {
  const sorted = [...rows].sort((a, b) => (b.Mom_6M ?? -Infinity) - (a.Mom_6M ?? -Infinity));
  const periods: Array<["Mom_1M" | "Mom_3M" | "Mom_6M" | "Mom_12M" | "Mom_12_1", string, string]> = [
    ["Mom_1M", "1M", t.accent], ["Mom_3M", "3M", t.hv20], ["Mom_6M", "6M", t.gain], ["Mom_12M", "12M", t.hv60], ["Mom_12_1", "12-1", t.loss],
  ];

  // L/S spread using 12-1 momentum
  const n = rows.length;
  const qSize = Math.max(1, Math.floor(n / 5));
  const byMom121 = [...rows].filter(r => r.Mom_12_1 !== undefined).sort((a, b) => (b.Mom_12_1 as number) - (a.Mom_12_1 as number));
  const topQ = byMom121.slice(0, qSize).map(r => r.ticker);
  const botQ = byMom121.slice(-qSize).map(r => r.ticker);
  const spread = byMom121.length >= 2 * qSize
    ? (byMom121.slice(0, qSize).reduce((s, r) => s + (r.Mom_12_1 as number), 0) / qSize)
      - (byMom121.slice(-qSize).reduce((s, r) => s + (r.Mom_12_1 as number), 0) / qSize)
    : NaN;

  return (
    <div className="space-y-4">
      <div className="card">
        <div className="text-sm font-semibold mb-2">Cross-Sectional Momentum</div>
        <Plot
          data={periods.map(([key, label, color]) => ({
            type: "bar" as const, name: label,
            x: sorted.map(r => r.ticker),
            y: sorted.map(r => r[key] ?? 0),
            marker: { color },
          }))}
          layout={{ height: 400, ...L, barmode: "group", yaxis: { title: "Return (%)", gridcolor: t.grid }, xaxis: { gridcolor: t.grid } }}
          config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }}
        />
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="card card-compact">
          <Metric label="L/S Spread (12-1 mom)" value={Number.isFinite(spread) ? spread.toFixed(1) + "%" : "—"} deltaType={spread > 0 ? "gain" : "loss"} />
          <div className="text-[11px] text-text-muted mt-2">Long top quintile minus short bottom quintile.</div>
        </div>
        <div className="card">
          <div className="text-sm font-semibold mb-2">Top Quintile (Long)</div>
          <div className="flex flex-wrap gap-1">{topQ.map(tk => <span key={tk} className="px-2 py-1 text-xs bg-gain/20 text-gain rounded">{tk}</span>)}</div>
          <div className="text-sm font-semibold mt-3 mb-2">Bottom Quintile (Short)</div>
          <div className="flex flex-wrap gap-1">{botQ.map(tk => <span key={tk} className="px-2 py-1 text-xs bg-loss/20 text-loss rounded">{tk}</span>)}</div>
        </div>
      </div>

      <div className="card">
        <div className="text-sm font-semibold mb-2">Momentum Quality</div>
        <div className="overflow-x-auto max-h-[400px] overflow-y-auto">
          <table className="data-table text-xs">
            <thead className="sticky top-0 bg-surface">
              <tr><th>Ticker</th><th className="text-right">1M</th><th className="text-right">3M</th><th className="text-right">6M</th><th className="text-right">12M</th><th className="text-right">12-1</th><th className="text-right">Accel</th><th className="text-right">Consistency</th><th className="text-right">Risk-Adj</th></tr>
            </thead>
            <tbody>
              {sorted.map(r => {
                const fmt = (v?: number) => v === undefined ? "—" : (v >= 0 ? "+" : "") + v.toFixed(1);
                const cls = (v?: number) => v === undefined ? "text-text-muted" : v > 0 ? "text-gain" : "text-loss";
                return (
                  <tr key={r.ticker}>
                    <td className="font-semibold">{r.ticker}</td>
                    <td className={`font-data text-right ${cls(r.Mom_1M)}`}>{fmt(r.Mom_1M)}</td>
                    <td className={`font-data text-right ${cls(r.Mom_3M)}`}>{fmt(r.Mom_3M)}</td>
                    <td className={`font-data text-right ${cls(r.Mom_6M)}`}>{fmt(r.Mom_6M)}</td>
                    <td className={`font-data text-right ${cls(r.Mom_12M)}`}>{fmt(r.Mom_12M)}</td>
                    <td className={`font-data text-right ${cls(r.Mom_12_1)}`}>{fmt(r.Mom_12_1)}</td>
                    <td className={`font-data text-right ${cls(r.Mom_Accel)}`}>{fmt(r.Mom_Accel)}</td>
                    <td className="font-data text-right">{r.Mom_Consistency !== undefined ? r.Mom_Consistency.toFixed(0) + "%" : "—"}</td>
                    <td className="font-data text-right">{r.Mom_RiskAdj !== undefined ? r.Mom_RiskAdj.toFixed(2) : "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────
// Tab 2: Mean Reversion
// ─────────────────────────────────────────────────────────────────
function MeanReversionTab({ rows, t, L }: { rows: SignalRow[]; t: ReturnType<typeof getChartTheme>; L: ReturnType<typeof getBaseLayout> }) {
  const sorted = [...rows].sort((a, b) => (b.MR_Confluence ?? 0) - (a.MR_Confluence ?? 0));
  const oversold = rows.filter(r => (r.MR_Confluence ?? 0) <= -2);
  const overbought = rows.filter(r => (r.MR_Confluence ?? 0) >= 2);

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="card">
          <div className="text-sm font-semibold mb-2">RSI-14 Distribution</div>
          <Plot
            data={[{
              type: "bar" as const,
              x: sorted.map(r => r.ticker),
              y: sorted.map(r => r.RSI_14 ?? 0),
              marker: { color: sorted.map(r => (r.RSI_14 ?? 50) < 30 ? t.gain : (r.RSI_14 ?? 50) > 70 ? t.loss : t.muted) },
            }]}
            layout={{
              height: 340, ...L,
              yaxis: { title: "RSI", range: [0, 100], gridcolor: t.grid },
              xaxis: { gridcolor: t.grid },
              shapes: [
                { type: "line", x0: 0, x1: 1, xref: "paper", y0: 30, y1: 30, line: { color: t.gain, width: 1, dash: "dash" as const } },
                { type: "line", x0: 0, x1: 1, xref: "paper", y0: 70, y1: 70, line: { color: t.loss, width: 1, dash: "dash" as const } },
              ],
            }}
            config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }}
          />
        </div>
        <div className="card">
          <div className="text-sm font-semibold mb-2">63-Day Z-Score</div>
          <Plot
            data={[{
              type: "bar" as const,
              x: sorted.map(r => r.ticker),
              y: sorted.map(r => r.Z_Score_63D ?? 0),
              marker: { color: sorted.map(r => (r.Z_Score_63D ?? 0) < -1.5 ? t.gain : (r.Z_Score_63D ?? 0) > 1.5 ? t.loss : t.muted) },
            }]}
            layout={{
              height: 340, ...L,
              yaxis: { title: "Z-Score", gridcolor: t.grid }, xaxis: { gridcolor: t.grid },
              shapes: [
                { type: "line", x0: 0, x1: 1, xref: "paper", y0: -1.5, y1: -1.5, line: { color: t.gain, width: 1, dash: "dash" as const } },
                { type: "line", x0: 0, x1: 1, xref: "paper", y0: 1.5, y1: 1.5, line: { color: t.loss, width: 1, dash: "dash" as const } },
              ],
            }}
            config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }}
          />
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="card border-l-4 border-l-gain">
          <div className="text-sm font-semibold text-gain mb-2">Oversold — Mean-Reversion Longs</div>
          {oversold.length === 0 ? <div className="text-xs text-text-muted">None.</div> : oversold.map(r => (
            <div key={r.ticker} className="text-xs py-1 border-b border-border last:border-0 flex justify-between">
              <span className="font-semibold">{r.ticker}</span>
              <span>RSI {r.RSI_14?.toFixed(0)} · BB {r.BB_Position?.toFixed(0)} · Z {r.Z_Score_63D?.toFixed(2)}</span>
              <span className="text-gain font-semibold">{r.MR_Confluence}</span>
            </div>
          ))}
        </div>
        <div className="card border-l-4 border-l-loss">
          <div className="text-sm font-semibold text-loss mb-2">Overbought — Mean-Reversion Shorts</div>
          {overbought.length === 0 ? <div className="text-xs text-text-muted">None.</div> : overbought.map(r => (
            <div key={r.ticker} className="text-xs py-1 border-b border-border last:border-0 flex justify-between">
              <span className="font-semibold">{r.ticker}</span>
              <span>RSI {r.RSI_14?.toFixed(0)} · BB {r.BB_Position?.toFixed(0)} · Z {r.Z_Score_63D?.toFixed(2)}</span>
              <span className="text-loss font-semibold">+{r.MR_Confluence}</span>
            </div>
          ))}
        </div>
      </div>

      <div className="card">
        <div className="text-sm font-semibold mb-2">Signal Confluence ({rows.length})</div>
        <div className="overflow-x-auto max-h-[400px] overflow-y-auto">
          <table className="data-table text-xs">
            <thead className="sticky top-0 bg-surface"><tr><th>Ticker</th><th className="text-right">RSI</th><th className="text-right">BB %</th><th className="text-right">Z-63D</th><th className="text-right">Confluence</th></tr></thead>
            <tbody>
              {sorted.map(r => (
                <tr key={r.ticker}>
                  <td className="font-semibold">{r.ticker}</td>
                  <td className="font-data text-right">{r.RSI_14?.toFixed(0) ?? "—"}</td>
                  <td className="font-data text-right">{r.BB_Position?.toFixed(0) ?? "—"}</td>
                  <td className="font-data text-right">{r.Z_Score_63D?.toFixed(2) ?? "—"}</td>
                  <td className={`font-data text-right ${(r.MR_Confluence ?? 0) < 0 ? "text-gain" : (r.MR_Confluence ?? 0) > 0 ? "text-loss" : ""}`}>{r.MR_Confluence ?? 0}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────
// Tab 3: Value & Quality
// ─────────────────────────────────────────────────────────────────
function ValueQualityTab({ rows, fundMap, ranks, t, L }: { rows: SignalRow[]; fundMap: Map<string, import("@/lib/api").SignalFundamentals>; ranks: Ranks; t: ReturnType<typeof getChartTheme>; L: ReturnType<typeof getBaseLayout> }) {
  const valueCols = ["Value_PE", "Value_PB", "Value_EVEBITDA", "Value_FCF", "Carry"].filter(c => ranks.ranks[c]);
  const qualityCols = ["Quality_ROE", "Quality_Margin", "Growth"].filter(c => ranks.ranks[c]);
  const hasFund = rows.some(r => fundMap.has(r.ticker));

  if (!hasFund) {
    return <div className="card text-center py-8 text-sm text-text-muted">No fundamental data available (some universes like ETFs won&apos;t have it).</div>;
  }

  const fmt = (v: number | null | undefined, unit = "") => v == null || !Number.isFinite(v) ? "—" : v.toFixed(1) + unit;

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="card">
          <div className="text-sm font-semibold mb-2">Value Factor Ranks</div>
          {valueCols.length === 0 ? <div className="text-xs text-text-muted">No valuation data.</div> : (
            <Plot
              data={valueCols.map((c, i) => ({
                type: "bar" as const, name: c.replace("Value_", "").replace("_", " "),
                x: ranks.tickers, y: ranks.ranks[c].map(v => v ?? 0),
                marker: { color: [t.accent, t.hv20, t.hv60, t.gain, t.spot][i % 5] },
              }))}
              layout={{ height: 340, ...L, barmode: "group", yaxis: { title: "Rank (0-100)", range: [0, 100], gridcolor: t.grid }, xaxis: { gridcolor: t.grid } }}
              config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }}
            />
          )}
        </div>
        <div className="card">
          <div className="text-sm font-semibold mb-2">Quality Factor Ranks</div>
          {qualityCols.length === 0 ? <div className="text-xs text-text-muted">No quality data.</div> : (
            <Plot
              data={qualityCols.map((c, i) => ({
                type: "bar" as const, name: c.replace("Quality_", "").replace("_", " "),
                x: ranks.tickers, y: ranks.ranks[c].map(v => v ?? 0),
                marker: { color: [t.gain, t.hv60, t.accent][i % 3] },
              }))}
              layout={{ height: 340, ...L, barmode: "group", yaxis: { title: "Rank (0-100)", range: [0, 100], gridcolor: t.grid }, xaxis: { gridcolor: t.grid } }}
              config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }}
            />
          )}
        </div>
      </div>

      <div className="card">
        <div className="text-sm font-semibold mb-2">Raw Fundamentals</div>
        <div className="overflow-x-auto max-h-[400px] overflow-y-auto">
          <table className="data-table text-xs">
            <thead className="sticky top-0 bg-surface">
              <tr><th>Ticker</th><th className="text-right">Fwd P/E</th><th className="text-right">P/B</th><th className="text-right">EV/EBITDA</th><th className="text-right">Div Yld</th><th className="text-right">FCF Yld</th><th className="text-right">ROE</th><th className="text-right">Profit Margin</th><th className="text-right">Rev Growth</th><th className="text-right">Beta</th></tr>
            </thead>
            <tbody>
              {rows.map(r => {
                const f = fundMap.get(r.ticker);
                return (
                  <tr key={r.ticker}>
                    <td className="font-semibold">{r.ticker}</td>
                    <td className="font-data text-right">{fmt(f?.forward_pe)}</td>
                    <td className="font-data text-right">{fmt(f?.price_to_book)}</td>
                    <td className="font-data text-right">{fmt(f?.ev_ebitda)}</td>
                    <td className="font-data text-right">{fmt(f?.dividend_yield, "%")}</td>
                    <td className="font-data text-right">{fmt(f?.fcf_yield, "%")}</td>
                    <td className="font-data text-right">{fmt(f?.roe, "%")}</td>
                    <td className="font-data text-right">{fmt(f?.profit_margin, "%")}</td>
                    <td className="font-data text-right">{fmt(f?.revenue_growth, "%")}</td>
                    <td className="font-data text-right">{fmt(f?.beta)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────
// Tab 4: Earnings & Sentiment
// ─────────────────────────────────────────────────────────────────
function EarningsTab({ rows, epsMap, insiderMap, t, L }: { rows: SignalRow[]; epsMap: Map<string, import("@/lib/api").SignalEpsRow>; insiderMap: Map<string, import("@/lib/api").SignalInsiderRow>; t: ReturnType<typeof getChartTheme>; L: ReturnType<typeof getBaseLayout> }) {
  const hasEps = rows.some(r => epsMap.has(r.ticker));
  const hasInsider = rows.some(r => insiderMap.has(r.ticker));

  return (
    <div className="space-y-4">
      {!hasEps && !hasInsider && (
        <div className="card text-center py-8 text-sm text-text-muted">No EPS revision or insider activity data for these tickers.</div>
      )}

      {hasEps && (
        <div className="card">
          <div className="text-sm font-semibold mb-2">EPS Estimate Revisions (30-day, net)</div>
          <Plot
            data={[{
              type: "bar" as const,
              x: rows.map(r => r.ticker),
              y: rows.map(r => epsMap.get(r.ticker)?.net_30d ?? 0),
              marker: { color: rows.map(r => (epsMap.get(r.ticker)?.net_30d ?? 0) > 0 ? t.gain : t.loss) },
            }]}
            layout={{ height: 340, ...L, yaxis: { title: "Net Revisions (Up − Down)", gridcolor: t.grid }, xaxis: { gridcolor: t.grid } }}
            config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }}
          />
        </div>
      )}

      {hasInsider && (
        <div className="card">
          <div className="text-sm font-semibold mb-2">Insider Net Activity (90-day value)</div>
          <Plot
            data={[{
              type: "bar" as const,
              x: rows.map(r => r.ticker),
              y: rows.map(r => insiderMap.get(r.ticker)?.net_value ?? 0),
              marker: { color: rows.map(r => (insiderMap.get(r.ticker)?.net_value ?? 0) > 0 ? t.gain : t.loss) },
            }]}
            layout={{ height: 340, ...L, yaxis: { title: "Net $ Value (buys − sells)", gridcolor: t.grid }, xaxis: { gridcolor: t.grid } }}
            config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }}
          />
        </div>
      )}

      <div className="card">
        <div className="text-sm font-semibold mb-2">Combined Table</div>
        <div className="overflow-x-auto max-h-[400px] overflow-y-auto">
          <table className="data-table text-xs">
            <thead className="sticky top-0 bg-surface"><tr><th>Ticker</th><th className="text-right">Up 30d</th><th className="text-right">Down 30d</th><th className="text-right">Net</th><th className="text-right">Insider Buys</th><th className="text-right">Insider Sells</th><th className="text-right">Net $</th></tr></thead>
            <tbody>
              {rows.map(r => {
                const e = epsMap.get(r.ticker);
                const i = insiderMap.get(r.ticker);
                return (
                  <tr key={r.ticker}>
                    <td className="font-semibold">{r.ticker}</td>
                    <td className="font-data text-right text-gain">{e?.up_30d ?? "—"}</td>
                    <td className="font-data text-right text-loss">{e?.down_30d ?? "—"}</td>
                    <td className={`font-data text-right font-semibold ${(e?.net_30d ?? 0) > 0 ? "text-gain" : (e?.net_30d ?? 0) < 0 ? "text-loss" : ""}`}>{e?.net_30d ?? "—"}</td>
                    <td className="font-data text-right text-gain">{i?.buy_count ?? "—"}</td>
                    <td className="font-data text-right text-loss">{i?.sell_count ?? "—"}</td>
                    <td className={`font-data text-right ${(i?.net_value ?? 0) > 0 ? "text-gain" : (i?.net_value ?? 0) < 0 ? "text-loss" : ""}`}>
                      {i?.net_value != null ? (i.net_value >= 0 ? "+" : "-") + "$" + (Math.abs(i.net_value) / 1e6).toFixed(1) + "M" : "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────
// Tab 5: Regime & Microstructure
// ─────────────────────────────────────────────────────────────────
function RegimeTab({ rows, t, L }: { rows: SignalRow[]; t: ReturnType<typeof getChartTheme>; L: ReturnType<typeof getBaseLayout> }) {
  const classify = (r: SignalRow): { regime: "FAVORABLE" | "CAUTION" | "AVOID"; color: string } => {
    let w = 0;
    if (r.VPIN !== undefined && r.VPIN > 0.6) w++;
    if (r.Entropy !== undefined && r.Entropy > 0.85) w++;
    if (r.Vol_Ratio !== undefined && r.Vol_Ratio > 1.3) w++;
    if (w >= 2) return { regime: "AVOID", color: t.loss };
    if (w === 1) return { regime: "CAUTION", color: t.hv20 };
    return { regime: "FAVORABLE", color: t.gain };
  };

  const sortedVR = [...rows].sort((a, b) => (a.Vol_Ratio ?? 1) - (b.Vol_Ratio ?? 1));
  const sortedVPIN = [...rows].filter(r => r.VPIN !== undefined).sort((a, b) => (a.VPIN as number) - (b.VPIN as number));
  const sortedEnt = [...rows].filter(r => r.Entropy !== undefined).sort((a, b) => (a.Entropy as number) - (b.Entropy as number));

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="card">
          <div className="text-sm font-semibold mb-2">Volatility Regime (20D / 63D Vol Ratio)</div>
          <Plot
            data={[{
              type: "bar" as const, orientation: "h" as const,
              y: sortedVR.map(r => r.ticker),
              x: sortedVR.map(r => r.Vol_Ratio ?? 1),
              marker: { color: sortedVR.map(r => (r.Vol_Ratio ?? 1) > 1.3 ? t.loss : (r.Vol_Ratio ?? 1) > 1.1 ? t.hv20 : (r.Vol_Ratio ?? 1) < 0.7 ? t.gain : t.accent) },
              text: sortedVR.map(r => (r.Vol_Ratio ?? 1).toFixed(2)), textposition: "outside" as const, textfont: { size: 10 },
            }]}
            layout={{
              height: Math.max(300, sortedVR.length * 22), ...L,
              xaxis: { title: "Vol Ratio", gridcolor: t.grid },
              yaxis: { autorange: "reversed", gridcolor: t.grid },
              shapes: [
                { type: "line", x0: 1.0, x1: 1.0, yref: "paper", y0: 0, y1: 1, line: { color: t.muted, width: 1, dash: "dash" as const } },
                { type: "line", x0: 1.3, x1: 1.3, yref: "paper", y0: 0, y1: 1, line: { color: t.loss, width: 1, dash: "dash" as const } },
                { type: "line", x0: 0.7, x1: 0.7, yref: "paper", y0: 0, y1: 1, line: { color: t.gain, width: 1, dash: "dash" as const } },
              ],
              margin: { l: 70, r: 60, t: 10, b: 40 },
            }}
            config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }}
          />
        </div>
        <div className="card">
          <div className="text-sm font-semibold mb-2">VPIN — Informed Trading Probability</div>
          {sortedVPIN.length > 0 ? (
            <Plot
              data={[{
                type: "bar" as const, orientation: "h" as const,
                y: sortedVPIN.map(r => r.ticker),
                x: sortedVPIN.map(r => r.VPIN as number),
                marker: { color: sortedVPIN.map(r => (r.VPIN as number) > 0.6 ? t.loss : (r.VPIN as number) > 0.45 ? t.hv20 : t.accent) },
                text: sortedVPIN.map(r => (r.VPIN as number).toFixed(2)), textposition: "outside" as const, textfont: { size: 10 },
              }]}
              layout={{
                height: Math.max(300, sortedVPIN.length * 22), ...L,
                xaxis: { title: "VPIN (0-1)", range: [0, 1], gridcolor: t.grid },
                yaxis: { autorange: "reversed", gridcolor: t.grid },
                shapes: [{ type: "line", x0: 0.6, x1: 0.6, yref: "paper", y0: 0, y1: 1, line: { color: t.loss, width: 1, dash: "dash" as const } }],
                margin: { l: 70, r: 60, t: 10, b: 40 },
              }}
              config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }}
            />
          ) : <div className="text-xs text-text-muted">Need 63+ days of data.</div>}
        </div>
      </div>

      {sortedEnt.length > 0 && (
        <div className="card">
          <div className="text-sm font-semibold mb-2">Entropy — Market Orderliness (lower = more predictable)</div>
          <Plot
            data={[{
              type: "bar" as const, orientation: "h" as const,
              y: sortedEnt.map(r => r.ticker),
              x: sortedEnt.map(r => r.Entropy as number),
              marker: { color: sortedEnt.map(r => (r.Entropy as number) > 0.85 ? t.loss : (r.Entropy as number) > 0.7 ? t.hv20 : t.accent) },
              text: sortedEnt.map(r => (r.Entropy as number).toFixed(2)), textposition: "outside" as const, textfont: { size: 10 },
            }]}
            layout={{
              height: Math.max(280, sortedEnt.length * 22), ...L,
              xaxis: { title: "Entropy (0-1)", range: [0, 1], gridcolor: t.grid },
              yaxis: { autorange: "reversed", gridcolor: t.grid },
              shapes: [{ type: "line", x0: 0.85, x1: 0.85, yref: "paper", y0: 0, y1: 1, line: { color: t.loss, width: 1, dash: "dash" as const } }],
              margin: { l: 70, r: 60, t: 10, b: 40 },
            }}
            config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }}
          />
        </div>
      )}

      <div className="card">
        <div className="text-sm font-semibold mb-2">Regime Classification</div>
        <div className="overflow-x-auto max-h-[400px] overflow-y-auto">
          <table className="data-table text-xs">
            <thead className="sticky top-0 bg-surface"><tr><th>Ticker</th><th className="text-right">VPIN</th><th className="text-right">Entropy</th><th className="text-right">Vol Ratio</th><th className="text-right">Vol (20D)</th><th>Regime</th></tr></thead>
            <tbody>
              {rows.map(r => {
                const c = classify(r);
                return (
                  <tr key={r.ticker}>
                    <td className="font-semibold">{r.ticker}</td>
                    <td className="font-data text-right">{r.VPIN?.toFixed(2) ?? "—"}</td>
                    <td className="font-data text-right">{r.Entropy?.toFixed(2) ?? "—"}</td>
                    <td className="font-data text-right">{r.Vol_Ratio?.toFixed(2) ?? "—"}</td>
                    <td className="font-data text-right">{r.Vol_20D?.toFixed(0) ?? "—"}%</td>
                    <td><span className="px-2 py-0.5 rounded text-xs font-bold" style={{ color: c.color, backgroundColor: c.color + "22" }}>{c.regime}</span></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────
// Tab 6: Factor Correlation
// ─────────────────────────────────────────────────────────────────
function FactorCorrelationTab({ ranks, t, L }: { ranks: Ranks; t: ReturnType<typeof getChartTheme>; L: ReturnType<typeof getBaseLayout> }) {
  // Spearman via rank-transformation. Our ranks are already percentile ranks, so Pearson on them ≈ Spearman on raw.
  const cols = Object.keys(ranks.ranks).filter(c => c !== "Composite");
  const n = ranks.tickers.length;
  if (cols.length < 3) return <div className="card text-center py-8 text-sm text-text-muted">Need at least 3 factors to compute correlation.</div>;

  // Correlation between two columns, ignoring null pairs
  const corr = (a: Array<number | null>, b: Array<number | null>): number => {
    const pairs: Array<[number, number]> = [];
    for (let i = 0; i < a.length; i++) { if (a[i] !== null && b[i] !== null) pairs.push([a[i] as number, b[i] as number]); }
    if (pairs.length < 3) return NaN;
    const ma = pairs.reduce((s, p) => s + p[0], 0) / pairs.length;
    const mb = pairs.reduce((s, p) => s + p[1], 0) / pairs.length;
    let num = 0, da = 0, db = 0;
    for (const [x, y] of pairs) {
      const xa = x - ma, yb = y - mb;
      num += xa * yb; da += xa * xa; db += yb * yb;
    }
    const d = Math.sqrt(da * db);
    return d > 0 ? num / d : 0;
  };

  const matrix = cols.map(c1 => cols.map(c2 => corr(ranks.ranks[c1], ranks.ranks[c2])));

  // Redundant / conflict detection
  const redundant: Array<[string, string, number]> = [];
  const conflict: Array<[string, string, number]> = [];
  for (let i = 0; i < cols.length; i++) {
    for (let j = i + 1; j < cols.length; j++) {
      const r = matrix[i][j];
      if (!Number.isFinite(r)) continue;
      if (Math.abs(r) > 0.7) redundant.push([cols[i], cols[j], r]);
      else if (r < -0.3) conflict.push([cols[i], cols[j], r]);
    }
  }
  redundant.sort((a, b) => Math.abs(b[2]) - Math.abs(a[2]));
  conflict.sort((a, b) => a[2] - b[2]);

  // Effective factors via participation ratio: (sum λ)² / sum λ²
  // Use eigenvalues via power iteration on correlation matrix
  // Simpler: sum of off-diagonal absolute correlations scaled approach
  // Actually, we can compute eigenvalues of small matrices via a simple Jacobi rotation
  const eigenvalues = jacobiEigen(matrix.map(row => row.slice()));
  const positive = eigenvalues.filter(v => v > 0).sort((a, b) => b - a);
  const total = positive.reduce((s, v) => s + v, 0);
  const effN = total > 0 ? (total * total) / positive.reduce((s, v) => s + v * v, 0) : 0;
  const cumulative: number[] = [];
  let run = 0;
  for (const v of positive) { run += v; cumulative.push((run / total) * 100); }
  const pcsFor90 = cumulative.findIndex(v => v >= 90) + 1;

  void n;  // appease eslint/typescript

  return (
    <div className="space-y-4">
      <div className="card">
        <div className="text-sm font-semibold mb-2">Factor Rank Correlation</div>
        <Plot
          data={[{
            type: "heatmap" as const,
            z: matrix, x: cols, y: cols,
            colorscale: [[0, t.loss], [0.5, t.grid], [1, t.accent]], zmid: 0, zmin: -1, zmax: 1,
            text: matrix.map(row => row.map(v => Number.isFinite(v) ? v.toFixed(2) : "")),
            texttemplate: "%{text}", textfont: { size: 9 },
            hovertemplate: "%{y} ↔ %{x}: %{z:.2f}<extra></extra>",
            colorbar: { title: { text: "ρ", font: { size: 10 } }, thickness: 12 },
          }]}
          layout={{ height: Math.max(400, cols.length * 26), ...L, margin: { l: 120, r: 40, t: 10, b: 120 }, xaxis: { tickangle: -45 } }}
          config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }}
        />
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="card">
          <div className="text-sm font-semibold mb-2">Redundant Pairs (|ρ| &gt; 0.7)</div>
          {redundant.length === 0 ? <div className="text-xs text-text-muted">None detected.</div> : redundant.slice(0, 10).map(([a, b, r], i) => (
            <div key={i} className="text-xs py-1 border-b border-border last:border-0 flex justify-between">
              <span><strong>{a}</strong> ↔ <strong>{b}</strong></span>
              <span className="font-data">ρ = {r.toFixed(2)}</span>
            </div>
          ))}
          <div className="text-[11px] text-text-muted mt-2">Consider removing one of each pair to avoid double-counting.</div>
        </div>
        <div className="card">
          <div className="text-sm font-semibold mb-2">Conflicting Pairs (ρ &lt; -0.3)</div>
          {conflict.length === 0 ? <div className="text-xs text-text-muted">None detected.</div> : conflict.slice(0, 10).map(([a, b, r], i) => (
            <div key={i} className="text-xs py-1 border-b border-border last:border-0 flex justify-between">
              <span><strong>{a}</strong> ↔ <strong>{b}</strong></span>
              <span className="font-data">ρ = {r.toFixed(2)}</span>
            </div>
          ))}
          <div className="text-[11px] text-text-muted mt-2">Opposing factors reduce composite stability.</div>
        </div>
      </div>

      <div className="card card-compact">
        <div className="flex flex-wrap gap-6">
          <Metric label="Effective Factors" value={`${effN.toFixed(1)} / ${cols.length}`} />
          <Metric label="PCs for 90% Variance" value={`${pcsFor90} / ${cols.length}`} />
        </div>
        <div className="text-[11px] text-text-muted mt-2">Participation ratio: how many truly independent signals exist among your factors.</div>
      </div>

      <div className="card">
        <div className="text-sm font-semibold mb-2">Eigenvalue Decomposition</div>
        <Plot
          data={[
            { type: "bar" as const, name: "% Variance", x: positive.map((_, i) => `PC${i + 1}`), y: positive.map(v => (v / total) * 100), marker: { color: t.accent } },
            { type: "scatter" as const, mode: "lines+markers" as const, name: "Cumulative %", x: cumulative.map((_, i) => `PC${i + 1}`), y: cumulative, line: { color: t.gain, width: 2 } },
          ]}
          layout={{
            height: 340, ...L, yaxis: { title: "%", gridcolor: t.grid }, xaxis: { gridcolor: t.grid },
            shapes: [{ type: "line", x0: 0, x1: 1, xref: "paper", y0: 90, y1: 90, line: { color: t.hv20, width: 1, dash: "dash" as const } }],
          }}
          config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }}
        />
      </div>
    </div>
  );
}

// Jacobi eigenvalue algorithm — returns eigenvalues of a symmetric matrix
function jacobiEigen(a: number[][]): number[] {
  const n = a.length;
  const m = a.map(row => row.slice());
  const maxIter = 100;
  for (let iter = 0; iter < maxIter; iter++) {
    let p = 0, q = 1, maxOff = 0;
    for (let i = 0; i < n; i++) for (let j = i + 1; j < n; j++) {
      if (Math.abs(m[i][j]) > maxOff) { maxOff = Math.abs(m[i][j]); p = i; q = j; }
    }
    if (maxOff < 1e-10) break;
    const theta = (m[q][q] - m[p][p]) / (2 * m[p][q]);
    const tt = Math.sign(theta) / (Math.abs(theta) + Math.sqrt(theta * theta + 1));
    const c = 1 / Math.sqrt(1 + tt * tt);
    const s = tt * c;
    for (let i = 0; i < n; i++) {
      if (i !== p && i !== q) {
        const mip = m[i][p], miq = m[i][q];
        m[i][p] = c * mip - s * miq;
        m[p][i] = m[i][p];
        m[i][q] = s * mip + c * miq;
        m[q][i] = m[i][q];
      }
    }
    const mpp = m[p][p], mqq = m[q][q], mpq = m[p][q];
    m[p][p] = c * c * mpp - 2 * s * c * mpq + s * s * mqq;
    m[q][q] = s * s * mpp + 2 * s * c * mpq + c * c * mqq;
    m[p][q] = 0;
    m[q][p] = 0;
  }
  return m.map((row, i) => row[i]);
}

// ─────────────────────────────────────────────────────────────────
// Tab 7: Composite
// ─────────────────────────────────────────────────────────────────
function CompositeTab({ rows, ranks, t, L }: { rows: SignalRow[]; ranks: Ranks; t: ReturnType<typeof getChartTheme>; L: ReturnType<typeof getBaseLayout> }) {
  const comp = ranks.ranks["Composite"];
  const sortedIdx = [...ranks.tickers.keys()].sort((a, b) => (comp[b] ?? -1) - (comp[a] ?? -1));
  const top10 = sortedIdx.slice(0, 10);

  const factorGroupCols: Record<string, string[]> = {
    Momentum: Object.keys(ranks.ranks).filter(c => c.startsWith("Mom_")),
    "Mean Reversion": ["RSI_Signal", "MeanRev"].filter(c => ranks.ranks[c]),
    Value: ["Value_PE", "Value_PB", "Value_EVEBITDA", "Value_FCF", "Carry"].filter(c => ranks.ranks[c]),
    Quality: ["Quality_ROE", "Quality_Margin", "Growth"].filter(c => ranks.ranks[c]),
    Earnings: ["EPS_Mom", "Insider"].filter(c => ranks.ranks[c]),
    Risk: ["LowVol"].filter(c => ranks.ranks[c]),
  };

  const groupAvg = (i: number, cols: string[]): number | null => {
    const vals = cols.map(c => ranks.ranks[c][i]).filter((v): v is number => v !== null);
    return vals.length > 0 ? vals.reduce((s, v) => s + v, 0) / vals.length : null;
  };

  // Top 10 factor profile
  const profileGroups = Object.keys(factorGroupCols).filter(g => factorGroupCols[g].length > 0);

  return (
    <div className="space-y-4">
      <div className="card">
        <div className="text-sm font-semibold mb-2">Composite Score Ranking</div>
        <Plot
          data={[{
            type: "bar" as const, orientation: "h" as const,
            y: sortedIdx.map(i => ranks.tickers[i]),
            x: sortedIdx.map(i => comp[i] ?? 0),
            marker: { color: sortedIdx.map(i => (comp[i] ?? 0) >= 60 ? t.gain : (comp[i] ?? 0) >= 40 ? t.accent : t.loss) },
            text: sortedIdx.map(i => (comp[i] ?? 0).toFixed(0)), textposition: "outside" as const, textfont: { size: 10 },
          }]}
          layout={{
            height: Math.max(400, rows.length * 22), ...L,
            xaxis: { title: "Composite Score", range: [0, 110], gridcolor: t.grid },
            yaxis: { autorange: "reversed", gridcolor: t.grid },
            margin: { l: 70, r: 50, t: 10, b: 40 },
          }}
          config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }}
        />
      </div>

      <div className="card">
        <div className="text-sm font-semibold mb-2">Factor Profile — Top 10</div>
        <Plot
          data={profileGroups.map((g, gi) => ({
            type: "bar" as const, name: g,
            x: top10.map(i => ranks.tickers[i]),
            y: top10.map(i => groupAvg(i, factorGroupCols[g]) ?? 0),
            marker: { color: [t.accent, t.gain, t.hv20, t.hv60, t.spot, t.loss][gi % 6] },
          }))}
          layout={{ height: 400, ...L, barmode: "group", yaxis: { title: "Group Rank (0-100)", gridcolor: t.grid }, xaxis: { gridcolor: t.grid } }}
          config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }}
        />
      </div>

      <div className="card">
        <div className="text-sm font-semibold mb-2">Full Ranking</div>
        <div className="overflow-x-auto max-h-[500px] overflow-y-auto">
          <table className="data-table text-xs">
            <thead className="sticky top-0 bg-surface">
              <tr><th>#</th><th>Ticker</th><th className="text-right">Composite</th>{profileGroups.map(g => <th key={g} className="text-right">{g}</th>)}</tr>
            </thead>
            <tbody>
              {sortedIdx.map((i, rank) => (
                <tr key={ranks.tickers[i]}>
                  <td className="font-data text-text-muted">{rank + 1}</td>
                  <td className="font-semibold">{ranks.tickers[i]}</td>
                  <td className="font-data text-right font-bold">{(comp[i] ?? 0).toFixed(0)}</td>
                  {profileGroups.map(g => {
                    const v = groupAvg(i, factorGroupCols[g]);
                    return <td key={g} className="font-data text-right">{v === null ? "—" : v.toFixed(0)}</td>;
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function Spinner() {
  return <div className="card text-center py-10"><div className="inline-block w-6 h-6 border-2 border-accent border-t-transparent rounded-full animate-spin" /></div>;
}

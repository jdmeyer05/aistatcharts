"use client";

import { useState, useMemo, useCallback, useRef } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTheme } from "next-themes";
import {
  fetchOptionsChain, fetchSnapshot, fetchPriceHistory,
  fetchTickerMetrics, fetchSurfaceSnapshots, saveSurfaceSnapshot,
  fetchAITradeIdeas,
  type PriceBar, type SurfaceSnapshot, type TickerMetrics,
} from "@/lib/api";
import { getChartTheme, getBaseLayout, get3dScene } from "@/lib/chart-theme";
import { Metric } from "@/components/ui/metric";
import dynamic from "next/dynamic";

const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

/* ═══════════════════════════════════════════════════════════════
   TYPE DEFINITIONS
   ═══════════════════════════════════════════════════════════════ */

interface ChainRow {
  strike_price: number;
  expiration_date: string;
  contract_type: string;
  implied_volatility: number;
  delta: number;
  gamma: number;
  theta: number;
  vega: number;
  open_interest: number;
  volume: number;
  bid: number;
  ask: number;
  last_price: number;
  [key: string]: unknown;
}

interface SkewData {
  label: string;
  exp: string;
  dte: number;
  strikes: number[];
  moneyness: number[];
  ivs: number[];
  atmIv: number;
  putSkew25d: number | null;
  callSkew25d: number | null;
  riskReversal: number | null;
  butterfly: number | null;
  skewSlope: number;
}

interface Dislocation {
  strike: number;
  exp: string;
  iv: number;
  atmIv: number;
  diff: number;
  type: "rich" | "cheap";
  oi: number;
  moneyness: number;
  dte: number;
}

interface GammaCandidate {
  exp: string;
  dte: number;
  strike: number;
  atmIv: number;
  ivHvRatio: number;
  straddleCost: number;
  gammaTheta: number;
  breakEvenMove: number;
  oi: number;
  verdict: string;
}

interface SurfaceData {
  strikes: number[];
  expLabels: string[];
  expirations: { exp: string; dte: number }[];
  zMatrix: (number | null)[][];
  deltaMatrix: (number | null)[][];
  gammaMatrix: (number | null)[][];
  vegaMatrix: (number | null)[][];
  spot: number;
  spotColIdx: number;
  spotZLine: number[];
  atmIv: number;
  hv20: number | null;
  hv60: number | null;
  vrp: number | null;
  pctiles: Record<string, number | null>;
  termStructure: { label: string; iv: number; dte: number }[];
  tsShape: string;
  skews: SkewData[];
  dislocations: Dislocation[];
  avgDislocation: number;
  gammaCandidates: GammaCandidate[];
  chainData: ChainRow[];
  snapshotData: { strike: number; dte: number; iv: number; delta?: number; gamma?: number; type: string; exp: string }[];
}

/* ═══════════════════════════════════════════════════════════════
   UTILITY FUNCTIONS
   ═══════════════════════════════════════════════════════════════ */

function fmtExp(exp: string) {
  try { return new Date(exp + "T12:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric" }); }
  catch { return exp; }
}

function calcDTE(exp: string): number {
  const d = new Date(exp + "T16:00:00");
  if (isNaN(d.getTime())) return 1;
  return Math.max(1, Math.round((d.getTime() - Date.now()) / 86400000));
}

function findNearestIdx(arr: number[], target: number): number {
  return arr.reduce((best, v, i) => Math.abs(v - target) < Math.abs(arr[best] - target) ? i : best, 0);
}

function interpolateRow(row: (number | null)[]): (number | null)[] {
  const result = [...row];
  let last: number | null = null;
  for (let i = 0; i < result.length; i++) {
    if (result[i] !== null) last = result[i];
    else if (last !== null) result[i] = last;
  }
  last = null;
  for (let i = result.length - 1; i >= 0; i--) {
    if (result[i] !== null) last = result[i];
    else if (last !== null) result[i] = last;
  }
  for (let i = 0; i < row.length; i++) {
    if (row[i] !== null) continue;
    let li = i - 1, ri = i + 1;
    while (li >= 0 && row[li] === null) li--;
    while (ri < row.length && row[ri] === null) ri++;
    if (li >= 0 && ri < row.length && row[li] !== null && row[ri] !== null) {
      const t = (i - li) / (ri - li);
      result[i] = row[li]! + t * (row[ri]! - row[li]!);
    }
  }
  return result;
}

function buildIvGrid(
  points: { moneyness: number; dte: number; iv: number }[],
  gridM: number[],
  gridD: number[],
): (number | null)[][] {
  if (points.length < 5) return gridD.map(() => gridM.map(() => null));
  const matrix: (number | null)[][] = gridD.map(() => gridM.map(() => null));
  for (const p of points) {
    const mi = findNearestIdx(gridM, p.moneyness);
    const di = findNearestIdx(gridD, p.dte);
    if (Math.abs(gridM[mi] - p.moneyness) < 0.03 && Math.abs(gridD[di] - p.dte) < 15) {
      const existing = matrix[di][mi];
      if (existing === null || Math.abs(p.moneyness - 1) < Math.abs(gridM[mi] - 1)) {
        matrix[di][mi] = p.iv;
      }
    }
  }
  const interp = matrix.map(interpolateRow);
  for (let col = 0; col < gridM.length; col++) {
    const colVals = interp.map(row => row[col]);
    const interpCol = interpolateRow(colVals);
    interpCol.forEach((v, rowIdx) => { interp[rowIdx][col] = v; });
  }
  return interp;
}

/* ═══════════════════════════════════════════════════════════════
   BUILD SURFACE — main computation
   ═══════════════════════════════════════════════════════════════ */

function buildSurface(
  rawChain: Record<string, unknown>[],
  spot: number,
  metrics: TickerMetrics | null,
  priceHistory: PriceBar[] | null,
): SurfaceData | null {
  if (!rawChain || rawChain.length < 20 || spot <= 0) return null;

  const chainData = rawChain as unknown as ChainRow[];
  const strikeLo = spot * 0.75;
  const strikeHi = spot * 1.25;

  // Stitch puts below ATM, calls at/above ATM
  interface Row { strike: number; exp: string; dte: number; iv: number; delta: number; gamma: number; theta: number; vega: number; oi: number; vol: number; bid: number; ask: number; last: number; type: string }
  const rows: Row[] = [];
  for (const c of chainData) {
    const ct = c.contract_type;
    const k = c.strike_price;
    const iv = c.implied_volatility;
    const exp = c.expiration_date;
    if (!iv || iv <= 0.01 || iv >= 3.0 || k < strikeLo || k > strikeHi) continue;
    const isPut = ct === "put" && k < spot;
    const isCall = ct === "call" && k >= spot;
    if (!isPut && !isCall) continue;
    const dte = calcDTE(exp);
    rows.push({
      strike: k, exp, dte, iv: iv * 100, delta: c.delta ?? 0, gamma: c.gamma ?? 0,
      theta: c.theta ?? 0, vega: c.vega ?? 0, oi: c.open_interest ?? 0,
      vol: c.volume ?? 0, bid: c.bid ?? 0, ask: c.ask ?? 0, last: c.last_price ?? 0,
      type: ct,
    });
  }
  if (rows.length < 15) return null;

  // Group by expiration
  const expCounts = new Map<string, { count: number; dte: number }>();
  rows.forEach(r => {
    const e = expCounts.get(r.exp);
    if (e) e.count++; else expCounts.set(r.exp, { count: 1, dte: r.dte });
  });
  const goodExps = Array.from(expCounts.entries())
    .filter(([, v]) => v.count >= 3)
    .sort(([, a], [, b]) => a.dte - b.dte)
    .slice(0, 10)
    .map(([exp, v]) => ({ exp, dte: v.dte }));
  if (goodExps.length < 2) return null;

  const allStrikes = [...new Set(rows.map(r => r.strike))].sort((a, b) => a - b);
  if (allStrikes.length < 5) return null;

  // Build IV, Delta, Gamma, Vega matrices
  const buildMatrix = (field: keyof Row) => {
    const map = new Map<string, Map<number, number>>();
    rows.forEach(r => {
      if (!map.has(r.exp)) map.set(r.exp, new Map());
      const val = r[field] as number;
      if (val !== undefined && val !== null) map.get(r.exp)!.set(r.strike, field === "iv" ? val : (val as number));
    });
    const raw: (number | null)[][] = goodExps.map(({ exp }) => {
      const ed = map.get(exp);
      return allStrikes.map(k => ed?.get(k) ?? null);
    });
    const interp = raw.map(interpolateRow);
    for (let col = 0; col < allStrikes.length; col++) {
      const colVals = interp.map(row => row[col]);
      const interpCol = interpolateRow(colVals);
      interpCol.forEach((v, rowIdx) => { interp[rowIdx][col] = v; });
    }
    return interp;
  };

  const ivMatrix = buildMatrix("iv");
  const deltaMatrix = buildMatrix("delta");
  const gammaMatrix = buildMatrix("gamma");
  const vegaMatrix = buildMatrix("vega");

  const expLabels = goodExps.map(({ exp, dte }) => {
    const d = new Date(exp + "T12:00:00");
    return `${d.toLocaleDateString("en-US", { month: "short", day: "numeric" })} (${dte}d)`;
  });

  const spotColIdx = findNearestIdx(allStrikes, spot);
  const spotZLine = ivMatrix.map(row => row[spotColIdx] ?? 0);
  const atmIv = ivMatrix[0]?.[spotColIdx] ?? 0;

  // Metrics from API
  const hv20 = metrics?.latest?.hv20 != null ? metrics.latest.hv20 * 100 : null;
  const hv60 = metrics?.latest?.hv60 != null ? metrics.latest.hv60 * 100 : null;
  const vrp = hv20 != null ? atmIv - hv20 : null;
  const pctiles = metrics?.percentiles ?? {};

  // Term structure
  const termStructure = goodExps.map(({ exp, dte }, i) => ({
    label: new Date(exp + "T12:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric" }),
    iv: ivMatrix[i]?.[spotColIdx] ?? 0,
    dte,
  }));
  const backAtmIv = ivMatrix[ivMatrix.length - 1]?.[spotColIdx] ?? 0;
  const tsShape = backAtmIv > atmIv * 1.02 ? "Contango" : backAtmIv < atmIv * 0.98 ? "Backwardation" : "Flat";

  // Find delta strikes helper
  function findDeltaStrike(expRows: Row[], targetDelta: number, optType: "call" | "put"): { strike: number; iv: number } | null {
    const filtered = expRows.filter(r => r.type === optType && r.oi > 0);
    if (filtered.length === 0) return null;
    const target = optType === "put" ? -targetDelta : targetDelta;
    let best = filtered[0];
    for (const r of filtered) {
      if (Math.abs(r.delta - target) < Math.abs(best.delta - target)) best = r;
    }
    return { strike: best.strike, iv: best.iv };
  }

  // Per-expiration skew data with full metrics
  const skews: SkewData[] = goodExps.map(({ exp, dte }, expIdx) => {
    const row = ivMatrix[expIdx];
    const expAtmIv = row[spotColIdx] ?? 0;
    const ivs = allStrikes.map((_, i) => row[i] ?? 0);
    const moneyness = allStrikes.map(k => k / spot);

    const expRows = rows.filter(r => r.exp === exp);
    const put25 = findDeltaStrike(expRows, 0.25, "put");
    const call25 = findDeltaStrike(expRows, 0.25, "call");

    // Fallback to moneyness proxy if no delta data
    const put25Idx = findNearestIdx(allStrikes, spot * 0.92);
    const call25Idx = findNearestIdx(allStrikes, spot * 1.08);
    const put25Iv = put25?.iv ?? (row[put25Idx] ?? 0);
    const call25Iv = call25?.iv ?? (row[call25Idx] ?? 0);

    const putSkew25d = expAtmIv > 0 ? Math.round(put25Iv / expAtmIv * 100) / 100 : null;
    const callSkew25d = expAtmIv > 0 ? Math.round(call25Iv / expAtmIv * 100) / 100 : null;
    const riskReversal = put25Iv && call25Iv ? Math.round((call25Iv - put25Iv) * 100) / 100 : null;
    const butterfly = put25Iv && call25Iv && expAtmIv > 0
      ? Math.round((put25Iv + call25Iv - 2 * expAtmIv) * 100) / 100 : null;

    const skewSlope = expAtmIv > 0 && put25Idx !== call25Idx
      ? Math.round((put25Iv - call25Iv) / Math.abs(call25Idx - put25Idx) * 100) : 0;

    return {
      label: fmtExp(exp), exp, dte, strikes: allStrikes, moneyness, ivs, atmIv: expAtmIv,
      putSkew25d, callSkew25d, riskReversal, butterfly, skewSlope,
    };
  });

  // Dislocations with more detail
  const dislocations: Dislocation[] = [];
  goodExps.forEach(({ exp, dte }, expIdx) => {
    const expAtmIv = ivMatrix[expIdx][spotColIdx] ?? 0;
    if (expAtmIv <= 0) return;
    allStrikes.forEach((k, kIdx) => {
      const iv = ivMatrix[expIdx][kIdx] ?? 0;
      if (iv <= 0) return;
      const diff = Math.round((iv - expAtmIv) * 100);
      if (Math.abs(diff) > 150) {
        const r = rows.find(r => r.exp === exp && Math.abs(r.strike - k) < 0.01);
        dislocations.push({
          strike: k, exp, iv, atmIv: expAtmIv, diff, type: diff > 0 ? "rich" : "cheap",
          oi: r?.oi ?? 0, moneyness: k / spot, dte,
        });
      }
    });
  });
  dislocations.sort((a, b) => Math.abs(b.diff) - Math.abs(a.diff));
  const avgDislocation = dislocations.length > 0
    ? Math.round(dislocations.reduce((s, d) => s + d.diff, 0) / dislocations.length) : 0;

  // Gamma scalping candidates
  const gammaCandidates: GammaCandidate[] = [];
  if (hv20 != null && hv20 > 0) {
    goodExps.forEach(({ exp, dte }, expIdx) => {
      const expAtmIv = ivMatrix[expIdx][spotColIdx] ?? 0;
      if (expAtmIv <= 0) return;
      const ivHvRatio = expAtmIv / hv20;
      const atmStrike = allStrikes[spotColIdx];

      // Find ATM straddle — match closest to spot within 2%
      const callRow = chainData.find(c => c.expiration_date === exp && c.contract_type === "call"
        && Math.abs(c.strike_price - atmStrike) < spot * 0.02);
      const putRow = chainData.find(c => c.expiration_date === exp && c.contract_type === "put"
        && Math.abs(c.strike_price - atmStrike) < spot * 0.02);

      if (callRow && putRow) {
        const callMid = ((callRow.bid || 0) + (callRow.ask || 0)) / 2 || callRow.last_price || 0;
        const putMid = ((putRow.bid || 0) + (putRow.ask || 0)) / 2 || putRow.last_price || 0;
        const straddleCost = callMid + putMid;
        const gamma = (callRow.gamma || 0) + (putRow.gamma || 0);
        const theta = Math.abs((callRow.theta || 0) + (putRow.theta || 0));
        const breakEvenMove = gamma > 0 && theta > 0 ? Math.sqrt(2 * theta / gamma) / spot * 100 : 0;
        const gammaTheta = theta > 0 ? (gamma * 100) / (theta * 100) : 0;

        let verdict = "Fair";
        if (ivHvRatio < 0.9) verdict = "Cheap Gamma";
        else if (ivHvRatio < 1.05) verdict = "Fair";
        else if (ivHvRatio < 1.2) verdict = "Expensive";
        else verdict = "Very Expensive";

        gammaCandidates.push({
          exp, dte, strike: atmStrike, atmIv: expAtmIv, ivHvRatio,
          straddleCost, gammaTheta, breakEvenMove,
          oi: (callRow.open_interest || 0) + (putRow.open_interest || 0),
          verdict,
        });
      }
    });
    gammaCandidates.sort((a, b) => a.ivHvRatio - b.ivHvRatio);
  }

  // Build snapshot data for saving
  const snapshotData = rows.map(r => ({
    strike: r.strike, dte: r.dte, iv: r.iv, delta: r.delta, gamma: r.gamma, type: r.type, exp: r.exp,
  }));

  return {
    strikes: allStrikes, expLabels, expirations: goodExps,
    zMatrix: ivMatrix, deltaMatrix, gammaMatrix, vegaMatrix,
    spot, spotColIdx, spotZLine, atmIv, hv20, hv60, vrp, pctiles,
    termStructure, tsShape, skews, dislocations, avgDislocation,
    gammaCandidates, chainData, snapshotData,
  };
}

/* ═══════════════════════════════════════════════════════════════
   SURFACE CONTEXT BUILDER (for AI tab)
   ═══════════════════════════════════════════════════════════════ */

function buildSurfaceContext(s: SurfaceData, ticker: string): string {
  const lines: string[] = [];
  lines.push(`TICKER: ${ticker} | Spot: $${s.spot.toFixed(2)}`);
  lines.push(`ATM IV (front): ${s.atmIv.toFixed(1)}%`);
  if (s.hv20 != null) lines.push(`HV20: ${s.hv20.toFixed(1)}% | HV60: ${s.hv60?.toFixed(1) ?? "N/A"}%`);
  if (s.vrp != null) lines.push(`VRP (IV-HV20): ${s.vrp > 0 ? "+" : ""}${s.vrp.toFixed(1)}% ${s.vrp > 3 ? "(RICH — premium sellers edge)" : s.vrp < -2 ? "(CHEAP — gamma buyers edge)" : "(fair)"}`);

  // Percentiles
  const p = s.pctiles;
  if (p && Object.keys(p).length > 0) {
    lines.push(`\n252-DAY PERCENTILES:`);
    if (p.atm_iv != null) lines.push(`  ATM IV: ${(p.atm_iv * 100).toFixed(0)}th percentile`);
    if (p.put_skew != null) lines.push(`  Put Skew: ${(p.put_skew * 100).toFixed(0)}th percentile`);
    if (p.vrp != null) lines.push(`  VRP: ${(p.vrp * 100).toFixed(0)}th percentile`);
  }

  // Term structure
  lines.push(`\nTERM STRUCTURE: ${s.tsShape}`);
  s.termStructure.slice(0, 8).forEach(t => {
    lines.push(`  ${t.label} (${t.dte}d): ${t.iv.toFixed(1)}%`);
  });

  // Skew metrics
  lines.push(`\nSKEW PROFILE:`);
  s.skews.slice(0, 6).forEach(sk => {
    lines.push(`  ${sk.label} (${sk.dte}d): ATM=${sk.atmIv.toFixed(1)}%, 25Δ Put Skew=${sk.putSkew25d?.toFixed(2) ?? "N/A"}x, RR=${sk.riskReversal?.toFixed(1) ?? "N/A"}%, Butterfly=${sk.butterfly?.toFixed(1) ?? "N/A"}%`);
  });

  // Gamma scalp
  if (s.gammaCandidates.length > 0) {
    const best = s.gammaCandidates[0];
    lines.push(`\nGAMMA SCALP: Best candidate ${fmtExp(best.exp)} (${best.dte}d), IV/HV=${best.ivHvRatio.toFixed(2)}, Break-Even Move=${best.breakEvenMove.toFixed(2)}%, Γ/Θ=${best.gammaTheta.toFixed(2)}`);
  }

  // Dislocations
  if (s.dislocations.length > 0) {
    lines.push(`\nDISLOCATIONS (${s.dislocations.length} total, avg ${s.avgDislocation > 0 ? "+" : ""}${s.avgDislocation}bp):`);
    const rich = s.dislocations.filter(d => d.type === "rich" && d.oi > 0).slice(0, 3);
    const cheap = s.dislocations.filter(d => d.type === "cheap" && d.oi > 0).slice(0, 3);
    rich.forEach(d => lines.push(`  RICH: $${d.strike} ${fmtExp(d.exp)} IV=${d.iv.toFixed(1)}% (+${d.diff}bp vs ATM) OI=${d.oi}`));
    cheap.forEach(d => lines.push(`  CHEAP: $${d.strike} ${fmtExp(d.exp)} IV=${d.iv.toFixed(1)}% (${d.diff}bp vs ATM) OI=${d.oi}`));
  }

  // Liquidity map
  const topOI = [...s.chainData].sort((a, b) => (b.open_interest || 0) - (a.open_interest || 0)).slice(0, 15);
  if (topOI.length > 0) {
    lines.push(`\nLIQUIDITY MAP (Top 15 by OI):`);
    topOI.forEach(c => {
      lines.push(`  $${c.strike_price} ${c.contract_type} ${fmtExp(c.expiration_date)} OI=${c.open_interest} Vol=${c.volume}`);
    });
  }

  return lines.join("\n");
}

/* ═══════════════════════════════════════════════════════════════
   SHARED PLOT CONFIG — theme-aware, computed per-render
   ═══════════════════════════════════════════════════════════════ */

/* ═══════════════════════════════════════════════════════════════
   MAIN COMPONENT
   ═══════════════════════════════════════════════════════════════ */

const TABS = [
  "3D Surface", "IV Skew", "Term Structure", "Dislocations",
  "Skew Metrics", "Gamma Scalping", "Animation", "Comparison", "AI Trade Ideas",
];

export function VolSurfaceContent() {
  const { resolvedTheme } = useTheme();
  const isDark = resolvedTheme === "dark";
  const t = getChartTheme(isDark);
  const L = getBaseLayout(t);

  const [ticker, setTicker] = useState("SPY");
  const [surface, setSurface] = useState<SurfaceData | null>(null);
  const [viewMode, setViewMode] = useState<"3d" | "heatmap">("3d");
  const [surfaceMetric, setSurfaceMetric] = useState<"iv" | "delta" | "gamma" | "vega">("iv");
  const [activeTab, setActiveTab] = useState(0);
  const [priceHistory, setPriceHistory] = useState<PriceBar[] | null>(null);
  const [metricsData, setMetricsData] = useState<TickerMetrics | null>(null);

  // Animation state
  const [snapshots, setSnapshots] = useState<SurfaceSnapshot[]>([]);
  const [animDay, setAnimDay] = useState(0);
  const [animViewMode, setAnimViewMode] = useState<"3d" | "heatmap">("heatmap");
  const [animCompare, setAnimCompare] = useState<"prev" | "first">("prev");
  const [animDays, setAnimDays] = useState(10);

  // Comparison state
  const [compMode, setCompMode] = useState<"historical" | "callput" | "cross">("historical");
  const [compHistDate, setCompHistDate] = useState(0);
  const [crossTicker, setCrossTicker] = useState("QQQ");
  const [crossSurface, setCrossSurface] = useState<SurfaceData | null>(null);

  // AI Trade Ideas state
  const [aiStyle, setAiStyle] = useState("full_scan");
  const [accountSize, setAccountSize] = useState("");
  const [aiContent, setAiContent] = useState("");
  const [aiCached, setAiCached] = useState(false);
  const [refinePrompt, setRefinePrompt] = useState("");

  // AI Surface Narrator + Evolution state
  const [narratorContent, setNarratorContent] = useState("");
  const [evolutionContent, setEvolutionContent] = useState("");

  // Historical date scrubber (Tab 0)
  const [histDateIdx, setHistDateIdx] = useState(-1); // -1 = today (live)

  // Dislocations state
  const [dislocBaseline, setDislocBaseline] = useState<"hv20" | "hv60" | "term">("hv20");
  const [dislocRange, setDislocRange] = useState([0.8, 1.2]);

  const tickerRef = useRef(ticker);

  // Main data load — pass ticker through result to avoid stale closure
  const load = useMutation({
    mutationFn: async (tk: string) => {
      tickerRef.current = tk;
      const [chain, snap, hist, met] = await Promise.all([
        fetchOptionsChain(tk),
        fetchSnapshot([tk]),
        fetchPriceHistory(tk, 252),
        fetchTickerMetrics(tk).catch(() => null),
      ]);
      return { chain, spot: snap[tk]?.price ?? 0, history: hist.data, metrics: met, ticker: tk };
    },
    onSuccess: (data) => {
      setPriceHistory(data.history);
      setMetricsData(data.metrics);
      const s = buildSurface(data.chain.data, data.spot, data.metrics, data.history);
      setSurface(s);
      setAiContent("");
      setSnapshots([]);
      setCrossSurface(null);
      // Save snapshot in background
      if (s) {
        saveSurfaceSnapshot(data.ticker, data.spot, s.snapshotData).catch(() => {});
      }
    },
  });

  // Load snapshots for animation
  const loadSnapshots = useMutation({
    mutationFn: async () => {
      const res = await fetchSurfaceSnapshots(tickerRef.current, animDays);
      return res.snapshots;
    },
    onSuccess: (snaps) => {
      setSnapshots(snaps);
      setAnimDay(snaps.length - 1);
    },
  });

  // Load cross-ticker for comparison
  const loadCross = useMutation({
    mutationFn: async (tk: string) => {
      const [chain, snap, met] = await Promise.all([
        fetchOptionsChain(tk),
        fetchSnapshot([tk]),
        fetchTickerMetrics(tk).catch(() => null),
      ]);
      return buildSurface(chain.data, snap[tk]?.price ?? 0, met, null);
    },
    onSuccess: (s) => setCrossSurface(s),
  });

  // AI Surface Narrator
  const loadNarrator = useMutation({
    mutationFn: async () => {
      if (!surface) throw new Error("No surface");
      const ctx = buildSurfaceContext(surface, tickerRef.current);
      const prompt = `${ctx}\n\nAs a senior vol surface analyst, give me:\n1. The single most important takeaway from this surface right now (2 sentences)\n2. One specific skew or dislocation trade idea with strikes and expiration\n3. What could change this surface overnight (1 sentence)\n\nBe specific with numbers. No disclaimers.`;
      return fetchAITradeIdeas({ ticker: tickerRef.current, context: prompt, style: "full_scan" });
    },
    onSuccess: (res) => setNarratorContent(res.content),
  });

  // AI Evolution Analysis (animation tab)
  const loadEvolution = useMutation({
    mutationFn: async () => {
      if (!surface || animGrids.length < 2) throw new Error("Need surface + snapshots");
      const cur = animGrids[animDay];
      const base = animGrids[0];
      if (!cur || !base) throw new Error("Missing frames");
      const curFlat = cur.grid.flat().filter((v): v is number => v !== null);
      const baseFlat = base.grid.flat().filter((v): v is number => v !== null);
      const curAvg = curFlat.length > 0 ? curFlat.reduce((s, v) => s + v, 0) / curFlat.length : 0;
      const baseAvg = baseFlat.length > 0 ? baseFlat.reduce((s, v) => s + v, 0) / baseFlat.length : 0;
      const ctx = `SURFACE EVOLUTION for ${tickerRef.current}\nPeriod: ${base.date} → ${cur.date}\nSpot: $${base.spot.toFixed(2)} → $${cur.spot.toFixed(2)} (${((cur.spot - base.spot) / base.spot * 100).toFixed(1)}%)\nAvg IV: ${baseAvg.toFixed(1)}% → ${curAvg.toFixed(1)}% (${(curAvg - baseAvg).toFixed(1)}pp)\n\nAs a vol surface analyst: Was this IV move driven by (a) an event (earnings, macro, FOMC), (b) dealer repositioning, (c) mean reversion, or (d) vanna/charm flows? Is skew rich or cheap relative to the spot move? What should I watch for next? Be specific with numbers.`;
      return fetchAITradeIdeas({ ticker: tickerRef.current, context: ctx, style: "full_scan" });
    },
    onSuccess: (res) => setEvolutionContent(res.content),
  });

  // AI trade ideas
  const loadAI = useMutation({
    mutationFn: async (params: { refine?: string }) => {
      if (!surface) throw new Error("No surface loaded");
      const ctx = buildSurfaceContext(surface, tickerRef.current);
      return fetchAITradeIdeas({
        ticker: tickerRef.current,
        context: ctx,
        style: aiStyle,
        account_size: accountSize ? parseFloat(accountSize) : undefined,
        refine_prompt: params.refine,
        previous_response: params.refine ? aiContent : undefined,
      });
    },
    onSuccess: (res) => {
      setAiContent(res.content);
      setAiCached(res.cached);
    },
  });

  // Daily returns for gamma scalping histogram
  const dailyMoves = useMemo(() => {
    if (!priceHistory || priceHistory.length < 20) return [];
    return priceHistory.slice(1).map((bar, i) => {
      const prev = priceHistory![i];
      return prev.Close > 0 ? Math.abs((bar.Close - prev.Close) / prev.Close * 100) : 0;
    }).filter(m => m > 0);
  }, [priceHistory]);

  const avgDailyMove = useMemo(() => {
    if (dailyMoves.length === 0) return 0;
    return dailyMoves.reduce((s, m) => s + m, 0) / dailyMoves.length;
  }, [dailyMoves]);

  const medianDailyMove = useMemo(() => {
    if (dailyMoves.length === 0) return 0;
    const sorted = [...dailyMoves].sort((a, b) => a - b);
    return sorted[Math.floor(sorted.length / 2)];
  }, [dailyMoves]);

  // Build animation grids from snapshots
  const animGrids = useMemo(() => {
    if (snapshots.length === 0) return [];
    const gridM = Array.from({ length: 40 }, (_, i) => 0.82 + i * (0.36 / 39));
    const gridD = Array.from({ length: 20 }, (_, i) => 5 + i * (360 / 19));
    return snapshots.map(snap => {
      const points = snap.data.map(d => ({
        moneyness: snap.spot > 0 ? d.strike / snap.spot : 0,
        dte: d.dte,
        iv: d.iv,
      })).filter(p => p.moneyness > 0.8 && p.moneyness < 1.2 && p.iv > 0);
      return { date: snap.date, spot: snap.spot, grid: buildIvGrid(points, gridM, gridD), gridM, gridD };
    });
  }, [snapshots]);

  // Get the active surface metric matrix
  const getMetricMatrix = useCallback(() => {
    if (!surface) return [];
    switch (surfaceMetric) {
      case "delta": return surface.deltaMatrix;
      case "gamma": return surface.gammaMatrix;
      case "vega": return surface.vegaMatrix;
      default: return surface.zMatrix;
    }
  }, [surface, surfaceMetric]);

  const metricLabel = surfaceMetric === "iv" ? "IV %" : surfaceMetric === "delta" ? "Delta" : surfaceMetric === "gamma" ? "Gamma" : "Vega";

  const getSpotZLine = useCallback(() => {
    if (!surface) return [];
    const matrix = getMetricMatrix();
    return matrix.map(row => row[surface.spotColIdx] ?? 0);
  }, [surface, getMetricMatrix]);

  return (
    <div className="space-y-5">

      {/* Controls */}
      <div className="card card-compact">
        <div className="flex items-center gap-3 flex-wrap">
          <input
            type="text"
            value={ticker}
            onChange={(e) => setTicker(e.target.value.toUpperCase())}
            onKeyDown={(e) => e.key === "Enter" && load.mutate(ticker.toUpperCase())}
            placeholder="SPY"
            className="w-32 px-3 py-2 border border-border rounded-lg text-sm font-data bg-surface"
          />
          <button
            onClick={() => load.mutate(ticker.toUpperCase())}
            disabled={load.isPending}
            className="px-6 py-2 bg-accent text-white font-semibold rounded-lg hover:bg-accent-hover disabled:opacity-50 transition-colors text-sm"
          >
            {load.isPending ? "Loading..." : "Load Surface"}
          </button>
          {surface && activeTab === 0 && (
            <>
              <div className="flex gap-1 ml-auto">
                {(["iv", "delta", "gamma", "vega"] as const).map(m => (
                  <button key={m} onClick={() => setSurfaceMetric(m)}
                    className={`px-3 py-1.5 text-xs font-semibold rounded-md transition-colors ${
                      surfaceMetric === m ? "bg-accent text-white" : "text-text-muted hover:text-text hover:bg-surface-alt"}`}>
                    {m === "iv" ? "IV" : m.charAt(0).toUpperCase() + m.slice(1)}
                  </button>
                ))}
              </div>
              <div className="flex gap-1">
                {(["3d", "heatmap"] as const).map(mode => (
                  <button key={mode} onClick={() => setViewMode(mode)}
                    className={`px-3 py-1.5 text-xs font-semibold rounded-md transition-colors ${
                      viewMode === mode ? "bg-accent text-white" : "text-text-muted hover:text-text hover:bg-surface-alt"}`}>
                    {mode === "3d" ? "3D" : "Heatmap"}
                  </button>
                ))}
              </div>
            </>
          )}
        </div>
      </div>

      {load.isPending && (
        <div className="card text-center py-12">
          <div className="inline-block w-6 h-6 border-2 border-accent border-t-transparent rounded-full animate-spin" />
          <p className="text-sm text-text-muted mt-3">Fetching options chain + metrics...</p>
        </div>
      )}

      {surface && (
        <>
          {/* Vol Regime Bar */}
          <div className="card card-compact">
            <div className="flex flex-wrap gap-6">
              <Metric label="Spot" value={`$${surface.spot.toFixed(2)}`} />
              <Metric label="ATM IV" value={`${surface.atmIv.toFixed(1)}%`} />
              {surface.hv20 != null && <Metric label="HV20" value={`${surface.hv20.toFixed(1)}%`} />}
              {surface.vrp != null && (
                <Metric label="VRP"
                  value={`${surface.vrp > 0 ? "+" : ""}${surface.vrp.toFixed(1)}%`}
                />
              )}
              <Metric label="Term Structure" value={surface.tsShape} />
              <Metric label="Put Skew (25Δ)" value={surface.skews[0]?.putSkew25d ? `${surface.skews[0].putSkew25d.toFixed(2)}x` : "N/A"} />
              <Metric label="Dislocations" value={`${surface.dislocations.length}`} />
              <Metric label="Expirations" value={String(surface.expLabels.length)} />
              {surface.pctiles.atm_iv != null && (
                <Metric label="IV %ile" value={`${(surface.pctiles.atm_iv * 100).toFixed(0)}th`} />
              )}
            </div>
          </div>

          {/* Tabs */}
          <div className="flex gap-1 border-b border-border pb-1 overflow-x-auto">
            {TABS.map((tab, i) => (
              <button key={tab} onClick={() => setActiveTab(i)}
                className={`px-3 py-1.5 text-xs font-semibold rounded-t-md transition-colors whitespace-nowrap ${
                  activeTab === i ? "bg-accent text-white" : "text-text-muted hover:text-text hover:bg-surface-alt"}`}>
                {tab}
              </button>
            ))}
          </div>

          {/* ═══ TAB 0: 3D Surface ═══ */}
          {activeTab === 0 && (
            <div className="card space-y-4">
              {/* How to read expander */}
              <details className="text-xs text-text-muted">
                <summary className="cursor-pointer text-accent hover:underline font-semibold">How to read this surface</summary>
                <div className="mt-2 space-y-1 pl-2">
                  <p><strong>X-axis:</strong> Strike prices. Left = OTM puts, Right = OTM calls, Center = ATM (near spot).</p>
                  <p><strong>Y-axis:</strong> Expirations. Front = near-term, Back = far-term.</p>
                  <p><strong>Z-axis (color):</strong> Implied volatility. Higher = market expects bigger moves.</p>
                  <p><strong>Skew:</strong> If left side (puts) is elevated = fear premium. Steeper skew = more crash hedging.</p>
                  <p><strong>Term structure:</strong> If front months higher = backwardation (event risk). Back months higher = contango (normal).</p>
                  <p><strong>Yellow spine:</strong> ATM IV along the curve — the backbone of the surface.</p>
                </div>
              </details>

              {/* Historical date scrubber */}
              {snapshots.length > 0 && (
                <div className="flex items-center gap-3 text-xs">
                  <span className="font-semibold text-text-muted">View:</span>
                  <select value={histDateIdx} onChange={e => setHistDateIdx(Number(e.target.value))}
                    className="px-2 py-1 border border-border rounded bg-surface text-xs">
                    <option value={-1}>Today (Live)</option>
                    {snapshots.map((s, i) => (
                      <option key={i} value={i}>{s.date} (Spot: ${s.spot.toFixed(2)})</option>
                    ))}
                  </select>
                  {histDateIdx >= 0 && snapshots[histDateIdx] && (
                    <span className="text-text-muted">Viewing historical: {snapshots[histDateIdx].date} · Spot ${snapshots[histDateIdx].spot.toFixed(2)}</span>
                  )}
                  {snapshots.length === 0 && (
                    <button onClick={() => loadSnapshots.mutate()} disabled={loadSnapshots.isPending}
                      className="px-3 py-1 text-xs bg-accent text-white rounded hover:bg-accent-hover disabled:opacity-50">
                      Load History
                    </button>
                  )}
                </div>
              )}
              {snapshots.length === 0 && (
                <div className="flex items-center gap-2 text-xs text-text-muted">
                  <span>Load snapshots to scrub historical surfaces:</span>
                  <button onClick={() => loadSnapshots.mutate()} disabled={loadSnapshots.isPending}
                    className="px-3 py-1 text-xs bg-accent/80 text-white rounded hover:bg-accent-hover disabled:opacity-50">
                    {loadSnapshots.isPending ? "Loading..." : "Load History"}
                  </button>
                </div>
              )}

              {/* Surface chart — show historical if selected, else live */}
              {histDateIdx >= 0 && snapshots[histDateIdx] && animGrids[histDateIdx] ? (() => {
                const grid = animGrids[histDateIdx];
                return (
                  <Plot
                    data={[{
                      type: "heatmap" as const,
                      x: grid.gridM.map(m => m.toFixed(3)),
                      y: grid.gridD.map(d => `${d.toFixed(0)}d`),
                      z: grid.grid,
                      colorscale: "Viridis",
                      colorbar: { title: { text: "IV %", font: { size: 9 } }, thickness: 12 },
                      zsmooth: "best",
                    }]}
                    layout={{
                      height: 400, ...L, margin: { l: 60, r: 20, t: 20, b: 50 },
                      xaxis: { title: "Moneyness", gridcolor: t.grid },
                      yaxis: { title: "DTE", gridcolor: t.grid },
                    }}
                    config={{ displayModeBar: false, responsive: true }}
                    style={{ width: "100%" }}
                  />
                );
              })() : viewMode === "3d" ? (
                <Plot
                  data={[
                    {
                      type: "surface" as const,
                      x: surface.strikes,
                      y: Array.from({ length: surface.expLabels.length }, (_, i) => i),
                      z: getMetricMatrix(),
                      colorscale: "Viridis",
                      colorbar: { title: { text: metricLabel, font: { size: 10 } }, tickformat: surfaceMetric === "iv" ? ".0f" : ".3f", len: 0.6, thickness: 15 },
                      hovertemplate: `Strike: $%{x:,.0f}<br>${metricLabel}: %{z:.${surfaceMetric === "iv" ? "1" : "4"}f}<extra></extra>`,
                      lighting: { ambient: 0.6, diffuse: 0.5, specular: 0.3, roughness: 0.5 },
                      opacity: 0.92,
                    },
                    {
                      type: "scatter3d" as const,
                      x: Array(surface.expLabels.length).fill(surface.spot),
                      y: Array.from({ length: surface.expLabels.length }, (_, i) => i),
                      z: getSpotZLine(),
                      mode: "lines+markers" as const,
                      line: { color: t.spot, width: 5 },
                      marker: { size: 3, color: t.spot },
                      name: "Spot",
                    },
                  ]}
                  layout={{
                    height: 600,
                    margin: { l: 0, r: 0, t: 10, b: 10 },
                    paper_bgcolor: t.surface3dBg,
                    font: { family: "Inter, sans-serif", color: t.text, size: 10 },
                    scene: {
                      ...get3dScene(t),
                      yaxis: { ...get3dScene(t).yaxis, title: "Expiration", tickvals: Array.from({ length: surface.expLabels.length }, (_, i) => i), ticktext: surface.expLabels },
                      zaxis: { ...get3dScene(t).zaxis, title: metricLabel },
                    },
                    legend: { x: 0, y: 1, bgcolor: isDark ? "rgba(22,27,34,0.8)" : "rgba(255,255,255,0.7)" },
                  }}
                  config={{ displayModeBar: true, responsive: true }}
                  style={{ width: "100%", height: "600px" }}
                />
              ) : (
                <Plot
                  data={[{
                    type: "heatmap" as const,
                    x: surface.strikes,
                    y: surface.expLabels,
                    z: getMetricMatrix(),
                    colorscale: "Viridis",
                    colorbar: { title: { text: metricLabel, font: { size: 10 } }, thickness: 15 },
                    hovertemplate: `Strike: $%{x:,.0f}<br>%{y}<br>${metricLabel}: %{z:.${surfaceMetric === "iv" ? "1" : "4"}f}<extra></extra>`,
                    zsmooth: "best",
                  }]}
                  layout={{
                    height: 450,
                    ...L,
                    margin: { l: 100, r: 20, t: 20, b: 50 },
                    xaxis: { title: "Strike ($)", gridcolor: t.grid },
                    yaxis: { gridcolor: t.grid },
                    shapes: [{ type: "line", x0: surface.spot, x1: surface.spot, y0: 0, y1: 1, yref: "paper", line: { color: t.spot, width: 2, dash: "dash" } }],
                  }}
                  config={{ displayModeBar: false, responsive: true }}
                  style={{ width: "100%", height: "450px" }}
                />
              )}

              {/* Higher-Order Greeks Snapshot */}
              {(() => {
                // Compute vanna/charm/speed from ATM front-month option
                const front = surface.expirations[0];
                if (!front) return null;
                const atmRow = surface.chainData.find(c =>
                  c.expiration_date === front.exp && c.contract_type === "call" &&
                  Math.abs(c.strike_price - surface.spot) < surface.spot * 0.02 &&
                  c.implied_volatility > 0 && c.gamma > 0
                );
                if (!atmRow) return null;
                const iv = atmRow.implied_volatility;
                const T = front.dte / 365;
                const S = surface.spot;
                const K = atmRow.strike_price;
                if (T <= 0 || iv <= 0) return null;
                const d1 = (Math.log(S / K) + (0.045 + iv * iv / 2) * T) / (iv * Math.sqrt(T));
                const nd1 = Math.exp(-d1 * d1 / 2) / Math.sqrt(2 * Math.PI);
                const vanna = -nd1 * (1 - d1 / (iv * Math.sqrt(T))) / S;
                const charm = -nd1 * (2 * 0.045 * T - d1 * iv * Math.sqrt(T)) / (2 * T * iv * Math.sqrt(T));
                const speed = -(atmRow.gamma / S) * (d1 / (iv * Math.sqrt(T)) + 1);

                return (
                  <div className="border border-border rounded-lg p-3">
                    <div className="flex items-center justify-between mb-2">
                      <div className="text-xs font-semibold">Higher-Order Greeks (Front ATM)</div>
                      <a href="/higher-greeks" className="text-[0.6rem] text-accent hover:underline">Full analysis →</a>
                    </div>
                    <div className="grid grid-cols-3 gap-3">
                      <div>
                        <div className="text-[0.6rem] text-text-muted">Vanna (δΔ/δσ)</div>
                        <div className="text-sm font-data font-semibold">{vanna.toFixed(5)}</div>
                        <div className="text-[0.55rem] text-text-muted">{Math.abs(vanna) > 0.01 ? "Significant dealer hedging flow" : "Normal"}</div>
                      </div>
                      <div>
                        <div className="text-[0.6rem] text-text-muted">Charm (δΔ/δt)</div>
                        <div className="text-sm font-data font-semibold">{charm.toFixed(5)}/day</div>
                        <div className="text-[0.55rem] text-text-muted">{Math.abs(charm) > 0.005 ? "Watch overnight delta drift" : "Minimal drift"}</div>
                      </div>
                      <div>
                        <div className="text-[0.6rem] text-text-muted">Speed (δΓ/δS)</div>
                        <div className="text-sm font-data font-semibold">{speed.toFixed(7)}</div>
                        <div className="text-[0.55rem] text-text-muted">{Math.abs(speed) > 0.0001 ? "Gamma accelerates on moves" : "Stable gamma"}</div>
                      </div>
                    </div>
                  </div>
                );
              })()}

              {/* Surface Reading — static prose interpretation */}
              <div className="border border-border rounded-lg p-3 space-y-2 text-xs text-text-muted">
                <div className="font-semibold text-text text-sm mb-1">Surface Reading</div>
                {/* VRP interpretation */}
                {surface.vrp != null && (
                  <p>
                    <strong className={surface.vrp > 3 ? "text-loss" : surface.vrp < -2 ? "text-gain" : ""}>VRP ({surface.vrp > 0 ? "+" : ""}{surface.vrp.toFixed(1)}%):</strong>{" "}
                    {surface.vrp > 5 ? "Implied vol is significantly rich vs realized — strong edge for premium sellers. Consider iron condors, strangles, or credit spreads."
                      : surface.vrp > 2 ? "Implied vol is moderately rich — favorable for selling premium. Calendar spreads and condors have positive expected value."
                      : surface.vrp > -2 ? "Implied vol is near fair value vs realized. No clear vol edge — focus on directional or skew-based trades."
                      : "Implied vol is cheap vs realized — gamma buyers have the edge. Consider straddles, strangles, or protective puts."}
                  </p>
                )}
                {/* Skew interpretation */}
                {surface.skews.length > 0 && surface.skews[0].putSkew25d != null && (
                  <p>
                    <strong className={surface.skews[0].putSkew25d > 1.15 ? "text-loss" : surface.skews[0].putSkew25d < 1.02 ? "text-gain" : ""}>
                      Put Skew ({surface.skews[0].putSkew25d.toFixed(2)}x):
                    </strong>{" "}
                    {surface.skews[0].putSkew25d > 1.18 ? "Extremely steep skew — heavy crash protection demand. Risk reversals are expensive. Consider selling put spreads or ratio put spreads to capture the skew premium."
                      : surface.skews[0].putSkew25d > 1.10 ? "Moderately steep skew — puts carry meaningful premium over calls. Standard for equity indices but worth monitoring for further steepening."
                      : surface.skews[0].putSkew25d > 1.02 ? "Normal skew — standard downside premium. No actionable skew trades unless combined with directional view."
                      : "Flat or inverted skew — unusually complacent market. Tail hedges may be cheap. Consider protective puts or put spreads."}
                  </p>
                )}
                {/* Term structure interpretation */}
                <p>
                  <strong className={surface.tsShape === "Backwardation" ? "text-loss" : surface.tsShape === "Contango" ? "text-gain" : ""}>
                    Term Structure ({surface.tsShape}):
                  </strong>{" "}
                  {surface.tsShape === "Backwardation" ? "Front-month IV exceeds back months — event risk or near-term fear. Favors iron condors (sell front) and caution on calendar spreads (front premium erodes faster)."
                    : surface.tsShape === "Contango" ? "Normal upward-sloping curve — back months carry time premium. Favorable for calendar spreads (buy back, sell front) and diagonal strategies."
                    : "Flat term structure — no clear term premium. Relative value opportunities may exist at specific expirations."}
                </p>
                {/* Percentile context */}
                {surface.pctiles && surface.pctiles.atm_iv != null && (
                  <p>
                    <strong>Percentile Context:</strong>{" "}
                    ATM IV at the {((surface.pctiles.atm_iv as number) * 100).toFixed(0)}th percentile vs 252-day history
                    {surface.pctiles.vrp != null && `, VRP at the ${((surface.pctiles.vrp as number) * 100).toFixed(0)}th percentile`}
                    {surface.pctiles.put_skew != null && `, Put Skew at the ${((surface.pctiles.put_skew as number) * 100).toFixed(0)}th percentile`}.
                    {(surface.pctiles.atm_iv as number) > 0.8 ? " Current IV is elevated — historically rich environment." : (surface.pctiles.atm_iv as number) < 0.2 ? " Current IV is historically low — cheap protection." : ""}
                  </p>
                )}
              </div>

              {/* AI Surface Narrator */}
              <div className="border border-border rounded-lg p-3">
                <div className="flex items-center justify-between mb-2">
                  <div className="text-xs font-semibold">AI Surface Narrator</div>
                  <button onClick={() => loadNarrator.mutate()} disabled={loadNarrator.isPending}
                    className="px-3 py-1 text-xs bg-accent text-white rounded hover:bg-accent-hover disabled:opacity-50">
                    {loadNarrator.isPending ? "Analyzing..." : narratorContent ? "Re-analyze" : "Analyze Surface"}
                  </button>
                </div>
                {loadNarrator.isPending && (
                  <div className="flex items-center gap-2 py-2">
                    <div className="w-4 h-4 border-2 border-accent border-t-transparent rounded-full animate-spin" />
                    <span className="text-xs text-text-muted">Gemini reading the surface...</span>
                  </div>
                )}
                {narratorContent && (
                  <div className="text-xs text-text-muted whitespace-pre-line">{narratorContent}</div>
                )}
              </div>
            </div>
          )}

          {/* ═══ TAB 1: IV Skew (overlaid) ═══ */}
          {activeTab === 1 && (
            <div className="card">
              <Plot
                data={surface.skews.map((skew, i) => ({
                  x: skew.moneyness,
                  y: skew.ivs,
                  type: "scatter" as const,
                  mode: "lines" as const,
                  name: `${skew.label} (${skew.dte}d)`,
                  line: { width: 2 },
                  hovertemplate: `${skew.label}<br>Moneyness: %{x:.2f}<br>IV: %{y:.1f}%<extra></extra>`,
                }))}
                layout={{
                  height: 450,
                  ...L,
                  xaxis: { title: "Moneyness (Strike / Spot)", gridcolor: t.grid, range: [0.85, 1.15] },
                  yaxis: { title: "IV (%)", gridcolor: t.grid },
                  legend: { x: 0.01, y: 0.99, bgcolor: isDark ? "rgba(22,27,34,0.85)" : "rgba(255,255,255,0.85)", font: { size: 9 } },
                  shapes: [{ type: "line", x0: 1, x1: 1, y0: 0, y1: 1, yref: "paper", line: { color: t.spot, width: 2, dash: "dash" } }],
                  annotations: [{ x: 1.001, y: 1, yref: "paper", text: "ATM", showarrow: false, font: { size: 9, color: t.spot } }],
                }}
                config={{ displayModeBar: false, responsive: true }}
                style={{ width: "100%", height: "450px" }}
              />
              <div className="mt-3 text-xs text-text-muted">
                <strong>Steep left skew</strong> = crash protection expensive (puts richly priced).
                <strong className="ml-2">Lines crossing</strong> = relative mispricings between expirations (calendar arb opportunity).
                <strong className="ml-2">Flat skew</strong> = complacent market, tail hedges may be cheap.
              </div>
            </div>
          )}

          {/* ═══ TAB 2: Term Structure ═══ */}
          {activeTab === 2 && (
            <div className="card space-y-4">
              <Plot
                data={[
                  {
                    x: surface.termStructure.map(t => `${t.label} (${t.dte}d)`),
                    y: surface.termStructure.map(t => t.iv),
                    type: "scatter" as const, mode: "lines+markers" as const,
                    line: { color: t.accent, width: 2 },
                    marker: { color: t.accent, size: 8 },
                    text: surface.termStructure.map(t => `${t.iv.toFixed(1)}%`),
                    textposition: "top center" as const,
                    textfont: { size: 9, color: t.text },
                    name: "ATM IV",
                    hovertemplate: "%{x}<br>IV: %{y:.1f}%<extra></extra>",
                  },
                  ...(surface.hv20 != null ? [{
                    x: surface.termStructure.map(t => `${t.label} (${t.dte}d)`),
                    y: surface.termStructure.map(() => surface.hv20!),
                    type: "scatter" as const, mode: "lines" as const,
                    line: { color: t.spot, width: 1.5, dash: "dash" as const },
                    name: `HV20 (${surface.hv20!.toFixed(1)}%)`,
                  }] : []),
                  ...(surface.hv60 != null ? [{
                    x: surface.termStructure.map(t => `${t.label} (${t.dte}d)`),
                    y: surface.termStructure.map(() => surface.hv60!),
                    type: "scatter" as const, mode: "lines" as const,
                    line: { color: t.gain, width: 1.5, dash: "dot" as const },
                    name: `HV60 (${surface.hv60!.toFixed(1)}%)`,
                  }] : []),
                ]}
                layout={{
                  height: 350, ...L,
                  xaxis: { title: "Expiration", gridcolor: t.grid },
                  yaxis: { title: "Volatility (%)", gridcolor: t.grid },
                  legend: { x: 0.01, y: 0.99, bgcolor: isDark ? "rgba(22,27,34,0.85)" : "rgba(255,255,255,0.85)" },
                }}
                config={{ displayModeBar: false, responsive: true }}
                style={{ width: "100%" }}
              />
              <div className="text-sm text-text-muted">
                Shape: <strong className={surface.tsShape === "Contango" ? "text-gain" : surface.tsShape === "Backwardation" ? "text-loss" : ""}>{surface.tsShape}</strong>
                {surface.tsShape === "Contango" && " — back-month IV higher than front (normal, favorable for calendars)"}
                {surface.tsShape === "Backwardation" && " — front-month IV higher than back (event risk, favor condors)"}
              </div>

              {/* IV/HV Ratio Table */}
              {surface.hv20 != null && (
                <div className="overflow-x-auto">
                  <table className="data-table text-xs">
                    <thead>
                      <tr><th>Expiration</th><th>DTE</th><th>ATM IV</th><th>IV/HV20</th><th>IV/HV60</th><th>Premium</th><th>Signal</th></tr>
                    </thead>
                    <tbody>
                      {surface.termStructure.map((t, i) => {
                        const ivHv20 = surface.hv20! > 0 ? t.iv / surface.hv20! : 0;
                        const ivHv60 = surface.hv60 && surface.hv60 > 0 ? t.iv / surface.hv60 : null;
                        const premium = t.iv - surface.hv20!;
                        const signal = ivHv20 > 1.3 ? "Rich" : ivHv20 > 1.1 ? "Slightly Rich" : ivHv20 > 0.95 ? "Fair" : ivHv20 > 0.8 ? "Slightly Cheap" : "Cheap";
                        const signalColor = signal === "Rich" ? "text-loss" : signal === "Cheap" ? "text-gain" : signal.includes("Rich") ? "text-orange-500" : signal.includes("Cheap") ? "text-emerald-500" : "";
                        return (
                          <tr key={i}>
                            <td className="font-semibold">{t.label}</td>
                            <td className="font-data">{t.dte}d</td>
                            <td className="font-data">{t.iv.toFixed(1)}%</td>
                            <td className="font-data">{ivHv20.toFixed(2)}x</td>
                            <td className="font-data">{ivHv60 ? `${ivHv60.toFixed(2)}x` : "N/A"}</td>
                            <td className={`font-data ${premium > 0 ? "text-loss" : "text-gain"}`}>{premium > 0 ? "+" : ""}{premium.toFixed(1)}%</td>
                            <td className={`font-semibold ${signalColor}`}>{signal}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}

              {/* Event Kink Detection */}
              {surface.termStructure.length >= 4 && (() => {
                const ts = surface.termStructure;
                // Fit linear trend through DTE vs IV
                const n = ts.length;
                const sumX = ts.reduce((s, t) => s + t.dte, 0);
                const sumY = ts.reduce((s, t) => s + t.iv, 0);
                const sumXY = ts.reduce((s, t) => s + t.dte * t.iv, 0);
                const sumX2 = ts.reduce((s, t) => s + t.dte * t.dte, 0);
                const slope = (n * sumXY - sumX * sumY) / (n * sumX2 - sumX * sumX);
                const intercept = (sumY - slope * sumX) / n;

                // Compute residuals, find 1.5σ+ deviations
                const residuals = ts.map(t => t.iv - (intercept + slope * t.dte));
                const residStd = Math.sqrt(residuals.reduce((s, r) => s + r * r, 0) / n);
                const kinks = ts.map((t, i) => ({
                  ...t, residual: residuals[i],
                  isKink: residStd > 0 && Math.abs(residuals[i]) > 1.5 * residStd,
                  premium: residuals[i],
                })).filter(k => k.isKink);

                if (kinks.length === 0) return null;
                return (
                  <div className="bg-warn-bg border border-warn/20 rounded-lg p-3 text-xs">
                    <div className="font-semibold text-warn mb-1">Event Kink Detected</div>
                    {kinks.map((k, i) => (
                      <div key={i} className="text-text-muted">
                        <strong>{k.label}</strong> ({k.dte}d): IV {k.iv.toFixed(1)}% is {k.premium > 0 ? "+" : ""}{k.premium.toFixed(1)}pp above trend
                        — check for earnings or FOMC near this expiration.
                      </div>
                    ))}
                  </div>
                );
              })()}
            </div>
          )}

          {/* ═══ TAB 3: Dislocations ═══ */}
          {activeTab === 3 && (
            <div className="card space-y-4">
              {/* Controls */}
              {surface.hv20 != null && (
                <div className="flex items-center gap-4 flex-wrap">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-semibold text-text-muted">Baseline:</span>
                    {(["hv20", "hv60", "term"] as const).map(b => (
                      <button key={b} onClick={() => setDislocBaseline(b)}
                        className={`px-2 py-1 text-xs rounded ${dislocBaseline === b ? "bg-accent text-white" : "text-text-muted hover:bg-surface-alt"}`}>
                        {b === "hv20" ? "20d HV" : b === "hv60" ? "60d HV" : "Term-Adj"}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {/* Dislocation Heatmap */}
              {(() => {
                const goodExps = surface.expirations;
                const baselineIv = (expIdx: number, dte: number) => {
                  if (dislocBaseline === "hv60" && surface.hv60) return surface.hv60;
                  if (dislocBaseline === "term" && surface.hv20) return surface.hv20 * Math.sqrt(dte / 20);
                  return surface.hv20 ?? surface.zMatrix[expIdx]?.[surface.spotColIdx] ?? 0;
                };
                const heatZ = goodExps.map(({ dte }, expIdx) => {
                  const base = baselineIv(expIdx, dte);
                  return surface.strikes.map((_, kIdx) => {
                    const iv = surface.zMatrix[expIdx]?.[kIdx];
                    if (iv == null || base <= 0) return null;
                    const m = surface.strikes[kIdx] / surface.spot;
                    if (m < dislocRange[0] || m > dislocRange[1]) return null;
                    return iv - base;
                  });
                });

                // Summary metrics
                const allVals = heatZ.flat().filter((v): v is number => v !== null);
                const putVals: number[] = [];
                const callVals: number[] = [];
                heatZ.forEach(row => row.forEach((v, ci) => {
                  if (v === null) return;
                  if (surface.strikes[ci] < surface.spot) putVals.push(v);
                  else callVals.push(v);
                }));
                const avgAll = allVals.length > 0 ? allVals.reduce((s, v) => s + v, 0) / allVals.length : 0;
                const avgPut = putVals.length > 0 ? putVals.reduce((s, v) => s + v, 0) / putVals.length : 0;
                const avgCall = callVals.length > 0 ? callVals.reduce((s, v) => s + v, 0) / callVals.length : 0;
                const surfaceLabel = avgAll > 3 ? "RICH" : avgAll < -3 ? "CHEAP" : "FAIR";

                return (
                  <>
                    <div className="flex gap-6 flex-wrap">
                      <Metric label="Surface" value={surfaceLabel} />
                      <Metric label="Put Side" value={`${avgPut > 0 ? "+" : ""}${avgPut.toFixed(1)}%`} />
                      <Metric label="Call Side" value={`${avgCall > 0 ? "+" : ""}${avgCall.toFixed(1)}%`} />
                      <Metric label="Skew Tilt" value={`${(avgPut - avgCall).toFixed(1)}%`} />
                    </div>
                    <Plot
                      data={[{
                        type: "heatmap" as const,
                        x: surface.strikes.map(k => k / surface.spot),
                        y: surface.expLabels,
                        z: heatZ,
                        colorscale: [[0, t.gain], [0.5, t.grid], [1, t.loss]],
                        zmid: 0,
                        colorbar: { title: { text: "IV - Baseline (%)", font: { size: 9 } }, thickness: 12 },
                        hovertemplate: "Moneyness: %{x:.3f}<br>%{y}<br>Dislocation: %{z:.1f}%<extra></extra>",
                        zsmooth: "best",
                      }]}
                      layout={{
                        height: 350, ...L,
                        margin: { l: 100, r: 20, t: 20, b: 50 },
                        xaxis: { title: "Moneyness (Strike / Spot)", gridcolor: t.grid },
                        yaxis: { gridcolor: t.grid },
                        shapes: [{
                          type: "line", x0: 1.0, x1: 1.0, y0: 0, y1: 1, yref: "paper",
                          line: { color: t.spot, width: 2, dash: "dash" },
                        }],
                      }}
                      config={{ displayModeBar: false, responsive: true }}
                      style={{ width: "100%" }}
                    />
                  </>
                );
              })()}

              {/* Dislocation by Expiration */}
              <Plot
                data={[{
                  x: surface.expLabels,
                  y: surface.expirations.map((_, expIdx) => {
                    const vals = surface.strikes.map((_, kIdx) => {
                      const iv = surface.zMatrix[expIdx]?.[kIdx];
                      const atm = surface.zMatrix[expIdx]?.[surface.spotColIdx];
                      if (iv == null || atm == null) return 0;
                      return iv - atm;
                    });
                    return vals.reduce((s, v) => s + v, 0) / vals.length;
                  }),
                  type: "bar" as const,
                  marker: {
                    color: surface.expirations.map((_, expIdx) => {
                      const avg = surface.strikes.map((_, kIdx) => {
                        const iv = surface.zMatrix[expIdx]?.[kIdx];
                        const atm = surface.zMatrix[expIdx]?.[surface.spotColIdx];
                        return iv != null && atm != null ? iv - atm : 0;
                      }).reduce((s, v) => s + v, 0) / surface.strikes.length;
                      return avg > 0 ? t.loss : t.gain;
                    }),
                  },
                  hovertemplate: "%{x}<br>Avg Dislocation: %{y:.1f}%<extra></extra>",
                }]}
                layout={{ height: 250, ...L, xaxis: { gridcolor: t.grid }, yaxis: { title: "Avg Dislocation (%)", gridcolor: t.grid } }}
                config={{ displayModeBar: false, responsive: true }}
                style={{ width: "100%" }}
              />

              {/* Most Mispriced Contracts Table */}
              {surface.dislocations.length > 0 && (
                <>
                  <div className="text-sm font-semibold mt-2">Most Mispriced Contracts</div>
                  <div className="overflow-x-auto">
                    <table className="data-table text-xs">
                      <thead>
                        <tr><th>Strike</th><th>Moneyness</th><th>Expiration</th><th>DTE</th><th>IV</th><th>ATM IV</th><th>Diff (bp)</th><th>OI</th><th>Type</th></tr>
                      </thead>
                      <tbody>
                        {surface.dislocations.slice(0, 20).map((d, i) => (
                          <tr key={i}>
                            <td className="font-data">${d.strike.toFixed(0)}</td>
                            <td className="font-data">{d.moneyness.toFixed(3)}</td>
                            <td>{fmtExp(d.exp)}</td>
                            <td className="font-data">{d.dte}d</td>
                            <td className="font-data">{d.iv.toFixed(1)}%</td>
                            <td className="font-data">{d.atmIv.toFixed(1)}%</td>
                            <td className={`font-data font-semibold ${d.type === "rich" ? "text-loss" : "text-gain"}`}>
                              {d.diff > 0 ? "+" : ""}{d.diff}
                            </td>
                            <td className="font-data">{d.oi.toLocaleString()}</td>
                            <td><span className={`badge ${d.type === "rich" ? "badge-loss" : "badge-gain"}`}>{d.type}</span></td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </>
              )}

              {/* Actionable Trade Ideas: Top 5 Rich + Top 5 Cheap */}
              {surface.dislocations.length > 0 && (() => {
                const withOI = surface.dislocations.filter(d => d.oi > 0);
                const rich = withOI.filter(d => d.type === "rich").slice(0, 5);
                const cheap = withOI.filter(d => d.type === "cheap").slice(0, 5);
                if (rich.length === 0 && cheap.length === 0) return null;
                return (
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    {cheap.length > 0 && (
                      <div className="border border-gain/20 rounded-lg p-3">
                        <div className="text-xs font-semibold text-gain mb-2">BUY — Cheapest Contracts</div>
                        {cheap.map((d, i) => (
                          <div key={i} className="text-[0.65rem] font-data text-text-muted flex justify-between border-b border-border py-1 last:border-0">
                            <span>${d.strike.toFixed(0)} {fmtExp(d.exp)} ({d.dte}d)</span>
                            <span className="text-gain font-semibold">{d.diff}bp · OI {d.oi.toLocaleString()}</span>
                          </div>
                        ))}
                      </div>
                    )}
                    {rich.length > 0 && (
                      <div className="border border-loss/20 rounded-lg p-3">
                        <div className="text-xs font-semibold text-loss mb-2">SELL — Richest Contracts</div>
                        {rich.map((d, i) => (
                          <div key={i} className="text-[0.65rem] font-data text-text-muted flex justify-between border-b border-border py-1 last:border-0">
                            <span>${d.strike.toFixed(0)} {fmtExp(d.exp)} ({d.dte}d)</span>
                            <span className="text-loss font-semibold">+{d.diff}bp · OI {d.oi.toLocaleString()}</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                );
              })()}
            </div>
          )}

          {/* ═══ TAB 4: Skew Metrics ═══ */}
          {activeTab === 4 && (
            <div className="card space-y-4">
              {/* Summary averages */}
              {surface.skews.length > 0 && (() => {
                const n = surface.skews.length;
                const avgPut = surface.skews.reduce((s, sk) => s + (sk.putSkew25d ?? 0), 0) / n;
                const avgCall = surface.skews.reduce((s, sk) => s + (sk.callSkew25d ?? 0), 0) / n;
                const avgRR = surface.skews.reduce((s, sk) => s + (sk.riskReversal ?? 0), 0) / n;
                const avgBfly = surface.skews.reduce((s, sk) => s + (sk.butterfly ?? 0), 0) / n;
                return (
                  <div className="flex gap-6 flex-wrap">
                    <Metric label="Avg Put Skew" value={`${avgPut.toFixed(2)}x`} />
                    <Metric label="Avg Call Skew" value={`${avgCall.toFixed(2)}x`} />
                    <Metric label="Avg Risk Rev" value={`${avgRR.toFixed(1)}%`} />
                    <Metric label="Avg Butterfly" value={`${avgBfly.toFixed(1)}%`} />
                  </div>
                );
              })()}

              {/* Full table */}
              <div className="overflow-x-auto">
                <table className="data-table text-xs">
                  <thead>
                    <tr><th>Expiration</th><th>DTE</th><th>ATM IV</th><th>25Δ Put Skew</th><th>25Δ Call Skew</th><th>Risk Reversal</th><th>Butterfly</th><th>Slope (bp/step)</th></tr>
                  </thead>
                  <tbody>
                    {surface.skews.map((s, i) => (
                      <tr key={i}>
                        <td className="font-semibold">{s.label}</td>
                        <td className="font-data">{s.dte}d</td>
                        <td className="font-data">{s.atmIv.toFixed(1)}%</td>
                        <td className={`font-data ${s.putSkew25d && s.putSkew25d > 1.15 ? "text-loss font-semibold" : s.putSkew25d && s.putSkew25d < 1.02 ? "text-gain" : ""}`}>
                          {s.putSkew25d?.toFixed(2) ?? "N/A"}x
                        </td>
                        <td className={`font-data ${s.callSkew25d && s.callSkew25d > 1.0 ? "text-loss" : ""}`}>
                          {s.callSkew25d?.toFixed(2) ?? "N/A"}x
                        </td>
                        <td className={`font-data ${s.riskReversal && s.riskReversal < 0 ? "text-loss" : s.riskReversal && s.riskReversal > 0 ? "text-gain" : ""}`}>
                          {s.riskReversal != null ? `${s.riskReversal > 0 ? "+" : ""}${s.riskReversal.toFixed(1)}%` : "N/A"}
                        </td>
                        <td className="font-data">{s.butterfly != null ? `${s.butterfly.toFixed(1)}%` : "N/A"}</td>
                        <td className="font-data">{s.skewSlope > 0 ? "+" : ""}{s.skewSlope}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {/* 4-panel chart */}
              <Plot
                data={[
                  // Put Skew
                  { x: surface.skews.map(s => `${s.label}`), y: surface.skews.map(s => s.putSkew25d ?? 0), type: "scatter" as const, mode: "lines+markers" as const, name: "Put Skew", line: { color: t.loss }, marker: { size: 6 }, xaxis: "x", yaxis: "y" },
                  // Call Skew
                  { x: surface.skews.map(s => `${s.label}`), y: surface.skews.map(s => s.callSkew25d ?? 0), type: "scatter" as const, mode: "lines+markers" as const, name: "Call Skew", line: { color: t.gain }, marker: { size: 6 }, xaxis: "x2", yaxis: "y2" },
                  // Risk Reversal
                  { x: surface.skews.map(s => `${s.label}`), y: surface.skews.map(s => s.riskReversal ?? 0), type: "bar" as const, name: "Risk Rev", marker: { color: surface.skews.map(s => (s.riskReversal ?? 0) < 0 ? t.loss : t.gain) }, xaxis: "x3", yaxis: "y3" },
                  // Butterfly
                  { x: surface.skews.map(s => `${s.label}`), y: surface.skews.map(s => s.butterfly ?? 0), type: "scatter" as const, mode: "lines" as const, fill: "tozeroy" as const, name: "Butterfly", line: { color: t.accent }, xaxis: "x4", yaxis: "y4" },
                ]}
                layout={{
                  height: 500,
                  ...L,
                  margin: { l: 50, r: 20, t: 30, b: 40 },
                  grid: { rows: 2, columns: 2, pattern: "independent" as const },
                  xaxis: { gridcolor: t.grid, domain: [0, 0.48] },
                  yaxis: { title: "Put Skew", gridcolor: t.grid, domain: [0.55, 1] },
                  xaxis2: { gridcolor: t.grid, domain: [0.52, 1] },
                  yaxis2: { title: "Call Skew", gridcolor: t.grid, domain: [0.55, 1], anchor: "x2" },
                  xaxis3: { gridcolor: t.grid, domain: [0, 0.48] },
                  yaxis3: { title: "Risk Rev (%)", gridcolor: t.grid, domain: [0, 0.45], anchor: "x3" },
                  xaxis4: { gridcolor: t.grid, domain: [0.52, 1] },
                  yaxis4: { title: "Butterfly (%)", gridcolor: t.grid, domain: [0, 0.45], anchor: "x4" },
                  showlegend: false,
                  annotations: [
                    { text: "Put Skew (25Δ)", x: 0.24, y: 1.02, xref: "paper", yref: "paper", showarrow: false, font: { size: 10, color: t.text } },
                    { text: "Call Skew (25Δ)", x: 0.76, y: 1.02, xref: "paper", yref: "paper", showarrow: false, font: { size: 10, color: t.text } },
                    { text: "Risk Reversal", x: 0.24, y: 0.48, xref: "paper", yref: "paper", showarrow: false, font: { size: 10, color: t.text } },
                    { text: "Butterfly", x: 0.76, y: 0.48, xref: "paper", yref: "paper", showarrow: false, font: { size: 10, color: t.text } },
                  ],
                }}
                config={{ displayModeBar: false, responsive: true }}
                style={{ width: "100%" }}
              />

              {/* Alerts */}
              {surface.skews.some(s => s.putSkew25d && s.putSkew25d > 1.20) && (
                <div className="text-xs text-loss bg-loss/10 border border-loss/20 rounded-lg px-3 py-2">
                  Heavy downside hedging demand detected — put skew &gt;1.20x at some expirations.
                </div>
              )}
              {surface.skews.some(s => s.putSkew25d && s.putSkew25d < 0.95) && (
                <div className="text-xs text-gain bg-gain/10 border border-gain/20 rounded-lg px-3 py-2">
                  Unusually flat skew detected — tail hedges may be cheap. Consider protective puts.
                </div>
              )}
            </div>
          )}

          {/* ═══ TAB 5: Gamma Scalping ═══ */}
          {activeTab === 5 && (
            <div className="card space-y-4">
              {surface.gammaCandidates.length === 0 ? (
                <p className="text-sm text-text-muted">Need HV20 data to evaluate gamma scalping candidates. Metrics may not be available for this ticker.</p>
              ) : (
                <>
                  {/* Best candidate callout */}
                  {surface.gammaCandidates[0].ivHvRatio < 1.0 && (
                    <div className="bg-gain/10 border border-gain/20 rounded-lg p-3">
                      <div className="text-sm font-semibold text-gain">Best Gamma Scalp Candidate</div>
                      <div className="text-xs mt-1">
                        {fmtExp(surface.gammaCandidates[0].exp)} ({surface.gammaCandidates[0].dte}d) — IV/HV: {surface.gammaCandidates[0].ivHvRatio.toFixed(2)}x
                        | Straddle: ${surface.gammaCandidates[0].straddleCost.toFixed(2)}
                        | Break-Even Move: {surface.gammaCandidates[0].breakEvenMove.toFixed(2)}%/day
                        | Γ/Θ: {surface.gammaCandidates[0].gammaTheta.toFixed(2)}
                      </div>
                    </div>
                  )}

                  {/* Candidate table */}
                  <div className="overflow-x-auto">
                    <table className="data-table text-xs">
                      <thead>
                        <tr><th>Expiration</th><th>DTE</th><th>ATM IV</th><th>IV/HV</th><th>Straddle $</th><th>Γ/Θ</th><th>Break-Even</th><th>OI</th><th>Verdict</th></tr>
                      </thead>
                      <tbody>
                        {surface.gammaCandidates.map((gc, i) => {
                          const verdictColor = gc.verdict === "Cheap Gamma" ? "text-gain" : gc.verdict === "Very Expensive" ? "text-loss" : gc.verdict === "Expensive" ? "text-orange-500" : "";
                          return (
                            <tr key={i} className={gc.verdict === "Cheap Gamma" ? "bg-gain/5" : gc.verdict.includes("Expensive") ? "bg-loss/5" : ""}>
                              <td className="font-semibold">{fmtExp(gc.exp)}</td>
                              <td className="font-data">{gc.dte}d</td>
                              <td className="font-data">{gc.atmIv.toFixed(1)}%</td>
                              <td className="font-data font-semibold">{gc.ivHvRatio.toFixed(2)}x</td>
                              <td className="font-data">${gc.straddleCost.toFixed(2)}</td>
                              <td className="font-data">{gc.gammaTheta.toFixed(2)}</td>
                              <td className="font-data">{gc.breakEvenMove.toFixed(2)}%</td>
                              <td className="font-data">{gc.oi.toLocaleString()}</td>
                              <td className={`font-semibold ${verdictColor}`}>{gc.verdict}</td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>

                  {/* Break-even vs actual moves chart */}
                  {dailyMoves.length > 20 && (
                    <Plot
                      data={[
                        {
                          x: surface.gammaCandidates.map(gc => `${fmtExp(gc.exp)} (${gc.dte}d)`),
                          y: surface.gammaCandidates.map(gc => gc.breakEvenMove),
                          type: "bar" as const,
                          name: "Break-Even Move",
                          marker: { color: surface.gammaCandidates.map(gc => gc.breakEvenMove < avgDailyMove ? t.gain : t.loss) },
                        },
                        {
                          x: surface.gammaCandidates.map(gc => `${fmtExp(gc.exp)} (${gc.dte}d)`),
                          y: surface.gammaCandidates.map(() => avgDailyMove),
                          type: "scatter" as const, mode: "lines" as const,
                          name: `Avg Daily Move (${avgDailyMove.toFixed(2)}%)`,
                          line: { color: t.accent, width: 2, dash: "dash" },
                        },
                        {
                          x: surface.gammaCandidates.map(gc => `${fmtExp(gc.exp)} (${gc.dte}d)`),
                          y: surface.gammaCandidates.map(() => medianDailyMove),
                          type: "scatter" as const, mode: "lines" as const,
                          name: `Median Move (${medianDailyMove.toFixed(2)}%)`,
                          line: { color: t.muted, width: 1.5, dash: "dot" },
                        },
                      ]}
                      layout={{
                        height: 300, ...L,
                        xaxis: { gridcolor: t.grid },
                        yaxis: { title: "Daily Move (%)", gridcolor: t.grid },
                        legend: { x: 0.01, y: 0.99, bgcolor: isDark ? "rgba(22,27,34,0.85)" : "rgba(255,255,255,0.85)", font: { size: 9 } },
                        barmode: "group",
                      }}
                      config={{ displayModeBar: false, responsive: true }}
                      style={{ width: "100%" }}
                    />
                  )}

                  {/* Daily move distribution histogram */}
                  {dailyMoves.length > 20 && surface.gammaCandidates.length > 0 && (
                    <Plot
                      data={[{
                        x: dailyMoves,
                        type: "histogram" as const,
                        nbinsx: 50,
                        marker: { color: isDark ? "rgba(88, 166, 255, 0.4)" : "rgba(26, 86, 219, 0.4)", line: { color: t.accent, width: 1 } },
                        name: "Daily Moves (252d)",
                      }]}
                      layout={{
                        height: 250, ...L,
                        xaxis: { title: "Absolute Daily Move (%)", gridcolor: t.grid },
                        yaxis: { title: "Frequency", gridcolor: t.grid },
                        shapes: [
                          { type: "line", x0: surface.gammaCandidates[0].breakEvenMove, x1: surface.gammaCandidates[0].breakEvenMove, y0: 0, y1: 1, yref: "paper",
                            line: { color: t.loss, width: 2, dash: "dash" } },
                          ...(surface.gammaCandidates.length > 1 ? [{
                            type: "line" as const, x0: surface.gammaCandidates[surface.gammaCandidates.length - 1].breakEvenMove,
                            x1: surface.gammaCandidates[surface.gammaCandidates.length - 1].breakEvenMove,
                            y0: 0, y1: 1, yref: "paper" as const,
                            line: { color: t.spot, width: 2, dash: "dash" as const },
                          }] : []),
                        ],
                        annotations: [
                          { x: surface.gammaCandidates[0].breakEvenMove, y: 1, yref: "paper", text: `Front (${surface.gammaCandidates[0].breakEvenMove.toFixed(2)}%)`, showarrow: true, arrowhead: 2, font: { size: 9 } },
                        ],
                        showlegend: false,
                      }}
                      config={{ displayModeBar: false, responsive: true }}
                      style={{ width: "100%" }}
                    />
                  )}

                  {/* Gamma Scalp P&L Backtest */}
                  {dailyMoves.length > 20 && surface.gammaCandidates.length > 0 && priceHistory && priceHistory.length > 20 && (() => {
                    const best = surface.gammaCandidates[0];
                    const gamma = best.straddleCost > 0 ? (best.gammaTheta * Math.abs(best.straddleCost * 0.01)) : 0;
                    const theta = gamma > 0 ? gamma / best.gammaTheta : 0;
                    const N = Math.min(best.dte, priceHistory.length - 1);
                    const pnlCum: number[] = [];
                    let cumPnl = 0;
                    for (let d = 0; d < N; d++) {
                      const idx = priceHistory.length - N + d;
                      if (idx <= 0) continue;
                      const dS = priceHistory[idx].Close - priceHistory[idx - 1].Close;
                      const dailyPnl = 0.5 * gamma * dS * dS - theta;
                      cumPnl += dailyPnl;
                      pnlCum.push(Math.round(cumPnl * 100));
                    }
                    if (pnlCum.length < 5) return null;
                    const finalPnl = pnlCum[pnlCum.length - 1];
                    return (
                      <div className="mt-4">
                        <div className="text-xs font-semibold mb-1">Gamma Scalp P&L Backtest ({pnlCum.length}d)</div>
                        <div className="text-[0.6rem] text-text-muted mb-2">
                          Simulates: buy ATM straddle {pnlCum.length} days ago, delta-hedge daily.
                          P&L ≈ ½γ(ΔS)² + θ per day.
                          Final: <span className={finalPnl >= 0 ? "text-gain font-semibold" : "text-loss font-semibold"}>${finalPnl}</span> per contract.
                        </div>
                        <Plot
                          data={[{
                            x: Array.from({ length: pnlCum.length }, (_, i) => i + 1),
                            y: pnlCum,
                            type: "scatter" as const, mode: "lines" as const,
                            fill: "tozeroy", fillcolor: finalPnl >= 0 ? t.gain + "15" : t.loss + "15",
                            line: { color: finalPnl >= 0 ? t.gain : t.loss, width: 2 },
                            showlegend: false,
                          }]}
                          layout={{
                            height: 200, ...L, margin: { l: 50, r: 10, t: 5, b: 30 },
                            xaxis: { title: "Days", gridcolor: t.grid },
                            yaxis: { title: "Cumulative P&L ($)", gridcolor: t.grid, zeroline: true, zerolinecolor: t.muted },
                            shapes: [
                              { type: "line", x0: 0, x1: 1, xref: "paper", y0: -best.straddleCost * 100, y1: -best.straddleCost * 100, line: { color: t.loss, width: 1, dash: "dash" } },
                            ],
                            annotations: [
                              { x: 1, xref: "paper", y: -best.straddleCost * 100, text: `−$${(best.straddleCost * 100).toFixed(0)} cost`, showarrow: false, xanchor: "right", font: { size: 8, color: t.loss } },
                            ],
                          }}
                          config={{ displayModeBar: false, responsive: true }}
                          style={{ width: "100%" }}
                        />
                      </div>
                    );
                  })()}
                </>
              )}
            </div>
          )}

          {/* ═══ TAB 6: Surface Animation ═══ */}
          {activeTab === 6 && (
            <div className="card space-y-4">
              <div className="flex items-center gap-3 flex-wrap">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-semibold text-text-muted">Days:</span>
                  <input type="number" value={animDays} onChange={e => setAnimDays(Number(e.target.value))}
                    min={5} max={30} className="w-16 px-2 py-1 border border-border rounded text-xs font-data bg-surface" />
                </div>
                <button onClick={() => loadSnapshots.mutate()} disabled={loadSnapshots.isPending}
                  className="px-4 py-1.5 bg-accent text-white text-xs font-semibold rounded hover:bg-accent-hover disabled:opacity-50">
                  {loadSnapshots.isPending ? "Loading..." : "Load History"}
                </button>
                {snapshots.length > 0 && (
                  <>
                    <div className="flex gap-1 ml-auto">
                      {(["heatmap", "3d"] as const).map(m => (
                        <button key={m} onClick={() => setAnimViewMode(m)}
                          className={`px-2 py-1 text-xs rounded ${animViewMode === m ? "bg-accent text-white" : "text-text-muted hover:bg-surface-alt"}`}>
                          {m === "3d" ? "3D" : "Heatmap"}
                        </button>
                      ))}
                    </div>
                    <div className="flex gap-1">
                      {(["prev", "first"] as const).map(c => (
                        <button key={c} onClick={() => setAnimCompare(c)}
                          className={`px-2 py-1 text-xs rounded ${animCompare === c ? "bg-accent text-white" : "text-text-muted hover:bg-surface-alt"}`}>
                          {c === "prev" ? "vs Prev Day" : "vs First Day"}
                        </button>
                      ))}
                    </div>
                  </>
                )}
              </div>

              {snapshots.length === 0 && !loadSnapshots.isPending && (
                <p className="text-sm text-text-muted py-4 text-center">
                  Click "Load History" to load {animDays} days of surface snapshots for animation.
                </p>
              )}

              {animGrids.length > 0 && (
                <>
                  {/* Day slider */}
                  <div className="flex items-center gap-3">
                    <span className="text-xs font-semibold text-text-muted">Day:</span>
                    <input type="range" min={0} max={animGrids.length - 1} value={animDay}
                      onChange={e => setAnimDay(Number(e.target.value))}
                      className="flex-1" />
                    <span className="text-xs font-data w-24 text-right">{animGrids[animDay]?.date}</span>
                  </div>

                  {/* Metrics vs baseline */}
                  {(() => {
                    const current = animGrids[animDay];
                    const compIdx = animCompare === "prev" ? Math.max(0, animDay - 1) : 0;
                    const baseline = animGrids[compIdx];
                    if (!current || !baseline) return null;
                    const curAvg = current.grid.flat().filter((v): v is number => v !== null);
                    const baseAvg = baseline.grid.flat().filter((v): v is number => v !== null);
                    const curMean = curAvg.length > 0 ? curAvg.reduce((s, v) => s + v, 0) / curAvg.length : 0;
                    const baseMean = baseAvg.length > 0 ? baseAvg.reduce((s, v) => s + v, 0) / baseAvg.length : 0;
                    return (
                      <div className="flex gap-6">
                        <Metric label="Date" value={current.date} />
                        <Metric label="Spot" value={`$${current.spot.toFixed(2)}`} />
                        <Metric label="Avg IV" value={`${curMean.toFixed(1)}%`} />
                        <Metric label="ΔIV vs Baseline" value={`${(curMean - baseMean) > 0 ? "+" : ""}${(curMean - baseMean).toFixed(1)}%`} />
                        <Metric label="Baseline" value={`${animCompare === "prev" ? "Prev Day" : "First Day"} (${baseline.date})`} />
                      </div>
                    );
                  })()}

                  {/* Surface view */}
                  {(() => {
                    const current = animGrids[animDay];
                    if (!current) return null;
                    const { gridM, gridD, grid } = current;

                    if (animViewMode === "heatmap") {
                      return (
                        <Plot
                          data={[{
                            type: "heatmap" as const,
                            x: gridM.map(m => m.toFixed(3)),
                            y: gridD.map(d => `${d.toFixed(0)}d`),
                            z: grid,
                            colorscale: "Viridis",
                            colorbar: { title: { text: "IV %", font: { size: 9 } }, thickness: 12 },
                            zsmooth: "best",
                          }]}
                          layout={{
                            height: 400, ...L,
                            margin: { l: 60, r: 20, t: 20, b: 50 },
                            xaxis: { title: "Moneyness", gridcolor: t.grid },
                            yaxis: { title: "DTE", gridcolor: t.grid },
                          }}
                          config={{ displayModeBar: false, responsive: true }}
                          style={{ width: "100%" }}
                        />
                      );
                    } else {
                      return (
                        <Plot
                          data={[{
                            type: "surface" as const,
                            x: gridM, y: gridD, z: grid,
                            colorscale: "Viridis",
                            colorbar: { title: { text: "IV %", font: { size: 9 } }, thickness: 12 },
                          }]}
                          layout={{
                            height: 500,
                            paper_bgcolor: t.surface3dBg,
                            font: { family: "Inter", size: 10, color: t.text },
                            margin: { l: 0, r: 0, t: 10, b: 10 },
                            scene: {
                              xaxis: { title: "Moneyness" },
                              yaxis: { title: "DTE" },
                              zaxis: { title: "IV %" },
                              camera: { eye: { x: 1.8, y: -1.4, z: 0.9 } },
                            },
                          }}
                          config={{ displayModeBar: true, responsive: true }}
                          style={{ width: "100%", height: "500px" }}
                        />
                      );
                    }
                  })()}

                  {/* IV Change Heatmap */}
                  {(() => {
                    const current = animGrids[animDay];
                    const compIdx = animCompare === "prev" ? Math.max(0, animDay - 1) : 0;
                    const baseline = animGrids[compIdx];
                    if (!current || !baseline || animDay === compIdx) {
                      if (animDay === 0) return <p className="text-xs text-text-muted mt-2">Select a later day to see IV changes vs baseline.</p>;
                      return null;
                    }

                    const diffGrid = current.grid.map((row, ri) =>
                      row.map((v, ci) => {
                        const bv = baseline.grid[ri]?.[ci];
                        if (v === null || bv === null) return null;
                        return v - bv;
                      })
                    );

                    return (
                      <>
                        <div className="text-sm font-semibold">IV Change: {current.date} vs {baseline.date}</div>
                        <Plot
                          data={[{
                            type: "heatmap" as const,
                            x: current.gridM.map(m => m.toFixed(3)),
                            y: current.gridD.map(d => `${d.toFixed(0)}d`),
                            z: diffGrid,
                            colorscale: [[0, t.accent], [0.5, t.grid], [1, t.loss]],
                            zmid: 0,
                            colorbar: { title: { text: "ΔIV %", font: { size: 9 } }, thickness: 12 },
                            zsmooth: "best",
                          }]}
                          layout={{
                            height: 350, ...L,
                            margin: { l: 60, r: 20, t: 20, b: 50 },
                            xaxis: { title: "Moneyness", gridcolor: t.grid },
                            yaxis: { title: "DTE", gridcolor: t.grid },
                          }}
                          config={{ displayModeBar: false, responsive: true }}
                          style={{ width: "100%" }}
                        />
                      </>
                    );
                  })()}

                  {/* AI Evolution Analysis */}
                  <div className="border border-border rounded-lg p-3 mt-4">
                    <div className="flex items-center justify-between mb-2">
                      <div className="text-xs font-semibold">AI Surface Evolution Analysis</div>
                      <button onClick={() => loadEvolution.mutate()} disabled={loadEvolution.isPending}
                        className="px-3 py-1 text-xs bg-accent text-white rounded hover:bg-accent-hover disabled:opacity-50">
                        {loadEvolution.isPending ? "Analyzing..." : evolutionContent ? "Re-analyze" : "Analyze Changes"}
                      </button>
                    </div>
                    {loadEvolution.isPending && (
                      <div className="flex items-center gap-2 py-2">
                        <div className="w-4 h-4 border-2 border-accent border-t-transparent rounded-full animate-spin" />
                        <span className="text-xs text-text-muted">Gemini interpreting surface evolution...</span>
                      </div>
                    )}
                    {evolutionContent && <div className="text-xs text-text-muted whitespace-pre-line">{evolutionContent}</div>}
                  </div>
                </>
              )}
            </div>
          )}

          {/* ═══ TAB 7: Surface Comparison ═══ */}
          {activeTab === 7 && (
            <div className="card space-y-4">
              {/* Mode selector */}
              <div className="flex gap-2">
                {([
                  ["historical", "Current vs Historical"],
                  ["callput", "Call vs Put Surface"],
                  ["cross", "Cross-Ticker"],
                ] as const).map(([mode, label]) => (
                  <button key={mode} onClick={() => setCompMode(mode)}
                    className={`px-3 py-1.5 text-xs font-semibold rounded ${compMode === mode ? "bg-accent text-white" : "text-text-muted hover:bg-surface-alt"}`}>
                    {label}
                  </button>
                ))}
              </div>

              {/* Mode: Current vs Historical */}
              {compMode === "historical" && (
                <>
                  {snapshots.length === 0 ? (
                    <div className="space-y-2">
                      <p className="text-sm text-text-muted">Load surface snapshots to compare.</p>
                      <button onClick={() => loadSnapshots.mutate()} disabled={loadSnapshots.isPending}
                        className="px-4 py-1.5 bg-accent text-white text-xs font-semibold rounded hover:bg-accent-hover disabled:opacity-50">
                        {loadSnapshots.isPending ? "Loading..." : "Load Snapshots"}
                      </button>
                    </div>
                  ) : (
                    <>
                      <div className="flex items-center gap-3">
                        <span className="text-xs font-semibold">Compare to:</span>
                        <select value={compHistDate} onChange={e => setCompHistDate(Number(e.target.value))}
                          className="px-2 py-1 border border-border rounded text-xs bg-surface">
                          {snapshots.map((s, i) => (
                            <option key={i} value={i}>{s.date} (Spot: ${s.spot.toFixed(2)})</option>
                          ))}
                        </select>
                      </div>
                      {(() => {
                        const gridM = Array.from({ length: 30 }, (_, i) => 0.85 + i * (0.3 / 29));
                        const gridD = Array.from({ length: 15 }, (_, i) => 5 + i * (300 / 14));
                        // Current surface grid
                        const curPoints = surface.snapshotData.map(d => ({
                          moneyness: surface.spot > 0 ? d.strike / surface.spot : 0, dte: d.dte, iv: d.iv,
                        })).filter(p => p.moneyness > 0.8 && p.moneyness < 1.2);
                        const curGrid = buildIvGrid(curPoints, gridM, gridD);
                        // Historical grid
                        const histSnap = snapshots[compHistDate];
                        const histPoints = histSnap?.data.map(d => ({
                          moneyness: histSnap.spot > 0 ? d.strike / histSnap.spot : 0, dte: d.dte, iv: d.iv,
                        })).filter(p => p.moneyness > 0.8 && p.moneyness < 1.2) ?? [];
                        const histGrid = buildIvGrid(histPoints, gridM, gridD);
                        // Diff grid
                        const diffGrid = curGrid.map((row, ri) =>
                          row.map((v, ci) => {
                            const hv = histGrid[ri]?.[ci];
                            if (v === null || hv === null) return null;
                            return v - hv;
                          })
                        );
                        const allDiffs = diffGrid.flat().filter((v): v is number => v !== null);
                        const avgDiff = allDiffs.length > 0 ? allDiffs.reduce((s, v) => s + v, 0) / allDiffs.length : 0;

                        return (
                          <>
                            <Metric label="Avg IV Change" value={`${avgDiff > 0 ? "+" : ""}${avgDiff.toFixed(1)}%`} />
                            <div className="grid grid-cols-2 gap-4">
                              <div>
                                <div className="text-xs font-semibold mb-1">Current ({new Date().toLocaleDateString()})</div>
                                <Plot
                                  data={[{ type: "heatmap" as const, x: gridM, y: gridD, z: curGrid, colorscale: "Viridis", zsmooth: "best", showscale: false }]}
                                  layout={{ height: 300, ...L, margin: { l: 40, r: 10, t: 10, b: 40 }, xaxis: { title: "Moneyness" }, yaxis: { title: "DTE" } }}
                                  config={{ displayModeBar: false, responsive: true }}
                                  style={{ width: "100%" }}
                                />
                              </div>
                              <div>
                                <div className="text-xs font-semibold mb-1">Historical ({histSnap?.date})</div>
                                <Plot
                                  data={[{ type: "heatmap" as const, x: gridM, y: gridD, z: histGrid, colorscale: "Viridis", zsmooth: "best", showscale: false }]}
                                  layout={{ height: 300, ...L, margin: { l: 40, r: 10, t: 10, b: 40 }, xaxis: { title: "Moneyness" }, yaxis: { title: "DTE" } }}
                                  config={{ displayModeBar: false, responsive: true }}
                                  style={{ width: "100%" }}
                                />
                              </div>
                            </div>
                            <div className="text-xs font-semibold">IV Difference (Current − Historical)</div>
                            <Plot
                              data={[{
                                type: "heatmap" as const, x: gridM, y: gridD, z: diffGrid,
                                colorscale: [[0, t.accent], [0.5, t.grid], [1, t.loss]], zmid: 0,
                                colorbar: { title: { text: "ΔIV %", font: { size: 9 } }, thickness: 12 },
                                zsmooth: "best",
                              }]}
                              layout={{ height: 300, ...L, margin: { l: 40, r: 20, t: 10, b: 40 }, xaxis: { title: "Moneyness" }, yaxis: { title: "DTE" } }}
                              config={{ displayModeBar: false, responsive: true }}
                              style={{ width: "100%" }}
                            />
                          </>
                        );
                      })()}
                    </>
                  )}
                </>
              )}

              {/* Mode: Call vs Put Surface */}
              {compMode === "callput" && (() => {
                const gridM = Array.from({ length: 30 }, (_, i) => 0.85 + i * (0.3 / 29));
                const gridD = Array.from({ length: 15 }, (_, i) => 5 + i * (300 / 14));
                const callRows = (rawChain: ChainRow[]) => rawChain.filter(c => c.contract_type === "call" && c.implied_volatility > 0.01 && c.implied_volatility < 3);
                const putRows = (rawChain: ChainRow[]) => rawChain.filter(c => c.contract_type === "put" && c.implied_volatility > 0.01 && c.implied_volatility < 3);
                const toPoints = (rows: ChainRow[]) => rows.map(c => ({
                  moneyness: surface.spot > 0 ? c.strike_price / surface.spot : 0,
                  dte: calcDTE(c.expiration_date),
                  iv: c.implied_volatility * 100,
                })).filter(p => p.moneyness > 0.8 && p.moneyness < 1.2);

                const callGrid = buildIvGrid(toPoints(callRows(surface.chainData)), gridM, gridD);
                const putGrid = buildIvGrid(toPoints(putRows(surface.chainData)), gridM, gridD);
                const diffGrid = putGrid.map((row, ri) =>
                  row.map((v, ci) => {
                    const cv = callGrid[ri]?.[ci];
                    if (v === null || cv === null) return null;
                    return v - cv;
                  })
                );
                const allDiffs = diffGrid.flat().filter((v): v is number => v !== null);
                const avgSpread = allDiffs.length > 0 ? allDiffs.reduce((s, v) => s + v, 0) / allDiffs.length : 0;

                return (
                  <>
                    <Metric label="Avg Put-Call Spread" value={`${avgSpread > 0 ? "+" : ""}${avgSpread.toFixed(1)}%`} />
                    <div className="grid grid-cols-2 gap-4">
                      <div>
                        <div className="text-xs font-semibold mb-1">Call Surface</div>
                        <Plot
                          data={[{ type: "heatmap" as const, x: gridM, y: gridD, z: callGrid, colorscale: "Viridis", zsmooth: "best", showscale: false }]}
                          layout={{ height: 300, ...L, margin: { l: 40, r: 10, t: 10, b: 40 }, xaxis: { title: "Moneyness" }, yaxis: { title: "DTE" } }}
                          config={{ displayModeBar: false, responsive: true }}
                          style={{ width: "100%" }}
                        />
                      </div>
                      <div>
                        <div className="text-xs font-semibold mb-1">Put Surface</div>
                        <Plot
                          data={[{ type: "heatmap" as const, x: gridM, y: gridD, z: putGrid, colorscale: "Viridis", zsmooth: "best", showscale: false }]}
                          layout={{ height: 300, ...L, margin: { l: 40, r: 10, t: 10, b: 40 }, xaxis: { title: "Moneyness" }, yaxis: { title: "DTE" } }}
                          config={{ displayModeBar: false, responsive: true }}
                          style={{ width: "100%" }}
                        />
                      </div>
                    </div>
                    <div className="text-xs font-semibold">Put − Call IV Spread</div>
                    <Plot
                      data={[{
                        type: "heatmap" as const, x: gridM, y: gridD, z: diffGrid,
                        colorscale: [[0, t.accent], [0.5, t.grid], [1, t.loss]], zmid: 0,
                        colorbar: { title: { text: "Put-Call ΔIV %", font: { size: 9 } }, thickness: 12 },
                        zsmooth: "best",
                      }]}
                      layout={{ height: 300, ...L, margin: { l: 40, r: 20, t: 10, b: 40 }, xaxis: { title: "Moneyness" }, yaxis: { title: "DTE" } }}
                      config={{ displayModeBar: false, responsive: true }}
                      style={{ width: "100%" }}
                    />
                    {avgSpread > 2 && (
                      <div className="text-xs text-loss bg-loss/10 border border-loss/20 rounded-lg px-3 py-2">
                        OTM puts priced significantly above calls — heavy downside hedging demand.
                      </div>
                    )}
                  </>
                );
              })()}

              {/* Mode: Cross-Ticker */}
              {compMode === "cross" && (
                <>
                  <div className="flex items-center gap-3">
                    <span className="text-xs font-semibold">Compare {tickerRef.current} vs</span>
                    <input type="text" value={crossTicker} onChange={e => setCrossTicker(e.target.value.toUpperCase())}
                      className="w-24 px-2 py-1 border border-border rounded text-xs font-data bg-surface" placeholder="QQQ" />
                    <button onClick={() => loadCross.mutate(crossTicker)} disabled={loadCross.isPending}
                      className="px-4 py-1.5 bg-accent text-white text-xs font-semibold rounded hover:bg-accent-hover disabled:opacity-50">
                      {loadCross.isPending ? "Loading..." : "Compare"}
                    </button>
                  </div>
                  {crossSurface && (() => {
                    const gridM = Array.from({ length: 30 }, (_, i) => 0.85 + i * (0.3 / 29));
                    const gridD = Array.from({ length: 15 }, (_, i) => 5 + i * (300 / 14));
                    const curPoints = surface.snapshotData.map(d => ({
                      moneyness: surface.spot > 0 ? d.strike / surface.spot : 0, dte: d.dte, iv: d.iv,
                    })).filter(p => p.moneyness > 0.8 && p.moneyness < 1.2);
                    const crossPoints = crossSurface.snapshotData.map(d => ({
                      moneyness: crossSurface.spot > 0 ? d.strike / crossSurface.spot : 0, dte: d.dte, iv: d.iv,
                    })).filter(p => p.moneyness > 0.8 && p.moneyness < 1.2);
                    const curGrid = buildIvGrid(curPoints, gridM, gridD);
                    const crossGrid = buildIvGrid(crossPoints, gridM, gridD);
                    const diffGrid = curGrid.map((row, ri) =>
                      row.map((v, ci) => {
                        const cv = crossGrid[ri]?.[ci];
                        if (v === null || cv === null) return null;
                        return v - cv;
                      })
                    );

                    return (
                      <>
                        <div className="grid grid-cols-2 gap-4">
                          <div>
                            <div className="text-xs font-semibold mb-1">{tickerRef.current} (Spot: ${surface.spot.toFixed(2)})</div>
                            <Plot
                              data={[{ type: "heatmap" as const, x: gridM, y: gridD, z: curGrid, colorscale: "Viridis", zsmooth: "best", showscale: false }]}
                              layout={{ height: 300, ...L, margin: { l: 40, r: 10, t: 10, b: 40 }, xaxis: { title: "Moneyness" }, yaxis: { title: "DTE" } }}
                              config={{ displayModeBar: false, responsive: true }}
                              style={{ width: "100%" }}
                            />
                          </div>
                          <div>
                            <div className="text-xs font-semibold mb-1">{crossTicker} (Spot: ${crossSurface.spot.toFixed(2)})</div>
                            <Plot
                              data={[{ type: "heatmap" as const, x: gridM, y: gridD, z: crossGrid, colorscale: "Viridis", zsmooth: "best", showscale: false }]}
                              layout={{ height: 300, ...L, margin: { l: 40, r: 10, t: 10, b: 40 }, xaxis: { title: "Moneyness" }, yaxis: { title: "DTE" } }}
                              config={{ displayModeBar: false, responsive: true }}
                              style={{ width: "100%" }}
                            />
                          </div>
                        </div>
                        <div className="text-xs font-semibold">{tickerRef.current} − {crossTicker} IV Difference</div>
                        <Plot
                          data={[{
                            type: "heatmap" as const, x: gridM, y: gridD, z: diffGrid,
                            colorscale: [[0, t.accent], [0.5, t.grid], [1, t.loss]], zmid: 0,
                            colorbar: { title: { text: "ΔIV %", font: { size: 9 } }, thickness: 12 },
                            zsmooth: "best",
                          }]}
                          layout={{ height: 300, ...L, margin: { l: 40, r: 20, t: 10, b: 40 }, xaxis: { title: "Moneyness" }, yaxis: { title: "DTE" } }}
                          config={{ displayModeBar: false, responsive: true }}
                          style={{ width: "100%" }}
                        />
                      </>
                    );
                  })()}
                </>
              )}
            </div>
          )}

          {/* ═══ TAB 8: AI Trade Ideas ═══ */}
          {activeTab === 8 && (
            <div className="card space-y-4">
              {/* Controls */}
              <div className="flex items-center gap-3 flex-wrap">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-semibold text-text-muted">Focus:</span>
                  {([
                    ["full_scan", "Full Scan"],
                    ["income", "Income/Theta"],
                    ["directional", "Directional"],
                    ["volatility", "Volatility"],
                    ["hedging", "Hedging"],
                  ] as const).map(([val, label]) => (
                    <button key={val} onClick={() => setAiStyle(val)}
                      className={`px-2 py-1 text-xs rounded ${aiStyle === val ? "bg-accent text-white" : "text-text-muted hover:bg-surface-alt"}`}>
                      {label}
                    </button>
                  ))}
                </div>
                <input type="text" value={accountSize} onChange={e => setAccountSize(e.target.value)}
                  placeholder="Account size ($)"
                  className="w-36 px-2 py-1 border border-border rounded text-xs bg-surface" />
                <button onClick={() => loadAI.mutate({})} disabled={loadAI.isPending}
                  className="px-4 py-1.5 bg-accent text-white text-xs font-semibold rounded hover:bg-accent-hover disabled:opacity-50">
                  {loadAI.isPending ? "Generating..." : "Get Trade Ideas"}
                </button>
                {aiCached && <span className="text-xs text-text-muted">(cached)</span>}
              </div>

              {loadAI.isPending && (
                <div className="text-center py-8">
                  <div className="inline-block w-6 h-6 border-2 border-accent border-t-transparent rounded-full animate-spin" />
                  <p className="text-sm text-text-muted mt-3">Gemini analyzing surface (~15-30s)...</p>
                </div>
              )}

              {aiContent && (
                <>
                  {/* Rendered markdown — escape first to prevent XSS, then apply formatting */}
                  <div className="prose prose-sm max-w-none text-sm" dangerouslySetInnerHTML={{
                    __html: (() => {
                      // Escape HTML entities first
                      const escaped = aiContent
                        .replace(/&/g, "&amp;")
                        .replace(/</g, "&lt;")
                        .replace(/>/g, "&gt;");
                      // Then apply safe markdown-like formatting
                      return escaped
                        .replace(/^## (.*?)$/gm, '<h3 class="text-base font-bold mt-4 mb-2 text-text">$1</h3>')
                        .replace(/^#### (.*?)$/gm, '<h4 class="text-sm font-semibold mt-3 mb-1 text-text">$1</h4>')
                        .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
                        .replace(/\n\|(.+)\|/g, (match) => {
                          const cells = match.trim().split("|").filter(c => c.trim());
                          if (cells.some(c => c.includes("---"))) return "";
                          const tag = cells.every(c => c.trim().match(/^[A-Z\s\/]+$/)) ? "th" : "td";
                          return `<tr>${cells.map(c => `<${tag} class="px-2 py-1 border border-border text-xs">${c.trim()}</${tag}>`).join("")}</tr>`;
                        })
                        .replace(/(<tr>.*<\/tr>\n?)+/g, (m) => `<table class="data-table text-xs my-2 w-full">${m}</table>`)
                        .replace(/\n/g, "<br/>");
                    })(),
                  }} />

                  {/* P&L Payoff Diagram — parse legs from AI markdown */}
                  {(() => {
                    // Parse legs table: look for rows like "| BUY | Call | $520 | May 16 | ~$3.50 |"
                    const legRegex = /\|\s*(BUY|SELL)\s*\|\s*(Put|Call)\s*\|\s*\$?([\d.]+)\s*\|[^|]*\|\s*~?\$?([\d.]+)/gi;
                    const legs: { action: string; type: string; strike: number; price: number }[] = [];
                    let m;
                    while ((m = legRegex.exec(aiContent)) !== null) {
                      legs.push({ action: m[1].toUpperCase(), type: m[2], strike: parseFloat(m[3]), price: parseFloat(m[4]) });
                    }
                    if (legs.length < 2 || !surface) return null;
                    const strikes = legs.map(l => l.strike);
                    const lo = Math.min(...strikes) * 0.92;
                    const hi = Math.max(...strikes) * 1.08;
                    const prices = Array.from({ length: 80 }, (_, i) => lo + (hi - lo) * i / 79);
                    const pnl = prices.map(px => {
                      let total = 0;
                      for (const leg of legs) {
                        const sign = leg.action === "BUY" ? 1 : -1;
                        let intrinsic = 0;
                        if (leg.type === "Call") intrinsic = Math.max(0, px - leg.strike);
                        else intrinsic = Math.max(0, leg.strike - px);
                        total += sign * (intrinsic - leg.price) * 100;
                      }
                      return Math.round(total);
                    });
                    return (
                      <details className="mt-3 border border-border rounded-lg p-3" open>
                        <summary className="text-xs font-semibold cursor-pointer">P&L Payoff Diagram ({legs.length} legs parsed)</summary>
                        <Plot
                          data={[
                            { x: prices, y: pnl, type: "scatter" as const, mode: "lines" as const,
                              fill: "tozeroy", fillcolor: t.gain + "12", line: { color: t.accent, width: 2 }, showlegend: false },
                            { x: prices, y: pnl.map(v => v < 0 ? v : 0), type: "scatter" as const, mode: "lines" as const,
                              fill: "tozeroy", fillcolor: t.loss + "15", line: { width: 0 }, showlegend: false, hoverinfo: "skip" as const },
                          ]}
                          layout={{
                            height: 220, ...L, margin: { l: 50, r: 10, t: 10, b: 30 },
                            xaxis: { title: "Underlying ($)", gridcolor: t.grid },
                            yaxis: { title: "P&L ($)", gridcolor: t.grid, zeroline: true, zerolinecolor: t.muted },
                            shapes: [
                              { type: "line", x0: surface.spot, x1: surface.spot, y0: 0, y1: 1, yref: "paper", line: { color: t.spot, width: 1, dash: "dash" } },
                              ...legs.map(l => ({ type: "line" as const, x0: l.strike, x1: l.strike, y0: 0, y1: 1, yref: "paper" as const, line: { color: t.muted, width: 0.5, dash: "dot" as const } })),
                            ],
                            annotations: [
                              { x: surface.spot, y: 1, yref: "paper", text: `Spot $${surface.spot.toFixed(0)}`, showarrow: false, font: { size: 8, color: t.spot } },
                            ],
                          }}
                          config={{ displayModeBar: false, responsive: true }}
                          style={{ width: "100%" }}
                        />
                      </details>
                    );
                  })()}

                  {/* Refine */}
                  <div className="flex items-center gap-2 mt-4 pt-3 border-t border-border">
                    <input type="text" value={refinePrompt} onChange={e => setRefinePrompt(e.target.value)}
                      onKeyDown={e => e.key === "Enter" && refinePrompt && surface && loadAI.mutate({ refine: refinePrompt })}
                      placeholder="Refine: e.g., 'wider strikes on Trade 2', 'show a calendar instead'"
                      className="flex-1 px-3 py-2 border border-border rounded text-xs bg-surface" />
                    <button onClick={() => refinePrompt && loadAI.mutate({ refine: refinePrompt })}
                      disabled={loadAI.isPending || !refinePrompt}
                      className="px-4 py-2 bg-accent text-white text-xs font-semibold rounded hover:bg-accent-hover disabled:opacity-50">
                      Refine
                    </button>
                  </div>

                  {/* CSV download */}
                  <button onClick={() => {
                    const blob = new Blob([aiContent], { type: "text/markdown" });
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement("a");
                    a.href = url;
                    a.download = `${tickerRef.current}_trade_ideas_${new Date().toISOString().slice(0, 10)}.md`;
                    a.click();
                    URL.revokeObjectURL(url);
                  }} className="text-xs text-accent hover:underline">
                    Download as Markdown
                  </button>

                  {/* View context */}
                  <details className="mt-2">
                    <summary className="text-xs text-text-muted cursor-pointer hover:text-text">View AI Context Sent</summary>
                    <pre className="mt-1 p-2 bg-surface-alt rounded text-xs overflow-x-auto whitespace-pre-wrap max-h-64">{buildSurfaceContext(surface, tickerRef.current)}</pre>
                  </details>
                </>
              )}

              {loadAI.isError && (
                <div className="text-sm text-loss">{(loadAI.error as Error).message}</div>
              )}
            </div>
          )}
        </>
      )}

      {surface === null && !load.isPending && load.isSuccess && (
        <div className="card text-center py-8 text-text-muted">
          Not enough options data to build a surface. Try a more liquid ticker.
        </div>
      )}

      {load.isError && (
        <div className="card border-loss/30 bg-loss-bg text-loss text-sm">
          Failed to load: {(load.error as Error).message}
        </div>
      )}
    </div>
  );
}

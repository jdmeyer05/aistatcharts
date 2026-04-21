"use client";

import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { useQuery, useQueries, useQueryClient } from "@tanstack/react-query";
import { useSearchParams, useRouter } from "next/navigation";
import { useTheme } from "next-themes";
import { Plot } from "@/components/plot";
import { AIInterpretation } from "@/components/ai-interpretation";
import {
  fetchSectorConfigs,
  fetchSectorOverview,
  fetchSectorCapex,
  fetchSectorValuation,
  fetchSectorAlpha,
  fetchSectorPrices,
  fetchSectorGuidance,
  fetchSectorMarket,
  type SectorConfig,
  type SectorFinancialRow,
  type SectorForecastRow,
  type SectorRevenueRow,
  type SectorMarginRow,
  type SectorCashflowRow,
  type SectorCapexQuarterlyRow,
  type SectorValuationRow,
  type SectorMomentumRow,
  type SectorEpsRevisionRow,
  type SectorInsiderRow,
  type SectorPricePoint,
  type SectorLiveEstimate,
  type SectorEarningsSurpriseRow,
  type SectorGuidanceCompany,
  type SectorCotRow,
  type SectorMacroPoint,
} from "@/lib/api";
import {
  getChartTheme,
  getBaseLayout,
  heatmapTrace,
  heatmapHeight,
  CHART_HEIGHT,
  type ChartTheme,
} from "@/lib/chart-theme";
import { Metric } from "@/components/ui/metric";


const TABS = [
  "Overview & Revenue",
  "CapEx",
  "Valuation & Returns",
  "Alpha Signals",
  "Risk Analytics",
  "Guidance",
  "Market & Positioning",
  "Pairs & Correlation",
  "Compare All",
];

// URL-friendly slugs — stable even if tab labels change
const TAB_SLUGS = [
  "overview", "capex", "valuation", "alpha", "risk",
  "guidance", "market", "pairs", "compare",
] as const;

const ENDPOINT_LABELS = [
  "Overview", "CapEx", "Valuation", "Alpha", "Prices", "Guidance", "Market",
] as const;

const STORAGE_KEY = "sector-analysis:last-sector";

const COLOR_CYCLE = [
  "#00d1ff", "#ffaa00", "#ff6b6b", "#00ff88", "#ff00ff",
  "#88ccff", "#ffcc00", "#ff8866", "#66ffcc", "#cc88ff",
];

const SUBSECTOR_COLORS = [
  "#00d1ff", "#00ff88", "#ffaa00", "#ff6b6b", "#ff00ff",
  "#88ccff", "#ffcc00", "#ff8866",
];

// ─── Utility functions ─────────────────────────────────────────────

function fmtBn(v: number | null | undefined, digits = 1): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return `$${(v / 1e9).toFixed(digits)}B`;
}

function fmtPctSigned(v: number | null | undefined, digits = 1): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return `${v >= 0 ? "+" : ""}${v.toFixed(digits)}%`;
}

function fmtPct(v: number | null | undefined, digits = 1): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return `${v.toFixed(digits)}%`;
}

function fmtX(v: number | null | undefined, digits = 1): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return `${v.toFixed(digits)}x`;
}

function quarterLabel(dateStr: string): string {
  const d = new Date(dateStr);
  const q = Math.floor(d.getUTCMonth() / 3) + 1;
  return `${d.getUTCFullYear()}-Q${q}`;
}

function colorByMagnitude(v: number, pos: string, neg: string): string {
  return v >= 0 ? pos : neg;
}

// Pearson correlation + covariance helpers
function mean(xs: number[]): number {
  if (xs.length === 0) return 0;
  return xs.reduce((a, b) => a + b, 0) / xs.length;
}

function stdev(xs: number[]): number {
  if (xs.length < 2) return 0;
  const m = mean(xs);
  const v = xs.reduce((s, x) => s + (x - m) ** 2, 0) / (xs.length - 1);
  return Math.sqrt(v);
}

function corr(a: number[], b: number[]): number {
  const n = Math.min(a.length, b.length);
  if (n < 2) return 0;
  const ma = mean(a.slice(-n));
  const mb = mean(b.slice(-n));
  let num = 0, da = 0, db = 0;
  for (let i = 0; i < n; i++) {
    const xa = a[a.length - n + i] - ma;
    const xb = b[b.length - n + i] - mb;
    num += xa * xb;
    da += xa * xa;
    db += xb * xb;
  }
  return da > 0 && db > 0 ? num / Math.sqrt(da * db) : 0;
}

function quantile(sorted: number[], q: number): number {
  if (sorted.length === 0) return 0;
  const pos = q * (sorted.length - 1);
  const lo = Math.floor(pos);
  const hi = Math.ceil(pos);
  if (lo === hi) return sorted[lo];
  return sorted[lo] + (pos - lo) * (sorted[hi] - sorted[lo]);
}

function toReturns(closes: number[]): number[] {
  const out: number[] = [];
  for (let i = 1; i < closes.length; i++) {
    const prev = closes[i - 1];
    if (prev > 0) out.push((closes[i] - prev) / prev);
  }
  return out;
}

function alignedReturns(
  prices: Record<string, SectorPricePoint[]>,
  tickers: string[],
): { dates: string[]; returns: Record<string, number[]>; closes: Record<string, number[]> } {
  const valid = tickers.filter(tk => prices[tk] && prices[tk].length > 30);
  if (valid.length === 0) return { dates: [], returns: {}, closes: {} };

  // Intersect dates
  let common: Set<string> | null = null;
  for (const tk of valid) {
    const s = new Set<string>(prices[tk].map(p => p.date));
    if (common === null) {
      common = s;
    } else {
      const next = new Set<string>();
      for (const d of common) if (s.has(d)) next.add(d);
      common = next;
    }
  }
  const dates = Array.from(common ?? []).sort();

  const closes: Record<string, number[]> = {};
  const returns: Record<string, number[]> = {};
  for (const tk of valid) {
    const map = new Map(prices[tk].map(p => [p.date, p.close]));
    const arr = dates.map(d => map.get(d) ?? NaN).filter(v => Number.isFinite(v));
    closes[tk] = arr;
    returns[tk] = toReturns(arr);
  }
  return { dates, returns, closes };
}

// ─── Main Page ─────────────────────────────────────────────

function SectorAnalysisInner() {
  const params = useSearchParams();
  const router = useRouter();
  const qc = useQueryClient();
  const { resolvedTheme } = useTheme();
  const t = getChartTheme(resolvedTheme === "dark");
  const L = getBaseLayout(t);

  const configsQ = useQuery({
    queryKey: ["sector-configs"],
    queryFn: fetchSectorConfigs,
    staleTime: 24 * 60 * 60_000,
    retry: 2,
  });

  const sectors = configsQ.data?.sectors ?? {};
  const etfs = useMemo(() => Object.keys(sectors), [sectors]);

  // Initial state precedence: URL param → localStorage → XLE default.
  // All reads are guarded for SSR; localStorage only touched after mount so
  // initial client render matches server render and avoids hydration warnings.
  const [etf, setEtf] = useState<string>(() => {
    const urlSector = params.get("sector")?.toUpperCase();
    return urlSector || "XLE";
  });
  const [activeTab, setActiveTab] = useState<number>(() => {
    const slug = params.get("tab");
    const idx = slug ? (TAB_SLUGS as readonly string[]).indexOf(slug) : -1;
    return idx >= 0 ? idx : 0;
  });

  // One-shot localStorage hydration: if URL didn't specify a sector, fall back
  // to whatever the user last viewed. Runs once on mount only.
  useEffect(() => {
    if (params.get("sector")) return;
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      if (stored) setEtf(stored);
    } catch {
      // localStorage may be unavailable (privacy modes) — ignore
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // URL → state sync (reverse direction). Keeps browser back/forward working:
  // when the URL changes externally (nav, shared link), mirror that into
  // state. Guards prevent cycling against the state → URL effect below.
  useEffect(() => {
    const urlSector = params.get("sector")?.toUpperCase();
    const urlTab = params.get("tab");
    const urlTabIdx = urlTab ? (TAB_SLUGS as readonly string[]).indexOf(urlTab) : -1;
    if (urlSector && urlSector !== etf) setEtf(urlSector);
    if (urlTabIdx >= 0 && urlTabIdx !== activeTab) setActiveTab(urlTabIdx);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params]);

  // State → URL sync. Persist selection + keep URL in sync so the view is
  // shareable. Only rewrites when the canonical URL actually differs;
  // combined with the reverse-sync effect this reaches a fixed point.
  useEffect(() => {
    try { localStorage.setItem(STORAGE_KEY, etf); } catch {}
    const urlSector = params.get("sector")?.toUpperCase();
    const urlTab = params.get("tab");
    if (urlSector === etf && urlTab === TAB_SLUGS[activeTab]) return;
    const search = new URLSearchParams();
    search.set("sector", etf);
    search.set("tab", TAB_SLUGS[activeTab]);
    router.replace(`/sector-analysis?${search.toString()}`, { scroll: false });
  }, [etf, activeTab, params, router]);

  const cfg: SectorConfig | undefined = sectors[etf];

  // Parallel queries — auto-enabled now (no Load-button gate). Fundamentals
  // endpoints (overview/capex/valuation/guidance) track quarterly reports so
  // 24h staleTime is appropriate; prices/alpha/market follow weekly-to-daily
  // flows and stay at 10 min.
  const results = useQueries({
    queries: [
      { queryKey: ["sector-overview", etf], queryFn: () => fetchSectorOverview(etf), enabled: !!cfg, staleTime: 24 * 60 * 60_000 },
      { queryKey: ["sector-capex", etf], queryFn: () => fetchSectorCapex(etf), enabled: !!cfg, staleTime: 24 * 60 * 60_000 },
      { queryKey: ["sector-valuation", etf], queryFn: () => fetchSectorValuation(etf), enabled: !!cfg, staleTime: 24 * 60 * 60_000 },
      { queryKey: ["sector-alpha", etf], queryFn: () => fetchSectorAlpha(etf), enabled: !!cfg, staleTime: 1000 * 60 * 10 },
      { queryKey: ["sector-prices", etf], queryFn: () => fetchSectorPrices(etf), enabled: !!cfg, staleTime: 1000 * 60 * 10 },
      { queryKey: ["sector-guidance", etf], queryFn: () => fetchSectorGuidance(etf), enabled: !!cfg, staleTime: 24 * 60 * 60_000 },
      { queryKey: ["sector-market", etf], queryFn: () => fetchSectorMarket(etf), enabled: !!cfg, staleTime: 1000 * 60 * 10 },
    ],
  });
  const [overviewQ, capexQ, valuationQ, alphaQ, pricesQ, guidanceQ, marketQ] = results;

  // `isFetching` covers both the initial fetch and Refresh-button refetches —
  // use that so the progress banner stays honest during a manual refresh.
  const anyFetching = results.some(r => r.isFetching);
  const failed = results.map((r, i) => r.isError ? ENDPOINT_LABELS[i] : null).filter(Boolean) as string[];
  const overview = overviewQ.data;
  const valuation = valuationQ.data;
  const alpha = alphaQ.data;
  const capex = capexQ.data;
  const prices = pricesQ.data;
  const guidance = guidanceQ.data;
  const market = marketQ.data;

  const hdr = useMemo(() => {
    if (!overview) return null;
    const fin = overview.financials;
    const totRev = fin.reduce((s, r) => s + (r.revenue ?? 0), 0);
    const marginRows = fin.filter(r => r.net_margin != null);
    const roeRows = fin.filter(r => r.roe != null);
    const avgMargin = marginRows.length > 0 ? marginRows.reduce((s, r) => s + (r.net_margin as number), 0) / marginRows.length : null;
    const avgRoe = roeRows.length > 0 ? roeRows.reduce((s, r) => s + (r.roe as number), 0) / roeRows.length : null;
    return { companies: fin.length, totalRevenue: totRev, avgMargin, avgRoe };
  }, [overview]);

  // Snapshot one-liner — what's notable about this sector right now.
  // Reveals itself progressively as each endpoint resolves; computed inline
  // from raw rows since the backend response doesn't ship aggregates.
  const snapshot = useMemo(() => {
    const bits: string[] = [];
    if (hdr?.avgMargin != null) bits.push(`avg net margin ${hdr.avgMargin.toFixed(1)}%`);
    if (hdr?.avgRoe != null) bits.push(`avg ROE ${hdr.avgRoe.toFixed(1)}%`);
    if (valuation?.valuation?.length) {
      const fwdPes = valuation.valuation
        .map(v => v.forward_pe)
        .filter((x): x is number => x != null && Number.isFinite(x) && x > 0 && x < 200);
      if (fwdPes.length > 0) {
        const sorted = [...fwdPes].sort((a, b) => a - b);
        const median = sorted[Math.floor(sorted.length / 2)];
        bits.push(`median fwd P/E ${median.toFixed(1)}x`);
      }
    }
    return bits.length ? bits.join(" · ") : null;
  }, [hdr, valuation]);

  // Prefetch overview on hover — warms the cache so the median case is
  // already sub-ms by the time the user actually selects the new sector.
  const prefetchSector = useCallback((nextEtf: string) => {
    if (!nextEtf || nextEtf === etf) return;
    qc.prefetchQuery({
      queryKey: ["sector-overview", nextEtf],
      queryFn: () => fetchSectorOverview(nextEtf),
      staleTime: 24 * 60 * 60_000,
    });
  }, [qc, etf]);

  const retryAll = useCallback(() => {
    results.forEach(r => { if (r.isError) r.refetch(); });
  }, [results]);

  // Unknown-sector fallback — if the URL / localStorage referenced a sector
  // that isn't in the configs response, silently snap to the first available.
  // Effect (not inline setState) so React doesn't complain about updating
  // state during a render.
  useEffect(() => {
    if (!configsQ.data) return;
    if (etfs.length > 0 && !sectors[etf]) setEtf(etfs[0]);
  }, [configsQ.data, etfs, sectors, etf]);

  if (configsQ.isPending) {
    return (
      <div className="card text-center py-12">
        <div className="inline-block w-6 h-6 border-2 border-accent border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }
  if (configsQ.isError) {
    return <div className="card text-center py-12 text-loss">Failed to load sector configs.</div>;
  }
  if (!cfg) {
    // Either no configs yet or the fallback effect hasn't fired yet.
    return <div className="card text-center py-8 text-text-muted text-sm">Loading sectors…</div>;
  }

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">{cfg.title}</h1>
        <p className="text-text-secondary text-sm mt-1">{cfg.subtitle}</p>
      </div>

      {/* Sector selector + refresh */}
      <div className="card card-compact">
        <div className="flex flex-wrap items-center gap-3">
          <select
            value={etf}
            onChange={e => { setEtf(e.target.value); setActiveTab(0); }}
            className="px-3 py-2 border border-border rounded-lg text-sm bg-surface min-w-[220px] font-data"
          >
            {etfs.map(k => (
              <option key={k} value={k} onMouseEnter={() => prefetchSector(k)}>{sectors[k].label}</option>
            ))}
          </select>
          <button
            onClick={() => results.forEach(r => r.refetch())}
            disabled={anyFetching}
            className="px-3 py-2 text-xs border border-border rounded-lg hover:bg-surface-alt disabled:opacity-50"
            title="Force refresh all data"
          >
            {anyFetching ? "Loading…" : "Refresh"}
          </button>
          <div className="text-xs text-text-muted">
            {Object.keys(cfg.companies).length} companies
            {snapshot && <> · <span className="text-text-secondary">{snapshot}</span></>}
          </div>
        </div>
      </div>

      {/* Per-endpoint progress + error surfacing */}
      {(anyFetching || failed.length > 0) && (
        <div className={`card card-compact ${failed.length > 0 ? "border-l-2 border-l-loss" : ""}`}>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px]">
            <span className="font-semibold text-text-muted uppercase tracking-wider">Loading</span>
            {results.map((r, i) => (
              <span key={i} className={
                r.isError ? "text-loss" :
                r.isFetching ? "text-accent" :
                r.isSuccess ? "text-gain" :
                "text-text-muted"
              }>
                {r.isError ? "✗" : r.isFetching ? "⋯" : r.isSuccess ? "✓" : "·"} {ENDPOINT_LABELS[i]}
              </span>
            ))}
            {failed.length > 0 && (
              <button onClick={retryAll} className="ml-auto px-2 py-0.5 text-[10px] rounded border border-loss/40 text-loss hover:bg-loss/10">
                Retry {failed.length}
              </button>
            )}
          </div>
        </div>
      )}

      {hdr && (
        <div className="card card-compact">
          <div className="flex flex-wrap gap-6">
            <Metric label="Companies" value={String(hdr.companies)} />
            <Metric
              label="Combined Revenue"
              value={hdr.totalRevenue > 0 ? `$${(hdr.totalRevenue / 1e12).toFixed(1)}T` : "—"}
            />
            <Metric label="Avg Net Margin" value={hdr.avgMargin != null ? fmtPct(hdr.avgMargin) : "—"} />
            <Metric label="Avg ROE" value={hdr.avgRoe != null ? fmtPct(hdr.avgRoe) : "—"} />
          </div>
        </div>
      )}

      <div className="flex gap-1 border-b border-border pb-1 overflow-x-auto">
        {TABS.map((tab, i) => (
          <button
            key={tab}
            onClick={() => setActiveTab(i)}
            className={`px-3 py-1.5 text-xs font-semibold rounded-t-md transition-colors whitespace-nowrap ${
              activeTab === i ? "bg-accent text-white" : "text-text-muted hover:text-text hover:bg-surface-alt"
            }`}
          >
            {tab}
          </button>
        ))}
      </div>

      {activeTab === 0 && (overview ? <OverviewTab cfg={cfg} overview={overview} t={t} L={L} />
        : overviewQ.isError ? <TabError label="Overview" onRetry={() => overviewQ.refetch()} />
        : <TabLoading label="Overview" />)}
      {activeTab === 1 && (capex ? <CapexTab cfg={cfg} capex={capex} overview={overview} t={t} L={L} />
        : capexQ.isError ? <TabError label="CapEx" onRetry={() => capexQ.refetch()} />
        : <TabLoading label="CapEx" />)}
      {activeTab === 2 && (valuation ? <ValuationTab valuation={valuation} t={t} L={L} />
        : valuationQ.isError ? <TabError label="Valuation" onRetry={() => valuationQ.refetch()} />
        : <TabLoading label="Valuation" />)}
      {activeTab === 3 && (valuation && alpha ? <AlphaTab cfg={cfg} valuation={valuation} alpha={alpha} t={t} L={L} />
        : (valuationQ.isError || alphaQ.isError) ? <TabError label="Alpha" onRetry={() => { valuationQ.refetch(); alphaQ.refetch(); }} />
        : <TabLoading label="Alpha" />)}
      {activeTab === 4 && (prices ? <RiskTab cfg={cfg} prices={prices} t={t} L={L} />
        : pricesQ.isError ? <TabError label="Risk" onRetry={() => pricesQ.refetch()} />
        : <TabLoading label="Risk" />)}
      {activeTab === 5 && (guidance ? <GuidanceTab cfg={cfg} guidance={guidance} t={t} L={L} />
        : guidanceQ.isError ? <TabError label="Guidance" onRetry={() => guidanceQ.refetch()} />
        : <TabLoading label="Guidance" />)}
      {activeTab === 6 && (market && overview ? <MarketTab cfg={cfg} market={market} overview={overview} t={t} L={L} />
        : (marketQ.isError || overviewQ.isError) ? <TabError label="Market" onRetry={() => { marketQ.refetch(); overviewQ.refetch(); }} />
        : <TabLoading label="Market" />)}
      {activeTab === 7 && (prices ? <PairsTab cfg={cfg} prices={prices} t={t} L={L} />
        : pricesQ.isError ? <TabError label="Pairs" onRetry={() => pricesQ.refetch()} />
        : <TabLoading label="Pairs" />)}
      {activeTab === 8 && <CompareTab etfs={etfs} configs={sectors} />}
    </div>
  );
}

function TabLoading({ label }: { label: string }) {
  return (
    <div className="card text-center py-10 text-xs text-text-muted">
      <div className="inline-block w-4 h-4 border-2 border-accent border-t-transparent rounded-full animate-spin mr-2 align-middle" />
      Loading {label}…
    </div>
  );
}

function TabError({ label, onRetry }: { label: string; onRetry: () => void }) {
  return (
    <div className="card card-compact border-l-2 border-l-loss">
      <div className="flex items-center gap-3 text-xs">
        <span className="text-loss font-semibold">{label} failed to load.</span>
        <button onClick={onRetry} className="px-2 py-1 rounded border border-loss/40 text-loss hover:bg-loss/10">Retry</button>
      </div>
    </div>
  );
}

export default function SectorAnalysisPage() {
  // useSearchParams requires a Suspense boundary in Next App Router.
  return (
    <Suspense fallback={
      <div className="card text-center py-12">
        <div className="inline-block w-6 h-6 border-2 border-accent border-t-transparent rounded-full animate-spin" />
      </div>
    }>
      <SectorAnalysisInner />
    </Suspense>
  );
}

// ══════════════════════════════════════════════════════════════
// TAB 1 — OVERVIEW & REVENUE
// ══════════════════════════════════════════════════════════════

function OverviewTab({
  cfg,
  overview,
  t,
  L,
}: {
  cfg: SectorConfig;
  overview: NonNullable<Awaited<ReturnType<typeof fetchSectorOverview>>>;
  t: ChartTheme;
  L: ReturnType<typeof getBaseLayout>;
}) {
  const { financials: fin, forecasts, revenue_history, margin_history, cashflow } = overview;

  const revBars = useMemo(() => {
    const rows = fin.filter(r => r.revenue != null).sort((a, b) => (a.revenue ?? 0) - (b.revenue ?? 0));
    return rows;
  }, [fin]);

  const marginRows = useMemo(
    () => fin.filter(r => r.net_margin != null).sort((a, b) => (a.net_margin ?? 0) - (b.net_margin ?? 0)),
    [fin],
  );

  const roeRows = useMemo(
    () => fin.filter(r => r.roe != null).sort((a, b) => (a.roe ?? 0) - (b.roe ?? 0)),
    [fin],
  );

  // Revenue trend toggle
  const [revMode, setRevMode] = useState<"absolute" | "indexed">("absolute");

  const revByTicker = useMemo(() => {
    const map = new Map<string, SectorRevenueRow[]>();
    for (const r of revenue_history) {
      if (!map.has(r.ticker)) map.set(r.ticker, []);
      map.get(r.ticker)!.push(r);
    }
    for (const arr of map.values()) arr.sort((a, b) => a.date.localeCompare(b.date));
    return map;
  }, [revenue_history]);

  const forecastMap = useMemo(() => {
    const m = new Map<string, SectorForecastRow>();
    for (const f of forecasts) m.set(f.ticker, f);
    return m;
  }, [forecasts]);

  // QoQ / YoY rows
  const growthRows = useMemo(() => {
    const rows: { ticker: string; q_label: string; qoq: number | null; yoy: number | null }[] = [];
    for (const [ticker, arr] of revByTicker) {
      if (arr.length < 2) continue;
      for (let i = 0; i < arr.length; i++) {
        const prev = i > 0 ? arr[i - 1].revenue : null;
        const yearAgo = i >= 4 ? arr[i - 4].revenue : null;
        const qoq = prev && prev > 0 ? (arr[i].revenue / prev - 1) * 100 : null;
        const yoy = yearAgo && yearAgo > 0 ? (arr[i].revenue / yearAgo - 1) * 100 : null;
        rows.push({ ticker, q_label: quarterLabel(arr[i].date), qoq, yoy });
      }
    }
    return rows;
  }, [revByTicker]);

  const latestQoq = useMemo(() => {
    const byT = new Map<string, { ticker: string; qoq: number | null }>();
    for (const r of growthRows) byT.set(r.ticker, { ticker: r.ticker, qoq: r.qoq });
    const arr = [...byT.values()].filter(r => r.qoq != null) as { ticker: string; qoq: number }[];
    arr.sort((a, b) => a.qoq - b.qoq);
    return arr;
  }, [growthRows]);

  const latestYoy = useMemo(() => {
    const byT = new Map<string, { ticker: string; yoy: number | null }>();
    for (const r of growthRows) byT.set(r.ticker, { ticker: r.ticker, yoy: r.yoy });
    const arr = [...byT.values()].filter(r => r.yoy != null) as { ticker: string; yoy: number }[];
    arr.sort((a, b) => a.yoy - b.yoy);
    return arr;
  }, [growthRows]);

  // Revenue volatility (CV %)
  const revVolRows = useMemo(() => {
    const out: { ticker: string; cv: number }[] = [];
    for (const [ticker, arr] of revByTicker) {
      if (arr.length < 3) continue;
      const revs = arr.map(r => r.revenue);
      const m = mean(revs);
      const s = stdev(revs);
      if (m > 0) out.push({ ticker, cv: (s / m) * 100 });
    }
    out.sort((a, b) => a.cv - b.cv);
    return out;
  }, [revByTicker]);

  // Margin history derivation
  const marginByTicker = useMemo(() => {
    const m = new Map<string, { date: string; net_margin: number }[]>();
    for (const r of margin_history) {
      if (r.revenue == null || r.net_income == null || r.revenue === 0) continue;
      if (!m.has(r.ticker)) m.set(r.ticker, []);
      m.get(r.ticker)!.push({ date: r.date, net_margin: (r.net_income / r.revenue) * 100 });
    }
    for (const arr of m.values()) arr.sort((a, b) => a.date.localeCompare(b.date));
    return m;
  }, [margin_history]);

  // Operating leverage
  const opLevRows = useMemo(() => {
    const out: { ticker: string; ol: number }[] = [];
    const byT = new Map<string, SectorMarginRow[]>();
    for (const r of margin_history) {
      if (!byT.has(r.ticker)) byT.set(r.ticker, []);
      byT.get(r.ticker)!.push(r);
    }
    for (const [ticker, arr] of byT) {
      const sorted = [...arr].sort((a, b) => a.date.localeCompare(b.date));
      const ratios: number[] = [];
      for (let i = 1; i < sorted.length; i++) {
        const rev0 = sorted[i - 1].revenue, rev1 = sorted[i].revenue;
        const oi0 = sorted[i - 1].operating_income, oi1 = sorted[i].operating_income;
        if (rev0 == null || rev1 == null || oi0 == null || oi1 == null || rev0 === 0 || oi0 === 0) continue;
        const rChg = rev1 / rev0 - 1;
        const oChg = oi1 / oi0 - 1;
        if (Math.abs(rChg) > 0.01) ratios.push(oChg / rChg);
      }
      if (ratios.length === 0) continue;
      const sortedR = [...ratios].sort((a, b) => a - b);
      const med = quantile(sortedR, 0.5);
      if (med >= -10 && med <= 10) out.push({ ticker, ol: med });
    }
    out.sort((a, b) => a.ol - b.ol);
    return out;
  }, [margin_history]);

  // Earnings quality (OpCF / NI)
  const eqRows = useMemo(() => {
    const niMap = new Map(fin.map(f => [f.ticker, f.net_income]));
    const out: { ticker: string; eq: number }[] = [];
    for (const c of cashflow) {
      const ni = niMap.get(c.ticker);
      if (c.operating_cf == null || ni == null || ni <= 0) continue;
      out.push({ ticker: c.ticker, eq: c.operating_cf / ni });
    }
    out.sort((a, b) => a.eq - b.eq);
    return out;
  }, [cashflow, fin]);

  // Composite scorecard
  const composite = useMemo(() => {
    const tickers = fin.map(f => f.ticker);
    const cols: { key: string; ascending: boolean; value: (tk: string) => number | null }[] = [
      { key: "net_margin", ascending: false, value: tk => fin.find(f => f.ticker === tk)?.net_margin ?? null },
      { key: "roe", ascending: false, value: tk => fin.find(f => f.ticker === tk)?.roe ?? null },
      {
        key: "rev_growth", ascending: false, value: tk => {
          const arr = revByTicker.get(tk);
          if (!arr || arr.length < 2) return null;
          const first = arr[0].revenue, last = arr[arr.length - 1].revenue;
          return first > 0 ? (last / first - 1) * 100 : null;
        },
      },
      { key: "fwd_pe", ascending: true, value: tk => forecastMap.get(tk)?.forward_pe ?? null },
      {
        key: "earnings_quality", ascending: false,
        value: tk => eqRows.find(e => e.ticker === tk)?.eq ?? null,
      },
      { key: "debt_to_equity", ascending: true, value: tk => fin.find(f => f.ticker === tk)?.debt_to_equity ?? null },
    ];

    const ranks: Record<string, Record<string, number>> = {};
    for (const col of cols) {
      const pairs = tickers.map(tk => ({ tk, v: col.value(tk) }));
      const withValues = pairs.filter(p => p.v != null) as { tk: string; v: number }[];
      withValues.sort((a, b) => col.ascending ? a.v - b.v : b.v - a.v);
      withValues.forEach((p, i) => {
        ranks[p.tk] = ranks[p.tk] ?? {};
        ranks[p.tk][col.key] = i + 1;
      });
      const missingRank = withValues.length + 1;
      for (const { tk } of pairs) {
        if (ranks[tk]?.[col.key] == null) {
          ranks[tk] = ranks[tk] ?? {};
          ranks[tk][col.key] = missingRank;
        }
      }
    }

    const scored = tickers.map(tk => {
      const r = ranks[tk] ?? {};
      const values = Object.values(r);
      const avg = values.length ? values.reduce((a, b) => a + b, 0) / values.length : Infinity;
      return { ticker: tk, composite_rank: avg, per_col: r };
    });
    scored.sort((a, b) => a.composite_rank - b.composite_rank);
    return { scored, cols };
  }, [fin, revByTicker, forecastMap, eqRows]);

  return (
    <div className="space-y-4">
      {/* Revenue ranking */}
      {revBars.length > 0 && (
        <div className="card">
          <Plot
            data={[{
              type: "bar",
              orientation: "h",
              y: revBars.map(r => r.ticker),
              x: revBars.map(r => (r.revenue ?? 0) / 1e9),
              marker: { color: t.accent },
              text: revBars.map(r => `$${((r.revenue ?? 0) / 1e9).toFixed(0)}B`),
              textposition: "outside",
              hovertemplate: "%{y}: $%{x:,.0f}B<extra></extra>",
            }]}
            layout={{
              ...L, height: CHART_HEIGHT.normal + 60,
              title: { text: "Annual Revenue ($B)", font: { size: 14, color: t.text } },
              xaxis: { title: { text: "Revenue ($B)" }, gridcolor: t.grid },
              yaxis: { gridcolor: t.grid },
              margin: { l: 60, r: 80, t: 40, b: 40 },
            }}
            config={{ displayModeBar: false, responsive: true }}
            style={{ width: "100%" }}
          />
        </div>
      )}

      {/* Net Margin + ROE bars */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {marginRows.length > 0 && (
          <div className="card">
            <Plot
              data={[{
                type: "bar", orientation: "h",
                y: marginRows.map(r => r.ticker),
                x: marginRows.map(r => r.net_margin ?? 0),
                marker: { color: marginRows.map(r => (r.net_margin ?? 0) < 0 ? t.loss : t.accent) },
                text: marginRows.map(r => `${(r.net_margin ?? 0).toFixed(1)}%`),
                textposition: "outside",
              }]}
              layout={{
                ...L, height: CHART_HEIGHT.normal,
                title: { text: "Net Margin (%)", font: { size: 13, color: t.text } },
                xaxis: { title: { text: "%" }, gridcolor: t.grid },
                yaxis: { gridcolor: t.grid },
                margin: { l: 50, r: 60, t: 40, b: 40 },
              }}
              config={{ displayModeBar: false, responsive: true }}
              style={{ width: "100%" }}
            />
          </div>
        )}
        {roeRows.length > 0 && (
          <div className="card">
            <Plot
              data={[{
                type: "bar", orientation: "h",
                y: roeRows.map(r => r.ticker),
                x: roeRows.map(r => r.roe ?? 0),
                marker: { color: t.spot },
                text: roeRows.map(r => `${(r.roe ?? 0).toFixed(1)}%`),
                textposition: "outside",
              }]}
              layout={{
                ...L, height: CHART_HEIGHT.normal,
                title: { text: "Return on Equity (%)", font: { size: 13, color: t.text } },
                xaxis: { title: { text: "%" }, gridcolor: t.grid },
                yaxis: { gridcolor: t.grid },
                margin: { l: 50, r: 60, t: 40, b: 40 },
              }}
              config={{ displayModeBar: false, responsive: true }}
              style={{ width: "100%" }}
            />
          </div>
        )}
      </div>

      {/* Revenue trend */}
      {revByTicker.size > 0 && (
        <div className="card">
          <div className="flex gap-2 mb-2">
            {(["absolute", "indexed"] as const).map(mode => (
              <button key={mode} onClick={() => setRevMode(mode)}
                className={`px-2.5 py-1 text-xs rounded ${revMode === mode
                  ? "bg-accent text-white" : "text-text-muted hover:text-text hover:bg-surface-alt"}`}>
                {mode === "absolute" ? "Absolute ($B)" : "Indexed (Q1 2024 = 100)"}
              </button>
            ))}
          </div>
          <Plot
            data={[...revByTicker.entries()].flatMap(([ticker, arr], ci) => {
              const color = COLOR_CYCLE[ci % COLOR_CYCLE.length];
              const base = arr[0]?.revenue || 1;
              const xs = arr.map(r => quarterLabel(r.date));
              const ys = revMode === "absolute"
                ? arr.map(r => r.revenue / 1e9)
                : arr.map(r => (r.revenue / base) * 100);
              const traces: Record<string, unknown>[] = [{
                x: xs, y: ys, type: "scatter", mode: "lines+markers", name: ticker,
                line: { width: 2, color }, legendgroup: ticker,
              }];
              if (revMode === "absolute") {
                const fc = forecastMap.get(ticker);
                const revQ = fc?.rev_est_q;
                if (revQ != null && arr.length > 0) {
                  const last = arr[arr.length - 1];
                  traces.push({
                    x: [quarterLabel(last.date), "2026-Q1 (est)"],
                    y: [last.revenue / 1e9, revQ / 1e9],
                    type: "scatter", mode: "lines+markers",
                    line: { width: 2, color, dash: "dot" },
                    marker: { size: 12, symbol: "star" },
                    legendgroup: ticker, showlegend: false,
                  });
                }
              }
              return traces;
            })}
            layout={{
              ...L, height: CHART_HEIGHT.tall,
              title: {
                text: revMode === "absolute"
                  ? "Quarterly Revenue — Absolute ($B)  (★ = estimate)"
                  : "Quarterly Revenue — Indexed (Q1 2024 = 100)",
                font: { size: 14, color: t.text },
              },
              yaxis: {
                title: { text: revMode === "absolute" ? "Revenue ($B)" : "Indexed (Q1 2024 = 100)" },
                gridcolor: t.grid,
              },
              xaxis: { gridcolor: t.grid },
              legend: { orientation: "h", y: -0.15 },
              margin: { l: 60, r: 20, t: 40, b: 60 },
              shapes: revMode === "indexed" ? [{
                type: "line", y0: 100, y1: 100, x0: 0, x1: 1, xref: "paper",
                line: { color: t.muted, width: 1, dash: "dash" },
              }] : [],
            }}
            config={{ displayModeBar: false, responsive: true }}
            style={{ width: "100%" }}
          />
        </div>
      )}

      {/* QoQ + YoY bars */}
      {(latestQoq.length > 0 || latestYoy.length > 0) && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {latestQoq.length > 0 && (
            <div className="card">
              <Plot
                data={[{
                  type: "bar", orientation: "h",
                  y: latestQoq.map(r => r.ticker),
                  x: latestQoq.map(r => r.qoq),
                  marker: { color: latestQoq.map(r => colorByMagnitude(r.qoq, t.accent, t.loss)) },
                  text: latestQoq.map(r => `${r.qoq >= 0 ? "+" : ""}${r.qoq.toFixed(1)}%`),
                  textposition: "outside",
                }]}
                layout={{
                  ...L, height: CHART_HEIGHT.normal,
                  title: { text: "Latest QoQ Revenue Growth (%)", font: { size: 13, color: t.text } },
                  xaxis: { title: { text: "% Change" }, gridcolor: t.grid },
                  yaxis: { gridcolor: t.grid },
                  margin: { l: 50, r: 60, t: 40, b: 40 },
                  shapes: [{ type: "line", x0: 0, x1: 0, y0: 0, y1: 1, yref: "paper", line: { color: t.muted, dash: "dash" } }],
                }}
                config={{ displayModeBar: false, responsive: true }}
                style={{ width: "100%" }}
              />
            </div>
          )}
          {latestYoy.length > 0 && (
            <div className="card">
              <Plot
                data={[{
                  type: "bar", orientation: "h",
                  y: latestYoy.map(r => r.ticker),
                  x: latestYoy.map(r => r.yoy),
                  marker: { color: latestYoy.map(r => colorByMagnitude(r.yoy, t.accent, t.loss)) },
                  text: latestYoy.map(r => `${r.yoy >= 0 ? "+" : ""}${r.yoy.toFixed(1)}%`),
                  textposition: "outside",
                }]}
                layout={{
                  ...L, height: CHART_HEIGHT.normal,
                  title: { text: "Latest YoY Revenue Growth (%)", font: { size: 13, color: t.text } },
                  xaxis: { title: { text: "% Change" }, gridcolor: t.grid },
                  yaxis: { gridcolor: t.grid },
                  margin: { l: 50, r: 60, t: 40, b: 40 },
                  shapes: [{ type: "line", x0: 0, x1: 0, y0: 0, y1: 1, yref: "paper", line: { color: t.muted, dash: "dash" } }],
                }}
                config={{ displayModeBar: false, responsive: true }}
                style={{ width: "100%" }}
              />
            </div>
          )}
        </div>
      )}

      {/* QoQ growth trend */}
      {growthRows.some(r => r.qoq != null) && (
        <div className="card">
          <Plot
            data={Array.from(new Set(growthRows.map(r => r.ticker))).map((ticker, ci) => {
              const sub = growthRows.filter(r => r.ticker === ticker && r.qoq != null);
              return {
                x: sub.map(r => r.q_label),
                y: sub.map(r => r.qoq),
                type: "scatter", mode: "lines+markers", name: ticker,
                line: { width: 2, color: COLOR_CYCLE[ci % COLOR_CYCLE.length] },
              };
            })}
            layout={{
              ...L, height: CHART_HEIGHT.normal,
              title: { text: "QoQ Revenue Growth Trend (%)", font: { size: 13, color: t.text } },
              yaxis: { title: { text: "% Change" }, gridcolor: t.grid },
              xaxis: { gridcolor: t.grid },
              legend: { orientation: "h", y: -0.2 },
              margin: { l: 50, r: 20, t: 40, b: 60 },
              shapes: [{ type: "line", y0: 0, y1: 0, x0: 0, x1: 1, xref: "paper", line: { color: t.muted, dash: "dash" } }],
            }}
            config={{ displayModeBar: false, responsive: true }}
            style={{ width: "100%" }}
          />
        </div>
      )}

      {/* Revenue volatility */}
      {revVolRows.length > 0 && (
        <div className="card">
          <Plot
            data={[{
              type: "bar", orientation: "h",
              y: revVolRows.map(r => r.ticker),
              x: revVolRows.map(r => r.cv),
              marker: { color: revVolRows.map(r => r.cv < 10 ? t.accent : r.cv < 20 ? t.spot : t.loss) },
              text: revVolRows.map(r => `${r.cv.toFixed(1)}%`),
              textposition: "outside",
            }]}
            layout={{
              ...L, height: CHART_HEIGHT.normal,
              title: { text: "Revenue Volatility (Coefficient of Variation %)", font: { size: 13, color: t.text } },
              xaxis: { title: { text: "CV %" }, gridcolor: t.grid },
              yaxis: { gridcolor: t.grid },
              margin: { l: 50, r: 60, t: 40, b: 40 },
            }}
            config={{ displayModeBar: false, responsive: true }}
            style={{ width: "100%" }}
          />
        </div>
      )}

      {/* Margin trend + Operating leverage */}
      {marginByTicker.size > 0 && (
        <div className="card">
          <Plot
            data={[...marginByTicker.entries()].map(([ticker, arr], ci) => ({
              x: arr.map(r => quarterLabel(r.date)),
              y: arr.map(r => r.net_margin),
              type: "scatter", mode: "lines+markers", name: ticker,
              line: { width: 2, color: COLOR_CYCLE[ci % COLOR_CYCLE.length] },
            }))}
            layout={{
              ...L, height: CHART_HEIGHT.normal + 60,
              title: { text: "Net Margin Trend (%)", font: { size: 14, color: t.text } },
              yaxis: { title: { text: "Net Margin %" }, gridcolor: t.grid },
              xaxis: { gridcolor: t.grid },
              legend: { orientation: "h", y: -0.18 },
              margin: { l: 50, r: 20, t: 40, b: 60 },
              shapes: [{ type: "line", y0: 0, y1: 0, x0: 0, x1: 1, xref: "paper", line: { color: t.muted, dash: "dash" } }],
            }}
            config={{ displayModeBar: false, responsive: true }}
            style={{ width: "100%" }}
          />
        </div>
      )}

      {opLevRows.length > 0 && (
        <div className="card">
          <Plot
            data={[{
              type: "bar", orientation: "h",
              y: opLevRows.map(r => r.ticker),
              x: opLevRows.map(r => r.ol),
              marker: { color: opLevRows.map(r => Math.abs(r.ol) > 3 ? t.loss : Math.abs(r.ol) > 1.5 ? t.spot : t.accent) },
              text: opLevRows.map(r => `${r.ol.toFixed(1)}x`),
              textposition: "outside",
            }]}
            layout={{
              ...L, height: CHART_HEIGHT.normal,
              title: { text: "Operating Leverage (median ΔOI% / ΔRev%)", font: { size: 13, color: t.text } },
              xaxis: { title: { text: "ΔOI% / ΔRev%" }, gridcolor: t.grid },
              yaxis: { gridcolor: t.grid },
              margin: { l: 50, r: 60, t: 40, b: 40 },
              shapes: [{ type: "line", x0: 1, x1: 1, y0: 0, y1: 1, yref: "paper", line: { color: t.muted, dash: "dash" } }],
            }}
            config={{ displayModeBar: false, responsive: true }}
            style={{ width: "100%" }}
          />
        </div>
      )}

      {eqRows.length > 0 && (
        <div className="card">
          <Plot
            data={[{
              type: "bar", orientation: "h",
              y: eqRows.map(r => r.ticker),
              x: eqRows.map(r => r.eq),
              marker: { color: eqRows.map(r => r.eq >= 1 ? t.accent : t.loss) },
              text: eqRows.map(r => `${r.eq.toFixed(1)}x`),
              textposition: "outside",
            }]}
            layout={{
              ...L, height: CHART_HEIGHT.normal,
              title: { text: "Earnings Quality (OpCF / Net Income)", font: { size: 13, color: t.text } },
              xaxis: { title: { text: "Ratio" }, gridcolor: t.grid },
              yaxis: { gridcolor: t.grid },
              margin: { l: 50, r: 60, t: 40, b: 40 },
              shapes: [{ type: "line", x0: 1, x1: 1, y0: 0, y1: 1, yref: "paper", line: { color: t.spot, dash: "dash" } }],
            }}
            config={{ displayModeBar: false, responsive: true }}
            style={{ width: "100%" }}
          />
        </div>
      )}

      {/* Composite scorecard */}
      {composite.scored.length > 0 && (
        <div className="card">
          <div className="font-semibold text-sm mb-1">Composite Scorecard</div>
          <div className="text-xs text-text-muted mb-3">
            Weighted ranking across growth, profitability, valuation, leverage, and quality. Lower rank = better.
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs font-data">
              <thead className="border-b border-border text-text-muted">
                <tr>
                  <th className="text-left py-1.5 px-2">Ticker</th>
                  <th className="text-right py-1.5 px-2">Score</th>
                  {composite.cols.map(col => (
                    <th key={col.key} className="text-right py-1.5 px-2">
                      {col.key.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase())}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {composite.scored.map(row => (
                  <tr key={row.ticker} className="border-b border-border/50 hover:bg-surface-alt">
                    <td className="py-1 px-2 font-semibold">{row.ticker}</td>
                    <td className="py-1 px-2 text-right">{row.composite_rank.toFixed(1)}</td>
                    {composite.cols.map(col => (
                      <td key={col.key} className="py-1 px-2 text-right">
                        {row.per_col[col.key] != null ? `#${row.per_col[col.key]}` : "—"}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {composite.scored.length > 0 && (
        <div className="card">
          <Plot
            data={[{
              type: "bar", orientation: "h",
              y: composite.scored.map(r => r.ticker),
              x: composite.scored.map(r => r.composite_rank),
              marker: {
                color: composite.scored.map((_, i) => i < 3 ? t.accent : i < 7 ? t.spot : t.loss),
              },
              text: composite.scored.map((r, i) => `#${i + 1}  (${r.composite_rank.toFixed(1)})`),
              textposition: "outside",
              hovertemplate: "%{y}: Avg Rank %{x:.1f}<extra></extra>",
            }]}
            layout={{
              ...L, height: CHART_HEIGHT.normal + 60,
              title: { text: "Composite Ranking (lower = better)", font: { size: 14, color: t.text } },
              xaxis: { title: { text: "Avg Rank Score" }, gridcolor: t.grid },
              yaxis: { gridcolor: t.grid, autorange: "reversed" },
              margin: { l: 60, r: 80, t: 40, b: 40 },
            }}
            config={{ displayModeBar: false, responsive: true }}
            style={{ width: "100%" }}
          />
        </div>
      )}

      {/* Financial ratios table */}
      {fin.length > 0 && (
        <div className="card">
          <div className="font-semibold text-sm mb-2">Financial Ratios</div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs font-data">
              <thead className="border-b border-border text-text-muted">
                <tr>
                  {["Ticker", "Company", "Revenue", "Net Income", "Net Margin", "Op Margin", "ROE", "ROA", "D/E", "Current", "EPS"].map(h => (
                    <th key={h} className="text-left py-1.5 px-2">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {fin.map(r => (
                  <tr key={r.ticker} className="border-b border-border/50 hover:bg-surface-alt">
                    <td className="py-1 px-2 font-semibold">{r.ticker}</td>
                    <td className="py-1 px-2">{r.company}</td>
                    <td className="py-1 px-2">{fmtBn(r.revenue)}</td>
                    <td className="py-1 px-2">{fmtBn(r.net_income)}</td>
                    <td className="py-1 px-2">{fmtPct(r.net_margin)}</td>
                    <td className="py-1 px-2">{fmtPct(r.operating_margin)}</td>
                    <td className="py-1 px-2">{fmtPct(r.roe)}</td>
                    <td className="py-1 px-2">{fmtPct(r.roa)}</td>
                    <td className="py-1 px-2">{r.debt_to_equity != null ? r.debt_to_equity.toFixed(2) : "—"}</td>
                    <td className="py-1 px-2">{r.current_ratio != null ? r.current_ratio.toFixed(2) : "—"}</td>
                    <td className="py-1 px-2">{r.eps != null ? `$${r.eps.toFixed(2)}` : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <AIInterpretation
        page="sector-overview"
        subject={cfg.etf}
        data={{
          etf: cfg.etf,
          label: cfg.label,
          financials: overview.financials.slice(0, 10),
          forecasts: overview.forecasts?.slice(0, 10) ?? [],
        }}
        buttonLabel={`Interpret ${cfg.etf} fundamentals`}
      />
    </div>
  );
}

// ══════════════════════════════════════════════════════════════
// TAB 2 — CAPEX
// ══════════════════════════════════════════════════════════════

function CapexTab({
  cfg,
  capex,
  overview,
  t,
  L,
}: {
  cfg: SectorConfig;
  capex: NonNullable<Awaited<ReturnType<typeof fetchSectorCapex>>>;
  overview?: NonNullable<Awaited<ReturnType<typeof fetchSectorOverview>>>;
  t: ChartTheme;
  L: ReturnType<typeof getBaseLayout>;
}) {
  const { capex_quarterly: qrows } = capex;
  const [mode, setMode] = useState<"absolute" | "indexed">("absolute");

  const snapMap = useMemo(() => {
    const m = new Map<string, SectorGuidanceCompany>();
    for (const d of cfg.guidance_snapshot.data) m.set(d.ticker, d);
    return m;
  }, [cfg.guidance_snapshot]);

  const byTicker = useMemo(() => {
    const m = new Map<string, SectorCapexQuarterlyRow[]>();
    for (const r of qrows) {
      if (!m.has(r.ticker)) m.set(r.ticker, []);
      m.get(r.ticker)!.push(r);
    }
    for (const arr of m.values()) arr.sort((a, b) => a.date.localeCompare(b.date));
    return m;
  }, [qrows]);

  const latestByTicker = useMemo(() => {
    const m = new Map<string, SectorCapexQuarterlyRow>();
    for (const [tk, arr] of byTicker) m.set(tk, arr[arr.length - 1]);
    return m;
  }, [byTicker]);

  const latestQ = useMemo(() => {
    const arr = [...latestByTicker.values()].sort((a, b) => a.q_capex - b.q_capex);
    return arr;
  }, [latestByTicker]);

  // Capital intensity (latest-quarter CapEx / latest-quarter revenue, per ticker).
  const intensity = useMemo(() => {
    if (!overview) return [];
    const byT = new Map<string, SectorRevenueRow[]>();
    for (const r of overview.revenue_history) {
      if (!byT.has(r.ticker)) byT.set(r.ticker, []);
      byT.get(r.ticker)!.push(r);
    }
    const out: { ticker: string; pct: number }[] = [];
    for (const [tk, arr] of byT) {
      const sorted = [...arr].sort((a, b) => a.date.localeCompare(b.date));
      const latest = sorted[sorted.length - 1];
      const cxLatest = latestByTicker.get(tk);
      if (!latest || !cxLatest || !latest.revenue) continue;
      out.push({ ticker: tk, pct: (cxLatest.q_capex / latest.revenue) * 100 });
    }
    out.sort((a, b) => a.pct - b.pct);
    return out;
  }, [overview, latestByTicker]);

  // YoY CapEx change
  const yoyRows = useMemo(() => {
    const out: { ticker: string; yoy: number }[] = [];
    for (const [tk, arr] of byTicker) {
      if (arr.length < 4) continue;
      const current = arr[arr.length - 1].q_capex;
      const yearAgo = arr[arr.length - 4].q_capex;
      if (yearAgo > 0) out.push({ ticker: tk, yoy: ((current - yearAgo) / yearAgo) * 100 });
    }
    out.sort((a, b) => a.yoy - b.yoy);
    return out;
  }, [byTicker]);

  // Stacked sector CapEx by quarter
  const stacked = useMemo(() => {
    const qLabels = new Set<string>();
    const byQuarter: Record<string, Record<string, number>> = {};
    for (const r of qrows) {
      const q = `${r.year}-Q${r.quarter}`;
      qLabels.add(q);
      byQuarter[q] = byQuarter[q] ?? {};
      byQuarter[q][r.ticker] = (byQuarter[q][r.ticker] ?? 0) + r.q_capex;
    }
    // Add forecast quarter if any tickers have guidance
    const estLabel = "2026-Q1 (est)";
    const estVals: Record<string, number> = {};
    for (const [tk, snap] of snapMap) {
      if (snap.capex_guidance) estVals[tk] = (snap.capex_guidance * 1e9) / 4;
    }
    if (Object.keys(estVals).length > 0) {
      qLabels.add(estLabel);
      byQuarter[estLabel] = estVals;
    }
    const sortedQ = [...qLabels].sort();
    const allTickers = new Set<string>();
    Object.values(byQuarter).forEach(row => Object.keys(row).forEach(tk => allTickers.add(tk)));
    return { sortedQ, byQuarter, tickers: [...allTickers].sort(), estLabel };
  }, [qrows, snapMap]);

  // Detail pivot table
  const detailPivot = useMemo(() => {
    const qLabels = Array.from(new Set(qrows.map(r => `${r.year}-Q${r.quarter}`))).sort();
    const tickers = Array.from(new Set(qrows.map(r => r.ticker))).sort();
    const grid: Record<string, Record<string, number | null>> = {};
    for (const tk of tickers) {
      grid[tk] = {};
      for (const q of qLabels) grid[tk][q] = null;
    }
    for (const r of qrows) {
      const q = `${r.year}-Q${r.quarter}`;
      grid[r.ticker][q] = r.q_capex;
    }
    return { qLabels, tickers, grid };
  }, [qrows]);

  return (
    <div className="space-y-4">
      {qrows.length === 0 && (
        <div className="card text-center py-8 text-text-muted text-sm">No CapEx data available.</div>
      )}

      {/* Latest Quarter CapEx + Capital Intensity */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {latestQ.length > 0 && (
          <div className="card">
            <Plot
              data={[{
                type: "bar", orientation: "h",
                y: latestQ.map(r => r.ticker),
                x: latestQ.map(r => r.q_capex / 1e9),
                marker: { color: t.loss },
                text: latestQ.map(r => `$${(r.q_capex / 1e9).toFixed(1)}B`),
                textposition: "outside",
              }]}
              layout={{
                ...L, height: CHART_HEIGHT.normal,
                title: { text: "Latest Quarter CapEx ($B)", font: { size: 13, color: t.text } },
                xaxis: { title: { text: "CapEx ($B)" }, gridcolor: t.grid },
                yaxis: { gridcolor: t.grid },
                margin: { l: 50, r: 80, t: 40, b: 40 },
              }}
              config={{ displayModeBar: false, responsive: true }}
              style={{ width: "100%" }}
            />
          </div>
        )}

        {intensity.length > 0 && (
          <div className="card">
            <Plot
              data={[{
                type: "bar", orientation: "h",
                y: intensity.map(r => r.ticker),
                x: intensity.map(r => r.pct),
                marker: { color: intensity.map(r => r.pct > 15 ? t.loss : r.pct > 8 ? t.spot : t.accent) },
                text: intensity.map(r => `${r.pct.toFixed(1)}%`),
                textposition: "outside",
              }]}
              layout={{
                ...L, height: CHART_HEIGHT.normal,
                title: { text: "Capital Intensity (CapEx / Revenue %)", font: { size: 13, color: t.text } },
                xaxis: { title: { text: "%" }, gridcolor: t.grid },
                yaxis: { gridcolor: t.grid },
                margin: { l: 50, r: 60, t: 40, b: 40 },
              }}
              config={{ displayModeBar: false, responsive: true }}
              style={{ width: "100%" }}
            />
          </div>
        )}
      </div>

      {/* CapEx Trend */}
      {byTicker.size > 0 && (
        <div className="card">
          <div className="flex gap-2 mb-2">
            {(["absolute", "indexed"] as const).map(m => (
              <button key={m} onClick={() => setMode(m)}
                className={`px-2.5 py-1 text-xs rounded ${mode === m ? "bg-accent text-white" : "text-text-muted hover:text-text hover:bg-surface-alt"}`}>
                {m === "absolute" ? "Absolute ($B)" : "Indexed (Q1 2024 = 100)"}
              </button>
            ))}
          </div>
          <Plot
            data={[...byTicker.entries()].flatMap(([ticker, arr], ci) => {
              const color = COLOR_CYCLE[ci % COLOR_CYCLE.length];
              const base = arr[0]?.q_capex || 1;
              const xs = arr.map(r => `${r.year}-Q${r.quarter}`);
              const ys = mode === "absolute"
                ? arr.map(r => r.q_capex / 1e9)
                : arr.map(r => (r.q_capex / base) * 100);
              const traces: Record<string, unknown>[] = [{
                x: xs, y: ys, type: "scatter", mode: "lines+markers", name: ticker,
                line: { width: 2, color }, legendgroup: ticker,
              }];
              if (mode === "absolute") {
                const snap = snapMap.get(ticker);
                const cxG = snap?.capex_guidance;
                if (cxG != null && arr.length > 0) {
                  const last = arr[arr.length - 1];
                  traces.push({
                    x: [`${last.year}-Q${last.quarter}`, "2026-Q1 (est)"],
                    y: [last.q_capex / 1e9, cxG / 4],
                    type: "scatter", mode: "lines+markers",
                    line: { width: 2, color, dash: "dot" },
                    marker: { size: 12, symbol: "star" },
                    legendgroup: ticker, showlegend: false,
                  });
                }
              }
              return traces;
            })}
            layout={{
              ...L, height: CHART_HEIGHT.tall,
              title: {
                text: mode === "absolute"
                  ? "Quarterly CapEx — Absolute ($B)  (★ = guidance)"
                  : "Quarterly CapEx — Indexed (Q1 2024 = 100)",
                font: { size: 14, color: t.text },
              },
              yaxis: { title: { text: mode === "absolute" ? "CapEx ($B)" : "Indexed" }, gridcolor: t.grid },
              xaxis: { gridcolor: t.grid },
              legend: { orientation: "h", y: -0.15 },
              margin: { l: 60, r: 20, t: 40, b: 60 },
              shapes: mode === "indexed" ? [{
                type: "line", y0: 100, y1: 100, x0: 0, x1: 1, xref: "paper",
                line: { color: t.muted, width: 1, dash: "dash" },
              }] : [],
            }}
            config={{ displayModeBar: false, responsive: true }}
            style={{ width: "100%" }}
          />
        </div>
      )}

      {/* YoY + Stacked */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {yoyRows.length > 0 && (
          <div className="card">
            <Plot
              data={[{
                type: "bar", orientation: "h",
                y: yoyRows.map(r => r.ticker),
                x: yoyRows.map(r => r.yoy),
                marker: { color: yoyRows.map(r => colorByMagnitude(r.yoy, t.accent, t.loss)) },
                text: yoyRows.map(r => `${r.yoy >= 0 ? "+" : ""}${r.yoy.toFixed(1)}%`),
                textposition: "outside",
              }]}
              layout={{
                ...L, height: CHART_HEIGHT.normal,
                title: { text: "CapEx YoY Change (%)", font: { size: 13, color: t.text } },
                xaxis: { title: { text: "% Change" }, gridcolor: t.grid },
                yaxis: { gridcolor: t.grid },
                margin: { l: 50, r: 60, t: 40, b: 40 },
              }}
              config={{ displayModeBar: false, responsive: true }}
              style={{ width: "100%" }}
            />
          </div>
        )}

        {stacked.sortedQ.length > 0 && (
          <div className="card">
            <Plot
              data={stacked.tickers.map((tk, ci) => ({
                type: "bar", name: tk,
                x: stacked.sortedQ,
                y: stacked.sortedQ.map(q => (stacked.byQuarter[q]?.[tk] ?? 0) / 1e9),
                marker: { color: COLOR_CYCLE[ci % COLOR_CYCLE.length] },
              }))}
              layout={{
                ...L, height: CHART_HEIGHT.normal + 20,
                barmode: "stack",
                title: { text: "Sector CapEx by Quarter ($B)", font: { size: 13, color: t.text } },
                yaxis: { title: { text: "Total ($B)" }, gridcolor: t.grid },
                xaxis: { gridcolor: t.grid },
                legend: { orientation: "h", y: -0.25 },
                margin: { l: 50, r: 20, t: 40, b: 60 },
              }}
              config={{ displayModeBar: false, responsive: true }}
              style={{ width: "100%" }}
            />
          </div>
        )}
      </div>

      {/* Detail table */}
      {detailPivot.tickers.length > 0 && (
        <div className="card">
          <div className="font-semibold text-sm mb-2">CapEx Detail (2024+)</div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs font-data">
              <thead className="border-b border-border text-text-muted">
                <tr>
                  <th className="text-left py-1.5 px-2">Ticker</th>
                  {detailPivot.qLabels.map(q => (
                    <th key={q} className="text-right py-1.5 px-2">{q}</th>
                  ))}
                  <th className="text-right py-1.5 px-2">FY Guidance</th>
                </tr>
              </thead>
              <tbody>
                {detailPivot.tickers.map(tk => (
                  <tr key={tk} className="border-b border-border/50 hover:bg-surface-alt">
                    <td className="py-1 px-2 font-semibold">{tk}</td>
                    {detailPivot.qLabels.map(q => (
                      <td key={q} className="py-1 px-2 text-right">
                        {detailPivot.grid[tk][q] != null ? `$${((detailPivot.grid[tk][q] as number) / 1e9).toFixed(1)}B` : ""}
                      </td>
                    ))}
                    <td className="py-1 px-2 text-right">
                      {snapMap.get(tk)?.capex_guidance != null ? `$${snapMap.get(tk)!.capex_guidance!.toFixed(1)}B` : ""}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

// ══════════════════════════════════════════════════════════════
// TAB 3 — VALUATION & RETURNS
// ══════════════════════════════════════════════════════════════

function ValuationTab({
  valuation,
  t,
  L,
}: {
  valuation: NonNullable<Awaited<ReturnType<typeof fetchSectorValuation>>>;
  t: ChartTheme;
  L: ReturnType<typeof getBaseLayout>;
}) {
  const val = valuation.valuation;
  if (val.length === 0) {
    return <div className="card text-center py-8 text-text-muted text-sm">Valuation data unavailable.</div>;
  }

  const pe = val.filter(v => v.forward_pe != null).sort((a, b) => (a.forward_pe ?? 0) - (b.forward_pe ?? 0));
  const ev = val.filter(v => v.ev_ebitda != null).sort((a, b) => (a.ev_ebitda ?? 0) - (b.ev_ebitda ?? 0));
  const dy = val.filter(v => v.dividend_yield != null).sort((a, b) => (a.dividend_yield ?? 0) - (b.dividend_yield ?? 0));
  const fcf = val.filter(v => v.fcf_yield != null).sort((a, b) => (a.fcf_yield ?? 0) - (b.fcf_yield ?? 0));
  const nd = val.filter(v => v.net_debt_ebitda != null).sort((a, b) => (a.net_debt_ebitda ?? 0) - (b.net_debt_ebitda ?? 0));
  const beta = val.filter(v => v.beta != null).sort((a, b) => (a.beta ?? 0) - (b.beta ?? 0));

  const barChart = (
    title: string,
    rows: SectorValuationRow[],
    xKey: keyof SectorValuationRow,
    format: (v: number) => string,
    colorize: (v: number) => string,
    xLabel: string,
    refLine?: number,
  ) => (
    <div className="card">
      <Plot
        data={[{
          type: "bar", orientation: "h",
          y: rows.map(r => r.ticker),
          x: rows.map(r => r[xKey] as number),
          marker: { color: rows.map(r => colorize(r[xKey] as number)) },
          text: rows.map(r => format(r[xKey] as number)),
          textposition: "outside",
        }]}
        layout={{
          ...L, height: CHART_HEIGHT.normal,
          title: { text: title, font: { size: 13, color: t.text } },
          xaxis: { title: { text: xLabel }, gridcolor: t.grid },
          yaxis: { gridcolor: t.grid },
          margin: { l: 50, r: 60, t: 40, b: 40 },
          shapes: refLine != null ? [{
            type: "line", x0: refLine, x1: refLine, y0: 0, y1: 1, yref: "paper",
            line: { color: t.muted, dash: "dash" },
          }] : [],
        }}
        config={{ displayModeBar: false, responsive: true }}
        style={{ width: "100%" }}
      />
    </div>
  );

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {barChart("Forward P/E", pe, "forward_pe", v => `${v.toFixed(1)}x`,
          v => v < 15 ? t.accent : v < 20 ? t.spot : t.loss, "P/E")}
        {barChart("EV / EBITDA", ev, "ev_ebitda", v => `${v.toFixed(1)}x`,
          v => v < 8 ? t.accent : v < 12 ? t.spot : t.loss, "EV/EBITDA")}
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {barChart("Dividend Yield (%)", dy, "dividend_yield", v => `${v.toFixed(1)}%`,
          () => t.gain, "%")}
        {barChart("Free Cash Flow Yield (%)", fcf, "fcf_yield", v => `${v.toFixed(1)}%`,
          () => t.accent, "%")}
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {barChart("Net Debt / EBITDA", nd, "net_debt_ebitda", v => `${v.toFixed(1)}x`,
          v => v < 1 ? t.accent : v < 2 ? t.spot : t.loss, "Leverage")}
        {barChart("Beta", beta, "beta", v => v.toFixed(2),
          v => v < 0.5 ? t.accent : v < 0.8 ? t.spot : t.loss, "Beta", 1.0)}
      </div>

      {/* Summary table */}
      <div className="card">
        <div className="font-semibold text-sm mb-2">Valuation Summary</div>
        <div className="overflow-x-auto">
          <table className="w-full text-xs font-data">
            <thead className="border-b border-border text-text-muted">
              <tr>
                {["Ticker", "Mkt Cap", "Fwd P/E", "EV/EBITDA", "P/B", "Div Yield", "FCF Yield", "Debt/EBITDA", "Payout", "Beta"].map(h => (
                  <th key={h} className="text-left py-1.5 px-2">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {val.map(r => (
                <tr key={r.ticker} className="border-b border-border/50 hover:bg-surface-alt">
                  <td className="py-1 px-2 font-semibold">{r.ticker}</td>
                  <td className="py-1 px-2">{r.market_cap != null ? `$${(r.market_cap / 1e9).toFixed(0)}B` : "—"}</td>
                  <td className="py-1 px-2">{fmtX(r.forward_pe)}</td>
                  <td className="py-1 px-2">{fmtX(r.ev_ebitda)}</td>
                  <td className="py-1 px-2">{fmtX(r.price_to_book)}</td>
                  <td className="py-1 px-2">{fmtPct(r.dividend_yield)}</td>
                  <td className="py-1 px-2">{fmtPct(r.fcf_yield)}</td>
                  <td className="py-1 px-2">{fmtX(r.net_debt_ebitda)}</td>
                  <td className="py-1 px-2">{r.payout_ratio != null ? `${(r.payout_ratio * 100).toFixed(0)}%` : "—"}</td>
                  <td className="py-1 px-2">{r.beta != null ? r.beta.toFixed(2) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <AIInterpretation
        page="sector-valuation"
        subject={valuation.etf}
        data={{
          etf: valuation.etf,
          valuation: valuation.valuation.slice(0, 12),
          momentum: valuation.momentum.slice(0, 12),
        }}
        buttonLabel={`Interpret ${valuation.etf} valuation`}
      />
    </div>
  );
}

// ══════════════════════════════════════════════════════════════
// TAB 4 — ALPHA SIGNALS
// ══════════════════════════════════════════════════════════════

function AlphaTab({
  cfg,
  valuation,
  alpha,
  t,
  L,
}: {
  cfg: SectorConfig;
  valuation: NonNullable<Awaited<ReturnType<typeof fetchSectorValuation>>>;
  alpha: NonNullable<Awaited<ReturnType<typeof fetchSectorAlpha>>>;
  t: ChartTheme;
  L: ReturnType<typeof getBaseLayout>;
}) {
  const tickerToSubsector = useMemo(() => {
    const m: Record<string, string> = {};
    for (const [ss, tks] of Object.entries(cfg.subsectors)) {
      for (const tk of tks) m[tk] = ss;
    }
    return m;
  }, [cfg.subsectors]);

  const subsectorColors = useMemo(() => {
    const m: Record<string, string> = {};
    Object.keys(cfg.subsectors).forEach((ss, i) => {
      m[ss] = SUBSECTOR_COLORS[i % SUBSECTOR_COLORS.length];
    });
    return m;
  }, [cfg.subsectors]);

  const rv = valuation.valuation.filter(v => v.forward_pe != null && v.fcf_yield != null);
  const sortedPe = [...rv].map(r => r.forward_pe!).sort((a, b) => a - b);
  const sortedFcf = [...rv].map(r => r.fcf_yield!).sort((a, b) => a - b);
  const medPe = quantile(sortedPe, 0.5);
  const medFcf = quantile(sortedFcf, 0.5);

  const momPeriods = ["1M", "3M", "6M", "12M"] as const;
  const [momPeriod, setMomPeriod] = useState<typeof momPeriods[number]>("3M");

  const mom = valuation.momentum;
  const momVal = (r: SectorMomentumRow, p: typeof momPeriods[number]): number | undefined => {
    return (r as unknown as Record<string, number | undefined>)[p];
  };
  const momSorted = useMemo(() => {
    const withAvg = mom.map(r => {
      const vs = momPeriods.map(p => momVal(r, p)).filter((v): v is number => Number.isFinite(v));
      return { ...r, _avg: vs.length ? vs.reduce((s, v) => s + v, 0) / vs.length : 0 };
    });
    return [...withAvg].sort((a, b) => b._avg - a._avg);
  }, [mom]);

  const momSelectedBars = useMemo(() => {
    const sub = mom
      .map(r => ({ ticker: r.ticker, v: momVal(r, momPeriod) }))
      .filter((r): r is { ticker: string; v: number } => Number.isFinite(r.v));
    sub.sort((a, b) => a.v - b.v);
    return sub;
  }, [mom, momPeriod]);

  const rev = alpha.eps_revisions.sort((a, b) => a.net_30d - b.net_30d);
  const ins = alpha.insider.sort((a, b) => a.net_value - b.net_value);

  return (
    <div className="space-y-4">
      <div className="card">
        <div className="font-semibold text-sm">Relative Value Map</div>
        <div className="text-xs text-text-muted mb-2">
          FCF yield vs forward P/E — top-left quadrant is cheap + cash-generative.
        </div>
        {rv.length > 0 ? (
          <Plot
            data={Object.keys(cfg.subsectors).map(ss => {
              const sub = rv.filter(r => tickerToSubsector[r.ticker] === ss);
              return {
                x: sub.map(r => r.forward_pe), y: sub.map(r => r.fcf_yield),
                mode: "markers+text", type: "scatter", name: ss,
                marker: { size: 16, color: subsectorColors[ss] },
                text: sub.map(r => r.ticker), textposition: "top center",
                textfont: { size: 11, color: t.text },
                hovertemplate: "%{text}<br>Fwd P/E: %{x:.1f}x<br>FCF Yield: %{y:.1f}%<extra></extra>",
              };
            })}
            layout={{
              ...L, height: CHART_HEIGHT.tall,
              title: { text: "Relative Value: FCF Yield vs Forward P/E", font: { size: 14, color: t.text } },
              xaxis: { title: { text: "Forward P/E (lower = cheaper)" }, gridcolor: t.grid },
              yaxis: { title: { text: "FCF Yield %" }, gridcolor: t.grid },
              legend: { orientation: "h", y: -0.15 },
              margin: { l: 60, r: 20, t: 40, b: 60 },
              shapes: [
                { type: "line", x0: medPe, x1: medPe, y0: 0, y1: 1, yref: "paper", line: { color: t.muted, dash: "dash" } },
                { type: "line", y0: medFcf, y1: medFcf, x0: 0, x1: 1, xref: "paper", line: { color: t.muted, dash: "dash" } },
              ],
              annotations: [
                { x: 0.02, y: 0.98, xref: "paper", yref: "paper", text: "CHEAP + HIGH CASH", showarrow: false, font: { color: t.gain, size: 10 } },
                { x: 0.98, y: 0.02, xref: "paper", yref: "paper", text: "EXPENSIVE + LOW CASH", showarrow: false, font: { color: t.loss, size: 10 }, xanchor: "right" },
              ],
            }}
            config={{ displayModeBar: false, responsive: true }}
            style={{ width: "100%" }}
          />
        ) : (
          <div className="text-xs text-text-muted py-4">Insufficient valuation data.</div>
        )}
      </div>

      {/* Momentum heatmap */}
      {momSorted.length > 0 && (
        <div className="card">
          <Plot
            data={[{
              ...heatmapTrace(t, "divergent", { colorbarTitle: "Return %" }),
              z: momSorted.map(r => momPeriods.map(p => momVal(r, p) ?? null)),
              x: [...momPeriods],
              y: momSorted.map(r => r.ticker),
              zmid: 0,
              text: momSorted.map(r => momPeriods.map(p => {
                const v = momVal(r, p);
                return Number.isFinite(v) ? `${(v as number) >= 0 ? "+" : ""}${(v as number).toFixed(1)}%` : "";
              })),
            }]}
            layout={{
              ...L, height: heatmapHeight(momSorted.length),
              title: { text: "Momentum Heatmap (sorted by avg)", font: { size: 14, color: t.text } },
              margin: { l: 60, r: 40, t: 40, b: 40 },
              yaxis: { autorange: "reversed", gridcolor: t.grid },
              xaxis: { gridcolor: t.grid },
            }}
            config={{ displayModeBar: false, responsive: true }}
            style={{ width: "100%" }}
          />
        </div>
      )}

      {momSelectedBars.length > 0 && (
        <div className="card">
          <div className="flex gap-1 mb-2">
            {momPeriods.map(p => (
              <button key={p} onClick={() => setMomPeriod(p)}
                className={`px-2 py-1 text-xs rounded ${momPeriod === p ? "bg-accent text-white" : "text-text-muted hover:text-text hover:bg-surface-alt"}`}>
                {p}
              </button>
            ))}
          </div>
          <Plot
            data={[{
              type: "bar", orientation: "h",
              y: momSelectedBars.map(r => r.ticker),
              x: momSelectedBars.map(r => r.v),
              marker: { color: momSelectedBars.map(r => colorByMagnitude(r.v, t.accent, t.loss)) },
              text: momSelectedBars.map(r => `${r.v >= 0 ? "+" : ""}${r.v.toFixed(1)}%`),
              textposition: "outside",
            }]}
            layout={{
              ...L, height: CHART_HEIGHT.normal,
              title: { text: `${momPeriod} Price Return (%)`, font: { size: 13, color: t.text } },
              xaxis: { title: { text: "Return %" }, gridcolor: t.grid },
              yaxis: { gridcolor: t.grid },
              margin: { l: 50, r: 60, t: 40, b: 40 },
              shapes: [{ type: "line", x0: 0, x1: 0, y0: 0, y1: 1, yref: "paper", line: { color: t.muted, dash: "dash" } }],
            }}
            config={{ displayModeBar: false, responsive: true }}
            style={{ width: "100%" }}
          />
        </div>
      )}

      {/* EPS revisions */}
      {rev.length > 0 && (
        <div className="card">
          <div className="font-semibold text-sm">Analyst Estimate Revisions</div>
          <div className="text-xs text-text-muted mb-2">
            EPS revision direction in last 30 days — revisions predict returns better than the estimates themselves.
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <Plot
              data={[{
                type: "bar", orientation: "h",
                y: rev.map(r => r.ticker), x: rev.map(r => r.net_30d),
                marker: { color: rev.map(r => colorByMagnitude(r.net_30d, t.accent, t.loss)) },
                text: rev.map(r => `${r.net_30d >= 0 ? "+" : ""}${r.net_30d}`),
                textposition: "outside",
              }]}
              layout={{
                ...L, height: CHART_HEIGHT.normal,
                title: { text: "Net EPS Revisions (30 Days)", font: { size: 13, color: t.text } },
                xaxis: { title: { text: "Up - Down" }, gridcolor: t.grid },
                yaxis: { gridcolor: t.grid },
                margin: { l: 50, r: 60, t: 40, b: 40 },
                shapes: [{ type: "line", x0: 0, x1: 0, y0: 0, y1: 1, yref: "paper", line: { color: t.muted, dash: "dash" } }],
              }}
              config={{ displayModeBar: false, responsive: true }}
              style={{ width: "100%" }}
            />
            <Plot
              data={[
                {
                  type: "bar", orientation: "h", name: "Upgrades",
                  y: rev.map(r => r.ticker), x: rev.map(r => r.up_30d),
                  marker: { color: t.accent },
                },
                {
                  type: "bar", orientation: "h", name: "Downgrades",
                  y: rev.map(r => r.ticker), x: rev.map(r => -r.down_30d),
                  marker: { color: t.loss },
                },
              ]}
              layout={{
                ...L, height: CHART_HEIGHT.normal, barmode: "relative",
                title: { text: "EPS Revision Breakdown (30 Days)", font: { size: 13, color: t.text } },
                xaxis: { title: { text: "Analysts" }, gridcolor: t.grid },
                yaxis: { gridcolor: t.grid },
                legend: { orientation: "h", y: -0.2 },
                margin: { l: 50, r: 20, t: 40, b: 60 },
              }}
              config={{ displayModeBar: false, responsive: true }}
              style={{ width: "100%" }}
            />
          </div>
        </div>
      )}

      {/* Insider */}
      {ins.length > 0 && (
        <div className="card">
          <div className="font-semibold text-sm">Insider Activity (90 Days)</div>
          <div className="text-xs text-text-muted mb-2">
            Net insider buying/selling — insider buying is a strong bullish signal.
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <Plot
              data={[{
                type: "bar", orientation: "h",
                y: ins.map(r => r.ticker), x: ins.map(r => r.net_value / 1e6),
                marker: { color: ins.map(r => colorByMagnitude(r.net_value, t.accent, t.loss)) },
                text: ins.map(r => `$${(r.net_value / 1e6 >= 0 ? "+" : "")}${(r.net_value / 1e6).toFixed(1)}M`),
                textposition: "outside",
              }]}
              layout={{
                ...L, height: CHART_HEIGHT.normal,
                title: { text: "Net Insider Value ($M, 90 Days)", font: { size: 13, color: t.text } },
                xaxis: { title: { text: "$M (Buys - Sells)" }, gridcolor: t.grid },
                yaxis: { gridcolor: t.grid },
                margin: { l: 50, r: 80, t: 40, b: 40 },
                shapes: [{ type: "line", x0: 0, x1: 0, y0: 0, y1: 1, yref: "paper", line: { color: t.muted, dash: "dash" } }],
              }}
              config={{ displayModeBar: false, responsive: true }}
              style={{ width: "100%" }}
            />
            <Plot
              data={[
                { type: "bar", orientation: "h", name: "Buys", y: ins.map(r => r.ticker), x: ins.map(r => r.buy_count), marker: { color: t.accent } },
                { type: "bar", orientation: "h", name: "Sells", y: ins.map(r => r.ticker), x: ins.map(r => -r.sell_count), marker: { color: t.loss } },
              ]}
              layout={{
                ...L, height: CHART_HEIGHT.normal, barmode: "relative",
                title: { text: "Insider Transaction Count (90 Days)", font: { size: 13, color: t.text } },
                xaxis: { title: { text: "Transactions" }, gridcolor: t.grid },
                yaxis: { gridcolor: t.grid },
                legend: { orientation: "h", y: -0.2 },
                margin: { l: 50, r: 20, t: 40, b: 60 },
              }}
              config={{ displayModeBar: false, responsive: true }}
              style={{ width: "100%" }}
            />
          </div>
        </div>
      )}

      <AIInterpretation
        page="sector-alpha"
        subject={cfg.etf}
        data={{
          etf: cfg.etf,
          eps_revisions: alpha.eps_revisions.slice(0, 10),
          insider: alpha.insider.slice(0, 10),
          momentum: valuation.momentum.slice(0, 10),
        }}
        buttonLabel={`Interpret ${cfg.etf} signals`}
      />
    </div>
  );
}

// ══════════════════════════════════════════════════════════════
// TAB 5 — RISK ANALYTICS
// ══════════════════════════════════════════════════════════════

function RiskTab({
  cfg,
  prices,
  t,
  L,
}: {
  cfg: SectorConfig;
  prices: NonNullable<Awaited<ReturnType<typeof fetchSectorPrices>>>;
  t: ChartTheme;
  L: ReturnType<typeof getBaseLayout>;
}) {
  const sectorTickers = useMemo(() => Object.keys(cfg.companies), [cfg.companies]);
  // Align sector tickers + SPY + factor proxies on a single common date set so
  // returns/closes for all of them line up day-by-day. Separate alignment per
  // ticker set previously caused date mismatches in the SPY comparison and
  // factor regression.
  const aligned = useMemo(() => {
    const union = Array.from(new Set([...sectorTickers, "SPY", ...cfg.factor_proxies]));
    return alignedReturns(prices.prices, union);
  }, [prices.prices, sectorTickers, cfg.factor_proxies]);
  const [ddTicker, setDdTicker] = useState<string>(sectorTickers.filter(tk => prices.prices[tk]?.length > 30)[0] ?? sectorTickers[0]);

  // Per-ticker drawdown
  const ddSelected = useMemo(() => {
    const arr = prices.prices[ddTicker] ?? [];
    if (arr.length === 0) return { x: [] as string[], y: [] as number[], maxDd: 0, maxDdDate: "" };
    let peak = arr[0].close;
    const y: number[] = [];
    const x: string[] = [];
    let maxDd = 0;
    let maxDdDate = "";
    for (const p of arr) {
      peak = Math.max(peak, p.close);
      const dd = ((p.close - peak) / peak) * 100;
      y.push(dd);
      x.push(p.date);
      if (dd < maxDd) { maxDd = dd; maxDdDate = p.date; }
    }
    return { x, y, maxDd, maxDdDate };
  }, [prices.prices, ddTicker]);

  // Max drawdown comparison
  const ddComparison = useMemo(() => {
    const rows: { ticker: string; max_dd: number }[] = [];
    for (const tk of sectorTickers) {
      const arr = prices.prices[tk];
      if (!arr || arr.length === 0) continue;
      let peak = arr[0].close;
      let min = 0;
      for (const p of arr) {
        peak = Math.max(peak, p.close);
        const dd = ((p.close - peak) / peak) * 100;
        if (dd < min) min = dd;
      }
      rows.push({ ticker: tk, max_dd: min });
    }
    rows.sort((a, b) => a.max_dd - b.max_dd);
    return rows;
  }, [prices.prices, sectorTickers]);

  // VaR / Vol
  const varRows = useMemo(() => {
    return sectorTickers.map(tk => {
      const r = aligned.returns[tk] ?? [];
      if (r.length < 10) return null;
      const mu = mean(r);
      const sigma = stdev(r);
      const sorted = [...r].sort((a, b) => a - b);
      const var95 = -quantile(sorted, 0.05) * 100;
      const var99 = -quantile(sorted, 0.01) * 100;
      return {
        ticker: tk,
        daily_vol: sigma * 100,
        annual_vol: sigma * Math.sqrt(252) * 100,
        var_95_hist: var95,
        var_99_hist: var99,
        var_95_param: -(mu + 1.645 * sigma) * 100,
        var_99_param: -(mu + 2.326 * sigma) * 100,
      };
    }).filter((x): x is NonNullable<typeof x> => x != null);
  }, [aligned.returns, sectorTickers]);

  const varByHist = useMemo(() => [...varRows].sort((a, b) => a.var_95_hist - b.var_95_hist), [varRows]);
  const annualVol = useMemo(() => [...varRows].sort((a, b) => a.annual_vol - b.annual_vol), [varRows]);

  // Sharpe/Sortino
  const riskRows = useMemo(() => {
    return sectorTickers.map(tk => {
      const r = aligned.returns[tk] ?? [];
      if (r.length < 10) return null;
      const annRet = mean(r) * 252 * 100;
      const annVol = stdev(r) * Math.sqrt(252) * 100;
      const neg = r.filter(v => v < 0);
      const downside = neg.length > 1 ? stdev(neg) * Math.sqrt(252) * 100 : 0;
      return {
        ticker: tk,
        ann_return: annRet, ann_vol: annVol,
        sharpe: annVol > 0 ? annRet / annVol : 0,
        sortino: downside > 0 ? annRet / downside : 0,
      };
    }).filter((x): x is NonNullable<typeof x> => x != null);
  }, [aligned.returns, sectorTickers]);

  const sharpeSort = [...riskRows].sort((a, b) => a.sharpe - b.sharpe);
  const sortinoSort = [...riskRows].sort((a, b) => a.sortino - b.sortino);

  // Sector vs SPY — SPY and sector tickers now share the same date set, so
  // compounded index values align by date without any post-hoc trimming.
  const sectorVsSpy = useMemo(() => {
    const spyCloses = aligned.closes["SPY"];
    const dates = aligned.dates;
    if (!spyCloses || spyCloses.length < 2 || dates.length < 2) return null;

    const sectorIdx = [100];
    for (let i = 1; i < dates.length; i++) {
      let sum = 0, n = 0;
      for (const tk of sectorTickers) {
        const c = aligned.closes[tk];
        if (!c || c[i - 1] == null || c[i] == null || c[i - 1] === 0) continue;
        sum += (c[i] - c[i - 1]) / c[i - 1];
        n++;
      }
      sectorIdx.push(sectorIdx[i - 1] * (1 + (n > 0 ? sum / n : 0)));
    }

    const spyIdx = [100];
    for (let i = 1; i < spyCloses.length; i++) {
      spyIdx.push(spyIdx[i - 1] * (spyCloses[i] / spyCloses[i - 1]));
    }

    const rel = sectorIdx.map((v, i) => v - spyIdx[i]);
    return { xs: dates, sIdx: sectorIdx, spyIdx, rel };
  }, [aligned, sectorTickers]);

  // Sub-sector decomposition
  const subsectorDecomp = useMemo(() => {
    const result: { name: string; xs: string[]; idx: number[]; color: string }[] = [];
    Object.entries(cfg.subsectors).forEach(([name, tks], i) => {
      const avail = tks.filter(tk => aligned.closes[tk] && aligned.closes[tk].length > 30);
      if (avail.length === 0) return;
      const len = aligned.dates.length;
      const idx = [100];
      for (let d = 1; d < len; d++) {
        let sum = 0, n = 0;
        for (const tk of avail) {
          const c = aligned.closes[tk];
          if (c[d] == null || c[d - 1] == null || c[d - 1] === 0) continue;
          sum += (c[d] - c[d - 1]) / c[d - 1];
          n++;
        }
        idx.push(idx[idx.length - 1] * (1 + (n > 0 ? sum / n : 0)));
      }
      result.push({ name, xs: aligned.dates.slice(0, idx.length), idx, color: SUBSECTOR_COLORS[i % SUBSECTOR_COLORS.length] });
    });
    return result;
  }, [aligned, cfg.subsectors]);

  // Factor regression — sector + factor returns now share the same date grid
  // via `aligned`, so lengths line up by date (no more date mismatch).
  const factors = useMemo(() => {
    const factorNames = cfg.factor_proxies.filter(f => (aligned.returns[f]?.length ?? 0) > 30);
    if (factorNames.length === 0) return null;
    const betas: { ticker: string; alpha: number; factors: Record<string, number>; r2: number }[] = [];
    for (const tk of sectorTickers) {
      const y = aligned.returns[tk];
      if (!y || y.length < 60) continue;
      const X = factorNames.map(f => aligned.returns[f]);
      if (X.some(col => col.length !== y.length)) continue;
      const n = y.length;
      const cols = factorNames.length + 1;
      const Xm: number[][] = [];
      for (let i = 0; i < n; i++) Xm.push([1, ...X.map(col => col[i])]);
      const XtX = Array.from({ length: cols }, () => Array(cols).fill(0));
      const Xty = Array(cols).fill(0);
      for (let i = 0; i < n; i++) {
        for (let a = 0; a < cols; a++) {
          Xty[a] += Xm[i][a] * y[i];
          for (let b = 0; b < cols; b++) XtX[a][b] += Xm[i][a] * Xm[i][b];
        }
      }
      const beta = solveLinear(XtX, Xty);
      if (!beta) continue;
      const yHat = Xm.map(row => row.reduce((s, v, j) => s + v * beta[j], 0));
      const ssRes = y.reduce((s, v, i) => s + (v - yHat[i]) ** 2, 0);
      const yMean = mean(y);
      const ssTot = y.reduce((s, v) => s + (v - yMean) ** 2, 0);
      const r2 = ssTot > 0 ? 1 - ssRes / ssTot : 0;
      const factorsRow: Record<string, number> = {};
      factorNames.forEach((f, i) => { factorsRow[f] = beta[i + 1]; });
      betas.push({ ticker: tk, alpha: beta[0] * 252 * 100, factors: factorsRow, r2 });
    }
    return { factorNames, betas };
  }, [aligned, sectorTickers, cfg.factor_proxies]);

  return (
    <div className="space-y-4">
      <div className="card">
        <div className="font-semibold text-sm">Maximum Drawdown</div>
        <div className="flex items-center gap-2 mt-2 mb-2">
          <label className="text-xs text-text-muted">Ticker:</label>
          <select value={ddTicker} onChange={e => setDdTicker(e.target.value)}
            className="px-2 py-1 text-xs border border-border rounded bg-surface font-data">
            {sectorTickers.filter(tk => (prices.prices[tk]?.length ?? 0) > 30).map(tk => (
              <option key={tk} value={tk}>{tk}</option>
            ))}
          </select>
        </div>
        {ddSelected.x.length > 0 && (
          <Plot
            data={[{
              x: ddSelected.x, y: ddSelected.y, type: "scatter", mode: "lines",
              line: { color: t.loss, width: 2 },
              fill: "tozeroy", fillcolor: "rgba(248,81,73,0.1)",
              hovertemplate: "%{x}<br>Drawdown: %{y:.1f}%<extra></extra>",
            }]}
            layout={{
              ...L, height: CHART_HEIGHT.normal,
              title: { text: `${ddTicker} — Drawdown from Peak (%)`, font: { size: 13, color: t.text } },
              yaxis: { title: { text: "Drawdown %" }, gridcolor: t.grid },
              xaxis: { gridcolor: t.grid },
              margin: { l: 60, r: 20, t: 40, b: 40 },
              annotations: [{
                x: ddSelected.maxDdDate, y: ddSelected.maxDd,
                text: `Max: ${ddSelected.maxDd.toFixed(1)}%`,
                showarrow: true, arrowhead: 2, arrowcolor: t.loss,
                font: { color: t.loss },
              }],
            }}
            config={{ displayModeBar: false, responsive: true }}
            style={{ width: "100%" }}
          />
        )}
      </div>

      {ddComparison.length > 0 && (
        <div className="card">
          <Plot
            data={[{
              type: "bar", orientation: "h",
              y: ddComparison.map(r => r.ticker),
              x: ddComparison.map(r => r.max_dd),
              marker: { color: t.loss },
              text: ddComparison.map(r => `${r.max_dd.toFixed(1)}%`),
              textposition: "outside",
            }]}
            layout={{
              ...L, height: CHART_HEIGHT.normal,
              title: { text: "Max Drawdown Comparison (2Y)", font: { size: 13, color: t.text } },
              xaxis: { title: { text: "Max Drawdown %" }, gridcolor: t.grid },
              yaxis: { gridcolor: t.grid },
              margin: { l: 50, r: 60, t: 40, b: 40 },
            }}
            config={{ displayModeBar: false, responsive: true }}
            style={{ width: "100%" }}
          />
        </div>
      )}

      {/* VaR / Vol */}
      {varByHist.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div className="card">
            <Plot
              data={[
                { type: "bar", orientation: "h", name: "95% VaR", y: varByHist.map(r => r.ticker), x: varByHist.map(r => r.var_95_hist), marker: { color: t.spot }, text: varByHist.map(r => `${r.var_95_hist.toFixed(1)}%`), textposition: "outside" },
                { type: "bar", orientation: "h", name: "99% VaR", y: varByHist.map(r => r.ticker), x: varByHist.map(r => r.var_99_hist), marker: { color: t.loss }, text: varByHist.map(r => `${r.var_99_hist.toFixed(1)}%`), textposition: "outside" },
              ]}
              layout={{
                ...L, height: CHART_HEIGHT.normal, barmode: "group",
                title: { text: "Historical VaR (Daily Loss %)", font: { size: 13, color: t.text } },
                xaxis: { title: { text: "% Loss" }, gridcolor: t.grid },
                yaxis: { gridcolor: t.grid },
                legend: { orientation: "h", y: -0.18 },
                margin: { l: 50, r: 50, t: 40, b: 60 },
              }}
              config={{ displayModeBar: false, responsive: true }}
              style={{ width: "100%" }}
            />
          </div>
          <div className="card">
            <Plot
              data={[{
                type: "bar", orientation: "h",
                y: annualVol.map(r => r.ticker), x: annualVol.map(r => r.annual_vol),
                marker: { color: t.accent },
                text: annualVol.map(r => `${r.annual_vol.toFixed(0)}%`), textposition: "outside",
              }]}
              layout={{
                ...L, height: CHART_HEIGHT.normal,
                title: { text: "Annualized Volatility (%)", font: { size: 13, color: t.text } },
                xaxis: { title: { text: "Vol %" }, gridcolor: t.grid },
                yaxis: { gridcolor: t.grid },
                margin: { l: 50, r: 60, t: 40, b: 40 },
              }}
              config={{ displayModeBar: false, responsive: true }}
              style={{ width: "100%" }}
            />
          </div>
        </div>
      )}

      {/* Sharpe/Sortino */}
      {riskRows.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div className="card">
            <Plot
              data={[{
                type: "bar", orientation: "h",
                y: sharpeSort.map(r => r.ticker), x: sharpeSort.map(r => r.sharpe),
                marker: { color: sharpeSort.map(r => r.sharpe > 0 ? t.accent : t.loss) },
                text: sharpeSort.map(r => r.sharpe.toFixed(2)), textposition: "outside",
              }]}
              layout={{
                ...L, height: CHART_HEIGHT.normal,
                title: { text: "Sharpe Ratio (2Y)", font: { size: 13, color: t.text } },
                xaxis: { title: { text: "Sharpe" }, gridcolor: t.grid },
                yaxis: { gridcolor: t.grid },
                margin: { l: 50, r: 60, t: 40, b: 40 },
                shapes: [{ type: "line", x0: 0, x1: 0, y0: 0, y1: 1, yref: "paper", line: { color: t.muted, dash: "dash" } }],
              }}
              config={{ displayModeBar: false, responsive: true }}
              style={{ width: "100%" }}
            />
          </div>
          <div className="card">
            <Plot
              data={[{
                type: "bar", orientation: "h",
                y: sortinoSort.map(r => r.ticker), x: sortinoSort.map(r => r.sortino),
                marker: { color: sortinoSort.map(r => r.sortino > 0 ? t.accent : t.loss) },
                text: sortinoSort.map(r => r.sortino.toFixed(2)), textposition: "outside",
              }]}
              layout={{
                ...L, height: CHART_HEIGHT.normal,
                title: { text: "Sortino Ratio (2Y)", font: { size: 13, color: t.text } },
                xaxis: { title: { text: "Sortino" }, gridcolor: t.grid },
                yaxis: { gridcolor: t.grid },
                margin: { l: 50, r: 60, t: 40, b: 40 },
                shapes: [{ type: "line", x0: 0, x1: 0, y0: 0, y1: 1, yref: "paper", line: { color: t.muted, dash: "dash" } }],
              }}
              config={{ displayModeBar: false, responsive: true }}
              style={{ width: "100%" }}
            />
          </div>
        </div>
      )}

      {/* Sector vs SPY */}
      {sectorVsSpy && (
        <>
          <div className="card">
            <Plot
              data={[
                { x: sectorVsSpy.xs, y: sectorVsSpy.sIdx, type: "scatter", mode: "lines", name: `${cfg.etf} (Equal-Wt)`, line: { color: t.accent, width: 2 } },
                { x: sectorVsSpy.xs, y: sectorVsSpy.spyIdx, type: "scatter", mode: "lines", name: "S&P 500", line: { color: t.spot, width: 2 } },
              ]}
              layout={{
                ...L, height: CHART_HEIGHT.tall,
                title: { text: `Cumulative Performance: ${cfg.etf} vs S&P 500 (2Y, base=100)`, font: { size: 14, color: t.text } },
                yaxis: { title: { text: "Indexed (100 = start)" }, gridcolor: t.grid },
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
            <Plot
              data={[{
                x: sectorVsSpy.xs, y: sectorVsSpy.rel, type: "scatter", mode: "lines",
                line: { color: t.accent, width: 2 },
                fill: "tozeroy", fillcolor: "rgba(88,166,255,0.08)",
              }]}
              layout={{
                ...L, height: CHART_HEIGHT.compact,
                title: { text: `Relative Performance (${cfg.etf} − SPY)`, font: { size: 13, color: t.text } },
                yaxis: { title: { text: "Excess Return (pts)" }, gridcolor: t.grid },
                xaxis: { gridcolor: t.grid },
                margin: { l: 60, r: 20, t: 40, b: 40 },
                shapes: [{ type: "line", y0: 0, y1: 0, x0: 0, x1: 1, xref: "paper", line: { color: t.muted, dash: "dash" } }],
              }}
              config={{ displayModeBar: false, responsive: true }}
              style={{ width: "100%" }}
            />
          </div>
        </>
      )}

      {/* Sub-sector decomposition */}
      {subsectorDecomp.length > 0 && (
        <div className="card">
          <Plot
            data={subsectorDecomp.map(s => ({
              x: s.xs, y: s.idx, type: "scatter", mode: "lines", name: s.name,
              line: { width: 3, color: s.color },
            }))}
            layout={{
              ...L, height: CHART_HEIGHT.tall,
              title: { text: "Sub-sector Cumulative Performance (2Y, base=100)", font: { size: 14, color: t.text } },
              yaxis: { title: { text: "Indexed" }, gridcolor: t.grid },
              xaxis: { gridcolor: t.grid },
              legend: { orientation: "h", y: -0.15 },
              margin: { l: 60, r: 20, t: 40, b: 60 },
              shapes: [{ type: "line", y0: 100, y1: 100, x0: 0, x1: 1, xref: "paper", line: { color: t.muted, dash: "dash" } }],
            }}
            config={{ displayModeBar: false, responsive: true }}
            style={{ width: "100%" }}
          />
        </div>
      )}

      {/* Factor exposure */}
      {factors && factors.betas.length > 0 && (
        <>
          <div className="card">
            <div className="font-semibold text-sm">Factor Exposure</div>
            <div className="text-xs text-text-muted mb-2">
              Regression of each stock's daily returns on factor proxies: {factors.factorNames.join(", ")}.
            </div>
            <Plot
              data={[{
                ...heatmapTrace(t, "divergent", { colorbarTitle: "Beta" }),
                z: factors.betas.map(r => factors.factorNames.map(f => r.factors[f])),
                x: factors.factorNames,
                y: factors.betas.map(r => r.ticker),
                zmid: 0,
                text: factors.betas.map(r => factors.factorNames.map(f => r.factors[f].toFixed(2))),
              }]}
              layout={{
                ...L, height: heatmapHeight(factors.betas.length),
                title: { text: `Factor Betas`, font: { size: 13, color: t.text } },
                margin: { l: 60, r: 40, t: 40, b: 40 },
                yaxis: { gridcolor: t.grid, autorange: "reversed" },
                xaxis: { gridcolor: t.grid },
              }}
              config={{ displayModeBar: false, responsive: true }}
              style={{ width: "100%" }}
            />
          </div>
          <div className="card">
            <div className="font-semibold text-sm mb-2">Factor Exposure Detail</div>
            <div className="overflow-x-auto">
              <table className="w-full text-xs font-data">
                <thead className="border-b border-border text-text-muted">
                  <tr>
                    <th className="text-left py-1.5 px-2">Ticker</th>
                    <th className="text-right py-1.5 px-2">Alpha (ann)</th>
                    {factors.factorNames.map(f => (
                      <th key={f} className="text-right py-1.5 px-2">β {f}</th>
                    ))}
                    <th className="text-right py-1.5 px-2">R²</th>
                  </tr>
                </thead>
                <tbody>
                  {factors.betas.map(r => (
                    <tr key={r.ticker} className="border-b border-border/50 hover:bg-surface-alt">
                      <td className="py-1 px-2 font-semibold">{r.ticker}</td>
                      <td className="py-1 px-2 text-right">{fmtPctSigned(r.alpha)}</td>
                      {factors.factorNames.map(f => (
                        <td key={f} className="py-1 px-2 text-right">{r.factors[f].toFixed(2)}</td>
                      ))}
                      <td className="py-1 px-2 text-right">{r.r2.toFixed(2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function solveLinear(A: number[][], b: number[]): number[] | null {
  const n = A.length;
  const M = A.map((row, i) => [...row, b[i]]);
  for (let i = 0; i < n; i++) {
    let piv = i;
    for (let r = i + 1; r < n; r++) if (Math.abs(M[r][i]) > Math.abs(M[piv][i])) piv = r;
    if (piv !== i) [M[i], M[piv]] = [M[piv], M[i]];
    const d = M[i][i];
    if (Math.abs(d) < 1e-12) return null;
    for (let j = i; j <= n; j++) M[i][j] /= d;
    for (let r = 0; r < n; r++) {
      if (r === i) continue;
      const f = M[r][i];
      for (let j = i; j <= n; j++) M[r][j] -= f * M[i][j];
    }
  }
  return M.map(row => row[n]);
}

// ══════════════════════════════════════════════════════════════
// TAB 6 — GUIDANCE
// ══════════════════════════════════════════════════════════════

function GuidanceTab({
  cfg,
  guidance,
  t,
  L,
}: {
  cfg: SectorConfig;
  guidance: NonNullable<Awaited<ReturnType<typeof fetchSectorGuidance>>>;
  t: ChartTheme;
  L: ReturnType<typeof getBaseLayout>;
}) {
  const snap = cfg.guidance_snapshot;
  const live = guidance.live_estimates;

  const daysOld = useMemo(() => {
    try {
      const snapDate = new Date(snap.date);
      const now = new Date();
      return Math.floor((now.getTime() - snapDate.getTime()) / (1000 * 60 * 60 * 24));
    } catch {
      return 0;
    }
  }, [snap.date]);

  const guidanceRows = useMemo(() => snap.data.map(d => {
    const lv = live[d.ticker] ?? {};
    return {
      ...d,
      live_target: lv.price_target ?? null,
      live_fwd_pe: lv.fwd_pe ?? null,
      live_rating: (lv.rating && lv.rating !== "None") ? lv.rating : null,
      live_fwd_eps: lv.fwd_eps ?? null,
      live_rev_growth: lv.rev_growth ?? null,
    };
  }), [snap.data, live]);

  const surp = guidance.earnings_surprises;
  const surpPivot = useMemo(() => {
    if (surp.length === 0) return null;
    const quarters = Array.from(new Set(surp.map(r => r.quarter))).sort().slice(-8);
    const tickers = Array.from(new Set(surp.map(r => r.ticker))).sort();
    const z = tickers.map(tk => quarters.map(q => {
      const r = surp.find(x => x.ticker === tk && x.quarter === q);
      return r && r.surprise_pct != null ? (r.surprise_pct as number) * 100 : null;
    }));
    return { quarters, tickers, z };
  }, [surp]);

  return (
    <div className="space-y-4">
      <div className="card">
        <div className="font-semibold text-sm">Guidance & Live Consensus</div>
        <div className="text-xs text-text-muted mb-2">
          Earnings call guidance (scraped {snap.date}) + live Wall Street consensus.
        </div>
        {daysOld > 90 && (
          <div className="text-xs mb-2 px-2 py-1 rounded bg-loss/15 text-loss">
            Guidance snapshot is <b>{daysOld} days old</b>. Data may not reflect recent earnings updates.
          </div>
        )}
        {daysOld > 30 && daysOld <= 90 && (
          <div className="text-xs mb-2 text-text-muted">
            Snapshot is {daysOld} days old — consider refreshing after next earnings season.
          </div>
        )}
        <div className="overflow-x-auto">
          <table className="w-full text-xs font-data">
            <thead className="border-b border-border text-text-muted">
              <tr>
                {["Ticker", "Company", "Rev Est (Y)", "Rev Growth", "EPS (Y)", "EPS (NY)", "Rating", "Target", "Fwd P/E", "CapEx", "Prod."].map(h => (
                  <th key={h} className="text-left py-1.5 px-2">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {guidanceRows.map(d => {
                const target = d.live_target ?? d.price_target;
                const fwd_pe = d.live_fwd_pe ?? d.fwd_pe;
                const rating = d.live_rating ?? d.rating;
                const eps_y = d.live_fwd_eps ?? d.eps_est_y;
                const rev_growth_str = d.live_rev_growth != null
                  ? `${(d.live_rev_growth * 100) >= 0 ? "+" : ""}${(d.live_rev_growth * 100).toFixed(0)}%`
                  : d.rev_growth;
                return (
                  <tr key={d.ticker} className="border-b border-border/50 hover:bg-surface-alt">
                    <td className="py-1 px-2 font-semibold">{d.ticker}</td>
                    <td className="py-1 px-2">{d.company}</td>
                    <td className="py-1 px-2">${d.rev_est_y.toFixed(0)}B</td>
                    <td className="py-1 px-2">{rev_growth_str}</td>
                    <td className="py-1 px-2">${eps_y.toFixed(2)}</td>
                    <td className="py-1 px-2">${d.eps_est_ny.toFixed(2)}</td>
                    <td className="py-1 px-2">{rating}</td>
                    <td className="py-1 px-2">{target ? `$${target.toLocaleString(undefined, { maximumFractionDigits: 0 })}` : "—"}</td>
                    <td className="py-1 px-2">{fwd_pe ? fwd_pe.toFixed(1) : "—"}</td>
                    <td className="py-1 px-2">{d.capex_guidance ? `$${d.capex_guidance.toFixed(1)}B` : ""}</td>
                    <td className="py-1 px-2">{d.production ?? ""}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* Outlook expander */}
      <details className="card">
        <summary className="cursor-pointer text-sm font-semibold">Company Outlook & Guidance Notes</summary>
        <div className="mt-3 space-y-3">
          {snap.data.map(d => (
            <div key={d.ticker} className="border-b border-border/50 pb-3 last:border-0">
              <div className="font-semibold text-sm">{d.ticker} — {d.company}</div>
              <div className="text-xs text-text-muted mt-1 flex flex-wrap gap-3">
                {d.capex_guidance != null && <span><b>CapEx:</b> ${d.capex_guidance.toFixed(1)}B</span>}
                {d.production && <span><b>Production:</b> {d.production}</span>}
                <span><b>Rev Est:</b> ${d.rev_est_y.toFixed(0)}B ({d.rev_growth} YoY)</span>
                <span><b>EPS:</b> ${d.eps_est_y.toFixed(2)}</span>
                <span><b>Rating:</b> {d.rating}</span>
              </div>
              {d.capex_note && <div className="text-xs italic text-text-muted mt-1">{d.capex_note}</div>}
              {d.outlook && <div className="text-xs mt-1">{d.outlook}</div>}
            </div>
          ))}
        </div>
      </details>

      {/* Surprises heatmap */}
      {surpPivot && surpPivot.tickers.length > 0 && (
        <div className="card">
          <div className="font-semibold text-sm">Earnings Surprise Heatmap</div>
          <div className="text-xs text-text-muted mb-2">
            Blue = beat, Red = miss. Intensity shows magnitude.
          </div>
          <Plot
            data={[{
              ...heatmapTrace(t, "divergent", { colorbarTitle: "Surprise %" }),
              z: surpPivot.z,
              x: surpPivot.quarters,
              y: surpPivot.tickers,
              zmid: 0,
              text: surpPivot.z.map(row => row.map(v => v != null ? `${v >= 0 ? "+" : ""}${v.toFixed(1)}%` : "")),
            }]}
            layout={{
              ...L, height: heatmapHeight(surpPivot.tickers.length),
              margin: { l: 60, r: 40, t: 40, b: 40 },
              yaxis: { gridcolor: t.grid, autorange: "reversed" },
              xaxis: { gridcolor: t.grid },
            }}
            config={{ displayModeBar: false, responsive: true }}
            style={{ width: "100%" }}
          />
        </div>
      )}
    </div>
  );
}

// ══════════════════════════════════════════════════════════════
// TAB 7 — MARKET & POSITIONING
// ══════════════════════════════════════════════════════════════

function MarketTab({
  cfg,
  market,
  overview,
  t,
  L,
}: {
  cfg: SectorConfig;
  market: NonNullable<Awaited<ReturnType<typeof fetchSectorMarket>>>;
  overview: NonNullable<Awaited<ReturnType<typeof fetchSectorOverview>>>;
  t: ChartTheme;
  L: ReturnType<typeof getBaseLayout>;
}) {
  // Aggregate sector revenue by quarter
  const sectorRev = useMemo(() => {
    const byQ: Record<string, number> = {};
    for (const r of overview.revenue_history) {
      const q = quarterLabel(r.date);
      byQ[q] = (byQ[q] ?? 0) + r.revenue;
    }
    const sortedQ = Object.keys(byQ).sort();
    return { quarters: sortedQ, values: sortedQ.map(q => byQ[q]) };
  }, [overview.revenue_history]);

  // Quarterize macro series
  const macroQuarterly = useMemo(() => {
    if (market.macro_series.length === 0) return { quarters: [] as string[], values: [] as number[] };
    const byQ: Record<string, { sum: number; n: number }> = {};
    for (const p of market.macro_series) {
      const q = quarterLabel(p.date);
      byQ[q] = byQ[q] ?? { sum: 0, n: 0 };
      byQ[q].sum += p.value;
      byQ[q].n += 1;
    }
    const qs = Object.keys(byQ).filter(q => q >= "2024-Q1").sort();
    return { quarters: qs, values: qs.map(q => byQ[q].sum / byQ[q].n) };
  }, [market.macro_series]);

  const [cotIdx, setCotIdx] = useState(0);
  const cotBundle = market.cot[cotIdx];

  return (
    <div className="space-y-4">
      <div className="card">
        <div className="font-semibold text-sm">Revenue vs {market.macro_label}</div>
        <div className="text-xs text-text-muted mb-2">
          Sector revenue overlaid with {market.macro_label} — shows macro sensitivity.
        </div>
        {sectorRev.quarters.length > 0 && macroQuarterly.quarters.length > 0 ? (
          <Plot
            data={[
              {
                type: "bar", x: sectorRev.quarters, y: sectorRev.values.map(v => v / 1e9),
                name: "Sector Revenue ($B)", marker: { color: "rgba(88,166,255,0.5)" },
                yaxis: "y1",
              },
              {
                type: "scatter", mode: "lines+markers",
                x: macroQuarterly.quarters.filter(q => sectorRev.quarters.includes(q)),
                y: macroQuarterly.quarters.filter(q => sectorRev.quarters.includes(q)).map(q => macroQuarterly.values[macroQuarterly.quarters.indexOf(q)]),
                name: market.macro_label,
                line: { color: t.spot, width: 3 }, marker: { size: 8 },
                yaxis: "y2",
              },
            ]}
            layout={{
              ...L, height: CHART_HEIGHT.normal + 60,
              title: { text: `Sector Revenue vs ${market.macro_label}`, font: { size: 14, color: t.text } },
              yaxis: { title: { text: "Revenue ($B)" }, gridcolor: t.grid, side: "left" },
              yaxis2: { title: { text: market.macro_label }, overlaying: "y", side: "right" },
              xaxis: { gridcolor: t.grid },
              legend: { orientation: "h", y: -0.15 },
              margin: { l: 60, r: 60, t: 40, b: 60 },
            }}
            config={{ displayModeBar: false, responsive: true }}
            style={{ width: "100%" }}
          />
        ) : (
          <div className="text-xs text-text-muted py-4">
            Macro data ({market.macro_series_id}) unavailable or requires FRED_API_KEY.
          </div>
        )}
      </div>

      {/* COT */}
      {market.cot.length > 0 && cotBundle && (
        <div className="card">
          <div className="font-semibold text-sm mb-1">Futures Positioning (CFTC COT)</div>
          {market.cot.length > 1 && (
            <div className="flex gap-1 mb-2">
              {market.cot.map((c, i) => (
                <button key={c.key} onClick={() => setCotIdx(i)}
                  className={`px-2.5 py-1 text-xs rounded ${i === cotIdx ? "bg-accent text-white" : "text-text-muted hover:text-text hover:bg-surface-alt"}`}>
                  {c.name}
                </button>
              ))}
            </div>
          )}
          {cotBundle.rows.length > 3 ? (
            <CotAnalysis bundle={cotBundle} t={t} L={L} />
          ) : (
            <div className="text-xs text-text-muted py-4">
              CFTC {cotBundle.name} data unavailable or insufficient.
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function CotAnalysis({
  bundle,
  t,
  L,
}: {
  bundle: { name: string; key: string; rows: SectorCotRow[]; price_history: SectorMacroPoint[] };
  t: ChartTheme;
  L: ReturnType<typeof getBaseLayout>;
}) {
  const rows = bundle.rows.filter(r => r.spec_net != null) as (SectorCotRow & { spec_net: number })[];
  if (rows.length < 3) return null;

  const specNets = rows.map(r => r.spec_net);
  const min = Math.min(...specNets);
  const max = Math.max(...specNets);
  const range = max - min;
  const latest = rows[rows.length - 1];
  const wow = rows.length > 1 ? (latest.spec_net! - rows[rows.length - 2].spec_net!) : 0;
  const pctile = range > 0 ? ((latest.spec_net! - min) / range) * 100 : 50;
  const specTotal = (latest.spec_long ?? 0) + (latest.spec_short ?? 0);
  const specPctLong = specTotal > 0 ? ((latest.spec_long ?? 0) / specTotal) * 100 : 50;

  const signal = pctile > 85 ? "Extreme Bullish"
    : pctile > 60 ? "Bullish"
    : pctile > 40 ? "Neutral"
    : pctile > 15 ? "Bearish" : "Extreme Bearish";
  const signalColor = signal.includes("Bullish") ? t.gain : signal.includes("Bearish") ? t.loss : t.muted;

  const ma4 = rows.map((_, i) => {
    const start = Math.max(0, i - 3);
    const window = rows.slice(start, i + 1).map(r => r.spec_net!);
    return window.reduce((s, v) => s + v, 0) / window.length;
  });

  const wowArr = rows.map((r, i) => i > 0 ? r.spec_net! - rows[i - 1].spec_net! : 0);
  const divergence = rows.map(r => (r.spec_net ?? 0) - (r.comm_net ?? 0));

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-6">
        <Metric label="Spec Net" value={latest.spec_net!.toLocaleString(undefined, { maximumFractionDigits: 0 })}
          delta={`${wow >= 0 ? "+" : ""}${wow.toFixed(0)} WoW`} deltaType={wow >= 0 ? "gain" : "loss"} />
        <Metric label="Comm Net" value={(latest.comm_net ?? 0).toLocaleString(undefined, { maximumFractionDigits: 0 })} />
        <Metric label="Spec % Long" value={`${specPctLong.toFixed(0)}%`} />
        <Metric label="Positioning Pctile" value={`${pctile.toFixed(0)}th`} />
        <div>
          <div className="metric-label">52-Week Signal</div>
          <div className="text-sm font-bold" style={{ color: signalColor }}>{signal}</div>
        </div>
      </div>

      <Plot
        data={[
          { x: rows.map(r => r.date), y: rows.map(r => r.spec_net), type: "scatter", mode: "lines", name: "Spec Net", line: { color: t.accent, width: 2 }, fill: "tozeroy", fillcolor: "rgba(88,166,255,0.1)", yaxis: "y1" },
          { x: rows.map(r => r.date), y: ma4, type: "scatter", mode: "lines", name: "4W MA", line: { color: t.accent, width: 1, dash: "dot" }, yaxis: "y1" },
          { x: rows.map(r => r.date), y: rows.map(r => r.comm_net), type: "scatter", mode: "lines", name: "Comm Net", line: { color: t.spot, width: 2 }, yaxis: "y1" },
          ...(bundle.price_history.length > 0 ? [{
            x: bundle.price_history.map(p => p.date), y: bundle.price_history.map(p => p.value),
            type: "scatter" as const, mode: "lines" as const, name: bundle.name,
            line: { color: t.loss, width: 2 }, yaxis: "y2",
          }] : []),
        ]}
        layout={{
          ...L, height: CHART_HEIGHT.tall,
          title: { text: `${bundle.name} — Net Positioning vs Price (52 Weeks)`, font: { size: 13, color: t.text } },
          yaxis: { title: { text: "Net Contracts" }, gridcolor: t.grid, side: "left" },
          yaxis2: { title: { text: bundle.name }, overlaying: "y", side: "right" },
          xaxis: { gridcolor: t.grid },
          legend: { orientation: "h", y: -0.15 },
          margin: { l: 60, r: 60, t: 40, b: 60 },
          shapes: [{ type: "line", y0: 0, y1: 0, x0: 0, x1: 1, xref: "paper", line: { color: t.muted, dash: "dash" } }],
        }}
        config={{ displayModeBar: false, responsive: true }}
        style={{ width: "100%" }}
      />

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <Plot
          data={[
            { x: rows.map(r => r.date), y: rows.map(r => r.spec_long), type: "scatter", mode: "lines", name: "Spec Long", line: { color: t.accent, width: 2 } },
            { x: rows.map(r => r.date), y: rows.map(r => r.spec_short), type: "scatter", mode: "lines", name: "Spec Short", line: { color: t.loss, width: 2 } },
            { x: rows.map(r => r.date), y: rows.map(r => r.comm_long), type: "scatter", mode: "lines", name: "Comm Long", line: { color: t.spot, width: 1, dash: "dash" } },
            { x: rows.map(r => r.date), y: rows.map(r => r.comm_short), type: "scatter", mode: "lines", name: "Comm Short", line: { color: "#ff8866", width: 1, dash: "dash" } },
          ]}
          layout={{
            ...L, height: CHART_HEIGHT.normal,
            title: { text: "Gross Long/Short Breakdown", font: { size: 13, color: t.text } },
            yaxis: { title: { text: "Contracts" }, gridcolor: t.grid },
            xaxis: { gridcolor: t.grid },
            legend: { orientation: "h", y: -0.25 },
            margin: { l: 60, r: 20, t: 40, b: 60 },
          }}
          config={{ displayModeBar: false, responsive: true }}
          style={{ width: "100%" }}
        />
        <Plot
          data={[{
            type: "bar",
            x: rows.slice(-26).map(r => r.date),
            y: wowArr.slice(-26),
            marker: { color: wowArr.slice(-26).map(v => v >= 0 ? t.accent : t.loss) },
          }]}
          layout={{
            ...L, height: CHART_HEIGHT.normal,
            title: { text: "Week-over-Week Change (Spec Net)", font: { size: 13, color: t.text } },
            yaxis: { title: { text: "Contracts" }, gridcolor: t.grid },
            xaxis: { gridcolor: t.grid },
            margin: { l: 60, r: 20, t: 40, b: 40 },
            shapes: [{ type: "line", y0: 0, y1: 0, x0: 0, x1: 1, xref: "paper", line: { color: t.muted, dash: "dash" } }],
          }}
          config={{ displayModeBar: false, responsive: true }}
          style={{ width: "100%" }}
        />
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <Plot
          data={[{
            x: rows.map(r => r.date),
            y: rows.map(r => range > 0 ? ((r.spec_net! - min) / range) * 100 : 50),
            type: "scatter", mode: "lines+markers",
            line: { color: t.accent, width: 2 }, marker: { size: 4 },
          }]}
          layout={{
            ...L, height: CHART_HEIGHT.normal,
            title: { text: "Positioning Percentile (52-Week)", font: { size: 13, color: t.text } },
            yaxis: { title: { text: "Percentile" }, gridcolor: t.grid, range: [0, 100] },
            xaxis: { gridcolor: t.grid },
            margin: { l: 60, r: 20, t: 40, b: 40 },
            shapes: [
              { type: "rect", x0: 0, x1: 1, xref: "paper", y0: 85, y1: 100, fillcolor: "rgba(0,255,136,0.08)", line: { width: 0 } },
              { type: "rect", x0: 0, x1: 1, xref: "paper", y0: 0, y1: 15, fillcolor: "rgba(255,107,107,0.08)", line: { width: 0 } },
              { type: "line", x0: 0, x1: 1, xref: "paper", y0: 50, y1: 50, line: { color: t.muted, dash: "dash" } },
            ],
          }}
          config={{ displayModeBar: false, responsive: true }}
          style={{ width: "100%" }}
        />
        <Plot
          data={[{
            x: rows.map(r => r.date),
            y: rows.map(r => {
              const tot = (r.spec_long ?? 0) + (r.spec_short ?? 0);
              return tot > 0 ? ((r.spec_long ?? 0) / tot) * 100 : 50;
            }),
            type: "scatter", mode: "lines+markers",
            line: { color: t.spot, width: 2 }, marker: { size: 4 },
          }]}
          layout={{
            ...L, height: CHART_HEIGHT.normal,
            title: { text: "Speculator % Long (Sentiment)", font: { size: 13, color: t.text } },
            yaxis: { title: { text: "% Long" }, gridcolor: t.grid, range: [0, 100] },
            xaxis: { gridcolor: t.grid },
            margin: { l: 60, r: 20, t: 40, b: 40 },
            shapes: [{ type: "line", x0: 0, x1: 1, xref: "paper", y0: 50, y1: 50, line: { color: t.muted, dash: "dash" } }],
          }}
          config={{ displayModeBar: false, responsive: true }}
          style={{ width: "100%" }}
        />
      </div>

      <Plot
        data={[{
          type: "bar",
          x: rows.map(r => r.date),
          y: divergence,
          marker: { color: divergence.map(v => v >= 0 ? t.accent : t.loss) },
          name: "Spec - Comm Divergence",
        }]}
        layout={{
          ...L, height: CHART_HEIGHT.compact + 20,
          title: { text: "Speculator vs Commercial Divergence", font: { size: 13, color: t.text } },
          yaxis: { title: { text: "Contracts" }, gridcolor: t.grid },
          xaxis: { gridcolor: t.grid },
          margin: { l: 60, r: 20, t: 40, b: 40 },
          shapes: [{ type: "line", y0: 0, y1: 0, x0: 0, x1: 1, xref: "paper", line: { color: t.muted, dash: "dash" } }],
        }}
        config={{ displayModeBar: false, responsive: true }}
        style={{ width: "100%" }}
      />
    </div>
  );
}

// ══════════════════════════════════════════════════════════════
// TAB 8 — PAIRS & CORRELATION
// ══════════════════════════════════════════════════════════════

function PairsTab({
  cfg,
  prices,
  t,
  L,
}: {
  cfg: SectorConfig;
  prices: NonNullable<Awaited<ReturnType<typeof fetchSectorPrices>>>;
  t: ChartTheme;
  L: ReturnType<typeof getBaseLayout>;
}) {
  const sectorTickers = useMemo(() => Object.keys(cfg.companies), [cfg.companies]);
  const aligned = useMemo(() => alignedReturns(prices.prices, sectorTickers), [prices.prices, sectorTickers]);
  const available = useMemo(
    () => Object.keys(aligned.returns).filter(tk => aligned.returns[tk].length > 30).sort(),
    [aligned.returns],
  );

  const corrMatrix = useMemo(() => {
    const mat: number[][] = [];
    for (const a of available) {
      const row: number[] = [];
      for (const b of available) row.push(corr(aligned.returns[a], aligned.returns[b]));
      mat.push(row);
    }
    return mat;
  }, [aligned.returns, available]);

  const [pairA, setPairA] = useState<string>(available[0] ?? "");
  const [pairB, setPairB] = useState<string>(available[1] ?? "");

  const pairStats = useMemo(() => {
    if (!pairA || !pairB || pairA === pairB) return null;
    const ra = aligned.returns[pairA];
    const rb = aligned.returns[pairB];
    if (!ra || !rb || ra.length < 30) return null;
    const n = Math.min(ra.length, rb.length);
    const raN = ra.slice(-n);
    const rbN = rb.slice(-n);
    const rho = corr(raN, rbN);
    const varA = stdev(raN) ** 2;
    const covAB = (() => {
      const ma = mean(raN), mb = mean(rbN);
      let s = 0;
      for (let i = 0; i < n; i++) s += (raN[i] - ma) * (rbN[i] - mb);
      return s / (n - 1);
    })();
    const beta = varA > 0 ? covAB / varA : 0;

    // Normalized prices
    const closesA = aligned.closes[pairA];
    const closesB = aligned.closes[pairB];
    const len = Math.min(closesA.length, closesB.length);
    const normA = closesA.slice(-len).map(c => (c / closesA[closesA.length - len]) * 100);
    const normB = closesB.slice(-len).map(c => (c / closesB[closesB.length - len]) * 100);
    const spread = normA.map((v, i) => v - normB[i]);
    const spreadMean = mean(spread);
    const spreadStd = stdev(spread);
    const spreadZ = spreadStd > 0 ? (spread[spread.length - 1] - spreadMean) / spreadStd : 0;

    // Rolling z on 63-day
    const rollingZ: number[] = [];
    for (let i = 0; i < spread.length; i++) {
      if (i < 62) { rollingZ.push(NaN); continue; }
      const window = spread.slice(i - 62, i + 1);
      const m = mean(window), s = stdev(window);
      rollingZ.push(s > 0 ? (spread[i] - m) / s : 0);
    }

    // Rolling correlation
    const rollCorr: { window: number; arr: number[]; color: string; dash: string }[] = [];
    for (const [w, color, dash] of [[21, t.accent, "solid"], [63, t.spot, "dash"]] as const) {
      if (n < w) continue;
      const arr: number[] = [];
      for (let i = 0; i < n; i++) {
        if (i < w - 1) { arr.push(NaN); continue; }
        const wa = raN.slice(i - w + 1, i + 1);
        const wb = rbN.slice(i - w + 1, i + 1);
        arr.push(corr(wa, wb));
      }
      rollCorr.push({ window: w, arr, color, dash });
    }

    return {
      rho, beta, spreadZ, spread, spreadMean, spreadStd,
      raN, rbN, normA, normB, rollCorr,
      len, dates: aligned.dates.slice(-len),
      retDates: aligned.dates.slice(-n),
    };
  }, [pairA, pairB, aligned, t]);

  // Pairs plot is N×N which explodes for 10 tickers. We'll skip the full pairs matrix
  // and just render the correlation matrix heatmap + deep-dive.
  return (
    <div className="space-y-4">
      <div className="card">
        <div className="font-semibold text-sm mb-2">Correlation Matrix (2Y daily returns)</div>
        <Plot
          data={[{
            ...heatmapTrace(t, "correlation", { colorbarTitle: "Corr" }),
            z: corrMatrix,
            x: available, y: available,
            zmid: 0, zmin: -1, zmax: 1,
            text: corrMatrix.map(row => row.map(v => v.toFixed(2))),
          }]}
          layout={{
            ...L, height: heatmapHeight(available.length),
            margin: { l: 60, r: 40, t: 20, b: 60 },
            yaxis: { autorange: "reversed", gridcolor: t.grid },
            xaxis: { gridcolor: t.grid },
          }}
          config={{ displayModeBar: false, responsive: true }}
          style={{ width: "100%" }}
        />
      </div>

      <div className="card">
        <div className="font-semibold text-sm mb-2">Pair Deep Dive</div>
        <div className="flex gap-3 items-center mb-3">
          <div>
            <label className="text-xs text-text-muted">Ticker A</label>
            <select value={pairA} onChange={e => setPairA(e.target.value)}
              className="ml-2 px-2 py-1 text-xs border border-border rounded bg-surface font-data">
              {available.map(tk => <option key={tk} value={tk}>{tk}</option>)}
            </select>
          </div>
          <div>
            <label className="text-xs text-text-muted">Ticker B</label>
            <select value={pairB} onChange={e => setPairB(e.target.value)}
              className="ml-2 px-2 py-1 text-xs border border-border rounded bg-surface font-data">
              {available.map(tk => <option key={tk} value={tk}>{tk}</option>)}
            </select>
          </div>
        </div>

        {pairA === pairB && (
          <div className="text-xs text-text-muted py-2">Select two different tickers to analyze the pair.</div>
        )}

        {pairStats && (
          <>
            <div className="flex flex-wrap gap-6 mb-4">
              <Metric label="Correlation" value={pairStats.rho.toFixed(3)} />
              <Metric label="R²" value={(pairStats.rho ** 2).toFixed(3)} />
              <Metric label={`Beta (${pairB} vs ${pairA})`} value={pairStats.beta.toFixed(2)} />
              <Metric label="Spread Z-Score" value={pairStats.spreadZ.toFixed(2)} />
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <Plot
                data={[
                  {
                    x: pairStats.raN, y: pairStats.rbN, type: "scatter", mode: "markers",
                    marker: { size: 3, color: t.accent, opacity: 0.4 },
                    hovertemplate: `${pairA}: %{x:.2%}<br>${pairB}: %{y:.2%}<extra></extra>`,
                  },
                ]}
                layout={{
                  ...L, height: CHART_HEIGHT.tall,
                  title: { text: `${pairA} vs ${pairB} Daily Returns (ρ = ${pairStats.rho.toFixed(3)})`, font: { size: 13, color: t.text } },
                  xaxis: { title: { text: `${pairA} Return` }, gridcolor: t.grid, tickformat: ".1%" },
                  yaxis: { title: { text: `${pairB} Return` }, gridcolor: t.grid, tickformat: ".1%" },
                  margin: { l: 70, r: 20, t: 40, b: 60 },
                }}
                config={{ displayModeBar: false, responsive: true }}
                style={{ width: "100%" }}
              />
              <Plot
                data={pairStats.rollCorr.map(rc => ({
                  x: pairStats.retDates, y: rc.arr, type: "scatter", mode: "lines",
                  name: `${rc.window}D Rolling ρ`,
                  line: { color: rc.color, width: 2, dash: rc.dash },
                }))}
                layout={{
                  ...L, height: CHART_HEIGHT.tall,
                  title: { text: `Rolling Correlation — ${pairA} vs ${pairB}`, font: { size: 13, color: t.text } },
                  yaxis: { title: { text: "Correlation" }, gridcolor: t.grid, range: [-0.5, 1.1] },
                  xaxis: { gridcolor: t.grid },
                  legend: { orientation: "h", y: -0.15 },
                  margin: { l: 60, r: 20, t: 40, b: 60 },
                  shapes: [
                    { type: "line", y0: pairStats.rho, y1: pairStats.rho, x0: 0, x1: 1, xref: "paper", line: { color: t.muted, dash: "dot" } },
                    { type: "line", y0: 0, y1: 0, x0: 0, x1: 1, xref: "paper", line: { color: t.muted, dash: "dash" } },
                  ],
                }}
                config={{ displayModeBar: false, responsive: true }}
                style={{ width: "100%" }}
              />
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-4">
              <Plot
                data={[
                  { x: pairStats.dates, y: pairStats.normA, type: "scatter", mode: "lines", name: pairA, line: { color: t.accent, width: 2 } },
                  { x: pairStats.dates, y: pairStats.normB, type: "scatter", mode: "lines", name: pairB, line: { color: t.spot, width: 2 } },
                ]}
                layout={{
                  ...L, height: CHART_HEIGHT.normal,
                  title: { text: "Normalized Price (base=100)", font: { size: 13, color: t.text } },
                  yaxis: { title: { text: "Indexed Price" }, gridcolor: t.grid },
                  xaxis: { gridcolor: t.grid },
                  legend: { orientation: "h", y: -0.18 },
                  margin: { l: 60, r: 20, t: 40, b: 60 },
                }}
                config={{ displayModeBar: false, responsive: true }}
                style={{ width: "100%" }}
              />
              <Plot
                data={[{
                  x: pairStats.dates,
                  y: (() => {
                    // Compute rolling 63d z-score of the normalized spread
                    const spread = pairStats.spread;
                    const zs: number[] = [];
                    for (let i = 0; i < spread.length; i++) {
                      if (i < 62) { zs.push(NaN); continue; }
                      const w = spread.slice(i - 62, i + 1);
                      const m = mean(w), s = stdev(w);
                      zs.push(s > 0 ? (spread[i] - m) / s : 0);
                    }
                    return zs;
                  })(),
                  type: "scatter", mode: "lines",
                  line: { color: t.accent, width: 2 },
                  fill: "tozeroy", fillcolor: "rgba(88,166,255,0.08)",
                }]}
                layout={{
                  ...L, height: CHART_HEIGHT.normal,
                  title: { text: `Spread Z-Score (${pairA} − ${pairB})`, font: { size: 13, color: t.text } },
                  yaxis: { title: { text: "Z-Score" }, gridcolor: t.grid },
                  xaxis: { gridcolor: t.grid },
                  margin: { l: 60, r: 20, t: 40, b: 40 },
                  shapes: [
                    { type: "line", y0: 2, y1: 2, x0: 0, x1: 1, xref: "paper", line: { color: t.loss, dash: "dash" } },
                    { type: "line", y0: -2, y1: -2, x0: 0, x1: 1, xref: "paper", line: { color: t.gain, dash: "dash" } },
                    { type: "line", y0: 0, y1: 0, x0: 0, x1: 1, xref: "paper", line: { color: t.muted, dash: "dash" } },
                  ],
                }}
                config={{ displayModeBar: false, responsive: true }}
                style={{ width: "100%" }}
              />
            </div>

            <div className="card mt-4">
              <Plot
                data={[
                  { type: "histogram", x: pairStats.raN, name: pairA, marker: { color: t.accent }, opacity: 0.6, nbinsx: 80 },
                  { type: "histogram", x: pairStats.rbN, name: pairB, marker: { color: t.spot }, opacity: 0.6, nbinsx: 80 },
                ]}
                layout={{
                  ...L, height: CHART_HEIGHT.normal, barmode: "overlay",
                  title: { text: `Return Distribution — ${pairA} vs ${pairB}`, font: { size: 13, color: t.text } },
                  xaxis: { title: { text: "Daily Return" }, gridcolor: t.grid, tickformat: ".1%" },
                  yaxis: { title: { text: "Frequency" }, gridcolor: t.grid },
                  legend: { orientation: "h", y: -0.18 },
                  margin: { l: 60, r: 20, t: 40, b: 60 },
                }}
                config={{ displayModeBar: false, responsive: true }}
                style={{ width: "100%" }}
              />
            </div>
          </>
        )}
      </div>
    </div>
  );
}

// ══════════════════════════════════════════════════════════════
// TAB 9 — COMPARE ALL SECTORS
// ══════════════════════════════════════════════════════════════

type CompareMetric = "totalRevenue" | "avgMargin" | "avgRoe" | "medianFwdPe" | "avgMom3" | "companies";

const METRIC_LABELS: Record<CompareMetric, string> = {
  totalRevenue: "Combined Revenue",
  avgMargin: "Avg Net Margin",
  avgRoe: "Avg ROE",
  medianFwdPe: "Median Fwd P/E",
  avgMom3: "Avg 3M Momentum",
  companies: "Companies",
};

// For each metric, whether "higher is better" — drives the green/red heat.
// P/E is inverted (cheaper is higher ranked).
const METRIC_HIGHER_BETTER: Record<CompareMetric, boolean> = {
  totalRevenue: true,
  avgMargin: true,
  avgRoe: true,
  medianFwdPe: false,
  avgMom3: true,
  companies: true,
};

function CompareTab({
  etfs,
  configs,
}: {
  etfs: string[];
  configs: Record<string, SectorConfig>;
}) {
  const overviewResults = useQueries({
    queries: etfs.map(etf => ({
      queryKey: ["sector-overview", etf],
      queryFn: () => fetchSectorOverview(etf),
      staleTime: 24 * 60 * 60_000,
    })),
  });
  const valuationResults = useQueries({
    queries: etfs.map(etf => ({
      queryKey: ["sector-valuation", etf],
      queryFn: () => fetchSectorValuation(etf),
      staleTime: 24 * 60 * 60_000,
    })),
  });

  const rows = useMemo(() => etfs.map((etf, i) => {
    const ov = overviewResults[i]?.data;
    const va = valuationResults[i]?.data;
    const fin = ov?.financials ?? [];
    const totRev = fin.reduce((s, r) => s + (r.revenue ?? 0), 0);
    const mRows = fin.filter(r => r.net_margin != null);
    const rRows = fin.filter(r => r.roe != null);
    const avgMargin = mRows.length > 0 ? mRows.reduce((s, r) => s + (r.net_margin as number), 0) / mRows.length : null;
    const avgRoe = rRows.length > 0 ? rRows.reduce((s, r) => s + (r.roe as number), 0) / rRows.length : null;
    const pes = (va?.valuation ?? [])
      .map(v => v.forward_pe)
      .filter((x): x is number => x != null && Number.isFinite(x) && x > 0 && x < 200);
    const sortedPes = [...pes].sort((a, b) => a - b);
    const medianPe = sortedPes.length > 0 ? sortedPes[Math.floor(sortedPes.length / 2)] : null;
    const m3s = (va?.momentum ?? [])
      .map(m => m["3M"])
      .filter((x): x is number => x != null && Number.isFinite(x));
    const avgMom3 = m3s.length > 0 ? m3s.reduce((s, x) => s + x, 0) / m3s.length : null;
    return {
      etf,
      label: configs[etf]?.label ?? etf,
      companies: fin.length,
      totalRevenue: totRev > 0 ? totRev : null,
      avgMargin,
      avgRoe,
      medianFwdPe: medianPe,
      avgMom3,
      loading: !!overviewResults[i]?.isPending || !!valuationResults[i]?.isPending,
      error: !!overviewResults[i]?.isError || !!valuationResults[i]?.isError,
    };
  }), [etfs, overviewResults, valuationResults, configs]);

  const [sortKey, setSortKey] = useState<CompareMetric>("totalRevenue");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");

  const sortedRows = useMemo(() => {
    const copy = [...rows];
    copy.sort((a, b) => {
      const av = a[sortKey];
      const bv = b[sortKey];
      // Push nulls to the bottom regardless of sort direction.
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      return sortDir === "desc" ? bv - av : av - bv;
    });
    return copy;
  }, [rows, sortKey, sortDir]);

  // Ranks per metric for heat-coloring. Rank 0 = best (for that metric),
  // rank N-1 = worst; we'll lerp color from gain → muted → loss.
  const ranks = useMemo(() => {
    const out: Record<string, Record<CompareMetric, number | null>> = {};
    for (const etf of etfs) out[etf] = {
      totalRevenue: null, avgMargin: null, avgRoe: null,
      medianFwdPe: null, avgMom3: null, companies: null,
    };
    (Object.keys(METRIC_LABELS) as CompareMetric[]).forEach(metric => {
      const valid = rows
        .map(r => ({ etf: r.etf, v: r[metric] as number | null }))
        .filter(r => r.v != null) as { etf: string; v: number }[];
      if (valid.length === 0) return;
      valid.sort((a, b) => METRIC_HIGHER_BETTER[metric] ? b.v - a.v : a.v - b.v);
      valid.forEach((x, i) => { out[x.etf][metric] = i; });
    });
    return out;
  }, [rows, etfs]);

  function cellColor(etf: string, metric: CompareMetric): string {
    const r = ranks[etf]?.[metric];
    const total = etfs.length;
    if (r == null || total < 2) return "text-text";
    const pct = r / (total - 1); // 0=best, 1=worst
    if (pct < 0.2) return "text-gain font-semibold";
    if (pct < 0.4) return "text-gain";
    if (pct < 0.6) return "text-text";
    if (pct < 0.8) return "text-loss";
    return "text-loss font-semibold";
  }

  function fmtCell(metric: CompareMetric, v: number | null): string {
    if (v == null) return "—";
    if (metric === "totalRevenue") return `$${(v / 1e12).toFixed(2)}T`;
    if (metric === "avgMargin" || metric === "avgRoe" || metric === "avgMom3") return `${v >= 0 ? "+" : ""}${v.toFixed(1)}%`;
    if (metric === "medianFwdPe") return `${v.toFixed(1)}x`;
    return String(v);
  }

  function toggleSort(metric: CompareMetric) {
    if (metric === sortKey) {
      setSortDir(d => d === "desc" ? "asc" : "desc");
    } else {
      setSortKey(metric);
      setSortDir(METRIC_HIGHER_BETTER[metric] ? "desc" : "asc");
    }
  }

  const pendingCount = rows.filter(r => r.loading).length;
  const errorCount = rows.filter(r => r.error).length;

  // Compact AI payload — one row per sector with the cross-sector metrics
  const aiData = useMemo(() => ({
    sectors: rows.map(r => ({
      etf: r.etf,
      label: r.label,
      median_forward_pe: r.medianFwdPe,
      avg_net_margin: r.avgMargin,
      avg_roe: r.avgRoe,
      companies_count: r.companies,
      total_revenue_usd: r.totalRevenue,
      avg_3m_momentum_pct: r.avgMom3,
    })),
  }), [rows]);

  return (
    <div className="space-y-4">
      <div className="card card-compact">
        <div className="flex items-center justify-between flex-wrap gap-2">
          <div>
            <div className="text-sm font-semibold text-text">Cross-sector comparison</div>
            <div className="text-xs text-text-muted">All 11 SPDR sectors × 6 fundamentals metrics. Click a column header to sort.</div>
          </div>
          {pendingCount > 0 && (
            <div className="text-xs text-text-muted flex items-center gap-2">
              <div className="w-3 h-3 border-2 border-accent border-t-transparent rounded-full animate-spin" />
              {pendingCount} / {etfs.length} loading
            </div>
          )}
          {errorCount > 0 && (
            <div className="text-xs text-loss">{errorCount} sector{errorCount === 1 ? "" : "s"} failed</div>
          )}
        </div>

        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-left border-b border-border">
                <th className="py-2 px-2 font-semibold">Sector</th>
                {(Object.keys(METRIC_LABELS) as CompareMetric[]).map(metric => (
                  <th key={metric} className="py-2 px-2 font-semibold cursor-pointer hover:bg-surface-alt select-none" onClick={() => toggleSort(metric)}>
                    <span className={sortKey === metric ? "text-accent" : ""}>{METRIC_LABELS[metric]}</span>
                    {sortKey === metric && <span className="ml-1 text-accent">{sortDir === "desc" ? "↓" : "↑"}</span>}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sortedRows.map(r => (
                <tr key={r.etf} className="border-b border-border/60 hover:bg-surface-alt">
                  <td className="py-1.5 px-2">
                    <div className="font-semibold">{r.etf}</div>
                    <div className="text-[10px] text-text-muted">{r.label}</div>
                  </td>
                  {(Object.keys(METRIC_LABELS) as CompareMetric[]).map(metric => (
                    <td key={metric} className={`py-1.5 px-2 font-data ${cellColor(r.etf, metric)}`}>
                      {r.loading && (r[metric] as number | null) == null
                        ? <span className="text-text-muted">…</span>
                        : fmtCell(metric, r[metric] as number | null)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="mt-2 text-[10px] text-text-muted">
          Color: <span className="text-gain">green</span> = best two ranks · <span className="text-loss">red</span> = worst two ranks · P/E inverted (cheaper ranks better).
        </div>
      </div>

      {pendingCount === 0 && errorCount < etfs.length && (
        <AIInterpretation
          page="sector-compare"
          subject="SPDR Sectors"
          data={aiData}
          buttonLabel="Interpret rotation across sectors"
        />
      )}
    </div>
  );
}

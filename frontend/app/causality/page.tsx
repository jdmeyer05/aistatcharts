"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTheme } from "next-themes";
import { Plot } from "@/components/plot";
import { ChartCard } from "@/components/ui/chart-card";
import { AIInterpretation } from "@/components/ai-interpretation";
import {
  fetchCausalityUniverse,
  fetchCcfPair,
  fetchCcfScan,
  fetchGrangerPair,
  fetchGrangerScan,
  fetchTePair,
  fetchTeScan,
  fetchVarBasket,
  type CausalityCategory,
  type CausalityLookback,
  type CausalityCcfPair,
  type CausalityCcfScan,
  type CausalityCcfScanRow,
  type CausalitySeriesMeta,
  type GrangerPair,
  type GrangerScan,
  type GrangerScanRow,
  type GrangerVerdict,
  type TePair,
  type TeScan,
  type TeScanRow,
  type VarBasket,
} from "@/lib/api";
import { getChartTheme, getBaseLayout, getPlotConfig, useIsMobile, CHART_HEIGHT } from "@/lib/chart-theme";

/* ─────────────────────────────────────────────────────────────
   Tab definitions — Tab 1 (CCF) live; 2-4 ship next
   ───────────────────────────────────────────────────────────── */

const TABS = ["Lead/Lag (CCF)", "Granger", "Transfer Entropy", "VAR + IRF"] as const;
type Tab = (typeof TABS)[number];

const LOOKBACKS: CausalityLookback[] = ["1Y", "3Y", "5Y", "10Y"];

const CATEGORY_ORDER: CausalityCategory[] = [
  "Equity", "Factor", "FX", "Rates", "Credit", "Commodity", "Vol", "Crypto", "Macro",
];

const TRANSFORM_LABEL: Record<string, string> = {
  log_return: "log-returns",
  diff: "first diff",
  level: "level",
};

/* ─────────────────────────────────────────────────────────────
   Symbol picker — grouped by category
   ───────────────────────────────────────────────────────────── */

function SymbolSelect({
  value, onChange, label, universe, exclude,
}: {
  value: string;
  onChange: (s: string) => void;
  label: string;
  universe: CausalitySeriesMeta[];
  exclude?: string;
}) {
  const grouped = useMemo(() => {
    const map: Record<string, CausalitySeriesMeta[]> = {};
    for (const s of universe) {
      if (s.symbol === exclude) continue;
      (map[s.category] = map[s.category] || []).push(s);
    }
    return map;
  }, [universe, exclude]);

  return (
    <label className="flex flex-col gap-1 text-xs">
      <span className="font-bold uppercase tracking-wider text-text-muted">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="bg-surface-alt border border-border rounded px-2 py-1.5 text-sm font-data"
      >
        {CATEGORY_ORDER.filter((c) => grouped[c]?.length).map((cat) => (
          <optgroup key={cat} label={cat}>
            {grouped[cat]
              .sort((a, b) => a.symbol.localeCompare(b.symbol))
              .map((s) => (
                <option key={s.symbol} value={s.symbol}>
                  {s.symbol} — {s.label}
                </option>
              ))}
          </optgroup>
        ))}
      </select>
    </label>
  );
}

/* ─────────────────────────────────────────────────────────────
   CCF Tab
   ───────────────────────────────────────────────────────────── */

type CcfMode = "pair" | "scan";

function CcfTab({ universe }: { universe: CausalitySeriesMeta[] }) {
  const [mode, setMode] = useState<CcfMode>("pair");
  const [x, setX] = useState("DXY");
  const [y, setY] = useState("EEM");
  const [target, setTarget] = useState("SPX");
  const [lookback, setLookback] = useState<CausalityLookback>("5Y");
  const [maxLag, setMaxLag] = useState(30);

  const pairQ = useQuery({
    queryKey: ["ccf-pair", x, y, lookback, maxLag],
    queryFn: () => fetchCcfPair(x, y, lookback, maxLag),
    enabled: mode === "pair" && x !== y,
    staleTime: 30 * 60_000,
  });

  const scanQ = useQuery({
    queryKey: ["ccf-scan", target, lookback, maxLag],
    queryFn: () => fetchCcfScan(target, lookback, maxLag),
    enabled: mode === "scan",
    staleTime: 30 * 60_000,
  });

  return (
    <div className="space-y-5">
      {/* Mode + global controls */}
      <div className="card p-4 flex flex-wrap items-end gap-4">
        <div className="inline-flex border border-border rounded overflow-hidden">
          <button
            className={`px-4 py-2 text-xs font-bold uppercase tracking-wider transition-colors ${
              mode === "pair" ? "bg-accent text-white" : "bg-surface-alt text-text-muted hover:bg-surface"
            }`}
            onClick={() => setMode("pair")}
          >
            Pair
          </button>
          <button
            className={`px-4 py-2 text-xs font-bold uppercase tracking-wider transition-colors ${
              mode === "scan" ? "bg-accent text-white" : "bg-surface-alt text-text-muted hover:bg-surface"
            }`}
            onClick={() => setMode("scan")}
          >
            Scan
          </button>
        </div>

        {mode === "pair" ? (
          <>
            <SymbolSelect label="Driver (X)" value={x} onChange={setX} universe={universe} exclude={y} />
            <SymbolSelect label="Target (Y)" value={y} onChange={setY} universe={universe} exclude={x} />
          </>
        ) : (
          <SymbolSelect label="Target" value={target} onChange={setTarget} universe={universe} />
        )}

        <label className="flex flex-col gap-1 text-xs">
          <span className="font-bold uppercase tracking-wider text-text-muted">Lookback</span>
          <select
            value={lookback}
            onChange={(e) => setLookback(e.target.value as CausalityLookback)}
            className="bg-surface-alt border border-border rounded px-2 py-1.5 text-sm font-data"
          >
            {LOOKBACKS.map((lb) => <option key={lb} value={lb}>{lb}</option>)}
          </select>
        </label>

        <label className="flex flex-col gap-1 text-xs">
          <span className="font-bold uppercase tracking-wider text-text-muted">Max Lag (days)</span>
          <select
            value={maxLag}
            onChange={(e) => setMaxLag(Number(e.target.value))}
            className="bg-surface-alt border border-border rounded px-2 py-1.5 text-sm font-data"
          >
            {[10, 20, 30, 60, 90, 120].map((m) => <option key={m} value={m}>±{m}</option>)}
          </select>
        </label>
      </div>

      {mode === "pair" ? (
        <CcfPairView query={pairQ} x={x} y={y} />
      ) : (
        <CcfScanView query={scanQ} target={target} />
      )}
    </div>
  );
}

/* ─────────────────────────────────────────────────────────────
   PAIR mode view — CCF chart + KPI cards + AI interpreter
   ───────────────────────────────────────────────────────────── */

function CcfPairView({
  query, x, y,
}: {
  query: ReturnType<typeof useQuery<CausalityCcfPair, Error>>;
  x: string;
  y: string;
}) {
  const { resolvedTheme } = useTheme();
  const isDark = resolvedTheme === "dark";
  const t = getChartTheme(isDark);
  const isMobile = useIsMobile();

  const data = query.data;
  const error = query.error?.message;

  const conf = data?.result.conf_band ?? 0;
  const peak = data?.result.peak;
  const xLeads = data?.result.x_leads;
  const yLeads = data?.result.y_leads;
  const contemp = data?.result.contemp_rho ?? 0;

  // Build the bar chart traces: ρ at each lag, with shaded conf band.
  const barTraces = useMemo(() => {
    if (!data) return [];
    const lags = data.result.lags;
    const ccf = data.result.ccf.map((v) => (v == null ? 0 : v));
    const colors = ccf.map((v) => {
      if (Math.abs(v) < conf) return t.muted;
      return v >= 0 ? t.gain : t.loss;
    });
    return [
      {
        type: "bar" as const,
        x: lags,
        y: ccf,
        marker: { color: colors },
        hovertemplate: "lag %{x}d<br>ρ %{y:.3f}<extra></extra>",
        name: "ρ(lag)",
      },
    ];
  }, [data, conf, t]);

  return (
    <div className="space-y-5">
      {/* KPI strip */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <KpiCard
          label={`${x} leads ${y}`}
          value={xLeads ? `${xLeads.lag >= 0 ? "+" : ""}${xLeads.lag}d  ρ ${xLeads.rho.toFixed(3)}` : "—"}
          tone={xLeads ? rhoTone(xLeads.rho, conf) : "neutral"}
          subtitle={xLeads && Math.abs(xLeads.rho) < conf ? "inside conf band" : `band ±${conf.toFixed(3)}`}
        />
        <KpiCard
          label={`${y} leads ${x}`}
          value={yLeads ? `${yLeads.lag}d  ρ ${yLeads.rho.toFixed(3)}` : "—"}
          tone={yLeads ? rhoTone(yLeads.rho, conf) : "neutral"}
          subtitle={yLeads && Math.abs(yLeads.rho) < conf ? "inside conf band" : `band ±${conf.toFixed(3)}`}
        />
        <KpiCard
          label="Contemporaneous"
          value={data ? `lag 0  ρ ${contemp.toFixed(3)}` : "—"}
          tone={data ? rhoTone(contemp, conf) : "neutral"}
          subtitle={data && Math.abs(contemp) > Math.max(Math.abs(xLeads?.rho ?? 0), Math.abs(yLeads?.rho ?? 0)) ? "co-mover, not lead/lag" : "same-day correlation"}
        />
        <KpiCard
          label="Peak |ρ|"
          value={peak ? `${peak.lag >= 0 ? "+" : ""}${peak.lag}d  ρ ${peak.rho.toFixed(3)}` : "—"}
          tone={peak ? rhoTone(peak.rho, conf) : "neutral"}
          subtitle={peak ? `n = ${data?.result.n}` : ""}
        />
      </div>

      {/* Stationarity transparency */}
      {data && (
        <div className="flex flex-wrap items-center gap-2 text-xs text-text-muted">
          <TransformBadge symbol={x} transform={data.x.transform} adfP={data.x.adf_p} />
          <TransformBadge symbol={y} transform={data.y.transform} adfP={data.y.adf_p} />
          <span className="ml-auto font-data">n = {data.result.n} obs · ±2σ band ≈ ±{conf.toFixed(3)}</span>
        </div>
      )}

      <ChartCard
        title={`CCF: ρ(lag) for ${x} vs ${y}`}
        subtitle="Sign convention: lag > 0 ⇒ X leads Y. Bars inside the shaded band are within sampling noise."
        loading={query.isPending}
        error={error}
        height={CHART_HEIGHT.tall}
      >
        <Plot
          data={barTraces}
          layout={{
            ...getBaseLayout(t, {
              showlegend: false,
              xaxis: {
                title: "Lag (business days). Positive = X leads.",
                zeroline: true,
                zerolinecolor: t.text,
                zerolinewidth: 1,
                gridcolor: t.grid,
              },
              yaxis: {
                title: "ρ",
                gridcolor: t.grid,
                zeroline: true,
                zerolinecolor: t.text,
                range: [-1, 1],
              },
              shapes: data
                ? [
                    {
                      type: "rect",
                      xref: "paper",
                      yref: "y",
                      x0: 0, x1: 1,
                      y0: -conf, y1: conf,
                      fillcolor: t.muted,
                      opacity: 0.18,
                      line: { width: 0 },
                    },
                  ]
                : [],
              annotations:
                data && peak && Math.abs(peak.rho) > conf
                  ? [
                      {
                        x: peak.lag,
                        y: peak.rho,
                        text: `peak: ${peak.lag >= 0 ? "+" : ""}${peak.lag}d  ρ ${peak.rho.toFixed(2)}`,
                        showarrow: true,
                        arrowhead: 2,
                        ax: 0,
                        ay: peak.rho >= 0 ? -30 : 30,
                        font: { color: t.text, size: 10 },
                        bgcolor: t.plot,
                        bordercolor: t.muted,
                        borderwidth: 1,
                        borderpad: 3,
                      },
                    ]
                  : [],
            }),
          }}
          config={getPlotConfig(isMobile)}
          style={{ width: "100%", height: CHART_HEIGHT.tall }}
        />
      </ChartCard>

      {/* AI interpreter — pair payload */}
      {data && (
        <AIInterpretation
          page="causality-ccf"
          subject={`${x} vs ${y}`}
          data={{
            mode: "pair",
            x: data.x,
            y: data.y,
            lookback: data.lookback,
            max_lag: data.max_lag,
            // Shrink the lag profile we send to Claude — peaks + contemp + a sparse grid is enough.
            result: {
              n: data.result.n,
              conf_band: data.result.conf_band,
              peak: data.result.peak,
              x_leads: data.result.x_leads,
              y_leads: data.result.y_leads,
              contemp_rho: data.result.contemp_rho,
              ccf_sample: sparseCcfSample(data.result.lags, data.result.ccf),
            },
          }}
        />
      )}
    </div>
  );
}

/* ─────────────────────────────────────────────────────────────
   SCAN mode view — ranked leaders/laggards table
   ───────────────────────────────────────────────────────────── */

type ScanSort = "x_leads" | "y_leads" | "peak" | "contemp";

function CcfScanView({
  query, target,
}: {
  query: ReturnType<typeof useQuery<CausalityCcfScan, Error>>;
  target: string;
}) {
  const [sort, setSort] = useState<ScanSort>("x_leads");
  const [categoryFilter, setCategoryFilter] = useState<CausalityCategory | "all">("all");

  const data = query.data;
  const error = query.error?.message;
  const conf = data?.rows[0]?.conf_band ?? 0;

  const rows = useMemo(() => {
    if (!data) return [];
    const filtered = categoryFilter === "all"
      ? data.rows
      : data.rows.filter((r) => r.category === categoryFilter);
    const sorted = [...filtered];
    sorted.sort((a, b) => {
      const av = Math.abs(rhoForSort(a, sort));
      const bv = Math.abs(rhoForSort(b, sort));
      return bv - av;
    });
    return sorted;
  }, [data, sort, categoryFilter]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-xs">
          <span className="font-bold uppercase tracking-wider text-text-muted">Sort by</span>
          <select
            value={sort}
            onChange={(e) => setSort(e.target.value as ScanSort)}
            className="bg-surface-alt border border-border rounded px-2 py-1.5 text-sm font-data"
          >
            <option value="x_leads">Drivers leading {target}</option>
            <option value="y_leads">Drivers led by {target}</option>
            <option value="peak">Strongest |ρ| at any lag</option>
            <option value="contemp">Strongest contemporaneous</option>
          </select>
        </label>
        <label className="flex flex-col gap-1 text-xs">
          <span className="font-bold uppercase tracking-wider text-text-muted">Category</span>
          <select
            value={categoryFilter}
            onChange={(e) => setCategoryFilter(e.target.value as CausalityCategory | "all")}
            className="bg-surface-alt border border-border rounded px-2 py-1.5 text-sm font-data"
          >
            <option value="all">All</option>
            {CATEGORY_ORDER.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </label>
        {data && (
          <span className="ml-auto text-xs text-text-muted font-data">
            {rows.length} drivers · ±2σ band ≈ ±{conf.toFixed(3)}
          </span>
        )}
      </div>

      <div className="card p-0 overflow-hidden">
        {query.isPending ? (
          <div className="h-[480px] bg-surface-alt/60 animate-pulse" />
        ) : error ? (
          <div className="p-4 text-sm text-loss">{error}</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-surface-alt sticky top-0">
                <tr className="text-xs font-bold uppercase tracking-wider text-text-muted">
                  <th className="text-left px-3 py-2">Driver</th>
                  <th className="text-left px-3 py-2">Category</th>
                  <th className="text-right px-3 py-2">{target}-leads-driver lag</th>
                  <th className="text-right px-3 py-2">ρ</th>
                  <th className="text-right px-3 py-2">Driver-leads-{target} lag</th>
                  <th className="text-right px-3 py-2">ρ</th>
                  <th className="text-right px-3 py-2">Contemp ρ</th>
                  <th className="text-right px-3 py-2">Peak |ρ|</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <ScanRow key={r.driver} r={r} conf={conf} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {data && (
        <AIInterpretation
          page="causality-ccf"
          subject={`drivers of ${target}`}
          data={{
            mode: "scan",
            target,
            lookback: data.lookback,
            max_lag: data.max_lag,
            target_meta: data.target_meta,
            // Send only the top 15 by current sort so payload stays under ~6KB
            top_rows: rows.slice(0, 15),
          }}
        />
      )}
    </div>
  );
}

function ScanRow({ r, conf }: { r: CausalityCcfScanRow; conf: number }) {
  const xRhoColor = rhoColor(r.x_leads_rho, conf);
  const yRhoColor = rhoColor(r.y_leads_rho, conf);
  const peakColor = rhoColor(r.peak_rho, conf);
  const contColor = rhoColor(r.contemp_rho, conf);
  return (
    <tr className="border-t border-border hover:bg-surface-alt/40">
      <td className="px-3 py-2 font-bold font-data">
        {r.driver}
        <span className="text-text-muted text-xs font-normal ml-2">{r.label}</span>
      </td>
      <td className="px-3 py-2 text-xs text-text-muted">{r.category}</td>
      <td className="px-3 py-2 text-right font-data">{r.y_leads_lag}d</td>
      <td className={`px-3 py-2 text-right font-data ${yRhoColor}`}>{r.y_leads_rho.toFixed(3)}</td>
      <td className="px-3 py-2 text-right font-data">+{r.x_leads_lag}d</td>
      <td className={`px-3 py-2 text-right font-data ${xRhoColor}`}>{r.x_leads_rho.toFixed(3)}</td>
      <td className={`px-3 py-2 text-right font-data ${contColor}`}>{r.contemp_rho.toFixed(3)}</td>
      <td className={`px-3 py-2 text-right font-data ${peakColor}`}>
        {r.peak_lag >= 0 ? "+" : ""}{r.peak_lag}d  {r.peak_rho.toFixed(3)}
      </td>
    </tr>
  );
}

/* ─────────────────────────────────────────────────────────────
   Helpers
   ───────────────────────────────────────────────────────────── */

function rhoTone(rho: number, conf: number): "gain" | "loss" | "neutral" {
  if (Math.abs(rho) < conf) return "neutral";
  return rho > 0 ? "gain" : "loss";
}

function rhoColor(rho: number, conf: number): string {
  if (Math.abs(rho) < conf) return "text-text-muted";
  if (rho > 0) return Math.abs(rho) > 0.4 ? "text-gain" : "text-gain/80";
  return Math.abs(rho) > 0.4 ? "text-loss" : "text-loss/80";
}

function rhoForSort(r: CausalityCcfScanRow, mode: ScanSort): number {
  if (mode === "x_leads") return r.x_leads_rho;
  if (mode === "y_leads") return r.y_leads_rho;
  if (mode === "peak") return r.peak_rho;
  return r.contemp_rho;
}

function sparseCcfSample(lags: number[], ccf: (number | null)[]): { lag: number; rho: number | null }[] {
  // Send the lag profile to Claude as a sparse grid — every 2nd lag in the
  // tight band ±5, then every 5th lag out to the edges. Keeps payload small
  // without losing the shape.
  const out: { lag: number; rho: number | null }[] = [];
  for (let i = 0; i < lags.length; i++) {
    const l = lags[i];
    const dense = Math.abs(l) <= 5 && l % 2 === 0;
    const sparse = Math.abs(l) > 5 && l % 5 === 0;
    if (dense || sparse || l === 0) out.push({ lag: l, rho: ccf[i] });
  }
  return out;
}

function KpiCard({
  label, value, tone, subtitle,
}: {
  label: string;
  value: string;
  tone: "gain" | "loss" | "neutral";
  subtitle?: string;
}) {
  const colorClass = tone === "gain" ? "text-gain" : tone === "loss" ? "text-loss" : "text-text-muted";
  return (
    <div className="card p-3">
      <div className="text-[0.65rem] font-bold uppercase tracking-wider text-text-muted mb-1">{label}</div>
      <div className={`text-base font-bold font-data ${colorClass}`}>{value}</div>
      {subtitle && <div className="text-[0.65rem] text-text-muted mt-1">{subtitle}</div>}
    </div>
  );
}

function TransformBadge({
  symbol, transform, adfP,
}: {
  symbol: string;
  transform: string;
  adfP: number | null;
}) {
  const stationary = adfP != null && adfP < 0.05;
  return (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-surface-alt border border-border text-[0.7rem] font-data">
      <span className="font-bold">{symbol}:</span>
      <span>{TRANSFORM_LABEL[transform] ?? transform}</span>
      {adfP != null && (
        <span className={stationary ? "text-gain" : "text-warn"}>
          ADF p {adfP < 0.001 ? "< 0.001" : adfP.toFixed(3)}
        </span>
      )}
    </span>
  );
}

/* ─────────────────────────────────────────────────────────────
   GRANGER TAB
   ───────────────────────────────────────────────────────────── */

function GrangerTab({ universe }: { universe: CausalitySeriesMeta[] }) {
  const [mode, setMode] = useState<CcfMode>("pair");
  const [x, setX] = useState("M2");
  const [y, setY] = useState("SPX");
  const [target, setTarget] = useState("SPX");
  const [lookback, setLookback] = useState<CausalityLookback>("5Y");
  const [maxLag, setMaxLag] = useState(10);

  const pairQ = useQuery({
    queryKey: ["granger-pair", x, y, lookback, maxLag],
    queryFn: () => fetchGrangerPair(x, y, lookback, maxLag),
    enabled: mode === "pair" && x !== y,
    staleTime: 30 * 60_000,
  });

  const scanQ = useQuery({
    queryKey: ["granger-scan", target, lookback, maxLag],
    queryFn: () => fetchGrangerScan(target, lookback, maxLag),
    enabled: mode === "scan",
    staleTime: 30 * 60_000,
  });

  return (
    <div className="space-y-5">
      <div className="card p-4 flex flex-wrap items-end gap-4">
        <div className="inline-flex border border-border rounded overflow-hidden">
          <button
            className={`px-4 py-2 text-xs font-bold uppercase tracking-wider transition-colors ${
              mode === "pair" ? "bg-accent text-white" : "bg-surface-alt text-text-muted hover:bg-surface"
            }`}
            onClick={() => setMode("pair")}
          >
            Pair
          </button>
          <button
            className={`px-4 py-2 text-xs font-bold uppercase tracking-wider transition-colors ${
              mode === "scan" ? "bg-accent text-white" : "bg-surface-alt text-text-muted hover:bg-surface"
            }`}
            onClick={() => setMode("scan")}
          >
            Scan
          </button>
        </div>

        {mode === "pair" ? (
          <>
            <SymbolSelect label="Driver (X)" value={x} onChange={setX} universe={universe} exclude={y} />
            <SymbolSelect label="Target (Y)" value={y} onChange={setY} universe={universe} exclude={x} />
          </>
        ) : (
          <SymbolSelect label="Target" value={target} onChange={setTarget} universe={universe} />
        )}

        <label className="flex flex-col gap-1 text-xs">
          <span className="font-bold uppercase tracking-wider text-text-muted">Lookback</span>
          <select
            value={lookback}
            onChange={(e) => setLookback(e.target.value as CausalityLookback)}
            className="bg-surface-alt border border-border rounded px-2 py-1.5 text-sm font-data"
          >
            {LOOKBACKS.map((lb) => <option key={lb} value={lb}>{lb}</option>)}
          </select>
        </label>

        <label className="flex flex-col gap-1 text-xs">
          <span className="font-bold uppercase tracking-wider text-text-muted">Max lag (test depth)</span>
          <select
            value={maxLag}
            onChange={(e) => setMaxLag(Number(e.target.value))}
            className="bg-surface-alt border border-border rounded px-2 py-1.5 text-sm font-data"
          >
            {[5, 10, 15, 20, 30].map((m) => <option key={m} value={m}>1..{m}</option>)}
          </select>
        </label>
      </div>

      {mode === "pair" ? (
        <GrangerPairView query={pairQ} />
      ) : (
        <GrangerScanView query={scanQ} target={target} />
      )}
    </div>
  );
}

function GrangerPairView({ query }: { query: ReturnType<typeof useQuery<GrangerPair, Error>> }) {
  const { resolvedTheme } = useTheme();
  const isDark = resolvedTheme === "dark";
  const t = getChartTheme(isDark);
  const isMobile = useIsMobile();

  const data = query.data;
  const error = query.error?.message;

  // Build a side-by-side -log10(p) bar chart across lags for X→Y vs Y→X.
  // -log10(p) puts low p (strong causation) tall; p=0.05 ≈ 1.30, p=0.01 ≈ 2,
  // p=0.001 ≈ 3 — easy to read against horizontal threshold bands.
  const traces = useMemo(() => {
    if (!data) return [];
    const xToY = data.x_to_y.by_lag;
    const yToX = data.y_to_x.by_lag;
    const safeLog = (p: number) => -Math.log10(Math.max(p, 1e-10));
    return [
      {
        type: "bar" as const,
        name: `${data.x.symbol} → ${data.y.symbol}`,
        x: xToY.map((r) => r.lag),
        y: xToY.map((r) => safeLog(r.p_value)),
        marker: { color: t.accent },
        hovertemplate: "lag %{x}<br>−log10(p) %{y:.2f}<br>p %{customdata:.4f}<extra></extra>",
        customdata: xToY.map((r) => r.p_value),
      },
      {
        type: "bar" as const,
        name: `${data.y.symbol} → ${data.x.symbol}`,
        x: yToX.map((r) => r.lag),
        y: yToX.map((r) => safeLog(r.p_value)),
        marker: { color: t.spot },
        hovertemplate: "lag %{x}<br>−log10(p) %{y:.2f}<br>p %{customdata:.4f}<extra></extra>",
        customdata: yToX.map((r) => r.p_value),
      },
    ];
  }, [data, t]);

  return (
    <div className="space-y-5">
      {/* Verdict cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <VerdictCard
          title={data ? `${data.x.symbol} Granger-causes ${data.y.symbol}` : "X → Y"}
          verdict={data?.x_to_y.verdict}
          bestLag={data?.x_to_y.best.lag}
          pValue={data?.x_to_y.best.p_value}
          n={data?.x_to_y.n}
        />
        <VerdictCard
          title={data ? `${data.y.symbol} Granger-causes ${data.x.symbol}` : "Y → X"}
          verdict={data?.y_to_x.verdict}
          bestLag={data?.y_to_x.best.lag}
          pValue={data?.y_to_x.best.p_value}
          n={data?.y_to_x.n}
        />
      </div>

      {data && (
        <div className="flex flex-wrap items-center gap-2 text-xs text-text-muted">
          <TransformBadge symbol={data.x.symbol} transform={data.x.transform} adfP={data.x.adf_p} />
          <TransformBadge symbol={data.y.symbol} transform={data.y.transform} adfP={data.y.adf_p} />
          <span className="ml-auto font-data">n = {data.x_to_y.n} · max lag tested {data.max_lag}</span>
        </div>
      )}

      <ChartCard
        title="Granger F-test significance by lag"
        subtitle={"Bars show −log10(p). Horizontal lines mark p=0.05 (1.30), p=0.01 (2.00), p=0.001 (3.00). Tall bars = strong causation at that lag."}
        loading={query.isPending}
        error={error}
        height={CHART_HEIGHT.tall}
      >
        <Plot
          data={traces}
          layout={{
            ...getBaseLayout(t, {
              barmode: "group",
              showlegend: true,
              legend: { orientation: "h", yanchor: "bottom", y: 1.02, xanchor: "left", x: 0 },
              xaxis: { title: "Lag (business days)", gridcolor: t.grid },
              yaxis: { title: "−log10(p)", gridcolor: t.grid, zeroline: true, zerolinecolor: t.text, rangemode: "tozero" },
              shapes: [
                { type: "line", xref: "paper", yref: "y", x0: 0, x1: 1, y0: 1.301, y1: 1.301, line: { color: t.muted, width: 1, dash: "dot" } },
                { type: "line", xref: "paper", yref: "y", x0: 0, x1: 1, y0: 2.0, y1: 2.0, line: { color: t.spot, width: 1, dash: "dot" } },
                { type: "line", xref: "paper", yref: "y", x0: 0, x1: 1, y0: 3.0, y1: 3.0, line: { color: t.gain, width: 1, dash: "dot" } },
              ],
              annotations: [
                { x: 1, y: 1.301, xref: "paper", yref: "y", xanchor: "right", text: "p=0.05", font: { color: t.muted, size: 9 }, showarrow: false, bgcolor: t.plot },
                { x: 1, y: 2.0,   xref: "paper", yref: "y", xanchor: "right", text: "p=0.01", font: { color: t.spot,  size: 9 }, showarrow: false, bgcolor: t.plot },
                { x: 1, y: 3.0,   xref: "paper", yref: "y", xanchor: "right", text: "p=0.001", font: { color: t.gain, size: 9 }, showarrow: false, bgcolor: t.plot },
              ],
            }),
          }}
          config={getPlotConfig(isMobile)}
          style={{ width: "100%", height: CHART_HEIGHT.tall }}
        />
      </ChartCard>

      {data && (
        <AIInterpretation
          page="causality-granger"
          subject={`${data.x.symbol} ⇄ ${data.y.symbol}`}
          data={{
            mode: "pair",
            x: data.x,
            y: data.y,
            lookback: data.lookback,
            max_lag: data.max_lag,
            x_to_y: data.x_to_y,
            y_to_x: data.y_to_x,
          }}
        />
      )}
    </div>
  );
}

function VerdictCard({
  title, verdict, bestLag, pValue, n,
}: {
  title: string;
  verdict?: GrangerVerdict;
  bestLag?: number;
  pValue?: number;
  n?: number;
}) {
  const v = verdict ?? "none";
  const tone = v === "strong" ? "text-gain" : v === "moderate" ? "text-accent" : v === "weak" ? "text-warn" : "text-text-muted";
  const bgTone = v === "strong" ? "border-l-gain" : v === "moderate" ? "border-l-accent" : v === "weak" ? "border-l-warn" : "border-l-text-muted";
  return (
    <div className={`card p-4 border-l-4 ${bgTone}`}>
      <div className="text-xs font-bold uppercase tracking-wider text-text-muted mb-1">{title}</div>
      <div className={`text-2xl font-bold uppercase tracking-tight ${tone}`}>
        {verdict ? verdict : "—"}
      </div>
      <div className="text-xs text-text-muted mt-2 font-data">
        {bestLag != null
          ? `Best lag: ${bestLag} · p = ${pValue! < 1e-4 ? "< 0.0001" : pValue!.toFixed(4)} · n = ${n}`
          : "Awaiting data"}
      </div>
      <div className="text-[0.65rem] text-text-muted mt-2 leading-relaxed">
        Verdicts at α: strong p&lt;0.001 · moderate p&lt;0.01 · weak p&lt;0.05 · none p≥0.05.
      </div>
    </div>
  );
}

function GrangerScanView({
  query, target,
}: {
  query: ReturnType<typeof useQuery<GrangerScan, Error>>;
  target: string;
}) {
  const [bonfOnly, setBonfOnly] = useState(false);
  const [categoryFilter, setCategoryFilter] = useState<CausalityCategory | "all">("all");

  const data = query.data;
  const error = query.error?.message;

  const rows = useMemo(() => {
    if (!data) return [];
    let r = categoryFilter === "all" ? data.rows : data.rows.filter((row) => row.category === categoryFilter);
    if (bonfOnly) r = r.filter((row) => row.xy_p_bonf < 0.05);
    return r;
  }, [data, bonfOnly, categoryFilter]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-xs">
          <span className="font-bold uppercase tracking-wider text-text-muted">Category</span>
          <select
            value={categoryFilter}
            onChange={(e) => setCategoryFilter(e.target.value as CausalityCategory | "all")}
            className="bg-surface-alt border border-border rounded px-2 py-1.5 text-sm font-data"
          >
            <option value="all">All</option>
            {CATEGORY_ORDER.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </label>
        <label className="flex items-center gap-2 text-xs">
          <input
            type="checkbox"
            checked={bonfOnly}
            onChange={(e) => setBonfOnly(e.target.checked)}
            className="w-4 h-4 accent-accent"
          />
          <span className="font-bold uppercase tracking-wider text-text-muted">Bonferroni-significant only</span>
        </label>
        {data && (
          <span className="ml-auto text-xs text-text-muted font-data">
            {rows.length} drivers · m = {data.bonferroni_m} family tests
          </span>
        )}
      </div>

      <div className="card p-0 overflow-hidden">
        {query.isPending ? (
          <div className="h-[480px] bg-surface-alt/60 animate-pulse" />
        ) : error ? (
          <div className="p-4 text-sm text-loss">{error}</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-surface-alt sticky top-0">
                <tr className="text-xs font-bold uppercase tracking-wider text-text-muted">
                  <th className="text-left px-3 py-2">Driver</th>
                  <th className="text-left px-3 py-2">Category</th>
                  <th className="text-right px-3 py-2">Driver→{target} lag</th>
                  <th className="text-right px-3 py-2">p</th>
                  <th className="text-right px-3 py-2">p (Bonf)</th>
                  <th className="text-right px-3 py-2">{target}→Driver lag</th>
                  <th className="text-right px-3 py-2">p</th>
                  <th className="text-left px-3 py-2">Verdict</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => <GrangerRow key={r.driver} r={r} />)}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {data && (
        <AIInterpretation
          page="causality-granger"
          subject={`drivers of ${target}`}
          data={{
            mode: "scan",
            target,
            lookback: data.lookback,
            max_lag: data.max_lag,
            n_drivers_tested: data.n_drivers_tested,
            bonferroni_m: data.bonferroni_m,
            target_meta: data.target_meta,
            // Top 15 by raw xy_p (page sort default)
            top_rows: rows.slice(0, 15),
          }}
        />
      )}
    </div>
  );
}

function GrangerRow({ r }: { r: GrangerScanRow }) {
  const verdict: GrangerVerdict =
    r.xy_p_bonf < 0.001 ? "strong" :
    r.xy_p_bonf < 0.01  ? "moderate" :
    r.xy_p_bonf < 0.05  ? "weak" : "none";
  const tone =
    verdict === "strong" ? "text-gain" :
    verdict === "moderate" ? "text-accent" :
    verdict === "weak" ? "text-warn" : "text-text-muted";
  return (
    <tr className="border-t border-border hover:bg-surface-alt/40">
      <td className="px-3 py-2 font-bold font-data">
        {r.driver}
        <span className="text-text-muted text-xs font-normal ml-2">{r.label}</span>
      </td>
      <td className="px-3 py-2 text-xs text-text-muted">{r.category}</td>
      <td className="px-3 py-2 text-right font-data">{r.xy_best_lag}</td>
      <td className="px-3 py-2 text-right font-data">{r.xy_best_p < 1e-4 ? "<.0001" : r.xy_best_p.toFixed(4)}</td>
      <td className={`px-3 py-2 text-right font-data ${tone}`}>{r.xy_p_bonf < 1e-4 ? "<.0001" : r.xy_p_bonf.toFixed(4)}</td>
      <td className="px-3 py-2 text-right font-data">{r.yx_best_lag}</td>
      <td className="px-3 py-2 text-right font-data">{r.yx_best_p < 1e-4 ? "<.0001" : r.yx_best_p.toFixed(4)}</td>
      <td className={`px-3 py-2 text-xs uppercase font-bold ${tone}`}>{verdict}</td>
    </tr>
  );
}

/* ─────────────────────────────────────────────────────────────
   TRANSFER ENTROPY TAB
   ───────────────────────────────────────────────────────────── */

function TeTab({ universe }: { universe: CausalitySeriesMeta[] }) {
  const [mode, setMode] = useState<CcfMode>("pair");
  const [x, setX] = useState("VIX");
  const [y, setY] = useState("SPX");
  const [target, setTarget] = useState("SPX");
  const [lookback, setLookback] = useState<CausalityLookback>("5Y");

  const pairQ = useQuery({
    queryKey: ["te-pair", x, y, lookback],
    queryFn: () => fetchTePair(x, y, lookback, 3, 200),
    enabled: mode === "pair" && x !== y,
    staleTime: 30 * 60_000,
  });

  const scanQ = useQuery({
    queryKey: ["te-scan", target, lookback],
    queryFn: () => fetchTeScan(target, lookback, 3, 100),
    enabled: mode === "scan",
    staleTime: 30 * 60_000,
  });

  return (
    <div className="space-y-5">
      <div className="card p-4 flex flex-wrap items-end gap-4">
        <div className="inline-flex border border-border rounded overflow-hidden">
          <button
            className={`px-4 py-2 text-xs font-bold uppercase tracking-wider transition-colors ${
              mode === "pair" ? "bg-accent text-white" : "bg-surface-alt text-text-muted hover:bg-surface"
            }`}
            onClick={() => setMode("pair")}
          >
            Pair
          </button>
          <button
            className={`px-4 py-2 text-xs font-bold uppercase tracking-wider transition-colors ${
              mode === "scan" ? "bg-accent text-white" : "bg-surface-alt text-text-muted hover:bg-surface"
            }`}
            onClick={() => setMode("scan")}
          >
            Scan
          </button>
        </div>

        {mode === "pair" ? (
          <>
            <SymbolSelect label="Driver (X)" value={x} onChange={setX} universe={universe} exclude={y} />
            <SymbolSelect label="Target (Y)" value={y} onChange={setY} universe={universe} exclude={x} />
          </>
        ) : (
          <SymbolSelect label="Target" value={target} onChange={setTarget} universe={universe} />
        )}

        <label className="flex flex-col gap-1 text-xs">
          <span className="font-bold uppercase tracking-wider text-text-muted">Lookback</span>
          <select
            value={lookback}
            onChange={(e) => setLookback(e.target.value as CausalityLookback)}
            className="bg-surface-alt border border-border rounded px-2 py-1.5 text-sm font-data"
          >
            {LOOKBACKS.map((lb) => <option key={lb} value={lb}>{lb}</option>)}
          </select>
        </label>
      </div>

      {mode === "pair" ? <TePairView query={pairQ} /> : <TeScanView query={scanQ} target={target} />}
    </div>
  );
}

function TePairView({ query }: { query: ReturnType<typeof useQuery<TePair, Error>> }) {
  const { resolvedTheme } = useTheme();
  const isDark = resolvedTheme === "dark";
  const t = getChartTheme(isDark);
  const isMobile = useIsMobile();

  const data = query.data;
  const error = query.error?.message;

  // Bidirectional bar chart: TE(X→Y), TE(Y→X), with each direction's null_95th
  // marker overlaid as a dashed line. Bars above the line = significant.
  const traces = useMemo(() => {
    if (!data) return [];
    return [
      {
        type: "bar" as const,
        x: [`${data.x.symbol} → ${data.y.symbol}`, `${data.y.symbol} → ${data.x.symbol}`],
        y: [data.x_to_y.te_bits, data.y_to_x.te_bits],
        marker: {
          color: [
            data.x_to_y.te_bits > data.x_to_y.null_95th ? t.gain : t.muted,
            data.y_to_x.te_bits > data.y_to_x.null_95th ? t.gain : t.muted,
          ],
        },
        text: [
          `p ${data.x_to_y.p_value.toFixed(3)}`,
          `p ${data.y_to_x.p_value.toFixed(3)}`,
        ],
        textposition: "outside" as const,
        hovertemplate: "%{x}<br>TE %{y:.4f} bits<extra></extra>",
      },
      // Null 95th threshold markers — drawn as scatter points so they sit
      // ABOVE the bar but BELOW the text label, making the visual story
      // ('bar above the bar = significant') immediately obvious.
      {
        type: "scatter" as const,
        mode: "markers" as const,
        x: [`${data.x.symbol} → ${data.y.symbol}`, `${data.y.symbol} → ${data.x.symbol}`],
        y: [data.x_to_y.null_95th, data.y_to_x.null_95th],
        marker: { symbol: "line-ew-open" as const, size: 60, color: t.spot, line: { width: 2 } },
        name: "null 95th %ile",
        hovertemplate: "null 95th: %{y:.4f}<extra></extra>",
      },
    ];
  }, [data, t]);

  return (
    <div className="space-y-5">
      {/* Headline cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <div className="card p-4">
          <div className="text-xs font-bold uppercase tracking-wider text-text-muted mb-1">Net TE</div>
          <div className={`text-2xl font-bold font-data ${
            data && Math.abs(data.net_te) > 0.005
              ? data.net_te > 0 ? "text-gain" : "text-loss"
              : "text-text-muted"
          }`}>
            {data ? `${data.net_te >= 0 ? "+" : ""}${data.net_te.toFixed(4)}` : "—"}
          </div>
          <div className="text-[0.65rem] text-text-muted mt-2 leading-relaxed">
            TE(X→Y) − TE(Y→X). Positive ⇒ X is the dominant info source. Bits per sample.
          </div>
        </div>
        <div className="card p-4">
          <div className="text-xs font-bold uppercase tracking-wider text-text-muted mb-1">Direction</div>
          <div className="text-base font-bold font-data text-accent">
            {data?.dominant ?? "—"}
          </div>
          <div className="text-[0.65rem] text-text-muted mt-2 leading-relaxed">
            Both p&lt;0.05 ⇒ feedback loop. One ⇒ asymmetric driver.
          </div>
        </div>
        <div className="card p-4">
          <div className="text-xs font-bold uppercase tracking-wider text-text-muted mb-1">Sample</div>
          <div className="text-base font-bold font-data">
            {data ? `n = ${data.n} · ${data.n_perm} perms` : "—"}
          </div>
          <div className="text-[0.65rem] text-text-muted mt-2 leading-relaxed">
            Min resolvable p = 1/(n_perm+1) ≈ {data ? (1 / (data.n_perm + 1)).toFixed(3) : "—"}
          </div>
        </div>
      </div>

      {data && (
        <div className="flex flex-wrap items-center gap-2 text-xs text-text-muted">
          <TransformBadge symbol={data.x.symbol} transform={data.x.transform} adfP={data.x.adf_p} />
          <TransformBadge symbol={data.y.symbol} transform={data.y.transform} adfP={data.y.adf_p} />
          <span className="ml-auto font-data">{data.bins}-bin rank discretization</span>
        </div>
      )}

      <ChartCard
        title="Bidirectional Transfer Entropy"
        subtitle="Bar = observed TE (bits/sample). Orange marker = 95th percentile of permutation null. Bar above the marker ⇒ significant."
        loading={query.isPending}
        error={error}
        height={CHART_HEIGHT.normal}
      >
        <Plot
          data={traces}
          layout={{
            ...getBaseLayout(t, {
              showlegend: false,
              xaxis: { gridcolor: t.grid },
              yaxis: { title: "TE (bits/sample)", gridcolor: t.grid, rangemode: "tozero" },
            }),
          }}
          config={getPlotConfig(isMobile)}
          style={{ width: "100%", height: CHART_HEIGHT.normal }}
        />
      </ChartCard>

      {data && (
        <AIInterpretation
          page="causality-te"
          subject={`${data.x.symbol} ⇄ ${data.y.symbol}`}
          data={{
            mode: "pair",
            x: data.x,
            y: data.y,
            lookback: data.lookback,
            n: data.n,
            n_perm: data.n_perm,
            x_to_y: data.x_to_y,
            y_to_x: data.y_to_x,
            net_te: data.net_te,
            dominant: data.dominant,
          }}
        />
      )}
    </div>
  );
}

function TeScanView({
  query, target,
}: {
  query: ReturnType<typeof useQuery<TeScan, Error>>;
  target: string;
}) {
  const [significantOnly, setSignificantOnly] = useState(false);
  const [categoryFilter, setCategoryFilter] = useState<CausalityCategory | "all">("all");
  const [sort, setSort] = useState<"te_xy" | "net_te" | "te_yx">("te_xy");

  const data = query.data;
  const error = query.error?.message;

  const rows = useMemo(() => {
    if (!data) return [];
    let r = categoryFilter === "all" ? data.rows : data.rows.filter((row) => row.category === categoryFilter);
    if (significantOnly) r = r.filter((row) => row.te_xy > row.null_95th);
    // For net_te, sort by absolute value (largest asymmetry, in either direction).
    // For te_xy/te_yx, sort by raw value descending.
    const sorted = [...r];
    sorted.sort((a, b) => {
      if (sort === "net_te") return Math.abs(b.net_te) - Math.abs(a.net_te);
      if (sort === "te_yx") return b.te_yx - a.te_yx;
      return b.te_xy - a.te_xy;
    });
    return sorted;
  }, [data, significantOnly, categoryFilter, sort]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-xs">
          <span className="font-bold uppercase tracking-wider text-text-muted">Sort by</span>
          <select
            value={sort}
            onChange={(e) => setSort(e.target.value as "te_xy" | "net_te" | "te_yx")}
            className="bg-surface-alt border border-border rounded px-2 py-1.5 text-sm font-data"
          >
            <option value="te_xy">TE Driver→{target} (raw)</option>
            <option value="net_te">|Net TE| (asymmetric flow)</option>
            <option value="te_yx">TE {target}→Driver (reverse)</option>
          </select>
        </label>
        <label className="flex flex-col gap-1 text-xs">
          <span className="font-bold uppercase tracking-wider text-text-muted">Category</span>
          <select
            value={categoryFilter}
            onChange={(e) => setCategoryFilter(e.target.value as CausalityCategory | "all")}
            className="bg-surface-alt border border-border rounded px-2 py-1.5 text-sm font-data"
          >
            <option value="all">All</option>
            {CATEGORY_ORDER.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </label>
        <label className="flex items-center gap-2 text-xs">
          <input
            type="checkbox"
            checked={significantOnly}
            onChange={(e) => setSignificantOnly(e.target.checked)}
            className="w-4 h-4 accent-accent"
          />
          <span className="font-bold uppercase tracking-wider text-text-muted">Above null 95th only</span>
        </label>
        {data && (
          <span className="ml-auto text-xs text-text-muted font-data">
            {rows.length} drivers · {data.n_perm} perms · min p ≈ {(1 / (data.n_perm + 1)).toFixed(3)}
          </span>
        )}
      </div>

      <div className="card p-0 overflow-hidden">
        {query.isPending ? (
          <div className="h-[480px] bg-surface-alt/60 animate-pulse" />
        ) : error ? (
          <div className="p-4 text-sm text-loss">{error}</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-surface-alt sticky top-0">
                <tr className="text-xs font-bold uppercase tracking-wider text-text-muted">
                  <th className="text-left px-3 py-2">Driver</th>
                  <th className="text-left px-3 py-2">Category</th>
                  <th className="text-right px-3 py-2">TE Driver→{target}</th>
                  <th className="text-right px-3 py-2">p</th>
                  <th className="text-right px-3 py-2">Null 95th</th>
                  <th className="text-right px-3 py-2">TE {target}→Driver</th>
                  <th className="text-right px-3 py-2">p</th>
                  <th className="text-right px-3 py-2">Net TE</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => <TeRow key={r.driver} r={r} />)}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {data && (
        <AIInterpretation
          page="causality-te"
          subject={`drivers of ${target}`}
          data={{
            mode: "scan",
            target,
            lookback: data.lookback,
            n_perm: data.n_perm,
            n_drivers_tested: data.n_drivers_tested,
            target_meta: data.target_meta,
            top_rows: rows.slice(0, 15),
          }}
        />
      )}
    </div>
  );
}

function TeRow({ r }: { r: TeScanRow }) {
  const xySig = r.te_xy > r.null_95th;
  const xyTone = xySig && r.p_xy < 0.05 ? "text-gain" : "text-text-muted";
  const yxSig = r.p_yx < 0.05;
  const yxTone = yxSig ? "text-gain/80" : "text-text-muted";
  const netTone = Math.abs(r.net_te) < 0.005 ? "text-text-muted" : r.net_te > 0 ? "text-gain" : "text-loss";
  return (
    <tr className="border-t border-border hover:bg-surface-alt/40">
      <td className="px-3 py-2 font-bold font-data">
        {r.driver}
        <span className="text-text-muted text-xs font-normal ml-2">{r.label}</span>
      </td>
      <td className="px-3 py-2 text-xs text-text-muted">{r.category}</td>
      <td className={`px-3 py-2 text-right font-data ${xyTone}`}>{r.te_xy.toFixed(4)}</td>
      <td className="px-3 py-2 text-right font-data">{r.p_xy.toFixed(3)}</td>
      <td className="px-3 py-2 text-right font-data text-text-muted">{r.null_95th.toFixed(4)}</td>
      <td className={`px-3 py-2 text-right font-data ${yxTone}`}>{r.te_yx.toFixed(4)}</td>
      <td className="px-3 py-2 text-right font-data">{r.p_yx.toFixed(3)}</td>
      <td className={`px-3 py-2 text-right font-data ${netTone}`}>
        {r.net_te >= 0 ? "+" : ""}{r.net_te.toFixed(4)}
      </td>
    </tr>
  );
}

/* ─────────────────────────────────────────────────────────────
   VAR + IRF TAB
   ───────────────────────────────────────────────────────────── */

const VAR_DEFAULT_BASKET = ["SPX", "VIX", "UST10Y", "DXY", "WTI", "HY_OAS"];
const VAR_HORIZONS = [10, 20, 40, 60] as const;
const VAR_BASKET_MAX = 8;

function VarTab({ universe }: { universe: CausalitySeriesMeta[] }) {
  const [basket, setBasket] = useState<string[]>(VAR_DEFAULT_BASKET);
  const [lookback, setLookback] = useState<CausalityLookback>("5Y");
  const [horizon, setHorizon] = useState<number>(20);
  const [ic, setIc] = useState<"aic" | "bic">("aic");

  const q = useQuery({
    queryKey: ["var-basket", basket.join(","), lookback, horizon, ic],
    queryFn: () => fetchVarBasket({ symbols: basket, lookback, irf_horizon: horizon, ic, max_lag: 10 }),
    enabled: basket.length >= 2 && basket.length <= VAR_BASKET_MAX,
    staleTime: 30 * 60_000,
  });

  const data = q.data;
  const error = q.error?.message;

  return (
    <div className="space-y-5">
      <div className="card p-4 space-y-3">
        <BasketEditor basket={basket} setBasket={setBasket} universe={universe} />
        <div className="flex flex-wrap items-end gap-4">
          <label className="flex flex-col gap-1 text-xs">
            <span className="font-bold uppercase tracking-wider text-text-muted">Lookback</span>
            <select
              value={lookback}
              onChange={(e) => setLookback(e.target.value as CausalityLookback)}
              className="bg-surface-alt border border-border rounded px-2 py-1.5 text-sm font-data"
            >
              {LOOKBACKS.map((lb) => <option key={lb} value={lb}>{lb}</option>)}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs">
            <span className="font-bold uppercase tracking-wider text-text-muted">IRF horizon (days)</span>
            <select
              value={horizon}
              onChange={(e) => setHorizon(Number(e.target.value))}
              className="bg-surface-alt border border-border rounded px-2 py-1.5 text-sm font-data"
            >
              {VAR_HORIZONS.map((h) => <option key={h} value={h}>{h}</option>)}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs">
            <span className="font-bold uppercase tracking-wider text-text-muted">Lag selection</span>
            <select
              value={ic}
              onChange={(e) => setIc(e.target.value as "aic" | "bic")}
              className="bg-surface-alt border border-border rounded px-2 py-1.5 text-sm font-data"
            >
              <option value="aic">AIC (richer)</option>
              <option value="bic">BIC (parsimonious)</option>
            </select>
          </label>
          <button
            onClick={() => setBasket(VAR_DEFAULT_BASKET)}
            className="px-3 py-1.5 text-xs uppercase tracking-wider font-bold rounded bg-surface-alt hover:bg-surface border border-border"
          >
            Reset to default basket
          </button>
        </div>
      </div>

      {q.isPending && <div className="card p-6"><div className="h-4 w-48 rounded bg-surface-alt animate-pulse" /></div>}
      {error && <div className="card p-4 text-sm text-loss">{error}</div>}

      {data && (
        <>
          <VarSummary data={data} />
          <div className="flex flex-wrap items-center gap-2 text-xs text-text-muted">
            {data.symbols.map((s) => (
              <TransformBadge key={s} symbol={s} transform={data.transforms[s]} adfP={null} />
            ))}
          </div>
          <IrfChart data={data} />
          <FevdChart data={data} />
          <AIInterpretation
            page="causality-var"
            subject={`VAR basket ${data.symbols.join("/")}`}
            data={{
              symbols: data.symbols,
              lookback: data.lookback,
              n: data.n,
              ic: data.ic,
              selected_lag: data.selected_lag,
              best_aic_lag: data.best_aic_lag,
              best_bic_lag: data.best_bic_lag,
              irf_horizon: data.irf_horizon,
              transforms: data.transforms,
              fevd_targets: data.fevd_targets,
              // Trim IRF: send only h=0,1,2,5,10,horizon for each shock-response pair
              shocks_summary: data.shocks.map((s) => ({
                origin: s.origin,
                responses: s.responses.map((r) => ({
                  variable: r.variable,
                  sparse: [0, 1, 2, 5, 10, data.irf_horizon].filter((h) => h <= data.irf_horizon).map((h) => ({ h, v: r.values[h] })),
                })),
              })),
            }}
          />
        </>
      )}
    </div>
  );
}

function BasketEditor({
  basket, setBasket, universe,
}: {
  basket: string[];
  setBasket: (b: string[]) => void;
  universe: CausalitySeriesMeta[];
}) {
  // Default adder = first universe symbol not already in the basket. Without
  // this, the picker would default to a value already in basket — which is
  // filtered out of the options list, producing a blank-looking select.
  const firstAvailable = useMemo(
    () => universe.find((s) => !basket.includes(s.symbol))?.symbol ?? "",
    [universe, basket],
  );
  const [pickedAdder, setPickedAdder] = useState<string>("");
  // Derive effective adder: if the user has explicitly picked a symbol that
  // is still a valid choice, honor it; otherwise default to firstAvailable.
  // This avoids a useEffect+setState sync (which would cause cascading renders).
  const adder = pickedAdder && !basket.includes(pickedAdder) ? pickedAdder : firstAvailable;
  const setAdder = setPickedAdder;
  const tooFew = basket.length < 2;
  const tooMany = basket.length >= VAR_BASKET_MAX;
  return (
    <div className="space-y-2">
      <div className="text-xs font-bold uppercase tracking-wider text-text-muted">
        Basket ({basket.length}/{VAR_BASKET_MAX})
      </div>
      <div className="flex flex-wrap gap-2">
        {basket.map((s) => (
          <span key={s} className="inline-flex items-center gap-1 px-2 py-1 rounded bg-accent/15 text-accent text-xs font-bold font-data">
            {s}
            <button
              onClick={() => setBasket(basket.filter((x) => x !== s))}
              disabled={basket.length <= 2}
              className="text-accent hover:text-loss disabled:opacity-40"
              aria-label={`Remove ${s}`}
            >
              ×
            </button>
          </span>
        ))}
      </div>
      <div className="flex items-center gap-2">
        <SymbolSelect
          label="Add series"
          value={adder}
          onChange={setAdder}
          universe={universe.filter((s) => !basket.includes(s.symbol))}
        />
        <button
          onClick={() => {
            if (adder && !basket.includes(adder)) setBasket([...basket, adder]);
          }}
          disabled={tooMany || !adder || basket.includes(adder)}
          className="px-3 py-1.5 text-xs uppercase tracking-wider font-bold rounded bg-accent text-white hover:bg-accent-hover disabled:opacity-40 self-end"
        >
          Add
        </button>
      </div>
      {tooFew && <div className="text-xs text-warn">Need at least 2 symbols.</div>}
    </div>
  );
}

function VarSummary({ data }: { data: VarBasket }) {
  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
      <div className="card p-3">
        <div className="text-[0.65rem] font-bold uppercase tracking-wider text-text-muted mb-1">Selected lag ({data.ic.toUpperCase()})</div>
        <div className="text-2xl font-bold font-data text-accent">{data.selected_lag}</div>
        <div className="text-[0.65rem] text-text-muted mt-1">AIC best: {data.best_aic_lag} · BIC best: {data.best_bic_lag}</div>
      </div>
      <div className="card p-3">
        <div className="text-[0.65rem] font-bold uppercase tracking-wider text-text-muted mb-1">Sample</div>
        <div className="text-2xl font-bold font-data">n = {data.n}</div>
        <div className="text-[0.65rem] text-text-muted mt-1">{data.lookback} lookback</div>
      </div>
      <div className="card p-3">
        <div className="text-[0.65rem] font-bold uppercase tracking-wider text-text-muted mb-1">Variables (Cholesky order)</div>
        <div className="text-sm font-bold font-data">{data.symbols.join(" → ")}</div>
        <div className="text-[0.65rem] text-text-muted mt-1">Most exogenous → most endogenous</div>
      </div>
      <div className="card p-3">
        <div className="text-[0.65rem] font-bold uppercase tracking-wider text-text-muted mb-1">IRF horizon</div>
        <div className="text-2xl font-bold font-data">{data.irf_horizon}d</div>
        <div className="text-[0.65rem] text-text-muted mt-1">Forecast steps</div>
      </div>
    </div>
  );
}

function IrfChart({ data }: { data: VarBasket }) {
  const { resolvedTheme } = useTheme();
  const isDark = resolvedTheme === "dark";
  const t = getChartTheme(isDark);
  const isMobile = useIsMobile();

  const [shockOrigin, setShockOrigin] = useState<string>(data.symbols[0]);

  const shock = data.shocks.find((s) => s.origin === shockOrigin) ?? data.shocks[0];
  const horizons = useMemo(() => Array.from({ length: data.irf_horizon + 1 }, (_, h) => h), [data.irf_horizon]);

  const traces = useMemo(() => {
    if (!shock) return [];
    const palette = [t.accent, t.spot, t.gain, t.loss, t.hv20, t.hv60, t.muted, "#06b6d4"];
    return shock.responses.map((r, i) => ({
      type: "scatter" as const,
      mode: "lines" as const,
      name: r.variable,
      x: horizons,
      y: r.values,
      line: { color: palette[i % palette.length], width: r.variable === shock.origin ? 3 : 2 },
      hovertemplate: `${r.variable} h=%{x}: %{y:+.5f}<extra></extra>`,
    }));
  }, [shock, horizons, t]);

  return (
    <ChartCard
      title={`Orthogonalized IRF — shock to ${shockOrigin} (+1σ)`}
      subtitle="Each line shows how a variable responds over h business days to a +1σ shock in the selected origin. Units are post-stationarization (log-returns / first-diff)."
      action={
        <select
          value={shockOrigin}
          onChange={(e) => setShockOrigin(e.target.value)}
          className="bg-surface-alt border border-border rounded px-2 py-1 text-xs font-data"
        >
          {data.symbols.map((s) => <option key={s} value={s}>Shock: {s}</option>)}
        </select>
      }
      height={CHART_HEIGHT.tall}
    >
      <Plot
        data={traces}
        layout={{
          ...getBaseLayout(t, {
            showlegend: true,
            legend: { orientation: "h", yanchor: "bottom", y: 1.02, xanchor: "left", x: 0 },
            xaxis: { title: "Horizon h (business days)", gridcolor: t.grid },
            yaxis: { title: "Response", gridcolor: t.grid, zeroline: true, zerolinecolor: t.text },
          }),
        }}
        config={getPlotConfig(isMobile)}
        style={{ width: "100%", height: CHART_HEIGHT.tall }}
      />
    </ChartCard>
  );
}

function FevdChart({ data }: { data: VarBasket }) {
  const { resolvedTheme } = useTheme();
  const isDark = resolvedTheme === "dark";
  const t = getChartTheme(isDark);
  const isMobile = useIsMobile();

  const [target, setTarget] = useState<string>(data.symbols[data.symbols.length - 1]); // most-endogenous as default
  const [excludeSelf, setExcludeSelf] = useState(false);

  const fevd = data.fevd_targets.find((f) => f.target === target) ?? data.fevd_targets[0];

  // Stacked bar: x = horizon, one trace per shock-source, y = % of variance.
  // When excludeSelf=true, drop the target's own-shock and renormalize so the
  // remaining external drivers sum to 100%. This is the trader-useful view —
  // own-shock dominates by construction (50-70%) and crowds out external story.
  const traces = useMemo(() => {
    if (!fevd) return [];
    const palette = [t.accent, t.spot, t.gain, t.loss, t.hv20, t.hv60, t.muted, "#06b6d4"];
    const visibleSymbols = excludeSelf ? data.symbols.filter((s) => s !== target) : data.symbols;
    const renormFactor: Record<number, number> = {};
    if (excludeSelf) {
      for (const h of fevd.horizons) {
        const ext = visibleSymbols.reduce((acc, src) => acc + (h.contributions[src] ?? 0), 0);
        renormFactor[h.horizon] = ext > 0 ? 1 / ext : 1;
      }
    }
    return visibleSymbols.map((src) => {
      const colorIdx = data.symbols.indexOf(src) % palette.length;
      return {
        type: "bar" as const,
        name: src,
        x: fevd.horizons.map((h) => `${h}d`),
        y: fevd.horizons.map((h) => {
          const raw = (h.contributions[src] ?? 0) * 100;
          return excludeSelf ? raw * renormFactor[h.horizon] : raw;
        }),
        marker: { color: palette[colorIdx] },
        hovertemplate: `${src} → ${target} at %{x}: %{y:.1f}%<extra></extra>`,
      };
    });
  }, [fevd, data.symbols, target, t, excludeSelf]);

  return (
    <ChartCard
      title={`FEVD — share of ${target}'s forecast variance attributable to each shock`}
      subtitle={excludeSelf
        ? `Own-shock (${target}) excluded; bars renormalized to 100% across external drivers.`
        : "Each stacked bar sums to 100%. Read across to see how the mix shifts with horizon — a series whose share grows with h has long-lasting impact."}
      action={
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-1 text-xs text-text-muted">
            <input
              type="checkbox"
              checked={excludeSelf}
              onChange={(e) => setExcludeSelf(e.target.checked)}
              className="w-3.5 h-3.5 accent-accent"
            />
            <span className="font-bold uppercase tracking-wider">Exclude self</span>
          </label>
          <select
            value={target}
            onChange={(e) => setTarget(e.target.value)}
            className="bg-surface-alt border border-border rounded px-2 py-1 text-xs font-data"
          >
            {data.symbols.map((s) => <option key={s} value={s}>Target: {s}</option>)}
          </select>
        </div>
      }
      height={CHART_HEIGHT.normal}
    >
      <Plot
        data={traces}
        layout={{
          ...getBaseLayout(t, {
            barmode: "stack",
            showlegend: true,
            legend: { orientation: "h", yanchor: "bottom", y: 1.02, xanchor: "left", x: 0 },
            xaxis: { title: "Horizon", gridcolor: t.grid },
            yaxis: { title: "Variance share (%)", gridcolor: t.grid, range: [0, 100] },
          }),
        }}
        config={getPlotConfig(isMobile)}
        style={{ width: "100%", height: CHART_HEIGHT.normal }}
      />
    </ChartCard>
  );
}

/* ─────────────────────────────────────────────────────────────
   (Stubs removed — all 4 tabs now live)
   ───────────────────────────────────────────────────────────── */

/* ─────────────────────────────────────────────────────────────
   Page shell
   ───────────────────────────────────────────────────────────── */

export default function CausalityPage() {
  const [tab, setTab] = useState<Tab>("Lead/Lag (CCF)");

  const universeQ = useQuery({
    queryKey: ["causality-universe"],
    queryFn: fetchCausalityUniverse,
    staleTime: 60 * 60_000,
  });

  return (
    <div className="page-section space-y-5">
      <header>
        <h1 className="text-2xl font-bold tracking-tight">Causality</h1>
        <p className="text-sm text-text-muted mt-1 max-w-3xl">
          Macro causal research — does X drive Y, or do they just move together? This page runs progressively
          stronger tests across a curated 50+ macro universe (indices, sectors, factors, FX, rates, credit, commodities,
          vol, crypto, FRED). Start with lead/lag intuition, then escalate to predictive precedence (Granger),
          nonlinear info flow (Transfer Entropy), and shock dynamics (VAR + IRF).
        </p>
      </header>

      {/* Tab nav */}
      <div className="flex flex-wrap gap-1 border-b border-border">
        {TABS.map((label) => (
          <button
            key={label}
            onClick={() => setTab(label)}
            className={`px-3 py-2 text-xs font-bold uppercase tracking-wider transition-colors -mb-px border-b-2 ${
              tab === label
                ? "border-accent text-accent"
                : "border-transparent text-text-muted hover:text-text"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {universeQ.isPending && (
        <div className="card p-6">
          <div className="h-4 w-48 rounded bg-surface-alt animate-pulse mb-3" />
          <div className="h-3 w-full rounded bg-surface-alt animate-pulse" />
        </div>
      )}
      {universeQ.error && <div className="card p-4 text-loss text-sm">Failed to load universe: {universeQ.error.message}</div>}

      {universeQ.data && tab === "Lead/Lag (CCF)" && <CcfTab universe={universeQ.data.series} />}
      {universeQ.data && tab === "Granger" && <GrangerTab universe={universeQ.data.series} />}
      {universeQ.data && tab === "Transfer Entropy" && <TeTab universe={universeQ.data.series} />}
      {universeQ.data && tab === "VAR + IRF" && <VarTab universe={universeQ.data.series} />}
    </div>
  );
}

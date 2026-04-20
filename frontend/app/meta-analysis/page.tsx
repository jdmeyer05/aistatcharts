"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useTheme } from "next-themes";
import { Plot } from "@/components/plot";
import {
  fetchMetaPresets,
  fetchMetaForecasts,
  runMetaBacktest,
  runMetaGrid,
  type MetaBacktestResponse,
  type MetaGridResponse,
} from "@/lib/api";
import { getChartTheme, getBaseLayout, heatmapTrace, heatmapHeight } from "@/lib/chart-theme";
import { Metric } from "@/components/ui/metric";


const TABS = [
  "Equity Curves",
  "Allocations",
  "Forecasts",
  "Performance",
  "Institutional",
  "Statistical Tests",
  "Drawdown",
  "Rolling Analysis",
  "Universe Grid",
];

const BASE_METHODS = [
  "Tangency", "Robust Sharpe", "Min Variance", "Risk Parity",
  "Max Diversification", "HRP", "HERC (CVaR)", "HCAA (1/N)", "Equal Weight",
];

const METHOD_COLORS: Record<string, string> = {
  "Tangency": "#00d1ff",
  "Robust Sharpe": "#00e0d0",
  "Min Variance": "#00ff88",
  "Risk Parity": "#ffaa00",
  "Max Diversification": "#ff00ff",
  "HRP": "#88ccff",
  "HERC (CVaR)": "#cc88ff",
  "HCAA (1/N)": "#66aacc",
  "Equal Weight": "#888888",
  "SPY Buy & Hold": "#ffffff",
};

const BLEND_COLORS = ["#ff8800", "#ff3388", "#33ff88", "#8833ff"];

const BLEND_PRESETS: Record<string, Record<string, number>> = {
  "Robust + MaxDiv (50/50)": { "Robust Sharpe": 0.5, "Max Diversification": 0.5 },
  "HRP + HERC (50/50)": { "HRP": 0.5, "HERC (CVaR)": 0.5 },
  "Risk Parity + HRP + HERC (1/3)": { "Risk Parity": 1/3, "HRP": 1/3, "HERC (CVaR)": 1/3 },
};

const DEFAULT_GROUPS = ["Multi-Asset", "Sector ETFs"];

function colorFor(method: string, fallbackIdx: number = 0): string {
  return METHOD_COLORS[method] ?? BLEND_COLORS[fallbackIdx % BLEND_COLORS.length];
}

function fmtPct(v: number, digits = 1): string {
  return `${v >= 0 ? "+" : ""}${(v * 100).toFixed(digits)}%`;
}
function fmtPctNoSign(v: number, digits = 1): string {
  return `${(v * 100).toFixed(digits)}%`;
}

export default function MetaAnalysisPage() {
  const { resolvedTheme } = useTheme();
  const t = getChartTheme(resolvedTheme === "dark");
  const L = getBaseLayout(t);

  // Controls
  const presetsQ = useQuery({
    queryKey: ["meta-presets"],
    queryFn: fetchMetaPresets,
    staleTime: 24 * 60 * 60_000,
    retry: 2,
  });
  const PRESET_GROUPS = presetsQ.data?.presets ?? {};

  const [selectedGroups, setSelectedGroups] = useState<string[]>(DEFAULT_GROUPS);
  const [customTickers, setCustomTickers] = useState("");
  const [deselected, setDeselected] = useState<Set<string>>(new Set());
  const [lookback, setLookback] = useState<"1Y" | "2Y" | "3Y" | "5Y">("2Y");
  const [rebalance, setRebalance] = useState<"Monthly" | "Quarterly">("Monthly");
  const [estDays, setEstDays] = useState<126 | 189 | 252 | 504>(252);
  const [denoise, setDenoise] = useState(true);
  const [rankBy, setRankBy] = useState<"Sharpe" | "Ann. Return" | "Sortino" | "Calmar" | "Max DD">("Sharpe");
  const [blendPreset, setBlendPreset] = useState<string>("");
  const [activeTab, setActiveTab] = useState(0);
  const [allocMethod, setAllocMethod] = useState<string>("");
  const [topN, setTopN] = useState(10);

  // Build ticker list
  const activeTickers = useMemo(() => {
    const fromGroups = new Set<string>();
    for (const g of selectedGroups) {
      (PRESET_GROUPS[g] ?? []).forEach((t) => fromGroups.add(t));
    }
    const custom = customTickers.split(",").map((s) => s.trim().toUpperCase()).filter(Boolean);
    custom.forEach((t) => fromGroups.add(t));
    return [...fromGroups].filter((t) => !deselected.has(t)).sort();
  }, [selectedGroups, customTickers, deselected, PRESET_GROUPS]);

  const availableTickers = useMemo(() => {
    const s = new Set<string>();
    for (const g of selectedGroups) {
      (PRESET_GROUPS[g] ?? []).forEach((t) => s.add(t));
    }
    return [...s].sort();
  }, [selectedGroups, PRESET_GROUPS]);

  const toggleGroup = (g: string) => {
    setSelectedGroups((prev) => prev.includes(g) ? prev.filter((x) => x !== g) : [...prev, g]);
    setDeselected(new Set());
  };
  const toggleTicker = (t: string) => {
    setDeselected((prev) => {
      const n = new Set(prev);
      if (n.has(t)) n.delete(t);
      else n.add(t);
      return n;
    });
  };

  const blends = useMemo(() => {
    if (!blendPreset || !BLEND_PRESETS[blendPreset]) return undefined;
    return { [blendPreset]: BLEND_PRESETS[blendPreset] };
  }, [blendPreset]);

  const backtest = useMutation({
    mutationFn: () => runMetaBacktest({
      tickers: activeTickers,
      lookback,
      rebalance,
      est_days: estDays,
      denoise,
      blends,
      rank_by: rankBy,
    }),
    onSuccess: (d) => {
      if (!d.error && d.ranked_methods?.length) {
        setAllocMethod(d.ranked_methods[0]);
        setTopN(Math.min(d.ranked_methods.length, 10));
      }
    },
  });

  const result = backtest.data && !backtest.data.error ? backtest.data : null;

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Meta Analysis</h1>
        <p className="text-text-secondary text-sm mt-1">
          Walk-forward backtest of 9 allocation methods on a configurable universe. Compare equity curves, drawdowns, and rolling performance head-to-head with SPY as benchmark.
        </p>
      </div>

      {/* Universe selector */}
      <div className="card card-compact">
        <div className="metric-label mb-2">Universe — select preset groups</div>
        <div className="flex flex-wrap gap-2">
          {Object.keys(PRESET_GROUPS).map((g) => (
            <button
              key={g}
              onClick={() => toggleGroup(g)}
              className={`px-2.5 py-1 text-xs font-semibold rounded border transition-colors ${
                selectedGroups.includes(g)
                  ? "bg-accent text-white border-accent"
                  : "bg-surface text-text-muted border-border hover:border-accent/50"
              }`}
            >
              {g}
            </button>
          ))}
        </div>

        {availableTickers.length > 0 && (
          <div className="mt-3">
            <div className="metric-label mb-1">Active tickers — click to toggle</div>
            <div className="flex flex-wrap gap-1">
              {availableTickers.map((tk) => {
                const on = !deselected.has(tk);
                return (
                  <button
                    key={tk}
                    onClick={() => toggleTicker(tk)}
                    className={`px-2 py-0.5 text-xs rounded border font-data ${
                      on
                        ? "bg-accent/10 text-accent border-accent/40"
                        : "bg-surface text-text-muted border-border line-through"
                    }`}
                  >
                    {tk}
                  </button>
                );
              })}
            </div>
          </div>
        )}

        <div className="mt-3">
          <label className="metric-label">Add custom tickers (comma-separated)</label>
          <input
            value={customTickers}
            onChange={(e) => setCustomTickers(e.target.value)}
            placeholder="e.g. AAPL, TSLA, NVDA"
            className="mt-0.5 w-full px-3 py-1.5 border border-border rounded text-sm bg-surface font-data"
          />
        </div>

        <div className="mt-3 text-xs text-text-muted">
          <span className="font-semibold text-text">{activeTickers.length}</span> tickers selected
        </div>
      </div>

      {/* Backtest parameters */}
      <div className="card card-compact">
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <label className="metric-label">Lookback</label>
            <div className="flex gap-1 mt-0.5">
              {(["1Y", "2Y", "3Y", "5Y"] as const).map((l) => (
                <button
                  key={l}
                  onClick={() => setLookback(l)}
                  className={`px-3 py-1.5 text-xs rounded ${lookback === l ? "bg-accent text-white" : "bg-surface-alt text-text-muted"}`}
                >
                  {l}
                </button>
              ))}
            </div>
          </div>
          <div>
            <label className="metric-label">Rebalance</label>
            <div className="flex gap-1 mt-0.5">
              {(["Monthly", "Quarterly"] as const).map((r) => (
                <button
                  key={r}
                  onClick={() => setRebalance(r)}
                  className={`px-3 py-1.5 text-xs rounded ${rebalance === r ? "bg-accent text-white" : "bg-surface-alt text-text-muted"}`}
                >
                  {r}
                </button>
              ))}
            </div>
          </div>
          <div>
            <label className="metric-label">Estimation Window</label>
            <div className="flex gap-1 mt-0.5">
              {([126, 189, 252, 504] as const).map((d) => (
                <button
                  key={d}
                  onClick={() => setEstDays(d)}
                  className={`px-2.5 py-1.5 text-xs rounded font-data ${estDays === d ? "bg-accent text-white" : "bg-surface-alt text-text-muted"}`}
                >
                  {d}D
                </button>
              ))}
            </div>
          </div>
          <label className="flex items-center gap-2 text-xs cursor-pointer">
            <input type="checkbox" checked={denoise} onChange={(e) => setDenoise(e.target.checked)} className="accent-accent" />
            <span>Ledoit-Wolf Denoising</span>
          </label>
        </div>

        <div className="mt-3 flex flex-wrap items-end gap-3">
          <div>
            <label className="metric-label">Blended Portfolio (optional)</label>
            <select
              value={blendPreset}
              onChange={(e) => setBlendPreset(e.target.value)}
              className="mt-0.5 px-3 py-1.5 border border-border rounded text-sm bg-surface min-w-[260px]"
            >
              <option value="">— None —</option>
              {Object.keys(BLEND_PRESETS).map((b) => (
                <option key={b} value={b}>{b}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="metric-label">Rank by</label>
            <div className="flex gap-1 mt-0.5">
              {(["Sharpe", "Ann. Return", "Sortino", "Calmar", "Max DD"] as const).map((r) => (
                <button
                  key={r}
                  onClick={() => setRankBy(r)}
                  className={`px-2.5 py-1.5 text-xs rounded ${rankBy === r ? "bg-accent text-white" : "bg-surface-alt text-text-muted"}`}
                >
                  {r}
                </button>
              ))}
            </div>
          </div>
          <button
            onClick={() => backtest.mutate()}
            disabled={backtest.isPending || activeTickers.length < 3}
            className="px-6 py-2 bg-accent text-white font-semibold rounded-lg hover:bg-accent-hover disabled:opacity-50 text-sm"
          >
            {backtest.isPending ? `Running…` : `Run Meta Analysis (${activeTickers.length})`}
          </button>
        </div>
      </div>

      {backtest.isPending && (
        <div className="card text-center py-12">
          <div className="inline-block w-6 h-6 border-2 border-accent border-t-transparent rounded-full animate-spin" />
          <div className="text-xs text-text-muted mt-2">
            Running walk-forward backtest on {activeTickers.length} assets…
          </div>
        </div>
      )}

      {backtest.data?.error && (
        <div className="card border-loss text-loss text-sm">{backtest.data.error}</div>
      )}

      {!result && !backtest.isPending && !backtest.data?.error && (
        <div className="card text-center py-10 text-text-muted text-sm">
          <div className="font-semibold text-text mb-1">Meta analysis is idle</div>
          Pick a universe and click <span className="text-accent font-semibold">Run Meta Analysis</span> to compute walk-forward equity curves,
          institutional metrics, regime analysis, and De Prado statistical tests across all 9 allocation methods.
        </div>
      )}

      {result && (
        <>
          <ExecutiveSummary data={result} t={t} />

          <div className="flex gap-1 border-b border-border pb-1 overflow-x-auto">
            {TABS.map((tab, i) => (
              <button
                key={tab}
                onClick={() => setActiveTab(i)}
                className={`px-3 py-1.5 text-xs font-semibold rounded-t-md whitespace-nowrap transition-colors ${
                  activeTab === i ? "bg-accent text-white" : "text-text-muted hover:text-text hover:bg-surface-alt"
                }`}
              >
                {tab}
              </button>
            ))}
          </div>

          {activeTab === 0 && <EquityCurvesTab data={result} t={t} L={L} topN={topN} setTopN={setTopN} />}
          {activeTab === 1 && <AllocationsTab data={result} t={t} L={L} allocMethod={allocMethod} setAllocMethod={setAllocMethod} />}
          {activeTab === 2 && <ForecastsTab tickers={result.tickers} t={t} L={L} />}
          {activeTab === 3 && <PerformanceTab data={result} t={t} L={L} />}
          {activeTab === 4 && <InstitutionalTab data={result} t={t} L={L} />}
          {activeTab === 5 && <StatisticalTestsTab data={result} t={t} L={L} />}
          {activeTab === 6 && <DrawdownTab data={result} t={t} L={L} />}
          {activeTab === 7 && <RollingTab data={result} t={t} L={L} />}
          {activeTab === 8 && <UniverseGridTab lookback={lookback} rebalance={rebalance} estDays={estDays} denoise={denoise} t={t} L={L} />}
        </>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════
// EXECUTIVE SUMMARY
// ═══════════════════════════════════════════════════════════════

function ExecutiveSummary({ data, t }: { data: MetaBacktestResponse; t: ReturnType<typeof getChartTheme> }) {
  const best = data.metrics[0];
  const spy = data.metrics.find((m) => m.method === "SPY Buy & Hold");
  if (!best) return null;
  const beatsSpy = spy ? best.sharpe > spy.sharpe : null;
  const edgeColor = beatsSpy ? t.gain : beatsSpy === false ? t.loss : t.muted;

  return (
    <div className="card card-compact" style={{ borderColor: spy ? edgeColor : undefined }}>
      <div className="flex flex-wrap items-start gap-6">
        <div>
          <div className="metric-label">Best method</div>
          <div className="text-lg font-bold mt-0.5">{best.method}</div>
          <div className="text-xs text-text-muted mt-0.5">
            {data.n_assets} assets · {data.data_start} → {data.data_end} · {data.rebalance}
          </div>
        </div>
        <Metric label="Sharpe" value={best.sharpe.toFixed(2)} />
        <Metric label="Ann Return" value={fmtPct(best.ann_return)} deltaType={best.ann_return >= 0 ? "gain" : "loss"} />
        <Metric label="Max DD" value={fmtPctNoSign(best.max_dd)} deltaType="loss" />
        <Metric label="Sortino" value={best.sortino.toFixed(2)} />
        {spy && (
          <div className="ml-auto text-right">
            <div className="metric-label" style={{ color: edgeColor }}>
              {beatsSpy ? "OUTPERFORMS" : "UNDERPERFORMS"} SPY
            </div>
            <div className="mt-1 text-xs font-data text-text">
              Sharpe {best.sharpe.toFixed(2)} vs {spy.sharpe.toFixed(2)} · edge <span style={{ color: edgeColor }}>{(best.sharpe - spy.sharpe >= 0 ? "+" : "") + (best.sharpe - spy.sharpe).toFixed(2)}</span>
            </div>
            <div className="text-xs font-data text-text">
              Return {fmtPct(best.ann_return)} vs {fmtPct(spy.ann_return)} · edge <span style={{ color: edgeColor }}>{((best.ann_return - spy.ann_return) * 100 >= 0 ? "+" : "") + ((best.ann_return - spy.ann_return) * 100).toFixed(1)}pp</span>
            </div>
            <div className="text-xs font-data text-text">
              Max DD {fmtPctNoSign(best.max_dd)} vs {fmtPctNoSign(spy.max_dd)} · edge <span style={{ color: edgeColor }}>{((best.max_dd - spy.max_dd) * 100 >= 0 ? "+" : "") + ((best.max_dd - spy.max_dd) * 100).toFixed(1)}pp</span>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════
// TAB 1: EQUITY CURVES
// ═══════════════════════════════════════════════════════════════

function EquityCurvesTab({
  data, t, L, topN, setTopN,
}: {
  data: MetaBacktestResponse;
  t: ReturnType<typeof getChartTheme>;
  L: ReturnType<typeof getBaseLayout>;
  topN: number;
  setTopN: (n: number) => void;
}) {
  const top = data.ranked_methods.slice(0, topN);
  const traces = data.ranked_methods.map((method, rank) => {
    const eq = data.equity_curves[method] ?? [];
    const isTop = top.includes(method);
    return {
      x: data.dates,
      y: eq,
      type: "scatter" as const,
      mode: "lines" as const,
      name: `#${rank + 1} ${method}`,
      line: { color: colorFor(method, rank), width: rank < 3 ? 2.5 : 1.5 },
      visible: isTop ? (true as const) : ("legendonly" as const),
    };
  });

  return (
    <div className="space-y-4">
      <div className="card card-compact">
        <div className="flex items-center gap-4 text-xs">
          <span className="metric-label">Show top N methods:</span>
          <input
            type="range"
            min={3}
            max={data.ranked_methods.length}
            value={topN}
            onChange={(e) => setTopN(Number(e.target.value))}
            className="flex-1 max-w-[300px] accent-accent"
          />
          <span className="font-data font-semibold">{topN}</span>
        </div>
      </div>
      <div className="card">
        <Plot
          data={traces}
          layout={{
            height: 500,
            ...L,
            title: { text: `Walk-Forward Equity Curves — ${data.n_assets} Assets, ${data.rebalance} Rebalance`, font: { size: 13 } },
            yaxis: { title: "Portfolio Value ($100 start)", gridcolor: t.grid },
            xaxis: { gridcolor: t.grid },
            hovermode: "x unified",
            legend: { orientation: "h", y: -0.15, bgcolor: "transparent" },
            shapes: [{ type: "line", xref: "paper", x0: 0, x1: 1, y0: 100, y1: 100, line: { color: t.muted, dash: "dash", width: 1 } }],
          }}
          config={{ displayModeBar: false, responsive: true }}
          style={{ width: "100%" }}
        />
      </div>
      <div className="card card-compact">
        <div className="metric-label mb-2">Final values (top 5)</div>
        <div className="flex flex-wrap gap-4">
          {top.slice(0, 5).map((m) => {
            const eq = data.equity_curves[m] ?? [];
            const fv = eq[eq.length - 1] ?? 100;
            return (
              <Metric
                key={m}
                label={m}
                value={`$${fv.toFixed(0)}`}
                delta={`${fv - 100 >= 0 ? "+" : ""}${(fv - 100).toFixed(1)}%`}
                deltaType={fv >= 100 ? "gain" : "loss"}
              />
            );
          })}
        </div>
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════
// TAB 2: ALLOCATIONS
// ═══════════════════════════════════════════════════════════════

function AllocationsTab({
  data, t, L, allocMethod, setAllocMethod,
}: {
  data: MetaBacktestResponse;
  t: ReturnType<typeof getChartTheme>;
  L: ReturnType<typeof getBaseLayout>;
  allocMethod: string;
  setAllocMethod: (m: string) => void;
}) {
  const methods = data.ranked_methods.filter((m) => data.current_weights[m]);
  const active = methods.includes(allocMethod) ? allocMethod : methods[0] ?? "";
  const curW = data.current_weights[active] ?? {};
  const hist = data.weight_history[active] ?? [];
  const turnover = data.turnover[active] ?? [];

  const sorted = Object.entries(curW)
    .sort((a, b) => b[1] - a[1])
    .filter(([, w]) => w > 0.005);
  const hhi = Object.values(curW).reduce((s, w) => s + w * w, 0);
  const effectiveN = hhi > 0 ? 1 / hhi : 0;

  // Rebalance history heatmap — filter to significant positions
  const histTickers = useMemo(() => {
    const maxW = new Map<string, number>();
    for (const h of hist) {
      for (const [tk, w] of Object.entries(h.weights)) {
        maxW.set(tk, Math.max(maxW.get(tk) ?? 0, w));
      }
    }
    return [...maxW.entries()]
      .filter(([, w]) => w > 0.02)
      .sort((a, b) => b[1] - a[1])
      .map(([tk]) => tk);
  }, [hist]);

  const histDates = hist.map((h) => h.date);
  const histZ: number[][] = histTickers.map((tk) => hist.map((h) => (h.weights[tk] ?? 0) * 100));
  const histText: string[][] = histZ.map((row) => row.map((v) => (v > 0.5 ? v.toFixed(1) : "")));

  const avgTurnover = turnover.length > 0 ? turnover.reduce((s, v) => s + v.turnover, 0) / turnover.length : 0;
  const rebalPerYear = data.rebalance === "Monthly" ? 12 : 4;
  const annualCost = avgTurnover * rebalPerYear * data.cost_bps / 10000;

  // Side-by-side all methods
  const allCols = methods;
  const sideTickers = data.tickers.filter((tk) => allCols.some((m) => (data.current_weights[m]?.[tk] ?? 0) > 0.005));

  return (
    <div className="space-y-4">
      <div className="card card-compact flex flex-wrap items-center gap-3">
        <span className="metric-label">Method:</span>
        <select
          value={active}
          onChange={(e) => setAllocMethod(e.target.value)}
          className="px-3 py-1.5 border border-border rounded text-sm bg-surface min-w-[200px]"
        >
          {methods.map((m) => (
            <option key={m} value={m}>{m}</option>
          ))}
        </select>
        <span className="text-xs text-text-muted">Positions &gt; 0.5%: <span className="font-data text-text">{sorted.length}</span> / {data.n_assets}</span>
        {effectiveN > 0 && (
          <span className="text-xs text-text-muted">Effective N: <span className="font-data text-text">{effectiveN.toFixed(1)}</span></span>
        )}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="card lg:col-span-2">
          {sorted.length > 0 && (
            <Plot
              data={[{
                type: "pie" as const,
                labels: sorted.map(([tk]) => tk),
                values: sorted.map(([, w]) => w * 100),
                hole: 0.45,
                textinfo: "label+percent" as const,
                textfont: { size: 11 },
                marker: { line: { color: t.plot, width: 2 } },
              }]}
              layout={{
                height: 400,
                ...L,
                title: { text: `Current ${active} Portfolio (as of ${data.data_end})`, font: { size: 13 } },
                showlegend: false,
              }}
              config={{ displayModeBar: false, responsive: true }}
              style={{ width: "100%" }}
            />
          )}
        </div>
        <div className="card card-compact">
          <div className="metric-label mb-2">{active} — weights</div>
          <div className="space-y-1 text-xs font-data max-h-[380px] overflow-y-auto">
            {sorted.map(([tk, w]) => (
              <div key={tk} className="flex justify-between">
                <span className="font-semibold text-text">{tk}</span>
                <span className="text-text-muted">{(w * 100).toFixed(1)}%</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* All methods side-by-side */}
      <div className="card">
        <div className="text-sm font-semibold mb-2">Current weights — all methods</div>
        <div className="text-xs text-text-muted mb-2">Side-by-side comparison of what each method recommends today.</div>
        <div className="overflow-x-auto">
          <table className="data-table text-xs">
            <thead>
              <tr>
                <th>Ticker</th>
                {allCols.map((m) => <th key={m}>{m}</th>)}
              </tr>
            </thead>
            <tbody>
              {sideTickers.map((tk) => (
                <tr key={tk}>
                  <td className="font-semibold">{tk}</td>
                  {allCols.map((m) => {
                    const w = data.current_weights[m]?.[tk] ?? 0;
                    return (
                      <td key={m} className="font-data">
                        {w > 0.005 ? `${(w * 100).toFixed(1)}%` : "—"}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Rebalance heatmap */}
      {hist.length > 0 && histTickers.length > 0 && (
        <div className="card">
          <div className="text-sm font-semibold mb-2">Rebalance history — {active}</div>
          <Plot
            data={[{
              ...heatmapTrace(t, "sequential", { colorbarTitle: "Weight %" }),
              z: histZ,
              x: histDates,
              y: histTickers,
              zmin: 0,
              text: histText,
            }]}
            layout={{
              height: heatmapHeight(histTickers.length, { compact: true }),
              ...L,
              title: { text: `${active} — weight evolution across rebalances`, font: { size: 12 } },
              xaxis: { title: "Rebalance date", gridcolor: t.grid },
            }}
            config={{ displayModeBar: false, responsive: true }}
            style={{ width: "100%" }}
          />
        </div>
      )}

      {/* Turnover */}
      {turnover.length > 0 && (
        <div className="card">
          <div className="text-sm font-semibold mb-2">Portfolio turnover</div>
          <Plot
            data={[{
              type: "bar" as const,
              x: turnover.map((t) => t.date),
              y: turnover.map((t) => t.turnover * 100),
              marker: { color: colorFor(active) },
              text: turnover.map((t) => `${(t.turnover * 100).toFixed(0)}%`),
              textposition: "outside" as const,
            }]}
            layout={{
              height: 280,
              ...L,
              title: { text: `${active} — one-way turnover per rebalance`, font: { size: 12 } },
              yaxis: { title: "Turnover (%)", gridcolor: t.grid },
              xaxis: { gridcolor: t.grid },
            }}
            config={{ displayModeBar: false, responsive: true }}
            style={{ width: "100%" }}
          />
          <div className="text-xs text-text-muted mt-2">
            Average one-way turnover: <span className="font-data text-text">{(avgTurnover * 100).toFixed(1)}%</span> per rebalance. Estimated annual cost at {data.cost_bps}bps: <span className="font-data text-text">{(annualCost * 100).toFixed(2)}%</span>.
          </div>
        </div>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════
// TAB 3: FORECASTS (placeholder — forward estimates are a separate workflow)
// ═══════════════════════════════════════════════════════════════

function ForecastsTab({
  tickers, t, L,
}: {
  tickers: string[];
  t: ReturnType<typeof getChartTheme>;
  L: ReturnType<typeof getBaseLayout>;
}) {
  const run = useMutation({ mutationFn: () => fetchMetaForecasts(tickers) });
  const data = run.data && !run.data.error ? run.data : null;
  // Cache the sorted components once so the 4 inline chart .sort() calls
  // stay aligned and don't re-sort on every render.
  const sortedComponents = useMemo(
    () => (data ? [...data.components].sort((a, b) => a.blended_forecast - b.blended_forecast) : []),
    [data]
  );

  return (
    <div className="space-y-4">
      <div className="card card-compact">
        <div className="text-sm font-semibold mb-1">Forward return estimates</div>
        <div className="text-text-muted text-xs mb-3">
          Blends four components into an annualized return estimate per ticker: analyst target (40%), EPS momentum (30%, currently 0 until EPS endpoint is wired), valuation vs peer-median forward P/E (20%), and macro yield-curve/VIX overlay (10%). Clipped to ±50%.
        </div>
        <button
          onClick={() => run.mutate()}
          disabled={run.isPending || tickers.length === 0}
          className="px-5 py-2 bg-accent text-white font-semibold rounded-lg hover:bg-accent-hover disabled:opacity-50 text-sm"
        >
          {run.isPending ? "Fetching analyst + macro…" : `Compute forecasts (${tickers.length} tickers)`}
        </button>
      </div>

      {run.isPending && (
        <div className="card text-center py-8"><div className="inline-block w-5 h-5 border-2 border-accent border-t-transparent rounded-full animate-spin" /></div>
      )}

      {run.data?.error && <div className="card border-loss text-loss text-sm">{run.data.error}</div>}
      {run.isError && <div className="card border-loss text-loss text-sm">Request failed: {(run.error as Error).message}</div>}

      {data && (
        <>
          <div className="card card-compact">
            <div className="text-sm font-semibold mb-2">Macro context</div>
            <div className="flex flex-wrap gap-6">
              <Metric label="Yield curve (10Y−2Y)" value={data.macro.yield_curve !== undefined ? `${data.macro.yield_curve.toFixed(2)}%` : "—"} delta={data.macro.yield_curve !== undefined ? (data.macro.yield_curve > 0 ? "Positive" : "Inverted") : undefined} deltaType={data.macro.yield_curve !== undefined && data.macro.yield_curve > 0 ? "gain" : "loss"} />
              <Metric label="VIX" value={data.macro.vix !== undefined ? data.macro.vix.toFixed(1) : "—"} />
              <Metric label="Fed Funds" value={data.macro.fed_funds !== undefined ? `${data.macro.fed_funds.toFixed(2)}%` : "—"} />
              <Metric label="10Y Treasury" value={data.macro.ten_year !== undefined ? `${data.macro.ten_year.toFixed(2)}%` : "—"} />
            </div>
            <div className="text-xs text-text-muted mt-2">
              Macro overlay: <span className={data.macro_adj_pct > 0 ? "text-gain" : data.macro_adj_pct < 0 ? "text-loss" : "text-text-muted"}>
                {data.macro_adj_pct >= 0 ? "+" : ""}{data.macro_adj_pct.toFixed(0)}%
              </span>
              {" "}
              ({data.macro_adj_pct > 0 ? "favorable — positive yield curve, low VIX" : data.macro_adj_pct < 0 ? "unfavorable — inverted curve or elevated VIX" : "neutral conditions"})
            </div>
          </div>

          <div className="card">
            <div className="text-sm font-semibold mb-1">Blended forecast returns (annualized)</div>
            <Plot
              data={[{
                type: "bar" as const,
                orientation: "h" as const,
                y: sortedComponents.map((c) => c.ticker),
                x: sortedComponents.map((c) => c.blended_forecast),
                marker: {
                  color: sortedComponents.map((c) =>
                    c.blended_forecast > 5 ? t.gain : c.blended_forecast > 0 ? t.accent : t.loss
                  ),
                },
                text: sortedComponents.map((c) => `${c.blended_forecast >= 0 ? "+" : ""}${c.blended_forecast.toFixed(1)}%`),
                textposition: "outside" as const,
              }]}
              layout={{
                height: Math.max(320, data.components.length * 26),
                ...L,
                xaxis: { title: "Forecast return (%)", gridcolor: t.grid },
                yaxis: { gridcolor: t.grid },
                shapes: [{ type: "line", xref: "x" as const, yref: "paper" as const, x0: 0, x1: 0, y0: 0, y1: 1, line: { color: t.muted, dash: "dash", width: 1 } }],
              }}
              config={{ displayModeBar: false, responsive: true }}
              style={{ width: "100%" }}
            />
          </div>

          <div className="card">
            <div className="text-sm font-semibold mb-2">Component breakdown (contribution, %)</div>
            <Plot
              data={[
                { type: "bar" as const, orientation: "h" as const, y: data.components.map((c) => c.ticker), x: data.components.map((c) => c.analyst_implied * 0.4), name: "Analyst × 40%", marker: { color: t.accent } },
                { type: "bar" as const, orientation: "h" as const, y: data.components.map((c) => c.ticker), x: data.components.map((c) => c.eps_momentum * 0.3), name: "EPS × 30%", marker: { color: t.gain } },
                { type: "bar" as const, orientation: "h" as const, y: data.components.map((c) => c.ticker), x: data.components.map((c) => c.valuation * 0.2), name: "Valuation × 20%", marker: { color: t.spot } },
                { type: "bar" as const, orientation: "h" as const, y: data.components.map((c) => c.ticker), x: data.components.map((c) => c.macro * 0.1), name: "Macro × 10%", marker: { color: t.loss } },
              ]}
              layout={{
                height: Math.max(320, data.components.length * 26),
                ...L,
                barmode: "relative" as const,
                xaxis: { title: "Contribution (%)", gridcolor: t.grid },
                yaxis: { gridcolor: t.grid },
                legend: { orientation: "h", y: -0.18 },
              }}
              config={{ displayModeBar: false, responsive: true }}
              style={{ width: "100%" }}
            />
          </div>

          <div className="card">
            <div className="text-sm font-semibold mb-2">Historical vs forecast (annualized %)</div>
            <Plot
              data={[
                { type: "bar" as const, x: data.components.map((c) => c.ticker), y: data.components.map((c) => c.historical_annual), name: "Historical", marker: { color: t.muted } },
                { type: "bar" as const, x: data.components.map((c) => c.ticker), y: data.components.map((c) => c.blended_forecast), name: "Forecast", marker: { color: t.accent } },
              ]}
              layout={{ height: 360, ...L, barmode: "group" as const, yaxis: { title: "Return (%)", gridcolor: t.grid }, xaxis: { gridcolor: t.grid }, legend: { orientation: "h", y: -0.18 } }}
              config={{ displayModeBar: false, responsive: true }}
              style={{ width: "100%" }}
            />
          </div>

          {data.coverage.length === 0 && data.components.length > 0 && (
            <div className="card card-compact text-xs text-text-muted">
              No analyst coverage data available for these tickers. Blended forecast fell back to historical mean for the analyst component.
            </div>
          )}
          {data.coverage.length > 0 && (
            <div className="card">
              <div className="text-sm font-semibold mb-2">Analyst coverage &amp; valuation</div>
              <div className="overflow-x-auto">
                <table className="data-table text-xs">
                  <thead>
                    <tr><th>Ticker</th><th>Price</th><th>Target</th><th>Implied</th><th>Analysts</th><th>Rec</th><th>Fwd P/E</th><th>Earn Growth</th><th>Rev Growth</th><th>Sector</th></tr>
                  </thead>
                  <tbody>
                    {data.coverage.map((c) => (
                      <tr key={c.ticker}>
                        <td className="font-semibold">{c.ticker}</td>
                        <td className="font-data">{c.current_price !== null ? `$${c.current_price.toFixed(2)}` : "—"}</td>
                        <td className="font-data">{c.target_price !== null ? `$${c.target_price.toFixed(2)}` : "—"}</td>
                        <td className={`font-data ${(c.implied_return ?? 0) >= 0 ? "text-gain" : "text-loss"}`}>{c.implied_return !== null ? `${c.implied_return >= 0 ? "+" : ""}${c.implied_return.toFixed(1)}%` : "—"}</td>
                        <td className="font-data">{c.n_analysts ?? "—"}</td>
                        <td className="font-data">{c.rec_mean !== null ? c.rec_mean.toFixed(1) : "—"}</td>
                        <td className="font-data">{c.forward_pe !== null ? c.forward_pe.toFixed(1) : "—"}</td>
                        <td className={`font-data ${(c.earnings_growth ?? 0) >= 0 ? "text-gain" : "text-loss"}`}>{c.earnings_growth !== null ? `${c.earnings_growth >= 0 ? "+" : ""}${c.earnings_growth.toFixed(1)}%` : "—"}</td>
                        <td className={`font-data ${(c.revenue_growth ?? 0) >= 0 ? "text-gain" : "text-loss"}`}>{c.revenue_growth !== null ? `${c.revenue_growth >= 0 ? "+" : ""}${c.revenue_growth.toFixed(1)}%` : "—"}</td>
                        <td className="text-text-muted">{c.sector ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════
// TAB 4: PERFORMANCE
// ═══════════════════════════════════════════════════════════════

function PerformanceTab({ data, t, L }: {
  data: MetaBacktestResponse;
  t: ReturnType<typeof getChartTheme>;
  L: ReturnType<typeof getBaseLayout>;
}) {
  return (
    <div className="space-y-4">
      <div className="card">
        <div className="text-sm font-semibold mb-2">Performance comparison — ranked by {data.ranked_by}</div>
        <div className="overflow-x-auto">
          <table className="data-table text-xs">
            <thead>
              <tr>
                <th>Rank</th><th>Method</th><th>Ann Return</th><th>Ann Vol</th>
                <th>Sharpe</th><th>Sortino</th><th>Max DD</th><th>Calmar</th><th>Win Rate</th>
              </tr>
            </thead>
            <tbody>
              {data.metrics.map((m, i) => (
                <tr key={m.method}>
                  <td className="font-data">{i + 1}</td>
                  <td className="font-semibold">{m.method}</td>
                  <td className={`font-data ${m.ann_return >= 0 ? "text-gain" : "text-loss"}`}>{fmtPct(m.ann_return)}</td>
                  <td className="font-data">{fmtPctNoSign(m.ann_vol)}</td>
                  <td className="font-data">{m.sharpe.toFixed(2)}</td>
                  <td className="font-data">{m.sortino.toFixed(2)}</td>
                  <td className="font-data text-loss">{fmtPctNoSign(m.max_dd)}</td>
                  <td className="font-data">{m.calmar.toFixed(2)}</td>
                  <td className="font-data">{(m.win_rate * 100).toFixed(0)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <div className="card">
        <div className="text-sm font-semibold mb-2">Risk-adjusted return metrics</div>
        <Plot
          data={[
            { type: "bar" as const, x: data.metrics.map((m) => m.method), y: data.metrics.map((m) => m.sharpe), name: "Sharpe", marker: { color: t.accent } },
            { type: "bar" as const, x: data.metrics.map((m) => m.method), y: data.metrics.map((m) => m.sortino), name: "Sortino", marker: { color: t.gain } },
            { type: "bar" as const, x: data.metrics.map((m) => m.method), y: data.metrics.map((m) => m.calmar), name: "Calmar", marker: { color: t.spot } },
          ]}
          layout={{
            height: 380,
            ...L,
            barmode: "group" as const,
            yaxis: { title: "Ratio", gridcolor: t.grid },
            xaxis: { gridcolor: t.grid },
            legend: { orientation: "h", y: -0.3 },
          }}
          config={{ displayModeBar: false, responsive: true }}
          style={{ width: "100%" }}
        />
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════
// TAB 5: INSTITUTIONAL
// ═══════════════════════════════════════════════════════════════

function InstitutionalTab({ data, t, L }: {
  data: MetaBacktestResponse;
  t: ReturnType<typeof getChartTheme>;
  L: ReturnType<typeof getBaseLayout>;
}) {
  // Net-of-cost curves vs gross
  const netTraces = data.ranked_methods.flatMap((method, rank) => {
    const gross = data.equity_curves[method] ?? [];
    const net = data.net_curves[method] ?? [];
    const c = colorFor(method, rank);
    return [
      { x: data.dates, y: gross, type: "scatter" as const, mode: "lines" as const, name: `${method} (gross)`, line: { color: c, width: 1, dash: "dot" as const }, legendgroup: method, showlegend: false },
      { x: data.dates, y: net, type: "scatter" as const, mode: "lines" as const, name: `${method} (net)`, line: { color: c, width: rank < 3 ? 2 : 1 }, legendgroup: method },
    ];
  });

  // Cost drag table
  const costRows = data.ranked_methods.map((method) => {
    const g = data.metrics.find((m) => m.method === method);
    const n = data.net_metrics.find((m) => m.method === method);
    if (!g || !n) return null;
    return {
      method,
      gross_ret: g.ann_return,
      net_ret: n.ann_return,
      drag: (g.ann_return - n.ann_return) * 100,
      gross_sharpe: g.sharpe,
      net_sharpe: n.sharpe,
    };
  }).filter(Boolean) as Array<{ method: string; gross_ret: number; net_ret: number; drag: number; gross_sharpe: number; net_sharpe: number }>;

  // Regime pivot — methods x regimes
  const regimeMethods = [...new Set(data.regime_analysis.map((r) => r.method))];
  const regimeOrder: Array<"Bull" | "Recovery" | "Bear" | "Crisis"> = ["Bull", "Recovery", "Bear", "Crisis"];
  const regimeZ: (number | null)[][] = regimeMethods.map((m) =>
    regimeOrder.map((r) => data.regime_analysis.find((x) => x.method === m && x.regime === r)?.sharpe ?? null)
  );
  const regimeText: string[][] = regimeZ.map((row) => row.map((v) => (v === null ? "" : v.toFixed(2))));

  // Capture scatter
  const captureRows = data.metrics.filter((m) => m.info_ratio !== undefined && m.method !== "Equal Weight");

  return (
    <div className="space-y-4">
      {/* 1. Net-of-cost curves */}
      <div className="card">
        <div className="text-sm font-semibold mb-1">1. Net-of-cost equity curves</div>
        <div className="text-xs text-text-muted mb-2">Gross (dotted) vs net (solid) — {data.cost_bps}bps round-trip cost.</div>
        <Plot
          data={netTraces}
          layout={{
            height: 450,
            ...L,
            yaxis: { title: "Portfolio value ($100 start)", gridcolor: t.grid },
            xaxis: { gridcolor: t.grid },
            legend: { orientation: "h", y: -0.2 },
            hovermode: "x unified",
          }}
          config={{ displayModeBar: false, responsive: true }}
          style={{ width: "100%" }}
        />

        <table className="data-table text-xs mt-3">
          <thead>
            <tr><th>Method</th><th>Gross Return</th><th>Net Return</th><th>Cost Drag</th><th>Gross Sharpe</th><th>Net Sharpe</th></tr>
          </thead>
          <tbody>
            {costRows.map((r) => (
              <tr key={r.method}>
                <td className="font-semibold">{r.method}</td>
                <td className={`font-data ${r.gross_ret >= 0 ? "text-gain" : "text-loss"}`}>{fmtPct(r.gross_ret)}</td>
                <td className={`font-data ${r.net_ret >= 0 ? "text-gain" : "text-loss"}`}>{fmtPct(r.net_ret)}</td>
                <td className="font-data text-text-muted">{r.drag.toFixed(2)}%</td>
                <td className="font-data">{r.gross_sharpe.toFixed(2)}</td>
                <td className="font-data">{r.net_sharpe.toFixed(2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* 2. Regime analysis */}
      {regimeMethods.length > 0 && (
        <div className="card">
          <div className="text-sm font-semibold mb-1">2. Regime analysis</div>
          <div className="text-xs text-text-muted mb-2">Sharpe ratio by market regime (Equal Weight as market proxy).</div>
          <Plot
            data={[{
              ...heatmapTrace(t, "divergent", { colorbarTitle: "Sharpe" }),
              z: regimeZ,
              x: regimeOrder,
              y: regimeMethods,
              zmid: 0,
              text: regimeText,
            }]}
            layout={{ height: heatmapHeight(regimeMethods.length), ...L }}
            config={{ displayModeBar: false, responsive: true }}
            style={{ width: "100%" }}
          />

          <Plot
            data={regimeOrder.map((reg) => ({
              type: "bar" as const,
              x: regimeMethods,
              y: regimeMethods.map((m) => {
                const row = data.regime_analysis.find((r) => r.method === m && r.regime === reg);
                return row ? row.ann_return * 100 : 0;
              }),
              name: reg,
              marker: { color: { "Bull": t.gain, "Recovery": t.accent, "Bear": t.loss, "Crisis": t.spot }[reg] },
            }))}
            layout={{
              height: 340,
              ...L,
              barmode: "group" as const,
              title: { text: "Annualized return by regime (%)", font: { size: 12 } },
              yaxis: { title: "Return (%)", gridcolor: t.grid },
              xaxis: { gridcolor: t.grid },
              legend: { orientation: "h", y: -0.3 },
            }}
            config={{ displayModeBar: false, responsive: true }}
            style={{ width: "100%" }}
          />
        </div>
      )}

      {/* 3. Capture ratios & IR */}
      {captureRows.length > 0 && (
        <div className="card">
          <div className="text-sm font-semibold mb-1">3. Capture ratios &amp; information ratio</div>
          <div className="text-xs text-text-muted mb-2">Benchmarked to Equal Weight. Up capture &gt; 100% and down capture &lt; 100% = ideal.</div>

          <Plot
            data={captureRows.map((r) => ({
              type: "scatter" as const,
              mode: "markers+text" as const,
              x: [(r.up_capture ?? 1) * 100],
              y: [(r.down_capture ?? 1) * 100],
              text: [r.method],
              textposition: "top center" as const,
              textfont: { size: 10, color: t.text },
              marker: { size: 14, color: colorFor(r.method), line: { width: 1, color: t.muted } },
              showlegend: false,
              name: r.method,
            }))}
            layout={{
              height: 380,
              ...L,
              title: { text: "Up capture vs down capture (vs Equal Weight)", font: { size: 12 } },
              xaxis: { title: "Up capture (%)", gridcolor: t.grid },
              yaxis: { title: "Down capture (%)", gridcolor: t.grid },
              shapes: [
                { type: "line", xref: "paper", x0: 0, x1: 1, y0: 100, y1: 100, line: { color: t.muted, dash: "dash", width: 1 } },
                { type: "line", yref: "paper", y0: 0, y1: 1, x0: 100, x1: 100, line: { color: t.muted, dash: "dash", width: 1 } },
              ],
            }}
            config={{ displayModeBar: false, responsive: true }}
            style={{ width: "100%" }}
          />

          {/* IR bar */}
          <Plot
            data={[{
              type: "bar" as const,
              orientation: "h" as const,
              y: [...captureRows].sort((a, b) => (a.info_ratio ?? 0) - (b.info_ratio ?? 0)).map((r) => r.method),
              x: [...captureRows].sort((a, b) => (a.info_ratio ?? 0) - (b.info_ratio ?? 0)).map((r) => r.info_ratio ?? 0),
              marker: {
                color: [...captureRows].sort((a, b) => (a.info_ratio ?? 0) - (b.info_ratio ?? 0)).map((r) =>
                  (r.info_ratio ?? 0) > 0.5 ? t.gain : (r.info_ratio ?? 0) > 0 ? t.accent : t.loss
                ),
              },
              text: [...captureRows].sort((a, b) => (a.info_ratio ?? 0) - (b.info_ratio ?? 0)).map((r) => (r.info_ratio ?? 0).toFixed(2)),
              textposition: "outside" as const,
            }]}
            layout={{
              height: Math.max(220, captureRows.length * 30),
              ...L,
              title: { text: "Information ratio vs Equal Weight", font: { size: 12 } },
              xaxis: { title: "Information ratio", gridcolor: t.grid },
              yaxis: { gridcolor: t.grid },
            }}
            config={{ displayModeBar: false, responsive: true }}
            style={{ width: "100%" }}
          />

          <table className="data-table text-xs mt-3">
            <thead>
              <tr><th>Method</th><th>IR</th><th>Tracking Error</th><th>Up Capture</th><th>Down Capture</th></tr>
            </thead>
            <tbody>
              {captureRows.map((r) => (
                <tr key={r.method}>
                  <td className="font-semibold">{r.method}</td>
                  <td className="font-data">{(r.info_ratio ?? 0).toFixed(2)}</td>
                  <td className="font-data">{fmtPctNoSign(r.tracking_error ?? 0)}</td>
                  <td className="font-data">{((r.up_capture ?? 1) * 100).toFixed(0)}%</td>
                  <td className="font-data">{((r.down_capture ?? 1) * 100).toFixed(0)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* 4. Stress scenarios */}
      {data.stress_scenarios.length > 0 && (
        <div className="card">
          <div className="text-sm font-semibold mb-1">4. Stress scenario analysis</div>
          <div className="text-xs text-text-muted mb-2">Estimated portfolio loss (= beta × historical market drawdown).</div>
          <Plot
            data={[{
              ...heatmapTrace(t, "divergent", { colorbarTitle: "Est. loss %" }),
              z: data.stress_scenarios.map((s) => data.stress_scenario_names.map((sc) => s.scenarios[sc] ?? 0)),
              x: data.stress_scenario_names,
              y: data.stress_scenarios.map((s) => s.method),
              zmid: -15,
              text: data.stress_scenarios.map((s) => data.stress_scenario_names.map((sc) => `${(s.scenarios[sc] ?? 0).toFixed(1)}%`)),
            }]}
            layout={{ height: heatmapHeight(data.stress_scenarios.length), ...L }}
            config={{ displayModeBar: false, responsive: true }}
            style={{ width: "100%" }}
          />
        </div>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════
// TAB 6: STATISTICAL TESTS
// ═══════════════════════════════════════════════════════════════

function StatisticalTestsTab({ data, t, L }: {
  data: MetaBacktestResponse;
  t: ReturnType<typeof getChartTheme>;
  L: ReturnType<typeof getBaseLayout>;
}) {
  const pbo = data.pbo?.value;
  const pboVerdict = pbo === null || pbo === undefined ? "—"
    : pbo < 0.25 ? "Robust"
    : pbo < 0.50 ? "Borderline" : "Likely overfit";

  return (
    <div className="space-y-4">
      <div className="card card-compact text-xs text-text-muted">
        Standard backtesting overstates performance because of multiple-testing bias. These tests from Lopez de Prado&apos;s <em>Advances in Financial Machine Learning</em> quantify how much of observed performance is genuine.
      </div>

      {/* 1. DSR */}
      <div className="card">
        <div className="text-sm font-semibold mb-1">1. Deflated Sharpe Ratio</div>
        <div className="text-xs text-text-muted mb-2">
          Adjusts each method&apos;s Sharpe for the fact you tested <span className="font-data text-text">{data.n_methods_tested}</span> methods and picked the best. DSR &gt; 0.95 = significant.
        </div>
        <Plot
          data={[{
            type: "bar" as const,
            x: data.dsr_results.map((r) => r.method),
            y: data.dsr_results.map((r) => r.dsr),
            marker: {
              color: data.dsr_results.map((r) => r.dsr > 0.95 ? t.gain : r.dsr > 0.8 ? t.spot : t.loss),
            },
            text: data.dsr_results.map((r) => r.dsr.toFixed(2)),
            textposition: "outside" as const,
          }]}
          layout={{
            height: 340,
            ...L,
            yaxis: { title: "DSR p-value", gridcolor: t.grid, range: [0, 1.1] },
            xaxis: { gridcolor: t.grid },
            shapes: [{ type: "line", xref: "paper", x0: 0, x1: 1, y0: 0.95, y1: 0.95, line: { color: t.gain, dash: "dash", width: 1 } }],
          }}
          config={{ displayModeBar: false, responsive: true }}
          style={{ width: "100%" }}
        />
      </div>

      {/* 2. PBO */}
      <div className="card">
        <div className="text-sm font-semibold mb-1">2. Probability of Backtest Overfitting (PBO)</div>
        <div className="text-xs text-text-muted mb-2">
          CPCV: best in-sample method vs its out-of-sample rank. PBO &lt; 0.25 = robust. PBO &gt; 0.50 = likely overfit.
        </div>
        <div className="flex flex-wrap gap-6 mb-3">
          <Metric label="PBO" value={pbo !== null && pbo !== undefined ? `${(pbo * 100).toFixed(0)}%` : "—"} />
          <Metric label="Verdict" value={pboVerdict} deltaType={pboVerdict === "Robust" ? "gain" : pboVerdict === "Borderline" ? "neutral" : "loss"} />
          <Metric label="CPCV splits" value="6 blocks" />
        </div>
        {data.pbo.logits && data.pbo.logits.length > 0 && (
          <Plot
            data={[{
              type: "histogram" as const,
              x: data.pbo.logits,
              nbinsx: 20,
              marker: { color: t.accent },
            }]}
            layout={{
              height: 260,
              ...L,
              title: { text: "PBO logit distribution (mass left of 0 = not overfit)", font: { size: 12 } },
              xaxis: { title: "Logit(rank)", gridcolor: t.grid },
              yaxis: { title: "Count", gridcolor: t.grid },
              shapes: [{ type: "line", yref: "paper", y0: 0, y1: 1, x0: 0, x1: 0, line: { color: t.loss, dash: "dash", width: 1 } }],
            }}
            config={{ displayModeBar: false, responsive: true }}
            style={{ width: "100%" }}
          />
        )}
      </div>

      {/* 3. Sequential Bootstrap */}
      {data.bootstrap_ci.length > 0 && (
        <div className="card">
          <div className="text-sm font-semibold mb-1">3. Sequential bootstrap 90% CI</div>
          <div className="text-xs text-text-muted mb-2">20-day block bootstrap preserves serial dependence. Honest (wider) CI.</div>
          <Plot
            data={data.bootstrap_ci.map((r) => ({
              type: "scatter" as const,
              mode: "markers" as const,
              x: [r.method],
              y: [r.sharpe],
              error_y: {
                type: "data" as const,
                symmetric: false,
                array: [r.ci_high - r.sharpe],
                arrayminus: [r.sharpe - r.ci_low],
                color: r.significant ? t.gain : t.loss,
                thickness: 2,
                width: 10,
              },
              marker: { size: 10, color: r.significant ? t.gain : t.loss },
              name: r.method,
              showlegend: false,
            }))}
            layout={{
              height: 340,
              ...L,
              yaxis: { title: "Sharpe ratio", gridcolor: t.grid },
              xaxis: { gridcolor: t.grid },
              shapes: [{ type: "line", xref: "paper", x0: 0, x1: 1, y0: 0, y1: 0, line: { color: t.muted, dash: "dash", width: 1 } }],
            }}
            config={{ displayModeBar: false, responsive: true }}
            style={{ width: "100%" }}
          />
        </div>
      )}

      {/* 4. Min track record */}
      <div className="card">
        <div className="text-sm font-semibold mb-1">4. Minimum track record length</div>
        <div className="text-xs text-text-muted mb-2">
          Days needed to trust each method&apos;s Sharpe at 95% confidence. Actual: <span className="font-data text-text">{data.dsr_results[0]?.actual_days ?? 0}D</span>.
        </div>
        <Plot
          data={[{
            type: "bar" as const,
            x: data.dsr_results.map((r) => r.method),
            y: data.dsr_results.map((r) => {
              const maxActual = Math.max(...data.dsr_results.map((x) => x.actual_days));
              return r.min_track_record < 0 ? maxActual * 3 : Math.min(r.min_track_record, maxActual * 3);
            }),
            marker: {
              color: data.dsr_results.map((r) => r.sufficient_data ? t.gain : t.loss),
            },
            text: data.dsr_results.map((r) => r.min_track_record < 0 ? "∞" : `${r.min_track_record.toFixed(0)}D`),
            textposition: "outside" as const,
          }]}
          layout={{
            height: 320,
            ...L,
            yaxis: { title: "Days required", gridcolor: t.grid },
            xaxis: { gridcolor: t.grid },
            shapes: [{ type: "line", xref: "paper", x0: 0, x1: 1, y0: data.dsr_results[0]?.actual_days ?? 0, y1: data.dsr_results[0]?.actual_days ?? 0, line: { color: t.accent, dash: "dash", width: 1 } }],
          }}
          config={{ displayModeBar: false, responsive: true }}
          style={{ width: "100%" }}
        />
      </div>

      {/* Scorecard */}
      <div className="card">
        <div className="text-sm font-semibold mb-1">Overfitting scorecard</div>
        <div className="text-xs text-text-muted mb-2">Method must pass all 4 tests to be considered robust.</div>
        <div className="overflow-x-auto">
          <table className="data-table text-xs">
            <thead>
              <tr><th>Method</th><th>Sharpe</th><th>DSR</th><th>PBO</th><th>Bootstrap CI</th><th>Track Record</th><th>Score</th><th>Verdict</th></tr>
            </thead>
            <tbody>
              {data.scorecard.map((r) => (
                <tr key={r.method}>
                  <td className="font-semibold">{r.method}</td>
                  <td className="font-data">{r.sharpe.toFixed(2)}</td>
                  <td className={`font-data font-semibold ${r.dsr_pass ? "text-gain" : "text-loss"}`}>{r.dsr_pass ? "PASS" : "FAIL"}</td>
                  <td className={`font-data font-semibold ${r.pbo_pass ? "text-gain" : "text-loss"}`}>{r.pbo_pass ? "PASS" : "FAIL"}</td>
                  <td className={`font-data font-semibold ${r.boot_pass ? "text-gain" : "text-loss"}`}>{r.boot_pass ? "PASS" : "FAIL"}</td>
                  <td className={`font-data font-semibold ${r.trl_pass ? "text-gain" : "text-loss"}`}>{r.trl_pass ? "PASS" : "FAIL"}</td>
                  <td className="font-data">{r.score}/4</td>
                  <td className={`font-semibold ${r.verdict === "Robust" ? "text-gain" : r.verdict === "Credible" ? "text-accent" : r.verdict === "Suspect" ? "text-spot" : "text-loss"}`}>{r.verdict}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════
// TAB 7: DRAWDOWN
// ═══════════════════════════════════════════════════════════════

function DrawdownTab({ data, t, L }: {
  data: MetaBacktestResponse;
  t: ReturnType<typeof getChartTheme>;
  L: ReturnType<typeof getBaseLayout>;
}) {
  const ddTraces = data.ranked_methods.map((method, rank) => ({
    x: data.dates,
    y: data.drawdown_curves[method] ?? [],
    type: "scatter" as const,
    mode: "lines" as const,
    name: method,
    line: { color: colorFor(method, rank), width: 1.5 },
    fill: rank === 0 ? ("tozeroy" as const) : undefined,
    fillcolor: rank === 0 ? colorFor(method, rank) + "20" : undefined,
  }));

  const mddSorted = [...data.metrics].sort((a, b) => b.max_dd - a.max_dd); // least negative first

  return (
    <div className="space-y-4">
      <div className="card">
        <div className="text-sm font-semibold mb-1">Underwater equity curves</div>
        <div className="text-xs text-text-muted mb-2">How far each method falls from its peak. The bottom of each curve is the max drawdown.</div>
        <Plot
          data={ddTraces}
          layout={{
            height: 420,
            ...L,
            yaxis: { title: "Drawdown (%)", gridcolor: t.grid },
            xaxis: { gridcolor: t.grid },
            legend: { orientation: "h", y: -0.2 },
            hovermode: "x unified",
          }}
          config={{ displayModeBar: false, responsive: true }}
          style={{ width: "100%" }}
        />
      </div>

      <div className="card">
        <div className="text-sm font-semibold mb-1">Max drawdown comparison</div>
        <Plot
          data={[{
            type: "bar" as const,
            orientation: "h" as const,
            y: mddSorted.map((m) => m.method),
            x: mddSorted.map((m) => m.max_dd * 100),
            marker: { color: mddSorted.map((m) => colorFor(m.method)) },
            text: mddSorted.map((m) => `${(m.max_dd * 100).toFixed(1)}%`),
            textposition: "outside" as const,
          }]}
          layout={{
            height: Math.max(240, mddSorted.length * 28),
            ...L,
            xaxis: { title: "Max Drawdown (%)", gridcolor: t.grid },
            yaxis: { gridcolor: t.grid },
          }}
          config={{ displayModeBar: false, responsive: true }}
          style={{ width: "100%" }}
        />
      </div>

      <div className="card">
        <div className="text-sm font-semibold mb-2">Drawdown duration</div>
        <table className="data-table text-xs">
          <thead>
            <tr><th>Method</th><th>Longest DD (days)</th><th>Avg DD Duration</th><th>DD Episodes</th></tr>
          </thead>
          <tbody>
            {data.ranked_methods.map((method) => {
              const d = data.drawdown_duration[method];
              if (!d) return null;
              return (
                <tr key={method}>
                  <td className="font-semibold">{method}</td>
                  <td className="font-data">{d.longest_days}</td>
                  <td className="font-data">{d.avg_days.toFixed(0)} days</td>
                  <td className="font-data">{d.episodes}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════
// TAB 8: ROLLING ANALYSIS
// ═══════════════════════════════════════════════════════════════

function RollingTab({ data, t, L }: {
  data: MetaBacktestResponse;
  t: ReturnType<typeof getChartTheme>;
  L: ReturnType<typeof getBaseLayout>;
}) {
  // Rolling Sharpe (already restricted to top 5 server-side)
  const rollingTraces = Object.entries(data.rolling_sharpe).map(([method, rs], i) => ({
    x: rs.dates,
    y: rs.values,
    type: "scatter" as const,
    mode: "lines" as const,
    name: method,
    line: { color: colorFor(method, i), width: 1.5 },
  }));

  // Independence/redundancy from correlation matrix
  const { independent, redundant } = useMemo(() => {
    const ind: Array<[string, string, number]> = [];
    const red: Array<[string, string, number]> = [];
    for (let i = 0; i < data.method_corr_methods.length; i++) {
      for (let j = i + 1; j < data.method_corr_methods.length; j++) {
        const r = data.method_corr[i]?.[j] ?? 0;
        if (r < 0.7) ind.push([data.method_corr_methods[i], data.method_corr_methods[j], r]);
        else if (r > 0.9) red.push([data.method_corr_methods[i], data.method_corr_methods[j], r]);
      }
    }
    ind.sort((a, b) => a[2] - b[2]);
    red.sort((a, b) => b[2] - a[2]);
    return { independent: ind.slice(0, 5), redundant: red.slice(0, 5) };
  }, [data.method_corr, data.method_corr_methods]);

  // Excess vs EW
  const excessTraces = Object.entries(data.excess_vs_ew).map(([method, ex], i) => ({
    x: ex.dates,
    y: ex.values,
    type: "scatter" as const,
    mode: "lines" as const,
    name: method,
    line: { color: colorFor(method, i), width: 1.5 },
  }));

  return (
    <div className="space-y-4">
      <div className="card">
        <div className="text-sm font-semibold mb-1">Rolling 63-day Sharpe (top 5 methods)</div>
        <div className="text-xs text-text-muted mb-2">Methods that maintain consistent Sharpe across time are more reliable.</div>
        <Plot
          data={rollingTraces}
          layout={{
            height: 360,
            ...L,
            yaxis: { title: "Sharpe ratio", gridcolor: t.grid },
            xaxis: { gridcolor: t.grid },
            legend: { orientation: "h", y: -0.2 },
            shapes: [{ type: "line", xref: "paper", x0: 0, x1: 1, y0: 0, y1: 0, line: { color: t.muted, dash: "dash", width: 1 } }],
          }}
          config={{ displayModeBar: false, responsive: true }}
          style={{ width: "100%" }}
        />
      </div>

      {data.method_corr.length > 0 && (
        <div className="card">
          <div className="text-sm font-semibold mb-1">Method return correlation</div>
          <div className="text-xs text-text-muted mb-2">High correlation = methods are redundant. Low correlation = true diversification of approach.</div>
          <Plot
            data={[{
              ...heatmapTrace(t, "correlation", { colorbarTitle: "Corr" }),
              z: data.method_corr,
              x: data.method_corr_methods,
              y: data.method_corr_methods,
              zmid: 0.5, zmin: 0, zmax: 1,
              text: data.method_corr.map((row) => row.map((v) => v.toFixed(2))),
            }]}
            layout={{ height: heatmapHeight(data.method_corr_methods.length), ...L }}
            config={{ displayModeBar: false, responsive: true }}
            style={{ width: "100%" }}
          />

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-3">
            <div>
              <div className="metric-label">Most independent pairs (r &lt; 0.7)</div>
              {independent.length === 0 ? (
                <div className="text-xs text-text-muted mt-1">All methods are highly correlated (r &gt; 0.7).</div>
              ) : (
                <ul className="text-xs mt-1 space-y-1">
                  {independent.map(([a, b, r]) => (
                    <li key={`${a}-${b}`}><span className="font-semibold">{a}</span> vs <span className="font-semibold">{b}</span>: <span className="font-data">r = {r.toFixed(2)}</span></li>
                  ))}
                </ul>
              )}
            </div>
            <div>
              <div className="metric-label">Redundant pairs (r &gt; 0.9)</div>
              {redundant.length === 0 ? (
                <div className="text-xs text-text-muted mt-1">No highly redundant method pairs found.</div>
              ) : (
                <ul className="text-xs mt-1 space-y-1">
                  {redundant.map(([a, b, r]) => (
                    <li key={`${a}-${b}`}><span className="font-semibold">{a}</span> vs <span className="font-semibold">{b}</span>: <span className="font-data">r = {r.toFixed(2)}</span></li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        </div>
      )}

      {excessTraces.length > 0 && (
        <div className="card">
          <div className="text-sm font-semibold mb-1">Cumulative excess return vs Equal Weight</div>
          <div className="text-xs text-text-muted mb-2">Rising line = method is adding value. Falling = underperforming 1/N.</div>
          <Plot
            data={excessTraces}
            layout={{
              height: 360,
              ...L,
              yaxis: { title: "Excess return (%)", gridcolor: t.grid },
              xaxis: { gridcolor: t.grid },
              legend: { orientation: "h", y: -0.2 },
              shapes: [{ type: "line", xref: "paper", x0: 0, x1: 1, y0: 0, y1: 0, line: { color: t.muted, dash: "dash", width: 1 } }],
            }}
            config={{ displayModeBar: false, responsive: true }}
            style={{ width: "100%" }}
          />
        </div>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════════════════════════
// TAB 9: UNIVERSE GRID
// ═══════════════════════════════════════════════════════════════

function UniverseGridTab({
  lookback, rebalance, estDays, denoise, t, L,
}: {
  lookback: string;
  rebalance: string;
  estDays: number;
  denoise: boolean;
  t: ReturnType<typeof getChartTheme>;
  L: ReturnType<typeof getBaseLayout>;
}) {
  const grid = useMutation({
    mutationFn: () => runMetaGrid({ lookback, rebalance, est_days: estDays, denoise }),
  });

  const data: MetaGridResponse | undefined = grid.data && !grid.data.error ? grid.data : undefined;

  const buildGrid = (key: "sharpe" | "ann_return" | "max_dd" | "sortino") => {
    if (!data) return { z: [], x: [], y: [] as string[] };
    const rows = data.universes.map((u) =>
      data.methods.map((m) => {
        const r = data.grid.find((x) => x.universe === u && x.method === m);
        return r ? r[key] : null;
      })
    );
    return { z: rows, x: data.methods, y: data.universes };
  };

  const sharpeGrid = buildGrid("sharpe");
  const returnGrid = buildGrid("ann_return");
  const maxddGrid = buildGrid("max_dd");
  const sortinoGrid = buildGrid("sortino");

  const combos = data ? [...data.grid].sort((a, b) => b.sharpe - a.sharpe).slice(0, 15) : [];

  // Best method per universe & avg sharpe per method
  const bestPerUniverse = data?.universes.map((u) => {
    const rows = data.grid.filter((x) => x.universe === u);
    if (rows.length === 0) return null;
    const best = rows.reduce((a, b) => (b.sharpe > a.sharpe ? b : a));
    const worst = rows.reduce((a, b) => (b.sharpe < a.sharpe ? b : a));
    return { universe: u, best, worst, spread: best.sharpe - worst.sharpe };
  }).filter(Boolean) as Array<{ universe: string; best: MetaGridResponse["grid"][0]; worst: MetaGridResponse["grid"][0]; spread: number }>;

  const avgSharpe = data?.methods.map((m) => {
    const rows = data.grid.filter((x) => x.method === m);
    const vals = rows.map((r) => r.sharpe);
    const avg = vals.length > 0 ? vals.reduce((s, v) => s + v, 0) / vals.length : 0;
    const sd = vals.length > 1 ? Math.sqrt(vals.reduce((s, v) => s + (v - avg) ** 2, 0) / vals.length) : 0;
    return { method: m, avg, sd };
  }).sort((a, b) => b.avg - a.avg) ?? [];

  return (
    <div className="space-y-4">
      <div className="card">
        <div className="text-sm font-semibold mb-1">Universe grid — all presets backtested</div>
        <div className="text-xs text-text-muted mb-3">
          Runs walk-forward on every preset group independently. Shows which universe × method combination works best. Takes 30–90 seconds.
        </div>
        <button
          onClick={() => grid.mutate()}
          disabled={grid.isPending}
          className="px-5 py-2 bg-accent text-white font-semibold rounded-lg hover:bg-accent-hover disabled:opacity-50 text-sm"
        >
          {grid.isPending ? "Running grid…" : data ? "Re-run grid" : "Run grid analysis"}
        </button>
      </div>

      {grid.isPending && (
        <div className="card text-center py-12">
          <div className="inline-block w-6 h-6 border-2 border-accent border-t-transparent rounded-full animate-spin" />
          <div className="text-xs text-text-muted mt-2">Backtesting all preset groups…</div>
        </div>
      )}

      {grid.data?.error && <div className="card border-loss text-loss text-sm">{grid.data.error}</div>}

      {data && (
        <>
          <div className="card">
            <div className="text-sm font-semibold mb-2">Sharpe ratio — universe × method</div>
            <Plot
              data={[{
                ...heatmapTrace(t, "divergent", { colorbarTitle: "Sharpe" }),
                z: sharpeGrid.z as number[][],
                x: sharpeGrid.x,
                y: sharpeGrid.y,
                zmid: 0,
                text: (sharpeGrid.z as number[][]).map((row) => row.map((v) => v === null ? "" : v.toFixed(2))),
              }]}
              layout={{ height: heatmapHeight(sharpeGrid.y.length), ...L }}
              config={{ displayModeBar: false, responsive: true }}
              style={{ width: "100%" }}
            />
          </div>

          <div className="card">
            <div className="text-sm font-semibold mb-2">Annualized return — universe × method</div>
            <Plot
              data={[{
                ...heatmapTrace(t, "divergent", { colorbarTitle: "Return %" }),
                z: (returnGrid.z as number[][]).map((row) => row.map((v) => v === null ? null : v * 100)),
                x: returnGrid.x,
                y: returnGrid.y,
                zmid: 0,
                text: (returnGrid.z as number[][]).map((row) => row.map((v) => v === null ? "" : `${(v * 100).toFixed(1)}%`)),
              }]}
              layout={{ height: heatmapHeight(returnGrid.y.length), ...L }}
              config={{ displayModeBar: false, responsive: true }}
              style={{ width: "100%" }}
            />
          </div>

          <div className="card">
            <div className="text-sm font-semibold mb-2">Max drawdown — universe × method</div>
            <Plot
              data={[{
                ...heatmapTrace(t, "divergent", { colorbarTitle: "Max DD %" }),
                z: (maxddGrid.z as number[][]).map((row) => row.map((v) => v === null ? null : v * 100)),
                x: maxddGrid.x,
                y: maxddGrid.y,
                zmid: -15,
                text: (maxddGrid.z as number[][]).map((row) => row.map((v) => v === null ? "" : `${(v * 100).toFixed(1)}%`)),
              }]}
              layout={{ height: heatmapHeight(maxddGrid.y.length), ...L }}
              config={{ displayModeBar: false, responsive: true }}
              style={{ width: "100%" }}
            />
          </div>

          <div className="card">
            <div className="text-sm font-semibold mb-2">Sortino ratio — universe × method</div>
            <Plot
              data={[{
                ...heatmapTrace(t, "divergent", { colorbarTitle: "Sortino" }),
                z: sortinoGrid.z as number[][],
                x: sortinoGrid.x,
                y: sortinoGrid.y,
                zmid: 0,
                text: (sortinoGrid.z as number[][]).map((row) => row.map((v) => v === null ? "" : v.toFixed(2))),
              }]}
              layout={{ height: heatmapHeight(sortinoGrid.y.length), ...L }}
              config={{ displayModeBar: false, responsive: true }}
              style={{ width: "100%" }}
            />
          </div>

          <div className="card">
            <div className="text-sm font-semibold mb-2">Top 15 universe + method combinations</div>
            <table className="data-table text-xs">
              <thead>
                <tr><th>Rank</th><th>Universe</th><th>Method</th><th>Sharpe</th><th>Ann Return</th><th>Max DD</th><th>Sortino</th></tr>
              </thead>
              <tbody>
                {combos.map((c, i) => (
                  <tr key={`${c.universe}-${c.method}`}>
                    <td className="font-data">{i + 1}</td>
                    <td>{c.universe}</td>
                    <td className="font-semibold">{c.method}</td>
                    <td className="font-data">{c.sharpe.toFixed(2)}</td>
                    <td className={`font-data ${c.ann_return >= 0 ? "text-gain" : "text-loss"}`}>{fmtPct(c.ann_return)}</td>
                    <td className="font-data text-loss">{fmtPctNoSign(c.max_dd)}</td>
                    <td className="font-data">{c.sortino.toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="card">
            <div className="text-sm font-semibold mb-2">Best method per universe</div>
            <table className="data-table text-xs">
              <thead>
                <tr><th>Universe</th><th>Best Method</th><th>Best Sharpe</th><th>Worst Method</th><th>Worst Sharpe</th><th>Spread</th></tr>
              </thead>
              <tbody>
                {bestPerUniverse.map((r) => (
                  <tr key={r.universe}>
                    <td className="font-semibold">{r.universe}</td>
                    <td>{r.best.method}</td>
                    <td className="font-data">{r.best.sharpe.toFixed(2)}</td>
                    <td>{r.worst.method}</td>
                    <td className="font-data">{r.worst.sharpe.toFixed(2)}</td>
                    <td className="font-data">{r.spread.toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="card">
            <div className="text-sm font-semibold mb-2">Method consistency across universes</div>
            <div className="text-xs text-text-muted mb-2">Average Sharpe across all universes. Methods with high average AND low spread are the most reliable.</div>
            <Plot
              data={[{
                type: "bar" as const,
                x: avgSharpe.map((r) => r.method),
                y: avgSharpe.map((r) => r.avg),
                marker: { color: avgSharpe.map((r) => colorFor(r.method)) },
                error_y: { type: "data" as const, array: avgSharpe.map((r) => r.sd), color: t.spot, thickness: 1.5, width: 4 },
                text: avgSharpe.map((r) => r.avg.toFixed(2)),
                textposition: "outside" as const,
              }]}
              layout={{ height: 360, ...L, yaxis: { title: "Avg Sharpe", gridcolor: t.grid }, xaxis: { gridcolor: t.grid } }}
              config={{ displayModeBar: false, responsive: true }}
              style={{ width: "100%" }}
            />
          </div>
        </>
      )}
    </div>
  );
}

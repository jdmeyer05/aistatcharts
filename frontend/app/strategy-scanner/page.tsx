"use client";

import { useState, Fragment } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTheme } from "next-themes";
import { fetchStrategyScan, fetchOptimizeStrategy, fetchDeepScan, fetchComboScan, type StrategyScanResult, type StrategyScanResponse, type OptimizeResponse, type DeepScanResponse, type ComboScanResponse } from "@/lib/api";
import { getChartTheme } from "@/lib/chart-theme";
import { Metric } from "@/components/ui/metric";
import dynamic from "next/dynamic";

const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

const DEFAULT_TICKERS = ["SPY","QQQ","AAPL","MSFT","NVDA","TSLA","AMD","AMZN","META","GOOGL","NFLX","JPM","BA","GLD","TLT","SMH","XLF","EEM"];

const ALL_STRATEGIES: Record<string, string> = {
  // Trend following
  sma_cross: "SMA Cross (50/200)", ema_cross: "EMA Cross (12/26)", golden_cross: "Golden Cross",
  macd: "MACD (12/26/9)", donchian: "Donchian (20)", atr_trail: "ATR Trailing (3×)",
  momentum: "Momentum (12-1)", dual_mom: "Dual Momentum",
  // TA-Lib trend
  adx_di: "ADX+DI System", parabolic_sar: "Parabolic SAR", ichimoku: "Ichimoku Cloud", tema_cross: "TEMA Cross (12/26)",
  // Mean reversion
  rsi_ob_os: "RSI (30/70)", mean_rev: "BB Mean Reversion", bb_breakout: "BB Breakout", zscore_mr: "Z-Score MR",
  // TA-Lib mean reversion
  stochastic: "Stochastic K/D", cci: "CCI (20)", williams_r: "Williams %R",
  // Volume
  obv_divergence: "OBV Divergence",
  // Composite (academic optimal)
  trend_mr_composite: "★ Trend+RSI Composite", trend_bb_composite: "★ Trend+BB Composite",
  // Calendar anomalies
  calendar_tom: "Turn-of-Month", halloween: "Halloween Effect",
};

const DSR_COLOR = (dsr: number) => dsr >= 0.95 ? "text-gain font-bold" : dsr >= 0.85 ? "text-warn" : "text-text-muted";
const SIGNAL_COLOR: Record<string, string> = { Long: "text-gain", Short: "text-loss", Flat: "text-text-muted" };

export default function StrategyScanner() {
  const { resolvedTheme } = useTheme();
  const t = getChartTheme(resolvedTheme === "dark");
  const [tickers, setTickers] = useState(DEFAULT_TICKERS.join(", "));
  const [selectedStrats, setSelectedStrats] = useState(Object.keys(ALL_STRATEGIES));
  const [lookback, setLookback] = useState(1260);
  const [timeframe, setTimeframe] = useState("daily");
  const [commBps, setCommBps] = useState(5);
  const [slipBps, setSlipBps] = useState(5);
  const [minDsr, setMinDsr] = useState(0);
  const [data, setData] = useState<StrategyScanResponse | null>(null);
  const [tab, setTab] = useState<"all" | "active" | "significant">("all");
  const [sortBy, setSortBy] = useState<"excess_sharpe" | "dsr" | "sharpe" | "cagr" | "win_rate" | "avg_wf_sharpe">("excess_sharpe");
  const [pageMode, setPageMode] = useState<"scan" | "optimize" | "deep" | "combo">("scan");
  const [optTicker, setOptTicker] = useState("SPY");
  const [optTrials, setOptTrials] = useState(100);
  const [optData, setOptData] = useState<OptimizeResponse | null>(null);
  const [deepTimeframes, setDeepTimeframes] = useState(["daily", "60min"]);
  const [deepData, setDeepData] = useState<DeepScanResponse | null>(null);
  const [deepTab, setDeepTab] = useState(0);
  const [comboTicker, setComboTicker] = useState("SPY");
  const [comboSize, setComboSize] = useState(2);
  const [comboData, setComboData] = useState<ComboScanResponse | null>(null);
  const [expandedCombo, setExpandedCombo] = useState<number | null>(null);
  const [expandedIndividual, setExpandedIndividual] = useState<string | null>(null);

  const scan = useMutation({
    mutationFn: () => {
      const tkList = tickers.split(",").map(t => t.trim().toUpperCase()).filter(Boolean);
      return fetchStrategyScan(tkList, selectedStrats, lookback, commBps, slipBps, minDsr, timeframe);
    },
    onSuccess: (d) => setData(d),
  });

  const optimize = useMutation({
    mutationFn: () => fetchOptimizeStrategy(optTicker.trim().toUpperCase(), selectedStrats, lookback, timeframe, optTrials, commBps, slipBps),
    onSuccess: (d) => setOptData(d),
  });

  const comboScan = useMutation({
    mutationFn: () => fetchComboScan(comboTicker.trim().toUpperCase(), selectedStrats, lookback, timeframe, comboSize, commBps, slipBps),
    onSuccess: (d) => setComboData(d),
  });

  const deepScan = useMutation({
    mutationFn: () => {
      const tkList = tickers.split(",").map(t => t.trim().toUpperCase()).filter(Boolean);
      return fetchDeepScan(tkList, selectedStrats, deepTimeframes, commBps, slipBps);
    },
    onSuccess: (d) => setDeepData(d),
  });

  const displayResults = (() => {
    if (!data) return [];
    let results = [...data.results];
    if (tab === "active") results = results.filter(r => r.current_signal !== "Flat");
    else if (tab === "significant") results = results.filter(r => r.dsr >= 0.95);
    results.sort((a, b) => ((b as any)[sortBy] ?? 0) - ((a as any)[sortBy] ?? 0));
    return results;
  })();

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Strategy Scanner</h1>
        <p className="text-text-secondary text-sm mt-1">
          {pageMode === "scan" && "Scan 24 strategies × N tickers. Ranked by Deflated Sharpe (multiple-testing corrected)."}
          {pageMode === "optimize" && "Bayesian optimization (Optuna TPE). Finds optimal parameters per strategy. Objective = walk-forward OOS Sharpe."}
          {pageMode === "combo" && "Test all pairs/triples of strategies using AND logic. Find which combinations outperform individual strategies."}
          {pageMode === "deep" && "Full universe scan across all timeframes. Meta-analysis: strategy rankings, heatmap, correlation, portfolio recommendation."}
        </p>
      </div>

      {/* Mode toggle */}
      <div className="flex gap-2">
        <button onClick={() => setPageMode("scan")} className={`px-4 py-2 text-sm font-semibold rounded-lg ${pageMode === "scan" ? "bg-accent text-white" : "text-text-muted border border-border hover:bg-surface-alt"}`}>
          Multi-Ticker Scan
        </button>
        <button onClick={() => setPageMode("optimize")} className={`px-4 py-2 text-sm font-semibold rounded-lg ${pageMode === "optimize" ? "bg-accent text-white" : "text-text-muted border border-border hover:bg-surface-alt"}`}>
          Parameter Optimizer
        </button>
        <button onClick={() => setPageMode("combo")} className={`px-4 py-2 text-sm font-semibold rounded-lg ${pageMode === "combo" ? "bg-accent text-white" : "text-text-muted border border-border hover:bg-surface-alt"}`}>
          Combinations
        </button>
        <button onClick={() => setPageMode("deep")} className={`px-4 py-2 text-sm font-semibold rounded-lg ${pageMode === "deep" ? "bg-accent text-white" : "text-text-muted border border-border hover:bg-surface-alt"}`}>
          Deep Scan
        </button>
      </div>

      {pageMode === "scan" && (<>
      {/* Controls */}
      <div className="card">
        <div className="space-y-3">
          <div>
            <label className="metric-label">Tickers</label>
            <textarea value={tickers} onChange={e => setTickers(e.target.value)} rows={2}
              className="w-full mt-1 px-3 py-2 border border-border rounded-lg text-sm font-data bg-surface" />
          </div>

          <div>
            <label className="metric-label mb-1 block">Strategies ({selectedStrats.length}/{Object.keys(ALL_STRATEGIES).length})</label>
            <div className="flex flex-wrap gap-2">
              {Object.entries(ALL_STRATEGIES).map(([key, label]) => (
                <label key={key} className="flex items-center gap-1 text-xs cursor-pointer">
                  <input type="checkbox" checked={selectedStrats.includes(key)}
                    onChange={e => setSelectedStrats(prev => e.target.checked ? [...prev, key] : prev.filter(s => s !== key))}
                    className="rounded border-border" />
                  {label}
                </label>
              ))}
              <button onClick={() => setSelectedStrats(Object.keys(ALL_STRATEGIES))} className="text-[0.6rem] text-accent hover:underline ml-2">All</button>
              <button onClick={() => setSelectedStrats([])} className="text-[0.6rem] text-text-muted hover:underline">None</button>
            </div>
          </div>

          <div className="flex flex-wrap gap-3 items-end">
            <div>
              <label className="metric-label">Timeframe</label>
              <div className="flex gap-1 mt-1">
                {(["daily", "60min", "15min", "5min"] as const).map(tf => (
                  <button key={tf} onClick={() => setTimeframe(tf)}
                    className={`px-2.5 py-1.5 text-xs rounded-md font-semibold ${timeframe === tf ? "bg-accent text-white" : "text-text-muted hover:bg-surface-alt border border-border"}`}>
                    {tf === "daily" ? "Daily" : tf}
                  </button>
                ))}
              </div>
            </div>
            <div>
              <label className="metric-label">Lookback{timeframe !== "daily" ? " (days)" : ""}</label>
              <input type="number" value={lookback} onChange={e => setLookback(+e.target.value)} step={timeframe === "daily" ? 252 : 5} min={timeframe === "daily" ? 252 : 5} max={timeframe === "daily" ? 5040 : 180}
                className="w-24 mt-1 px-2 py-1.5 border border-border rounded-lg text-sm font-data bg-surface" />
            </div>
            <div>
              <label className="metric-label">Comm (bps)</label>
              <input type="number" value={commBps} onChange={e => setCommBps(+e.target.value)} step={1}
                className="w-16 mt-1 px-2 py-1.5 border border-border rounded-lg text-sm font-data bg-surface" />
            </div>
            <div>
              <label className="metric-label">Slip (bps)</label>
              <input type="number" value={slipBps} onChange={e => setSlipBps(+e.target.value)} step={1}
                className="w-16 mt-1 px-2 py-1.5 border border-border rounded-lg text-sm font-data bg-surface" />
            </div>
            <div>
              <label className="metric-label">Min DSR</label>
              <input type="number" value={minDsr} onChange={e => setMinDsr(+e.target.value)} step={0.05} min={0} max={1}
                className="w-20 mt-1 px-2 py-1.5 border border-border rounded-lg text-sm font-data bg-surface" />
            </div>
          </div>

          <button onClick={() => scan.mutate()} disabled={scan.isPending}
            className="w-full py-3 bg-accent text-white font-bold rounded-lg hover:bg-accent-hover disabled:opacity-50 transition-colors">
            {scan.isPending
              ? `Scanning ${tickers.split(",").filter(Boolean).length} tickers × ${selectedStrats.length} strategies (${timeframe})...`
              : `Scan ${tickers.split(",").filter(Boolean).length} × ${selectedStrats.length} = ${tickers.split(",").filter(Boolean).length * selectedStrats.length} combinations (${timeframe})`}
          </button>
          {timeframe !== "daily" && (
            <p className="text-xs text-text-muted mt-1">
              Intraday data from Polygon. {timeframe === "5min" ? "Max ~60 days" : timeframe === "15min" ? "Max ~60 days" : "Max ~180 days"} lookback.
              Annualized with {timeframe === "60min" ? "1,638" : timeframe === "15min" ? "6,552" : "19,656"} bars/year.
            </p>
          )}
          {scan.isPending && (
            <div className="flex items-center justify-center gap-3 py-2">
              <div className="w-5 h-5 border-2 border-accent border-t-transparent rounded-full animate-spin" />
              <span className="text-sm text-text-muted">Running backtests in parallel... (2-5 minutes)</span>
            </div>
          )}
        </div>
      </div>

      {scan.isError && <div className="card border-loss/30 bg-loss-bg text-loss text-sm">Scan failed: {(scan.error as Error).message}</div>}

      {data && (<>
        {/* Summary */}
        <div className="card card-compact">
          <div className="flex flex-wrap gap-6">
            <Metric label="Tested" value={String(data.n_tested)} />
            <Metric label="Significant" value={`${data.n_significant} (${data.n_tested > 0 ? Math.round(data.n_significant / data.n_tested * 100) : 0}%)`} />
            <Metric label="Active Signals" value={String(data.n_active_signals)} />
            <Metric label="Hit Rate" value={`${data.n_tested > 0 ? Math.round(data.n_significant / data.n_tested * 100) : 0}%`} />
          </div>
          <p className="text-xs text-text-muted mt-2">
            DSR corrected for {data.n_tested} simultaneous tests. Only DSR ≥ 95% survives the multiple testing correction. A {Math.round(data.n_significant / Math.max(data.n_tested, 1) * 100)}% hit rate means {data.n_significant === 0 ? "no genuine edge found — try different tickers or timeframe." : `${data.n_significant} combinations have statistically verified edge.`}
          </p>
        </div>

        {/* Active Signals Highlight */}
        {data.active_signals.length > 0 && (
          <div className="card border-accent/30">
            <div className="flex items-center justify-between mb-2">
              <div className="metric-label">Active Signals Right Now</div>
              <span className="text-xs text-text-muted">{data.active_signals.filter(s => s.dsr >= 0.85).length} with DSR ≥ 85%</span>
            </div>
            {data.active_signals.filter(s => s.dsr >= 0.85).length === 0 && (
              <p className="text-xs text-text-muted py-2">No active signals with DSR ≥ 85%. Lower the threshold or scan more tickers.</p>
            )}
            <div className="space-y-1">
              {data.active_signals.filter(s => s.dsr >= 0.85).slice(0, 10).map((s, i) => (
                <div key={i} className={`flex items-center gap-3 text-sm font-data px-3 py-2 rounded border ${i === 0 ? "border-accent bg-accent-light" : "border-border"}`}>
                  <span className="font-bold w-12">{s.ticker}</span>
                  <span className="text-text-muted w-32 text-xs truncate">{ALL_STRATEGIES[s.strategy] || s.strategy}</span>
                  <span className={`font-bold w-14 ${SIGNAL_COLOR[s.current_signal] || ""}`}>{s.current_signal} {s.signal_days}d</span>
                  <span className={`text-xs font-bold ${s.excess_sharpe > 0.3 ? "text-gain" : s.excess_sharpe > 0 ? "text-warn" : "text-loss"}`}>α {s.excess_sharpe}</span>
                  <span className={`text-xs ${DSR_COLOR(s.dsr)}`}>DSR {s.dsr_pct}%</span>
                  <span className="text-xs text-text-muted">vs B&H {s.bh_sharpe}</span>
                  {s.avg_wf_sharpe != null && <span className={`text-xs ${s.avg_wf_sharpe > 0 ? "text-gain" : "text-loss"}`}>WF {s.avg_wf_sharpe}</span>}
                  {i === 0 && <span className="badge badge-gain text-[0.55rem] ml-auto">TOP</span>}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Tab filter + sort */}
        <div className="card card-compact">
          <div className="flex items-center gap-3 flex-wrap">
            <div className="flex gap-1">
              {([["all", "All Results"], ["significant", "Significant (DSR ≥ 95%)"], ["active", "Active Signals"]] as const).map(([key, label]) => (
                <button key={key} onClick={() => setTab(key)}
                  className={`px-3 py-1 text-xs rounded font-semibold ${tab === key ? "bg-accent text-white" : "text-text-muted hover:bg-surface-alt"}`}>
                  {label}
                </button>
              ))}
            </div>
            <div className="flex items-center gap-1.5 ml-auto">
              <span className="text-[0.65rem] text-text-muted uppercase">Sort</span>
              <select value={sortBy} onChange={e => setSortBy(e.target.value as any)}
                className="text-xs border border-border rounded px-1.5 py-1 bg-surface">
                <option value="excess_sharpe">Excess Sharpe</option>
                <option value="dsr">DSR</option>
                <option value="sharpe">Sharpe</option>
                <option value="cagr">CAGR</option>
                <option value="win_rate">Win Rate</option>
                <option value="avg_wf_sharpe">WF Sharpe</option>
              </select>
            </div>
            <span className="text-xs text-text-muted">{displayResults.length} results</span>
          </div>
        </div>

        {/* Results Table */}
        <div className="card overflow-x-auto">
          <table className="data-table text-xs">
            <thead>
              <tr>
                <th>Ticker</th><th>Strategy</th><th>Signal</th><th>Days</th>
                <th>Excess Sharpe</th><th>Sharpe</th><th>B&H Sharpe</th><th>DSR %</th>
                <th>CAGR</th><th>B&H CAGR</th><th>Max DD</th>
                <th>Win Rate</th><th>Active%</th><th>WF Sharpe</th>
              </tr>
            </thead>
            <tbody>
              {displayResults.slice(0, 100).map((r, i) => (
                <tr key={`${r.ticker}-${r.strategy}`} className={r.dsr >= 0.95 ? "bg-gain/5" : ""}>
                  <td className="font-bold">{r.ticker}</td>
                  <td className="text-text-muted">{ALL_STRATEGIES[r.strategy] || r.strategy}</td>
                  <td className={`font-semibold ${SIGNAL_COLOR[r.current_signal] || ""}`}>{r.current_signal}</td>
                  <td className="font-data">{r.signal_days}d</td>
                  <td className={`font-data font-bold ${r.excess_sharpe > 0.3 ? "text-gain" : r.excess_sharpe > 0 ? "text-warn" : "text-loss"}`}>{r.excess_sharpe}</td>
                  <td className="font-data">{r.sharpe}</td>
                  <td className="font-data text-text-muted">{r.bh_sharpe}</td>
                  <td className={`font-data ${DSR_COLOR(r.dsr)}`}>{r.dsr_pct}%</td>
                  <td className={`font-data ${r.cagr > 0 ? "text-gain" : "text-loss"}`}>{r.cagr}%</td>
                  <td className="font-data text-text-muted">{r.bh_cagr}%</td>
                  <td className="font-data text-loss">{r.max_dd}%</td>
                  <td className="font-data">{r.win_rate}%</td>
                  <td className="font-data">{r.pct_active}%</td>
                  <td className={`font-data ${r.avg_wf_sharpe != null && r.avg_wf_sharpe > 0 ? "text-gain" : "text-loss"}`}>
                    {r.avg_wf_sharpe ?? "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {displayResults.length > 100 && <p className="text-xs text-text-muted mt-2">Showing first 100 of {displayResults.length}.</p>}
        </div>

        {/* Interpretation */}
        <div className="card card-compact text-xs text-text-muted space-y-1">
          <p><strong>Excess Sharpe (α)</strong> = strategy Sharpe minus buy-and-hold Sharpe. Positive = strategy adds value over just holding. This is the primary metric.</p>
          <p><strong>DSR</strong> = analytical metric for statistical significance after {data.n_tested} tests. <strong>WF Sharpe</strong> = out-of-sample validation. <strong>Active%</strong> = time in market.</p>
          <p><strong>Reliable signal:</strong> Excess Sharpe &gt; 0 + DSR ≥ 85% + WF Sharpe &gt; 0. Everything else is noise or underperforms buy-and-hold.</p>
        </div>
      </>)}
      </>)}

      {/* ═══ OPTIMIZER MODE ═══ */}
      {pageMode === "optimize" && (<>
        <div className="card">
          <div className="space-y-3">
            <div className="flex flex-wrap gap-3 items-end">
              <div>
                <label className="metric-label">Ticker</label>
                <input type="text" value={optTicker} onChange={e => setOptTicker(e.target.value.toUpperCase())}
                  className="w-24 mt-1 px-2 py-1.5 border border-border rounded-lg text-sm font-data bg-surface" />
              </div>
              <div>
                <label className="metric-label">Trials per Strategy</label>
                <input type="number" value={optTrials} onChange={e => setOptTrials(+e.target.value)} step={25} min={25} max={500}
                  className="w-24 mt-1 px-2 py-1.5 border border-border rounded-lg text-sm font-data bg-surface" />
              </div>
              <div>
                <label className="metric-label">Timeframe</label>
                <div className="flex gap-1 mt-1">
                  {(["daily", "60min", "15min", "5min"] as const).map(tf => (
                    <button key={tf} onClick={() => setTimeframe(tf)}
                      className={`px-2.5 py-1.5 text-xs rounded-md font-semibold ${timeframe === tf ? "bg-accent text-white" : "text-text-muted hover:bg-surface-alt border border-border"}`}>
                      {tf === "daily" ? "Daily" : tf}
                    </button>
                  ))}
                </div>
              </div>
              <div>
                <label className="metric-label">Lookback</label>
                <input type="number" value={lookback} onChange={e => setLookback(+e.target.value)} step={timeframe === "daily" ? 252 : 5}
                  className="w-24 mt-1 px-2 py-1.5 border border-border rounded-lg text-sm font-data bg-surface" />
              </div>
            </div>

            <div>
              <label className="metric-label mb-1 block">Strategies to Optimize</label>
              <div className="flex flex-wrap gap-2">
                {Object.entries(ALL_STRATEGIES).map(([key, label]) => (
                  <label key={key} className="flex items-center gap-1 text-xs cursor-pointer">
                    <input type="checkbox" checked={selectedStrats.includes(key)}
                      onChange={e => setSelectedStrats(prev => e.target.checked ? [...prev, key] : prev.filter(s => s !== key))}
                      className="rounded border-border" />
                    {label}
                  </label>
                ))}
              </div>
            </div>

            <button onClick={() => optimize.mutate()} disabled={optimize.isPending}
              className="w-full py-3 bg-accent text-white font-bold rounded-lg hover:bg-accent-hover disabled:opacity-50 transition-colors">
              {optimize.isPending
                ? `Optimizing ${optTicker} × ${selectedStrats.length} strategies (${optTrials} trials each)...`
                : `Optimize ${optTicker} — ${selectedStrats.length} strategies × ${optTrials} trials = ${selectedStrats.length * optTrials} total`}
            </button>
            {optimize.isPending && (
              <div className="flex items-center justify-center gap-3 py-2">
                <div className="w-5 h-5 border-2 border-accent border-t-transparent rounded-full animate-spin" />
                <span className="text-sm text-text-muted">Optuna Bayesian optimization in progress... (2-10 minutes)</span>
              </div>
            )}
            <p className="text-xs text-text-muted">Optuna uses Bayesian optimization (TPE) to find the best parameters for each strategy. Objective = walk-forward OOS Sharpe (not in-sample). DSR corrected for total trials.</p>
          </div>
        </div>

        {optimize.isError && <div className="card border-loss/30 bg-loss-bg text-loss text-sm">Optimization failed: {(optimize.error as Error).message}</div>}

        {optData?.success && (<>
          <div className="card card-compact">
            <div className="flex flex-wrap gap-6">
              <Metric label="Ticker" value={optData.ticker} />
              <Metric label="Timeframe" value={optData.timeframe} />
              <Metric label="Total Trials" value={String(optData.total_trials)} />
              <Metric label="Strategies" value={String(optData.strategies_tested)} />
            </div>
          </div>

          <div className="space-y-4">
            {optData.results.map((r, i) => (
              <div key={r.strategy} className={`card ${i === 0 ? "border-accent" : ""}`}>
                <div className="flex justify-between items-start mb-3">
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="font-bold text-sm">{ALL_STRATEGIES[r.strategy] || r.strategy}</span>
                      {i === 0 && <span className="badge badge-gain text-[0.55rem]">BEST</span>}
                      <span className={`font-semibold text-sm ${r.current_signal === "Long" ? "text-gain" : r.current_signal === "Short" ? "text-loss" : "text-text-muted"}`}>
                        {r.current_signal} {r.signal_days}d
                      </span>
                    </div>
                    <div className="text-xs text-text-muted font-data mt-1">
                      {r.n_trials} trials · DSR corrected for {r.n_tested_total} total trials
                    </div>
                  </div>
                  <div className="text-right">
                    <div className={`text-lg font-bold ${r.dsr >= 0.95 ? "text-gain" : r.dsr >= 0.85 ? "text-warn" : "text-text-muted"}`}>{r.dsr_pct}%</div>
                    <div className="text-[0.6rem] text-text-muted">DSR</div>
                  </div>
                </div>

                {/* Best parameters */}
                <div className="bg-surface-alt rounded-lg p-2 mb-3">
                  <div className="metric-label mb-1">Optimal Parameters</div>
                  <div className="flex flex-wrap gap-3 text-xs font-data">
                    {Object.entries(r.best_params).map(([k, v]) => (
                      <span key={k} className="px-2 py-0.5 rounded border border-border">
                        <span className="text-text-muted">{k}:</span> <strong>{typeof v === "number" ? (Number.isInteger(v) ? v : v.toFixed(2)) : v}</strong>
                      </span>
                    ))}
                  </div>
                </div>

                {/* Metrics */}
                <div className="grid grid-cols-4 sm:grid-cols-8 gap-2 text-[0.65rem] font-data mb-3">
                  <div><span className="text-text-muted block">WF Sharpe</span><strong className={r.wf_sharpe > 0.5 ? "text-gain" : r.wf_sharpe > 0 ? "text-warn" : "text-loss"}>{r.wf_sharpe}</strong></div>
                  <div><span className="text-text-muted block">Sharpe</span>{r.sharpe}</div>
                  <div><span className="text-text-muted block">CAGR</span><span className={r.cagr > 0 ? "text-gain" : "text-loss"}>{r.cagr}%</span></div>
                  <div><span className="text-text-muted block">Max DD</span><span className="text-loss">{r.max_dd}%</span></div>
                  <div><span className="text-text-muted block">Win Rate</span>{r.win_rate}%</div>
                  <div><span className="text-text-muted block">Trades</span>{r.trades}</div>
                  <div><span className="text-text-muted block">Total Ret</span>{r.total_ret}%</div>
                  <div><span className="text-text-muted block">DSR</span><strong className={r.dsr >= 0.95 ? "text-gain" : "text-text-muted"}>{r.dsr_pct}%</strong></div>
                </div>

                {/* Parameter importance */}
                {Object.keys(r.param_importance).length > 0 && (
                  <div>
                    <div className="metric-label mb-1">Parameter Importance</div>
                    <div className="flex gap-2">
                      {Object.entries(r.param_importance).sort(([,a],[,b]) => b - a).map(([k, v]) => (
                        <div key={k} className="flex items-center gap-1 text-xs">
                          <span className="text-text-muted">{k}:</span>
                          <div className="w-16 h-2 bg-surface-alt rounded overflow-hidden">
                            <div className="h-full bg-accent rounded" style={{ width: `${v * 100}%` }} />
                          </div>
                          <span className="font-data">{(v * 100).toFixed(0)}%</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>

          {optData.results.length === 0 && <div className="card text-center py-6 text-text-muted">No strategies found positive OOS Sharpe. Try more trials or different ticker.</div>}
        </>)}
      </>)}

      {/* ═══ COMBO MODE ═══ */}
      {pageMode === "combo" && (<>
        <div className="card">
          <div className="space-y-3">
            <div className="flex flex-wrap gap-3 items-end">
              <div><label className="metric-label">Ticker</label>
                <input type="text" value={comboTicker} onChange={e => setComboTicker(e.target.value.toUpperCase())}
                  className="w-24 mt-1 px-2 py-1.5 border border-border rounded-lg text-sm font-data bg-surface" /></div>
              <div><label className="metric-label">Combo Size</label>
                <div className="flex gap-1 mt-1">
                  {[2, 3].map(s => (
                    <button key={s} onClick={() => setComboSize(s)}
                      className={`px-3 py-1.5 text-xs rounded-md font-semibold ${comboSize === s ? "bg-accent text-white" : "text-text-muted border border-border"}`}>
                      {s === 2 ? "Pairs" : "Triples"}
                    </button>
                  ))}
                </div>
              </div>
              <div><label className="metric-label">Timeframe</label>
                <div className="flex gap-1 mt-1">
                  {(["daily", "60min"] as const).map(tf => (
                    <button key={tf} onClick={() => setTimeframe(tf)}
                      className={`px-2.5 py-1.5 text-xs rounded-md font-semibold ${timeframe === tf ? "bg-accent text-white" : "text-text-muted border border-border"}`}>
                      {tf === "daily" ? "Daily" : tf}
                    </button>
                  ))}
                </div>
              </div>
            </div>
            <div><label className="metric-label mb-1 block">Strategies to Combine ({selectedStrats.length})</label>
              <div className="flex flex-wrap gap-2">
                {Object.entries(ALL_STRATEGIES).map(([k, lab]) => (
                  <label key={k} className="flex items-center gap-1 text-xs cursor-pointer">
                    <input type="checkbox" checked={selectedStrats.includes(k)} onChange={e => setSelectedStrats(prev => e.target.checked ? [...prev, k] : prev.filter(s => s !== k))} className="rounded border-border" />
                    {lab}
                  </label>
                ))}
              </div>
            </div>
            <button onClick={() => comboScan.mutate()} disabled={comboScan.isPending}
              className="w-full py-3 bg-accent text-white font-bold rounded-lg hover:bg-accent-hover disabled:opacity-50">
              {comboScan.isPending
                ? `Testing combinations on ${comboTicker}...`
                : (() => {
                    const n = selectedStrats.length;
                    const pairs = n * (n - 1) / 2;
                    const triples = n * (n - 1) * (n - 2) / 6;
                    const total = comboSize === 2 ? pairs : pairs + triples;
                    return `Test ${comboSize === 2 ? "Pairs" : "Pairs + Triples"}: ${Math.round(total)} combinations on ${comboTicker}`;
                  })()}
            </button>
            {comboScan.isPending && <div className="flex items-center justify-center gap-3 py-2"><div className="w-5 h-5 border-2 border-accent border-t-transparent rounded-full animate-spin" /><span className="text-sm text-text-muted">Testing all {comboSize === 2 ? "pairs" : "triples"}... (1-3 minutes)</span></div>}
            <p className="text-xs text-text-muted">AND logic: enter only when ALL strategies in the combo agree. Reduces false signals but also reduces time in market.</p>
          </div>
        </div>

        {comboScan.isError && <div className="card border-loss/30 bg-loss-bg text-loss text-sm">Failed: {(comboScan.error as Error).message}</div>}

        {comboData?.success && (<>
          <div className="card card-compact">
            <div className="flex flex-wrap gap-6">
              <Metric label="Ticker" value={comboData.ticker} />
              <Metric label="Combos Tested" value={String(comboData.n_combos_tested)} />
              <Metric label="Best Individual" value={comboData.best_individual ? `${ALL_STRATEGIES[comboData.best_individual] || comboData.best_individual}` : "N/A"} />
              {comboData.best_combo && <Metric label="Best Combo α" value={String(comboData.best_combo.excess_sharpe)} />}
            </div>
          </div>

          {/* Best combo highlight */}
          {comboData.best_combo && (
            <div className="card border-accent">
              <div className="flex items-center justify-between mb-2">
                <div className="metric-label">Best Combination</div>
                {comboData.best_combo.excess_sharpe > 0 ? (
                  <span className="badge badge-gain">Beats B&H</span>
                ) : (
                  <span className="badge badge-loss">Underperforms B&H</span>
                )}
              </div>
              <div className="text-sm font-bold mb-1">{comboData.best_combo.combo.map(s => ALL_STRATEGIES[s] || s).join("  +  ")}</div>
              <div className="flex items-center gap-4 text-xs font-data flex-wrap">
                <span className={`font-bold ${comboData.best_combo.current_signal === "Long" ? "text-gain" : comboData.best_combo.current_signal === "Short" ? "text-loss" : "text-text-muted"}`}>
                  Signal: {comboData.best_combo.current_signal}
                </span>
                <span className={`font-bold ${comboData.best_combo.excess_sharpe > 0 ? "text-gain" : "text-loss"}`}>
                  Excess α: {comboData.best_combo.excess_sharpe}
                </span>
                <span>Sharpe: {comboData.best_combo.sharpe} (B&H: {comboData.best_combo.bh_sharpe})</span>
                <span>CAGR: {comboData.best_combo.cagr}% (B&H: {((comboData.best_combo.cagr - comboData.best_combo.excess_sharpe * 5).toFixed(1))}%)</span>
                <span>Max DD: {comboData.best_combo.max_dd}%</span>
                <span>Active: {comboData.best_combo.pct_active}%</span>
                <span>DSR: {comboData.best_combo.dsr_pct}%</span>
              </div>
            </div>
          )}

          {/* Quick insight */}
          {comboData.combos.length > 0 && (() => {
            const beating_bh = comboData.combos.filter(c => c.excess_sharpe > 0).length;
            const best_ind = comboData.best_individual ? comboData.individual[comboData.best_individual] : null;
            const best_combo_beats_ind = comboData.best_combo && best_ind && comboData.best_combo.excess_sharpe > best_ind.excess_sharpe;
            return (
              <div className="card card-compact text-xs text-text-muted">
                <strong>Insight:</strong> {beating_bh} of {comboData.combos.length} combinations beat buy-and-hold.
                {best_combo_beats_ind ? ` The best combination outperforms the best individual strategy (${ALL_STRATEGIES[comboData.best_individual!] || comboData.best_individual}) — confluence adds value.` : ` No combination beats the best individual strategy — added complexity doesn't help on ${comboData.ticker}.`}
              </div>
            );
          })()}

          {/* Individual strategies for comparison */}
          <div className="card">
            <div className="metric-label mb-2">Individual Strategies (baseline)</div>
            <div className="overflow-x-auto">
              <table className="data-table text-xs">
                <thead><tr><th>Strategy</th><th>Excess α</th><th>Sharpe</th><th>B&H</th><th>CAGR</th><th>Max DD</th><th>Active%</th><th>Signal</th></tr></thead>
                <tbody>
                  {Object.entries(comboData.individual).sort(([,a],[,b]) => b.excess_sharpe - a.excess_sharpe).map(([strat, r]) => (
                    <tr key={strat}>
                      <td className="font-semibold">{ALL_STRATEGIES[strat] || strat}</td>
                      <td className={`font-data font-bold ${r.excess_sharpe > 0 ? "text-gain" : "text-loss"}`}>{r.excess_sharpe}</td>
                      <td className="font-data">{r.sharpe}</td>
                      <td className="font-data text-text-muted">{r.bh_sharpe}</td>
                      <td className={`font-data ${r.cagr > 0 ? "text-gain" : "text-loss"}`}>{r.cagr}%</td>
                      <td className="font-data text-loss">{r.max_dd}%</td>
                      <td className="font-data">{r.pct_active}%</td>
                      <td className={`font-semibold ${r.current_signal === "Long" ? "text-gain" : r.current_signal === "Short" ? "text-loss" : "text-text-muted"}`}>{r.current_signal}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* Combo results */}
          <div className="card">
            <div className="metric-label mb-2">Top Combinations (AND logic — all must agree)</div>
            <div className="overflow-x-auto">
              <table className="data-table text-xs">
                <thead><tr><th>Combination</th><th>Excess α</th><th>Sharpe</th><th>B&H</th><th>DSR%</th><th>CAGR</th><th>Max DD</th><th>Active%</th><th>Trades</th><th>Signal</th></tr></thead>
                <tbody>
                  {comboData.combos.map((r, i) => (<Fragment key={i}>
                    <tr key={i} onClick={() => setExpandedCombo(expandedCombo === i ? null : i)}
                      className={`cursor-pointer ${i === 0 ? "bg-gain/5" : r.excess_sharpe > 0 ? "hover:bg-surface-alt" : "opacity-60 hover:bg-surface-alt"} ${expandedCombo === i ? "border-b-0" : ""}`}>
                      <td className="font-semibold text-[0.6rem]">
                        {expandedCombo === i ? "▾ " : "▸ "}{r.combo.map(s => ALL_STRATEGIES[s]?.split(" ")[0] || s).join(" + ")}
                      </td>
                      <td className={`font-data font-bold ${r.excess_sharpe > 0.3 ? "text-gain" : r.excess_sharpe > 0 ? "text-warn" : "text-loss"}`}>{r.excess_sharpe}</td>
                      <td className="font-data">{r.sharpe}</td>
                      <td className="font-data text-text-muted">{r.bh_sharpe}</td>
                      <td className={`font-data ${r.dsr >= 0.95 ? "text-gain font-bold" : ""}`}>{r.dsr_pct}%</td>
                      <td className={`font-data ${r.cagr > 0 ? "text-gain" : "text-loss"}`}>{r.cagr}%</td>
                      <td className="font-data text-loss">{r.max_dd}%</td>
                      <td className="font-data">{r.pct_active}%</td>
                      <td className="font-data">{r.trades}</td>
                      <td className={`font-semibold ${r.current_signal === "Long" ? "text-gain" : r.current_signal === "Short" ? "text-loss" : "text-text-muted"}`}>{r.current_signal}</td>
                    </tr>
                    {expandedCombo === i && r.chart && (
                      <tr key={`${i}-chart`}><td colSpan={10} className="p-0">
                        <div className="p-3 bg-surface-alt border-t border-border space-y-3">
                          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
                            <div>
                              <div className="text-[0.6rem] font-semibold text-text-muted mb-1">Equity Curve (% return)</div>
                              <Plot data={[
                                { x: r.chart.x_indices, y: r.chart.equity, type: "scatter" as const, mode: "lines" as const,
                                  line: { color: resolvedTheme === "dark" ? "#58a6ff" : "#1a56db", width: 2 }, name: "Strategy" },
                                { x: r.chart.x_indices, y: r.chart.bh_equity, type: "scatter" as const, mode: "lines" as const,
                                  line: { color: resolvedTheme === "dark" ? "#8b949e" : "#868e96", width: 1.5, dash: "dot" }, name: "Buy & Hold" },
                              ]} layout={{
                                height: 180, margin: { l: 40, r: 10, t: 5, b: 25 },
                                paper_bgcolor: "transparent", plot_bgcolor: resolvedTheme === "dark" ? "#0d1117" : "#fff",
                                font: { color: resolvedTheme === "dark" ? "#e6edf3" : "#1a2332", size: 8 },
                                xaxis: { gridcolor: resolvedTheme === "dark" ? "#21262d" : "#f1f3f5", title: "Bar" },
                                yaxis: { gridcolor: resolvedTheme === "dark" ? "#21262d" : "#f1f3f5", title: "Return %" },
                                legend: { x: 0.01, y: 0.99, font: { size: 8 }, bgcolor: "transparent" },
                                showlegend: true,
                              }} config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
                            </div>
                            <div>
                              <div className="text-[0.6rem] font-semibold text-text-muted mb-1">Drawdown</div>
                              <Plot data={[
                                { x: r.chart.x_indices, y: r.chart.drawdown, type: "scatter" as const, mode: "lines" as const,
                                  fill: "tozeroy", fillcolor: (resolvedTheme === "dark" ? "#f85149" : "#b91c1c") + "20",
                                  line: { color: resolvedTheme === "dark" ? "#f85149" : "#b91c1c", width: 1.5 }, showlegend: false },
                              ]} layout={{
                                height: 180, margin: { l: 40, r: 10, t: 5, b: 25 },
                                paper_bgcolor: "transparent", plot_bgcolor: resolvedTheme === "dark" ? "#0d1117" : "#fff",
                                font: { color: resolvedTheme === "dark" ? "#e6edf3" : "#1a2332", size: 8 },
                                xaxis: { gridcolor: resolvedTheme === "dark" ? "#21262d" : "#f1f3f5", title: "Bar" },
                                yaxis: { gridcolor: resolvedTheme === "dark" ? "#21262d" : "#f1f3f5", title: "DD %" },
                              }} config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
                            </div>
                          </div>
                          <div>
                            <div className="text-[0.6rem] font-semibold text-text-muted mb-1">Signal Timeline (1=Long, 0=Flat, -1=Short)</div>
                            <Plot data={[
                              { x: r.chart.x_indices, y: r.chart.signals, type: "scatter" as const, mode: "lines" as const,
                                fill: "tozeroy", line: { width: 0.5, color: resolvedTheme === "dark" ? "#58a6ff" : "#1a56db" },
                                fillcolor: (resolvedTheme === "dark" ? "#58a6ff" : "#1a56db") + "20", showlegend: false },
                            ]} layout={{
                              height: 80, margin: { l: 40, r: 10, t: 5, b: 20 },
                              paper_bgcolor: "transparent", plot_bgcolor: resolvedTheme === "dark" ? "#0d1117" : "#fff",
                              font: { color: resolvedTheme === "dark" ? "#e6edf3" : "#1a2332", size: 7 },
                              xaxis: { gridcolor: resolvedTheme === "dark" ? "#21262d" : "#f1f3f5" },
                              yaxis: { range: [-1.2, 1.2], gridcolor: resolvedTheme === "dark" ? "#21262d" : "#f1f3f5", dtick: 1 },
                            }} config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
                          </div>
                        </div>
                      </td></tr>
                    )}
                  </Fragment>))}
                </tbody>
              </table>
            </div>
            <p className="text-xs text-text-muted mt-2">Combinations with excess α &gt; 0 beat buy-and-hold. Lower active% means more selective (fewer false signals). Faded rows underperform B&H.</p>
          </div>
        </>)}
      </>)}

      {/* ═══ DEEP SCAN MODE ═══ */}
      {pageMode === "deep" && (<>
        <div className="card">
          <div className="space-y-3">
            <div><label className="metric-label">Tickers</label>
              <textarea value={tickers} onChange={e => setTickers(e.target.value)} rows={2} className="w-full mt-1 px-3 py-2 border border-border rounded-lg text-sm font-data bg-surface" /></div>

            <div>
              <label className="metric-label mb-1 block">Timeframes</label>
              <div className="flex gap-2">
                {(["daily", "60min", "15min", "5min"] as const).map(tf => (
                  <label key={tf} className="flex items-center gap-1 text-xs cursor-pointer">
                    <input type="checkbox" checked={deepTimeframes.includes(tf)}
                      onChange={e => setDeepTimeframes(prev => e.target.checked ? [...prev, tf] : prev.filter(t => t !== tf))} className="rounded border-border" />
                    {tf === "daily" ? "Daily" : tf}
                  </label>
                ))}
              </div>
            </div>

            <div><label className="metric-label mb-1 block">Strategies ({selectedStrats.length})</label>
              <div className="flex flex-wrap gap-2">
                {Object.entries(ALL_STRATEGIES).map(([k, lab]) => (
                  <label key={k} className="flex items-center gap-1 text-xs cursor-pointer">
                    <input type="checkbox" checked={selectedStrats.includes(k)} onChange={e => setSelectedStrats(prev => e.target.checked ? [...prev, k] : prev.filter(s => s !== k))} className="rounded border-border" />
                    {lab}
                  </label>
                ))}
              </div>
            </div>

            <button onClick={() => deepScan.mutate()} disabled={deepScan.isPending}
              className="w-full py-3 bg-accent text-white font-bold rounded-lg hover:bg-accent-hover disabled:opacity-50">
              {deepScan.isPending
                ? `Deep scanning ${tickers.split(",").filter(Boolean).length} tickers × ${selectedStrats.length} strategies × ${deepTimeframes.length} timeframes...`
                : `Deep Scan: ${tickers.split(",").filter(Boolean).length} × ${selectedStrats.length} × ${deepTimeframes.length} = ${tickers.split(",").filter(Boolean).length * selectedStrats.length * deepTimeframes.length} combos`}
            </button>
            {deepScan.isPending && <div className="flex items-center justify-center gap-3 py-3">
              <div className="w-5 h-5 border-2 border-accent border-t-transparent rounded-full animate-spin" />
              <span className="text-sm text-text-muted">Running all timeframes... (5-15 minutes)</span>
            </div>}
          </div>
        </div>

        {deepScan.isError && <div className="card border-loss/30 bg-loss-bg text-loss text-sm">Deep scan failed: {(deepScan.error as Error).message}</div>}

        {deepData?.success && (<>
          {/* Summary */}
          <div className="card card-compact">
            <div className="flex flex-wrap gap-6">
              <Metric label="Tested" value={String(deepData.total_results)} />
              <Metric label="Significant" value={`${deepData.n_significant} (${deepData.total_results > 0 ? Math.round(deepData.n_significant / deepData.total_results * 100) : 0}%)`} />
              <Metric label="Active Signals" value={String(deepData.n_active)} />
              <Metric label="Portfolio Picks" value={String(deepData.portfolio_recommendation.length)} />
            </div>
            <p className="text-xs text-text-muted mt-2">
              DSR corrected for {deepData.total_results} total combinations across all timeframes. {deepData.n_significant === 0 ? "No statistically significant edges found." : `${deepData.n_significant} combinations survive the multiple testing correction.`}
            </p>
          </div>

          {/* Portfolio Recommendation */}
          <div className="card border-accent">
            <div className="flex items-center justify-between mb-3">
              <div>
                <div className="text-sm font-bold">Recommended Portfolio</div>
                <div className="text-xs text-text-muted">Uncorrelated, statistically significant, currently active</div>
              </div>
              {deepData.portfolio_recommendation.length > 0 && (
                <span className="badge badge-gain">{deepData.portfolio_recommendation.length} trades</span>
              )}
            </div>
            {deepData.portfolio_recommendation.length === 0 && (
              <p className="text-xs text-text-muted py-3">No qualifying trades found. Need: DSR ≥ 85%, active signal, uncorrelated with other picks.</p>
            )}
            <div className="space-y-2">
              {deepData.portfolio_recommendation.map((p, i) => (
                <div key={i} className={`flex items-center gap-3 p-3 rounded-lg border ${i === 0 ? "border-accent bg-accent-light" : "border-border"}`}>
                  <div className="flex items-center gap-2 w-48">
                    <span className="font-bold text-sm">{p.ticker}</span>
                    <span className={`font-bold ${p.signal === "Long" ? "text-gain" : "text-loss"}`}>{p.signal}</span>
                    <span className="text-xs text-text-muted">{p.signal_days}d</span>
                  </div>
                  <span className="text-xs text-text-muted w-28 truncate">{ALL_STRATEGIES[p.strategy] || p.strategy}</span>
                  <span className="text-[0.65rem] text-text-muted">{p.timeframe}</span>
                  <div className="ml-auto flex items-center gap-3 font-data text-xs">
                    <span className={p.dsr >= 0.95 ? "text-gain font-bold" : "text-warn"}>DSR {(p.dsr * 100).toFixed(0)}%</span>
                    <span>Sharpe {p.sharpe}</span>
                    <span>CAGR {p.cagr}%</span>
                    <span>WR {p.win_rate}%</span>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Analysis tabs */}
          <div className="flex gap-1 border-b border-border pb-1 overflow-x-auto">
            {["Strategy Rankings", "Ticker Rankings", "Timeframe Rankings", "Active Signals", "Top Results", "Heatmap", "Regime Analysis", "Export"].map((tab, i) => (
              <button key={tab} onClick={() => setDeepTab(i)}
                className={`px-3 py-1.5 text-xs font-semibold rounded-t-md whitespace-nowrap ${deepTab === i ? "bg-accent text-white" : "text-text-muted hover:bg-surface-alt"}`}>{tab}</button>
            ))}
          </div>

          {/* Strategy Rankings */}
          {deepTab === 0 && (
            <div className="card overflow-x-auto">
              <table className="data-table text-xs">
                <thead><tr><th>Strategy</th><th>Avg DSR</th><th>Median DSR</th><th>Avg Sharpe</th><th>Avg WR</th><th>Significant</th><th>% Sig</th><th>Active</th></tr></thead>
                <tbody>{deepData.strategy_rankings.map((r, i) => (
                  <tr key={r.strategy} className={i === 0 ? "bg-gain/5" : ""}>
                    <td className="font-semibold">{ALL_STRATEGIES[r.strategy] || r.strategy}</td>
                    <td className={`font-data ${r.avg_dsr >= 0.5 ? "text-gain" : ""}`}>{(r.avg_dsr * 100).toFixed(1)}%</td>
                    <td className="font-data">{(r.median_dsr * 100).toFixed(1)}%</td>
                    <td className="font-data">{r.avg_sharpe}</td>
                    <td className="font-data">{r.avg_win_rate}%</td>
                    <td className="font-data font-semibold">{r.n_significant}/{r.n_tested}</td>
                    <td className={`font-data ${r.pct_significant >= 20 ? "text-gain font-bold" : ""}`}>{r.pct_significant}%</td>
                    <td className="font-data">{r.active_signals}</td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          )}

          {/* Ticker Rankings */}
          {deepTab === 1 && (
            <div className="card overflow-x-auto">
              <table className="data-table text-xs">
                <thead><tr><th>Ticker</th><th>Avg DSR</th><th>Avg Sharpe</th><th>Significant</th><th>Best Strategy</th><th>Best DSR</th></tr></thead>
                <tbody>{deepData.ticker_rankings.map((r, i) => (
                  <tr key={r.ticker} className={i === 0 ? "bg-gain/5" : ""}>
                    <td className="font-bold">{r.ticker}</td>
                    <td className={`font-data ${r.avg_dsr >= 0.5 ? "text-gain" : ""}`}>{(r.avg_dsr * 100).toFixed(1)}%</td>
                    <td className="font-data">{r.avg_sharpe}</td>
                    <td className="font-data font-semibold">{r.n_significant}</td>
                    <td className="text-xs text-text-muted">{r.best_strategy}</td>
                    <td className={`font-data ${r.best_dsr >= 0.95 ? "text-gain font-bold" : ""}`}>{(r.best_dsr * 100).toFixed(1)}%</td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          )}

          {/* Timeframe Rankings */}
          {deepTab === 2 && (
            <div className="card overflow-x-auto">
              <table className="data-table text-xs">
                <thead><tr><th>Timeframe</th><th>Avg DSR</th><th>Avg Sharpe</th><th>Significant</th><th>Tested</th></tr></thead>
                <tbody>{deepData.timeframe_rankings.map((r, i) => (
                  <tr key={r.timeframe} className={i === 0 ? "bg-gain/5" : ""}>
                    <td className="font-bold">{r.timeframe}</td>
                    <td className={`font-data ${r.avg_dsr >= 0.5 ? "text-gain" : ""}`}>{(r.avg_dsr * 100).toFixed(1)}%</td>
                    <td className="font-data">{r.avg_sharpe}</td>
                    <td className="font-data font-semibold">{r.n_significant}</td>
                    <td className="font-data">{r.n_tested}</td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          )}

          {/* Active Signals */}
          {deepTab === 3 && (
            <div className="card overflow-x-auto">
              <table className="data-table text-xs">
                <thead><tr><th>Ticker</th><th>Strategy</th><th>TF</th><th>Signal</th><th>Days</th><th>DSR</th><th>Sharpe</th><th>WR</th><th>CAGR</th></tr></thead>
                <tbody>{deepData.significant_active.map((r, i) => (
                  <tr key={`${r.ticker}-${r.strategy}-${r.timeframe}`} className={r.dsr >= 0.95 ? "bg-gain/5" : ""}>
                    <td className="font-bold">{r.ticker}</td>
                    <td className="text-text-muted">{ALL_STRATEGIES[r.strategy] || r.strategy}</td>
                    <td className="font-data">{r.timeframe}</td>
                    <td className={`font-semibold ${r.current_signal === "Long" ? "text-gain" : "text-loss"}`}>{r.current_signal}</td>
                    <td className="font-data">{r.signal_days}d</td>
                    <td className={`font-data ${r.dsr >= 0.95 ? "text-gain font-bold" : ""}`}>{(r.dsr * 100).toFixed(1)}%</td>
                    <td className="font-data">{r.sharpe}</td>
                    <td className="font-data">{r.win_rate}%</td>
                    <td className={`font-data ${r.cagr > 0 ? "text-gain" : "text-loss"}`}>{r.cagr}%</td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          )}

          {/* Top Results */}
          {deepTab === 4 && (
            <div className="card overflow-x-auto">
              <table className="data-table text-xs">
                <thead><tr><th>Ticker</th><th>Strategy</th><th>TF</th><th>Signal</th><th>DSR</th><th>Sharpe</th><th>CAGR</th><th>Max DD</th><th>WR</th><th>WF Sharpe</th></tr></thead>
                <tbody>{deepData.all_results.slice(0, 50).map((r, i) => (
                  <tr key={`${r.ticker}-${r.strategy}-${r.timeframe}-${i}`} className={r.dsr >= 0.95 ? "bg-gain/5" : ""}>
                    <td className="font-bold">{r.ticker}</td>
                    <td className="text-text-muted">{ALL_STRATEGIES[r.strategy] || r.strategy}</td>
                    <td className="font-data">{r.timeframe}</td>
                    <td className={`font-semibold ${r.current_signal === "Long" ? "text-gain" : r.current_signal === "Short" ? "text-loss" : "text-text-muted"}`}>{r.current_signal}</td>
                    <td className={`font-data ${r.dsr >= 0.95 ? "text-gain font-bold" : r.dsr >= 0.85 ? "text-warn" : ""}`}>{(r.dsr * 100).toFixed(1)}%</td>
                    <td className="font-data">{r.sharpe}</td>
                    <td className={`font-data ${r.cagr > 0 ? "text-gain" : "text-loss"}`}>{r.cagr}%</td>
                    <td className="font-data text-loss">{r.max_dd}%</td>
                    <td className="font-data">{r.win_rate}%</td>
                    <td className={`font-data ${r.avg_wf_sharpe != null && r.avg_wf_sharpe > 0 ? "text-gain" : "text-loss"}`}>{r.avg_wf_sharpe ?? "—"}</td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          )}

          {/* Heatmap: Strategy × Ticker */}
          {deepTab === 5 && (() => {
            const strats = [...new Set(deepData.heatmap.map(h => h.strategy))];
            const tks = [...new Set(deepData.heatmap.map(h => h.ticker))];
            const z = strats.map(s => tks.map(tk => {
              const entry = deepData.heatmap.find(h => h.strategy === s && h.ticker === tk);
              return entry ? entry.dsr : null;
            }));
            const text = strats.map(s => tks.map(tk => {
              const entry = deepData.heatmap.find(h => h.strategy === s && h.ticker === tk);
              return entry ? `${(entry.dsr * 100).toFixed(0)}%${entry.signal !== "Flat" ? ` ${entry.signal[0]}` : ""}` : "";
            }));
            return (
              <div className="card">
                <Plot data={[{
                  type: "heatmap" as const,
                  x: tks,
                  y: strats.map(s => ALL_STRATEGIES[s] || s),
                  z, text, texttemplate: "%{text}", textfont: { size: 9 },
                  colorscale: [[0, "#dc2626"], [0.5, "#1f2937"], [0.85, "#facc15"], [0.95, "#22c55e"], [1, "#00ff96"]],
                  colorbar: { title: { text: "DSR", font: { size: 9 } }, thickness: 12 },
                  hovertemplate: "%{y}<br>%{x}<br>DSR: %{z:.3f}<extra></extra>",
                  zmin: 0, zmax: 1,
                }]} layout={{
                  height: Math.max(400, strats.length * 25 + 100),
                  margin: { l: 180, r: 20, t: 20, b: 60 },
                  paper_bgcolor: "transparent", plot_bgcolor: "transparent",
                  font: { family: "Inter", color: resolvedTheme === "dark" ? "#e6edf3" : "#1a2332", size: 9 },
                  xaxis: { side: "bottom" }, yaxis: { autorange: "reversed" },
                }} config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
                <p className="text-xs text-text-muted mt-2">Green = high DSR (significant edge). Red = low. Letters indicate active signal (L=Long, S=Short).</p>
              </div>
            );
          })()}

          {/* Regime Analysis */}
          {deepTab === 6 && (() => {
            // Group results by strategy type (trend vs MR) and show avg DSR
            const trendStrats = ["sma_cross", "ema_cross", "golden_cross", "macd", "donchian", "atr_trail", "momentum", "dual_mom", "adx_di", "parabolic_sar", "ichimoku", "tema_cross"];
            const mrStrats = ["rsi_ob_os", "mean_rev", "bb_breakout", "zscore_mr", "stochastic", "cci", "williams_r"];
            const compositeStrats = ["trend_mr_composite", "trend_bb_composite"];

            const groupDsr = (strats: string[]) => {
              const vals = deepData.all_results.filter(r => strats.includes(r.strategy)).map(r => r.dsr);
              return vals.length > 0 ? { avg: vals.reduce((s,v) => s+v, 0) / vals.length, n: vals.length, sig: vals.filter(v => v >= 0.95).length } : { avg: 0, n: 0, sig: 0 };
            };
            const trend = groupDsr(trendStrats);
            const mr = groupDsr(mrStrats);
            const comp = groupDsr(compositeStrats);

            // Win rate distribution
            const wrBuckets = [0, 30, 40, 50, 60, 70, 80, 100];
            const wrDist = wrBuckets.slice(0, -1).map((lo, i) => ({
              range: `${lo}-${wrBuckets[i+1]}%`,
              count: deepData.all_results.filter(r => r.win_rate >= lo && r.win_rate < wrBuckets[i+1]).length,
            }));

            return (
              <div className="card space-y-4">
                <div className="metric-label">Strategy Type Performance</div>
                <div className="grid grid-cols-3 gap-3">
                  <div className="card card-compact bg-surface-alt">
                    <div className="metric-label">Trend Following ({trend.n})</div>
                    <div className="text-lg font-bold">{(trend.avg * 100).toFixed(1)}%</div>
                    <div className="text-xs text-text-muted">{trend.sig} significant</div>
                  </div>
                  <div className="card card-compact bg-surface-alt">
                    <div className="metric-label">Mean Reversion ({mr.n})</div>
                    <div className="text-lg font-bold">{(mr.avg * 100).toFixed(1)}%</div>
                    <div className="text-xs text-text-muted">{mr.sig} significant</div>
                  </div>
                  <div className="card card-compact bg-surface-alt">
                    <div className="metric-label">Composite ({comp.n})</div>
                    <div className="text-lg font-bold">{(comp.avg * 100).toFixed(1)}%</div>
                    <div className="text-xs text-text-muted">{comp.sig} significant</div>
                  </div>
                </div>

                <div className="metric-label">DSR Distribution</div>
                <Plot data={[{
                  x: deepData.all_results.map(r => r.dsr * 100),
                  type: "histogram" as const, nbinsx: 20,
                  marker: { color: resolvedTheme === "dark" ? "rgba(88,166,255,0.4)" : "rgba(26,86,219,0.4)", line: { color: resolvedTheme === "dark" ? "#58a6ff" : "#1a56db", width: 1 } },
                }]} layout={{
                  height: 200, margin: { l: 40, r: 10, t: 5, b: 30 },
                  paper_bgcolor: "transparent", plot_bgcolor: resolvedTheme === "dark" ? "#0d1117" : "#ffffff",
                  font: { color: resolvedTheme === "dark" ? "#e6edf3" : "#1a2332", size: 9 },
                  xaxis: { title: "DSR %", gridcolor: resolvedTheme === "dark" ? "#21262d" : "#f1f3f5" },
                  yaxis: { title: "Count", gridcolor: resolvedTheme === "dark" ? "#21262d" : "#f1f3f5" },
                  shapes: [{ type: "line", x0: 95, x1: 95, y0: 0, y1: 1, yref: "paper", line: { color: "#22c55e", width: 2, dash: "dash" } }],
                  annotations: [{ x: 95, y: 1, yref: "paper", text: "95% threshold", showarrow: false, font: { size: 8, color: "#22c55e" } }],
                }} config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />

                <div className="metric-label">Sharpe vs Max Drawdown (Risk-Return)</div>
                <Plot data={[{
                  x: deepData.all_results.slice(0, 50).map(r => r.max_dd),
                  y: deepData.all_results.slice(0, 50).map(r => r.sharpe),
                  text: deepData.all_results.slice(0, 50).map(r => `${r.ticker} ${ALL_STRATEGIES[r.strategy] || r.strategy}`),
                  type: "scatter" as const, mode: "markers" as const,
                  marker: {
                    size: 8,
                    color: deepData.all_results.slice(0, 50).map(r => r.dsr),
                    colorscale: [[0, "#dc2626"], [0.5, "#facc15"], [1, "#22c55e"]],
                    colorbar: { title: { text: "DSR", font: { size: 9 } }, thickness: 10 },
                  },
                  hovertemplate: "%{text}<br>Sharpe: %{y:.2f}<br>Max DD: %{x:.1f}%<br>DSR: %{marker.color:.3f}<extra></extra>",
                }]} layout={{
                  height: 350, margin: { l: 50, r: 20, t: 10, b: 40 },
                  paper_bgcolor: "transparent", plot_bgcolor: resolvedTheme === "dark" ? "#0d1117" : "#ffffff",
                  font: { color: resolvedTheme === "dark" ? "#e6edf3" : "#1a2332", size: 9 },
                  xaxis: { title: "Max Drawdown (%)", gridcolor: resolvedTheme === "dark" ? "#21262d" : "#f1f3f5" },
                  yaxis: { title: "Sharpe Ratio", gridcolor: resolvedTheme === "dark" ? "#21262d" : "#f1f3f5" },
                }} config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />

                <div className="metric-label">Win Rate Distribution</div>
                <Plot data={[{
                  x: wrDist.map(d => d.range), y: wrDist.map(d => d.count),
                  type: "bar" as const,
                  marker: { color: wrDist.map((_, i) => i >= 3 ? "#22c55e" : i >= 2 ? "#facc15" : "#dc2626") },
                }]} layout={{
                  height: 180, margin: { l: 40, r: 10, t: 5, b: 30 },
                  paper_bgcolor: "transparent", plot_bgcolor: resolvedTheme === "dark" ? "#0d1117" : "#ffffff",
                  font: { color: resolvedTheme === "dark" ? "#e6edf3" : "#1a2332", size: 9 },
                  xaxis: { title: "Win Rate", gridcolor: resolvedTheme === "dark" ? "#21262d" : "#f1f3f5" },
                  yaxis: { title: "Strategies", gridcolor: resolvedTheme === "dark" ? "#21262d" : "#f1f3f5" },
                }} config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />

                {/* Correlation matrix */}
                {deepData.correlation.strategies.length > 1 && (<>
                  <div className="metric-label">Strategy Signal Correlation</div>
                  <Plot data={[{
                    type: "heatmap" as const,
                    x: deepData.correlation.strategies.map(s => ALL_STRATEGIES[s]?.slice(0, 12) || s.slice(0, 12)),
                    y: deepData.correlation.strategies.map(s => ALL_STRATEGIES[s]?.slice(0, 12) || s.slice(0, 12)),
                    z: deepData.correlation.matrix,
                    colorscale: [[0, "#1a56db"], [0.5, "#1f2937"], [1, "#dc2626"]],
                    zmid: 0.5, zmin: 0, zmax: 1,
                    text: deepData.correlation.matrix.map(row => row.map(v => `${(v * 100).toFixed(0)}%`)),
                    texttemplate: "%{text}", textfont: { size: 7 },
                    colorbar: { title: { text: "Agreement", font: { size: 9 } }, thickness: 10 },
                  }]} layout={{
                    height: Math.max(300, deepData.correlation.strategies.length * 25 + 80),
                    margin: { l: 100, r: 20, t: 20, b: 80 },
                    paper_bgcolor: "transparent", plot_bgcolor: "transparent",
                    font: { color: resolvedTheme === "dark" ? "#e6edf3" : "#1a2332", size: 8 },
                    xaxis: { tickangle: -45 }, yaxis: { autorange: "reversed" },
                  }} config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
                  <p className="text-xs text-text-muted">Blue = low agreement (uncorrelated, good for diversification). Red = high agreement (correlated, redundant).</p>
                </>)}
              </div>
            );
          })()}

          {/* Export */}
          {deepTab === 7 && (
            <div className="card space-y-4">
              <div className="metric-label">Export Results</div>
              <div className="flex gap-3">
                <button onClick={() => {
                  const headers = ["Ticker", "Strategy", "Timeframe", "Signal", "Days", "DSR%", "Sharpe", "CAGR%", "MaxDD%", "WinRate%", "Trades", "WF_Sharpe"];
                  const rows = deepData.all_results.map(r => [r.ticker, r.strategy, r.timeframe, r.current_signal, r.signal_days, r.dsr_pct, r.sharpe, r.cagr, r.max_dd, r.win_rate, r.trades, r.avg_wf_sharpe ?? ""].join(","));
                  const csv = [headers.join(","), ...rows].join("\n");
                  const blob = new Blob([csv], { type: "text/csv" });
                  const url = URL.createObjectURL(blob);
                  const a = document.createElement("a"); a.href = url; a.download = `deep_scan_${new Date().toISOString().slice(0, 10)}.csv`; a.click(); URL.revokeObjectURL(url);
                }} className="px-4 py-2 bg-accent text-white text-sm font-semibold rounded hover:bg-accent-hover">
                  Download CSV (All Results)
                </button>
                <button onClick={() => {
                  const json = JSON.stringify(deepData, null, 2);
                  const blob = new Blob([json], { type: "application/json" });
                  const url = URL.createObjectURL(blob);
                  const a = document.createElement("a"); a.href = url; a.download = `deep_scan_full_${new Date().toISOString().slice(0, 10)}.json`; a.click(); URL.revokeObjectURL(url);
                }} className="px-4 py-2 border border-border text-sm font-semibold rounded hover:bg-surface-alt">
                  Download JSON (Full Data)
                </button>
              </div>
              <div className="text-xs text-text-muted space-y-1">
                <p><strong>CSV:</strong> {deepData.all_results.length} rows × 12 columns. Import into Excel, Google Sheets, or Python for further analysis.</p>
                <p><strong>JSON:</strong> Complete scan data including strategy rankings, ticker rankings, heatmap, correlation matrix, and portfolio recommendation.</p>
              </div>
              <div className="border border-border rounded p-3">
                <div className="metric-label mb-1">Quick Stats</div>
                <pre className="text-xs font-data text-text-muted whitespace-pre-wrap">{`Total combinations tested: ${deepData.total_results}
Statistically significant (DSR ≥ 95%): ${deepData.n_significant}
Active signals right now: ${deepData.n_active}
Best strategy: ${deepData.strategy_rankings[0]?.strategy ?? "N/A"} (avg DSR ${deepData.strategy_rankings[0] ? (deepData.strategy_rankings[0].avg_dsr * 100).toFixed(1) : "0"}%)
Best ticker: ${deepData.ticker_rankings[0]?.ticker ?? "N/A"} (avg DSR ${deepData.ticker_rankings[0] ? (deepData.ticker_rankings[0].avg_dsr * 100).toFixed(1) : "0"}%)
Portfolio: ${deepData.portfolio_recommendation.length} uncorrelated trades recommended`}</pre>
              </div>
            </div>
          )}
        </>)}
      </>)}
    </div>
  );
}

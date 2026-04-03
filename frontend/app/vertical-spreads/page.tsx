"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTheme } from "next-themes";
import { scanVerticalSpreads, addPosition, type VSResult, type VSScanConfig, type ICStressScenario } from "@/lib/api";
import { getChartTheme } from "@/lib/chart-theme";
import { Metric } from "@/components/ui/metric";
import { FreshnessBar } from "@/components/ui/freshness-dot";
import dynamic from "next/dynamic";

const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

const DEFAULT_TICKERS = ["SPY","QQQ","IWM","DIA","AAPL","TSLA","NVDA","AMD","AMZN","META","MSFT","GOOGL","NFLX","GLD","SMH","XLF","TLT","EEM","JPM","BA"];
const LIQ_COLORS: Record<string,string> = { A:"text-gain", B:"text-gain", C:"text-warn", D:"text-warn", F:"text-loss" };
const BAND_COLORS: Record<string,string> = { Optimal:"badge-gain", Normal:"badge-info", Extreme:"badge-warn", Low:"badge-loss" };
const TYPE_COLORS: Record<string,string> = { bull_put:"text-gain", bear_call:"text-loss", bull_call:"text-gain", bear_put:"text-loss" };
const SPREAD_TYPES = [
  { value: "bull_put", label: "Bull Put (Credit)", desc: "Sell put spread — bullish, collect premium" },
  { value: "bear_call", label: "Bear Call (Credit)", desc: "Sell call spread — bearish, collect premium" },
  { value: "bull_call", label: "Bull Call (Debit)", desc: "Buy call spread — bullish directional" },
  { value: "bear_put", label: "Bear Put (Debit)", desc: "Buy put spread — bearish directional" },
];

function fmtExp(exp: string) {
  try { return new Date(exp + "T12:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric" }); }
  catch { return exp; }
}

export default function VerticalSpreadScanner() {
  const { resolvedTheme } = useTheme();
  const t = getChartTheme(resolvedTheme === "dark");
  const [tickers, setTickers] = useState(DEFAULT_TICKERS.join(", "));
  const [dteMin, setDteMin] = useState(7);
  const [dteMax, setDteMax] = useState(60);
  const [shortDelta, setShortDelta] = useState(0.30);
  const [width, setWidth] = useState(5);
  const [profitTarget, setProfitTarget] = useState(50);
  const [stopMult, setStopMult] = useState(1.5);
  const [accountSize, setAccountSize] = useState(25000);
  const [maxRiskPct, setMaxRiskPct] = useState(5.0);
  const [kellyFrac, setKellyFrac] = useState(0.5);
  const [selectedTypes, setSelectedTypes] = useState(["bull_put", "bear_call"]);
  const [results, setResults] = useState<VSResult[]>([]);
  const [scanTime, setScanTime] = useState<Date | null>(null);
  const [selectedIdx, setSelectedIdx] = useState(0);
  const [sortBy, setSortBy] = useState("adj_score");
  const [minPop, setMinPop] = useState(40);
  const [minLiq, setMinLiq] = useState("Any");
  const [showN, setShowN] = useState("All");
  const [typeFilter, setTypeFilter] = useState("all");
  const [detailTab, setDetailTab] = useState(0);
  const [booked, setBooked] = useState<Set<string>>(new Set());
  const [showKelly, setShowKelly] = useState(false);

  const scan = useMutation({
    mutationFn: (config: Partial<VSScanConfig>) => scanVerticalSpreads(config),
    onSuccess: (data) => { setResults(data.results); setScanTime(new Date()); setSelectedIdx(0); },
  });

  function handleScan() {
    scan.mutate({
      tickers: tickers.split(",").map(t => t.trim().toUpperCase()).filter(Boolean),
      spread_types: selectedTypes, dte_min: dteMin, dte_max: dteMax,
      short_delta: shortDelta, width, profit_target_pct: profitTarget,
      stop_multiplier: stopMult, account_size: accountSize, max_risk_pct: maxRiskPct, kelly_fraction: kellyFrac,
    });
  }

  // Filtering
  const LIQ_ORDER: Record<string,number> = { A:4, B:3, C:2, D:1, F:0 };
  let filtered = [...results];
  if (typeFilter !== "all") filtered = filtered.filter(r => r.spread_type === typeFilter);
  filtered = filtered.filter(r => r.pop >= minPop);
  if (minLiq !== "Any") {
    const minVal = { "D+":1, "C+":2, "B+":3, "A":4 }[minLiq] ?? 0;
    filtered = filtered.filter(r => (LIQ_ORDER[r.liq_grade] ?? 0) >= minVal);
  }
  filtered.sort((a, b) => ((b as any)[sortBy] ?? 0) - ((a as any)[sortBy] ?? 0));
  const preTopN = filtered.length;
  if (showN === "Top 5") filtered = filtered.slice(0, 5);
  else if (showN === "Top 10") filtered = filtered.slice(0, 10);
  else if (showN === "Top 20") filtered = filtered.slice(0, 20);

  const selected = filtered[selectedIdx] || null;
  const ageMin = scanTime ? (Date.now() - scanTime.getTime()) / 60000 : null;

  async function handleBook(r: VSResult) {
    try {
      await addPosition({ ticker: r.ticker, type: r.spread_type, qty: r.contracts || 1,
        entry_price: r.fill_estimate / 100,
        details: { strategy: r.spread_type, short_strike: r.short_strike, long_strike: r.long_strike,
          expiration: r.expiration, dte: r.dte, premium: r.premium, max_risk: r.max_risk, pop: r.pop },
        source_page: "next_vertical_spread_scanner" });
      setBooked(prev => new Set(prev).add(r.ticker + r.expiration + r.spread_type));
    } catch (e) { console.error("Book failed:", e); }
  }

  const DETAIL_TABS = ["Overview", "Management", "Compare Exps", "Greeks", "Results Table"];

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Vertical Spread Scanner</h1>
        <p className="text-text-secondary text-sm mt-1">Scan for optimal bull/bear credit and debit spreads ranked by score, POP, and IV percentile.</p>
      </div>

      {/* Config */}
      <div className="card">
        {/* Spread type selector */}
        <div className="flex flex-wrap gap-2 mb-3">
          {SPREAD_TYPES.map(st => (
            <label key={st.value} className="flex items-center gap-1.5 text-xs cursor-pointer">
              <input type="checkbox" checked={selectedTypes.includes(st.value)}
                onChange={e => setSelectedTypes(prev => e.target.checked ? [...prev, st.value] : prev.filter(v => v !== st.value))}
                className="rounded border-border" />
              <span className={TYPE_COLORS[st.value]}>{st.label}</span>
            </label>
          ))}
        </div>

        <div className="grid grid-cols-2 sm:grid-cols-5 gap-3 mb-3">
          {([
            ["Min DTE", dteMin, setDteMin, 1], ["Max DTE", dteMax, setDteMax, 1],
            ["Short Delta", shortDelta, setShortDelta, 0.01], ["Width ($)", width, setWidth, 1],
            ["Profit Target (%)", profitTarget, setProfitTarget, 5],
          ] as [string, number, (v:number)=>void, number][]).map(([label, val, setter, step]) => (
            <div key={label}>
              <label className="metric-label">{label}</label>
              <input type="number" step={step} value={val} onChange={e => setter(+e.target.value)}
                className="w-full mt-1 px-2 py-1.5 border border-border rounded-lg text-sm font-data bg-surface" />
            </div>
          ))}
        </div>

        <textarea value={tickers} onChange={e => setTickers(e.target.value)} rows={2}
          className="w-full px-3 py-2 border border-border rounded-lg text-sm font-data bg-surface mb-3" />

        <button onClick={() => setShowKelly(!showKelly)} className="text-xs text-accent hover:underline mb-2">
          {showKelly ? "▾ Position Sizing (Kelly)" : "▸ Position Sizing (Kelly)"}
        </button>
        {showKelly && (
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-3 p-3 rounded-lg bg-surface-alt border border-border">
            <div><label className="metric-label">Account ($)</label>
              <input type="number" step={5000} value={accountSize} onChange={e => setAccountSize(+e.target.value)} className="w-full mt-1 px-2 py-1.5 border border-border rounded-lg text-sm font-data bg-surface" /></div>
            <div><label className="metric-label">Hard Cap (%)</label>
              <input type="number" step={0.5} value={maxRiskPct} onChange={e => setMaxRiskPct(+e.target.value)} className="w-full mt-1 px-2 py-1.5 border border-border rounded-lg text-sm font-data bg-surface" /></div>
            <div><label className="metric-label">Kelly Frac</label>
              <input type="number" step={0.1} min={0.1} max={1} value={kellyFrac} onChange={e => setKellyFrac(+e.target.value)} className="w-full mt-1 px-2 py-1.5 border border-border rounded-lg text-sm font-data bg-surface" /></div>
            <div><label className="metric-label">Stop (× premium)</label>
              <input type="number" step={0.25} value={stopMult} onChange={e => setStopMult(+e.target.value)} className="w-full mt-1 px-2 py-1.5 border border-border rounded-lg text-sm font-data bg-surface" /></div>
          </div>
        )}

        <button onClick={handleScan} disabled={scan.isPending}
          className="w-full py-2.5 bg-accent text-white font-semibold rounded-lg hover:bg-accent-hover disabled:opacity-50 transition-colors">
          {scan.isPending ? "Scanning..." : "Scan for Vertical Spreads"}
        </button>
        {scan.isPending && <div className="mt-3 text-center"><div className="inline-block w-5 h-5 border-2 border-accent border-t-transparent rounded-full animate-spin" /><p className="text-sm text-text-muted mt-2">Scanning {tickers.split(",").filter(Boolean).length} tickers × {selectedTypes.length} spread types...</p></div>}
      </div>

      {results.length > 0 && (<>
        <FreshnessBar sources={[{ label: "Chains", ageMinutes: ageMin, greenThreshold: 30, yellowThreshold: 120 }]} />

        {/* Portfolio Summary */}
        <div className="card card-compact">
          <div className="flex flex-wrap gap-6">
            <Metric label="Setups" value={String(filtered.length)} />
            <Metric label="Contracts" value={String(filtered.reduce((s,r) => s + (r.contracts||0), 0))} />
            <Metric label="Total Premium" value={`$${filtered.reduce((s,r) => s + (r.total_credit||0), 0).toLocaleString()}`} />
            <Metric label="Total Risk" value={`$${filtered.reduce((s,r) => s + (r.total_risk||0), 0).toLocaleString()}`} />
            <Metric label="Credit" value={String(filtered.filter(r=>r.is_credit).length)} />
            <Metric label="Debit" value={String(filtered.filter(r=>!r.is_credit).length)} />
          </div>
        </div>

        {/* Filters */}
        <div className="card card-compact">
          <div className="flex flex-wrap items-center gap-3">
            <div className="flex items-center gap-1.5">
              <span className="text-[0.65rem] text-text-muted uppercase">Type</span>
              <select value={typeFilter} onChange={e => setTypeFilter(e.target.value)} className="text-xs border border-border rounded px-1.5 py-1 bg-surface">
                <option value="all">All</option>
                {SPREAD_TYPES.map(st => <option key={st.value} value={st.value}>{st.label}</option>)}
              </select>
            </div>
            {([
              ["Sort", sortBy, setSortBy, { Score:"adj_score", POP:"pop", Premium:"premium", IVR:"ivr", "Max Profit":"max_profit" }],
              ["Show", showN, setShowN, { All:"All", "Top 5":"Top 5", "Top 10":"Top 10", "Top 20":"Top 20" }],
              ["Min Liq", minLiq, setMinLiq, { Any:"Any", "D+":"D+", "C+":"C+", "B+":"B+", A:"A" }],
            ] as [string,string,(v:string)=>void,Record<string,string>][]).map(([label,val,setter,opts]) => (
              <div key={label} className="flex items-center gap-1.5">
                <span className="text-[0.65rem] text-text-muted uppercase">{label}</span>
                <select value={val} onChange={e => setter(e.target.value)} className="text-xs border border-border rounded px-1.5 py-1 bg-surface">
                  {Object.entries(opts).map(([k,v]) => <option key={k} value={v}>{k}</option>)}
                </select>
              </div>
            ))}
            <div className="flex items-center gap-1.5">
              <span className="text-[0.65rem] text-text-muted uppercase">Min POP</span>
              <input type="number" value={minPop} onChange={e => setMinPop(+e.target.value)} className="w-14 text-xs border border-border rounded px-1.5 py-1 bg-surface font-data" />
            </div>
            <span className="text-xs text-text-muted ml-auto">{filtered.length}{preTopN > filtered.length ? ` of ${preTopN}` : ""} setups</span>
          </div>
        </div>

        {/* Results grid */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          {/* List */}
          <div className="space-y-1 max-h-[700px] overflow-y-auto">
            {filtered.map((r, i) => (
              <button key={r.ticker+r.expiration+r.spread_type} onClick={() => { setSelectedIdx(i); setDetailTab(0); }}
                className={`w-full text-left card card-compact transition-colors ${i === selectedIdx ? "border-accent bg-accent-light" : "hover:bg-surface-alt"}`}>
                <div className="flex justify-between items-center">
                  <div>
                    <span className="font-bold text-sm">{r.ticker}</span>
                    <span className={`text-[0.65rem] ml-1.5 ${TYPE_COLORS[r.spread_type] || ""}`}>{r.spread_label}</span>
                  </div>
                  <div className="text-right font-data">
                    <span className="font-semibold text-sm">${r.fill_estimate}</span>
                    <span className={`text-[0.65rem] ml-1.5 ${LIQ_COLORS[r.liq_grade]||""}`}>{r.liq_grade}</span>
                  </div>
                </div>
                <div className="flex gap-2 mt-0.5 text-[0.6rem] text-text-muted font-data">
                  <span>POP {r.pop}%</span><span>Score {r.adj_score.toFixed(3)}</span>
                  <span>{fmtExp(r.expiration)} · {r.dte}d</span>
                  {r.contracts > 0 && <span>{r.contracts}×</span>}
                  {r.earnings_before && <span className="text-loss">EARN</span>}
                </div>
              </button>
            ))}
          </div>

          {/* Detail */}
          {selected && (
            <div className="lg:col-span-2 space-y-3">
              <div className="card">
                <div className="flex justify-between items-start mb-3">
                  <div>
                    <h2 className="text-xl font-bold">{selected.ticker} <span className={`text-sm ${TYPE_COLORS[selected.spread_type]||""}`}>{selected.spread_label}</span></h2>
                    <p className="text-sm text-text-muted">{fmtExp(selected.expiration)} · {selected.dte}d · Spot ${selected.spot.toFixed(2)}</p>
                  </div>
                  <span className={`badge ${BAND_COLORS[selected.ivr_band]||"badge-info"}`}>IVR {selected.ivr?.toFixed(0) ?? "N/A"} · {selected.ivr_band}</span>
                </div>

                {/* Warning badges */}
                {(selected.inside_exp_move || selected.earnings_before || selected.ivr_band === "Low" || selected.ivr_band === "Extreme" || selected.n_synthetic > 0 || selected.liq_grade === "D" || selected.liq_grade === "F") && (
                  <div className="flex flex-wrap gap-1.5 mb-3">
                    {selected.inside_exp_move && <span className="badge badge-loss">Short strike inside expected move!</span>}
                    {selected.earnings_before && <span className="badge badge-loss">Earnings in {selected.earnings_days}d</span>}
                    {selected.ivr_band === "Extreme" && <span className="badge badge-warn">IVR &gt;75 jump risk</span>}
                    {selected.ivr_band === "Low" && <span className="badge badge-loss">IVR &lt;30 low premium</span>}
                    {(selected.liq_grade === "D" || selected.liq_grade === "F") && <span className="badge badge-loss">Low liquidity ({selected.liq_grade})</span>}
                    {selected.n_synthetic > 0 && <span className="badge badge-warn">{selected.n_synthetic} leg(s) no live quote</span>}
                  </div>
                )}

                <div className="grid grid-cols-2 sm:grid-cols-5 gap-3 mb-3">
                  <Metric label={selected.is_credit ? "Credit" : "Debit"} value={`$${selected.fill_estimate}`} />
                  <Metric label="Max Risk" value={`$${selected.max_risk}`} />
                  <Metric label="Max Profit" value={`$${selected.max_profit}`} />
                  <Metric label="POP" value={`${selected.pop}%`} />
                  <Metric label="R:R" value={`${selected.rr_ratio}x`} />
                </div>
                <div className="text-[0.65rem] font-data text-text-muted mb-1">
                  Strikes: ${selected.long_strike.toFixed(0)} / ${selected.short_strike.toFixed(0)} ({selected.opt_type.toUpperCase()}) · Width: ${selected.width.toFixed(0)} · BE: ${selected.breakeven.toFixed(1)} ({selected.be_pct}% from spot)
                </div>
                <div className="text-[0.65rem] font-data text-text-muted mb-1">
                  IVR: {selected.ivr?.toFixed(0) ?? "N/A"} · IV: {selected.avg_iv}% · HV20: {selected.hv20 ?? "N/A"}% · VRP: {selected.vrp != null ? `${selected.vrp > 0 ? "+" : ""}${selected.vrp}%` : "N/A"} · Put Skew: {selected.put_skew}x
                </div>
                <div className="text-[0.65rem] font-data text-text-muted mb-3">
                  30Δ Trigger: ${selected.trigger_30d.toFixed(0)} · ~{selected.days_to_target}d to {selected.profit_target_pct}% target · Exp Move: ±{selected.exp_move_pct}% · Short Dist: {selected.short_dist_pct}%
                </div>

                {/* Leg diagram */}
                <div className="flex items-center justify-center gap-2 text-[0.65rem] font-data mb-3">
                  {selected.is_credit ? (
                    <>
                      <span className="px-1.5 py-0.5 rounded border border-loss text-loss">${selected.long_strike.toFixed(0)}{selected.opt_type[0].toUpperCase()} (long/protect)</span>
                      <span className="text-text-muted">—</span>
                      <span className="px-1.5 py-0.5 rounded border border-warn text-warn">${selected.short_strike.toFixed(0)}{selected.opt_type[0].toUpperCase()} (short/sell)</span>
                    </>
                  ) : (
                    <>
                      <span className="px-1.5 py-0.5 rounded border border-gain text-gain">${selected.long_strike.toFixed(0)}{selected.opt_type[0].toUpperCase()} (long/buy)</span>
                      <span className="text-text-muted">—</span>
                      <span className="px-1.5 py-0.5 rounded border border-warn text-warn">${selected.short_strike.toFixed(0)}{selected.opt_type[0].toUpperCase()} (short/sell)</span>
                    </>
                  )}
                  <span className="text-text-muted">· Spot ${selected.spot.toFixed(2)}</span>
                </div>

                <div className="flex gap-3 items-center">
                  <button onClick={() => handleBook(selected)} disabled={booked.has(selected.ticker+selected.expiration+selected.spread_type)}
                    className={`flex-1 py-2 rounded-lg font-semibold text-sm transition-colors ${booked.has(selected.ticker+selected.expiration+selected.spread_type) ? "bg-gain/20 text-gain" : "bg-accent text-white hover:bg-accent-hover"}`}>
                    {booked.has(selected.ticker+selected.expiration+selected.spread_type) ? "✓ Booked" : `Book ${selected.contracts||1}× ${selected.ticker}`}
                  </button>
                  <div className="text-[0.65rem] text-text-muted font-data">{selected.contracts||1}× · ${selected.fill_estimate}/ct · Kelly {selected.kelly_adj?.toFixed(1)}%</div>
                </div>
              </div>

              {/* Sub-tabs */}
              <div className="card">
                <div className="flex gap-1 mb-3 border-b border-border pb-2 overflow-x-auto">
                  {DETAIL_TABS.map((tab, i) => (
                    <button key={tab} onClick={() => setDetailTab(i)} className={`px-3 py-1 text-xs font-semibold rounded-md transition-colors whitespace-nowrap ${detailTab === i ? "bg-accent text-white" : "text-text-muted hover:text-text hover:bg-surface-alt"}`}>{tab}</button>
                  ))}
                </div>

                {/* Overview */}
                {detailTab === 0 && (
                  <div className="space-y-4">
                    <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
                      {selected.payoff_prices?.length > 0 && (
                        <div>
                          <div className="metric-label mb-1">P&L at Expiration</div>
                          <Plot data={[
                            { x: selected.payoff_prices, y: selected.payoff_pnl, type: "scatter" as const, mode: "lines" as const,
                              fill: "tozeroy", fillcolor: t.gain + "14", line: { color: t.gain, width: 2 }, hovertemplate: "$%{x:.0f}: $%{y:,.0f}<extra></extra>" },
                            { x: selected.payoff_prices, y: selected.payoff_pnl.map(v => v < 0 ? v : 0), type: "scatter" as const, mode: "lines" as const,
                              fill: "tozeroy", fillcolor: t.loss + "1a", line: { width: 0 }, hoverinfo: "skip" as const, showlegend: false },
                          ]} layout={{ height: 200, margin: { l: 40, r: 10, t: 10, b: 30 }, paper_bgcolor: "transparent", plot_bgcolor: t.plot,
                            font: { family: "Inter", color: t.text, size: 9 }, xaxis: { title: "Price", gridcolor: t.grid }, yaxis: { title: "P&L ($)", gridcolor: t.grid, zeroline: true, zerolinecolor: t.muted }, showlegend: false,
                            shapes: [
                              { type: "line", x0: selected.spot, x1: selected.spot, y0: 0, y1: 1, yref: "paper", line: { color: t.accent, width: 1, dash: "dash" } },
                              { type: "line", x0: selected.breakeven, x1: selected.breakeven, y0: 0, y1: 1, yref: "paper", line: { color: t.loss, width: 1, dash: "dot" } },
                            ],
                            annotations: [
                              { x: selected.spot, y: 1, yref: "paper", text: "Spot", showarrow: false, font: { size: 8, color: t.accent } },
                              { x: selected.breakeven, y: 0, yref: "paper", text: "BE", showarrow: false, font: { size: 7, color: t.loss }, yanchor: "top" },
                            ],
                          }} config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
                        </div>
                      )}
                      {selected.decay_days?.length > 0 && (
                        <div>
                          <div className="metric-label mb-1">Theta Decay</div>
                          <Plot data={[{ x: selected.decay_days, y: selected.decay_vals, type: "scatter" as const, mode: "lines" as const,
                            fill: "tozeroy", fillcolor: t.accent + "10", line: { color: t.accent, width: 2 }, hovertemplate: "Day %{x}: $%{y:,.0f}<extra></extra>" }]}
                            layout={{ height: 200, margin: { l: 40, r: 10, t: 10, b: 30 }, paper_bgcolor: "transparent", plot_bgcolor: t.plot,
                              font: { family: "Inter", color: t.text, size: 9 }, xaxis: { title: "Days", gridcolor: t.grid }, yaxis: { title: "Value ($)", gridcolor: t.grid }, showlegend: false,
                            }} config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
                        </div>
                      )}
                    </div>
                  </div>
                )}

                {/* Management */}
                {detailTab === 1 && (
                  <div className="space-y-4">
                    <div className="grid grid-cols-3 gap-3">
                      <div className="card card-compact bg-surface-alt"><div className="metric-label">Take Profit</div><div className="text-sm font-semibold mt-1">{selected.profit_target_pct}%</div><div className="text-xs text-text-muted">${selected.target_profit}</div></div>
                      <div className="card card-compact bg-surface-alt"><div className="metric-label">Stop Loss</div><div className="text-sm font-semibold mt-1">{selected.stop_multiplier}× premium</div><div className="text-xs text-text-muted">${selected.stop_loss}</div></div>
                      <div className="card card-compact bg-surface-alt"><div className="metric-label">30Δ Trigger</div><div className="text-sm font-semibold mt-1">${selected.trigger_30d.toFixed(0)}</div><div className="text-xs text-text-muted">Close or roll when short hits 30Δ</div></div>
                    </div>
                    {selected.hist_winrate && (
                      <div className="card card-compact border-border">
                        <div className="metric-label mb-2">Historical Backtest ({selected.hist_winrate.n_trials} trades)</div>
                        <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
                          <Metric label="Managed WR" value={`${selected.hist_winrate.win_rate}%`} />
                          <Metric label="Exp-Only WR" value={`${selected.hist_winrate.exp_win_rate}%`} />
                          <Metric label="Early Profits" value={String(selected.hist_winrate.early_profit)} />
                          <Metric label="Stopped Out" value={String(selected.hist_winrate.stopped_out)} />
                          <Metric label="Breached" value={String(selected.hist_winrate.breached_at_exp)} />
                        </div>
                        <p className="text-[0.6rem] text-text-muted mt-2">Avg max move: {selected.hist_winrate.avg_max_move_pct}% · Median: {selected.hist_winrate.median_max_move_pct}%</p>
                      </div>
                    )}
                    {!selected.hist_winrate && <div className="text-xs text-text-muted italic">Insufficient price history for backtest.</div>}
                    {/* Stress test */}
                    {selected.stress_test?.length > 0 && (
                      <div className="card card-compact border-border">
                        <div className="metric-label mb-2">Forward Event Stress Test</div>
                        <div className="overflow-x-auto">
                          <table className="data-table text-xs">
                            <thead><tr><th>Event</th><th>Date</th><th>Scenario</th><th>Move</th><th>P&L</th><th>OK?</th></tr></thead>
                            <tbody>{selected.stress_test.map((s: ICStressScenario, i: number) => (
                              <tr key={i}>
                                <td className="font-semibold">{s.event}</td>
                                <td className="font-data">{fmtExp(s.date)} ({s.days_away}d)</td>
                                <td className="font-data">{s.scenario}</td>
                                <td className="font-data">{s.move_pct.toFixed(1)}%</td>
                                <td className={`font-data ${s.pnl >= 0 ? "text-gain" : "text-loss"}`}>${s.pnl}</td>
                                <td>{s.survives ? <span className="text-gain font-semibold">OK</span> : <span className="text-loss font-semibold">STOP</span>}</td>
                              </tr>
                            ))}</tbody>
                          </table>
                        </div>
                        {selected.stress_test.every(s => s.survives)
                          ? <p className="text-xs text-gain mt-2">All scenarios survive.</p>
                          : <p className="text-xs text-loss mt-2">{selected.stress_test.filter(s => !s.survives).length} scenario(s) hit stop. Consider skipping.</p>}
                      </div>
                    )}
                    {(!selected.stress_test || selected.stress_test.length === 0) && <div className="text-xs text-gain italic">No known events within DTE window.</div>}
                    <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                      <Metric label="Managed WR" value={`${selected.managed_wr}%`} />
                      <Metric label="Kelly (full)" value={`${selected.kelly_full}%`} />
                      <Metric label="Kelly (adj)" value={`${selected.kelly_adj}%`} />
                      <Metric label="Contracts" value={String(selected.contracts)} />
                    </div>
                  </div>
                )}

                {/* Compare Expirations */}
                {detailTab === 2 && (
                  <div className="space-y-3">
                    <p className="text-[0.65rem] text-text-muted">Same spread structure across alternative expirations.</p>
                    {selected.alt_expirations?.length > 0 ? (
                      <div className="overflow-x-auto">
                        <table className="data-table text-xs">
                          <thead><tr><th>Exp</th><th>DTE</th><th>Strikes</th><th>Credit/Debit</th><th>$/Day</th><th>Risk</th><th>POP</th></tr></thead>
                          <tbody>
                            <tr className="bg-accent-light">
                              <td className="font-semibold">{fmtExp(selected.expiration)} ★</td>
                              <td className="font-data">{selected.dte}</td>
                              <td className="font-data">${selected.long_strike.toFixed(0)}/${selected.short_strike.toFixed(0)}</td>
                              <td className="font-data">${selected.fill_estimate}</td>
                              <td className="font-data">${(selected.fill_estimate / Math.max(selected.dte, 1)).toFixed(1)}</td>
                              <td className="font-data">${selected.max_risk}</td>
                              <td className="font-data">{selected.pop}%</td>
                            </tr>
                            {selected.alt_expirations.map((alt, i) => (
                              <tr key={i}>
                                <td className="font-semibold">{fmtExp(alt.exp)}</td>
                                <td className="font-data">{alt.dte}</td>
                                <td className="font-data">{alt.strikes}</td>
                                <td className="font-data">${alt.credit}</td>
                                <td className="font-data">${alt.credit_per_day}</td>
                                <td className="font-data">${alt.max_risk}</td>
                                <td className="font-data">{alt.pop}%</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    ) : <p className="text-xs text-text-muted italic">No alternative expirations in DTE range.</p>}
                  </div>
                )}

                {/* Greeks */}
                {detailTab === 3 && (
                  <div className="space-y-4">
                    <div className="grid grid-cols-4 gap-3">
                      <Metric label="Δ Delta" value={`${(selected.net_delta * 100).toFixed(1)}`} />
                      <Metric label="Γ Gamma" value={`${(selected.net_gamma * 100).toFixed(2)}`} />
                      <Metric label="Θ Theta" value={`$${(selected.net_theta * 100).toFixed(1)}/d`} />
                      <Metric label="ν Vega" value={`$${(selected.net_vega * 100).toFixed(1)}/1%`} />
                    </div>
                    {selected.legs && (
                      <div className="overflow-x-auto">
                        <table className="data-table text-xs">
                          <thead><tr><th>Leg</th><th>Bid</th><th>Ask</th><th>Mid</th><th>Δ</th><th>OI</th><th>Quote</th></tr></thead>
                          <tbody>{selected.legs.map((leg, i) => (
                            <tr key={i}>
                              <td className="font-semibold">{leg.label}</td>
                              <td className="font-data">${leg.bid.toFixed(2)}</td><td className="font-data">${leg.ask.toFixed(2)}</td>
                              <td className="font-data">${leg.mid.toFixed(2)}</td><td className="font-data">{leg.delta.toFixed(3)}</td>
                              <td className="font-data">{leg.oi.toLocaleString()}</td>
                              <td><span className={`badge ${leg.live ? "badge-gain" : "badge-warn"}`}>{leg.live ? "Live" : "Synthetic"}</span></td>
                            </tr>
                          ))}</tbody>
                        </table>
                      </div>
                    )}
                  </div>
                )}

                {/* Results Table */}
                {detailTab === 4 && (
                  <div className="overflow-x-auto">
                    <table className="data-table text-xs">
                      <thead><tr><th>Ticker</th><th>Type</th><th>Liq</th><th>Exp</th><th>Strikes</th><th>Premium</th><th>Risk</th><th>Profit</th><th>POP</th><th>IVR</th><th>Score</th></tr></thead>
                      <tbody>{filtered.map((r, i) => (
                        <tr key={r.ticker+r.expiration+r.spread_type} className={i === selectedIdx ? "bg-accent-light" : "cursor-pointer"} onClick={() => { setSelectedIdx(i); setDetailTab(0); }}>
                          <td className="font-semibold">{r.ticker}</td>
                          <td><span className={`text-[0.6rem] ${TYPE_COLORS[r.spread_type]||""}`}>{r.spread_label}</span></td>
                          <td><span className={LIQ_COLORS[r.liq_grade]||""}>{r.liq_grade}</span></td>
                          <td className="font-data">{fmtExp(r.expiration)} ({r.dte}d)</td>
                          <td className="font-data">${r.long_strike.toFixed(0)}/${r.short_strike.toFixed(0)}</td>
                          <td className="font-data">${r.fill_estimate}</td>
                          <td className="font-data">${r.max_risk}</td>
                          <td className="font-data text-gain">${r.max_profit}</td>
                          <td className="font-data">{r.pop}%</td>
                          <td className="font-data">{r.ivr?.toFixed(0) ?? "—"}</td>
                          <td className="font-data">{r.adj_score.toFixed(3)}</td>
                        </tr>
                      ))}</tbody>
                    </table>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      </>)}

      {scan.isError && <div className="card border-loss/30 bg-loss-bg text-loss text-sm">Scan failed: {(scan.error as Error).message}</div>}
    </div>
  );
}

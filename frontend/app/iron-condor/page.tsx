"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { scanIronCondors, addPosition, type ICResult, type ICScanConfig } from "@/lib/api";
import { Metric } from "@/components/ui/metric";
import { FreshnessBar } from "@/components/ui/freshness-dot";
import dynamic from "next/dynamic";

const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

const DEFAULT_TICKERS = [
  "SPY", "QQQ", "IWM", "DIA", "AAPL", "TSLA", "NVDA", "AMD", "AMZN", "META",
  "MSFT", "GOOGL", "NFLX", "GLD", "SMH", "XLF", "TLT", "EEM", "JPM", "BA",
];

function fmtExp(exp: string) {
  try { return new Date(exp + "T12:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric" }); }
  catch { return exp; }
}

const LIQ_COLORS: Record<string, string> = { A: "text-gain", B: "text-green-500", C: "text-warn", D: "text-orange-400", F: "text-loss" };
const BAND_COLORS: Record<string, string> = { Optimal: "badge-gain", Normal: "badge-info", Extreme: "badge-warn", Low: "badge-loss" };

export default function IronCondorScanner() {
  const [tickers, setTickers] = useState(DEFAULT_TICKERS.join(", "));
  const [dteMin, setDteMin] = useState(7);
  const [dteMax, setDteMax] = useState(90);
  const [shortDelta, setShortDelta] = useState(0.25);
  const [wingWidth, setWingWidth] = useState(10);
  const [profitTarget, setProfitTarget] = useState(50);
  const [stopMult, setStopMult] = useState(1.5);
  const [accountSize, setAccountSize] = useState(25000);
  const [maxRiskPct, setMaxRiskPct] = useState(5.0);
  const [kellyFrac, setKellyFrac] = useState(0.5);

  const [results, setResults] = useState<ICResult[]>([]);
  const [scanTime, setScanTime] = useState<Date | null>(null);
  const [selectedIdx, setSelectedIdx] = useState(0);
  const [sortBy, setSortBy] = useState("adj_score");
  const [minPop, setMinPop] = useState(40);
  const [minLiq, setMinLiq] = useState("Any");
  const [showN, setShowN] = useState("All");
  const [booked, setBooked] = useState<Set<string>>(new Set());
  const [detailTab, setDetailTab] = useState(0);

  const scan = useMutation({
    mutationFn: (config: Partial<ICScanConfig>) => scanIronCondors(config),
    onSuccess: (data) => { setResults(data.results); setScanTime(new Date()); setSelectedIdx(0); },
  });

  function handleScan() {
    scan.mutate({
      tickers: tickers.split(",").map(t => t.trim().toUpperCase()).filter(Boolean),
      dte_min: dteMin, dte_max: dteMax, short_delta: shortDelta, wing_width: wingWidth,
      profit_target_pct: profitTarget, stop_multiplier: stopMult,
      account_size: accountSize, max_risk_pct: maxRiskPct, kelly_fraction: kellyFrac,
    });
  }

  const LIQ_ORDER: Record<string, number> = { A: 4, B: 3, C: 2, D: 1, F: 0 };
  let filtered = results.filter(r => r.pop >= minPop);
  if (minLiq !== "Any") {
    const minVal = { "D+": 1, "C+": 2, "B+": 3, "A": 4 }[minLiq] ?? 0;
    filtered = filtered.filter(r => (LIQ_ORDER[r.liq_grade] ?? 0) >= minVal);
  }
  filtered.sort((a, b) => ((b as any)[sortBy] ?? 0) - ((a as any)[sortBy] ?? 0));
  const preTopN = filtered.length;
  if (showN === "Top 5") filtered = filtered.slice(0, 5);
  else if (showN === "Top 10") filtered = filtered.slice(0, 10);
  else if (showN === "Top 20") filtered = filtered.slice(0, 20);

  const selected = filtered[selectedIdx] || null;
  const ageMin = scanTime ? (Date.now() - scanTime.getTime()) / 60000 : null;

  async function handleBook(r: ICResult) {
    try {
      await addPosition({
        ticker: r.ticker, type: "iron_condor", qty: r.contracts || 1,
        entry_price: r.fill_estimate / 100,
        details: {
          strategy: "iron_condor", short_put: r.short_put, long_put: r.long_put,
          short_call: r.short_call, long_call: r.long_call, expiration: r.expiration,
          dte: r.dte, credit: r.credit, max_risk: r.max_risk, pop: r.pop,
        },
        source_page: "next_iron_condor_scanner",
      });
      setBooked(prev => new Set(prev).add(r.ticker + r.expiration));
    } catch (e) { console.error("Book failed:", e); }
  }

  const DETAIL_TABS = ["Overview", "Management", "Greeks", "Results Table"];

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Iron Condor Scanner</h1>
        <p className="text-text-secondary text-sm mt-1">
          Scan for the best short iron condor setups ranked by credit, probability of profit, and IV percentile.
        </p>
      </div>

      {/* Config */}
      <div className="card">
        <div className="grid grid-cols-2 sm:grid-cols-5 gap-3 mb-3">
          {[
            ["Min DTE", dteMin, setDteMin, 1], ["Max DTE", dteMax, setDteMax, 1],
            ["Short Delta", shortDelta, setShortDelta, 0.01], ["Wing Width ($)", wingWidth, setWingWidth, 1],
            ["Profit Target (%)", profitTarget, setProfitTarget, 5],
          ].map(([label, val, setter, step]) => (
            <div key={label as string}>
              <label className="metric-label">{label as string}</label>
              <input type="number" step={step as number} value={val as number}
                onChange={e => (setter as any)(+e.target.value)}
                className="w-full mt-1 px-2 py-1.5 border border-border rounded-lg text-sm font-data bg-surface" />
            </div>
          ))}
        </div>
        <textarea value={tickers} onChange={e => setTickers(e.target.value)} rows={2}
          className="w-full px-3 py-2 border border-border rounded-lg text-sm font-data bg-surface mb-3" />
        <button onClick={handleScan} disabled={scan.isPending}
          className="w-full py-2.5 bg-accent text-white font-semibold rounded-lg hover:bg-accent-hover
                     disabled:opacity-50 disabled:cursor-not-allowed transition-colors">
          {scan.isPending ? "Scanning..." : "Scan for Iron Condors"}
        </button>
        {scan.isPending && (
          <div className="mt-3 text-center">
            <div className="inline-block w-5 h-5 border-2 border-accent border-t-transparent rounded-full animate-spin" />
            <p className="text-sm text-text-muted mt-2">Scanning {tickers.split(",").filter(Boolean).length} tickers...</p>
          </div>
        )}
      </div>

      {results.length > 0 && (
        <>
          <FreshnessBar sources={[{ label: "Chains", ageMinutes: ageMin, greenThreshold: 30, yellowThreshold: 120 }]} />

          {/* Portfolio Summary Bar */}
          <div className="card card-compact">
            <div className="flex flex-wrap gap-6">
              <Metric label="Setups" value={String(filtered.length)} />
              <Metric label="Total Contracts" value={String(filtered.reduce((s, r) => s + (r.contracts || 0), 0))} />
              <Metric label="Total Credit" value={`$${filtered.reduce((s, r) => s + (r.total_credit || 0), 0).toLocaleString()}`} />
              <Metric label="Total Risk" value={`$${filtered.reduce((s, r) => s + (r.total_risk || 0), 0).toLocaleString()}`} />
              <Metric label="Earnings Risk" value={`${filtered.filter(r => r.earnings_before).length}`} />
            </div>
          </div>

          {/* Filters */}
          <div className="card card-compact">
            <div className="flex flex-wrap items-center gap-3">
              {[
                ["Sort", sortBy, setSortBy, { Score: "adj_score", POP: "pop", Credit: "credit", IVR: "ivr", VRP: "vrp", "Hist WR": "managed_wr", Liquidity: "liq_grade" }],
                ["Show", showN, setShowN, { All: "All", "Top 5": "Top 5", "Top 10": "Top 10", "Top 20": "Top 20" }],
                ["Min Liq", minLiq, setMinLiq, { Any: "Any", "D+": "D+", "C+": "C+", "B+": "B+", A: "A" }],
              ].map(([label, val, setter, opts]) => (
                <div key={label as string} className="flex items-center gap-1.5">
                  <span className="text-[0.65rem] text-text-muted uppercase">{label as string}</span>
                  <select value={val as string} onChange={e => (setter as any)(e.target.value)}
                    className="text-xs border border-border rounded px-1.5 py-1 bg-surface">
                    {Object.entries(opts as Record<string, string>).map(([k, v]) => (
                      <option key={k} value={v}>{k}</option>
                    ))}
                  </select>
                </div>
              ))}
              <div className="flex items-center gap-1.5">
                <span className="text-[0.65rem] text-text-muted uppercase">Min POP</span>
                <input type="number" value={minPop} onChange={e => setMinPop(+e.target.value)}
                  className="w-14 text-xs border border-border rounded px-1.5 py-1 bg-surface font-data" />
              </div>
              <span className="text-xs text-text-muted ml-auto">
                {filtered.length}{preTopN > filtered.length ? ` of ${preTopN}` : ""} setups
              </span>
            </div>
          </div>

          {/* Results grid + detail */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            {/* List */}
            <div className="space-y-1 max-h-[700px] overflow-y-auto">
              {filtered.map((r, i) => (
                <button key={r.ticker + r.expiration} onClick={() => { setSelectedIdx(i); setDetailTab(0); }}
                  className={`w-full text-left card card-compact transition-colors ${
                    i === selectedIdx ? "border-accent bg-accent-light" : "hover:bg-surface-alt"}`}>
                  <div className="flex justify-between items-center">
                    <div>
                      <span className="font-bold text-sm">{r.ticker}</span>
                      <span className="text-text-muted text-[0.65rem] ml-1.5">{fmtExp(r.expiration)} · {r.dte}d</span>
                    </div>
                    <div className="text-right font-data">
                      <span className="font-semibold text-sm">${r.fill_estimate}</span>
                      <span className={`text-[0.65rem] ml-1.5 ${LIQ_COLORS[r.liq_grade] || ""}`}>{r.liq_grade}</span>
                    </div>
                  </div>
                  <div className="flex gap-2 mt-0.5 text-[0.6rem] text-text-muted font-data">
                    <span>POP {r.pop}%</span>
                    <span>IVR {r.ivr ?? "—"}</span>
                    <span>Score {r.adj_score.toFixed(3)}</span>
                    {r.contracts > 0 && <span>{r.contracts}×</span>}
                    {r.earnings_before && <span className="text-loss">EARN</span>}
                    {r.n_synthetic > 0 && <span className="text-warn">SYN</span>}
                  </div>
                </button>
              ))}
            </div>

            {/* Detail */}
            {selected && (
              <div className="lg:col-span-2 space-y-3">
                {/* Header + badges */}
                <div className="card">
                  <div className="flex justify-between items-start mb-3">
                    <div>
                      <h2 className="text-xl font-bold">{selected.ticker}</h2>
                      <p className="text-sm text-text-muted">{fmtExp(selected.expiration)} · {selected.dte}d · Spot ${selected.spot.toFixed(2)}</p>
                    </div>
                    <span className={`badge ${BAND_COLORS[selected.ivr_band] || "badge-info"}`}>
                      IVR {selected.ivr?.toFixed(0) ?? "N/A"} · {selected.ivr_band}
                    </span>
                  </div>

                  {/* Warnings */}
                  {(selected.earnings_before || selected.n_synthetic > 0 || selected.ivr_band === "Low") && (
                    <div className="flex flex-wrap gap-1.5 mb-3">
                      {selected.earnings_before && <span className="badge badge-loss">Earnings in {selected.earnings_days}d</span>}
                      {selected.n_synthetic > 0 && <span className="badge badge-warn">{selected.n_synthetic} legs no live quote</span>}
                      {selected.ivr_band === "Low" && <span className="badge badge-loss">IVR below 30</span>}
                    </div>
                  )}

                  {/* Key metrics */}
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-3">
                    <Metric label="Open For" value={`$${selected.fill_estimate}`} />
                    <Metric label="Max Risk" value={`$${selected.max_risk}`} />
                    <Metric label="POP" value={`${selected.pop}%`} />
                    <Metric label="Expiration" value={fmtExp(selected.expiration)} />
                  </div>

                  <div className="text-[0.65rem] font-data text-text-muted mb-3">
                    Natural ${selected.natural} · <strong>Fill ${selected.fill_estimate}</strong> · Mid ${selected.mid}
                  </div>

                  {/* Leg diagram */}
                  <div className="flex items-center justify-center gap-1 text-[0.65rem] font-data mb-3 flex-wrap">
                    <span className="px-1.5 py-0.5 rounded border border-loss text-loss">{selected.long_put.toFixed(0)}P</span>
                    <span className="text-text-muted">—</span>
                    <span className="px-1.5 py-0.5 rounded border border-warn text-warn">{selected.short_put.toFixed(0)}P</span>
                    <span className="text-text-muted">· {selected.spot.toFixed(0)} ·</span>
                    <span className="px-1.5 py-0.5 rounded border border-warn text-warn">{selected.short_call.toFixed(0)}C</span>
                    <span className="text-text-muted">—</span>
                    <span className="px-1.5 py-0.5 rounded border border-loss text-loss">{selected.long_call.toFixed(0)}C</span>
                  </div>

                  {/* Book It */}
                  <div className="flex gap-3 items-center">
                    <button onClick={() => handleBook(selected)}
                      disabled={booked.has(selected.ticker + selected.expiration)}
                      className={`flex-1 py-2 rounded-lg font-semibold text-sm transition-colors ${
                        booked.has(selected.ticker + selected.expiration)
                          ? "bg-gain/20 text-gain" : "bg-accent text-white hover:bg-accent-hover"}`}>
                      {booked.has(selected.ticker + selected.expiration)
                        ? `✓ Booked` : `Book ${selected.contracts || 1}× ${selected.ticker}`}
                    </button>
                    <div className="text-[0.65rem] text-text-muted font-data">
                      {selected.contracts || 1}× · ${selected.fill_estimate}/ct · Kelly {selected.kelly_adj?.toFixed(1)}%
                    </div>
                  </div>
                </div>

                {/* Sub-tabs */}
                <div className="card">
                  <div className="flex gap-1 mb-3 border-b border-border pb-2">
                    {DETAIL_TABS.map((tab, i) => (
                      <button key={tab} onClick={() => setDetailTab(i)}
                        className={`px-3 py-1 text-xs font-semibold rounded-md transition-colors ${
                          detailTab === i ? "bg-accent text-white" : "text-text-muted hover:text-text hover:bg-surface-alt"}`}>
                        {tab}
                      </button>
                    ))}
                  </div>

                  {/* Overview tab */}
                  {detailTab === 0 && (
                    <div className="space-y-4">
                      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
                        {selected.payoff_prices?.length > 0 && (
                          <div>
                            <div className="metric-label mb-1">P&L at Expiration</div>
                            <Plot
                              data={[
                                { x: selected.payoff_prices, y: selected.payoff_pnl, type: "scatter" as const, mode: "lines" as const,
                                  fill: "tozeroy", fillcolor: "rgba(15,123,63,0.08)", line: { color: "#0f7b3f", width: 2 },
                                  hovertemplate: "$%{x:.0f}: $%{y:,.0f}<extra></extra>" },
                                { x: selected.payoff_prices, y: selected.payoff_pnl.map(v => v < 0 ? v : 0),
                                  type: "scatter" as const, mode: "lines" as const, fill: "tozeroy",
                                  fillcolor: "rgba(185,28,28,0.1)", line: { color: "transparent", width: 0 },
                                  hoverinfo: "skip" as const, showlegend: false },
                              ]}
                              layout={{
                                height: 200, margin: { l: 40, r: 10, t: 10, b: 30 },
                                paper_bgcolor: "transparent", plot_bgcolor: "#fff",
                                font: { family: "Inter", color: "#1a2332", size: 9 },
                                xaxis: { title: "Price", gridcolor: "#f1f3f5" },
                                yaxis: { title: "P&L ($)", gridcolor: "#f1f3f5", zeroline: true, zerolinecolor: "#495057" },
                                showlegend: false,
                                shapes: [
                                  { type: "line", x0: selected.spot, x1: selected.spot, y0: 0, y1: 1, yref: "paper", line: { color: "#1a56db", width: 1, dash: "dash" } },
                                  { type: "line", x0: selected.lower_be, x1: selected.lower_be, y0: 0, y1: 1, yref: "paper", line: { color: "#b91c1c", width: 1, dash: "dot" } },
                                  { type: "line", x0: selected.upper_be, x1: selected.upper_be, y0: 0, y1: 1, yref: "paper", line: { color: "#b91c1c", width: 1, dash: "dot" } },
                                  { type: "line", x0: selected.put_30d_trigger, x1: selected.put_30d_trigger, y0: 0, y1: 1, yref: "paper", line: { color: "#f59e0b", width: 1, dash: "dashdot" } },
                                  { type: "line", x0: selected.call_30d_trigger, x1: selected.call_30d_trigger, y0: 0, y1: 1, yref: "paper", line: { color: "#f59e0b", width: 1, dash: "dashdot" } },
                                ],
                                annotations: [
                                  { x: selected.spot, y: 1, yref: "paper", text: "Spot", showarrow: false, font: { size: 8, color: "#1a56db" } },
                                  { x: selected.lower_be, y: 0, yref: "paper", text: `BE`, showarrow: false, font: { size: 7, color: "#b91c1c" }, yanchor: "top" },
                                  { x: selected.upper_be, y: 0, yref: "paper", text: `BE`, showarrow: false, font: { size: 7, color: "#b91c1c" }, yanchor: "top" },
                                  { x: selected.put_30d_trigger, y: 1, yref: "paper", text: "30Δ", showarrow: false, font: { size: 7, color: "#f59e0b" } },
                                  { x: selected.call_30d_trigger, y: 1, yref: "paper", text: "30Δ", showarrow: false, font: { size: 7, color: "#f59e0b" } },
                                ],
                              }}
                              config={{ displayModeBar: false, responsive: true }}
                              style={{ width: "100%" }}
                            />
                          </div>
                        )}
                        {selected.decay_days?.length > 0 && (
                          <div>
                            <div className="metric-label mb-1">Theta Decay</div>
                            <Plot
                              data={[{ x: selected.decay_days, y: selected.decay_vals, type: "scatter" as const, mode: "lines" as const,
                                fill: "tozeroy", fillcolor: "rgba(26,86,219,0.06)", line: { color: "#1a56db", width: 2 },
                                hovertemplate: "Day %{x}: $%{y:,.0f}<extra></extra>" }]}
                              layout={{
                                height: 200, margin: { l: 40, r: 10, t: 10, b: 30 },
                                paper_bgcolor: "transparent", plot_bgcolor: "#fff",
                                font: { family: "Inter", color: "#1a2332", size: 9 },
                                xaxis: { title: "Days", gridcolor: "#f1f3f5" },
                                yaxis: { title: "Value ($)", gridcolor: "#f1f3f5" },
                                showlegend: false,
                                shapes: [
                                  { type: "line", x0: 0, x1: selected.dte, y0: selected.credit, y1: selected.credit, line: { color: "#0f7b3f", width: 1, dash: "dot" } },
                                  { type: "line", x0: 0, x1: selected.dte, y0: selected.target_credit, y1: selected.target_credit, line: { color: "#b45309", width: 1, dash: "dash" } },
                                ],
                                annotations: [
                                  { x: 0, y: selected.credit, text: "Credit", showarrow: false, xanchor: "left", font: { size: 8, color: "#0f7b3f" } },
                                  { x: 0, y: selected.target_credit, text: `${selected.profit_target_pct}% Target`, showarrow: false, xanchor: "left", font: { size: 8, color: "#b45309" } },
                                ],
                              }}
                              config={{ displayModeBar: false, responsive: true }}
                              style={{ width: "100%" }}
                            />
                          </div>
                        )}
                      </div>
                      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                        <Metric label="Lower BE" value={`$${selected.lower_be.toFixed(0)}`} />
                        <Metric label="Upper BE" value={`$${selected.upper_be.toFixed(0)}`} />
                        <Metric label="IV" value={`${selected.avg_iv}%`} />
                        <Metric label="VRP" value={selected.vrp != null ? `${selected.vrp > 0 ? "+" : ""}${selected.vrp}%` : "N/A"} />
                      </div>
                    </div>
                  )}

                  {/* Management tab */}
                  {detailTab === 1 && (
                    <div className="space-y-4">
                      <div className="grid grid-cols-3 gap-3">
                        <div className="card card-compact bg-surface-alt">
                          <div className="metric-label">Take Profit</div>
                          <div className="text-sm font-semibold mt-1">{selected.profit_target_pct}% of credit</div>
                          <div className="text-xs text-text-muted">${selected.target_credit}</div>
                        </div>
                        <div className="card card-compact bg-surface-alt">
                          <div className="metric-label">Stop Loss</div>
                          <div className="text-sm font-semibold mt-1">{selected.stop_multiplier}× credit</div>
                          <div className="text-xs text-text-muted">${selected.stop_loss_amt}</div>
                        </div>
                        <div className="card card-compact bg-surface-alt">
                          <div className="metric-label">Time Stop</div>
                          <div className="text-sm font-semibold mt-1">21 DTE</div>
                          <div className="text-xs text-text-muted">Roll or close</div>
                        </div>
                      </div>
                      <div className="card card-compact bg-surface-alt">
                        <div className="metric-label">30Δ Adjustment Triggers</div>
                        <div className="text-sm mt-1">
                          Put: <strong className="font-data">${selected.put_30d_trigger.toFixed(0)}</strong> ·
                          Call: <strong className="font-data">${selected.call_30d_trigger.toFixed(0)}</strong>
                          <span className="text-text-muted"> — roll untested side toward the money</span>
                        </div>
                      </div>
                      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                        <Metric label="Managed WR" value={`${selected.managed_wr}%`} />
                        <Metric label="Kelly (full)" value={`${selected.kelly_full}%`} />
                        <Metric label="Kelly (adj)" value={`${selected.kelly_adj}%`} />
                        <Metric label="Contracts" value={String(selected.contracts)} />
                      </div>
                    </div>
                  )}

                  {/* Greeks tab */}
                  {detailTab === 2 && (
                    <div className="space-y-4">
                      <div className="grid grid-cols-5 gap-3">
                        <Metric label="Δ Delta" value={`${(selected.net_delta * 100).toFixed(1)}`} />
                        <Metric label="Γ Gamma" value={`${(selected.net_gamma * 100).toFixed(2)}`} />
                        <Metric label="Θ Theta" value={`$${(selected.net_theta * 100).toFixed(1)}/d`} />
                        <Metric label="ν Vega" value={`$${(selected.net_vega * 100).toFixed(1)}/1%`} />
                        <Metric label="Θ/ν" value={`${selected.theta_vega_ratio.toFixed(2)}`} />
                      </div>
                      {selected.legs && (
                        <div className="overflow-x-auto">
                          <table className="data-table">
                            <thead>
                              <tr>
                                <th>Leg</th><th>Bid</th><th>Ask</th><th>Mid</th><th>B/A</th>
                                <th>Δ</th><th>Γ</th><th>Θ</th><th>ν</th>
                                <th>OI</th><th>Vol</th><th>Quote</th>
                              </tr>
                            </thead>
                            <tbody>
                              {selected.legs.map((leg, i) => (
                                <tr key={i}>
                                  <td className="font-semibold">{leg.label}</td>
                                  <td className="font-data">${leg.bid.toFixed(2)}</td>
                                  <td className="font-data">${leg.ask.toFixed(2)}</td>
                                  <td className="font-data">${leg.mid.toFixed(2)}</td>
                                  <td className="font-data">${(leg.ask - leg.bid).toFixed(2)}</td>
                                  <td className="font-data">{leg.delta.toFixed(3)}</td>
                                  <td className="font-data">{leg.gamma.toFixed(4)}</td>
                                  <td className="font-data">{leg.theta.toFixed(3)}</td>
                                  <td className="font-data">{leg.vega.toFixed(3)}</td>
                                  <td className="font-data">{leg.oi.toLocaleString()}</td>
                                  <td className="font-data">{leg.vol.toLocaleString()}</td>
                                  <td><span className={`badge ${leg.live ? "badge-gain" : "badge-warn"}`}>
                                    {leg.live ? "Live" : "Synthetic"}</span></td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      )}
                      <div className="grid grid-cols-3 gap-3">
                        <Metric label="Liquidity" value={selected.liq_grade} />
                        <Metric label="Min OI" value={selected.min_oi.toLocaleString()} />
                        <Metric label="Max B/A" value={selected.max_ba != null ? `$${selected.max_ba.toFixed(2)}` : "N/A"} />
                      </div>
                    </div>
                  )}

                  {/* Results Table tab */}
                  {detailTab === 3 && (
                    <div className="overflow-x-auto">
                      <table className="data-table">
                        <thead>
                          <tr>
                            <th>Ticker</th><th>Liq</th><th>Exp</th><th>Strikes</th>
                            <th>Credit</th><th>Risk</th><th>POP</th><th>IVR</th><th>VRP</th>
                            <th>Contracts</th><th>Score</th>
                          </tr>
                        </thead>
                        <tbody>
                          {filtered.map((r, i) => (
                            <tr key={r.ticker + r.expiration}
                              className={i === selectedIdx ? "bg-accent-light" : "cursor-pointer"}
                              onClick={() => { setSelectedIdx(i); setDetailTab(0); }}>
                              <td className="font-semibold">{r.ticker}</td>
                              <td><span className={LIQ_COLORS[r.liq_grade] || ""}>{r.liq_grade}</span></td>
                              <td className="font-data">{fmtExp(r.expiration)} ({r.dte}d)</td>
                              <td className="font-data">{r.short_put.toFixed(0)}P/{r.short_call.toFixed(0)}C</td>
                              <td className="font-data">${r.fill_estimate}</td>
                              <td className="font-data">${r.max_risk}</td>
                              <td className="font-data">{r.pop}%</td>
                              <td className="font-data">{r.ivr?.toFixed(0) ?? "—"}</td>
                              <td className="font-data">{r.vrp != null ? `${r.vrp > 0 ? "+" : ""}${r.vrp}` : "—"}</td>
                              <td className="font-data">{r.contracts}</td>
                              <td className="font-data">{r.adj_score.toFixed(3)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>
        </>
      )}

      {scan.isError && (
        <div className="card border-loss/30 bg-loss-bg text-loss text-sm">
          Scan failed: {(scan.error as Error).message}
        </div>
      )}
    </div>
  );
}

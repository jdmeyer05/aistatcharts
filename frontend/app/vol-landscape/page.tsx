"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTheme } from "next-themes";
import { fetchVolLandscape, fetchAITradeIdeas, type VolLandscapeScan } from "@/lib/api";
import { getChartTheme, getBaseLayout } from "@/lib/chart-theme";
import { Metric } from "@/components/ui/metric";
import dynamic from "next/dynamic";

const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

const TABS = ["Vol Landscape", "Market Environment", "Metrics Table", "Signals & Alerts", "AI Analysis"];

const MONEYNESS_PTS = ["0.9", "0.95", "0.98", "1.0", "1.02", "1.05", "1.1"];
const MONEYNESS_LABELS = ["90%", "95%", "98%", "ATM", "102%", "105%", "110%"];

export default function VolLandscapePage() {
  const { resolvedTheme } = useTheme();
  const t = getChartTheme(resolvedTheme === "dark");
  const L = getBaseLayout(t);
  const [activeTab, setActiveTab] = useState(0);
  const [data, setData] = useState<VolLandscapeScan | null>(null);
  const [groupFilter, setGroupFilter] = useState<"All" | "Sectors" | "Macro">("All");
  const [sortBy, setSortBy] = useState("Group");
  const [aiContent, setAiContent] = useState("");

  const load = useMutation({
    mutationFn: fetchVolLandscape,
    onSuccess: d => { setData(d); setAiContent(""); },
  });

  const loadAI = useMutation({
    mutationFn: async () => {
      if (!data) throw new Error("Load data first");
      const ctx = [
        `CROSS-ASSET OPTIONS LANDSCAPE (${data.count} tickers)`,
        `REGIME: ${data.regime} | ACTION: ${data.regime_action}`,
        `Avg IV: ${data.summary.avg_iv}% | IV/HV: ${data.summary.avg_ivhv}x | Skew: ${data.summary.avg_skew}x`,
        `Inverted: ${data.summary.n_inverted}/${data.summary.n_tickers} | Steep: ${data.summary.n_steep_skew}/${data.summary.n_tickers}`,
        data.impl_corr != null ? `Impl Corr: ${data.impl_corr}` : "",
        "",
        ...data.metrics.map(m =>
          `${m.Ticker} (${m.Label}) [${m.Group}] $${m.Spot?.toFixed(2)} IV=${((m.Front_IV ?? 0) * 100).toFixed(1)}% IV/HV=${m.IV_HV?.toFixed(2) ?? "N/A"}x Skew=${m.Put_Skew?.toFixed(2)}x TS=${((m.TS_Slope ?? 0) * 100).toFixed(1)}%/mo`
        ),
        "",
        ...data.divergences.map(d => `DIVERGENCE: ${d.pair} (${d.metric}): ${d.signal}`),
      ].filter(Boolean).join("\n");
      return fetchAITradeIdeas({ ticker: "VOL_LANDSCAPE", context: ctx, style: "full_scan" });
    },
    onSuccess: d => setAiContent(d.content),
  });

  // Filter + sort metrics
  const filteredMetrics = data ? data.metrics
    .filter(m => groupFilter === "All" || m.Group === groupFilter)
    .sort((a, b) => {
      if (sortBy === "Front IV") return (b.Front_IV ?? 0) - (a.Front_IV ?? 0);
      if (sortBy === "Skew") return (b.Put_Skew ?? 0) - (a.Put_Skew ?? 0);
      if (sortBy === "IV/HV") return (b.IV_HV ?? 0) - (a.IV_HV ?? 0);
      return (a.Group ?? "").localeCompare(b.Group ?? "");
    }) : [];

  const filteredTickers = filteredMetrics.map(m => m.Ticker);

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Vol Landscape</h1>
        <p className="text-text-secondary text-sm mt-1">Cross-asset volatility surface analysis across 20 ETFs.</p>
      </div>

      <div className="card card-compact">
        <button onClick={() => load.mutate()} disabled={load.isPending}
          className="px-6 py-2 bg-accent text-white font-semibold rounded-lg hover:bg-accent-hover disabled:opacity-50 text-sm">
          {load.isPending ? "Scanning 20 ETFs (~15s)..." : "Scan Market"}
        </button>
      </div>

      {load.isPending && (
        <div className="card text-center py-16">
          <div className="inline-block w-8 h-8 border-2 border-accent border-t-transparent rounded-full animate-spin" />
          <p className="text-sm text-text-muted mt-4">Loading options chains for 20 ETFs and computing cross-asset metrics...</p>
        </div>
      )}

      {data && (<>
        {/* Regime Banner */}
        <div className={`card card-compact border-l-4 ${data.regime.includes("Rich") || data.regime.includes("Fear") ? "border-l-loss" : data.regime.includes("Cheap") || data.regime.includes("Low") ? "border-l-gain" : data.regime.includes("Event") ? "border-l-warn" : "border-l-border"}`}>
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div>
              <div className={`text-sm font-bold ${data.regime.includes("Rich") || data.regime.includes("Fear") ? "text-loss" : data.regime.includes("Cheap") ? "text-gain" : data.regime.includes("Event") ? "text-warn" : ""}`}>
                {data.regime}
              </div>
              <div className="text-xs text-text-muted mt-0.5">{data.regime_action}</div>
            </div>
            <div className="flex flex-wrap gap-3">
              <Metric label="Avg IV" value={`${data.summary.avg_iv.toFixed(1)}%`} />
              <Metric label="IV/HV" value={`${data.summary.avg_ivhv.toFixed(2)}x`} />
              <Metric label="Skew" value={`${data.summary.avg_skew.toFixed(2)}x`} />
              <Metric label="Inverted" value={`${data.summary.n_inverted}/${data.summary.n_tickers}`} />
              {data.impl_corr != null && <Metric label="Impl Corr" value={data.impl_corr.toFixed(2)} />}
            </div>
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

        {/* ═══ TAB 0: Vol Landscape Heatmaps ═══ */}
        {activeTab === 0 && (
          <div className="space-y-5">
            {/* Controls */}
            <div className="flex items-center gap-3 flex-wrap">
              <div className="flex gap-1">
                {(["All", "Sectors", "Macro"] as const).map(g => (
                  <button key={g} onClick={() => setGroupFilter(g)}
                    className={`px-2 py-1 text-xs rounded ${groupFilter === g ? "bg-accent text-white" : "text-text-muted hover:bg-surface-alt"}`}>{g}</button>
                ))}
              </div>
              <select value={sortBy} onChange={e => setSortBy(e.target.value)}
                className="px-2 py-1 border border-border rounded text-xs bg-surface">
                <option value="Group">Sort: Group</option>
                <option value="Front IV">Sort: Front IV</option>
                <option value="Skew">Sort: Skew</option>
                <option value="IV/HV">Sort: IV/HV</option>
              </select>
            </div>

            {/* Smile Heatmap */}
            {data.smile_data.length > 0 && (() => {
              const smiles = data.smile_data.filter(s => filteredTickers.includes(s.ticker));
              const tickers = smiles.map(s => s.ticker);
              const z = smiles.map(s => MONEYNESS_PTS.map(m => (s[m] as number) ?? 0));
              return (
                <div className="card">
                  <div className="text-sm font-bold mb-1">Volatility Smile (Front Month)</div>
                  <div className="text-xs text-text-muted mb-2">Left = OTM puts (crash protection). Right = OTM calls. Center = ATM.</div>
                  <Plot data={[{
                    type: "heatmap" as const, z, x: MONEYNESS_LABELS, y: tickers,
                    colorscale: "RdYlBu_r",
                    text: z.map(row => row.map(v => v.toFixed(1))), texttemplate: "%{text}",
                    textfont: { size: 10 },
                    colorbar: { title: { text: "IV %", font: { size: 9 } }, thickness: 12, len: 0.6 },
                    hovertemplate: "<b>%{y}</b> | %{x}<br>IV: %{z:.1f}%<extra></extra>",
                  }]}
                    layout={{ height: Math.max(350, tickers.length * 28 + 80), ...L, margin: { l: 60, r: 20, t: 10, b: 40 },
                      xaxis: { title: "Moneyness", gridcolor: t.grid }, yaxis: { autorange: "reversed" } }}
                    config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
                </div>
              );
            })()}

            {/* Term Structure Heatmap */}
            {data.ts_data.length > 0 && (() => {
              const ts = data.ts_data.filter(s => filteredTickers.includes(s.ticker));
              const maxCols = Math.max(...ts.map(s => s.term_structure.length));
              const tickers = ts.map(s => s.ticker);
              const dteLabels = ts[0]?.term_structure.map(t => `${t.dte}d`) ?? [];
              const z = ts.map(s => {
                const row = s.term_structure.map(t => t.iv);
                while (row.length < maxCols) row.push(0);
                return row;
              });
              return (
                <div className="card">
                  <div className="text-sm font-bold mb-1">ATM IV Term Structure</div>
                  <div className="text-xs text-text-muted mb-2">Hotter left than right = backwardation (near-term fear).</div>
                  <Plot data={[{
                    type: "heatmap" as const, z, x: dteLabels.slice(0, maxCols), y: tickers,
                    colorscale: "Viridis",
                    text: z.map(row => row.map(v => v.toFixed(1))), texttemplate: "%{text}",
                    textfont: { size: 10 },
                    colorbar: { title: { text: "IV %", font: { size: 9 } }, thickness: 12, len: 0.6 },
                    hovertemplate: "<b>%{y}</b> | %{x}<br>IV: %{z:.1f}%<extra></extra>",
                  }]}
                    layout={{ height: Math.max(350, tickers.length * 28 + 80), ...L, margin: { l: 60, r: 20, t: 10, b: 40 },
                      xaxis: { title: "DTE", gridcolor: t.grid }, yaxis: { autorange: "reversed" } }}
                    config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
                </div>
              );
            })()}

            {/* Expected Move Rankings */}
            {(() => {
              const moveData = filteredMetrics.filter(m => m.Impl_Move > 0).sort((a, b) => b.Impl_Move - a.Impl_Move);
              if (moveData.length === 0) return null;
              return (
                <div className="card">
                  <div className="text-sm font-bold mb-1">Expected Move Rankings</div>
                  <div className="text-xs text-text-muted mb-2">Straddle-implied move for front month. Stars = earnings within front expiration.</div>
                  <Plot data={[{
                    x: moveData.map(m => m.Ticker),
                    y: moveData.map(m => m.Impl_Move),
                    type: "bar" as const,
                    marker: { color: moveData.map(m => {
                      const hasEarn = data.earnings[m.Ticker] && data.earnings[m.Ticker].days <= m.Front_DTE;
                      return hasEarn ? "#ff66ff" : m.Impl_Move > 8 ? t.loss : m.Impl_Move > 4 ? t.spot : t.accent;
                    })},
                    text: moveData.map(m => {
                      const hasEarn = data.earnings[m.Ticker] && data.earnings[m.Ticker].days <= m.Front_DTE;
                      return `${m.Impl_Move.toFixed(1)}%${hasEarn ? "*" : ""}`;
                    }),
                    textposition: "outside" as const, textfont: { size: 9, color: t.text },
                  }]}
                    layout={{ height: 250, ...L, yaxis: { title: "Implied Move (%)", gridcolor: t.grid } }}
                    config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
                </div>
              );
            })()}
          </div>
        )}

        {/* ═══ TAB 1: Market Environment ═══ */}
        {activeTab === 1 && (
          <div className="space-y-4">
            {/* IV/HV Ranking */}
            <div className="card">
              <div className="text-sm font-bold mb-1">Implied vs Realized Volatility</div>
              <div className="text-xs text-text-muted mb-2">Red (&gt;1.2x) = sell premium. Green (&lt;0.85x) = buy protection.</div>
              {(() => {
                const ivhv = filteredMetrics.filter(m => m.IV_HV != null).sort((a, b) => (b.IV_HV ?? 0) - (a.IV_HV ?? 0));
                return (
                  <Plot data={[{
                    x: ivhv.map(m => m.Ticker), y: ivhv.map(m => m.IV_HV),
                    type: "bar" as const,
                    marker: { color: ivhv.map(m => (m.IV_HV ?? 0) > 1.2 ? t.loss : (m.IV_HV ?? 0) < 0.85 ? t.gain : t.accent) },
                    hovertemplate: "<b>%{x}</b><br>IV/HV: %{y:.2f}x<extra></extra>",
                  }]}
                    layout={{ height: 280, ...L, yaxis: { title: "IV / HV20", gridcolor: t.grid },
                      shapes: [
                        { type: "line", y0: 1.2, y1: 1.2, x0: 0, x1: 1, xref: "paper", line: { color: t.loss, width: 1, dash: "dash" } },
                        { type: "line", y0: 1.0, y1: 1.0, x0: 0, x1: 1, xref: "paper", line: { color: t.muted, width: 1, dash: "dot" } },
                        { type: "line", y0: 0.85, y1: 0.85, x0: 0, x1: 1, xref: "paper", line: { color: t.gain, width: 1, dash: "dash" } },
                      ] }}
                    config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
                );
              })()}
            </div>

            {/* Two-column: Term Structure + Skew */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
              {/* Term Structure Slope */}
              <div className="card">
                <div className="text-sm font-bold mb-1">Term Structure</div>
                <div className="text-xs text-text-muted mb-2">Red = backwardation (event risk).</div>
                {(() => {
                  const ts = filteredMetrics.filter(m => m.TS_Slope != null).sort((a, b) => (a.TS_Slope ?? 0) - (b.TS_Slope ?? 0));
                  return (
                    <Plot data={[{
                      x: ts.map(m => m.Ticker), y: ts.map(m => (m.TS_Slope ?? 0) * 100),
                      type: "bar" as const,
                      marker: { color: ts.map(m => (m.TS_Slope ?? 0) < 0 ? t.loss : t.gain) },
                    }]}
                      layout={{ height: 250, ...L, yaxis: { title: "%/mo", gridcolor: t.grid },
                        shapes: [{ type: "line", y0: 0, y1: 0, x0: 0, x1: 1, xref: "paper", line: { color: t.muted, width: 1, dash: "dot" } }] }}
                      config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
                  );
                })()}
              </div>

              {/* Put Skew */}
              <div className="card">
                <div className="text-sm font-bold mb-1">Put Skew (25Δ / ATM)</div>
                <div className="text-xs text-text-muted mb-2">&gt;1.10x = heavy fear premium.</div>
                {(() => {
                  const sk = filteredMetrics.sort((a, b) => (b.Put_Skew ?? 0) - (a.Put_Skew ?? 0));
                  return (
                    <Plot data={[{
                      x: sk.map(m => m.Ticker), y: sk.map(m => m.Put_Skew),
                      type: "bar" as const,
                      marker: { color: sk.map(m => (m.Put_Skew ?? 0) > 1.10 ? t.loss : (m.Put_Skew ?? 0) < 1.03 ? t.gain : t.accent) },
                    }]}
                      layout={{ height: 250, ...L, yaxis: { title: "Skew", gridcolor: t.grid },
                        shapes: [{ type: "line", y0: 1.10, y1: 1.10, x0: 0, x1: 1, xref: "paper", line: { color: t.loss, width: 1, dash: "dash" } }] }}
                      config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
                  );
                })()}
              </div>
            </div>

            {/* VRP Scatter */}
            {(() => {
              const vrp = filteredMetrics.filter(m => m.VRP_Vol != null && m.IV_Pctile != null);
              if (vrp.length < 3) return null;
              return (
                <div className="card">
                  <div className="text-sm font-bold mb-1">VRP Concentration</div>
                  <div className="text-xs text-text-muted mb-2">Top-right = rich premium (sell). Bottom-left = cheap protection (buy).</div>
                  <Plot data={[
                    { x: vrp.filter(m => m.Group === "Sectors").map(m => m.IV_Pctile),
                      y: vrp.filter(m => m.Group === "Sectors").map(m => (m.VRP_Vol ?? 0) * 100),
                      text: vrp.filter(m => m.Group === "Sectors").map(m => m.Ticker),
                      type: "scatter" as const, mode: "markers+text" as const, name: "Sectors",
                      textposition: "top center" as const, textfont: { size: 9 },
                      marker: { size: 10, color: t.accent, line: { width: 1, color: t.grid } } },
                    { x: vrp.filter(m => m.Group === "Macro").map(m => m.IV_Pctile),
                      y: vrp.filter(m => m.Group === "Macro").map(m => (m.VRP_Vol ?? 0) * 100),
                      text: vrp.filter(m => m.Group === "Macro").map(m => m.Ticker),
                      type: "scatter" as const, mode: "markers+text" as const, name: "Macro",
                      textposition: "top center" as const, textfont: { size: 9 },
                      marker: { size: 10, color: t.spot, line: { width: 1, color: t.grid } } },
                  ]}
                    layout={{ height: 350, ...L, xaxis: { title: "IV Percentile", gridcolor: t.grid }, yaxis: { title: "VRP (IV - HV, %)", gridcolor: t.grid }, hovermode: "closest",
                      shapes: [
                        { type: "line", y0: 0, y1: 0, x0: 0, x1: 1, xref: "paper", line: { color: t.muted, width: 1, dash: "dot" } },
                        { type: "line", x0: 50, x1: 50, y0: 0, y1: 1, yref: "paper", line: { color: t.muted, width: 1, dash: "dot" } },
                      ] }}
                    config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
                </div>
              );
            })()}

            {/* Implied Correlation Gauge */}
            {data.impl_corr != null && (
              <div className="card">
                <div className="text-sm font-bold mb-1">Implied Correlation</div>
                <div className="text-xs text-text-muted mb-2">Low (&lt;0.4) = dispersion opportunity. High (&gt;0.7) = systemic risk.</div>
                <div className="flex items-center gap-6">
                  <div className={`text-4xl font-bold font-data ${data.impl_corr > 0.7 ? "text-loss" : data.impl_corr < 0.4 ? "text-gain" : "text-warn"}`}>
                    {data.impl_corr.toFixed(2)}
                  </div>
                  <div className="flex-1">
                    <div className="h-3 bg-surface-alt rounded-full overflow-hidden flex">
                      <div className="bg-gain h-full" style={{ width: "40%" }} />
                      <div className="bg-warn h-full" style={{ width: "30%" }} />
                      <div className="bg-loss h-full" style={{ width: "30%" }} />
                    </div>
                    <div className="flex justify-between text-[0.55rem] text-text-muted mt-0.5">
                      <span>0 — Dispersion</span><span>0.4</span><span>0.7</span><span>1 — Systemic</span>
                    </div>
                  </div>
                </div>
              </div>
            )}
          </div>
        )}

        {/* ═══ TAB 2: Metrics Table ═══ */}
        {activeTab === 2 && (
          <div className="card">
            <div className="overflow-x-auto">
              <table className="data-table text-xs">
                <thead>
                  <tr><th>Ticker</th><th>Name</th><th>Group</th><th>Spot</th><th>Front IV</th><th>IV/HV</th><th>Skew</th><th>TS</th><th>VRP</th><th>Move%</th><th>HV20</th><th>P/C</th><th>%ile</th></tr>
                </thead>
                <tbody>
                  {filteredMetrics.map(m => (
                    <tr key={m.Ticker}>
                      <td className="font-semibold">{m.Ticker}</td>
                      <td className="text-text-muted">{m.Label}</td>
                      <td>{m.Group}</td>
                      <td className="font-data">${m.Spot?.toFixed(2)}</td>
                      <td className="font-data">{((m.Front_IV ?? 0) * 100).toFixed(1)}%</td>
                      <td className={`font-data font-semibold ${(m.IV_HV ?? 0) > 1.2 ? "text-loss" : (m.IV_HV ?? 0) < 0.85 ? "text-gain" : ""}`}>
                        {m.IV_HV?.toFixed(2) ?? "—"}x
                      </td>
                      <td className={`font-data ${m.Put_Skew > 1.10 ? "text-loss" : ""}`}>{m.Put_Skew?.toFixed(2)}x</td>
                      <td className={`font-data ${(m.TS_Slope ?? 0) < 0 ? "text-loss" : ""}`}>{((m.TS_Slope ?? 0) * 100).toFixed(1)}</td>
                      <td className="font-data">{m.VRP_Vol != null ? `${(m.VRP_Vol * 100).toFixed(1)}%` : "—"}</td>
                      <td className="font-data">{m.Impl_Move > 0 ? `${m.Impl_Move.toFixed(1)}%` : "—"}</td>
                      <td className="font-data">{m.HV20 != null ? `${(m.HV20 * 100).toFixed(1)}%` : "—"}</td>
                      <td className="font-data">{m.PC_Ratio?.toFixed(2) ?? "—"}</td>
                      <td className="font-data">{m.IV_Pctile?.toFixed(0) ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* ═══ TAB 3: Signals & Alerts ═══ */}
        {activeTab === 3 && (
          <div className="space-y-4">
            {/* Regime */}
            <div className={`card border-l-4 ${data.regime.includes("Rich") || data.regime.includes("Fear") ? "border-l-loss" : data.regime.includes("Cheap") ? "border-l-gain" : "border-l-warn"}`}>
              <div className={`text-sm font-bold ${data.regime.includes("Rich") || data.regime.includes("Fear") ? "text-loss" : data.regime.includes("Cheap") ? "text-gain" : "text-warn"}`}>{data.regime}</div>
              <div className="text-xs text-text-muted mt-1">{data.regime_action}</div>
            </div>

            {/* Divergences */}
            {data.divergences.length > 0 && (
              <div className="space-y-2">
                <div className="text-sm font-bold">Cross-Asset Divergences</div>
                {data.divergences.map((dv, i) => (
                  <div key={i} className="card card-compact border-l-4 border-l-warn">
                    <div className="text-xs font-bold text-warn">{dv.pair} — {dv.metric} Divergence</div>
                    <div className="text-[0.7rem] text-text-muted">{dv.description}</div>
                    <div className="text-xs mt-1">{dv.signal}</div>
                  </div>
                ))}
              </div>
            )}

            {/* Richest / Cheapest */}
            <div className="grid grid-cols-2 gap-4">
              <div className="card border-l-4 border-l-loss">
                <div className="text-xs font-bold text-loss mb-2">Richest Vol</div>
                {[...filteredMetrics].sort((a, b) => (b.Front_IV ?? 0) - (a.Front_IV ?? 0)).slice(0, 3).map(m => (
                  <div key={m.Ticker} className="text-xs py-0.5">
                    <span className="font-semibold">{m.Ticker}</span> IV:{((m.Front_IV ?? 0) * 100).toFixed(1)}% {m.IV_HV ? `${m.IV_HV.toFixed(2)}x` : ""}
                  </div>
                ))}
              </div>
              <div className="card border-l-4 border-l-gain">
                <div className="text-xs font-bold text-gain mb-2">Cheapest Vol</div>
                {[...filteredMetrics].sort((a, b) => (a.Front_IV ?? 0) - (b.Front_IV ?? 0)).slice(0, 3).map(m => (
                  <div key={m.Ticker} className="text-xs py-0.5">
                    <span className="font-semibold">{m.Ticker}</span> IV:{((m.Front_IV ?? 0) * 100).toFixed(1)}% {m.IV_HV ? `${m.IV_HV.toFixed(2)}x` : ""}
                  </div>
                ))}
              </div>
            </div>

            {/* Correlation risk */}
            {data.impl_corr != null && data.impl_corr > 0.7 && data.summary.n_inverted >= 3 && (
              <div className="card border-l-4 border-l-loss">
                <div className="text-sm font-bold text-loss">SYSTEMIC EVENT RISK</div>
                <div className="text-xs text-text-muted mt-1">Corr {data.impl_corr.toFixed(2)} + {data.summary.n_inverted} inverted = correlated shock. Index hedges efficient.</div>
              </div>
            )}
            {data.impl_corr != null && data.impl_corr < 0.35 && (
              <div className="card border-l-4 border-l-gain">
                <div className="text-sm font-bold text-gain">DISPERSION OPPORTUNITY</div>
                <div className="text-xs text-text-muted mt-1">Corr {data.impl_corr.toFixed(2)} = sell index vol, buy sector vol.</div>
              </div>
            )}
          </div>
        )}

        {/* ═══ TAB 4: AI Analysis ═══ */}
        {activeTab === 4 && (
          <div className="card space-y-4">
            {!aiContent && (
              <button onClick={() => loadAI.mutate()} disabled={loadAI.isPending}
                className="px-6 py-2 bg-accent text-white font-semibold rounded-lg hover:bg-accent-hover disabled:opacity-50 text-sm">
                {loadAI.isPending ? "Generating..." : "Generate AI Vol Briefing (Gemini)"}
              </button>
            )}
            {loadAI.isPending && (
              <div className="text-center py-8">
                <div className="inline-block w-6 h-6 border-2 border-accent border-t-transparent rounded-full animate-spin" />
                <p className="text-sm text-text-muted mt-3">Gemini analyzing cross-asset landscape...</p>
              </div>
            )}
            {aiContent && (
              <div className="prose prose-sm max-w-none text-sm dark:prose-invert" dangerouslySetInnerHTML={{
                __html: (() => {
                  const escaped = aiContent.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
                  return escaped
                    .replace(/^## (.*?)$/gm, '<h3 class="text-base font-bold mt-4 mb-2">$1</h3>')
                    .replace(/^#### (.*?)$/gm, '<h4 class="text-sm font-semibold mt-3 mb-1">$1</h4>')
                    .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
                    .replace(/\n/g, "<br/>");
                })(),
              }} />
            )}
          </div>
        )}
      </>)}

      {load.isError && <div className="card border-loss/30 bg-loss-bg text-loss text-sm">Failed: {(load.error as Error).message}</div>}
    </div>
  );
}

"use client";

import { useState, useMemo } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTheme } from "next-themes";
import { fetchOptionsChain, fetchSnapshot } from "@/lib/api";
import { getChartTheme, getBaseLayout } from "@/lib/chart-theme";
import { Metric } from "@/components/ui/metric";
import dynamic from "next/dynamic";

const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

const TABS = ["IV Skew", "Open Interest", "Gamma Exposure", "Max Pain", "Greeks Heatmap", "Chain View", "Vol Surface 3D", "Term Structure", "Unusual Activity"];

function calcDTE(exp: string): number { return Math.max(1, Math.round((new Date(exp + "T16:00:00").getTime() - Date.now()) / 86400000)); }

interface ChainRow {
  strike_price: number; contract_type: string; expiration_date: string;
  implied_volatility: number; delta: number; gamma: number; theta: number; vega: number;
  open_interest: number; volume: number; last_price: number; bid: number; ask: number;
}

export default function OptionsAnalysis() {
  const { resolvedTheme } = useTheme();
  const t = getChartTheme(resolvedTheme === "dark");
  const L = getBaseLayout(t);
  const [ticker, setTicker] = useState("SPY");
  const [activeTab, setActiveTab] = useState(0);
  const [chain, setChain] = useState<ChainRow[]>([]);
  const [spot, setSpot] = useState(0);
  const [selectedExp, setSelectedExp] = useState("");

  const load = useMutation({
    mutationFn: async (tk: string) => {
      const [ch, snap] = await Promise.all([fetchOptionsChain(tk), fetchSnapshot([tk])]);
      return { chain: ch.data as unknown as ChainRow[], spot: snap[tk]?.price ?? 0 };
    },
    onSuccess: (d) => {
      setChain(d.chain);
      setSpot(d.spot);
      const exps = [...new Set(d.chain.map(c => c.expiration_date))].sort();
      if (exps.length > 0) setSelectedExp(exps[0]);
    },
  });

  const expirations = useMemo(() => [...new Set(chain.map(c => c.expiration_date))].sort(), [chain]);
  const expChain = useMemo(() => chain.filter(c => c.expiration_date === selectedExp), [chain, selectedExp]);
  const calls = useMemo(() => expChain.filter(c => c.contract_type === "call").sort((a, b) => a.strike_price - b.strike_price), [expChain]);
  const puts = useMemo(() => expChain.filter(c => c.contract_type === "put").sort((a, b) => a.strike_price - b.strike_price), [expChain]);

  const strikeLo = spot * 0.85, strikeHi = spot * 1.15;
  const visCalls = calls.filter(c => c.strike_price >= strikeLo && c.strike_price <= strikeHi);
  const visPuts = puts.filter(c => c.strike_price >= strikeLo && c.strike_price <= strikeHi);

  // Max pain calculation
  const maxPain = useMemo(() => {
    if (!expChain.length || !spot) return null;
    const strikes = [...new Set(expChain.map(c => c.strike_price))].sort((a, b) => a - b)
      .filter(s => s >= strikeLo && s <= strikeHi);
    let minPain = Infinity, mpStrike = spot;
    for (const testStrike of strikes) {
      let pain = 0;
      for (const c of expChain) {
        if (c.contract_type === "call" && testStrike > c.strike_price) pain += (testStrike - c.strike_price) * c.open_interest * 100;
        if (c.contract_type === "put" && testStrike < c.strike_price) pain += (c.strike_price - testStrike) * c.open_interest * 100;
      }
      if (pain < minPain) { minPain = pain; mpStrike = testStrike; }
    }
    return { strike: mpStrike, pain: minPain };
  }, [expChain, spot, strikeLo, strikeHi]);

  // GEX
  const gex = useMemo(() => {
    if (!spot || !expChain.length) return null;
    const byStrike = new Map<number, number>();
    for (const c of expChain.filter(c => c.gamma > 0 && c.strike_price >= strikeLo && c.strike_price <= strikeHi)) {
      const g = c.gamma * c.open_interest * 100 * spot * spot / 1e7;
      const net = c.contract_type === "call" ? g : -g;
      byStrike.set(c.strike_price, (byStrike.get(c.strike_price) ?? 0) + net);
    }
    return [...byStrike.entries()].sort(([a], [b]) => a - b);
  }, [expChain, spot, strikeLo, strikeHi]);

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Options Analysis</h1>
        <p className="text-text-secondary text-sm mt-1">IV skew, open interest walls, gamma exposure, max pain, Greeks heatmap.</p>
      </div>

      <div className="card card-compact">
        <div className="flex items-center gap-3 flex-wrap">
          <input type="text" value={ticker} onChange={e => setTicker(e.target.value.toUpperCase())}
            onKeyDown={e => e.key === "Enter" && load.mutate(ticker)}
            placeholder="SPY" className="w-32 px-3 py-2 border border-border rounded-lg text-sm font-data bg-surface" />
          <button onClick={() => load.mutate(ticker)} disabled={load.isPending}
            className="px-6 py-2 bg-accent text-white font-semibold rounded-lg hover:bg-accent-hover disabled:opacity-50 text-sm">
            {load.isPending ? "Loading..." : "Fetch Chain"}
          </button>
          {expirations.length > 0 && (
            <select value={selectedExp} onChange={e => setSelectedExp(e.target.value)}
              className="px-2 py-2 border border-border rounded-lg text-sm bg-surface">
              {expirations.map(e => <option key={e} value={e}>{e}</option>)}
            </select>
          )}
        </div>
      </div>

      {load.isPending && (
        <div className="card text-center py-12">
          <div className="inline-block w-6 h-6 border-2 border-accent border-t-transparent rounded-full animate-spin" />
          <p className="text-sm text-text-muted mt-3">Fetching chain...</p>
        </div>
      )}

      {chain.length > 0 && (
        <>
          <div className="card card-compact">
            <div className="flex flex-wrap gap-6">
              <Metric label="Spot" value={`$${spot.toFixed(2)}`} />
              <Metric label="Contracts" value={String(expChain.length)} />
              <Metric label="Expirations" value={String(expirations.length)} />
              {maxPain && <Metric label="Max Pain" value={`$${maxPain.strike.toFixed(0)}`} />}
            </div>
          </div>

          <div className="flex gap-1 border-b border-border pb-1 overflow-x-auto">
            {TABS.map((tab, i) => (
              <button key={tab} onClick={() => setActiveTab(i)}
                className={`px-3 py-1.5 text-xs font-semibold rounded-t-md transition-colors whitespace-nowrap ${
                  activeTab === i ? "bg-accent text-white" : "text-text-muted hover:text-text hover:bg-surface-alt"}`}>
                {tab}
              </button>
            ))}
          </div>

          {/* Tab 0: IV Skew */}
          {activeTab === 0 && (
            <div className="card">
              <Plot data={[
                { x: visCalls.map(c => c.strike_price), y: visCalls.map(c => c.implied_volatility * 100), type: "scatter" as const, mode: "lines+markers" as const, name: "Call IV", line: { color: t.gain, width: 2 }, marker: { size: 4 } },
                { x: visPuts.map(c => c.strike_price), y: visPuts.map(c => c.implied_volatility * 100), type: "scatter" as const, mode: "lines+markers" as const, name: "Put IV", line: { color: t.loss, width: 2 }, marker: { size: 4 } },
              ]} layout={{ height: 400, ...L, xaxis: { title: "Strike", gridcolor: t.grid }, yaxis: { title: "IV (%)", gridcolor: t.grid }, hovermode: "x unified",
                shapes: [{ type: "line", x0: spot, x1: spot, y0: 0, y1: 1, yref: "paper", line: { color: t.spot, width: 1, dash: "dot" } }] }}
                config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
            </div>
          )}

          {/* Tab 1: Open Interest */}
          {activeTab === 1 && (
            <div className="card">
              <Plot data={[
                { x: visCalls.map(c => c.strike_price), y: visCalls.map(c => c.open_interest), type: "bar" as const, name: "Call OI", marker: { color: t.gain }, opacity: 0.8 },
                { x: visPuts.map(c => c.strike_price), y: visPuts.map(c => c.open_interest), type: "bar" as const, name: "Put OI", marker: { color: t.loss }, opacity: 0.8 },
              ]} layout={{ height: 400, ...L, barmode: "group", xaxis: { title: "Strike", gridcolor: t.grid }, yaxis: { title: "Open Interest", gridcolor: t.grid }, hovermode: "x unified",
                shapes: [{ type: "line", x0: spot, x1: spot, y0: 0, y1: 1, yref: "paper", line: { color: t.spot, width: 1, dash: "dot" } }] }}
                config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
            </div>
          )}

          {/* Tab 2: GEX */}
          {activeTab === 2 && gex && (
            <div className="card space-y-4">
              <Plot data={[{
                x: gex.map(([s]) => s), y: gex.map(([, g]) => g), type: "bar" as const,
                marker: { color: gex.map(([, g]) => g > 0 ? t.gain : t.loss) },
                hovertemplate: "$%{x}: GEX %{y:,.0f}<extra></extra>",
              }]} layout={{ height: 400, ...L, xaxis: { title: "Strike", gridcolor: t.grid }, yaxis: { title: "Net GEX", gridcolor: t.grid },
                shapes: [
                  { type: "line", x0: spot, x1: spot, y0: 0, y1: 1, yref: "paper", line: { color: t.spot, width: 1, dash: "dot" } },
                  { type: "line", y0: 0, y1: 0, x0: 0, x1: 1, xref: "paper", line: { color: t.muted, width: 1 } },
                ] }}
                config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
              <div className="flex gap-6">
                <Metric label="Net GEX" value={gex.reduce((s, [, g]) => s + g, 0).toLocaleString(undefined, { maximumFractionDigits: 0 })}
                  delta={gex.reduce((s, [, g]) => s + g, 0) > 0 ? "Long Gamma (Stable)" : "Short Gamma (Vol)"} deltaType={gex.reduce((s, [, g]) => s + g, 0) > 0 ? "gain" : "loss"} />
                <Metric label="Max GEX Strike" value={`$${(gex.reduce((best, [s, g]) => g > best[1] ? [s, g] : best, [0, -Infinity] as [number, number])[0]).toFixed(0)}`} />
              </div>
            </div>
          )}

          {/* Tab 3: Max Pain */}
          {activeTab === 3 && maxPain && (
            <div className="card space-y-4">
              <div className="flex gap-6">
                <Metric label="Max Pain Strike" value={`$${maxPain.strike.toFixed(0)}`} />
                <Metric label="Spot" value={`$${spot.toFixed(2)}`} />
                <Metric label="Distance" value={`${((maxPain.strike - spot) / spot * 100).toFixed(1)}%`} deltaType={maxPain.strike > spot ? "gain" : "loss"} />
              </div>
              {(() => {
                const strikes = [...new Set(expChain.map(c => c.strike_price))].sort((a, b) => a - b).filter(s => s >= strikeLo && s <= strikeHi);
                const pains = strikes.map(testStrike => {
                  let pain = 0;
                  for (const c of expChain) {
                    if (c.contract_type === "call" && testStrike > c.strike_price) pain += (testStrike - c.strike_price) * c.open_interest * 100;
                    if (c.contract_type === "put" && testStrike < c.strike_price) pain += (c.strike_price - testStrike) * c.open_interest * 100;
                  }
                  return pain;
                });
                return (
                  <Plot data={[{
                    x: strikes, y: pains, type: "scatter" as const, mode: "lines" as const,
                    line: { color: t.accent, width: 2 }, fill: "tozeroy" as const, fillcolor: t.accent + "15",
                    hovertemplate: "$%{x}: $%{y:,.0f}<extra></extra>",
                  }]} layout={{ height: 350, ...L, xaxis: { title: "Settlement Price", gridcolor: t.grid }, yaxis: { title: "Total Pain ($)", gridcolor: t.grid },
                    shapes: [
                      { type: "line", x0: spot, x1: spot, y0: 0, y1: 1, yref: "paper", line: { color: t.spot, width: 2, dash: "dot" } },
                      { type: "line", x0: maxPain.strike, x1: maxPain.strike, y0: 0, y1: 1, yref: "paper", line: { color: t.loss, width: 2, dash: "dash" } },
                    ],
                    annotations: [
                      { x: spot, y: 1, yref: "paper", text: "Spot", showarrow: false, font: { size: 9, color: t.spot } },
                      { x: maxPain.strike, y: 1, yref: "paper", text: "Max Pain", showarrow: false, font: { size: 9, color: t.loss } },
                    ] }}
                    config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
                );
              })()}
            </div>
          )}

          {/* Tab 4: Greeks Heatmap */}
          {activeTab === 4 && (
            <div className="card">
              {(() => {
                const greekExps = expirations.slice(0, 8);
                const strikes = [...new Set(chain.filter(c => c.strike_price >= strikeLo && c.strike_price <= strikeHi).map(c => c.strike_price))].sort((a, b) => a - b);
                const z = greekExps.map(exp => strikes.map(k => {
                  const c = chain.find(c => c.expiration_date === exp && c.strike_price === k && c.contract_type === "call");
                  return c ? c.delta : null;
                }));
                return (
                  <Plot data={[{
                    type: "heatmap" as const, x: strikes, y: greekExps, z,
                    colorscale: [[0, t.loss], [0.5, t.grid], [1, t.gain]], zmid: 0.5,
                    colorbar: { title: { text: "Delta", font: { size: 9 } }, thickness: 12 },
                    hovertemplate: "$%{x} %{y}<br>Delta: %{z:.3f}<extra></extra>",
                  }]} layout={{ height: 400, ...L, margin: { l: 100, r: 20, t: 10, b: 50 },
                    xaxis: { title: "Strike", gridcolor: t.grid },
                    shapes: [{ type: "line", x0: spot, x1: spot, y0: 0, y1: 1, yref: "paper", line: { color: t.spot, width: 2, dash: "dot" } }] }}
                    config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
                );
              })()}
            </div>
          )}

          {/* Tab 5: Chain View */}
          {activeTab === 5 && (
            <div className="card">
              <div className="overflow-x-auto">
                <table className="data-table text-xs">
                  <thead><tr><th>Strike</th><th>Type</th><th>Bid</th><th>Ask</th><th>Last</th><th>IV</th><th>Delta</th><th>Gamma</th><th>Theta</th><th>Vega</th><th>OI</th><th>Volume</th></tr></thead>
                  <tbody>
                    {expChain.filter(c => c.strike_price >= strikeLo && c.strike_price <= strikeHi)
                      .sort((a, b) => a.strike_price - b.strike_price || a.contract_type.localeCompare(b.contract_type))
                      .map((c, i) => (
                      <tr key={i} className={Math.abs(c.strike_price - spot) < spot * 0.005 ? "bg-accent-light" : ""}>
                        <td className="font-data">${c.strike_price.toFixed(0)}</td>
                        <td><span className={`badge ${c.contract_type === "call" ? "badge-gain" : "badge-loss"}`}>{c.contract_type}</span></td>
                        <td className="font-data">{c.bid?.toFixed(2) ?? "—"}</td>
                        <td className="font-data">{c.ask?.toFixed(2) ?? "—"}</td>
                        <td className="font-data">{c.last_price?.toFixed(2) ?? "—"}</td>
                        <td className="font-data">{c.implied_volatility ? `${(c.implied_volatility * 100).toFixed(1)}%` : "—"}</td>
                        <td className="font-data">{c.delta?.toFixed(3) ?? "—"}</td>
                        <td className="font-data">{c.gamma?.toFixed(4) ?? "—"}</td>
                        <td className="font-data">{c.theta?.toFixed(2) ?? "—"}</td>
                        <td className="font-data">{c.vega?.toFixed(2) ?? "—"}</td>
                        <td className="font-data">{c.open_interest?.toLocaleString() ?? "—"}</td>
                        <td className="font-data">{c.volume?.toLocaleString() ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
          {/* ═══ Tab 6: Vol Surface 3D ═══ */}
          {activeTab === 6 && (() => {
            const exps = [...new Set(chain.map(c => c.expiration_date))].sort().slice(0, 8);
            const strikeLo = spot * 0.85, strikeHi = spot * 1.15;
            const rows = chain.filter(c => exps.includes(c.expiration_date) && c.strike_price >= strikeLo && c.strike_price <= strikeHi &&
              ((c.contract_type === "put" && c.strike_price < spot) || (c.contract_type === "call" && c.strike_price >= spot)) &&
              c.implied_volatility > 0.01 && c.implied_volatility < 3);
            const strikes = [...new Set(rows.map(r => r.strike_price))].sort((a, b) => a - b);
            const expLabels = exps.map(e => { const d = new Date(e + "T12:00:00"); return `${d.toLocaleDateString("en-US", { month: "short", day: "numeric" })} (${calcDTE(e)}d)`; });
            const ivMatrix = exps.map(exp => strikes.map(k => {
              const r = rows.find(c => c.expiration_date === exp && Math.abs(c.strike_price - k) < 0.5);
              return r ? r.implied_volatility * 100 : null;
            }));
            return (
              <div className="card">
                <Plot data={[
                  { type: "surface" as const, x: strikes, y: Array.from({ length: exps.length }, (_, i) => i), z: ivMatrix,
                    colorscale: "Viridis", colorbar: { title: { text: "IV %", font: { size: 10 } }, thickness: 15 },
                    hovertemplate: "Strike: $%{x:,.0f}<br>IV: %{z:.1f}%<extra></extra>",
                    lighting: { ambient: 0.6, diffuse: 0.5, specular: 0.3 }, opacity: 0.92 },
                  { type: "scatter3d" as const, x: Array(exps.length).fill(spot),
                    y: Array.from({ length: exps.length }, (_, i) => i),
                    z: ivMatrix.map(row => { const idx = strikes.reduce((b, s, i) => Math.abs(s - spot) < Math.abs(strikes[b] - spot) ? i : b, 0); return row[idx] ?? 0; }),
                    mode: "lines+markers" as const, line: { color: t.spot, width: 5 }, marker: { size: 3, color: t.spot }, name: "Spot" },
                ]} layout={{ height: 550, margin: { l: 0, r: 0, t: 10, b: 10 }, paper_bgcolor: "transparent",
                  font: { family: "Inter", color: t.text, size: 10 },
                  scene: { xaxis: { title: "Strike ($)" }, yaxis: { title: "Expiration", tickvals: Array.from({ length: exps.length }, (_, i) => i), ticktext: expLabels }, zaxis: { title: "IV %" },
                    camera: { eye: { x: 1.8, y: -1.4, z: 0.9 } } },
                }} config={{ displayModeBar: true, responsive: true }} style={{ width: "100%", height: "550px" }} />
              </div>
            );
          })()}

          {/* ═══ Tab 7: Term Structure ═══ */}
          {activeTab === 7 && (() => {
            const exps = [...new Set(chain.map(c => c.expiration_date))].sort();
            const ts = exps.map(exp => {
              const atm = chain.filter(c => c.expiration_date === exp && c.contract_type === "call" && c.implied_volatility > 0)
                .sort((a, b) => Math.abs(a.strike_price - spot) - Math.abs(b.strike_price - spot))[0];
              if (!atm) return null;
              const dte = calcDTE(exp);
              const iv = atm.implied_volatility * 100;
              const move = spot * atm.implied_volatility * Math.sqrt(dte / 365);
              return { exp, dte, iv, move, movePct: (move / spot * 100) };
            }).filter(Boolean) as { exp: string; dte: number; iv: number; move: number; movePct: number }[];
            const shape = ts.length >= 2 ? (ts[ts.length - 1].iv > ts[0].iv * 1.02 ? "Contango" : ts[ts.length - 1].iv < ts[0].iv * 0.98 ? "Backwardation" : "Flat") : "N/A";
            return (
              <div className="card space-y-4">
                <div className="text-sm text-text-muted">Shape: <strong className={shape === "Contango" ? "text-gain" : shape === "Backwardation" ? "text-loss" : ""}>{shape}</strong></div>
                <Plot data={[{ x: ts.map(t => `${new Date(t.exp + "T12:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric" })} (${t.dte}d)`),
                  y: ts.map(t => t.iv), type: "scatter" as const, mode: "lines+markers" as const,
                  line: { color: t.accent, width: 2 }, marker: { size: 8, color: t.accent },
                  text: ts.map(t => `${t.iv.toFixed(1)}%`), textposition: "top center" as const, textfont: { size: 9, color: t.text },
                  hovertemplate: "%{x}<br>IV: %{y:.1f}%<extra></extra>" }]}
                  layout={{ height: 300, ...L, xaxis: { title: "Expiration", gridcolor: t.grid }, yaxis: { title: "ATM IV (%)", gridcolor: t.grid } }}
                  config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
                <div className="overflow-x-auto">
                  <table className="data-table text-xs">
                    <thead><tr><th>Expiration</th><th>DTE</th><th>ATM IV</th><th>Expected Move ($)</th><th>Expected Move (%)</th></tr></thead>
                    <tbody>{ts.map((t, i) => (
                      <tr key={i}>
                        <td className="font-semibold">{new Date(t.exp + "T12:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric" })}</td>
                        <td className="font-data">{t.dte}d</td>
                        <td className="font-data">{t.iv.toFixed(1)}%</td>
                        <td className="font-data">±${t.move.toFixed(2)}</td>
                        <td className="font-data">±{t.movePct.toFixed(1)}%</td>
                      </tr>
                    ))}</tbody>
                  </table>
                </div>
              </div>
            );
          })()}

          {/* ═══ Tab 8: Unusual Activity ═══ */}
          {activeTab === 8 && (() => {
            const unusual = chain.filter(c => c.volume > 100 && c.open_interest > 0 && (c.volume / c.open_interest) > 2)
              .map(c => ({ ...c, volOI: c.volume / c.open_interest, mid: (c.bid + c.ask) / 2, notional: c.volume * ((c.bid + c.ask) / 2) * 100 }))
              .sort((a, b) => b.volOI - a.volOI).slice(0, 20);
            const totalVol = unusual.reduce((s, c) => s + c.volume, 0);
            const totalNotional = unusual.reduce((s, c) => s + c.notional, 0);
            return (
              <div className="card space-y-4">
                <div className="flex gap-6">
                  <Metric label="Unusual Strikes" value={String(unusual.length)} />
                  <Metric label="Total Unusual Volume" value={totalVol.toLocaleString()} />
                  <Metric label="Est. Notional" value={`$${(totalNotional / 1e6).toFixed(1)}M`} />
                </div>
                {unusual.length > 0 && (
                  <>
                    <Plot data={[
                      { x: unusual.filter(c => c.contract_type === "call").map(c => c.strike_price), y: unusual.filter(c => c.contract_type === "call").map(c => c.volOI),
                        type: "bar" as const, name: "Calls", marker: { color: t.gain } },
                      { x: unusual.filter(c => c.contract_type === "put").map(c => c.strike_price), y: unusual.filter(c => c.contract_type === "put").map(c => c.volOI),
                        type: "bar" as const, name: "Puts", marker: { color: t.loss } },
                    ]} layout={{ height: 250, ...L, barmode: "group",
                      xaxis: { title: "Strike", gridcolor: t.grid }, yaxis: { title: "Vol/OI Ratio", gridcolor: t.grid },
                      shapes: [{ type: "line", x0: 0, x1: 1, xref: "paper", y0: 2, y1: 2, line: { color: t.spot, width: 1, dash: "dash" } }],
                    }} config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
                    <div className="overflow-x-auto">
                      <table className="data-table text-xs">
                        <thead><tr><th>Type</th><th>Strike</th><th>Volume</th><th>OI</th><th>Vol/OI</th><th>IV</th><th>Bid</th><th>Ask</th><th>Notional</th></tr></thead>
                        <tbody>{unusual.map((c, i) => (
                          <tr key={i}>
                            <td><span className={`badge ${c.contract_type === "call" ? "badge-gain" : "badge-loss"}`}>{c.contract_type}</span></td>
                            <td className="font-data">${c.strike_price}</td>
                            <td className="font-data font-semibold">{c.volume.toLocaleString()}</td>
                            <td className="font-data">{c.open_interest.toLocaleString()}</td>
                            <td className="font-data font-semibold">{c.volOI.toFixed(1)}x</td>
                            <td className="font-data">{(c.implied_volatility * 100).toFixed(1)}%</td>
                            <td className="font-data">${c.bid.toFixed(2)}</td>
                            <td className="font-data">${c.ask.toFixed(2)}</td>
                            <td className="font-data">${(c.notional / 1000).toFixed(0)}K</td>
                          </tr>
                        ))}</tbody>
                      </table>
                    </div>
                  </>
                )}
                {unusual.length === 0 && <p className="text-xs text-text-muted">No unusual activity detected (Vol/OI &gt; 2x with Vol &gt; 100).</p>}
              </div>
            );
          })()}
        </>
      )}

      {chain.length === 0 && !load.isPending && load.isSuccess && <div className="card text-center py-8 text-text-muted">No chain data returned.</div>}
      {load.isError && <div className="card border-loss/30 bg-loss-bg text-loss text-sm">Failed: {(load.error as Error).message}</div>}
    </div>
  );
}

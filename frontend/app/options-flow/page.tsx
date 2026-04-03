"use client";

import { useState, useMemo } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTheme } from "next-themes";
import { fetchOptionsChain, fetchSnapshot } from "@/lib/api";
import { getChartTheme, getBaseLayout } from "@/lib/chart-theme";
import { Metric } from "@/components/ui/metric";
import dynamic from "next/dynamic";

const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

const TABS = ["Unusual Activity", "Put/Call Analysis", "Gamma Exposure (GEX)", "Block Trade Detection"];

interface ChainRow {
  strike_price: number; contract_type: string; expiration_date: string;
  implied_volatility: number; delta: number; gamma: number;
  open_interest: number; volume: number; last_price: number;
  bid: number; ask: number;
}

export default function OptionsFlow() {
  const { resolvedTheme } = useTheme();
  const t = getChartTheme(resolvedTheme === "dark");
  const L = getBaseLayout(t);
  const [ticker, setTicker] = useState("SPY");
  const [activeTab, setActiveTab] = useState(0);
  const [chain, setChain] = useState<ChainRow[]>([]);
  const [spot, setSpot] = useState(0);
  const [minVol, setMinVol] = useState(100);
  const [minRatio, setMinRatio] = useState(2.0);
  const [blockMinVol, setBlockMinVol] = useState(500);
  const [blockMinNotional, setBlockMinNotional] = useState(50000);

  const load = useMutation({
    mutationFn: async (tk: string) => {
      const [ch, snap] = await Promise.all([fetchOptionsChain(tk), fetchSnapshot([tk])]);
      return { chain: ch.data as unknown as ChainRow[], spot: snap[tk]?.price ?? 0 };
    },
    onSuccess: (d) => { setChain(d.chain); setSpot(d.spot); },
  });

  const calls = useMemo(() => chain.filter(c => c.contract_type === "call"), [chain]);
  const puts = useMemo(() => chain.filter(c => c.contract_type === "put"), [chain]);

  const unusual = useMemo(() => {
    return chain.filter(c => c.volume > minVol && c.open_interest > 0)
      .map(c => ({ ...c, vol_oi: c.volume / c.open_interest }))
      .filter(c => c.vol_oi >= minRatio)
      .sort((a, b) => b.vol_oi - a.vol_oi);
  }, [chain, minVol, minRatio]);

  const pcStats = useMemo(() => {
    const cv = calls.reduce((s, c) => s + c.volume, 0);
    const pv = puts.reduce((s, c) => s + c.volume, 0);
    const co = calls.reduce((s, c) => s + c.open_interest, 0);
    const po = puts.reduce((s, c) => s + c.open_interest, 0);
    const pcVol = cv > 0 ? pv / cv : 0;
    const pcOI = co > 0 ? po / co : 0;
    const histMean = 0.70, histStd = 0.15;
    const z = histStd > 0 ? (pcVol - histMean) / histStd : 0;
    const regime = z > 1.5 ? "Extreme Fear" : z > 0.5 ? "Elevated Hedging" : z < -1.5 ? "Extreme Complacency" : z < -0.5 ? "Low Hedging" : "Neutral";
    return { cv, pv, co, po, pcVol, pcOI, z, histMean, histStd, regime };
  }, [calls, puts]);

  const gex = useMemo(() => {
    if (!spot) return null;
    const gexRows = chain.filter(c => c.strike_price >= spot * 0.85 && c.strike_price <= spot * 1.15 && c.gamma > 0);
    if (gexRows.length === 0) return null;
    const byStrike = new Map<number, { call: number; put: number }>();
    for (const c of gexRows) {
      const g = c.gamma * c.open_interest * 100 * spot * spot / 1e7;
      const entry = byStrike.get(c.strike_price) ?? { call: 0, put: 0 };
      if (c.contract_type === "call") entry.call += g; else entry.put += g;
      byStrike.set(c.strike_price, entry);
    }
    const strikes = [...byStrike.keys()].sort((a, b) => a - b);
    const net = strikes.map(s => ({ strike: s, call: byStrike.get(s)!.call, put: byStrike.get(s)!.put, net: byStrike.get(s)!.call - byStrike.get(s)!.put }));
    const totalGex = net.reduce((s, n) => s + n.net, 0);
    const maxGexStrike = net.reduce((best, n) => n.net > best.net ? n : best, net[0])?.strike ?? 0;
    const minGexStrike = net.reduce((best, n) => n.net < best.net ? n : best, net[0])?.strike ?? 0;
    return { net, totalGex, maxGexStrike, minGexStrike };
  }, [chain, spot]);

  const blocks = useMemo(() => {
    return chain.filter(c => c.volume >= blockMinVol && c.last_price > 0 && c.volume * c.last_price * 100 >= blockMinNotional)
      .map(c => ({ ...c, notional: c.volume * c.last_price * 100 }))
      .sort((a, b) => b.notional - a.notional);
  }, [chain, blockMinVol, blockMinNotional]);

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Options Flow Intelligence</h1>
        <p className="text-text-secondary text-sm mt-1">Unusual activity scanner, put/call analysis, gamma exposure, block trade detection.</p>
      </div>

      <div className="card card-compact">
        <div className="flex items-center gap-3">
          <input type="text" value={ticker} onChange={e => setTicker(e.target.value.toUpperCase())}
            onKeyDown={e => e.key === "Enter" && load.mutate(ticker)}
            placeholder="SPY" className="w-32 px-3 py-2 border border-border rounded-lg text-sm font-data bg-surface" />
          <button onClick={() => load.mutate(ticker)} disabled={load.isPending}
            className="px-6 py-2 bg-accent text-white font-semibold rounded-lg hover:bg-accent-hover disabled:opacity-50 transition-colors text-sm">
            {load.isPending ? "Loading..." : "Load Options Data"}
          </button>
        </div>
      </div>

      {load.isPending && (
        <div className="card text-center py-12">
          <div className="inline-block w-6 h-6 border-2 border-accent border-t-transparent rounded-full animate-spin" />
          <p className="text-sm text-text-muted mt-3">Fetching options chain...</p>
        </div>
      )}

      {chain.length > 0 && (
        <>
          <div className="flex gap-1 border-b border-border pb-1">
            {TABS.map((tab, i) => (
              <button key={tab} onClick={() => setActiveTab(i)}
                className={`px-3 py-1.5 text-xs font-semibold rounded-t-md transition-colors whitespace-nowrap ${
                  activeTab === i ? "bg-accent text-white" : "text-text-muted hover:text-text hover:bg-surface-alt"}`}>
                {tab}
              </button>
            ))}
          </div>

          {/* Tab 0: Unusual Activity */}
          {activeTab === 0 && (
            <div className="card space-y-4">
              <div className="flex gap-4">
                <div><label className="metric-label">Min Volume</label>
                  <input type="number" value={minVol} onChange={e => setMinVol(Number(e.target.value))} className="w-24 px-2 py-1 border border-border rounded text-xs font-data bg-surface" /></div>
                <div><label className="metric-label">Min Vol/OI</label>
                  <input type="number" value={minRatio} onChange={e => setMinRatio(Number(e.target.value))} step={0.5} className="w-24 px-2 py-1 border border-border rounded text-xs font-data bg-surface" /></div>
              </div>
              {unusual.length > 0 ? (<>
                <div className="flex flex-wrap gap-6">
                  <Metric label="Unusual Contracts" value={String(unusual.length)} />
                  <Metric label="Unusual Calls" value={String(unusual.filter(c => c.contract_type === "call").length)} />
                  <Metric label="Unusual Puts" value={String(unusual.filter(c => c.contract_type === "put").length)} />
                  <Metric label="Total Unusual Vol" value={unusual.reduce((s, c) => s + c.volume, 0).toLocaleString()} />
                </div>
                <div className="overflow-x-auto">
                  <table className="data-table text-xs">
                    <thead><tr><th>Strike</th><th>Type</th><th>Exp</th><th>Volume</th><th>OI</th><th>Vol/OI</th><th>IV</th><th>Delta</th><th>Last</th></tr></thead>
                    <tbody>
                      {unusual.slice(0, 30).map((c, i) => (
                        <tr key={i}>
                          <td className="font-data">${c.strike_price.toFixed(0)}</td>
                          <td><span className={`badge ${c.contract_type === "call" ? "badge-gain" : "badge-loss"}`}>{c.contract_type}</span></td>
                          <td>{c.expiration_date}</td>
                          <td className="font-data">{c.volume.toLocaleString()}</td>
                          <td className="font-data">{c.open_interest.toLocaleString()}</td>
                          <td className="font-data font-semibold">{c.vol_oi.toFixed(1)}x</td>
                          <td className="font-data">{(c.implied_volatility * 100).toFixed(1)}%</td>
                          <td className="font-data">{c.delta?.toFixed(3) ?? "—"}</td>
                          <td className="font-data">${c.last_price.toFixed(2)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <Plot data={["call", "put"].map(ct => {
                  const sub = unusual.filter(c => c.contract_type === ct);
                  return { x: sub.map(c => c.open_interest), y: sub.map(c => c.volume), type: "scatter" as const, mode: "markers" as const,
                    name: ct === "call" ? "Calls" : "Puts",
                    marker: { color: ct === "call" ? t.gain : t.loss, size: sub.map(c => Math.min(c.vol_oi * 2, 50)), opacity: 0.7 },
                    text: sub.map(c => `$${c.strike_price}`), hovertemplate: "Strike: %{text}<br>Vol: %{y:,}<br>OI: %{x:,}<extra></extra>" };
                })} layout={{ height: 400, ...L, xaxis: { title: "Open Interest", gridcolor: t.grid }, yaxis: { title: "Volume", gridcolor: t.grid }, hovermode: "closest",
                  shapes: unusual.length > 0 ? [{ type: "line", x0: 0, y0: 0, x1: Math.max(...unusual.map(c => c.open_interest)), y1: Math.max(...unusual.map(c => c.open_interest)), line: { color: t.muted, width: 1, dash: "dot" } }] : [] }}
                  config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
                <p className="text-xs text-text-muted">Bubble size = Vol/OI ratio. Dotted line = 1:1.</p>
              </>) : <p className="text-sm text-text-muted">No unusual activity found with current filters.</p>}
            </div>
          )}

          {/* Tab 1: Put/Call Analysis */}
          {activeTab === 1 && (
            <div className="card space-y-4">
              <div className="flex flex-wrap gap-6">
                <Metric label="P/C Volume Ratio" value={pcStats.pcVol.toFixed(2)} deltaType={pcStats.pcVol > 1 ? "loss" : "gain"} />
                <Metric label="P/C OI Ratio" value={pcStats.pcOI.toFixed(2)} deltaType={pcStats.pcOI > 1 ? "loss" : "gain"} />
                <Metric label="Call Volume" value={pcStats.cv.toLocaleString()} />
                <Metric label="Put Volume" value={pcStats.pv.toLocaleString()} />
              </div>
              <p className="text-xs text-text-muted">&gt;1.0 = Bearish | &lt;1.0 = Bullish</p>
              <div className="flex flex-wrap gap-6 pt-2 border-t border-border">
                <Metric label="Z-Score" value={`${pcStats.z > 0 ? "+" : ""}${pcStats.z.toFixed(1)}σ`} />
                <Metric label="Sentiment" value={pcStats.regime} />
              </div>
              {/* Gauge */}
              {(() => {
                const gx = Array.from({ length: 200 }, (_, i) => 0.2 + i * (1.3 / 199));
                const gy = gx.map(x => Math.exp(-0.5 * ((x - pcStats.histMean) / pcStats.histStd) ** 2));
                return <Plot data={[{ x: gx, y: gy, type: "scatter" as const, mode: "lines" as const, fill: "tozeroy" as const, fillcolor: t.accent + "25", line: { color: t.accent, width: 1 }, showlegend: false, hoverinfo: "skip" as const }]}
                  layout={{ height: 180, ...L, margin: { l: 30, r: 20, t: 10, b: 30 }, xaxis: { title: "P/C Ratio", gridcolor: t.grid }, yaxis: { visible: false },
                    shapes: [
                      { type: "line", x0: pcStats.pcVol, x1: pcStats.pcVol, y0: 0, y1: 1, yref: "paper", line: { color: t.spot, width: 3 } },
                      { type: "line", x0: 0.5, x1: 0.5, y0: 0, y1: 1, yref: "paper", line: { color: t.gain, width: 1, dash: "dot" } },
                      { type: "line", x0: 1.0, x1: 1.0, y0: 0, y1: 1, yref: "paper", line: { color: t.loss, width: 1, dash: "dot" } },
                    ], annotations: [{ x: pcStats.pcVol, y: 1, yref: "paper", text: `${pcStats.pcVol.toFixed(2)}`, showarrow: false, font: { size: 9, color: t.spot } }] }}
                  config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />;
              })()}
              {pcStats.z > 1.5 && <div className="text-xs text-gain bg-gain-bg border border-gain/20 rounded-lg px-3 py-2">Contrarian Bullish: extreme put buying historically marks bottoms.</div>}
              {pcStats.z < -1.5 && <div className="text-xs text-loss bg-loss-bg border border-loss/20 rounded-lg px-3 py-2">Contrarian Bearish: low hedging = complacency warning.</div>}
              {/* Volume + OI by strike */}
              {spot > 0 && (() => {
                const lo = spot * 0.9, hi = spot * 1.1;
                const cv = calls.filter(c => c.strike_price >= lo && c.strike_price <= hi);
                const pv = puts.filter(c => c.strike_price >= lo && c.strike_price <= hi);
                const spotLine = { type: "line" as const, x0: spot, x1: spot, y0: 0, y1: 1, yref: "paper" as const, line: { color: t.spot, width: 1, dash: "dot" as const } };
                const zeroLine = { type: "line" as const, y0: 0, y1: 0, x0: lo, x1: hi, line: { color: t.muted, width: 1 } };
                return (<>
                  <Plot data={[
                    { x: cv.map(c => c.strike_price), y: cv.map(c => c.volume), type: "bar" as const, name: "Call Vol", marker: { color: t.gain } },
                    { x: pv.map(c => c.strike_price), y: pv.map(c => -c.volume), type: "bar" as const, name: "Put Vol", marker: { color: t.loss } },
                  ]} layout={{ height: 300, ...L, barmode: "overlay", yaxis: { title: "Volume", gridcolor: t.grid }, xaxis: { range: [lo, hi], gridcolor: t.grid }, shapes: [spotLine, zeroLine] }}
                    config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
                  <Plot data={[
                    { x: cv.map(c => c.strike_price), y: cv.map(c => c.open_interest), type: "bar" as const, name: "Call OI", marker: { color: t.gain } },
                    { x: pv.map(c => c.strike_price), y: pv.map(c => -c.open_interest), type: "bar" as const, name: "Put OI", marker: { color: t.loss } },
                  ]} layout={{ height: 300, ...L, barmode: "overlay", yaxis: { title: "Open Interest", gridcolor: t.grid }, xaxis: { range: [lo, hi], gridcolor: t.grid }, shapes: [spotLine, zeroLine] }}
                    config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
                </>);
              })()}
              {/* P/C by expiration */}
              {(() => {
                const byExp = new Map<string, { cv: number; pv: number }>();
                chain.forEach(c => { const e = byExp.get(c.expiration_date) ?? { cv: 0, pv: 0 }; if (c.contract_type === "call") e.cv += c.volume; else e.pv += c.volume; byExp.set(c.expiration_date, e); });
                const exps = [...byExp.entries()].sort(([a], [b]) => a.localeCompare(b));
                const ratios = exps.map(([, v]) => v.cv > 0 ? v.pv / v.cv : 0);
                return exps.length > 0 ? <Plot data={[{ x: exps.map(([e]) => e), y: ratios, type: "bar" as const, marker: { color: ratios.map(v => v > 1 ? t.loss : t.gain) } }]}
                  layout={{ height: 250, ...L, yaxis: { title: "P/C Ratio", gridcolor: t.grid }, shapes: [{ type: "line", y0: 1, y1: 1, x0: 0, x1: 1, xref: "paper", line: { color: t.muted, width: 1, dash: "dot" } }] }}
                  config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} /> : null;
              })()}
            </div>
          )}

          {/* Tab 2: GEX */}
          {activeTab === 2 && (
            <div className="card space-y-4">
              {gex ? (<>
                <div className="flex flex-wrap gap-6">
                  <Metric label="Net GEX" value={gex.totalGex.toLocaleString(undefined, { maximumFractionDigits: 0 })} delta={gex.totalGex > 0 ? "Long Gamma (Stable)" : "Short Gamma (Volatile)"} deltaType={gex.totalGex > 0 ? "gain" : "loss"} />
                  <Metric label="Max GEX (Pin)" value={`$${gex.maxGexStrike.toFixed(0)}`} />
                  <Metric label="Min GEX (Vol)" value={`$${gex.minGexStrike.toFixed(0)}`} />
                  <Metric label="Spot" value={`$${spot.toFixed(2)}`} />
                </div>
                <Plot data={[{ x: gex.net.map(n => n.strike), y: gex.net.map(n => n.net), type: "bar" as const, marker: { color: gex.net.map(n => n.net > 0 ? t.gain : t.loss) }, hovertemplate: "$%{x}: %{y:,.0f}<extra></extra>" }]}
                  layout={{ height: 450, ...L, xaxis: { title: "Strike", gridcolor: t.grid }, yaxis: { title: "GEX", gridcolor: t.grid },
                    shapes: [{ type: "line", x0: spot, x1: spot, y0: 0, y1: 1, yref: "paper", line: { color: t.spot, width: 1, dash: "dot" } }, { type: "line", y0: 0, y1: 0, x0: 0, x1: 1, xref: "paper", line: { color: t.muted, width: 1 } }] }}
                  config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
                <Plot data={[
                  { x: gex.net.map(n => n.strike), y: gex.net.map(n => n.call), type: "bar" as const, name: "Call GEX", marker: { color: t.gain }, opacity: 0.8 },
                  { x: gex.net.map(n => n.strike), y: gex.net.map(n => -n.put), type: "bar" as const, name: "Put GEX (inv)", marker: { color: t.loss }, opacity: 0.8 },
                ]} layout={{ height: 350, ...L, barmode: "overlay", yaxis: { title: "GEX", gridcolor: t.grid },
                  shapes: [{ type: "line", x0: spot, x1: spot, y0: 0, y1: 1, yref: "paper", line: { color: t.spot, width: 1, dash: "dot" } }, { type: "line", y0: 0, y1: 0, x0: 0, x1: 1, xref: "paper", line: { color: t.muted, width: 1 } }] }}
                  config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
              </>) : <p className="text-sm text-text-muted">{spot ? "No gamma data in ±15% range." : "Load data first."}</p>}
            </div>
          )}

          {/* Tab 3: Block Trades */}
          {activeTab === 3 && (
            <div className="card space-y-4">
              <div className="flex gap-4">
                <div><label className="metric-label">Min Volume</label><input type="number" value={blockMinVol} onChange={e => setBlockMinVol(Number(e.target.value))} step={100} className="w-28 px-2 py-1 border border-border rounded text-xs font-data bg-surface" /></div>
                <div><label className="metric-label">Min Notional ($)</label><input type="number" value={blockMinNotional} onChange={e => setBlockMinNotional(Number(e.target.value))} step={10000} className="w-32 px-2 py-1 border border-border rounded text-xs font-data bg-surface" /></div>
              </div>
              {blocks.length > 0 ? (<>
                <div className="flex flex-wrap gap-6">
                  <Metric label="Block Trades" value={String(blocks.length)} />
                  <Metric label="Block Calls" value={String(blocks.filter(b => b.contract_type === "call").length)} />
                  <Metric label="Block Puts" value={String(blocks.filter(b => b.contract_type === "put").length)} />
                  <Metric label="Total Notional" value={`$${blocks.reduce((s, b) => s + b.notional, 0).toLocaleString()}`} />
                </div>
                {(() => {
                  const cn = blocks.filter(b => b.contract_type === "call").reduce((s, b) => s + b.notional, 0);
                  const pn = blocks.filter(b => b.contract_type === "put").reduce((s, b) => s + b.notional, 0);
                  const total = cn + pn; if (total === 0) return null;
                  const cp = cn / total * 100;
                  const bias = cp > 60 ? "Bullish" : cp < 40 ? "Bearish" : "Neutral";
                  return (
                    <div className="text-center py-3 border border-border rounded-lg">
                      <div className="metric-label">Institutional Block Flow Bias</div>
                      <div className={`text-xl font-bold ${bias === "Bullish" ? "text-gain" : bias === "Bearish" ? "text-loss" : "text-warn"}`}>{bias}</div>
                      <div className="text-xs text-text-muted">Calls: ${cn.toLocaleString()} ({cp.toFixed(0)}%) | Puts: ${pn.toLocaleString()} ({(100 - cp).toFixed(0)}%)</div>
                    </div>
                  );
                })()}
                <div className="overflow-x-auto">
                  <table className="data-table text-xs">
                    <thead><tr><th>Strike</th><th>Type</th><th>Exp</th><th>Volume</th><th>OI</th><th>Price</th><th>Notional</th><th>IV</th><th>Delta</th></tr></thead>
                    <tbody>
                      {blocks.slice(0, 25).map((b, i) => (
                        <tr key={i}>
                          <td className="font-data">${b.strike_price.toFixed(0)}</td>
                          <td><span className={`badge ${b.contract_type === "call" ? "badge-gain" : "badge-loss"}`}>{b.contract_type}</span></td>
                          <td>{b.expiration_date}</td>
                          <td className="font-data">{b.volume.toLocaleString()}</td>
                          <td className="font-data">{b.open_interest.toLocaleString()}</td>
                          <td className="font-data">${b.last_price.toFixed(2)}</td>
                          <td className="font-data font-semibold">${b.notional.toLocaleString()}</td>
                          <td className="font-data">{(b.implied_volatility * 100).toFixed(1)}%</td>
                          <td className="font-data">{b.delta?.toFixed(3) ?? "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {spot > 0 && (() => {
                  const byStrike = new Map<number, { call: number; put: number }>();
                  blocks.forEach(b => { const e = byStrike.get(b.strike_price) ?? { call: 0, put: 0 }; if (b.contract_type === "call") e.call += b.notional; else e.put += b.notional; byStrike.set(b.strike_price, e); });
                  const strikes = [...byStrike.keys()].sort((a, b) => a - b);
                  return <Plot data={[
                    { x: strikes, y: strikes.map(s => byStrike.get(s)!.call), type: "bar" as const, name: "Call Blocks", marker: { color: t.gain }, opacity: 0.8 },
                    { x: strikes, y: strikes.map(s => byStrike.get(s)!.put), type: "bar" as const, name: "Put Blocks", marker: { color: t.loss }, opacity: 0.8 },
                  ]} layout={{ height: 350, ...L, barmode: "group", xaxis: { title: "Strike", range: [spot * 0.9, spot * 1.1], gridcolor: t.grid }, yaxis: { title: "Notional ($)", gridcolor: t.grid },
                    shapes: [{ type: "line", x0: spot, x1: spot, y0: 0, y1: 1, yref: "paper", line: { color: t.spot, width: 1, dash: "dot" } }] }}
                    config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />;
                })()}
              </>) : <p className="text-sm text-text-muted">No block trades found. Try lowering thresholds.</p>}
            </div>
          )}
        </>
      )}

      {chain.length === 0 && !load.isPending && load.isSuccess && <div className="card text-center py-8 text-text-muted">No options data returned.</div>}
      {load.isError && <div className="card border-loss/30 bg-loss-bg text-loss text-sm">Failed: {(load.error as Error).message}</div>}
    </div>
  );
}

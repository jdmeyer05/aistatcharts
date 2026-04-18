"use client";

import { useState, useMemo } from "react";
import { useTheme } from "next-themes";
import { getChartTheme, getBaseLayout } from "@/lib/chart-theme";
import { Metric } from "@/components/ui/metric";
import dynamic from "next/dynamic";

const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

const TABS = ["Portfolio Summary", "Risk Scenarios", "Delta Hedging", "Greeks by Expiration", "Greeks Over Time"];

function normCdf(x: number): number {
  const t = 1 / (1 + 0.2316419 * Math.abs(x));
  const p = 0.3989422804014327 * Math.exp(-x * x / 2) * (t * (0.3193815 + t * (-0.3565638 + t * (1.781478 + t * (-1.8212560 + t * 1.3302744)))));
  return x > 0 ? 1 - p : p;
}
function normPdf(x: number): number { return Math.exp(-0.5 * x * x) / Math.sqrt(2 * Math.PI); }

function bsGreeks(S: number, K: number, T: number, r: number, sigma: number, optType: string) {
  if (T <= 0.001 || sigma <= 0 || S <= 0 || K <= 0) return { delta: optType === "call" ? 1 : -1, gamma: 0, theta: 0, vega: 0, rho: 0 };
  const sqrtT = Math.sqrt(T);
  const d1 = (Math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrtT);
  const d2 = d1 - sigma * sqrtT;
  const nd1 = normPdf(d1);
  return {
    delta: optType === "call" ? normCdf(d1) : normCdf(d1) - 1,
    gamma: nd1 / (S * sigma * sqrtT),
    theta: (-(S * nd1 * sigma) / (2 * sqrtT) - r * K * Math.exp(-r * T) * (optType === "call" ? normCdf(d2) : -normCdf(-d2))) / 365,
    vega: S * nd1 * sqrtT / 100,
    rho: optType === "call" ? K * T * Math.exp(-r * T) * normCdf(d2) / 100 : -K * T * Math.exp(-r * T) * normCdf(-d2) / 100,
  };
}

interface Position {
  ticker: string; type: "Stock" | "Call" | "Put"; qty: number;
  strike: number; expiration: string; entryPrice: number; iv: number; spot: number;
}

export function PortfolioGreeksContent() {
  const { resolvedTheme } = useTheme();
  const t = getChartTheme(resolvedTheme === "dark");
  const L = getBaseLayout(t);
  const [activeTab, setActiveTab] = useState(0);
  const [positions, setPositions] = useState<Position[]>([]);
  const rfr = 0.045;

  // Form state
  const [fTicker, setFTicker] = useState("SPY");
  const [fType, setFType] = useState<"Stock" | "Call" | "Put">("Call");
  const [fQty, setFQty] = useState(1);
  const [fStrike, setFStrike] = useState(500);
  const [fExp, setFExp] = useState("2026-06-19");
  const [fEntry, setFEntry] = useState(10);
  const [fIv, setFIv] = useState(25);
  const [fSpot, setFSpot] = useState(500);

  const addPosition = () => {
    setPositions([...positions, { ticker: fTicker, type: fType, qty: fQty, strike: fStrike, expiration: fExp, entryPrice: fEntry, iv: fIv / 100, spot: fSpot }]);
  };
  const removePosition = (i: number) => setPositions(positions.filter((_, idx) => idx !== i));

  // Compute Greeks for each position
  const posGreeks = useMemo(() => {
    return positions.map(pos => {
      if (pos.type === "Stock") {
        return { ...pos, delta: 1, gamma: 0, theta: 0, vega: 0, rho: 0, dollarDelta: pos.qty * pos.spot, dollarGamma: 0, dollarTheta: 0, dollarVega: 0 };
      }
      const dte = Math.max((new Date(pos.expiration + "T16:00:00").getTime() - Date.now()) / 86400000, 0.001);
      const T = dte / 365;
      const g = bsGreeks(pos.spot, pos.strike, T, rfr, pos.iv, pos.type.toLowerCase());
      const mult = pos.qty * 100;
      return {
        ...pos, ...g, dte,
        dollarDelta: g.delta * mult * pos.spot / 100,
        dollarGamma: g.gamma * mult * pos.spot * pos.spot / 100,
        dollarTheta: g.theta * mult,
        dollarVega: g.vega * mult,
      };
    });
  }, [positions, rfr]);

  const totals = useMemo(() => ({
    delta: posGreeks.reduce((s, p) => s + p.dollarDelta, 0),
    gamma: posGreeks.reduce((s, p) => s + p.dollarGamma, 0),
    theta: posGreeks.reduce((s, p) => s + p.dollarTheta, 0),
    vega: posGreeks.reduce((s, p) => s + p.dollarVega, 0),
  }), [posGreeks]);

  return (
    <div className="space-y-5">
      {/* Position Entry */}
      <div className="card space-y-3">
        <div className="text-sm font-bold">Add Position</div>
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          <div><label className="metric-label">Ticker</label><input type="text" value={fTicker} onChange={e => setFTicker(e.target.value.toUpperCase())} className="w-full px-2 py-1.5 border border-border rounded text-sm font-data bg-surface" /></div>
          <div><label className="metric-label">Type</label>
            <div className="flex gap-1 mt-0.5">{(["Stock", "Call", "Put"] as const).map(v => (
              <button key={v} onClick={() => setFType(v)} className={`flex-1 px-2 py-1.5 text-xs font-semibold rounded ${fType === v ? "bg-accent text-white" : "bg-surface-alt text-text-muted"}`}>{v}</button>
            ))}</div>
          </div>
          <div><label className="metric-label">Qty (neg=short)</label><input type="number" value={fQty} onChange={e => setFQty(Number(e.target.value))} className="w-full px-2 py-1.5 border border-border rounded text-sm font-data bg-surface" /></div>
          <div><label className="metric-label">Spot ($)</label><input type="number" value={fSpot} onChange={e => setFSpot(Number(e.target.value))} className="w-full px-2 py-1.5 border border-border rounded text-sm font-data bg-surface" /></div>
        </div>
        {fType !== "Stock" && (
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            <div><label className="metric-label">Strike ($)</label><input type="number" value={fStrike} onChange={e => setFStrike(Number(e.target.value))} className="w-full px-2 py-1.5 border border-border rounded text-sm font-data bg-surface" /></div>
            <div><label className="metric-label">Expiration</label><input type="text" value={fExp} onChange={e => setFExp(e.target.value)} placeholder="YYYY-MM-DD" className="w-full px-2 py-1.5 border border-border rounded text-sm font-data bg-surface" /></div>
            <div><label className="metric-label">Entry Price ($)</label><input type="number" value={fEntry} onChange={e => setFEntry(Number(e.target.value))} step={0.5} className="w-full px-2 py-1.5 border border-border rounded text-sm font-data bg-surface" /></div>
            <div><label className="metric-label">IV (%)</label><input type="number" value={fIv} onChange={e => setFIv(Number(e.target.value))} step={0.5} className="w-full px-2 py-1.5 border border-border rounded text-sm font-data bg-surface" /></div>
          </div>
        )}
        <button onClick={addPosition} className="px-4 py-1.5 bg-accent text-white text-xs font-semibold rounded hover:bg-accent-hover">Add Position</button>
      </div>

      {positions.length > 0 && (
        <>
          {/* Aggregate Greeks */}
          <div className="card card-compact">
            <div className="flex flex-wrap gap-6">
              <Metric label="$ Delta" value={`$${totals.delta.toLocaleString(undefined, { maximumFractionDigits: 0 })}`} deltaType={totals.delta > 0 ? "gain" : "loss"} />
              <Metric label="$ Gamma" value={`$${totals.gamma.toLocaleString(undefined, { maximumFractionDigits: 0 })}`} />
              <Metric label="$ Theta/day" value={`$${totals.theta.toFixed(0)}`} deltaType={totals.theta > 0 ? "gain" : "loss"} />
              <Metric label="$ Vega" value={`$${totals.vega.toFixed(0)}`} />
              <Metric label="Positions" value={String(positions.length)} />
            </div>
          </div>

          {/* Positions table */}
          <div className="card">
            <div className="overflow-x-auto">
              <table className="data-table text-xs">
                <thead><tr><th>Ticker</th><th>Type</th><th>Qty</th><th>Strike</th><th>Exp</th><th>IV</th><th>Delta</th><th>Gamma</th><th>Theta</th><th>Vega</th><th>$ Delta</th><th></th></tr></thead>
                <tbody>
                  {posGreeks.map((p, i) => (
                    <tr key={i}>
                      <td className="font-semibold">{p.ticker}</td>
                      <td><span className={`badge ${p.type === "Call" ? "badge-gain" : p.type === "Put" ? "badge-loss" : "badge-info"}`}>{p.type}</span></td>
                      <td className="font-data">{p.qty}</td>
                      <td className="font-data">{p.type !== "Stock" ? `$${p.strike}` : "—"}</td>
                      <td>{p.type !== "Stock" ? p.expiration : "—"}</td>
                      <td className="font-data">{p.type !== "Stock" ? `${(p.iv * 100).toFixed(1)}%` : "—"}</td>
                      <td className="font-data">{p.delta.toFixed(3)}</td>
                      <td className="font-data">{p.gamma.toFixed(4)}</td>
                      <td className="font-data">{p.theta.toFixed(2)}</td>
                      <td className="font-data">{p.vega.toFixed(2)}</td>
                      <td className="font-data">${p.dollarDelta.toFixed(0)}</td>
                      <td><button onClick={() => removePosition(i)} className="text-loss text-xs">x</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="flex gap-1 border-b border-border pb-1">
            {TABS.map((tab, i) => (
              <button key={tab} onClick={() => setActiveTab(i)}
                className={`px-3 py-1.5 text-xs font-semibold rounded-t-md transition-colors whitespace-nowrap ${
                  activeTab === i ? "bg-accent text-white" : "text-text-muted hover:text-text hover:bg-surface-alt"}`}>
                {tab}
              </button>
            ))}
          </div>

          {/* Tab 0: Summary — Greek bars */}
          {activeTab === 0 && (
            <div className="card space-y-4">
              {/* Greeks contribution by position */}
              <Plot data={[
                { x: posGreeks.map(p => `${p.ticker} ${p.type} ${p.type !== "Stock" ? "$" + p.strike : ""}`),
                  y: posGreeks.map(p => p.dollarDelta), type: "bar" as const, name: "$ Delta",
                  marker: { color: posGreeks.map(p => p.dollarDelta > 0 ? t.gain : t.loss) } },
              ]} layout={{ height: 300, ...L, yaxis: { title: "Dollar Delta", gridcolor: t.grid }, xaxis: { gridcolor: t.grid } }}
                config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />

              {/* Theta/Vega */}
              <Plot data={[
                { x: posGreeks.map(p => `${p.ticker} ${p.type}`), y: posGreeks.map(p => p.dollarTheta), type: "bar" as const, name: "Theta/day", marker: { color: t.loss } },
                { x: posGreeks.map(p => `${p.ticker} ${p.type}`), y: posGreeks.map(p => p.dollarVega), type: "bar" as const, name: "Vega", marker: { color: t.accent } },
              ]} layout={{ height: 250, ...L, barmode: "group", yaxis: { title: "Dollar Greeks", gridcolor: t.grid }, xaxis: { gridcolor: t.grid } }}
                config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
            </div>
          )}

          {/* Tab 1: Risk Scenarios — P&L heatmap for spot × vol moves */}
          {activeTab === 1 && (
            <div className="card">
              {(() => {
                const spotMoves = [-10, -5, -3, -1, 0, 1, 3, 5, 10]; // %
                const volMoves = [-5, -3, -1, 0, 1, 3, 5]; // absolute vol points
                const baseSpot = posGreeks[0]?.spot ?? fSpot;

                const z = volMoves.map(dv => spotMoves.map(ds => {
                  let pnl = 0;
                  for (const p of posGreeks) {
                    const newSpot = p.spot * (1 + ds / 100);
                    const newIv = Math.max(0.01, p.iv + dv / 100);
                    if (p.type === "Stock") {
                      pnl += p.qty * (newSpot - p.spot);
                    } else {
                      const dte = Math.max(("dte" in p ? (p as { dte: number }).dte : 30) / 365, 0.001);
                      const oldPrice = Math.max(0, bsGreeks(p.spot, p.strike, dte, rfr, p.iv, p.type.toLowerCase()).delta) > -2
                        ? p.entryPrice : 0; // simplified
                      const newG = bsGreeks(newSpot, p.strike, dte, rfr, newIv, p.type.toLowerCase());
                      // Approximate P&L from Greeks
                      const spotChg = newSpot - p.spot;
                      const volChg = newIv - p.iv;
                      pnl += p.qty * 100 * (p.delta * spotChg + 0.5 * p.gamma * spotChg * spotChg + p.vega * volChg * 100);
                    }
                  }
                  return Math.round(pnl);
                }));

                return (
                  <Plot data={[{
                    type: "heatmap" as const,
                    x: spotMoves.map(s => `${s > 0 ? "+" : ""}${s}%`),
                    y: volMoves.map(v => `${v > 0 ? "+" : ""}${v}vol`),
                    z,
                    colorscale: [[0, t.loss], [0.5, t.grid], [1, t.gain]], zmid: 0,
                    text: z.map(row => row.map(v => `$${v.toLocaleString()}`)),
                    texttemplate: "%{text}",
                    textfont: { size: 10 },
                    hovertemplate: "Spot: %{x}<br>Vol: %{y}<br>P&L: %{text}<extra></extra>",
                    colorbar: { title: { text: "P&L ($)", font: { size: 9 } }, thickness: 12 },
                  }]}
                    layout={{ height: 400, ...L, margin: { l: 60, r: 20, t: 20, b: 50 },
                      xaxis: { title: "Spot Move", gridcolor: t.grid },
                      yaxis: { title: "Vol Move", gridcolor: t.grid } }}
                    config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
                );
              })()}
            </div>
          )}

          {/* Tab 2: Delta Hedging */}
          {activeTab === 2 && (
            <div className="card space-y-4">
              <div className="text-sm font-bold">Delta Hedging Calculator</div>
              {(() => {
                const netDelta = posGreeks.reduce((s, p) => s + p.delta * p.qty * (p.type === "Stock" ? 1 : 100), 0);
                const mainSpot = posGreeks[0]?.spot ?? fSpot;
                const sharesToHedge = -Math.round(netDelta);
                const hedgeCost = Math.abs(sharesToHedge * mainSpot);
                return (<>
                  <div className="flex flex-wrap gap-6">
                    <Metric label="Net Delta (contracts)" value={netDelta.toFixed(1)} />
                    <Metric label="Shares to Hedge" value={`${sharesToHedge > 0 ? "Buy" : "Sell"} ${Math.abs(sharesToHedge)} shares`} />
                    <Metric label="Hedge Cost" value={`$${hedgeCost.toLocaleString()}`} />
                    <Metric label="Post-Hedge Delta" value="~0" />
                  </div>
                  <div className="text-xs text-text-muted border-t border-border pt-3 mt-2">
                    <strong>Dynamic hedging schedule:</strong> Re-hedge when delta drifts beyond ±{Math.max(10, Math.abs(Math.round(netDelta * 0.1)))} shares.
                    With current gamma of ${totals.gamma.toFixed(0)}, a 1% spot move shifts delta by ~{Math.abs(totals.gamma * 0.01).toFixed(0)} shares.
                  </div>
                </>);
              })()}
            </div>
          )}

          {/* ═══ Tab 3: Greeks by Expiration ═══ */}
          {activeTab === 3 && (() => {
            const expMap = new Map<string, { delta: number; gamma: number; theta: number; vega: number }>();
            for (const g of posGreeks) {
              if (g.type === "Stock") continue;
              const exp = g.expiration;
              const cur = expMap.get(exp) || { delta: 0, gamma: 0, theta: 0, vega: 0 };
              cur.delta += g.delta * g.qty * 100;
              cur.gamma += g.gamma * g.qty * 100;
              cur.theta += g.theta * g.qty * 100;
              cur.vega += g.vega * g.qty * 100;
              expMap.set(exp, cur);
            }
            const expEntries = [...expMap.entries()].sort(([a], [b]) => a.localeCompare(b));
            const nearestDTE = expEntries.length > 0 ? Math.max(1, Math.round((new Date(expEntries[0][0] + "T16:00:00").getTime() - Date.now()) / 86400000)) : 999;
            return (
              <div className="card space-y-4">
                {nearestDTE <= 7 && <div className="text-xs text-loss bg-loss/10 border border-loss/20 rounded px-3 py-2">Next expiration in {nearestDTE}d — gamma/theta risk accelerating.</div>}
                {nearestDTE <= 21 && nearestDTE > 7 && <div className="text-xs text-warn bg-warn-bg border border-warn/20 rounded px-3 py-2">Next expiration in {nearestDTE}d — monitor theta decay.</div>}
                <Plot data={[
                  { x: expEntries.map(([e]) => e), y: expEntries.map(([, g]) => g.delta), type: "bar" as const, name: "Δ Delta $", marker: { color: t.accent }, xaxis: "x", yaxis: "y" },
                  { x: expEntries.map(([e]) => e), y: expEntries.map(([, g]) => g.gamma), type: "bar" as const, name: "Γ Gamma $", marker: { color: t.gain }, xaxis: "x2", yaxis: "y2" },
                  { x: expEntries.map(([e]) => e), y: expEntries.map(([, g]) => g.theta), type: "bar" as const, name: "Θ Theta $/day", marker: { color: t.loss }, xaxis: "x3", yaxis: "y3" },
                  { x: expEntries.map(([e]) => e), y: expEntries.map(([, g]) => g.vega), type: "bar" as const, name: "ν Vega $/1%", marker: { color: t.spot }, xaxis: "x4", yaxis: "y4" },
                ]} layout={{ height: 500, ...L, margin: { l: 50, r: 20, t: 20, b: 40 }, showlegend: false,
                  grid: { rows: 2, columns: 2, pattern: "independent" as const },
                  xaxis: { gridcolor: t.grid, domain: [0, 0.48] }, yaxis: { title: "Delta $", gridcolor: t.grid, domain: [0.55, 1] },
                  xaxis2: { gridcolor: t.grid, domain: [0.52, 1] }, yaxis2: { title: "Gamma $", gridcolor: t.grid, domain: [0.55, 1], anchor: "x2" },
                  xaxis3: { gridcolor: t.grid, domain: [0, 0.48] }, yaxis3: { title: "Theta $/day", gridcolor: t.grid, domain: [0, 0.45], anchor: "x3" },
                  xaxis4: { gridcolor: t.grid, domain: [0.52, 1] }, yaxis4: { title: "Vega $/1%", gridcolor: t.grid, domain: [0, 0.45], anchor: "x4" },
                  annotations: [
                    { text: "Delta by Exp", x: 0.24, y: 1.02, xref: "paper", yref: "paper", showarrow: false, font: { size: 10, color: t.text } },
                    { text: "Gamma by Exp", x: 0.76, y: 1.02, xref: "paper", yref: "paper", showarrow: false, font: { size: 10, color: t.text } },
                    { text: "Theta by Exp", x: 0.24, y: 0.48, xref: "paper", yref: "paper", showarrow: false, font: { size: 10, color: t.text } },
                    { text: "Vega by Exp", x: 0.76, y: 0.48, xref: "paper", yref: "paper", showarrow: false, font: { size: 10, color: t.text } },
                  ],
                }} config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
              </div>
            );
          })()}

          {/* ═══ Tab 4: Greeks Over Time ═══ */}
          {activeTab === 4 && (() => {
            // Simulate Greeks evolution over next 30 days (constant spot & IV)
            const maxDays = Math.min(30, ...(posGreeks as any[]).filter(g => g.type !== "Stock").map(g => Math.max(1, Math.round((new Date(g.expiration + "T16:00:00").getTime() - Date.now()) / 86400000))));
            const days = Array.from({ length: maxDays }, (_, i) => i);
            const evolution = days.map(d => {
              let delta = 0, gamma = 0, theta = 0, vega = 0;
              for (const g of posGreeks) {
                if (g.type === "Stock") { delta += g.qty * g.spot; continue; }
                const dte = Math.max(0.001, (new Date(g.expiration + "T16:00:00").getTime() - Date.now()) / 86400000 - d);
                if (dte <= 0) continue;
                const T = dte / 365;
                const sqrtT = Math.sqrt(T);
                const d1 = (Math.log(g.spot / g.strike) + (0.045 + g.iv * g.iv / 2) * T) / (g.iv * sqrtT);
                const nd1 = Math.exp(-d1 * d1 / 2) / Math.sqrt(2 * Math.PI);
                const Nd1 = normCdf(d1);
                const dlt = g.type.toLowerCase() === "call" ? Nd1 : Nd1 - 1;
                const gam = nd1 / (g.spot * g.iv * sqrtT);
                const tht = -(g.spot * nd1 * g.iv) / (2 * sqrtT) / 365;
                const veg = g.spot * nd1 * sqrtT / 100;
                const sign = g.qty > 0 ? 1 : -1;
                delta += dlt * Math.abs(g.qty) * 100 * sign;
                gamma += gam * Math.abs(g.qty) * 100 * sign;
                theta += tht * Math.abs(g.qty) * 100 * sign;
                vega += veg * Math.abs(g.qty) * 100 * sign;
              }
              return { d, delta, gamma, theta, vega };
            });
            return (
              <div className="card">
                <Plot data={[
                  { x: days, y: evolution.map(e => e.delta), type: "scatter" as const, mode: "lines" as const, fill: "tozeroy", fillcolor: t.accent + "10", line: { color: t.accent, width: 2 }, name: "Delta $", xaxis: "x", yaxis: "y" },
                  { x: days, y: evolution.map(e => e.gamma), type: "scatter" as const, mode: "lines" as const, fill: "tozeroy", fillcolor: t.gain + "10", line: { color: t.gain, width: 2 }, name: "Gamma $", xaxis: "x2", yaxis: "y2" },
                  { x: days, y: evolution.map(e => e.theta), type: "scatter" as const, mode: "lines" as const, fill: "tozeroy", fillcolor: t.loss + "10", line: { color: t.loss, width: 2 }, name: "Theta $/day", xaxis: "x3", yaxis: "y3" },
                  { x: days, y: evolution.map(e => e.vega), type: "scatter" as const, mode: "lines" as const, fill: "tozeroy", fillcolor: t.spot + "10", line: { color: t.spot, width: 2 }, name: "Vega $/1%", xaxis: "x4", yaxis: "y4" },
                ]} layout={{ height: 500, ...L, margin: { l: 50, r: 20, t: 20, b: 40 }, showlegend: false,
                  grid: { rows: 2, columns: 2, pattern: "independent" as const },
                  xaxis: { title: "Days", gridcolor: t.grid, domain: [0, 0.48] }, yaxis: { title: "Delta $", gridcolor: t.grid, domain: [0.55, 1] },
                  xaxis2: { title: "Days", gridcolor: t.grid, domain: [0.52, 1] }, yaxis2: { title: "Gamma $", gridcolor: t.grid, domain: [0.55, 1], anchor: "x2" },
                  xaxis3: { title: "Days", gridcolor: t.grid, domain: [0, 0.48] }, yaxis3: { title: "Theta $/day", gridcolor: t.grid, domain: [0, 0.45], anchor: "x3" },
                  xaxis4: { title: "Days", gridcolor: t.grid, domain: [0.52, 1] }, yaxis4: { title: "Vega $/1%", gridcolor: t.grid, domain: [0, 0.45], anchor: "x4" },
                }} config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
              </div>
            );
          })()}
        </>
      )}

      {positions.length === 0 && (
        <div className="card text-center py-8 text-text-muted">
          Add positions above to see aggregate Greeks, risk scenarios, and delta hedging.
        </div>
      )}
    </div>
  );
}

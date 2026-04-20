"use client";

import { useState, useMemo } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTheme } from "next-themes";
import { fetchOptionsChain, fetchSnapshot, fetchPriceHistory } from "@/lib/api";
import { getChartTheme, getBaseLayout } from "@/lib/chart-theme";
import { Metric } from "@/components/ui/metric";
import { Plot } from "@/components/plot";


const TABS = ["BS Pricing & Greeks", "Strategy P&L Modeler", "Earnings Move Analyzer", "Strategy Optimizer"];

interface ChainRow { strike_price: number; contract_type: string; expiration_date: string; implied_volatility: number; delta: number; gamma: number; theta: number; vega: number; open_interest: number; volume: number; bid: number; ask: number; last_price: number }

function normCdf(x: number): number {
  const t = 1 / (1 + 0.2316419 * Math.abs(x));
  const d = 0.3989422804014327;
  const p = d * Math.exp(-x * x / 2) * (t * (0.3193815 + t * (-0.3565638 + t * (1.781478 + t * (-1.8212560 + t * 1.3302744)))));
  return x > 0 ? 1 - p : p;
}
function normPdf(x: number): number { return Math.exp(-0.5 * x * x) / Math.sqrt(2 * Math.PI); }

function bsPrice(S: number, K: number, T: number, r: number, sigma: number, optType: string): number {
  if (T <= 0) return optType === "call" ? Math.max(S - K, 0) : Math.max(K - S, 0);
  const d1 = (Math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * Math.sqrt(T));
  const d2 = d1 - sigma * Math.sqrt(T);
  return optType === "call" ? S * normCdf(d1) - K * Math.exp(-r * T) * normCdf(d2) : K * Math.exp(-r * T) * normCdf(-d2) - S * normCdf(-d1);
}

function bsGreeks(S: number, K: number, T: number, r: number, sigma: number, optType: string) {
  if (T <= 0 || sigma <= 0) return { delta: 0, gamma: 0, theta: 0, vega: 0, rho: 0 };
  const sqrtT = Math.sqrt(T);
  const d1 = (Math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrtT);
  const d2 = d1 - sigma * sqrtT;
  const nd1 = normPdf(d1);
  const delta = optType === "call" ? normCdf(d1) : normCdf(d1) - 1;
  const gamma = nd1 / (S * sigma * sqrtT);
  const theta = (-(S * nd1 * sigma) / (2 * sqrtT) - r * K * Math.exp(-r * T) * (optType === "call" ? normCdf(d2) : -normCdf(-d2))) / 365;
  const vega = S * nd1 * sqrtT / 100;
  const rho = optType === "call" ? K * T * Math.exp(-r * T) * normCdf(d2) / 100 : -K * T * Math.exp(-r * T) * normCdf(-d2) / 100;
  return { delta, gamma, theta, vega, rho };
}

interface Leg { type: string; strike: number; qty: number; optType: string; premium: number }

export function OptionsLabContent() {
  const { resolvedTheme } = useTheme();
  const t = getChartTheme(resolvedTheme === "dark");
  const L = getBaseLayout(t);
  const [activeTab, setActiveTab] = useState(0);

  // Earnings & Strategy Optimizer state
  const [labTicker, setLabTicker] = useState("SPY");
  const [labChain, setLabChain] = useState<ChainRow[]>([]);
  const [labSpot, setLabSpot] = useState(0);
  const [labHist, setLabHist] = useState<{ Close: number }[]>([]);

  const loadChain = useMutation({
    mutationFn: async (tk: string) => {
      const [ch, snap, hist] = await Promise.all([fetchOptionsChain(tk), fetchSnapshot([tk]), fetchPriceHistory(tk, 252)]);
      return { chain: ch.data as unknown as ChainRow[], spot: snap[tk]?.price ?? 0, hist: hist.data };
    },
    onSuccess: (d) => { setLabChain(d.chain); setLabSpot(d.spot); setLabHist(d.hist); },
  });

  // BS Calculator state
  const [spot, setSpot] = useState(500);
  const [strike, setStrike] = useState(500);
  const [dte, setDte] = useState(30);
  const [vol, setVol] = useState(25);
  const [rate, setRate] = useState(4.5);
  const [optType, setOptType] = useState<"call" | "put">("call");

  // Strategy state
  const [stratSpot, setStratSpot] = useState(500);
  const [legs, setLegs] = useState<Leg[]>([
    { type: "long", strike: 500, qty: 1, optType: "call", premium: 10 },
  ]);

  const T = dte / 365;
  const sigma = vol / 100;
  const r = rate / 100;

  const price = useMemo(() => bsPrice(spot, strike, T, r, sigma, optType), [spot, strike, T, r, sigma, optType]);
  const greeks = useMemo(() => bsGreeks(spot, strike, T, r, sigma, optType), [spot, strike, T, r, sigma, optType]);

  // P&L curve for strategy
  const stratPnL = useMemo(() => {
    const lo = stratSpot * 0.8, hi = stratSpot * 1.2;
    const prices = Array.from({ length: 200 }, (_, i) => lo + i * (hi - lo) / 199);
    const pnl = prices.map(p => {
      let total = 0;
      for (const leg of legs) {
        const intrinsic = leg.optType === "call" ? Math.max(p - leg.strike, 0) : Math.max(leg.strike - p, 0);
        const legPnl = (intrinsic - leg.premium) * 100 * leg.qty;
        total += leg.type === "long" ? legPnl : -legPnl;
      }
      return total;
    });
    return { prices, pnl };
  }, [stratSpot, legs]);

  const addLeg = () => setLegs([...legs, { type: "long", strike: stratSpot, qty: 1, optType: "call", premium: 5 }]);
  const removeLeg = (i: number) => setLegs(legs.filter((_, idx) => idx !== i));
  const updateLeg = (i: number, field: keyof Leg, val: string | number) => {
    const updated = [...legs];
    (updated[i] as unknown as Record<string, unknown>)[field] = val;
    setLegs(updated);
  };

  return (
    <div className="space-y-5">
      <div className="flex gap-1 border-b border-border pb-1">
        {TABS.map((tab, i) => (
          <button key={tab} onClick={() => setActiveTab(i)}
            className={`px-3 py-1.5 text-xs font-semibold rounded-t-md transition-colors whitespace-nowrap ${
              activeTab === i ? "bg-accent text-white" : "text-text-muted hover:text-text hover:bg-surface-alt"}`}>
            {tab}
          </button>
        ))}
      </div>

      {/* Tab 0: BS Pricing & Greeks */}
      {activeTab === 0 && (
        <div className="space-y-4">
          <div className="card card-compact">
            <div className="grid grid-cols-3 lg:grid-cols-6 gap-3">
              <div><label className="metric-label">Spot ($)</label><input type="number" value={spot} onChange={e => setSpot(Number(e.target.value))} className="w-full px-2 py-1.5 border border-border rounded text-sm font-data bg-surface" /></div>
              <div><label className="metric-label">Strike ($)</label><input type="number" value={strike} onChange={e => setStrike(Number(e.target.value))} className="w-full px-2 py-1.5 border border-border rounded text-sm font-data bg-surface" /></div>
              <div><label className="metric-label">DTE</label><input type="number" value={dte} onChange={e => setDte(Number(e.target.value))} className="w-full px-2 py-1.5 border border-border rounded text-sm font-data bg-surface" /></div>
              <div><label className="metric-label">Vol (%)</label><input type="number" value={vol} onChange={e => setVol(Number(e.target.value))} step={0.5} className="w-full px-2 py-1.5 border border-border rounded text-sm font-data bg-surface" /></div>
              <div><label className="metric-label">Rate (%)</label><input type="number" value={rate} onChange={e => setRate(Number(e.target.value))} step={0.25} className="w-full px-2 py-1.5 border border-border rounded text-sm font-data bg-surface" /></div>
              <div><label className="metric-label">Type</label>
                <div className="flex gap-1 mt-1">
                  {(["call", "put"] as const).map(ot => (
                    <button key={ot} onClick={() => setOptType(ot)} className={`flex-1 px-2 py-1.5 text-xs font-semibold rounded ${optType === ot ? "bg-accent text-white" : "bg-surface-alt text-text-muted"}`}>{ot}</button>
                  ))}
                </div>
              </div>
            </div>
          </div>

          <div className="card card-compact">
            <div className="flex flex-wrap gap-6">
              <Metric label="Option Price" value={`$${price.toFixed(2)}`} />
              <Metric label="Delta" value={greeks.delta.toFixed(4)} />
              <Metric label="Gamma" value={greeks.gamma.toFixed(4)} />
              <Metric label="Theta" value={`$${greeks.theta.toFixed(2)}/day`} />
              <Metric label="Vega" value={`$${greeks.vega.toFixed(2)}/vol%`} />
              <Metric label="Rho" value={`$${greeks.rho.toFixed(2)}/rate%`} />
            </div>
          </div>

          {/* P&L at expiry + Greeks sensitivity */}
          <div className="card">
            {(() => {
              const lo = spot * 0.8, hi = spot * 1.2;
              const px = Array.from({ length: 200 }, (_, i) => lo + i * (hi - lo) / 199);
              const pnl = px.map(p => {
                const intrinsic = optType === "call" ? Math.max(p - strike, 0) : Math.max(strike - p, 0);
                return (intrinsic - price) * 100;
              });
              const pnlNow = px.map(p => (bsPrice(p, strike, T, r, sigma, optType) - price) * 100);
              return (
                <Plot data={[
                  { x: px, y: pnl, type: "scatter" as const, mode: "lines" as const, name: "P&L at Expiry", line: { color: t.accent, width: 2 } },
                  { x: px, y: pnlNow, type: "scatter" as const, mode: "lines" as const, name: "P&L Now", line: { color: t.muted, width: 1.5, dash: "dash" } },
                ]} layout={{ height: 350, ...L, yaxis: { title: "P&L ($)", gridcolor: t.grid }, xaxis: { title: "Spot Price", gridcolor: t.grid }, hovermode: "x unified",
                  shapes: [
                    { type: "line", y0: 0, y1: 0, x0: lo, x1: hi, line: { color: t.muted, width: 1 } },
                    { type: "line", x0: spot, x1: spot, y0: 0, y1: 1, yref: "paper", line: { color: t.spot, width: 1, dash: "dot" } },
                    { type: "line", x0: strike, x1: strike, y0: 0, y1: 1, yref: "paper", line: { color: t.loss, width: 1, dash: "dot" } },
                  ] }}
                  config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
              );
            })()}
          </div>

          {/* Delta vs Spot */}
          <div className="card">
            {(() => {
              const lo = spot * 0.85, hi = spot * 1.15;
              const px = Array.from({ length: 100 }, (_, i) => lo + i * (hi - lo) / 99);
              const deltas = px.map(p => bsGreeks(p, strike, T, r, sigma, optType).delta);
              const gammas = px.map(p => bsGreeks(p, strike, T, r, sigma, optType).gamma);
              return (
                <Plot data={[
                  { x: px, y: deltas, type: "scatter" as const, mode: "lines" as const, name: "Delta", line: { color: t.accent, width: 2 }, yaxis: "y" },
                  { x: px, y: gammas, type: "scatter" as const, mode: "lines" as const, name: "Gamma", line: { color: t.hv20, width: 2 }, yaxis: "y2" },
                ]} layout={{ height: 300, ...L, yaxis: { title: "Delta", gridcolor: t.grid }, yaxis2: { title: "Gamma", overlaying: "y", side: "right", showgrid: false },
                  xaxis: { title: "Spot Price", gridcolor: t.grid }, hovermode: "x unified",
                  shapes: [{ type: "line", x0: spot, x1: spot, y0: 0, y1: 1, yref: "paper", line: { color: t.spot, width: 1, dash: "dot" } }] }}
                  config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
              );
            })()}
          </div>
        </div>
      )}

      {/* Tab 1: Strategy P&L Modeler */}
      {activeTab === 1 && (
        <div className="space-y-4">
          <div className="card card-compact">
            <div className="flex items-center gap-3 mb-3">
              <label className="metric-label">Underlying Spot ($)</label>
              <input type="number" value={stratSpot} onChange={e => setStratSpot(Number(e.target.value))} className="w-28 px-2 py-1.5 border border-border rounded text-sm font-data bg-surface" />
              <button onClick={addLeg} className="px-3 py-1.5 text-xs font-semibold bg-accent text-white rounded hover:bg-accent-hover">+ Add Leg</button>
            </div>
            <div className="overflow-x-auto">
              <table className="data-table text-xs">
                <thead><tr><th>Long/Short</th><th>Call/Put</th><th>Strike</th><th>Qty</th><th>Premium</th><th></th></tr></thead>
                <tbody>
                  {legs.map((leg, i) => (
                    <tr key={i}>
                      <td>
                        <select value={leg.type} onChange={e => updateLeg(i, "type", e.target.value)} className="px-1 py-0.5 border border-border rounded text-xs bg-surface">
                          <option value="long">Long</option><option value="short">Short</option>
                        </select>
                      </td>
                      <td>
                        <select value={leg.optType} onChange={e => updateLeg(i, "optType", e.target.value)} className="px-1 py-0.5 border border-border rounded text-xs bg-surface">
                          <option value="call">Call</option><option value="put">Put</option>
                        </select>
                      </td>
                      <td><input type="number" value={leg.strike} onChange={e => updateLeg(i, "strike", Number(e.target.value))} className="w-20 px-1 py-0.5 border border-border rounded text-xs font-data bg-surface" /></td>
                      <td><input type="number" value={leg.qty} onChange={e => updateLeg(i, "qty", Number(e.target.value))} min={1} className="w-14 px-1 py-0.5 border border-border rounded text-xs font-data bg-surface" /></td>
                      <td><input type="number" value={leg.premium} onChange={e => updateLeg(i, "premium", Number(e.target.value))} step={0.5} className="w-20 px-1 py-0.5 border border-border rounded text-xs font-data bg-surface" /></td>
                      <td>{legs.length > 1 && <button onClick={() => removeLeg(i)} className="text-loss text-xs">Remove</button>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* Net metrics */}
          {(() => {
            const netDebit = legs.reduce((s, l) => s + (l.type === "long" ? l.premium : -l.premium) * l.qty * 100, 0);
            const maxProfit = Math.max(...stratPnL.pnl);
            const maxLoss = Math.min(...stratPnL.pnl);
            const breakevens = stratPnL.prices.filter((_, i) => i > 0 && ((stratPnL.pnl[i] >= 0 && stratPnL.pnl[i - 1] < 0) || (stratPnL.pnl[i] <= 0 && stratPnL.pnl[i - 1] > 0)));
            return (
              <div className="card card-compact">
                <div className="flex flex-wrap gap-6">
                  <Metric label="Net Cost" value={`$${Math.abs(netDebit).toFixed(0)} ${netDebit > 0 ? "Debit" : "Credit"}`} />
                  <Metric label="Max Profit" value={maxProfit > 1e6 ? "Unlimited" : `$${maxProfit.toFixed(0)}`} />
                  <Metric label="Max Loss" value={maxLoss < -1e6 ? "Unlimited" : `$${maxLoss.toFixed(0)}`} />
                  <Metric label="Breakevens" value={breakevens.map(b => `$${b.toFixed(0)}`).join(", ") || "None"} />
                </div>
              </div>
            );
          })()}

          {/* P&L chart */}
          <div className="card">
            <Plot data={[
              { x: stratPnL.prices, y: stratPnL.pnl, type: "scatter" as const, mode: "lines" as const, name: "P&L at Expiry",
                line: { color: t.accent, width: 2 }, fill: "tozeroy" as const, fillcolor: t.accent + "10" },
              { x: stratPnL.prices, y: stratPnL.pnl.map(v => v < 0 ? v : 0), type: "scatter" as const, mode: "lines" as const,
                fill: "tozeroy" as const, fillcolor: t.loss + "15", line: { color: "transparent", width: 0 }, showlegend: false, hoverinfo: "skip" as const },
            ]} layout={{ height: 400, ...L, yaxis: { title: "P&L ($)", gridcolor: t.grid }, xaxis: { title: "Spot at Expiry", gridcolor: t.grid }, hovermode: "x unified",
              shapes: [
                { type: "line", y0: 0, y1: 0, x0: stratPnL.prices[0], x1: stratPnL.prices[stratPnL.prices.length - 1], line: { color: t.muted, width: 1 } },
                { type: "line", x0: stratSpot, x1: stratSpot, y0: 0, y1: 1, yref: "paper", line: { color: t.spot, width: 1, dash: "dot" } },
                ...legs.map(l => ({ type: "line" as const, x0: l.strike, x1: l.strike, y0: 0, y1: 1, yref: "paper" as const, line: { color: t.muted, width: 1, dash: "dot" as const } })),
              ] }}
              config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
          </div>
        </div>
      )}

      {/* ═══ Tab 2: Earnings Move Analyzer ═══ */}
      {activeTab === 2 && (
        <div className="card space-y-4">
          <div className="flex items-center gap-3">
            <input type="text" value={labTicker} onChange={e => setLabTicker(e.target.value.toUpperCase())}
              className="w-24 px-2 py-1.5 border border-border rounded text-sm font-data bg-surface" />
            <button onClick={() => loadChain.mutate(labTicker)} disabled={loadChain.isPending}
              className="px-4 py-1.5 bg-accent text-white text-sm font-semibold rounded hover:bg-accent-hover disabled:opacity-50">
              {loadChain.isPending ? "Loading..." : "Analyze"}</button>
          </div>
          {labHist.length > 20 && labSpot > 0 && (() => {
            const dailyMoves = labHist.slice(1).map((b, i) => labHist[i].Close > 0 ? Math.abs(b.Close / labHist[i].Close - 1) * 100 : 0).filter(m => m > 0);
            const avgMove = dailyMoves.reduce((s, m) => s + m, 0) / dailyMoves.length;
            const maxMove = Math.max(...dailyMoves);
            // Implied moves from ATM straddles
            const exps = [...new Set(labChain.map(c => c.expiration_date))].sort().slice(0, 6);
            const impliedMoves = exps.map(exp => {
              const dte = Math.max(1, Math.round((new Date(exp + "T16:00:00").getTime() - Date.now()) / 86400000));
              const call = labChain.filter(c => c.expiration_date === exp && c.contract_type === "call").sort((a, b) => Math.abs(a.strike_price - labSpot) - Math.abs(b.strike_price - labSpot))[0];
              const put = labChain.filter(c => c.expiration_date === exp && c.contract_type === "put").sort((a, b) => Math.abs(a.strike_price - labSpot) - Math.abs(b.strike_price - labSpot))[0];
              if (!call || !put) return null;
              const straddle = ((call.bid + call.ask) / 2 + (put.bid + put.ask) / 2);
              return { exp, dte, straddle, movePct: (straddle / labSpot * 100), move$: straddle };
            }).filter(Boolean) as { exp: string; dte: number; straddle: number; movePct: number; move$: number }[];
            return (
              <>
                <div className="flex gap-6"><Metric label="Avg Daily Move" value={`${avgMove.toFixed(2)}%`} /><Metric label="Max Daily Move" value={`${maxMove.toFixed(1)}%`} /><Metric label="Spot" value={`$${labSpot.toFixed(2)}`} /></div>
                <Plot data={[{ x: dailyMoves, type: "histogram" as const, nbinsx: 50, marker: { color: t.accent + "60", line: { color: t.accent, width: 1 } } }]}
                  layout={{ height: 200, ...L, margin: { l: 40, r: 10, t: 5, b: 30 }, xaxis: { title: "Daily Move (%)", gridcolor: t.grid }, yaxis: { title: "Freq", gridcolor: t.grid } }}
                  config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
                {impliedMoves.length > 0 && (
                  <div className="overflow-x-auto">
                    <table className="data-table text-xs">
                      <thead><tr><th>Expiration</th><th>DTE</th><th>ATM Straddle</th><th>Implied Move ($)</th><th>Implied Move (%)</th></tr></thead>
                      <tbody>{impliedMoves.map((m, i) => (
                        <tr key={i}><td>{m.exp}</td><td className="font-data">{m.dte}d</td><td className="font-data">${m.straddle.toFixed(2)}</td><td className="font-data">±${m.move$.toFixed(2)}</td><td className="font-data font-semibold">±{m.movePct.toFixed(1)}%</td></tr>
                      ))}</tbody>
                    </table>
                  </div>
                )}
              </>
            );
          })()}
        </div>
      )}

      {/* ═══ Tab 3: Strategy Optimizer ═══ */}
      {activeTab === 3 && (
        <div className="card space-y-4">
          <div className="flex items-center gap-3 flex-wrap">
            <input type="text" value={labTicker} onChange={e => setLabTicker(e.target.value.toUpperCase())}
              className="w-24 px-2 py-1.5 border border-border rounded text-sm font-data bg-surface" />
            <button onClick={() => loadChain.mutate(labTicker)} disabled={loadChain.isPending}
              className="px-4 py-1.5 bg-accent text-white text-sm font-semibold rounded hover:bg-accent-hover disabled:opacity-50">
              {loadChain.isPending ? "Loading..." : "Load Chain"}</button>
          </div>
          {labChain.length > 0 && labSpot > 0 && (() => {
            const exps = [...new Set(labChain.map(c => c.expiration_date))].sort();
            const frontExp = exps[Math.min(2, exps.length - 1)] || exps[0];
            const expChain = labChain.filter(c => c.expiration_date === frontExp);
            const calls = expChain.filter(c => c.contract_type === "call" && c.open_interest > 10).sort((a, b) => a.strike_price - b.strike_price);
            const puts = expChain.filter(c => c.contract_type === "put" && c.open_interest > 10).sort((a, b) => a.strike_price - b.strike_price);
            // Bull Call Spread optimizer: max R:R with reasonable width
            const bullSpreads = calls.flatMap((long, i) => calls.slice(i + 1, i + 6).map(short => {
              const debit = ((long.bid + long.ask) / 2) - ((short.bid + short.ask) / 2);
              const maxProfit = short.strike_price - long.strike_price - debit;
              if (debit <= 0 || maxProfit <= 0) return null;
              const rr = maxProfit / debit;
              const pop = 1 - Math.abs(long.delta || 0.5);
              return { type: "Bull Call", longK: long.strike_price, shortK: short.strike_price, debit: Math.round(debit * 100), maxProfit: Math.round(maxProfit * 100), rr: rr.toFixed(2), pop: (pop * 100).toFixed(0), exp: frontExp };
            })).filter(Boolean).sort((a, b) => Number(b!.rr) - Number(a!.rr)).slice(0, 5);
            // Iron Condor optimizer
            const icSpreads = puts.filter(p => p.strike_price < labSpot * 0.95).slice(-3).flatMap(sp =>
              calls.filter(c => c.strike_price > labSpot * 1.05).slice(0, 3).map(sc => {
                const lp = puts.find(p => p.strike_price <= sp.strike_price - 5);
                const lc = calls.find(c => c.strike_price >= sc.strike_price + 5);
                if (!lp || !lc) return null;
                const credit = ((sp.bid + sp.ask) / 2 + (sc.bid + sc.ask) / 2) - ((lp.bid + lp.ask) / 2 + (lc.bid + lc.ask) / 2);
                const width = Math.max(sp.strike_price - lp.strike_price, lc.strike_price - sc.strike_price);
                const risk = width - credit;
                if (credit <= 0 || risk <= 0) return null;
                const pop = 1 - Math.abs(sp.delta || 0.15) - Math.abs(sc.delta || 0.15);
                return { type: "Iron Condor", longK: `${lp.strike_price}P/${lc.strike_price}C`, shortK: `${sp.strike_price}P/${sc.strike_price}C`, debit: Math.round(-credit * 100), maxProfit: Math.round(credit * 100), rr: (credit / risk).toFixed(2), pop: (pop * 100).toFixed(0), exp: frontExp };
              })
            ).filter(Boolean).sort((a, b) => Number(b!.rr) - Number(a!.rr)).slice(0, 5);
            const all = [...(bullSpreads as NonNullable<typeof bullSpreads[0]>[]), ...(icSpreads as NonNullable<typeof icSpreads[0]>[])].sort((a, b) => Number(b.rr) - Number(a.rr));
            return (
              <>
                <div className="text-xs text-text-muted">Optimizing for {frontExp} expiration · Spot ${labSpot.toFixed(2)}</div>
                <div className="overflow-x-auto">
                  <table className="data-table text-xs">
                    <thead><tr><th>Strategy</th><th>Long</th><th>Short</th><th>Debit/Credit</th><th>Max Profit</th><th>R:R</th><th>POP Est</th></tr></thead>
                    <tbody>{all.map((s, i) => (
                      <tr key={i} className={i === 0 ? "bg-gain/5" : ""}>
                        <td className="font-semibold">{s.type}</td>
                        <td className="font-data">${s.longK}</td>
                        <td className="font-data">${s.shortK}</td>
                        <td className={`font-data ${s.debit < 0 ? "text-gain" : ""}`}>${s.debit}</td>
                        <td className="font-data text-gain">${s.maxProfit}</td>
                        <td className="font-data font-semibold">{s.rr}x</td>
                        <td className="font-data">{s.pop}%</td>
                      </tr>
                    ))}</tbody>
                  </table>
                </div>
              </>
            );
          })()}
        </div>
      )}
    </div>
  );
}

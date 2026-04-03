"use client";

import { useState, useMemo } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTheme } from "next-themes";
import { fetchOptionsChain, fetchSnapshot, fetchPriceHistory, fetchAITradeIdeas } from "@/lib/api";
import { getChartTheme, getBaseLayout } from "@/lib/chart-theme";
import { Metric } from "@/components/ui/metric";
import dynamic from "next/dynamic";

const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

const TABS = ["Spread Builder", "Term Structure", "P&L Simulator", "Risk Analysis", "Scanner", "Roll Optimizer", "Backtest", "AI Assessment"];

const SCAN_TICKERS = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "META", "GOOGL", "AMD"];

function normCdf(x: number): number {
  const t = 1 / (1 + 0.2316419 * Math.abs(x));
  const p = 0.3989422804014327 * Math.exp(-x * x / 2) * (t * (0.3193815 + t * (-0.3565638 + t * (1.781478 + t * (-1.8212560 + t * 1.3302744)))));
  return x > 0 ? 1 - p : p;
}

function bsPrice(S: number, K: number, T: number, r: number, sigma: number, ot: string): number {
  if (T <= 0) return ot === "call" ? Math.max(S - K, 0) : Math.max(K - S, 0);
  const d1 = (Math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * Math.sqrt(T));
  const d2 = d1 - sigma * Math.sqrt(T);
  return ot === "call" ? S * normCdf(d1) - K * Math.exp(-r * T) * normCdf(d2) : K * Math.exp(-r * T) * normCdf(-d2) - S * normCdf(-d1);
}

interface ChainRow { strike_price: number; contract_type: string; expiration_date: string; implied_volatility: number; delta: number; gamma: number; theta: number; vega: number; open_interest: number; bid: number; ask: number; last_price: number }

function calcDTE(exp: string): number { return Math.max(1, Math.round((new Date(exp + "T16:00:00").getTime() - Date.now()) / 86400000)); }

export default function CalendarSpreads() {
  const { resolvedTheme } = useTheme();
  const t = getChartTheme(resolvedTheme === "dark");
  const L = getBaseLayout(t);
  const [ticker, setTicker] = useState("SPY");
  const [activeTab, setActiveTab] = useState(0);
  const [chain, setChain] = useState<ChainRow[]>([]);
  const [spot, setSpot] = useState(0);
  const [frontExp, setFrontExp] = useState("");
  const [backExp, setBackExp] = useState("");
  const [strike, setStrike] = useState(0);
  const [optType, setOptType] = useState<"call" | "put">("call");

  // Scanner state
  const [scanResults, setScanResults] = useState<Record<string, unknown>[]>([]);
  const [scanning, setScanning] = useState(false);

  // Backtest state
  const [btResults, setBtResults] = useState<{ date: string; pnl: number; reason: string }[]>([]);
  const [btTarget, setBtTarget] = useState(50);
  const [btStop, setBtStop] = useState(100);

  // AI Assessment state
  const [aiContent, setAiContent] = useState("");

  const load = useMutation({
    mutationFn: async (tk: string) => {
      const [ch, snap] = await Promise.all([fetchOptionsChain(tk), fetchSnapshot([tk])]);
      return { chain: ch.data as unknown as ChainRow[], spot: snap[tk]?.price ?? 0 };
    },
    onSuccess: (d) => {
      setChain(d.chain); setSpot(d.spot);
      const exps = [...new Set(d.chain.map(c => c.expiration_date))].sort();
      if (exps.length >= 2) { setFrontExp(exps[0]); setBackExp(exps[Math.min(2, exps.length - 1)]); }
      setStrike(Math.round(d.spot));
    },
  });

  const expirations = useMemo(() => [...new Set(chain.map(c => c.expiration_date))].sort(), [chain]);

  // Spread details
  const spread = useMemo(() => {
    if (!frontExp || !backExp || !strike || !spot) return null;
    const frontRow = chain.find(c => c.expiration_date === frontExp && Math.abs(c.strike_price - strike) < 0.5 && c.contract_type === optType);
    const backRow = chain.find(c => c.expiration_date === backExp && Math.abs(c.strike_price - strike) < 0.5 && c.contract_type === optType);
    if (!frontRow || !backRow) return null;
    const frontMid = (frontRow.bid + frontRow.ask) / 2 || frontRow.last_price || 0;
    const backMid = (backRow.bid + backRow.ask) / 2 || backRow.last_price || 0;
    const debit = backMid - frontMid;
    const netDelta = (backRow.delta || 0) - (frontRow.delta || 0);
    const netGamma = (backRow.gamma || 0) - (frontRow.gamma || 0);
    const netTheta = (backRow.theta || 0) - (frontRow.theta || 0);
    const netVega = (backRow.vega || 0) - (frontRow.vega || 0);
    const frontIv = frontRow.implied_volatility || 0;
    const backIv = backRow.implied_volatility || 0;
    return { debit, netDelta, netGamma, netTheta, netVega, frontMid, backMid, frontIv, backIv, frontDTE: calcDTE(frontExp), backDTE: calcDTE(backExp) };
  }, [chain, frontExp, backExp, strike, spot, optType]);

  // Term structure
  const termStructure = useMemo(() => {
    if (!spot || chain.length === 0) return [];
    return expirations.map(exp => {
      const atm = chain.filter(c => c.expiration_date === exp && c.contract_type === "call")
        .sort((a, b) => Math.abs(a.strike_price - spot) - Math.abs(b.strike_price - spot))[0];
      return atm ? { exp, dte: calcDTE(exp), iv: (atm.implied_volatility || 0) * 100 } : null;
    }).filter(Boolean) as { exp: string; dte: number; iv: number }[];
  }, [chain, spot, expirations]);

  return (
    <div className="space-y-5">
      <div><h1 className="text-2xl font-bold tracking-tight">Calendar Spreads</h1>
        <p className="text-text-secondary text-sm mt-1">Build, analyze, and simulate calendar spread strategies.</p></div>

      <div className="card card-compact">
        <div className="flex items-center gap-3 flex-wrap">
          <input type="text" value={ticker} onChange={e => setTicker(e.target.value.toUpperCase())} onKeyDown={e => e.key === "Enter" && load.mutate(ticker)}
            className="w-24 px-3 py-2 border border-border rounded-lg text-sm font-data bg-surface" />
          <button onClick={() => load.mutate(ticker)} disabled={load.isPending}
            className="px-6 py-2 bg-accent text-white font-semibold rounded-lg hover:bg-accent-hover disabled:opacity-50 text-sm">
            {load.isPending ? "Loading..." : "Load Chain"}</button>
        </div>
      </div>

      {load.isPending && <div className="card text-center py-12"><div className="inline-block w-6 h-6 border-2 border-accent border-t-transparent rounded-full animate-spin" /></div>}

      {chain.length > 0 && expirations.length >= 2 && (<>
        <div className="flex gap-1 border-b border-border pb-1 overflow-x-auto">
          {TABS.map((tab, i) => (<button key={tab} onClick={() => setActiveTab(i)}
            className={`px-3 py-1.5 text-xs font-semibold rounded-t-md transition-colors whitespace-nowrap ${activeTab === i ? "bg-accent text-white" : "text-text-muted hover:text-text hover:bg-surface-alt"}`}>{tab}</button>))}
        </div>

        {/* Tab 0: Spread Builder */}
        {activeTab === 0 && (
          <div className="card space-y-4">
            <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
              <div><label className="metric-label">Front Exp</label>
                <select value={frontExp} onChange={e => setFrontExp(e.target.value)} className="w-full px-2 py-1.5 border border-border rounded text-sm bg-surface">
                  {expirations.map(e => <option key={e} value={e}>{e} ({calcDTE(e)}d)</option>)}</select></div>
              <div><label className="metric-label">Back Exp</label>
                <select value={backExp} onChange={e => setBackExp(e.target.value)} className="w-full px-2 py-1.5 border border-border rounded text-sm bg-surface">
                  {expirations.filter(e => e > frontExp).map(e => <option key={e} value={e}>{e} ({calcDTE(e)}d)</option>)}</select></div>
              <div><label className="metric-label">Strike</label>
                <input type="number" value={strike} onChange={e => setStrike(Number(e.target.value))} className="w-full px-2 py-1.5 border border-border rounded text-sm font-data bg-surface" /></div>
              <div><label className="metric-label">Type</label>
                <div className="flex gap-1 mt-0.5">{(["call","put"] as const).map(ot => (
                  <button key={ot} onClick={() => setOptType(ot)} className={`flex-1 px-2 py-1.5 text-xs font-semibold rounded ${optType===ot?"bg-accent text-white":"bg-surface-alt text-text-muted"}`}>{ot}</button>))}</div></div>
              <div><label className="metric-label">Spot</label><div className="text-lg font-bold font-data mt-1">${spot.toFixed(2)}</div></div>
            </div>

            {spread && (<>
              <div className="flex flex-wrap gap-6">
                <Metric label="Net Debit" value={`$${spread.debit.toFixed(2)}`} />
                <Metric label="Front IV" value={`${(spread.frontIv * 100).toFixed(1)}%`} />
                <Metric label="Back IV" value={`${(spread.backIv * 100).toFixed(1)}%`} />
                <Metric label="IV Diff" value={`${((spread.backIv - spread.frontIv) * 100).toFixed(1)}%`} />
                <Metric label="Net Δ" value={spread.netDelta.toFixed(3)} />
                <Metric label="Net Γ" value={spread.netGamma.toFixed(4)} />
                <Metric label="Net Θ" value={`$${spread.netTheta.toFixed(2)}/day`} />
                <Metric label="Net ν" value={`$${spread.netVega.toFixed(2)}`} />
              </div>

              {/* P&L at front expiry */}
              {(() => {
                const lo = spot * 0.9, hi = spot * 1.1;
                const prices = Array.from({ length: 100 }, (_, i) => lo + i * (hi - lo) / 99);
                const backT = (spread.backDTE - spread.frontDTE) / 365;
                const pnl = prices.map(p => {
                  const backVal = bsPrice(p, strike, backT, 0.045, spread.backIv, optType);
                  return (backVal - spread.debit) * 100;
                });
                return (
                  <Plot data={[{ x: prices, y: pnl, type: "scatter" as const, mode: "lines" as const, line: { color: t.accent, width: 2 },
                    fill: "tozeroy" as const, fillcolor: t.accent + "10" }]}
                    layout={{ height: 350, ...L, yaxis: { title: "P&L ($)", gridcolor: t.grid }, xaxis: { title: "Price at Front Expiry", gridcolor: t.grid }, hovermode: "x unified",
                      shapes: [
                        { type: "line", x0: spot, x1: spot, y0: 0, y1: 1, yref: "paper", line: { color: t.spot, width: 1, dash: "dot" } },
                        { type: "line", x0: strike, x1: strike, y0: 0, y1: 1, yref: "paper", line: { color: t.loss, width: 1, dash: "dot" } },
                        { type: "line", y0: 0, y1: 0, x0: lo, x1: hi, line: { color: t.muted, width: 1 } },
                      ] }}
                    config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
                );
              })()}
            </>)}
          </div>
        )}

        {/* Tab 1: Term Structure */}
        {activeTab === 1 && termStructure.length > 0 && (
          <div className="card space-y-4">
            <Plot data={[{
              x: termStructure.map(ts => `${ts.exp} (${ts.dte}d)`),
              y: termStructure.map(ts => ts.iv),
              type: "scatter" as const, mode: "lines+markers" as const,
              line: { color: t.accent, width: 2 }, marker: { size: 8 },
              text: termStructure.map(ts => `${ts.iv.toFixed(1)}%`), textposition: "top center" as const, textfont: { size: 9, color: t.text },
            }]}
              layout={{ height: 350, ...L, yaxis: { title: "ATM IV (%)", gridcolor: t.grid }, xaxis: { gridcolor: t.grid } }}
              config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
            {(() => {
              const shape = termStructure.length >= 2 && termStructure[termStructure.length - 1].iv > termStructure[0].iv * 1.02 ? "Contango" : termStructure[termStructure.length - 1].iv < termStructure[0].iv * 0.98 ? "Backwardation" : "Flat";
              return <div className="text-sm text-text-muted">Shape: <strong className={shape === "Contango" ? "text-gain" : shape === "Backwardation" ? "text-loss" : ""}>{shape}</strong>
                {shape === "Contango" && " — favorable for calendar spreads (sell cheap front, buy expensive back)"}
                {shape === "Backwardation" && " — caution: front-month IV elevated (event risk)"}</div>;
            })()}
          </div>
        )}

        {/* Tab 2: P&L Simulator */}
        {activeTab === 2 && spread && (
          <div className="card space-y-4">
            <p className="text-xs text-text-muted">P&L heatmap across spot price and days elapsed.</p>
            {(() => {
              const lo = spot * 0.92, hi = spot * 1.08;
              const prices = Array.from({ length: 40 }, (_, i) => lo + i * (hi - lo) / 39);
              const days = Array.from({ length: spread.frontDTE }, (_, i) => i + 1);
              const z = days.map(day => {
                const backT = (spread.backDTE - day) / 365;
                return prices.map(p => {
                  if (backT <= 0) return 0;
                  return (bsPrice(p, strike, backT, 0.045, spread.backIv, optType) - spread.debit) * 100;
                });
              });
              return (
                <Plot data={[{
                  type: "heatmap" as const, x: prices, y: days, z,
                  colorscale: [[0, t.loss], [0.5, t.grid], [1, t.gain]], zmid: 0,
                  colorbar: { title: { text: "P&L ($)", font: { size: 9 } }, thickness: 12 },
                  hovertemplate: "Price: $%{x:.0f}<br>Day: %{y}<br>P&L: $%{z:.0f}<extra></extra>",
                }]}
                  layout={{ height: 400, ...L, xaxis: { title: "Spot Price", gridcolor: t.grid }, yaxis: { title: "Days Elapsed", gridcolor: t.grid },
                    shapes: [{ type: "line", x0: spot, x1: spot, y0: 0, y1: 1, yref: "paper", line: { color: t.spot, width: 2, dash: "dot" } }] }}
                  config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
              );
            })()}
          </div>
        )}

        {/* Tab 3: Risk Analysis — Vega risk heatmap */}
        {activeTab === 3 && spread && (
          <div className="card space-y-4">
            <p className="text-xs text-text-muted">P&L sensitivity to parallel IV shifts and term structure tilts.</p>
            {(() => {
              const ivShifts = [-10, -5, -2, 0, 2, 5, 10];
              const tilts = [-8, -4, -2, 0, 2, 4, 8];
              const backT = (spread.backDTE - spread.frontDTE) / 365;
              const z = tilts.map(tilt => ivShifts.map(shift => {
                const newBackIv = Math.max(0.01, spread.backIv + (shift + tilt) / 100);
                const val = bsPrice(spot, strike, backT, 0.045, newBackIv, optType);
                return Math.round((val - spread.debit) * 100);
              }));
              return (
                <Plot data={[{
                  type: "heatmap" as const,
                  x: ivShifts.map(s => `${s > 0 ? "+" : ""}${s}%`),
                  y: tilts.map(t => `${t > 0 ? "+" : ""}${t}% tilt`),
                  z,
                  colorscale: [[0, t.loss], [0.5, t.grid], [1, t.gain]], zmid: 0,
                  text: z.map(row => row.map(v => `$${v}`)), texttemplate: "%{text}", textfont: { size: 10 },
                  colorbar: { title: { text: "P&L ($)", font: { size: 9 } }, thickness: 12 },
                }]}
                  layout={{ height: 350, ...L, margin: { l: 80, r: 20, t: 10, b: 50 }, xaxis: { title: "Parallel IV Shift", gridcolor: t.grid }, yaxis: { title: "Term Structure Tilt", gridcolor: t.grid } }}
                  config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
              );
            })()}
          </div>
        )}
        {/* ═══ Tab 4: Scanner ═══ */}
        {activeTab === 4 && (
          <div className="card space-y-4">
            <div className="flex items-center gap-3">
              <button onClick={async () => {
                setScanning(true);
                try {
                  const results: Record<string, unknown>[] = [];
                  for (const tk of SCAN_TICKERS) {
                    try {
                      const [ch, snap] = await Promise.all([fetchOptionsChain(tk), fetchSnapshot([tk])]);
                      const rows = ch.data as unknown as ChainRow[];
                      const sp = snap[tk]?.price ?? 0;
                      if (!sp || rows.length < 20) continue;
                      const exps = [...new Set(rows.map(c => c.expiration_date))].sort();
                      // Find front (30-60 DTE) and back (60-120 DTE)
                      const frontCands = exps.filter(e => { const d = calcDTE(e); return d >= 20 && d <= 60; });
                      const backCands = exps.filter(e => { const d = calcDTE(e); return d >= 50 && d <= 120; });
                      if (frontCands.length === 0 || backCands.length === 0) continue;
                      const fe = frontCands[0], be = backCands[backCands.length - 1];
                      if (fe === be) continue;
                      const atmStrike = Math.round(sp);
                      const fr = rows.find(c => c.expiration_date === fe && Math.abs(c.strike_price - atmStrike) < sp * 0.02 && c.contract_type === "call");
                      const br = rows.find(c => c.expiration_date === be && Math.abs(c.strike_price - atmStrike) < sp * 0.02 && c.contract_type === "call");
                      if (!fr || !br) continue;
                      const fMid = (fr.bid + fr.ask) / 2 || fr.last_price || 0;
                      const bMid = (br.bid + br.ask) / 2 || br.last_price || 0;
                      const debit = bMid - fMid;
                      if (debit <= 0) continue;
                      const thetaDay = Math.abs((br.theta || 0) - (fr.theta || 0));
                      const ivDiff = ((br.implied_volatility || 0) - (fr.implied_volatility || 0)) * 100;
                      const vegaRatio = Math.abs((br.vega || 0) - (fr.vega || 0));
                      const thetaDebit = debit > 0 ? thetaDay / debit : 0;
                      const score = thetaDebit * 0.4 + Math.max(0, ivDiff) * 0.03 + (vegaRatio > 0 && thetaDay > 0 ? thetaDay / vegaRatio * 0.3 : 0);
                      results.push({ ticker: tk, strike: atmStrike, frontExp: fe, backExp: be, frontDTE: calcDTE(fe), backDTE: calcDTE(be),
                        debit: Math.round(debit * 100), frontIV: ((fr.implied_volatility || 0) * 100).toFixed(1), backIV: ((br.implied_volatility || 0) * 100).toFixed(1),
                        ivDiff: ivDiff.toFixed(1), thetaDay: (thetaDay * 100).toFixed(1), thetaDebit: thetaDebit.toFixed(3),
                        minOI: Math.min(fr.open_interest || 0, br.open_interest || 0), score: score.toFixed(4) });
                    } catch { /* skip ticker */ }
                  }
                  results.sort((a, b) => Number(b.score) - Number(a.score));
                  setScanResults(results);
                } finally { setScanning(false); }
              }} disabled={scanning}
                className="px-6 py-2 bg-accent text-white font-semibold rounded-lg hover:bg-accent-hover disabled:opacity-50 text-sm">
                {scanning ? "Scanning..." : "Scan 10 Tickers"}
              </button>
              {scanning && <div className="w-4 h-4 border-2 border-accent border-t-transparent rounded-full animate-spin" />}
            </div>
            {scanResults.length > 0 && (
              <div className="overflow-x-auto">
                <table className="data-table text-xs">
                  <thead><tr><th>Ticker</th><th>Strike</th><th>Front</th><th>Back</th><th>Debit</th><th>Front IV</th><th>Back IV</th><th>IV Diff</th><th>Θ/Day</th><th>Θ/Debit</th><th>Min OI</th><th>Score</th></tr></thead>
                  <tbody>
                    {scanResults.map((r, i) => (
                      <tr key={i} className={i === 0 ? "bg-gain/5" : ""}>
                        <td className="font-semibold">{r.ticker as string}</td>
                        <td className="font-data">${r.strike as number}</td>
                        <td className="font-data">{r.frontDTE as number}d</td>
                        <td className="font-data">{r.backDTE as number}d</td>
                        <td className="font-data">${r.debit as number}</td>
                        <td className="font-data">{r.frontIV as string}%</td>
                        <td className="font-data">{r.backIV as string}%</td>
                        <td className={`font-data ${Number(r.ivDiff) > 0 ? "text-gain" : "text-loss"}`}>{r.ivDiff as string}%</td>
                        <td className="font-data">${r.thetaDay as string}</td>
                        <td className="font-data font-semibold">{r.thetaDebit as string}</td>
                        <td className="font-data">{(r.minOI as number).toLocaleString()}</td>
                        <td className="font-data font-semibold">{r.score as string}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}

        {/* ═══ Tab 5: Roll Optimizer ═══ */}
        {activeTab === 5 && spread && (
          <div className="card space-y-4">
            <div className="metric-label mb-2">Theta Decay Profile & Roll Candidates</div>
            {/* Theta decay curve for current front leg */}
            {(() => {
              const days = Array.from({ length: spread.frontDTE }, (_, i) => i + 1);
              const thetaProfile = days.map(d => {
                const Tf = (spread.frontDTE - d) / 365;
                const Tb = (spread.backDTE - d) / 365;
                if (Tf <= 0.003) return { day: d, value: 0, theta: 0 };
                const fv = bsPrice(spot, strike, Tf, 0.045, spread.frontIv || 0.2, optType);
                const bv = bsPrice(spot, strike, Tb, 0.045, spread.backIv || 0.2, optType);
                return { day: d, value: Math.round((bv - fv) * 100), theta: 0 };
              });
              return (
                <Plot data={[{
                  x: thetaProfile.map(p => p.day), y: thetaProfile.map(p => p.value),
                  type: "scatter" as const, mode: "lines" as const, fill: "tozeroy", fillcolor: t.accent + "15",
                  line: { color: t.accent, width: 2 }, name: "Spread Value ($)", showlegend: false,
                }]} layout={{ height: 250, ...L, xaxis: { title: "Days Held", gridcolor: t.grid }, yaxis: { title: "Spread Value ($)", gridcolor: t.grid },
                  shapes: [
                    { type: "rect", x0: Math.max(0, spread.frontDTE - 21), x1: Math.max(0, spread.frontDTE - 7), y0: 0, y1: 1, yref: "paper", fillcolor: t.spot + "15", line: { width: 0 } },
                  ],
                  annotations: [{ x: Math.max(0, spread.frontDTE - 14), y: 1, yref: "paper", text: "Roll Window", showarrow: false, font: { size: 8, color: t.spot } }],
                }} config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
              );
            })()}

            {/* Roll candidates */}
            {(() => {
              const midExps = expirations.filter(e => {
                const d = calcDTE(e);
                return d > spread.frontDTE && d < spread.backDTE;
              }).slice(0, 5);
              if (midExps.length === 0) return <p className="text-xs text-text-muted">No intermediate expirations for rolling.</p>;
              const candidates = midExps.map(e => {
                const row = chain.find(c => c.expiration_date === e && Math.abs(c.strike_price - strike) < spot * 0.02 && c.contract_type === optType);
                if (!row) return null;
                const newMid = (row.bid + row.ask) / 2 || row.last_price || 0;
                const frontRow = chain.find(c => c.expiration_date === frontExp && Math.abs(c.strike_price - strike) < 0.5 && c.contract_type === optType);
                const currentFrontMid = frontRow ? (frontRow.bid + frontRow.ask) / 2 || frontRow.last_price || 0 : 0;
                const rollCost = newMid - currentFrontMid;
                const newTheta = (row.theta || 0) - ((chain.find(c => c.expiration_date === backExp && Math.abs(c.strike_price - strike) < 0.5 && c.contract_type === optType)?.theta || 0));
                return { exp: e, dte: calcDTE(e), rollCost: Math.round(rollCost * 100), newIV: ((row.implied_volatility || 0) * 100).toFixed(1), newTheta: (Math.abs(newTheta) * 100).toFixed(1), newDelta: (row.delta || 0).toFixed(3) };
              }).filter(Boolean);
              return (
                <div className="overflow-x-auto">
                  <table className="data-table text-xs">
                    <thead><tr><th>New Front</th><th>DTE</th><th>Roll Cost</th><th>New IV</th><th>New Θ/Day</th><th>New Δ</th></tr></thead>
                    <tbody>
                      {candidates.map((c, i) => c && (
                        <tr key={i}>
                          <td className="font-semibold">{c.exp}</td>
                          <td className="font-data">{c.dte}d</td>
                          <td className={`font-data ${c.rollCost > 0 ? "text-loss" : "text-gain"}`}>{c.rollCost > 0 ? "+" : ""}${c.rollCost}</td>
                          <td className="font-data">{c.newIV}%</td>
                          <td className="font-data">${c.newTheta}</td>
                          <td className="font-data">{c.newDelta}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              );
            })()}
          </div>
        )}

        {/* ═══ Tab 6: Backtest ═══ */}
        {activeTab === 6 && (
          <div className="card space-y-4">
            <div className="flex items-center gap-3 flex-wrap">
              <div><label className="metric-label">Profit Target %</label>
                <input type="number" value={btTarget} onChange={e => setBtTarget(+e.target.value)} step={10} min={10} max={200}
                  className="w-20 mt-1 px-2 py-1 border border-border rounded text-xs font-data bg-surface" /></div>
              <div><label className="metric-label">Stop Loss %</label>
                <input type="number" value={btStop} onChange={e => setBtStop(+e.target.value)} step={10} min={20} max={200}
                  className="w-20 mt-1 px-2 py-1 border border-border rounded text-xs font-data bg-surface" /></div>
              <button onClick={async () => {
                if (!spread) return;
                const res = await fetchPriceHistory(ticker, 504);
                const bars = res.data || [];
                if (bars.length < 60) return;
                const results: { date: string; pnl: number; reason: string }[] = [];
                const frontDTE = spread.frontDTE, backDTE = spread.backDTE;
                const fIv = spread.frontIv || 0.2, bIv = spread.backIv || 0.2;
                // Simulate entries every 21 days
                for (let i = 0; i + frontDTE < bars.length; i += 21) {
                  const entrySpot = bars[i].Close;
                  const entryDebit = bsPrice(entrySpot, entrySpot, backDTE / 365, 0.045, bIv, "call") - bsPrice(entrySpot, entrySpot, frontDTE / 365, 0.045, fIv, "call");
                  if (entryDebit <= 0) continue;
                  let exitPnl = 0, exitReason = "DTE";
                  for (let d = 1; d <= frontDTE && i + d < bars.length; d++) {
                    const px = bars[i + d].Close;
                    const Tf = Math.max(0.003, (frontDTE - d) / 365), Tb = (backDTE - d) / 365;
                    const sv = bsPrice(px, entrySpot, Tb, 0.045, bIv, "call") - bsPrice(px, entrySpot, Tf, 0.045, fIv, "call");
                    const pnlPct = (sv - entryDebit) / entryDebit * 100;
                    if (pnlPct >= btTarget) { exitPnl = pnlPct; exitReason = "Target"; break; }
                    if (pnlPct <= -btStop) { exitPnl = pnlPct; exitReason = "Stop"; break; }
                    exitPnl = pnlPct;
                  }
                  results.push({ date: bars[i].Date, pnl: Math.round(exitPnl * 10) / 10, reason: exitReason });
                }
                setBtResults(results);
              }} className="px-6 py-1.5 bg-accent text-white font-semibold rounded-lg text-sm hover:bg-accent-hover">
                Run Backtest
              </button>
            </div>
            {btResults.length > 0 && (
              <>
                <div className="flex gap-4 text-xs font-data text-text-muted">
                  <span>Trades: {btResults.length}</span>
                  <span>Win Rate: {(btResults.filter(r => r.pnl > 0).length / btResults.length * 100).toFixed(0)}%</span>
                  <span>Avg P&L: {(btResults.reduce((s, r) => s + r.pnl, 0) / btResults.length).toFixed(1)}%</span>
                  <span>Target: {btResults.filter(r => r.reason === "Target").length}</span>
                  <span>Stop: {btResults.filter(r => r.reason === "Stop").length}</span>
                  <span>DTE: {btResults.filter(r => r.reason === "DTE").length}</span>
                </div>
                <Plot data={[{
                  x: btResults.map(r => r.date), y: btResults.map(r => r.pnl), type: "bar" as const,
                  marker: { color: btResults.map(r => r.pnl >= 0 ? t.gain : t.loss) },
                  hovertemplate: "%{x}<br>P&L: %{y:.1f}%<extra></extra>",
                }]} layout={{ height: 250, ...L, xaxis: { gridcolor: t.grid }, yaxis: { title: "P&L %", gridcolor: t.grid } }}
                  config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
                <Plot data={[{
                  x: btResults.map((_, i) => i + 1), y: btResults.reduce((acc: number[], r) => { acc.push((acc[acc.length - 1] || 0) + r.pnl); return acc; }, []),
                  type: "scatter" as const, mode: "lines" as const, fill: "tozeroy",
                  fillcolor: t.accent + "15", line: { color: t.accent, width: 2 }, showlegend: false,
                }]} layout={{ height: 200, ...L, xaxis: { title: "Trade #", gridcolor: t.grid }, yaxis: { title: "Cumulative P&L %", gridcolor: t.grid } }}
                  config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
                <div className="overflow-x-auto">
                  <table className="data-table text-xs">
                    <thead><tr><th>#</th><th>Entry</th><th>P&L %</th><th>Exit Reason</th></tr></thead>
                    <tbody>
                      {btResults.map((r, i) => (
                        <tr key={i}>
                          <td className="font-data">{i + 1}</td>
                          <td className="font-data">{r.date}</td>
                          <td className={`font-data font-semibold ${r.pnl >= 0 ? "text-gain" : "text-loss"}`}>{r.pnl}%</td>
                          <td><span className={`badge ${r.reason === "Target" ? "badge-gain" : r.reason === "Stop" ? "badge-loss" : "badge-info"}`}>{r.reason}</span></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </div>
        )}

        {/* ═══ Tab 7: AI Assessment ═══ */}
        {activeTab === 7 && (
          <div className="card space-y-4">
            {(() => {
              const loadAI = async () => {
                if (!spread) return;
                const ctx = `CALENDAR SPREAD ASSESSMENT for ${ticker}\nSpot: $${spot.toFixed(2)} | Strike: $${strike} | Type: ${optType}\nFront: ${frontExp} (${spread.frontDTE}d, IV ${(spread.frontIv * 100).toFixed(1)}%)\nBack: ${backExp} (${spread.backDTE}d, IV ${(spread.backIv * 100).toFixed(1)}%)\nDebit: $${(spread.debit * 100).toFixed(0)} | IV Diff: ${((spread.backIv - spread.frontIv) * 100).toFixed(1)}%\nNet Greeks: Δ ${spread.netDelta.toFixed(3)} | Γ ${spread.netGamma.toFixed(4)} | Θ $${(spread.netTheta * 100).toFixed(1)}/day | ν $${(spread.netVega * 100).toFixed(1)}\n\nGrade this setup A-F. Provide: 1) 2-3 paragraph assessment, 2) key risks, 3) suggested adjustments, 4) optimal entry timing. Search X/Twitter for sentiment on ${ticker}.`;
                try {
                  const res = await fetchAITradeIdeas({ ticker, context: ctx, style: "full_scan" });
                  setAiContent(res.content);
                } catch (e) { setAiContent(`Error: ${(e as Error).message}`); }
              };
              return (
                <>
                  <button onClick={loadAI} disabled={!spread}
                    className="px-6 py-2 bg-accent text-white font-semibold rounded-lg hover:bg-accent-hover disabled:opacity-50 text-sm">
                    {aiContent ? "Re-run" : "Run"} Gemini Assessment
                  </button>
                  {aiContent && (
                    <div className="prose prose-sm max-w-none text-sm" dangerouslySetInnerHTML={{
                      __html: aiContent.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
                        .replace(/^## (.*?)$/gm, '<h3 class="text-base font-bold mt-4 mb-2">$1</h3>')
                        .replace(/^#### (.*?)$/gm, '<h4 class="text-sm font-semibold mt-3 mb-1">$1</h4>')
                        .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
                        .replace(/\n/g, "<br/>"),
                    }} />
                  )}
                </>
              );
            })()}
          </div>
        )}
      </>)}

      {load.isError && <div className="card border-loss/30 bg-loss-bg text-loss text-sm">Failed: {(load.error as Error).message}</div>}
    </div>
  );
}

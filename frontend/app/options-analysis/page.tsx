"use client";

import { useState, useMemo, useEffect } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useTheme } from "next-themes";
import { fetchOptionsChain, fetchSnapshot, fetchOIHistory } from "@/lib/api";
import { getChartTheme, getBaseLayout } from "@/lib/chart-theme";
import { Metric } from "@/components/ui/metric";
import dynamic from "next/dynamic";

const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

const TABS = ["IV & Skew", "Positioning & Max Pain", "Order Flow", "Dealer Greeks", "OI Changes", "Chain"];

const INDEX_TICKERS = new Set([
  "SPY", "QQQ", "IWM", "DIA", "VTI", "VOO", "SPX", "NDX", "RUT", "VIX",
  "XLE", "XLF", "XLK", "XLI", "XLU", "XLP", "XLY", "XLV", "XLB", "XLC", "XLRE",
  "TLT", "IEF", "HYG", "LQD", "GLD", "SLV", "USO", "UNG", "EEM", "EFA",
]);
const SENTIMENT_DTE_MAX = 45;

function calcDTE(exp: string): number {
  return Math.max(1, Math.round((new Date(exp + "T16:00:00").getTime() - Date.now()) / 86400000));
}

interface ChainRow {
  strike_price: number; contract_type: string; expiration_date: string;
  implied_volatility: number; delta: number; gamma: number; theta: number; vega: number;
  open_interest: number; volume: number; last_price: number; bid: number; ask: number;
}

export default function OptionsIntelligence() {
  const { resolvedTheme } = useTheme();
  const t = getChartTheme(resolvedTheme === "dark");
  const L = getBaseLayout(t);
  const [ticker, setTicker] = useState("SPY");
  const [loadedTicker, setLoadedTicker] = useState("");
  const [activeTab, setActiveTab] = useState(0);
  const [chain, setChain] = useState<ChainRow[]>([]);
  const [spot, setSpot] = useState(0);
  const [selectedExp, setSelectedExp] = useState("");
  // Flow tab filters
  const [minVol, setMinVol] = useState(100);
  const [minRatio, setMinRatio] = useState(2.0);
  const [blockMinVol, setBlockMinVol] = useState(500);
  const [blockMinNotional, setBlockMinNotional] = useState(50000);

  const load = useMutation({
    mutationFn: async (tk: string) => {
      const [ch, snap] = await Promise.all([fetchOptionsChain(tk), fetchSnapshot([tk])]);
      return { chain: ch.data as unknown as ChainRow[], spot: snap[tk]?.price ?? 0, tk };
    },
    onSuccess: (d) => {
      setChain(d.chain);
      setSpot(d.spot);
      setLoadedTicker(d.tk);
      const exps = [...new Set(d.chain.map(c => c.expiration_date))].sort();
      if (exps.length > 0) setSelectedExp(exps[0]);
    },
  });

  const expirations = useMemo(() => [...new Set(chain.map(c => c.expiration_date))].sort(), [chain]);
  const expChain = useMemo(() => chain.filter(c => c.expiration_date === selectedExp), [chain, selectedExp]);
  const calls = useMemo(() => expChain.filter(c => c.contract_type === "call").sort((a, b) => a.strike_price - b.strike_price), [expChain]);
  const puts = useMemo(() => expChain.filter(c => c.contract_type === "put").sort((a, b) => a.strike_price - b.strike_price), [expChain]);

  const strikeLo = spot * 0.85, strikeHi = spot * 1.15;
  const visCalls = useMemo(() => calls.filter(c => c.strike_price >= strikeLo && c.strike_price <= strikeHi), [calls, strikeLo, strikeHi]);
  const visPuts = useMemo(() => puts.filter(c => c.strike_price >= strikeLo && c.strike_price <= strikeHi), [puts, strikeLo, strikeHi]);

  // ── Max pain (selected expiry) ─────────────────────────────────
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

  // ── Aggregate GEX (full chain near spot) ────────────────────────
  const gexAgg = useMemo(() => {
    if (!spot) return null;
    const rows = chain.filter(c => c.strike_price >= spot * 0.85 && c.strike_price <= spot * 1.15 && c.gamma > 0);
    if (!rows.length) return null;
    const byStrike = new Map<number, { call: number; put: number }>();
    for (const c of rows) {
      const g = c.gamma * c.open_interest * 100 * spot * spot / 1e7;
      const entry = byStrike.get(c.strike_price) ?? { call: 0, put: 0 };
      if (c.contract_type === "call") entry.call += g; else entry.put += g;
      byStrike.set(c.strike_price, entry);
    }
    const strikes = [...byStrike.keys()].sort((a, b) => a - b);
    const net = strikes.map(s => ({ strike: s, call: byStrike.get(s)!.call, put: byStrike.get(s)!.put, net: byStrike.get(s)!.call - byStrike.get(s)!.put }));
    const totalGex = net.reduce((s, n) => s + n.net, 0);
    const maxGex = net.reduce((b, n) => n.net > b.net ? n : b, net[0]);
    const minGex = net.reduce((b, n) => n.net < b.net ? n : b, net[0]);
    return { net, totalGex, maxGexStrike: maxGex.strike, minGexStrike: minGex.strike };
  }, [chain, spot]);

  // ── Put/Call sentiment (0–45 DTE, ticker-aware baseline) ────────
  const pcStats = useMemo(() => {
    const now = Date.now();
    const inWindow = (exp: string) => {
      const d = Date.parse(exp);
      if (Number.isNaN(d)) return true;
      const days = (d - now) / 86400000;
      return days >= 0 && days <= SENTIMENT_DTE_MAX;
    };
    const allCalls = chain.filter(c => c.contract_type === "call" && inWindow(c.expiration_date));
    const allPuts = chain.filter(c => c.contract_type === "put" && inWindow(c.expiration_date));
    const cv = allCalls.reduce((s, c) => s + c.volume, 0);
    const pv = allPuts.reduce((s, c) => s + c.volume, 0);
    const co = allCalls.reduce((s, c) => s + c.open_interest, 0);
    const po = allPuts.reduce((s, c) => s + c.open_interest, 0);
    const pcVol = cv > 0 ? pv / cv : 0;
    const pcOI = co > 0 ? po / co : 0;
    const isIndex = INDEX_TICKERS.has(loadedTicker);
    const histMean = isIndex ? 1.10 : 0.70;
    const histStd = isIndex ? 0.22 : 0.15;
    const z = histStd > 0 ? (pcVol - histMean) / histStd : 0;
    const regime = z > 1.5 ? "Extreme Fear" : z > 0.5 ? "Elevated Hedging" : z < -1.5 ? "Extreme Complacency" : z < -0.5 ? "Low Hedging" : "Neutral";
    return { cv, pv, co, po, pcVol, pcOI, z, histMean, histStd, regime, isIndex };
  }, [chain, loadedTicker]);

  // ── Unusual activity (filtered) ─────────────────────────────────
  const unusual = useMemo(() => chain.filter(c => c.volume > minVol && c.open_interest > 0)
    .map(c => ({ ...c, vol_oi: c.volume / c.open_interest }))
    .filter(c => c.vol_oi >= minRatio)
    .sort((a, b) => b.vol_oi - a.vol_oi), [chain, minVol, minRatio]);

  // ── Block trades ────────────────────────────────────────────────
  const blocks = useMemo(() => chain.filter(c => c.volume >= blockMinVol && c.last_price > 0 && c.volume * c.last_price * 100 >= blockMinNotional)
    .map(c => ({ ...c, notional: c.volume * c.last_price * 100 }))
    .sort((a, b) => b.notional - a.notional), [chain, blockMinVol, blockMinNotional]);

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Options Intelligence</h1>
        <p className="text-text-secondary text-sm mt-1">Volatility surface, positioning, flow, dealer Greeks, and full chain — unified.</p>
      </div>

      <div className="card card-compact">
        <div className="flex items-center gap-3 flex-wrap">
          <input type="text" value={ticker} onChange={e => setTicker(e.target.value.toUpperCase())}
            onKeyDown={e => e.key === "Enter" && load.mutate(ticker)}
            placeholder="SPY" className="w-32 px-3 py-2 border border-border rounded-lg text-sm font-data bg-surface" />
          <button onClick={() => load.mutate(ticker)} disabled={load.isPending}
            className="px-6 py-2 bg-accent text-white font-semibold rounded-lg hover:bg-accent-hover disabled:opacity-50 text-sm">
            {load.isPending ? "Loading..." : "Load Chain"}
          </button>
          {expirations.length > 0 && (
            <>
              <label className="metric-label">Expiration</label>
              <select value={selectedExp} onChange={e => setSelectedExp(e.target.value)}
                className="px-2 py-2 border border-border rounded-lg text-sm bg-surface">
                {expirations.map(e => <option key={e} value={e}>{e} ({calcDTE(e)}d)</option>)}
              </select>
            </>
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
              <Metric label="P/C Vol (≤45d)" value={pcStats.pcVol.toFixed(2)} deltaType={pcStats.pcVol > pcStats.histMean ? "loss" : "gain"} />
              <Metric label="Sentiment" value={pcStats.regime} />
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

          {/* ═══ Tab 0: Volatility ═══ */}
          {activeTab === 0 && (
            <div className="card space-y-6">
              <div>
                <h3 className="text-xs font-semibold uppercase tracking-wide">IV skew — {selectedExp} ({calcDTE(selectedExp)}d)</h3>
                <p className="text-xs text-text-muted mt-0.5">Call vs put IV across strikes. Right-skew = call demand; left-skew = put demand.</p>
              </div>
              <Plot data={[
                { x: visCalls.map(c => c.strike_price), y: visCalls.map(c => c.implied_volatility * 100), type: "scatter" as const, mode: "lines+markers" as const, name: "Call IV", line: { color: t.gain, width: 2 }, marker: { size: 4 } },
                { x: visPuts.map(c => c.strike_price), y: visPuts.map(c => c.implied_volatility * 100), type: "scatter" as const, mode: "lines+markers" as const, name: "Put IV", line: { color: t.loss, width: 2 }, marker: { size: 4 } },
              ]} layout={{ height: 350, ...L, xaxis: { title: "Strike", gridcolor: t.grid }, yaxis: { title: "IV (%)", gridcolor: t.grid }, hovermode: "x unified",
                shapes: [{ type: "line", x0: spot, x1: spot, y0: 0, y1: 1, yref: "paper", line: { color: t.spot, width: 1, dash: "dot" } }] }}
                config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />

              {(() => {
                const ts = expirations.map(exp => {
                  const atm = chain.filter(c => c.expiration_date === exp && c.contract_type === "call" && c.implied_volatility > 0)
                    .sort((a, b) => Math.abs(a.strike_price - spot) - Math.abs(b.strike_price - spot))[0];
                  if (!atm) return null;
                  const dte = calcDTE(exp);
                  const iv = atm.implied_volatility * 100;
                  const move = spot * atm.implied_volatility * Math.sqrt(dte / 365);
                  return { exp, dte, iv, move, movePct: (move / spot * 100) };
                }).filter(Boolean) as { exp: string; dte: number; iv: number; move: number; movePct: number }[];
                const shape = ts.length >= 2 ? (ts[ts.length - 1].iv > ts[0].iv * 1.02 ? "Contango" : ts[ts.length - 1].iv < ts[0].iv * 0.98 ? "Backwardation" : "Flat") : "N/A";
                return ts.length > 0 && (
                  <>
                    <div className="pt-3 border-t border-border">
                      <h3 className="text-xs font-semibold uppercase tracking-wide">Term structure — ATM IV across expirations</h3>
                      <p className="text-xs text-text-muted mt-0.5">Shape: <strong className={shape === "Contango" ? "text-gain" : shape === "Backwardation" ? "text-loss" : ""}>{shape}</strong>. Backwardation = near-term stress priced.</p>
                    </div>
                    <Plot data={[{
                      x: ts.map(r => `${new Date(r.exp + "T12:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric" })} (${r.dte}d)`),
                      y: ts.map(r => r.iv), type: "scatter" as const, mode: "lines+markers" as const,
                      line: { color: t.accent, width: 2 }, marker: { size: 8, color: t.accent },
                      hovertemplate: "%{x}<br>IV: %{y:.1f}%<extra></extra>",
                    }]} layout={{ height: 280, ...L, xaxis: { title: "Expiration", gridcolor: t.grid }, yaxis: { title: "ATM IV (%)", gridcolor: t.grid } }}
                      config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
                    <div className="overflow-x-auto">
                      <table className="data-table text-xs">
                        <thead><tr><th>Expiration</th><th>DTE</th><th>ATM IV</th><th>Expected Move ($)</th><th>Expected Move (%)</th></tr></thead>
                        <tbody>{ts.map((r, i) => (
                          <tr key={i}>
                            <td className="font-semibold">{new Date(r.exp + "T12:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric" })}</td>
                            <td className="font-data">{r.dte}d</td>
                            <td className="font-data">{r.iv.toFixed(1)}%</td>
                            <td className="font-data">±${r.move.toFixed(2)}</td>
                            <td className="font-data">±{r.movePct.toFixed(1)}%</td>
                          </tr>
                        ))}</tbody>
                      </table>
                    </div>
                  </>
                );
              })()}
            </div>
          )}

          {/* ═══ Tab 1: Positioning ═══ */}
          {activeTab === 1 && (
            <div className="card space-y-6">
              <div className="flex flex-wrap gap-6">
                <Metric label="P/C Volume Ratio" value={pcStats.pcVol.toFixed(2)} deltaType={pcStats.pcVol > pcStats.histMean ? "loss" : "gain"} />
                <Metric label="P/C OI Ratio" value={pcStats.pcOI.toFixed(2)} deltaType={pcStats.pcOI > 1 ? "loss" : "gain"} />
                <Metric label="Call Volume (≤45d)" value={pcStats.cv.toLocaleString()} />
                <Metric label="Put Volume (≤45d)" value={pcStats.pv.toLocaleString()} />
                <Metric label="Z-Score" value={`${pcStats.z > 0 ? "+" : ""}${pcStats.z.toFixed(1)}σ`} />
                <Metric label="Sentiment" value={pcStats.regime} />
              </div>
              <p className="text-xs text-text-muted">
                Baseline for {loadedTicker || "this ticker"}: {pcStats.histMean.toFixed(2)} ({pcStats.isIndex ? "index/ETF" : "single-name"}). Near-dated (≤{SENTIMENT_DTE_MAX} DTE) only — LEAPS hedges excluded.
              </p>

              {/* Gauge */}
              {(() => {
                const gLo = Math.max(0, pcStats.histMean - 3 * pcStats.histStd);
                const gHi = pcStats.histMean + 3 * pcStats.histStd;
                const gx = Array.from({ length: 200 }, (_, i) => gLo + i * ((gHi - gLo) / 199));
                const gy = gx.map(x => Math.exp(-0.5 * ((x - pcStats.histMean) / pcStats.histStd) ** 2));
                return <Plot data={[{ x: gx, y: gy, type: "scatter" as const, mode: "lines" as const, fill: "tozeroy" as const, fillcolor: t.accent + "25", line: { color: t.accent, width: 1 }, showlegend: false, hoverinfo: "skip" as const }]}
                  layout={{ height: 180, ...L, margin: { l: 30, r: 20, t: 10, b: 30 }, xaxis: { title: "P/C Ratio", gridcolor: t.grid }, yaxis: { visible: false },
                    shapes: [
                      { type: "line", x0: pcStats.pcVol, x1: pcStats.pcVol, y0: 0, y1: 1, yref: "paper", line: { color: t.spot, width: 3 } },
                      { type: "line", x0: pcStats.histMean - pcStats.histStd, x1: pcStats.histMean - pcStats.histStd, y0: 0, y1: 1, yref: "paper", line: { color: t.gain, width: 1, dash: "dot" } },
                      { type: "line", x0: pcStats.histMean + pcStats.histStd, x1: pcStats.histMean + pcStats.histStd, y0: 0, y1: 1, yref: "paper", line: { color: t.loss, width: 1, dash: "dot" } },
                    ], annotations: [{ x: pcStats.pcVol, y: 1.05, yref: "paper", text: `${pcStats.pcVol.toFixed(2)}`, showarrow: false, font: { size: 10, color: t.spot }, xshift: 0, yshift: 0 }] }}
                  config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />;
              })()}
              {pcStats.z > 1.5 && <div className="text-xs text-gain bg-gain-bg border border-gain/20 rounded-lg px-3 py-2">Contrarian Bullish: extreme put buying historically marks bottoms.</div>}
              {pcStats.z < -1.5 && <div className="text-xs text-loss bg-loss-bg border border-loss/20 rounded-lg px-3 py-2">Contrarian Bearish: low hedging = complacency warning.</div>}

              {/* Open Interest by strike (selected expiry) */}
              <div className="pt-3 border-t border-border">
                <h3 className="text-xs font-semibold uppercase tracking-wide">Open interest by strike — {selectedExp}</h3>
                <p className="text-xs text-text-muted mt-0.5">Heavy call OI above spot = resistance (dealer hedge); heavy put OI below = support.</p>
              </div>
              <Plot data={[
                { x: visCalls.map(c => c.strike_price), y: visCalls.map(c => c.open_interest), type: "bar" as const, name: "Call OI", marker: { color: t.gain }, opacity: 0.8 },
                { x: visPuts.map(c => c.strike_price), y: visPuts.map(c => c.open_interest), type: "bar" as const, name: "Put OI", marker: { color: t.loss }, opacity: 0.8 },
              ]} layout={{ height: 320, ...L, barmode: "group", xaxis: { title: "Strike", gridcolor: t.grid }, yaxis: { title: "Open Interest", gridcolor: t.grid },
                shapes: [{ type: "line", x0: spot, x1: spot, y0: 0, y1: 1, yref: "paper", line: { color: t.spot, width: 1, dash: "dot" } }] }}
                config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />

              {/* Max Pain */}
              {maxPain && (() => {
                const strikes = [...new Set(expChain.map(c => c.strike_price))].sort((a, b) => a - b).filter(s => s >= strikeLo && s <= strikeHi);
                const pains = strikes.map(ts2 => {
                  let pain = 0;
                  for (const c of expChain) {
                    if (c.contract_type === "call" && ts2 > c.strike_price) pain += (ts2 - c.strike_price) * c.open_interest * 100;
                    if (c.contract_type === "put" && ts2 < c.strike_price) pain += (c.strike_price - ts2) * c.open_interest * 100;
                  }
                  return pain;
                });
                const mpPct = (maxPain.strike - spot) / spot * 100;
                const mpDesc = Math.abs(mpPct) < 0.5 ? "pinned at spot" : `${mpPct > 0 ? "+" : ""}${mpPct.toFixed(1)}% from spot (${mpPct > 0 ? "upward pin" : "downward pin"})`;
                return (<>
                  <div className="pt-3 border-t border-border">
                    <h3 className="text-xs font-semibold uppercase tracking-wide">Max pain — {selectedExp}</h3>
                    <p className="text-xs text-text-muted mt-0.5">Strike where aggregate option holders lose the most at expiration: <strong>{mpDesc}</strong>.</p>
                  </div>
                  <Plot data={[{
                    x: strikes, y: pains, type: "scatter" as const, mode: "lines" as const,
                    line: { color: t.accent, width: 2 }, fill: "tozeroy" as const, fillcolor: t.accent + "15",
                    hovertemplate: "$%{x}: $%{y:,.0f}<extra></extra>",
                  }]} layout={{ height: 280, ...L, xaxis: { title: "Settlement Price", gridcolor: t.grid }, yaxis: { title: "Total Pain ($)", gridcolor: t.grid },
                    shapes: [
                      { type: "line", x0: spot, x1: spot, y0: 0, y1: 1, yref: "paper", line: { color: t.spot, width: 2, dash: "dot" } },
                      { type: "line", x0: maxPain.strike, x1: maxPain.strike, y0: 0, y1: 1, yref: "paper", line: { color: t.loss, width: 2, dash: "dash" } },
                    ],
                    annotations: [
                      { x: spot, y: 1, yref: "paper", text: "Spot", showarrow: false, font: { size: 9, color: t.spot } },
                      { x: maxPain.strike, y: 1, yref: "paper", text: "Max Pain", showarrow: false, font: { size: 9, color: t.loss } },
                    ] }}
                    config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
                </>);
              })()}

              {/* P/C ratio by expiration */}
              {(() => {
                const byExp = new Map<string, { cv: number; pv: number }>();
                chain.forEach(c => { const e = byExp.get(c.expiration_date) ?? { cv: 0, pv: 0 }; if (c.contract_type === "call") e.cv += c.volume; else e.pv += c.volume; byExp.set(c.expiration_date, e); });
                const exps = [...byExp.entries()].sort(([a], [b]) => a.localeCompare(b));
                const ratios = exps.map(([, v]) => v.cv > 0 ? v.pv / v.cv : 0);
                return exps.length > 0 && (<>
                  <div className="pt-3 border-t border-border">
                    <h3 className="text-xs font-semibold uppercase tracking-wide">P/C ratio by expiration</h3>
                    <p className="text-xs text-text-muted mt-0.5">Term structure of hedging demand. Red = put-heavy expiries; green = call-heavy.</p>
                  </div>
                  <Plot data={[{ x: exps.map(([e]) => e), y: ratios, type: "bar" as const, marker: { color: ratios.map(v => v > 1 ? t.loss : t.gain) } }]}
                    layout={{ height: 250, ...L, yaxis: { title: "P/C Ratio", gridcolor: t.grid }, shapes: [{ type: "line", y0: 1, y1: 1, x0: 0, x1: 1, xref: "paper", line: { color: t.muted, width: 1, dash: "dot" } }] }}
                    config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
                </>);
              })()}
            </div>
          )}

          {/* ═══ Tab 2: Flow ═══ */}
          {activeTab === 2 && (
            <div className="card space-y-6">
              <div className="flex gap-4 flex-wrap">
                <div><label className="metric-label">Unusual Min Vol</label>
                  <input type="number" value={minVol} onChange={e => setMinVol(Number(e.target.value))} className="w-24 px-2 py-1 border border-border rounded text-xs font-data bg-surface" /></div>
                <div><label className="metric-label">Unusual Min Vol/OI</label>
                  <input type="number" value={minRatio} onChange={e => setMinRatio(Number(e.target.value))} step={0.5} className="w-24 px-2 py-1 border border-border rounded text-xs font-data bg-surface" /></div>
                <div><label className="metric-label">Block Min Vol</label>
                  <input type="number" value={blockMinVol} onChange={e => setBlockMinVol(Number(e.target.value))} step={100} className="w-28 px-2 py-1 border border-border rounded text-xs font-data bg-surface" /></div>
                <div><label className="metric-label">Block Min Notional ($)</label>
                  <input type="number" value={blockMinNotional} onChange={e => setBlockMinNotional(Number(e.target.value))} step={10000} className="w-32 px-2 py-1 border border-border rounded text-xs font-data bg-surface" /></div>
              </div>

              <div>
                <h3 className="text-xs font-semibold uppercase tracking-wide">Unusual activity — Vol/OI spike</h3>
                <p className="text-xs text-text-muted mt-0.5">Contracts where today's volume dwarfs resting open interest — a signal of new-money entry.</p>
              </div>
              {unusual.length > 0 ? (<>
                <div className="flex flex-wrap gap-6">
                  <Metric label="Unusual Contracts" value={String(unusual.length)} />
                  <Metric label="Calls" value={String(unusual.filter(c => c.contract_type === "call").length)} />
                  <Metric label="Puts" value={String(unusual.filter(c => c.contract_type === "put").length)} />
                  <Metric label="Total Vol" value={unusual.reduce((s, c) => s + c.volume, 0).toLocaleString()} />
                </div>
                <div className="overflow-x-auto">
                  <table className="data-table text-xs">
                    <thead><tr><th>Strike</th><th>Type</th><th>Exp</th><th>Volume</th><th>OI</th><th>Vol/OI</th><th>IV</th><th>Delta</th><th>Last</th></tr></thead>
                    <tbody>
                      {unusual.slice(0, 25).map((c, i) => (
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
                {(() => {
                  const axMax = Math.max(
                    ...unusual.map(c => c.open_interest),
                    ...unusual.map(c => c.volume),
                    1,
                  );
                  return <Plot data={["call", "put"].map(ct => {
                    const sub = unusual.filter(c => c.contract_type === ct);
                    return { x: sub.map(c => c.open_interest), y: sub.map(c => c.volume), type: "scatter" as const, mode: "markers" as const,
                      name: ct === "call" ? "Calls" : "Puts",
                      marker: { color: ct === "call" ? t.gain : t.loss, size: sub.map(c => Math.min(c.vol_oi * 2, 50)), opacity: 0.7 },
                      text: sub.map(c => `$${c.strike_price}`), hovertemplate: "Strike: %{text}<br>Vol: %{y:,}<br>OI: %{x:,}<extra></extra>" };
                  })} layout={{ height: 350, ...L, xaxis: { title: "Open Interest", gridcolor: t.grid, range: [0, axMax * 1.05] }, yaxis: { title: "Volume", gridcolor: t.grid, range: [0, axMax * 1.05] }, hovermode: "closest",
                    shapes: [{ type: "line", x0: 0, y0: 0, x1: axMax, y1: axMax, line: { color: t.muted, width: 1, dash: "dot" } }] }}
                    config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />;
                })()}
                <p className="text-xs text-text-muted">Bubble size = Vol/OI ratio. Dotted line = 1:1 ratio — points above are unusual.</p>
              </>) : <p className="text-sm text-text-muted">No unusual activity with current filters.</p>}

              {/* Volume by strike */}
              {spot > 0 && (() => {
                const lo = spot * 0.9, hi = spot * 1.1;
                const cv = chain.filter(c => c.contract_type === "call" && c.strike_price >= lo && c.strike_price <= hi);
                const pv = chain.filter(c => c.contract_type === "put" && c.strike_price >= lo && c.strike_price <= hi);
                return (<>
                  <div className="pt-3 border-t border-border">
                    <h3 className="text-xs font-semibold uppercase tracking-wide">Volume by strike (±10% of spot, all expirations)</h3>
                    <p className="text-xs text-text-muted mt-0.5">Today's flow concentration. Dotted line = spot ${spot.toFixed(2)}.</p>
                  </div>
                  <Plot data={[
                    { x: cv.map(c => c.strike_price), y: cv.map(c => c.volume), type: "bar" as const, name: "Call Vol", marker: { color: t.gain } },
                    { x: pv.map(c => c.strike_price), y: pv.map(c => -c.volume), type: "bar" as const, name: "Put Vol", marker: { color: t.loss } },
                  ]} layout={{ height: 300, ...L, barmode: "overlay", yaxis: { title: "Volume", gridcolor: t.grid }, xaxis: { range: [lo, hi], gridcolor: t.grid },
                    shapes: [
                      { type: "line", x0: spot, x1: spot, y0: 0, y1: 1, yref: "paper", line: { color: t.spot, width: 1, dash: "dot" } },
                      { type: "line", y0: 0, y1: 0, x0: lo, x1: hi, line: { color: t.muted, width: 1 } },
                    ] }}
                    config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
                </>);
              })()}

              {/* Block trades */}
              <div className="pt-3 border-t border-border">
                <h3 className="text-xs font-semibold uppercase tracking-wide">Block trade detection</h3>
                <p className="text-xs text-text-muted mt-0.5">Institutional-size prints (vol × price × 100 ≥ ${blockMinNotional.toLocaleString()}).</p>
              </div>
              {blocks.length > 0 ? (<>
                <div className="flex flex-wrap gap-6">
                  <Metric label="Blocks" value={String(blocks.length)} />
                  <Metric label="Call Blocks" value={String(blocks.filter(b => b.contract_type === "call").length)} />
                  <Metric label="Put Blocks" value={String(blocks.filter(b => b.contract_type === "put").length)} />
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
                      <div className="metric-label">Institutional block flow bias</div>
                      <div className={`text-xl font-bold ${bias === "Bullish" ? "text-gain" : bias === "Bearish" ? "text-loss" : "text-warn"}`}>{bias}</div>
                      <div className="text-xs text-text-muted">Calls: ${cn.toLocaleString()} ({cp.toFixed(0)}%) | Puts: ${pn.toLocaleString()} ({(100 - cp).toFixed(0)}%)</div>
                    </div>
                  );
                })()}
                <div className="overflow-x-auto">
                  <table className="data-table text-xs">
                    <thead><tr><th>Strike</th><th>Type</th><th>Exp</th><th>Volume</th><th>OI</th><th>Price</th><th>Notional</th><th>IV</th><th>Delta</th></tr></thead>
                    <tbody>
                      {blocks.slice(0, 20).map((b, i) => (
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
              </>) : <p className="text-sm text-text-muted">No block trades — lower thresholds or try a more liquid ticker.</p>}
            </div>
          )}

          {/* ═══ Tab 3: Greeks ═══ */}
          {activeTab === 3 && (
            <div className="card space-y-6">
              {gexAgg ? (<>
                <div className="flex flex-wrap gap-6">
                  <Metric label="Net GEX (full chain)" value={gexAgg.totalGex.toLocaleString(undefined, { maximumFractionDigits: 0 })}
                    delta={gexAgg.totalGex > 0 ? "Long Gamma (Stable)" : "Short Gamma (Volatile)"} deltaType={gexAgg.totalGex > 0 ? "gain" : "loss"} />
                  <Metric label="Max GEX (Pin)" value={`$${gexAgg.maxGexStrike.toFixed(0)}`} />
                  <Metric label="Min GEX (Vol)" value={`$${gexAgg.minGexStrike.toFixed(0)}`} />
                  <Metric label="Spot" value={`$${spot.toFixed(2)}`} />
                </div>
                <div>
                  <h3 className="text-xs font-semibold uppercase tracking-wide">Net GEX by strike — aggregate</h3>
                  <p className="text-xs text-text-muted mt-0.5">Positive = dealers long gamma (suppresses moves toward pin). Negative = short gamma (amplifies moves).</p>
                </div>
                <Plot data={[{ x: gexAgg.net.map(n => n.strike), y: gexAgg.net.map(n => n.net), type: "bar" as const,
                  marker: { color: gexAgg.net.map(n => n.net > 0 ? t.gain : t.loss) },
                  hovertemplate: "$%{x}: %{y:,.0f}<extra></extra>" }]}
                  layout={{ height: 400, ...L, xaxis: { title: "Strike", gridcolor: t.grid }, yaxis: { title: "GEX", gridcolor: t.grid },
                    shapes: [
                      { type: "line", x0: spot, x1: spot, y0: 0, y1: 1, yref: "paper", line: { color: t.spot, width: 1, dash: "dot" } },
                      { type: "line", y0: 0, y1: 0, x0: 0, x1: 1, xref: "paper", line: { color: t.muted, width: 1 } },
                    ] }}
                  config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />

                <div className="pt-3 border-t border-border">
                  <h3 className="text-xs font-semibold uppercase tracking-wide">Call vs put GEX split</h3>
                  <p className="text-xs text-text-muted mt-0.5">Where call-side gamma concentrates (upside hedging) vs put-side (downside).</p>
                </div>
                <Plot data={[
                  { x: gexAgg.net.map(n => n.strike), y: gexAgg.net.map(n => n.call), type: "bar" as const, name: "Call GEX", marker: { color: t.gain }, opacity: 0.8 },
                  { x: gexAgg.net.map(n => n.strike), y: gexAgg.net.map(n => -n.put), type: "bar" as const, name: "Put GEX (inv)", marker: { color: t.loss }, opacity: 0.8 },
                ]} layout={{ height: 320, ...L, barmode: "overlay", yaxis: { title: "GEX", gridcolor: t.grid },
                  shapes: [
                    { type: "line", x0: spot, x1: spot, y0: 0, y1: 1, yref: "paper", line: { color: t.spot, width: 1, dash: "dot" } },
                    { type: "line", y0: 0, y1: 0, x0: 0, x1: 1, xref: "paper", line: { color: t.muted, width: 1 } },
                  ] }}
                  config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
              </>) : <p className="text-sm text-text-muted">No gamma data within ±15% of spot.</p>}

              {/* Delta Heatmap */}
              <div className="pt-3 border-t border-border">
                <h3 className="text-xs font-semibold uppercase tracking-wide">Call delta heatmap — strike × expiration</h3>
                <p className="text-xs text-text-muted mt-0.5">Green = deep ITM (delta near 1); red = OTM (delta near 0). Spot line marked.</p>
              </div>
              {(() => {
                const greekExps = expirations.slice(0, 8);
                const greekLabels = greekExps.map(e => {
                  const d = new Date(e + "T12:00:00");
                  return `${d.toLocaleDateString("en-US", { month: "short", day: "numeric" })} (${calcDTE(e)}d)`;
                });
                const strikes = [...new Set(chain.filter(c => c.strike_price >= strikeLo && c.strike_price <= strikeHi).map(c => c.strike_price))].sort((a, b) => a - b);
                const z = greekExps.map(exp => strikes.map(k => {
                  const c = chain.find(c => c.expiration_date === exp && c.strike_price === k && c.contract_type === "call");
                  return c ? c.delta : null;
                }));
                return (
                  <Plot data={[{
                    type: "heatmap" as const, x: strikes, y: greekLabels, z,
                    colorscale: [[0, t.loss], [0.5, t.grid], [1, t.gain]], zmid: 0.5,
                    colorbar: { title: { text: "Delta", font: { size: 9 } }, thickness: 12 },
                    hovertemplate: "$%{x} %{y}<br>Delta: %{z:.3f}<extra></extra>",
                  }]} layout={{ height: 380, ...L, margin: { l: 110, r: 20, t: 10, b: 50 },
                    xaxis: { title: "Strike", gridcolor: t.grid },
                    shapes: [{ type: "line", x0: spot, x1: spot, y0: 0, y1: 1, yref: "paper", line: { color: t.spot, width: 2, dash: "dot" } }] }}
                    config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
                );
              })()}
            </div>
          )}

          {/* ═══ Tab 4: OI Changes (historical) ═══ */}
          {activeTab === 4 && <OIChangesPanel ticker={loadedTicker} themeKeys={t} baseLayout={L} />}

          {/* ═══ Tab 5: Chain ═══ */}
          {activeTab === 5 && (
            <div className="card">
              <p className="text-xs text-text-muted mb-3">Full chain for {selectedExp}. ATM row highlighted. Strikes filtered to ±15% of spot.</p>
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
        </>
      )}

      {chain.length === 0 && !load.isPending && !load.isSuccess && !load.isError && (
        <div className="card text-center py-8 text-sm text-text-muted">
          Enter a ticker above and click <strong>Load Chain</strong> to see volatility skew, positioning, flow, Greeks, and full chain.
        </div>
      )}
      {chain.length === 0 && !load.isPending && load.isSuccess && <div className="card text-center py-8 text-text-muted">No chain data returned for {loadedTicker}.</div>}
      {load.isError && <div className="card border-loss/30 bg-loss-bg text-loss text-sm">Failed: {(load.error as Error).message}</div>}
    </div>
  );
}


// ─────────────────────────────────────────────────────────────
// OI Changes panel — reads from the accumulated OI history table
// populated by the daily Cloud Scheduler job.
// ─────────────────────────────────────────────────────────────

type ChartTheme = ReturnType<typeof getChartTheme>;
type BaseLayout = ReturnType<typeof getBaseLayout>;

function OIChangesPanel({ ticker, themeKeys: t, baseLayout: L }: { ticker: string; themeKeys: ChartTheme; baseLayout: BaseLayout }) {
  const [lookback, setLookback] = useState(10);
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["oi-history", ticker, lookback],
    queryFn: () => fetchOIHistory(ticker, lookback),
    enabled: !!ticker,
    staleTime: 60_000 * 15,
  });

  if (!ticker) {
    return <div className="card text-center py-8 text-sm text-text-muted">Load a chain first — OI history is keyed off the loaded ticker.</div>;
  }
  if (isLoading) {
    return <div className="card text-center py-8"><div className="inline-block w-6 h-6 border-2 border-accent border-t-transparent rounded-full animate-spin" /><p className="text-xs text-text-muted mt-3">Loading OI history...</p></div>;
  }
  if (isError) {
    return <div className="card border-loss/30 bg-loss-bg text-loss text-sm p-4">Failed to load OI history: {(error as Error)?.message}</div>;
  }
  if (!data || data.n_days_captured < 2) {
    return (
      <div className="card space-y-3 py-6 text-center">
        <div className="text-sm font-semibold">Accumulating data</div>
        <p className="text-xs text-text-muted max-w-md mx-auto">
          {data?.n_days_captured === 0
            ? `No history captured yet for ${ticker}. The daily worker writes a snapshot each trading day at 4:30 PM ET — check back after 2+ weekdays of captures.`
            : `Only ${data.n_days_captured} day of history so far. Need at least 2 to compute change. The worker captures daily post-close.`}
        </p>
      </div>
    );
  }

  const summary = data.summary!;
  return (
    <div className="card space-y-6">
      <div className="flex items-center gap-3 flex-wrap">
        <label className="metric-label">Lookback (days)</label>
        <select value={lookback} onChange={e => setLookback(Number(e.target.value))}
          className="px-2 py-1 border border-border rounded text-xs font-data bg-surface">
          {[5, 10, 20, 30, 60].map(n => <option key={n} value={n}>{n}</option>)}
        </select>
        <span className="text-xs text-text-muted">
          {data.n_days_captured} days shown · {data.total_days_available} total captured · first: {data.dates[0]} · last: {data.dates[data.dates.length - 1]}
        </span>
      </div>

      {/* Daily net OI */}
      <div>
        <h3 className="text-xs font-semibold uppercase tracking-wide">Aggregate OI over time</h3>
        <p className="text-xs text-text-muted mt-0.5">Total call vs put OI across all tracked strikes, day by day.</p>
      </div>
      <Plot data={[
        { x: summary.daily_net.map(d => d.date), y: summary.daily_net.map(d => d.call_oi), type: "scatter" as const, mode: "lines+markers" as const, name: "Call OI", line: { color: t.gain, width: 2 }, marker: { size: 6 } },
        { x: summary.daily_net.map(d => d.date), y: summary.daily_net.map(d => d.put_oi), type: "scatter" as const, mode: "lines+markers" as const, name: "Put OI", line: { color: t.loss, width: 2 }, marker: { size: 6 } },
      ]} layout={{ height: 300, ...L, xaxis: { title: "Date", gridcolor: t.grid }, yaxis: { title: "Open Interest", gridcolor: t.grid } }}
        config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />

      {/* Biggest builds */}
      <div className="pt-3 border-t border-border">
        <h3 className="text-xs font-semibold uppercase tracking-wide">Biggest OI builds (net gain over window)</h3>
        <p className="text-xs text-text-muted mt-0.5">New positioning — accumulation since {data.dates[0]}.</p>
      </div>
      {summary.biggest_builds.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="data-table text-xs">
            <thead><tr><th>Strike</th><th>Type</th><th>Exp</th><th>OI {data.dates[0]}</th><th>OI {data.dates[data.dates.length - 1]}</th><th>Δ OI</th><th>% Change</th></tr></thead>
            <tbody>
              {summary.biggest_builds.map((s, i) => (
                <tr key={i}>
                  <td className="font-data">${s.strike.toFixed(0)}</td>
                  <td><span className={`badge ${s.type === "call" ? "badge-gain" : "badge-loss"}`}>{s.type}</span></td>
                  <td>{s.exp}</td>
                  <td className="font-data">{s.first.toLocaleString()}</td>
                  <td className="font-data">{s.last.toLocaleString()}</td>
                  <td className="font-data font-semibold text-gain">+{s.delta_abs.toLocaleString()}</td>
                  <td className="font-data">{s.delta_pct !== null ? `+${s.delta_pct.toFixed(0)}%` : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : <p className="text-xs text-text-muted">No meaningful OI growth over this window.</p>}

      {/* Biggest unwinds */}
      <div className="pt-3 border-t border-border">
        <h3 className="text-xs font-semibold uppercase tracking-wide">Biggest OI unwinds (net loss over window)</h3>
        <p className="text-xs text-text-muted mt-0.5">Positioning being closed out — distribution of old paper.</p>
      </div>
      {summary.biggest_unwinds.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="data-table text-xs">
            <thead><tr><th>Strike</th><th>Type</th><th>Exp</th><th>OI {data.dates[0]}</th><th>OI {data.dates[data.dates.length - 1]}</th><th>Δ OI</th><th>% Change</th></tr></thead>
            <tbody>
              {summary.biggest_unwinds.map((s, i) => (
                <tr key={i}>
                  <td className="font-data">${s.strike.toFixed(0)}</td>
                  <td><span className={`badge ${s.type === "call" ? "badge-gain" : "badge-loss"}`}>{s.type}</span></td>
                  <td>{s.exp}</td>
                  <td className="font-data">{s.first.toLocaleString()}</td>
                  <td className="font-data">{s.last.toLocaleString()}</td>
                  <td className="font-data font-semibold text-loss">{s.delta_abs.toLocaleString()}</td>
                  <td className="font-data">{s.delta_pct !== null ? `${s.delta_pct.toFixed(0)}%` : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : <p className="text-xs text-text-muted">No notable unwinds over this window.</p>}
    </div>
  );
}

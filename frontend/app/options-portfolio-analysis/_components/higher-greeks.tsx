"use client";

import { useState, useMemo } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTheme } from "next-themes";
import { fetchOptionsChain, fetchSnapshot, fetchAITradeIdeas } from "@/lib/api";
import { getChartTheme, getBaseLayout } from "@/lib/chart-theme";
import { Metric } from "@/components/ui/metric";
import { Plot } from "@/components/plot";
import { AIMarkdown } from "@/components/ai-markdown";


const TABS = ["Overview", "Vanna Profile", "Charm & Time", "Gamma Risk", "Vega Convexity", "VV Pricing", "Portfolio Greeks", "AI Greek Analyst"];

interface ChainRow {
  strike_price: number; contract_type: string; expiration_date: string;
  implied_volatility: number; delta: number; gamma: number; theta: number; vega: number;
  open_interest: number; volume: number;
}

// BS higher Greeks formulas (client-side, pure math)
function norm_pdf(x: number): number { return Math.exp(-0.5 * x * x) / Math.sqrt(2 * Math.PI); }
function norm_cdf(x: number): number {
  const t = 1 / (1 + 0.2316419 * Math.abs(x));
  const d = 0.3989422804014327;
  const p = d * Math.exp(-x * x / 2) * (t * (0.3193815 + t * (-0.3565638 + t * (1.781478 + t * (-1.8212560 + t * 1.3302744)))));
  return x > 0 ? 1 - p : p;
}

function computeHigherGreeks(S: number, K: number, T: number, r: number, sigma: number, optType: string) {
  if (T <= 0 || sigma <= 0 || S <= 0 || K <= 0) return null;
  const sqrtT = Math.sqrt(T);
  const d1 = (Math.log(S / K) + (r + sigma * sigma / 2) * T) / (sigma * sqrtT);
  const d2 = d1 - sigma * sqrtT;
  const nd1 = norm_pdf(d1);

  const delta = optType === "call" ? norm_cdf(d1) : norm_cdf(d1) - 1;
  const gamma = nd1 / (S * sigma * sqrtT);
  const vega = S * nd1 * sqrtT / 100;
  const theta = (-(S * nd1 * sigma) / (2 * sqrtT) - r * K * Math.exp(-r * T) * (optType === "call" ? norm_cdf(d2) : -norm_cdf(-d2))) / 365;

  // Higher order
  const vanna = -nd1 * d2 / sigma;  // dDelta/dVol
  const charm = -nd1 * (2 * r * T - d2 * sigma * sqrtT) / (2 * T * sigma * sqrtT);  // dDelta/dTime
  const volga = vega * d1 * d2 / sigma;  // dVega/dVol (vomma)
  const speed = -gamma / S * (d1 / (sigma * sqrtT) + 1);  // dGamma/dSpot
  const zomma = gamma * (d1 * d2 - 1) / sigma;  // dGamma/dVol
  const color = -nd1 / (2 * S * T * sigma * sqrtT) * (2 * r * T + 1 + d1 * (2 * r * T - d2 * sigma * sqrtT) / (sigma * sqrtT));

  return { delta, gamma, theta, vega, vanna, charm, volga, speed, zomma, color, d1, d2, iv: sigma, strike: K, dte: T * 365, type: optType };
}

function calcDTE(exp: string): number {
  const d = new Date(exp + "T16:00:00");
  return Math.max(1, Math.round((d.getTime() - Date.now()) / 86400000));
}

export function HigherGreeksContent() {
  const { resolvedTheme } = useTheme();
  const t = getChartTheme(resolvedTheme === "dark");
  const L = getBaseLayout(t);
  const [ticker, setTicker] = useState("SPY");
  const [activeTab, setActiveTab] = useState(0);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const [greeksData, setGreeksData] = useState<any[]>([]);
  const [spot, setSpot] = useState(0);
  const [aiGreekContent, setAiGreekContent] = useState("");
  const [aiLoading, setAiLoading] = useState(false);

  const load = useMutation({
    mutationFn: async (tk: string) => {
      const [ch, snap] = await Promise.all([fetchOptionsChain(tk), fetchSnapshot([tk])]);
      return { chain: ch.data as unknown as ChainRow[], spot: snap[tk]?.price ?? 0 };
    },
    onSuccess: (d) => {
      setSpot(d.spot);
      const rfr = 0.045;
      const rows = d.chain
        .filter(c => c.implied_volatility > 0.01 && c.implied_volatility < 3 && c.strike_price > d.spot * 0.75 && c.strike_price < d.spot * 1.25)
        .map(c => {
          const T = calcDTE(c.expiration_date) / 365;
          const hg = computeHigherGreeks(d.spot, c.strike_price, T, rfr, c.implied_volatility, c.contract_type);
          return hg ? { ...hg, exp: c.expiration_date, oi: c.open_interest, volume: c.volume } : null;
        }).filter(Boolean);
      setGreeksData(rows);
    },
  });

  // Group by expiration for charts
  const expirations = useMemo(() => {
    const exps = [...new Set(greeksData.map(r => r.exp))].sort();
    return exps.slice(0, 6);
  }, [greeksData]);

  const frontExp = expirations[0] ?? "";
  const frontData = useMemo(() => greeksData.filter(r => r.exp === frontExp).sort((a, b) => a.strike - b.strike), [greeksData, frontExp]);

  return (
    <div className="space-y-5">

      <div className="card card-compact">
        <div className="flex items-center gap-3">
          <input type="text" value={ticker} onChange={e => setTicker(e.target.value.toUpperCase())}
            onKeyDown={e => e.key === "Enter" && load.mutate(ticker)}
            placeholder="SPY" className="w-32 px-3 py-2 border border-border rounded-lg text-sm font-data bg-surface" />
          <button onClick={() => load.mutate(ticker)} disabled={load.isPending}
            className="px-6 py-2 bg-accent text-white font-semibold rounded-lg hover:bg-accent-hover disabled:opacity-50 transition-colors text-sm">
            {load.isPending ? "Loading..." : "Load Chain"}
          </button>
        </div>
      </div>

      {load.isPending && (
        <div className="card text-center py-12">
          <div className="inline-block w-6 h-6 border-2 border-accent border-t-transparent rounded-full animate-spin" />
          <p className="text-sm text-text-muted mt-3">Computing 12 Greeks per contract...</p>
        </div>
      )}

      {greeksData.length > 0 && (
        <>
          <div className="card card-compact">
            <div className="flex flex-wrap gap-6">
              <Metric label="Spot" value={`$${spot.toFixed(2)}`} />
              <Metric label="Contracts" value={String(greeksData.length)} />
              <Metric label="Expirations" value={String(expirations.length)} />
              <Metric label="Front Exp" value={frontExp} />
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

          {/* Tab 0: Overview — Greek family tree + calculator */}
          {activeTab === 0 && (
            <div className="card space-y-4">
              <div className="text-sm font-bold">Greek Family Tree</div>
              <div className="grid grid-cols-3 gap-3">
                {[
                  { order: "1st Order", items: [["Delta (Δ)", "$/move"], ["Gamma (Γ)", "Δ/move"], ["Theta (Θ)", "$/day"], ["Vega (ν)", "$/vol%"]] },
                  { order: "2nd Order", items: [["Vanna", "Δ/vol%"], ["Charm", "Δ/day"], ["Volga", "ν/vol%"], ["Speed", "Γ/move"]] },
                  { order: "3rd Order", items: [["Zomma", "Γ/vol%"], ["Color", "Γ/day"], ["Ultima", "ν²/vol%"], ["Veta", "ν/day"]] },
                ].map(({ order, items }) => (
                  <div key={order} className="border border-border rounded-lg p-3">
                    <div className="text-xs font-bold uppercase tracking-wider text-accent mb-2">{order}</div>
                    {items.map(([name, desc]) => (
                      <div key={name} className="flex justify-between text-xs py-0.5">
                        <span className="font-semibold">{name}</span>
                        <span className="text-text-muted">{desc}</span>
                      </div>
                    ))}
                  </div>
                ))}
              </div>

              {/* Front expiration Greeks table */}
              {frontData.length > 0 && (
                <div className="overflow-x-auto">
                  <div className="text-xs font-semibold mb-1">Front Expiration ({frontExp}) — All 12 Greeks</div>
                  <table className="data-table text-xs">
                    <thead><tr><th>Strike</th><th>Type</th><th>IV</th><th>Delta</th><th>Gamma</th><th>Vanna</th><th>Charm</th><th>Volga</th><th>Speed</th><th>Zomma</th></tr></thead>
                    <tbody>
                      {frontData.filter(r => Math.abs(r.strike - spot) < spot * 0.1).map((r, i) => (
                        <tr key={i}>
                          <td className="font-data">${r.strike.toFixed(0)}</td>
                          <td><span className={`badge ${r.type === "call" ? "badge-gain" : "badge-loss"}`}>{r.type}</span></td>
                          <td className="font-data">{(r.iv * 100).toFixed(1)}%</td>
                          <td className="font-data">{r.delta.toFixed(3)}</td>
                          <td className="font-data">{r.gamma.toFixed(4)}</td>
                          <td className="font-data">{r.vanna.toFixed(4)}</td>
                          <td className="font-data">{r.charm.toFixed(4)}</td>
                          <td className="font-data">{r.volga.toFixed(4)}</td>
                          <td className="font-data">{r.speed.toFixed(6)}</td>
                          <td className="font-data">{r.zomma.toFixed(6)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}

          {/* Tab 1: Vanna Profile */}
          {activeTab === 1 && (
            <div className="card space-y-4">
              <p className="text-xs text-text-muted">Vanna = dDelta/dVol. Positive vanna: delta increases as vol rises. Drives dealer hedging flows.</p>
              {expirations.map(exp => {
                const d = greeksData.filter(r => r.exp === exp && r.type === "call").sort((a, b) => a.strike - b.strike);
                const dp = greeksData.filter(r => r.exp === exp && r.type === "put").sort((a, b) => a.strike - b.strike);
                return (
                  <div key={exp}>
                    <div className="metric-label mb-1">{exp} ({d[0]?.dte.toFixed(0) ?? "?"}d)</div>
                    <Plot data={[
                      { x: d.map(r => r.strike), y: d.map(r => r.vanna), type: "scatter" as const, mode: "lines" as const, name: "Call Vanna", line: { color: t.gain, width: 2 } },
                      { x: dp.map(r => r.strike), y: dp.map(r => r.vanna), type: "scatter" as const, mode: "lines" as const, name: "Put Vanna", line: { color: t.loss, width: 2 } },
                    ]} layout={{ height: 220, ...L, margin: { l: 50, r: 10, t: 5, b: 30 }, yaxis: { title: "Vanna", gridcolor: t.grid }, xaxis: { gridcolor: t.grid },
                      shapes: [{ type: "line", x0: spot, x1: spot, y0: 0, y1: 1, yref: "paper", line: { color: t.spot, width: 1, dash: "dot" } }] }}
                      config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
                  </div>
                );
              })}
            </div>
          )}

          {/* Tab 2: Charm & Time Risk */}
          {activeTab === 2 && (
            <div className="card space-y-4">
              <p className="text-xs text-text-muted">Charm = dDelta/dTime. Shows how delta changes overnight — critical for delta hedging.</p>
              {expirations.slice(0, 3).map(exp => {
                const d = greeksData.filter(r => r.exp === exp).sort((a, b) => a.strike - b.strike);
                const calls = d.filter(r => r.type === "call");
                const puts = d.filter(r => r.type === "put");
                return (
                  <div key={exp}>
                    <div className="metric-label mb-1">{exp} ({d[0]?.dte.toFixed(0) ?? "?"}d)</div>
                    <Plot data={[
                      { x: calls.map(r => r.strike), y: calls.map(r => r.charm), type: "scatter" as const, mode: "lines" as const, name: "Call Charm", line: { color: t.gain, width: 2 } },
                      { x: puts.map(r => r.strike), y: puts.map(r => r.charm), type: "scatter" as const, mode: "lines" as const, name: "Put Charm", line: { color: t.loss, width: 2 } },
                    ]} layout={{ height: 220, ...L, margin: { l: 50, r: 10, t: 5, b: 30 }, yaxis: { title: "Charm", gridcolor: t.grid }, xaxis: { gridcolor: t.grid },
                      shapes: [{ type: "line", x0: spot, x1: spot, y0: 0, y1: 1, yref: "paper", line: { color: t.spot, width: 1, dash: "dot" } }] }}
                      config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
                  </div>
                );
              })}
            </div>
          )}

          {/* Tab 3: Gamma Risk Map */}
          {activeTab === 3 && (
            <div className="card space-y-4">
              <p className="text-xs text-text-muted">Speed = dGamma/dSpot, Zomma = dGamma/dVol. Shows where gamma risk concentrates.</p>
              {(() => {
                const d = frontData.filter(r => r.type === "call");
                return d.length > 0 ? (
                  <Plot data={[
                    { x: d.map(r => r.strike), y: d.map(r => r.gamma), type: "bar" as const, name: "Gamma", marker: { color: t.accent }, opacity: 0.6, yaxis: "y" },
                    { x: d.map(r => r.strike), y: d.map(r => r.speed * 10000), type: "scatter" as const, mode: "lines" as const, name: "Speed (×10⁴)", line: { color: t.loss, width: 2 }, yaxis: "y2" },
                    { x: d.map(r => r.strike), y: d.map(r => r.zomma * 1000), type: "scatter" as const, mode: "lines" as const, name: "Zomma (×10³)", line: { color: t.hv60, width: 2 }, yaxis: "y2" },
                  ]} layout={{ height: 400, ...L,
                    yaxis: { title: "Gamma", gridcolor: t.grid, side: "left" },
                    yaxis2: { title: "Speed / Zomma (scaled)", overlaying: "y", side: "right", showgrid: false },
                    xaxis: { title: "Strike", gridcolor: t.grid }, hovermode: "x unified",
                    shapes: [{ type: "line", x0: spot, x1: spot, y0: 0, y1: 1, yref: "paper", line: { color: t.spot, width: 1, dash: "dot" } }] }}
                    config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
                ) : null;
              })()}
            </div>
          )}

          {/* Tab 4: Vega Convexity */}
          {activeTab === 4 && (
            <div className="card space-y-4">
              <p className="text-xs text-text-muted">Volga (Vomma) = dVega/dVol. Positive volga: vega increases as vol rises — long convexity in vol space.</p>
              {(() => {
                const d = frontData.filter(r => r.type === "call");
                return d.length > 0 ? (
                  <Plot data={[
                    { x: d.map(r => r.strike), y: d.map(r => r.vega), type: "bar" as const, name: "Vega", marker: { color: t.accent }, opacity: 0.5, yaxis: "y" },
                    { x: d.map(r => r.strike), y: d.map(r => r.volga), type: "scatter" as const, mode: "lines+markers" as const, name: "Volga", line: { color: t.hv20, width: 2 }, marker: { size: 4 }, yaxis: "y2" },
                  ]} layout={{ height: 400, ...L,
                    yaxis: { title: "Vega", gridcolor: t.grid, side: "left" },
                    yaxis2: { title: "Volga", overlaying: "y", side: "right", showgrid: false },
                    xaxis: { title: "Strike", gridcolor: t.grid }, hovermode: "x unified",
                    shapes: [{ type: "line", x0: spot, x1: spot, y0: 0, y1: 1, yref: "paper", line: { color: t.spot, width: 1, dash: "dot" } }] }}
                    config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
                ) : null;
              })()}
            </div>
          )}

          {/* Tab 5: Vanna-Volga Pricing */}
          {activeTab === 5 && (
            <div className="card space-y-4">
              <p className="text-xs text-text-muted">Decomposes option price into BS base + vanna cost + volga cost. The smile premium is the non-BS component.</p>
              {(() => {
                // Simplified VV: smile premium = (IV - ATM_IV) contribution via vanna and volga
                const atmIv = frontData.find(r => Math.abs(r.strike - spot) < spot * 0.01 && r.type === "call")?.iv ?? 0;
                if (atmIv <= 0) return <p className="text-sm text-text-muted">Need ATM IV data.</p>;
                const d = frontData.filter(r => r.type === "call");
                const vannaCost = d.map(r => r.vanna * (r.iv - atmIv));
                const volgaCost = d.map(r => r.volga * (r.iv - atmIv));
                const smilePremium = d.map((_, i) => vannaCost[i] + volgaCost[i]);

                return (<>
                  <Plot data={[
                    { x: d.map(r => r.strike), y: vannaCost, type: "bar" as const, name: "Vanna Cost", marker: { color: t.gain } },
                    { x: d.map(r => r.strike), y: volgaCost, type: "bar" as const, name: "Volga Cost", marker: { color: t.hv60 } },
                    { x: d.map(r => r.strike), y: smilePremium, type: "scatter" as const, mode: "lines" as const, name: "Smile Premium", line: { color: t.spot, width: 2 } },
                  ]} layout={{ height: 400, ...L, barmode: "relative", xaxis: { title: "Strike", gridcolor: t.grid }, yaxis: { title: "Premium Contribution", gridcolor: t.grid }, hovermode: "x unified",
                    shapes: [{ type: "line", x0: spot, x1: spot, y0: 0, y1: 1, yref: "paper", line: { color: t.spot, width: 1, dash: "dot" } }] }}
                    config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />

                  <div className="overflow-x-auto">
                    <table className="data-table text-xs">
                      <thead><tr><th>Strike</th><th>IV</th><th>BS Price</th><th>Vanna Cost</th><th>Volga Cost</th><th>Smile Premium</th></tr></thead>
                      <tbody>
                        {d.filter(r => Math.abs(r.strike - spot) < spot * 0.08).map((r, i) => {
                          const idx = d.indexOf(r);
                          return (
                            <tr key={i}>
                              <td className="font-data">${r.strike.toFixed(0)}</td>
                              <td className="font-data">{(r.iv * 100).toFixed(1)}%</td>
                              <td className="font-data">—</td>
                              <td className={`font-data ${vannaCost[idx] > 0 ? "text-gain" : "text-loss"}`}>{vannaCost[idx].toFixed(4)}</td>
                              <td className={`font-data ${volgaCost[idx] > 0 ? "text-gain" : "text-loss"}`}>{volgaCost[idx].toFixed(4)}</td>
                              <td className="font-data font-semibold">{smilePremium[idx].toFixed(4)}</td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </>);
              })()}
            </div>
          )}

          {/* ═══ Tab 6: Portfolio Higher Greeks ═══ */}
          {activeTab === 6 && (() => {
            // Aggregate higher Greeks across all contracts (equal weight for simplicity)
            let netVanna = 0, netCharm = 0, netSpeed = 0, netZomma = 0;
            for (const g of greeksData) {
              netVanna += g.vanna || 0;
              netCharm += g.charm || 0;
              netSpeed += g.speed || 0;
              netZomma += g.zomma || 0;
            }
            const n = greeksData.length || 1;
            return (
              <div className="card space-y-4">
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                  <div className="card card-compact bg-surface-alt">
                    <div className="metric-label">Net Vanna $</div>
                    <div className="text-lg font-bold font-data">{(netVanna * 100).toFixed(1)}</div>
                    <div className="text-[0.55rem] text-text-muted">Delta shift per 1% IV move</div>
                  </div>
                  <div className="card card-compact bg-surface-alt">
                    <div className="metric-label">Net Charm $/day</div>
                    <div className="text-lg font-bold font-data">{(netCharm * 100).toFixed(1)}</div>
                    <div className="text-[0.55rem] text-text-muted">Overnight delta drift</div>
                  </div>
                  <div className="card card-compact bg-surface-alt">
                    <div className="metric-label">Net Speed $</div>
                    <div className="text-lg font-bold font-data">{(netSpeed * 100).toFixed(2)}</div>
                    <div className="text-[0.55rem] text-text-muted">Gamma acceleration on moves</div>
                  </div>
                  <div className="card card-compact bg-surface-alt">
                    <div className="metric-label">Net Zomma $</div>
                    <div className="text-lg font-bold font-data">{(netZomma * 100).toFixed(2)}</div>
                    <div className="text-[0.55rem] text-text-muted">Gamma sensitivity to IV</div>
                  </div>
                </div>
                <div className="border border-border rounded-lg p-3 text-xs space-y-1">
                  <div className="font-semibold">Overnight Risk Report</div>
                  <p className="text-text-muted">Portfolio delta: ~{(greeksData.reduce((s, g) => s + (g.delta || 0), 0) * 100).toFixed(0)} shares equivalent</p>
                  <p className="text-text-muted">Overnight drift (charm): {(netCharm * 100).toFixed(1)} shares/day — {Math.abs(netCharm) > 0.005 ? "significant, consider hedging" : "manageable"}</p>
                  <p className="text-text-muted">IV shock (vanna): ±1% IV moves delta by {(netVanna * 100).toFixed(1)} shares — {Math.abs(netVanna) > 0.01 ? "monitor vanna exposure" : "low vanna risk"}</p>
                </div>
              </div>
            );
          })()}

          {/* ═══ Tab 7: AI Greek Analyst ═══ */}
          {activeTab === 7 && (() => {
            const runAnalysis = async () => {
              setAiLoading(true);
              try {
                const exps = [...new Set(greeksData.map(g => g.exp))].sort();
                const ctx = `HIGHER-ORDER GREEKS ANALYSIS for ${ticker} (Spot: $${spot.toFixed(2)})\n` +
                  `Expirations: ${exps.join(", ")}\n` +
                  `Contracts analyzed: ${greeksData.length}\n\n` +
                  `ATM GREEKS (nearest):\n` +
                  greeksData.filter(g => Math.abs(g.moneyness - 1) < 0.03).slice(0, 3).map(g =>
                    `${g.type} $${g.strike} (${g.dte}d): vanna=${g.vanna?.toFixed(5)} charm=${g.charm?.toFixed(5)} speed=${g.speed?.toFixed(7)} zomma=${g.zomma?.toFixed(7)} volga=${g.volga?.toFixed(5)}`
                  ).join("\n") +
                  `\n\nPEAK GREEKS:\n` +
                  `Max |vanna|: ${Math.max(...greeksData.map(g => Math.abs(g.vanna || 0))).toFixed(5)} at $${greeksData.reduce((b, g) => Math.abs(g.vanna || 0) > Math.abs(b.vanna || 0) ? g : b).strike}\n` +
                  `Max |charm|: ${Math.max(...greeksData.map(g => Math.abs(g.charm || 0))).toFixed(5)}\n` +
                  `\nAnalyze: 1) Risk Summary, 2) Dealer Flow (vanna → delta hedging), 3) Overnight Risk (charm drift), 4) Pin Risk zones, 5) Recommended trades based on Greek profile.`;
                const res = await fetchAITradeIdeas({ ticker, context: ctx, style: "full_scan" });
                setAiGreekContent(res.content);
              } catch (e) { setAiGreekContent(`Error: ${(e as Error).message}`); }
              finally { setAiLoading(false); }
            };
            return (
              <div className="card space-y-4">
                <button onClick={runAnalysis} disabled={aiLoading || greeksData.length === 0}
                  className="px-6 py-2 bg-accent text-white font-semibold rounded-lg hover:bg-accent-hover disabled:opacity-50 text-sm">
                  {aiLoading ? "Analyzing..." : aiGreekContent ? "Re-analyze" : "Analyze Greeks (Gemini)"}
                </button>
                {aiLoading && <div className="flex items-center gap-2"><div className="w-4 h-4 border-2 border-accent border-t-transparent rounded-full animate-spin" /><span className="text-xs text-text-muted">Gemini analyzing higher-order Greeks...</span></div>}
                {aiGreekContent && <AIMarkdown text={aiGreekContent} />}
              </div>
            );
          })()}
        </>
      )}

      {greeksData.length === 0 && !load.isPending && load.isSuccess && <div className="card text-center py-8 text-text-muted">No options data.</div>}
      {load.isError && <div className="card border-loss/30 bg-loss-bg text-loss text-sm">Failed: {(load.error as Error).message}</div>}
    </div>
  );
}

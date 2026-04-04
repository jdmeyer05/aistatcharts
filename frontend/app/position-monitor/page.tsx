"use client";

import { useState, useRef } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { fetchRobinhoodPositions, fetchHoldingsResearch, type RHStock, type RHSpread, type RHPortfolioGreeks, type HoldingResearch } from "@/lib/api";

// ── P/L at expiration from actual legs ──
function computeSpreadPL(spread: RHSpread) {
  const legs = spread.legs;
  if (!legs.length || !spread.stock_price) return null;

  // Net premium: sum of (short credits - long debits) per share
  const netPremiumPerShare = legs.reduce((sum, l) => {
    return sum + l.avg_price * (l.direction === "short" ? 1 : -1);
  }, 0);

  // Price range: extend proportionally beyond strikes
  const strikes = legs.map(l => l.strike);
  const strikeMin = Math.min(...strikes);
  const strikeMax = Math.max(...strikes);
  const strikeSpan = strikeMax - strikeMin || 1;
  // Extend by 3x the strike width on each side (shows full P/L shape)
  const extend = Math.max(strikeSpan * 3, strikeMin * 0.02);
  const lo = strikeMin - extend;
  const hi = strikeMax + extend;
  const pts = 100;
  const step = (hi - lo) / pts;

  const prices: number[] = [];
  const pls: number[] = [];
  const breakevens: number[] = [];
  const qty = Math.max(...legs.map(l => l.qty));

  for (let i = 0; i <= pts; i++) {
    const s = lo + i * step;
    let plPerShare = netPremiumPerShare;
    for (const leg of legs) {
      const sign = leg.direction === "short" ? -1 : 1;
      const intrinsic = leg.opt_type === "call" ? Math.max(s - leg.strike, 0) : Math.max(leg.strike - s, 0);
      plPerShare += sign * intrinsic;
    }
    prices.push(s);
    pls.push(plPerShare * 100 * qty);
  }

  // Find breakevens (where P/L crosses zero)
  for (let i = 1; i < pls.length; i++) {
    if ((pls[i - 1] <= 0 && pls[i] > 0) || (pls[i - 1] >= 0 && pls[i] < 0)) {
      // Linear interpolation
      const ratio = Math.abs(pls[i - 1]) / (Math.abs(pls[i - 1]) + Math.abs(pls[i]));
      breakevens.push(prices[i - 1] + ratio * (prices[i] - prices[i - 1]));
    }
  }

  const maxProfit = Math.max(...pls);
  const maxLoss = Math.min(...pls);

  return { prices, pls, breakevens, maxProfit, maxLoss };
}

// ── Monte Carlo simulation ──
function monteCarloSpread(spread: RHSpread, nSims = 10000) {
  const legs = spread.legs;
  if (!legs.length || !spread.stock_price) return null;

  const S = spread.stock_price;
  const dte = Math.max(1, Math.round((new Date(spread.expiration + "T16:00:00").getTime() - Date.now()) / 86400000));
  const T = dte / 365;
  const avgIV = legs.reduce((s, l) => s + (l.iv || 0), 0) / (legs.length || 1);
  if (avgIV <= 0) return null;

  const netPremiumPerShare = legs.reduce((sum, l) => {
    return sum + l.avg_price * (l.direction === "short" ? 1 : -1);
  }, 0);
  const qty = Math.max(...legs.map(l => l.qty));

  // Simulate terminal stock prices using GBM: S_T = S * exp((r - σ²/2)T + σ√T * Z)
  const r = 0.045; // risk-free rate
  const drift = (r - 0.5 * avgIV * avgIV) * T;
  const vol = avgIV * Math.sqrt(T);

  const results: number[] = [];
  // Box-Muller for normal random numbers (guard against u1=0)
  for (let i = 0; i < nSims; i++) {
    const u1 = Math.max(1e-10, Math.random()), u2 = Math.random();
    const z = Math.sqrt(-2 * Math.log(u1)) * Math.cos(2 * Math.PI * u2);
    const sT = S * Math.exp(drift + vol * z);

    let plPerShare = netPremiumPerShare;
    for (const leg of legs) {
      const sign = leg.direction === "short" ? -1 : 1;
      const intrinsic = leg.opt_type === "call" ? Math.max(sT - leg.strike, 0) : Math.max(leg.strike - sT, 0);
      plPerShare += sign * intrinsic;
    }
    results.push(plPerShare * 100 * qty);
  }

  results.sort((a, b) => a - b);

  let profitCount = 0, plSum = 0;
  for (const r of results) { if (r > 0) profitCount++; plSum += r; }
  const probProfit = profitCount / nSims * 100;
  const median = results[Math.floor(nSims / 2)];
  const p10 = results[Math.floor(nSims * 0.1)];
  const p25 = results[Math.floor(nSims * 0.25)];
  const p75 = results[Math.floor(nSims * 0.75)];
  const p90 = results[Math.floor(nSims * 0.9)];
  const expectedPL = plSum / nSims;
  const maxSim = results[results.length - 1];
  const minSim = results[0];

  // Build histogram — single pass
  const bins = 30;
  const iqr = p90 - p10;
  const histMin = iqr > 0 ? p10 - iqr * 0.3 : minSim - 1;
  const histMax = iqr > 0 ? p90 + iqr * 0.3 : maxSim + 1;
  const binWidth = (histMax - histMin) / bins || 1;
  const histogram: { center: number; count: number }[] = Array.from({ length: bins }, (_, i) => ({
    center: histMin + (i + 0.5) * binWidth, count: 0,
  }));
  for (const r of results) {
    const idx = Math.min(bins - 1, Math.max(0, Math.floor((r - histMin) / binWidth)));
    histogram[idx].count++;
  }

  return {
    probProfit: Math.round(probProfit),
    expectedPL: Math.round(expectedPL),
    median: Math.round(median),
    p10: Math.round(p10), p25: Math.round(p25),
    p75: Math.round(p75), p90: Math.round(p90),
    min: Math.round(minSim), max: Math.round(maxSim),
    histogram,
    nSims, dte, iv: avgIV,
  };
}

// ── MC distribution chart ──
function MCChart({ mc }: { mc: NonNullable<ReturnType<typeof monteCarloSpread>> }) {
  const W = 320, H = 70;
  const { histogram } = mc;
  const maxCount = Math.max(...histogram.map(h => h.count));
  const padX = 4, padBot = 12;
  const w = W - padX * 2;
  const h = H - padBot;

  return (
    <svg width={W} height={H} style={{ display: "block" }}>
      {histogram.map((bin, i) => {
        const x = padX + (i / histogram.length) * w;
        const barW = w / histogram.length - 1;
        const barH = maxCount > 0 ? (bin.count / maxCount) * h : 0;
        const isProfit = bin.center > 0;
        return (
          <rect key={i} x={x} y={h - barH} width={Math.max(barW, 1)} height={barH}
            fill={isProfit ? "rgba(34,197,94,0.5)" : "rgba(239,68,68,0.4)"}
            rx="1" />
        );
      })}
      {/* Zero line */}
      {(() => {
        const zeroX = padX + ((0 - histogram[0].center) / (histogram[histogram.length - 1].center - histogram[0].center)) * w;
        if (zeroX > padX && zeroX < W - padX) {
          return <line x1={zeroX} x2={zeroX} y1={0} y2={h} stroke="#888" strokeWidth="1" strokeDasharray="2,2" />;
        }
        return null;
      })()}
      {/* Labels */}
      <text x={padX} y={H - 1} fill="#ef4444" fontSize="7" fontFamily="monospace">${mc.p10.toLocaleString()}</text>
      <text x={W / 2} y={H - 1} fill="#888" fontSize="7" fontFamily="monospace" textAnchor="middle">median ${mc.median.toLocaleString()}</text>
      <text x={W - padX} y={H - 1} fill="#22c55e" fontSize="7" fontFamily="monospace" textAnchor="end">${mc.p90.toLocaleString()}</text>
    </svg>
  );
}

// ── SVG P/L chart ──
let _plId = 0;
function SpreadPLChart({ spread }: { spread: RHSpread }) {
  const [clipId] = useState(() => `spl-${++_plId}`);
  const result = computeSpreadPL(spread);
  if (!result || result.prices.length < 2) return null;

  const { prices, pls, breakevens, maxProfit, maxLoss } = result;
  const W = 320, topM = 12, botM = 14, chartH = 70, H = topM + chartH + botM;
  const padX = 4, w = W - padX * 2;
  // Clamp Y-axis so loss zone is always at least 25% of chart height
  // This prevents extreme profit/loss ratios from hiding one side
  const rawMin = Math.min(...pls), rawMax = Math.max(...pls);
  const absMax = Math.max(Math.abs(rawMin), Math.abs(rawMax));
  const minP = Math.min(rawMin, -absMax * 0.25);
  const maxP = Math.max(rawMax, absMax * 0.25);
  const range = maxP - minP || 1;
  const zeroY = topM + chartH - ((0 - minP) / range) * chartH;
  const pMin = prices[0], pMax = prices[prices.length - 1], pRange = pMax - pMin || 1;

  const toX = (i: number) => padX + (i / (prices.length - 1)) * w;
  const priceToX = (p: number) => padX + ((p - pMin) / pRange) * w;
  const toY = (pl: number) => topM + chartH - ((pl - minP) / range) * chartH;

  const pathD = prices.map((_, i) => `${i === 0 ? "M" : "L"}${toX(i).toFixed(1)},${toY(pls[i]).toFixed(1)}`).join(" ");
  const fillD = pathD + `L${toX(prices.length - 1)},${zeroY}L${toX(0)},${zeroY}Z`;

  const stockInRange = spread.stock_price >= pMin && spread.stock_price <= pMax;

  return (
    <svg width={W} height={H} style={{ display: "block" }}>
      <defs>
        <clipPath id={`${clipId}-a`}><rect x={padX} y={topM} width={w} height={Math.max(0, zeroY - topM)} /></clipPath>
        <clipPath id={`${clipId}-b`}><rect x={padX} y={zeroY} width={w} height={Math.max(0, topM + chartH - zeroY)} /></clipPath>
      </defs>
      <text x={padX} y={topM - 3} fill="#22c55e" fontSize="8" fontFamily="monospace">+${maxProfit.toFixed(0)}</text>
      <text x={W - padX} y={topM - 3} fill="#ef4444" fontSize="8" fontFamily="monospace" textAnchor="end">-${Math.abs(maxLoss).toFixed(0)}</text>
      <line x1={padX} x2={W - padX} y1={zeroY} y2={zeroY} stroke="#555" strokeWidth="0.5" strokeDasharray="3,3" />
      <path d={fillD} fill="rgba(34,197,94,0.12)" clipPath={`url(#${clipId}-a)`} />
      <path d={fillD} fill="rgba(239,68,68,0.12)" clipPath={`url(#${clipId}-b)`} />
      <path d={pathD} fill="none" stroke="#888" strokeWidth="1.5" strokeLinejoin="round" />
      {breakevens.map((be, i) => (
        <g key={i}>
          <circle cx={priceToX(be)} cy={zeroY} r="3" fill="#888" />
          <text x={priceToX(be)} y={topM + chartH + 10} fill="#888" fontSize="7" fontFamily="monospace" textAnchor="middle">BE ${be.toFixed(0)}</text>
        </g>
      ))}
      {stockInRange && (
        <>
          <line x1={priceToX(spread.stock_price)} x2={priceToX(spread.stock_price)} y1={topM} y2={topM + chartH} stroke="#6366f1" strokeWidth="1" strokeDasharray="2,2" />
          <text x={priceToX(spread.stock_price)} y={topM + 10} fill="#6366f1" fontSize="8" fontFamily="monospace" textAnchor="middle">${spread.stock_price.toFixed(0)}</text>
        </>
      )}
    </svg>
  );
}

export default function PositionMonitor() {
  const { data, isLoading, error, refetch, dataUpdatedAt } = useQuery({
    queryKey: ["rh-positions"],
    queryFn: fetchRobinhoodPositions,
    staleTime: 60_000,
    refetchInterval: 5 * 60_000,
  });

  const portfolio = data?.portfolio;
  const stocks = data?.stocks ?? [];
  const spreads = data?.spreads ?? [];
  const totalPL = portfolio?.total_pl ?? 0;

  const [research, setResearch] = useState<HoldingResearch[]>([]);
  const [researchError, setResearchError] = useState("");
  const researchMutation = useMutation({
    mutationFn: () => {
      const tickers = (data?.stocks ?? []).map(s => s.ticker);
      return fetchHoldingsResearch(tickers);
    },
    onSuccess: (r) => {
      if (r.success) { setResearch(r.research); setResearchError(""); }
      else setResearchError(r.error || "Research failed");
    },
  });

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Position Monitor</h1>
          <p className="text-text-secondary text-sm mt-0.5">
            Live Robinhood positions
            {dataUpdatedAt > 0 && <span className="text-text-muted text-xs ml-2">Updated {new Date(dataUpdatedAt).toLocaleTimeString()}</span>}
          </p>
        </div>
        <button onClick={() => refetch()} disabled={isLoading}
          className="px-4 py-1.5 bg-accent text-white font-semibold rounded-lg hover:bg-accent-hover disabled:opacity-50 text-sm">
          {isLoading ? "Loading..." : "Refresh"}
        </button>
      </div>

      {error && <div className="card border-loss/30 bg-loss-bg text-loss text-sm">{(error as Error).message}</div>}
      {data && !data.success && <div className="card border-loss/30 bg-loss-bg text-loss text-sm">{data.error}</div>}

      {isLoading && !data && (
        <div className="flex items-center gap-2 py-8 justify-center">
          <div className="w-5 h-5 border-2 border-accent border-t-transparent rounded-full animate-spin" />
          <span className="text-sm text-text-muted">Connecting to Robinhood...</span>
        </div>
      )}

      {portfolio && (
        <>
          {/* Portfolio summary strip */}
          <div className="flex flex-wrap items-center gap-x-6 gap-y-1.5 px-4 py-3 rounded-lg border border-border bg-surface">
            <div>
              <div className="text-[0.6rem] text-text-muted uppercase">Equity</div>
              <div className="text-lg font-bold font-data">${portfolio.equity.toLocaleString()}</div>
            </div>
            <div>
              <div className="text-[0.6rem] text-text-muted uppercase">Market Value</div>
              <div className="text-lg font-bold font-data">${portfolio.market_value.toLocaleString()}</div>
            </div>
            <div>
              <div className="text-[0.6rem] text-text-muted uppercase">Cash</div>
              <div className="text-lg font-bold font-data">${portfolio.cash.toLocaleString()}</div>
            </div>
            <div className="w-px h-8 bg-border hidden sm:block" />
            <div>
              <div className="text-[0.6rem] text-text-muted uppercase">Stock P&L</div>
              <div className={`text-lg font-bold font-data ${portfolio.stock_pl >= 0 ? "text-gain" : "text-loss"}`}>
                {portfolio.stock_pl >= 0 ? "+" : ""}${portfolio.stock_pl.toLocaleString()}
              </div>
            </div>
            <div>
              <div className="text-[0.6rem] text-text-muted uppercase">Options P&L</div>
              <div className={`text-lg font-bold font-data ${portfolio.option_pl >= 0 ? "text-gain" : "text-loss"}`}>
                {portfolio.option_pl >= 0 ? "+" : ""}${portfolio.option_pl.toLocaleString()}
              </div>
            </div>
            <div>
              <div className="text-[0.6rem] text-text-muted uppercase">Total P&L</div>
              <div className={`text-xl font-bold font-data ${totalPL >= 0 ? "text-gain" : "text-loss"}`}>
                {totalPL >= 0 ? "+" : ""}${totalPL.toLocaleString()}
              </div>
            </div>
            {data?.greeks && (
              <>
                <div className="w-px h-8 bg-border hidden sm:block" />
                <GreeksDisplay greeks={data.greeks} />
              </>
            )}
          </div>

          {/* Scenario Analysis + Concentration */}
          {(spreads.length > 0 || stocks.length > 0) && data?.greeks && (
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-3">
              {/* Scenario table */}
              <div className="lg:col-span-2 rounded-lg border border-border bg-surface px-4 py-3">
                <div className="text-[0.65rem] font-bold uppercase tracking-wider text-text-muted mb-2">What If — Portfolio P&L by Market Move</div>
                <div className="overflow-x-auto">
                  <table className="w-full text-[0.6rem] font-data">
                    <thead>
                      <tr className="text-text-muted">
                        <th className="text-left py-1 pr-2">Position</th>
                        {[-5, -3, -1, 0, 1, 3, 5].map(pct => (
                          <th key={pct} className={`text-right py-1 px-1.5 ${pct === 0 ? "text-text font-bold" : ""}`}>
                            {pct === 0 ? "Now" : `${pct > 0 ? "+" : ""}${pct}%`}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {/* Each spread */}
                      {spreads.map((s, i) => {
                        const d = s.greeks?.delta || 0;
                        const g = s.greeks?.gamma || 0;
                        const stockPx = s.stock_price || 0;
                        return (
                          <tr key={`s-${i}`} className="border-t border-border/50">
                            <td className="py-1 pr-2 text-text">{s.ticker} <span className="text-text-muted">{s.type.slice(0, 10)}</span></td>
                            {[-5, -3, -1, 0, 1, 3, 5].map(pct => {
                              if (pct === 0) return <td key={pct} className="text-right py-1 px-1.5 text-text font-bold">${s.pl.toFixed(0)}</td>;
                              const move = stockPx * (pct / 100);
                              const change = d * move + 0.5 * g * move * move;
                              return (
                                <td key={pct} className={`text-right py-1 px-1.5 ${change >= 0 ? "text-gain" : "text-loss"}`}>
                                  {change >= 0 ? "+" : ""}{change.toFixed(0)}
                                </td>
                              );
                            })}
                          </tr>
                        );
                      })}
                      {/* Stocks aggregated */}
                      {stocks.length > 0 && (
                        <tr className="border-t border-border/50">
                          <td className="py-1 pr-2 text-text">Stocks <span className="text-text-muted">({stocks.length})</span></td>
                          {[-5, -3, -1, 0, 1, 3, 5].map(pct => {
                            if (pct === 0) return <td key={pct} className="text-right py-1 px-1.5 text-text font-bold">${stocks.reduce((sum, st) => sum + st.pl, 0).toFixed(0)}</td>;
                            const change = stocks.reduce((sum, st) => sum + st.qty * st.current_price * (pct / 100), 0);
                            return (
                              <td key={pct} className={`text-right py-1 px-1.5 ${change >= 0 ? "text-gain" : "text-loss"}`}>
                                {change >= 0 ? "+" : ""}{change.toFixed(0)}
                              </td>
                            );
                          })}
                        </tr>
                      )}
                      {/* Portfolio total — projected total P&L (current + change) */}
                      {(() => {
                        const totals = [-5, -3, -1, 0, 1, 3, 5].map(pct => {
                          if (pct === 0) return totalPL;
                          let change = 0;
                          for (const s of spreads) {
                            const d = s.greeks?.delta || 0, g = s.greeks?.gamma || 0, px = s.stock_price || 0;
                            const move = px * (pct / 100);
                            change += d * move + 0.5 * g * move * move;
                          }
                          change += stocks.reduce((sum, st) => sum + st.qty * st.current_price * (pct / 100), 0);
                          return totalPL + change;
                        });
                        const worstIdx = totals.indexOf(Math.min(...totals));
                        return (
                          <tr className="border-t-2 border-border font-bold">
                            <td className="py-1.5 pr-2 text-text">TOTAL</td>
                            {totals.map((pl, idx) => (
                              <td key={idx} className={`text-right py-1.5 px-1.5 ${
                                idx === 3 ? "text-text" : idx === worstIdx ? "text-loss bg-loss/5 font-bold" : pl >= 0 ? "text-gain" : "text-loss"
                              }`}>
                                ${pl.toLocaleString(undefined, { maximumFractionDigits: 0 })}
                              </td>
                            ))}
                          </tr>
                        );
                      })()}
                    </tbody>
                  </table>
                </div>
                {/* Worst case summary */}
                {(() => {
                  const worstChange = (() => {
                    let worst = 0;
                    for (const pct of [-5, -3, 5, 3]) {
                      let change = 0;
                      for (const s of spreads) {
                        const d = s.greeks?.delta || 0, g = s.greeks?.gamma || 0, px = s.stock_price || 0;
                        const move = px * (pct / 100);
                        change += d * move + 0.5 * g * move * move;
                      }
                      change += stocks.reduce((sum, st) => sum + st.qty * st.current_price * (pct / 100), 0);
                      if (change < worst) worst = change;
                    }
                    return worst;
                  })();
                  const dailyTheta = data?.greeks?.theta || 0;
                  return (
                    <div className="flex gap-4 mt-2 text-[0.55rem] font-data">
                      <span className="text-loss">Worst case (5% move): <span className="font-bold">${worstChange.toLocaleString(undefined, { maximumFractionDigits: 0 })}</span></span>
                      <span className="text-text-muted">That's <span className="font-bold">{portfolio ? (Math.abs(worstChange) / portfolio.equity * 100).toFixed(1) : "?"}%</span> of equity</span>
                      {dailyTheta !== 0 && (
                        <span className={dailyTheta > 0 ? "text-gain" : "text-loss"}>
                          Daily theta: <span className="font-bold">{dailyTheta > 0 ? "+" : ""}${dailyTheta.toFixed(0)}/day</span>
                        </span>
                      )}
                      <span className="text-text-muted">Greek approximation — accurate for small moves, less so for deep ITM positions</span>
                    </div>
                  );
                })()}
              </div>

              {/* Concentration */}
              <div className="rounded-lg border border-border bg-surface px-4 py-3">
                <div className="text-[0.65rem] font-bold uppercase tracking-wider text-text-muted mb-2">Concentration Risk</div>
                {(data.concentration ?? []).map((c, i) => (
                  <div key={i} className="mb-2">
                    <div className="flex items-center justify-between text-[0.6rem] mb-0.5">
                      <span className="font-semibold text-text">{c.theme}</span>
                      <span className={`font-data font-bold ${c.warning === "HIGH" ? "text-loss" : c.warning === "MODERATE" ? "text-warn" : "text-gain"}`}>
                        {c.pct}%
                      </span>
                    </div>
                    <div className="relative h-2 rounded-full bg-surface-alt border border-border overflow-hidden">
                      <div className={`absolute inset-y-0 left-0 rounded-full ${
                        c.warning === "HIGH" ? "bg-loss/40" : c.warning === "MODERATE" ? "bg-warn/40" : "bg-gain/30"
                      }`} style={{ width: `${Math.min(c.pct, 100)}%` }} />
                    </div>
                    <div className="text-[0.5rem] text-text-muted mt-0.5">
                      {c.tickers.join(", ")} · ${c.value.toLocaleString()}
                    </div>
                    {c.warning === "HIGH" && (
                      <div className="text-[0.5rem] text-loss mt-0.5">
                        !! {c.pct}% in one theme — a single headline moves all {c.tickers.length} positions
                      </div>
                    )}
                  </div>
                ))}
                {(data.concentration ?? []).length === 0 && (
                  <span className="text-[0.6rem] text-text-muted">No stock positions</span>
                )}
              </div>
            </div>
          )}

          {/* Options Spreads */}
          {spreads.length > 0 && (
            <div className="card">
              <div className="metric-label mb-3">Options ({spreads.length})</div>
              <div className="space-y-2">
                {spreads.map((s, i) => (
                  <SpreadRow key={`${s.ticker}-${s.expiration}-${i}`} spread={s} />
                ))}
              </div>
            </div>
          )}

          {/* Stock Positions */}
          {stocks.length > 0 && (
            <div className="card">
              <div className="flex items-center justify-between mb-3">
                <div className="metric-label">Stocks ({stocks.length})</div>
                <span className={`text-xs font-data font-semibold ${portfolio.stock_pl >= 0 ? "text-gain" : "text-loss"}`}>
                  ${stocks.reduce((s, x) => s + x.market_value, 0).toLocaleString()} mkt · {portfolio.stock_pl >= 0 ? "+" : ""}${portfolio.stock_pl.toLocaleString()} P&L
                </span>
              </div>
              <div className="space-y-1">
                {stocks.map((s) => (
                  <StockRow key={s.ticker} stock={s} />
                ))}
              </div>
            </div>
          )}

          {/* Holdings Research */}
          {stocks.length > 0 && (
            <div className="card">
              <div className="flex items-center justify-between mb-3">
                <div className="metric-label">Holdings Research</div>
                <button onClick={() => researchMutation.mutate()} disabled={researchMutation.isPending}
                  className="px-4 py-1 bg-accent text-white text-[0.6rem] font-semibold rounded hover:bg-accent-hover disabled:opacity-50">
                  {researchMutation.isPending ? "Researching..." : research.length > 0 ? "Re-research" : "Research All"}
                </button>
              </div>
              {researchMutation.isPending && (
                <div className="flex items-center gap-2 py-4">
                  <div className="w-4 h-4 border-2 border-accent border-t-transparent rounded-full animate-spin" />
                  <span className="text-xs text-text-muted">Grok searching recent developments for {stocks.length} holdings...</span>
                </div>
              )}
              {(researchMutation.isError || researchError) && (
                <div className="text-xs text-loss mb-2">Research failed: {researchError || (researchMutation.error as Error).message}</div>
              )}
              {research.length === 0 && !researchMutation.isPending && !researchMutation.isError && (
                <p className="text-xs text-text-muted text-center py-3">Click "Research All" to search for recent developments on your held stocks.</p>
              )}
              <div className="space-y-3">
                {research.map((r) => (
                  <ResearchCard key={r.ticker} research={r} stock={stocks.find(s => s.ticker === r.ticker)} />
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function GreeksDisplay({ greeks }: { greeks: RHPortfolioGreeks }) {
  const items = [
    { label: "\u0394", value: greeks.delta, detail: `Options: ${greeks.option_delta > 0 ? "+" : ""}${greeks.option_delta.toFixed(0)} | Stock: ${greeks.stock_delta.toFixed(0)}`, color: greeks.delta > 50 ? "text-gain" : greeks.delta < -50 ? "text-loss" : "" },
    { label: "\u0393", value: greeks.gamma, detail: "Portfolio gamma exposure", color: "" },
    { label: "\u0398", value: greeks.theta, detail: `${greeks.theta > 0 ? "Collecting" : "Paying"} $${Math.abs(greeks.theta).toFixed(0)}/day`, color: greeks.theta > 0 ? "text-gain" : "text-loss" },
    { label: "\u03BD", value: greeks.vega, detail: `${greeks.vega > 0 ? "Long" : "Short"} vol $${Math.abs(greeks.vega).toFixed(0)} per 1% IV`, color: greeks.vega > 0 ? "text-gain" : "text-loss" },
  ];
  return (
    <div className="flex gap-4">
      {items.map(g => (
        <div key={g.label} title={g.detail}>
          <div className="text-[0.55rem] text-text-muted uppercase">{g.label}</div>
          <div className={`text-base font-bold font-data ${g.color}`}>
            {g.value > 0 ? "+" : ""}{g.value.toFixed(0)}
          </div>
        </div>
      ))}
    </div>
  );
}

function StockRow({ stock }: { stock: RHStock }) {
  const plColor = stock.pl >= 0 ? "text-gain" : "text-loss";
  const priceColor = stock.current_price >= stock.avg_cost ? "text-gain" : "text-loss";
  const daysHeld = stock.entry_date ? Math.max(0, Math.round((Date.now() - new Date(stock.entry_date).getTime()) / 86400000)) : null;
  return (
    <div className={`flex items-center gap-3 px-3 py-2 rounded-lg border text-xs font-data ${
      stock.pl_pct < -15 ? "border-loss/30" : stock.pl_pct > 15 ? "border-gain/30" : "border-border"
    }`}>
      <span className="font-bold text-sm w-14">{stock.ticker}</span>
      <span className="text-text-muted">{Math.round(stock.qty)} sh</span>
      <span className="text-text-muted">${stock.avg_cost.toFixed(2)}</span>
      <span className={priceColor}>${stock.current_price.toFixed(2)}</span>
      {stock.theme && <span className="text-[0.5rem] text-text-muted">{stock.theme}</span>}
      {daysHeld !== null && <span className="text-[0.5rem] text-text-muted">{daysHeld}d held</span>}
      <span className="ml-auto" />
      <span className="text-text-muted">${stock.market_value.toLocaleString()}</span>
      <span className={`font-semibold ${plColor}`}>
        {stock.pl >= 0 ? "+" : ""}${stock.pl.toFixed(0)}
      </span>
      <span className={`text-[0.6rem] ${plColor}`}>
        {stock.pl_pct >= 0 ? "+" : ""}{stock.pl_pct.toFixed(1)}%
      </span>
    </div>
  );
}

function SpreadRow({ spread }: { spread: RHSpread }) {
  const [hovered, setHovered] = useState(false);
  // Lightweight MC for the card (1000 sims, cached)
  const [quickMC] = useState(() => monteCarloSpread(spread, 1000));
  const plColor = spread.pl >= 0 ? "text-gain" : "text-loss";
  const dte = Math.max(0, Math.round((new Date(spread.expiration + "T16:00:00").getTime() - Date.now()) / 86400000));

  // Short leg analysis
  const shortLegs = spread.legs.filter(l => l.direction === "short");
  const shortDistances = shortLegs.map(leg => {
    if (!spread.stock_price) return null;
    const isCall = leg.opt_type === "call";
    const dangerDist = isCall
      ? ((spread.stock_price - leg.strike) / leg.strike) * 100
      : ((leg.strike - spread.stock_price) / leg.strike) * 100;
    return { strike: leg.strike, type: leg.opt_type, dangerDist, label: `$${leg.strike.toFixed(0)}${isCall ? "C" : "P"}` };
  }).filter(Boolean) as { strike: number; type: string; dangerDist: number; label: string }[];

  const worstDanger = shortDistances.length > 0 ? Math.max(...shortDistances.map(d => d.dangerDist)) : -99;
  const borderColor = worstDanger > 1 ? "border-loss/50" : worstDanger > -3 ? "border-warn/50" : spread.pl >= 0 ? "border-gain/30" : "border-border";

  // % of max profit captured
  const maxProfit = Math.abs(spread.net_premium); // credit collected = max profit for credit spreads
  const pctCaptured = maxProfit > 0 ? (spread.pl / maxProfit) * 100 : 0;
  const costToClose = Math.abs(spread.current_value);

  // Trade outlook — computed from Greeks
  const expDate = spread.expiration.slice(5);
  const dailyTheta = spread.greeks?.theta || 0;
  const posDelta = spread.greeks?.delta || 0;

  // Theta recovery: days to break even if P&L is negative and theta is positive
  const thetaRecoveryDays = (spread.pl < 0 && dailyTheta > 0)
    ? Math.ceil(Math.abs(spread.pl) / dailyTheta)
    : null;
  const canRecover = thetaRecoveryDays !== null && thetaRecoveryDays <= dte;

  // Per-leg probability of expiring ITM (delta ≈ prob ITM)
  const legProbs = shortDistances.map(sd => {
    const leg = spread.legs.find(l => l.direction === "short" && l.strike === sd.strike);
    if (!leg) return null;
    const probITM = Math.abs(leg.delta / (100 * leg.qty)) * 100; // undo position scaling
    return { ...sd, probITM: Math.min(probITM, 100), probOTM: Math.min(100 - probITM, 100) };
  }).filter(Boolean) as (typeof shortDistances[0] & { probITM: number; probOTM: number })[];

  // Avg daily stock move (from IV): stock × IV / √252
  const avgIV = spread.legs.reduce((s, l) => s + (l.iv || 0), 0) / (spread.legs.length || 1);
  const dailyMove = spread.stock_price * avgIV / Math.sqrt(252);
  // Theta vs delta race: is daily theta collecting more than delta loses on an avg move?
  const thetaWinning = dailyTheta > 0 && Math.abs(posDelta * dailyMove) < dailyTheta;

  // Verdict
  let signal: { text: string; color: string };
  let verdict: string;
  if (worstDanger > 0 && !canRecover) {
    signal = { text: "MANAGE", color: "text-loss" };
    verdict = `Breached and theta can't recover in time${thetaRecoveryDays ? ` (need ${thetaRecoveryDays}d, have ${dte}d)` : ""}. Roll or close.`;
  } else if (worstDanger > 0 && canRecover) {
    signal = { text: "HOLD — recoverable", color: "text-warn" };
    verdict = `Breached but theta recovers in ${thetaRecoveryDays}d (${dte}d left). ${thetaWinning ? "Theta is winning the race." : "Watch closely — delta could overrun theta."}`;
  } else if (dte <= 5) {
    signal = { text: "EXPIRING", color: "text-loss" };
    verdict = `${dte}d to ${expDate}. ${pctCaptured >= 0 ? `Close for ${pctCaptured.toFixed(0)}% profit.` : "Close to limit further loss."}`;
  } else if (pctCaptured >= 50) {
    signal = { text: "CLOSE", color: "text-gain" };
    verdict = `${pctCaptured.toFixed(0)}% of max profit captured. Standard management = take the win.`;
  } else if (thetaWinning && spread.pl < 0) {
    signal = { text: "HOLD — theta winning", color: "text-text-muted" };
    verdict = `Underwater ${pctCaptured.toFixed(0)}% but theta ($${dailyTheta.toFixed(0)}/d) > avg delta loss ($${Math.abs(posDelta * dailyMove).toFixed(0)}/d). Recovery in ~${thetaRecoveryDays || "?"}d.`;
  } else if (spread.pl >= 0) {
    signal = { text: "HOLD — profitable", color: "text-gain" };
    verdict = `${pctCaptured.toFixed(0)}% captured, target 50%. ${dte}d left. ${thetaWinning ? "Theta accelerating." : ""}`;
  } else {
    signal = { text: "HOLD", color: "text-text-muted" };
    verdict = `${pctCaptured.toFixed(0)}% captured. ${dte}d to ${expDate}. ${dailyTheta > 0 ? `Collecting $${dailyTheta.toFixed(0)}/d theta.` : ""}`;
  }

  // Strike bar visual — wider padding for tight spreads
  const allStrikes = spread.legs.map(l => l.strike);
  const priceMin = Math.min(...allStrikes, spread.stock_price || Infinity);
  const priceMax = Math.max(...allStrikes, spread.stock_price || 0);
  const priceSpan = priceMax - priceMin || 1;
  // Add 15% padding on each side so tight spreads don't bunch in the center
  const pad = priceSpan * 0.15;
  const sMin = priceMin - Math.max(pad, priceMin * 0.005);
  const sMax = priceMax + Math.max(pad, priceMax * 0.005);
  const sRange = sMax - sMin || 1;
  const toBarPct = (price: number) => Math.max(2, Math.min(98, ((price - sMin) / sRange) * 100));

  // P/L — only when hovered
  const plResult = hovered ? computeSpreadPL(spread) : null;
  // MC — cached after first hover (10K sims)
  const mcCacheRef = useRef<ReturnType<typeof monteCarloSpread>>(null);
  if (hovered && !mcCacheRef.current) mcCacheRef.current = monteCarloSpread(spread);
  const mcResult = hovered ? mcCacheRef.current : null;

  return (
    <div className="relative" onMouseEnter={() => setHovered(true)} onMouseLeave={() => setHovered(false)}>
    <div className={`rounded-lg border p-3 ${borderColor}`}>
      {/* Row 1: Header + P&L + Signal */}
      <div className="flex items-start justify-between mb-1.5">
        <div>
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-bold text-sm">{spread.ticker}</span>
            <span className={`badge text-[0.5rem] ${
              spread.type.includes("Iron") ? "badge-info"
              : spread.type.includes("Bull") ? "badge-gain"
              : spread.type.includes("Bear") ? "badge-loss"
              : spread.type.includes("Naked") ? "badge-loss"
              : "badge-info"
            }`}>{spread.type}</span>
            <span className="text-[0.6rem] text-text-muted font-data">{spread.strikes} · {dte}d · {"\u00D7"}{spread.qty}</span>
          </div>
          <div className={`text-[0.6rem] font-semibold mt-0.5 ${signal.color}`}>{signal.text}</div>
          <div className="text-[0.55rem] text-text-muted mt-0.5">{verdict}</div>
          <div className="flex gap-3 mt-0.5 text-[0.5rem] font-data flex-wrap">
            {quickMC && (
              <>
                <span className={quickMC.probProfit >= 60 ? "text-gain font-bold" : quickMC.probProfit >= 40 ? "text-warn font-semibold" : "text-loss font-bold"}>
                  {quickMC.probProfit}% win rate
                </span>
                <span className={quickMC.expectedPL >= 0 ? "text-gain" : "text-loss"}>
                  E[P/L] ${quickMC.expectedPL.toLocaleString()}
                </span>
                <span className="text-text-muted">
                  range ${quickMC.p10.toLocaleString()} to ${quickMC.p90.toLocaleString()}
                </span>
              </>
            )}
            {legProbs.map((lp, j) => (
              <span key={j} className={lp.probITM > 50 ? "text-loss" : lp.probITM > 30 ? "text-warn" : "text-gain"}>
                {lp.label}: {lp.probITM.toFixed(0)}% ITM
              </span>
            ))}
          </div>
        </div>
        <div className="text-right">
          <div className={`text-lg font-bold font-data ${plColor}`}>
            {spread.pl >= 0 ? "+" : ""}${spread.pl.toFixed(0)}
          </div>
        </div>
      </div>

      {/* Row 2: Strike position bar */}
      {spread.stock_price > 0 && (
        <div className="mb-2 pt-3 pb-3">
          <div className="relative h-3 rounded-full bg-surface-alt border border-border">
            {/* Profit zone */}
            {(() => {
              const shorts = shortDistances.map(s => s.strike);
              if (shorts.length === 2) {
                const lo = toBarPct(Math.min(...shorts));
                const hi = toBarPct(Math.max(...shorts));
                return <div className="absolute inset-y-0 bg-gain/15 rounded-full" style={{ left: `${lo}%`, width: `${hi - lo}%` }} />;
              } else if (shorts.length === 1) {
                // Single short: profit is on the OTM side
                const sd = shortDistances[0];
                const pct = toBarPct(sd.strike);
                if (sd.type === "call") {
                  // Short call: profit below strike
                  return <div className="absolute inset-y-0 bg-gain/15 rounded-full" style={{ left: "0%", width: `${pct}%` }} />;
                } else {
                  // Short put: profit above strike
                  return <div className="absolute inset-y-0 bg-gain/15 rounded-full" style={{ left: `${pct}%`, width: `${100 - pct}%` }} />;
                }
              }
              return null;
            })()}
            {/* Strike markers — labels above */}
            {spread.legs.map((leg, j) => {
              const pct = toBarPct(leg.strike);
              const isShort = leg.direction === "short";
              return (
                <div key={j} className="absolute" style={{ left: `${pct}%`, top: "-14px", transform: "translateX(-50%)" }}>
                  <div className={`text-[0.5rem] font-data whitespace-nowrap ${isShort ? "text-loss font-semibold" : "text-gain"}`}>
                    ${leg.strike.toFixed(0)}{leg.opt_type[0].toUpperCase()}
                  </div>
                  <div className={`w-px h-5 mx-auto ${isShort ? "bg-loss/50" : "bg-gain/30"}`} />
                </div>
              );
            })}
            {/* Stock price — label below */}
            <div className="absolute" style={{ left: `${toBarPct(spread.stock_price)}%`, bottom: "-14px", transform: "translateX(-50%)" }}>
              <div className="w-2.5 h-2.5 rounded-full bg-accent border-2 border-surface mx-auto" style={{ marginTop: "-4px" }} />
              <div className="text-[0.5rem] font-data text-accent font-bold whitespace-nowrap mt-0.5">
                ${spread.stock_price.toFixed(2)}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Row 3: Profit capture bar */}
      <div className="mb-2">
        <div className="flex items-center gap-2 text-[0.55rem] font-data">
          <span className="text-text-muted shrink-0">Profit</span>
          <div className="flex-1 h-1.5 rounded-full bg-surface-alt border border-border relative overflow-hidden">
            {pctCaptured > 0 ? (
              <div className="absolute inset-y-0 left-0 bg-gain rounded-full" style={{ width: `${Math.min(pctCaptured, 100)}%` }} />
            ) : pctCaptured < 0 ? (
              <div className="absolute inset-y-0 left-0 bg-loss rounded-full" style={{ width: `${Math.min(Math.abs(pctCaptured), 100)}%` }} />
            ) : null}
            {/* 50% target marker */}
            <div className="absolute inset-y-0 w-px bg-text-muted" style={{ left: "50%" }} />
          </div>
          <span className={`shrink-0 font-semibold ${pctCaptured >= 50 ? "text-gain" : pctCaptured < 0 ? "text-loss" : "text-text"}`}>
            {pctCaptured.toFixed(0)}%
          </span>
          <span className="text-text-muted shrink-0">
            ${spread.net_premium.toFixed(0)} in · ${costToClose.toFixed(0)} out
          </span>
        </div>
      </div>

      {/* Row 4: Legs + Greeks compact */}
      <div className="flex items-center gap-3 text-[0.55rem] font-data flex-wrap">
        {spread.legs.map((leg, j) => (
          <span key={j} className={leg.direction === "short" ? "text-loss" : "text-gain"}>
            {leg.direction === "short" ? "S" : "L"} ${leg.strike.toFixed(0)}{leg.opt_type[0].toUpperCase()} @${Math.abs(leg.avg_price).toFixed(2)}
          </span>
        ))}
        <span className="w-px h-3 bg-border" />
        {spread.greeks && (
          <>
            <span className="text-text-muted">{"\u0394"}<span className={spread.greeks.delta > 0 ? "text-gain" : spread.greeks.delta < 0 ? "text-loss" : "text-text"}>{spread.greeks.delta > 0 ? "+" : ""}{spread.greeks.delta.toFixed(0)}</span></span>
            <span className="text-text-muted">{"\u0398"}<span className={spread.greeks.theta > 0 ? "text-gain" : "text-loss"}>{spread.greeks.theta > 0 ? "+" : ""}{spread.greeks.theta.toFixed(0)}/d</span></span>
            <span className="text-text-muted">{"\u03BD"}<span className="text-text">{spread.greeks.vega > 0 ? "+" : ""}{spread.greeks.vega.toFixed(0)}</span></span>
          </>
        )}
        {/* Strike distances as text for screen readers / detail */}
        <span className="w-px h-3 bg-border" />
        {shortDistances.map((sd, j) => (
          <span key={j} className={sd.dangerDist > 1 ? "text-loss font-bold" : sd.dangerDist > -3 ? "text-warn" : "text-gain"}>
            {sd.label} {sd.dangerDist > 0 ? `${sd.dangerDist.toFixed(1)}% ITM` : `${Math.abs(sd.dangerDist).toFixed(1)}% OTM`}
          </span>
        ))}
      </div>
    </div>
    {/* Hover tooltip: P/L chart + Monte Carlo */}
    {hovered && (plResult || mcResult) && (
      <div className="absolute z-50 top-full right-0 mt-1 p-2.5 rounded-lg border border-border bg-surface shadow-lg" style={{ minWidth: 360 }}>
        {/* P/L at expiration */}
        {plResult && (
          <>
            <div className="text-[0.65rem] font-semibold text-text mb-1">P/L at Expiration</div>
            <SpreadPLChart spread={spread} />
          </>
        )}
        {/* Monte Carlo simulation */}
        {mcResult && (
          <div className={plResult ? "mt-2 pt-2 border-t border-border" : ""}>
            <div className="flex items-center justify-between mb-1">
              <span className="text-[0.65rem] font-semibold text-text">
                Monte Carlo — {mcResult.nSims.toLocaleString()} sims · IV {(mcResult.iv * 100).toFixed(0)}% · {mcResult.dte}d
              </span>
              <span className={`text-[0.6rem] font-bold ${mcResult.expectedPL >= 0 ? "text-gain" : "text-loss"}`}>
                {mcResult.expectedPL >= 0 ? "+EV" : "-EV"}
              </span>
            </div>
            <MCChart mc={mcResult} />
            <div className="grid grid-cols-2 gap-x-4 gap-y-0.5 mt-1.5 text-[0.55rem] font-data">
              <span className="text-text-muted">Prob of Profit</span>
              <span className={`font-bold ${mcResult.probProfit >= 60 ? "text-gain" : mcResult.probProfit >= 40 ? "text-warn" : "text-loss"}`}>
                {mcResult.probProfit}%
              </span>
              <span className="text-text-muted">Expected P/L</span>
              <span className={mcResult.expectedPL >= 0 ? "text-gain font-semibold" : "text-loss font-semibold"}>
                ${mcResult.expectedPL.toLocaleString()}
              </span>
              <span className="text-text-muted">Median P/L</span>
              <span className={mcResult.median >= 0 ? "text-gain" : "text-loss"}>${mcResult.median.toLocaleString()}</span>
              <span className="text-text-muted">Best case (90th)</span><span className="text-gain">${mcResult.p90.toLocaleString()}</span>
              <span className="text-text-muted">Worst case (10th)</span><span className="text-loss">${mcResult.p10.toLocaleString()}</span>
              <span className="text-text-muted">Max Profit</span><span className="text-gain">${(plResult?.maxProfit ?? mcResult.max).toLocaleString()}</span>
              <span className="text-text-muted">Max Loss</span><span className="text-loss">${Math.abs(plResult?.maxLoss ?? mcResult.min).toLocaleString()}</span>
              <span className="text-text-muted">Breakeven{plResult && plResult.breakevens.length > 1 ? "s" : ""}</span>
              <span>{plResult?.breakevens.map(b => `$${b.toFixed(1)}`).join(", ") || "—"}</span>
            </div>
          </div>
        )}
        {/* Position details */}
        <div className="mt-2 pt-2 border-t border-border grid grid-cols-2 gap-x-4 gap-y-0.5 text-[0.55rem] font-data">
          <span className="text-text-muted">Current P/L</span><span className={plColor}>${spread.pl.toFixed(0)}</span>
          <span className="text-text-muted">Collected / Close</span><span>${spread.net_premium.toFixed(0)} / ${costToClose.toFixed(0)}</span>
          <span className="text-text-muted">% Captured</span><span className={pctCaptured >= 50 ? "text-gain font-bold" : pctCaptured < 0 ? "text-loss" : ""}>{pctCaptured.toFixed(0)}%</span>
          <span className="text-text-muted">DTE</span><span>{dte}d ({spread.expiration})</span>
          <span className="text-text-muted">Stock</span><span>${spread.stock_price.toFixed(2)}</span>
          {spread.greeks && <>
            <span className="text-text-muted">Greeks</span>
            <span>
              {"\u0394"}{spread.greeks.delta > 0 ? "+" : ""}{spread.greeks.delta.toFixed(0)}
              {" "}{"\u0398"}{spread.greeks.theta > 0 ? "+" : ""}{spread.greeks.theta.toFixed(0)}/d
              {" "}{"\u03BD"}{spread.greeks.vega > 0 ? "+" : ""}{spread.greeks.vega.toFixed(0)}
            </span>
          </>}
        </div>
      </div>
    )}
    </div>
  );
}

const THESIS_COLORS: Record<string, { bg: string; text: string; label: string }> = {
  intact: { bg: "bg-surface-alt", text: "text-text", label: "INTACT" },
  strengthened: { bg: "bg-gain/10", text: "text-gain", label: "STRENGTHENED" },
  weakened: { bg: "bg-warn/10", text: "text-warn", label: "WEAKENED" },
  broken: { bg: "bg-loss/10", text: "text-loss", label: "BROKEN" },
};

function ResearchCard({ research: r, stock }: { research: HoldingResearch; stock?: RHStock }) {
  const thesis = THESIS_COLORS[r.thesis_status] || THESIS_COLORS.intact;
  const borderColor = r.thesis_status === "broken" ? "border-loss/50"
    : r.thesis_status === "weakened" ? "border-warn/50"
    : r.thesis_status === "strengthened" ? "border-gain/50"
    : "border-border";

  // Analyst upside/downside from current price
  const analystUpside = stock && r.analyst_target
    ? ((r.analyst_target - stock.current_price) / stock.current_price * 100)
    : null;

  return (
    <div className={`rounded-lg border p-3 ${borderColor}`}>
      {/* Header */}
      <div className="flex items-start justify-between mb-2">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="font-bold text-sm">{r.ticker}</span>
          <span className="text-xs text-text-muted">{r.company}</span>
          <span className={`px-1.5 py-0.5 rounded text-[0.55rem] font-bold ${thesis.bg} ${thesis.text}`}>
            {thesis.label}
          </span>
          {stock && (
            <span className="text-[0.55rem] font-data text-text-muted">
              {Math.round(stock.qty)} sh @ ${stock.avg_cost.toFixed(2)} {"\u2192"} ${stock.current_price.toFixed(2)}
            </span>
          )}
        </div>
        {stock && (
          <span className={`text-sm font-bold font-data ${stock.pl >= 0 ? "text-gain" : "text-loss"}`}>
            {stock.pl >= 0 ? "+" : ""}${stock.pl.toFixed(0)} <span className="text-[0.6rem]">({stock.pl_pct >= 0 ? "+" : ""}{stock.pl_pct.toFixed(1)}%)</span>
          </span>
        )}
      </div>

      {/* Fundamentals grid */}
      {(r.market_cap || r.revenue_ttm || r.analyst_target) && (
        <div className="flex flex-wrap gap-x-4 gap-y-0.5 mb-2 text-[0.55rem] font-data">
          {r.analyst_target && (
            <span className="text-text-muted">
              PT <span className={`font-semibold ${analystUpside !== null && analystUpside >= 0 ? "text-gain" : "text-loss"}`}>
                ${r.analyst_target}
              </span>
              {analystUpside !== null && (
                <span className={analystUpside >= 0 ? "text-gain" : "text-loss"}>
                  {" "}({analystUpside > 0 ? "+" : ""}{analystUpside.toFixed(0)}%)
                </span>
              )}
              {r.analyst_low && r.analyst_high && (
                <span className="text-text-muted"> [${r.analyst_low}-${r.analyst_high}]</span>
              )}
              {r.analyst_count ? <span className="text-text-muted"> {r.analyst_count}a</span> : null}
            </span>
          )}
          {r.market_cap && <span className="text-text-muted">Cap <span className="text-text">{r.market_cap}</span></span>}
          {r.revenue_ttm && (
            <span className="text-text-muted">
              Rev <span className="text-text">{r.revenue_ttm}</span>
              {r.revenue_growth && (
                <span className={r.revenue_growth.startsWith("-") ? "text-loss" : "text-gain"}> {r.revenue_growth} YoY</span>
              )}
            </span>
          )}
          {r.eps && (
            <span className="text-text-muted">
              EPS <span className={r.eps.startsWith("-") || r.eps.startsWith("$-") ? "text-loss" : "text-text"}>{r.eps}</span>
            </span>
          )}
          {r.cash && (
            <span className="text-text-muted">
              Cash <span className="text-text">{r.cash}</span>
              {r.cash_runway && <span> ({r.cash_runway})</span>}
            </span>
          )}
          {r.quarterly_burn && <span className="text-text-muted">Burn <span className="text-loss">{r.quarterly_burn}/qtr</span></span>}
          {r.gross_margin && <span className="text-text-muted">GM <span className="text-text">{r.gross_margin}</span></span>}
          {r.ps_ratio && <span className="text-text-muted">P/S <span className="text-text">{r.ps_ratio}x</span></span>}
          {r.next_earnings && (
            <span className={`${r.next_earnings_days && r.next_earnings_days <= 14 ? "text-warn font-semibold" : "text-text-muted"}`}>
              Earnings {r.next_earnings} {r.next_earnings_days ? `(${r.next_earnings_days}d)` : ""}
            </span>
          )}
        </div>
      )}

      {/* Outlook */}
      <div className="text-xs text-text mb-2">{r.outlook}</div>

      {/* Developments */}
      {r.developments.length > 0 && (
        <div className="space-y-1 mb-2">
          {r.developments.map((d, i) => (
            <div key={i} className={`pl-2 border-l-2 text-[0.6rem] py-0.5 ${
              d.impact === "positive" ? "border-l-gain" : d.impact === "negative" ? "border-l-loss" : "border-l-border"
            }`}>
              <div className="flex items-baseline gap-1.5">
                <span className={`font-semibold shrink-0 ${
                  d.impact === "positive" ? "text-gain" : d.impact === "negative" ? "text-loss" : "text-text-muted"
                }`}>{d.impact === "positive" ? "+" : d.impact === "negative" ? "-" : "~"}</span>
                <span className="text-text">{d.headline}</span>
                <span className="text-text-muted shrink-0">{d.date}</span>
              </div>
              {d.detail && <div className="text-text-muted mt-0.5 pl-3">{d.detail}</div>}
            </div>
          ))}
        </div>
      )}
      {r.developments.length === 0 && (
        <div className="text-[0.6rem] text-text-muted mb-2">No material developments in last 14 days.</div>
      )}

      {/* Risk */}
      <div className="text-[0.55rem] text-loss">
        Risk: {r.risk}
      </div>
    </div>
  );
}

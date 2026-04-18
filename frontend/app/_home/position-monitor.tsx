"use client";

import { useState, useRef, useCallback, useMemo } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { fetchRobinhoodPositions, fetchHoldingsResearch, fetchTradeArchitect, fetchHoldingDeepDive, type RHStock, type RHSpread, type RHPortfolioGreeks, type HoldingResearch, type StructuredTrade, type HoldingDiveResponse } from "@/lib/api";
import { MatrixLoader } from "@/components/matrix-loader";

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

// ── Trade Architect P/L chart + tooltip ──
function computeTradePL(t: StructuredTrade): { prices: number[]; pls: number[]; breakevens: number[] } {
  if (t.type === "stock") {
    const entry = t.entry;
    const lo = entry * 0.92, hi = entry * 1.08, pts = 80;
    const step = (hi - lo) / pts;
    const prices: number[] = [], pls: number[] = [];
    const qty = t.legs[0]?.qty ?? 1;
    for (let i = 0; i <= pts; i++) {
      const s = lo + i * step;
      prices.push(s); pls.push((s - entry) * qty);
    }
    return { prices, pls, breakevens: [entry] };
  }
  const optLegs = t.legs.filter(l => l.instrument !== "shares");
  const stockLegs = t.legs.filter(l => l.instrument === "shares");
  const strikes = optLegs.map(l => l.strike ?? 0).filter(s => s > 0);
  const allPrices = [...strikes, ...stockLegs.map(l => l.price)].filter(p => p > 0);
  if (allPrices.length < 1) return { prices: [], pls: [], breakevens: [] };
  const minP = Math.min(...allPrices), maxP = Math.max(...allPrices);
  const span = (maxP - minP) || minP * 0.1 || 1;
  const lo = minP - span * 2, hi = maxP + span * 2, pts = 100;
  const step = (hi - lo) / pts;
  const prices: number[] = [], pls: number[] = [], breakevens: number[] = [];
  for (let i = 0; i <= pts; i++) {
    const s = lo + i * step;
    let pl = 0;
    for (const leg of optLegs) {
      const sign = leg.action === "sell" ? 1 : -1;
      const strike = leg.strike ?? 0;
      const intrinsic = leg.instrument === "call" ? Math.max(s - strike, 0) : Math.max(strike - s, 0);
      pl += (sign * leg.price - sign * intrinsic) * 100 * leg.qty;
    }
    for (const leg of stockLegs) { pl += (s - leg.price) * leg.qty; }
    prices.push(s); pls.push(pl);
  }
  for (let i = 1; i < pls.length; i++) {
    if ((pls[i - 1] <= 0 && pls[i] > 0) || (pls[i - 1] >= 0 && pls[i] < 0)) {
      const ratio = Math.abs(pls[i - 1]) / (Math.abs(pls[i - 1]) + Math.abs(pls[i]));
      breakevens.push(prices[i - 1] + ratio * (prices[i] - prices[i - 1]));
    }
  }
  return { prices, pls, breakevens };
}

let _tradePlId = 0;
function TradePLChart({ trade }: { trade: StructuredTrade }) {
  const [clipId] = useState(() => `tpl-clip-${++_tradePlId}`);
  const { prices, pls, breakevens } = computeTradePL(trade);
  if (prices.length < 2) return null;
  const W = 300, topM = 12, botM = 14, chartH = 65, H = topM + chartH + botM;
  const padX = 4, w = W - padX * 2;
  const rawMin = Math.min(...pls), rawMax = Math.max(...pls);
  const absMax = Math.max(Math.abs(rawMin), Math.abs(rawMax), 1);
  const minP = Math.min(rawMin, -absMax * 0.2);
  const maxP = Math.max(rawMax, absMax * 0.2);
  const range = maxP - minP || 1;
  const zeroY = topM + chartH - ((0 - minP) / range) * chartH;
  const pMin = prices[0], pMax2 = prices[prices.length - 1], pRange = pMax2 - pMin || 1;
  const toX = (i: number) => padX + (i / (prices.length - 1)) * w;
  const priceToX = (p: number) => padX + ((p - pMin) / pRange) * w;
  const toY = (pl: number) => topM + chartH - ((pl - minP) / range) * chartH;
  const pathD = prices.map((_, i) => `${i === 0 ? "M" : "L"}${toX(i).toFixed(1)},${toY(pls[i]).toFixed(1)}`).join(" ");
  const fillD = pathD + `L${toX(prices.length - 1)},${zeroY}L${toX(0)},${zeroY}Z`;
  const spot = trade.entry;
  const spotInRange = spot >= pMin && spot <= pMax2;
  return (
    <svg width={W} height={H} style={{ display: "block" }}>
      <defs>
        <clipPath id={`${clipId}-a`}><rect x={padX} y={topM} width={w} height={Math.max(0, zeroY - topM)} /></clipPath>
        <clipPath id={`${clipId}-b`}><rect x={padX} y={zeroY} width={w} height={Math.max(0, topM + chartH - zeroY)} /></clipPath>
      </defs>
      <text x={padX} y={topM - 3} fill="#22c55e" fontSize="8" fontFamily="monospace">+${trade.max_profit.toLocaleString()}</text>
      <text x={W - padX} y={topM - 3} fill="#ef4444" fontSize="8" fontFamily="monospace" textAnchor="end">-${trade.max_risk.toLocaleString()}</text>
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
      {spotInRange && (
        <>
          <line x1={priceToX(spot)} x2={priceToX(spot)} y1={topM} y2={topM + chartH} stroke="#6366f1" strokeWidth="1" strokeDasharray="2,2" />
          <text x={priceToX(spot)} y={topM + 10} fill="#6366f1" fontSize="8" fontFamily="monospace" textAnchor="middle">${spot.toFixed(0)}</text>
        </>
      )}
    </svg>
  );
}

function TradeTooltip({ trade }: { trade: StructuredTrade }) {
  return (
    <div className="absolute z-50 bottom-full left-0 mb-1 p-2.5 rounded-lg border border-border-strong bg-surface shadow-xl" style={{ minWidth: 320 }}>
      <div className="text-[0.65rem] font-semibold text-text mb-1">{trade.label} — P/L at Expiration</div>
      <TradePLChart trade={trade} />
      <div className="grid grid-cols-2 gap-x-4 gap-y-0.5 mt-1.5 text-[0.55rem] font-data">
        <span className="text-text-muted">Max Profit</span><span className="text-gain font-semibold">${trade.max_profit.toLocaleString()}</span>
        <span className="text-text-muted">Max Risk</span><span className="text-loss font-semibold">${trade.max_risk.toLocaleString()}</span>
        <span className="text-text-muted">Breakeven</span><span>${trade.breakeven.toFixed(2)}</span>
        {trade.pop != null && <><span className="text-text-muted">POP</span><span>{trade.pop}%</span></>}
        <span className="text-text-muted">R:R</span><span>{trade.rr_ratio}x</span>
        <span className="text-text-muted">Delta</span><span>{trade.greeks.delta.toFixed(1)}</span>
        <span className="text-text-muted">Theta/day</span><span>${trade.greeks.theta.toFixed(2)}</span>
      </div>
    </div>
  );
}

export function PositionMonitorContent() {
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

  // Trade Architect
  const [archInput, setArchInput] = useState("");
  const [archResult, setArchResult] = useState("");
  const [archTrades, setArchTrades] = useState<StructuredTrade[]>([]);
  const [archSources, setArchSources] = useState<string[]>([]);
  const [archLoading, setArchLoading] = useState(false);
  const [archError, setArchError] = useState("");
  const [archRisk, setArchRisk] = useState<"conservative" | "moderate" | "aggressive">("moderate");
  const [archStrategy, setArchStrategy] = useState<"auto" | "sell" | "buy">("auto");
  const [archDirection, setArchDirection] = useState<"" | "bullish" | "bearish" | "neutral">("");
  const accountEquity = portfolio?.equity ?? 0;

  const submitArchitect = useCallback((deep = false) => {
    if (!archInput.trim() || archLoading) return;
    if (accountEquity <= 0) { setArchError("Portfolio data still loading — wait a moment."); return; }
    setArchLoading(true); setArchError(""); setArchResult(""); setArchTrades([]);
    const heldTickers = stocks.map(s => s.ticker);
    const portfolioCtx = stocks.map(s =>
      `${s.ticker}: ${s.qty} sh @ $${s.avg_cost.toFixed(2)} (now $${s.current_price.toFixed(2)}, ${s.pl_pct >= 0 ? "+" : ""}${s.pl_pct.toFixed(1)}%, $${s.market_value.toLocaleString()})`
    ).join("\n");
    fetchTradeArchitect(archInput, [], portfolioCtx, heldTickers, accountEquity, deep, archRisk, archStrategy, archDirection).then(res => {
      if (res.success) {
        setArchResult(res.analysis ?? "");
        setArchTrades(res.trades ?? []);
        setArchSources(res.context_sources ?? []);
      } else {
        setArchError(res.error || "Analysis failed.");
      }
    }).catch(e => setArchError(e instanceof Error ? e.message : "Request failed"))
      .finally(() => setArchLoading(false));
  }, [archInput, archLoading, accountEquity, stocks, archRisk, archStrategy, archDirection]);
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
    <div className="space-y-4 relative">
      {/* Matrix loader only for Trade Architect, not initial page load */}
      {/* Header (refresh control only — page-level h1 comes from Home wrapper) */}
      <div className="flex items-center justify-end">
        {dataUpdatedAt > 0 && <span className="text-text-muted text-xs mr-3">Updated {new Date(dataUpdatedAt).toLocaleTimeString()}</span>}
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
                            <td className="py-1 pr-2 text-text">{s.ticker} <span className="text-text-muted">{s.type.replace("Iron Condor", "IC").replace("Covered Call", "CC").replace("Bear Call", "BC").replace("Bull Put", "BP")}</span></td>
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

          {/* Trade Architect */}
          <div className="card">
            <div className="flex items-center gap-2 mb-2">
              <span className="metric-label">Trade Architect</span>
              <span className="text-[0.5rem] text-text-muted">Claude Opus · 14 data sources</span>
              {archSources.length > 0 && (
                <span className="text-[0.45rem] text-text-muted">[{archSources.join(", ")}]</span>
              )}
              {archTrades.length > 0 && !archLoading && (
                <button onClick={() => submitArchitect(false)}
                  className="ml-auto text-[0.5rem] text-text-muted hover:text-accent px-1.5 py-0.5 border border-border rounded">
                  Refresh Prices
                </button>
              )}
            </div>
            <div className="flex gap-2">
              <input type="text" value={archInput}
                onChange={e => setArchInput(e.target.value)}
                onKeyDown={e => { if (e.key === "Enter") submitArchitect(false); }}
                placeholder="e.g. 'hedge my RGTI' or 'sell premium on SPY' or 'balance my portfolio'"
                className="flex-1 px-3 py-2 border border-border rounded-lg text-sm bg-surface placeholder:text-text-muted"
                disabled={archLoading} />
              <button onClick={() => submitArchitect(false)} disabled={archLoading || !archInput.trim()}
                className="px-3 py-2 bg-accent text-white text-sm font-semibold rounded-lg hover:bg-accent-hover disabled:opacity-50 whitespace-nowrap">
                {archLoading ? "Analyzing..." : "Analyze"}
              </button>
              <button onClick={() => submitArchitect(true)} disabled={archLoading || !archInput.trim()}
                title="Claude Opus — deeper reasoning, slower"
                className="px-3 py-2 border border-accent/40 text-accent text-[0.65rem] font-semibold rounded-lg hover:bg-accent/10 disabled:opacity-50 whitespace-nowrap">
                Deep Analyze
              </button>
            </div>
            {/* Parameter controls */}
            <div className="flex items-center gap-3 mt-1.5">
              <div className="flex items-center gap-1">
                <span className="text-[0.55rem] text-text-muted">Risk:</span>
                {(["conservative", "moderate", "aggressive"] as const).map(r => (
                  <button key={r} onClick={() => setArchRisk(r)}
                    className={`px-1.5 py-0.5 text-[0.5rem] rounded ${archRisk === r ? "bg-accent/15 text-accent font-semibold" : "text-text-muted hover:text-text"}`}>
                    {r.charAt(0).toUpperCase() + r.slice(1)}
                  </button>
                ))}
              </div>
              <div className="flex items-center gap-1">
                <span className="text-[0.55rem] text-text-muted">Strategy:</span>
                {([["auto", "Auto"], ["sell", "Sell Premium"], ["buy", "Buy Premium"]] as const).map(([v, label]) => (
                  <button key={v} onClick={() => setArchStrategy(v as typeof archStrategy)}
                    className={`px-1.5 py-0.5 text-[0.5rem] rounded ${archStrategy === v ? "bg-accent/15 text-accent font-semibold" : "text-text-muted hover:text-text"}`}>
                    {label}
                  </button>
                ))}
              </div>
              <div className="flex items-center gap-1">
                <span className="text-[0.55rem] text-text-muted">Direction:</span>
                {([["", "Auto"], ["bullish", "Bull"], ["bearish", "Bear"], ["neutral", "Neutral"]] as const).map(([v, label]) => (
                  <button key={v} onClick={() => setArchDirection(v as typeof archDirection)}
                    className={`px-1.5 py-0.5 text-[0.5rem] rounded ${archDirection === v
                      ? v === "bullish" ? "bg-gain/15 text-gain font-semibold"
                      : v === "bearish" ? "bg-loss/15 text-loss font-semibold"
                      : "bg-accent/15 text-accent font-semibold"
                      : "text-text-muted hover:text-text"}`}>
                    {label}
                  </button>
                ))}
              </div>
            </div>
            {archLoading && (
              <div className="relative mt-3" style={{ minHeight: 320 }}>
                <MatrixLoader loading={true} status="GATHERING 14 DATA SOURCES... STRUCTURING TRADES..." />
              </div>
            )}
            {archError && <div className="text-xs text-loss mt-2">{archError}</div>}
            {archTrades.length > 0 && (
              <div className="mt-3 border-t border-border pt-3 space-y-3">
                {/* Context strip */}
                <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[0.55rem] font-data text-text-muted">
                  {archTrades[0]?.direction && (
                    <span className={`font-semibold ${archTrades[0].direction === "bullish" ? "text-gain" : archTrades[0].direction === "bearish" ? "text-loss" : "text-accent"}`}>
                      {archTrades[0].direction.toUpperCase()}
                    </span>
                  )}
                  {archTrades[0]?.vol_suggestion && <span>{archTrades[0].vol_suggestion}</span>}
                  {archTrades[0]?.signal_consensus && <span>Signal: <span className="text-accent">{archTrades[0].signal_consensus}</span></span>}
                  {archTrades[0]?.portfolio_delta_before != null && (
                    <span>Portfolio Δ: {archTrades[0].portfolio_delta_before.toFixed(0)}</span>
                  )}
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-[0.6rem] font-data">
                    <thead>
                      <tr className="text-text-muted border-b border-border">
                        <th className="text-left py-1 pr-2">Trade</th>
                        <th className="text-right px-2">Profit</th>
                        <th className="text-right px-2">Risk</th>
                        <th className="text-right px-2">R:R</th>
                        <th className="text-right px-2">POP</th>
                        <th className="text-right px-2">Delta</th>
                        <th className="text-right pl-2">Acct %</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(() => {
                        const bestFit = Math.max(...archTrades.map(x => x.account_fit ?? 0));
                        const tied = archTrades.filter(x => (x.account_fit ?? 0) === bestFit);
                        const winner = tied.sort((a, b) => b.rr_ratio - a.rr_ratio)[0];
                        const bestIdx = archTrades.indexOf(winner);
                        return archTrades.map((t, i) => (
                          <tr key={i} className={`border-b border-border/50 ${i === bestIdx ? "bg-accent/5" : ""}`}>
                            <td className="py-1.5 pr-2 font-semibold text-text">
                              {t.label}
                              {i === bestIdx && <span className="ml-1 text-[0.45rem] text-accent font-bold">BEST{t.account_fit != null ? ` · Fit ${t.account_fit}` : ""}</span>}
                            </td>
                            <td className="text-right px-2 text-gain">${t.max_profit.toLocaleString()}</td>
                            <td className="text-right px-2 text-loss">${t.max_risk.toLocaleString()}</td>
                            <td className="text-right px-2 font-semibold">{t.rr_ratio}x</td>
                            <td className="text-right px-2">{t.pop != null ? `${t.pop}%` : "—"}</td>
                            <td className="text-right px-2">{t.greeks.delta.toFixed(1)}</td>
                            <td className={`text-right pl-2 ${t.risk_pct_of_account && t.risk_pct_of_account > 5 ? "text-loss" : ""}`}>
                              {t.risk_pct_of_account != null ? `${t.risk_pct_of_account.toFixed(1)}%` : "—"}
                            </td>
                          </tr>
                        ));
                      })()}
                    </tbody>
                  </table>
                </div>
                <div className={`grid grid-cols-1 ${archTrades.length >= 3 ? "md:grid-cols-3" : "md:grid-cols-2"} gap-2`}>
                  {archTrades.map((t, i) => {
                    const colors: Record<string, string> = { stock: "border-[#58a6ff]", options: "border-[#a371f7]", combination: "border-[#d29922]" };
                    const icons: Record<string, string> = { stock: "S", options: "O", combination: "C" };
                    return (
                      <div key={i} className={`relative group rounded-lg border-l-4 ${colors[t.type] || "border-border"} border border-border p-3 bg-surface`}>
                        <div className="flex items-center gap-2 mb-2">
                          <span className="w-6 h-6 rounded-full flex items-center justify-center text-[0.6rem] font-bold bg-surface-alt text-text-muted">
                            {icons[t.type] || "?"}
                          </span>
                          <span className="text-[0.7rem] font-bold text-text">{t.label}</span>
                          <button onClick={(e) => {
                            const text = t.legs.map(l =>
                              l.instrument === "shares" ? `${l.action} ${l.qty} ${l.ticker} @ $${l.price}`
                              : `${l.action} ${l.qty}× ${l.ticker} $${l.strike} ${l.instrument} ${l.exp} @ $${l.price}`
                            ).join("\n");
                            navigator.clipboard.writeText(text);
                            const btn = e.currentTarget; btn.textContent = "Copied!";
                            setTimeout(() => { btn.textContent = "Copy"; }, 1500);
                          }} className="ml-auto text-[0.5rem] text-text-muted hover:text-accent px-1.5 py-0.5 border border-border rounded">Copy</button>
                        </div>
                        <div className="space-y-0.5 mb-2">
                          {t.legs.map((l, li) => (
                            <div key={li} className="flex items-center gap-2 text-[0.6rem] font-data">
                              <span className={`font-semibold ${l.action === "buy" ? "text-gain" : "text-loss"}`}>{l.action.toUpperCase()}</span>
                              <span className="text-text">
                                {l.qty}× {l.instrument === "shares" ? `${l.ticker} shares` : `${l.ticker} $${l.strike} ${l.instrument}`}
                                {l.exp && <span className="text-text-muted ml-1">{l.exp}</span>}
                              </span>
                              <span className="text-text-muted ml-auto">${l.price.toFixed(2)}</span>
                            </div>
                          ))}
                        </div>
                        <div className="grid grid-cols-2 gap-x-3 gap-y-0.5 text-[0.55rem] font-data border-t border-border/50 pt-1.5">
                          <span className="text-text-muted">Profit</span><span className="text-gain text-right">${t.max_profit.toLocaleString()}</span>
                          <span className="text-text-muted">Risk</span><span className="text-loss text-right">${t.max_risk.toLocaleString()}</span>
                          <span className="text-text-muted">R:R</span><span className="text-right">{t.rr_ratio}x</span>
                          {t.pop != null && <><span className="text-text-muted">POP</span><span className="text-right">{t.pop}%</span></>}
                          {t.breakeven > 0 && <><span className="text-text-muted">Breakeven</span><span className="text-right">${t.breakeven.toFixed(2)}{t.breakeven_upper ? ` / $${t.breakeven_upper.toFixed(2)}` : ""}</span></>}
                          {t.stop != null && <><span className="text-text-muted">Stop</span><span className="text-right text-loss">${t.stop.toFixed(2)}</span></>}
                          {t.target != null && <><span className="text-text-muted">Target</span><span className="text-right text-gain">${t.target.toFixed(2)}</span></>}
                          <span className="text-text-muted">Timeframe</span><span className="text-right">{t.timeframe}</span>
                          {t.risk_pct_of_account != null && (
                            <><span className="text-text-muted">Account Risk</span>
                            <span className={`text-right font-semibold ${t.risk_pct_of_account > 3 ? "text-loss" : "text-text"}`}>{t.risk_pct_of_account.toFixed(1)}%</span></>
                          )}
                        </div>
                        {/* Greeks */}
                        <div className="flex flex-wrap gap-x-2 gap-y-0.5 mt-1.5 pt-1.5 border-t border-border/50 text-[0.5rem] font-data text-text-muted">
                          <span>Δ {t.greeks.delta.toFixed(1)}</span>
                          <span>Θ ${t.greeks.theta.toFixed(2)}/d</span>
                          <span>Γ {t.greeks.gamma.toFixed(3)}</span>
                          <span>V {t.greeks.vega.toFixed(1)}</span>
                          {t.portfolio_delta_before != null && (
                            <span className="text-text-secondary">
                              Port Δ: {t.portfolio_delta_before.toFixed(0)} → <span className="text-text font-semibold">{t.portfolio_delta_after!.toFixed(0)}</span>
                            </span>
                          )}
                        </div>
                        {/* Account fit + backtest + save */}
                        <div className="flex flex-wrap gap-1 mt-1">
                          {t.account_fit != null && (
                            <span className={`text-[0.45rem] px-1 py-0.5 rounded ${t.account_fit >= 70 ? "bg-gain-bg text-gain" : t.account_fit >= 40 ? "bg-warn-bg text-warn" : "bg-loss-bg text-loss"}`}>
                              Fit: {t.account_fit}/100
                            </span>
                          )}
                          {t.hist_winrate != null && (
                            <span className={`text-[0.45rem] px-1 py-0.5 rounded ${t.hist_winrate >= 65 ? "bg-gain-bg text-gain" : t.hist_winrate >= 50 ? "bg-warn-bg text-warn" : "bg-loss-bg text-loss"}`}>
                              Backtest: {t.hist_winrate}% WR ({t.hist_trials} trials)
                            </span>
                          )}
                          <button onClick={() => {
                            const saved = JSON.parse(localStorage.getItem("saved_trades") || "[]");
                            saved.unshift({
                              ticker: t.legs[0]?.ticker || "?", label: t.label, type: t.type,
                              max_profit: t.max_profit, max_risk: t.max_risk, rr_ratio: t.rr_ratio,
                              pop: t.pop, breakeven: t.breakeven, timeframe: t.timeframe,
                              legs: t.legs.map(l => `${l.action} ${l.qty}× ${l.instrument === "shares" ? "shares" : `$${l.strike} ${l.instrument}`}`).join(", "),
                              thesis: archInput, timestamp: new Date().toISOString(),
                            });
                            localStorage.setItem("saved_trades", JSON.stringify(saved.slice(0, 20)));
                          const b = document.querySelector(`[data-save-idx="${i}"]`) as HTMLButtonElement;
                          if (b) { b.textContent = "Saved!"; setTimeout(() => { b.textContent = "Save"; }, 1500); }
                          }} data-save-idx={i} className="text-[0.45rem] text-text-muted hover:text-accent px-1 py-0.5 border border-border rounded">
                            Save
                          </button>
                        </div>
                        {/* P/L tooltip on hover */}
                        <div className="hidden group-hover:block">
                          <TradeTooltip trade={t} />
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
            {archResult && (
              <div className="mt-3 border-t border-border pt-3">
                <div className="text-[0.65rem] font-bold text-accent uppercase tracking-wider mb-2">AI Assessment</div>
                <div className="text-xs leading-relaxed text-text arch-assessment"
                  dangerouslySetInnerHTML={{ __html: (() => {
                    let h = archResult
                      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
                      .replace(/^\s*---+\s*$/gm, "")
                      .replace(/^#{1,3}\s+(.+)$/gm, '<div class="arch-h">$1</div>')
                      .replace(/^([A-Z]+(?:\s+[A-Z:]+){1,6})$/gm, (m) => m.length >= 8 ? `<div class="arch-h">${m}</div>` : m)
                      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
                      .replace(/\*([^*]{4,})\*/g, '<em>$1</em>')
                      .replace(/[\u{FFFD}\u{FE0F}]/gu, "")
                      .replace(/^[\u{1F534}\u{1F7E1}\u{26A0}\u{2022}\u{FFFD}•\-\*]\s*[\u{FE0F}]?\s*(.+)$/gmu, (_, content) => {
                        const isRisk = /risk|warn|cpi|nfp|earn|liquid|slippage/i.test(content);
                        const cleaned = content.replace(/^[\u{1F534}\u{1F7E1}\u{26A0}\u{FFFD}\u{FE0F}]\s*/gu, "");
                        return `<div class="${isRisk ? "arch-risk" : "arch-bullet"}">${cleaned}</div>`;
                      })
                      // Markdown tables
                      .replace(/(\|.+\|[\n])+/g, (tableBlock: string) => {
                        const rows = tableBlock.trim().split("\n").filter((r: string) => r.includes("|"));
                        if (rows.length < 2) return tableBlock;
                        const parseRow = (r: string) => r.split("|").filter((c: string) => c.trim()).map((c: string) => c.trim());
                        const isSep = (r: string) => /^\|[\s\-:]+\|$/.test(r.trim());
                        const headers = parseRow(rows[0]);
                        const dataRows = rows.filter((r: string) => !isSep(r)).slice(1);
                        return '<table class="arch-table"><thead><tr>' +
                          headers.map((h: string) => `<th>${h}</th>`).join("") +
                          '</tr></thead><tbody>' +
                          dataRows.map((r: string) => '<tr>' + parseRow(r).map((c: string) => `<td>${c}</td>`).join("") + '</tr>').join("") +
                          '</tbody></table>';
                      })
                      .replace(/\n\n+/g, '</p><p class="arch-p">')
                      .replace(/^/, '<p class="arch-p">') + '</p>';
                    h = h.replace(/<p class="arch-p">\s*<\/p>/g, "");
                    return h;
                  })()}} />
                <style jsx>{`
                  .arch-assessment :global(.arch-h) {
                    font-size: 0.7rem; font-weight: 700; color: var(--color-accent);
                    text-transform: uppercase; letter-spacing: 0.05em;
                    margin-top: 0.85rem; margin-bottom: 0.3rem;
                    padding-bottom: 0.2rem; border-bottom: 1px solid var(--color-border);
                  }
                  .arch-assessment :global(.arch-h:first-child) { margin-top: 0; }
                  .arch-assessment :global(.arch-p) { margin-bottom: 0.4rem; line-height: 1.6; }
                  .arch-assessment :global(.arch-risk) {
                    padding: 0.3rem 0.5rem; margin: 0.2rem 0;
                    border-left: 2px solid var(--color-loss); background: var(--color-loss-bg);
                    border-radius: 0 4px 4px 0; font-size: 0.7rem; line-height: 1.5;
                  }
                  .arch-assessment :global(.arch-bullet) {
                    padding: 0.2rem 0 0.2rem 0.65rem; margin: 0.1rem 0;
                    border-left: 2px solid var(--color-border); line-height: 1.5;
                  }
                  .arch-assessment :global(strong) { color: var(--color-text); }
                  .arch-assessment :global(.arch-table) {
                    width: 100%; border-collapse: collapse; font-size: 0.65rem;
                    font-family: var(--font-mono); margin: 0.5rem 0;
                  }
                  .arch-assessment :global(.arch-table th) {
                    text-align: left; padding: 0.25rem 0.5rem; font-weight: 700;
                    border-bottom: 1px solid var(--color-border); color: var(--color-text-secondary);
                  }
                  .arch-assessment :global(.arch-table td) {
                    padding: 0.2rem 0.5rem; border-bottom: 1px solid var(--color-border);
                  }
                `}</style>
              </div>
            )}
          </div>

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
                  <StockRow key={s.ticker} stock={s} equity={portfolio.equity} />
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
                <div className="flex flex-col items-center gap-3 py-8">
                  <div className="w-8 h-8 border-3 border-accent border-t-transparent rounded-full animate-spin" />
                  <span className="text-sm text-text-secondary font-semibold">Researching {stocks.length} holdings...</span>
                  <span className="text-xs text-text-muted">Grok searching X + yfinance fundamentals</span>
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

function StockRow({ stock, equity }: { stock: RHStock; equity: number }) {
  const plColor = stock.pl >= 0 ? "text-gain" : "text-loss";
  const priceColor = stock.current_price >= stock.avg_cost ? "text-gain" : "text-loss";
  const daysHeld = stock.entry_date ? Math.max(0, Math.round((Date.now() - new Date(stock.entry_date).getTime()) / 86400000)) : null;

  // Position analysis
  const concentration = equity > 0 ? (stock.market_value / equity) * 100 : 0;
  const distFromEntry = ((stock.current_price - stock.avg_cost) / stock.avg_cost) * 100;

  // Verdict
  let signal: { text: string; color: string };
  let action = "";
  if (stock.pl_pct <= -40) {
    signal = { text: "REVIEW", color: "text-loss" };
    action = `Down ${Math.abs(stock.pl_pct).toFixed(0)}% from entry. Consider tax-loss harvesting or cutting to reduce drawdown.`;
  } else if (stock.pl_pct <= -20) {
    signal = { text: "UNDERWATER", color: "text-warn" };
    action = `${Math.abs(distFromEntry).toFixed(0)}% below cost basis. ${concentration > 20 ? "Position oversized — consider trimming." : "Monitor for support break."}`;
  } else if (stock.pl_pct >= 30) {
    signal = { text: "PROFITABLE", color: "text-gain" };
    action = `Up ${stock.pl_pct.toFixed(0)}%. Consider selling ${Math.round(stock.qty * 0.3)} shares to lock in gains.`;
  } else if (concentration > 25) {
    signal = { text: "CONCENTRATED", color: "text-warn" };
    action = `${concentration.toFixed(0)}% of portfolio — consider reducing to < 15%.`;
  } else if (stock.pl >= 0) {
    signal = { text: "HOLD", color: "text-gain" };
    action = "";
  } else {
    signal = { text: "HOLD", color: "text-text-muted" };
    action = "";
  }

  return (
    <div className={`rounded-lg border px-3 py-2 text-xs font-data ${
      stock.pl_pct < -15 ? "border-loss/30" : stock.pl_pct > 15 ? "border-gain/30" : "border-border"
    }`}>
      <div className="flex items-center gap-3">
        <span className="font-bold text-sm w-14">{stock.ticker}</span>
        <span className="text-text-muted">{Math.round(stock.qty)} sh</span>
        <span className="text-text-muted">${stock.avg_cost.toFixed(2)}</span>
        <span className={priceColor}>${stock.current_price.toFixed(2)}</span>
        {stock.theme && <span className="text-[0.5rem] text-text-muted">{stock.theme}</span>}
        {daysHeld !== null && <span className="text-[0.5rem] text-text-muted">{daysHeld}d</span>}
        <span className="ml-auto" />
        <span className="text-text-muted">${stock.market_value.toLocaleString()}</span>
        <span className={`font-semibold ${plColor}`}>
          {stock.pl >= 0 ? "+" : ""}${stock.pl.toFixed(0)}
        </span>
        <span className={`text-[0.6rem] ${plColor}`}>
          {stock.pl_pct >= 0 ? "+" : ""}{stock.pl_pct.toFixed(1)}%
        </span>
        <span className={`text-[0.5rem] font-semibold ${signal.color}`}>{signal.text}</span>
      </div>
      {action && (
        <div className={`text-[0.55rem] mt-1 ${signal.color === "text-loss" ? "text-loss" : signal.color === "text-warn" ? "text-warn" : "text-text-muted"}`}>
          → {action}
          {concentration > 10 && <span className="text-text-muted ml-2">({concentration.toFixed(0)}% of portfolio)</span>}
        </div>
      )}
    </div>
  );
}

function SpreadRow({ spread }: { spread: RHSpread }) {
  const [hovered, setHovered] = useState(false);
  // Lightweight MC for the card (1000 sims, recomputed when spread data changes)
  const quickMC = useMemo(() => monteCarloSpread(spread, 1000), [spread]);
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

  // Credit vs debit spread detection
  const isCredit = spread.net_premium > 0;
  const costToClose = Math.abs(spread.current_value);
  const isNaked = spread.legs.length === 1;
  const isDefined = spread.legs.length >= 2; // defined-risk (vertical, IC, etc.)

  // Spread width calculation (for multi-leg spreads, find widest wing)
  const callLegs = spread.legs.filter(l => l.opt_type === "call").map(l => l.strike).sort((a, b) => a - b);
  const putLegs = spread.legs.filter(l => l.opt_type === "put").map(l => l.strike).sort((a, b) => a - b);
  const callWidth = callLegs.length >= 2 ? Math.abs(callLegs[callLegs.length - 1] - callLegs[0]) : 0;
  const putWidth = putLegs.length >= 2 ? Math.abs(putLegs[putLegs.length - 1] - putLegs[0]) : 0;
  const spreadWidth = Math.max(callWidth, putWidth, Math.abs((spread.legs[0]?.strike || 0) - (spread.legs[1]?.strike || spread.legs[0]?.strike || 0)));
  const totalWidth = spreadWidth * 100 * spread.qty;

  // Max profit / max loss depend on position type
  let maxProfit: number;
  let maxLossAtExp: number;
  if (isNaked && isCredit) {
    // Naked short: max profit = credit, max loss = undefined (use 0 to flag)
    maxProfit = spread.net_premium;
    maxLossAtExp = 0; // undefined risk — handled specially in verdicts
  } else if (isNaked && !isCredit) {
    // Naked long call: max profit = unlimited, naked long put: max profit = strike × 100 × qty - debit
    // Use 0 to indicate unlimited/unknown
    const leg = spread.legs[0];
    maxProfit = leg?.opt_type === "put" ? (leg.strike * 100 * spread.qty) - Math.abs(spread.net_premium) : 0;
    maxLossAtExp = Math.abs(spread.net_premium); // debit paid = max loss
  } else if (isCredit && isDefined) {
    // Defined-risk credit spread: max profit = credit, max loss = width - credit
    maxProfit = spread.net_premium;
    maxLossAtExp = totalWidth - spread.net_premium;
  } else {
    // Defined-risk debit spread: max profit = width - debit, max loss = debit
    maxProfit = totalWidth > 0 ? totalWidth - Math.abs(spread.net_premium) : 0;
    maxLossAtExp = Math.abs(spread.net_premium);
  }
  const pctCaptured = maxProfit > 0 ? (spread.pl / maxProfit) * 100 : 0;

  // Mark-to-market loss
  const mtmLoss = spread.pl < 0 ? Math.abs(spread.pl) : 0;
  const pctOfMaxRisk = maxLossAtExp > 0 ? (mtmLoss / maxLossAtExp) * 100 : 0;

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

  // Theta recovery math
  const thetaNeededPerDay = spread.pl < 0 && dte > 0 ? Math.abs(spread.pl) / dte : 0;
  const thetaDeficit = thetaNeededPerDay > 0 && dailyTheta > 0 ? thetaNeededPerDay - dailyTheta : 0;

  // Breakeven via theta: days for theta to recover the loss
  const breakEvenDays = spread.pl < 0 && dailyTheta > 0 ? Math.ceil(Math.abs(spread.pl) / dailyTheta) : null;

  // Verdict
  let signal: { text: string; color: string };
  let verdict: string;
  let action = "";  // specific action recommendation
  // MTM loss exceeds max expiration loss = time value inflating close cost
  const mtmExceedsMax = mtmLoss > 0 && maxLossAtExp > 0 && mtmLoss > maxLossAtExp;

  if (worstDanger > 0 && !canRecover) {
    signal = { text: "MANAGE", color: "text-loss" };
    if (isNaked && isCredit) {
      // Naked short — undefined max loss, NEVER advise holding through a breach
      verdict = `Breached — UNDEFINED RISK. ${dte}d left. Theta deficit $${thetaDeficit.toFixed(0)}/d.`;
      action = `Close immediately for $${mtmLoss.toLocaleString()} loss. Naked position — loss can grow without limit.`;
    } else if (mtmExceedsMax && isDefined) {
      // Defined-risk only: closing NOW locks in a worse loss than holding — time value inflating cost
      verdict = `Breached — MTM loss $${mtmLoss.toLocaleString()} exceeds max expiration loss $${maxLossAtExp.toLocaleString()}. Time value inflating close cost. ${dte}d left.`;
      action = `HOLD preferred — closing locks in $${mtmLoss.toLocaleString()} loss vs $${maxLossAtExp.toLocaleString()} max at expiration. Theta deficit $${thetaDeficit.toFixed(0)}/d (need $${thetaNeededPerDay.toFixed(0)}, collecting $${dailyTheta.toFixed(0)}).`;
    } else if (spread.type.includes("Call") && spread.stock_price) {
      const rollStrike = Math.ceil(spread.stock_price * 1.05);
      verdict = `Breached — need $${thetaNeededPerDay.toFixed(0)}/d theta, collecting $${dailyTheta.toFixed(0)}/d (deficit $${thetaDeficit.toFixed(0)}/d). ${dte}d left.`;
      action = `Close for $${mtmLoss.toLocaleString()} loss${maxLossAtExp > 0 ? ` (max at exp $${maxLossAtExp.toLocaleString()})` : ""}, or roll short strike to $${rollStrike}+ (next monthly).`;
    } else {
      verdict = `Breached — need $${thetaNeededPerDay.toFixed(0)}/d theta, collecting $${dailyTheta.toFixed(0)}/d (deficit $${thetaDeficit.toFixed(0)}/d). ${dte}d left.`;
      action = maxLossAtExp > 0
        ? `Close for $${mtmLoss.toLocaleString()} loss. Max at expiration: $${maxLossAtExp.toLocaleString()} (${pctOfMaxRisk.toFixed(0)}% consumed).`
        : `Close for $${mtmLoss.toLocaleString()} loss.`;
    }
  } else if (worstDanger > 0 && canRecover) {
    signal = { text: "HOLD — recoverable", color: "text-warn" };
    verdict = `Breached but theta recovers in ${thetaRecoveryDays}d (${dte}d left). ${thetaWinning ? "Theta winning the race." : "Delta could overrun — watch closely."}`;
    if (breakEvenDays) action = `Breakeven in ~${breakEvenDays}d at current theta rate.`;
  } else if (dte <= 5) {
    signal = { text: "EXPIRING", color: "text-loss" };
    verdict = `${dte}d to ${expDate}. ${pctCaptured >= 0 ? `Close for ${pctCaptured.toFixed(0)}% profit.` : "Close to limit further loss."}`;
    action = pctCaptured >= 0 ? "Close now — gamma risk accelerates." : `Close for $${mtmLoss.toLocaleString()} loss to avoid assignment risk.`;
  } else if (pctCaptured >= 50 && isCredit) {
    signal = { text: "CLOSE", color: "text-gain" };
    verdict = `${pctCaptured.toFixed(0)}% of max profit captured. Standard management = take the win.`;
    action = `GTC limit to close at $${(maxProfit * 0.5 / (100 * spread.qty)).toFixed(2)} debit.`;
  } else if (thetaWinning && spread.pl < 0) {
    signal = { text: "HOLD — theta winning", color: "text-text-muted" };
    verdict = `Underwater ${pctCaptured.toFixed(0)}% but theta ($${dailyTheta.toFixed(0)}/d) > avg delta loss ($${Math.abs(posDelta * dailyMove).toFixed(0)}/d).`;
    if (breakEvenDays) action = `Breakeven in ~${breakEvenDays}d at current theta. ${breakEvenDays < dte ? "On track." : "Tight — may need to manage."}`;
  } else if (spread.pl >= 0) {
    signal = { text: "HOLD — profitable", color: "text-gain" };
    verdict = `${pctCaptured.toFixed(0)}% captured, target 50%. ${dte}d left.${thetaWinning ? " Theta accelerating." : ""}`;
    const daysToTarget = dailyTheta > 0 ? Math.ceil((maxProfit * 0.5 - spread.pl) / dailyTheta) : null;
    if (daysToTarget && daysToTarget > 0) action = `Target 50% ($${(maxProfit * 0.5).toFixed(0)}) in ~${daysToTarget}d at current theta.`;
  } else {
    signal = { text: "HOLD", color: "text-text-muted" };
    verdict = `${pctCaptured.toFixed(0)}% captured. ${dte}d to ${expDate}. ${dailyTheta > 0 ? `Collecting $${dailyTheta.toFixed(0)}/d theta.` : ""}`;
    if (breakEvenDays && dailyTheta > 0) action = `Breakeven in ~${breakEvenDays}d at $${dailyTheta.toFixed(0)}/d theta.`;
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
          {action && (
            <div className={`text-[0.55rem] mt-0.5 font-semibold ${signal.color === "text-loss" ? "text-loss" : signal.color === "text-gain" ? "text-gain" : "text-accent"}`}>
              → {action}
            </div>
          )}
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
          {maxProfit > 0 ? (
            <>
              <div className="flex-1 h-1.5 rounded-full bg-surface-alt border border-border relative overflow-hidden">
                {pctCaptured > 0 ? (
                  <div className="absolute inset-y-0 left-0 bg-gain rounded-full" style={{ width: `${Math.min(pctCaptured, 100)}%` }} />
                ) : pctCaptured < 0 ? (
                  <div className="absolute inset-y-0 left-0 bg-loss rounded-full" style={{ width: `${Math.min(Math.abs(pctCaptured), 100)}%` }} />
                ) : null}
                {isCredit && <div className="absolute inset-y-0 w-px bg-text-muted" style={{ left: "50%" }} />}
              </div>
              <span className={`shrink-0 font-semibold ${pctCaptured >= 50 ? "text-gain" : pctCaptured < 0 ? "text-loss" : "text-text"}`}>
                {pctCaptured.toFixed(0)}%
              </span>
            </>
          ) : (
            <span className="text-text-muted text-[0.5rem]">unlimited upside</span>
          )}
          <span className="text-text-muted shrink-0">
            ${Math.abs(spread.net_premium).toFixed(0)} {isCredit ? "in" : "out"} · ${costToClose.toFixed(0)} {isCredit ? "out" : "in"}
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
  strengthened: { bg: "bg-gain-bg", text: "text-gain", label: "STRENGTHENED" },
  weakened: { bg: "bg-warn-bg", text: "text-warn", label: "WEAKENED" },
  broken: { bg: "bg-loss-bg", text: "text-loss", label: "BROKEN" },
};

function ResearchCard({ research: r, stock }: { research: HoldingResearch; stock?: RHStock }) {
  const [dive, setDive] = useState<HoldingDiveResponse | null>(null);
  const [diveLoading, setDiveLoading] = useState(false);
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

      {/* Deep Dive */}
      <div className="mt-2 pt-2 border-t border-border/50">
        {!dive && !diveLoading && stock && (
          <button
            onClick={async () => {
              setDiveLoading(true);
              try {
                const res = await fetchHoldingDeepDive(stock);
                setDive(res);
              } catch { setDive({ success: false, ticker: r.ticker, verdict: "", error: "Request failed" }); }
              finally { setDiveLoading(false); }
            }}
            className="text-[0.55rem] font-semibold text-accent hover:text-accent-hover transition-colors">
            Deep Dive — hold, add, trim, or close?
          </button>
        )}
        {diveLoading && (
          <div className="flex items-center gap-2 py-2">
            <div className="w-3 h-3 border-2 border-accent border-t-transparent rounded-full animate-spin" />
            <span className="text-[0.55rem] text-text-muted">Analyzing {r.ticker} with 5 data sources + Claude...</span>
          </div>
        )}
        {dive && dive.success && (
          <div className="mt-2 rounded-lg border border-border bg-surface p-3">
            {/* Verdict badge */}
            <div className="flex items-center gap-2 mb-2">
              <span className={`inline-block px-2.5 py-1 rounded text-[0.65rem] font-bold ${
                dive.verdict === "CLOSE" ? "bg-loss-bg text-loss border border-loss/30" :
                dive.verdict === "TRIM" ? "bg-warn-bg text-warn border border-warn/30" :
                dive.verdict === "ADD" ? "bg-gain-bg text-gain border border-gain/30" :
                "bg-info-bg text-info border border-info/30"
              }`}>
                {dive.verdict}
              </span>
              <span className="text-[0.55rem] text-text-muted">Claude analysis · {dive.sources?.length ?? 0} sources</span>
              <button onClick={() => { setDive(null); }} className="text-[0.5rem] text-text-muted hover:text-accent ml-auto">Re-analyze</button>
            </div>
            {/* Analysis text — structured rendering */}
            {(() => {
              let text = dive.analysis ?? "";
              // Strip verdict header (shown as badge)
              text = text.replace(/^##?\s*VERDICT:?\s*\w+\s*/im, "").replace(/^\s*---+\s*$/gm, "").trim();

              // Split into: body paragraphs + KEY RISKS section
              // Handle **Key Risks**, ## Key Risks, Key Risks:, etc.
              const riskMatch = text.match(/\*{0,2}\s*#{0,2}\s*Key\s*Risks?\s*\*{0,2}:?\s*\n?([\s\S]*$)/i);
              const bodyText = riskMatch && riskMatch.index != null ? text.slice(0, riskMatch.index).trim() : text;
              const risksText = riskMatch ? riskMatch[1].trim() : "";

              // Parse risks into array — handle ** bold markers as bullet prefixes
              const risks = risksText
                .split(/\n(?=\*{1,2}[^*]|[•\-]|\d\.)/g)
                .map(r => r.replace(/^\*{1,2}\s*/, "").replace(/^[•\-]\s*/, "").replace(/^\d+\.\s*/, "").replace(/\*{1,2}\s*$/, "").trim())
                .filter(r => r.length > 10);

              return (
                <>
                  <div className="text-xs leading-relaxed text-text mb-2"
                    dangerouslySetInnerHTML={{ __html: bodyText
                      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
                      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
                      .replace(/\*{2,}/g, "")  // strip orphaned ** markers
                    }} />
                  {risks.length > 0 && (
                    <div className="space-y-1.5">
                      <div className="text-[0.6rem] font-bold text-loss uppercase tracking-wider">Key Risks</div>
                      {risks.map((risk, ri) => {
                        // Split on colon to get title + detail
                        const colonIdx = risk.indexOf(":");
                        const title = colonIdx > 0 ? risk.slice(0, colonIdx).trim() : "";
                        const detail = colonIdx > 0 ? risk.slice(colonIdx + 1).trim() : risk;
                        return (
                          <div key={ri} className="pl-2 border-l-2 border-loss/30 text-[0.65rem] leading-relaxed">
                            {title && <strong className="text-loss">{title}:</strong>}{" "}
                            <span className="text-text" dangerouslySetInnerHTML={{ __html: detail
                              .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
                              .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
                              .replace(/\*{2,}/g, "")
                            }} />
                          </div>
                        );
                      })}
                    </div>
                  )}
                </>
              );
            })()}
            {dive.sources && (
              <div className="text-[0.5rem] text-text-muted mt-2 pt-1.5 border-t border-border/50">Sources: {dive.sources.join(" · ")}</div>
            )}
          </div>
        )}
        {dive && !dive.success && (
          <div className="text-[0.55rem] text-loss mt-1">{dive.error}</div>
        )}
      </div>
    </div>
  );
}

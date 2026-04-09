"use client";

import { useState, useRef, useCallback, useEffect, Fragment } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { fetchDailyBriefing, fetchMorningNote, fetchNewsSearch, fetchNewsVerify, fetchNewsAnalysis, fetchTradeArchitect, fetchPolymarket, fetchPolymarketHistory, fetchRobinhoodPositions, fetchStrategyScan, fetchVolAnalysis, addPosition, type DailyBriefingResult, type NewsItem, type PolymarketEvent, type PolymarketHistoryPoint, type RHSpread, type RHStock, type StrategyScanResult, type VolAnalysis } from "@/lib/api";
import { TradingViewChart } from "@/components/tradingview-chart";
import { LightweightChart } from "@/components/lightweight-chart";

const DEFAULT_WATCHLIST = ["SPY","QQQ","AAPL","MSFT","NVDA","TSLA","AMD","AMZN","META","GOOGL","NFLX","GLD","SMH","XLF","TLT","JPM","BA"];

// Strategy families for signal consensus
const SIG_FAMILIES: Record<string, string[]> = {
  trend: ["sma_cross", "ema_cross", "golden_cross", "macd", "donchian", "atr_trail", "momentum", "adx_di", "parabolic_sar", "ichimoku", "tema_cross"],
  mean_rev: ["rsi_ob_os", "mean_rev", "bb_breakout", "zscore_mr", "stochastic", "cci", "williams_r"],
  volume: ["obv_divergence"],
  composite: ["trend_mr_composite", "trend_bb_composite"],
  calendar: ["turn_of_month", "halloween"],
};
const ALL_STRATS = Object.values(SIG_FAMILIES).flat();
const STRAT_LABELS: Record<string, string> = {
  sma_cross:"SMA", ema_cross:"EMA", golden_cross:"Golden", macd:"MACD", donchian:"Donchian",
  atr_trail:"ATR", momentum:"Mom", adx_di:"ADX", parabolic_sar:"SAR", ichimoku:"Ichimoku", tema_cross:"TEMA",
  rsi_ob_os:"RSI", mean_rev:"BB MR", bb_breakout:"BB Break", zscore_mr:"Z-Score", stochastic:"Stoch", cci:"CCI", williams_r:"Will%R",
  obv_divergence:"OBV", trend_mr_composite:"Trend+RSI", trend_bb_composite:"Trend+BB",
  turn_of_month:"ToM", halloween:"Halloween",
};

function computeSignalSummary(results: StrategyScanResult[], volMap: Record<string, VolAnalysis> = {}): string {
  // Group by ticker, find fresh flips with family confirmation
  const byTicker: Record<string, StrategyScanResult[]> = {};
  for (const r of results) (byTicker[r.ticker] ??= []).push(r);

  const ideas: { famConfirm: number; text: string }[] = [];

  for (const [ticker, trs] of Object.entries(byTicker)) {
    const fresh = trs.filter(r => r.current_signal !== "Flat" && r.signal_days <= 10 && r.dsr >= 0.5)
      .sort((a, b) => b.dsr - a.dsr);
    if (fresh.length === 0) continue;

    const trigger = fresh[0];
    const dir = trigger.current_signal; // Long or Short
    const matchCount = trs.filter(r => r.current_signal === dir).length;

    // Family confirmation
    let famConfirm = 0;
    for (const [, strats] of Object.entries(SIG_FAMILIES)) {
      if (trs.some(r => strats.includes(r.strategy) && r.current_signal === dir)) famConfirm++;
    }
    if (famConfirm < 2) continue;

    const price = trigger.current_price || 0;
    const atr = trigger.atr_14 || 0;
    const rsi = trigger.rsi || 50;
    if (price <= 0 || atr <= 0) continue;

    const stop = dir === "Long" ? price - 2 * atr : price + 2 * atr;
    const target = dir === "Long" ? Math.max(price + 3 * atr, trigger.high_20d || price) : Math.max(0.01, Math.min(price - 3 * atr, trigger.low_20d || price));
    const riskPct = ((Math.abs(price - stop) / price) * 100).toFixed(1);
    const targetPct = ((Math.abs(target - price) / price) * 100).toFixed(1);
    const rr = (Math.abs(target - price) / Math.abs(price - stop)).toFixed(1);

    // Vol/options context
    const vol = volMap[ticker];
    let optionsLine = "";
    if (vol?.ivr !== undefined && vol?.ivr !== null) {
      const ivr = vol.ivr;
      if (dir === "Long") {
        optionsLine = ivr >= 50 ? ` IVR ${ivr} (elevated) → sell bull put spread.` : ivr <= 25 ? ` IVR ${ivr} (low) → buy bull call spread.` : ` IVR ${ivr}.`;
      } else {
        optionsLine = ivr >= 50 ? ` IVR ${ivr} (elevated) → sell bear call spread.` : ivr <= 25 ? ` IVR ${ivr} (low) → buy bear put spread.` : ` IVR ${ivr}.`;
      }
      if (vol.avg_earnings_move) optionsLine += ` Avg earnings move ±${vol.avg_earnings_move}%.`;
    }

    ideas.push({
      famConfirm,
      text: `${ticker} ${dir.toUpperCase()} signal: ${STRAT_LABELS[trigger.strategy] || trigger.strategy} flipped ${trigger.signal_days}d ago (DSR ${(trigger.dsr * 100).toFixed(0)}%, win ${trigger.win_rate}%). ` +
        `${famConfirm}/5 families confirm, ${matchCount} strategies agree. ` +
        `Price $${price.toFixed(2)}, RSI ${rsi}, ATR $${atr.toFixed(2)}. ` +
        `Stop $${stop.toFixed(2)} (-${riskPct}%), Target $${target.toFixed(2)} (+${targetPct}%), R:R ${rr}x.` +
        optionsLine,
    });
  }

  // Sort by family confirmation count, then take top 3
  ideas.sort((a, b) => b.famConfirm - a.famConfirm);
  return ideas.slice(0, 3).map(i => i.text).join("\n");
}
const VIX_COLORS: Record<string,string> = { Low:"text-gain", Normal:"text-gain", Elevated:"text-warn", High:"text-loss", Extreme:"text-loss" };
const LIQ_COLORS: Record<string,string> = { A:"text-gain", B:"text-gain", C:"text-warn", D:"text-warn", F:"text-loss" };
const CAT_STYLE: Record<string, { border: string; label: string; color: string }> = {
  trump:    { border: "border-l-orange-400", label: "TRUMP", color: "text-orange-400" },
  iran_oil: { border: "border-l-amber-500",  label: "IRAN/OIL", color: "text-amber-500" },
  macro:    { border: "border-l-blue-400",    label: "MACRO", color: "text-blue-400" },
  earnings: { border: "border-l-yellow-400",  label: "EARNINGS", color: "text-yellow-400" },
  news:     { border: "border-l-border",      label: "", color: "" },
};

function fmtExp(exp: string) {
  try { return new Date(exp + "T12:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric" }); }
  catch { return exp; }
}

// ── Sparkline ──
function Sparkline({ points, width = 160, height = 48 }: { points: PolymarketHistoryPoint[]; width?: number; height?: number }) {
  if (points.length < 2) return null;
  const prices = points.map(p => p.p);
  const min = Math.min(...prices), max = Math.max(...prices), range = max - min || 1;
  const pad = 4, textH = 12, w = width - pad * 2, h = height - pad - textH;
  const d = points.map((pt, i) => {
    const x = pad + (i / (points.length - 1)) * w;
    const y = pad + h - ((pt.p - min) / range) * h;
    return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  const up = prices[prices.length - 1] >= prices[0];
  const c = up ? "#22c55e" : "#ef4444";
  return (
    <svg width={width} height={height} style={{ display: "block" }}>
      <path d={d} fill="none" stroke={c} strokeWidth="1.5" strokeLinejoin="round" />
      <text x={pad} y={height - 1} fill="#888" fontSize="9" fontFamily="monospace">{prices[0].toFixed(0)}%</text>
      <text x={width - pad} y={height - 1} fill={c} fontSize="9" fontFamily="monospace" textAnchor="end">{prices[prices.length - 1].toFixed(0)}%</text>
    </svg>
  );
}

// ── Polymarket pill with hover tooltip ──
function PolyPill({ ev }: { ev: PolymarketEvent }) {
  const [hovered, setHovered] = useState(false);
  const [history, setHistory] = useState<PolymarketHistoryPoint[] | null>(null);
  const fetchedRef = useRef(false);
  const top = ev.outcomes[0];
  if (!top) return null;
  const shortTitle = ev.title.replace(/\?$/, "").replace(/^(Will |What |Who will |US )/, "");

  const handleEnter = async () => {
    setHovered(true);
    if (!fetchedRef.current && top.token_id) {
      fetchedRef.current = true;
      try { const res = await fetchPolymarketHistory(top.token_id); if (res.success) setHistory(res.points); } catch {}
    }
  };

  return (
    <div className="relative" onMouseEnter={handleEnter} onMouseLeave={() => setHovered(false)}>
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded border border-border bg-surface-alt text-[0.6rem] font-data hover:border-accent/50 transition-colors cursor-pointer">
        <span className="text-text-muted">{shortTitle.slice(0, 35)}</span>
        <span className={`font-bold ${top.yes_pct >= 80 ? "text-gain" : top.yes_pct >= 40 ? "text-warn" : "text-text"}`}>
          {top.label.slice(0, 20)}: {top.yes_pct}%
        </span>
      </span>
      {hovered && (
        <div className="absolute z-50 top-full left-0 mt-1 p-2 rounded-lg border border-border bg-surface shadow-lg" style={{ minWidth: 220 }}>
          <div className="text-[0.6rem] font-semibold text-text mb-1">{ev.title}</div>
          {ev.outcomes.slice(0, 4).map((o, j) => (
            <div key={j} className="flex justify-between text-[0.55rem] font-data">
              <span className="text-text-muted">{o.label}</span>
              <span className={o.yes_pct >= 80 ? "text-gain font-bold" : o.yes_pct >= 40 ? "text-warn" : "text-text-muted"}>{o.yes_pct}%</span>
            </div>
          ))}
          <div className="text-[0.5rem] text-text-muted mt-1">${(ev.volume_24h / 1000).toFixed(0)}k vol/24h · ${(ev.liquidity / 1000).toFixed(0)}k liq</div>
          {history && history.length > 2 && (
            <div className="mt-1.5 border-t border-border pt-1.5">
              <div className="text-[0.5rem] text-text-muted mb-0.5">30-day trend</div>
              <Sparkline points={history} />
            </div>
          )}
          {history === null && top.token_id && <div className="mt-1.5 text-[0.5rem] text-text-muted animate-pulse">Loading chart...</div>}
        </div>
      )}
    </div>
  );
}

// ── P/L diagram for spreads ──
interface OppData {
  type: string; label: string; premium: number; max_risk: number; max_profit: number;
  long_strike?: number; short_strike?: number; stock_price?: number;
  short_put?: number; long_put?: number; short_call?: number; long_call?: number;
  pop: number; rr_ratio: number; managed_wr: number; kelly_adj: number;
  dte: number; ivr?: number; ivr_band: string; liq_grade: string;
  [key: string]: unknown;
}

function computePL(opp: OppData): { prices: number[]; pls: number[]; breakevens: number[] } {
  const pts = 80;
  const credit = opp.premium; // in dollars (per spread)

  if (opp.type === "condor" && opp.short_put && opp.short_call && opp.long_put && opp.long_call) {
    const sp = opp.short_put, lp = opp.long_put, sc = opp.short_call, lc = opp.long_call;
    const span = Math.max(lc - lp, 1);
    const extend = Math.max(span * 3, lp * 0.02);
    const lo = lp - extend, hi = lc + extend;
    const step = (hi - lo) / pts;
    const prices: number[] = [], pls: number[] = [], breakevens: number[] = [];
    const putWidth = Math.abs(sp - lp) * 100, callWidth = Math.abs(lc - sc) * 100;
    const maxLoss = Math.max(putWidth, callWidth) - credit;
    for (let i = 0; i <= pts; i++) {
      const s = lo + i * step;
      let pl: number;
      if (s <= lp) pl = -maxLoss;
      else if (s <= sp) pl = (s - lp) * 100 - maxLoss;
      else if (s <= sc) pl = credit;
      else if (s <= lc) pl = credit - (s - sc) * 100;
      else pl = -maxLoss;
      prices.push(s); pls.push(pl);
    }
    // Breakevens
    const bePut = sp - credit / 100;
    const beCall = sc + credit / 100;
    if (bePut > lp) breakevens.push(bePut);
    if (beCall < lc) breakevens.push(beCall);
    return { prices, pls, breakevens };
  }

  if (opp.type === "vertical" && opp.long_strike && opp.short_strike) {
    const ls = opp.long_strike, ss = opp.short_strike;
    const span = Math.abs(ss - ls) || 1;
    const extend = Math.max(span * 3, Math.min(ls, ss) * 0.02);
    const lo = Math.min(ls, ss) - extend, hi = Math.max(ls, ss) + extend;
    const step = (hi - lo) / pts;
    const prices: number[] = [], pls: number[] = [], breakevens: number[] = [];
    const width = Math.abs(ss - ls) * 100;
    const isBullPut = opp.label.includes("Bull Put");
    const isBearCall = opp.label.includes("Bear Call");

    for (let i = 0; i <= pts; i++) {
      const s = lo + i * step;
      let pl: number;
      if (isBullPut) {
        // Credit spread: sell higher put (ss), buy lower put (ls)
        const shortLeg = Math.max(ss - s, 0) * 100;
        const longLeg = Math.max(ls - s, 0) * 100;
        pl = credit - shortLeg + longLeg;
      } else if (isBearCall) {
        // Credit spread: sell lower call (ss), buy higher call (ls)
        const shortLeg = Math.max(s - ss, 0) * 100;
        const longLeg = Math.max(s - ls, 0) * 100;
        pl = credit - shortLeg + longLeg;
      } else if (opp.label.includes("Bull Call")) {
        // Debit spread: buy lower call (ls), sell higher call (ss)
        const longLeg = Math.max(s - ls, 0) * 100;
        const shortLeg = Math.max(s - ss, 0) * 100;
        pl = longLeg - shortLeg - (width - credit);
      } else {
        // Bear Put: buy higher put (ls), sell lower put (ss)
        const longLeg = Math.max(ls - s, 0) * 100;
        const shortLeg = Math.max(ss - s, 0) * 100;
        pl = longLeg - shortLeg - (width - credit);
      }
      prices.push(s); pls.push(Math.max(Math.min(pl, width), -width));
    }
    // Breakeven
    if (isBullPut) breakevens.push(ss - credit / 100);
    else if (isBearCall) breakevens.push(ss + credit / 100);
    else if (opp.label.includes("Bull Call")) breakevens.push(ls + (width - credit) / 100);
    else breakevens.push(ls - (width - credit) / 100);
    return { prices, pls, breakevens };
  }

  return { prices: [], pls: [], breakevens: [] };
}

let _plChartId = 0;
function PLChart({ opp }: { opp: OppData }) {
  const [clipId] = useState(() => `pl-clip-${++_plChartId}`);
  const { prices, pls, breakevens } = computePL(opp);
  if (prices.length < 2) return null;

  // Layout: top labels (10px) | chart area | bottom labels (12px)
  const W = 320, topM = 12, botM = 14, chartH = 70, H = topM + chartH + botM;
  const padX = 4;
  const w = W - padX * 2;
  // Clamp Y-axis so both profit and loss zones are visible
  const rawMin = Math.min(...pls), rawMax = Math.max(...pls);
  const absMax = Math.max(Math.abs(rawMin), Math.abs(rawMax));
  const minP = Math.min(rawMin, -absMax * 0.25);
  const maxP = Math.max(rawMax, absMax * 0.25);
  const range = maxP - minP || 1;
  const zeroY = topM + chartH - ((0 - minP) / range) * chartH;
  const pMin = prices[0], pMax2 = prices[prices.length - 1], pRange = pMax2 - pMin || 1;

  const toX = (i: number) => padX + (i / (prices.length - 1)) * w;
  const priceToX = (p: number) => padX + ((p - pMin) / pRange) * w;
  const toY = (pl: number) => topM + chartH - ((pl - minP) / range) * chartH;

  const pathD = prices.map((_, i) => `${i === 0 ? "M" : "L"}${toX(i).toFixed(1)},${toY(pls[i]).toFixed(1)}`).join(" ");
  const fillD = pathD + `L${toX(prices.length - 1)},${zeroY}L${toX(0)},${zeroY}Z`;

  const stockPrice = opp.stock_price as number | undefined;
  const stockInRange = stockPrice && stockPrice >= pMin && stockPrice <= pMax2;

  return (
    <svg width={W} height={H} style={{ display: "block" }}>
      <defs>
        <clipPath id={`${clipId}-above`}><rect x={padX} y={topM} width={w} height={Math.max(0, zeroY - topM)} /></clipPath>
        <clipPath id={`${clipId}-below`}><rect x={padX} y={zeroY} width={w} height={Math.max(0, topM + chartH - zeroY)} /></clipPath>
      </defs>
      {/* P/L axis labels — top left / top right, outside chart area */}
      <text x={padX} y={topM - 3} fill="#22c55e" fontSize="8" fontFamily="monospace">+${opp.max_profit}</text>
      <text x={W - padX} y={topM - 3} fill="#ef4444" fontSize="8" fontFamily="monospace" textAnchor="end">-${opp.max_risk}</text>
      {/* Zero line + label */}
      <line x1={padX} x2={W - padX} y1={zeroY} y2={zeroY} stroke="#555" strokeWidth="0.5" strokeDasharray="3,3" />
      <text x={W - padX + 1} y={zeroY + 3} fill="#555" fontSize="7" fontFamily="monospace" textAnchor="start">$0</text>
      {/* Fills */}
      <path d={fillD} fill="rgba(34,197,94,0.12)" clipPath={`url(#${clipId}-above)`} />
      <path d={fillD} fill="rgba(239,68,68,0.12)" clipPath={`url(#${clipId}-below)`} />
      {/* P/L line */}
      <path d={pathD} fill="none" stroke="#888" strokeWidth="1.5" strokeLinejoin="round" />
      {/* Breakeven dots + labels — below chart area */}
      {breakevens.map((be, i) => {
        const bx = priceToX(be);
        return (
          <g key={i}>
            <circle cx={bx} cy={zeroY} r="3" fill="#888" />
            <text x={bx} y={topM + chartH + 10} fill="#888" fontSize="7" fontFamily="monospace" textAnchor="middle">BE ${be.toFixed(0)}</text>
          </g>
        );
      })}
      {/* Stock price — vertical line + label above chart */}
      {stockInRange && (
        <>
          <line x1={priceToX(stockPrice)} x2={priceToX(stockPrice)} y1={topM} y2={topM + chartH} stroke="#6366f1" strokeWidth="1" strokeDasharray="2,2" />
          <text x={priceToX(stockPrice)} y={topM + 10} fill="#6366f1" fontSize="8" fontFamily="monospace" textAnchor="middle">${stockPrice.toFixed(0)}</text>
        </>
      )}
    </svg>
  );
}

function OppTooltip({ opp }: { opp: OppData }) {
  const breakevens = computePL(opp).breakevens;
  return (
    <div className="absolute z-50 top-full left-0 mt-1 p-2.5 rounded-lg border border-border bg-surface shadow-lg" style={{ minWidth: 340 }}>
      <div className="text-[0.65rem] font-semibold text-text mb-1">{opp.ticker} {opp.label} — P/L at Expiration</div>
      <PLChart opp={opp} />
      <div className="grid grid-cols-2 gap-x-4 gap-y-0.5 mt-1.5 text-[0.55rem] font-data">
        <span className="text-text-muted">Max Profit</span><span className="text-gain font-semibold">${opp.max_profit}</span>
        <span className="text-text-muted">Max Risk</span><span className="text-loss font-semibold">${opp.max_risk}</span>
        <span className="text-text-muted">Breakeven{breakevens.length > 1 ? "s" : ""}</span>
        <span className="font-semibold">{breakevens.map(b => `$${b.toFixed(1)}`).join(", ") || "—"}</span>
        <span className="text-text-muted">POP</span><span>{opp.pop}%</span>
        <span className="text-text-muted">R:R</span><span>{opp.rr_ratio}x</span>
        <span className="text-text-muted">Win Rate (50%)</span><span>{opp.managed_wr}%</span>
        <span className="text-text-muted">Kelly</span><span>{opp.kelly_adj.toFixed(1)}%</span>
        <span className="text-text-muted">IVR</span><span>{opp.ivr?.toFixed(0) ?? "?"} ({opp.ivr_band})</span>
        <span className="text-text-muted">Liquidity</span><span>{opp.liq_grade}</span>
        <span className="text-text-muted">DTE</span><span>{opp.dte}d</span>
        {opp.stock_price ? <><span className="text-text-muted">Stock</span><span>${(opp.stock_price as number).toFixed(2)}</span></> : null}
      </div>
    </div>
  );
}

// ── Opportunity row with hover P/L tooltip ──
function OppRow({ opp, i, isBooked, onBook }: { opp: OppData & Record<string, unknown>; i: number; isBooked: boolean; onBook: (o: any) => void }) {
  const [hovered, setHovered] = useState(false);
  return (
    <div className="relative" onMouseEnter={() => setHovered(true)} onMouseLeave={() => setHovered(false)}>
      <div className={`border rounded-lg p-2.5 bg-surface ${i === 0 ? "border-accent" : "border-border"}`}>
        <div className="flex justify-between items-start">
          <div>
            <div className="flex items-center gap-1.5 flex-wrap">
              <span className="font-bold">{opp.ticker as string}</span>
              <span className={`badge text-[0.5rem] ${opp.type === "condor" ? "badge-info" : opp.label.includes("Bull") ? "badge-gain" : "badge-loss"}`}>{opp.label}</span>
              <span className={`text-[0.5rem] ${LIQ_COLORS[opp.liq_grade] || ""}`}>{opp.liq_grade}</span>
              <span className="text-[0.5rem] text-text-muted">{opp.sector as string}</span>
              {opp.earnings_before && <span className="badge badge-loss text-[0.5rem]">EARN</span>}
              {opp.inside_exp_move && <span className="badge badge-loss text-[0.5rem]">IN EM</span>}
              {i === 0 && <span className="badge badge-gain text-[0.5rem]">TOP</span>}
            </div>
            <div className="text-[0.6rem] text-text-muted font-data mt-0.5">
              {opp.strikes as string} · {fmtExp(opp.expiration as string || "")} ({opp.dte}d) · IVR {opp.ivr?.toFixed(0) ?? "?"} ({opp.ivr_band})
            </div>
          </div>
          <div className="text-right">
            <div className="text-base font-bold font-data">{(opp.score as number).toFixed(3)}</div>
            <div className="text-[0.5rem] text-text-muted">score</div>
          </div>
        </div>
        <div className="flex items-center gap-3 mt-1.5 text-[0.6rem] font-data flex-wrap">
          <span><span className="text-text-muted">Prem</span> ${opp.premium}</span>
          <span><span className="text-text-muted">Risk</span> ${opp.max_risk}</span>
          <span className="text-gain"><span className="text-text-muted">Profit</span> ${opp.max_profit}</span>
          <span><span className="text-text-muted">POP</span> {opp.pop}%</span>
          <span><span className="text-text-muted">R:R</span> {opp.rr_ratio}x</span>
          <span><span className="text-text-muted">WR</span> {opp.managed_wr}%</span>
          <span><span className="text-text-muted">Kelly</span> {opp.kelly_adj.toFixed(1)}%</span>
          <span><span className="text-text-muted">Size</span> {opp.contracts as number}×</span>
          <button onClick={() => onBook(opp)} disabled={isBooked}
            className={`ml-auto px-2 py-0.5 text-[0.55rem] rounded font-semibold ${isBooked ? "bg-gain/20 text-gain" : "bg-accent/80 text-white hover:bg-accent"}`}>
            {isBooked ? "✓" : `Book ${opp.contracts as number}×`}
          </button>
        </div>
      </div>
      {hovered && <OppTooltip opp={opp} />}
    </div>
  );
}

function OppList({ opps, booked, onBook }: { opps: any[]; booked: Set<string>; onBook: (o: any) => void }) {
  return (
    <div className="space-y-1.5">
      {opps.map((opp, i) => {
        const key = opp.ticker + opp.strikes + opp.expiration;
        return <OppRow key={key + i} opp={opp} i={i} isBooked={booked.has(key)} onBook={onBook} />;
      })}
    </div>
  );
}

// ═══════════════════════════════════════════
// ═══════════════════════════════════════════
// Trade Card P/L Tooltip
// ═══════════════════════════════════════════
function computeTradePL(t: import("@/lib/api").StructuredTrade): { prices: number[]; pls: number[]; breakevens: number[] } {
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

  // Options + combination: compute from all legs
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
    // Option legs: each leg's qty already reflects contracts
    for (const leg of optLegs) {
      const sign = leg.action === "sell" ? 1 : -1;
      const strike = leg.strike ?? 0;
      const intrinsic = leg.instrument === "call" ? Math.max(s - strike, 0) : Math.max(strike - s, 0);
      pl += (sign * leg.price - sign * intrinsic) * 100 * leg.qty;
    }
    // Stock legs
    for (const leg of stockLegs) {
      pl += (s - leg.price) * leg.qty;
    }
    prices.push(s); pls.push(pl);
  }

  // Find breakevens
  for (let i = 1; i < pls.length; i++) {
    if ((pls[i - 1] <= 0 && pls[i] > 0) || (pls[i - 1] >= 0 && pls[i] < 0)) {
      const ratio = Math.abs(pls[i - 1]) / (Math.abs(pls[i - 1]) + Math.abs(pls[i]));
      breakevens.push(prices[i - 1] + ratio * (prices[i] - prices[i - 1]));
    }
  }

  return { prices, pls, breakevens };
}

function TradePLChart({ trade }: { trade: import("@/lib/api").StructuredTrade }) {
  const [clipId] = useState(() => `tpl-clip-${++_plChartId}`);
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

function TradeTooltip({ trade }: { trade: import("@/lib/api").StructuredTrade }) {
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

// ═══════════════════════════════════════════
// News Hover Tooltip — lazy AI analysis on hover
// ═══════════════════════════════════════════
const _analysisCache = new Map<string, string>();

function NewsTooltip({ item, onTickerClick }: { item: NewsItem; onTickerClick?: (ticker: string) => void }) {
  const [open, setOpen] = useState(false);
  const [analysis, setAnalysis] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const activeRef = useRef(false); // guards against stale fetch results
  const rowRef = useRef<HTMLDivElement>(null);
  const [above, setAbove] = useState(false); // flip tooltip above if near bottom

  const handleEnter = useCallback(() => {
    activeRef.current = true;
    timeoutRef.current = setTimeout(async () => {
      if (!activeRef.current) return;
      // Flip tooltip above if row is in the bottom third of viewport
      if (rowRef.current) {
        const rect = rowRef.current.getBoundingClientRect();
        setAbove(rect.bottom > window.innerHeight * 0.7);
      }
      setOpen(true);
      const cacheKey = item.headline.slice(0, 200);
      if (_analysisCache.has(cacheKey)) {
        setAnalysis(_analysisCache.get(cacheKey)!);
        return;
      }
      setLoading(true);
      try {
        const res = await fetchNewsAnalysis(item.headline, item.ticker, item.source, item.impact);
        if (!activeRef.current) return; // user already left
        _analysisCache.set(cacheKey, res.analysis);
        setAnalysis(res.analysis);
      } catch {
        if (activeRef.current) setAnalysis("Analysis unavailable.");
      } finally {
        if (activeRef.current) setLoading(false);
      }
    }, 400);
  }, [item.headline, item.ticker, item.source, item.impact]);

  const handleLeave = useCallback(() => {
    activeRef.current = false;
    if (timeoutRef.current) clearTimeout(timeoutRef.current);
    setOpen(false);
  }, []);

  const cat = item.category || "news";
  const style = CAT_STYLE[cat] || CAT_STYLE.news;

  return (
    <div ref={rowRef} className="relative" onMouseEnter={handleEnter} onMouseLeave={handleLeave}>
      <div className="flex items-baseline gap-1.5 flex-wrap">
        <span className="font-bold text-[0.7rem] cursor-pointer hover:text-accent transition-colors"
          onClick={() => onTickerClick?.(item.ticker)}>{item.ticker}</span>
        {style.label && <span className={`text-[0.45rem] font-bold ${style.color}`}>{style.label}</span>}
        <span className={`text-[0.5rem] ${item.impact === "bull" ? "text-gain" : item.impact === "bear" ? "text-loss" : "text-text-muted"}`}>
          {item.impact}
        </span>
        {item.confidence === "verified" && <span className="text-gain text-[0.5rem]">✓</span>}
        {item.confidence === "likely" && <span className="text-warn text-[0.5rem]">◐</span>}
        {item.confidence === "unverified" && <span className="text-text-muted text-[0.5rem]">○</span>}
        <span className="text-text flex-1">{item.headline}</span>
        <span className="text-text-muted text-[0.5rem] shrink-0">{item.source} · {item.time}</span>
        {item.url && <a href={item.url} target="_blank" rel="noopener" className="text-accent text-[0.5rem] shrink-0 hover:underline">→</a>}
      </div>
      {item.verification_note && <div className="text-[0.5rem] text-text-muted mt-0.5 italic pl-0.5">{item.verification_note}</div>}
      {open && (
        <div className={`absolute left-0 z-50 w-80 p-2.5 rounded-md border border-border-strong bg-surface shadow-xl backdrop-blur-sm text-[0.7rem] leading-relaxed ${above ? "bottom-full mb-1" : "top-full mt-1"}`}>
          {loading ? (
            <div className="flex items-center gap-2">
              <div className="w-3 h-3 border-2 border-accent border-t-transparent rounded-full animate-spin" />
              <span className="text-text-muted">Analyzing...</span>
            </div>
          ) : (
            <div className="text-text">{analysis}</div>
          )}
        </div>
      )}
    </div>
  );
}

// ═══════════════════════════════════════════
// Main Page
// ═══════════════════════════════════════════
export default function DailyBriefing() {
  const [watchlist, setWatchlist] = useState(DEFAULT_WATCHLIST.join(", "));
  const [accountSize, setAccountSize] = useState(12500);
  const [data, setData] = useState<DailyBriefingResult | null>(null);
  const [aiNote, setAiNote] = useState("");
  const [typeFilter, setTypeFilter] = useState("all");
  const [booked, setBooked] = useState<Set<string>>(new Set());
  const [newsItems, setNewsItems] = useState<NewsItem[]>([]);
  const [newsSources, setNewsSources] = useState<Record<string, number>>({});
  const [newsPhase, setNewsPhase] = useState<"idle" | "searching" | "verifying" | "done">("idle");
  const [newsError, setNewsError] = useState("");
  const [runningAll, setRunningAll] = useState(false);
  const [signalSummary, setSignalSummary] = useState("");
  const [newsCatFilter, setNewsCatFilter] = useState("all");
  const [chartTicker, setChartTicker] = useState("SPY");
  const [chartInput, setChartInput] = useState("");
  const [chartMode, setChartMode] = useState<"tradingview" | "custom">("custom");

  // Trade Architect
  const [archInput, setArchInput] = useState("");
  const [archResult, setArchResult] = useState("");
  const [archTrades, setArchTrades] = useState<import("@/lib/api").StructuredTrade[]>([]);
  const [archSources, setArchSources] = useState<string[]>([]);
  const [archLoading, setArchLoading] = useState(false);
  const [archError, setArchError] = useState("");
  const [archRisk, setArchRisk] = useState<"conservative" | "moderate" | "aggressive">("moderate");
  const [archStrategy, setArchStrategy] = useState<"auto" | "sell" | "buy">("auto");
  const [archDirection, setArchDirection] = useState<"" | "bullish" | "bearish" | "neutral">("");

  const submitArchitect = useCallback((deep = false) => {
    if (!archInput.trim() || archLoading) return;
    setArchLoading(true); setArchError(""); setArchResult(""); setArchTrades([]);
    fetchTradeArchitect(archInput, [], "", [], accountSize, deep, archRisk, archStrategy, archDirection).then(res => {
      if (res.success) {
        setArchResult(res.analysis ?? "");
        setArchTrades(res.trades ?? []);
        setArchSources(res.context_sources ?? []);
        if (res.tickers?.[0]) setChartTicker(res.tickers[0]);
      } else {
        setArchError(res.error || "Analysis failed.");
      }
    }).catch(e => setArchError(e instanceof Error ? e.message : "Request failed"))
      .finally(() => setArchLoading(false));
  }, [archInput, archLoading, accountSize, archRisk, archStrategy, archDirection]);

  const polyQuery = useQuery({ queryKey: ["polymarket"], queryFn: fetchPolymarket, staleTime: 5 * 60_000 });
  const polyMarkets = polyQuery.data?.markets ?? [];

  // Robinhood positions — auto-fetch on mount
  const rhQuery = useQuery({ queryKey: ["rh-positions"], queryFn: fetchRobinhoodPositions, staleTime: 2 * 60_000 });
  const rhSpreads = rhQuery.data?.spreads ?? [];
  const rhStocks = rhQuery.data?.stocks ?? [];
  const rhPortfolio = rhQuery.data?.portfolio;

  // Compute book warnings
  const bookWarnings = (() => {
    const warnings: { severity: "danger" | "warn" | "ok"; text: string }[] = [];
    for (const s of rhSpreads) {
      if (!s.stock_price) continue;
      // Check each short leg — direction matters
      const shortLegs = s.legs.filter(l => l.direction === "short");
      for (const leg of shortLegs) {
        // Short call is breached when stock > strike (dist positive = danger)
        // Short put is breached when stock < strike (dist negative = danger)
        const dist = ((s.stock_price - leg.strike) / leg.strike) * 100;
        const isCall = leg.opt_type === "call";
        // For calls: stock above strike = breached (dist > 0 is bad)
        // For puts: stock below strike = breached (dist < 0 is bad)
        const dangerDist = isCall ? dist : -dist; // positive = in trouble
        const label = `$${leg.strike.toFixed(0)}${isCall ? "C" : "P"}`;
        if (dangerDist > 0 && dangerDist < 1) {
          warnings.push({ severity: "danger", text: `${s.ticker} ${label} short strike TESTED — stock $${s.stock_price.toFixed(2)} (${dangerDist.toFixed(1)}% past)` });
        } else if (dangerDist > 1) {
          warnings.push({ severity: "danger", text: `${s.ticker} ${label} short strike BREACHED — stock $${s.stock_price.toFixed(2)} (${dangerDist.toFixed(1)}% ITM)` });
        } else if (dangerDist > -3) {
          // Within 3% of being breached
          warnings.push({ severity: "warn", text: `${s.ticker} ${label} short strike ${Math.abs(dangerDist).toFixed(1)}% away` });
        }
      }
      // Max loss check
      if (s.pl < -500) {
        warnings.push({ severity: s.pl < -1000 ? "danger" : "warn", text: `${s.ticker} ${s.type} P&L $${s.pl.toFixed(0)}` });
      }
    }
    // Stock P&L warnings suppressed — user holds intentionally. Only flag options.
    // Sort: danger first
    warnings.sort((a, b) => (a.severity === "danger" ? 0 : 1) - (b.severity === "danger" ? 0 : 1));
    return warnings;
  })();

  // Helper: compute DTE for a spread
  const spreadDte = (exp: string) => Math.max(0, Math.round((new Date(exp + "T16:00:00").getTime() - Date.now()) / 86400000));

  // DTE warnings
  for (const s of rhSpreads) {
    const dte = spreadDte(s.expiration);
    if (dte <= 3 && dte > 0) {
      bookWarnings.push({ severity: "danger", text: `${s.ticker} ${s.type} expires in ${dte}d — manage or close` });
    } else if (dte <= 7) {
      bookWarnings.push({ severity: "warn", text: `${s.ticker} ${s.type} expires in ${dte}d` });
    }
  }

  // One-line portfolio summary for AI prompt
  const bookSummary = rhPortfolio ? (() => {
    const parts: string[] = [];
    parts.push(`Portfolio: $${rhPortfolio.equity.toLocaleString()} equity, P&L $${rhPortfolio.total_pl.toLocaleString()}`);
    if (rhQuery.data?.greeks) {
      const g = rhQuery.data.greeks;
      parts.push(`Greeks: delta ${g.delta > 0 ? "+" : ""}${g.delta.toFixed(0)} (${g.delta > 0 ? "net long" : "net short"}), theta ${g.theta > 0 ? "+" : ""}${g.theta.toFixed(0)}/day, vega ${g.vega > 0 ? "+" : ""}${g.vega.toFixed(0)}`);
    }
    for (const w of bookWarnings.filter(w => w.severity === "danger")) parts.push(w.text);
    for (const s of rhSpreads) {
      parts.push(`${s.ticker} ${s.type} ${s.strikes} ${spreadDte(s.expiration)}d P&L $${s.pl.toFixed(0)}`);
    }
    return parts.join(". ");
  })() : "";
  const bookSummaryRef = useRef("");
  bookSummaryRef.current = bookSummary;

  const scanDoneRef = useRef<DailyBriefingResult | null>(null);
  const newsDoneRef = useRef<NewsItem[]>([]);
  const aiTriggeredRef = useRef(false);
  const polyRef = useRef<PolymarketEvent[]>([]);
  polyRef.current = polyMarkets;

  const signalRef = useRef("");
  signalRef.current = signalSummary;

  const aiMutation = useMutation({
    mutationFn: (args: { scanData: DailyBriefingResult; news: NewsItem[]; poly: PolymarketEvent[]; book: string; signals: string }) =>
      fetchMorningNote(args.scanData, args.news, args.poly, args.book, args.signals),
    onSuccess: (r) => { if (r.success) setAiNote(r.content); else setAiNote(`Error: ${r.content}`); },
  });

  const maybeAutoAI = useCallback(() => {
    if (scanDoneRef.current && newsDoneRef.current.length > 0 && !aiTriggeredRef.current) {
      aiTriggeredRef.current = true;
      aiMutation.mutate({ scanData: scanDoneRef.current, news: newsDoneRef.current, poly: polyRef.current, book: bookSummaryRef.current, signals: signalRef.current });
    }
  }, [aiMutation]);

  const scan = useMutation({
    mutationFn: () => fetchDailyBriefing(watchlist.split(",").map(t => t.trim().toUpperCase()).filter(Boolean), accountSize),
    onSuccess: (d) => { setData(d); setAiNote(""); scanDoneRef.current = d; maybeAutoAI(); },
  });

  async function handleBook(opp: DailyBriefingResult["opportunities"][0]) {
    try {
      await addPosition({ ticker: opp.ticker, type: opp.type, qty: opp.contracts || 1, entry_price: opp.premium / 100,
        details: { strategy: opp.label, strikes: opp.strikes, expiration: opp.expiration, dte: opp.dte, premium: opp.premium, max_risk: opp.max_risk, pop: opp.pop },
        source_page: "daily_briefing" });
      setBooked(prev => new Set(prev).add(opp.ticker + opp.strikes + opp.expiration));
    } catch (e) { console.error(e); }
  }

  const filteredOpps = data?.opportunities.filter(o => typeFilter === "all" || o.type === typeFilter) ?? [];

  const runNews = async () => {
    if (newsPhase === "searching") return; // don't double-fetch if pre-fetch is running
    const tkList = watchlist.split(",").map(t => t.trim().toUpperCase()).filter(Boolean);
    setNewsPhase("searching"); setNewsError(""); setNewsItems([]); setNewsCatFilter("all");
    try {
      const searchRes = await fetchNewsSearch(tkList);
      if (!searchRes.success) { setNewsError(searchRes.error || "Search failed"); setNewsPhase("idle"); return; }
      // Show results immediately (unverified) — don't wait for verification
      setNewsItems(searchRes.items); setNewsSources(searchRes.sources);
      newsDoneRef.current = searchRes.items; maybeAutoAI();
      setNewsPhase("done");
      // Verify in background — update items silently when done
      if (searchRes.items.length > 0) {
        fetchNewsVerify(searchRes.items).then(verifyRes => {
          if (verifyRes.success && verifyRes.items.length > 0) {
            setNewsItems(verifyRes.items); setNewsSources(verifyRes.sources);
            newsDoneRef.current = verifyRes.items;
          }
        }).catch(() => {});
      }
    } catch (e) { setNewsError((e as Error).message); setNewsPhase("idle"); }
  };

  // Pre-fetch news on page mount (runs once, results cached 30 min on backend)
  const newsPrefetchedRef = useRef(false);
  useEffect(() => {
    if (newsPrefetchedRef.current) return;
    newsPrefetchedRef.current = true;
    const tkList = watchlist.split(",").map(t => t.trim().toUpperCase()).filter(Boolean);
    // Don't set "searching" immediately — if cached, response is instant and we avoid a flash
    const phaseTimer = setTimeout(() => { if (newsItems.length === 0) setNewsPhase("searching"); }, 500);
    fetchNewsSearch(tkList).then(res => {
      clearTimeout(phaseTimer);
      if (res.success && res.items.length > 0) {
        setNewsItems(res.items); setNewsSources(res.sources);
        newsDoneRef.current = res.items;
        setNewsPhase("done");
        // Background verify
        fetchNewsVerify(res.items).then(v => {
          if (v.success && v.items.length > 0) {
            setNewsItems(v.items); setNewsSources(v.sources);
            newsDoneRef.current = v.items;
          }
        }).catch(() => {});
      } else {
        setNewsPhase("idle");
      }
    }).catch(() => setNewsPhase("idle"));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const runAll = async () => {
    setRunningAll(true); aiTriggeredRef.current = false;
    scanDoneRef.current = null; newsDoneRef.current = []; setAiNote(""); setSignalSummary("");

    // Fire all three in parallel: opportunity scan + news + strategy signals
    const tkList = watchlist.split(",").map(t => t.trim().toUpperCase()).filter(Boolean);
    scan.mutate();

    // Strategy scan + vol analysis in background (doesn't block AI note)
    Promise.all([
      fetchStrategyScan(tkList, ALL_STRATS, 2520),  // 10yr — cached
      fetchVolAnalysis(tkList).catch(() => ({ success: false, results: {} })),
    ]).then(([scanRes, volRes]) => {
      if (scanRes?.results) {
        const volMap = volRes.success ? volRes.results : {};
        const summary = computeSignalSummary(scanRes.results, volMap);
        setSignalSummary(summary);
        signalRef.current = summary;
      }
    }).catch(() => {});

    await runNews();
    setRunningAll(false);
  };

  const isLoading = scan.isPending || newsPhase === "searching" || newsPhase === "verifying" || aiMutation.isPending;

  // News filtering
  const catCounts: Record<string, number> = {};
  newsItems.forEach(n => { const c = n.category || "news"; catCounts[c] = (catCounts[c] || 0) + 1; });
  const filteredNews = newsCatFilter === "all" ? newsItems : newsItems.filter(n => (n.category || "news") === newsCatFilter);

  return (
    <div className="space-y-3 relative">
      {/* ═══ 1. Controls ═══ */}
      <div className="card card-compact">
        <div className="flex flex-wrap items-end gap-3">
          <div className="flex-1 min-w-[200px]">
            <h1 className="text-xl font-bold tracking-tight mb-1">Market Scan</h1>
            <textarea value={watchlist} onChange={e => setWatchlist(e.target.value)} rows={2}
              className="w-full px-3 py-1.5 border border-border rounded-lg text-xs font-data bg-surface resize-y" />
          </div>
          <div>
            <label className="metric-label text-[0.6rem]">Account</label>
            <input type="number" value={accountSize} onChange={e => setAccountSize(+e.target.value)} step={5000}
              className="w-24 mt-0.5 px-2 py-1.5 border border-border rounded-lg text-xs font-data bg-surface" />
          </div>
          <button onClick={runAll} disabled={runningAll || scan.isPending}
            className="px-5 py-1.5 bg-accent text-white font-semibold rounded-lg hover:bg-accent-hover disabled:opacity-50 text-sm">
            {runningAll ? "Running..." : "Run All"}
          </button>
          <button onClick={() => scan.mutate()} disabled={scan.isPending}
            className="px-3 py-1.5 border border-border text-text-muted rounded-lg hover:bg-surface-alt disabled:opacity-50 text-xs">
            {scan.isPending ? "..." : "Scan"}
          </button>
          <button onClick={runNews} disabled={newsPhase === "searching"}
            className="px-3 py-1.5 border border-border text-text-muted rounded-lg hover:bg-surface-alt disabled:opacity-50 text-xs">
            {newsPhase === "searching" ? "..." : "News"}
          </button>
        </div>
        {isLoading && (
          <div className="flex items-center gap-3 mt-2 text-xs text-text-muted">
            <div className="w-3 h-3 border-2 border-accent border-t-transparent rounded-full animate-spin" />
            {scan.isPending && <span>Scanning tickers</span>}
            {newsPhase === "searching" && <span>Grok searching web + X</span>}
            {newsPhase === "verifying" && <span>Fact-checking</span>}
            {aiMutation.isPending && <span>Writing AI note</span>}
          </div>
        )}
      </div>

      {scan.isError && <div className="card border-loss/30 bg-loss-bg text-loss text-sm">{(scan.error as Error).message}</div>}

      {/* ═══ Trade Architect ═══ */}
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
            placeholder="e.g. 'NVDA bullish into earnings' or 'sell premium on SPY, VIX is elevated'"
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
        {archError && <div className="text-xs text-loss mt-2">{archError}</div>}
        {/* Structured trade cards */}
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
            {/* Comparison table */}
            <div className="overflow-x-auto">
              <table className="w-full text-[0.6rem] font-data">
                <thead>
                  <tr className="text-text-muted border-b border-border">
                    <th className="text-left py-1 pr-2">Trade</th>
                    <th className="text-right px-2">Max Profit</th>
                    <th className="text-right px-2">Max Risk</th>
                    <th className="text-right px-2">R:R</th>
                    <th className="text-right px-2">POP</th>
                    <th className="text-right px-2">Breakeven</th>
                    <th className="text-right px-2">Delta</th>
                    <th className="text-right px-2">Theta/day</th>
                    <th className="text-right pl-2">Acct %</th>
                  </tr>
                </thead>
                <tbody>
                  {(() => {
                    // Best = highest account fit. Tie-break: highest R:R.
                    const bestFit = Math.max(...archTrades.map(t => t.account_fit ?? 0));
                    const bestTrades = archTrades.filter(t => (t.account_fit ?? 0) === bestFit);
                    const bestIdx = bestTrades.length === 1
                      ? archTrades.indexOf(bestTrades[0])
                      : archTrades.indexOf(bestTrades.sort((a, b) => b.rr_ratio - a.rr_ratio)[0]);
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
                        <td className="text-right px-2">${t.breakeven.toFixed(2)}</td>
                        <td className="text-right px-2">{t.greeks.delta.toFixed(1)}</td>
                        <td className="text-right px-2">{t.greeks.theta !== 0 ? `$${t.greeks.theta.toFixed(2)}` : "—"}</td>
                        <td className={`text-right pl-2 ${t.risk_pct_of_account && t.risk_pct_of_account > 5 ? "text-loss" : ""}`}>
                          {t.risk_pct_of_account != null ? `${t.risk_pct_of_account.toFixed(1)}%` : "—"}
                        </td>
                      </tr>
                    ));
                  })()}
                </tbody>
              </table>
            </div>

            {/* Trade cards */}
            <div className={`grid grid-cols-1 ${archTrades.length >= 3 ? "md:grid-cols-3" : "md:grid-cols-2"} gap-2`}>
              {archTrades.map((t, i) => {
                const colors: Record<string, string> = { stock: "border-[#58a6ff]", options: "border-[#a371f7]", combination: "border-[#d29922]" };
                const icons: Record<string, string> = { stock: "S", options: "O", combination: "C" };
                return (
                  <div key={i} className={`relative rounded-lg border-l-4 ${colors[t.type] || "border-border"} border border-border p-3 bg-surface`}
                    onMouseEnter={(e) => { const tt = e.currentTarget.querySelector("[data-trade-tooltip]") as HTMLElement; if (tt) tt.style.display = "block"; }}
                    onMouseLeave={(e) => { const tt = e.currentTarget.querySelector("[data-trade-tooltip]") as HTMLElement; if (tt) tt.style.display = "none"; }}>
                    <div className="flex items-center justify-between mb-2">
                      <div className="flex items-center gap-2">
                        <span className="w-6 h-6 rounded-full flex items-center justify-center text-[0.6rem] font-bold bg-surface-alt text-text-muted">
                          {icons[t.type] || "?"}
                        </span>
                        <span className="text-[0.7rem] font-bold text-text">{t.label}</span>
                      </div>
                      <button onClick={(e) => {
                        const text = t.legs.map(l =>
                          l.instrument === "shares" ? `${l.action} ${l.qty} ${l.ticker} @ $${l.price}`
                          : `${l.action} ${l.qty}× ${l.ticker} $${l.strike} ${l.instrument} ${l.exp} @ $${l.price}`
                        ).join("\n");
                        navigator.clipboard.writeText(text);
                        const btn = e.currentTarget;
                        btn.textContent = "Copied!";
                        setTimeout(() => { btn.textContent = "Copy"; }, 1500);
                      }} className="text-[0.5rem] text-text-muted hover:text-accent px-1.5 py-0.5 border border-border rounded">
                        Copy
                      </button>
                    </div>

                    {/* Legs */}
                    <div className="space-y-0.5 mb-2">
                      {t.legs.map((l, li) => (
                        <div key={li} className="flex items-center gap-2 text-[0.6rem] font-data">
                          <span className={`font-semibold ${l.action === "buy" ? "text-gain" : "text-loss"}`}>
                            {l.action.toUpperCase()}
                          </span>
                          <span className="text-text">
                            {l.qty}× {l.instrument === "shares" ? `${l.ticker} shares` : `${l.ticker} $${l.strike} ${l.instrument}`}
                            {l.exp && <span className="text-text-muted ml-1">{l.exp}</span>}
                          </span>
                          <span className="text-text-muted ml-auto">${l.price.toFixed(2)}</span>
                        </div>
                      ))}
                    </div>

                    {/* Key metrics grid */}
                    <div className="grid grid-cols-2 gap-x-3 gap-y-0.5 text-[0.55rem] font-data border-t border-border/50 pt-1.5">
                      <span className="text-text-muted">Max Profit</span>
                      <span className="text-gain text-right font-semibold">${t.max_profit.toLocaleString()}</span>
                      <span className="text-text-muted">Max Risk</span>
                      <span className="text-loss text-right font-semibold">${t.max_risk.toLocaleString()}</span>
                      <span className="text-text-muted">Breakeven</span>
                      <span className="text-right">${t.breakeven.toFixed(2)}{t.breakeven_upper ? ` / $${t.breakeven_upper.toFixed(2)}` : ""}</span>
                      {t.pop != null && <><span className="text-text-muted">POP</span><span className="text-right">{t.pop}%</span></>}
                      <span className="text-text-muted">R:R</span>
                      <span className="text-right">{t.rr_ratio}x</span>
                      {t.stop && <><span className="text-text-muted">Stop</span><span className="text-right text-loss">${t.stop.toFixed(2)}</span></>}
                      {t.target && <><span className="text-text-muted">Target</span><span className="text-right text-gain">${t.target.toFixed(2)}</span></>}
                      <span className="text-text-muted">Timeframe</span>
                      <span className="text-right">{t.timeframe}</span>
                      {t.risk_pct_of_account != null && (
                        <><span className="text-text-muted">Account Risk</span>
                        <span className={`text-right font-semibold ${t.risk_pct_of_account > 3 ? "text-loss" : "text-text"}`}>
                          {t.risk_pct_of_account.toFixed(1)}%
                        </span></>
                      )}
                    </div>

                    {/* Greeks + portfolio impact */}
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
                    {/* Account fit + historical winrate */}
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
                    <div data-trade-tooltip style={{ display: "none" }}>
                      <TradeTooltip trade={t} />
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* AI Assessment (from Claude) */}
        {archResult && (
          <div className="mt-3 border-t border-border pt-3">
            <div className="text-[0.65rem] font-bold text-accent uppercase tracking-wider mb-2">AI Assessment</div>
            <div className="text-xs leading-relaxed text-text arch-assessment"
              dangerouslySetInnerHTML={{ __html: (() => {
                let h = archResult
                  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
                  // Strip --- rules
                  .replace(/^\s*---+\s*$/gm, "")
                  // Headers (## or all-caps lines ending with colon)
                  .replace(/^#{1,3}\s+(.+)$/gm, '<div class="arch-h">$1</div>')
                  .replace(/^([A-Z]+(?:\s+[A-Z:]+){1,6})$/gm, (m) => m.length >= 8 ? `<div class="arch-h">${m}</div>` : m)
                  // Bold then italic
                  .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
                  .replace(/\*([^*]{4,})\*/g, '<em>$1</em>')
                  // Strip stray emoji/replacement chars
                  .replace(/[\u{FFFD}\u{FE0F}]/gu, "")
                  // Risk bullets (emoji, •, -, * at line start)
                  .replace(/^[\u{1F534}\u{1F7E1}\u{26A0}\u{2022}\u{FFFD}•\-\*]\s*[\u{FE0F}]?\s*(.+)$/gmu, (_, content) => {
                    const isRisk = /risk|warn|cpi|nfp|earn|liquid|slippage/i.test(content);
                    const cleaned = content.replace(/^[\u{1F534}\u{1F7E1}\u{26A0}\u{FFFD}\u{FE0F}]\s*/gu, "");
                    return `<div class="${isRisk ? "arch-risk" : "arch-bullet"}">${cleaned}</div>`;
                  })
                  // Markdown tables: |col|col| → HTML table
                  .replace(/(\|.+\|[\n])+/g, (tableBlock) => {
                    const rows = tableBlock.trim().split("\n").filter(r => r.includes("|"));
                    if (rows.length < 2) return tableBlock;
                    const parseRow = (r: string) => r.split("|").filter(c => c.trim()).map(c => c.trim());
                    const isSep = (r: string) => /^\|[\s\-:]+\|$/.test(r.trim());
                    const headers = parseRow(rows[0]);
                    const dataRows = rows.filter(r => !isSep(r)).slice(1);
                    return '<table class="arch-table"><thead><tr>' +
                      headers.map(h => `<th>${h}</th>`).join("") +
                      '</tr></thead><tbody>' +
                      dataRows.map(r => '<tr>' + parseRow(r).map(c => `<td>${c}</td>`).join("") + '</tr>').join("") +
                      '</tbody></table>';
                  })
                  // Paragraphs
                  .replace(/\n\n+/g, '</p><p class="arch-p">')
                  .replace(/^/, '<p class="arch-p">') + '</p>';
                // Clean empty paragraphs
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

      {/* ═══ Saved Trades ═══ */}
      {(() => {
        // Only read on client, avoid SSR + avoid re-parse every render by checking length
        if (typeof window === "undefined") return null;
        let saved: Record<string, unknown>[] = [];
        try { saved = JSON.parse(localStorage.getItem("saved_trades") || "[]"); } catch { return null; }
        if (!saved.length) return null;
        return (
          <details className="card">
            <summary className="metric-label cursor-pointer select-none">Saved Trades ({saved.length})</summary>
            <div className="space-y-1.5 mt-2">
              {saved.slice(0, 10).map((s, i) => (
                <div key={i} className="flex items-center justify-between text-[0.6rem] font-data px-2 py-1.5 rounded border border-border">
                  <div className="flex items-center gap-1.5 flex-wrap">
                    <span className="font-bold text-text">{s.ticker as string}</span>
                    <span className="text-text-muted">{s.label as string}</span>
                    <span className="font-data">R:R {s.rr_ratio as number}x</span>
                    {s.pop != null && <span className="text-text-muted">POP {s.pop as number}%</span>}
                    {s.thesis && <span className="text-[0.5rem] text-text-muted italic truncate max-w-[200px]">&ldquo;{s.thesis as string}&rdquo;</span>}
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <span className="text-[0.5rem] text-text-muted">{new Date(s.timestamp as string).toLocaleDateString()}</span>
                    <button onClick={() => {
                      try {
                        const all = JSON.parse(localStorage.getItem("saved_trades") || "[]");
                        all.splice(i, 1);
                        localStorage.setItem("saved_trades", JSON.stringify(all));
                      } catch {}
                      window.location.reload();
                    }} className="text-[0.5rem] text-text-muted hover:text-loss px-1">×</button>
                  </div>
                </div>
              ))}
              {saved.length > 10 && <div className="text-[0.5rem] text-text-muted text-center">+ {saved.length - 10} more</div>}
            </div>
          </details>
        );
      })()}

      {/* ═══ 2. Context Strip + Polymarket + Regime ═══ */}
      {data && (
        <div className="space-y-1.5">
          <div className="flex flex-wrap items-center gap-x-5 gap-y-1.5 px-3 py-2 rounded-lg border border-border bg-surface">
            <div className="flex items-center gap-1.5">
              <span className="text-[0.6rem] text-text-muted">SPY</span>
              <span className="font-bold font-data text-sm">${data.market_context.spy.price}</span>
              <span className={`text-xs font-data ${data.market_context.spy.change_pct >= 0 ? "text-gain" : "text-loss"}`}>
                {data.market_context.spy.change_pct >= 0 ? "+" : ""}{data.market_context.spy.change_pct}%
              </span>
            </div>
            <div className="flex items-center gap-1.5">
              <span className="text-[0.6rem] text-text-muted">QQQ</span>
              <span className="font-bold font-data text-sm">${data.market_context.qqq.price}</span>
              <span className={`text-xs font-data ${data.market_context.qqq.change_pct >= 0 ? "text-gain" : "text-loss"}`}>
                {data.market_context.qqq.change_pct >= 0 ? "+" : ""}{data.market_context.qqq.change_pct}%
              </span>
            </div>
            <div className="flex items-center gap-1.5">
              <span className="text-[0.6rem] text-text-muted">VIX</span>
              <span className="font-bold font-data text-sm">{data.market_context.vix.price}</span>
              <span className={`text-[0.6rem] font-semibold ${VIX_COLORS[data.market_context.vix.regime] || ""}`}>{data.market_context.vix.regime}</span>
              {data.market_context.vix.term_structure && (
                <span className="text-[0.55rem] text-text-muted font-data">{data.market_context.vix.term_structure} {data.market_context.vix.term_ratio}x</span>
              )}
            </div>
            <div className="w-px h-5 bg-border hidden sm:block" />
            <span className="text-[0.6rem] text-text-muted font-data">
              {data.risk_budget.pct_of_account}% deployed · ${data.risk_budget.remaining.toLocaleString()} free
            </span>
            <span className={`badge text-[0.5rem] ${data.risk_budget.verdict === "Conservative" ? "badge-gain" : data.risk_budget.verdict === "Moderate" ? "badge-info" : data.risk_budget.verdict === "Aggressive" ? "badge-warn" : "badge-loss"}`}>
              {data.risk_budget.verdict}
            </span>
            {data.market_context.fomc_events.map((ev, i) => (
              <span key={i} className="badge badge-warn text-[0.5rem]">{ev.type} {ev.days_away}d</span>
            ))}
            {data.earnings_this_week.map((e, i) => (
              <span key={`e-${i}`} className="badge badge-warn text-[0.5rem]">{e.ticker} earn {e.days}d</span>
            ))}
            {data.warnings.map((w, i) => (
              <span key={`w-${i}`} className="text-[0.5rem] text-loss">{w}</span>
            ))}
          </div>
          {polyMarkets.length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5 px-3 py-1.5 rounded-lg border border-border bg-surface">
              <span className="text-[0.55rem] text-text-muted font-semibold uppercase tracking-wider mr-1">Polymarket</span>
              {polyMarkets.slice(0, 8).map((ev) => <PolyPill key={ev.slug} ev={ev} />)}
            </div>
          )}
          <div className="text-[0.55rem] text-text-muted px-1">
            {data.market_context.vix.regime === "Low" && "Low vol — thin premiums, favor debits"}
            {data.market_context.vix.regime === "Normal" && "Normal vol — focus on IVR per name"}
            {data.market_context.vix.regime === "Elevated" && "Elevated vol — credit spreads at 50-75 IVR"}
            {data.market_context.vix.regime === "High" && "High vol — reduce size, widen wings"}
            {data.market_context.vix.regime === "Extreme" && "Extreme vol — very small or avoid"}
            {data.market_context.vix.term_structure === "Backwardation" && " · Backwardation = rich front-month"}
          </div>
        </div>
      )}

      {/* ═══ Chart ═══ */}
      <div className="card">
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            {/* Chart mode toggle */}
            <button onClick={() => setChartMode("custom")}
              className={`px-2 py-0.5 text-[0.6rem] font-semibold rounded-l border ${chartMode === "custom" ? "bg-accent/15 text-accent border-accent" : "border-border text-text-muted hover:text-text"}`}>
              Chart
            </button>
            <button onClick={() => setChartMode("tradingview")}
              className={`px-2 py-0.5 text-[0.6rem] font-semibold rounded-r border border-l-0 ${chartMode === "tradingview" ? "bg-accent/15 text-accent border-accent" : "border-border text-text-muted hover:text-text"}`}>
              TradingView
            </button>
          </div>
          <div className="flex items-center gap-1">
            {["SPY","QQQ","NVDA","AAPL","TSLA","GLD","TLT"].map(tk => (
              <button key={tk} onClick={() => setChartTicker(tk)}
                className={`px-1.5 py-0.5 text-[0.55rem] rounded border ${chartTicker === tk ? "border-accent text-accent bg-accent/10" : "border-border text-text-muted hover:border-text-muted"}`}>
                {tk}
              </button>
            ))}
            <input
              type="text" value={chartInput} onChange={e => setChartInput(e.target.value.toUpperCase())}
              onKeyDown={e => { if (e.key === "Enter" && chartInput.trim()) { setChartTicker(chartInput.trim()); } }}
              placeholder="Ticker"
              className="w-16 px-1.5 py-0.5 text-[0.55rem] rounded border border-border bg-transparent text-text placeholder:text-text-muted"
            />
          </div>
        </div>
        {chartMode === "custom" ? (
          <LightweightChart symbol={chartTicker} height={500} showVolume
            positions={{ stocks: rhStocks, spreads: rhSpreads }} />
        ) : (
          <div style={{ height: 500 }}>
            <TradingViewChart symbol={chartTicker} theme="dark" height={500} />
          </div>
        )}
      </div>

      {/* ═══ News Feed ═══ */}
      {(newsItems.length > 0 || newsPhase !== "idle") && (
        <div className="card">
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-2">
              <span className="metric-label">News Feed</span>
              {newsPhase === "searching" && <span className="text-[0.5rem] text-accent animate-pulse">searching...</span>}
              {newsPhase === "verifying" && <span className="text-[0.5rem] text-accent animate-pulse">verifying...</span>}
              {newsSources.grok_verified > 0 && <span className="text-[0.5rem] text-gain">✓{newsSources.grok_verified}</span>}
            </div>
            <div className="flex gap-1">
              <button onClick={() => setNewsCatFilter("all")}
                className={`px-1.5 py-0.5 rounded text-[0.5rem] font-semibold border ${newsCatFilter === "all" ? "border-accent text-accent bg-accent/10" : "border-border text-text-muted"}`}>
                All ({newsItems.length})
              </button>
              {Object.entries(catCounts).sort(([a],[b]) => {
                const order = ["trump","iran_oil","macro","earnings","news"];
                return order.indexOf(a) - order.indexOf(b);
              }).map(([cat, count]) => {
                const style = CAT_STYLE[cat] || CAT_STYLE.news;
                return (
                  <button key={cat} onClick={() => setNewsCatFilter(cat)}
                    className={`px-1.5 py-0.5 rounded text-[0.5rem] font-semibold border ${newsCatFilter === cat ? "border-accent bg-accent/10 " + style.color : "border-border text-text-muted"}`}>
                    {style.label || cat} ({count})
                  </button>
                );
              })}
            </div>
          </div>
          {newsPhase === "searching" && newsItems.length === 0 && (
            <div className="flex items-center gap-2 py-6 justify-center">
              <div className="w-4 h-4 border-2 border-accent border-t-transparent rounded-full animate-spin" />
              <span className="text-xs text-text-muted">Searching web + X...</span>
            </div>
          )}
          {newsError && <div className="text-xs text-loss mb-2">{newsError}</div>}
          <div className="space-y-1">
            {filteredNews.map((item, i) => {
              const cat = item.category || "news";
              const style = CAT_STYLE[cat] || CAT_STYLE.news;
              return (
                <div key={i} className={`pl-2 border-l-2 ${style.border} py-1 text-xs`}>
                  <NewsTooltip item={item} onTickerClick={setChartTicker} />
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* ═══ 3. AI Market Note — THE SYNTHESIS ═══ */}
      {(aiNote || aiMutation.isPending) && (
        <div className="card">
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-2">
              <span className="metric-label">Trading Thesis</span>
              {signalSummary && <span className="text-[0.5rem] text-gain">+ signals</span>}
              {!signalSummary && runningAll && <span className="text-[0.5rem] text-text-muted animate-pulse">scanning signals...</span>}
            </div>
            <button onClick={() => { if (data) aiMutation.mutate({ scanData: data, news: newsItems, poly: polyMarkets, book: bookSummary, signals: signalSummary }); }}
              disabled={aiMutation.isPending || !data}
              className="px-3 py-1 bg-accent text-white text-[0.6rem] font-semibold rounded hover:bg-accent-hover disabled:opacity-50">
              {aiMutation.isPending ? "Writing..." : signalSummary && aiNote ? "Regenerate + Signals" : "Regenerate"}
            </button>
          </div>
          {aiMutation.isPending && (
            <div className="flex items-center gap-2 py-4">
              <div className="w-4 h-4 border-2 border-accent border-t-transparent rounded-full animate-spin" />
              <span className="text-xs text-text-muted">Gemini analyzing scan + news + prediction markets...</span>
            </div>
          )}
          {aiNote && (
            <div className="text-xs leading-relaxed ai-note" dangerouslySetInnerHTML={{
              __html: (() => {
                let html = aiNote;
                html = html.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
                html = html.replace(/\*\*(STANCE)(.*?)\*\*/gi, '<div class="ai-header ai-header-stance">$1$2</div>');
                html = html.replace(/\*\*(TOP TRADES?)(.*?)\*\*/gi, '<div class="ai-header ai-header-trade">$1$2</div>');
                html = html.replace(/\*\*(RISKS?)(.*?)\*\*/gi, '<div class="ai-header ai-header-risk">$1$2</div>');
                html = html.replace(/\*\*(SIZING|SIZE)(.*?)\*\*/gi, '<div class="ai-header ai-header-accent">$1$2</div>');
                html = html.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
                const lines = html.split("\n");
                const out: string[] = [];
                let inList = false;
                for (const line of lines) {
                  const trimmed = line.trim();
                  if (/^[\*\-•] /.test(trimmed)) {
                    if (!inList) { out.push('<ul class="ai-bullets">'); inList = true; }
                    out.push(`<li>${trimmed.replace(/^[\*\-•] /, "")}</li>`);
                  } else {
                    if (inList) { out.push("</ul>"); inList = false; }
                    if (trimmed) out.push(trimmed);
                  }
                }
                if (inList) out.push("</ul>");
                html = out.join("\n");
                html = html.replace(/⚠/g, '<span class="text-loss">⚠</span>');
                html = html.replace(/\(Polymarket:([^)]+)\)/g, '<span class="ai-poly">(Polymarket:$1)</span>');
                return html;
              })(),
            }} />
          )}
          <style jsx>{`
            .ai-note :global(.ai-header) { margin-top: 0.875rem; margin-bottom: 0.25rem; font-size: 0.65rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; border-bottom: 1px solid; padding-bottom: 0.2rem; }
            .ai-note :global(.ai-header:first-child) { margin-top: 0; }
            .ai-note :global(.ai-header-stance) { color: #e2e8f0; border-color: rgba(226,232,240,0.3); font-size: 0.75rem; }
            .ai-note :global(.ai-header-trade) { color: #22c55e; border-color: rgba(34,197,94,0.25); }
            .ai-note :global(.ai-header-risk) { color: #ef4444; border-color: rgba(239,68,68,0.25); }
            .ai-note :global(.ai-header-accent) { color: var(--accent); border-color: rgba(99,102,241,0.15); }
            .ai-note :global(.ai-bullets) { list-style: none; padding: 0; margin: 0.2rem 0; display: flex; flex-direction: column; gap: 0.3rem; }
            .ai-note :global(.ai-bullets li) { padding-left: 0.75rem; position: relative; line-height: 1.5; }
            .ai-note :global(.ai-bullets li::before) { content: "•"; position: absolute; left: 0; color: var(--text-muted); }
            .ai-note :global(.ai-poly) { font-size: 0.55rem; color: var(--text-muted); }
            .ai-note :global(strong) { font-weight: 600; }
          `}</style>
        </div>
      )}

      {/* ═══ 4. Computed Outlook ═══ */}
      {data?.outlook && data.outlook.spy_price > 0 && filteredOpps.length > 0 && (
        <div className="rounded-lg border border-border bg-surface px-4 py-3">
          {/* Header + implied range bar */}
          <div className="flex items-center justify-between mb-2">
            <span className="text-[0.7rem] font-bold uppercase tracking-wider" style={{ color: "#38bdf8" }}>5-Day Outlook</span>
            <span className="text-[0.55rem] text-text-muted font-data">VIX {data.outlook.vix} → ±{data.outlook.implied_move_pct}% implied</span>
          </div>
          <div className="mb-3">
            <div className="flex items-center justify-between text-[0.6rem] font-data mb-1">
              <span className="text-loss">${data.outlook.implied_low}</span>
              <span className="text-text-muted">SPY ${data.outlook.spy_price}</span>
              <span className="text-gain">${data.outlook.implied_high}</span>
            </div>
            <div className="relative h-2 rounded-full bg-surface-alt border border-border">
              <div className="absolute inset-y-0 rounded-full bg-accent/10" style={{ left: "25%", right: "25%" }} />
              {(() => {
                const range = data.outlook.implied_high - data.outlook.implied_low;
                const pct = range > 0 ? ((data.outlook.spy_price - data.outlook.implied_low) / range) * 100 : 50;
                return <div className="absolute top-1/2 -translate-y-1/2 w-2.5 h-2.5 rounded-full bg-accent border-2 border-surface" style={{ left: `calc(${pct}% - 5px)` }} />;
              })()}
            </div>
          </div>

          {/* Position risk table */}
          <div className="text-[0.6rem] font-data">
            <div className="grid grid-cols-[1fr_auto_auto_auto_auto] gap-x-3 gap-y-0.5 items-baseline">
              {/* Header row */}
              <span className="text-text-muted text-[0.5rem] uppercase">Trade</span>
              <span className="text-text-muted text-[0.5rem] uppercase text-right">Short Strike</span>
              <span className="text-text-muted text-[0.5rem] uppercase text-right">vs Stock</span>
              <span className="text-text-muted text-[0.5rem] uppercase text-right">vs Range</span>
              <span className="text-text-muted text-[0.5rem] uppercase text-center">Status</span>

              {filteredOpps.slice(0, 5).map((opp, i) => {
                const stock = (opp.stock_price as number) || data.outlook.spy_price || 0;
                const impliedPct = data.outlook.implied_move_pct;

                // Determine the short strikes that matter (where risk lives)
                let strikes: { label: string; price: number; side: "put" | "call" }[] = [];
                if (opp.type === "condor" && opp.short_put && opp.short_call) {
                  strikes = [
                    { label: `$${(opp.short_put as number).toFixed(0)}P`, price: opp.short_put as number, side: "put" },
                    { label: `$${(opp.short_call as number).toFixed(0)}C`, price: opp.short_call as number, side: "call" },
                  ];
                } else if (opp.short_strike) {
                  const isBull = opp.label.includes("Bull");
                  strikes = [{ label: `$${(opp.short_strike as number).toFixed(0)}`, price: opp.short_strike as number, side: isBull ? "put" : "call" }];
                }

                if (strikes.length === 0) return null;

                return strikes.map((s, si) => {
                  // Directional distance: positive = ITM (danger), negative = OTM (safe)
                  const dangerDist = s.side === "call"
                    ? ((stock - s.price) / s.price) * 100
                    : ((s.price - stock) / s.price) * 100;
                  const absDistPct = Math.abs(dangerDist);
                  const ratio = impliedPct > 0 ? absDistPct / impliedPct : 999;
                  // If already ITM, it's inside range regardless
                  const isITM = dangerDist > 0;
                  const status = isITM ? "inside" : ratio > 1.2 ? "safe" : ratio > 0.8 ? "edge" : "inside";
                  const statusIcon = status === "safe" ? "✓" : status === "edge" ? "⚠" : "✗";
                  const statusColor = status === "safe" ? "text-gain" : status === "edge" ? "text-warn" : "text-loss";
                  const statusText = isITM ? `${dangerDist.toFixed(1)}% ITM` : status === "safe" ? "Outside" : status === "edge" ? "Near edge" : "Inside range";

                  return (
                    <Fragment key={`${i}-${si}`}>
                      {si === 0 ? (
                        <span className="font-semibold">{opp.ticker as string} <span className="font-normal text-text-muted">{opp.label}</span></span>
                      ) : (
                        <span />
                      )}
                      <span className="text-right">{s.label}</span>
                      <span className={`text-right ${isITM ? "text-loss font-bold" : "text-gain"}`}>
                        {isITM ? `${dangerDist.toFixed(1)}% ITM` : `${absDistPct.toFixed(1)}% OTM`}
                      </span>
                      <span className="text-right text-text-muted">{isITM ? "—" : `${ratio.toFixed(1)}σ`}</span>
                      <span className={`text-center font-semibold ${statusColor}`}>{statusIcon} {statusText}</span>
                    </Fragment>
                  );
                });
              })}
            </div>
          </div>

          {/* Exposure warnings — only if any */}
          {data.outlook.exposure_notes.length > 0 && (
            <div className="mt-2 pt-2 border-t border-border space-y-0.5">
              {data.outlook.exposure_notes.map((n: any, i: number) => (
                <div key={i} className="text-[0.55rem]">
                  <span className={n.type === "earnings" ? "text-warn" : n.type === "correlated" || n.type === "directional" ? "text-loss" : "text-text-muted"}>
                    ⚠ {n.note}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ═══ 5. Top Opportunities ═══ */}
      {data && (
        <div className="card">
          <div className="flex items-center justify-between mb-2">
            <div className="metric-label">Top Opportunities ({filteredOpps.length > 5 ? `5 of ${filteredOpps.length}` : filteredOpps.length})</div>
            <div className="flex items-center gap-2">
              <select value={typeFilter} onChange={e => setTypeFilter(e.target.value)}
                className="text-[0.6rem] border border-border rounded px-1.5 py-0.5 bg-surface">
                <option value="all">All</option>
                <option value="vertical">Verticals</option>
                <option value="condor">Condors</option>
              </select>
              <span className="text-[0.55rem] text-text-muted font-data">{data.scan_stats.spreads_found}s · {data.scan_stats.condors_found}c</span>
            </div>
          </div>
          {filteredOpps.length === 0 && <p className="text-xs text-text-muted py-3 text-center">No setups found.</p>}
          <OppList opps={filteredOpps.slice(0, 5)} booked={booked} onBook={handleBook} />
        </div>
      )}

      {/* News Feed moved to top — see above Trading Thesis */}

      {/* ═══ Timestamp ═══ */}
      {data && (
        <div className="text-[0.55rem] text-text-muted text-center">
          {new Date(data.market_context.timestamp).toLocaleTimeString()} · Not financial advice
        </div>
      )}
    </div>
  );
}

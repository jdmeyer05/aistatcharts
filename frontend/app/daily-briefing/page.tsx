"use client";

import { useState, useRef, useCallback, Fragment } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { fetchDailyBriefing, fetchMorningNote, fetchNewsSearch, fetchNewsVerify, fetchPolymarket, fetchPolymarketHistory, fetchRobinhoodPositions, fetchStrategyScan, fetchVolAnalysis, addPosition, type DailyBriefingResult, type NewsItem, type PolymarketEvent, type PolymarketHistoryPoint, type RHSpread, type RHStock, type StrategyScanResult, type VolAnalysis } from "@/lib/api";

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
      <div className={`border rounded-lg p-2.5 ${i === 0 ? "border-accent bg-accent-light" : "border-border"}`}>
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
    const tkList = watchlist.split(",").map(t => t.trim().toUpperCase()).filter(Boolean);
    setNewsPhase("searching"); setNewsError(""); setNewsItems([]); setNewsCatFilter("all");
    try {
      const searchRes = await fetchNewsSearch(tkList);
      if (!searchRes.success) { setNewsError(searchRes.error || "Search failed"); setNewsPhase("idle"); return; }
      setNewsItems(searchRes.items); setNewsSources(searchRes.sources);
      newsDoneRef.current = searchRes.items; maybeAutoAI();
      if (searchRes.items.length > 0) {
        setNewsPhase("verifying");
        await new Promise(r => setTimeout(r, 2000));
        try {
          const verifyRes = await fetchNewsVerify(searchRes.items);
          if (verifyRes.success && verifyRes.items.length > 0) {
            setNewsItems(verifyRes.items); setNewsSources(verifyRes.sources);
            newsDoneRef.current = verifyRes.items;
          }
        } catch {}
      }
      setNewsPhase("done");
    } catch (e) { setNewsError((e as Error).message); setNewsPhase("idle"); }
  };

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
    <div className="space-y-3">
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
                  <div className="flex items-baseline gap-1.5 flex-wrap">
                    <span className="font-bold text-[0.7rem]">{item.ticker}</span>
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

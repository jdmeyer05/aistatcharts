"use client";

import React, { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { fetchStrategyScan, fetchVolAnalysis, fetchRobinhoodPositions, fetchTradeIdeaAnalysis, fetchNewsSearch, type StrategyScanResult, type VolAnalysis } from "@/lib/api";

// ── Strategy families ──
// Scoring families — only these count toward confluence
const SCORING_FAMILIES: Record<string, { label: string; color: string; strategies: string[] }> = {
  trend: { label: "Trend", color: "text-blue-400", strategies: ["sma_cross", "ema_cross", "golden_cross", "macd", "donchian", "atr_trail", "momentum", "adx_di", "parabolic_sar", "ichimoku", "tema_cross"] },
  mean_rev: { label: "Mean Rev", color: "text-purple-400", strategies: ["rsi_ob_os", "mean_rev", "bb_breakout", "zscore_mr", "stochastic", "cci", "williams_r"] },
  volume: { label: "Volume", color: "text-cyan-400", strategies: ["obv_divergence"] },
  composite: { label: "Composite", color: "text-amber-400", strategies: ["trend_mr_composite", "trend_bb_composite"] },
};
// Calendar is display-only — always-on seasonal, not a real signal
const DISPLAY_FAMILIES: Record<string, { label: string; color: string; strategies: string[] }> = {
  calendar: { label: "Calendar", color: "text-emerald-400", strategies: ["turn_of_month", "halloween"] },
};
const ALL_FAMILIES = { ...SCORING_FAMILIES, ...DISPLAY_FAMILIES };
const ALL_STRATEGIES = Object.values(ALL_FAMILIES).flatMap(f => f.strategies);
const STRAT_NAMES: Record<string, string> = {
  sma_cross: "SMA Cross", ema_cross: "EMA Cross", golden_cross: "Golden Cross",
  macd: "MACD", donchian: "Donchian", atr_trail: "ATR Trail",
  momentum: "Momentum", adx_di: "ADX+DI", parabolic_sar: "SAR",
  ichimoku: "Ichimoku", tema_cross: "TEMA",
  rsi_ob_os: "RSI", mean_rev: "BB MR", bb_breakout: "BB Breakout",
  zscore_mr: "Z-Score", stochastic: "Stochastic", cci: "CCI", williams_r: "Williams %R",
  obv_divergence: "OBV Div", trend_mr_composite: "Trend+RSI", trend_bb_composite: "Trend+BB",
  turn_of_month: "Turn-of-Month", halloween: "Halloween",
};
const PRESETS: Record<string, { label: string; tickers: string[] }> = {
  bluechip: {
    label: "Blue Chips + ETFs",
    tickers: ["SPY","QQQ","IWM","DIA","AAPL","MSFT","NVDA","TSLA","AMZN","META","GOOGL","NFLX","AMD","JPM","BA","GS","V","MA","UNH","JNJ","PG","HD","COST","XOM","CVX"],
  },
  sectors: {
    label: "Sector Rotation",
    tickers: ["XLK","XLF","XLE","XLV","XLI","XLC","XLY","XLP","XLRE","XLU","XLB","SMH","GLD","SLV","TLT","HYG","USO","GDX","IBB","ARKK"],
  },
  highvol: {
    label: "High Volatility",
    tickers: ["TSLA","AMD","NVDA","MSTR","COIN","PLTR","SOFI","RIVN","LCID","ARM","SMCI","RBLX","SNAP","SQ","SHOP","ROKU","CRWD","NET","DKNG","MARA"],
  },
};
const DEFAULT_TICKERS = PRESETS.bluechip.tickers;

interface TradeIdea {
  ticker: string;
  direction: "long" | "short";
  // Trigger
  trigger: { strategy: string; signalDays: number; dsr: number; excessSharpe: number; winRate: number; best_stop_atr?: number; avg_mae_atr?: number; avg_mfe_atr?: number; stop_2x_survival?: number };
  // Confluence
  familyConfirmations: { family: string; label: string; color: string; count: number; total: number; best: string }[];
  confluenceScore: number;
  dissentFamilies: number;
  totalFamilies: number;
  // Price levels
  price: number;
  stop: number;
  target: number;
  riskReward: number;
  riskPct: number;
  targetPct: number;
  stopMult: number;
  atr: number;
  rsi: number;
  high20: number;
  low20: number;
  // All confirming strategies
  confirmingStrategies: StrategyScanResult[];
  freshCount: number;
  // Computed
  expectedValue: number; // win% × reward - loss% × risk, per trade
  warnings: string[];
  triggerDiversity: boolean; // false if all ideas share the same trigger strategy
  // Vol/options data (added after scan)
  vol?: VolAnalysis;
  optionsSuggestion?: string;
  alreadyHeld?: boolean;
}

function computeTradeIdeas(results: StrategyScanResult[]): TradeIdea[] {
  const byTicker: Record<string, StrategyScanResult[]> = {};
  for (const r of results) (byTicker[r.ticker] ??= []).push(r);

  const ideas: TradeIdea[] = [];

  for (const [ticker, tickerResults] of Object.entries(byTicker)) {
    const freshSignals = tickerResults.filter(r =>
      r.current_signal !== "Flat" && r.signal_days <= 10 && r.dsr >= 0.5
    ).sort((a, b) => b.dsr - a.dsr);

    if (freshSignals.length === 0) continue;

    const trigger = freshSignals[0];
    const direction = trigger.current_signal === "Long" ? "long" : "short";
    const matchSignal = trigger.current_signal;

    // SCORING families only — calendar excluded from confluence count
    const confirmations: TradeIdea["familyConfirmations"] = [];
    let confirmedScoringFamilies = 0;

    for (const [famKey, fam] of Object.entries(SCORING_FAMILIES)) {
      const famResults = tickerResults.filter(r => fam.strategies.includes(r.strategy));
      const matching = famResults.filter(r => r.current_signal === matchSignal);
      const best = matching.sort((a, b) => b.dsr - a.dsr)[0];
      if (matching.length > 0) {
        confirmedScoringFamilies++;
        confirmations.push({
          family: famKey, label: fam.label, color: fam.color,
          count: matching.length, total: famResults.length,
          best: best ? (STRAT_NAMES[best.strategy] || best.strategy) : "",
        });
      }
    }
    // Calendar display-only (not counted)
    for (const [famKey, fam] of Object.entries(DISPLAY_FAMILIES)) {
      const famResults = tickerResults.filter(r => fam.strategies.includes(r.strategy));
      const matching = famResults.filter(r => r.current_signal === matchSignal);
      if (matching.length > 0) {
        confirmations.push({
          family: famKey, label: fam.label + " *", color: "text-text-muted",
          count: matching.length, total: famResults.length,
          best: "",
        });
      }
    }

    // Need at least 2 SCORING families confirming
    if (confirmedScoringFamilies < 2) continue;

    // Dissent check — scoring families only
    const oppositeSignal = matchSignal === "Long" ? "Short" : "Long";
    const dissentFamilies = Object.entries(SCORING_FAMILIES).filter(([, fam]) => {
      const famResults = tickerResults.filter(r => fam.strategies.includes(r.strategy));
      return famResults.some(r => r.current_signal === oppositeSignal);
    }).length;
    if (dissentFamilies >= confirmedScoringFamilies) continue;

    const confirming = tickerResults
      .filter(r => r.current_signal === matchSignal)
      .sort((a, b) => b.dsr - a.dsr);

    const price = trigger.current_price || 0;
    const atr = trigger.atr_14 || 0;
    const rsi = trigger.rsi || 50;
    const high20 = trigger.high_20d || price;
    const low20 = trigger.low_20d || price;
    if (price <= 0 || atr <= 0) continue;

    // Stop/target: use backtest data for trend strategies, signal-based for mean reversion
    const isTrendTrigger = SCORING_FAMILIES.trend.strategies.includes(trigger.strategy) ||
                           SCORING_FAMILIES.composite.strategies.includes(trigger.strategy);
    const isMeanRevTrigger = SCORING_FAMILIES.mean_rev.strategies.includes(trigger.strategy);

    // For trend: use backtest-validated ATR stop (if survival > 50%)
    // For mean reversion: use wider stop (the trade expects drawdown before recovery)
    let stopMult: number;
    if (isTrendTrigger && trigger.best_stop_atr && trigger.stop_2x_survival && trigger.stop_2x_survival > 50) {
      stopMult = trigger.best_stop_atr;
    } else if (isMeanRevTrigger) {
      // MR trades draw down before recovering — use the avg MAE as a guide, minimum 2.5
      stopMult = Math.max(2.5, Math.min(trigger.avg_mae_atr || 3.0, 5.0));
    } else {
      stopMult = 2.0; // default
    }
    const targetMult = stopMult * 1.5;

    let stop: number, target: number;
    if (direction === "long") {
      stop = Math.round((price - stopMult * atr) * 100) / 100;
      target = Math.round(Math.max(high20, price + targetMult * atr) * 100) / 100;
    } else {
      stop = Math.max(0.01, Math.round((price + stopMult * atr) * 100) / 100);
      target = Math.max(0.01, Math.round(Math.min(low20, price - targetMult * atr) * 100) / 100);
    }

    const risk = Math.abs(price - stop);
    const reward = Math.abs(target - price);
    const riskReward = risk > 0 ? Math.round(reward / risk * 10) / 10 : 0;
    const riskPct = Math.round((risk / price) * 1000) / 10;
    const targetPct = Math.round((reward / price) * 1000) / 10;
    if (riskReward < 1.0) continue;

    // Expected value per trade: win% × reward - loss% × risk
    const winRate = trigger.win_rate / 100;
    const ev = winRate * reward - (1 - winRate) * risk;
    const evPct = (ev / price) * 100;

    // Skip negative EV or negligible edge
    if (ev <= 0) continue;

    // Warnings
    const warnings: string[] = [];
    const range20 = high20 - low20;
    const rangePct = range20 > 0 ? ((price - low20) / range20) * 100 : 50;
    if (direction === "long" && rangePct > 80) warnings.push(`Buying near 20d high (${rangePct.toFixed(0)}% of range)`);
    if (direction === "short" && rangePct < 20) warnings.push(`Shorting near 20d low (${rangePct.toFixed(0)}% of range)`);
    if (evPct < 0.5) warnings.push(`Thin edge — EV ${evPct.toFixed(2)}% per trade ($${ev.toFixed(2)})`);
    if (dissentFamilies >= 1) warnings.push(`${dissentFamilies}/${Object.keys(SCORING_FAMILIES).length} families dissenting`);

    const freshCount = confirming.filter(r => r.signal_days <= 5).length;

    ideas.push({
      ticker, direction,
      trigger: {
        strategy: trigger.strategy,
        signalDays: trigger.signal_days,
        dsr: trigger.dsr,
        excessSharpe: trigger.excess_sharpe,
        winRate: trigger.win_rate,
        best_stop_atr: trigger.best_stop_atr,
        avg_mae_atr: trigger.avg_mae_atr,
        avg_mfe_atr: trigger.avg_mfe_atr,
        stop_2x_survival: trigger.stop_2x_survival,
      },
      familyConfirmations: confirmations,
      confluenceScore: confirmedScoringFamilies, dissentFamilies,
      totalFamilies: Object.keys(SCORING_FAMILIES).length,
      price, stop, target, riskReward, riskPct, targetPct, stopMult, atr, rsi, high20, low20,
      confirmingStrategies: confirming,
      expectedValue: Math.round(ev * 100) / 100,
      warnings,
      triggerDiversity: true, // set below after all ideas computed
      freshCount,
    });
  }

  // Sort: most families confirming, then best R:R
  // Check trigger diversity — if >50% of ideas share the same trigger strategy, flag them
  const triggerCounts: Record<string, number> = {};
  for (const idea of ideas) triggerCounts[idea.trigger.strategy] = (triggerCounts[idea.trigger.strategy] || 0) + 1;
  const mostCommonTrigger = Object.entries(triggerCounts).sort((a, b) => b[1] - a[1])[0];
  if (mostCommonTrigger && mostCommonTrigger[1] > ideas.length * 0.5) {
    for (const idea of ideas) {
      if (idea.trigger.strategy === mostCommonTrigger[0]) {
        idea.triggerDiversity = false;
        idea.warnings.push(`Same trigger (${STRAT_NAMES[idea.trigger.strategy] || idea.trigger.strategy}) across ${mostCommonTrigger[1]}/${ideas.length} ideas`);
      }
    }
  }

  ideas.sort((a, b) => b.confluenceScore - a.confluenceScore || b.expectedValue - a.expectedValue);
  return ideas;
}

function suggestOptions(direction: string, vol: VolAnalysis | undefined): string {
  if (!vol) return "";
  const ivr = vol.ivr;
  const iv = vol.iv;
  const rv = vol.rv_20d;
  const isLong = direction === "long";

  // Use IV vs RV as primary signal (more reliable than IVR proxy)
  if (iv && rv) {
    const ivRich = iv > rv * 1.05; // IV > RV by 5%+ = options overpriced → sell
    const ivCheap = rv > iv * 1.05; // RV > IV by 5%+ = options underpriced → buy

    if (ivRich) {
      return isLong
        ? `IV ${iv}% > RV ${rv}% — market pricing more vol than realized → Sell Bull Put Spread (collect overpriced premium)`
        : `IV ${iv}% > RV ${rv}% — market pricing more vol than realized → Sell Bear Call Spread (collect overpriced premium)`;
    } else if (ivCheap) {
      return isLong
        ? `IV ${iv}% < RV ${rv}% — options are cheap relative to actual moves → Buy Bull Call Spread (leveraged upside)`
        : `IV ${iv}% < RV ${rv}% — options are cheap relative to actual moves → Buy Bear Put Spread (leveraged downside)`;
    }
  }

  // Fallback to IVR if IV/RV not available
  if (ivr != null) {
    if (ivr >= 50) return isLong
      ? `IV Percentile ${ivr}% (options expensive vs history) → Sell Bull Put Spread to collect premium`
      : `IV Percentile ${ivr}% (options expensive vs history) → Sell Bear Call Spread to collect premium`;
    if (ivr <= 25) return isLong
      ? `IV Percentile ${ivr}% (options cheap vs history) → Buy Bull Call Spread for leveraged upside`
      : `IV Percentile ${ivr}% (options cheap vs history) → Buy Bear Put Spread for leveraged downside`;
    return `IV Percentile ${ivr}% (mid-range) → credit or debit spread both viable`;
  }
  return "";
}

export default function TradeIdeas() {
  const [watchlist, setWatchlist] = useState(DEFAULT_TICKERS.join(", "));
  const [volData, setVolData] = useState<Record<string, VolAnalysis>>({});
  const rhQuery = useQuery({ queryKey: ["rh-positions"], queryFn: fetchRobinhoodPositions, staleTime: 5 * 60_000 });
  const accountEquity = rhQuery.data?.portfolio?.equity || 12500;

  const scan = useMutation({
    mutationFn: async () => {
      const tickers = watchlist.split(",").map(t => t.trim().toUpperCase()).filter(Boolean);
      setAnalysis(""); // clear old analysis on re-scan
      const [scanRes, volRes] = await Promise.all([
        fetchStrategyScan(tickers, ALL_STRATEGIES, 756),  // 3yr lookback — enough for signals, faster
        fetchVolAnalysis(tickers).catch(() => ({ success: false, results: {} })),
      ]);
      if (volRes.success) setVolData(volRes.results);
      return scanRes;
    },
  });

  const [analysis, setAnalysis] = useState("");
  const [newsSummary, setNewsSummary] = useState("");

  const analysisMutation = useMutation({
    mutationFn: async () => {
      const bookSummary = rhQuery.data?.portfolio
        ? `Portfolio: $${rhQuery.data.portfolio.equity.toLocaleString()} equity. ` +
          (rhQuery.data.spreads ?? []).map(s => `${s.ticker} ${s.type} ${s.strikes}`).join(", ") +
          ". " + (rhQuery.data.stocks ?? []).map(s => `${s.ticker} ${Math.round(s.qty)}sh`).join(", ")
        : "";
      // Fetch news for the idea tickers if we don't have it yet
      let news = newsSummary;
      if (!news) {
        try {
          const ideaTickers = ideas.map(i => i.ticker);
          const newsRes = await fetchNewsSearch(ideaTickers);
          if (newsRes.success && newsRes.items.length > 0) {
            news = newsRes.items.slice(0, 10).map(n =>
              `[${n.ticker}] ${n.headline} — ${n.source} (${n.impact})`
            ).join("\n");
            setNewsSummary(news);
          }
        } catch {}
      }
      return fetchTradeIdeaAnalysis(ideas, bookSummary, news);
    },
    onSuccess: (r) => { if (r.success) setAnalysis(r.analysis); else setAnalysis(`Error: ${r.error}`); },
  });

  const heldTickers = new Set([
    ...(rhQuery.data?.stocks ?? []).map(s => s.ticker),
    ...(rhQuery.data?.spreads ?? []).map(s => s.ticker),
  ]);

  const ideas = scan.data ? computeTradeIdeas(scan.data.results).map(idea => {
    const vol = volData[idea.ticker];
    return { ...idea, vol, optionsSuggestion: suggestOptions(idea.direction, vol), alreadyHeld: heldTickers.has(idea.ticker) };
  }) : [];

  return (
    <div className="space-y-4">
      <div className="card card-compact">
        <div className="flex flex-wrap items-end gap-3">
          <div className="flex-1 min-w-[200px]">
            <h1 className="text-xl font-bold tracking-tight mb-1">Trade Ideas</h1>
            <p className="text-text-secondary text-[0.6rem] mb-1">Fresh signals ({"\u2264"}10d) + 2+ family confluence + positive EV + ATR stops + IV/RV options overlay</p>
            <div className="flex gap-1 mb-1">
              {Object.entries(PRESETS).map(([key, preset]) => (
                <button key={key} onClick={() => setWatchlist(preset.tickers.join(", "))}
                  className={`px-2 py-0.5 rounded text-[0.55rem] font-semibold border transition-colors ${
                    watchlist === preset.tickers.join(", ") ? "border-accent text-accent bg-accent/10" : "border-border text-text-muted hover:text-text"
                  }`}>
                  {preset.label} ({preset.tickers.length})
                </button>
              ))}
            </div>
            <textarea value={watchlist} onChange={e => setWatchlist(e.target.value)} rows={2}
              className="w-full px-3 py-1.5 border border-border rounded-lg text-xs font-data bg-surface resize-y" />
          </div>
          <button onClick={() => scan.mutate()} disabled={scan.isPending}
            className="px-5 py-1.5 bg-accent text-white font-semibold rounded-lg hover:bg-accent-hover disabled:opacity-50 text-sm">
            {scan.isPending ? "Scanning..." : "Find Ideas"}
          </button>
        </div>
        {scan.isPending && (
          <div className="flex items-center gap-2 mt-2">
            <div className="w-3 h-3 border-2 border-accent border-t-transparent rounded-full animate-spin" />
            <span className="text-xs text-text-muted">Running {ALL_STRATEGIES.length} strategies on {watchlist.split(",").filter(Boolean).length} tickers...</span>
          </div>
        )}
      </div>

      {scan.isError && <div className="card border-loss/30 bg-loss-bg text-loss text-sm">{(scan.error as Error).message}</div>}

      {scan.data && ideas.length === 0 && (
        <div className="card text-center text-sm text-text-muted py-6">
          No trade ideas found. Filters: fresh signal ({"\u2264"}10d), 2+ family confirmation, R:R {"\u2265"} 1.0, positive expected value.
        </div>
      )}

      {/* Summary */}
      {scan.data && ideas.length > 0 && (
        <div className="flex items-center gap-4 px-3 py-2 rounded-lg border border-border bg-surface text-xs font-data">
          <span className="text-text-muted">{scan.data.n_tested} scanned</span>
          <span className="text-text-muted">{scan.data.n_active_signals} active signals</span>
          <span className="text-text-muted">{"\u2192"} {ideas.length} passed filters</span>
          <span className="text-gain font-semibold">{ideas.filter(i => i.direction === "long").length} long</span>
          <span className="text-loss font-semibold">{ideas.filter(i => i.direction === "short").length} short</span>
        </div>
      )}

      {/* AI Analysis */}
      {ideas.length > 0 && (
        <div className="card">
          <div className="flex items-center justify-between mb-2">
            <div className="metric-label">AI Trade Analysis</div>
            <button onClick={() => analysisMutation.mutate()} disabled={analysisMutation.isPending}
              className="px-3 py-1 bg-accent text-white text-[0.6rem] font-semibold rounded hover:bg-accent-hover disabled:opacity-50">
              {analysisMutation.isPending ? "Analyzing..." : analysis ? "Re-analyze" : "Analyze Ideas"}
            </button>
          </div>
          {analysisMutation.isPending && (
            <div className="flex items-center gap-2 py-3">
              <div className="w-4 h-4 border-2 border-accent border-t-transparent rounded-full animate-spin" />
              <span className="text-xs text-text-muted">Gemini reviewing {ideas.length} trade ideas...</span>
            </div>
          )}
          {analysis && (
            <div className="text-xs leading-relaxed" dangerouslySetInnerHTML={{
              __html: analysis
                .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
                .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
                .replace(/^#+\s*(.+)$/gm, '<div class="mt-3 mb-1 text-[0.7rem] font-bold text-accent border-b border-accent/20 pb-0.5">$1</div>')
                .replace(/\n\n/g, '</p><p class="mt-1.5">')
                .replace(/\n/g, "<br/>"),
            }} />
          )}
          {!analysis && !analysisMutation.isPending && (
            <p className="text-xs text-text-muted">
              Click "Analyze Ideas" for AI review of each trade with specific recommendations.
              {Object.keys(volData).length === 0 && " (Vol data loading — analysis will be more complete after it loads.)"}
            </p>
          )}
        </div>
      )}

      {/* Ideas */}
      <div className="space-y-3">
        {ideas.map((idea) => <IdeaCard key={idea.ticker} idea={idea} acctEquity={accountEquity} />)}
      </div>
    </div>
  );
}

function IdeaCard({ idea, acctEquity }: { idea: TradeIdea; acctEquity: number }) {
  const [expanded, setExpanded] = useState(false);
  const isLong = idea.direction === "long";

  return (
    <div className={`rounded-lg border p-3 ${
      idea.confluenceScore >= 4 ? (isLong ? "border-gain/50" : "border-loss/50")
      : idea.confluenceScore >= 3 ? (isLong ? "border-gain/30" : "border-loss/30")
      : "border-border"
    }`}>
      {/* Row 1: Ticker + direction + conviction */}
      <div className="flex items-start justify-between mb-2">
        <div>
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-bold text-lg">{idea.ticker}</span>
            <span className={`badge font-bold text-[0.55rem] ${isLong ? "badge-gain" : "badge-loss"}`}>
              {isLong ? "BUY" : "SELL"}
            </span>
            <span className={`text-[0.6rem] font-bold ${idea.confluenceScore >= 4 ? "text-gain" : idea.confluenceScore >= 3 ? "text-text" : "text-text-muted"}`}>
              {idea.confluenceScore}/{idea.totalFamilies}
            </span>
            {idea.confluenceScore >= 4 && <span className="badge badge-gain text-[0.5rem]">STRONG</span>}
            {idea.alreadyHeld && <span className="badge badge-info text-[0.5rem]">HELD</span>}
            {idea.freshCount >= 3 && <span className="badge badge-warn text-[0.5rem]">{idea.freshCount} FRESH</span>}
          </div>

          {/* Trigger */}
          <div className="text-[0.6rem] text-text mt-0.5">
            <span className="text-text-muted">Trigger:</span>{" "}
            <span className="font-semibold">{STRAT_NAMES[idea.trigger.strategy] || idea.trigger.strategy}</span>{" "}
            flipped {idea.trigger.signalDays}d ago{" "}
            <span className="text-text-muted">(DSR {(idea.trigger.dsr * 100).toFixed(0)}%, win {idea.trigger.winRate}%)</span>
            {" "}<span className={`font-semibold ${idea.expectedValue > 0 ? "text-gain" : "text-loss"}`}>
              EV ${idea.expectedValue > 0 ? "+" : ""}{idea.expectedValue.toFixed(2)}/trade
            </span>
          </div>
          {/* Warnings */}
          {idea.warnings.length > 0 && (
            <div className="flex flex-wrap gap-1.5 mt-1">
              {idea.warnings.map((w, i) => (
                <span key={i} className="text-[0.5rem] text-warn px-1.5 py-0.5 rounded border border-warn/30 bg-warn/5">{w}</span>
              ))}
            </div>
          )}
        </div>
        <div className="text-right font-data">
          <div className="text-lg font-bold">${idea.price.toFixed(2)}</div>
          <div className="text-[0.5rem] text-text-muted">RSI {idea.rsi}</div>
        </div>
      </div>

      {/* Row 2: Price levels bar */}
      <div className="mb-2">
        {(() => {
          const lo = Math.min(idea.stop, idea.low20, idea.price) * 0.998;
          const hi = Math.max(idea.target, idea.high20, idea.price) * 1.002;
          const range = hi - lo || 1;
          const toPct = (v: number) => Math.max(1, Math.min(99, ((v - lo) / range) * 100));
          return (
            <div className="relative h-4 rounded-full bg-surface-alt border border-border">
              {/* Risk zone */}
              {(() => {
                const riskLeft = isLong ? toPct(idea.stop) : toPct(idea.price);
                const riskRight = isLong ? toPct(idea.price) : toPct(idea.stop);
                const w = Math.max(0, riskRight - riskLeft);
                return w > 0 ? <div className="absolute inset-y-0 bg-loss/10" style={{ left: `${riskLeft}%`, width: `${w}%` }} /> : null;
              })()}
              {/* Reward zone */}
              {(() => {
                const rewLeft = isLong ? toPct(idea.price) : toPct(idea.target);
                const rewRight = isLong ? toPct(idea.target) : toPct(idea.price);
                const w = Math.max(0, rewRight - rewLeft);
                return w > 0 ? <div className="absolute inset-y-0 bg-gain/10" style={{ left: `${rewLeft}%`, width: `${w}%` }} /> : null;
              })()}
              {/* Stop marker */}
              <div className="absolute top-0 bottom-0" style={{ left: `${toPct(idea.stop)}%`, transform: "translateX(-50%)" }}>
                <div className="w-0.5 h-full bg-loss/60" />
              </div>
              {/* Target marker */}
              <div className="absolute top-0 bottom-0" style={{ left: `${toPct(idea.target)}%`, transform: "translateX(-50%)" }}>
                <div className="w-0.5 h-full bg-gain/60" />
              </div>
              {/* Current price */}
              <div className="absolute top-0 bottom-0" style={{ left: `${toPct(idea.price)}%`, transform: "translateX(-50%)" }}>
                <div className="w-2 h-full bg-accent rounded-full" />
              </div>
            </div>
          );
        })()}
        <div className="flex justify-between text-[0.5rem] font-data mt-0.5">
          <span className="text-loss">Stop ${idea.stop.toFixed(2)} ({isLong ? "-" : "+"}{idea.riskPct}%)</span>
          <span className="text-accent">${idea.price.toFixed(2)}</span>
          <span className="text-gain">Target ${idea.target.toFixed(2)} ({isLong ? "+" : "-"}{idea.targetPct}%)</span>
        </div>
      </div>

      {/* Row 3: R:R + family confirmations + position sizing */}
      <div className="flex items-center gap-2 flex-wrap">
        <div className={`px-2 py-1 rounded border font-data text-[0.6rem] font-bold ${
          idea.riskReward >= 2 ? "border-gain/40 text-gain" : idea.riskReward >= 1.5 ? "border-gain/20 text-text" : "border-border text-text-muted"
        }`}>
          {idea.riskReward}:1 R:R
        </div>
        {(() => {
          const acct = acctEquity;
          const riskDollars = acct * 0.01;
          const riskPerShare = Math.abs(idea.price - idea.stop);
          const shares = riskPerShare > 0 ? Math.floor(riskDollars / riskPerShare) : 0;
          const contracts = Math.floor(shares / 100);
          return (
            <span className="text-[0.5rem] font-data text-text-muted">
              1% risk = {shares} shares{contracts > 0 ? ` (${contracts} contracts)` : ""}
            </span>
          );
        })()}
        {idea.familyConfirmations.map((fc) => (
          <span key={fc.family} className={`px-1.5 py-0.5 rounded border border-border text-[0.5rem] font-data ${fc.color}`}>
            {fc.label} {fc.count}/{fc.total} <span className="text-text-muted">({fc.best})</span>
          </span>
        ))}
        {/* dissent shown in warnings — no duplicate here */}
      </div>

      {/* Row 4: Trade structure suggestions */}
      <div className="mt-1.5 flex flex-wrap gap-1.5">
        {/* Stock trade */}
        <div className="px-2 py-1 rounded border border-border text-[0.55rem] font-data">
          <span className="text-text-muted">Stock:</span>{" "}
          <span className="text-text">
            {idea.direction === "long" ? "Buy" : "Short"} at ${idea.price.toFixed(2)}, stop ${idea.stop.toFixed(2)} ({idea.stopMult.toFixed(1)}×ATR{idea.trigger.stop_2x_survival != null && idea.trigger.stop_2x_survival > 50 ? " validated" : ""}), target ${idea.target.toFixed(2)}
          </span>
        </div>
        {/* Options trade */}
        {idea.optionsSuggestion && (
          <div className={`px-2 py-1 rounded border text-[0.55rem] font-data ${
            idea.vol?.iv && idea.vol?.rv_20d && idea.vol.iv > idea.vol.rv_20d ? "border-gain/30 bg-gain/5 text-gain"
            : idea.vol?.iv && idea.vol?.rv_20d ? "border-purple-400/30 bg-purple-400/5 text-purple-400"
            : "border-border text-text"
          }`}>
            <span className="text-text-muted">Options:</span> {idea.optionsSuggestion}
          </div>
        )}
        {idea.vol?.avg_earnings_move && (
          <span className="px-2 py-1 rounded border border-warn/30 bg-warn/5 text-warn text-[0.55rem] font-data">
            Avg earnings move: ±{idea.vol.avg_earnings_move}%
          </span>
        )}
        {idea.vol?.next_earnings_days != null && idea.vol.next_earnings_days <= 21 && (
          <span className="px-2 py-1 rounded border border-loss/30 bg-loss/5 text-loss text-[0.55rem] font-data font-bold">
            Earnings in {idea.vol.next_earnings_days}d
          </span>
        )}
      </div>

      {/* Row 5: Context */}
      <div className="flex gap-3 mt-1.5 text-[0.5rem] font-data text-text-muted flex-wrap">
        <span>ATR ${idea.atr.toFixed(2)}</span>
        <span>20d: ${idea.low20.toFixed(2)}-${idea.high20.toFixed(2)}</span>
        <span>RSI {idea.rsi}{idea.rsi < 30 ? " (oversold)" : idea.rsi > 70 ? " (overbought)" : ""}</span>
        <span>
          Price at {(() => {
            const range = idea.high20 - idea.low20;
            if (range <= 0) return "?";
            const pct = ((idea.price - idea.low20) / range) * 100;
            return `${pct.toFixed(0)}% of 20d range`;
          })()}
        </span>
        {idea.vol?.iv && <span>IV {idea.vol.iv}%</span>}
        {idea.vol?.rv_20d && <span>RV20 {idea.vol.rv_20d}%</span>}
        {idea.vol?.iv && idea.vol?.rv_20d && (
          <span className={idea.vol.iv > idea.vol.rv_20d ? "text-gain" : "text-loss"}>
            {idea.vol.iv > idea.vol.rv_20d
              ? `IV > RV by ${(idea.vol.iv - idea.vol.rv_20d).toFixed(1)}pp (sell premium)`
              : `IV < RV by ${(idea.vol.rv_20d - idea.vol.iv).toFixed(1)}pp (buy options)`}
          </span>
        )}
        {idea.vol?.next_earnings && (
          <span className={idea.vol.next_earnings_days != null && idea.vol.next_earnings_days <= 14 ? "text-warn font-semibold" : ""}>
            Earn {idea.vol.next_earnings_days}d
          </span>
        )}
        {idea.vol?.short_pct != null && (
          <span className={idea.vol.short_pct >= 20 ? "text-warn font-semibold" : idea.vol.short_pct >= 10 ? "text-text" : "text-text-muted"}>
            SI {idea.vol.short_pct}%
            {idea.vol.short_pct >= 20 && idea.direction === "long" ? " (squeeze potential)" : ""}
            {idea.vol.short_pct >= 20 && idea.direction === "short" ? " (crowded short)" : ""}
          </span>
        )}
        {idea.vol?.short_ratio != null && idea.vol.short_ratio >= 5 && (
          <span className="text-warn">{idea.vol.short_ratio}d to cover</span>
        )}
        <span>{idea.confirmingStrategies.length} confirming</span>
        {idea.trigger.stop_2x_survival != null && (
          <span className={idea.trigger.stop_2x_survival >= 80 ? "text-gain" : idea.trigger.stop_2x_survival >= 60 ? "text-text" : "text-warn"}>
            2×ATR stop: {idea.trigger.stop_2x_survival}% survival
          </span>
        )}
        {idea.trigger.avg_mfe_atr != null && idea.trigger.avg_mae_atr != null && idea.trigger.avg_mae_atr < 10 && (
          <span className="text-text-muted">
            avg run {idea.trigger.avg_mfe_atr}× / drawdown {idea.trigger.avg_mae_atr}× ATR
          </span>
        )}
      </div>

      {/* Expand */}
      <button onClick={() => setExpanded(!expanded)} className="text-[0.55rem] text-text-muted hover:text-accent mt-1.5">
        {expanded ? "▾ Hide" : `▸ ${idea.confirmingStrategies.length} confirming strategies`}
      </button>

      {expanded && (
        <div className="mt-2 pt-2 border-t border-border">
          <div className="grid grid-cols-[1fr_auto_auto_auto_auto_auto] gap-x-3 gap-y-0.5 text-[0.55rem] font-data">
            <span className="text-text-muted">Strategy</span>
            <span className="text-text-muted text-right">Signal</span>
            <span className="text-text-muted text-right">Age</span>
            <span className="text-text-muted text-right">DSR</span>
            <span className="text-text-muted text-right">Ex.Sharpe</span>
            <span className="text-text-muted text-right">Win%</span>
            {idea.confirmingStrategies.slice(0, 15).map((r, i) => (
              <React.Fragment key={i}>
                <span className={r.signal_days <= 5 ? "text-text font-semibold" : "text-text-muted"}>
                  {STRAT_NAMES[r.strategy] || r.strategy}
                  {r.signal_days <= 5 && <span className="text-warn ml-1">NEW</span>}
                </span>
                <span className={`text-right ${r.current_signal === "Long" ? "text-gain" : "text-loss"}`}>{r.current_signal}</span>
                <span className={`text-right ${r.signal_days <= 5 ? "text-warn font-bold" : "text-text-muted"}`}>{r.signal_days}d</span>
                <span className={`text-right ${r.dsr >= 0.95 ? "text-gain font-semibold" : ""}`}>{(r.dsr * 100).toFixed(0)}%</span>
                <span className={`text-right ${r.excess_sharpe > 0 ? "text-gain" : "text-loss"}`}>{r.excess_sharpe > 0 ? "+" : ""}{r.excess_sharpe.toFixed(2)}</span>
                <span className="text-right">{r.win_rate}%</span>
              </React.Fragment>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

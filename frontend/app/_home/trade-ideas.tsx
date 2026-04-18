"use client";

import React, { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { fetchStrategyScan, fetchVolAnalysis, fetchRobinhoodPositions, fetchTradeIdeaAnalysis, fetchTradeIdeaQuick, fetchNewsSearch, type StrategyScanResult, type VolAnalysis } from "@/lib/api";

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
    label: "Blue Chips",
    tickers: ["SPY","QQQ","IWM","DIA","AAPL","MSFT","NVDA","TSLA","AMZN","META","GOOGL","NFLX","AMD","JPM","BA","GS","V","MA","UNH","JNJ","PG","HD","COST","XOM","CVX"],
  },
  sectors: {
    label: "Sectors",
    tickers: ["XLK","XLF","XLE","XLV","XLI","XLC","XLY","XLP","XLRE","XLU","XLB","SMH","GLD","SLV","TLT","HYG","USO","GDX","IBB","ARKK"],
  },
  highvol: {
    label: "High Vol",
    tickers: ["TSLA","AMD","NVDA","MSTR","COIN","PLTR","SOFI","RIVN","LCID","ARM","SMCI","RBLX","SNAP","SQ","SHOP","ROKU","CRWD","NET","DKNG","MARA"],
  },
  meme: {
    label: "Meme / Reddit",
    tickers: ["GME","AMC","MSTR","COIN","PLTR","SOFI","HOOD","RIVN","LCID","MARA","RIOT","SMCI","IONQ","RGTI","RKLB","BBAI"],
  },
  commodities: {
    label: "Commodities + Macro",
    tickers: ["GLD","SLV","GDX","USO","UNG","TLT","TIP","DBA","WEAT","URA","COPX","LIT"],
  },
  semis: {
    label: "Semiconductors",
    tickers: ["NVDA","AMD","INTC","AVGO","QCOM","MU","MRVL","ARM","SMCI","TSM","ASML","LRCX","KLAC","AMAT"],
  },
  defense: {
    label: "Defense / Iran",
    tickers: ["LMT","RTX","NOC","GD","BA","HII","LHX","KTOS","DFEN","XAR"],
  },
  holdings: {
    label: "My Holdings",
    tickers: ["RGTI","QUBT","QBTS","IONQ","UAMY","SPY","USO"],  // updated dynamically from RH below
  },
};
const DEFAULT_TICKERS = PRESETS.bluechip.tickers;


interface TradeIdea {
  ticker: string;
  direction: "long" | "short";
  // Trigger
  trigger: { strategy: string; signalDays: number; dsr: number; sharpe: number; excessSharpe: number; winRate: number; trades: number; recentSharpe?: number; best_stop_atr?: number; avg_mae_atr?: number; avg_mfe_atr?: number; stop_2x_survival?: number; avgHoldDays?: number; medianHoldDays?: number; entryUrgency?: string; delaySharpes?: Record<string, number> };
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

  // DSR threshold scales with n_tested: stricter for small scans, looser for large
  // With 460 tests (20 tickers): threshold ≈ 0.5
  // With 2277 tests (99 tickers): threshold ≈ 0.15
  const nTickers = Object.keys(byTicker).length;
  const dsrThreshold = nTickers <= 20 ? 0.5 : nTickers <= 50 ? 0.3 : 0.15;

  const ideas: TradeIdea[] = [];

  for (const [ticker, tickerResults] of Object.entries(byTicker)) {
    const freshSignals = tickerResults.filter(r =>
      r.current_signal !== "Flat" && r.signal_days <= 10 && r.dsr >= dsrThreshold && r.trades >= 5 && r.sharpe > 0
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
      // Count strategies with non-negative Sharpe as confirmations (deeply negative = proven non-performer)
      const matching = famResults.filter(r => r.current_signal === matchSignal && r.sharpe > -0.5);
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
      // Only count non-terrible strategies as meaningful dissent
      return famResults.some(r => r.current_signal === oppositeSignal && r.sharpe > -0.5);
    }).length;
    if (dissentFamilies >= confirmedScoringFamilies) continue;

    const confirming = tickerResults
      .filter(r => r.current_signal === matchSignal && r.sharpe > -0.5)
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

    let stopMult: number;
    if (isTrendTrigger && trigger.best_stop_atr && trigger.stop_2x_survival && trigger.stop_2x_survival > 50) {
      stopMult = trigger.best_stop_atr;
    } else if (isMeanRevTrigger) {
      stopMult = Math.max(2.5, Math.min(trigger.avg_mae_atr || 3.0, 5.0));
    } else {
      stopMult = 2.0;
    }
    // Target: use avg MFE from backtest (how far trades actually run), minimum 1.5× stop
    const mfeMult = trigger.avg_mfe_atr || 0;
    const targetMult = Math.max(stopMult * 1.5, mfeMult > 0 ? mfeMult : stopMult * 1.5);

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

    // Skip significantly negative EV — but allow marginal trades with warning
    if (ev < -(risk * 0.1)) continue;  // only skip if losing > 10% of risk per trade

    // Warnings
    const warnings: string[] = [];
    const range20 = high20 - low20;
    const rangePct = range20 > 0 ? ((price - low20) / range20) * 100 : 50;
    if (direction === "long" && rangePct > 80) warnings.push(`Buying near 20d high (${rangePct.toFixed(0)}% of range)`);
    if (direction === "short" && rangePct < 20) warnings.push(`Shorting near 20d low (${rangePct.toFixed(0)}% of range)`);
    if (ev <= 0) warnings.push(`Negative EV — ${evPct.toFixed(2)}% per trade ($${ev.toFixed(2)}). Signal-based exit may perform better than ATR stop.`);
    else if (evPct < 0.5) warnings.push(`Thin edge — EV ${evPct.toFixed(2)}% per trade ($${ev.toFixed(2)})`);
    if (dissentFamilies >= 1) warnings.push(`${dissentFamilies}/${Object.keys(SCORING_FAMILIES).length} families dissenting`);
    if (trigger.trades < 10) warnings.push(`Low sample: only ${trigger.trades} historical trades`);
    const rSharpe = trigger.recent_sharpe;
    const hSharpe = trigger.sharpe;
    if (rSharpe != null && rSharpe < 0) {
      warnings.push(`Degrading — recent 1yr Sharpe ${rSharpe.toFixed(2)} (negative)`);
    } else if (rSharpe != null && hSharpe > 0 && rSharpe < hSharpe * 0.3) {
      warnings.push(`Weakening — recent Sharpe ${rSharpe.toFixed(2)} vs ${hSharpe.toFixed(2)} historical`);
    }

    const freshCount = confirming.filter(r => r.signal_days <= 5).length;

    ideas.push({
      ticker, direction,
      trigger: {
        strategy: trigger.strategy,
        signalDays: trigger.signal_days,
        dsr: trigger.dsr,
        excessSharpe: trigger.excess_sharpe, sharpe: trigger.sharpe,
        avgHoldDays: trigger.avg_hold_days, medianHoldDays: trigger.median_hold_days,
        entryUrgency: trigger.entry_urgency, delaySharpes: trigger.delay_sharpes,
        winRate: trigger.win_rate, trades: trigger.trades, recentSharpe: trigger.recent_sharpe,
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
    const ivRich = iv > rv * 1.05;
    const ivCheap = rv > iv * 1.05;

    if (ivRich) {
      return isLong
        ? `IV ${iv}% > RV ${rv}% — market pricing more vol than realized → Sell Bull Put Spread (collect overpriced premium)`
        : `IV ${iv}% > RV ${rv}% — market pricing more vol than realized → Sell Bear Call Spread (collect overpriced premium)`;
    } else if (ivCheap) {
      return isLong
        ? `IV ${iv}% < RV ${rv}% — options are cheap relative to actual moves → Buy Bull Call Spread (leveraged upside)`
        : `IV ${iv}% < RV ${rv}% — options are cheap relative to actual moves → Buy Bear Put Spread (leveraged downside)`;
    } else {
      // IV ≈ RV — no vol edge either way
      return `IV ${iv}% ≈ RV ${rv}% — no vol edge, stock or directional spread`;
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

export function TradeIdeasContent() {
  const [watchlist, setWatchlist] = useState(DEFAULT_TICKERS.join(", "));
  const [volData, setVolData] = useState<Record<string, VolAnalysis>>({});
  const rhQuery = useQuery({ queryKey: ["rh-positions"], queryFn: fetchRobinhoodPositions, staleTime: 5 * 60_000 });
  const accountEquity = rhQuery.data?.portfolio?.equity || 0;

  // Dynamic holdings from RH
  const rhTickers = [...new Set([
    ...(rhQuery.data?.stocks ?? []).map(s => s.ticker),
    ...(rhQuery.data?.spreads ?? []).map(s => s.ticker),
  ])];
  const activePresets = {
    ...PRESETS,
    holdings: { ...PRESETS.holdings, tickers: rhTickers.length > 0 ? rhTickers : PRESETS.holdings.tickers },
  };
  const allPresetTickers = [...new Set(Object.values(activePresets).flatMap(p => p.tickers))];

  const scan = useMutation({
    mutationFn: async () => {
      const tickers = watchlist.split(",").map(t => t.trim().toUpperCase()).filter(Boolean);
      setAnalysis(""); setNewsSummary(""); // clear stale data on re-scan
      const [scanRes, volRes] = await Promise.all([
        fetchStrategyScan(tickers, ALL_STRATEGIES, 2520),  // 10yr — max data, cached so no cost
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
    onError: (e) => { setAnalysis(`Error: ${e instanceof Error ? e.message : "Request failed"}`); },
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
            <div className="flex flex-wrap gap-1 mb-1">
              {Object.entries(activePresets).map(([key, preset]) => (
                <button key={key} onClick={() => setWatchlist(preset.tickers.join(", "))}
                  className={`px-2 py-0.5 rounded text-[0.55rem] font-semibold border transition-colors ${
                    watchlist === preset.tickers.join(", ") ? "border-accent text-accent bg-accent/10" : "border-border text-text-muted hover:text-text"
                  }`}>
                  {preset.label} ({preset.tickers.length})
                </button>
              ))}
              <button onClick={() => setWatchlist(allPresetTickers.join(", "))}
                className={`px-2 py-0.5 rounded text-[0.55rem] font-bold border transition-colors ${
                  watchlist === allPresetTickers.join(", ") ? "border-accent text-accent bg-accent/10" : "border-warn text-warn hover:bg-warn/10"
                }`}>
                ALL ({allPresetTickers.length})
              </button>
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
            <span className="text-xs text-text-muted">
              Running {ALL_STRATEGIES.length} strategies on {watchlist.split(",").filter(Boolean).length} tickers
              ({(ALL_STRATEGIES.length * watchlist.split(",").filter(Boolean).length).toLocaleString()} combinations)
              {watchlist.split(",").filter(Boolean).length > 50 && " — large scan, may take 2-3 min"}
            </span>
          </div>
        )}
      </div>

      {scan.isError && <div className="card border-loss/30 bg-loss-bg text-loss text-sm">{(scan.error as Error).message}</div>}

      {scan.data && ideas.length === 0 && (
        <div className="card text-center text-sm text-text-muted py-6">
          No trade ideas passed all filters. Scanned {scan.data.n_tested} combinations, found {scan.data.n_active_signals} active signals.
          <br />Filters: fresh ({"\u2264"}10d) + DSR {"\u2265"} 0.5 + 2+ family confirmation + R:R {"\u2265"} 1.0 + EV not deeply negative.
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
            <div className="space-y-4">
              {analysis.split(/^##\s+/m).filter(Boolean).map((block, i) => {
                const lines = block.trim().split("\n");
                const header = lines[0].trim();
                const body = lines.slice(1).join("\n").trim();
                // Parse ticker and direction from header
                const hMatch = header.match(/^(\w+)\s+(LONG|SHORT|long|short)/i);
                const ticker = hMatch?.[1] || header;
                const dir = hMatch?.[2]?.toUpperCase() || "";
                const isLong = dir === "LONG";

                // Parse WHY/RISK/ACTION sections using matchAll (avoids split capture group misalignment)
                const sections: { label: string; text: string }[] = [];
                const sectionRe = /\*{0,2}(WHY|RISK|ACTION)\*{0,2}[:\s—–-]*/gi;
                const matches = [...body.matchAll(sectionRe)];
                for (let j = 0; j < matches.length; j++) {
                  const start = (matches[j].index ?? 0) + matches[j][0].length;
                  const end = j + 1 < matches.length ? matches[j + 1].index : body.length;
                  const text = body.slice(start, end).trim()
                    .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
                    .replace(/\*{2,}/g, "");
                  if (text) sections.push({ label: matches[j][1].toUpperCase(), text });
                }
                // Fallback if no sections parsed
                if (sections.length === 0 && body) {
                  sections.push({ label: "", text: body.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>').replace(/\*{2,}/g, "") });
                }

                return (
                  <div key={i} className="rounded-lg border border-border p-3">
                    <div className="flex items-center gap-2 mb-2">
                      <span className="font-bold text-sm">{ticker}</span>
                      {dir && <span className={`badge text-[0.5rem] font-bold ${isLong ? "badge-gain" : "badge-loss"}`}>{dir}</span>}
                    </div>
                    <div className="space-y-1.5">
                      {sections.map((s, si) => (
                        <div key={si} className="text-[0.65rem] leading-relaxed">
                          {s.label && (
                            <span className={`font-bold mr-1.5 ${
                              s.label === "WHY" ? "text-accent" :
                              s.label === "RISK" ? "text-loss" :
                              s.label === "ACTION" ? "text-gain" : "text-text-muted"
                            }`}>{s.label}</span>
                          )}
                          <span className="text-text" dangerouslySetInnerHTML={{ __html: s.text }} />
                        </div>
                      ))}
                    </div>
                  </div>
                );
              })}
            </div>
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
        {ideas.map((idea) => <IdeaCard key={idea.ticker} idea={idea} acctEquity={accountEquity} bookSummary={
          rhQuery.data?.portfolio
            ? `$${rhQuery.data.portfolio.equity.toLocaleString()} equity, ` +
              (rhQuery.data.stocks ?? []).map(s => `${s.ticker} ${Math.round(s.qty)}sh`).join(", ")
            : ""
        } />)}
      </div>
    </div>
  );
}

function IdeaCard({ idea, acctEquity, bookSummary }: { idea: TradeIdea; acctEquity: number; bookSummary: string }) {
  const [expanded, setExpanded] = useState(false);
  const [quickVerdict, setQuickVerdict] = useState<{ verdict: string; analysis: string } | null>(null);
  const [quickLoading, setQuickLoading] = useState(false);
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
            {idea.trigger.entryUrgency && idea.trigger.entryUrgency !== "neutral" && (
              <span className={`ml-1 px-1.5 py-0.5 rounded text-[0.5rem] font-bold border ${
                idea.trigger.entryUrgency === "urgent" ? "border-loss/40 bg-loss/10 text-loss"
                : idea.trigger.entryUrgency === "wait" ? "border-gain/40 bg-gain/10 text-gain"
                : "border-border text-text-muted"  // patient
              }`}>
                {idea.trigger.entryUrgency === "urgent" ? `ENTER NOW — edge decays fast`
                : idea.trigger.entryUrgency === "wait" ? `WAIT — improves with delay`
                : `PATIENT — edge persists`}
              </span>
            )}
          </div>
          {/* Warnings */}
          {idea.warnings.length > 0 && (
            <div className="flex flex-wrap gap-1.5 mt-1">
              {idea.warnings.map((w, i) => (
                <span key={i} className="text-[0.5rem] text-warn px-1.5 py-0.5 rounded border border-warn/30 bg-warn/5">{w}</span>
              ))}
            </div>
          )}
          {/* Quick AI Verdict */}
          {quickVerdict && (
            <div className={`mt-1.5 px-2.5 py-1.5 rounded text-[0.6rem] leading-relaxed border ${
              quickVerdict.verdict === "ENTER" ? "border-gain/30 bg-gain/5" :
              quickVerdict.verdict === "SKIP" ? "border-loss/30 bg-loss/5" :
              "border-warn/30 bg-warn/5"
            }`}>
              <span className={`font-bold mr-1.5 ${
                quickVerdict.verdict === "ENTER" ? "text-gain" :
                quickVerdict.verdict === "SKIP" ? "text-loss" : "text-warn"
              }`}>{quickVerdict.verdict}</span>
              <span className="text-text">{quickVerdict.analysis.replace(/^(ENTER|WAIT|SKIP)[:\s—–-]*/i, "")}</span>
            </div>
          )}
        </div>
        <div className="text-right font-data flex flex-col items-end gap-1">
          <div>
            <div className="text-lg font-bold">${idea.price.toFixed(2)}</div>
            <div className="text-[0.5rem] text-text-muted">RSI {idea.rsi}</div>
          </div>
          <button
            onClick={async () => {
              setQuickLoading(true);
              try {
                const res = await fetchTradeIdeaQuick(idea as unknown as Record<string, unknown>, bookSummary);
                if (res.success) setQuickVerdict({ verdict: res.verdict, analysis: res.analysis || "" });
                else setQuickVerdict({ verdict: "ERROR", analysis: res.error || "Analysis failed" });
              } catch { setQuickVerdict({ verdict: "ERROR", analysis: "Request failed" }); }
              setQuickLoading(false);
            }}
            disabled={quickLoading}
            className="text-[0.5rem] px-2 py-0.5 border border-border rounded hover:border-accent hover:text-accent disabled:opacity-50">
            {quickLoading ? "..." : quickVerdict ? quickVerdict.verdict : "AI Verdict"}
          </button>
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
        {acctEquity > 0 && (() => {
          const riskDollars = acctEquity * 0.01;
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

      {/* Row 4: Trade vehicle recommendation */}
      {(() => {
        const hold = idea.trigger.medianHoldDays || 10;
        const iv = idea.vol?.iv;
        const rv = idea.vol?.rv_20d;
        const ivRich = iv && rv && iv > rv * 1.05;
        const ivCheap = iv && rv && rv > iv * 1.05;
        const isLong = idea.direction === "long";
        const risk = Math.abs(idea.price - idea.stop) || 1; // guard against 0
        const sharesFor1Pct = Math.floor((acctEquity * 0.01) / risk);
        const shareCost = sharesFor1Pct * idea.price;
        const needsLeverage = shareCost > acctEquity * 0.3;

        // Optimal DTE: 3-4× hold period gives manageable theta decay
        const optimalDTE = Math.max(21, Math.round(hold * 3.5));
        const thetaDecayPct = Math.round(hold / optimalDTE * 100);

        // Vehicle logic — more specific conditions first
        let vehicle: { label: string; detail: string; color: string };
        if (ivCheap && hold <= 15 && needsLeverage) {
          // Cheap options + expensive stock = debit spread (capped cost + cheap vol)
          vehicle = {
            label: isLong ? "Bull Call Spread" : "Bear Put Spread",
            detail: `IV ${iv}% < RV ${rv}% (cheap) + stock needs $${shareCost.toLocaleString()} (${Math.round(acctEquity > 0 ? shareCost / acctEquity * 100 : 0)}% of account). ${optimalDTE}d DTE debit spread: cheap leverage + capped risk. ~${thetaDecayPct}% theta decay over ${hold}d.`,
            color: "border-purple-400/30 bg-purple-400/5 text-purple-400",
          };
        } else if (ivCheap && hold <= 15) {
          // Cheap options + affordable stock = buy options for leverage
          vehicle = {
            label: isLong ? "Buy Calls" : "Buy Puts",
            detail: `IV ${iv}% < RV ${rv}% — options underpriced. ${optimalDTE}d DTE ${isLong ? "calls" : "puts"} give cheap leverage. ~${thetaDecayPct}% theta decay over ${hold}d.`,
            color: "border-purple-400/30 bg-purple-400/5 text-purple-400",
          };
        } else if (ivRich) {
          // Options are expensive = sell premium
          vehicle = {
            label: isLong ? "Sell Bull Put" : "Sell Bear Call",
            detail: `IV ${iv}% > RV ${rv}% — collect overpriced premium. ${optimalDTE}d DTE credit spread. Theta works for you over ~${hold}d hold.`,
            color: "border-gain/30 bg-gain/5 text-gain",
          };
        } else if (needsLeverage) {
          vehicle = {
            label: isLong ? "Bull Call Spread" : "Bear Put Spread",
            detail: `Stock position needs $${shareCost.toLocaleString()} (${Math.round(acctEquity > 0 ? shareCost / acctEquity * 100 : 0)}% of account). ${optimalDTE}d DTE spread is more capital efficient.`,
            color: "border-accent/30 bg-accent/5 text-accent",
          };
        } else {
          vehicle = {
            label: "Stock",
            detail: `${sharesFor1Pct} shares = $${shareCost.toLocaleString()} (${Math.round(acctEquity > 0 ? shareCost / acctEquity * 100 : 0)}% of account). Simple, no theta decay.`,
            color: "border-border",
          };
        }

        const earnDays = idea.vol?.next_earnings_days;
        const holdsThroughEarnings = earnDays != null && earnDays <= 21 && hold > 0 && earnDays <= hold;

        return (
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            <div className={`px-2 py-1 rounded border text-[0.55rem] font-data ${vehicle.color}`}>
              <span className="font-semibold">{vehicle.label}:</span> {vehicle.detail}
            </div>
            <div className="px-2 py-1 rounded border border-border text-[0.55rem] font-data text-text-muted">
              Entry ${idea.price.toFixed(2)} · Stop ${idea.stop.toFixed(2)} ({idea.stopMult.toFixed(1)}×ATR) · Target ${idea.target.toFixed(2)} · ~{hold}d hold
            </div>
            {idea.vol?.avg_earnings_move && (
              <span className="px-2 py-1 rounded border border-warn/30 bg-warn/5 text-warn text-[0.55rem] font-data">
                Avg earnings move: ±{idea.vol.avg_earnings_move}%
              </span>
            )}
            {earnDays != null && earnDays <= 21 && (
              <span className={`px-2 py-1 rounded border text-[0.55rem] font-data font-bold ${
                holdsThroughEarnings ? "border-loss bg-loss/10 text-loss" : "border-loss/30 bg-loss/5 text-loss"
              }`}>
                {holdsThroughEarnings
                  ? `!! HOLDS THROUGH EARNINGS (${earnDays}d) — typical hold is ${hold}d`
                  : `Earnings in ${earnDays}d`}
              </span>
            )}
          </div>
        );
      })()}

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
        {idea.vol?.iv && idea.vol?.rv_20d && (() => {
          const diff = idea.vol!.iv! - idea.vol!.rv_20d!;
          const absDiff = Math.abs(diff);
          const pct = Math.max(idea.vol!.iv!, idea.vol!.rv_20d!) * 0.05;
          if (absDiff < pct) return <span className="text-text-muted">IV ≈ RV (no edge)</span>;
          return diff > 0
            ? <span className="text-gain">IV &gt; RV by {absDiff.toFixed(1)}pp (sell premium)</span>
            : <span className="text-loss">IV &lt; RV by {absDiff.toFixed(1)}pp (buy options)</span>;
        })()}
        {idea.vol?.next_earnings_days != null && (
          <span className={idea.vol.next_earnings_days <= 14 ? "text-warn font-semibold" : ""}>
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
        {idea.trigger.recentSharpe != null && (
          <span className={idea.trigger.recentSharpe >= 0.5 ? "text-gain" : idea.trigger.recentSharpe >= 0 ? "text-text" : "text-loss"}>
            1yr: {idea.trigger.recentSharpe.toFixed(2)} Sharpe
          </span>
        )}
        {idea.trigger.medianHoldDays != null && idea.trigger.medianHoldDays > 0 && (
          <span className="text-text-muted">
            hold: ~{idea.trigger.medianHoldDays}d median{idea.trigger.avgHoldDays && idea.trigger.avgHoldDays !== idea.trigger.medianHoldDays ? ` (${idea.trigger.avgHoldDays}d avg)` : ""} over {idea.trigger.trades} trades
          </span>
        )}
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

/**
 * Typed FastAPI client for AI Statcharts backend.
 * All data flows through these functions — no direct fetch calls in components.
 */
import { hasSupabaseConfig, supabaseBrowser } from "@/lib/supabase";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

/** Pull the current Supabase access token (if any) to attach as Bearer. */
async function getAuthHeader(): Promise<Record<string, string>> {
  // Server-side callers (RSC/route handlers) can't use the browser client.
  // apiFetch is used exclusively from "use client" components, so this path
  // only runs in the browser. Bail out on SSR to avoid hydration mismatches.
  if (typeof window === "undefined" || !hasSupabaseConfig()) return {};
  try {
    const supabase = supabaseBrowser();
    const { data } = await supabase.auth.getSession();
    const token = data.session?.access_token;
    return token ? { Authorization: `Bearer ${token}` } : {};
  } catch {
    return {};
  }
}

async function apiFetch<T>(
  path: string,
  options?: RequestInit & { timeoutMs?: number }
): Promise<T> {
  const url = `${API_BASE}${path}`;
  const { timeoutMs = 30_000, ...fetchOptions } = options ?? {};

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const authHeader = await getAuthHeader();
    const res = await fetch(url, {
      ...fetchOptions,
      signal: controller.signal,
      headers: {
        "Content-Type": "application/json",
        ...authHeader,
        ...fetchOptions.headers,
      },
    });
    clearTimeout(timer);
    if (!res.ok) {
      throw new Error(`API error: ${res.status} ${res.statusText}`);
    }
    return res.json();
  } catch (err) {
    clearTimeout(timer);
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new Error(`Request timed out after ${Math.round(timeoutMs / 1000)}s`);
    }
    throw err;
  }
}

// ─── Market Data ─────────────────────────────────────────────

export interface Snapshot {
  [ticker: string]: {
    price: number;
    change: number;
    prev_close?: number;
  };
}

export async function fetchSnapshot(tickers: string[]): Promise<Snapshot> {
  return apiFetch(`/api/market/snapshot?tickers=${tickers.join(",")}`);
}

export interface PriceBar {
  Date: string;
  Open: number;
  High: number;
  Low: number;
  Close: number;
  Volume: number;
}

export interface OHLCVBar {
  time: number; // unix seconds
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface IndicatorPoint { time: number; value: number; }
export interface ChartIndicators {
  ema9?: IndicatorPoint[];
  ema21?: IndicatorPoint[];
  ema50?: IndicatorPoint[];
  ema200?: IndicatorPoint[];
  rsi?: IndicatorPoint[];
  macd?: IndicatorPoint[];
  macd_signal?: IndicatorPoint[];
  macd_hist?: IndicatorPoint[];
  bb_upper?: IndicatorPoint[];
  bb_middle?: IndicatorPoint[];
  bb_lower?: IndicatorPoint[];
  vwap?: IndicatorPoint[];
}

export async function fetchOHLCV(
  ticker: string,
  days = 365,
  interval = "1d",
): Promise<{ ticker: string; data: OHLCVBar[]; indicators?: ChartIndicators }> {
  return apiFetch(`/api/market/ohlcv/${ticker}?days=${days}&interval=${interval}`);
}

export async function fetchPriceHistory(
  ticker: string,
  days = 252
): Promise<{ ticker: string; data: PriceBar[] }> {
  return apiFetch(`/api/market/history/${ticker}?days=${days}`);
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export async function fetchStockData(ticker: string): Promise<Record<string, any>> {
  return apiFetch(`/api/market/stock-data/${ticker}`, { timeoutMs: 30_000 });
}

// ── Full Stock Data (with technicals, fundamentals, EDGAR) ──

export interface StockDataFull {
  ticker: string;
  price: number;
  prev_close: number;
  change: number;
  change_pct: number;
  info: Record<string, string | number | boolean | null>;
  history: Record<string, number | string | null>[];
  technicals: {
    ema20?: number; ema50?: number | null; ema200?: number | null;
    rsi?: number | null;
    macd?: number; macd_signal?: number; macd_hist?: number; macd_bullish?: boolean;
    bb_upper?: number | null; bb_lower?: number | null; bb_pctb?: number | null;
    atr?: number | null; atr_pct?: number | null;
    volume_ratio?: number | null;
    trend_score?: number;
  };
  fundamentals: Record<string, number | string | null>;
  xbrl_history: Record<string, { period: string; value: number }[]>;
  recommendations: Record<string, string | number | null>[];
  analyst_summary: {
    buys?: number; holds?: number; sells?: number; total?: number;
    consensus?: string;
    target_mean?: number; target_low?: number; target_high?: number;
    upside_pct?: number;
  };
  insiders: Record<string, string | number | null>[];
  insider_score: { score: number; signal: string; breakdown: Record<string, number | boolean> };
  events_8k: { filed: string; form: string; company: string; items: string; url: string }[];
  stocktwits: { symbol: string; messages: number; bullish: number; bearish: number; bull_ratio: number; signal: string } | null;
}

export async function fetchStockDataFull(ticker: string, days = 365): Promise<StockDataFull> {
  return apiFetch(`/api/market/stock-data-full/${ticker}?days=${days}`, { timeoutMs: 60_000 });
}

// ── 3-Model Stock AI Analysis ──

export interface StockModelResult {
  success: boolean;
  error?: string;
  model_name: string;
  color: string;
  scores?: { technical: number; fundamental: number; sentiment: number; macro: number; valuation: number };
  composite_score?: number;
  recommendation?: string;
  price_targets?: { bull: number; base: number; bear: number; bull_prob: number; base_prob: number; bear_prob: number };
  analysis?: Record<string, string>;
  risks?: string[];
  catalysts?: string[];
  confidence?: number;
  summary?: string;
  sentiment_pulse?: string;
}

export interface StockAIResult {
  success: boolean;
  error?: string;
  scores?: { technical: number; fundamental: number; sentiment: number; macro: number; valuation: number };
  composite_score?: number;
  recommendation?: string;
  price_targets?: { bull: number; base: number; bear: number; bull_prob: number; base_prob: number; bear_prob: number };
  analysis?: Record<string, string>;
  risks?: string[];
  catalysts?: string[];
  confidence?: number;
  summary?: string;
  sentiment_pulse?: string;
  agreement?: string;
  model_results?: Record<string, StockModelResult>;
  blend_note?: string;
}

export async function fetchStockAIAnalysis(ticker: string, stockPrompt: string): Promise<StockAIResult> {
  return apiFetch("/api/market/stock-ai-analysis", {
    method: "POST",
    body: JSON.stringify({ ticker, stock_prompt: stockPrompt }),
    timeoutMs: 120_000,
  });
}

// ── Backtest Statistics (de Prado) ──

export interface WalkForwardResult {
  train_pct: number; test_pct: number; n_folds: number;
  avg_sharpe: number; min_sharpe: number; max_sharpe: number; pct_positive: number;
}

export interface BacktestStatsResult {
  success: boolean; error?: string;
  sharpe: number; dsr: number; dsr_verdict: string;
  pbo: number | null; pbo_verdict: string | null;
  bootstrap_p: number | null; bootstrap_verdict: string | null;
  walk_forward: WalkForwardResult[];
  regimes: Record<string, { n_days: number; sharpe: number; avg_return: number; volatility: number }>;
  n_returns: number; skew: number; kurtosis: number;
}

export async function fetchBacktestStats(
  returns: number[], trades: Record<string, unknown>[] = [], nStrategies = 1
): Promise<BacktestStatsResult> {
  return apiFetch("/api/market/backtest-stats", {
    method: "POST",
    body: JSON.stringify({ returns, trades, n_strategies_tested: nStrategies, walk_forward: true, n_bootstrap: 1000 }),
    timeoutMs: 60_000,
  });
}

// ── Daily Briefing ──

export interface DailyBriefingResult {
  market_context: {
    spy: { price: number; change_pct: number };
    vix: { price: number; regime: string; vix3m?: number; term_ratio?: number; term_structure?: string };
    qqq: { price: number; change_pct: number };
    fomc_events: { date: string; days_away: number; type: string }[];
    timestamp: string;
  };
  watchlist: { ticker: string; price: number; change_pct: number; earnings: { date: string; days: number } | null }[];
  earnings_this_week: { ticker: string; date: string; days: number }[];
  opportunities: {
    type: string; label: string; ticker: string; sector: string; score: number; pop: number;
    premium: number; max_risk: number; max_profit: number; rr_ratio: number;
    contracts: number; strikes: string; expiration: string; dte: number;
    ivr: number | null; ivr_band: string; liq_grade: string;
    earnings_before: boolean; inside_exp_move: boolean;
    managed_wr: number; kelly_adj: number;
    // Vertical spreads only
    long_strike?: number; short_strike?: number;
    // Iron condors only
    short_put?: number; long_put?: number; short_call?: number; long_call?: number;
    // Underlying reference (both types)
    stock_price?: number;
  }[];
  risk_budget: { account_size: number; top5_risk: number; pct_of_account: number; remaining: number; verdict: string };
  warnings: string[];
  sector_exposure: Record<string, number>;
  scan_stats: { spreads_found: number; condors_found: number; top_shown: number };
  outlook: {
    spy_price: number;
    vix: number;
    implied_move_pct: number;
    implied_move_dollar: number;
    implied_low: number;
    implied_high: number;
    earnings: { ticker: string; date: string; days: number }[];
    fomc_events: { date: string; days_away: number; type: string }[];
    exposure_notes: { type: string; note: string }[];
  };
}

export async function fetchDailyBriefing(watchlist: string[], accountSize = 25000): Promise<DailyBriefingResult> {
  return apiFetch("/api/market/daily-briefing", {
    method: "POST",
    body: JSON.stringify({ watchlist, account_size: accountSize, scan_spreads: true, scan_condors: true }),
    timeoutMs: 3 * 60 * 1000,
  });
}

export interface NewsItem {
  ticker: string; headline: string; source: string; source_type: string;
  impact: string; confidence: string; time: string; url: string;
  category?: string; verification_note?: string;
}

export interface NewsIntelResponse {
  success: boolean; error?: string; items: NewsItem[];
  sources: Record<string, number>;
  total: number;
}

export async function fetchNewsIntel(watchlist: string[]): Promise<NewsIntelResponse> {
  return apiFetch("/api/market/news-intel", { method: "POST", body: JSON.stringify({ watchlist }), timeoutMs: 5 * 60_000 });
}

export async function fetchNewsAnalysis(headline: string, ticker: string, source: string, impact: string): Promise<{ analysis: string; cached: boolean }> {
  return apiFetch("/api/market/news-analyze", {
    method: "POST",
    body: JSON.stringify({ headline, ticker, source, impact }),
    timeoutMs: 15_000,
  });
}

export async function fetchNewsSearch(watchlist: string[]): Promise<NewsIntelResponse> {
  return apiFetch("/api/market/news-intel-search", { method: "POST", body: JSON.stringify({ watchlist }), timeoutMs: 2 * 60_000 });
}

export async function fetchNewsVerify(items: NewsItem[]): Promise<NewsIntelResponse> {
  return apiFetch("/api/market/news-intel-verify", { method: "POST", body: JSON.stringify({ items }), timeoutMs: 3 * 60_000 });
}

export interface PolymarketOutcome { label: string; yes_pct: number; token_id?: string; days_out?: number; actionability?: number; }
export interface PolymarketEvent { title: string; slug: string; category?: string; volume_24h: number; liquidity: number; outcomes: PolymarketOutcome[]; url: string; }
export interface PolymarketResponse { success: boolean; markets: PolymarketEvent[]; }
export interface PolymarketHistoryPoint { t: number; p: number; }

export async function fetchPolymarket(): Promise<PolymarketResponse> {
  return apiFetch("/api/market/polymarket", { timeoutMs: 20_000 });
}

export async function fetchPolymarketHistory(tokenId: string, interval = "1m"): Promise<{ success: boolean; points: PolymarketHistoryPoint[] }> {
  return apiFetch(`/api/market/polymarket-history?token_id=${encodeURIComponent(tokenId)}&interval=${interval}`, { timeoutMs: 10_000 });
}

export async function fetchMorningNote(briefingData: DailyBriefingResult, newsItems: NewsItem[] = [], polymarket: PolymarketEvent[] = [], bookSummary = "", signalSummary = ""): Promise<{ content: string; success: boolean }> {
  return apiFetch("/api/market/morning-note", {
    method: "POST",
    body: JSON.stringify({ briefing_data: briefingData, news_items: newsItems, polymarket, book_summary: bookSummary, signal_summary: signalSummary }),
    timeoutMs: 90_000,
  });
}

// ── Robinhood Positions ──

export interface ArchitectMessage { role: "user" | "assistant"; content: string; }

export interface StructuredTradeLeg {
  action: string; instrument: string; ticker: string;
  qty: number; price: number; strike?: number; exp?: string;
}
export interface StructuredTrade {
  type: "stock" | "options" | "combination";
  label: string; legs: StructuredTradeLeg[];
  entry: number; stop: number | null; target: number | null;
  max_profit: number; max_risk: number;
  breakeven: number; breakeven_upper?: number; pop: number | null; rr_ratio: number;
  greeks: { delta: number; theta: number; gamma: number; vega: number };
  timeframe: string; contracts?: number; width?: number;
  short_strike?: number; long_strike?: number;
  portfolio_equity?: number; risk_pct_of_account?: number;
  portfolio_delta_before?: number; portfolio_delta_after?: number;
  portfolio_theta_before?: number; portfolio_theta_after?: number;
  account_fit?: number; vol_suggestion?: string; signal_consensus?: string;
  direction?: string;
  hist_winrate?: number; hist_trials?: number;
}
export interface TradeArchitectResponse {
  success: boolean;
  analysis?: string;
  trades?: StructuredTrade[];
  tickers?: string[];
  context?: string;
  context_sources?: string[];
  model?: string;
  error?: string;
}

export async function fetchTradeArchitect(
  thesis: string,
  messages: ArchitectMessage[] = [],
  context = "",
  tickers: string[] = [],
  accountSize = 25000,
  deep = false,
  risk: "conservative" | "moderate" | "aggressive" = "moderate",
  strategy: "auto" | "sell" | "buy" = "auto",
  direction: "" | "bullish" | "bearish" | "neutral" = "",
): Promise<TradeArchitectResponse> {
  return apiFetch("/api/market/trade-architect", {
    method: "POST",
    body: JSON.stringify({ thesis, messages, context, tickers, account_size: accountSize, deep, risk, strategy, direction }),
    timeoutMs: deep ? 120_000 : 75_000,
  });
}

export interface HoldingDiveResponse {
  success: boolean; ticker: string; verdict: string;
  analysis?: string; sources?: string[]; error?: string;
}

export async function fetchHoldingDeepDive(stock: RHStock): Promise<HoldingDiveResponse> {
  return apiFetch("/api/market/holding-deep-dive", {
    method: "POST",
    body: JSON.stringify({
      ticker: stock.ticker, qty: stock.qty, avg_cost: stock.avg_cost,
      current_price: stock.current_price, market_value: stock.market_value,
      pl: stock.pl, pl_pct: stock.pl_pct,
      entry_date: stock.entry_date || "",
    }),
    timeoutMs: 30_000,
  });
}

export interface RHStock {
  ticker: string; qty: number; avg_cost: number; current_price: number;
  market_value: number; cost_basis: number; pl: number; pl_pct: number;
  entry_date?: string; theme?: string;
}

export interface RHConcentration {
  theme: string; value: number; pct: number; tickers: string[]; warning: string;
}

export interface RHLeg {
  chain: string; strike: number; exp: string; opt_type: string;
  direction: string; qty: number; avg_price: number; current_price: number;
  pl: number; iv: number; delta: number; gamma: number; theta: number; vega: number;
}

export interface RHGreeks { delta: number; gamma: number; theta: number; vega: number; }

export interface RHSpread {
  ticker: string; type: string; strikes: string; expiration: string;
  qty: number; legs: RHLeg[]; net_premium: number; current_value: number;
  pl: number; stock_price: number; short_strikes: number[]; long_strikes: number[];
  greeks: RHGreeks;
}

export interface RHPortfolioGreeks {
  delta: number; option_delta: number; stock_delta: number;
  gamma: number; theta: number; vega: number;
}

export interface RHPortfolio {
  equity: number; market_value: number; cash: number;
  stock_pl: number; option_pl: number; total_pl: number;
}

export interface RHPositionsResponse {
  success: boolean; error?: string;
  portfolio: RHPortfolio; stocks: RHStock[]; spreads: RHSpread[];
  greeks: RHPortfolioGreeks; concentration?: RHConcentration[];
}

export async function fetchRobinhoodPositions(): Promise<RHPositionsResponse> {
  return apiFetch("/api/positions/robinhood", { timeoutMs: 30_000 });
}

export interface HoldingDevelopment { headline: string; date: string; impact: string; detail: string; }
export interface HoldingResearch {
  ticker: string; company: string; thesis_status: string;
  developments: HoldingDevelopment[]; outlook: string; risk: string;
  // Fundamentals from yfinance
  market_cap?: string; revenue_ttm?: string; revenue_growth?: string;
  eps?: string; gross_margin?: string; operating_margin?: string;
  pe_ratio?: number; ps_ratio?: number;
  cash?: string; debt?: string; fcf?: string;
  quarterly_burn?: string; cash_runway?: string;
  analyst_target?: number; analyst_low?: number; analyst_high?: number;
  analyst_count?: number; recommendation?: string;
  next_earnings?: string; next_earnings_days?: number;
}
export interface HoldingsResearchResponse { success: boolean; error?: string; research: HoldingResearch[]; }

export async function fetchHoldingsResearch(tickers: string[]): Promise<HoldingsResearchResponse> {
  return apiFetch("/api/positions/holdings-research", { method: "POST", body: JSON.stringify({ tickers }), timeoutMs: 2 * 60_000 });
}

// ── Trade Idea Analysis ──

export async function fetchTradeIdeaAnalysis(ideas: unknown[], bookSummary = "", newsSummary = ""): Promise<{ success: boolean; error?: string; analysis: string }> {
  return apiFetch("/api/market/trade-idea-analysis", {
    method: "POST", body: JSON.stringify({ ideas, book_summary: bookSummary, news_summary: newsSummary }), timeoutMs: 120_000,
  });
}

export interface TradeIdeaQuickResponse {
  success: boolean; ticker: string; verdict: string; analysis?: string; error?: string;
}

export async function fetchTradeIdeaQuick(idea: Record<string, unknown>, bookSummary = ""): Promise<TradeIdeaQuickResponse> {
  return apiFetch("/api/market/trade-idea-quick", {
    method: "POST",
    body: JSON.stringify({
      ticker: idea.ticker, direction: idea.direction,
      trigger: (idea.trigger as Record<string, unknown>)?.strategy || "",
      signal_days: (idea.trigger as Record<string, unknown>)?.signalDays || 0,
      confluence: idea.confluenceScore, total_families: idea.totalFamilies,
      price: idea.price, stop: idea.stop, target: idea.target,
      rr: idea.riskReward, ev: idea.expectedValue, win_rate: (idea.trigger as Record<string, unknown>)?.winRate || 0,
      iv: (idea.vol as Record<string, unknown>)?.iv || 0,
      rv: (idea.vol as Record<string, unknown>)?.rv_20d || 0,
      rsi: idea.rsi, warnings: idea.warnings || [],
      book_summary: bookSummary,
    }),
    timeoutMs: 15_000,
  });
}

// ── Vol Analysis ──

export interface VolAnalysis {
  ticker: string; current_price?: number; rv_20d?: number;
  iv?: number; ivr?: number; iv_percentile?: number;
  vol_cone?: Record<string, number>;
  avg_earnings_move?: number; max_earnings_move?: number; n_earnings?: number;
  next_earnings?: string; next_earnings_days?: number;
  suggestion?: string;
  short_pct?: number; short_ratio?: number;
}

export async function fetchVolAnalysis(tickers: string[]): Promise<{ success: boolean; results: Record<string, VolAnalysis> }> {
  return apiFetch("/api/market/vol-analysis", { method: "POST", body: JSON.stringify({ tickers }), timeoutMs: 60_000 });
}

// ── Strategy Scanner ──

export interface StrategyScanResult {
  ticker: string; strategy: string;
  sharpe: number; dsr: number; dsr_pct: number;
  cagr: number; max_dd: number; total_ret: number;
  win_rate: number; trades: number;
  bh_sharpe: number; bh_cagr: number; bh_total_ret: number;
  excess_sharpe: number; excess_cagr: number; excess_ret: number;
  pct_active: number;
  avg_wf_sharpe: number | null; pct_wf_positive: number | null;
  current_signal: string; signal_days: number;
  n_days: number; skew: number; kurtosis: number;
  recent_sharpe?: number;
  current_price?: number; atr_14?: number; high_20d?: number; low_20d?: number; rsi?: number;
  best_stop_atr?: number; avg_mae_atr?: number; avg_mfe_atr?: number; stop_2x_survival?: number;
  avg_hold_days?: number; median_hold_days?: number;
  entry_urgency?: string; delay_sharpes?: Record<string, number>;
}

export interface StrategyScanResponse {
  results: StrategyScanResult[];
  n_tested: number; n_significant: number; n_active_signals: number;
  active_signals: StrategyScanResult[];
}

export async function fetchStrategyScan(
  tickers: string[], strategies: string[], lookbackDays = 1260, commBps = 5, slipBps = 5, minDsr = 0, timeframe = "daily"
): Promise<StrategyScanResponse> {
  return apiFetch("/api/market/strategy-scan", {
    method: "POST",
    body: JSON.stringify({ tickers, strategies, lookback_days: lookbackDays, commission_bps: commBps, slippage_bps: slipBps, min_dsr: minDsr, timeframe }),
    timeoutMs: 8 * 60 * 1000,
  });
}

// ── Optuna Strategy Optimizer ──

export interface OptimizeResult {
  strategy: string;
  best_params: Record<string, number>;
  wf_sharpe: number;
  sharpe: number; dsr: number; dsr_pct: number;
  cagr: number; max_dd: number; total_ret: number;
  win_rate: number; trades: number;
  current_signal: string; signal_days: number;
  n_trials: number; n_tested_total: number;
  param_importance: Record<string, number>;
}

export interface OptimizeResponse {
  ticker: string; timeframe: string;
  total_trials: number; strategies_tested: number;
  results: OptimizeResult[];
  success: boolean; error?: string;
}

export async function fetchOptimizeStrategy(
  ticker: string, strategies: string[], lookbackDays = 1260, timeframe = "daily", nTrials = 100, commBps = 5, slipBps = 5
): Promise<OptimizeResponse> {
  return apiFetch("/api/market/optimize-strategy", {
    method: "POST",
    body: JSON.stringify({ ticker, strategies, lookback_days: lookbackDays, timeframe, n_trials: nTrials, commission_bps: commBps, slippage_bps: slipBps }),
    timeoutMs: 10 * 60 * 1000,
  });
}

// ── Combo Scan (strategy combinations) ──

export interface ComboChart {
  equity: number[]; bh_equity: number[]; drawdown: number[];
  signals: number[]; x_indices: number[];
}

export interface ComboResult {
  combo: string[]; size: number; logic: string;
  sharpe: number; bh_sharpe: number; excess_sharpe: number;
  cagr: number; total_ret: number; max_dd: number;
  pct_active: number; trades: number; current_signal: string;
  dsr: number; dsr_pct: number;
  chart?: ComboChart;
}

export interface ComboScanResponse {
  success: boolean; error?: string;
  ticker: string; timeframe: string;
  n_strategies: number; n_combos_tested: number;
  individual: Record<string, { sharpe: number; bh_sharpe: number; excess_sharpe: number; cagr: number; total_ret: number; max_dd: number; pct_active: number; trades: number; current_signal: string }>;
  combos: ComboResult[];
  best_combo: ComboResult | null;
  best_individual: string | null;
}

export async function fetchComboScan(
  ticker: string, strategies: string[], lookbackDays = 1260, timeframe = "daily", maxComboSize = 2, commBps = 5, slipBps = 5
): Promise<ComboScanResponse> {
  return apiFetch("/api/market/combo-scan", {
    method: "POST",
    body: JSON.stringify({ ticker, strategies, lookback_days: lookbackDays, timeframe, max_combo_size: maxComboSize, commission_bps: commBps, slippage_bps: slipBps }),
    timeoutMs: 5 * 60 * 1000,
  });
}

// ── Deep Scan (multi-timeframe meta-analysis) ──

export interface DeepScanResponse {
  success: boolean; error?: string;
  total_results: number; total_tested: number; n_significant: number; n_active: number;
  all_results: (StrategyScanResult & { timeframe: string })[];
  strategy_rankings: { strategy: string; avg_dsr: number; median_dsr: number; avg_sharpe: number; avg_win_rate: number; n_significant: number; n_tested: number; pct_significant: number; active_signals: number }[];
  ticker_rankings: { ticker: string; avg_dsr: number; avg_sharpe: number; n_significant: number; best_strategy: string; best_dsr: number }[];
  timeframe_rankings: { timeframe: string; avg_dsr: number; avg_sharpe: number; n_significant: number; n_tested: number }[];
  heatmap: { strategy: string; ticker: string; dsr: number; timeframe: string; signal: string }[];
  significant_active: (StrategyScanResult & { timeframe: string })[];
  correlation: { strategies: string[]; matrix: number[][] };
  portfolio_recommendation: { ticker: string; strategy: string; timeframe: string; signal: string; signal_days: number; dsr: number; sharpe: number; win_rate: number; cagr: number }[];
}

export async function fetchDeepScan(
  tickers: string[], strategies: string[], timeframes: string[], commBps = 5, slipBps = 5
): Promise<DeepScanResponse> {
  return apiFetch("/api/market/deep-scan", {
    method: "POST",
    body: JSON.stringify({ tickers, strategies, timeframes, commission_bps: commBps, slippage_bps: slipBps }),
    timeoutMs: 15 * 60 * 1000,
  });
}

export interface FFRecord { date: string; "Mkt-RF": number; SMB: number; HML: number; RMW: number; CMA: number; RF: number }

export async function fetchFamaFrench(days = 252): Promise<{ factors: FFRecord[]; count: number }> {
  return apiFetch(`/api/market/fama-french?days=${days}`, { timeoutMs: 30_000 });
}

export async function fetchFredBatch(
  series: string[],
  periods = 60
): Promise<Record<string, Record<string, unknown>[]>> {
  return apiFetch(`/api/market/fred-batch?series=${series.join(",")}&periods=${periods}`, { timeoutMs: 60_000 });
}

export async function fetchPriceHistoryBatch(
  tickers: string[],
  days = 252
): Promise<Record<string, { Date: string; Close: number }[]>> {
  return apiFetch(`/api/market/history-batch?tickers=${tickers.join(",")}&days=${days}`, { timeoutMs: 60_000 });
}

export async function fetchOptionsChain(
  ticker: string,
  expiration?: string
): Promise<{
  ticker: string;
  count: number;
  data: Record<string, unknown>[];
  expirations: string[];
  spot?: number;
}> {
  const params = expiration ? `?expiration=${expiration}` : "";
  // 90s: SPY / QQQ chains paginate across dozens of Polygon snapshot pages
  // (250 contracts each × 20+ expirations). Default 25-30s wasn't enough on
  // cold cache. Single-expiration queries are much faster; keep same budget.
  return apiFetch(`/api/market/chain/${ticker}${params}`, { timeoutMs: 90_000 });
}

/**
 * Chain + spot in a single resilient call.
 *
 * The chain endpoint now returns `spot` itself, so this avoids the
 * Promise.all([chain, snapshot]) pattern that used to surface a slow/failed
 * snapshot fetch as a chain failure (the snapshot's 30s default timeout was
 * tanking "Load Chain" for users on cold Cloud Run). If the chain response
 * lacks a spot (legacy fallback path, or a ticker the snapshot endpoint
 * couldn't resolve), this performs a best-effort snapshot fetch as a
 * fallback — failures are swallowed so the chain still renders.
 */
export async function fetchOptionsChainWithSpot(ticker: string): Promise<{
  chain: Awaited<ReturnType<typeof fetchOptionsChain>>;
  spot: number;
}> {
  const chain = await fetchOptionsChain(ticker);
  let spot = chain.spot ?? 0;
  if (!spot) {
    try {
      const snap = await fetchSnapshot([ticker]);
      spot = snap[ticker]?.price ?? 0;
    } catch {
      // Best-effort fallback only — chain renders without spot.
    }
  }
  return { chain, spot };
}

export interface MarketNews {
  content: string | null;
  age_hours: number | null;
}

export interface HeatmapItem {
  symbol: string;
  label: string;
  price: number;
  change: number;
}

export async function fetchHeatmap(
  group = "sectors"
): Promise<{ group: string; items: HeatmapItem[] }> {
  return apiFetch(`/api/market/heatmap?group=${group}`);
}

/** How much wider than a normal session a release has ACTUALLY made the tape,
 *  measured over 3,677 sessions in `research/market_movers/`.
 *
 *  This exists because `impact` does not answer the question the calendar card
 *  was using it to answer. `impact` is assigned by judgement and is about
 *  TIMING — "a scheduled discontinuity lands at 08:30". This is measured and
 *  is about SIZING. They disagree hard: CPI is `high` on the first axis and
 *  1.06x, 12th of 23, on the second; quad witching is 0.94x — NARROWER than an
 *  ordinary day, once SPY's quarterly ex-dividend stops masquerading as a
 *  price move. */
export interface EventMeasuredImpact {
  /** Median |close-to-close| on the print over that session's own trailing
   *  60-session median. 1.00 = an ordinary day. */
  multiplier: number;
  ci95: [number, number];
  n: number;
  p: number;
  /** 23 events were tested, and one p=0.03 among 23 is what a null family
   *  looks like — so this, not the p-value, decides whether a number means
   *  anything. True for Nonfarm payrolls and nothing else. */
  survives_fdr: boolean;
  rank: number;
  of: number;
  /** Multiplier on the FOLLOWING session. Every one is near 1.0: nothing
   *  carries past the print. */
  next_session: number;
  share_over_1_5x: number;
  /** SD of the event's yearly rank, out of ~21 places. Large for everything
   *  except payrolls, which is why one multiplier is not a fixed property of
   *  the event. */
  rank_sd?: number | null;
  share_in_top_k?: number | null;
  /** False when the calendar event is a subset of what was measured — the SEP
   *  meetings were never split out of the pooled FOMC sample. */
  exact: boolean;
  study_event?: string;
  band: "established" | "unconfirmed" | "none";
  headline: string;
  caveat?: string;
}

export interface CalendarEvent {
  name: string;
  date: string;
  days_away: number;
  /** TIMING, assigned by judgement: is this a scheduled discontinuity to be at
   *  the screen for. Drives the ES card's scheduled-risk block. Do NOT read it
   *  as a range forecast — `measured` is that. */
  impact?: "high" | "medium" | "low" | string | null;
  /** SIZING, measured. `null` means the event was never in the study's
   *  universe (U-Mich preliminary, consumer confidence, the EIA report), which
   *  is a different statement from "measured, and ordinary". */
  measured?: EventMeasuredImpact | null;
  note?: string | null;
  time_et?: string | null;
  source?: string | null;
  derived?: boolean;
}

export async function fetchEvents(): Promise<{ events: CalendarEvent[] }> {
  return apiFetch("/api/market/events");
}

// ── Economic Calendar ────────────────────────────────────────────────
export interface EconEvent {
  date: string;          // YYYY-MM-DD
  event: string;
  impact: "High" | "Medium" | "Low" | string;
  category: string;
  series: string;
}
export async function fetchEconCalendarReleases(): Promise<{ events: EconEvent[] }> {
  return apiFetch("/api/market/econ-calendar-releases", { timeoutMs: 30_000 });
}

export interface EarningsEntry {
  date: string;         // YYYY-MM-DD
  symbol: string;
  epsEstimate: number | null;
  epsActual: number | null;
  revenueEstimate: number | null;
  revenueActual: number | null;
  hour: string;         // bmo | amc | dmh | ""
  quarter?: number;
  year?: number;
}
export async function fetchEarningsCalendar(from: string, to: string): Promise<{ earnings: EarningsEntry[] }> {
  return apiFetch(`/api/market/earnings-calendar?from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}`, { timeoutMs: 30_000 });
}

export interface TreasuryAuction {
  record_date: string;
  security_type: string;  // Bill | Note | Bond | TIPS | FRN | CMB
  security_term: string;
  reopening?: string;
  cusip?: string;
  offering_amt?: string;   // millions $ as string from Treasury
  announcemt_date?: string;
  auction_date: string;
  issue_date?: string;
}
export async function fetchTreasuryAuctions(): Promise<{ auctions: TreasuryAuction[] }> {
  return apiFetch("/api/market/treasury-auctions", { timeoutMs: 30_000 });
}

// ── Signal Scanner bundle ────────────────────────────────────────────
export interface SignalFundamentals {
  ticker: string;
  forward_pe: number | null;
  trailing_pe: number | null;
  price_to_book: number | null;
  ev_ebitda: number | null;
  dividend_yield: number | null;
  fcf_yield: number | null;
  roe: number | null;
  profit_margin: number | null;
  operating_margin: number | null;
  gross_margin: number | null;
  revenue_growth: number | null;
  earnings_growth: number | null;
  beta: number | null;
  net_debt_ebitda: number | null;
  current_ratio: number | null;
  market_cap: number | null;
}
export interface SignalEpsRow {
  ticker: string;
  up_7d: number; up_30d: number;
  down_7d: number; down_30d: number;
  net_30d: number;
}
export interface SignalInsiderRow {
  ticker: string;
  buy_count: number; sell_count: number;
  buy_value: number; sell_value: number;
  net_value: number;
}
export interface SignalScanBundle {
  prices: Record<string, { Date: string; Close: number; Volume: number }[]>;
  fundamentals: SignalFundamentals[];
  eps_revisions: SignalEpsRow[];
  insider: SignalInsiderRow[];
}
export async function fetchSignalBundle(tickers: string[], lookback: "6mo" | "1y" | "2y" = "1y"): Promise<SignalScanBundle> {
  return apiFetch("/api/scan/signal-bundle", {
    method: "POST",
    body: JSON.stringify({ tickers, lookback }),
    timeoutMs: 3 * 60 * 1000,
  });
}

export interface RiskSnapshot {
  iran: { score: number; level: string; oil_range: string | null } | null;
  macro: {
    top_regime: string;
    top_prob: number;
    regimes: { name: string; probability: number }[];
  } | null;
  vol: { atm_iv: number; level: string; vrp: number | null } | null;
  strategy: { rec: string; reason: string } | null;
}

export async function fetchRisk(): Promise<RiskSnapshot> {
  return apiFetch("/api/market/risk");
}

export async function fetchMarketNews(): Promise<MarketNews> {
  return apiFetch("/api/market/news");
}

// ─── Market Driver (home-page regime synthesis) ───────────────
export interface MarketDriverQuote { label: string; price: number; change_pct_1d: number; }
export interface MarketDriverCitation { label: string; source: string; detail?: string; }
export interface MarketDriverResponse {
  regime_label: string;
  paragraphs: { what_happened: string; whats_driving: string; what_to_watch: string };
  citations: MarketDriverCitation[];
  confidence: number;
  model?: string;
  escalated?: boolean;
  as_of_utc: string;
  quotes: Record<string, MarketDriverQuote>;
  /** Measured cross-asset attribution. Carried for the interpretation panel,
   *  not rendered on the card. */
  drivers?: CrossAssetDrivers | null;
  cache_hit?: boolean;
  error?: string;
}

export interface CrossAssetDrivers {
  available: boolean;
  window_sessions: number;
  as_of: string;
  explained_share: number;
  explained_share_a_year_ago: number | null;
  credit_increment: number | null;
  ranking: Array<{
    driver: string;
    ticker: string;
    rank: number;
    share_of_variance: number;
    corr_with_spy: number;
    rank_a_year_ago: number | null;
  }>;
  note: string;
}

export async function fetchMarketDriver(): Promise<MarketDriverResponse> {
  return apiFetch("/api/market/market-driver", { timeoutMs: 45_000 });
}

// ─── WallStreetBets mentions ──────────────────────────────────
export interface WsbTopPost {
  title: string;
  url: string;
  ups: number;
  subreddit: string;
  flair: string;
}
export interface WsbTicker {
  ticker: string;
  mentions: number;
  upvote_weighted: number;
  bull_score: number;
  bear_score: number;
  sentiment: number;         // -1..1
  calls_mentions: number;
  puts_mentions: number;
  options_lean: "calls" | "puts" | "mixed" | "neutral";
  dd_posts: number;
  top_post: WsbTopPost | null;
}
export interface WsbResponse {
  as_of_utc: string;
  subreddits_scanned: string[];
  post_count: number;
  tickers: WsbTicker[];
  cache_hit?: boolean;
  error?: string;
}

export async function fetchWsb(forceRefresh = false): Promise<WsbResponse> {
  const qs = forceRefresh ? "?force_refresh=true" : "";
  return apiFetch(`/api/wsb/mentions${qs}`, { timeoutMs: 45_000 });
}

// ─── Signals ─────────────────────────────────────────────────

export interface SignalSummary {
  n_tickers: number;
  n_bullish: number;
  n_bearish: number;
  n_neutral: number;
  avg_conviction: number;
}

export async function fetchSignalSummary(): Promise<SignalSummary> {
  return apiFetch("/api/signals/summary");
}

export interface TradeIdea {
  ticker: string;
  overall_direction: string;
  overall_conviction: number;
  n_signals: number;
  signal_agreement: number;
}

export async function fetchTopIdeas(n = 5): Promise<TradeIdea[]> {
  return apiFetch(`/api/signals/top?n=${n}`);
}

// ─── Positions ───────────────────────────────────────────────

export interface Position {
  id: string;
  ticker: string;
  type: string;
  qty: number;
  entry_price: number;
  entry_date: string;
  status: string;
  details: Record<string, unknown>;
}

export async function fetchPositions(
  status = "open"
): Promise<Position[]> {
  return apiFetch(`/api/positions/?status=${status}`);
}

export interface PortfolioSummary {
  n_positions: number;
  total_pnl: number;
  positions: unknown[];
  alerts: unknown[];
}

export async function fetchPortfolioSummary(): Promise<PortfolioSummary> {
  return apiFetch("/api/positions/summary");
}

export async function addPosition(data: {
  ticker: string;
  type: string;
  qty: number;
  entry_price: number;
  details?: Record<string, unknown>;
  source_page?: string;
}): Promise<{ id: string }> {
  return apiFetch("/api/positions/add", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

// ─── Options ─────────────────────────────────────────────────

export interface Greeks {
  delta: number;
  gamma: number;
  theta: number;
  vega: number;
  rho: number;
}

export async function fetchGreeks(params: {
  spot: number;
  strike: number;
  time_years: number;
  vol: number;
  rate?: number;
  opt_type?: string;
}): Promise<Greeks> {
  return apiFetch("/api/options/greeks", {
    method: "POST",
    body: JSON.stringify(params),
  });
}

export interface TickerMetrics {
  ticker: string;
  latest: Record<string, number | null> | null;
  percentiles: Record<string, number | null>;
  history_count: number;
}

export async function fetchTickerMetrics(
  ticker: string,
  days = 252
): Promise<TickerMetrics> {
  return apiFetch(`/api/options/metrics/${ticker}?days=${days}`);
}

// ─── Energy ──────────────────────────────────────────────────

export interface EIARecord {
  period: string;
  value: number;
  wow_change: number | null;
}

export interface NatGasBundle {
  storage: EIARecord[];
  regions: Record<string, EIARecord[]>;
  henry_hub: EIARecord[];
  consumption: EIARecord[];
}

export async function fetchNatGasBundle(): Promise<NatGasBundle> {
  return apiFetch("/api/energy/natgas", { timeoutMs: 60_000 });
}

export interface OilBundle {
  inventories: EIARecord[];
  production: EIARecord[];
  cushing: EIARecord[];
  refinery: EIARecord[];
  imports: EIARecord[];
  exports: EIARecord[];
  wti: EIARecord[];
  gasoline: EIARecord[];
  distillate: EIARecord[];
  supplied: EIARecord[];
  spr: EIARecord[];
  padd1: EIARecord[];
  padd2: EIARecord[];
  padd3: EIARecord[];
  padd4: EIARecord[];
  padd5: EIARecord[];
  // Track B — global / OECD (STEO monthly, carries an ~18-mo forecast tail)
  oecd_stocks: EIARecord[];        // OECD commercial crude+liquids inventory (Mb, eop)
  world_production: EIARecord[];   // World total liquids production (mb/d)
  world_consumption: EIARecord[];  // World total liquids consumption (mb/d)
  world_crude: EIARecord[];        // World crude oil production (mb/d)
  world_stock_change: EIARecord[]; // Net world inventory withdrawals (mb/d)
}

export async function fetchOilBundle(): Promise<OilBundle> {
  const raw = await apiFetch<Partial<OilBundle>>("/api/energy/oil", { timeoutMs: 60_000 });
  return normalizeOilBundle(raw);
}

/** Fill missing fields with []. Lets us evolve the bundle (adding SPR / PADDs
 * etc.) without crashing on stale backend cache or older Cloud Run revisions
 * that haven't rolled yet. */
export function normalizeOilBundle(raw: Partial<OilBundle>): OilBundle {
  return {
    inventories: raw.inventories ?? [],
    production:  raw.production  ?? [],
    cushing:     raw.cushing     ?? [],
    refinery:    raw.refinery    ?? [],
    imports:     raw.imports     ?? [],
    exports:     raw.exports     ?? [],
    wti:         raw.wti         ?? [],
    gasoline:    raw.gasoline    ?? [],
    distillate:  raw.distillate  ?? [],
    supplied:    raw.supplied    ?? [],
    spr:         raw.spr         ?? [],
    padd1:       raw.padd1       ?? [],
    padd2:       raw.padd2       ?? [],
    padd3:       raw.padd3       ?? [],
    padd4:       raw.padd4       ?? [],
    padd5:       raw.padd5       ?? [],
    oecd_stocks:        raw.oecd_stocks        ?? [],
    world_production:   raw.world_production   ?? [],
    world_consumption:  raw.world_consumption  ?? [],
    world_crude:        raw.world_crude        ?? [],
    world_stock_change: raw.world_stock_change ?? [],
  };
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export async function fetchErcotBundle(): Promise<Record<string, any>> {
  return apiFetch("/api/energy/ercot-bundle", { timeoutMs: 30_000 });
}

export interface ErcotCapacityMonth { date_path: string; month_label: string; }
export interface ErcotCapacityProject {
  inr: string;
  project_name: string;
  county: string;
  projected_cod: string | null;  // ISO date
  ia_signed: string | null;       // ISO date
  fuel_type: string;              // Wind | Solar | Battery | Gas
  fuel_detail: string;            // e.g. Gas-CC / Gas-CT/Other
  technology: string;
  capacity_mw: number;
  year: number | null;
  financial_security: string;     // Yes | No | ""
}
export interface ErcotCapacityResponse {
  month_label: string;
  date_path: string;
  planned_only: boolean;
  projects: ErcotCapacityProject[];
}
export async function fetchErcotCapacityMonths(): Promise<{ months: ErcotCapacityMonth[] }> {
  return apiFetch("/api/energy/ercot-capacity/months", { timeoutMs: 60_000 });
}
export async function fetchErcotCapacity(monthLabel: string, datePath: string, plannedOnly = false): Promise<ErcotCapacityResponse> {
  const q = new URLSearchParams({ month_label: monthLabel, date_path: datePath, planned_only: String(plannedOnly) });
  return apiFetch(`/api/energy/ercot-capacity?${q.toString()}`, { timeoutMs: 60_000 });
}

export async function fetchEIASeries(
  seriesId: string,
  rows = 260
): Promise<{ series_id: string; data: EIARecord[] }> {
  return apiFetch(`/api/energy/eia/${seriesId}?rows=${rows}`);
}

// ─── EDGAR / Smart Money ─────────────────────────────────────

export async function fetchTrackedFunds(): Promise<{ funds: { name: string; cik: string }[] }> {
  return apiFetch("/api/edgar/funds");
}

export interface GlobalFund {
  name: string;
  cik: string;
  category: "Sovereign Wealth" | "Public Pension" | "Endowment";
  country: string;
}
export async function fetchGlobalFunds(): Promise<{ funds: GlobalFund[] }> {
  return apiFetch("/api/edgar/global-funds");
}

export interface ShortInterest {
  ticker: string;
  ok: boolean;
  name?: string | null;
  price?: number | null;
  market_cap?: number | null;
  float_shares?: number | null;
  shares_short?: number | null;
  shares_short_prior?: number | null;
  short_ratio?: number | null;
  short_pct_float?: number | null;
  short_pct_outstanding?: number | null;
  avg_volume_10d?: number | null;
  last_updated?: number | string | null;
  error?: string;
}
export async function fetchShortInterest(ticker: string): Promise<ShortInterest> {
  return apiFetch(`/api/edgar/shorts/${ticker}`, { timeoutMs: 30_000 });
}

export interface ShortsWatchlistRow {
  ticker: string;
  name?: string | null;
  price?: number | null;
  market_cap?: number | null;
  short_pct_float?: number | null;
  short_ratio?: number | null;
  shares_short?: number | null;
  shares_short_prior?: number | null;
}
export async function fetchShortsWatchlist(): Promise<{ count: number; data: ShortsWatchlistRow[] }> {
  return apiFetch(`/api/edgar/shorts-watchlist`, { timeoutMs: 60_000 });
}

export interface BuybackPeriod {
  period: string;
  repurchase: number | null;
  dividend: number | null;
}
export interface BuybacksResponse {
  ticker: string;
  ok: boolean;
  name?: string | null;
  market_cap?: number | null;
  ttm_repurchase?: number | null;
  ttm_dividend?: number | null;
  buyback_yield?: number | null;
  dividend_yield?: number | null;
  total_shareholder_yield?: number | null;
  quarterly?: BuybackPeriod[];
  annual?: BuybackPeriod[];
  error?: string;
}
export async function fetchBuybacks(ticker: string): Promise<BuybacksResponse> {
  return apiFetch(`/api/edgar/buybacks/${ticker}`, { timeoutMs: 30_000 });
}

// ─── Smart Money Alerts ─────────────────────────

export type AlertType =
  | "fund" | "ticker" | "politician" | "activist" | "keyword"
  | "cftc_crowded_long" | "cftc_crowded_short" | "cftc_sign_flip" | "cftc_new_extreme";
export type AlertChannel = "email" | "sms" | "push";
export interface UserAlert {
  id: string;
  user_email: string;
  alert_type: AlertType;
  target: string;
  label: string | null;
  channels: AlertChannel[];
  active: boolean;
  created_at: string;
  last_fired_at: string | null;
}
export async function fetchAlerts(): Promise<{ count: number; data: UserAlert[]; setup_required?: boolean }> {
  return apiFetch("/api/alerts");
}
export async function createAlert(body: {
  alert_type: AlertType;
  target: string;
  label?: string;
  channels?: AlertChannel[];
}): Promise<{ ok: boolean; alert: UserAlert }> {
  return apiFetch("/api/alerts", { method: "POST", body: JSON.stringify(body) });
}
export async function deleteAlert(id: string): Promise<{ ok: boolean; deleted: number }> {
  return apiFetch(`/api/alerts/${id}`, { method: "DELETE" });
}
export interface AlertFiring {
  id: string;
  alert_id: string;
  user_id: string;
  alert_type: AlertType;
  target: string;
  fired_at: string;
  context: Record<string, unknown>;
  notified_at: string | null;
  notify_error: string | null;
}

export async function fetchAlertFirings(limit = 20): Promise<{ count: number; firings: AlertFiring[] }> {
  return apiFetch(`/api/alerts/firings?limit=${limit}`);
}

export async function patchAlert(id: string, body: { active?: boolean; label?: string; channels?: AlertChannel[] }): Promise<{ ok: boolean; changed: number }> {
  return apiFetch(`/api/alerts/${id}`, { method: "PATCH", body: JSON.stringify(body) });
}

// ─── AI Interpretation ─────────────────────────

export interface AIInterpretation {
  ok: boolean;
  model: string;
  interpretation: string;
  grounding?: {
    grounded_count: number;
    unverified_count: number;
    unverified_tokens: string[];
  };
  cache_creation_tokens?: number;
  cache_read_tokens?: number;
  input_tokens: number;
  output_tokens: number;
}
export async function fetchInterpretation(params: {
  page: string;
  data: unknown;
  subject?: string;
}): Promise<AIInterpretation> {
  return apiFetch("/api/ai/interpret", {
    method: "POST",
    body: JSON.stringify(params),
    timeoutMs: 90_000,
  });
}

export interface HomeChatTurn {
  role: "user" | "assistant";
  content: string;
}
export interface HomeChatAnswer {
  ok: boolean;
  model: string;
  answer: string;
  grounding: {
    grounded_count: number;
    unverified_count: number;
    unverified_tokens: string[];
  };
  snapshot_truncated: boolean;
  /** The model hit its token budget mid-answer. The text is real but unfinished. */
  answer_truncated?: boolean;
  cache_read_tokens: number;
  input_tokens: number;
  output_tokens: number;
}
/** Ask a question about the home page.
 *
 *  `data` must be the SAME snapshot for the life of one conversation. Sending a
 *  fresher one each turn would let turn 3 answer off different numbers than
 *  turn 1 — the conversation would quietly contradict itself — and it would
 *  invalidate the cached prompt prefix on every turn, so it would cost more as
 *  well. The server caches that prefix; a stable snapshot is what makes the
 *  second and later questions cheap. */
export async function askHomeChat(params: {
  data: unknown;
  question: string;
  history: HomeChatTurn[];
}): Promise<HomeChatAnswer> {
  return apiFetch("/api/ai/chat", {
    method: "POST",
    body: JSON.stringify(params),
    // MEASURED: a demanding synthesis question at effort "high" took 112.4s and
    // returned 7,797 output tokens. Against the previous 120s that is eight
    // seconds of headroom — and a timeout there is the worst outcome available,
    // because the server completes the work and bills it while the reader is
    // shown a failure. Raised well clear of the measured case; the SDK's own
    // 10-minute ceiling still bounds it.
    timeoutMs: 300_000,
  });
}

export interface Holding13F {
  company: string | null;
  class: string | null;
  cusip: string | null;
  value: number | null;
  shares: number | null;
  put_call: string | null;
  filing_date: string | null;
}
export interface Holdings13FResponse {
  cik: string;
  count: number;
  filing_date: string | null;
  holdings: Holding13F[];
}
export async function fetch13FHoldings(cik: string): Promise<Holdings13FResponse> {
  return apiFetch(`/api/edgar/13f/${cik}`, { timeoutMs: 60_000 });
}

export async function fetchInsiderTransactions(ticker: string): Promise<{ ticker: string; data: Record<string, unknown>[] }> {
  return apiFetch(`/api/edgar/insider/${ticker}`);
}

export interface EightKEvent {
  filed: string;
  form: string;
  company: string;
  items: string;
  url: string;
}
export async function fetch8KEvents(ticker: string, days = 30): Promise<{ ticker: string; count: number; data: EightKEvent[] }> {
  return apiFetch(`/api/edgar/8k/${ticker}?days=${days}`, { timeoutMs: 60_000 });
}

export interface Activist13D {
  filed: string;
  form: string;
  is_new: boolean;
  target: string;
  ticker: string;
  activist: string;
  url: string;
}
export async function fetchRecent13D(days = 90): Promise<{ days: number; count: number; data: Activist13D[] }> {
  return apiFetch(`/api/edgar/13d?days=${days}`, { timeoutMs: 60_000 });
}

export interface CongressionalTrade {
  member: string;
  state: string;
  ticker: string;
  type: string;            // "Purchase" | "Sale" | "Exchange"
  date: string | null;
  amount: string;
  filed: string | null;
}
export async function fetchCongressionalTrades(params?: { year?: number; maxFilings?: number }): Promise<{ year: number | null; count: number; data: CongressionalTrade[] }> {
  const qs = new URLSearchParams();
  if (params?.year) qs.set("year", String(params.year));
  if (params?.maxFilings) qs.set("max_filings", String(params.maxFilings));
  const suffix = qs.toString() ? `?${qs.toString()}` : "";
  return apiFetch(`/api/edgar/congressional-trades${suffix}`, { timeoutMs: 3 * 60_000 });
}

export interface GuidanceRow {
  filed: string | null;
  quarter: string | null;
  revenue: number | null;
  revenue_high: number | null;
  revenue_growth_low?: number | null;
  revenue_growth_high?: number | null;
  gross_margin: number | null;
  eps: number | null;
  eps_high: number | null;
  opex: number | null;
  operating_income: number | null;
  oi_high: number | null;
  outlook: string | null;
}
export async function fetchGuidanceHistory(ticker: string, numQuarters = 6): Promise<{ ticker: string; count: number; data: GuidanceRow[] }> {
  return apiFetch(`/api/edgar/guidance/${ticker}?num_quarters=${numQuarters}`, { timeoutMs: 2 * 60_000 });
}

export async function fetchTranscriptUrls(ticker: string, limit = 4): Promise<{ ticker: string; count: number; urls: string[] }> {
  return apiFetch(`/api/edgar/transcript-urls/${ticker}?limit=${limit}`, { timeoutMs: 60_000 });
}

export async function fetchTranscriptGuidance(ticker: string, urls: string[]): Promise<{ ticker: string; count: number; data: GuidanceRow[] }> {
  return apiFetch("/api/edgar/transcript-guidance", {
    method: "POST",
    body: JSON.stringify({ ticker, urls }),
    timeoutMs: 3 * 60_000,
  });
}

export interface EdgarEarningsCalendarRow {
  filed: string;
  ticker: string;
  company: string;
}
export async function fetchEdgarEarningsCalendar(days = 7): Promise<{ days: number; count: number; data: EdgarEarningsCalendarRow[] }> {
  return apiFetch(`/api/edgar/earnings-calendar?days=${days}`, { timeoutMs: 60_000 });
}

// ─── Options OI history ──────────────────────────────────────

export interface OIHistorySeries {
  strike: number;
  exp: string;
  type: "call" | "put";
  oi: (number | null)[];
  first: number;
  last: number;
  delta_abs: number;
  delta_pct: number | null;
}
export interface OIHistoryResponse {
  ticker: string;
  n_days_captured: number;
  total_days_available?: number;
  // `available: false` means the capture TABLE is not provisioned, which is a
  // different fact from "no captures yet" — the latter resolves on its own
  // after a couple of trading days, this one never does without a migration.
  // Optional because a frontend deploy can lead the API's.
  available?: boolean;
  reason?: string;
  dates: string[];
  series: OIHistorySeries[];
  summary: {
    biggest_builds: OIHistorySeries[];
    biggest_unwinds: OIHistorySeries[];
    daily_net: { date: string; call_oi: number; put_oi: number }[];
  } | null;
}
export async function fetchOIHistory(ticker: string, days = 10): Promise<OIHistoryResponse> {
  return apiFetch(`/api/market/oi-history/${ticker}?days=${days}`, { timeoutMs: 30_000 });
}

export interface OIUniverseEntry {
  ticker: string;
  rank: number;
  total_oi: number;
  total_volume: number | null;
}
export async function fetchOIUniverse(limit = 200): Promise<{ capture_date: string | null; tickers: OIUniverseEntry[] }> {
  return apiFetch(`/api/market/oi-universe?limit=${limit}`, { timeoutMs: 15_000 });
}

// ─── Macro / Analyst / Earnings History ──────────────────────

export interface MacroDashboardResponse {
  series: Record<string, { date: string; value: number }[]>;
  latest: Record<string, number>;
  labels: Record<string, string>;
}
export async function fetchMacroDashboard(): Promise<MacroDashboardResponse> {
  return apiFetch("/api/market/macro-dashboard", { timeoutMs: 90_000 });
}

export interface AnalystEstimatesData {
  price_target_mean?: number | null;
  price_target_high?: number | null;
  price_target_low?: number | null;
  num_analysts?: number | null;
  recommendation?: string | null;
  forward_eps?: number | null;
  trailing_eps?: number | null;
  forward_pe?: number | null;
  trailing_pe?: number | null;
  short_pct_float?: number | null;
  current_price?: number | null;
  market_cap?: number | null;
  sector?: string | null;
  industry?: string | null;
  eps_est_current_q?: number | null;
  eps_est_current_y?: number | null;
  eps_est_next_y?: number | null;
  rev_est_current_q?: number | null;
  rev_est_current_y?: number | null;
  rev_growth_current_y?: number | null;
  rec_strong_buy?: number | null;
  rec_buy?: number | null;
  rec_hold?: number | null;
  rec_sell?: number | null;
  rec_strong_sell?: number | null;
  [key: string]: unknown;
}
export async function fetchAnalystEstimates(ticker: string): Promise<{ ticker: string; data: AnalystEstimatesData }> {
  return apiFetch(`/api/market/analyst-estimates/${ticker}`, { timeoutMs: 60_000 });
}

export interface EarningsHistoryRow {
  quarter: string;
  actual: number | null;
  estimate: number | null;
  surprise_pct: number | null;
  [key: string]: unknown;
}
export async function fetchEarningsHistory(ticker: string): Promise<{ ticker: string; data: EarningsHistoryRow[] }> {
  return apiFetch(`/api/market/earnings-history/${ticker}`, { timeoutMs: 60_000 });
}

export interface FredPoint { date: string; value: number; }
export async function fetchFredSeriesCustom(seriesId: string, periods = 252): Promise<{ series_id: string; data: FredPoint[] }> {
  return apiFetch(`/api/market/fred/${seriesId}?periods=${periods}`, { timeoutMs: 60_000 });
}

export interface PeerRow {
  ticker: string;
  price: number | null;
  change: number;
  market_cap: number | null;
  pe: number | null;
  pb: number | null;
  revenue_growth: number | null;
  profit_margin: number | null;
  is_target: boolean;
}
export async function fetchPeerComparison(ticker: string): Promise<{ ticker: string; peers: PeerRow[] }> {
  return apiFetch(`/api/market/peers/${ticker}`, { timeoutMs: 30_000 });
}

// ─── Tracking ────────────────────────────────────────────────

export async function fetchPredictions(params?: { status?: string; source?: string; limit?: number }): Promise<{ count: number; data: Record<string, unknown>[] }> {
  const qs = new URLSearchParams();
  if (params?.status) qs.set("status", params.status);
  if (params?.source) qs.set("source", params.source);
  if (params?.limit) qs.set("limit", String(params.limit));
  return apiFetch(`/api/tracking/predictions?${qs.toString()}`);
}

export async function fetchAccuracySummary(): Promise<{
  total: number; evaluated: number; correct: number; accuracy: number;
  by_source: Record<string, { total: number; correct: number; accuracy: number }>;
}> {
  return apiFetch("/api/tracking/accuracy");
}

export async function fetchClosedPositions(limit = 50): Promise<{ count: number; data: Record<string, unknown>[] }> {
  return apiFetch(`/api/tracking/closed-positions?limit=${limit}`);
}

export interface SignalEngineSummary {
  n_tickers: number;
  n_bullish: number;
  n_bearish: number;
  n_neutral?: number;
  avg_conviction: number;
  top_bulls?: string[];
  top_bears?: string[];
}

export interface SignalEngineIdea {
  ticker: string;
  overall_direction: string;      // "bull" | "bear" | "neutral"
  overall_conviction: number;     // 0..1
  signal_agreement: number;       // 0..1
  n_signals: number;
  vol_regime?: string;
  strength?: number;
  direction_score?: number;
  [k: string]: unknown;
}

export interface SignalEngineResponse {
  summary: SignalEngineSummary;
  source_weights: Record<string, number>;
  ideas: SignalEngineIdea[];
}

export async function fetchSignalEngine(topN = 10): Promise<SignalEngineResponse> {
  return apiFetch(`/api/tracking/signal-engine?top_n=${topN}`, { timeoutMs: 30_000 });
}

// ─── Vol Surface ─────────────────────────────────────────────

export interface SurfaceSnapshot {
  date: string;
  spot: number;
  data: { strike: number; dte: number; iv: number; delta?: number; gamma?: number; type: string; exp: string }[];
}

export async function fetchSurfaceSnapshots(
  ticker: string,
  days = 10
): Promise<{ ticker: string; count: number; snapshots: SurfaceSnapshot[] }> {
  return apiFetch(`/api/options/surface-snapshots/${ticker}?days=${days}`);
}

export async function saveSurfaceSnapshot(
  ticker: string,
  spot: number,
  data: { strike: number; dte: number; iv: number; delta?: number; gamma?: number; type: string; exp: string }[]
): Promise<{ status: string }> {
  return apiFetch(`/api/options/surface-snapshots/${ticker}`, {
    method: "POST",
    body: JSON.stringify({ spot, data }),
  });
}

export interface AITradeIdeasResponse {
  content: string;
  cached: boolean;
  cost: number;
}

export async function fetchAITradeIdeas(params: {
  ticker: string;
  context: string;
  style?: string;
  account_size?: number;
  refine_prompt?: string;
  previous_response?: string;
}): Promise<AITradeIdeasResponse> {
  return apiFetch("/api/options/ai-trade-ideas", {
    method: "POST",
    body: JSON.stringify(params),
    timeoutMs: 120_000, // 2 minutes for AI generation
  });
}

export interface VolLandscapeMetric {
  Ticker: string; Label: string; Group: string; Spot: number;
  Front_IV: number; Back_IV: number | null; IV_HV: number | null;
  /** Null when the chain could not be measured — the 25-delta put or the ATM
   *  put was missing. It is NOT 1.0 ("no skew"), which would be a claim. */
  Put_Skew: number | null;
  /** 25-delta call IV minus 25-delta put IV, in vol points. Cross-type by
   *  definition, so no ATM anchor to get wrong. Null when either wing is
   *  missing — not 0.0, which would say "flat". */
  Risk_Rev: number | null;
  /** Convexity: each 25-delta wing measured against its OWN type's ATM. Null
   *  when a leg is missing. Not 0.0, which would say "no fat tails". */
  Butterfly: number | null;
  /** ATM put IV over ATM call IV. Put-call parity forces this to 1.0, so the
   *  distance from 1.0 measures how stale the chain's quotes are. Null when
   *  either leg is missing — a chain with no data scores no confidence. */
  Parity?: number | null;
  /** Fraction of adjacent strikes whose deltas contradict no-arbitrage.
   *  Diagnostic only: it flags 19 of 20 live names because deep wings are thin
   *  everywhere, so it is reported and never gated on. */
  Ladder_Broken?: number | null;
  TS_Slope: number; VRP_Vol: number | null; Impl_Move: number;
  HV20: number | null; PC_Ratio: number | null; IV_Pctile: number | null;
  Front_DTE: number;
  [key: string]: unknown;
}

export interface VolLandscapeScan {
  count: number;
  metrics: VolLandscapeMetric[];
  /** Per-moneyness IV in percent. A point is null when no strike sits near that
   *  moneyness — it is not 0, which would plot as zero volatility. Note the
   *  curve is built OTM (puts below spot, calls above), so on a chain whose ATM
   *  quotes disagree the two wings sit at different levels and the seam shows
   *  as a kink at 1.00. `Parity` on the same ticker measures that gap. */
  smile_data: { ticker: string; [moneyness: string]: number | string | null }[];
  ts_data: { ticker: string; term_structure: { dte: number; iv: number }[] }[];
  impl_corr: number | null;
  /** What the cross-asset scan implies for the ES session. Each read is a
   *  measured value plus the sentence that says what it means; `caveat` is
   *  present only where the number rests on an assumption the reader needs.
   *  Null when SPY is missing — every read is anchored on it. */
  es_read?: {
    spy?: Record<string, number | null>;
    reads?: { label: string; value: string; note: string; caveat?: string }[];
  } | null;
  divergences: { pair: string; metric: string; description: string; signal: string }[];
  earnings: Record<string, { date: string; days: number }>;
  regime: string;
  regime_action: string;
  summary: {
    avg_iv: number; avg_ivhv: number; avg_skew: number;
    n_inverted: number; n_steep_skew: number; n_tickers: number;
    /** Chains the skew stats were computed over. Lower than n_tickers when a
     *  chain's ATM put and ATM call disagree by more than put-call parity
     *  allows — n_steep_skew is out of THIS, not out of n_tickers. */
    n_skew_rated?: number;
    avg_sector_iv?: number;
    /** The honest version of `n_steep_skew`. The 1.10 cut behind that count
     *  sits at the median of the cross section, so "more than half above it"
     *  reduces to "is the median above 1.10" — readable directly from here. */
    median_skew?: number | null;
    impl_corr?: number | null;
  };
  /** Where each hardcoded cut sits in TODAY's cross section. Disclosure, not
   *  validation: `near_median` marks a cut that splits the universe in half and
   *  therefore cannot separate a regime from its opposite. `validated` is
   *  always false until a stored history is deep enough to judge against. */
  thresholds?: Record<string, {
    cut: number; column: string;
    pctile_in_universe: number | null;
    n?: number; near_median?: boolean; validated?: boolean;
  }>;
  /** Percentile of each summary measure against its own recorded history.
   *  `pctile` is null until `n_history` clears the floor — null means "not yet
   *  knowable", never a stand-in middle value. */
  history?: Record<string, { pctile: number | null; n_history: number }>;
}

export async function fetchVolLandscape(): Promise<VolLandscapeScan> {
  return apiFetch("/api/options/vol-landscape", { timeoutMs: 120_000 });
}

export async function fetchHigherGreeks(params: {
  spot: number;
  strike: number;
  time_years: number;
  vol: number;
  rate?: number;
  opt_type?: string;
}): Promise<{
  vanna: number; volga: number; charm: number; veta: number;
  speed: number; zomma: number; color: number; ultima: number;
}> {
  return apiFetch("/api/options/higher-greeks", {
    method: "POST",
    body: JSON.stringify(params),
  });
}

export async function fetchImpliedVol(params: {
  spot: number;
  strike: number;
  time_years: number;
  market_price: number;
  rate?: number;
  opt_type?: string;
}): Promise<{ implied_vol: number | null }> {
  return apiFetch(`/api/options/implied-vol?${new URLSearchParams(
    Object.entries(params).map(([k, v]) => [k, String(v)])
  ).toString()}`);
}

// ─── Health ──────────────────────────────────────────────────

export async function fetchHealth(): Promise<{
  status: string;
  database: string;
}> {
  return apiFetch("/api/health");
}

// ─── Scanners ────────────────────────────────────────────────

export interface ICScanConfig {
  tickers: string[];
  dte_min: number;
  dte_max: number;
  short_delta: number;
  wing_width: number;
  profit_target_pct: number;
  stop_multiplier: number;
  account_size: number;
  max_risk_pct: number;
  kelly_fraction: number;
  win_rate_bump: number;
}

export interface ICHistWinrate {
  win_rate: number;
  exp_win_rate: number;
  n_trials: number;
  early_profit: number;
  stopped_out: number;
  breached_at_exp: number;
  avg_max_move_pct: number;
  median_max_move_pct: number;
}

export interface ICAltExpiration {
  exp: string;
  dte: number;
  strikes: string;
  credit: number;
  credit_per_day: number;
  max_risk: number;
  pop: number;
}

export interface ICStressScenario {
  event: string;
  date: string;
  days_away: number;
  scenario: string;
  move_pct: number;
  pnl: number;
  survives: boolean;
}

export interface ICResult {
  ticker: string;
  expiration: string;
  dte: number;
  spot: number;
  short_put: number;
  long_put: number;
  short_call: number;
  long_call: number;
  credit: number;
  fill_estimate: number;
  natural: number;
  mid: number;
  max_risk: number;
  pop: number;
  avg_iv: number;
  ivr: number | null;
  ivr_band: string;
  vrp: number | null;
  hv20: number | null;
  liq_grade: string;
  min_oi: number;
  max_ba: number | null;
  upper_be: number;
  lower_be: number;
  upper_be_pct: number;
  lower_be_pct: number;
  earnings_before: boolean;
  earnings_days: number | null;
  adj_score: number;
  n_synthetic: number;
  ev_per_contract: number;
  wing_pct: number;
  days_to_target: number;
  // Greeks
  net_delta: number;
  net_gamma: number;
  net_theta: number;
  net_vega: number;
  theta_vega_ratio: number;
  sp_delta: number;
  sc_delta: number;
  // Per-leg
  legs: {
    label: string; bid: number; ask: number; mid: number;
    delta: number; gamma: number; theta: number; vega: number;
    oi: number; vol: number; live: boolean;
  }[];
  // Kelly
  managed_wr: number;
  kelly_full: number;
  kelly_adj: number;
  contracts: number;
  total_risk: number;
  total_credit: number;
  // Adjustment triggers
  put_30d_trigger: number;
  call_30d_trigger: number;
  // Management
  profit_target_pct: number;
  stop_multiplier: number;
  target_credit: number;
  stop_loss_amt: number;
  // Chart data
  payoff_prices: number[];
  payoff_pnl: number[];
  decay_days: number[];
  decay_vals: number[];
  // Historical backtest
  hist_winrate: ICHistWinrate | null;
  // Alternative expirations
  alt_expirations: ICAltExpiration[];
  // Forward event stress test
  stress_test: ICStressScenario[];
}

export async function scanIronCondors(
  config: Partial<ICScanConfig> = {}
): Promise<{ count: number; results: ICResult[] }> {
  return apiFetch("/api/scan/iron-condor", {
    method: "POST",
    body: JSON.stringify(config),
    timeoutMs: 5 * 60 * 1000, // 5 minutes — scan takes 1-3min
  });
}

// ── Vertical Spread Scanner ──

export interface VSScanConfig {
  tickers: string[];
  spread_types: string[];
  dte_min: number; dte_max: number;
  short_delta: number; width: number;
  profit_target_pct: number; stop_multiplier: number;
  account_size: number; max_risk_pct: number;
  kelly_fraction: number; win_rate_bump: number;
}

export interface VSResult {
  ticker: string; spread_type: string; spread_label: string;
  is_credit: boolean; is_bullish: boolean; opt_type: string;
  expiration: string; dte: number; spot: number;
  short_strike: number; long_strike: number; width: number;
  premium: number; fill_estimate: number; max_risk: number; max_profit: number;
  pop: number; rr_ratio: number; breakeven: number; be_pct: number;
  avg_iv: number; ivr: number | null; ivr_band: string; vrp: number | null; hv20: number | null;
  put_skew: number; exp_move_pct: number; short_dist_pct: number; inside_exp_move: boolean;
  liq_grade: string; min_oi: number; max_ba: number | null;
  earnings_before: boolean; earnings_days: number | null;
  adj_score: number; n_synthetic: number;
  net_delta: number; net_gamma: number; net_theta: number; net_vega: number;
  trigger_30d: number; days_to_target: number;
  managed_wr: number; kelly_full: number; kelly_adj: number; contracts: number;
  total_credit: number; total_risk: number;
  profit_target_pct: number; stop_multiplier: number; target_profit: number; stop_loss: number;
  payoff_prices: number[]; payoff_pnl: number[]; decay_days: number[]; decay_vals: number[];
  hist_winrate: ICHistWinrate | null;
  stress_test: ICStressScenario[];
  alt_expirations: ICAltExpiration[];
  legs: { label: string; bid: number; ask: number; mid: number; delta: number; oi: number; live: boolean }[];
}

export async function scanVerticalSpreads(
  config: Partial<VSScanConfig> = {}
): Promise<{ count: number; results: VSResult[] }> {
  return apiFetch("/api/scan/vertical-spread", {
    method: "POST",
    body: JSON.stringify(config),
    timeoutMs: 5 * 60 * 1000,
  });
}

// ─── Trump Decoder ──────────────────────────────────────────

export interface TrumpPsychProfile {
  mbti?: string;
  big_five?: Record<string, number>;
  dark_triad?: Record<string, number>;
  negotiation_style?: Record<string, unknown>;
  bluff_patterns?: { pattern: string; frequency: string; example: string }[];
  escalation_tells?: { tell: string; indicates: string; example: string }[];
  deescalation_tells?: { tell: string; indicates: string; example: string }[];
  known_triggers?: { trigger: string; typical_response: string; market_impact: string }[];
  communication_patterns?: Record<string, unknown>;
  bluff_detection_rubric?: { factor: string; bluff_indicator: string; weight: number }[];
  full_profile?: string;
  current_behavioral_snapshot?: string;
}

export interface TrumpPsychResponse {
  success: boolean; error?: string;
  cached: boolean; profile: TrumpPsychProfile;
  version?: number; created_at?: string;
}

export interface TrumpHistoricalAnalog {
  date: string; statement_summary?: string; similarity?: string;
  outcome: string; days_to_resolution?: number; was_bluff?: boolean;
  market_reaction?: string; sector_impact?: string;
}

export interface TrumpPositionRisk {
  ticker: string; position_type: string; risk_level: string; recommendation: string;
}

export interface TrumpAffectedSector {
  sector: string; direction: string; magnitude: number; reason: string;
}

export interface TrumpAffectedTicker {
  ticker: string; direction: string; magnitude: number; reason: string;
}

export interface TrumpMoodIndex {
  posting_frequency?: string; sentiment?: string; escalation_level?: number;
  notable_recent_posts?: string[]; tone_shift?: string;
}

export interface TrumpDecodeResponse {
  success: boolean; error?: string;
  statement: string; context: string;
  decoded_meaning: string;
  bluff_score: number; bluff_label: string; bluff_reasoning: string;
  market_impact: number; market_impact_label: string;
  probability_distribution: Record<string, number>;
  historical_analogs: TrumpHistoricalAnalog[];
  position_risks: TrumpPositionRisk[];
  affected_sectors: TrumpAffectedSector[];
  affected_tickers: TrumpAffectedTicker[];
  mood_index: TrumpMoodIndex;
  pattern_match?: Record<string, unknown>;
  spy_range_pct?: number[];
  vol_impact?: string;
  historical_avg_reaction?: string;
  key_signals_to_watch?: string[];
  timeline?: string;
  narrative: string;
  model_sources: Record<string, string>;
}

export interface TrumpPredictResponse {
  success: boolean; error?: string;
  scenario: string; timeframe: string;
  predicted_actions: {
    action: string; probability: number; timeline: string;
    historical_precedent: string; market_impact: number; signals_to_watch: string[];
  }[];
  psychological_reasoning: string;
  wild_card_risk: string;
  recommended_positioning: string;
  narrative: string;
  historical_analogs: { date: string; situation: string; trump_response: string; timeline: string; market_reaction: string }[];
  base_rate: string;
}

export interface TrumpPost {
  timestamp: string; text: string; platform: string;
  interpretation: string; market_relevance: number;
  category: string; sentiment: string;
}

export interface TrumpMonitorResponse {
  success: boolean; error?: string;
  posts: TrumpPost[];
  mood_summary: string; posting_frequency: string;
  escalation_trend: string; key_themes: string[];
  market_alert: string | null;
  breaking_developments?: string | null;
}

export interface TrumpPattern {
  id?: number; category: string; date_range: string;
  trigger_statement: string; escalation_path: { date: string; event: string; market_reaction: string }[];
  resolution: string; resolution_type: string; days_to_resolution: number;
  market_impact_summary: string; spy_move_pct: number; vix_peak: number;
  most_affected_sectors: string[]; pattern_type: string; bluff_score: number;
  key_lesson?: string;
}

export interface TrumpPatternResponse {
  success: boolean; error?: string;
  patterns: TrumpPattern[]; source: string; count: number;
}

export interface TrumpDecodedStatement {
  id: number; statement: string; user_context: string;
  decoded_meaning: string; bluff_score: number; bluff_label: string;
  market_impact: number; market_impact_label: string;
  probability_distribution: Record<string, number>;
  actual_outcome?: string; outcome_market_move?: number; was_accurate?: boolean;
  created_at: string;
}

export async function fetchTrumpPsychProfile(): Promise<TrumpPsychResponse> {
  return apiFetch("/api/trump/psych-profile", { timeoutMs: 3 * 60_000 });
}

export async function decodeTrumpStatement(statement: string, context: string = "", positions_summary: string = "", image_base64: string = ""): Promise<TrumpDecodeResponse> {
  return apiFetch("/api/trump/decode-statement", {
    method: "POST",
    body: JSON.stringify({ statement, context, positions_summary, ...(image_base64 ? { image_base64 } : {}) }),
    timeoutMs: 3 * 60_000,
  });
}

export async function predictTrumpResponse(scenario: string, timeframe: string = "48h"): Promise<TrumpPredictResponse> {
  return apiFetch("/api/trump/predict-response", {
    method: "POST",
    body: JSON.stringify({ scenario, timeframe }),
    timeoutMs: 3 * 60_000,
  });
}

export async function fetchTrumpMonitor(): Promise<TrumpMonitorResponse> {
  return apiFetch("/api/trump/monitor", { timeoutMs: 2 * 60_000 });
}

export async function fetchTrumpPatterns(query: string = "", category: string = ""): Promise<TrumpPatternResponse> {
  const params = new URLSearchParams();
  if (query) params.set("query", query);
  if (category) params.set("category", category);
  return apiFetch(`/api/trump/pattern-database?${params}`, { timeoutMs: 2 * 60_000 });
}

export async function fetchTrumpHistory(limit: number = 20): Promise<{ success: boolean; statements: TrumpDecodedStatement[] }> {
  return apiFetch(`/api/trump/history?limit=${limit}`, { timeoutMs: 30_000 });
}

export interface TrumpTrackRecord {
  success: boolean;
  total_decodes: number;
  graded_count: number;
  pending_count: number;
  accuracy_pct: number | null;
  bluff_call_count: number;
  bluff_accuracy_pct: number | null;
  genuine_call_count: number;
  genuine_accuracy_pct: number | null;
  most_recent_graded: {
    id: number;
    created_at: string;
    statement_preview: string;
    bluff_score: number;
    actual_spy_move_pct: number | null;
    was_accurate: boolean;
  } | null;
  error?: string;
}

export async function fetchTrumpTrackRecord(): Promise<TrumpTrackRecord> {
  return apiFetch("/api/trump/track-record", { timeoutMs: 30_000 });
}

// ── Meta Analysis ─────────────────────────────────────────────────────

export interface MetaMetric {
  method: string;
  ann_return: number;
  ann_vol: number;
  sharpe: number;
  sortino: number;
  max_dd: number;
  calmar: number;
  win_rate: number;
  info_ratio?: number;
  tracking_error?: number;
  up_capture?: number;
  down_capture?: number;
}

export interface MetaWeightHistoryEntry {
  date: string;
  weights: Record<string, number>;
}

export interface MetaTurnoverEntry {
  date: string;
  turnover: number;
}

export interface MetaRegimeRow {
  method: string;
  regime: "Bull" | "Recovery" | "Bear" | "Crisis";
  ann_return: number;
  ann_vol: number;
  sharpe: number;
  days: number;
}

export interface MetaStressRow {
  method: string;
  beta: number;
  scenarios: Record<string, number>;
}

export interface MetaDsrRow {
  method: string;
  sharpe: number;
  dsr: number;
  skew: number;
  kurtosis: number;
  min_track_record: number;
  min_years: number;
  actual_days: number;
  sufficient_data: boolean;
  significant: boolean;
}

export interface MetaBootstrapRow {
  method: string;
  sharpe: number;
  ci_low: number;
  ci_high: number;
  p_positive: number;
  significant: boolean;
}

export interface MetaScoreRow {
  method: string;
  sharpe: number;
  dsr_pass: boolean;
  pbo_pass: boolean;
  boot_pass: boolean;
  trl_pass: boolean;
  score: number;
  verdict: "Robust" | "Credible" | "Suspect" | "Unreliable";
}

export interface MetaDrawdownDuration {
  longest_days: number;
  avg_days: number;
  episodes: number;
}

export interface MetaBacktestResponse {
  tickers: string[];
  n_assets: number;
  dates: string[];
  n_days: number;
  data_start: string | null;
  data_end: string | null;
  ranked_methods: string[];
  ranked_by: string;
  rebalance: string;
  est_days: number;
  equity_curves: Record<string, number[]>;
  net_curves: Record<string, number[]>;
  drawdown_curves: Record<string, number[]>;
  drawdown_duration: Record<string, MetaDrawdownDuration>;
  metrics: MetaMetric[];
  net_metrics: MetaMetric[];
  current_weights: Record<string, Record<string, number>>;
  weight_history: Record<string, MetaWeightHistoryEntry[]>;
  turnover: Record<string, MetaTurnoverEntry[]>;
  cost_bps: number;
  regime_analysis: MetaRegimeRow[];
  stress_scenarios: MetaStressRow[];
  stress_scenario_names: string[];
  dsr_results: MetaDsrRow[];
  pbo: { value: number | null; logits: number[] };
  bootstrap_ci: MetaBootstrapRow[];
  scorecard: MetaScoreRow[];
  rolling_sharpe: Record<string, { dates: string[]; values: number[] }>;
  method_corr_methods: string[];
  method_corr: number[][];
  excess_vs_ew: Record<string, { dates: string[]; values: number[] }>;
  n_methods_tested: number;
  error?: string;
}

export interface MetaBacktestRequest {
  tickers: string[];
  lookback?: "1Y" | "2Y" | "3Y" | "5Y";
  rebalance?: "Monthly" | "Quarterly";
  est_days?: 126 | 189 | 252 | 504;
  denoise?: boolean;
  blends?: Record<string, Record<string, number>>;
  rank_by?: "Sharpe" | "Ann. Return" | "Sortino" | "Calmar" | "Max DD";
}

export async function runMetaBacktest(req: MetaBacktestRequest): Promise<MetaBacktestResponse> {
  return apiFetch("/api/meta/backtest", {
    method: "POST",
    body: JSON.stringify(req),
    timeoutMs: 5 * 60_000,
  });
}

export interface MetaGridRow {
  universe: string;
  method: string;
  sharpe: number;
  ann_return: number;
  max_dd: number;
  sortino: number;
}

export interface MetaGridResponse {
  universes: string[];
  methods: string[];
  grid: MetaGridRow[];
  lookback: string;
  rebalance: string;
  est_days: number;
  error?: string;
}

export async function runMetaGrid(req: {
  lookback?: string;
  rebalance?: string;
  est_days?: number;
  denoise?: boolean;
}): Promise<MetaGridResponse> {
  return apiFetch("/api/meta/grid", {
    method: "POST",
    body: JSON.stringify(req),
    timeoutMs: 10 * 60_000,
  });
}

export async function fetchMetaPresets(): Promise<{ presets: Record<string, string[]> }> {
  return apiFetch("/api/meta/presets");
}

// ── Scenario Analysis ─────────────────────────────────────────────────

export interface ScenarioRegime {
  name: string;
  description: string;
  rationale: string;
  base_probability: number;
  driver_moves: Record<string, number>;
}

export interface ScenarioTickerEstimate {
  point: number;
  lo: number;
  hi: number;
  r2: number;
  beta_stability: number;
  source: string;
}

export interface ScenarioRegimeResult {
  regime: string;
  pnl: number;
  pnl_lo: number;
  pnl_hi: number;
  pnl_pct: number;
  prob: number;
  ticker_moves: Record<string, ScenarioTickerEstimate>;
}

export interface ScenarioMonteCarlo {
  mean: number;
  median: number;
  var_95: number;
  cvar_95: number;
  p10: number;
  p90: number;
  prob_loss: number;
  prob_gain: number;
  percentiles: Record<string, number>;
  histogram: { counts: number[]; edges: number[] };
  regime_draw_counts: Record<string, number>;
}

export interface ScenarioFactorDiag {
  ticker: string;
  r2: number;
  beta_stability: number;
  n_obs: number;
  residual_std: number;
  stressed_residual_std: number;
  sector: string;
  betas: Record<string, number>;
  alpha: number;
}

export interface ScenarioCorrelation {
  normal_methods?: string[];
  normal?: number[][];
  stressed_methods?: string[];
  stressed?: number[][];
}

export interface FedDriverInfo {
  name: string;
  unit: string;
  yoy: boolean;
  category: string;
}

export interface PortfolioImpactResponse {
  tickers: string[];
  failed: string[];
  n_assets: number;
  portfolio_value: number;
  horizon_days: number;
  alloc_per_ticker: number;
  regimes: ScenarioRegime[];
  driver_keys: string[];
  fed_drivers: Record<string, FedDriverInfo>;
  factor_series: string[];
  regime_results: ScenarioRegimeResult[];
  ev_pnl: number;
  ev_lo: number;
  ev_hi: number;
  monte_carlo: ScenarioMonteCarlo;
  concentration: { sectors: Record<string, string[]>; warnings: string[] };
  correlation: ScenarioCorrelation;
  factor_diagnostics: ScenarioFactorDiag[];
  avg_r2: number;
  avg_stability: number;
  error?: string;
}

export interface PortfolioImpactRequest {
  tickers: string[];
  portfolio_value?: number;
  lookback?: number;
  horizon_days?: number;
  user_probs?: Record<string, number>;
  n_sims?: number;
}

export async function fetchPortfolioImpact(req: PortfolioImpactRequest): Promise<PortfolioImpactResponse> {
  return apiFetch("/api/scenario/portfolio-impact", {
    method: "POST",
    body: JSON.stringify(req),
    timeoutMs: 4 * 60_000,
  });
}

export interface GbmScenarioResult {
  mean_path: number[];
  p10_path: number[];
  p90_path: number[];
  median_terminal: number;
  mean_terminal: number;
  p10_terminal: number;
  p90_terminal: number;
  prob_profit: number;
  annual_ret: number;
}

export interface GbmResponse {
  ticker: string;
  spot: number;
  hist_vol: number;
  history: { dates: string[]; closes: number[] };
  scenarios: Record<string, GbmScenarioResult>;
  error?: string;
}

export async function fetchGbmProjection(req: {
  ticker: string;
  lookback?: number;
  proj_days?: number;
  num_paths?: number;
  bull_ret?: number;
  base_ret?: number;
  bear_ret?: number;
}): Promise<GbmResponse> {
  return apiFetch("/api/scenario/gbm-projection", {
    method: "POST",
    body: JSON.stringify(req),
    timeoutMs: 60_000,
  });
}

export interface RegimeTrackEvaluation {
  date: string;
  top_regime: string;
  probability: number;
  expected: "Bullish" | "Bearish" | "Neutral";
  spy_30d: number;
  actual: "Bullish" | "Bearish";
  correct: boolean | null;
}

export interface RegimeTrackResponse {
  history_count: number;
  evaluations_count: number;
  directional_count: number;
  correct_count: number;
  accuracy: number | null;
  evaluations: RegimeTrackEvaluation[];
  error?: string;
}

export async function fetchRegimeTrackRecord(): Promise<RegimeTrackResponse> {
  return apiFetch("/api/scenario/regime-track-record", { timeoutMs: 60_000 });
}

export interface GrokLatestResponse {
  available: boolean;
  timestamp?: string;
  regimes?: Array<{ name: string; probability: number; rationale?: string }>;
  sentiment_summary?: string;
  change_summary?: string;
  asset_estimates?: Record<string, Record<string, number>>;
}

export async function fetchGrokLatest(): Promise<GrokLatestResponse> {
  return apiFetch("/api/scenario/grok-latest");
}

// ── Quant Lab ─────────────────────────────────────────────────────────

export interface QuantLabAdfRow {
  d: number;
  adf_stat: number | null;
  pvalue: number;
  corr: number;
}

export interface QuantLabOHLCV {
  dates: string[];
  close: number[];
  log_prices: number[];
  log_returns: number[];
  volume: number[];
  high: number[];
  low: number[];
}

export interface QuantLabFeatureImportance {
  features: string[];
  mdi: Record<string, number>;
  mda: Record<string, number>;
  oos_accuracy: number;
}

export interface QuantLabAnalyzeResponse {
  ticker: string;
  lookback: number;
  n_obs: number;
  date_start: string;
  date_end: string;
  ann_return: number;
  ann_vol: number;
  ohlcv: QuantLabOHLCV;
  adf_scan: QuantLabAdfRow[];
  min_d: number;
  fd_optimal: { d: number; dates: string[]; values: number[] };
  sadf: { dates: string[]; values: number[]; cv_95: number; max: number; n_periods: number };
  chow: { dates: string[]; f_stats: number[]; cv_99: number };
  feature_importance: QuantLabFeatureImportance | null;
  error?: string;
}

export async function fetchQuantLabAnalyze(ticker: string, lookback: number = 756): Promise<QuantLabAnalyzeResponse> {
  return apiFetch("/api/quant-lab/analyze", {
    method: "POST",
    body: JSON.stringify({ ticker, lookback }),
    timeoutMs: 4 * 60_000,
  });
}

export interface QuantLabHrpMetrics {
  ann_return: number;
  ann_vol: number;
  sharpe: number;
  max_dd: number;
}

export interface QuantLabHrpWeightHistoryEntry {
  date: string;
  weights: Record<string, number>;
}

export interface QuantLabHrpResponse {
  tickers: string[];
  failed: string[];
  weights: {
    hrp: Record<string, number>;
    equal: Record<string, number>;
    inverse_vol: Record<string, number>;
  };
  dates: string[];
  cum_hrp: number[];
  cum_eq: number[];
  cum_iv: number[];
  static_metrics: {
    hrp: QuantLabHrpMetrics;
    equal: QuantLabHrpMetrics;
    inverse_vol: QuantLabHrpMetrics;
  };
  walk_forward: {
    dates: string[];
    cum: number[];
    metrics: QuantLabHrpMetrics;
    weight_history: QuantLabHrpWeightHistoryEntry[];
    rebalance: string;
  };
  error?: string;
}

export async function fetchQuantLabHrp(req: {
  tickers: string[];
  lookback?: number;
  rebalance?: "Monthly" | "Quarterly";
  estimation_window?: number;
}): Promise<QuantLabHrpResponse> {
  return apiFetch("/api/quant-lab/hrp", {
    method: "POST",
    body: JSON.stringify(req),
    timeoutMs: 4 * 60_000,
  });
}

// ── Fed Macro Drivers ─────────────────────────────────────────────────

export interface StockTwitsItem {
  symbol: string;
  bullish: number;
  bearish: number;
  messages: number;
  bull_ratio: number;
  signal: string;
}

export interface PolymarketItem {
  category: string;
  question: string;
  yes_prob: number;
  no_prob: number;
}

export async function fetchFedMacroSentiment(): Promise<{
  stocktwits: StockTwitsItem[];
  polymarket: PolymarketItem[];
}> {
  return apiFetch("/api/fed-macro/sentiment", { timeoutMs: 90_000 });
}

export interface FedBalanceSheetResponse {
  series: Record<string, (number | null)[]>;
  dates: string[];
  snapshot: {
    total_assets?: number | null;
    tga?: number | null;
    rrp?: number | null;
    net_liquidity?: number | null;
    net_liq_change?: number | null;
    draining?: boolean | null;
  };
  error?: string;
}
export async function fetchFedBalanceSheet(): Promise<FedBalanceSheetResponse> {
  return apiFetch("/api/fed-macro/balance-sheet", { timeoutMs: 60_000 });
}

export interface CotPositioningResponse {
  positioning: Record<string, { direction: string; net_pct_oi: number; change: number }>;
}
export async function fetchCotPositioning(): Promise<CotPositioningResponse> {
  return apiFetch("/api/fed-macro/cot", { timeoutMs: 60_000 });
}

// ─── CFTC / Positioning (wide universe) ──────────────────────────

export type CftcAssetClass = "equity" | "rates" | "fx" | "energy" | "metals" | "grains" | "softs" | "meats";
export type CftcReportType = "disaggregated" | "tff" | "legacy" | "supplemental";

export interface CftcContract {
  code: string;
  symbol: string;
  name: string;
  asset_class: CftcAssetClass;
  spec_report: CftcReportType;
  track_legacy: boolean;
  priority: number;
}

export interface CftcHeatmapTile {
  code: string;
  symbol: string;
  name: string;
  asset_class: CftcAssetClass;
  report_type: CftcReportType;
  date: string | null;
  spec_net: number | null;
  spec_pct_oi: number | null;
  pctile_3y: number | null;
  pctile_1y: number | null;
  cot_index_3y: number | null;
  zscore_3y: number | null;
  chg_1w: number | null;
  chg_4w: number | null;
  chg_1w_sign: "up" | "down";
  comm_pctile_3y: number | null;
  divergence_z: number | null;
  oi: number | null;
  conc_lt4: number | null;
}

export interface CftcHistoryRow {
  date: string;
  oi: number;
  spec_long: number;
  spec_short: number;
  spec_spread: number;
  spec_net: number;
  spec_gross: number;
  spec_pct_oi: number | null;
  spec_n_traders_long?: number;
  spec_n_traders_short?: number;
  spec_n_traders?: number;
  conc_gross_lt4?: number;
  conc_gross_lt8?: number;
  comm_long?: number;
  comm_short?: number;
  comm_net?: number | null;
  comm_pct_oi?: number | null;
  spec_pctile_3y: number | null;
  spec_pctile_1y: number | null;
  cot_index_3y: number | null;
  spec_zscore_3y: number | null;
  spec_chg_1w: number | null;
  spec_chg_4w: number | null;
  comm_pctile_3y: number | null;
  spec_vs_comm_z: number | null;
  conc_lt4_chg_4w: number | null;
  traders_zscore_3y: number | null;
}

export interface CftcHistoryResponse {
  code: string;
  symbol: string;
  name: string;
  asset_class: CftcAssetClass;
  spec_report: CftcReportType;
  count: number;
  data: CftcHistoryRow[];
}

export interface CftcDivergenceRow {
  code: string;
  symbol: string;
  name: string;
  asset_class: CftcAssetClass;
  date: string;
  divergence_z: number;
  spec_pctile_3y: number | null;
  comm_pctile_3y: number | null;
  spec_net: number;
  comm_net: number | null;
}

export interface CftcRegime {
  risk_on_off: number;
  reflation: number;
  safe_haven: number;
  dollar: number;
  interpretation: Record<string, string>;
}

export interface CftcUnwindRow {
  code: string;
  symbol: string;
  name: string;
  asset_class: CftcAssetClass;
  pctile_3y: number;
  vol_pctile: number;
  unwind_score: number;
  direction: "long" | "short";
  extremity: number;
}

export interface CftcFlowRow {
  code: string;
  symbol: string;
  name: string;
  asset_class: CftcAssetClass;
  date: string;
  chg_1w: number;
  chg_1w_pct_oi: number;
  chg_4w: number | null;
  conc_lt4_chg_4w: number | null;
  spec_net: number;
  pctile_3y: number | null;
}

export interface CftcDashboard {
  regime: CftcRegime;
  heatmap: CftcHeatmapTile[];
  divergence_top: CftcDivergenceRow[];
  flow_radar_top: CftcFlowRow[];
  cta_unwind_top: CftcUnwindRow[];
}

export async function fetchCftcContracts(assetClass?: CftcAssetClass): Promise<{ count: number; contracts: CftcContract[] }> {
  const q = assetClass ? `?asset_class=${assetClass}` : "";
  return apiFetch(`/api/cftc/contracts${q}`, { timeoutMs: 30_000 });
}

export async function fetchCftcHistory(code: string, lookbackWeeks = 260): Promise<CftcHistoryResponse> {
  return apiFetch(`/api/cftc/history/${code}?lookback_weeks=${lookbackWeeks}`, { timeoutMs: 60_000 });
}

export async function fetchCftcHeatmap(): Promise<{ count: number; tiles: CftcHeatmapTile[] }> {
  return apiFetch("/api/cftc/heatmap", { timeoutMs: 120_000 });
}

export async function fetchCftcDivergence(minAbsZ = 1.0): Promise<{ count: number; threshold: number; rows: CftcDivergenceRow[] }> {
  return apiFetch(`/api/cftc/divergence?min_abs_z=${minAbsZ}`, { timeoutMs: 120_000 });
}

export async function fetchCftcRegime(): Promise<CftcRegime> {
  return apiFetch("/api/cftc/regime", { timeoutMs: 120_000 });
}

export async function fetchCftcCtaUnwind(): Promise<{ count: number; rows: CftcUnwindRow[] }> {
  return apiFetch("/api/cftc/cta-unwind", { timeoutMs: 120_000 });
}

export async function fetchCftcFlowRadar(minPctOi = 3.0): Promise<{ count: number; threshold_pct_oi: number; rows: CftcFlowRow[] }> {
  return apiFetch(`/api/cftc/flow-radar?min_pct_oi=${minPctOi}`, { timeoutMs: 120_000 });
}

export async function fetchCftcDashboard(): Promise<CftcDashboard> {
  return apiFetch("/api/cftc/dashboard", { timeoutMs: 180_000 });
}

// ─── CTA Model (ZeroHedge / Nomura framework) ────────────────────

export type CtaBias = "all_buying" | "all_selling" | "mixed" | "neutral" | "unknown";

export interface CtaTrigger {
  type: string;
  window: number;
  level: number;
  distance_pct: number;
  side_if_breached: "long" | "short";
}

export interface CtaScenario {
  target_price: number;
  delta_exposure: number;
  projected_exposure: number;
}

export interface CtaModelStatus {
  code: string;
  symbol: string | null;
  name: string | null;
  asset_class: CftcAssetClass | null;
  yf_symbol: string | null;
  last_price: number;
  available: boolean;
  reason?: string;
  exposure?: number;
  components?: Record<string, number>;
  triggers?: CtaTrigger[];
  scenarios?: {
    current_exposure: number;
    horizons: Record<string, Record<string, CtaScenario>>;
    bias_1w?: CtaBias;
    bias_1m?: CtaBias;
    vol_1w_pct?: number;
    vol_1m_pct?: number;
  };
}

// ─── CTA flow board (home page chart) ────────────────────────────
// Exposure is in model points (-100..100), not notional dollars. Desk
// readouts quote $bn by scaling to an assumed trend-following AUM; we don't
// have that scalar, so it is deliberately not applied anywhere here.

export interface CtaPathPoint {
  day: number;
  price: number;
  exposure: number;
  delta_exposure: number;
}

export interface CtaScenarioPath {
  target_price: number;
  move_pct: number;
  path: CtaPathPoint[];
}

export interface CtaPivot {
  window: number;
  level: number;
  distance_pct: number;
  side_if_breached: "long" | "short";
}

export interface CtaFlowBoard {
  available: boolean;
  reason?: string;
  code: string;
  symbol: string | null;
  name: string | null;
  last_price: number;
  current_exposure: number;
  /** 1σ move over `horizon_days` — the chart's own horizon. */
  sigma_1_pct: number;
  /** Per-horizon sigmas matching the `terminal` table. Don't describe a 1w
   *  flow using sigma_1_pct when horizon_days is 20. */
  sigma_1w_pct?: number;
  sigma_1m_pct?: number;
  horizon_days: number;
  scenarios: Record<string, CtaScenarioPath>;
  pivots: Partial<Record<"short_term" | "medium_term" | "long_term", CtaPivot>>;
  terminal: Record<string, Record<string, CtaScenario>>;
  bias_1w?: CtaBias;
  bias_1m?: CtaBias;
  /** Date of the last price bar used — the real freshness signal. `asof` is
   *  merely when the board was computed, which a price cache makes newer. */
  price_asof?: string | null;
  asof?: string;
}

export async function fetchCtaFlows(code = "13874A"): Promise<CtaFlowBoard> {
  return apiFetch(`/api/cftc/cta-flows?code=${encodeURIComponent(code)}`, {
    timeoutMs: 30_000,
  });
}

// ─── S&P valuation (multpl.com) ──────────────────────────────────

export interface SpValuationRow {
  key: string; label: string; unit: "x" | "pct"; why: string;
  value: number; mean: number | null; median: number | null;
  min?: number | null; max?: number | null;
  premium_to_median_pct: number | null;
  /** How RARE the reading is, always answering "how expensive" regardless of
   *  which direction the metric points. Absent when history is too short. */
  percentile?: number | null;
  percentile_recent?: number | null;
  recent_years?: number | null;
  n_months?: number | null;
  z_score?: number | null;
  sd?: number | null;
  asof_text?: string | null;
}

/** The only part of the valuation block that moves daily, and the only part
 *  with anything to say about a session.
 *
 *  The two halves are deliberately NOT joined by a causal claim. "A thin equity
 *  risk premium makes equities more rate-sensitive" was tested and rejected:
 *  ERP is earnings yield minus the 10-year, so sorting on it mostly sorts on
 *  the level of rates, and in one regression with HAC errors ERP falls to
 *  t = -0.94 while the rate level holds t = -7.66. */
export interface SpRateContext {
  earnings_yield_pct?: number;
  ten_year_pct?: number;
  /** Earnings yield minus the 10-year, in percentage points. */
  erp_pct?: number;
  /** Measured sensitivity of the index to rates: percent of SPX per basis
   *  point, over `beta_window_days`. */
  beta_pct_per_bp?: number;
  /** The readable form — what a 10bp move in the 10-year has mapped to. */
  move_per_10bp_pct?: number;
  beta_window_days?: number;
  /** Share of the index's daily variance that rates explain at all. A large
   *  beta with a low R² means the relationship is not currently carrying the
   *  tape. */
  rates_r2?: number;
  beta_pctile?: number;
  beta_pctile_years?: number;
  erp_pctile?: number;
  erp_n_months?: number;
  /** A negative ERP is NOT unusual — it was the norm from 1986 to 2003 — so
   *  the streak is the fact worth reading, not the sign. */
  erp_negative_share_pct?: number;
  erp_streak_months?: number;
  erp_streak_is_negative?: boolean;
}

export interface SpValuation {
  available: boolean; reason?: string; asof?: string; source?: string;
  median_premium_pct?: number | null;
  median_percentile?: number | null;
  median_percentile_recent?: number | null;
  recent_years?: number | null;
  distribution_note?: string;
  rate_context?: SpRateContext;
  rows?: SpValuationRow[];
  unavailable?: string[];
}

export async function fetchSpValuation(): Promise<SpValuation> {
  return apiFetch("/api/market/sp-valuation", { timeoutMs: 30_000 });
}

// ─── ES session briefing ─────────────────────────────────────────
// The top-of-page synthesis for someone trading the E-mini intraday. Bundled
// server-side on purpose: six separate round-trips would let the panels
// disagree about what time it is, and the whole point is one coherent read of
// the session.

export type EsImpact = "high" | "medium" | "low";

export interface EsSessionPhase {
  /** overnight | europe | premarket | rth_open | rth_midday | rth_close | post | closed */
  phase: string;
  label: string;
  note: string;
  /** Set when CME's hours likely differ from the normal session table. */
  holiday?: string | null;
  is_rth: boolean;
  now: string;
}

export interface EsScheduleItem {
  name: string;
  time_et: string;
  /** Absolute ET instant of the release. Countdowns derive from this, so they
   *  stay correct when the session's schedule is on the next calendar day. */
  when?: string;
  /** TIMING, assigned: is this a scheduled discontinuity to be at the screen
   *  for. Drives the countdown and `high_impact_today`. */
  impact: EsImpact;
  /** SIZING, measured. Same block as `CalendarEvent.measured` — see
   *  `EventMeasuredImpact`. Null on single-name earnings and on the handful of
   *  releases that were never in the study's universe, which is a different
   *  statement from "measured, and ordinary". */
  measured?: EventMeasuredImpact | null;
  note: string;
  /** Rule-derived date rather than a published one — can slip a day. */
  derived?: boolean;
  /** Agencies publish to the minute; companies do not. "After the close" is a
   *  half-hour window, so the countdown on these is a convention, not a promise. */
  time_approx?: boolean;
  minutes_away: number;
  status: "upcoming" | "released";
  before_open: boolean;
  /** "macro" for a scheduled release, "earnings" for a single-name report. */
  kind?: "macro" | "earnings";
  /** Earnings only — WHICH session this bears on. An after-the-bell report
   *  carries the report date but moves the NEXT session's gap, so this, not the
   *  date, is what says whether it can touch the range in front of you. */
  affects?: "this_session_gap" | "this_session_open" | "next_session_gap";
  affects_label?: string;
  symbol?: string;
  /** Market cap, the selection criterion. NOT an index weight — no constituent
   *  feed is available on this stack, and nothing here claims to price the
   *  name's contribution to the index. */
  market_cap?: number;
  /** Same window, below the size cut and not shown as rows of their own. Kept
   *  so a truncated list cannot be read as a complete one. */
  also_reporting?: string[] | null;
}

/** What SPX options charge for spanning the close-to-close segment that
 *  contains an after-the-bell event. Variance is additive, so the segment
 *  between two expiries prices at sqrt(next² − today²); dividing by the plain
 *  session gives `vs_session` — ordinary sessions of movement packed into one
 *  overnight. Absent when nothing lands after the bell. */
export interface EsEventPremium {
  available: boolean;
  reason?: string;
  session_expiry?: string;
  next_expiry?: string;
  this_session_straddle?: number;
  next_session_straddle?: number;
  segment_handles?: number;
  segment_pct?: number;
  /** The headline multiple. 1.0 is an ordinary night. NULL once the session is
   *  under way: the near straddle it divides by covers only the hours that are
   *  left, so the ratio would measure elapsed time rather than the event. */
  vs_session?: number | null;
  baseline_is_full_session?: boolean;
  vs_session_withheld?: string;
  /** "settled" means both straddles came from a closed book — the ratio
   *  survives that better than either level, but hedge the wording. */
  quote_source?: "live" | "settled";
  note?: string;
}

export interface EsLevel {
  key: string;
  label: string;
  group: string;
  note: string;
  value: number;
  /** Signed: last - level. Positive means price is above the level. */
  distance: number;
  distance_pct: number;
  side: "above" | "below";
  /** Distance as a share of the expected session range — the "can price even get
   *  there today" question. Qualitative by design: a real touch probability
   *  would need its own study. Absent when no expected move is available. */
  pct_of_expected_range?: number;
  reach?: "routine" | "reachable" | "a stretch" | "beyond a typical session";
}

/** Which frame the levels describe. `rth` = a cash session is open or done
 *  today; `premarket` = Globex running, no RTH yet, so the overnight range is
 *  the developing one and no session levels exist; `last_session` = market
 *  closed with nothing developing, describing the last completed session. */
export type EsLevelsMode = "rth" | "premarket" | "last_session";

export interface EsLevels {
  available: boolean;
  reason?: string;
  symbol: string;
  last: number;
  /** Timestamp of the QUOTE `last` came from — the snapshot's last trade when
   *  one is available and newer than the bar, otherwise the bar close. */
  asof: string;
  /** "last trade" | "5m bar close". A 5-minute bar trails the market by up to
   *  five minutes by construction, on top of the tier's delay. */
  quote_source?: string;
  quote_age_min?: number;
  /** True when the vendor labels its own feed delayed — the Starter futures
   *  tier does, measured at ~10 minutes. Naming it stops the card implying a
   *  real-time print, and it is the FLOOR on how fresh anything here can be. */
  quote_delayed?: boolean | null;
  bar_asof?: string;
  /** Age of the last 5-minute bar. Kept beside `quote_age_min` so the two
   *  cannot be confused — they differ by the bar's own granularity. */
  bar_age_min: number;
  /** The QUOTE lagging a session that is actually trading. False when the
   *  market is simply closed. */
  stale: boolean;
  mode: EsLevelsMode;
  session_date: string;
  prior_session_date: string | null;
  rth_open_bars: number;
  rth_complete: boolean;
  overnight_developing: boolean;
  overnight_bars: number;
  /** ES=F is a continuous front-month series: across a quarterly roll it steps
   *  by the roll spread, so prior-session levels can be from the expiring
   *  contract while `last` is the new one. Flagged, not silently corrected. */
  contract_roll_risk?: boolean;
  /** True when the value area shown is the prior session's, because the
   *  developing one doesn't yet have enough bars to mean anything. */
  profile_is_prior_session?: boolean;
  profile_sessions: number;
  /** Which session the volume profile covers — the prior one before the bell. */
  profile_session_date: string | null;
  nearest: EsLevel | null;
  levels: EsLevel[];
}

export interface EsNewsItem {
  source: string;
  title: string;
  url: string | null;
  published: string | null;
  /** 1 moves the index, 2 is market-wide colour, 3 is everything else.
   *
   *  THE LIST IS ORDERED BY THIS, then by recency within it — deliberately, so
   *  a colour piece from an hour ago cannot outrank an FOMC line from
   *  yesterday. It was computed and never rendered, so the only ordering cue on
   *  screen was the age, and a correctly-ordered list read as an unsorted one:
   *  `3h · 4h · 9h · 1d · 2d · 3d · 1h · 5h · 4d`. */
  tier?: number;
  /** "since last close" | "earlier" — bucketed against the prior cash close, so
   *  a Monday read treats the whole weekend as one bucket rather than as three
   *  degrees of old. */
  age?: string | null;
  hours_ago?: number | null;
}

/** One estimate of the day's range. `sigma_handles` is a one-sigma
 *  close-to-close move; `range_handles` is the expected high-low, which is
 *  ~1.6x larger. They are not interchangeable — see src/es_expected_move.py. */
export interface EsMoveEstimate {
  source: string;
  sigma_handles: number;
  range_handles: number;
  pct: number | null;
  detail: string;
  forward_looking: boolean;
  /** "settled" means the market was shut and the straddle is last settlement. */
  quote_source?: string;
}

export interface EsExpectedMove {
  available: boolean;
  reason?: string;
  spx_spot?: number | null;
  headline?: EsMoveEstimate;
  expected_handles?: number;
  expected_range?: number;
  upper?: number | null;
  lower?: number | null;
  estimates?: EsMoveEstimate[];
  consumed?: { range: number; expected_range: number; pct: number; note?: string } | null;
  vol_regime?: {
    implied: number; realized: number; ratio: number; label: string; note: string;
  } | null;
  overnight?: { range: number; pct_of_expected: number } | null;
}

export interface EsGamma {
  available: boolean;
  reason?: string;
  spx_spot?: number;
  /** When that SPX print is from. Outside cash hours it is a completed
   *  session, not a live quote. */
  spx_spot_asof?: string | null;
  spx_cash_open?: boolean;
  /** Where the book was actually evaluated — the cash print while cash trades,
   *  `es_last - basis` otherwise. Every above/below question (both walls, the
   *  nearest flip crossing, the sign of gamma at price) is answered here.
   *  Asking them at a frozen close can return a wall "above" that price has
   *  already traded through. `spx_spot_effective + es_basis == es_last`. */
  spx_spot_effective?: number;
  spot_source?: "cash" | "es_implied";
  /** SPX strike + this = the ES level. Measured from two SIMULTANEOUS quotes:
   *  live during RTH, otherwise carried from the last cash close. Using
   *  `es_last - spx_spot` outside RTH books the whole move since the bell as
   *  basis — on the 2026-08-02 reopen that was 26.75 handles, which put the
   *  call wall 40 handles away when price was 13 handles under it. */
  es_basis?: number | null;
  es_basis_asof?: string | null;
  /** False means the basis is carried from the last simultaneous pair, so the
   *  ES levels map the SPX strikes at THAT relationship rather than a live one.
   *  Still the right ladder, just not fresh. */
  es_basis_is_live?: boolean;
  regime?: "long" | "short";
  regime_note?: string;
  total_gex?: number;
  zero_dte_gex?: number;
  zero_dte_share?: number | null;
  /** Level where aggregate dealer gamma crosses zero — the regime boundary. */
  flip_spx?: number | null;
  flip_es?: number | null;
  distance_to_flip?: number | null;
  above_flip?: boolean;
  call_wall_es?: number | null;
  put_wall_es?: number | null;
  top_strikes?: Array<{ strike_spx: number; strike_es: number | null; gex: number; side: string }>;
  profile?: Array<{ spot: number; gex: number }>;
  expiries?: string[];
}

export interface EsIntraday {
  available: boolean;
  opening_range?: {
    available: boolean;
    or5?: { high: number; low: number; range: number; complete: boolean };
    or15?: { high: number; low: number; range: number; complete: boolean };
    or30?: { high: number; low: number; range: number; complete: boolean };
    ib?: {
      high: number; low: number; range: number; complete: boolean;
      extension_up: number; extension_down: number; extended: boolean;
    };
  };
  day_type?: {
    available: boolean; label?: string; note?: string;
    ib_multiple?: number; close_position?: number;
    extension_direction?: string | null; range?: number;
  };
  relative_volume?: {
    available: boolean; ratio?: number; verdict?: string; note?: string;
    elapsed_minutes?: number; sessions_compared?: number;
  };
  overnight_inventory?: {
    available: boolean; high?: number; low?: number; range?: number;
    position_in_range?: number | null; took_prior_high?: boolean;
    took_prior_low?: boolean; skew?: string; note?: string;
  };
  naked_pocs?: Array<{ date: string; value: number; distance: number; side: string; sessions_ago: number }>;
  unfilled_gaps?: Array<{ date: string; from: number; to: number; size: number; direction: string; distance: number }>;
  cross_asset?: { available: boolean; rows?: Array<{ symbol: string; label: string; why: string; last: number; change_pct: number }> };
}

export interface EsBaseRates {
  available: boolean;
  source?: string;
  /** What the DAILY statistics here were measured on. Distinct from
   *  `path.instrument` (SPY 5-minute bars, a shorter window) and from the ES
   *  overnight study elsewhere on the card. Three instruments, three windows,
   *  all rendered as percentages; each must say which it is. */
  instrument?: string;
  window_years?: number;
  sessions?: number;
  from?: string;
  to?: string;
  gaps?: {
    available: boolean;
    buckets?: Array<{ bucket: string; n: number; fill_rate: number; up_fill_rate: number | null; down_fill_rate: number | null; close_above_open_rate: number }>;
    /** `fill_rate` POOLS up and down gaps — the split is a null in every bucket
     *  on both measured windows, so quoting the direction-specific rate would
     *  halve the sample for nothing. `up_fill_rate`/`down_fill_rate` remain in
     *  `buckets` as measurements, but nothing is conditioned on them. */
    today?: { gap_pct: number; direction: string; bucket: string; fill_rate: number; n: number; note: string } | null;
    direction_note?: string;
  };
  /** The same question asked of the session actually running: how often a gap
   *  this size, STILL OPEN at this hour and this far from the prior close, ends
   *  up filling. A different study from `gaps` — SPY 5-minute bars over five
   *  years, not ^GSPC dailies over ten — so it carries its own window and must
   *  never be rendered as though it were the same series. */
  gap_fill_live?: {
    available: boolean;
    reason?: string;
    state?: "open" | "filled";
    bucket?: string;
    direction?: string;
    gap_pct?: number;
    unconditional?: number;
    unconditional_n?: number;
    as_of?: string | null;
    minutes_in?: number;
    fill_rate?: number;
    n?: number;
    conditioned_on?: "clock" | "clock and distance";
    distance_r?: number | null;
    distance?: "retraced" | "holding" | null;
    pct_closed?: number | null;
    note?: string;
    instrument?: string;
    window_years?: number;
    sessions?: number;
    from?: string;
    to?: string;
    curve?: Array<{ time: string; minutes: number; fill_rate: number; n: number }>;
  } | null;
  range?: {
    available: boolean; n?: number;
    median_range_pct?: number; median_range_handles?: number | null;
    p25_handles?: number | null; p75_handles?: number | null; p90_handles?: number | null;
    took_prior_high_pct?: number; took_prior_low_pct?: number;
    took_both_pct?: number; took_neither_pct?: number; trend_day_pct?: number;
  };
  events?: {
    available: boolean; baseline_range_pct?: number; note?: string;
    events?: Array<{ name: string; n: number; median_range_pct: number; range_vs_normal: number | null; median_abs_move_pct: number; move_vs_normal: number | null; up_close_rate: number }>;
  };
  /** WHEN the session gets where it is going, measured on hourly cash bars.
   *  Its own, much shorter window than the daily statistics above — it carries
   *  its own `sessions`/`from`/`to` and must not be labelled with theirs. */
  path?: {
    available: boolean;
    reason?: string;
    source?: string;
    /** SPY 5-minute bars — NOT the daily index study above, and NOT the ES
     *  overnight study elsewhere on the card. Three instruments, three windows,
     *  all rendered as percentages; each must say which it is. */
    instrument?: string;
    instrument_note?: string;
    sessions?: number;
    from?: string;
    to?: string;
    slots?: string[];
    /** `minutes` is 30 for the final bucket and 60 for the rest — its share of
     *  the extremes is not comparable to the others minute for minute. */
    extremes?: Array<{ slot: string; minutes: number; high_pct: number; low_pct: number }>;
    progress?: Array<{ slot: string; range_complete_pct: number; range_complete_p25: number; high_in_pct: number; low_in_pct: number; both_in_pct: number }>;
    initial_balance?: {
      definition: string; share_of_day_range_pct: number;
      one_sided_pct: number; both_sides_pct: number; inside_pct: number;
      held_high_of_day_pct: number; held_low_of_day_pct: number; note: string;
    };
    ib_breaks?: Array<{ buffer_pct_of_ib: number; up_n: number; up_held_pct: number; down_n: number; down_held_pct: number; both_sides_pct: number; clean_up_n: number; clean_up_held_pct: number }>;
    ib_width?: Array<{ band: string; n: number; one_sided_pct: number; both_sides_pct: number; day_range_x_ib: number }>;
    close_location?: { upper_third_pct: number; middle_third_pct: number; lower_third_pct: number };
    /** Present ONLY during a live cash session — null on weekends, holidays and
     *  outside 09:30-16:00, because "30% of the range is still to come" is a
     *  lie about a day that is not trading. */
    live?: { slot: string; elapsed_label: string; range_complete_pct: number; high_in_pct: number; low_in_pct: number; note: string } | null;
    caveats?: string[];
  } | null;
}

/** How many stocks are going with the index. Reconstructed on a liquid US
 *  universe, NOT NYSE-listed issues, so it will not tie out against a terminal's
 *  A/D or TRIN — the direction and the extremes carry, the counts are ours. */
export interface EsBreadth {
  available: boolean;
  reason?: string;
  /** True while the session is trading; false means these are the last
   *  completed session's counts, named in `session`. */
  live?: boolean;
  session?: string | null;
  asof_note?: string;
  /** `n` is the names that have actually traded; `eligible_n` (live only) is how
   *  many passed the liquidity filter, so the gap is participation. */
  universe?: { n: number; eligible_n?: number | null; definition: string; note: string };
  advancers?: number;
  decliners?: number;
  unchanged?: number;
  net_advancers?: number;
  net_advancers_pct?: number | null;
  ad_ratio?: number | null;
  up_volume?: number;
  down_volume?: number;
  up_volume_pct?: number | null;
  volume_ratio?: number | null;
  trin?: number | null;
  trin_band?: { label: string; why: string } | null;
  /** Share of the universe above its own 50- and 200-day average.
   *
   *  A DIFFERENT QUESTION from every other field here. Those ask whether TODAY
   *  was broad; this asks whether the market is broadly in an uptrend, and an
   *  index can close green with most of its names below their own 200-day.
   *  Null when the 200-session walk has not completed or failed — absent rather
   *  than degrading the counts beside it.
   *
   *  `history` carries the percentile against its own recorded sessions, null
   *  until 60 have accumulated. A level with no reference set is a fact about
   *  nothing, and backfilling would cost 200 grouped fetches per point. */
  trend?: {
    available: boolean;
    asof?: string;
    sessions_used?: number;
    from?: string;
    universe?: { n: number; note: string };
    windows?: Record<string, {
      above: number; below: number; n: number;
      pct_above: number | null; excluded_short_history: number;
    }>;
    pct_above_50dma?: number | null;
    pct_above_200dma?: number | null;
    history?: Record<string, { pctile: number | null; n_history: number }>;
  } | null;
  equal_vs_cap?: {
    available: boolean;
    equal_weight?: number; cap_weight?: number; spread_pct?: number;
    label?: string; note?: string;
    /** Which feed answered — the same Polygon snapshot the counts came from
     *  while the market trades, yfinance when it is shut or the names are
     *  missing. Surfaced so a source change is visible rather than silent. */
    source?: string;
  };
  divergence?: { label: string; note: string } | null;
  /** Always unavailable — NYSE TICK needs a classified tick stream no wired
   *  source provides. The field exists to say so rather than to be filled in. */
  tick?: { available: boolean; reason: string };
  reconstruction?: string;
}

/** The last daily bar as six continuous numbers, plus what it says about
 *  TOMORROW'S RANGE. Measured on 434,624 bars: geometry forecasts range with a
 *  rank IC of 0.158 (t=75) and direction with -0.016 (t=-5.5). Size off the
 *  range; the directional tilt is a tiebreaker, never a signal. */
export interface EsCandleContext {
  available: boolean;
  reason?: string;
  symbol?: string;
  asof?: string;
  close?: number;
  bar?: {
    range: number; range_atr: number; range_label: string;
    body_atr: number; upper_wick_atr: number; lower_wick_atr: number;
    close_location: number; close_location_label: string;
    volume_vs_20d?: number | null;
  };
  tomorrow_range?: {
    n: number; atr: number;
    p25_atr: number; p50_atr: number; p75_atr: number; p90_atr: number;
    p25: number; p50: number; p75: number; p90: number;
    prob_exceeds_1_atr: number; note: string;
  } | null;
  direction_tilt?: {
    n: number; next_up_pct: number; median_next_ret_pct: number;
    t: number; p: number; ic?: number | null; ic_t?: number | null; note: string;
  } | null;
  /** All five close-location buckets, so the card can show the effect is
   *  MONOTONIC — the only reason to believe a 10bp edge. Served from the study
   *  rather than hardcoded client-side, which would drift on regeneration. */
  close_location_curve?: Array<{
    bucket: number; n: number; next_up_pct: number;
    median_next_ret_pct: number; is_today: boolean;
  }>;
  /** Options-implied range against the empirical one — two estimates of the same
   *  quantity from unrelated inputs. Present only when both exist. */
  vs_implied?: {
    implied_range: number; empirical_p50: number; ratio: number;
    gap: number; gap_atr?: number | null;
    label: string; note: string; caveat: string;
  } | null;
  /** What the study's range forecast means for an ES session specifically.
   *  The study measures the CASH INDEX and reports a bare point figure; ES is a
   *  future on that index, so the basis is a level offset and not a scale
   *  factor — index points and ES points are the same size. Null until the
   *  forecast exists. */
  es_read?: {
    reads?: { label: string; value: string; note: string; caveat?: string }[];
  } | null;
  study?: Record<string, unknown>;
  disclaimer?: string;
}

/** How the character read has actually done, replayed over the full history.
 *  Describes the MODULE, not today. */
export interface EsTrackRecord {
  available: boolean;
  reason?: string;
  sessions?: number;
  mark?: string;
  base_wide_pct?: number;
  base_up_pct?: number;
  median_abs_err_pct?: number;
  buckets?: Array<{
    band: string; n: number; said_x: number; actual_x: number;
    delivered_wide_pct: number; median_abs_err_pct: number;
    median_err_pct: number; closed_up_pct: number;
  }>;
  headline?: string | null;
  /** Calibration is not uniform — the compressed end runs ~30% low. */
  bias_note?: string | null;
  direction_note?: string;
  method?: string;
}

export async function fetchEsTrackRecord(): Promise<EsTrackRecord> {
  return apiFetch("/api/market/es-track-record");
}

/** The conditions gate scored against completed sessions.
 *
 *  Separate from `EsTrackRecord` because it is a different KIND of record. The
 *  character read is a pure function of price, so its whole history could be
 *  replayed the day it shipped. The gate reads dealer gamma and an
 *  options-implied range, neither of which is retained, so it can only be
 *  scored forward from the day logging started — and until 30 sessions have
 *  accumulated it reports `available: false` with the count rather than
 *  quoting a number computed on five days.
 *
 *  The refusing state is worth rendering. The gate LEADS the ES card, on the
 *  argument that standing aside is the decision that saves the most money, and
 *  it was the one module on the page with no visible score at all. */
export interface EsGateTrackRecord {
  available: boolean;
  reason?: string;
  logging_since?: string | null;
  /** Completed sessions logged so far. */
  sessions?: number;
  /** How many more are needed before this reports. */
  needed?: number;
  snapshots?: number;
  scored_sessions?: number;
  buckets?: Array<{
    verdict: string;
    /** Distinct sessions — the sample size. */
    n_sessions: number;
    /** 30-minute marks the verdict stood for. Persistence, not sample size. */
    n_marks: number;
    median_range_x: number;
    wide_pct: number;
    closed_up_pct: number;
  }>;
  base_wide_pct?: number;
  base_up_pct?: number;
  caveat?: string;
}

export async function fetchEsGateTrackRecord(): Promise<EsGateTrackRecord> {
  return apiFetch("/api/market/es-track-record-gate", { timeoutMs: 30_000 });
}

/** One historical session that resembled today, and what it went on to do. */
export interface EsAnalogSession {
  date: string;
  range_mult: number | null;
  range_pct: number | null;
  ret_oc: number | null;
  up: boolean | null;
  close_pos: number | null;
  trendiness: number | null;
  /** When the session's own high / low printed. */
  hi_slot?: string | null;
  lo_slot?: string | null;
  max_up: number | null;
  max_dn: number | null;
}

/** Similar-session matching, the "similar day" method from power trading.
 *
 *  `today.implied_range_mult` is VALIDATED out of sample (8.4% better than the
 *  unconditional forecast, p=0.0005, 1.87x lift on wide-day calls).
 *  `today.share_up` and everything under `next_session` are NOT — direction is
 *  a measured null and the next-session horizon came in at p=0.051 with the
 *  sign flipping between halves. The split is carried so the card can print it
 *  as the null it is, rather than omitting it and letting a reader assume it
 *  was never checked. */
export interface EsAnalogs {
  available: boolean;
  reason?: string;
  session_date?: string;
  n_history?: number;
  k_shown?: number;
  k_scored?: number;
  features?: string[];
  analogs?: (EsAnalogSession & { next: EsAnalogSession | null })[];
  /** "pre-open" or "intraday blend" — the blend is only engaged at the slots it
   *  was measured to help at (10:30, 11:30). After 11:30 it measured worse than
   *  path-implied alone, so the module falls back rather than blending anyway. */
  mode?: string;
  slot?: string | null;
  today?: {
    implied_range_mult: number | null;
    /** The two inputs behind `implied_range_mult` when blending, so the card can
     *  show what each half contributed rather than one fused number. */
    analog_only?: number | null;
    path_implied?: number | null;
    calls_wide: boolean;
    /** The analogs' spread. Ten agreeing on 1.1x and ten spanning 0.6-2.1x are
     *  different claims and a lone median cannot tell them apart. */
    p25?: number | null;
    p75?: number | null;
    share_up: number | null;
    median_distance?: number | null;
  };
  next_session?: {
    implied_range_mult: number | null;
    validated: boolean;
    note: string;
  };
  accuracy?: Record<string, number>;
  caveat?: string;
}

export async function fetchEsAnalogs(): Promise<EsAnalogs> {
  return apiFetch("/api/market/es-analogs", { timeoutMs: 45_000 });
}

// ── TSMOM book state ─────────────────────────────────────────────────
/** Bookkeeping for the 12-month time-series momentum system, not a signal.
 *
 *  Every other block on the home page describes the market. This one describes
 *  a position book already committed to: what the last month-end set, what the
 *  rule would say if rebalanced today, and when those get reconciled. The
 *  distinction matters — the rule rebalances MONTHLY and holds in between, and
 *  trading the daily drift is a different (worse) system: daily 0.62 vs
 *  monthly 0.72-0.74. */
export interface TsmomRow {
  ticker: string;
  asset_class: string;
  return_12m_pct: number;
  ann_vol_pct: number | null;
  side: "long" | "short" | "flat";
  weight_pct: number;
  /** |12m return| / annualised vol. The median split on this beat a de Prado
   *  meta-labelling classifier built for the same job (0.62 -> 0.68). */
  trend_strength: number | null;
  above_strength_median: boolean | null;
}

export interface TsmomExposure {
  gross_long_pct: number;
  gross_short_pct: number;
  net_pct: number;
  total_gross_pct: number;
  n_long: number;
  n_short: number;
  n_flat: number;
}

export interface TsmomBook {
  available: boolean;
  reason?: string;
  asof?: string;
  generated_utc?: string;
  n_markets?: number;
  /** Portfolio vol scaler today, and the one in force when the book was set.
   *  They differ by the month's vol drift; quoting today's on last month's
   *  weights would describe a book nobody holds. */
  portfolio_scale?: number;
  portfolio_scale_held?: number;
  portfolio_scale_capped?: boolean;
  last_rebalance?: string | null;
  held?: { rows: TsmomRow[]; exposure: TsmomExposure };
  live?: { rows: TsmomRow[]; exposure: TsmomExposure };
  /** Markets whose signal has changed sign since the book was set. Carries
   *  `trend_strength` because a flip on a 12m return of +0.1% is a coin landing
   *  on its edge, and reduced to "TLT: short → long" it looks like conviction. */
  flips_since_rebalance?: Array<{
    ticker: string; from: string; to: string;
    return_12m_pct: number; trend_strength: number | null;
  }>;
  next_rebalance?: {
    estimated_date: string; sessions_away: number;
    sessions_this_month: number; note: string;
  };
  trend_strength_median?: number | null;
  research?: {
    sharpe_backtest: number;
    sharpe_posterior: number;
    sharpe_posterior_ci95: [number, number];
    ann_return_pct: number;
    ann_vol_pct: number;
    max_drawdown_pct: number;
    spy_sharpe: number;
    spy_max_drawdown_pct: number;
    turnover_per_year: number;
    capital_floor_usd: number;
    worst_episode: string;
    source: string;
    eras_positive: string;
  };
  rule?: Record<string, unknown>;
  caveats?: string[];
}

export async function fetchTsmomBook(): Promise<TsmomBook> {
  return apiFetch("/api/market/tsmom-book", { timeoutMs: 45_000 });
}

/** Internal contradictions on the card. Claims about THIS PAGE, never about the
 *  market — whether two blocks can both be right, not which one is. */
export interface EsCardAudit {
  available: boolean;
  reason?: string;
  findings?: Array<{
    severity: "high" | "medium" | "low";
    where: string;
    finding: string;
    /** "rule" is deterministic; "model" is a reading of the payload and can be wrong. */
    source: "rule" | "model";
  }>;
  n_rule?: number;
  n_model?: number;
  model?: string | null;
  /** True is the common case and a success, not an empty result. */
  clean?: boolean;
  note?: string;
  caveat?: string;
}

export async function fetchEsCardAudit(): Promise<EsCardAudit> {
  return apiFetch("/api/market/es-card-audit");
}

/** Levels price cannot tell apart are ONE reference, not several. Counting rows
 *  on the ladder counts confirmations that are not there. */
export interface EsLevelClusters {
  available: boolean;
  reason?: string;
  clusters?: Array<{
    low: number; high: number; center: number; span: number; n: number;
    members: Array<{ key: string; label: string; value: number }>;
    families: string[];
    /** Same-family co-location is arithmetic; cross-method is several
     *  mechanisms landing on one price. Only the latter is confluence. */
    cross_method: boolean;
    note: string;
  }>;
  n_clusters?: number;
  n_cross_method?: number;
  tolerance?: number;
  tolerance_basis?: string;
  note?: string | null;
  caveat?: string;
}

/** The macro setup: named drivers with their MEASURED range lift, the direction
 *  stated as an explicit null, and the transmission chain checked against the
 *  tape. The mechanisms explain what is moving; they never forecast direction,
 *  which the measured tests do not support. */
export interface EsMacroSetup {
  available: boolean;
  reason?: string;
  character?: string;
  note?: string;
  n_drivers?: number;
  drivers?: Array<{
    key: string;
    label: string;
    symbol: string;
    mechanism: string;
    z: number;
    day_pct: number;
    median_x: number;
    wide_pct: number;
    n: number;
    p_size: number;
    size_significant: boolean;
    /** Each implication of the mechanism, measured. "flat" is a failure of the
     *  chain too — it predicts a move, and no move does not corroborate it. */
    chain: Array<{ symbol: string; expected: string; actual_pct: number; state: string }>;
    broken_links: Array<{ symbol: string; expected: string; actual_pct: number; state: string }>;
  }>;
  size?: {
    source: string; median_x: number; wide_pct: number; base_wide_pct: number;
    lift: number; n: number; p: number; significant: boolean;
    note: string; combination_note: string;
  };
  direction?: {
    base_up_pct: number;
    tests: Array<{ label: string; up_pct: number; ci: [number, number]; p: number; n: number }>;
    verdict: string;
  };
  broken_links?: Array<{ symbol: string; expected: string; actual_pct: number; state: string }>;
  chain_note?: string | null;
  caveat?: string;
}

/** What actually moved the tape, ranked from the TAPE and annotated from the
 *  feed — never the other way round. What sits beside a move is coincidence in
 *  the clock, not demonstrated causation. */
export interface EsAttribution {
  available: boolean;
  reason?: string;
  headline?: string | null;
  moves?: Array<{
    start: string;
    end: string;
    range: number;
    net: number;
    x_normal_bar: number | null;
    bars: number;
    is_open?: boolean;
    event: { name: string; impact: string; at: string } | null;
    headlines: Array<{ title: string; source: string; at: string }>;
    attributed: boolean;
  }>;
  n_moves?: number;
  /** Moves with nothing in either feed. A session whose expansions carry no
   *  catalyst is a different kind of day — information, not a gap. */
  n_unattributed?: number;
  event_impacts?: Array<{
    name: string; at: string; impact: string;
    range: number; net: number;
    /** x a normal 30-minute window AT THAT HOUR (deseasonalised 2026-08-30). */
    x_normal_window: number | null;
    x_normal_window_flat?: number | null;
    tod_factor?: number | null;
  }>;
  median_bar?: number;
  median_30min?: number | null;
  unattributed_note?: string | null;
  caveat?: string;
}

/** Is this session ordinary, and by how much? The only range estimator on the
 *  card not fixed at the open — measured from the range actually delivered, so
 *  it can see an unscheduled event while that event is still running. */
export interface EsRegime {
  available: boolean;
  /** "wide" | "normal" | "compressed" | "possibly wide" | "unknown" */
  character: string;
  /** Which instrument produced the headline: "path" | "har" | "dispersion" | null.
   *  Precedence is path, then har, then dispersion — see `session_character`. */
  basis: string | null;
  /** HAR-RV pre-open prior, added 2026-08-30. Covers the window `path_implied`
   *  cannot reach — the pre-open to the first bucket close. Built only from
   *  sessions that have already closed, so it cannot see today's catalyst. */
  har?: {
    available: boolean;
    reason?: string;
    multiplier?: number | null;
    implied_range?: number | null;
    normal_range?: number | null;
    character?: string;
    sessions?: number;
    /** Last COMPLETED session in the panel; the forecast is for the one after it. */
    asof?: string | null;
    calibration?: number;
    calibration_theory?: number;
    persistence?: number;
    /** 33.7% measured on this module's own input, against 40.0% for the
     *  trailing-median benchmark it replaces. */
    oos_mae_pct?: number;
    note?: string;
    caveat?: string;
    method?: string;
  } | null;
  /** Only present once BOTH instruments have spoken: how far the session has
   *  run against what last night's volatility implied. */
  divergence?: {
    path_multiplier?: number | null;
    har_multiplier?: number | null;
    ratio?: number | null;
    note?: string;
  } | null;
  path_implied: {
    available: boolean;
    reason?: string;
    slot?: string;
    implied_range?: number;
    range_so_far?: number;
    normal_range?: number | null;
    multiplier?: number | null;
    note?: string;
    typical_pct_covered?: number;
    /** Measured out-of-sample error at this slot, so the reader can weigh the
     *  estimate rather than trust it. */
    oos_mae_pct?: number;
    method?: string;
  };
  dispersion: {
    available: boolean;
    count?: number;
    sum_z?: number;
    band?: string;
    assets?: Array<{ symbol: string; label: string; z: number; pct: number }>;
    outliers?: Array<{ symbol: string; label: string; z: number; pct: number }>;
    median_multiplier?: number;
    p_wide_pct?: number;
    base_rate_pct?: number;
    lift?: number;
    sample?: number;
    note?: string;
    caveat?: string;
    method?: string;
  } | null;
  disclaimer?: string;
}

/** What comparable sessions did between this state and their close. Addressed
 *  to somebody already positioned, which no other block on the card is. */
export interface EsRestOfSession {
  available: boolean;
  reason?: string;
  mark?: string;
  band?: string;
  regime?: string;
  /** False means the exact (mark, band, regime) cell was too thin and the other
   *  regime at the same mark was used instead. Surfaced, never silent. */
  exact_cell?: boolean;
  n?: number;
  sessions?: number;
  p_new_high?: number;
  p_new_low?: number;
  /** Sits near 55% from every position band — read it as a coin flip. */
  p_close_above?: number;
  to_close?: {
    p25: number; median: number; p75: number;
    p25_units: number | null; median_units: number | null; p75_units: number | null;
  };
  median_max_up?: number;
  median_max_up_units?: number | null;
  median_max_dn?: number;
  median_max_dn_units?: number | null;
  note?: string;
  caveat?: string;
  method?: string;
}

/** Whether the session suits intraday trading at all — conditions, never
 *  direction. Each reason states the points it contributed. */
export interface EsConditions {
  available: boolean;
  score: number | null;
  verdict: string;
  note: string;
  /** `surface` marks a factor that scored 0 for a stated REASON rather than
   *  because it agreed — the card renders those as text, not a tooltip. */
  reasons: Array<{ factor: string; effect: number; why: string; surface?: boolean }>;
  /** How much of the gate was readable. A -2 from two factors and a -2 from six
   *  are not the same statement. */
  factors_scored?: number | null;
  factors_zero_effect?: number | null;
  disclaimer?: string;
}

/** One opening-position bucket of the overnight study. */
export interface EsOvernightBand {
  band: string;
  n: number;
  breaks_on_high_pct: number;
  breaks_on_low_pct: number;
  both_pct: number;
  median_rth_range: number;
}

export interface EsOvernight {
  available: boolean;
  reason?: string;
  /** Stated rather than inferred: the path base rates sitting beside this are
   *  SPY over five years, these are ES over two. A reader glancing between them
   *  would otherwise assume one instrument. */
  instrument?: string;
  sessions?: number;
  from?: string;
  to?: string;
  /** False when contracts were missing from the study window. A quarter of
   *  absent sessions changes nothing about how the tables LOOK. */
  complete?: boolean;
  contracts_missing?: string[];
  range_survival?: {
    one_sided_pct: number; both_sides_pct: number; held_inside_pct: number; note?: string;
  };
  by_open_position?: EsOvernightBand[];
  median_on_range?: number;
  median_rth_range?: number;
  /** Median share of the full 23-hour range already made before the bell. */
  overnight_share_of_full_range_pct?: number;
  notes?: string[];
  /** Today's read. Null when the live session could not be read — the historical
   *  study stands alone and is NOT blanked by a missing live frame. */
  live?: {
    contract?: string | null;
    session_date?: string;
    phase?: string;
    overnight_high: number; overnight_low: number;
    overnight_range: number; overnight_range_pct: number;
    last: number;
    /** The cash OPEN, which is what the base rates are conditioned on. Null
     *  pre-open, where `open_is_estimated` is true and `last` stands in. */
    open: number | null;
    open_is_estimated: boolean;
    position_in_range_pct: number;
    band: string;
    to_on_high: number; to_on_low: number;
    /** Already resolved — a fact, not a forecast. The matching side is dropped
     *  from `expected` rather than restated as a probability. */
    broke_on_high: boolean; broke_on_low: boolean;
    expected?: {
      n: number;
      breaks_on_high_pct?: number;
      breaks_on_low_pct?: number;
      /** Present when the frequencies are withheld because the overnight range
       *  is still forming. They describe where the cash session OPENS inside a
       *  FINISHED range, so they do not apply until 09:30. */
      withheld?: string;
      note?: string;
    } | null;
    /** Share of the 18:00–09:30 window that has elapsed. */
    overnight_elapsed_pct?: number | null;
    /** True once the cash session has opened and the range is final. Every
     *  conditioned table here is keyed on the finished range. */
    overnight_complete?: boolean;
    /** Null until the overnight completes — it is bucketed on the range SIZE,
     *  and a half-built range lands in the wrong bucket. Measured 2026-08-02:
     *  15.5 pts three hours in against a 43.0 median for a finished one, which
     *  read as the smallest bucket and forecast a quiet session. */
    rth_range_expectation?: { p25: number; median: number; p75: number; n: number } | null;
  } | null;
}

export interface EsBrief {
  available: boolean;
  asof?: string;
  session?: EsSessionPhase;
  /** The RTH date the current Globex session leads into — after 18:00 ET this
   *  is the next weekday, and the schedule describes that session, not today's. */
  session_day?: string;
  schedule_is_today?: boolean;
  schedule?: EsScheduleItem[];
  next_event?: EsScheduleItem | null;
  /** Lands after this session's close — megacap earnings, in practice. Kept out
   *  of `next_event` on purpose: these size the risk of HOLDING, not the range
   *  of the session in front of you. */
  after_close?: EsScheduleItem[];
  event_premium?: EsEventPremium | null;
  high_impact_today?: EsScheduleItem[];
  news?: EsNewsItem[];
  /** Synthesis of the headline set. Keyed on the headlines themselves, so it is
   *  absent until the feeds return something and null when the model call
   *  fails — the headline list below it stands on its own either way. */
  news_digest?: { text?: string; model?: string; cached?: boolean; n_headlines?: number } | null;
  levels?: EsLevels | null;
  /** Why `levels` is null, when it is. A feed outage and an empty session read
   *  identically on the card without this. */
  levels_reason?: string | null;
  regime?: EsRegime | null;
  /** How STRAIGHT the session has been, as against how big — the axis
   *  `regime` is blind to. Descriptive only: its forward correlation is a
   *  measured null and ships in `forward` so the card can say so. */
  chop_trend?: EsChopTrend | null;
  rest_of_session?: EsRestOfSession | null;
  attribution?: EsAttribution | null;
  macro_setup?: EsMacroSetup | null;
  level_clusters?: EsLevelClusters | null;
  cta?: {
    bias_1w?: CtaBias;
    current_exposure?: number;
    pivots?: Partial<Record<"short_term" | "medium_term" | "long_term", CtaPivot>>;
    terminal_1w?: Record<string, CtaScenario>;
  } | null;
  macro?: {
    net_label?: string;
    net_score?: number;
    counts?: { supportive: number; neutral: number; headwind: number };
    biggest_headwind?: string;
    biggest_support?: string;
    /** Coverage. A verdict from two surviving factors looks identical to one
     *  from twelve unless the card says otherwise. */
    factors_reporting?: number;
    factors_unavailable?: number;
  } | null;
  expected_move?: EsExpectedMove | null;
  gamma?: EsGamma | null;
  intraday?: EsIntraday | null;
  base_rates?: EsBaseRates | null;
  breadth?: EsBreadth | null;
  candles?: EsCandleContext | null;
  /** Globex range against what the cash session has historically done with it.
   *  Measured on real ES, front contract by volume — NOT the same instrument or
   *  window as `base_rates`, which is SPY over five years. Both are labelled. */
  overnight?: EsOvernight | null;
  conditions?: EsConditions | null;
  /** Cash-open gap vs the prior close, in percent. Before the bell this is the
   *  gap as it currently stands, measured from the live price. */
  gap_pct?: number | null;
  /** Which upstreams failed — the card degrades per-block rather than 500ing. */
  degraded?: string[];
}

export async function fetchEsChopRecord(): Promise<EsChopRecord> {
  return apiFetch("/api/market/es-chop-record", { timeoutMs: 30_000 });
}

export async function fetchEsBrief(): Promise<EsBrief> {
  return apiFetch("/api/market/es-brief", { timeoutMs: 30_000 });
}

// ─── Sector Relative Rotation Graph ──────────────────────────────

export type RrgQuadrant = "leading" | "weakening" | "lagging" | "improving";

export interface RrgPoint { date: string; ratio: number; mom: number }

export interface RrgRow {
  symbol: string;
  label: string;
  ratio: number;
  mom: number;
  quadrant: RrgQuadrant;
  prev_quadrant: RrgQuadrant;
  /** Degrees, 0 = due east. Null when the dot has not meaningfully moved —
   *  atan2(0,0) is 0.0, which would claim "due east" for no movement. */
  heading: number | null;
  tail: RrgPoint[];
}

/** The environment that accompanied a band, averaged over the weeks that shared
 *  it. CONTEMPORANEOUS — these co-occurred with the band, they were not
 *  forecast by it. */
export interface RrgContext {
  n: number;
  realized_vol: number | null;
  avg_sector_corr: number | null;
  trend_vs_50dma: number | null;
}

/** One regime measure: its current value, where that sits in its own history,
 *  and what the environment has looked like at that level. `pctile` is null
 *  when there is too little history to place it — never 50 ("middling"). */
export interface RrgMeasure {
  value: number;
  pctile: number | null;
  band: string | null;
  n_history: number;
  context: RrgContext | null;
}


/** Session shape — chop versus trend, on Kaufman efficiency over 5-minute closes.
 *
 *  `efficiency` is NOT comparable across clock times: the ratio falls
 *  mechanically with bar count, so every reading is scored against the
 *  historical distribution at the SAME `mark`, which is what `pctile` carries.
 *  `label` is one of "confident trendy" / "likely trendy" / "mixed" /
 *  "likely choppy" / "confident choppy", and the confidence half of it is the
 *  measured frequency in `p_finish_*_pct`, never a word chosen by feel. */
/** Walk-forward scorecard for the chop/trend read. Every session is scored
 *  against a fit built only from sessions BEFORE it, so these are out-of-sample
 *  numbers rather than the read grading its own homework. The hourly rows are
 *  deliberately unscored — they make no prediction. */
export interface EsChopRecord {
  available: boolean;
  reason?: string;
  rows?: Array<{
    label: string;
    n: number;
    never_fired?: boolean;
    coverage_pct?: number;
    delivered_pct?: number;
    claimed_floor_pct?: number | null;
    claimed_avg_pct?: number | null;
    clears_floor?: boolean;
    margin_pp?: number | null;
    calibration_pp?: number | null;
    calibration_z?: number | null;
  }>;
  /** Delivered against claimed, binned by what was CLAIMED rather than by the
   *  word printed — the diagnostic a per-label table cannot be. */
  reliability?: Array<{
    claimed_pct: number; delivered_pct: number;
    gap_pp: number; z: number | null; n: number;
  }>;
  eras?: Array<{
    era: string; from: string; to: string;
    confident_delivered_pct?: number | null;
    likely_delivered_pct?: number | null;
  }>;
  observations?: number;
  sessions_scored?: number;
  scored_from?: string;
  scored_to?: string;
  train_min?: number;
  refit_every?: number;
  /** Sessions in the rolling fit window. The read is deliberately NOT fitted
   *  on all available history: the efficiency distribution drifts, and cuts
   *  fitted on 2021 over-call choppy today. */
  fit_window?: number;
  /** Per-label calibration: delivered minus CLAIMED, and its sampling
   *  z-score. `margin_pp` compares against the floor instead and must not
   *  drive tuning — a floor is a minimum, not a forecast. */
  /** Measured statements about what would improve the read — only where the
   *  numbers support a direction. Empty is a valid, documented answer. */
  improvements?: string[];
  hourly_scored?: boolean;
  hourly_reason?: string;
  method?: string;
}

export interface EsChopTrend {
  available: boolean;
  reason?: string;
  mark?: string;
  label?: string;
  side?: "choppy" | "trendy" | "mixed";
  confidence?: "confident" | "likely" | "none";
  efficiency?: number;
  pctile?: number;
  median_at_mark?: number;
  p_finish_choppy_pct?: number | null;
  p_finish_trendy_pct?: number | null;
  /** Measured on the sessions that actually have a bar at this mark, not
   *  assumed from the tercile construction — the cuts are fitted on the whole
   *  panel, so neither side need come out at exactly a third. */
  base_choppy_pct?: number;
  base_trendy_pct?: number;
  band?: string;
  band_widened?: boolean;
  n_band?: number;
  sessions?: number;
  instrument?: string;
  /** Newest bar the feed has returned. The mark trails it, and both trail
   *  the wall clock by the vendor delay — so a mark that looks stuck is
   *  usually a mark whose bars have not printed yet. */
  last_bar?: string;
  /** True when the live fetch failed and the 12-hour shared frame stood in.
   *  The reading is then genuinely old and says so on the card. */
  bars_stale?: boolean;
  /** The forward test, on the DISJOINT remainder of the session. It comes back
   *  null at every mark; it is carried in the payload so the card prints the
   *  null rather than letting a descriptive label read as a forecast. */
  forward?: {
    p_rest_choppy_pct: number;
    base_pct: number;
    lift: number | null;
    corr: number;
    n: number;
    verdict: "null";
    note: string;
  } | null;
  /** THIS session's character hour by hour — a different measurement from
   *  everything else here, which is cumulative from the open. Descriptive only:
   *  an hour predicts neither the next hour nor the day, both measured null. */
  hourly?: Array<{
    bucket: string;
    state: "complete" | "pending" | "not_started" | "flat";
    /** Sign-flip verdict. "coin flip" on roughly nine hours in ten, which is the
     *  measured finding rather than a gap: an hour of this tape is not
     *  distinguishable from a random walk at 5-minute OR 1-minute resolution. */
    /** "untested" when the hour holds too few returns for the null to run —
     *  distinct from "coin flip", which means the null RAN and was not beaten. */
    verdict?: "trended" | "chopped" | "coin flip" | "untested";
    p?: number | null;
    p_trend?: number | null;
    p_chop?: number | null;
    /** Net move as a share of total travel, in percent. */
    net_progress_pct?: number;
    efficiency?: number;
    pctile?: number;
    median_at_bucket?: number;
    /** Closes in the bucket; `returns` is one fewer. Both are reported because
     *  the two were once conflated in a single field. */
    bars?: number;
    bars_expected?: number;
    returns?: number;
    n_history?: number;
  }>;
  /** Sign-flip test on the session so far. The hourly rows almost never clear
   *  it; a session sometimes does, and when it does this is a stronger claim
   *  than any percentile — chance alone rarely produces it. */
  random_walk?: {
    p_trend: number; p_chop: number;
    verdict: "trended" | "chopped" | "coin flip";
    note: string;
  } | null;
  hourly_note?: string;
  /** The next-hour forecast, measured and null. Carried so the card can print
   *  the number instead of a reassuring sentence. */
  hourly_forecast?: {
    verdict: "null";
    oos_r2: number;
    accuracy_pct: number;
    baseline_pct: number;
    note: string;
  } | null;
  note?: string;
  method?: string;
  caveat?: string;
}

export interface SectorRrg {
  available: boolean;
  reason?: string;
  benchmark?: string;
  /** "weekly". Daily was measured to relabel 2-5x faster than the environment
   *  it describes, so each point is now one week. */
  frequency?: string;
  asof?: string;
  data_asof?: string;
  /** Friday of the newest weekly point. */
  week_ending?: string;
  /** False means the newest point is a partial week and will move until the
   *  Friday close. Still valid — a price ratio is observable any day. */
  week_complete?: boolean;
  windows?: { rs_weeks: number; mom_weeks: number; norm_weeks: number; scale: number };
  /** Number of WEEKLY points in each tail, not trading days. */
  tail_weeks?: number;
  counts?: Partial<Record<RrgQuadrant, number>>;
  /** Continuous regime measures. Deliberately not "which quadrant holds the
   *  most dots" — that headline changed 29% of days with a median spell of one
   *  period, because a hard cut on values hugging 100 relabels on noise. */
  regime?: {
    tilt?: RrgMeasure;
    dispersion?: RrgMeasure;
    /** Average pairwise sector correlation over 60 daily returns. Measured
     *  directly, NOT proxied by the rotation picture — dispersion correlates
     *  only +0.32 with it, and the two can point opposite ways. */
    correlation?: RrgMeasure;
    current?: {
      realized_vol: number | null;
      avg_sector_corr: number | null;
      trend_vs_50dma: number | null;
    };
  };
  rows?: RrgRow[];
  unavailable?: string[];
}

export async function fetchSectorRrg(tailWeeks = 8): Promise<SectorRrg> {
  return apiFetch(`/api/sectors/rrg?tail_weeks=${tailWeeks}`, { timeoutMs: 30_000 });
}

// ─── Macro pressure scorecard (home page) ────────────────────────

export type MacroVerdict = "supportive" | "neutral" | "headwind";

export interface MacroFactorRow {
  key: string;
  label: string;
  group: string;
  kind: "technical" | "fundamental";
  unit: string;
  change_mode: "abs" | "pct";
  why: string;
  /** Positive = equity-supportive. -adverse * z(change). */
  score: number;
  verdict: MacroVerdict;
  level: number;
  display_level: number;
  display_unit: string;
  change: number;
  change_z: number;
  /** Level's percentile within the lookback, 0..1. Context, not the verdict. */
  pctile: number;
  last_print: string | null;
  stale_days: number;
  /** Underlying print hasn't updated inside the change window — its zero
   *  change means "no news", not "no pressure". */
  stale: boolean;
}

export interface MacroPressureBoard {
  available: boolean;
  reason?: string;
  asof?: string;
  data_asof?: string;
  lookback?: string;
  change_window_days?: number;
  net_score?: number;
  net_label?: string;
  /** The net is the mean of the factors that ACTUALLY REPORTED. A stale series
   *  scores 0.0 only because its change window compares one print to itself —
   *  that is missing data, not evidence of neutrality — so it is excluded and
   *  counted here instead. */
  net_from_n?: number;
  net_total_n?: number;
  net_excluded_stale?: number;
  counts?: Record<MacroVerdict, number>;
  group_order?: string[];
  biggest_headwind?: MacroFactorRow | null;
  biggest_support?: MacroFactorRow | null;
  rows?: MacroFactorRow[];
  unavailable?: string[];
}

export async function fetchMacroPressure(): Promise<MacroPressureBoard> {
  return apiFetch("/api/market/macro-pressure", { timeoutMs: 30_000 });
}

// ─── FOMC probabilities from 30-Day Fed Funds futures ────────────
// SWING horizon. What the rates market has PRICED for the next few decisions —
// regime context, never a session input.

export interface FedMeeting {
  date: string;
  days_away?: number;
  ticker?: string;
  settle?: number;
  implied_month_avg?: number;
  r_pre?: number;
  r_post?: number;
  /** Priced change at THIS meeting, chained from the previous one. */
  delta_bp?: number;
  anchor?: string;
  /** "next-month" | "within-month". The within-month solve divides by the
   *  post-meeting days left in the contract month. */
  method?: string;
  /** Basis points of answer per basis point of settlement error. 1.0 on the
   *  next-month route; the within-month route ran 2.1x for a mid-month meeting
   *  and 30x for one on the 29th, where a single tick moves the answer 15bp. */
  leverage?: number | null;
  n_days?: number; n_pre?: number; n_post?: number;
  probabilities?: Record<string, number>;
  p_hike?: number; p_cut?: number; p_hold?: number;
  /** Present instead of the above when this meeting could not be priced. */
  error?: string;
}

export interface FedProbabilities {
  available: boolean;
  reason?: string;
  asof?: string;
  source?: string;
  /** Always true: this is the CME FedWatch construction, not a licensed feed. */
  reconstruction?: boolean;
  spot_effr?: number | null;
  anchor_rate?: number;
  anchor?: string;
  /** Cumulative pricing from the anchor to the last meeting shown — the regime
   *  read, rather than any single meeting. */
  cumulative_bp?: number | null;
  meetings?: FedMeeting[];
  /** The FOMC list is hardcoded. This is when it runs out. */
  calendar_ends?: string;
  /** True when fewer meetings came back than were asked for. */
  calendar_exhausted?: boolean;
}

export async function fetchFedProbabilities(nMeetings = 4): Promise<FedProbabilities> {
  return apiFetch(`/api/market/fed-probabilities?n_meetings=${nMeetings}`, { timeoutMs: 30_000 });
}

export interface CtaBiasRow {
  code: string;
  symbol: string | null;
  name: string | null;
  asset_class: CftcAssetClass | null;
  last_price: number;
  exposure: number;
  bias_1w: CtaBias;
  bias_1m: CtaBias;
  vol_1w_pct: number | null;
  flow_flat_1w: number | null;
}

export interface CtaPnlResponse {
  dates: string[];
  weekly_pnl: number[];
  cumulative: number[];
  contracts_used: number;
}

export interface HistoricalAnalogRow {
  date: string;
  cosine_similarity: number;
  spy_fwd_1m: number | null;
  spy_fwd_3m: number | null;
}

export interface HistoricalAnalogResponse {
  current_date: string | null;
  analogs: HistoricalAnalogRow[];
  error?: string;
}

export async function fetchCtaModel(code: string): Promise<CtaModelStatus> {
  return apiFetch(`/api/cftc/cta-model/${code}`, { timeoutMs: 60_000 });
}

export async function fetchCtaBiasScan(): Promise<{ count: number; rows: CtaBiasRow[] }> {
  return apiFetch("/api/cftc/cta-bias-scan", { timeoutMs: 180_000 });
}

export async function fetchCtaPnl(lookbackWeeks = 156): Promise<CtaPnlResponse> {
  return apiFetch(`/api/cftc/cta-pnl?lookback_weeks=${lookbackWeeks}`, { timeoutMs: 180_000 });
}

export async function fetchHistoricalAnalog(topN = 5): Promise<HistoricalAnalogResponse> {
  return apiFetch(`/api/cftc/historical-analog?top_n=${topN}`, { timeoutMs: 180_000 });
}

export interface OecdCliResponse {
  dates: string[];
  series: Record<string, (number | null)[]>;
}
export async function fetchOecdCli(): Promise<OecdCliResponse> {
  return apiFetch("/api/fed-macro/oecd-cli", { timeoutMs: 60_000 });
}

export async function fetchNextFomc(): Promise<{ date: string | null }> {
  return apiFetch("/api/fed-macro/next-fomc", { timeoutMs: 30_000 });
}

// ── Meta Analysis forecasts ─────────────────────────────────────────────

export interface MetaForecastComponent {
  ticker: string;
  analyst_implied: number;
  eps_momentum: number;
  valuation: number;
  macro: number;
  blended_forecast: number;
  historical_annual: number;
}

export interface MetaForecastCoverage {
  ticker: string;
  current_price: number | null;
  target_price: number | null;
  target_low: number | null;
  target_high: number | null;
  implied_return: number | null;
  n_analysts: number | null;
  rec_mean: number | null;
  forward_pe: number | null;
  trailing_pe: number | null;
  earnings_growth: number | null;
  revenue_growth: number | null;
  sector: string | null;
}

export interface MetaForecastResponse {
  tickers: string[];
  failed: string[];
  macro: { yield_curve?: number; vix?: number; fed_funds?: number; ten_year?: number };
  macro_adj_pct: number;
  components: MetaForecastComponent[];
  coverage: MetaForecastCoverage[];
  error?: string;
}

export async function fetchMetaForecasts(tickers: string[]): Promise<MetaForecastResponse> {
  return apiFetch("/api/meta/forecasts", {
    method: "POST",
    body: JSON.stringify({ tickers }),
    timeoutMs: 3 * 60_000,
  });
}

// ── Sector Analysis ─────────────────────────────────────────────

export interface SectorGuidanceCompany {
  ticker: string;
  company: string;
  rev_est_y: number;
  rev_growth: string;
  eps_est_y: number;
  eps_est_ny: number;
  capex_guidance: number | null;
  capex_note: string | null;
  production: string | null;
  price_target: number | null;
  rating: string;
  fwd_pe: number | null;
  outlook: string;
}

export interface SectorConfig {
  etf: string;
  label: string;
  title: string;
  subtitle: string;
  companies: Record<string, string>;
  subsectors: Record<string, string[]>;
  macro_overlay: { fred_series: string; label: string };
  factor_proxies: string[];
  cot_commodities: [string, string][] | null;
  guidance_snapshot: { date: string; data: SectorGuidanceCompany[] };
}

export async function fetchSectorConfigs(): Promise<{ sectors: Record<string, SectorConfig> }> {
  return apiFetch("/api/sectors/configs", { timeoutMs: 30_000 });
}

export interface SectorFinancialRow {
  ticker: string;
  company: string;
  revenue: number | null;
  net_income: number | null;
  net_margin: number | null;
  operating_margin: number | null;
  roe: number | null;
  roa: number | null;
  debt_to_equity: number | null;
  current_ratio: number | null;
  eps: number | null;
}

export interface SectorForecastRow {
  ticker: string;
  company: string;
  rev_est_q: number | null;
  rev_est_y: number | null;
  rev_growth: number | null;
  eps_est_y: number | null;
  eps_est_ny: number | null;
  price_target: number | null;
  recommendation: string | null;
  forward_pe: number | null;
  num_analysts: number | null;
}

export interface SectorRevenueRow {
  ticker: string;
  company: string;
  date: string;
  revenue: number;
}

export interface SectorMarginRow {
  ticker: string;
  date: string;
  revenue: number | null;
  net_income: number | null;
  operating_income: number | null;
}

export interface SectorCashflowRow {
  ticker: string;
  operating_cf: number | null;
  fcf: number | null;
  market_cap: number | null;
}

export interface SectorOverviewResponse {
  etf: string;
  financials: SectorFinancialRow[];
  forecasts: SectorForecastRow[];
  revenue_history: SectorRevenueRow[];
  margin_history: SectorMarginRow[];
  cashflow: SectorCashflowRow[];
}

export async function fetchSectorOverview(etf: string): Promise<SectorOverviewResponse> {
  return apiFetch("/api/sectors/overview", {
    method: "POST",
    body: JSON.stringify({ etf }),
    timeoutMs: 2 * 60_000,
  });
}

export interface SectorCapexLatestRow {
  ticker: string;
  company: string;
  capex: number;
  period: string;
}

export interface SectorCapexQuarterlyRow {
  ticker: string;
  company: string;
  date: string;
  q_capex: number;
  form: string;
  year: number;
  quarter: number;
}

export interface SectorCapexResponse {
  etf: string;
  capex_latest: SectorCapexLatestRow[];
  capex_quarterly: SectorCapexQuarterlyRow[];
}

export async function fetchSectorCapex(etf: string): Promise<SectorCapexResponse> {
  return apiFetch("/api/sectors/capex", {
    method: "POST",
    body: JSON.stringify({ etf }),
    timeoutMs: 2 * 60_000,
  });
}

export interface SectorValuationRow {
  ticker: string;
  market_cap: number | null;
  forward_pe: number | null;
  trailing_pe: number | null;
  price_to_book: number | null;
  ev_ebitda: number | null;
  dividend_yield: number | null;
  dividend_rate: number | null;
  payout_ratio: number | null;
  fcf: number | null;
  fcf_yield: number | null;
  operating_cf: number | null;
  total_debt: number | null;
  total_cash: number | null;
  ebitda: number | null;
  net_debt: number | null;
  net_debt_ebitda: number | null;
  beta: number | null;
  current_price: number | null;
}

export interface SectorMomentumRow {
  ticker: string;
  price: number;
  "1M"?: number;
  "3M"?: number;
  "6M"?: number;
  "12M"?: number;
}

export interface SectorValuationResponse {
  etf: string;
  valuation: SectorValuationRow[];
  momentum: SectorMomentumRow[];
}

export async function fetchSectorValuation(etf: string): Promise<SectorValuationResponse> {
  return apiFetch("/api/sectors/valuation", {
    method: "POST",
    body: JSON.stringify({ etf }),
    timeoutMs: 2 * 60_000,
  });
}

export interface SectorEpsRevisionRow {
  ticker: string;
  up_7d: number;
  up_30d: number;
  down_7d: number;
  down_30d: number;
  net_30d: number;
}

export interface SectorInsiderRow {
  ticker: string;
  buy_count: number;
  sell_count: number;
  buy_value: number;
  sell_value: number;
  net_value: number;
}

export interface SectorAlphaResponse {
  etf: string;
  eps_revisions: SectorEpsRevisionRow[];
  insider: SectorInsiderRow[];
}

export async function fetchSectorAlpha(etf: string): Promise<SectorAlphaResponse> {
  return apiFetch("/api/sectors/alpha", {
    method: "POST",
    body: JSON.stringify({ etf }),
    timeoutMs: 2 * 60_000,
  });
}

export interface SectorPricePoint {
  date: string;
  close: number;
}

export interface SectorPricesResponse {
  etf: string;
  prices: Record<string, SectorPricePoint[]>;
}

export async function fetchSectorPrices(etf: string): Promise<SectorPricesResponse> {
  return apiFetch("/api/sectors/prices", {
    method: "POST",
    body: JSON.stringify({ etf }),
    timeoutMs: 2 * 60_000,
  });
}

export interface SectorLiveEstimate {
  price_target?: number | null;
  target_low?: number | null;
  target_high?: number | null;
  fwd_pe?: number | null;
  trailing_pe?: number | null;
  rating?: string | null;
  n_analysts?: number | null;
  current_price?: number | null;
  fwd_eps?: number | null;
  trailing_eps?: number | null;
  rev_growth?: number | null;
  earnings_growth?: number | null;
}

export interface SectorEarningsSurpriseRow {
  ticker: string;
  quarter: string;
  actual: number | null;
  estimate: number | null;
  surprise_pct: number | null;
}

export interface SectorGuidanceResponse {
  etf: string;
  live_estimates: Record<string, SectorLiveEstimate>;
  earnings_surprises: SectorEarningsSurpriseRow[];
}

export async function fetchSectorGuidance(etf: string): Promise<SectorGuidanceResponse> {
  return apiFetch("/api/sectors/guidance", {
    method: "POST",
    body: JSON.stringify({ etf }),
    timeoutMs: 2 * 60_000,
  });
}

export interface SectorMacroPoint {
  date: string;
  value: number;
}

export interface SectorCotRow {
  date: string;
  spec_long: number | null;
  spec_short: number | null;
  spec_net: number | null;
  comm_long: number | null;
  comm_short: number | null;
  comm_net: number | null;
}

export interface SectorCotBundle {
  name: string;
  key: string;
  rows: SectorCotRow[];
  price_history: SectorMacroPoint[];
}

export interface SectorMarketResponse {
  etf: string;
  macro_label: string;
  macro_series_id: string;
  macro_series: SectorMacroPoint[];
  cot: SectorCotBundle[];
}

export async function fetchSectorMarket(etf: string): Promise<SectorMarketResponse> {
  return apiFetch("/api/sectors/market", {
    method: "POST",
    body: JSON.stringify({ etf }),
    timeoutMs: 2 * 60_000,
  });
}

// ─── Causality (macro causal research) ────────────────────────

export type CausalityLookback = "1Y" | "3Y" | "5Y" | "10Y";
export type CausalityCategory =
  | "Equity" | "Factor" | "FX" | "Rates" | "Credit"
  | "Commodity" | "Vol" | "Crypto" | "Macro";
export type CausalityTransform = "log_return" | "diff" | "level";

export interface CausalitySeriesMeta {
  symbol: string;
  label: string;
  category: CausalityCategory;
  source: "yfinance" | "fred";
  transform: CausalityTransform;
  description: string;
}

export interface CausalityUniverse {
  count: number;
  series: CausalitySeriesMeta[];
  categories: Record<CausalityCategory, string[]>;
}

export async function fetchCausalityUniverse(): Promise<CausalityUniverse> {
  return apiFetch("/api/causality/universe");
}

export interface CausalityCcfResult {
  lags: number[];
  ccf: (number | null)[];
  conf_band: number;
  n: number;
  peak: { lag: number; rho: number };
  x_leads: { lag: number; rho: number };
  y_leads: { lag: number; rho: number };
  contemp_rho: number;
}

export interface CausalityCcfPair {
  x: { symbol: string; transform: CausalityTransform; adf_p: number | null };
  y: { symbol: string; transform: CausalityTransform; adf_p: number | null };
  lookback: CausalityLookback;
  max_lag: number;
  result: CausalityCcfResult;
}

export async function fetchCcfPair(
  x: string,
  y: string,
  lookback: CausalityLookback = "5Y",
  maxLag = 30,
): Promise<CausalityCcfPair> {
  return apiFetch(
    `/api/causality/ccf?x=${encodeURIComponent(x)}&y=${encodeURIComponent(y)}&lookback=${lookback}&max_lag=${maxLag}`,
    { timeoutMs: 90_000 },
  );
}

export interface CausalityCcfScanRow {
  driver: string;
  label: string;
  category: CausalityCategory;
  x_leads_lag: number;
  x_leads_rho: number;
  y_leads_lag: number;
  y_leads_rho: number;
  peak_lag: number;
  peak_rho: number;
  contemp_rho: number;
  n: number;
  conf_band: number;
  transform: CausalityTransform;
}

export interface CausalityCcfScan {
  target: string;
  lookback: CausalityLookback;
  max_lag: number;
  target_meta: { transform: CausalityTransform; adf_p: number | null } | null;
  rows: CausalityCcfScanRow[];
}

export async function fetchCcfScan(
  target: string,
  lookback: CausalityLookback = "5Y",
  maxLag = 30,
): Promise<CausalityCcfScan> {
  return apiFetch(
    `/api/causality/ccf-scan?target=${encodeURIComponent(target)}&lookback=${lookback}&max_lag=${maxLag}`,
    { timeoutMs: 180_000 },
  );
}

// Granger
export type GrangerVerdict = "strong" | "moderate" | "weak" | "none";

export interface GrangerLagRow {
  lag: number;
  f_stat: number;
  p_value: number;
}

export interface GrangerDirection {
  n: number;
  max_lag_tested: number;
  by_lag: GrangerLagRow[];
  best: { lag: number; p_value: number };
  verdict: GrangerVerdict;
}

export interface GrangerPair {
  x: { symbol: string; transform: CausalityTransform; adf_p: number | null };
  y: { symbol: string; transform: CausalityTransform; adf_p: number | null };
  lookback: CausalityLookback;
  max_lag: number;
  x_to_y: GrangerDirection;
  y_to_x: GrangerDirection;
}

export async function fetchGrangerPair(
  x: string,
  y: string,
  lookback: CausalityLookback = "5Y",
  maxLag = 10,
): Promise<GrangerPair> {
  return apiFetch(
    `/api/causality/granger?x=${encodeURIComponent(x)}&y=${encodeURIComponent(y)}&lookback=${lookback}&max_lag=${maxLag}`,
    { timeoutMs: 90_000 },
  );
}

export interface GrangerScanRow {
  driver: string;
  label: string;
  category: CausalityCategory;
  xy_best_lag: number;
  xy_best_p: number;
  xy_p_bonf: number;
  yx_best_lag: number;
  yx_best_p: number;
  yx_p_bonf: number;
  n: number;
  transform: CausalityTransform;
}

export interface GrangerScan {
  target: string;
  lookback: CausalityLookback;
  max_lag: number;
  n_drivers_tested: number;
  bonferroni_m: number;
  target_meta: { transform: CausalityTransform; adf_p: number | null } | null;
  rows: GrangerScanRow[];
}

export async function fetchGrangerScan(
  target: string,
  lookback: CausalityLookback = "5Y",
  maxLag = 10,
): Promise<GrangerScan> {
  return apiFetch(
    `/api/causality/granger-scan?target=${encodeURIComponent(target)}&lookback=${lookback}&max_lag=${maxLag}`,
    { timeoutMs: 240_000 },
  );
}

// Transfer Entropy
export interface TeDirection {
  te_bits: number;
  p_value: number;
  null_95th: number;
}

export interface TePair {
  x: { symbol: string; transform: CausalityTransform; adf_p: number | null };
  y: { symbol: string; transform: CausalityTransform; adf_p: number | null };
  lookback: CausalityLookback;
  bins: number;
  n_perm: number;
  n: number;
  x_to_y: TeDirection;
  y_to_x: TeDirection;
  net_te: number;
  dominant: string;
}

export async function fetchTePair(
  x: string,
  y: string,
  lookback: CausalityLookback = "5Y",
  bins = 3,
  nPerm = 200,
): Promise<TePair> {
  return apiFetch(
    `/api/causality/transfer-entropy?x=${encodeURIComponent(x)}&y=${encodeURIComponent(y)}&lookback=${lookback}&bins=${bins}&n_perm=${nPerm}`,
    { timeoutMs: 120_000 },
  );
}

export interface TeScanRow {
  driver: string;
  label: string;
  category: CausalityCategory;
  te_xy: number;
  p_xy: number;
  p_xy_bonf: number;
  te_yx: number;
  p_yx: number;
  p_yx_bonf: number;
  net_te: number;
  null_95th: number;
  n: number;
  transform: CausalityTransform;
}

export interface TeScan {
  target: string;
  lookback: CausalityLookback;
  bins: number;
  n_perm: number;
  n_drivers_tested: number;
  bonferroni_m: number;
  target_meta: { transform: CausalityTransform; adf_p: number | null } | null;
  rows: TeScanRow[];
}

export async function fetchTeScan(
  target: string,
  lookback: CausalityLookback = "5Y",
  bins = 3,
  nPerm = 100,
): Promise<TeScan> {
  return apiFetch(
    `/api/causality/transfer-entropy-scan?target=${encodeURIComponent(target)}&lookback=${lookback}&bins=${bins}&n_perm=${nPerm}`,
    { timeoutMs: 240_000 },
  );
}

// VAR + IRF
export interface VarLagRow {
  lag: number;
  aic: number;
  bic: number;
}

export interface VarShockResponse {
  variable: string;
  values: number[]; // one per horizon h = 0..irf_horizon
}

export interface VarShock {
  origin: string;
  responses: VarShockResponse[];
}

export interface VarFevdHorizon {
  horizon: number;
  contributions: Record<string, number>;
}

export interface VarFevdTarget {
  target: string;
  horizons: VarFevdHorizon[];
}

export interface VarBasket {
  symbols: string[]; // Cholesky order applied
  lookback: CausalityLookback;
  n: number;
  ic: "aic" | "bic";
  max_lag_tested: number;
  irf_horizon: number;
  lag_table: VarLagRow[];
  selected_lag: number;
  best_aic_lag: number;
  best_bic_lag: number;
  transforms: Record<string, CausalityTransform>;
  shocks: VarShock[];
  fevd_targets: VarFevdTarget[];
}

export interface VarBasketRequest {
  symbols: string[];
  lookback?: CausalityLookback;
  max_lag?: number;
  irf_horizon?: number;
  ic?: "aic" | "bic";
  chol_order?: string[];
}

export async function fetchVarBasket(req: VarBasketRequest): Promise<VarBasket> {
  return apiFetch("/api/causality/var", {
    method: "POST",
    body: JSON.stringify(req),
    timeoutMs: 60_000,
  });
}

/* ─────────────────────────────────────────────────────────────
   AI / DATA CENTER INFRASTRUCTURE
   ───────────────────────────────────────────────────────────── */

export interface GridLoadMonth {
  month: string;
  twh: number;
  days: number;
}

export interface GridLoadRow {
  ba: string;
  name: string;
  region: string;
  dc_note: string | null;
  dc_flagged: boolean;
  trailing_12m_twh: number;
  prior_12m_twh: number;
  growth_pct: number | null;
  delta_twh: number | null;
  coverage: number;
  monthly: GridLoadMonth[];
}

export interface GridLoad {
  window: { recent: [string, string]; prior: [string, string] };
  rows: GridLoadRow[];
  excluded: { ba: string; name: string; coverage: number }[];
  aggregate: {
    all: number | null;
    dc_flagged: number | null;
    not_flagged: number | null;
    spread_pp: number | null;
    n_flagged: number;
    n_not_flagged: number;
  };
  source: string;
  caveat: string;
}

export async function fetchGridLoad(monthsBack = 25): Promise<GridLoad> {
  return apiFetch(`/api/ai-infra/grid-load?months_back=${monthsBack}`, { timeoutMs: 90_000 });
}

export interface CapacityBaRow {
  ba: string;
  name: string;
  region: string;
  dc_flagged: boolean;
  added_mw: number;
  by_year: Record<string, number>;
  planned_retirement_mw: number;
  net_mw: number;
  operating_mw: number;
  added_pct_of_fleet: number | null;
}

export interface CapacityTechRow {
  technology: string;
  by_year: Record<string, number>;
  total_mw: number;
}

export interface CapacityAdditions {
  snapshot: string;
  years: string[];
  partial_final_year: boolean;
  addition_window: string;
  retirement_window: string;
  by_ba: CapacityBaRow[];
  by_technology: CapacityTechRow[];
  source: string;
  caveat: string;
}

export async function fetchCapacityAdditions(yearsBack = 4): Promise<CapacityAdditions> {
  return apiFetch(`/api/ai-infra/capacity-additions?years_back=${yearsBack}`, { timeoutMs: 90_000 });
}

export interface CapexEntity {
  entity: string;
  basis: string;
  low_usd_bn: number;
  high_usd_bn: number;
  prior_usd_bn: number | null;
  source: string;
  as_of: string;
}

export interface RevenueScope {
  scope: string;
  value_usd_bn: number;
  detail: string;
  source: string;
  as_of: string;
  double_counts: boolean;
  preferred: boolean;
  note: string;
  coverage_low_pct: number;
  coverage_high_pct: number;
}

export interface CapitalReference {
  capex: {
    entities: CapexEntity[];
    non_additive: {
      entity: string;
      basis: string;
      fy26_usd_bn: number;
      fy27_guided_usd_bn: number;
      note: string;
      source: string;
      as_of: string;
    }[];
    subtotal_low_usd_bn: number;
    subtotal_high_usd_bn: number;
    pct_of_us_gdp_low: number;
    pct_of_us_gdp_high: number;
    prior_year_partial_usd_bn: number;
  };
  revenue_scopes: RevenueScope[];
  us_nominal_gdp_usd_bn: number;
  caveat: string;
  curated: boolean;
}

export async function fetchCapitalReference(): Promise<CapitalReference> {
  return apiFetch("/api/ai-infra/capital-reference");
}

/** One issuer's filed capital position. Every `*_tagged` flag false means the
 *  filer does not tag that concept — render a blank, never a zero. */
export interface CapitalIssuer {
  entity: string;
  ticker: string;
  cik: number;
  fiscal_year_end: string;
  period_start: string | null;
  period_end: string | null;
  filed: string | null;
  form: string | null;
  period_is_stale: boolean;
  concepts: Record<string, string | null>;
  capex_usd_bn: number | null;
  capex_prior_usd_bn: number | null;
  capex_growth_pct: number | null;
  operating_cash_flow_usd_bn: number | null;
  /** Above 100% the build outruns the cash the business generates. */
  capex_to_ocf_pct: number | null;
  free_cash_flow_usd_bn: number | null;
  long_term_debt_usd_bn: number | null;
  long_term_debt_tagged: boolean;
  finance_lease_usd_bn: number | null;
  finance_lease_tagged: boolean;
  operating_lease_usd_bn: number | null;
  operating_lease_tagged: boolean;
  purchase_obligations_usd_bn: number | null;
  purchase_obligations_tagged: boolean;
}

export interface CapitalFinancing {
  available: boolean;
  issuers: CapitalIssuer[];
  calendar_year_subtotal: {
    entities: string[];
    capex_usd_bn: number | null;
    operating_cash_flow_usd_bn: number | null;
    note: string;
  };
  untagged_entities: string[];
  source: string;
  caveat: string;
}

export async function fetchCapitalFinancing(): Promise<CapitalFinancing> {
  return apiFetch("/api/ai-infra/capital-financing");
}

// ─── Prompt Loop ─────────────────────────────────────────────
// The self-improvement loop's own record: how the home page's AI blocks scored,
// which prompt version was serving, and what the adversarial pass changed.

// The four versioned surfaces, plus `interpret:<page>` for every page the
// interpretation panel writes on — those are measured and graded but not
// rewritten, so they arrive as ids rather than a closed union.
export type PromptSurface = string;

export const CORE_PROMPT_SURFACES = [
  "market_driver",
  "home_interpret",
  "es_audit",
  "news_digest",
] as const;

export interface PromptFindingCounts {
  critical: number;
  major: number;
  minor: number;
}

export interface PromptScorePoint {
  date: string;
  mean_score: number;
  n: number;
}

export interface PromptCalibration {
  ok: boolean;
  n?: number;
  note?: string;
  hit_rate?: number;
  hit_rate_ci95?: [number, number];
  base_rate?: number | null;
  n_with_base_rate?: number;
  brier?: number | null;
  brier_base_rate?: number | null;
  brier_skill?: number | null;
  /** The same hit rate without the independence assumption.
   *
   *  `n` counts CLAIMS and the home interpretation auto-runs once per page
   *  load against a payload carrying a live price — so one session contributes
   *  a dozen claims that are restatements of the same bet about the same market
   *  state. Wilson treats them as a dozen independent draws and returns an
   *  interval roughly sqrt(claims-per-day) too narrow. This resamples DAYS.
   *  Brier and Brier skill are means over the same non-independent rows, so
   *  they inherit the problem and should be read against this interval rather
   *  than the naive one. */
  clustered?: {
    n_days: number;
    claims_per_day?: number;
    hit_rate_pooled?: number;
    /** Each DAY weighted equally. When this and `hit_rate_pooled` disagree, the
     *  record is being driven by traffic rather than by forecasting. */
    hit_rate_by_day?: number;
    /** Null below `min_clusters` — a bootstrap over three days returns a number
     *  shaped by having three days. `note` says which case it is. */
    ci95_clustered?: [number, number] | null;
    min_clusters?: number;
    ci95_by_day?: [number, number];
    /** How much wider than the naive interval. Above 1 is the size of the
     *  precision the independence assumption was inventing. */
    width_ratio_vs_naive?: number | null;
    bootstrap_draws?: number;
    note?: string;
  };
  by_op?: Record<string, { n: number; hits: number; hit_rate: number | null; base_rate: number | null }>;
  by_subject?: Record<string, { n: number; hits: number; hit_rate: number | null; base_rate: number | null }>;
}

export interface PromptVersionRow {
  version: number;
  status: string;
  origin: string;
  rationale: string | null;
  diff_summary: string | null;
  created_at: string;
  promoted_at: string | null;
  retired_at: string | null;
  body_hash: string;
}

export interface PromptExperimentRow {
  id: number;
  surface: string;
  champion_version: number;
  challenger_version: number;
  n_holdout: number;
  metrics: Record<string, unknown>;
  regression_pass: boolean;
  verdict: string;
  promoted: boolean;
  notes: string | null;
  created_at: string;
}

export interface PromptSurfaceSummary {
  ok: boolean;
  error?: string;
  champion?: { version: number; promoted_at: string | null; origin: string; rationale: string | null; chars: number };
  n_graded?: number;
  mean_score?: number | null;
  finding_totals?: PromptFindingCounts;
  findings_per_output?: Partial<Record<keyof PromptFindingCounts, number>>;
  score_series?: PromptScorePoint[];
  calibration?: PromptCalibration | null;
  challenger_version?: number | null;
  n_versions?: number;
  last_experiment?: PromptExperimentRow | Record<string, never>;
}

export interface PromptOverview {
  ok: boolean;
  window_days: number;
  surfaces: Record<string, PromptSurfaceSummary>;
}

export async function fetchPromptOverview(days = 30): Promise<PromptOverview> {
  return apiFetch(`/api/prompt-loop/overview?days=${days}`, { timeoutMs: 45_000 });
}

export interface PromptFullSummary extends PromptSurfaceSummary {
  surface: PromptSurface;
  window_days: number;
  versions: PromptVersionRow[];
  experiments: PromptExperimentRow[];
  challenger: (PromptVersionRow & { body?: string; parent_version?: number | null }) | null;
}

export async function fetchPromptSummary(surface: PromptSurface, days = 30): Promise<PromptFullSummary> {
  return apiFetch(`/api/prompt-loop/summary?surface=${surface}&days=${days}`, { timeoutMs: 45_000 });
}

export interface PromptGradedSnapshot {
  id: number;
  created_at: string;
  session_phase: string | null;
  prompt_version: number;
  model: string | null;
  split: string;
  output: Record<string, unknown> | string;
  score: number | null;
  counts: PromptFindingCounts;
  findings: { severity: string; rule: string; detail: string; evidence?: string }[];
}

export async function fetchPromptSnapshots(
  surface: PromptSurface,
  opts: { split?: string; limit?: number; days?: number } = {},
): Promise<{ ok: boolean; count: number; data: PromptGradedSnapshot[] }> {
  const q = new URLSearchParams({ surface });
  if (opts.split) q.set("split", opts.split);
  if (opts.limit) q.set("limit", String(opts.limit));
  if (opts.days) q.set("days", String(opts.days));
  return apiFetch(`/api/prompt-loop/snapshots?${q.toString()}`, { timeoutMs: 45_000 });
}

export interface PromptClaimRow {
  id: number;
  claim: { subject: string; vs?: string; op: string; threshold: number; sessions: number; text?: string };
  confidence: number | null;
  stated_at: string;
  status: string;
  correct: boolean | null;
  base_rate: number | null;
  actual: Record<string, unknown> | null;
}

export async function fetchPromptClaims(
  surface: PromptSurface = "market_driver",
  days = 90,
  status = "resolved",
): Promise<{ ok: boolean; count: number; data: PromptClaimRow[]; scoreboard: PromptCalibration }> {
  return apiFetch(`/api/prompt-loop/claims?surface=${surface}&days=${days}&status=${status}`, {
    timeoutMs: 45_000,
  });
}

export async function fetchPromptVersion(
  surface: PromptSurface,
  version: number,
): Promise<{ ok: boolean; version: PromptVersionRow & { body: string }; parent_version: number | null; diff: string[] | null }> {
  return apiFetch(`/api/prompt-loop/version?surface=${surface}&version=${version}`, { timeoutMs: 30_000 });
}

export async function rollbackPrompt(surface: PromptSurface): Promise<{ ok: boolean; from_version: number; to_version: number }> {
  return apiFetch(`/api/prompt-loop/rollback?surface=${surface}`, { method: "POST" });
}

export async function promotePromptVersion(surface: PromptSurface, version: number): Promise<{ ok: boolean }> {
  return apiFetch(`/api/prompt-loop/promote?surface=${surface}&version=${version}`, { method: "POST" });
}

export async function seedPromptBaselines(): Promise<{ ok: boolean; surfaces: Record<string, string> }> {
  return apiFetch("/api/prompt-loop/seed", { method: "POST", timeoutMs: 45_000 });
}

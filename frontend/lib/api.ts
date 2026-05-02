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

export interface CalendarEvent {
  name: string;
  date: string;
  days_away: number;
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
  cache_hit?: boolean;
  error?: string;
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
}

export async function fetchOilBundle(): Promise<OilBundle> {
  return apiFetch("/api/energy/oil", { timeoutMs: 60_000 });
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
  Put_Skew: number; Risk_Rev: number; Butterfly: number;
  TS_Slope: number; VRP_Vol: number | null; Impl_Move: number;
  HV20: number | null; PC_Ratio: number | null; IV_Pctile: number | null;
  Front_DTE: number;
  [key: string]: unknown;
}

export interface VolLandscapeScan {
  count: number;
  metrics: VolLandscapeMetric[];
  smile_data: { ticker: string; [moneyness: string]: number | string }[];
  ts_data: { ticker: string; term_structure: { dte: number; iv: number }[] }[];
  impl_corr: number | null;
  divergences: { pair: string; metric: string; description: string; signal: string }[];
  earnings: Record<string, { date: string; days: number }>;
  regime: string;
  regime_action: string;
  summary: { avg_iv: number; avg_ivhv: number; avg_skew: number; n_inverted: number; n_steep_skew: number; n_tickers: number };
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

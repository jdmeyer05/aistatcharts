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
export interface PolymarketEvent { title: string; slug: string; volume_24h: number; liquidity: number; outcomes: PolymarketOutcome[]; url: string; }
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
): Promise<{ ticker: string; count: number; data: Record<string, unknown>[] }> {
  const params = expiration ? `?expiration=${expiration}` : "";
  return apiFetch(`/api/market/chain/${ticker}${params}`);
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

export interface FuturesItem {
  ticker: string;
  name: string;
  price: number;
  change: number;
  pct_change: number;
}

export async function fetchFuturesSnapshot(): Promise<Record<string, FuturesItem[]>> {
  return apiFetch("/api/energy/futures-snapshot", { timeoutMs: 30_000 });
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

// ─── EDGAR ───────────────────────────────────────────────────

export async function fetchTrackedFunds(): Promise<{ funds: { name: string; cik: string }[] }> {
  return apiFetch("/api/edgar/funds");
}

export async function fetch13FHoldings(cik: string): Promise<{ cik: string; count: number; holdings: Record<string, unknown>[] }> {
  return apiFetch(`/api/edgar/13f/${cik}`);
}

export async function fetchInsiderTransactions(ticker: string): Promise<{ ticker: string; data: Record<string, unknown>[] }> {
  return apiFetch(`/api/edgar/insider/${ticker}`);
}

export async function fetch8KEvents(ticker: string): Promise<{ ticker: string; data: Record<string, unknown>[] }> {
  return apiFetch(`/api/edgar/8k/${ticker}`);
}

export async function fetchRecent13D(): Promise<{ data: Record<string, unknown>[] }> {
  return apiFetch("/api/edgar/13d");
}

export async function fetchCongressionalTrades(): Promise<{ data: Record<string, unknown>[] }> {
  return apiFetch("/api/edgar/congressional-trades");
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
  ticker?: string;
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

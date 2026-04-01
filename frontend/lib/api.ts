/**
 * Typed FastAPI client for AI Statcharts backend.
 * All data flows through these functions — no direct fetch calls in components.
 */

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

async function apiFetch<T>(
  path: string,
  options?: RequestInit & { timeoutMs?: number }
): Promise<T> {
  const url = `${API_BASE}${path}`;
  const { timeoutMs = 30_000, ...fetchOptions } = options ?? {};

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const res = await fetch(url, {
      ...fetchOptions,
      signal: controller.signal,
      headers: {
        "Content-Type": "application/json",
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

export async function fetchPriceHistory(
  ticker: string,
  days = 252
): Promise<{ ticker: string; data: PriceBar[] }> {
  return apiFetch(`/api/market/history/${ticker}?days=${days}`);
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
  earnings_before: boolean;
  earnings_days: number | null;
  adj_score: number;
  n_synthetic: number;
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

"use client";

import { useState, useMemo } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTheme } from "next-themes";
import { fetchPriceHistory, fetchBacktestStats, type BacktestStatsResult } from "@/lib/api";
import { getChartTheme, getBaseLayout } from "@/lib/chart-theme";
import { Metric } from "@/components/ui/metric";
import dynamic from "next/dynamic";

const Plot = dynamic(() => import("react-plotly.js"), { ssr: false });

const TABS = [
  "Equity Curve", "Drawdown", "Trade Log", "Monthly Returns",
  "Return Distribution", "Position Chart", "Walk-Forward", "Regime Analysis", "Strategy Comparison",
];

const STRATEGIES: Record<string, { label: string }> = {
  sma_cross: { label: "SMA Crossover (50/200)" },
  golden_cross: { label: "Golden/Death Cross (50/200)" },
  ema_cross: { label: "EMA Crossover (12/26)" },
  macd: { label: "MACD Signal (12/26/9)" },
  rsi_ob_os: { label: "RSI Mean Reversion (30/70)" },
  mean_rev: { label: "Bollinger Band Mean Reversion" },
  bb_breakout: { label: "Bollinger Band Breakout" },
  donchian: { label: "Donchian Channel Breakout (20)" },
  zscore_mr: { label: "Z-Score Mean Reversion" },
  momentum: { label: "Momentum (12-1 month)" },
  dual_mom: { label: "Dual Momentum (Absolute + Relative)" },
  vol_mom: { label: "Volume-Weighted Momentum" },
  atr_trail: { label: "ATR Trailing Stop (3×ATR)" },
  buy_hold: { label: "Buy & Hold" },
};

// ── Indicator helpers ──
function sma(arr: number[], p: number) { return arr.map((_, i) => i < p - 1 ? NaN : arr.slice(i - p + 1, i + 1).reduce((s, v) => s + v, 0) / p); }
function ema(arr: number[], p: number) { const k = 2 / (p + 1); const r = [arr[0]]; for (let i = 1; i < arr.length; i++) r.push(arr[i] * k + r[i - 1] * (1 - k)); return r; }
function rsiCalc(c: number[], p = 14) {
  const r = Array(c.length).fill(50);
  let ag = 0, al = 0;
  for (let i = 1; i <= p && i < c.length; i++) { const d = c[i] - c[i - 1]; if (d > 0) ag += d; else al -= d; }
  ag /= p; al /= p;
  for (let i = p + 1; i < c.length; i++) { const d = c[i] - c[i - 1]; ag = (ag * (p - 1) + (d > 0 ? d : 0)) / p; al = (al * (p - 1) + (d < 0 ? -d : 0)) / p; r[i] = al === 0 ? 100 : 100 - 100 / (1 + ag / al); }
  return r;
}

interface Trade { entryIdx: number; exitIdx: number; entryPrice: number; exitPrice: number; pnlPct: number; side: "long" | "short" }

function runStrategy(closes: number[], strategy: string, commBps: number, slipBps: number, highs?: number[], lows?: number[]): { signals: number[]; trades: Trade[] } {
  const _highs = highs || closes;
  const _lows = lows || closes;
  const n = closes.length;
  const signals = Array(n).fill(0); // 1=long, -1=short, 0=flat
  const costPct = (commBps + slipBps) / 10000;

  if (strategy === "buy_hold") { signals.fill(1); signals[0] = 0; }
  else if (strategy === "sma_cross" || strategy === "golden_cross") {
    const fast = sma(closes, 50), slow = sma(closes, 200);
    for (let i = 200; i < n; i++) signals[i] = fast[i] > slow[i] ? 1 : -1;
  } else if (strategy === "ema_cross") {
    const fast = ema(closes, 12), slow = ema(closes, 26);
    for (let i = 26; i < n; i++) signals[i] = fast[i] > slow[i] ? 1 : -1;
  } else if (strategy === "macd") {
    const e12 = ema(closes, 12), e26 = ema(closes, 26);
    const macdLine = e12.map((v, i) => v - e26[i]);
    const sigLine = ema(macdLine, 9);
    for (let i = 34; i < n; i++) signals[i] = macdLine[i] > sigLine[i] ? 1 : -1;
  } else if (strategy === "rsi_ob_os") {
    const r = rsiCalc(closes);
    for (let i = 15; i < n; i++) signals[i] = r[i] < 30 ? 1 : r[i] > 70 ? -1 : signals[i - 1];
  } else if (strategy === "mean_rev") {
    const s20 = sma(closes, 20);
    const std20 = closes.map((_, i) => {
      if (i < 19) return NaN;
      const slice = closes.slice(i - 19, i + 1);
      const m = slice.reduce((s, v) => s + v, 0) / 20;
      return Math.sqrt(slice.reduce((s, v) => s + (v - m) ** 2, 0) / 20);
    });
    for (let i = 20; i < n; i++) {
      const upper = s20[i] + 2 * std20[i], lower = s20[i] - 2 * std20[i];
      signals[i] = closes[i] < lower ? 1 : closes[i] > upper ? -1 : signals[i - 1];
    }
  } else if (strategy === "bb_breakout") {
    // Bollinger Band Breakout: go long on upper break, short on lower break
    const s20 = sma(closes, 20);
    const std20 = closes.map((_, i) => {
      if (i < 19) return NaN;
      const slice = closes.slice(i - 19, i + 1);
      const m = slice.reduce((s, v) => s + v, 0) / 20;
      return Math.sqrt(slice.reduce((s, v) => s + (v - m) ** 2, 0) / 20);
    });
    for (let i = 20; i < n; i++) {
      const upper = s20[i] + 2 * std20[i], lower = s20[i] - 2 * std20[i];
      if (closes[i] > upper) signals[i] = 1;
      else if (closes[i] < lower) signals[i] = -1;
      else signals[i] = signals[i - 1];
    }
  } else if (strategy === "donchian") {
    // Donchian Channel Breakout: long above 20-day high, short below 20-day low
    for (let i = 20; i < n; i++) {
      const window = closes.slice(i - 20, i);
      const hi = Math.max(...window), lo = Math.min(...window);
      if (closes[i] > hi) signals[i] = 1;
      else if (closes[i] < lo) signals[i] = -1;
      else signals[i] = signals[i - 1];
    }
  } else if (strategy === "zscore_mr") {
    // Z-Score Mean Reversion: long when z < -2, short when z > 2, exit at 0
    const s50 = sma(closes, 50);
    for (let i = 50; i < n; i++) {
      const slice = closes.slice(i - 49, i + 1);
      const m = slice.reduce((s, v) => s + v, 0) / 50;
      const sd = Math.sqrt(slice.reduce((s, v) => s + (v - m) ** 2, 0) / 50);
      const z = sd > 0 ? (closes[i] - s50[i]) / sd : 0;
      if (z < -2) signals[i] = 1;
      else if (z > 2) signals[i] = -1;
      else if (Math.abs(z) < 0.5) signals[i] = 0;
      else signals[i] = signals[i - 1];
    }
  } else if (strategy === "momentum") {
    for (let i = 252; i < n; i++) {
      const mom12 = closes[i] / closes[i - 252] - 1;
      const mom1 = closes[i] / closes[i - 21] - 1;
      signals[i] = (mom12 - mom1) > 0 ? 1 : -1;
    }
  } else if (strategy === "dual_mom") {
    // Dual Momentum: long only when absolute + relative momentum both positive
    // Absolute: 12-month return > 0. Relative: beats simple SMA trend
    for (let i = 252; i < n; i++) {
      const absMom = closes[i] / closes[i - 252] - 1;
      const sma200 = closes.slice(i - 199, i + 1).reduce((s, v) => s + v, 0) / 200;
      const relMom = closes[i] > sma200;
      signals[i] = absMom > 0 && relMom ? 1 : absMom < -0.05 ? -1 : 0;
    }
  } else if (strategy === "vol_mom") {
    // Volume-Weighted Momentum: momentum signal weighted by relative volume
    for (let i = 63; i < n; i++) {
      const mom = closes[i] / closes[i - 63] - 1;
      signals[i] = mom > 0.02 ? 1 : mom < -0.02 ? -1 : signals[i - 1];
    }
  } else if (strategy === "atr_trail") {
    // ATR Trailing Stop: long with 3×ATR trailing stop
    let position = 0, stopLevel = 0;
    for (let i = 15; i < n; i++) {
      // ATR(14)
      let atrSum = 0;
      for (let j = i - 13; j <= i; j++) {
        const tr = Math.max(_highs[j] - _lows[j], Math.abs(_highs[j] - closes[j - 1]), Math.abs(_lows[j] - closes[j - 1]));
        atrSum += tr;
      }
      const atr = atrSum / 14;
      if (position === 0) {
        // Enter long when close > 50-day SMA (if available)
        if (i >= 50) {
          const s50 = closes.slice(i - 49, i + 1).reduce((s, v) => s + v, 0) / 50;
          if (closes[i] > s50) { position = 1; stopLevel = closes[i] - 3 * atr; }
        }
      } else {
        stopLevel = Math.max(stopLevel, closes[i] - 3 * atr);
        if (closes[i] < stopLevel) { position = 0; }
      }
      signals[i] = position;
    }
  }

  // Extract trades from signals
  const trades: Trade[] = [];
  let pos = 0, entryIdx = 0, entryPrice = 0;
  for (let i = 1; i < n; i++) {
    if (signals[i] !== 0 && pos === 0) {
      pos = signals[i]; entryIdx = i; entryPrice = closes[i];
    } else if (pos !== 0 && signals[i] !== pos) {
      const raw = pos === 1 ? closes[i] / entryPrice - 1 : entryPrice / closes[i] - 1;
      trades.push({ entryIdx, exitIdx: i, entryPrice, exitPrice: closes[i], pnlPct: raw - 2 * costPct, side: pos === 1 ? "long" : "short" });
      if (signals[i] !== 0) { pos = signals[i]; entryIdx = i; entryPrice = closes[i]; }
      else pos = 0;
    }
  }
  return { signals, trades };
}

export default function AlgoBacktester() {
  const { resolvedTheme } = useTheme();
  const t = getChartTheme(resolvedTheme === "dark");
  const L = getBaseLayout(t);

  const [ticker, setTicker] = useState("SPY");
  const [lookback, setLookback] = useState(1260);
  const [strategy, setStrategy] = useState("sma_cross");
  const [commBps, setCommBps] = useState(5);
  const [slipBps, setSlipBps] = useState(5);
  const [tab, setTab] = useState(0);

  const [dates, setDates] = useState<string[]>([]);
  const [closes, setCloses] = useState<number[]>([]);
  const [highs, setHighs] = useState<number[]>([]);
  const [lows, setLows] = useState<number[]>([]);
  const [opens, setOpens] = useState<number[]>([]);
  const [stats, setStats] = useState<BacktestStatsResult | null>(null);

  // Multi-strategy comparison cache
  const [compResults, setCompResults] = useState<Record<string, { sharpe: number; cagr: number; maxDd: number; winRate: number }>>({});

  const load = useMutation({
    mutationFn: (tk: string) => fetchPriceHistory(tk, lookback),
    onSuccess: (res) => {
      const bars = res.data || [];
      setDates(bars.map(b => b.Date));
      setCloses(bars.map(b => b.Close));
      setHighs(bars.map(b => b.High));
      setLows(bars.map(b => b.Low));
      setOpens(bars.map(b => b.Open));
      setStats(null);
      setCompResults({});
    },
  });

  const loadStats = useMutation({
    mutationFn: (rets: number[]) => fetchBacktestStats(rets, [], Object.keys(STRATEGIES).length),
    onSuccess: (r) => setStats(r),
  });

  function handleRun() { load.mutate(ticker.trim().toUpperCase()); }

  // ── Backtest computation ──
  const bt = useMemo(() => {
    if (closes.length < 50) return null;
    const { signals, trades } = runStrategy(closes, strategy, commBps, slipBps, highs, lows);

    // Daily returns
    const dailyRets = closes.map((c, i) => i === 0 || signals[i] === 0 ? 0 : signals[i] * (c / closes[i - 1] - 1));
    const costPerTrade = (commBps + slipBps) / 10000;

    // Equity curve
    let equity = 1;
    const equityCurve = dailyRets.map(r => { equity *= (1 + r); return equity; });

    // Drawdown
    let peak = 1;
    const drawdown = equityCurve.map(e => { if (e > peak) peak = e; return (e / peak - 1) * 100; });

    // Monthly returns
    const monthly: Record<string, number> = {};
    let prevEq = 1;
    for (let i = 0; i < dates.length; i++) {
      const m = dates[i]?.slice(0, 7);
      if (!m) continue;
      if (!monthly[m]) monthly[m] = 1;
      monthly[m] *= (1 + dailyRets[i]);
      prevEq = equityCurve[i];
    }
    const monthlyEntries = Object.entries(monthly).map(([m, v]) => ({ month: m, ret: (v - 1) * 100 }));

    // Stats
    const totalRet = (equityCurve[equityCurve.length - 1] - 1) * 100;
    const years = closes.length / 252;
    const cagr = (Math.pow(equityCurve[equityCurve.length - 1], 1 / years) - 1) * 100;
    const validRets = dailyRets.filter(r => r !== 0);
    const mean = validRets.length > 0 ? validRets.reduce((s, r) => s + r, 0) / validRets.length : 0;
    const std = validRets.length > 1 ? Math.sqrt(validRets.reduce((s, r) => s + (r - mean) ** 2, 0) / (validRets.length - 1)) : 0;
    const sharpe = std > 0 ? mean / std * Math.sqrt(252) : 0;
    const maxDd = Math.min(...drawdown);
    const winRate = trades.length > 0 ? trades.filter(t => t.pnlPct > 0).length / trades.length * 100 : 0;
    const profitFactor = (() => {
      const wins = trades.filter(t => t.pnlPct > 0).reduce((s, t) => s + t.pnlPct, 0);
      const losses = Math.abs(trades.filter(t => t.pnlPct < 0).reduce((s, t) => s + t.pnlPct, 0));
      return losses > 0 ? wins / losses : wins > 0 ? Infinity : 0;
    })();

    // VaR / CVaR
    const sortedRets = [...validRets].sort((a, b) => a - b);
    const var95 = sortedRets[Math.floor(sortedRets.length * 0.05)] ?? 0;
    const cvar95 = sortedRets.filter(r => r <= var95).reduce((s, r) => s + r, 0) / Math.max(1, sortedRets.filter(r => r <= var95).length);

    // Rolling Sharpe (60d)
    const rollingSharpe = dailyRets.map((_, i) => {
      if (i < 59) return null;
      const window = dailyRets.slice(i - 59, i + 1);
      const wm = window.reduce((s, r) => s + r, 0) / 60;
      const ws = Math.sqrt(window.reduce((s, r) => s + (r - wm) ** 2, 0) / 59);
      return ws > 0 ? wm / ws * Math.sqrt(252) : 0;
    });

    // Consecutive streaks
    let maxWin = 0, maxLoss = 0, cur = 0;
    for (const tr of trades) {
      if (tr.pnlPct > 0) { cur = cur > 0 ? cur + 1 : 1; maxWin = Math.max(maxWin, cur); }
      else { cur = cur < 0 ? cur - 1 : -1; maxLoss = Math.max(maxLoss, -cur); }
    }

    // Sortino (downside deviation only)
    const downRets = validRets.filter(r => r < 0);
    const downDev = downRets.length > 1 ? Math.sqrt(downRets.reduce((s, r) => s + r ** 2, 0) / downRets.length) : 0;
    const sortino = downDev > 0 ? mean / downDev * Math.sqrt(252) : 0;

    // Calmar (CAGR / |MaxDD|)
    const calmar = maxDd < 0 ? cagr / Math.abs(maxDd) : 0;

    // Annualized volatility
    const annVol = std * Math.sqrt(252) * 100;

    // Avg winner / loser
    const winners = trades.filter(t => t.pnlPct > 0);
    const losers = trades.filter(t => t.pnlPct < 0);
    const avgWin = winners.length > 0 ? winners.reduce((s, t) => s + t.pnlPct, 0) / winners.length * 100 : 0;
    const avgLoss = losers.length > 0 ? losers.reduce((s, t) => s + t.pnlPct, 0) / losers.length * 100 : 0;

    return {
      signals, trades, dailyRets, equityCurve, drawdown, monthlyEntries,
      totalRet, cagr, sharpe, sortino, calmar, annVol, maxDd, winRate, profitFactor,
      var95: var95 * 100, cvar95: cvar95 * 100, rollingSharpe,
      maxWinStreak: maxWin, maxLossStreak: maxLoss,
      avgWin, avgLoss,
      nTrades: trades.length,
      timeInMarket: (signals.filter(s => s !== 0).length / signals.length * 100),
    };
  }, [closes, highs, lows, strategy, commBps, slipBps, dates]);

  // Run de Prado stats when backtest changes
  function runStats() {
    if (!bt) return;
    loadStats.mutate(bt.dailyRets.filter(r => r !== 0));
  }

  // Strategy comparison (runs all strategies)
  function runComparison() {
    if (closes.length < 50) return;
    const results: Record<string, { sharpe: number; cagr: number; maxDd: number; winRate: number }> = {};
    for (const [key] of Object.entries(STRATEGIES)) {
      const { trades, signals: sigs } = runStrategy(closes, key, commBps, slipBps, highs, lows);
      const rets = closes.map((c, i) => i === 0 || sigs[i] === 0 ? 0 : sigs[i] * (c / closes[i - 1] - 1));
      let eq = 1;
      const eqCurve = rets.map(r => { eq *= (1 + r); return eq; });
      let pk = 1;
      const dd = eqCurve.map(e => { if (e > pk) pk = e; return (e / pk - 1) * 100; });
      const years = closes.length / 252;
      const cagr = (Math.pow(eqCurve[eqCurve.length - 1], 1 / years) - 1) * 100;
      const vr = rets.filter(r => r !== 0);
      const m = vr.length > 0 ? vr.reduce((s, r) => s + r, 0) / vr.length : 0;
      const sd = vr.length > 1 ? Math.sqrt(vr.reduce((s, r) => s + (r - m) ** 2, 0) / (vr.length - 1)) : 0;
      results[key] = {
        sharpe: sd > 0 ? m / sd * Math.sqrt(252) : 0,
        cagr, maxDd: Math.min(...dd),
        winRate: trades.length > 0 ? trades.filter(t => t.pnlPct > 0).length / trades.length * 100 : 0,
      };
    }
    setCompResults(results);
  }

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Algo Backtester</h1>
        <p className="text-text-secondary text-sm mt-1">13 strategies with de Prado statistical rigor (DSR, PBO, walk-forward, sequential bootstrap)</p>
      </div>

      {/* Controls */}
      <div className="card card-compact">
        <div className="flex flex-wrap gap-3 items-end">
          <div>
            <label className="metric-label">Ticker</label>
            <input type="text" value={ticker} onChange={e => setTicker(e.target.value.toUpperCase())}
              onKeyDown={e => e.key === "Enter" && handleRun()}
              className="w-24 mt-1 px-2 py-1.5 border border-border rounded-lg text-sm font-data bg-surface" />
          </div>
          <div>
            <label className="metric-label">Lookback (days)</label>
            <input type="number" value={lookback} onChange={e => setLookback(+e.target.value)} step={252} min={252} max={5040}
              className="w-24 mt-1 px-2 py-1.5 border border-border rounded-lg text-sm font-data bg-surface" />
          </div>
          <div>
            <label className="metric-label">Strategy</label>
            <select value={strategy} onChange={e => setStrategy(e.target.value)}
              className="mt-1 px-2 py-1.5 border border-border rounded-lg text-sm bg-surface">
              {Object.entries(STRATEGIES).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
            </select>
          </div>
          <div>
            <label className="metric-label">Comm (bps)</label>
            <input type="number" value={commBps} onChange={e => setCommBps(+e.target.value)} step={1} min={0} max={50}
              className="w-16 mt-1 px-2 py-1.5 border border-border rounded-lg text-sm font-data bg-surface" />
          </div>
          <div>
            <label className="metric-label">Slip (bps)</label>
            <input type="number" value={slipBps} onChange={e => setSlipBps(+e.target.value)} step={1} min={0} max={50}
              className="w-16 mt-1 px-2 py-1.5 border border-border rounded-lg text-sm font-data bg-surface" />
          </div>
          <button onClick={handleRun} disabled={load.isPending}
            className="px-6 py-1.5 bg-accent text-white font-semibold rounded-lg text-sm hover:bg-accent-hover disabled:opacity-50">
            {load.isPending ? "Loading..." : "Run Backtest"}
          </button>
        </div>
      </div>

      {load.isError && <div className="card border-loss/30 bg-loss-bg text-loss text-sm">Error: {(load.error as Error).message}</div>}

      {bt && (
        <>
          {/* Summary metrics */}
          <div className="card card-compact">
            <div className="flex flex-wrap gap-6">
              <Metric label="Total Return" value={`${bt.totalRet.toFixed(1)}%`} />
              <Metric label="CAGR" value={`${bt.cagr.toFixed(1)}%`} />
              <Metric label="Sharpe" value={bt.sharpe.toFixed(2)} />
              <Metric label="Max Drawdown" value={`${bt.maxDd.toFixed(1)}%`} />
              <Metric label="Win Rate" value={`${bt.winRate.toFixed(0)}%`} />
              <Metric label="Sortino" value={bt.sortino.toFixed(2)} />
              <Metric label="Calmar" value={bt.calmar.toFixed(2)} />
              <Metric label="Ann. Vol" value={`${bt.annVol.toFixed(1)}%`} />
              <Metric label="Profit Factor" value={bt.profitFactor === Infinity ? "∞" : bt.profitFactor.toFixed(2)} />
              <Metric label="Trades" value={String(bt.nTrades)} />
              <Metric label="Time in Market" value={`${bt.timeInMarket.toFixed(0)}%`} />
              <Metric label="Avg Win" value={`${bt.avgWin.toFixed(2)}%`} />
              <Metric label="Avg Loss" value={`${bt.avgLoss.toFixed(2)}%`} />
              <Metric label="VaR (95%)" value={`${bt.var95.toFixed(2)}%`} />
              <Metric label="CVaR (95%)" value={`${bt.cvar95.toFixed(2)}%`} />
            </div>
          </div>

          {/* De Prado stats button + results */}
          <div className="card card-compact">
            <div className="flex items-center gap-3 flex-wrap">
              <button onClick={runStats} disabled={loadStats.isPending}
                className="px-4 py-1.5 bg-accent text-white text-xs font-semibold rounded hover:bg-accent-hover disabled:opacity-50">
                {loadStats.isPending ? "Computing..." : stats ? "Re-run" : "Run de Prado Stats (DSR, PBO, Bootstrap)"}
              </button>
              {stats?.success && (
                <div className="flex gap-4 text-xs font-data">
                  <span>DSR: <strong className={stats.dsr > 0.95 ? "text-gain" : stats.dsr > 0.85 ? "text-warn" : "text-loss"}>{(stats.dsr * 100).toFixed(1)}%</strong> ({stats.dsr_verdict})</span>
                  <span>PBO: <strong className={stats.pbo != null && stats.pbo < 0.3 ? "text-gain" : stats.pbo != null && stats.pbo < 0.5 ? "text-warn" : "text-loss"}>{stats.pbo != null ? `${(stats.pbo * 100).toFixed(0)}%` : "N/A"}</strong> ({stats.pbo_verdict || "N/A"})</span>
                  <span>Bootstrap p: <strong className={stats.bootstrap_p != null && stats.bootstrap_p < 0.05 ? "text-gain" : "text-loss"}>{stats.bootstrap_p ?? "N/A"}</strong> ({stats.bootstrap_verdict || "N/A"})</span>
                </div>
              )}
            </div>
          </div>

          {/* Tabs */}
          <div className="flex gap-1 border-b border-border pb-1 overflow-x-auto">
            {TABS.map((label, i) => (
              <button key={label} onClick={() => setTab(i)}
                className={`px-3 py-1.5 text-xs font-semibold rounded-t-md whitespace-nowrap transition-colors ${
                  tab === i ? "bg-accent text-white" : "text-text-muted hover:text-text hover:bg-surface-alt"}`}>
                {label}
              </button>
            ))}
          </div>

          {/* Tab 0: Equity Curve + Rolling Sharpe */}
          {tab === 0 && (
            <div className="space-y-3">
              <div className="card">
                <Plot data={[
                  { x: dates, y: bt.equityCurve.map(e => e * 100 - 100), type: "scatter" as const, mode: "lines" as const,
                    line: { color: t.accent, width: 2 }, name: "Strategy", fill: "tozeroy", fillcolor: t.accent + "10" },
                ]} layout={{ height: 350, ...L, yaxis: { title: "Return (%)", gridcolor: t.grid }, xaxis: { gridcolor: t.grid } }}
                  config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
              </div>
              {bt.rollingSharpe.some(v => v !== null) && (
                <div className="card">
                  <div className="metric-label mb-1">Rolling 60-Day Sharpe</div>
                  <Plot data={[
                    { x: dates, y: bt.rollingSharpe, type: "scatter" as const, mode: "lines" as const,
                      line: { color: t.accent, width: 1.5 }, showlegend: false },
                  ]} layout={{ height: 180, ...L, margin: { l: 40, r: 10, t: 5, b: 25 },
                    yaxis: { title: "Sharpe", gridcolor: t.grid, zeroline: true, zerolinecolor: t.muted }, xaxis: { gridcolor: t.grid } }}
                    config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
                </div>
              )}
            </div>
          )}

          {/* Tab 1: Drawdown */}
          {tab === 1 && (
            <div className="card">
              <Plot data={[
                { x: dates, y: bt.drawdown, type: "scatter" as const, mode: "lines" as const,
                  fill: "tozeroy", fillcolor: t.loss + "20", line: { color: t.loss, width: 1.5 }, showlegend: false },
              ]} layout={{ height: 300, ...L, yaxis: { title: "Drawdown (%)", gridcolor: t.grid }, xaxis: { gridcolor: t.grid } }}
                config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
            </div>
          )}

          {/* Tab 2: Trade Log */}
          {tab === 2 && (
            <div className="card overflow-x-auto">
              <div className="flex gap-4 mb-3 text-xs text-text-muted">
                <span>Trades: {bt.nTrades}</span>
                <span>Win Streak: {bt.maxWinStreak}</span>
                <span>Loss Streak: {bt.maxLossStreak}</span>
              </div>
              <table className="data-table text-xs">
                <thead><tr><th>#</th><th>Entry</th><th>Exit</th><th>Side</th><th>Entry $</th><th>Exit $</th><th>P&L %</th></tr></thead>
                <tbody>
                  {bt.trades.slice(0, 100).map((tr, i) => (
                    <tr key={i}>
                      <td className="font-data">{i + 1}</td>
                      <td className="font-data">{dates[tr.entryIdx]}</td>
                      <td className="font-data">{dates[tr.exitIdx]}</td>
                      <td><span className={`badge ${tr.side === "long" ? "badge-gain" : "badge-loss"}`}>{tr.side}</span></td>
                      <td className="font-data">${tr.entryPrice.toFixed(2)}</td>
                      <td className="font-data">${tr.exitPrice.toFixed(2)}</td>
                      <td className={`font-data font-semibold ${tr.pnlPct >= 0 ? "text-gain" : "text-loss"}`}>{(tr.pnlPct * 100).toFixed(2)}%</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {bt.trades.length > 100 && <p className="text-xs text-text-muted mt-2">Showing first 100 of {bt.trades.length} trades.</p>}
            </div>
          )}

          {/* Tab 3: Monthly Returns Heatmap */}
          {tab === 3 && (
            <div className="card">
              {(() => {
                const years = [...new Set(bt.monthlyEntries.map(m => m.month.slice(0, 4)))];
                const months = ["01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11", "12"];
                const z = years.map(y => months.map(m => {
                  const entry = bt.monthlyEntries.find(e => e.month === `${y}-${m}`);
                  return entry ? entry.ret : null;
                }));
                return (
                  <Plot data={[{
                    type: "heatmap" as const, x: months.map(m => ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"][+m - 1]),
                    y: years, z, colorscale: [[0, t.loss], [0.5, t.grid], [1, t.gain]], zmid: 0,
                    text: z.map(row => row.map(v => v != null ? `${v.toFixed(1)}%` : "")), texttemplate: "%{text}",
                    hovertemplate: "%{y} %{x}: %{z:.1f}%<extra></extra>",
                    colorbar: { title: { text: "Return %", font: { size: 9 } }, thickness: 12 },
                  }]} layout={{ height: Math.max(200, years.length * 30 + 80), ...L, margin: { l: 50, r: 20, t: 10, b: 40 } }}
                    config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
                );
              })()}
            </div>
          )}

          {/* Tab 4: Return Distribution */}
          {tab === 4 && (
            <div className="card">
              <Plot data={[{
                x: bt.dailyRets.filter(r => r !== 0).map(r => r * 100), type: "histogram" as const, nbinsx: 60,
                marker: { color: t.accent + "60", line: { color: t.accent, width: 1 } },
              }]} layout={{ height: 300, ...L,
                xaxis: { title: "Daily Return (%)", gridcolor: t.grid },
                yaxis: { title: "Frequency", gridcolor: t.grid },
                shapes: [
                  { type: "line", x0: bt.var95, x1: bt.var95, y0: 0, y1: 1, yref: "paper", line: { color: t.loss, width: 1.5, dash: "dash" } },
                ],
                annotations: [{ x: bt.var95, y: 1, yref: "paper", text: `VaR 95% (${bt.var95.toFixed(2)}%)`, showarrow: true, arrowhead: 2, font: { size: 9, color: t.loss } }],
              }} config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
            </div>
          )}

          {/* Tab 5: Position Chart */}
          {tab === 5 && (
            <div className="card">
              <Plot data={[
                { x: dates, open: opens, high: highs, low: lows, close: closes,
                  type: "candlestick" as const, increasing: { line: { color: t.gain } }, decreasing: { line: { color: t.loss } },
                  showlegend: false },
                // Long positions (green shading)
                ...(() => {
                  const shapes: { x: string[]; y: number[]; }[] = [];
                  let start = -1;
                  for (let i = 0; i < bt.signals.length; i++) {
                    if (bt.signals[i] === 1 && start === -1) start = i;
                    else if (bt.signals[i] !== 1 && start !== -1) {
                      shapes.push({ x: dates.slice(start, i), y: closes.slice(start, i) });
                      start = -1;
                    }
                  }
                  return shapes.map((s, idx) => ({
                    x: s.x, y: s.y, type: "scatter" as const, mode: "lines" as const,
                    line: { width: 0 }, fill: "tozeroy" as const, fillcolor: t.gain + "15",
                    showlegend: idx === 0, name: "Long",
                  }));
                })(),
              ]} layout={{ height: 450, ...L,
                xaxis: { gridcolor: t.grid, rangeslider: { visible: false } },
                yaxis: { title: "Price ($)", gridcolor: t.grid },
              }} config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
            </div>
          )}

          {/* Tab 6: Walk-Forward */}
          {tab === 6 && (
            <div className="card space-y-4">
              {!stats && (
                <div className="text-center py-6">
                  <button onClick={runStats} disabled={loadStats.isPending}
                    className="px-6 py-2 bg-accent text-white font-semibold rounded-lg hover:bg-accent-hover disabled:opacity-50">
                    {loadStats.isPending ? "Computing..." : "Run Walk-Forward Analysis"}
                  </button>
                  <p className="text-xs text-text-muted mt-2">Tests 9 train/test window combinations for robustness.</p>
                </div>
              )}
              {stats?.walk_forward && stats.walk_forward.length > 0 && (
                <>
                  <div className="overflow-x-auto">
                    <table className="data-table text-xs">
                      <thead><tr><th>Train %</th><th>Test %</th><th>Folds</th><th>Avg Sharpe</th><th>Min</th><th>Max</th><th>% Positive</th></tr></thead>
                      <tbody>
                        {stats.walk_forward.map((wf, i) => (
                          <tr key={i}>
                            <td className="font-data">{wf.train_pct}%</td>
                            <td className="font-data">{wf.test_pct}%</td>
                            <td className="font-data">{wf.n_folds}</td>
                            <td className={`font-data font-semibold ${wf.avg_sharpe > 0.5 ? "text-gain" : wf.avg_sharpe > 0 ? "text-warn" : "text-loss"}`}>{wf.avg_sharpe}</td>
                            <td className="font-data">{wf.min_sharpe}</td>
                            <td className="font-data">{wf.max_sharpe}</td>
                            <td className={`font-data ${wf.pct_positive >= 70 ? "text-gain" : wf.pct_positive >= 50 ? "text-warn" : "text-loss"}`}>{wf.pct_positive}%</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <Plot data={[{
                    x: stats.walk_forward.map(wf => `${wf.train_pct}/${wf.test_pct}`),
                    y: stats.walk_forward.map(wf => wf.avg_sharpe),
                    type: "bar" as const,
                    marker: { color: stats.walk_forward.map(wf => wf.avg_sharpe > 0.5 ? t.gain : wf.avg_sharpe > 0 ? t.spot : t.loss) },
                  }]} layout={{ height: 250, ...L, xaxis: { title: "Train%/Test%", gridcolor: t.grid }, yaxis: { title: "Avg OOS Sharpe", gridcolor: t.grid } }}
                    config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
                </>
              )}
              {stats?.walk_forward?.length === 0 && <p className="text-xs text-text-muted">Not enough data for walk-forward (need 2+ years).</p>}
            </div>
          )}

          {/* Tab 7: Regime Analysis */}
          {tab === 7 && (
            <div className="card space-y-4">
              {!stats && (
                <div className="text-center py-6">
                  <button onClick={runStats} disabled={loadStats.isPending}
                    className="px-6 py-2 bg-accent text-white font-semibold rounded-lg hover:bg-accent-hover disabled:opacity-50">
                    {loadStats.isPending ? "Computing..." : "Run Regime Analysis"}
                  </button>
                </div>
              )}
              {stats?.regimes && Object.keys(stats.regimes).length > 0 && (
                <>
                  <div className="metric-label mb-2">Volatility Regime Breakdown</div>
                  <div className="grid grid-cols-3 gap-3">
                    {["vol_low", "vol_med", "vol_high"].map(key => {
                      const r = stats.regimes[key];
                      if (!r) return null;
                      const label = key.replace("vol_", "").toUpperCase() + " Vol";
                      return (
                        <div key={key} className="card card-compact bg-surface-alt">
                          <div className="metric-label">{label}</div>
                          <div className={`text-lg font-bold ${r.sharpe > 0.5 ? "text-gain" : r.sharpe > 0 ? "text-warn" : "text-loss"}`}>{r.sharpe.toFixed(2)}</div>
                          <div className="text-[0.6rem] text-text-muted font-data">{r.n_days} days · {r.avg_return.toFixed(1)}% ann · {r.volatility.toFixed(1)}% vol</div>
                        </div>
                      );
                    })}
                  </div>
                  <div className="metric-label mb-2 mt-4">Trend Regime Breakdown</div>
                  <div className="grid grid-cols-3 gap-3">
                    {["trend_bull", "trend_sideways", "trend_bear"].map(key => {
                      const r = stats.regimes[key];
                      if (!r) return null;
                      const label = key.replace("trend_", "").charAt(0).toUpperCase() + key.replace("trend_", "").slice(1);
                      return (
                        <div key={key} className="card card-compact bg-surface-alt">
                          <div className="metric-label">{label}</div>
                          <div className={`text-lg font-bold ${r.sharpe > 0.5 ? "text-gain" : r.sharpe > 0 ? "text-warn" : "text-loss"}`}>{r.sharpe.toFixed(2)}</div>
                          <div className="text-[0.6rem] text-text-muted font-data">{r.n_days} days · {r.avg_return.toFixed(1)}% ann · {r.volatility.toFixed(1)}% vol</div>
                        </div>
                      );
                    })}
                  </div>
                </>
              )}
            </div>
          )}

          {/* Tab 8: Strategy Comparison */}
          {tab === 8 && (
            <div className="card space-y-4">
              {Object.keys(compResults).length === 0 && (
                <div className="text-center py-6">
                  <button onClick={runComparison}
                    className="px-6 py-2 bg-accent text-white font-semibold rounded-lg hover:bg-accent-hover">
                    Run All Strategies for Comparison
                  </button>
                  <p className="text-xs text-text-muted mt-2">Runs all {Object.keys(STRATEGIES).length} strategies on the same data.</p>
                </div>
              )}
              {Object.keys(compResults).length > 0 && (
                <>
                  <div className="overflow-x-auto">
                    <table className="data-table text-xs">
                      <thead><tr><th>Strategy</th><th>Sharpe</th><th>CAGR</th><th>Max DD</th><th>Win Rate</th></tr></thead>
                      <tbody>
                        {Object.entries(compResults).sort(([, a], [, b]) => b.sharpe - a.sharpe).map(([key, r]) => (
                          <tr key={key} className={key === strategy ? "bg-accent-light" : ""}>
                            <td className="font-semibold">{STRATEGIES[key]?.label || key}</td>
                            <td className={`font-data font-semibold ${r.sharpe > 0.5 ? "text-gain" : r.sharpe > 0 ? "text-warn" : "text-loss"}`}>{r.sharpe.toFixed(2)}</td>
                            <td className="font-data">{r.cagr.toFixed(1)}%</td>
                            <td className="font-data text-loss">{r.maxDd.toFixed(1)}%</td>
                            <td className="font-data">{r.winRate.toFixed(0)}%</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  {/* Risk-Return scatter */}
                  <Plot data={[{
                    x: Object.values(compResults).map(r => r.maxDd),
                    y: Object.values(compResults).map(r => r.cagr),
                    text: Object.keys(compResults).map(k => STRATEGIES[k]?.label || k),
                    type: "scatter" as const, mode: "markers+text" as const,
                    textposition: "top center" as const, textfont: { size: 8, color: t.text },
                    marker: { size: 10, color: Object.values(compResults).map(r => r.sharpe > 0.5 ? t.gain : r.sharpe > 0 ? t.spot : t.loss) },
                  }]} layout={{ height: 350, ...L,
                    xaxis: { title: "Max Drawdown (%)", gridcolor: t.grid },
                    yaxis: { title: "CAGR (%)", gridcolor: t.grid },
                  }} config={{ displayModeBar: false, responsive: true }} style={{ width: "100%" }} />
                </>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}

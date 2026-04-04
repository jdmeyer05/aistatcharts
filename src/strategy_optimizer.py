"""Batch strategy parameter optimization with Supabase caching.

Runs Optuna TPE optimization for each strategy × ticker combination.
Stores optimal parameters in Supabase. The strategy scan loads cached
params instead of using defaults.
"""

import logging
import numpy as np
import json
from datetime import datetime

logger = logging.getLogger(__name__)

# Parameter search spaces per strategy
PARAM_SPACES = {
    "sma_cross": {"fast": (10, 100, 5), "slow": (50, 300, 10)},
    "ema_cross": {"fast": (5, 50, 1), "slow": (15, 100, 5)},
    "macd": {"fast": (6, 20, 1), "slow": (18, 40, 1), "signal": (5, 15, 1)},
    "rsi_ob_os": {"period": (7, 21, 1), "oversold": (20, 40, 1), "overbought": (60, 80, 1)},
    "mean_rev": {"period": (10, 40, 1), "num_std": (1.5, 3.0, 0.25)},  # float
    "momentum": {"lookback": (42, 252, 21)},
    "donchian": {"period": (10, 50, 5)},
    "stochastic": {"k_period": (5, 21, 1), "d_period": (2, 7, 1), "oversold": (15, 30, 5), "overbought": (70, 85, 5)},
    "parabolic_sar": {"accel": (0.01, 0.04, 0.005), "max_accel": (0.1, 0.3, 0.05)},  # float
    "adx_di": {"period": (7, 28, 1), "threshold": (15, 30, 5)},
    "ichimoku": {"tenkan": (5, 15, 1), "kijun": (18, 40, 2)},
    "tema_cross": {"fast": (5, 20, 1), "slow": (15, 50, 5)},
    "obv_divergence": {"sma_period": (10, 40, 5)},
    "trend_mr_composite": {"sma_period": (100, 300, 50), "rsi_period": (7, 21, 1), "rsi_low": (25, 45, 5), "rsi_high": (55, 75, 5)},
    "trend_bb_composite": {"sma_period": (100, 300, 50), "bb_period": (10, 30, 5), "bb_std": (1.5, 3.0, 0.25)},
}

# Default parameters (current hardcoded values)
DEFAULT_PARAMS = {
    "sma_cross": {"fast": 50, "slow": 200},
    "ema_cross": {"fast": 12, "slow": 26},
    "macd": {"fast": 12, "slow": 26, "signal": 9},
    "rsi_ob_os": {"period": 14, "oversold": 30, "overbought": 70},
    "mean_rev": {"period": 20, "num_std": 2.0},
    "momentum": {"lookback": 126},
    "donchian": {"period": 20},
    "stochastic": {"k_period": 14, "d_period": 3, "oversold": 20, "overbought": 80},
    "parabolic_sar": {"accel": 0.02, "max_accel": 0.2},
    "adx_di": {"period": 14, "threshold": 20},
    "ichimoku": {"tenkan": 9, "kijun": 26},
    "tema_cross": {"fast": 8, "slow": 21},
    "obv_divergence": {"sma_period": 20},
    "trend_mr_composite": {"sma_period": 200, "rsi_period": 14, "rsi_low": 40, "rsi_high": 60},
    "trend_bb_composite": {"sma_period": 200, "bb_period": 20, "bb_std": 2.0},
}


def get_cached_params(ticker: str, strategy: str) -> dict | None:
    """Load optimized parameters from Supabase cache."""
    try:
        from src.db import get_client
        sb = get_client()
        if not sb:
            return None
        r = sb.table("strategy_params").select("params,wf_sharpe,optimized_at").eq("ticker", ticker).eq("strategy", strategy).limit(1).execute()
        if r.data:
            row = r.data[0]
            # Check if optimization is recent enough (< 30 days)
            opt_at = datetime.fromisoformat(row["optimized_at"].replace("Z", "+00:00"))
            age_days = (datetime.now(opt_at.tzinfo) - opt_at).days
            if age_days <= 30:
                return row["params"]
    except Exception as e:
        logger.debug(f"Cache miss for {ticker}/{strategy}: {e}")
    return None


def get_default_params(strategy: str) -> dict:
    """Return default parameters for a strategy."""
    return DEFAULT_PARAMS.get(strategy, {})


def save_optimized_params(ticker: str, strategy: str, params: dict, stats: dict):
    """Store optimized parameters in Supabase."""
    try:
        from src.db import get_client
        sb = get_client()
        if not sb:
            return
        sb.table("strategy_params").upsert({
            "ticker": ticker,
            "strategy": strategy,
            "params": params,
            "wf_sharpe": stats.get("wf_sharpe"),
            "sharpe": stats.get("sharpe"),
            "dsr": stats.get("dsr"),
            "win_rate": stats.get("win_rate"),
            "trades": stats.get("trades"),
            "optimized_at": datetime.utcnow().isoformat(),
        }, on_conflict="ticker,strategy").execute()
    except Exception as e:
        logger.warning(f"Failed to save params for {ticker}/{strategy}: {e}")


def optimize_strategy(ticker: str, strategy: str, closes, highs, lows, volumes=None, n_trials=50) -> dict | None:
    """Run Optuna optimization for a single strategy × ticker. Returns optimal params + stats."""
    import talib
    import optuna
    optuna.logging.set_verbosity(optuna.logging.WARNING)

    if strategy not in PARAM_SPACES:
        return None  # not optimizable

    space = PARAM_SPACES[strategy]
    n = len(closes)
    ann = np.sqrt(252)

    def _sma(c, p): return talib.SMA(c, timeperiod=p)
    def _ema(c, p): return talib.EMA(c, timeperiod=p)

    def _generate_signals_with_params(params):
        sig = np.zeros(n)
        try:
            if strategy == "sma_cross":
                f, s = _sma(closes, params["fast"]), _sma(closes, params["slow"])
                warmup = params["slow"] + 1
                for i in range(warmup, n):
                    if not np.isnan(f[i]) and not np.isnan(s[i]):
                        sig[i] = 1 if f[i] > s[i] else -1
            elif strategy == "ema_cross":
                f, s = _ema(closes, params["fast"]), _ema(closes, params["slow"])
                warmup = params["slow"] + 1
                for i in range(warmup, n):
                    if not np.isnan(f[i]) and not np.isnan(s[i]):
                        sig[i] = 1 if f[i] > s[i] else -1
            elif strategy == "macd":
                m, ms, _ = talib.MACD(closes, fastperiod=params["fast"], slowperiod=params["slow"], signalperiod=params["signal"])
                warmup = params["slow"] + params["signal"] + 1
                for i in range(warmup, n):
                    if not np.isnan(m[i]) and not np.isnan(ms[i]):
                        sig[i] = 1 if m[i] > ms[i] else -1
            elif strategy == "rsi_ob_os":
                r = talib.RSI(closes, timeperiod=params["period"])
                pos = 0
                for i in range(params["period"] + 1, n):
                    if not np.isnan(r[i]):
                        if r[i] < params["oversold"]: pos = 1
                        elif r[i] > params["overbought"]: pos = -1
                        sig[i] = pos
            elif strategy == "mean_rev":
                upper, _, lower = talib.BBANDS(closes, timeperiod=params["period"], nbdevup=params["num_std"], nbdevdn=params["num_std"])
                pos = 0
                for i in range(params["period"] + 1, n):
                    if not np.isnan(lower[i]):
                        if closes[i] < lower[i]: pos = 1
                        elif closes[i] > upper[i]: pos = -1
                        sig[i] = pos
            elif strategy == "momentum":
                lb = params["lookback"]
                for i in range(lb, n):
                    sig[i] = 1 if closes[i] > closes[i - lb] else -1
            elif strategy == "donchian":
                p = params["period"]
                for i in range(p, n):
                    hh = np.max(highs[i-p:i])
                    ll = np.min(lows[i-p:i])
                    if closes[i] > hh: sig[i] = 1
                    elif closes[i] < ll: sig[i] = -1
                    else: sig[i] = sig[i-1] if i > 0 else 0
            elif strategy == "stochastic":
                k, d = talib.STOCH(highs, lows, closes, fastk_period=params["k_period"], slowk_period=params["d_period"], slowd_period=params["d_period"])
                pos = 0
                for i in range(params["k_period"] + params["d_period"], n):
                    if not np.isnan(k[i]):
                        if k[i] < params["oversold"]: pos = 1
                        elif k[i] > params["overbought"]: pos = -1
                        sig[i] = pos
            elif strategy == "parabolic_sar":
                sar = talib.SAR(highs, lows, acceleration=params["accel"], maximum=params["max_accel"])
                for i in range(5, n):
                    if not np.isnan(sar[i]):
                        sig[i] = 1 if closes[i] > sar[i] else -1
            elif strategy == "adx_di":
                adx = talib.ADX(highs, lows, closes, timeperiod=params["period"])
                pdi = talib.PLUS_DI(highs, lows, closes, timeperiod=params["period"])
                mdi = talib.MINUS_DI(highs, lows, closes, timeperiod=params["period"])
                for i in range(params["period"] * 2, n):
                    if not np.isnan(adx[i]) and adx[i] > params["threshold"]:
                        sig[i] = 1 if pdi[i] > mdi[i] else -1
            elif strategy == "ichimoku":
                th = talib.MAX(highs, params["tenkan"])
                tl = talib.MIN(lows, params["tenkan"])
                kh = talib.MAX(highs, params["kijun"])
                kl = talib.MIN(lows, params["kijun"])
                for i in range(params["kijun"] * 2, n):
                    tenkan = (th[i] + tl[i]) / 2
                    kijun = (kh[i] + kl[i]) / 2
                    sig[i] = 1 if closes[i] > kijun and tenkan > kijun else (-1 if closes[i] < kijun and tenkan < kijun else 0)
            elif strategy == "tema_cross":
                f = talib.TEMA(closes, timeperiod=params["fast"])
                s = talib.TEMA(closes, timeperiod=params["slow"])
                for i in range(params["slow"] + 1, n):
                    if not np.isnan(f[i]) and not np.isnan(s[i]):
                        sig[i] = 1 if f[i] > s[i] else -1
            elif strategy == "obv_divergence":
                if volumes is not None:
                    obv = np.zeros(n)
                    for i in range(1, n):
                        obv[i] = obv[i-1] + (volumes[i] if closes[i] > closes[i-1] else (-volumes[i] if closes[i] < closes[i-1] else 0))
                    obv_sma = _sma(obv, params["sma_period"])
                    for i in range(params["sma_period"] + 10, n):
                        if not np.isnan(obv_sma[i]):
                            sig[i] = 1 if obv[i] > obv_sma[i] else -1
            elif strategy == "trend_mr_composite":
                sma = _sma(closes, params["sma_period"])
                r = talib.RSI(closes, timeperiod=params["rsi_period"])
                for i in range(params["sma_period"] + 1, n):
                    if not np.isnan(sma[i]) and not np.isnan(r[i]):
                        if closes[i] > sma[i] and r[i] < params["rsi_low"]: sig[i] = 1
                        elif closes[i] < sma[i] and r[i] > params["rsi_high"]: sig[i] = -1
            elif strategy == "trend_bb_composite":
                sma = _sma(closes, params["sma_period"])
                upper, _, lower = talib.BBANDS(closes, timeperiod=params["bb_period"], nbdevup=params["bb_std"], nbdevdn=params["bb_std"])
                for i in range(params["sma_period"] + 1, n):
                    if not np.isnan(sma[i]) and not np.isnan(lower[i]):
                        if closes[i] > sma[i] and closes[i] < lower[i]: sig[i] = 1
                        elif closes[i] < sma[i] and closes[i] > upper[i]: sig[i] = -1
        except Exception:
            pass
        return sig

    def _compute_wf_sharpe(signals):
        """Walk-forward OOS Sharpe on active days."""
        daily_rets = np.zeros(n)
        for i in range(1, n):
            if signals[i] != 0:
                daily_rets[i] = signals[i] * (closes[i] / closes[i - 1] - 1)

        # Walk-forward
        test_size = max(n // 5, 63)
        train_size = max(int(n * 0.6), 252)
        wf_sharpes = []
        start = 0
        while start + train_size + test_size <= n:
            test_start = start + train_size
            test_end = test_start + test_size
            test_rets = daily_rets[test_start:test_end]
            test_sigs = signals[test_start:test_end]
            test_active = test_rets[test_sigs != 0]
            if len(test_active) >= 10:
                tm = float(np.mean(test_active))
                ts = float(np.std(test_active, ddof=1))
                wf_sharpes.append(tm / ts * ann if ts > 0 else 0)
            start += test_size
        return float(np.mean(wf_sharpes)) if wf_sharpes else 0

    # Run Optuna
    def objective(trial):
        params = {}
        for key, (lo, hi, step) in space.items():
            if isinstance(step, float):
                params[key] = trial.suggest_float(key, lo, hi, step=step)
            else:
                params[key] = trial.suggest_int(key, lo, hi, step=step)

        # Constraint: fast < slow for cross strategies
        if "fast" in params and "slow" in params and params["fast"] >= params["slow"]:
            return -10

        signals = _generate_signals_with_params(params)
        return _compute_wf_sharpe(signals)

    study = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler(seed=42))
    study.optimize(objective, n_trials=n_trials, timeout=30)

    best_params = study.best_params
    best_signals = _generate_signals_with_params(best_params)

    # Compute full stats with best params
    daily_rets = np.zeros(n)
    for i in range(1, n):
        if best_signals[i] != 0:
            daily_rets[i] = best_signals[i] * (closes[i] / closes[i - 1] - 1)

    warmup = max(200, int(n * 0.1))
    active_mask = best_signals[warmup:] != 0
    active_rets = daily_rets[warmup:][active_mask]
    if len(active_rets) < 10:
        return None

    mean_r = float(np.mean(active_rets))
    std_r = float(np.std(active_rets, ddof=1))
    sharpe = mean_r / std_r * ann if std_r > 0 else 0
    wf_sharpe = _compute_wf_sharpe(best_signals)

    # Trade count + win rate
    trades, wins, pos, entry_px = 0, 0, 0, 0.0
    for i in range(1, n):
        if best_signals[i] != 0 and pos == 0:
            pos = int(best_signals[i]); entry_px = closes[i]
        elif pos != 0 and best_signals[i] != pos:
            pnl = (closes[i] / entry_px - 1) * pos
            trades += 1
            if pnl > 0: wins += 1
            pos = int(best_signals[i]) if best_signals[i] != 0 else 0
            entry_px = closes[i] if pos != 0 else 0
    win_rate = round(wins / max(trades, 1) * 100, 1)

    return {
        "params": best_params,
        "wf_sharpe": round(wf_sharpe, 3),
        "sharpe": round(sharpe, 3),
        "trades": trades,
        "win_rate": win_rate,
    }

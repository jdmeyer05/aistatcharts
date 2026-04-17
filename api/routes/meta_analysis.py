"""Meta Analysis — walk-forward backtest + De Prado statistical tests.

Runs 9 allocation methods (Tangency, Robust Sharpe, Min Variance, Risk Parity,
Max Diversification, HRP, HERC-CVaR, HCAA, Equal Weight) + custom blends against
a user-supplied ticker universe, with SPY buy-and-hold as benchmark.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from itertools import combinations
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from scipy.optimize import minimize
from scipy.stats import kurtosis as sp_kurtosis, norm, skew as sp_skew

from api.deps import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()


BASE_METHODS = [
    "Tangency", "Robust Sharpe", "Min Variance", "Risk Parity",
    "Max Diversification", "HRP", "HERC (CVaR)", "HCAA (1/N)", "Equal Weight",
]

PRESET_GROUPS: Dict[str, List[str]] = {
    "Multi-Asset": ["SPY", "TLT", "GLD", "EFA", "IWM", "USO", "HYG", "VNQ"],
    "Sector ETFs": ["XLE", "XLF", "XLK", "XLV", "XLI", "XLC", "XLY", "XLP", "XLU", "XLB", "XLRE"],
    "Energy": ["XOM", "CVX", "COP", "EOG", "SLB", "MPC", "OXY", "PSX", "VLO", "DVN"],
    "Technology": ["AAPL", "MSFT", "NVDA", "AVGO", "CRM", "ORCL", "AMD", "ADBE", "ACN", "CSCO"],
    "Financials": ["JPM", "BAC", "WFC", "GS", "MS", "BLK", "SCHW", "C", "AXP", "MMC"],
    "Healthcare": ["UNH", "LLY", "JNJ", "ABBV", "MRK", "TMO", "ABT", "AMGN", "DHR", "PFE"],
    "Industrials": ["GE", "CAT", "UNP", "HON", "RTX", "DE", "LMT", "BA", "ETN", "ADP"],
    "Communication": ["META", "GOOGL", "NFLX", "T", "CMCSA", "VZ", "DIS", "TMUS", "EA", "CHTR"],
    "Consumer Disc": ["AMZN", "TSLA", "HD", "MCD", "NKE", "LOW", "BKNG", "SBUX", "TJX", "CMG"],
    "Consumer Staples": ["PG", "COST", "WMT", "KO", "PEP", "PM", "MDLZ", "MO", "CL", "STZ"],
    "Utilities": ["NEE", "SO", "DUK", "CEG", "SRE", "AEP", "D", "EXC", "XEL", "PEG"],
    "Materials": ["LIN", "SHW", "APD", "ECL", "FCX", "NUE", "NEM", "VMC", "MLM", "DOW"],
    "Real Estate": ["PLD", "AMT", "EQIX", "SPG", "PSA", "O", "WELL", "DLR", "VICI", "CCI"],
    "Mega Caps": ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "BRK-B", "JPM", "V"],
    "Global Macro": ["SPY", "EFA", "EEM", "TLT", "IEF", "GLD", "USO", "UNG", "DBA", "UUP"],
}

STRESS_SCENARIOS = {
    "2008 GFC": -0.38, "COVID Mar 2020": -0.34,
    "2022 Rate Shock": -0.19, "2018 Q4 Selloff": -0.14,
    "2015 China Deval": -0.10, "Flash Crash 2010": -0.07,
}

COST_BPS = 10  # 10 bps per round-trip


# ═══════════════════════════════════════════════
# PRICE DATA
# ═══════════════════════════════════════════════

def _lookback_days(label: str) -> int:
    return {"1Y": 365, "2Y": 730, "3Y": 1095, "5Y": 1825}.get(label, 730)


def _fetch_prices(tickers: List[str], days: int) -> pd.DataFrame:
    """Fetch close prices for all tickers. Uses Polygon via fetch_massive_data."""
    from src.data_engine import fetch_massive_data

    def _one(tk: str):
        try:
            df = fetch_massive_data(tk, days)
            if df is None or df.empty:
                return tk, None
            s = df["Close"].copy()
            s.name = tk
            return tk, s
        except Exception as e:
            logger.warning(f"price fetch failed for {tk}: {e}")
            return tk, None

    results: Dict[str, pd.Series] = {}
    with ThreadPoolExecutor(max_workers=10) as ex:
        for tk, s in ex.map(_one, tickers):
            if s is not None:
                results[tk] = s

    if not results:
        return pd.DataFrame()
    return pd.DataFrame(results).sort_index()


# ═══════════════════════════════════════════════
# OPTIMIZATION
# ═══════════════════════════════════════════════

def _tangency(mu, cov):
    n = len(mu)
    w0 = np.full(n, 1 / n)
    cons = [{"type": "eq", "fun": lambda w: np.sum(w) - 1}]
    bounds = [(0, 1)] * n

    def neg_sharpe(w):
        vol = np.sqrt(w @ cov @ w)
        return -(w @ mu) / vol if vol > 1e-12 else 1e10

    r = minimize(neg_sharpe, w0, method="SLSQP", bounds=bounds, constraints=cons)
    return r.x if r.success else w0


def _robust_sharpe(mu, cov, se):
    n = len(mu)
    w0 = np.full(n, 1 / n)
    mu_r = mu - se
    cons = [{"type": "eq", "fun": lambda w: np.sum(w) - 1}]
    bounds = [(0, 1)] * n

    def obj(w):
        vol = np.sqrt(w @ cov @ w)
        return -(w @ mu_r) / vol if vol > 1e-12 else 1e10

    r = minimize(obj, w0, method="SLSQP", bounds=bounds, constraints=cons)
    return r.x if r.success else w0


def _min_var(cov):
    n = cov.shape[0]
    w0 = np.full(n, 1 / n)
    cons = [{"type": "eq", "fun": lambda w: np.sum(w) - 1}]
    bounds = [(0, 1)] * n
    r = minimize(lambda w: np.sqrt(w @ cov @ w), w0, method="SLSQP",
                 bounds=bounds, constraints=cons)
    return r.x if r.success else w0


def _risk_parity(cov):
    n = cov.shape[0]
    w0 = np.full(n, 1 / n)
    min_w = max(0.001, 1 / (n * 5))

    def obj(w):
        pv = np.sqrt(w @ cov @ w)
        if pv == 0: return 0
        mrc = cov @ w / pv
        rc = w * mrc
        target = pv / n
        return np.sum((rc - target) ** 2)

    cons = [{"type": "eq", "fun": lambda w: np.sum(w) - 1}]
    bounds = [(min_w, 1)] * n
    r = minimize(obj, w0, method="SLSQP", bounds=bounds, constraints=cons)
    return r.x if r.success else w0


def _max_div(cov):
    n = cov.shape[0]
    vols = np.sqrt(np.diag(cov))
    w0 = np.full(n, 1 / n)
    cons = [{"type": "eq", "fun": lambda w: np.sum(w) - 1}]
    bounds = [(0, 1)] * n

    def obj(w):
        pv = np.sqrt(w @ cov @ w)
        return -(w @ vols) / pv if pv > 0 else 0

    r = minimize(obj, w0, method="SLSQP", bounds=bounds, constraints=cons)
    return r.x if r.success else w0


def _compute_all_weights(est_ret: pd.DataFrame, tickers: List[str], use_dn: bool,
                          blends: Optional[Dict] = None) -> Dict[str, np.ndarray]:
    from src.quant_features import (
        denoise_covariance, hrp_allocate, herc_allocate, hcaa_allocate,
    )
    n = len(tickers)
    est_mu = est_ret.mean().values
    est_cov = est_ret.cov().values
    mu_se = est_ret.std().values / np.sqrt(len(est_ret))

    if use_dn:
        try:
            dn_cov, dn_corr = denoise_covariance(est_ret)
        except Exception:
            dn_cov = pd.DataFrame(est_cov * 252, index=tickers, columns=tickers)
            dn_corr = est_ret.corr()
    else:
        dn_cov = pd.DataFrame(est_cov * 252, index=tickers, columns=tickers)
        dn_corr = est_ret.corr()

    base = {
        "Tangency": _tangency(est_mu, est_cov),
        "Robust Sharpe": _robust_sharpe(est_mu, est_cov, mu_se),
        "Min Variance": _min_var(est_cov),
        "Risk Parity": _risk_parity(est_cov),
        "Max Diversification": _max_div(est_cov),
        "HRP": hrp_allocate(est_ret, cov=dn_cov, corr=dn_corr).reindex(tickers).fillna(0).values,
        "HERC (CVaR)": herc_allocate(est_ret, cov=dn_cov, corr=dn_corr, risk_metric="cvar").reindex(tickers).fillna(0).values,
        "HCAA (1/N)": hcaa_allocate(est_ret, corr=dn_corr).reindex(tickers).fillna(0).values,
        "Equal Weight": np.full(n, 1 / n),
    }

    if blends:
        for bname, components in blends.items():
            blended = np.zeros(n)
            for method, pct in components.items():
                if method in base:
                    blended += base[method] * pct
            total = blended.sum()
            if total > 0:
                blended /= total
            base[bname] = blended

    return base


# ═══════════════════════════════════════════════
# WALK-FORWARD
# ═══════════════════════════════════════════════

def _walkforward(returns: pd.DataFrame, est_days: int, rebal_period: str,
                 use_dn: bool, blends: Optional[Dict] = None,
                 method_names: Optional[List[str]] = None):
    """Run walk-forward on returns DataFrame. Returns (results_dict, weight_history)."""
    tickers = returns.columns.tolist()
    names = method_names or (BASE_METHODS + list((blends or {}).keys()))

    if len(tickers) < 3 or len(returns) < est_days + 40:
        return {}, {}

    rebal_groups = returns.resample(rebal_period).last()
    rebal_dates = []
    for period_end in rebal_groups.index:
        mask = returns.index <= period_end
        if mask.any():
            actual_date = returns.index[mask][-1]
            loc = returns.index.get_loc(actual_date)
            if loc >= est_days:
                rebal_dates.append(actual_date)

    if len(rebal_dates) < 2:
        return {}, {}

    raw = {m: [] for m in names}
    weight_history = {m: [] for m in names}

    for i in range(len(rebal_dates)):
        rd = rebal_dates[i]
        rd_loc = returns.index.get_loc(rd)
        est_ret = returns.iloc[rd_loc - est_days:rd_loc]

        methods_w = _compute_all_weights(est_ret, tickers, use_dn, blends=blends)

        for method, w in methods_w.items():
            weight_history[method].append({"date": rd, "weights": dict(zip(tickers, w))})

        end = rebal_dates[i + 1] if i < len(rebal_dates) - 1 else returns.index[-1]
        oos = returns.loc[rd:end]
        for method, w in methods_w.items():
            port_ret = oos.values @ w
            for j, dt in enumerate(oos.index):
                raw[method].append({"date": dt, "return": float(port_ret[j])})

    result = {}
    for method, data in raw.items():
        if not data:
            continue
        df = pd.DataFrame(data).set_index("date")
        df = df[~df.index.duplicated(keep="first")]
        result[method] = df["return"]

    return result, weight_history


# ═══════════════════════════════════════════════
# METRICS
# ═══════════════════════════════════════════════

def _portfolio_metrics(daily_returns: pd.Series, name: str,
                       benchmark_returns: Optional[pd.Series] = None) -> Dict:
    ann_ret = float(daily_returns.mean() * 252)
    ann_vol = float(daily_returns.std() * np.sqrt(252))
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0.0
    cum = (1 + daily_returns).cumprod()
    max_dd = float(((cum / cum.cummax()) - 1).min())
    neg = daily_returns[daily_returns < 0]
    downside_vol = float(neg.std() * np.sqrt(252)) if len(neg) > 1 else ann_vol
    sortino = ann_ret / downside_vol if downside_vol > 0 else 0.0
    calmar = ann_ret / abs(max_dd) if abs(max_dd) > 1e-10 else 0.0
    win_rate = float((daily_returns > 0).mean())

    result = {
        "method": name,
        "ann_return": ann_ret,
        "ann_vol": ann_vol,
        "sharpe": float(sharpe),
        "sortino": float(sortino),
        "max_dd": max_dd,
        "calmar": float(calmar),
        "win_rate": win_rate,
    }

    if benchmark_returns is not None:
        common = daily_returns.index.intersection(benchmark_returns.index)
        if len(common) > 20:
            p = daily_returns.loc[common]
            b = benchmark_returns.loc[common]
            excess = p - b
            te = float(excess.std() * np.sqrt(252))
            ir = float(excess.mean() * 252 / te) if te > 0 else 0.0
            up_days = b > 0
            down_days = b < 0
            up_cap = float(p[up_days].mean() / b[up_days].mean()) if up_days.sum() > 5 and abs(b[up_days].mean()) > 1e-10 else 1.0
            dn_cap = float(p[down_days].mean() / b[down_days].mean()) if down_days.sum() > 5 and abs(b[down_days].mean()) > 1e-10 else 1.0
            result["info_ratio"] = ir
            result["tracking_error"] = te
            result["up_capture"] = up_cap
            result["down_capture"] = dn_cap

    return result


def _apply_transaction_costs(daily_ret_dict: Dict[str, pd.Series],
                              weight_history: Dict, cost_bps: int = COST_BPS) -> Dict[str, pd.Series]:
    net: Dict[str, pd.Series] = {}
    for method, gross_ret in daily_ret_dict.items():
        hist = weight_history.get(method, [])
        rebal_costs: Dict = {}
        for j in range(1, len(hist)):
            prev_w = np.array(list(hist[j - 1]["weights"].values()))
            curr_w = np.array(list(hist[j]["weights"].values()))
            turnover = float(np.sum(np.abs(curr_w - prev_w)) / 2)
            rebal_costs[hist[j]["date"]] = turnover * cost_bps / 10000

        if not rebal_costs:
            net[method] = gross_ret
            continue

        net_ret = gross_ret.copy()
        rebal_sorted = sorted(rebal_costs.keys())
        for k, rd in enumerate(rebal_sorted):
            end = rebal_sorted[k + 1] if k < len(rebal_sorted) - 1 else net_ret.index[-1]
            mask = (net_ret.index >= rd) & (net_ret.index <= end)
            n_days = int(mask.sum())
            if n_days > 0:
                net_ret.loc[mask] -= rebal_costs[rd] / n_days
        net[method] = net_ret
    return net


# ═══════════════════════════════════════════════
# DE PRADO STATISTICAL TESTS
# ═══════════════════════════════════════════════

def _deflated_sharpe(observed_sr: float, n_obs: int, n_trials: int,
                     skew: float = 0, kurtosis: float = 3) -> float:
    if n_trials <= 1:
        e_max_sr = 0
    else:
        euler_m = 0.5772156649
        e_max_sr = ((1 - euler_m) * norm.ppf(1 - 1 / n_trials) +
                    euler_m * norm.ppf(1 - 1 / (n_trials * np.e)))
    se_sr = np.sqrt((1 + 0.5 * observed_sr ** 2 - skew * observed_sr +
                     (kurtosis - 3) / 4 * observed_sr ** 2) / (n_obs - 1))
    if se_sr < 1e-10:
        return 0.0
    return float(norm.cdf((observed_sr - e_max_sr) / se_sr))


def _min_track_record(observed_sr: float, n_obs: int,
                       skew: float = 0, kurtosis: float = 3,
                       confidence: float = 0.95) -> float:
    z = norm.ppf(confidence)
    if observed_sr <= 0:
        return float("inf")
    min_n = (1 + (1 - skew * observed_sr + (kurtosis - 3) / 4 * observed_sr ** 2)) * (z / observed_sr) ** 2
    return float(max(1, min_n))


def _pbo_cpcv(daily_ret_dict: Dict[str, pd.Series], n_splits: int = 6):
    methods = list(daily_ret_dict.keys())
    if len(methods) < 2:
        return None, []
    aligned = pd.DataFrame(daily_ret_dict).dropna()
    if len(aligned) < n_splits * 10:
        return None, []

    block = len(aligned) // n_splits
    blocks = [aligned.iloc[i * block:(i + 1) * block] for i in range(n_splits)]
    n_train = n_splits // 2
    combos = list(combinations(range(n_splits), n_train))
    overfit_count = 0
    logits: List[float] = []

    for train_idx in combos:
        test_idx = tuple(i for i in range(n_splits) if i not in train_idx)
        train_data = pd.concat([blocks[i] for i in train_idx])
        test_data = pd.concat([blocks[i] for i in test_idx])
        is_sharpe = {m: train_data[m].mean() / train_data[m].std() * np.sqrt(252)
                     if train_data[m].std() > 0 else 0 for m in methods}
        oos_sharpe = {m: test_data[m].mean() / test_data[m].std() * np.sqrt(252)
                      if test_data[m].std() > 0 else 0 for m in methods}
        best_is = max(is_sharpe, key=is_sharpe.get)
        oos_ranked = sorted(oos_sharpe, key=oos_sharpe.get, reverse=True)
        oos_rank = oos_ranked.index(best_is) if best_is in oos_ranked else 0
        if oos_rank >= len(methods) // 2:
            overfit_count += 1
        w_bar = oos_rank / (len(methods) - 1) if len(methods) > 1 else 0.5
        if 0 < w_bar < 1:
            logits.append(float(np.log(w_bar / (1 - w_bar))))

    pbo = overfit_count / len(combos) if combos else None
    return pbo, logits


def _bootstrap_ci(daily_returns: pd.Series, n_bootstrap: int = 2000,
                   confidence: float = 0.90, seed: int = 42):
    rng = np.random.default_rng(seed)
    n = len(daily_returns)
    block = min(20, n // 5)
    if block < 2 or n < 40:
        return None, None, None
    boot_sr: List[float] = []
    for _ in range(n_bootstrap):
        n_blocks = n // block + 1
        starts = rng.integers(0, n - block, n_blocks)
        sample = np.concatenate([daily_returns.values[s:s + block] for s in starts])[:n]
        if sample.std() > 0:
            boot_sr.append(sample.mean() / sample.std() * np.sqrt(252))
    if not boot_sr:
        return None, None, None
    alpha = (1 - confidence) / 2
    return (float(np.percentile(boot_sr, alpha * 100)),
            float(np.percentile(boot_sr, (1 - alpha) * 100)),
            float(np.mean([s > 0 for s in boot_sr])))


# ═══════════════════════════════════════════════
# SERIALIZATION HELPERS
# ═══════════════════════════════════════════════

def _series_to_list(s: pd.Series) -> List[float]:
    return [float(v) if pd.notna(v) else 0.0 for v in s.values]


def _dates_to_list(idx) -> List[str]:
    return [pd.Timestamp(d).strftime("%Y-%m-%d") for d in idx]


# ═══════════════════════════════════════════════
# REQUEST / RESPONSE MODELS
# ═══════════════════════════════════════════════

class BacktestRequest(BaseModel):
    tickers: List[str]
    lookback: str = "2Y"             # "1Y" | "2Y" | "3Y" | "5Y"
    rebalance: str = "Monthly"       # "Monthly" | "Quarterly"
    est_days: int = 252              # 126, 189, 252, 504
    denoise: bool = True
    blends: Optional[Dict[str, Dict[str, float]]] = None  # {blend_name: {method: pct}}
    rank_by: str = "Sharpe"


class GridRequest(BaseModel):
    lookback: str = "2Y"
    rebalance: str = "Monthly"
    est_days: int = 252
    denoise: bool = True


class ForecastRequest(BaseModel):
    tickers: List[str]


# ═══════════════════════════════════════════════
# FORECAST HELPERS (ported from Streamlit page)
# ═══════════════════════════════════════════════

def _fetch_analyst_targets(tickers: List[str]) -> pd.DataFrame:
    """Per-ticker analyst targets, valuation, growth. yfinance Ticker is
    thread-safe; yf.download with threads=True is not (see CLAUDE guidance)."""
    import yfinance as yf

    def _one(tk: str):
        try:
            info = yf.Ticker(tk).info or {}
            current = info.get("currentPrice") or info.get("regularMarketPrice")
            target = info.get("targetMeanPrice")
            implied = (target / current - 1) if target and current and current > 0 else None
            eg = info.get("earningsGrowth")
            rg = info.get("revenueGrowth")
            return {
                "ticker": tk,
                "current_price": current,
                "target_price": target,
                "target_low": info.get("targetLowPrice"),
                "target_high": info.get("targetHighPrice"),
                "implied_return": implied,
                "n_analysts": info.get("numberOfAnalystOpinions"),
                "rec_mean": info.get("recommendationMean"),
                "forward_pe": info.get("forwardPE"),
                "trailing_pe": info.get("trailingPE"),
                "earnings_growth": (eg * 100) if eg is not None else None,
                "revenue_growth": (rg * 100) if rg is not None else None,
                "sector": info.get("sector"),
            }
        except Exception as e:
            logger.warning(f"forecast fetch failed for {tk}: {e}")
            return None

    with ThreadPoolExecutor(max_workers=8) as ex:
        rows = [r for r in ex.map(_one, tickers) if r is not None]
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def _fetch_macro_context() -> Dict[str, float]:
    """Yield curve, VIX, Fed Funds, 10Y Treasury — latest print each."""
    from src.market_data import fetch_fred_series
    out: Dict[str, float] = {}
    for sid, label in [("T10Y2Y", "yield_curve"), ("VIXCLS", "vix"),
                       ("DFF", "fed_funds"), ("DGS10", "ten_year")]:
        try:
            s = fetch_fred_series(sid, periods=60)
            if s is not None and not s.empty and "value" in s.columns:
                vals = s["value"].dropna()
                if not vals.empty:
                    out[label] = float(vals.iloc[-1])
        except Exception as e:
            logger.warning(f"macro fetch failed for {sid}: {e}")
    return out


def _build_forecast_components(
    forecast_df: pd.DataFrame,
    tickers: List[str],
    hist_mu: np.ndarray,
    macro: Dict[str, float],
) -> Dict:
    """Blend analyst / EPS / valuation / macro into per-ticker forecasts.

    EPS momentum is omitted here because fetch_eps_revisions sits behind a
    separate API and Streamlit only uses it if an EPS DataFrame is passed.
    The 30% weight falls back to zero if EPS data isn't provided.
    """
    n = len(tickers)
    analyst_ret = np.full(n, np.nan)
    eps_signal = np.zeros(n)
    val_signal = np.zeros(n)

    if not forecast_df.empty:
        fc = forecast_df.set_index("ticker") if "ticker" in forecast_df.columns else forecast_df
        for i, t in enumerate(tickers):
            if t in fc.index:
                row = fc.loc[t]
                ir = row.get("implied_return") if not isinstance(row, pd.DataFrame) else None
                if ir is not None and pd.notna(ir):
                    analyst_ret[i] = ir

        if "forward_pe" in forecast_df.columns:
            fwd_pes = forecast_df.set_index("ticker")["forward_pe"].reindex(tickers)
            median_pe = fwd_pes.median()
            if pd.notna(median_pe) and median_pe > 0:
                for i, t in enumerate(tickers):
                    pe = fwd_pes.get(t)
                    if pd.notna(pe) and pe > 0:
                        ratio = pe / median_pe
                        if ratio < 0.5:
                            val_signal[i] = 0.06
                        elif ratio < 0.8:
                            val_signal[i] = 0.03
                        elif ratio > 2.0:
                            val_signal[i] = -0.06
                        elif ratio > 1.5:
                            val_signal[i] = -0.03

    try:
        yc = float(macro.get("yield_curve", 0))
    except (TypeError, ValueError):
        yc = 0.0
    try:
        vix = float(macro.get("vix", 20))
    except (TypeError, ValueError):
        vix = 20.0
    if yc > 0.5 and vix < 20:
        macro_adj = 0.03
    elif yc > 0 and vix < 25:
        macro_adj = 0.01
    elif yc < 0 or vix > 30:
        macro_adj = -0.03
    else:
        macro_adj = 0.0

    hist_annual = hist_mu * 252
    analyst_annual = np.where(np.isnan(analyst_ret), hist_annual, analyst_ret)

    blended = (0.40 * analyst_annual + 0.30 * eps_signal +
               0.20 * val_signal + 0.10 * macro_adj)
    blended = np.clip(blended, -0.50, 0.50)

    components = []
    for i, t in enumerate(tickers):
        components.append({
            "ticker": t,
            "analyst_implied": float(analyst_annual[i] * 100),
            "eps_momentum": float(eps_signal[i] * 100),
            "valuation": float(val_signal[i] * 100),
            "macro": float(macro_adj * 100),
            "blended_forecast": float(blended[i] * 100),
            "historical_annual": float(hist_mu[i] * 252 * 100),
        })

    return {"components": components, "macro_adj": float(macro_adj * 100)}


# ═══════════════════════════════════════════════
# ENDPOINT: /api/meta/backtest
# ═══════════════════════════════════════════════

@router.post("/backtest")
async def meta_backtest(req: BacktestRequest, user: str = Depends(get_current_user)):
    universe = [t.strip().upper() for t in req.tickers if t.strip()]
    universe = sorted(set(universe))[:60]  # hard cap
    if len(universe) < 3:
        return {"error": "Need at least 3 tickers."}

    need_spy = "SPY" not in universe
    fetch_list = universe + (["SPY"] if need_spy else [])

    days = _lookback_days(req.lookback)
    prices = _fetch_prices(fetch_list, days + 30)
    if prices.empty or prices.dropna(axis=1, how="all").shape[1] < 3:
        return {"error": "Insufficient price data."}

    prices = prices.dropna(axis=1, how="all")
    spy_series = prices["SPY"].copy() if "SPY" in prices.columns else None
    use_tickers = [t for t in universe if t in prices.columns]
    if len(use_tickers) < 3:
        return {"error": "Fewer than 3 tickers have price data."}

    ticker_prices = prices[use_tickers]
    returns = ticker_prices.pct_change().dropna()
    if returns.empty or len(returns) < req.est_days + 40:
        return {"error": f"Not enough history for {req.est_days}D estimation window."}

    tickers = returns.columns.tolist()
    rebal_period = "ME" if req.rebalance == "Monthly" else "QE"
    blends = req.blends or {}
    all_method_names = BASE_METHODS + list(blends.keys())

    # Run walk-forward
    wf, weight_history = _walkforward(returns, req.est_days, rebal_period,
                                       req.denoise, blends, all_method_names)
    if not wf:
        return {"error": "Walk-forward produced no results. Try shorter estimation window."}

    # SPY benchmark aligned to OOS period
    method_daily = dict(wf)
    if spy_series is not None:
        spy_ret_full = spy_series.pct_change().dropna()
        oos_idx = method_daily[list(method_daily.keys())[0]].index
        spy_ret = spy_ret_full.reindex(oos_idx).dropna()
        if len(spy_ret) > 20:
            method_daily["SPY Buy & Hold"] = spy_ret

    benchmark_ret = method_daily.get("Equal Weight")

    # Metrics (gross)
    metrics_list = [_portfolio_metrics(r, m, benchmark_returns=benchmark_ret)
                    for m, r in method_daily.items()]

    # Net of cost
    net_daily = _apply_transaction_costs(wf, weight_history)
    if "SPY Buy & Hold" in method_daily:
        net_daily["SPY Buy & Hold"] = method_daily["SPY Buy & Hold"]
    net_metrics_list = [_portfolio_metrics(r, m, benchmark_returns=benchmark_ret)
                        for m, r in net_daily.items()]

    # Rank
    sort_col = {"Sharpe": "sharpe", "Ann. Return": "ann_return",
                "Sortino": "sortino", "Calmar": "calmar",
                "Max DD": "max_dd"}.get(req.rank_by, "sharpe")
    reverse = (sort_col != "max_dd")
    metrics_list.sort(key=lambda m: m[sort_col], reverse=reverse)
    ranked_methods = [m["method"] for m in metrics_list]

    # Equity curves (aligned to OOS date range)
    if ranked_methods:
        oos_dates = method_daily[ranked_methods[0]].index
    else:
        oos_dates = pd.DatetimeIndex([])

    equity_curves = {}
    net_curves = {}
    drawdown_curves = {}
    for method in method_daily.keys():
        r = method_daily[method].reindex(oos_dates).fillna(0)
        eq = (1 + r).cumprod() * 100
        equity_curves[method] = _series_to_list(eq)
        dd = (eq / eq.cummax() - 1) * 100
        drawdown_curves[method] = _series_to_list(dd)
        nr = net_daily.get(method, r).reindex(oos_dates).fillna(0)
        net_curves[method] = _series_to_list((1 + nr).cumprod() * 100)

    dates_str = _dates_to_list(oos_dates)

    # Current weights (using all available data up to end)
    current_w_raw = _compute_all_weights(returns.iloc[-req.est_days:], tickers,
                                          req.denoise, blends=blends)
    current_weights = {m: {t: float(w) for t, w in zip(tickers, arr)}
                       for m, arr in current_w_raw.items()}

    # Weight history (per method: [{date, weights}])
    weight_history_out = {}
    turnover_out = {}
    for method in all_method_names:
        hist = weight_history.get(method, [])
        weight_history_out[method] = [
            {"date": pd.Timestamp(h["date"]).strftime("%Y-%m-%d"),
             "weights": {t: float(w) for t, w in h["weights"].items()}}
            for h in hist
        ]
        # Turnover
        to = []
        for j in range(1, len(hist)):
            prev_w = np.array(list(hist[j - 1]["weights"].values()))
            curr_w = np.array(list(hist[j]["weights"].values()))
            to.append({
                "date": pd.Timestamp(hist[j]["date"]).strftime("%Y-%m-%d"),
                "turnover": float(np.sum(np.abs(curr_w - prev_w)) / 2),
            })
        turnover_out[method] = to

    # Drawdown duration per method
    dd_duration = {}
    for method in method_daily.keys():
        dd_arr = np.array(drawdown_curves[method])
        in_dd = dd_arr < -0.5
        longest = avg_dur = n_episodes = 0
        if in_dd.any():
            # Find consecutive runs
            runs = []
            cur = 0
            for flag in in_dd:
                if flag:
                    cur += 1
                else:
                    if cur > 0:
                        runs.append(cur)
                    cur = 0
            if cur > 0:
                runs.append(cur)
            if runs:
                longest = int(max(runs))
                avg_dur = float(np.mean(runs))
                n_episodes = int(len(runs))
        dd_duration[method] = {
            "longest_days": longest,
            "avg_days": round(avg_dur, 1),
            "episodes": n_episodes,
        }

    # Regime analysis (using EW benchmark)
    regime_rows = []
    if benchmark_ret is not None and len(benchmark_ret) > 63:
        bm_vol = benchmark_ret.rolling(20).std() * np.sqrt(252)
        vol_75 = float(bm_vol.quantile(0.75))
        regime = pd.Series("Normal", index=benchmark_ret.index)
        regime[benchmark_ret > 0] = "Bull"
        regime[benchmark_ret <= 0] = "Bear"
        hv_mask = bm_vol > vol_75
        regime[hv_mask & (benchmark_ret <= 0)] = "Crisis"
        regime[hv_mask & (benchmark_ret > 0)] = "Recovery"
        for method in ranked_methods[:6]:
            if method not in method_daily:
                continue
            m_ret = method_daily[method]
            common = m_ret.index.intersection(regime.index)
            if len(common) < 20:
                continue
            ma = m_ret.loc[common]
            ra = regime.loc[common]
            for reg_name in ["Bull", "Recovery", "Bear", "Crisis"]:
                mask = ra == reg_name
                if mask.sum() < 10:
                    continue
                sub = ma[mask]
                regime_rows.append({
                    "method": method,
                    "regime": reg_name,
                    "ann_return": float(sub.mean() * 252),
                    "ann_vol": float(sub.std() * np.sqrt(252)),
                    "sharpe": float(sub.mean() / sub.std() * np.sqrt(252)) if sub.std() > 0 else 0.0,
                    "days": int(mask.sum()),
                })

    # Stress scenarios (estimated via beta to EW)
    stress_rows = []
    if benchmark_ret is not None and len(benchmark_ret) > 63:
        for method in ranked_methods[:8]:
            if method not in method_daily or method in ("Equal Weight", "SPY Buy & Hold"):
                continue
            m_ret = method_daily[method]
            common = m_ret.index.intersection(benchmark_ret.index)
            if len(common) < 40:
                continue
            ma = m_ret.loc[common].values
            ba = benchmark_ret.loc[common].values
            b_var = float(np.var(ba))
            beta = float(np.cov(ma, ba)[0, 1] / b_var) if b_var > 0 else 1.0
            scenarios = {s: beta * drawdown * 100 for s, drawdown in STRESS_SCENARIOS.items()}
            stress_rows.append({"method": method, "beta": beta, "scenarios": scenarios})

    # Statistical tests
    method_oos = {m: r for m, r in wf.items()}
    n_methods_tested = len(method_oos)
    dsr_rows = []
    for method in ranked_methods:
        if method not in method_oos:
            continue
        rets = method_oos[method]
        n_obs = len(rets)
        sr = float(rets.mean() / rets.std() * np.sqrt(252)) if rets.std() > 0 else 0.0
        sk = float(sp_skew(rets.dropna())) if len(rets) > 10 else 0.0
        kt = float(sp_kurtosis(rets.dropna(), fisher=False)) if len(rets) > 10 else 3.0
        dsr = _deflated_sharpe(sr, n_obs, n_methods_tested, sk, kt)
        min_trl = _min_track_record(sr, n_obs, sk, kt)
        dsr_rows.append({
            "method": method,
            "sharpe": sr,
            "dsr": dsr,
            "skew": sk,
            "kurtosis": kt,
            "min_track_record": (min_trl if np.isfinite(min_trl) else -1),
            "min_years": (min_trl / 252 if np.isfinite(min_trl) else -1),
            "actual_days": int(n_obs),
            "sufficient_data": bool(n_obs > min_trl),
            "significant": bool(dsr > 0.95),
        })

    pbo_val, pbo_logits = _pbo_cpcv(method_oos, n_splits=6)

    boot_rows = []
    for method in ranked_methods:
        if method not in method_oos:
            continue
        rets = method_oos[method]
        ci_lo, ci_hi, p_pos = _bootstrap_ci(rets)
        if ci_lo is not None:
            sr = float(rets.mean() / rets.std() * np.sqrt(252)) if rets.std() > 0 else 0.0
            boot_rows.append({
                "method": method,
                "sharpe": sr,
                "ci_low": ci_lo,
                "ci_high": ci_hi,
                "p_positive": p_pos,
                "significant": bool(ci_lo > 0),
            })

    # Scorecard
    scorecard = []
    boot_lookup = {r["method"]: r for r in boot_rows}
    for row in dsr_rows:
        m = row["method"]
        br = boot_lookup.get(m, {})
        dsr_pass = bool(row["dsr"] > 0.95)
        pbo_pass = bool(pbo_val is not None and pbo_val < 0.50)
        boot_pass = bool(br.get("significant", False))
        trl_pass = bool(row["sufficient_data"])
        passes = sum([dsr_pass, pbo_pass, boot_pass, trl_pass])
        verdict = ("Robust" if passes == 4 else "Credible" if passes >= 3
                   else "Suspect" if passes >= 2 else "Unreliable")
        scorecard.append({
            "method": m, "sharpe": row["sharpe"],
            "dsr_pass": dsr_pass, "pbo_pass": pbo_pass,
            "boot_pass": boot_pass, "trl_pass": trl_pass,
            "score": passes, "verdict": verdict,
        })

    # Rolling Sharpe (63D) for top 5 methods
    rolling_sharpe = {}
    for method in ranked_methods[:5]:
        if method not in wf:
            continue
        r = wf[method]
        if len(r) > 63:
            roll_ret = r.rolling(63).mean() * 252
            roll_vol = r.rolling(63).std() * np.sqrt(252)
            rs = (roll_ret / roll_vol.replace(0, np.nan)).clip(-10, 10).dropna()
            rolling_sharpe[method] = {
                "dates": _dates_to_list(rs.index),
                "values": [float(v) for v in rs.values],
            }

    # Method correlation (pairwise on wf only — real method returns)
    mr_df = pd.DataFrame({m: wf[m] for m in ranked_methods if m in wf}).dropna()
    method_corr_methods: List[str] = []
    method_corr: List[List[float]] = []
    if not mr_df.empty:
        corr = mr_df.corr()
        method_corr_methods = corr.columns.tolist()
        method_corr = [[float(v) for v in row] for row in corr.values]

    # Excess vs EW
    excess_vs_ew = {}
    eq_ret = wf.get("Equal Weight")
    if eq_ret is not None:
        for method in ranked_methods:
            if method in ("Equal Weight", "SPY Buy & Hold"):
                continue
            if method not in wf:
                continue
            m_ret = wf[method]
            common = m_ret.index.intersection(eq_ret.index)
            excess = (m_ret.loc[common] - eq_ret.loc[common]).cumsum() * 100
            excess_vs_ew[method] = {
                "dates": _dates_to_list(excess.index),
                "values": [float(v) for v in excess.values],
            }

    return {
        "tickers": tickers,
        "n_assets": len(tickers),
        "dates": dates_str,
        "n_days": len(dates_str),
        "data_start": dates_str[0] if dates_str else None,
        "data_end": dates_str[-1] if dates_str else None,
        "ranked_methods": ranked_methods,
        "ranked_by": req.rank_by,
        "rebalance": req.rebalance,
        "est_days": req.est_days,
        "equity_curves": equity_curves,
        "net_curves": net_curves,
        "drawdown_curves": drawdown_curves,
        "drawdown_duration": dd_duration,
        "metrics": metrics_list,
        "net_metrics": net_metrics_list,
        "current_weights": current_weights,
        "weight_history": weight_history_out,
        "turnover": turnover_out,
        "cost_bps": COST_BPS,
        "regime_analysis": regime_rows,
        "stress_scenarios": stress_rows,
        "stress_scenario_names": list(STRESS_SCENARIOS.keys()),
        "dsr_results": dsr_rows,
        "pbo": {"value": pbo_val, "logits": pbo_logits},
        "bootstrap_ci": boot_rows,
        "scorecard": scorecard,
        "rolling_sharpe": rolling_sharpe,
        "method_corr_methods": method_corr_methods,
        "method_corr": method_corr,
        "excess_vs_ew": excess_vs_ew,
        "n_methods_tested": n_methods_tested,
    }


# ═══════════════════════════════════════════════
# ENDPOINT: /api/meta/presets
# ═══════════════════════════════════════════════

@router.get("/presets")
async def get_presets(user: str = Depends(get_current_user)):
    """Return the preset ticker groups."""
    return {"presets": PRESET_GROUPS}


# ═══════════════════════════════════════════════
# ENDPOINT: /api/meta/grid
# ═══════════════════════════════════════════════

@router.post("/grid")
async def meta_grid(req: GridRequest, user: str = Depends(get_current_user)):
    """Backtest every preset group independently and return a grid of results."""
    all_tickers = sorted(set(t for tks in PRESET_GROUPS.values() for t in tks))
    days = _lookback_days(req.lookback)
    prices = _fetch_prices(all_tickers, days + 30)
    if prices.empty:
        return {"error": "Failed to fetch price data."}

    prices = prices.dropna(axis=1, how="all")
    returns_all = prices.pct_change().dropna()
    available = set(returns_all.columns)
    rebal_period = "ME" if req.rebalance == "Monthly" else "QE"

    grid_rows = []
    for gname, gtks in PRESET_GROUPS.items():
        have = [t for t in gtks if t in available]
        if len(have) < 3:
            continue
        g_ret = returns_all[have]
        wf, _ = _walkforward(g_ret, req.est_days, rebal_period, req.denoise, None, BASE_METHODS)
        for method, r in wf.items():
            m = _portfolio_metrics(r, method)
            grid_rows.append({
                "universe": gname,
                "method": method,
                "sharpe": m["sharpe"],
                "ann_return": m["ann_return"],
                "max_dd": m["max_dd"],
                "sortino": m["sortino"],
            })

    methods = BASE_METHODS
    universes = sorted(set(r["universe"] for r in grid_rows))

    return {
        "universes": universes,
        "methods": methods,
        "grid": grid_rows,
        "lookback": req.lookback,
        "rebalance": req.rebalance,
        "est_days": req.est_days,
    }


# ═══════════════════════════════════════════════
# ENDPOINT: /api/meta/forecasts
# ═══════════════════════════════════════════════

@router.post("/forecasts")
async def meta_forecasts(req: ForecastRequest, user: str = Depends(get_current_user)):
    """Blend analyst/valuation/macro into per-ticker annual return forecasts."""
    tickers = sorted({t.strip().upper() for t in req.tickers if t.strip()})[:60]
    if len(tickers) < 1:
        return {"error": "At least 1 ticker required."}

    # Historical mean from ~1y of closes via Polygon
    prices = _fetch_prices(tickers, 365)
    usable = [t for t in tickers if t in prices.columns]
    if not usable:
        return {"error": "No price data available for the requested tickers."}

    returns = prices[usable].pct_change().dropna()
    hist_mu = returns.mean().reindex(usable).fillna(0).values

    # Analyst targets + macro, in parallel
    with ThreadPoolExecutor(max_workers=2) as ex:
        fc_future = ex.submit(_fetch_analyst_targets, usable)
        macro_future = ex.submit(_fetch_macro_context)
        forecast_df = fc_future.result()
        macro = macro_future.result()

    blend = _build_forecast_components(forecast_df, usable, hist_mu, macro)

    # Analyst coverage rows for the table
    coverage: List[Dict] = []
    if not forecast_df.empty:
        for _, row in forecast_df.iterrows():
            coverage.append({
                "ticker": row.get("ticker"),
                "current_price": (float(row["current_price"]) if pd.notna(row.get("current_price")) else None),
                "target_price": (float(row["target_price"]) if pd.notna(row.get("target_price")) else None),
                "target_low": (float(row["target_low"]) if pd.notna(row.get("target_low")) else None),
                "target_high": (float(row["target_high"]) if pd.notna(row.get("target_high")) else None),
                "implied_return": (float(row["implied_return"]) * 100 if pd.notna(row.get("implied_return")) else None),
                "n_analysts": (int(row["n_analysts"]) if pd.notna(row.get("n_analysts")) else None),
                "rec_mean": (float(row["rec_mean"]) if pd.notna(row.get("rec_mean")) else None),
                "forward_pe": (float(row["forward_pe"]) if pd.notna(row.get("forward_pe")) else None),
                "trailing_pe": (float(row["trailing_pe"]) if pd.notna(row.get("trailing_pe")) else None),
                "earnings_growth": (float(row["earnings_growth"]) if pd.notna(row.get("earnings_growth")) else None),
                "revenue_growth": (float(row["revenue_growth"]) if pd.notna(row.get("revenue_growth")) else None),
                "sector": row.get("sector"),
            })

    return {
        "tickers": usable,
        "failed": [t for t in tickers if t not in usable],
        "macro": macro,
        "macro_adj_pct": blend["macro_adj"],
        "components": blend["components"],
        "coverage": coverage,
    }

"""Quant Lab — Marcos Lopez de Prado methods.

Server-side heavy lifting: ADF scan for fractional differentiation, rolling
ADF (SADF) bubble test, Chow breakpoint test, feature importance (MDI+MDA),
and hierarchical risk parity. The Next.js client handles CUSUM, triple
barrier, uniqueness bootstrap, microstructure, and entropy inline.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.deps import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()


# ═══════════════════════════════════════════════
# DATA FETCHING
# ═══════════════════════════════════════════════

def _fetch_history(ticker: str, days: int):
    """Return DataFrame with Open/High/Low/Close/Volume indexed by date."""
    from src.data_engine import fetch_massive_data, format_massive_ticker
    tk = format_massive_ticker(ticker.strip().upper())
    try:
        df = fetch_massive_data(tk, days)
    except Exception as e:
        logger.warning(f"history fetch failed for {ticker}: {e}")
        return None
    if df is None or df.empty:
        return None
    # fetch_massive_data returns Close only. Try to get OHLCV via a second call.
    try:
        from src.data_engine import _get_polygon_key  # type: ignore
        import requests
        from datetime import date, timedelta
        api_key = _get_polygon_key()
        if api_key:
            end = date.today()
            start = end - timedelta(days=days + 10)
            url = f"https://api.polygon.io/v2/aggs/ticker/{tk}/range/1/day/{start.isoformat()}/{end.isoformat()}"
            r = requests.get(url, params={"apiKey": api_key, "sort": "asc", "limit": 50000, "adjusted": "true"}, timeout=20)
            r.raise_for_status()
            results = r.json().get("results", [])
            if results:
                bars = pd.DataFrame([{
                    "Date": pd.to_datetime(b["t"], unit="ms"),
                    "Open": b.get("o", 0),
                    "High": b.get("h", 0),
                    "Low": b.get("l", 0),
                    "Close": b.get("c", 0),
                    "Volume": b.get("v", 0),
                } for b in results]).set_index("Date").sort_index()
                return bars
    except Exception:
        pass
    # Fallback: Close only
    return df


def _fetch_closes(tickers: List[str], days: int) -> pd.DataFrame:
    """Fetch close prices for multiple tickers as a DataFrame."""
    from src.data_engine import fetch_massive_data, format_massive_ticker

    def _one(tk: str):
        try:
            df = fetch_massive_data(format_massive_ticker(tk), days)
            if df is None or df.empty:
                return tk, None
            s = df["Close"].copy()
            s.name = tk
            return tk, s
        except Exception:
            return tk, None

    result: Dict[str, pd.Series] = {}
    with ThreadPoolExecutor(max_workers=10) as ex:
        for tk, s in ex.map(_one, tickers):
            if s is not None:
                result[tk] = s
    if not result:
        return pd.DataFrame()
    return pd.DataFrame(result).sort_index()


# ═══════════════════════════════════════════════
# FRACTIONAL DIFFERENTIATION + ADF SCAN
# ═══════════════════════════════════════════════

def _frac_diff_weights(d: float, size: int, thresh: float = 1e-5) -> np.ndarray:
    w = [1.0]
    for k in range(1, size):
        w_ = -w[-1] * (d - k + 1) / k
        if abs(w_) < thresh:
            break
        w.append(w_)
    return np.array(w)


def _frac_diff(series: pd.Series, d: float, thresh: float = 1e-5) -> pd.Series:
    w = _frac_diff_weights(d, len(series), thresh)
    width = len(w)
    vals = series.values
    out = np.full(len(series), np.nan)
    for i in range(width - 1, len(series)):
        out[i] = np.dot(w, vals[i - width + 1:i + 1][::-1])
    return pd.Series(out, index=series.index).dropna()


def _adf_scan(log_prices: pd.Series, d_values: np.ndarray, thresh: float = 1e-5):
    from statsmodels.tsa.stattools import adfuller
    rows = []
    for d in d_values:
        if d == 0:
            fd = log_prices
        else:
            fd = _frac_diff(log_prices, d, thresh)
        if len(fd) < 30:
            continue
        try:
            result = adfuller(fd.dropna(), autolag="AIC")
            stat, pvalue = float(result[0]), float(result[1])
        except Exception:
            stat, pvalue = np.nan, 1.0
        common = log_prices.index.intersection(fd.index)
        if len(common) > 1:
            corr = float(log_prices.loc[common].corr(fd.loc[common]))
        else:
            corr = 0.0
        rows.append({
            "d": float(round(d, 3)),
            "adf_stat": stat if np.isfinite(stat) else None,
            "pvalue": pvalue,
            "corr": corr,
        })
    return rows


# ═══════════════════════════════════════════════
# SADF BUBBLE + CHOW BREAKPOINT
# ═══════════════════════════════════════════════

def _rolling_adf(log_prices: pd.Series, min_window: int = 63):
    """Supremum ADF: expanding window ADF statistics."""
    from statsmodels.tsa.stattools import adfuller
    rows = []
    n = len(log_prices)
    step = max(1, n // 300)  # cap at ~300 points for speed
    for end in range(min_window, n, step):
        window = log_prices.iloc[:end + 1]
        try:
            stat = float(adfuller(window.values, autolag="AIC", regression="c")[0])
            rows.append({"date": log_prices.index[end], "adf_stat": stat})
        except Exception:
            pass
    return rows


def _chow_test(series: pd.Series, min_segment: int = 30):
    """Rolling Chow F-test across all candidate breakpoints."""
    y = series.values
    n = len(y)
    x = np.arange(n)
    X_full = np.column_stack([np.ones(n), x])
    rows = []
    step = max(1, (n - 2 * min_segment) // 400)
    for bp in range(min_segment, n - min_segment, step):
        try:
            beta_full = np.linalg.lstsq(X_full, y, rcond=None)[0]
            rss_full = float(np.sum((y - X_full @ beta_full) ** 2))
            X1, y1 = X_full[:bp], y[:bp]
            X2, y2 = X_full[bp:], y[bp:]
            beta1 = np.linalg.lstsq(X1, y1, rcond=None)[0]
            beta2 = np.linalg.lstsq(X2, y2, rcond=None)[0]
            rss_sub = float(np.sum((y1 - X1 @ beta1) ** 2) + np.sum((y2 - X2 @ beta2) ** 2))
            k = X_full.shape[1]
            f_stat = ((rss_full - rss_sub) / k) / (rss_sub / (n - 2 * k)) if rss_sub > 0 else 0.0
            rows.append({"date": series.index[bp], "f_stat": float(f_stat)})
        except Exception:
            pass
    return rows


# ═══════════════════════════════════════════════
# FEATURE IMPORTANCE (MDI + MDA)
# ═══════════════════════════════════════════════

def _build_features(close: pd.Series, log_returns: pd.Series, log_prices: pd.Series,
                     volume: pd.Series) -> pd.DataFrame:
    feat = pd.DataFrame(index=close.index)
    feat["ret_1"] = log_returns
    feat["ret_5"] = log_prices.diff(5)
    feat["ret_20"] = log_prices.diff(20)
    feat["vol_20"] = log_returns.rolling(20).std()
    feat["vol_60"] = log_returns.rolling(60).std()
    gain = log_returns.clip(lower=0).rolling(14).mean()
    loss = log_returns.clip(upper=0).abs().rolling(14).mean()
    rs = np.where(loss > 0, gain / loss, 0)
    feat["rsi"] = pd.Series(np.where(loss > 0, 100 - 100 / (1 + rs), 50), index=gain.index)
    feat["macd"] = close.ewm(span=12).mean() - close.ewm(span=26).mean()
    feat["bb_width"] = (close.rolling(20).std() * 2) / close.rolling(20).mean() * 100
    feat["skew_20"] = log_returns.rolling(20).skew()
    feat["kurt_20"] = log_returns.rolling(20).kurt()
    if volume is not None and not volume.empty and volume.notna().any():
        feat["volume_ratio"] = volume / volume.rolling(20).mean()
        feat["obv_slope"] = (volume * np.sign(log_returns)).rolling(20).mean()
    feat["target"] = np.sign(log_prices.shift(-5) - log_prices)
    return feat.dropna()


def _feature_importance(feat_df: pd.DataFrame):
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.inspection import permutation_importance

    cols = [c for c in feat_df.columns if c != "target"]
    X = feat_df[cols].values
    y = (feat_df["target"] > 0).astype(int).values
    if len(X) < 100 or len(cols) < 3:
        return None

    split = len(X) * 3 // 4
    X_train, X_test = X[:split], X[split:]
    y_train, y_test = y[:split], y[split:]

    rf = RandomForestClassifier(n_estimators=100, max_depth=5, random_state=42, n_jobs=-1)
    rf.fit(X, y)
    mdi = {c: float(v) for c, v in zip(cols, rf.feature_importances_)}

    rf_mda = RandomForestClassifier(n_estimators=100, max_depth=5, random_state=42, n_jobs=-1)
    rf_mda.fit(X_train, y_train)
    perm = permutation_importance(rf_mda, X_test, y_test, n_repeats=10, random_state=42, n_jobs=-1)
    mda = {c: float(v) for c, v in zip(cols, perm.importances_mean)}

    oos_acc = float(rf_mda.score(X_test, y_test))

    return {
        "features": cols,
        "mdi": mdi,
        "mda": mda,
        "oos_accuracy": oos_acc,
    }


# ═══════════════════════════════════════════════
# HRP ALLOCATION
# ═══════════════════════════════════════════════

def _hrp_allocate(cov: pd.DataFrame, corr: pd.DataFrame) -> pd.Series:
    from scipy.cluster.hierarchy import linkage, leaves_list
    from scipy.spatial.distance import squareform
    tickers = cov.columns.tolist()
    if len(tickers) < 2:
        return pd.Series(1.0, index=tickers)
    dist = ((1 - corr) / 2.0).clip(lower=0) ** 0.5
    np.fill_diagonal(dist.values, 0)
    dist = (dist + dist.T) / 2
    condensed = squareform(dist.values, checks=False)
    link = linkage(condensed, method="single")
    sort_idx = leaves_list(link).tolist()
    sorted_tickers = [tickers[i] for i in sort_idx]
    weights = pd.Series(1.0, index=sorted_tickers)

    def cvar(cov_sub, tks):
        ivp = 1.0 / np.diag(cov_sub.loc[tks, tks].values)
        ivp /= ivp.sum()
        return float(np.dot(ivp, np.dot(cov_sub.loc[tks, tks].values, ivp)))

    clusters = [sorted_tickers]
    while clusters:
        new_clusters = []
        for cluster in clusters:
            if len(cluster) <= 1:
                continue
            mid = len(cluster) // 2
            left, right = cluster[:mid], cluster[mid:]
            vl, vr = cvar(cov, left), cvar(cov, right)
            total = vl + vr
            alpha = 1 - vl / total if total > 0 else 0.5
            for t in left:
                weights[t] *= alpha
            for t in right:
                weights[t] *= (1 - alpha)
            if len(left) > 1:
                new_clusters.append(left)
            if len(right) > 1:
                new_clusters.append(right)
        clusters = new_clusters
    total = float(weights.sum())
    if total > 0:
        weights = weights / total
    else:
        # Degenerate covariance — fall back to equal weight
        weights = pd.Series(1.0 / len(weights), index=weights.index)
    return weights.reindex(tickers).fillna(0)


# ═══════════════════════════════════════════════
# REQUEST MODELS
# ═══════════════════════════════════════════════

class AnalyzeRequest(BaseModel):
    ticker: str
    lookback: int = 756   # 504, 756, 1260, 2520


class HrpRequest(BaseModel):
    tickers: List[str]
    lookback: int = 504    # ~2 years
    rebalance: str = "Monthly"  # Monthly | Quarterly
    estimation_window: int = 252


# ═══════════════════════════════════════════════
# MAIN ANALYZE ENDPOINT
# ═══════════════════════════════════════════════

@router.post("/analyze")
async def analyze(req: AnalyzeRequest, user: str = Depends(get_current_user)):
    df = _fetch_history(req.ticker, req.lookback)
    if df is None or df.empty or len(df) < 100:
        return {"error": f"Insufficient data for {req.ticker}. Need at least 100 trading days."}

    close = df["Close"].astype(float)
    log_prices = np.log(close.replace(0, np.nan)).dropna()
    close = close.loc[log_prices.index]
    log_returns = log_prices.diff().dropna()
    volume = df.get("Volume", pd.Series(dtype=float))
    if isinstance(volume, pd.Series):
        volume = volume.reindex(close.index).fillna(0)
    high = df.get("High", pd.Series(dtype=float))
    low = df.get("Low", pd.Series(dtype=float))
    if isinstance(high, pd.Series):
        high = high.reindex(close.index)
    if isinstance(low, pd.Series):
        low = low.reindex(close.index)

    dates = [pd.Timestamp(d).strftime("%Y-%m-%d") for d in close.index]

    # ── ADF scan for fractional differentiation ──
    d_values = np.arange(0.0, 1.05, 0.05)
    adf_rows = _adf_scan(log_prices, d_values)
    stationary_ds = [r["d"] for r in adf_rows if r["pvalue"] < 0.05]
    min_d = min(stationary_ds) if stationary_ds else 1.0

    # ── Fractionally differenced series at optimal d ──
    if min_d > 0 and min_d < 1.0:
        fd_opt = _frac_diff(log_prices, min_d)
    else:
        fd_opt = log_returns.copy()

    fd_optimal = {
        "d": float(min_d),
        "dates": [pd.Timestamp(d).strftime("%Y-%m-%d") for d in fd_opt.index],
        "values": [float(v) for v in fd_opt.values],
    }

    # ── SADF bubble ──
    sadf_rows = _rolling_adf(log_prices, min_window=max(63, len(log_prices) // 10))
    T = len(log_prices)
    cv_95 = 0.60 if T >= 800 else 0.40 if T >= 400 else 0.20 if T >= 200 else 0.0

    # ── Chow breakpoint ──
    chow_rows = _chow_test(log_returns, min_segment=max(30, len(log_returns) // 20))
    from scipy.stats import f as f_dist
    chow_cv_99 = float(f_dist.ppf(0.99, 2, len(log_returns) - 4)) if len(log_returns) > 10 else 0.0

    # ── Feature importance ──
    feat_df = _build_features(close, log_returns, log_prices, volume)
    importance = _feature_importance(feat_df)

    # ── Summary metrics ──
    ann_ret = float(log_returns.mean() * 252 * 100)
    ann_vol = float(log_returns.std() * np.sqrt(252) * 100)

    return {
        "ticker": req.ticker.upper(),
        "lookback": req.lookback,
        "n_obs": len(close),
        "date_start": dates[0],
        "date_end": dates[-1],
        "ann_return": ann_ret,
        "ann_vol": ann_vol,
        "ohlcv": {
            "dates": dates,
            "close": [float(v) for v in close.values],
            "log_prices": [float(v) for v in log_prices.values],
            "log_returns": [float(v) if pd.notna(v) else 0.0 for v in log_returns.reindex(close.index).fillna(0).values],
            "volume": [float(v) for v in volume.values] if isinstance(volume, pd.Series) else [],
            "high": [float(v) if pd.notna(v) else 0.0 for v in high.values] if isinstance(high, pd.Series) else [],
            "low": [float(v) if pd.notna(v) else 0.0 for v in low.values] if isinstance(low, pd.Series) else [],
        },
        "adf_scan": adf_rows,
        "min_d": float(min_d),
        "fd_optimal": fd_optimal,
        "sadf": {
            "dates": [pd.Timestamp(r["date"]).strftime("%Y-%m-%d") for r in sadf_rows],
            "values": [r["adf_stat"] for r in sadf_rows],
            "cv_95": cv_95,
            "max": float(max((r["adf_stat"] for r in sadf_rows), default=0)),
            "n_periods": len(sadf_rows),
        },
        "chow": {
            "dates": [pd.Timestamp(r["date"]).strftime("%Y-%m-%d") for r in chow_rows],
            "f_stats": [r["f_stat"] for r in chow_rows],
            "cv_99": chow_cv_99,
        },
        "feature_importance": importance,
    }


# ═══════════════════════════════════════════════
# HRP ENDPOINT (static + walk-forward)
# ═══════════════════════════════════════════════

@router.post("/hrp")
async def hrp(req: HrpRequest, user: str = Depends(get_current_user)):
    tickers = [t.strip().upper() for t in req.tickers if t.strip()]
    if len(tickers) < 3:
        return {"error": "Need at least 3 tickers for HRP."}

    prices = _fetch_closes(tickers, req.lookback)
    if prices.empty:
        return {"error": "Failed to fetch price data."}

    prices = prices.dropna(axis=1, how="all")
    tickers_avail = prices.columns.tolist()
    if len(tickers_avail) < 3:
        return {"error": "Fewer than 3 tickers have data."}

    returns = prices.pct_change().dropna()
    cov = returns.cov() * 252
    corr = returns.corr()

    # Static allocations
    hrp_w = _hrp_allocate(cov, corr)
    eq_w = pd.Series(1.0 / len(tickers_avail), index=tickers_avail)
    vol = returns.std() * np.sqrt(252)
    iv_w = (1 / vol) / (1 / vol).sum()

    def to_dict(s: pd.Series) -> Dict[str, float]:
        return {k: float(v) for k, v in s.items()}

    def metrics(port_ret: pd.Series) -> Dict[str, float]:
        ann_r = float(port_ret.mean() * 252 * 100)
        ann_v = float(port_ret.std() * np.sqrt(252) * 100)
        sharpe = ann_r / ann_v if ann_v > 0 else 0.0
        cum = (1 + port_ret).cumprod()
        dd = float(((cum / cum.cummax()) - 1).min() * 100)
        return {"ann_return": ann_r, "ann_vol": ann_v, "sharpe": sharpe, "max_dd": dd}

    port_hrp = (returns * hrp_w).sum(axis=1)
    port_eq = (returns * eq_w).sum(axis=1)
    port_iv = (returns * iv_w.reindex(tickers_avail)).sum(axis=1)

    dates_out = [pd.Timestamp(d).strftime("%Y-%m-%d") for d in returns.index]
    cum_hrp = ((1 + port_hrp).cumprod() * 100)
    cum_eq = ((1 + port_eq).cumprod() * 100)
    cum_iv = ((1 + port_iv).cumprod() * 100)

    # Walk-forward HRP
    rebal_period = "ME" if req.rebalance == "Monthly" else "QE"
    rebal_dates = returns.resample(rebal_period).last().index
    wf_returns: List[Dict] = []
    wf_weights_history: List[Dict] = []
    prev_weights = eq_w
    est_window = int(req.estimation_window)
    for i in range(len(rebal_dates)):
        rd = rebal_dates[i]
        if rd not in returns.index:
            continue
        end_loc = returns.index.get_loc(rd)
        start_loc = max(0, end_loc - est_window)
        est_returns = returns.iloc[start_loc:end_loc + 1]
        if len(est_returns) >= 60:
            try:
                est_cov = est_returns.cov() * 252
                est_corr = est_returns.corr()
                prev_weights = _hrp_allocate(est_cov, est_corr)
            except Exception:
                pass
        wf_weights_history.append({
            "date": pd.Timestamp(rd).strftime("%Y-%m-%d"),
            "weights": to_dict(prev_weights),
        })
        if i < len(rebal_dates) - 1:
            next_rd = rebal_dates[i + 1]
            period = returns.loc[rd:next_rd]
        else:
            period = returns.loc[rd:]
        for dt, row in period.iterrows():
            wf_returns.append({
                "date": pd.Timestamp(dt).strftime("%Y-%m-%d"),
                "return": float((row * prev_weights).sum()),
            })

    wf_series = pd.Series([r["return"] for r in wf_returns],
                          index=[pd.Timestamp(r["date"]) for r in wf_returns])
    wf_series = wf_series[~wf_series.index.duplicated(keep="first")]
    cum_wf = ((1 + wf_series).cumprod() * 100) if not wf_series.empty else pd.Series(dtype=float)
    wf_metrics = metrics(wf_series) if not wf_series.empty else {"ann_return": 0, "ann_vol": 0, "sharpe": 0, "max_dd": 0}

    return {
        "tickers": tickers_avail,
        "failed": [t for t in tickers if t not in tickers_avail],
        "weights": {
            "hrp": to_dict(hrp_w),
            "equal": to_dict(eq_w),
            "inverse_vol": to_dict(iv_w.reindex(tickers_avail).fillna(0)),
        },
        "dates": dates_out,
        "cum_hrp": [float(v) for v in cum_hrp.values],
        "cum_eq": [float(v) for v in cum_eq.values],
        "cum_iv": [float(v) for v in cum_iv.values],
        "static_metrics": {
            "hrp": metrics(port_hrp),
            "equal": metrics(port_eq),
            "inverse_vol": metrics(port_iv),
        },
        "walk_forward": {
            "dates": [pd.Timestamp(d).strftime("%Y-%m-%d") for d in cum_wf.index],
            "cum": [float(v) for v in cum_wf.values],
            "metrics": wf_metrics,
            "weight_history": wf_weights_history,
            "rebalance": req.rebalance,
        },
    }

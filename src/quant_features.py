"""
Quant Lab shared functions — reusable across trading pages.

Provides fractional differentiation, CUSUM filtering, triple barrier labeling,
sample weights, HRP allocation, and microstructure/entropy regime indicators.

Based on Lopez de Prado, Advances in Financial Machine Learning.
"""
import logging

import numpy as np
import pandas as pd
import streamlit as st

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# FRACTIONAL DIFFERENTIATION (AFML Ch. 5)
# ─────────────────────────────────────────────

def frac_diff_weights(d: float, size: int, thresh: float = 1e-5) -> np.ndarray:
    """Compute fractional differencing weights (expanding window)."""
    w = [1.0]
    for k in range(1, size):
        w_ = -w[-1] * (d - k + 1) / k
        if abs(w_) < thresh:
            break
        w.append(w_)
    return np.array(w)


def frac_diff(series: pd.Series, d: float, thresh: float = 1e-5) -> pd.Series:
    """Apply fractional differencing of order d to a series."""
    w = frac_diff_weights(d, len(series), thresh)
    width = len(w)
    result = pd.Series(index=series.index, dtype=float)
    for i in range(width - 1, len(series)):
        result.iloc[i] = np.dot(w, series.iloc[i - width + 1:i + 1].values[::-1])
    return result.dropna()


def find_min_d(log_prices: pd.Series, thresh: float = 1e-5, significance: float = 0.05) -> float:
    """Find the minimum fractional differencing order d that achieves stationarity."""
    from statsmodels.tsa.stattools import adfuller
    for d in np.arange(0.0, 1.05, 0.05):
        if d == 0:
            continue
        fd = frac_diff(log_prices, d, thresh)
        if len(fd) < 30:
            continue
        try:
            pvalue = adfuller(fd.dropna(), autolag="AIC")[1]
            if pvalue < significance:
                return round(d, 2)
        except Exception:
            continue
    return 1.0


# ─────────────────────────────────────────────
# CUSUM FILTER (AFML Ch. 17)
# ─────────────────────────────────────────────

def cusum_filter(log_returns: pd.Series, h_sigma: float = 2.0) -> pd.DatetimeIndex:
    """Apply symmetric CUSUM filter. Returns event timestamps.
    h_sigma: threshold in standard deviations of the return series."""
    h = h_sigma * log_returns.std()
    s_pos, s_neg = 0.0, 0.0
    events = []
    for dt, r in log_returns.items():
        s_pos = max(0, s_pos + r)
        s_neg = min(0, s_neg + r)
        if s_pos > h:
            events.append(dt)
            s_pos = 0
        elif s_neg < -h:
            events.append(dt)
            s_neg = 0
    return pd.DatetimeIndex(events)


# ─────────────────────────────────────────────
# TRIPLE BARRIER LABELING (AFML Ch. 3)
# ─────────────────────────────────────────────

def triple_barrier_labels(close: pd.Series, events: pd.DatetimeIndex = None,
                          pt_mult: float = 2.0, sl_mult: float = 2.0,
                          max_holding: int = 20, atr: pd.Series = None) -> pd.DataFrame:
    """Apply triple barrier labeling at given event timestamps.
    Returns DataFrame with label (+1, -1, 0), return_pct, hold_days."""
    if events is None:
        events = close.index
    if atr is None:
        atr = close.rolling(20).std()

    labels = []
    close_idx = close.index.tolist()
    for event_date in events:
        if event_date not in close.index:
            continue
        i = close_idx.index(event_date)
        entry = close.iloc[i]
        entry_atr = atr.iloc[i]
        if pd.isna(entry_atr) or entry_atr <= 0:
            continue
        if i + 1 >= len(close):
            continue

        upper = entry + pt_mult * entry_atr
        lower = entry - sl_mult * entry_atr

        label = 0
        exit_idx = min(i + max_holding, len(close) - 1)
        for j in range(i + 1, min(i + max_holding + 1, len(close))):
            if close.iloc[j] >= upper:
                label = 1
                exit_idx = j
                break
            elif close.iloc[j] <= lower:
                label = -1
                exit_idx = j
                break

        ret = (close.iloc[exit_idx] / entry - 1) * 100
        labels.append({
            "entry_date": event_date,
            "label": label,
            "return_pct": ret,
            "hold_days": exit_idx - i,
        })
    return pd.DataFrame(labels)


# ─────────────────────────────────────────────
# SAMPLE WEIGHTS (AFML Ch. 4)
# ─────────────────────────────────────────────

def avg_uniqueness(returns: pd.Series, window: int = 20) -> pd.Series:
    """Compute average uniqueness for each observation.
    Uniqueness = 1 / (number of concurrent labels at time t)."""
    n = len(returns)
    concurrency = np.ones(n)
    for i in range(n):
        start = max(0, i - window + 1)
        end = min(n, i + window)
        concurrency[i] = end - start
    return pd.Series(1.0 / concurrency, index=returns.index, name="uniqueness")


def sequential_bootstrap_sharpe(returns: pd.Series, uniqueness: pd.Series,
                                 n_bootstrap: int = 1000, seed: int = 42) -> tuple:
    """Run sequential bootstrap and return (standard_sharpes, sequential_sharpes)."""
    rng = np.random.default_rng(seed)
    ret_vals = returns.values
    uniq_vals = uniqueness.reindex(returns.index).values
    uniq_probs = uniq_vals / uniq_vals.sum()
    ann = np.sqrt(252)

    standard, sequential = [], []
    for _ in range(n_bootstrap):
        idx = rng.integers(0, len(ret_vals), len(ret_vals))
        s = ret_vals[idx]
        if s.std() > 0:
            standard.append(s.mean() / s.std() * ann)
        idx_seq = rng.choice(len(ret_vals), size=len(ret_vals), replace=True, p=uniq_probs)
        s_seq = ret_vals[idx_seq]
        if s_seq.std() > 0:
            sequential.append(s_seq.mean() / s_seq.std() * ann)
    return standard, sequential


# ─────────────────────────────────────────────
# HIERARCHICAL CLUSTERING HELPERS
# ─────────────────────────────────────────────

def _hrp_linkage(corr: pd.DataFrame, method: str = "ward"):
    """Build hierarchical tree from a correlation matrix.

    Returns (linkage_matrix, sorted_tickers).
    """
    from scipy.cluster.hierarchy import linkage, leaves_list
    from scipy.spatial.distance import squareform

    dist = ((1 - corr) / 2.0).clip(lower=0) ** 0.5
    np.fill_diagonal(dist.values, 0)
    dist = (dist + dist.T) / 2
    condensed = squareform(dist.values, checks=False)
    link = linkage(condensed, method=method)
    sort_idx = leaves_list(link).tolist()
    sorted_tickers = [corr.columns[i] for i in sort_idx]
    return link, sorted_tickers


def denoise_covariance(returns: pd.DataFrame):
    """Apply Ledoit-Wolf shrinkage to get denoised covariance and correlation.

    Returns (shrunk_cov_df, shrunk_corr_df) as DataFrames with ticker labels.
    """
    from sklearn.covariance import LedoitWolf

    lw = LedoitWolf()
    shrunk_cov_arr = lw.fit(returns.values).covariance_ * 252  # annualised
    tickers = returns.columns
    shrunk_cov = pd.DataFrame(shrunk_cov_arr, index=tickers, columns=tickers)

    vols = np.sqrt(np.diag(shrunk_cov_arr))
    outer = np.outer(vols, vols)
    outer[outer == 0] = 1e-12
    shrunk_corr = pd.DataFrame(
        shrunk_cov_arr / outer, index=tickers, columns=tickers,
    )
    np.fill_diagonal(shrunk_corr.values, 1.0)
    return shrunk_cov, shrunk_corr


# ─────────────────────────────────────────────
# HRP ALLOCATION (AFML/MLAM Ch. 16)
# ─────────────────────────────────────────────

def hrp_allocate(
    returns: pd.DataFrame,
    cov: pd.DataFrame | None = None,
    corr: pd.DataFrame | None = None,
    linkage_method: str = "ward",
) -> pd.Series:
    """Hierarchical Risk Parity (de Prado 2016).

    Upgraded from original: supports Ward linkage (default) and accepts
    pre-computed (optionally denoised) covariance / correlation matrices.
    """
    if cov is None:
        cov = returns.cov() * 252
    if corr is None:
        corr = returns.corr()
    tickers = cov.columns.tolist()

    link, sorted_tickers = _hrp_linkage(corr, method=linkage_method)

    # Recursive bisection with inverse-variance weighting
    weights = pd.Series(1.0, index=sorted_tickers)

    def _cluster_var(cov_sub, tk):
        ivp = 1.0 / np.diag(cov_sub.loc[tk, tk].values)
        ivp /= ivp.sum()
        return np.dot(ivp, np.dot(cov_sub.loc[tk, tk].values, ivp))

    clusters = [sorted_tickers]
    while clusters:
        new_clusters = []
        for cluster in clusters:
            if len(cluster) <= 1:
                continue
            mid = len(cluster) // 2
            left, right = cluster[:mid], cluster[mid:]
            vl = _cluster_var(cov, left)
            vr = _cluster_var(cov, right)
            total = vl + vr
            alpha = 1 - vl / total if total > 0 else 0.5
            weights[left] *= alpha
            weights[right] *= (1 - alpha)
            if len(left) > 1:
                new_clusters.append(left)
            if len(right) > 1:
                new_clusters.append(right)
        clusters = new_clusters

    return weights / weights.sum()


# ─────────────────────────────────────────────
# HERC — Hierarchical Equal Risk Contribution (Raffinot 2018)
# ─────────────────────────────────────────────

def herc_allocate(
    returns: pd.DataFrame,
    cov: pd.DataFrame | None = None,
    corr: pd.DataFrame | None = None,
    risk_metric: str = "cvar",
    linkage_method: str = "ward",
    cvar_alpha: float = 0.05,
) -> pd.Series:
    """HERC allocation — Equal Risk Contribution across and within clusters.

    Unlike HRP's naive inverse-variance bisection, HERC:
      1. Uses the cluster tree structure for allocation (not just ordering)
      2. Applies Equal Risk Contribution at each tree split
      3. Supports CVaR/variance as the risk metric

    Parameters
    ----------
    risk_metric : 'cvar' or 'variance'
    cvar_alpha  : tail probability for CVaR (default 5%)
    """
    if cov is None:
        cov = returns.cov() * 252
    if corr is None:
        corr = returns.corr()
    tickers = cov.columns.tolist()

    link, sorted_tickers = _hrp_linkage(corr, method=linkage_method)

    def _cluster_risk(tk_list):
        """Compute cluster risk using inverse-variance portfolio, then measure
        its risk with the chosen metric."""
        sub_cov = cov.loc[tk_list, tk_list].values
        ivp = 1.0 / np.diag(sub_cov)
        ivp /= ivp.sum()
        if risk_metric == "cvar":
            # Build cluster portfolio returns, compute CVaR
            port_ret = (returns[tk_list].values @ ivp)
            sorted_ret = np.sort(port_ret)
            cutoff = max(1, int(len(sorted_ret) * cvar_alpha))
            return -sorted_ret[:cutoff].mean() * np.sqrt(252)
        else:
            return np.sqrt(ivp @ sub_cov @ ivp)

    # Recursive bisection with Equal Risk Contribution
    weights = pd.Series(1.0, index=sorted_tickers)
    clusters = [sorted_tickers]
    while clusters:
        new_clusters = []
        for cluster in clusters:
            if len(cluster) <= 1:
                continue
            mid = len(cluster) // 2
            left, right = cluster[:mid], cluster[mid:]
            rl = _cluster_risk(left)
            rr = _cluster_risk(right)
            total = rl + rr
            # ERC: allocate inversely proportional to risk contribution
            alpha = 1 - rl / total if total > 0 else 0.5
            weights[left] *= alpha
            weights[right] *= (1 - alpha)
            if len(left) > 1:
                new_clusters.append(left)
            if len(right) > 1:
                new_clusters.append(right)
        clusters = new_clusters

    return weights / weights.sum()


# ─────────────────────────────────────────────
# HCAA — Hierarchical Clustering-Based Asset Allocation (Raffinot)
# ─────────────────────────────────────────────

def hcaa_allocate(
    returns: pd.DataFrame,
    corr: pd.DataFrame | None = None,
    linkage_method: str = "ward",
) -> pd.Series:
    """HCAA — 1/N within each cluster branch.

    The most robust, lowest-turnover hierarchical allocation.
    Divides capital equally across major branches, then equally within.
    No risk estimation needed beyond the correlation for clustering.
    """
    if corr is None:
        corr = returns.corr()
    tickers = corr.columns.tolist()

    link, sorted_tickers = _hrp_linkage(corr, method=linkage_method)

    # Equal split at each bisection (no risk weighting)
    weights = pd.Series(1.0, index=sorted_tickers)
    clusters = [sorted_tickers]
    while clusters:
        new_clusters = []
        for cluster in clusters:
            if len(cluster) <= 1:
                continue
            mid = len(cluster) // 2
            left, right = cluster[:mid], cluster[mid:]
            weights[left] *= 0.5
            weights[right] *= 0.5
            if len(left) > 1:
                new_clusters.append(left)
            if len(right) > 1:
                new_clusters.append(right)
        clusters = new_clusters

    return weights / weights.sum()


# ─────────────────────────────────────────────
# MICROSTRUCTURE / REGIME INDICATORS (AFML Ch. 18-19)
# ─────────────────────────────────────────────

def compute_vpin(volume: pd.Series, returns: pd.Series, window: int = 50) -> pd.Series:
    """Volume-Synchronized Probability of Informed Trading."""
    tick_sign = np.sign(returns).replace(0, np.nan).ffill().fillna(1)
    buy_vol = volume * (tick_sign == 1).astype(float)
    sell_vol = volume * (tick_sign == -1).astype(float)
    vpin = (buy_vol.rolling(window).sum() - sell_vol.rolling(window).sum()).abs() / \
           volume.rolling(window).sum().replace(0, np.nan)
    return vpin.dropna()


def compute_entropy(returns: pd.Series, n_bins: int = 10, window: int = 63) -> pd.Series:
    """Rolling normalized Shannon entropy of returns."""
    rolling_ent = []
    for i in range(window, len(returns)):
        w = returns.iloc[i - window:i]
        bins = pd.qcut(w, min(n_bins, len(w.unique())), labels=False, duplicates="drop")
        probs = bins.value_counts(normalize=True)
        h = -np.sum(probs * np.log2(probs.replace(0, 1)))
        max_h = np.log2(max(len(probs), 2))
        rolling_ent.append(h / max_h if max_h > 0 else 1.0)
    return pd.Series(rolling_ent, index=returns.index[window:], name="entropy")


def regime_filter(vpin: pd.Series = None, entropy: pd.Series = None,
                  vpin_threshold: float = 0.6, entropy_threshold: float = 0.85) -> pd.Series:
    """Combine VPIN and entropy into a regime signal.
    Returns a Series with values: 'favorable' (trade), 'caution', 'avoid'.
    Favorable = low toxicity + low entropy (predictable + safe)."""
    if vpin is None and entropy is None:
        return pd.Series(dtype=str)

    # Align indices
    if vpin is not None and entropy is not None:
        common = vpin.index.intersection(entropy.index)
        vpin = vpin.loc[common]
        entropy = entropy.loc[common]
        signal = pd.Series("favorable", index=common)
        signal[vpin > vpin_threshold] = "caution"
        signal[entropy > entropy_threshold] = "caution"
        signal[(vpin > vpin_threshold) & (entropy > entropy_threshold)] = "avoid"
    elif vpin is not None:
        signal = pd.Series("favorable", index=vpin.index)
        signal[vpin > vpin_threshold] = "avoid"
    else:
        signal = pd.Series("favorable", index=entropy.index)
        signal[entropy > entropy_threshold] = "caution"

    return signal

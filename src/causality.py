"""Causality compute primitives — panel building, stationarity, CCF.

Tab 1 of the /causality page is Cross-Correlation Function (CCF). This module
implements the panel construction (yfinance + FRED, daily aligned, business-day
calendar) and the CCF compute. Tabs 2-5 will reuse `aligned_panel` and
`stationarize`.

Design intent (trader-first):
- Auto-stationarity is *transparent*: the API surfaces what transform was
  applied to each series so the UI can show a small "Used log-returns" badge.
- CCF returns a 95% asymptotic confidence band (1.96 / sqrt(N)) so traders
  can immediately see whether a peak ρ is signal or noise.
- Pair mode returns the full lag profile; scan mode returns just the peak so
  it stays cheap when fanning out across the whole universe.
"""

from __future__ import annotations

import logging
import math
from datetime import date, timedelta
from typing import Literal

import numpy as np
import pandas as pd

from src.causality_universe import MacroSeries, SYMBOLS_BY_KEY, get_series

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────
# DATA FETCH — yfinance + FRED, daily, aligned
# ─────────────────────────────────────────────────────────────────

LookbackKey = Literal["1Y", "3Y", "5Y", "10Y"]
_LOOKBACK_DAYS: dict[str, int] = {"1Y": 365, "3Y": 365 * 3, "5Y": 365 * 5, "10Y": 365 * 10}


def _lookback_days(lookback: str) -> int:
    return _LOOKBACK_DAYS.get(lookback, _LOOKBACK_DAYS["5Y"])


def _fetch_one_series(spec: MacroSeries, lookback_days: int) -> pd.Series:
    """Fetch a single series (yfinance via OHLCV cache, or FRED) as a daily
    pandas Series indexed by date. Returns empty Series on failure rather than
    raising — callers handle missing series by dropping them from the panel."""
    if spec.source == "yfinance":
        try:
            from src.ohlcv_cache import fetch_ohlcv
            df = fetch_ohlcv(spec.ticker, lookback_days=max(lookback_days, 252))
            if df is None or len(df) == 0:
                return pd.Series(dtype=float, name=spec.symbol)
            s = pd.Series(df["Close"].values, index=pd.to_datetime(df.index), name=spec.symbol)
            return s
        except Exception as e:
            logger.warning(f"yfinance fetch failed for {spec.symbol} ({spec.ticker}): {e}")
            return pd.Series(dtype=float, name=spec.symbol)

    # FRED
    try:
        from src.data_engine import _fred_history
        # FRED helper accepts days; pad slightly so weekends/holidays are filled.
        df = _fred_history(spec.ticker, days=lookback_days + 30)
        if df is None or df.empty:
            return pd.Series(dtype=float, name=spec.symbol)
        s = pd.Series(df["Close"].values, index=pd.to_datetime(df.index), name=spec.symbol)
        return s
    except Exception as e:
        logger.warning(f"FRED fetch failed for {spec.symbol} ({spec.ticker}): {e}")
        return pd.Series(dtype=float, name=spec.symbol)


def aligned_panel(
    symbols: list[str],
    lookback: str = "5Y",
    freq: Literal["B", "W"] = "B",
) -> pd.DataFrame:
    """Build a daily (or weekly) aligned price/level panel for the given symbols.

    - Forward-fills macro/FRED series across days where they don't update
      (CPI is monthly, NFP is monthly, etc.). This is correct for level
      series, and stationarity transforms downstream collapse the FFill plateau
      to zero changes.
    - Drops any symbol that returned no data so the rest of the panel survives.
    - Index is calendar-business days for `freq="B"`.
    """
    days = _lookback_days(lookback)
    series_list: list[pd.Series] = []
    for sym in symbols:
        spec = get_series(sym)
        s = _fetch_one_series(spec, days)
        if s.empty:
            logger.info(f"aligned_panel: dropping {sym} (no data)")
            continue
        series_list.append(s)

    if not series_list:
        return pd.DataFrame()

    df = pd.concat(series_list, axis=1)
    df = df.sort_index()
    # Trim to lookback window (use the latest available date as the anchor)
    end = df.index.max()
    start = end - pd.Timedelta(days=days)
    df = df.loc[start:end]

    if freq == "B":
        idx = pd.date_range(df.index.min(), df.index.max(), freq="B")
    else:
        idx = pd.date_range(df.index.min(), df.index.max(), freq="W-FRI")
    df = df.reindex(idx).ffill()
    # Drop rows where every column is still NaN (front of series before first obs)
    df = df.dropna(how="all")
    return df


# ─────────────────────────────────────────────────────────────────
# STATIONARITY
# ─────────────────────────────────────────────────────────────────

TransformApplied = Literal["log_return", "diff", "level"]


def _adf_pvalue(s: pd.Series) -> float | None:
    """Augmented Dickey-Fuller p-value, or None if it fails."""
    try:
        from statsmodels.tsa.stattools import adfuller
        s_clean = s.dropna()
        if len(s_clean) < 30:
            return None
        result = adfuller(s_clean.values, autolag="AIC")
        return float(result[1])
    except Exception as e:
        logger.debug(f"ADF failed: {e}")
        return None


def stationarize_series(s: pd.Series, default: TransformApplied) -> tuple[pd.Series, TransformApplied, float | None]:
    """Apply the default transform, then sanity-check with ADF. If the chosen
    transform still has p > 0.05, escalate (level → diff → log_return).

    Returns (transformed_series, applied_transform, adf_pvalue_after).
    """
    if default == "log_return":
        # Guard: log of non-positive values is -inf/NaN. WTI famously printed
        # negative in April 2020. If any obs is ≤ 0, fall back to first-diff
        # rather than silently produce inf and shorten the series.
        if (s.dropna() <= 0).any():
            out = s.diff()
            default = "diff"
        else:
            out = np.log(s).diff()
    elif default == "diff":
        out = s.diff()
    else:
        out = s.copy()
    out = out.dropna()

    p = _adf_pvalue(out)
    # If the default transform leaves the series non-stationary, escalate one
    # step. We don't iterate further — twice-differenced data is a pathology
    # signal worth surfacing rather than masking.
    if p is not None and p > 0.05:
        if default == "level":
            esc = s.diff().dropna()
            esc_p = _adf_pvalue(esc)
            if esc_p is not None and esc_p < p:
                return esc, "diff", esc_p
        elif default == "diff" and (s > 0).all():
            esc = np.log(s).diff().dropna()
            esc_p = _adf_pvalue(esc)
            if esc_p is not None and esc_p < p:
                return esc, "log_return", esc_p
    return out, default, p


def stationarize_panel(panel: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, dict]]:
    """Stationarize every column of a panel using each symbol's default
    transform (with ADF escalation). Returns the transformed panel and a
    metadata dict {symbol: {"transform": str, "adf_p": float}}."""
    out_cols: dict[str, pd.Series] = {}
    meta: dict[str, dict] = {}
    for col in panel.columns:
        spec = SYMBOLS_BY_KEY.get(col)
        default = spec.transform if spec else "log_return"
        s, applied, p = stationarize_series(panel[col], default)  # type: ignore[arg-type]
        out_cols[col] = s
        meta[col] = {"transform": applied, "adf_p": p}
    out = pd.concat(out_cols, axis=1).dropna(how="all")
    return out, meta


# ─────────────────────────────────────────────────────────────────
# CCF — Cross-Correlation Function
# ─────────────────────────────────────────────────────────────────

def _pearson(x: np.ndarray, y: np.ndarray) -> float:
    """Pearson correlation, NaN-safe."""
    if len(x) < 2 or len(y) < 2:
        return float("nan")
    sx, sy = float(np.std(x, ddof=1)), float(np.std(y, ddof=1))
    if sx == 0.0 or sy == 0.0 or not math.isfinite(sx) or not math.isfinite(sy):
        return float("nan")
    return float(np.corrcoef(x, y)[0, 1])


def compute_ccf(x: pd.Series, y: pd.Series, max_lag: int = 30) -> dict:
    """Cross-correlation function ρ(lag) = corr(x_t, y_{t+lag}).

    Sign convention:
      lag > 0 means X leads Y by `lag` periods (X today predicts Y `lag` later).
      lag < 0 means Y leads X.

    Returns a dict with the full lag profile, the 95% asymptotic confidence
    band, the peak (max |ρ|), and key metadata.
    """
    df = pd.concat([x.rename("x"), y.rename("y")], axis=1).dropna()
    if len(df) < max_lag + 30:
        raise ValueError(f"Not enough overlapping observations: {len(df)} (need >= {max_lag + 30})")

    xv = df["x"].to_numpy()
    yv = df["y"].to_numpy()
    n = len(df)

    lags: list[int] = []
    rhos: list[float] = []
    for lag in range(-max_lag, max_lag + 1):
        if lag < 0:
            a, b = xv[-lag:], yv[: n + lag]
        elif lag > 0:
            a, b = xv[: n - lag], yv[lag:]
        else:
            a, b = xv, yv
        rhos.append(_pearson(a, b))
        lags.append(lag)

    rhos_arr = np.array(rhos, dtype=float)
    # Asymptotic 95% CI under H0: ρ=0 → ~ Normal(0, 1/N).
    conf_band = float(1.96 / math.sqrt(n))

    # Peak by |ρ|
    abs_rhos = np.where(np.isfinite(rhos_arr), np.abs(rhos_arr), 0.0)
    peak_idx = int(np.argmax(abs_rhos))
    peak_lag = int(lags[peak_idx])
    peak_rho = float(rhos_arr[peak_idx])

    # Best positive- and negative-lag peaks separately (X-leads vs Y-leads)
    pos_mask = np.array([lag > 0 for lag in lags])
    neg_mask = np.array([lag < 0 for lag in lags])
    pos_peak = int(np.argmax(abs_rhos * pos_mask)) if pos_mask.any() else peak_idx
    neg_peak = int(np.argmax(abs_rhos * neg_mask)) if neg_mask.any() else peak_idx

    return {
        "lags": lags,
        "ccf": [None if not math.isfinite(r) else round(r, 6) for r in rhos_arr],
        "conf_band": round(conf_band, 6),
        "n": int(n),
        "peak": {"lag": peak_lag, "rho": round(peak_rho, 6)},
        "x_leads": {"lag": int(lags[pos_peak]), "rho": round(float(rhos_arr[pos_peak]), 6)},
        "y_leads": {"lag": int(lags[neg_peak]), "rho": round(float(rhos_arr[neg_peak]), 6)},
        "contemp_rho": round(float(rhos_arr[max_lag]), 6),  # lag=0
    }


def ccf_pair(x_symbol: str, y_symbol: str, lookback: str = "5Y", max_lag: int = 30) -> dict:
    """End-to-end CCF for a pair: fetch → align → stationarize → ccf."""
    panel = aligned_panel([x_symbol, y_symbol], lookback=lookback)
    if panel.empty or x_symbol not in panel.columns or y_symbol not in panel.columns:
        raise ValueError("Failed to fetch data for one or both symbols")

    stat_panel, meta = stationarize_panel(panel)
    if x_symbol not in stat_panel.columns or y_symbol not in stat_panel.columns:
        raise ValueError("Stationarization dropped a series")

    ccf = compute_ccf(stat_panel[x_symbol], stat_panel[y_symbol], max_lag=max_lag)
    return {
        "x": {"symbol": x_symbol, **meta[x_symbol]},
        "y": {"symbol": y_symbol, **meta[y_symbol]},
        "lookback": lookback,
        "max_lag": max_lag,
        "result": ccf,
    }


# ─────────────────────────────────────────────────────────────────
# VAR + IMPULSE RESPONSE (Tab 4)
# ─────────────────────────────────────────────────────────────────
#
# Default Cholesky ordering by exogeneity. The ordering matters for
# orthogonalized IRF — earlier series are treated as more exogenous and
# shocks to later series cannot contemporaneously affect them. This is a
# *modeling assumption*; the UI should let the user override.

_VAR_EXOGENEITY_ORDER: dict[str, int] = {}
for _i, _cat in enumerate(["Macro", "Vol", "Rates", "Credit", "FX", "Commodity", "Equity", "Factor", "Crypto"]):
    _VAR_EXOGENEITY_ORDER[_cat] = _i


def _default_chol_order(symbols: list[str]) -> list[str]:
    """Sort symbols by their universe category's default exogeneity rank.
    Within a category, sort alphabetically for determinism."""
    def _key(sym: str) -> tuple[int, str]:
        spec = SYMBOLS_BY_KEY.get(sym)
        cat = spec.category if spec else "Equity"
        return (_VAR_EXOGENEITY_ORDER.get(cat, 99), sym)
    return sorted(symbols, key=_key)


def var_basket(
    symbols: list[str],
    lookback: str = "5Y",
    max_lag: int = 10,
    irf_horizon: int = 20,
    ic: str = "aic",
    chol_order: list[str] | None = None,
) -> dict:
    """Fit a VAR on a basket of macro series, return:
      - Lag selection metrics (AIC/BIC across 1..max_lag)
      - Selected lag and its coefficient summary
      - Orthogonalized IRF: shocks[i].responses[j].values[h] for each
        shock-origin × response × horizon
      - FEVD: target[j].horizons[h].contributions[i] = % of target j's
        forecast variance at horizon h explained by shocks to i
    """
    if not (2 <= len(symbols) <= 8):
        raise ValueError("VAR basket requires 2-8 symbols")

    panel = aligned_panel(symbols, lookback=lookback)
    if panel.empty or any(s not in panel.columns for s in symbols):
        missing = [s for s in symbols if s not in panel.columns]
        raise ValueError(f"Failed to fetch data for: {missing}")

    stat_panel, meta = stationarize_panel(panel)
    # Apply Cholesky ordering to the columns we hand to statsmodels — IRF
    # ordering directly reflects column order in the input matrix.
    order = chol_order if chol_order else _default_chol_order(list(stat_panel.columns))
    order = [s for s in order if s in stat_panel.columns]
    stat_panel = stat_panel[order].dropna()

    if len(stat_panel) < max_lag + 30:
        raise ValueError(f"Not enough overlapping observations: {len(stat_panel)} (need >= {max_lag + 30})")

    from statsmodels.tsa.api import VAR
    model = VAR(stat_panel.values)

    # Lag selection: fit at every lag 1..max_lag and capture AIC + BIC.
    lag_table: list[dict] = []
    best_aic_lag: int = 1
    best_bic_lag: int = 1
    best_aic_val: float = float("inf")
    best_bic_val: float = float("inf")
    for lag in range(1, max_lag + 1):
        try:
            r = model.fit(lag)
            aic_val = float(r.aic)
            bic_val = float(r.bic)
            lag_table.append({"lag": lag, "aic": round(aic_val, 4), "bic": round(bic_val, 4)})
            if aic_val < best_aic_val:
                best_aic_val, best_aic_lag = aic_val, lag
            if bic_val < best_bic_val:
                best_bic_val, best_bic_lag = bic_val, lag
        except Exception as e:
            logger.debug(f"VAR lag {lag} fit failed: {e}")
            continue

    if not lag_table:
        raise ValueError("No VAR lag fit succeeded — check input series for collinearity or stationarity")
    selected_lag = best_aic_lag if ic.lower() == "aic" else best_bic_lag
    try:
        fitted = model.fit(selected_lag)
    except Exception as e:
        raise ValueError(f"VAR fit failed at selected lag {selected_lag}: {e}")

    # Orthogonalized IRF: shape (irf_horizon+1, M, M) where [h, j, i] is the
    # response of variable j at horizon h to a 1-σ shock to variable i.
    irf_obj = fitted.irf(irf_horizon)
    irf_arr = np.array(irf_obj.orth_irfs)  # (h+1, M, M)
    M = stat_panel.shape[1]

    shocks: list[dict] = []
    for i, shock_origin in enumerate(order):
        responses: list[dict] = []
        for j, response_var in enumerate(order):
            values = [round(float(irf_arr[h, j, i]), 6) for h in range(irf_horizon + 1)]
            responses.append({"variable": response_var, "values": values})
        shocks.append({"origin": shock_origin, "responses": responses})

    # FEVD: for each target j, share of forecast variance at horizon h
    # attributed to shocks in each variable i. statsmodels' decomp has
    # shape (M, periods, M) — so for periods=N we get h indexes 0..N-1.
    # Request periods = irf_horizon + 1 so h = irf_horizon is valid.
    fevd = fitted.fevd(irf_horizon + 1)
    fevd_data = np.array(fevd.decomp)  # (M, irf_horizon+1, M)
    targets: list[dict] = []
    # Sparse horizons trader cares about: 1d, 5d, 10d, full horizon.
    horizon_keys = sorted({h for h in [1, 5, 10, irf_horizon] if 1 <= h <= irf_horizon})
    for j, target_var in enumerate(order):
        horizons: list[dict] = []
        for h in horizon_keys:
            contrib = {order[i]: round(float(fevd_data[j, h, i]), 4) for i in range(M)}
            horizons.append({"horizon": h, "contributions": contrib})
        targets.append({"target": target_var, "horizons": horizons})

    return {
        "symbols": list(order),  # ordered as passed to VAR (Cholesky order)
        "lookback": lookback,
        "n": int(len(stat_panel)),
        "ic": ic,
        "max_lag_tested": max_lag,
        "irf_horizon": irf_horizon,
        "lag_table": lag_table,
        "selected_lag": selected_lag,
        "best_aic_lag": best_aic_lag,
        "best_bic_lag": best_bic_lag,
        "transforms": {s: meta[s]["transform"] for s in order},
        "shocks": shocks,
        "fevd_targets": targets,
    }


# ─────────────────────────────────────────────────────────────────
# TRANSFER ENTROPY (symbolic, rank-binned)
# ─────────────────────────────────────────────────────────────────
#
# TE_{X→Y} = H(Y_+ | Y) - H(Y_+ | Y, X)
#
# where Y_+ = y_{t+1}, Y = y_t, X = x_t. We bin both series into k quantile
# bins (rank-based discretization — robust to outliers and distribution
# shape) and estimate joint/marginal probabilities by counting. Memory
# length l=1: the most recent sample summarizes the past for daily macro
# data. Higher l blows up the joint table exponentially.
#
# Permutation null: shuffle X (destroys temporal alignment with Y while
# preserving X's marginal distribution) and re-compute TE. The sample TE
# is significant if it sits above the (1-α)-th percentile of the null.

def _rank_bin(s: pd.Series, k: int) -> np.ndarray:
    """Rank-based discretization into k bins. Maps each obs to {0,...,k-1}.

    Robust to outliers and distribution shape — what matters for symbolic
    TE is the relative ordering, not the magnitude. Ties get split by
    rank's default 'average' method, then floored to integers."""
    ranks = s.rank(method="average").to_numpy()
    n = len(ranks)
    edges = np.linspace(0, n, k + 1)
    # Assign each rank to a bin via searchsorted on edge boundaries.
    bins = np.clip(np.searchsorted(edges[1:], ranks, side="right"), 0, k - 1)
    return bins.astype(np.int8)


def _transfer_entropy(x_bins: np.ndarray, y_bins: np.ndarray, k: int) -> float:
    """Compute symbolic transfer entropy TE_{X→Y} in bits per sample.
    Both inputs are integer bin labels of equal length, already aligned.
    """
    if len(x_bins) != len(y_bins) or len(x_bins) < 50:
        return float("nan")

    # Build (y_+, y, x) triples for t = 0..n-2
    y_next = y_bins[1:]
    y_cur  = y_bins[:-1]
    x_cur  = x_bins[:-1]
    n = len(y_next)

    # Joint count tables. k^3 cells for triples, k^2 for pairs.
    # np.add.at handles duplicate indices correctly.
    p_yyx = np.zeros((k, k, k), dtype=np.float64)
    np.add.at(p_yyx, (y_next, y_cur, x_cur), 1.0)
    p_yyx /= n

    p_yy = p_yyx.sum(axis=2)         # marginal over X: p(y_+, y)
    p_yx = p_yyx.sum(axis=0)         # marginal over Y_+: p(y, x)
    p_y  = p_yy.sum(axis=0)          # p(y)

    if not (p_yyx > 0).any():
        return 0.0

    # Vectorized: TE = sum_{yn,yc,xc} p(yn,yc,xc) * log2[ p(yn,yc,xc) * p(yc) / (p(yn,yc) * p(yc,xc)) ]
    # Broadcast the marginals to (k,k,k) shape:
    #   p_y       indexed by yc          → axis=1
    #   p_yy      indexed by (yn, yc)    → axes (0,1)
    #   p_yx      indexed by (yc, xc)    → axes (1,2)
    p_y_b  = p_y[None, :, None]
    p_yy_b = p_yy[:, :, None]
    p_yx_b = p_yx[None, :, :]
    num = p_yyx * p_y_b
    den = p_yy_b * p_yx_b
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where((num > 0) & (den > 0), num / den, 1.0)
        log_ratio = np.where(ratio > 0, np.log2(ratio), 0.0)
    te = float(np.sum(p_yyx * log_ratio))
    return te


def _te_with_permutation(
    x: np.ndarray, y: np.ndarray, k: int, n_perm: int, rng: np.random.Generator,
) -> tuple[float, float, float]:
    """Returns (te, p_value, null_95th).

    Permutation null: reshuffle X and recompute TE. p = (#null ≥ obs) / n_perm.
    Small-sample correction: Laplace +1 in numerator and denominator so a
    perfectly significant result reports p = 1/(n_perm+1) instead of 0."""
    obs = _transfer_entropy(x, y, k)
    if not np.isfinite(obs):
        return float("nan"), float("nan"), float("nan")
    null = np.zeros(n_perm, dtype=np.float64)
    x_perm = x.copy()
    for i in range(n_perm):
        rng.shuffle(x_perm)
        null[i] = _transfer_entropy(x_perm, y, k)
    n_ge = int(np.sum(null >= obs))
    p = (n_ge + 1) / (n_perm + 1)
    null_95 = float(np.percentile(null[np.isfinite(null)], 95)) if np.any(np.isfinite(null)) else float("nan")
    return float(obs), float(p), null_95


def transfer_entropy_pair(
    x_symbol: str, y_symbol: str, lookback: str = "5Y",
    bins: int = 3, n_perm: int = 200,
) -> dict:
    """Pair-mode TE: bidirectional X→Y and Y→X with permutation p-values.
    Returns Net TE = TE(X→Y) - TE(Y→X) for the trader-headline asymmetry.
    """
    panel = aligned_panel([x_symbol, y_symbol], lookback=lookback)
    if panel.empty or x_symbol not in panel.columns or y_symbol not in panel.columns:
        raise ValueError("Failed to fetch data for one or both symbols")
    stat_panel, meta = stationarize_panel(panel)
    if x_symbol not in stat_panel.columns or y_symbol not in stat_panel.columns:
        raise ValueError("Stationarization dropped a series")

    df = pd.concat([stat_panel[x_symbol].rename("x"), stat_panel[y_symbol].rename("y")], axis=1).dropna()
    if len(df) < 50:
        raise ValueError(f"Not enough overlapping observations: {len(df)}")

    x_bins = _rank_bin(df["x"], bins)
    y_bins = _rank_bin(df["y"], bins)

    rng = np.random.default_rng(seed=42)  # deterministic for cacheability
    te_xy, p_xy, null95_xy = _te_with_permutation(x_bins, y_bins, bins, n_perm, rng)
    te_yx, p_yx, null95_yx = _te_with_permutation(y_bins, x_bins, bins, n_perm, rng)

    net_te = float(te_xy - te_yx)
    # Direction call: who's the dominant info-source?
    if p_xy < 0.05 and p_yx >= 0.05:
        dominant = f"{x_symbol}→{y_symbol}"
    elif p_yx < 0.05 and p_xy >= 0.05:
        dominant = f"{y_symbol}→{x_symbol}"
    elif p_xy < 0.05 and p_yx < 0.05:
        dominant = "feedback (both directions significant)"
    else:
        dominant = "neither significant"

    return {
        "x": {"symbol": x_symbol, **meta[x_symbol]},
        "y": {"symbol": y_symbol, **meta[y_symbol]},
        "lookback": lookback,
        "bins": bins,
        "n_perm": n_perm,
        "n": int(len(df)),
        "x_to_y": {"te_bits": round(te_xy, 5), "p_value": round(p_xy, 4), "null_95th": round(null95_xy, 5)},
        "y_to_x": {"te_bits": round(te_yx, 5), "p_value": round(p_yx, 4), "null_95th": round(null95_yx, 5)},
        "net_te": round(net_te, 5),
        "dominant": dominant,
    }


def transfer_entropy_scan(
    target: str, universe: list[str] | None, lookback: str = "5Y",
    bins: int = 3, n_perm: int = 100,
) -> dict:
    """Scan-mode TE: rank universe drivers by TE(driver → target).

    Lower n_perm than pair (100 vs 200) — we're running 2 directions × ~50
    drivers, so even at 100 perms the scan takes ~30-60s. Bonferroni applied
    to the family of significance tests across the scan."""
    if universe is None:
        universe = list(SYMBOLS_BY_KEY.keys())
    if target in universe:
        universe = [s for s in universe if s != target]

    panel = aligned_panel([target] + universe, lookback=lookback)
    if panel.empty or target not in panel.columns:
        raise ValueError(f"Failed to fetch target series: {target}")
    stat_panel, meta = stationarize_panel(panel)
    if target not in stat_panel.columns:
        raise ValueError(f"Target series {target} dropped during stationarization")

    target_series = stat_panel[target]
    rows: list[dict] = []
    rng = np.random.default_rng(seed=42)

    for driver in universe:
        if driver not in stat_panel.columns:
            continue
        try:
            df = pd.concat([stat_panel[driver].rename("x"), target_series.rename("y")], axis=1).dropna()
            if len(df) < 50:
                continue
            x_bins = _rank_bin(df["x"], bins)
            y_bins = _rank_bin(df["y"], bins)
            te_xy, p_xy, null95 = _te_with_permutation(x_bins, y_bins, bins, n_perm, rng)
            te_yx, p_yx, _ = _te_with_permutation(y_bins, x_bins, bins, n_perm, rng)
        except Exception as e:
            logger.debug(f"TE scan skipped {driver}: {e}")
            continue

        rows.append({
            "driver": driver,
            "label": SYMBOLS_BY_KEY[driver].label,
            "category": SYMBOLS_BY_KEY[driver].category,
            "te_xy": round(te_xy, 5),
            "p_xy": round(p_xy, 4),
            "te_yx": round(te_yx, 5),
            "p_yx": round(p_yx, 4),
            "net_te": round(te_xy - te_yx, 5),
            "null_95th": round(null95, 5),
            "n": int(len(df)),
            "transform": meta[driver]["transform"],
        })

    # Bonferroni across the family of TE p-values (2 per driver)
    m = max(1, 2 * len(rows))
    for r in rows:
        r["p_xy_bonf"] = round(min(1.0, r["p_xy"] * m), 4)
        r["p_yx_bonf"] = round(min(1.0, r["p_yx"] * m), 4)

    rows.sort(key=lambda r: -r["te_xy"])  # rank by raw TE driver→target

    return {
        "target": target,
        "lookback": lookback,
        "bins": bins,
        "n_perm": n_perm,
        "n_drivers_tested": len(rows),
        "bonferroni_m": m,
        "target_meta": meta.get(target),
        "rows": rows,
    }


# ─────────────────────────────────────────────────────────────────
# GRANGER CAUSALITY
# ─────────────────────────────────────────────────────────────────

def _granger_test(driver: pd.Series, target: pd.Series, max_lag: int) -> dict:
    """Run statsmodels' grangercausalitytests on a driver→target pair.

    Tests whether `driver`'s past `lag` values improve prediction of `target`
    beyond `target`'s own past. statsmodels expects the test column FIRST
    (the variable being predicted) and the explanatory column SECOND. So we
    pass [target, driver] in that order to test 'driver causes target'.
    """
    from statsmodels.tsa.stattools import grangercausalitytests

    df = pd.concat([target.rename("y"), driver.rename("x")], axis=1).dropna()
    if len(df) < max_lag + 30:
        raise ValueError(f"Not enough overlapping observations: {len(df)} (need >= {max_lag + 30})")

    # statsmodels prints chatty warnings; we suppress at the call site.
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        results = grangercausalitytests(df.values, maxlag=max_lag, verbose=False)

    by_lag: list[dict] = []
    best_p: float = 1.0
    best_lag: int = 1
    for lag, res in results.items():
        # Each lag has 4 tests; the F-test ('ssr_ftest') is the standard.
        # ssr_ftest tuple: (F-statistic, p-value, df_denom, df_num)
        ftest = res[0]["ssr_ftest"]
        f_stat, p_val = float(ftest[0]), float(ftest[1])
        by_lag.append({"lag": int(lag), "f_stat": round(f_stat, 4), "p_value": round(p_val, 6)})
        if p_val < best_p:
            best_p = p_val
            best_lag = int(lag)

    return {
        "n": int(len(df)),
        "max_lag_tested": int(max_lag),
        "by_lag": by_lag,
        "best": {"lag": best_lag, "p_value": round(best_p, 6)},
    }


def granger_pair(x_symbol: str, y_symbol: str, lookback: str = "5Y", max_lag: int = 10) -> dict:
    """Bidirectional Granger: tests both X→Y and Y→X. Trader-useful because
    feedback loops (rates ⇄ equities) often show in both directions."""
    panel = aligned_panel([x_symbol, y_symbol], lookback=lookback)
    if panel.empty or x_symbol not in panel.columns or y_symbol not in panel.columns:
        raise ValueError("Failed to fetch data for one or both symbols")
    stat_panel, meta = stationarize_panel(panel)
    if x_symbol not in stat_panel.columns or y_symbol not in stat_panel.columns:
        raise ValueError("Stationarization dropped a series")

    xy = _granger_test(stat_panel[x_symbol], stat_panel[y_symbol], max_lag)
    yx = _granger_test(stat_panel[y_symbol], stat_panel[x_symbol], max_lag)

    # Verdict at α=0.05 — for the trader-facing summary
    def _verdict(p: float) -> str:
        if p < 0.001: return "strong"
        if p < 0.01:  return "moderate"
        if p < 0.05:  return "weak"
        return "none"

    return {
        "x": {"symbol": x_symbol, **meta[x_symbol]},
        "y": {"symbol": y_symbol, **meta[y_symbol]},
        "lookback": lookback,
        "max_lag": max_lag,
        "x_to_y": {**xy, "verdict": _verdict(xy["best"]["p_value"])},
        "y_to_x": {**yx, "verdict": _verdict(yx["best"]["p_value"])},
    }


def granger_scan(target: str, universe: list[str] | None, lookback: str = "5Y", max_lag: int = 10) -> dict:
    """Scan: which universe drivers Granger-cause target? Returns rows with
    the best (min p) lag for each driver, plus Bonferroni-adjusted p-values
    so the user can see what survives multiple-testing correction across the
    full universe."""
    if universe is None:
        universe = list(SYMBOLS_BY_KEY.keys())
    if target in universe:
        universe = [s for s in universe if s != target]

    panel = aligned_panel([target] + universe, lookback=lookback)
    if panel.empty or target not in panel.columns:
        raise ValueError(f"Failed to fetch target series: {target}")
    stat_panel, meta = stationarize_panel(panel)
    if target not in stat_panel.columns:
        raise ValueError(f"Target series {target} dropped during stationarization")

    target_series = stat_panel[target]
    rows: list[dict] = []
    for driver in universe:
        if driver not in stat_panel.columns:
            continue
        try:
            xy = _granger_test(stat_panel[driver], target_series, max_lag)
            yx = _granger_test(target_series, stat_panel[driver], max_lag)
        except Exception as e:
            logger.debug(f"Granger scan skipped {driver}: {e}")
            continue
        rows.append({
            "driver": driver,
            "label": SYMBOLS_BY_KEY[driver].label,
            "category": SYMBOLS_BY_KEY[driver].category,
            "xy_best_lag": xy["best"]["lag"],
            "xy_best_p":   xy["best"]["p_value"],
            "yx_best_lag": yx["best"]["lag"],
            "yx_best_p":   yx["best"]["p_value"],
            "n":           xy["n"],
            "transform":   meta[driver]["transform"],
        })

    # Bonferroni correction across the family of tests run on this scan.
    # Each row contributed two tests (xy and yx), so total tests = 2 * len(rows).
    m = max(1, 2 * len(rows))
    for r in rows:
        r["xy_p_bonf"] = round(min(1.0, r["xy_best_p"] * m), 6)
        r["yx_p_bonf"] = round(min(1.0, r["yx_best_p"] * m), 6)

    # Default sort: drivers most strongly Granger-causing target (lowest xy p)
    rows.sort(key=lambda r: r["xy_best_p"])

    return {
        "target": target,
        "lookback": lookback,
        "max_lag": max_lag,
        "n_drivers_tested": len(rows),
        "bonferroni_m": m,
        "target_meta": meta.get(target),
        "rows": rows,
    }


def ccf_scan(target: str, universe: list[str] | None, lookback: str = "5Y", max_lag: int = 30) -> dict:
    """Scan CCF(driver, target) across the universe. For each candidate driver,
    return the strongest |ρ| at non-zero lag where driver leads target (lag > 0
    in the X-leads convention) AND the strongest |ρ| at any lag.

    This is the trader 'who leads X' tool.
    """
    if universe is None:
        universe = list(SYMBOLS_BY_KEY.keys())
    if target in universe:
        universe = [s for s in universe if s != target]

    panel = aligned_panel([target] + universe, lookback=lookback)
    if panel.empty or target not in panel.columns:
        raise ValueError(f"Failed to fetch target series: {target}")

    stat_panel, meta = stationarize_panel(panel)
    if target not in stat_panel.columns:
        raise ValueError(f"Target series {target} dropped during stationarization")

    target_series = stat_panel[target]
    rows: list[dict] = []

    for driver in universe:
        if driver not in stat_panel.columns:
            continue
        try:
            ccf = compute_ccf(stat_panel[driver], target_series, max_lag=max_lag)
        except ValueError:
            continue
        rows.append({
            "driver": driver,
            "label": SYMBOLS_BY_KEY[driver].label if driver in SYMBOLS_BY_KEY else driver,
            "category": SYMBOLS_BY_KEY[driver].category if driver in SYMBOLS_BY_KEY else None,
            "x_leads_lag": ccf["x_leads"]["lag"],     # driver leads target by N
            "x_leads_rho": ccf["x_leads"]["rho"],
            "y_leads_lag": ccf["y_leads"]["lag"],     # target leads driver
            "y_leads_rho": ccf["y_leads"]["rho"],
            "peak_lag":    ccf["peak"]["lag"],
            "peak_rho":    ccf["peak"]["rho"],
            "contemp_rho": ccf["contemp_rho"],
            "n":           ccf["n"],
            "conf_band":   ccf["conf_band"],
            "transform":   meta[driver]["transform"],
        })

    # Default sort: drivers that lead target the strongest (positive lag, abs ρ)
    rows.sort(key=lambda r: -abs(r["x_leads_rho"] or 0))

    return {
        "target": target,
        "lookback": lookback,
        "max_lag": max_lag,
        "target_meta": meta.get(target),
        "rows": rows,
    }

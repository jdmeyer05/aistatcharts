import numpy as np
import pandas as pd
from datetime import date

def weekly_log_returns(px_close: pd.Series) -> pd.Series:
    """Calculates weekly log returns keyed to Monday dates of ISO weeks."""
    px_close = px_close.sort_index()
    dlog = np.log(px_close / px_close.shift(1)).dropna()
    df = pd.DataFrame({"dlog": dlog})
    ic = dlog.index.isocalendar()
    df["iso_year"] = ic["year"].astype(int).to_numpy()
    df["iso_week"] = ic["week"].astype(int).to_numpy()
    wk_log = df.groupby(["iso_year", "iso_week"], sort=True)["dlog"].sum()
    monday_dates = pd.to_datetime([date.fromisocalendar(int(y), int(w), 1) for (y, w) in wk_log.index])
    wk_log.index = monday_dates
    wk_log.name = "weekly_logret"
    return wk_log.sort_index()

def _seasonal_profile(logrets: pd.Series) -> pd.Series:
    """Average log return by ISO week (1..53)."""
    weeks = logrets.index.isocalendar().week.astype(int)
    df = pd.DataFrame({"logret": logrets.values, "week": weeks.to_numpy()})
    wk_mu = df.groupby("week", sort=True)["logret"].mean()
    wk_mu.index = wk_mu.index.astype(int)
    return wk_mu.sort_index()

def _future_seasonal_baseline(dates: pd.DatetimeIndex, seasonal_mu: pd.Series) -> np.ndarray:
    """Maps the historical seasonal mean to future dates."""
    keys = dates.isocalendar().week.astype(int).to_numpy()
    mu_map = seasonal_mu.to_dict()
    return np.array([float(mu_map.get(int(k), 0.0)) for k in keys], dtype=float)

def simulate_to_year_end_weekly(px_close: pd.Series, n_sims: int, lookback_days: int, 
                                method: str, drift_bias_annual_pct: float, vol_mult: float, 
                                seed: int, use_seasonality: bool = True):
    """
    Weekly Monte Carlo from next ISO week to the last ISO week of the current year.
    Returns: (paths_matrix, future_mondays_index)
    """
    if px_close.empty:
        return np.empty((n_sims, 0)), pd.DatetimeIndex([])

    # Clean data
    px_close = px_close[px_close > 0].dropna()
    last_ts = pd.Timestamp(px_close.index[-1])
    year = last_ts.year

    # Find Next Monday
    days_to_next_mon = (7 - last_ts.weekday()) % 7
    if days_to_next_mon == 0: days_to_next_mon = 7
    next_monday = (last_ts + pd.Timedelta(days=days_to_next_mon)).normalize()

    # Find Last ISO week Monday of the year
    last_iso_week_monday = pd.Timestamp(date.fromisocalendar(
        year, pd.Timestamp(f"{year}-12-28").isocalendar().week, 1
    ))

    if next_monday > last_iso_week_monday:
        return np.empty((n_sims, 0)), pd.DatetimeIndex([])

    future_mondays = pd.date_range(next_monday, last_iso_week_monday, freq="W-MON")
    T = len(future_mondays)

    # Historical metrics
    wk_logrets = weekly_log_returns(px_close)
    lookback_start = (wk_logrets.index.max() - pd.Timedelta(days=lookback_days))
    wk_tail = wk_logrets.loc[wk_logrets.index > lookback_start]
    if wk_tail.empty: wk_tail = wk_logrets.copy()

    if use_seasonality:
        seasonal_mu_hist = _seasonal_profile(wk_logrets)
        wk_nums_hist = wk_logrets.index.isocalendar().week.astype(int)
        seasonal_component = wk_nums_hist.map(seasonal_mu_hist).astype(float).fillna(0.0).to_numpy()
        resid = wk_logrets.values - seasonal_component

        resid_series = pd.Series(resid, index=wk_logrets.index)
        resid_tail = resid_series.loc[resid_series.index > lookback_start]
        if resid_tail.empty: resid_tail = resid_series.copy()

        mu_hist = float(resid_tail.mean())
        sigma_hist = float(resid_tail.std(ddof=1))
        seasonal_future = _future_seasonal_baseline(future_mondays, seasonal_mu_hist)
        bootstrap_source = resid_tail.to_numpy()
    else:
        mu_hist = float(wk_tail.mean())
        sigma_hist = float(wk_tail.std(ddof=1))
        seasonal_future = np.zeros(T, dtype=float)
        bootstrap_source = wk_tail.to_numpy()

    # Apply modifiers
    drift_bias_weekly = np.log(1.0 + drift_bias_annual_pct / 100.0) / 52.0
    mu_resid_adj = mu_hist + drift_bias_weekly
    sigma_adj = sigma_hist * vol_mult

    rng = np.random.default_rng(seed)
    start_price = float(px_close.iloc[-1])

    paths = np.empty((n_sims, T), dtype=float)
    prices = np.full(n_sims, start_price, dtype=float)

    # Generate Paths
    for t in range(T):
        if method == "bootstrap" and bootstrap_source.size > 0:
            eps = rng.choice(bootstrap_source, size=n_sims, replace=True)
            eps = (eps - eps.mean()) * vol_mult + eps.mean()
            r_t = seasonal_future[t] + drift_bias_weekly + eps
        else:
            eps = rng.normal(mu_resid_adj, sigma_adj, size=n_sims)
            r_t = seasonal_future[t] + eps
        
        prices = prices * np.exp(r_t)
        paths[:, t] = prices

    return paths, future_mondays

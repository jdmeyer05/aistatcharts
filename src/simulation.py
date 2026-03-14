import streamlit as st
import numpy as np
import pandas as pd
from datetime import date

# --- ORIGINAL DAILY TACTICAL ENGINE ---

@st.cache_data(show_spinner="Running 30-Day Tactical Forecast...")
def simulate_30d_tactical(px_close: pd.Series, n_sims: int, lookback_days: int, 
                          method: str, drift_bias_annual_pct: float, vol_mult: float, 
                          seed: int):
    """
    The Original Daily Engine: Uses high-resolution daily log-returns.
    Ideal for 30-day tactical outlooks.
    """
    if px_close.empty:
        return np.empty((n_sims, 0)), pd.DatetimeIndex([])

    # Process returns
    px_close = px_close[px_close > 0].dropna()
    dlog = np.log(px_close / px_close.shift(1)).dropna()
    
    # Apply lookback window
    lookback_start = (dlog.index.max() - pd.Timedelta(days=lookback_days))
    tail = dlog.loc[dlog.index > lookback_start]
    if tail.empty: tail = dlog.copy()

    # Calculate Parameters
    mu_hist = float(tail.mean())
    sigma_hist = float(tail.std(ddof=1))
    
    # Convert annual drift to daily (252 trading days)
    drift_bias_daily = (drift_bias_annual_pct / 100.0) / 252.0
    
    rng = np.random.default_rng(seed)
    start_price = float(px_close.iloc[-1])
    T = 30 # Hardcoded to 30 days as requested

    paths = np.empty((n_sims, T), dtype=float)
    prices = np.full(n_sims, start_price, dtype=float)

    # Simulation Loop
    for t in range(T):
        if method == "bootstrap":
            # Pure daily bootstrap from the historical tail
            shocks = rng.choice(tail.to_numpy(), size=n_sims, replace=True)
            # Center and scale shocks by vol_mult
            shocks = (shocks - shocks.mean()) * vol_mult + shocks.mean()
            r_t = drift_bias_daily + shocks
        else:
            # Standard Gaussian Random Walk
            r_t = rng.normal(mu_hist + drift_bias_daily, sigma_hist * vol_mult, size=n_sims)
        
        prices = prices * np.exp(r_t)
        paths[:, t] = prices

    # Generate business day index
    last_ts = pd.Timestamp(px_close.index[-1])
    future_dates = pd.bdate_range(start=last_ts + pd.Timedelta(days=1), periods=T)
    
    return paths, future_dates

# --- WEEKLY SEASONAL ENGINE (For Year-End) ---

def weekly_log_returns(px_close: pd.Series) -> pd.Series:
    px_close = px_close.sort_index()
    dlog = np.log(px_close / px_close.shift(1)).dropna()
    df = pd.DataFrame({"dlog": dlog})
    ic = dlog.index.isocalendar()
    df["iso_year"] = ic["year"].astype(int).to_numpy()
    df["iso_week"] = ic["week"].astype(int).to_numpy()
    wk_log = df.groupby(["iso_year", "iso_week"], sort=True)["dlog"].sum()
    monday_dates = pd.to_datetime([date.fromisocalendar(int(y), int(w), 1) for (y, w) in wk_log.index])
    wk_log.index = monday_dates
    return wk_log.sort_index()

@st.cache_data(show_spinner="Running Year-End Strategic Forecast...")
def simulate_to_year_end_weekly(px_close: pd.Series, n_sims: int, lookback_days: int, 
                                method: str, drift_bias_annual_pct: float, vol_mult: float, 
                                seed: int, use_seasonality: bool = True):
    # (Existing weekly seasonal logic remains for long-term outlook)
    if px_close.empty: return np.empty((n_sims, 0)), pd.DatetimeIndex([])
    
    last_ts = pd.Timestamp(px_close.index[-1])
    year = last_ts.year
    days_to_next_mon = (7 - last_ts.weekday()) % 7
    if days_to_next_mon == 0: days_to_next_mon = 7
    next_monday = (last_ts + pd.Timedelta(days=days_to_next_mon)).normalize()
    last_iso_mon = pd.Timestamp(date.fromisocalendar(year, pd.Timestamp(f"{year}-12-28").isocalendar().week, 1))

    if next_monday > last_iso_mon: return np.empty((n_sims, 0)), pd.DatetimeIndex([])
    future_mondays = pd.date_range(next_monday, last_iso_mon, freq="W-MON")
    T = len(future_mondays)
    
    wk_logrets = weekly_log_returns(px_close)
    rng = np.random.default_rng(seed)
    start_price = float(px_close.iloc[-1])
    paths = np.empty((n_sims, T), dtype=float)
    prices = np.full(n_sims, start_price, dtype=float)
    
    drift_weekly = (drift_bias_annual_pct / 100.0) / 52.0
    
    for t in range(T):
        shocks = rng.choice(wk_logrets.to_numpy(), size=n_sims, replace=True)
        shocks = (shocks - shocks.mean()) * vol_mult + shocks.mean()
        prices = prices * np.exp(drift_weekly + shocks)
        paths[:, t] = prices
        
    return paths, future_mondays

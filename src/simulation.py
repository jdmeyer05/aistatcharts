import streamlit as st
import numpy as np
import pandas as pd
from datetime import date
from sklearn.ensemble import RandomForestRegressor

# --- HELPER FUNCTIONS FOR SEASONALITY ---

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

# --- DEEP RANDOM FOREST TACTICAL ENGINE (30-Day) ---

@st.cache_data(show_spinner="Training Deep Random Forest & Predicting...")
def predict_30d_random_forest(px_close: pd.Series, n_estimators: int = 200, lookback_days: int = 1000):
    """
    Uses a Deep Random Forest to predict the next 30 days.
    """
    if len(px_close) < 100:
        return np.empty((0, 0)), pd.DatetimeIndex([])

    df = pd.DataFrame(px_close).rename(columns={px_close.name: 'Close'})
    df['Returns'] = df['Close'].pct_change()
    for l in [1, 2, 3, 5, 10]:
        df[f'lag_{l}'] = df['Close'].shift(l)
    df['MA5'] = df['Close'].rolling(5).mean()
    df['MA20'] = df['Close'].rolling(20).mean()
    df['Vol20'] = df['Returns'].rolling(20).std()
    
    df['Target'] = df['Close'].shift(-1)
    df = df.dropna().tail(lookback_days)
    
    features = [c for c in df.columns if 'lag' in c or 'MA' in c or 'Vol' in c]
    X, y = df[features], df['Target']
    
    model = RandomForestRegressor(n_estimators=n_estimators, max_depth=10, random_state=42, n_jobs=-1)
    model.fit(X, y)
    
    T = 30
    current_features = X.iloc[-1:].copy()
    temp_close = df['Close'].tolist()
    
    preds_mean, preds_std = [], []
    for _ in range(T):
        tree_preds = [tree.predict(current_features.values)[0] for tree in model.estimators_]
        p_mean, p_std = np.mean(tree_preds), np.std(tree_preds)
        preds_mean.append(p_mean)
        preds_std.append(p_std)
        
        temp_close.append(p_mean)
        new_row = {
            'lag_1': temp_close[-1], 'lag_2': temp_close[-2], 'lag_3': temp_close[-3],
            'lag_5': temp_close[-5], 'lag_10': temp_close[-10],
            'MA5': np.mean(temp_close[-5:]), 'MA20': np.mean(temp_close[-20:]),
            'Vol20': np.std(np.diff(np.log(temp_close[-21:])))
        }
        current_features = pd.DataFrame([new_row])

    preds_mean, preds_std = np.array(preds_mean), np.array(preds_std)
    expansion = np.sqrt(np.arange(1, T + 1))
    lower_bound = preds_mean - (preds_std * 2 * expansion)
    upper_bound = preds_mean + (preds_std * 2 * expansion)
    
    last_ts = pd.Timestamp(px_close.index[-1])
    future_dates = pd.bdate_range(start=last_ts + pd.Timedelta(days=1), periods=T)
    
    return {'mean': preds_mean, 'lower': lower_bound, 'upper': upper_bound}, future_dates

# --- RESTORED ROBUST SEASONAL MONTE CARLO (Year-End) ---

@st.cache_data(show_spinner="Running Robust Seasonal Year-End Forecast...")
def simulate_to_year_end_weekly(px_close: pd.Series, n_sims: int, lookback_days: int, 
                                method: str, drift_bias_annual_pct: float, vol_mult: float, 
                                seed: int, use_seasonality: bool = True):
    """
    Weekly Monte Carlo using historical ISO-week seasonality profile.
    This version avoids 'flat lines' by layering seasonal drift on top of residuals.
    """
    if px_close.empty:
        return np.empty((n_sims, 0)), pd.DatetimeIndex([])

    px_close = px_close[px_close > 0].dropna()
    last_ts = pd.Timestamp(px_close.index[-1])
    year = last_ts.year

    # Calculate Timeline
    days_to_next_mon = (7 - last_ts.weekday()) % 7
    if days_to_next_mon == 0: days_to_next_mon = 7
    next_monday = (last_ts + pd.Timedelta(days=days_to_next_mon)).normalize()
    last_iso_week_monday = pd.Timestamp(date.fromisocalendar(year, pd.Timestamp(f"{year}-12-28").isocalendar().week, 1))

    if next_monday > last_iso_week_monday:
        return np.empty((n_sims, 0)), pd.DatetimeIndex([])

    future_mondays = pd.date_range(next_monday, last_iso_week_monday, freq="W-MON")
    T = len(future_mondays)

    # Historical Returns & Seasonality
    wk_logrets = weekly_log_returns(px_close)
    seasonal_mu_hist = _seasonal_profile(wk_logrets)
    
    # Calculate Residuals (Removing seasonality to get pure noise)
    wk_nums_hist = wk_logrets.index.isocalendar().week.astype(int)
    seasonal_component = wk_nums_hist.map(seasonal_mu_hist).astype(float).fillna(0.0).to_numpy()
    residuals = wk_logrets.values - seasonal_component
    
    # Apply Lookback to noise
    lookback_start = (wk_logrets.index.max() - pd.Timedelta(days=lookback_days))
    resid_series = pd.Series(residuals, index=wk_logrets.index)
    resid_tail = resid_series.loc[resid_series.index > lookback_start]
    if resid_tail.empty: resid_tail = resid_series.copy()

    # Model Parameters
    mu_noise = float(resid_tail.mean())
    sigma_noise = float(resid_tail.std(ddof=1))
    drift_weekly_adj = (drift_bias_annual_pct / 100.0) / 52.0
    
    # Future Baseline
    seasonal_future = _future_seasonal_baseline(future_mondays, seasonal_mu_hist)
    
    rng = np.random.default_rng(seed)
    start_price = float(px_close.iloc[-1])
    paths = np.empty((n_sims, T), dtype=float)
    prices = np.full(n_sims, start_price, dtype=float)

    bootstrap_source = resid_tail.to_numpy()

    for t in range(T):
        if method == "bootstrap":
            # Sample noise, then center and scale it
            shocks = rng.choice(bootstrap_source, size=n_sims, replace=True)
            shocks = (shocks - shocks.mean()) * vol_mult + shocks.mean()
            # Total return = Seasonal Baseline + Bias + Noise
            r_t = seasonal_future[t] + drift_weekly_adj + shocks
        else:
            # Gaussian approach
            shocks = rng.normal(mu_noise + drift_weekly_adj, sigma_noise * vol_mult, size=n_sims)
            r_t = seasonal_future[t] + shocks
            
        prices = prices * np.exp(r_t)
        paths[:, t] = prices

    return paths, future_mondays

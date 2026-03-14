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

# --- ADVANCED ML TACTICAL ENGINE (30-Day) ---

@st.cache_data(show_spinner="Training Advanced Multi-Output ML Forecast...")
def predict_30d_random_forest(px_close: pd.Series, n_estimators: int = 200, lookback_days: int = 1000):
    """
    Advanced Direct Multi-Step Random Forest.
    Predicts stationary returns, utilizes momentum features (RSI/MACD), 
    and generates uncertainty bands via tree-level simulation paths.
    """
    if len(px_close) < 100:
        return np.empty((0, 0)), pd.DatetimeIndex([])

    df = pd.DataFrame(px_close).rename(columns={px_close.name: 'Close'})
    
    # 1. STATIONARITY: Predict Returns, not Prices
    df['Returns'] = df['Close'].pct_change()
    
    # 2. ADVANCED FEATURE ENGINEERING
    # Autoregressive Lags
    for l in [1, 2, 3, 5, 10]:
        df[f'lag_ret_{l}'] = df['Returns'].shift(l)
        
    # Volatility Cluster Detection
    df['Vol_20'] = df['Returns'].rolling(20).std()
    
    # RSI (14-day Wilder's Smoothing)
    delta = df['Close'].diff()
    gain = delta.where(delta > 0, 0.0).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0.0)).ewm(alpha=1/14, adjust=False).mean()
    rs = gain / loss
    df['RSI_14'] = 100 - (100 / (1 + rs))
    
    # MACD & Momentum
    ema_12 = df['Close'].ewm(span=12, adjust=False).mean()
    ema_26 = df['Close'].ewm(span=26, adjust=False).mean()
    df['MACD'] = ema_12 - ema_26
    df['MACD_Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
    df['MACD_Hist'] = df['MACD'] - df['MACD_Signal']
    
    # 3. DIRECT MULTI-STEP TARGETS
    T = 30
    for i in range(1, T + 1):
        df[f'Target_Ret_{i}'] = df['Returns'].shift(-i)
        
    features = [c for c in df.columns if 'lag' in c or 'Vol' in c or 'RSI' in c or 'MACD' in c]
    targets = [f'Target_Ret_{i}' for i in range(1, T + 1)]
    
    # Grab the absolute latest features for inference BEFORE dropping NaNs
    current_features = df[features].iloc[-1:].values
    current_price = df['Close'].iloc[-1]
        
    # NOW drop NaNs to create a perfectly clean training dataset
    train_df = df.dropna().tail(lookback_days)
    X_train = train_df[features]
    y_train = train_df[targets]
    
    # 4. TRAIN MULTI-OUTPUT MODEL
    model = RandomForestRegressor(n_estimators=n_estimators, max_depth=10, random_state=42, n_jobs=-1)
    model.fit(X_train, y_train)
    
    # 5. GENERATE SIMULATION PATHS FROM TREE VARIANCE
    tree_preds = [tree.predict(current_features)[0] for tree in model.estimators_]
    tree_preds = np.array(tree_preds) 
    
    tree_growth = 1.0 + tree_preds
    tree_cum_returns = np.cumprod(tree_growth, axis=1)
    tree_price_paths = current_price * tree_cum_returns 
    
    p50_path = np.percentile(tree_price_paths, 50, axis=0)
    p5_path = np.percentile(tree_price_paths, 5, axis=0)
    p95_path = np.percentile(tree_price_paths, 95, axis=0)
    
    last_ts = pd.Timestamp(px_close.index[-1])
    future_dates = pd.bdate_range(start=last_ts + pd.Timedelta(days=1), periods=T)
    
    return {
        'mean': p50_path,
        'lower': p5_path,
        'upper': p95_path
    }, future_dates

# --- RESTORED ROBUST SEASONAL MONTE CARLO (Year-End) ---

@st.cache_data(show_spinner="Running Robust Seasonal Year-End Forecast...")
def simulate_to_year_end_weekly(px_close: pd.Series, n_sims: int, lookback_days: int, 
                                method: str, drift_bias_annual_pct: float, vol_mult: float, 
                                seed: int, use_seasonality: bool = True):
    """
    Weekly Monte Carlo using historical ISO-week seasonality profile.
    """
    if px_close.empty:
        return np.empty((n_sims, 0)), pd.DatetimeIndex([])

    px_close = px_close[px_close > 0].dropna()
    last_ts = pd.Timestamp(px_close.index[-1])
    year = last_ts.year

    days_to_next_mon = (7 - last_ts.weekday()) % 7
    if days_to_next_mon == 0: days_to_next_mon = 7
    next_monday = (last_ts + pd.Timedelta(days=days_to_next_mon)).normalize()
    last_iso_week_monday = pd.Timestamp(date.fromisocalendar(year, pd.Timestamp(f"{year}-12-28").isocalendar().week, 1))

    if next_monday > last_iso_week_monday:
        return np.empty((n_sims, 0)), pd.DatetimeIndex([])

    future_mondays = pd.date_range(next_monday, last_iso_week_monday, freq="W-MON")
    T = len(future_mondays)

    wk_logrets = weekly_log_returns(px_close)
    seasonal_mu_hist = _seasonal_profile(wk_logrets)
    wk_nums_hist = wk_logrets.index.isocalendar().week.astype(int)
    seasonal_component = wk_nums_hist.map(seasonal_mu_hist).astype(float).fillna(0.0).to_numpy()
    residuals = wk_logrets.values - seasonal_component
    
    resid_series = pd.Series(residuals, index=wk_logrets.index)
    resid_tail = resid_series.loc[resid_series.index > (wk_logrets.index.max() - pd.Timedelta(days=lookback_days))]
    if resid_tail.empty: resid_tail = resid_series.copy()

    mu_noise = float(resid_tail.mean())
    sigma_noise = float(resid_tail.std(ddof=1))
    drift_weekly_adj = (drift_bias_annual_pct / 100.0) / 52.0
    seasonal_future = _future_seasonal_baseline(future_mondays, seasonal_mu_hist)
    
    rng = np.random.default_rng(seed)
    start_price = float(px_close.iloc[-1])
    paths = np.empty((n_sims, T), dtype=float)
    prices = np.full(n_sims, start_price, dtype=float)
    bootstrap_source = resid_tail.to_numpy()

    for t in range(T):
        if method == "bootstrap":
            shocks = rng.choice(bootstrap_source, size=n_sims, replace=True)
            shocks = (shocks - shocks.mean()) * vol_mult + shocks.mean()
            r_t = seasonal_future[t] + drift_weekly_adj + shocks
        else:
            shocks = rng.normal(mu_noise + drift_weekly_adj, sigma_noise * vol_mult, size=n_sims)
            r_t = seasonal_future[t] + shocks
            
        prices = prices * np.exp(r_t)
        paths[:, t] = prices

    return paths, future_mondays

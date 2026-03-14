import numpy as np
import pandas as pd

def get_returns(px, freq='W'):
    """Calculates log returns grouped by week or month."""
    log_rets = np.log(px / px.shift(1)).dropna()
    df = pd.DataFrame({"log_ret": log_rets.values}, index=log_rets.index)
    if freq == 'W':
        ic = df.index.isocalendar()
        df["group"], df["year"] = ic["week"].astype(int), ic["year"].astype(int)
    else:
        df["group"], df["year"] = df.index.month, df.index.year
    
    return df.groupby(["year", "group"])["log_ret"].sum()

def run_monte_carlo_engine(px, n_sims, drift_bias, vol_mult, method, use_seasonality):
    """Vectorized calculation of year-end price paths."""
    today = pd.Timestamp.now()
    weeks_to_sim = max(1, ((pd.Timestamp(year=today.year, month=12, day=31) - today).days // 7) + 1)
    
    wk_logrets = get_returns(px, 'W')
    seasonal_profile = wk_logrets.groupby(level=1).mean()
    
    target_weeks = [(today + pd.Timedelta(weeks=t)).isocalendar().week for t in range(weeks_to_sim)]
    s_drifts = np.array([seasonal_profile.get(w, 0) if use_seasonality else 0 for w in target_weeks])
    drift_weekly = np.log(1 + drift_bias/100) / 52
    
    if method == "bootstrap":
        shocks = np.random.choice(wk_logrets.values, size=(n_sims, weeks_to_sim))
        shocks = (shocks - wk_logrets.mean()) * vol_mult
    else:
        shocks = np.random.normal(0, wk_logrets.std() * vol_mult, size=(n_sims, weeks_to_sim))
        
    total_returns = s_drifts + drift_weekly + shocks
    paths = float(px.iloc[-1]) * np.exp(np.cumsum(total_returns, axis=1))
    
    p5, p50, p95 = np.percentile(paths, [5, 50, 95], axis=0)
    return p5, p50, p95, weeks_to_sim

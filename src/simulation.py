import numpy as np
import pandas as pd

def run_monte_carlo_engine(px_close, n_sims, drift_pct, vol_mult, method, seasonal):
    """
    Runs the Monte Carlo simulation and returns the 5th, 50th, and 95th percentiles.
    """
    # 1. Calculate historical daily returns
    daily_returns = px_close.pct_change().dropna()
    
    # Calculate baseline statistics
    mu = daily_returns.mean()
    sigma = daily_returns.std()
    
    # 2. Apply user-defined multipliers
    # Convert annual drift percentage to daily decimal (Assuming 252 trading days)
    daily_drift = (drift_pct / 100) / 252
    adjusted_mu = mu + daily_drift
    adjusted_sigma = sigma * vol_mult
    
    # We will simulate 1 year forward (252 trading days)
    days_to_sim = 252
    
    # Initialize the simulation matrix (rows = days, columns = simulations)
    sim_returns = np.zeros((days_to_sim, n_sims))
    
    # 3. Generate Random Walks
    if method == "gaussian":
        # Standard normal distribution
        random_shocks = np.random.normal(0, 1, (days_to_sim, n_sims))
        sim_returns = adjusted_mu + (adjusted_sigma * random_shocks)
        
    elif method == "bootstrap":
        # Randomly sample historical returns
        historical_array = daily_returns.values
        for i in range(n_sims):
            # Pick random historical returns and apply multipliers
            sampled = np.random.choice(historical_array, size=days_to_sim, replace=True)
            # Center the sampled returns, then add the adjusted mean and multiply the variance
            centered = sampled - np.mean(sampled)
            sim_returns[:, i] = adjusted_mu + (centered * vol_mult)

    # 4. Convert returns to price paths
    # Start all simulations at the last known closing price
    last_price = px_close.iloc[-1]
    
    # Calculate cumulative returns
    price_paths = np.zeros_like(sim_returns)
    price_paths[0] = last_price * (1 + sim_returns[0])
    
    for t in range(1, days_to_sim):
        price_paths[t] = price_paths[t-1] * (1 + sim_returns[t])
        
    # 5. Extract Percentiles (Cross-sectional across all simulations for each day)
    p5 = np.percentile(price_paths, 5, axis=1)
    p50 = np.percentile(price_paths, 50, axis=1)
    p95 = np.percentile(price_paths, 95, axis=1)
    
    # Generate the step index for the X-axis
    steps = np.arange(1, days_to_sim + 1)
    
    return p5, p50, p95, steps

import streamlit as st
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import yfinance as yf
from datetime import date
import datetime
import re

# --- 1. CONFIG & UI SETUP ---
st.set_page_config(page_title="Monte Carlo & Seasonality", layout="wide")

with st.sidebar:
    st.header("📈 Data Settings")
    ticker = st.text_input("Ticker", value="BTC-USD")
    hist_period = st.selectbox("History", ["2y", "5y", "10y", "max"], index=1)
    years_back = st.slider("Lookback (Years)", 2, 15, 8)
    
    st.header("🔮 Simulation Params")
    n_sims = st.slider("Simulations", 1000, 20000, 5000)
    drift_bias = st.slider("Annual Drift %", -50.0, 50.0, 0.0)
    vol_mult = st.slider("Vol Multiplier", 0.5, 3.0, 1.0)
    mc_method = st.selectbox("Method", ["bootstrap", "gaussian"])
    use_seasonality = st.checkbox("Use Seasonality", value=True)

# --- 2. DATA ENGINE ---
# --- Updated fetch_data function ---
@st.cache_data(ttl=3600)
def fetch_data(symbol, period):
    try:
        # We explicitly set auto_adjust=True to ensure 'Close' is the adjusted price
        df = yf.download(symbol, period=period, progress=False, auto_adjust=True)
        
        if df.empty:
            return None
            
        # FIX: Handle MultiIndex columns (Ticker/Price levels)
        if isinstance(df.columns, pd.MultiIndex):
            # Flatten to just the Price levels (Open, High, Low, Close, etc.)
            df.columns = df.columns.get_level_values(0)
            
        return df.dropna()
    except Exception as e:
        st.error(f"Download Error: {e}")
        return None

# --- Updated Execution Block ---
px_data = fetch_data(ticker, hist_period)

if px_data is not None and 'Close' in px_data.columns:
    px = px_data['Close']
    # ... rest of your code
else:
    st.error(f"Could not find 'Close' data for {ticker}. Check if the symbol is correct.")
    
def get_weekly_log_returns(px):
    # Calculate log returns and group by ISO week
    log_rets = np.log(px / px.shift(1)).dropna()
    df = pd.DataFrame({"log_ret": log_rets.values}, index=log_rets.index)
    ic = df.index.isocalendar()
    df["year"], df["week"] = ic["year"].astype(int), ic["week"].astype(int)
    # Group to get weekly sum of log returns
    wk_log = df.groupby(["year", "week"])["log_ret"].sum()
    # Create Monday dates for the index
    idx = [pd.Timestamp(date.fromisocalendar(int(y), int(w), 1)) for (y, w) in wk_log.index]
    return pd.Series(wk_log.values, index=idx).sort_index()

# --- 3. MONTE CARLO ENGINE ---
def run_simulation(px, n_sims, drift_annual, vol_mult, method, use_seasonal):
    wk_logrets = get_weekly_log_returns(px)
    last_price = float(px.iloc[-1])
    
    # Calculate weeks remaining in the year
    today = pd.Timestamp.now()
    end_of_year = pd.Timestamp(year=today.year, month=12, day=31)
    weeks_to_sim = ((end_of_year - today).days // 7) + 1
    
    if weeks_to_sim <= 0: weeks_to_sim = 12 # Fallback if year is ending

    # Drift and Vol adjustments
    drift_weekly = np.log(1 + drift_annual/100) / 52
    
    # Seasonal Profile (Mean log return per ISO week)
    seasonal_profile = wk_logrets.groupby(wk_logrets.index.isocalendar().week).mean()
    
    # Generate Paths
    results = np.zeros((n_sims, weeks_to_sim))
    current_prices = np.full(n_sims, last_price)
    
    for t in range(weeks_to_sim):
        target_date = today + pd.Timedelta(weeks=t)
        wk_num = target_date.isocalendar().week
        
        # Base seasonal drift
        s_drift = seasonal_profile.get(wk_num, 0) if use_seasonal else 0
        
        if method == "bootstrap":
            shocks = np.random.choice(wk_logrets.values, size=n_sims)
            # Center and scale shocks
            shocks = (shocks - shocks.mean()) * vol_mult
        else:
            shocks = np.random.normal(0, wk_logrets.std() * vol_mult, size=n_sims)
            
        current_prices *= np.exp(s_drift + drift_weekly + shocks)
        results[:, t] = current_prices
        
    return results, weeks_to_sim

# --- 4. EXECUTION & RENDERING ---
st.title(f"📊 {ticker} Analysis")

px_data = fetch_data(ticker, hist_period)

if px_data is not None:
    px = px_data['Close']
    
    # Row 1: Seasonality Plots (Standard code from before)
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Weekly Seasonals")
        wk_rets = get_weekly_log_returns(px.tail(252 * years_back))
        fig1, ax1 = plt.subplots()
        df_box = pd.DataFrame({"ret": wk_rets.values, "wk": wk_rets.index.isocalendar().week})
        df_box.boxplot(column='ret', by='wk', ax=ax1, grid=False)
        st.pyplot(fig1)

    # Row 2: THE MONTE CARLO FAN CHART
    st.divider()
    st.subheader("🔮 Price Projection (Monte Carlo)")
    
    paths, steps = run_simulation(px, n_sims, drift_bias, vol_mult, mc_method, use_seasonality)
    
    # Calculate Percentiles for the Fan
    p5 = np.percentile(paths, 5, axis=0)
    p50 = np.percentile(paths, 50, axis=0)
    p95 = np.percentile(paths, 95, axis=0)
    
    # Create the Plot
    fig_mc, ax_mc = plt.subplots(figsize=(10, 5))
    x_axis = np.arange(1, steps + 1)
    
    ax_mc.plot(x_axis, p50, color='cyan', label='Median Path', lw=2)
    ax_mc.fill_between(x_axis, p5, p95, color='cyan', alpha=0.2, label='5th-95th Percentile')
    
    ax_mc.set_title(f"Simulated Price for {ticker} to Year-End")
    ax_mc.set_ylabel("Price")
    ax_mc.set_xlabel("Weeks from Today")
    ax_mc.legend()
    
    # CRITICAL: This renders the chart on your webpage
    st.pyplot(fig_mc)
    
    # Summary Table
    st.write("### Stats")
    summary = {
        "Current Price": f"${px.iloc[-1]:,.2f}",
        "Expected Year-End (Median)": f"${p50[-1]:,.2f}",
        "Implied Return": f"{((p50[-1]/px.iloc[-1])-1)*100:.2f}%"
    }
    st.table(pd.DataFrame([summary]))

else:
    st.error("Could not load data. Check ticker.")

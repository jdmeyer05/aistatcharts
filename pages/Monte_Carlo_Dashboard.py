import streamlit as st
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import os
from massive import RESTClient 
from datetime import date, datetime, timedelta

# --- 1. CONFIG & UI SETUP ---
st.set_page_config(page_title="Monte Carlo & Seasonality", layout="wide")

# Secure Authentication
api_key = os.environ.get("MASSIVE_API_KEY")

if not api_key:
    st.error("❌ Massive API Key not found. Please verify Google Cloud Environment Variables.")
    st.stop()

client = RESTClient(api_key)

with st.sidebar:
    st.header("📈 Data Settings")
    # Massive Tickers: 'X:BTC-USD' for Crypto, 'ERCOT.HB_WEST' for Power
    ticker = st.text_input("Massive Ticker", value="X:BTC-USD")
    lookback_days = st.slider("Lookback (Days)", 30, 730, 365)
    
    st.header("🔮 Simulation Params")
    n_sims = st.slider("Simulations", 1000, 10000, 5000)
    drift_bias = st.slider("Annual Drift %", -50.0, 50.0, 0.0)
    vol_mult = st.slider("Vol Multiplier", 0.5, 3.0, 1.0)
    mc_method = st.selectbox("Method", ["bootstrap", "gaussian"])
    use_seasonality = st.checkbox("Use Seasonality", value=True)

# --- 2. DATA ENGINE (MASSIVE) ---
@st.cache_data(ttl=3600)
def fetch_massive_data(symbol, days):
    try:
        end_date = date.today()
        start_date = end_date - timedelta(days=days)
        
        # Fetching Daily Aggregates
        aggs = client.list_aggs(
            ticker=symbol,
            multiplier=1,
            timespan="day",
            from_=start_date.strftime("%Y-%m-%d"),
            to=end_date.strftime("%Y-%m-%d"),
            limit=5000
        )
        
        if not aggs:
            return None
            
        df = pd.DataFrame(aggs)
        # Massive uses 'c' for Close and 't' for Unix Timestamp (ms)
        df['Date'] = pd.to_datetime(df['t'], unit='ms')
        df.set_index('Date', inplace=True)
        df.rename(columns={'c': 'Close'}, inplace=True)
        return df[['Close']].dropna()
    except Exception as e:
        st.error(f"Massive API Error: {e}")
        return None

def get_weekly_log_returns(px):
    log_rets = np.log(px / px.shift(1)).dropna()
    df = pd.DataFrame({"log_ret": log_rets.values}, index=log_rets.index)
    ic = df.index.isocalendar()
    df["year"], df["week"] = ic["year"].astype(int), ic["week"].astype(int)
    wk_log = df.groupby(["year", "week"])["log_ret"].sum()
    idx = [pd.Timestamp(date.fromisocalendar(int(y), int(w), 1)) for (y, w) in wk_log.index]
    return pd.Series(wk_log.values, index=idx).sort_index()

# --- 3. SIMULATION ENGINE ---
def run_simulation(px, n_sims, drift_annual, vol_mult, method, use_seasonal):
    wk_logrets = get_weekly_log_returns(px)
    last_price = float(px.iloc[-1])
    
    # Calculate weeks remaining in the current year (2026)
    today = pd.Timestamp.now()
    end_of_year = pd.Timestamp(year=today.year, month=12, day=31)
    weeks_to_sim = max(1, ((end_of_year - today).days // 7) + 1)
    
    drift_weekly = np.log(1 + drift_annual/100) / 52
    seasonal_profile = wk_logrets.groupby(wk_logrets.index.isocalendar().week).mean()
    
    results = np.zeros((n_sims, weeks_to_sim))
    current_prices = np.full(n_sims, last_price)
    
    for t in range(weeks_to_sim):
        target_date = today + pd.Timedelta(weeks=t)
        wk_num = target_date.isocalendar().week
        s_drift = seasonal_profile.get(wk_num, 0) if use_seasonal else 0
        
        if method == "bootstrap":
            shocks = np.random.choice(wk_logrets.values, size=n_sims)
            shocks = (shocks - shocks.mean()) * vol_mult
        else:
            shocks = np.random.normal(0, wk_logrets.std() * vol_mult, size=n_sims)
            
        current_prices *= np.exp(s_drift + drift_weekly + shocks)
        results[:, t] = current_prices
        
    return results, weeks_to_sim

# --- 4. EXECUTION & RENDERING ---
st.title(f"📊 {ticker} Seasonal Analysis")
st.markdown("Professional-grade simulations powered by **Massive** data.")

px_data = fetch_massive_data(ticker, lookback_days)

if px_data is not None and not px_data.empty:
    px = px_data['Close']
    
    # Seasonality Row
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Weekly Distribution")
        wk_rets = get_weekly_log_returns(px)
        fig_box, ax_box = plt.subplots(figsize=(10, 6))
        df_box = pd.DataFrame({"ret": wk_rets.values, "wk": wk_rets.index.isocalendar().week})
        df_box.boxplot(column='ret', by='wk', ax=ax_box, grid=False)
        ax_box.set_title("")
        st.pyplot(fig_box)
    
    with col2:
        st.subheader("Last 30 Days")
        st.line_chart(px.tail(30))

    # Monte Carlo Row
    st.divider()
    st.subheader("🔮 Price Projection to Year-End")
    
    paths, steps = run_simulation(px, n_sims, drift_bias, vol_mult, mc_method, use_seasonality)
    
    p5 = np.percentile(paths, 5, axis=0)
    p50 = np.percentile(paths, 50, axis=0)
    p95 = np.percentile(paths, 95, axis=0)
    
    fig_mc, ax_mc = plt.subplots(figsize=(12, 6))
    x_axis = np.arange(1, steps + 1)
    
    ax_mc.plot(x_axis, p50, color='#00d1ff', label='Median Forecast', lw=2)
    ax_mc.fill_between(x_axis, p5, p95, color='#00d1ff', alpha=0.15, label='90% Confidence Interval')
    
    ax_mc.set_title(f"Monte Carlo Projection: {ticker}")
    ax_mc.set_ylabel("Price (USD)")
    ax_mc.set_xlabel("Weeks from Today")
    ax_mc.grid(True, linestyle='--', alpha=0.5)
    ax_mc.legend()
    
    st.pyplot(fig_mc)

    # Metrics Table
    m1, m2, m3 = st.columns(3)
    m1.metric("Current Price", f"${px.iloc[-1]:,.2f}")
    m2.metric("Median Target", f"${p50[-1]:,.2f}")
    m3.metric("Exp. Return", f"{((p50[-1]/px.iloc[-1])-1)*100:.2f}%")

else:
    st.warning("Data not found. Verify your Massive ticker (e.g., 'X:BTC-USD' or 'ERCOT.HB_WEST').")

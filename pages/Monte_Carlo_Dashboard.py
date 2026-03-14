import streamlit as st
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import os
from massive import RESTClient 
from datetime import date, datetime, timedelta

# --- 1. CONFIG & UI SETUP ---
st.set_page_config(page_title="Monte Carlo & Seasonality", layout="wide")

api_key = os.environ.get("MASSIVE_API_KEY")
if not api_key:
    st.error("❌ Massive API Key not found. Please verify Google Cloud Environment Variables.")
    st.stop()

client = RESTClient(api_key)

with st.sidebar:
    st.header("📈 Data Settings")
    ticker = st.text_input("Massive Ticker", value="X:BTC-USD")
    lookback_days = st.slider("Lookback (Days)", 365, 1825, 1095) # Default 3y
    
    st.header("🔮 Simulation Params")
    n_sims = st.slider("Simulations", 1000, 10000, 5000)
    drift_bias = st.slider("Annual Drift %", -50.0, 50.0, 0.0)
    vol_mult = st.slider("Vol Multiplier", 0.5, 3.0, 1.0)
    mc_method = st.selectbox("Method", ["bootstrap", "gaussian"])
    use_seasonality = st.checkbox("Use Seasonality", value=True)

# --- 2. DATA & MATH ENGINES ---
@st.cache_data(ttl=3600)
def fetch_massive_data(symbol, days):
    try:
        end_date = date.today()
        start_date = end_date - timedelta(days=days)
        
        # list_aggs returns a generator of 'Agg' objects
        aggs = client.list_aggs(
            ticker=symbol,
            multiplier=1,
            timespan="day",
            from_=start_date.strftime("%Y-%m-%d"),
            to=end_date.strftime("%Y-%m-%d"),
            limit=5000
        )
        
        # Convert the list of objects into a DataFrame
        df = pd.DataFrame(aggs)

        if df.empty:
            st.error(f"No data returned for {symbol}.")
            return None
            
        # FIX: Massive Objects use full names: 'timestamp' and 'close'
        if 'timestamp' in df.columns and 'close' in df.columns:
            # Convert Unix ms to datetime
            df['Date'] = pd.to_datetime(df['timestamp'], unit='ms')
            df.set_index('Date', inplace=True)
            
            # Rename 'close' to 'Close' to match the rest of your math logic
            df.rename(columns={'close': 'Close'}, inplace=True)
            
            # Return only the Close column
            return df[['Close']].dropna()
        else:
            st.error(f"Mapping error. Found columns: {df.columns.tolist()}")
            return None

    except Exception as e:
        st.error(f"Massive API Error: {e}")
        return None

def get_returns(px, freq='W'):
    log_rets = np.log(px / px.shift(1)).dropna()
    df = pd.DataFrame({"log_ret": log_rets.values}, index=log_rets.index)
    if freq == 'W':
        ic = df.index.isocalendar()
        df["group"] = ic["week"].astype(int)
        df["year"] = ic["year"].astype(int)
        grouped = df.groupby(["year", "group"])["log_ret"].sum()
        idx = [pd.Timestamp(date.fromisocalendar(int(y), int(w), 1)) for (y, w) in grouped.index]
    else: # Monthly
        df["group"] = df.index.month
        df["year"] = df.index.year
        grouped = df.groupby(["year", "group"])["log_ret"].sum()
        idx = [pd.Timestamp(year=int(y), month=int(m), day=1) for (y, m) in grouped.index]
    return pd.Series(grouped.values, index=idx).sort_index()

# --- 3. RENDERING SECTION ---
st.title(f"📊 {ticker} Advanced Analytics")

px_data = fetch_massive_data(ticker, lookback_days)

if px_data is not None and not px_data.empty:
    px = px_data['Close']
    
    # --- CHART 1 & 2: SEASONALITY BOXPLOTS ---
    st.subheader("1. Return Distributions (Seasonality)")
    col1, col2 = st.columns(2)
    
    with col1:
        st.write("**Weekly Log Returns by ISO Week**")
        wk_rets = get_returns(px, 'W')
        fig_wk, ax_wk = plt.subplots()
        df_wk = pd.DataFrame({"ret": wk_rets.values, "wk": wk_rets.index.isocalendar().week})
        df_wk.boxplot(column='ret', by='wk', ax=ax_wk, grid=False, showfliers=False)
        plt.suptitle(""); ax_wk.set_title("")
        st.pyplot(fig_wk)

    with col2:
        st.write("**Monthly Log Returns by Month**")
        mo_rets = get_returns(px, 'M')
        fig_mo, ax_mo = plt.subplots()
        df_mo = pd.DataFrame({"ret": mo_rets.values, "mo": mo_rets.index.month})
        df_mo.boxplot(column='ret', by='mo', ax=ax_mo, grid=False, showfliers=False)
        plt.suptitle(""); ax_mo.set_title("")
        st.pyplot(fig_mo)

    # --- CHART 3: YTD OVERLAY ---
    st.divider()
    st.subheader("2. Yearly YTD Performance Overlay")
    fig_ytd, ax_ytd = plt.subplots(figsize=(12, 5))
    years = sorted(px.index.year.unique())
    for y in years:
        yr_data = px[px.index.year == y]
        ytd = (yr_data / yr_data.iloc[0]) - 1.0
        alpha = 1.0 if y == years[-1] else 0.3
        lw = 2.5 if y == years[-1] else 1.0
        ax_ytd.plot(range(len(ytd)), ytd.values, label=str(y), alpha=alpha, lw=lw)
    ax_ytd.set_title("Relative Return Since Jan 1")
    ax_ytd.legend(ncol=4, fontsize='small')
    st.pyplot(fig_ytd)

    # --- CHART 4: MONTE CARLO ---
    st.divider()
    st.subheader("3. Monte Carlo Year-End Projection")
    
    # Simulation Logic
    today = pd.Timestamp.now()
    weeks_to_sim = max(1, ((pd.Timestamp(year=today.year, month=12, day=31) - today).days // 7) + 1)
    wk_logrets = get_returns(px, 'W')
    seasonal_profile = wk_logrets.groupby(wk_logrets.index.isocalendar().week).mean()
    
    paths = np.zeros((n_sims, weeks_to_sim))
    current_prices = np.full(n_sims, float(px.iloc[-1]))
    drift_weekly = np.log(1 + drift_bias/100) / 52

    for t in range(weeks_to_sim):
        wk_num = (today + pd.Timedelta(weeks=t)).isocalendar().week
        s_drift = seasonal_profile.get(wk_num, 0) if use_seasonality else 0
        if mc_method == "bootstrap":
            shocks = (np.random.choice(wk_logrets.values, size=n_sims) - wk_logrets.mean()) * vol_mult
        else:
            shocks = np.random.normal(0, wk_logrets.std() * vol_mult, size=n_sims)
        current_prices *= np.exp(s_drift + drift_weekly + shocks)
        paths[:, t] = current_prices

    # Plotting Fan
    fig_mc, ax_mc = plt.subplots(figsize=(12, 5))
    p5, p50, p95 = np.percentile(paths, [5, 50, 95], axis=0)
    ax_mc.plot(range(1, weeks_to_sim + 1), p50, color='cyan', label='Median', lw=2)
    ax_mc.fill_between(range(1, weeks_to_sim + 1), p5, p95, color='cyan', alpha=0.15, label='90% CI')
    ax_mc.set_title(f"Simulation for {ticker}")
    ax_mc.legend()
    st.pyplot(fig_mc)

    st.metric("Expected Year-End Price", f"${p50[-1]:,.2f}", f"{((p50[-1]/px.iloc[-1])-1)*100:.2f}%")

else:
    st.warning("No data found. Check ticker format.")

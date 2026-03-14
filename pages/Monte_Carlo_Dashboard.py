import streamlit as st
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import os
from massive import RESTClient 
from datetime import date, timedelta

# --- 1. CONFIG & UI SETUP ---
st.set_page_config(page_title="Monte Carlo & Seasonality", layout="wide")

api_key = os.environ.get("MASSIVE_API_KEY")
if not api_key:
    st.error("❌ Massive API Key not found. Please verify Google Cloud Environment Variables.")
    st.stop()

client = RESTClient(api_key)

# SMART TICKER PARSER
def format_massive_ticker(user_input: str) -> str:
    t = user_input.strip().upper()
    
    # If the user already included a prefix (like X: or ERCOT.), trust them
    if ":" in t or t.startswith("ERCOT."):
        return t
        
    # Crypto detection (e.g., BTC-USD -> X:BTC-USD)
    if "-USD" in t:
        return f"X:{t}"
        
    # ERCOT Hub/Zone detection (e.g., HB_WEST -> ERCOT.HB_WEST)
    if t.startswith("HB_") or t.startswith("LZ_"):
        return f"ERCOT.{t}"
        
    # Default assumption: Standard US Equity (e.g., AAPL)
    return t

with st.sidebar:
    st.header("📈 Data Settings")
    raw_ticker = st.text_input("Ticker (e.g. BTC-USD, AAPL, HB_WEST)", value="BTC-USD")
    lookback_days = st.slider("Lookback (Days)", 365, 1825, 1095)
    
    st.header("🔮 Simulation Params")
    n_sims = st.slider("Simulations", 1000, 10000, 5000)
    drift_bias = st.slider("Annual Drift %", -50.0, 50.0, 0.0)
    vol_mult = st.slider("Vol Multiplier", 0.5, 3.0, 1.0)
    mc_method = st.selectbox("Method", ["bootstrap", "gaussian"])
    use_seasonality = st.checkbox("Use Seasonality", value=True)

# Apply the smart parser
formatted_ticker = format_massive_ticker(raw_ticker)

# --- 2. DATA & MATH ENGINES ---
@st.cache_data(ttl=3600)
def fetch_massive_data(symbol, days):
    try:
        end_date = date.today()
        start_date = end_date - timedelta(days=days)
        aggs = client.list_aggs(ticker=symbol, multiplier=1, timespan="day", 
                                from_=start_date.strftime("%Y-%m-%d"), 
                                to=end_date.strftime("%Y-%m-%d"), limit=5000)
        
        df = pd.DataFrame(aggs)
        if df.empty: return None
            
        if 'timestamp' in df.columns and 'close' in df.columns:
            df['Date'] = pd.to_datetime(df['timestamp'], unit='ms')
            df.set_index('Date', inplace=True)
            df.rename(columns={'close': 'Close'}, inplace=True)
            return df[['Close']].dropna()
        return None
    except Exception as e:
        st.error(f"API Error: {e}"); return None

def get_returns(px, freq='W'):
    log_rets = np.log(px / px.shift(1)).dropna()
    df = pd.DataFrame({"log_ret": log_rets.values}, index=log_rets.index)
    if freq == 'W':
        ic = df.index.isocalendar()
        df["group"], df["year"] = ic["week"].astype(int), ic["year"].astype(int)
        grouped = df.groupby(["year", "group"])["log_ret"].sum()
        idx = [pd.Timestamp(date.fromisocalendar(int(y), int(w), 1)) for (y, w) in grouped.index]
    else:
        df["group"], df["year"] = df.index.month, df.index.year
        grouped = df.groupby(["year", "group"])["log_ret"].sum()
        idx = [pd.Timestamp(year=int(y), month=int(m), day=1) for (y, m) in grouped.index]
    return pd.Series(grouped.values, index=idx).sort_index()

# --- 3. RENDERING SECTION ---
st.title(f"📊 {raw_ticker} Advanced Analytics")
if raw_ticker != formatted_ticker:
    st.caption(f"*(Querying database as: {formatted_ticker})*")

px_data = fetch_massive_data(formatted_ticker, lookback_days)

if px_data is not None and not px_data.empty:
    px = px_data['Close']
    
    st.subheader("1. Return Distributions (Seasonality)")
    col1, col2 = st.columns(2)
    
    with col1:
        st.write("**Weekly Log Returns**")
        wk_rets = get_returns(px, 'W')
        # REDUCED SIZE: from default to (6, 3)
        fig_wk, ax_wk = plt.subplots(figsize=(6, 3))
        df_wk = pd.DataFrame({"ret": wk_rets.values, "wk": wk_rets.index.isocalendar().week})
        df_wk.boxplot(column='ret', by='wk', ax=ax_wk, grid=False, showfliers=False)
        plt.suptitle(""); ax_wk.set_title("")
        st.pyplot(fig_wk)

    with col2:
        st.write("**Monthly Log Returns**")
        mo_rets = get_returns(px, 'M')
        # REDUCED SIZE: from default to (6, 3)
        fig_mo, ax_mo = plt.subplots(figsize=(6, 3))
        df_mo = pd.DataFrame({"ret": mo_rets.values, "mo": mo_rets.index.month})
        df_mo.boxplot(column='ret', by='mo', ax=ax_mo, grid=False, showfliers=False)
        plt.suptitle(""); ax_mo.set_title("")
        st.pyplot(fig_mo)

    st.divider()
    st.subheader("2. Yearly YTD Performance Overlay")
    # REDUCED SIZE: from (12, 5) to (10, 4)
    fig_ytd, ax_ytd = plt.subplots(figsize=(10, 4))
    years = sorted(px.index.year.unique())
    for y in years:
        yr_data = px[px.index.year == y]
        ytd = (yr_data / yr_data.iloc[0]) - 1.0
        alpha, lw = (1.0, 2.5) if y == years[-1] else (0.3, 1.0)
        ax_ytd.plot(range(len(ytd)), ytd.values, label=str(y), alpha=alpha, lw=lw)
    ax_ytd.set_title("Relative Return Since Jan 1")
    ax_ytd.legend(ncol=4, fontsize='small')
    st.pyplot(fig_ytd)

    st.divider()
    st.subheader("3. Monte Carlo Year-End Projection")
    
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

    # REDUCED SIZE: from (12, 6) to (10, 4)
    fig_mc, ax_mc = plt.subplots(figsize=(10, 4))
    p5, p50, p95 = np.percentile(paths, [5, 50, 95], axis=0)
    ax_mc.plot(range(1, weeks_to_sim + 1), p50, color='cyan', label='Median', lw=2)
    ax_mc.fill_between(range(1, weeks_to_sim + 1), p5, p95, color='cyan', alpha=0.15, label='90% CI')
    ax_mc.set_title(f"Simulation for {raw_ticker}")
    ax_mc.legend()
    st.pyplot(fig_mc)

    st.metric("Expected Year-End Price", f"${p50[-1]:,.2f}", f"{((p50[-1]/px.iloc[-1])-1)*100:.2f}%")

else:
    st.warning(f"No data found for {raw_ticker}. Try standard formats like BTC-USD, AAPL, or HB_WEST.")

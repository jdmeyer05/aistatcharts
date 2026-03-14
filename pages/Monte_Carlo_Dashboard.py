import streamlit as st
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px_plot
import os
from massive import RESTClient 
from datetime import date, timedelta

# --- 1. CONFIG & UI SETUP ---
st.set_page_config(page_title="Monte Carlo & Seasonality", layout="wide")

# Premium SaaS Polish: Hide Streamlit branding
st.markdown("""
    <style>
        #MainMenu {visibility: hidden;}
        footer {visibility: hidden;}
        header {visibility: hidden;}
    </style>
""", unsafe_allow_html=True)

api_key = os.environ.get("MASSIVE_API_KEY")
if not api_key:
    st.error("❌ Massive API Key not found. Please verify Google Cloud Environment Variables.")
    st.stop()

client = RESTClient(api_key)

# SMART TICKER PARSER
def format_massive_ticker(user_input: str) -> str:
    t = user_input.strip().upper()
    if ":" in t or t.startswith("ERCOT."): return t
    if "-USD" in t: return f"X:{t}"
    if t.startswith("HB_") or t.startswith("LZ_"): return f"ERCOT.{t}"
    return t

with st.sidebar:
    st.header("📈 Data Settings")
    raw_ticker = st.text_input("Ticker (e.g. BTC-USD, AAPL, HB_WEST)", value="BTC-USD")
    lookback_days = st.slider("Lookback (Days)", 365, 1825, 1095)
    
    st.header("🔮 Simulation Params")
    n_sims = st.slider("Simulations", 1000, 20000, 5000)
    drift_bias = st.slider("Annual Drift %", -50.0, 50.0, 0.0)
    vol_mult = st.slider("Vol Multiplier", 0.5, 3.0, 1.0)
    mc_method = st.selectbox("Method", ["bootstrap", "gaussian"])
    use_seasonality = st.checkbox("Use Seasonality", value=True)

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
        wk_rets = get_returns(px, 'W')
        df_wk = pd.DataFrame({"ret": wk_rets.values, "wk": wk_rets.index.isocalendar().week})
        fig_wk = px_plot.box(df_wk, x='wk', y='ret', color_discrete_sequence=['#00d1ff'])
        fig_wk.update_layout(title="Weekly Log Returns", xaxis_title="ISO Week", yaxis_title="Log Return", margin=dict(l=0, r=0, t=30, b=0), template="plotly_dark", plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig_wk, use_container_width=True)

    with col2:
        mo_rets = get_returns(px, 'M')
        df_mo = pd.DataFrame({"ret": mo_rets.values, "mo": mo_rets.index.month})
        fig_mo = px_plot.box(df_mo, x='mo', y='ret', color_discrete_sequence=['#00d1ff'])
        fig_mo.update_layout(title="Monthly Log Returns", xaxis_title="Month", yaxis_title="Log Return", margin=dict(l=0, r=0, t=30, b=0), template="plotly_dark", plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig_mo, use_container_width=True)

    st.divider()
    st.subheader("2. Yearly YTD Performance Overlay")
    
    fig_ytd = go.Figure()
    years = sorted(px.index.year.unique())
    for y in years:
        yr_data = px[px.index.year == y]
        ytd = (yr_data / yr_data.iloc[0]) - 1.0
        is_current = (y == years[-1])
        
        fig_ytd.add_trace(go.Scatter(
            x=list(range(len(ytd))), y=ytd.values, mode='lines',
            line=dict(color='#00d1ff' if is_current else 'gray', width=3 if is_current else 1),
            opacity=1.0 if is_current else 0.3,
            name=str(y), hoverinfo='skip' if not is_current else 'all',
            hovertemplate='Day %{x}: %{y:.2%}<extra></extra>' if is_current else None
        ))
    
    fig_ytd.update_layout(xaxis_title="Trading Days Since Jan 1", yaxis_title="Relative Return", hovermode="x unified", margin=dict(l=0, r=0, t=20, b=0), template="plotly_dark", plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)")
    st.plotly_chart(fig_ytd, use_container_width=True)

    st.divider()
    st.subheader("3. Monte Carlo Year-End Projection")
    
    # --- VECTORIZED MONTE CARLO ENGINE ---
    today = pd.Timestamp.now()
    weeks_to_sim = max(1, ((pd.Timestamp(year=today.year, month=12, day=31) - today).days // 7) + 1)
    
    wk_logrets = get_returns(px, 'W')
    seasonal_profile = wk_logrets.groupby(wk_logrets.index.isocalendar().week).mean()
    
    # Pre-calculate arrays for vectorization
    target_weeks = [(today + pd.Timedelta(weeks=t)).isocalendar().week for t in range(weeks_to_sim)]
    s_drifts = np.array([seasonal_profile.get(w, 0) if use_seasonality else 0 for w in target_weeks])
    drift_weekly = np.log(1 + drift_bias/100) / 52
    
    # Generate random shocks matrix all at once
    if mc_method == "bootstrap":
        shocks = np.random.choice(wk_logrets.values, size=(n_sims, weeks_to_sim))
        shocks = (shocks - wk_logrets.mean()) * vol_mult
    else:
        shocks = np.random.normal(0, wk_logrets.std() * vol_mult, size=(n_sims, weeks_to_sim))
        
    # Calculate cumulative paths instantly
    total_step_returns = s_drifts + drift_weekly + shocks
    paths = float(px.iloc[-1]) * np.exp(total_step_returns).cumprod(axis=1)

    # Calculate percentiles
    p5, p50, p95 = np.percentile(paths, [5, 50, 95], axis=0)
    x_axis = list(range(1, weeks_to_sim + 1))

    # --- PREMIUM PLOTLY FAN CHART ---
    fig_mc = go.Figure()

    # 90% Confidence Interval
    fig_mc.add_trace(go.Scatter(
        x=x_axis + x_axis[::-1], y=list(p95) + list(p5)[::-1], 
        fill='toself', fillcolor='rgba(0, 209, 255, 0.1)', line=dict(color='rgba(255,255,255,0)'),
        hoverinfo="skip", name='90% Confidence Interval'
    ))

    # Glow effect
    fig_mc.add_trace(go.Scatter(
        x=x_axis, y=p50, mode='lines', line=dict(color='rgba(0, 209, 255, 0.3)', width=8),
        hoverinfo="skip", showlegend=False
    ))

    # Median Line
    fig_mc.add_trace(go.Scatter(
        x=x_axis, y=p50, mode='lines', line=dict(color='#00d1ff', width=2),
        name='Median Forecast', hovertemplate='<b>Week %{x}</b><br>Target: $%{y:,.2f}<extra></extra>'
    ))

    fig_mc.update_layout(
        template="plotly_dark", plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(title="Weeks from Today", showgrid=False, showline=True, linecolor='rgba(255,255,255,0.2)'),
        yaxis=dict(title="Price (USD)", showgrid=True, gridcolor='rgba(255,255,255,0.05)', zeroline=False, tickprefix="$"),
        hovermode="x unified", margin=dict(l=20, r=20, t=20, b=20),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )

    st.plotly_chart(fig_mc, use_container_width=True)
    st.metric("Expected Year-End Price", f"${p50[-1]:,.2f}", f"{((p50[-1]/px.iloc[-1])-1)*100:.2f}%")

else:
    st.warning(f"No data found for {raw_ticker}. Try standard formats like BTC-USD, AAPL, or HB_WEST.")

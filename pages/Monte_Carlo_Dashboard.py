import streamlit as st
import plotly.graph_objects as go
import pandas as pd
import numpy as np
from src.data_engine import fetch_massive_data, format_massive_ticker, render_data_source_footer
from src.simulation import simulate_to_year_end_weekly, simulate_30d_tactical
from src.chatbot import run_sidebar_chatbot

st.title("📉 Multi-Timeframe Monte Carlo")

with st.sidebar:
    st.header("Simulation Settings")
    with st.form("mc_settings"):
        raw_ticker = st.text_input("Ticker", value="BTC-USD")
        n_sims = st.slider("Simulations", 1000, 100000, 10000, step=1000)
        lookback = st.slider("Lookback (Days)", 60, 2000, 365, step=5)
        method = st.selectbox("MC Method", ["bootstrap", "gaussian"])
        drift = st.slider("Drift Bias (Annual %)", -50.0, 50.0, 0.0, step=0.5)
        vol_mult = st.slider("Vol Multiplier", 0.2, 3.0, 1.0, step=0.05)
        seed = st.number_input("Random Seed", value=42)
        submit_button = st.form_submit_button(label="🚀 Run Simulations")

ticker = format_massive_ticker(raw_ticker)
# Fetch 5 years to ensure we have enough data for a 2-year plot + lookback
data = fetch_massive_data(ticker, 1825)

if data is not None and not data.empty:
    px_close = data['Close'].astype(float).squeeze()
    last_price = float(px_close.iloc[-1])
    
    # --- 1. 30-DAY TACTICAL CHART (Original Daily Model) ---
    st.subheader("📅 30-Day Tactical Projection (Daily Steps)")
    
    paths_30d, dates_30d = simulate_30d_tactical(
        px_close=px_close, n_sims=n_sims, lookback_days=lookback,
        method=method, drift_bias_annual_pct=drift, vol_mult=vol_mult, seed=seed
    )

    if paths_30d.size > 0:
        p5_30, p50_30, p95_30 = np.percentile(paths_30d, [5, 50, 95], axis=0)
        
        # SLICE: EXACTLY 2 YEARS BACK FOR HISTORICAL PLOT
        start_2y = px_close.index[-1] - pd.DateOffset(years=2)
        hist_2y = px_close.loc[px_close.index >= start_2y]

        fig_30 = go.Figure()
        fig_30.add_trace(go.Scatter(x=hist_2y.index, y=hist_2y.values, name="2-Year History", line=dict(color='#00d1ff', width=1.5)))
        fig_30.add_trace(go.Scatter(
            x=dates_30d.tolist() + dates_30d.tolist()[::-1],
            y=p95_30.tolist() + p5_30.tolist()[::-1],
            fill='toself', fillcolor='rgba(255, 170, 0, 0.15)', line=dict(color='rgba(255,255,255,0)'), name='5%-95% Range'
        ))
        fig_30.add_trace(go.Scatter(x=dates_30d, y=p50_30, name="Median Path", line=dict(color='#ffaa00', width=2.5, dash='dot')))

        fig_30.update_layout(template="plotly_dark", height=450, hovermode='x unified', margin=dict(t=20, b=0))
        st.plotly_chart(fig_30, use_container_width=True)
        
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Current Price", f"${last_price:,.2f}")
        c2.metric("30-Day Target", f"${p50_30[-1]:,.2f}")
        c3.metric("Exp. Return", f"{((p50_30[-1]/last_price)-1)*100:.2f}%")
        c4.metric("Risk (P5)", f"${p5_30[-1]:,.2f}")

    st.divider()

    # --- 2. YEAR-END STRATEGIC CHART (Weekly Seasonal Model) ---
    st.subheader("🎯 Year-End Strategic Projection (Weekly Steps)")
    
    paths_ye, dates_ye = simulate_to_year_end_weekly(
        px_close=px_close, n_sims=n_sims, lookback_days=lookback,
        method=method, drift_bias_annual_pct=drift, vol_mult=vol_mult, seed=seed
    )

    if paths_ye.size > 0:
        p5_ye, p50_ye, p95_ye = np.percentile(paths_ye, [5, 50, 95], axis=0)
        
        # Scale history to 6 months for strategic focus
        hist_6m = px_close.tail(180)

        fig_ye = go.Figure()
        fig_ye.add_trace(go.Scatter(x=hist_6m.index, y=hist_6m.values, name="Recent History", line=dict(color='#00d1ff', width=1.5)))
        fig_ye.add_trace(go.Scatter(
            x=dates_ye.tolist() + dates_ye.tolist()[::-1],
            y=p95_ye.tolist() + p5_ye.tolist()[::-1],
            fill='toself', fillcolor='rgba(0, 255, 150, 0.1)', line=dict(color='rgba(255,255,255,0)'), name='Confidence Band'
        ))
        fig_ye.add_trace(go.Scatter(x=dates_ye, y=p50_ye, name="Year-End Median", line=dict(color='#00ff96', width=2.5, dash='dot')))

        fig_ye.update_layout(template="plotly_dark", height=400, hovermode='x unified', margin=dict(t=20, b=0))
        st.plotly_chart(fig_ye, use_container_width=True)
        
        # AI Chatbot Context
        ctx = f"Ticker: {ticker}. 30-Day Forecast: ${p50_30[-1]:,.2f}. Year-End Forecast: ${p50_ye[-1]:,.2f}."
        run_sidebar_chatbot(ctx)

    render_data_source_footer()
else:
    st.warning("No data found to run the simulation.")

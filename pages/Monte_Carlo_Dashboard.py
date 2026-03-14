import streamlit as st
# Must be the first Streamlit command to ensure enough width for side-by-side charts
st.set_page_config(page_title="Multi-Timeframe Forecasts", layout="wide")

import plotly.graph_objects as go
import pandas as pd
import numpy as np
from src.data_engine import fetch_massive_data, format_massive_ticker, render_data_source_footer
from src.simulation import simulate_to_year_end_weekly, predict_30d_random_forest
from src.chatbot import run_sidebar_chatbot

st.title("📉 Multi-Timeframe Forecasts")
st.markdown("Combines a **Deep Random Forest (Tactical)** with a **Seasonal Monte Carlo (Strategic)**.")

with st.sidebar:
    st.header("Model Settings")
    with st.form("mc_settings"):
        raw_ticker = st.text_input("Ticker", value="BTC-USD")
        st.info("Random Forest trained on 1000 days of technical features.")
        n_trees = st.slider("RF Trees", 50, 500, 200, step=50)
        
        st.divider()
        st.caption("Strategic Settings (Year-End)")
        n_sims = st.slider("Simulations", 1000, 100000, 10000, step=1000)
        drift = st.slider("Drift Bias (Annual %)", -50.0, 50.0, 0.0, step=0.5)
        vol_mult = st.slider("Vol Multiplier", 0.2, 3.0, 1.0, step=0.05)
        seed = st.number_input("Random Seed", value=42)
        submit_button = st.form_submit_button(label="🚀 Generate Forecasts")

ticker = format_massive_ticker(raw_ticker)
# Fetch 5 years of data for deep seasonality
data = fetch_massive_data(ticker, 1825)

if data is not None and not data.empty:
    px_close = data['Close'].astype(float).squeeze()
    last_price = float(px_close.iloc[-1])
    
    # Create the side-by-side layout
    col_left, col_right = st.columns(2)
    
    # --- LEFT COLUMN: 30-DAY TACTICAL CHART ---
    with col_left:
        st.subheader("🤖 30-Day Tactical Projection")
        st.caption("Deep Random Forest Engine")
        
        rf_results, dates_30d = predict_30d_random_forest(
            px_close=px_close, 
            n_estimators=n_trees
        )

        if len(dates_30d) > 0:
            p50_30 = rf_results['mean']
            p5_30 = rf_results['lower']
            p95_30 = rf_results['upper']
            
            # SLICE: EXACTLY 2 YEARS BACK FOR HISTORICAL PLOT
            start_2y = px_close.index[-1] - pd.DateOffset(years=2)
            hist_2y = px_close.loc[px_close.index >= start_2y]

            fig_30 = go.Figure()
            fig_30.add_trace(go.Scatter(x=hist_2y.index, y=hist_2y.values, name="2-Year History", line=dict(color='#00d1ff', width=1.5)))
            
            # Uncertainty Band
            fig_30.add_trace(go.Scatter(
                x=dates_30d.tolist() + dates_30d.tolist()[::-1],
                y=p95_30.tolist() + p5_30.tolist()[::-1],
                fill='toself', fillcolor='rgba(173, 127, 255, 0.15)', line=dict(color='rgba(255,255,255,0)'), name='RF Uncertainty (95%)'
            ))
            fig_30.add_trace(go.Scatter(x=dates_30d, y=p50_30, name="RF Mean Prediction", line=dict(color='#ad7fff', width=2.5, dash='dot')))

            # Hardcoded height to prevent resizing
            fig_30.update_layout(template="plotly_dark", height=350, hovermode='x unified', margin=dict(t=20, b=0, l=0, r=0))
            st.plotly_chart(fig_30, use_container_width=True)
            
            # 2x2 Metric Grid to fit the column width gracefully
            m1, m2 = st.columns(2)
            m1.metric("Current Price", f"${last_price:,.2f}")
            m2.metric("30-Day RF Target", f"${p50_30[-1]:,.2f}")
            
            m3, m4 = st.columns(2)
            m3.metric("AI Predicted Move", f"{((p50_30[-1]/last_price)-1)*100:.2f}%")
            m4.metric("Lower Bound (P5)", f"${p5_30[-1]:,.2f}")

    # --- RIGHT COLUMN: YEAR-END STRATEGIC CHART ---
    with col_right:
        st.subheader("🎯 Year-End Strategic Projection")
        st.caption("Robust Seasonal Monte Carlo")
        
        # Using 3 years (1095 days) for lookback
        paths_ye, dates_ye = simulate_to_year_end_weekly(
            px_close=px_close, n_sims=n_sims, lookback_days=1095,
            method="bootstrap", drift_bias_annual_pct=drift, vol_mult=vol_mult, seed=seed
        )

        if paths_ye.size > 0:
            p5_ye, p50_ye, p95_ye = np.percentile(paths_ye, [5, 50, 95], axis=0)
            
            hist_6m = px_close.tail(180)

            fig_ye = go.Figure()
            fig_ye.add_trace(go.Scatter(x=hist_6m.index, y=hist_6m.values, name="Recent History", line=dict(color='#00d1ff', width=1.5)))
            fig_ye.add_trace(go.Scatter(
                x=dates_ye.tolist() + dates_ye.tolist()[::-1],
                y=p95_ye.tolist() + p5_ye.tolist()[::-1],
                fill='toself', fillcolor='rgba(0, 255, 150, 0.1)', line=dict(color='rgba(255,255,255,0)'), name='Seasonal Confidence'
            ))
            fig_ye.add_trace(go.Scatter(x=dates_ye, y=p50_ye, name="Year-End Median", line=dict(color='#00ff96', width=2.5, dash='dot')))

            # Hardcoded height to prevent resizing
            fig_ye.update_layout(template="plotly_dark", height=350, hovermode='x unified', margin=dict(t=20, b=0, l=0, r=0))
            st.plotly_chart(fig_ye, use_container_width=True)
            
            # 2x2 Metric Grid to fit the column width gracefully
            m1, m2 = st.columns(2)
            m1.metric("Year-End Target", f"${p50_ye[-1]:,.2f}")
            m2.metric("Exp. Return", f"{((p50_ye[-1]/last_price)-1)*100:.2f}%")
            
            m3, m4 = st.columns(2)
            m3.metric("Bull Case (P95)", f"${p95_ye[-1]:,.2f}")
            m4.metric("Bear Case (P5)", f"${p5_ye[-1]:,.2f}")
            
            # AI Chatbot Context 
            ctx = f"Ticker: {ticker}. 30-Day RF Forecast: ${p50_30[-1]:,.2f}. Year-End Seasonal Forecast: ${p50_ye[-1]:,.2f}."
            run_sidebar_chatbot(ctx)

    render_data_source_footer()
else:
    st.warning("No data found to run the forecasts.")

import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
from src.data_engine import fetch_massive_data, format_massive_ticker
from src.simulation import run_monte_carlo_engine
from src.chatbot import run_sidebar_chatbot
import logging
import pandas as pd
import numpy as np

# Setup logging
logger = logging.getLogger(__name__)

# NO set_page_config here (it is already in app.py)

st.title("📈 Monte Carlo Simulation")

# --- Sidebar Controls ---
with st.sidebar:
    st.header("Settings")
    raw_ticker = st.text_input("Ticker", value="BTC-USD")
    lookback = st.slider("Lookback (Days)", 365, 1825, 1095)
    n_sims = st.slider("Simulations", 1000, 20000, 5000)
    drift = st.slider("Annual Drift %", -50.0, 50.0, 0.0)
    vol_mult = st.slider("Vol Multiplier", 0.5, 3.0, 1.0)
    method = st.selectbox("Method", ["bootstrap", "gaussian"])
    seasonal = st.checkbox("Use Seasonality", value=True)

# --- Logic & Rendering ---
try:
    ticker = format_massive_ticker(raw_ticker)
    data = fetch_massive_data(ticker, lookback)

    if data is not None and not data.empty:
        # Validate data structure
        if 'Close' not in data.columns:
            st.error("❌ Error: Data does not contain 'Close' column. Check data source.")
        else:
            # THE FIX: .squeeze() forces the (1093, 1) matrix into a flat 1D line
            px_close = data['Close'].astype(float).squeeze()
            
            # Clean data (remove NaNs)
            px_close = px_close.dropna()
            
            # Clean data (remove NaNs)
            px_close = px_close.dropna()
            
            if len(px_close) < 10:
                st.error(f"❌ Error: Not enough valid data points ({len(px_close)}).")
            else:
                # Run the Simulation
                p5, p50, p95, steps = run_monte_carlo_engine(
                    px_close, n_sims, drift, vol_mult, method, seasonal
                )
                
                # --- Main Plotting Logic ---
                fig = go.Figure()
                
                # Historical Data
                fig.add_trace(go.Scatter(
                    x=px_close.index, y=px_close.values,
                    name="Historical",
                    line=dict(color='#00d1ff', width=2)
                ))
                
                # Projection Dates
                last_date = px_close.index[-1]
                future_dates = pd.date_range(start=last_date, periods=len(p50), freq='D')
                
                # Confidence Interval (Shaded Area)
                fig.add_trace(go.Scatter(
                    x=future_dates.tolist() + future_dates.tolist()[::-1],
                    y=p95.tolist() + p5.tolist()[::-1],
                    fill='toself',
                    fillcolor='rgba(255, 170, 0, 0.1)',
                    line=dict(color='rgba(255,255,255,0)'),
                    name='90% Confidence Interval'
                ))

                # Median Forecast
                fig.add_trace(go.Scatter(
                    x=future_dates, y=p50,
                    name="Median Forecast",
                    line=dict(color='#ffaa00', width=3)
                ))

                fig.update_layout(
                    template="plotly_dark",
                    title=f"Projection for {ticker}",
                    xaxis_title="Date",
                    yaxis_title="Price ($)",
                    hovermode='x unified',
                    height=600
                ) # Parenthesis closed here correctly
                
                st.plotly_chart(fig, use_container_width=True)

                # --- Chatbot Integration ---
                mc_context = f"Ticker: {ticker}. Projected Median: ${p50[-1]:,.2f}. Annual Drift: {drift}%."
                run_sidebar_chatbot(context_data=mc_context)
    else:
        st.warning("No data found. Check ticker or API limit.")

except Exception as e:
    st.error(f"An error occurred: {e}")

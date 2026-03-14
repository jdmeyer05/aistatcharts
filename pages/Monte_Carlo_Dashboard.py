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

# Set unique page ID for session state isolation
if 'page_id' not in st.session_state:
    st.session_state.page_id = 'monte_carlo'

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
            logger.error(f"Invalid data structure for {ticker}: missing 'Close' column")
        elif len(data) < 10:
            st.warning(f"⚠️ Warning: Only {len(data)} data points available. Results may be unreliable (minimum 10 recommended).")
        else:
            # Extract Close as a Series and handle both DataFrame and Series returns
            close_data = data['Close']
            if isinstance(close_data, pd.DataFrame):
                px_close = close_data.squeeze()
            else:
                px_close = close_data
            
            # Ensure px_close is a proper pandas Series with no NaN values
            if px_close.isnull().any():
                st.warning(f"⚠️ Warning: Data contains {px_close.isnull().sum()} NaN values. Removing them.")
                px_close = px_close.dropna()
            
            # Double check we still have enough data after cleaning
            if len(px_close) < 10:
                st.error(f"❌ Error: Not enough valid data points after cleaning ({len(px_close)} available).")
                logger.error(f"Insufficient data for {ticker} after NaN removal")
            else:
                # Run the Simulation from src/simulation.py
                try:
                    st.info(f"📊 Running Monte Carlo simulation with {len(px_close)} data points...")
                    
                    p5, p50, p95, steps = run_monte_carlo_engine(
                        px_close, n_sims, drift, vol_mult, method, seasonal
                    )
                    
                    # Chart: Historical & Projection with confidence intervals
                    fig = go.Figure()
                    
                    # Create date index for historical data
                    historical_dates = pd.date_range(end=pd.Timestamp.now(), periods=len(px_close), freq='D')
                    
                    # Add historical data
                    fig.add_trace(go.Scatter(
                        x=historical_dates,
                        y=px_close.values,  # Use .values to ensure numpy array
                        name="Historical Data",
                        line=dict(color='#1f77b4', width=2),
                        hovertemplate='<b>Date:</b> %{x|%Y-%m-%d}<br><b>Price:</b> $%{y:,.2f}<extra></extra>'
                    ))
                    
                    # Create future dates for projections
                    future_dates = pd.date_range(start=pd.Timestamp.now(), periods=len(p50), freq='D')
                    
                    # Add projection lines (percentiles)
                    fig.add_trace(go.Scatter(
                        x=future_dates,
                        y=p95,
                        name="95th Percentile (Upside)",
                        line=dict(color='#00aa00', dash='dash', width=1),
                        opacity=0.7,
                        hovertemplate='<b>Date:</b> %{x|%Y-%m-%d}<br><b>95th Percentile:</b> $%{y:,.2f}<extra></extra>'
                    ))
                    
                    fig.add_trace(go.Scatter(
                        x=future_dates,
                        y=p50, 
                        name="Median Forecast", 
                        line=dict(color='#ffaa00', width=3),
                        hovertemplate='<b>Date:</b> %{x|%Y-%m-%d}<br><b>Median:</b> $%{y:,.2f}<extra></extra>'
                    ))
                    
                    fig.add_trace(go.Scatter(
                        x=future_dates,
                        y=p5,
                        name="5th Percentile (Downside)",
                        line=dict(color='#ff0000', dash='dash', width=1),
                        opacity=0.7,
                        fill='tonexty',
                        hovertemplate='<b>Date:</b> %{x|%Y-%m-%d}<br><b>5th Percentile:</b> $%{y:,.2f}<extra></extra>'
                    ))
                    
                    fig.update_layout(
                        template="plotly_dark", 
                        title=f"📊 {ticker} - Monte Carlo Forecast ({lookback}-day lookback, {n_sims} simulations)",
                        xaxis_title="Date",
                        yaxis_title="Price ($)",
                        hovermode='x unified',
                        height=600

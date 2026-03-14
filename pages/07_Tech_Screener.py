import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from src.data_engine import fetch_massive_data, format_massive_ticker
from src.chatbot import run_sidebar_chatbot

st.title("🔬 High-Beta & Emerging Tech Screener")

with st.sidebar:
    st.header("Screener Settings")
    # Defaulting to quantum computing / high-beta tech
    raw_ticker = st.text_input("Tech Ticker", value="RGTI").upper()
    lookback = st.slider("Momentum Lookback (Days)", 30, 365, 90)
    vol_window = st.slider("Volatility Window", 10, 50, 20)

ticker = format_massive_ticker(raw_ticker)
data = fetch_massive_data(ticker, lookback)

if data is not None and not data.empty:
    df = pd.DataFrame(data['Close'])
    
    # --- Custom Metrics Calculation ---
    df['Daily_Return'] = df['Close'].pct_change()
    # Annualized Volatility (assuming 252 trading days)
    df['Ann_Volatility'] = df['Daily_Return'].rolling(window=vol_window).std() * np.sqrt(252) * 100
    # Momentum (Cumulative return over the lookback period)
    df['Momentum_Index'] = (df['Close'] / df['Close'].iloc[0]) * 100

    st.subheader(f"Volatility & Momentum Profile: {raw_ticker}")
    
    # --- CHART 1: PRICE VS VOLATILITY ---
    fig = go.Figure()
    
    # Price Line
    fig.add_trace(go.Scatter(
        x=df.index, y=df['Close'], 
        name="Close Price", line=dict(color='#00d1ff', width=2),
        yaxis="y1"
    ))
    
    # Volatility Overlay (Secondary Axis)
    fig.add_trace(go.Scatter(
        x=df.index, y=df['Ann_Volatility'], 
        name=f"{vol_window}-Day Volatility %", line=dict(color='#ffaa00', dash='dot', width=2),
        yaxis="y2"
    ))
    
    fig.update_layout(
        template="plotly_dark",
        yaxis=dict(title="Price ($)", side="left"),
        yaxis2=dict(title="Annualized Volatility (%)", side="right", overlaying="y", showgrid=False),
        hovermode="x unified",
        height=500
    )
    st.plotly_chart(fig, use_container_width=True)

    # --- FUNDAMENTAL AI SCRAPER (Simulated via Chatbot Context) ---
    st.divider()
    st.subheader("🤖 Bull vs. Bear Fundamental Case")
    st.info("Ask the AI to generate a bull and bear case for this ticker based on current market mechanics.")
    
    curr_vol = df['Ann_Volatility'].iloc[-1]
    curr_price = df['Close'].iloc[-1]
    
    # We feed the AI the exact volatility metrics so it can contextualize its fundamental analysis
    ctx = (f"Act as a fundamental tech analyst. The ticker {raw_ticker} is currently trading at ${curr_price:.2f} "
           f"with an extremely high annualized volatility of {curr_vol:.1f}%. Generate a concise Bull Case and Bear Case "
           f"for this specific asset.")
    run_sidebar_chatbot(context_data=ctx)

else:
    st.warning("No data found for the selected ticker. Please check your data engine connection.")

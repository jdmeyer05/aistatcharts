import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from src.data_engine import fetch_massive_data, format_massive_ticker, render_data_source_footer
from src.chatbot import run_sidebar_chatbot

from src.layout import setup_page, get_active_ticker, set_active_ticker, fun_loader
setup_page("10_Tech_Screener")

st.title("🛰️ Advanced Technical Screener")
st.markdown("Multi-dimensional technical analysis: Trend (EMAs), Momentum (MACD/RSI), and Volatility (Bollinger Bands).")

# --- SIDEBAR CONFIGURATION ---
with st.sidebar:
    st.header("Screener Settings")
    with st.form("tech_settings"):
        raw_ticker = st.text_input("Ticker", value=get_active_ticker())
        lookback = st.slider("Lookback (Days)", 90, 730, 365, step=30)
        
        st.divider()
        st.caption("Indicator Parameters")
        rsi_period = st.number_input("RSI Period", value=14)
        macd_fast = st.number_input("MACD Fast", value=12)
        macd_slow = st.number_input("MACD Slow", value=26)
        bb_window = st.number_input("Bollinger Period", value=20)
        
        submit = st.form_submit_button("🚀 Run Technicals")

ticker = format_massive_ticker(raw_ticker)
set_active_ticker(ticker)

# --- CALCULATE INDICATORS ---
if submit or 'tech_df' not in st.session_state or st.session_state.get('tech_ticker') != ticker:
    with fun_loader("compute"):
        df = fetch_massive_data(ticker, lookback)
        
        if df is None or df.empty:
            st.error(f"Failed to fetch data for {ticker}.")
            st.stop()
            
        # 1. EMAs (Trend)
        df['EMA_20'] = df['Close'].ewm(span=20, adjust=False).mean()
        df['EMA_50'] = df['Close'].ewm(span=50, adjust=False).mean()
        df['EMA_200'] = df['Close'].ewm(span=200, adjust=False).mean()
        
        # 2. RSI (Momentum)
        delta = df['Close'].diff()
        gain = delta.where(delta > 0, 0.0).ewm(alpha=1/rsi_period, adjust=False).mean()
        loss = (-delta.where(delta < 0, 0.0)).ewm(alpha=1/rsi_period, adjust=False).mean()
        rs = gain / loss
        df['RSI'] = 100 - (100 / (1 + rs))
        
        # 3. MACD (Trend/Momentum)
        ema_fast = df['Close'].ewm(span=macd_fast, adjust=False).mean()
        ema_slow = df['Close'].ewm(span=macd_slow, adjust=False).mean()
        df['MACD'] = ema_fast - ema_slow
        df['MACD_Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
        df['MACD_Hist'] = df['MACD'] - df['MACD_Signal']
        
        # 4. Bollinger Bands (Volatility)
        df['BB_Mid'] = df['Close'].rolling(window=bb_window).mean()
        df['BB_Std'] = df['Close'].rolling(window=bb_window).std()
        df['BB_Upper'] = df['BB_Mid'] + (df['BB_Std'] * 2)
        df['BB_Lower'] = df['BB_Mid'] - (df['BB_Std'] * 2)

        st.session_state.tech_df = df
        st.session_state.tech_ticker = ticker

# --- RENDER DASHBOARD ---
if 'tech_df' in st.session_state:
    df = st.session_state.tech_df
    current_px = df['Close'].iloc[-1]
    
    # Trim NA values from the beginning caused by rolling windows
    plot_df = df.dropna().tail(252) # Show last 1 trading year in charts to keep it readable
    
    # --- 2x2 CHART GRID ---
    r1c1, r1c2 = st.columns(2)
    
    # 1. Trend (Price + EMAs)
    with r1c1:
        st.subheader("1. Price Action & Trend (EMAs)")
        fig_trend = go.Figure()
        fig_trend.add_trace(go.Scatter(x=plot_df.index, y=plot_df['Close'], name='Close', line=dict(color='white', width=2)))
        fig_trend.add_trace(go.Scatter(x=plot_df.index, y=plot_df['EMA_20'], name='EMA 20', line=dict(color='#00d1ff', width=1.5)))
        fig_trend.add_trace(go.Scatter(x=plot_df.index, y=plot_df['EMA_50'], name='EMA 50', line=dict(color='#ffaa00', width=1.5)))
        fig_trend.add_trace(go.Scatter(x=plot_df.index, y=plot_df['EMA_200'], name='EMA 200', line=dict(color='#ff4b4b', width=1.5, dash='dot')))
        fig_trend.update_layout(template="plotly_dark", height=350, margin=dict(t=20, b=0, l=0, r=0), hovermode='x unified')
        st.plotly_chart(fig_trend, use_container_width=True)

    # 2. Volatility (Bollinger Bands)
    with r1c2:
        st.subheader("2. Volatility (Bollinger Bands)")
        fig_bb = go.Figure()
        fig_bb.add_trace(go.Scatter(x=plot_df.index, y=plot_df['Close'], name='Close', line=dict(color='white', width=1.5)))
        fig_bb.add_trace(go.Scatter(x=plot_df.index, y=plot_df['BB_Upper'], name='Upper Band', line=dict(color='rgba(173, 127, 255, 0.5)')))
        fig_bb.add_trace(go.Scatter(x=plot_df.index, y=plot_df['BB_Lower'], name='Lower Band', line=dict(color='rgba(173, 127, 255, 0.5)'), fill='tonexty', fillcolor='rgba(173, 127, 255, 0.1)'))
        fig_bb.update_layout(template="plotly_dark", height=350, margin=dict(t=20, b=0, l=0, r=0), hovermode='x unified')
        st.plotly_chart(fig_bb, use_container_width=True)

    r2c1, r2c2 = st.columns(2)
    
    # 3. Momentum (MACD)
    with r2c1:
        st.subheader("3. Momentum (MACD)")
        fig_macd = go.Figure()
        
        # Color histogram based on positive/negative
        colors = np.where(plot_df['MACD_Hist'] >= 0, '#00ff96', '#ff4b4b')
        fig_macd.add_trace(go.Bar(x=plot_df.index, y=plot_df['MACD_Hist'], name='Histogram', marker_color=colors))
        fig_macd.add_trace(go.Scatter(x=plot_df.index, y=plot_df['MACD'], name='MACD Line', line=dict(color='#00d1ff', width=2)))
        fig_macd.add_trace(go.Scatter(x=plot_df.index, y=plot_df['MACD_Signal'], name='Signal Line', line=dict(color='#ffaa00', width=2)))
        fig_macd.update_layout(template="plotly_dark", height=350, margin=dict(t=20, b=0, l=0, r=0), hovermode='x unified')
        st.plotly_chart(fig_macd, use_container_width=True)

    # 4. Strength (RSI)
    with r2c2:
        st.subheader("4. Relative Strength (RSI)")
        fig_rsi = go.Figure()
        fig_rsi.add_trace(go.Scatter(x=plot_df.index, y=plot_df['RSI'], name='RSI', line=dict(color='#ad7fff', width=2)))
        
        # Overbought / Oversold zones
        fig_rsi.add_hline(y=70, line_dash="dash", line_color="#ff4b4b", annotation_text="Overbought (70)")
        fig_rsi.add_hline(y=30, line_dash="dash", line_color="#00ff96", annotation_text="Oversold (30)")
        
        # Color background for extreme zones
        fig_rsi.add_hrect(y0=70, y1=100, fillcolor="rgba(255, 75, 75, 0.1)", line_width=0)
        fig_rsi.add_hrect(y0=0, y1=30, fillcolor="rgba(0, 255, 150, 0.1)", line_width=0)
        
        fig_rsi.update_layout(template="plotly_dark", height=350, margin=dict(t=20, b=0, l=0, r=0), yaxis=dict(range=[0, 100]))
        st.plotly_chart(fig_rsi, use_container_width=True)

    # --- AI CONTEXT INJECTION ---
    latest_rsi = df['RSI'].iloc[-1]
    latest_macd = df['MACD'].iloc[-1]
    latest_sig = df['MACD_Signal'].iloc[-1]
    macd_cross = "Bullish" if latest_macd > latest_sig else "Bearish"
    ema_trend = "Bullish" if df['EMA_20'].iloc[-1] > df['EMA_50'].iloc[-1] else "Bearish"
    
    ctx = (f"Technical Scan for {ticker}. Spot: ${current_px:.2f}. "
           f"RSI: {latest_rsi:.1f}. MACD Cross: {macd_cross}. Short-Term Trend (EMA20 vs EMA50): {ema_trend}.")
    run_sidebar_chatbot(ctx)

render_data_source_footer()

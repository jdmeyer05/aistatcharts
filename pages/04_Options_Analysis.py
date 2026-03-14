import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from src.data_engine import get_expiration_dates, fetch_options_chain, fetch_massive_data, format_massive_ticker, render_data_source_footer
from src.chatbot import run_sidebar_chatbot
from src.auth import check_auth

st.set_page_config(page_title="Advanced Options Analysis", layout="wide")
check_auth()

st.title("📊 Advanced Options Surface Analysis")
st.markdown("Analyze Implied Volatility skew, Open Interest walls, Volume distribution, and Greek exposures across specific expirations.")

# --- SIDEBAR CONFIGURATION ---
with st.sidebar:
    st.header("Chain Settings")
    raw_ticker = st.text_input("Underlying Ticker", value="SPY")
    ticker = format_massive_ticker(raw_ticker)
    
    if ":" in ticker or "ERCOT" in ticker.upper():
        st.error("🚨 Equities only.")
        st.stop()
        
    # Instantly load expirations to populate the dropdown
    expirations = get_expiration_dates(ticker)
    
    if expirations:
        selected_exp = st.selectbox("🎯 Expiration Date", expirations)
        submit = st.button("🚀 Fetch Chain Data", type="primary", use_container_width=True)
    else:
        st.warning("No expirations found. Check ticker.")
        submit = False
        selected_exp = None

# --- RENDER DASHBOARD ---
if submit and selected_exp:
    with st.spinner(f"Processing Black-Scholes surface for {ticker}..."):
        df = fetch_options_chain(ticker, selected_exp)
        px_df = fetch_massive_data(ticker, 5)
        current_px = px_df['Close'].iloc[-1] if px_df is not None else None
        
        if df is None or df.empty:
            st.error(f"Failed to fetch options data for {ticker}.")
            st.stop()
            
        exp_df = df.sort_values('strike_price')
        calls = exp_df[exp_df['contract_type'] == 'call']
        puts = exp_df[exp_df['contract_type'] == 'put']
        
        # --- 2x2 CHART GRID ---
        r1c1, r1c2 = st.columns(2)
        
        with r1c1:
            st.subheader("1. Implied Volatility Smile (Skew)")
            fig_iv = go.Figure()
            fig_iv.add_trace(go.Scatter(x=calls['strike_price'], y=calls['implied_volatility'], mode='lines+markers', name='Calls', line=dict(color='#00ff96')))
            fig_iv.add_trace(go.Scatter(x=puts['strike_price'], y=puts['implied_volatility'], mode='lines+markers', name='Puts', line=dict(color='#ff4b4b')))
            if current_px: fig_iv.add_vline(x=current_px, line_dash="dot", line_color="#ffaa00", annotation_text="Spot")
            fig_iv.update_layout(template="plotly_dark", height=350, margin=dict(t=20, b=0, l=0, r=0), yaxis_title="Implied Volatility")
            st.plotly_chart(fig_iv, use_container_width=True)

        with r1c2:
            st.subheader("2. Open Interest Profile (Liquidity Walls)")
            fig_oi = go.Figure()
            fig_oi.add_trace(go.Bar(x=calls['strike_price'], y=calls['open_interest'], name='Call OI', marker_color='rgba(0, 255, 150, 0.7)'))
            fig_oi.add_trace(go.Bar(x=puts['strike_price'], y=puts['open_interest'], name='Put OI', marker_color='rgba(255, 75, 75, 0.7)'))
            if current_px: fig_oi.add_vline(x=current_px, line_dash="dot", line_color="#ffaa00", annotation_text="Spot")
            fig_oi.update_layout(template="plotly_dark", height=350, margin=dict(t=20, b=0, l=0, r=0), barmode='group', yaxis_title="Open Interest")
            st.plotly_chart(fig_oi, use_container_width=True)

        r2c1, r2c2 = st.columns(2)
        
        with r2c1:
            st.subheader("3. Delta Exposure Curve (Calculated)")
            fig_delta = go.Figure()
            fig_delta.add_trace(go.Scatter(x=calls['strike_price'], y=calls['delta'], mode='lines', name='Call Delta', line=dict(color='#00ff96', width=2)))
            fig_delta.add_trace(go.Scatter(x=puts['strike_price'], y=puts['delta'], mode='lines', name='Put Delta', line=dict(color='#ff4b4b', width=2)))
            if current_px: fig_delta.add_vline(x=current_px, line_dash="dot", line_color="#ffaa00")
            fig_delta.add_hline(y=0, line_dash="solid", line_color="white", opacity=0.3)
            fig_delta.update_layout(template="plotly_dark", height=350, margin=dict(t=20, b=0, l=0, r=0), yaxis_title="Delta")
            st.plotly_chart(fig_delta, use_container_width=True)

        with r2c2:
            st.subheader("4. Intraday Volume Distribution")
            fig_vol = go.Figure()
            fig_vol.add_trace(go.Bar(x=calls['strike_price'], y=calls['volume'], name='Call Vol', marker_color='rgba(0, 209, 255, 0.7)'))
            fig_vol.add_trace(go.Bar(x=puts['strike_price'], y=puts['volume'], name='Put Vol', marker_color='rgba(173, 127, 255, 0.7)'))
            if current_px: fig_vol.add_vline(x=current_px, line_dash="dot", line_color="#ffaa00", annotation_text="Spot")
            fig_vol.update_layout(template="plotly_dark", height=350, margin=dict(t=20, b=0, l=0, r=0), barmode='group', yaxis_title="Volume")
            st.plotly_chart(fig_vol, use_container_width=True)

        with st.expander("View Raw Options Chain Data"):
            st.dataframe(exp_df[['strike_price', 'contract_type', 'bid', 'ask', 'volume', 'open_interest', 'implied_volatility', 'delta']], use_container_width=True)

        max_call_oi_strike = calls.loc[calls['open_interest'].idxmax()]['strike_price'] if not calls.empty else "N/A"
        max_put_oi_strike = puts.loc[puts['open_interest'].idxmax()]['strike_price'] if not puts.empty else "N/A"
        run_sidebar_chatbot(f"Options Analysis for {ticker} expiring {selected_exp}. Spot: {current_px}. Highest Call OI Strike: {max_call_oi_strike}. Highest Put OI Strike: {max_put_oi_strike}.")

render_data_source_footer()

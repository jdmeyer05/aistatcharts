import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from src.data_engine import fetch_options_chain, fetch_massive_data, format_massive_ticker, render_data_source_footer
from src.chatbot import run_sidebar_chatbot

# Must be wide to support the 2x2 grid gracefully
st.set_page_config(page_title="Advanced Options Analysis", layout="wide")

st.title("📊 Advanced Options Surface Analysis")
st.markdown("Analyze Implied Volatility skew, Open Interest walls, Volume distribution, and Greek exposures across specific expirations.")

# --- SIDEBAR CONFIGURATION ---
with st.sidebar:
    st.header("Chain Settings")
    with st.form("options_settings"):
        raw_ticker = st.text_input("Underlying Ticker", value="SPY")
        submit = st.form_submit_button("🚀 Fetch Chain Data")

ticker = format_massive_ticker(raw_ticker)

if ":" in ticker or "ERCOT" in ticker.upper():
    st.error("🚨 Options analysis is only supported for Equities (Stocks/ETFs).")
    st.stop()

# --- FETCH DATA ---
if submit or 'opt_surface_df' not in st.session_state or st.session_state.get('opt_surface_ticker') != ticker:
    with st.spinner(f"Pulling live snapshot for {ticker}..."):
        df = fetch_options_chain(ticker)
        px_df = fetch_massive_data(ticker, 5)
        
        if df is None or df.empty:
            st.error(f"Failed to fetch options data for {ticker}.")
            st.stop()
            
        st.session_state.opt_surface_df = df
        st.session_state.opt_surface_ticker = ticker
        st.session_state.opt_underlying_px = px_df['Close'].iloc[-1] if px_df is not None else None

# --- RENDER DASHBOARD ---
if 'opt_surface_df' in st.session_state:
    df = st.session_state.opt_surface_df
    current_px = st.session_state.opt_underlying_px
    
    # Filter Controls
    expirations = sorted(df['expiration_date'].dropna().unique())
    if not expirations:
        st.warning("No valid expiration dates found in the chain.")
        st.stop()
        
    selected_exp = st.selectbox("🎯 Select Expiration Date to Analyze", expirations, index=0)
    
    # Filter dataframe by selected expiration and sort by strike
    exp_df = df[df['expiration_date'] == selected_exp].sort_values('strike_price')
    calls = exp_df[exp_df['contract_type'] == 'call']
    puts = exp_df[exp_df['contract_type'] == 'put']
    
    st.divider()
    
    # --- 2x2 CHART GRID ---
    # Row 1
    r1c1, r1c2 = st.columns(2)
    
    with r1c1:
        st.subheader("1. Implied Volatility Smile (Skew)")
        fig_iv = go.Figure()
        fig_iv.add_trace(go.Scatter(x=calls['strike_price'], y=calls['implied_volatility'], mode='lines+markers', name='Calls', line=dict(color='#00ff96')))
        fig_iv.add_trace(go.Scatter(x=puts['strike_price'], y=puts['implied_volatility'], mode='lines+markers', name='Puts', line=dict(color='#ff4b4b')))
        if current_px:
            fig_iv.add_vline(x=current_px, line_dash="dot", line_color="#ffaa00", annotation_text="Spot")
        fig_iv.update_layout(template="plotly_dark", height=350, margin=dict(t=20, b=0, l=0, r=0), yaxis_title="Implied Volatility")
        st.plotly_chart(fig_iv, use_container_width=True)

    with r1c2:
        st.subheader("2. Open Interest Profile (Liquidity Walls)")
        fig_oi = go.Figure()
        fig_oi.add_trace(go.Bar(x=calls['strike_price'], y=calls['open_interest'], name='Call OI', marker_color='rgba(0, 255, 150, 0.7)'))
        fig_oi.add_trace(go.Bar(x=puts['strike_price'], y=puts['open_interest'], name='Put OI', marker_color='rgba(255, 75, 75, 0.7)'))
        if current_px:
            fig_oi.add_vline(x=current_px, line_dash="dot", line_color="#ffaa00", annotation_text="Spot")
        fig_oi.update_layout(template="plotly_dark", height=350, margin=dict(t=20, b=0, l=0, r=0), barmode='group', yaxis_title="Open Interest")
        st.plotly_chart(fig_oi, use_container_width=True)

    # Row 2
    r2c1, r2c2 = st.columns(2)
    
    with r2c1:
        st.subheader("3. Delta Exposure Curve")
        fig_delta = go.Figure()
        fig_delta.add_trace(go.Scatter(x=calls['strike_price'], y=calls['delta'], mode='lines', name='Call Delta', line=dict(color='#00ff96', width=2)))
        fig_delta.add_trace(go.Scatter(x=puts['strike_price'], y=puts['delta'], mode='lines', name='Put Delta', line=dict(color='#ff4b4b', width=2)))
        if current_px:
            fig_delta.add_vline(x=current_px, line_dash="dot", line_color="#ffaa00")
        fig_delta.add_hline(y=0, line_dash="solid", line_color="white", opacity=0.3)
        fig_delta.update_layout(template="plotly_dark", height=350, margin=dict(t=20, b=0, l=0, r=0), yaxis_title="Delta")
        st.plotly_chart(fig_delta, use_container_width=True)

    with r2c2:
        st.subheader("4. Intraday Volume Distribution")
        fig_vol = go.Figure()
        fig_vol.add_trace(go.Bar(x=calls['strike_price'], y=calls['volume'], name='Call Vol', marker_color='rgba(0, 209, 255, 0.7)'))
        fig_vol.add_trace(go.Bar(x=puts['strike_price'], y=puts['volume'], name='Put Vol', marker_color='rgba(173, 127, 255, 0.7)'))
        if current_px:
            fig_vol.add_vline(x=current_px, line_dash="dot", line_color="#ffaa00", annotation_text="Spot")
        fig_vol.update_layout(template="plotly_dark", height=350, margin=dict(t=20, b=0, l=0, r=0), barmode='group', yaxis_title="Volume")
        st.plotly_chart(fig_vol, use_container_width=True)

    # --- RAW DATA TABLE ---
    with st.expander("View Raw Options Chain Data"):
        st.dataframe(exp_df[['strike_price', 'contract_type', 'bid', 'ask', 'volume', 'open_interest', 'implied_volatility', 'delta', 'theta', 'gamma']], use_container_width=True)

    # --- AI CONTEXT ---
    max_call_oi_strike = calls.loc[calls['open_interest'].idxmax()]['strike_price'] if not calls.empty else "N/A"
    max_put_oi_strike = puts.loc[puts['open_interest'].idxmax()]['strike_price'] if not puts.empty else "N/A"
    
    ctx = (f"Options Analysis for {ticker} expiring {selected_exp}. Spot: {current_px}. "
           f"Highest Call OI Strike: {max_call_oi_strike}. Highest Put OI Strike: {max_put_oi_strike}.")
    run_sidebar_chatbot(ctx)

render_data_source_footer()

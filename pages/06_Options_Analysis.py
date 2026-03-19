import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from src.data_engine import get_expiration_dates, fetch_options_chain, fetch_massive_data, format_massive_ticker, render_data_source_footer
from src.chatbot import run_sidebar_chatbot
from src.layout import setup_page
setup_page("06_Options_Analysis")

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

    expirations = get_expiration_dates(ticker)

    if expirations:
        selected_exp = st.selectbox("🎯 Expiration Date", expirations)
        submit = st.button("🚀 Fetch Chain Data", type="primary", use_container_width=True)
    else:
        st.warning("No expirations found. Check ticker.")
        submit = False
        selected_exp = None

# --- FETCH & STORE IN SESSION STATE ---
if submit and selected_exp:
    with st.spinner(f"Fetching options surface for {ticker}..."):
        df = fetch_options_chain(ticker, selected_exp)
        px_df = fetch_massive_data(ticker, 5)
        current_px = px_df['Close'].iloc[-1] if px_df is not None else None

        if df is not None and not df.empty:
            st.session_state['options_df'] = df
            st.session_state['options_current_px'] = current_px
            st.session_state['options_ticker'] = ticker
            st.session_state['options_exp'] = selected_exp
        else:
            st.error(f"Failed to fetch options data for {ticker}.")
            st.stop()

# --- RENDER DASHBOARD FROM SESSION STATE ---
if 'options_df' in st.session_state:
    df = st.session_state['options_df']
    current_px = st.session_state['options_current_px']
    ticker_display = st.session_state['options_ticker']
    exp_display = st.session_state['options_exp']

    exp_df = df.sort_values('strike_price')

    # --- Filter to strikes within x-axis range and clip y-axis outliers at 95th percentile ---
    calls_raw = exp_df[exp_df['contract_type'] == 'call']
    puts_raw = exp_df[exp_df['contract_type'] == 'put']

    # --- Dynamic x-axis range centered on spot ---
    if current_px is not None:
        x_range_pct = 0.15
        x_min = current_px * (1 - x_range_pct)
        x_max = current_px * (1 + x_range_pct)
    else:
        x_min = exp_df['strike_price'].min()
        x_max = exp_df['strike_price'].max()

    # Filter to visible range for charting
    calls = calls_raw[(calls_raw['strike_price'] >= x_min) & (calls_raw['strike_price'] <= x_max)]
    puts = puts_raw[(puts_raw['strike_price'] >= x_min) & (puts_raw['strike_price'] <= x_max)]

    # 95th percentile caps for y-axes
    oi_cap = pd.concat([calls['open_interest'], puts['open_interest']]).quantile(0.95)
    vol_cap = pd.concat([calls['volume'], puts['volume']]).quantile(0.95)
    iv_cap = pd.concat([calls['implied_volatility'], puts['implied_volatility']]).quantile(0.95)

    # --- 2x2 CHART GRID ---
    r1c1, r1c2 = st.columns(2)

    with r1c1:
        st.subheader("1. Implied Volatility Smile (Skew)")
        fig_iv = go.Figure()
        fig_iv.add_trace(go.Scatter(x=calls['strike_price'], y=calls['implied_volatility'], mode='lines+markers', name='Calls', line=dict(color='#00ff96')))
        fig_iv.add_trace(go.Scatter(x=puts['strike_price'], y=puts['implied_volatility'], mode='lines+markers', name='Puts', line=dict(color='#ff4b4b')))
        if current_px: fig_iv.add_vline(x=current_px, line_dash="dot", line_color="#ffaa00", annotation_text="Spot")
        fig_iv.update_layout(template="plotly_dark", height=350, margin=dict(t=20, b=0, l=0, r=0), yaxis_title="Implied Volatility", xaxis=dict(range=[x_min, x_max]), yaxis=dict(range=[0, iv_cap * 1.05]))
        st.plotly_chart(fig_iv, use_container_width=True)

    with r1c2:
        st.subheader("2. Open Interest Profile (Liquidity Walls)")
        fig_oi = go.Figure()
        fig_oi.add_trace(go.Bar(x=calls['strike_price'], y=calls['open_interest'], name='Call OI', marker_color='#00ff96', opacity=1.0))
        fig_oi.add_trace(go.Bar(x=puts['strike_price'], y=puts['open_interest'], name='Put OI', marker_color='#ff4b4b', opacity=1.0))
        if current_px: fig_oi.add_vline(x=current_px, line_dash="dot", line_color="#ffaa00", annotation_text="Spot")
        fig_oi.update_layout(template="plotly_dark", height=350, margin=dict(t=20, b=0, l=0, r=0), barmode='group', yaxis_title="Open Interest", bargap=0.1, bargroupgap=0.05, xaxis=dict(range=[x_min, x_max]), yaxis=dict(range=[0, oi_cap * 1.05]))
        st.plotly_chart(fig_oi, use_container_width=True)

    r2c1, r2c2 = st.columns(2)

    with r2c1:
        st.subheader("3. Delta Exposure Curve")
        fig_delta = go.Figure()
        fig_delta.add_trace(go.Scatter(x=calls['strike_price'], y=calls['delta'], mode='lines', name='Call Delta', line=dict(color='#00ff96', width=2)))
        fig_delta.add_trace(go.Scatter(x=puts['strike_price'], y=puts['delta'], mode='lines', name='Put Delta', line=dict(color='#ff4b4b', width=2)))
        if current_px: fig_delta.add_vline(x=current_px, line_dash="dot", line_color="#ffaa00")
        fig_delta.add_hline(y=0, line_dash="solid", line_color="white", opacity=0.3)
        fig_delta.update_layout(template="plotly_dark", height=350, margin=dict(t=20, b=0, l=0, r=0), yaxis_title="Delta", xaxis=dict(range=[x_min, x_max]))
        st.plotly_chart(fig_delta, use_container_width=True)

    with r2c2:
        st.subheader("4. Intraday Volume Distribution")
        fig_vol = go.Figure()
        fig_vol.add_trace(go.Bar(x=calls['strike_price'], y=calls['volume'], name='Call Vol', marker_color='#00d1ff', opacity=1.0))
        fig_vol.add_trace(go.Bar(x=puts['strike_price'], y=puts['volume'], name='Put Vol', marker_color='#ad7fff', opacity=1.0))
        if current_px: fig_vol.add_vline(x=current_px, line_dash="dot", line_color="#ffaa00", annotation_text="Spot")
        fig_vol.update_layout(template="plotly_dark", height=350, margin=dict(t=20, b=0, l=0, r=0), barmode='group', yaxis_title="Volume", bargap=0.1, bargroupgap=0.05, xaxis=dict(range=[x_min, x_max]), yaxis=dict(range=[0, vol_cap * 1.05]))
        st.plotly_chart(fig_vol, use_container_width=True)

    with st.expander("View Options Chain — Straddle View"):
        call_cols = calls[['strike_price', 'delta', 'implied_volatility', 'volume', 'open_interest', 'bid', 'ask']].set_index('strike_price')
        call_cols.columns = ['C Delta', 'C IV', 'C Vol', 'C OI', 'C Bid', 'C Ask']
        put_cols = puts[['strike_price', 'bid', 'ask', 'open_interest', 'volume', 'implied_volatility', 'delta']].set_index('strike_price')
        put_cols.columns = ['P Bid', 'P Ask', 'P OI', 'P Vol', 'P IV', 'P Delta']
        straddle_df = call_cols.join(put_cols, how='outer').reset_index()
        straddle_df = straddle_df.rename(columns={'strike_price': 'Strike'})
        straddle_df = straddle_df[['C Delta', 'C IV', 'C Vol', 'C OI', 'C Bid', 'C Ask', 'Strike', 'P Bid', 'P Ask', 'P OI', 'P Vol', 'P IV', 'P Delta']]

        # Center at the money
        if current_px is not None:
            atm_idx = (straddle_df['Strike'] - current_px).abs().idxmin()
            num_strikes = 20
            start = max(0, atm_idx - num_strikes)
            end = min(len(straddle_df), atm_idx + num_strikes + 1)
            straddle_df = straddle_df.iloc[start:end].reset_index(drop=True)

        st.dataframe(straddle_df, use_container_width=True, hide_index=True)

    max_call_oi_strike = calls.loc[calls['open_interest'].idxmax()]['strike_price'] if not calls.empty else "N/A"
    max_put_oi_strike = puts.loc[puts['open_interest'].idxmax()]['strike_price'] if not puts.empty else "N/A"
    run_sidebar_chatbot(f"Options Analysis for {ticker_display} expiring {exp_display}. Spot: {current_px}. Highest Call OI Strike: {max_call_oi_strike}. Highest Put OI Strike: {max_put_oi_strike}.")

    render_data_source_footer()

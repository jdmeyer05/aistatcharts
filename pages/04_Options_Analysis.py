import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from src.data_engine import fetch_options_chain
from src.chatbot import run_sidebar_chatbot

st.title("🎯 Options Chain & Volatility Analysis")

# --- Sidebar Controls ---
with st.sidebar:
    st.header("Options Parameters")
    ticker = st.text_input("Underlying Ticker", value="SPY").upper()
    # For a live app, you'd dynamically fetch available expirations. 
    # For now, we allow manual input or leave blank for all near-term.
    expiry = st.text_input("Expiration Date (YYYY-MM-DD)", value="") 
    strike_range = st.slider("Strike Range % from Spot", 5, 50, 20)

# --- Data Fetching ---
# In a fully connected state, you'd fetch the current spot price here too.
st.info(f"Fetching options flow for {ticker}...")

# Assuming we have a mock dataframe structure returned by the API for the sake of the layout:
# columns: ['contract_type', 'strike_price', 'open_interest', 'volume', 'implied_volatility']
df_options = fetch_options_chain(ticker, expiry if expiry else None)

if df_options is not None and not df_options.empty:
    # Ensure columns exist (adjust these to match Massive's exact column names)
    req_cols = ['contract_type', 'strike_price', 'open_interest', 'volume', 'implied_volatility']
    if all(col in df_options.columns for col in req_cols):
        
        # Split Calls and Puts
        calls = df_options[df_options['contract_type'] == 'call'].sort_values('strike_price')
        puts = df_options[df_options['contract_type'] == 'put'].sort_values('strike_price')
        
        # --- CHART 1: OPEN INTEREST PROFILE ---
        st.subheader("Open Interest Profile (Support & Resistance)")
        
        fig_oi = go.Figure()
        fig_oi.add_trace(go.Bar(
            x=calls['strike_price'], y=calls['open_interest'],
            name='Call OI', marker_color='#00d1ff', opacity=0.7
        ))
        fig_oi.add_trace(go.Bar(
            x=puts['strike_price'], y=puts['open_interest'],
            name='Put OI', marker_color='#ffaa00', opacity=0.7
        ))
        
        fig_oi.update_layout(
            template="plotly_dark",
            barmode='group',
            xaxis_title="Strike Price",
            yaxis_title="Open Interest",
            hovermode="x unified",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
        )
        st.plotly_chart(fig_oi, use_container_width=True)
        
        # --- CHART 2: IMPLIED VOLATILITY SMILE ---
        st.divider()
        st.subheader("Implied Volatility Smile")
        
        fig_iv = go.Figure()
        fig_iv.add_trace(go.Scatter(
            x=calls['strike_price'], y=calls['implied_volatility'],
            mode='lines+markers', name='Call IV', line=dict(color='#00d1ff')
        ))
        fig_iv.add_trace(go.Scatter(
            x=puts['strike_price'], y=puts['implied_volatility'],
            mode='lines+markers', name='Put IV', line=dict(color='#ffaa00')
        ))
        
        fig_iv.update_layout(
            template="plotly_dark",
            xaxis_title="Strike Price",
            yaxis_title="Implied Volatility",
            hovermode="x unified"
        )
        st.plotly_chart(fig_iv, use_container_width=True)
        
        # --- CHATBOT CONTEXT ---
        # Find peak OI strikes for the AI to analyze
        max_call_strike = calls.loc[calls['open_interest'].idxmax()]['strike_price'] if not calls.empty else "N/A"
        max_put_strike = puts.loc[puts['open_interest'].idxmax()]['strike_price'] if not puts.empty else "N/A"
        
        ctx = (f"Analyzing {ticker} options. Heaviest Call OI is at ${max_call_strike}. "
               f"Heaviest Put OI is at ${max_put_strike}.")
        run_sidebar_chatbot(context_data=ctx)

    else:
        st.error("Data missing required columns for charting. Check API response structure.")
else:
    st.warning("No options data returned. Check API keys or ticker format.")

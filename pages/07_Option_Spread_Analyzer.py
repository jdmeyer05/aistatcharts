import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from datetime import datetime
from src.data_engine import fetch_massive_data, fetch_options_chain, format_massive_ticker, render_data_source_footer
from src.chatbot import run_sidebar_chatbot

st.set_page_config(page_title="Option Spread Analyzer", layout="wide")

st.title("🦅 Option Spread & PnL Analyzer")
st.markdown("""
Model complex option strategies, visualize expiration PnL profiles, and analyze the 'Greeks' of your spread. 
*Note: This analyzer currently supports Stock and ETF tickers only.*
""")

# --- Sidebar: Strategy Selection ---
with st.sidebar:
    st.header("Strategy Configuration")
    with st.form("strategy_settings"):
        ticker_input = st.text_input("Underlying Ticker (Stocks/ETFs Only)", value="SPY")
        strategy_type = st.selectbox(
            "Strategy Type", 
            ["Bull Call Spread", "Bear Put Spread", "Iron Condor", "Straddle", "Strangle", "Custom"]
        )
        contract_qty = st.number_input("Number of Contracts", min_value=1, value=1)
        submit_fetch = st.form_submit_button("🔍 Fetch Chain & Analyze")

# --- Helper: PnL Calculations ---
def calculate_option_pnl(strike, premium, type, side, price_at_expiry):
    """Calculates PnL for a single leg."""
    mult = 1 if side == "Long" else -1
    if type == "call":
        payoff = np.maximum(price_at_expiry - strike, 0)
    else:
        payoff = np.maximum(strike - price_at_expiry, 0)
    return (payoff - premium) * mult * 100

# --- State Management for Speed ---
# Tracking the last ticker to prevent redundant API calls on page switch
if 'last_ticker' not in st.session_state:
    st.session_state.last_ticker = None

# Format the ticker
ticker = format_massive_ticker(ticker_input)

# VALIDATION: Strictly allow only stock/equity tickers for options
if ":" in ticker or "ERCOT" in ticker.upper():
    st.error("🚨 Invalid Ticker Type: Options analysis is only supported for Equities (Stocks/ETFs). Please enter a symbol like AAPL, TSLA, or SPY.")
    st.stop()

# Clear local cache if the user changes the ticker
if ticker != st.session_state.last_ticker:
    if 'options_df' in st.session_state:
        del st.session_state['options_df']
    if 'underlying_price' in st.session_state:
        del st.session_state['underlying_price']
    st.session_state.last_ticker = ticker

# --- Main Execution ---
if submit_fetch or 'options_df' in st.session_state:
    # 1. Fetch Underlying Price (Optimized via Session State)
    if 'underlying_price' not in st.session_state or submit_fetch:
        underlying_df = fetch_massive_data(ticker, 5)
        if underlying_df is not None and not underlying_df.empty:
            st.session_state['underlying_price'] = underlying_df['Close'].iloc[-1]
        else:
            st.error(f"Could not fetch underlying price for {ticker}. Please verify the stock ticker.")
            st.stop()
    
    # 2. Fetch Options Chain (Optimized via Session State)
    if 'options_df' not in st.session_state or submit_fetch:
        options_df = fetch_options_chain(ticker)
        if options_df is not None and not options_df.empty:
            st.session_state['options_df'] = options_df
        else:
            st.error(f"No options data available for {ticker}. Ensure it is a stock with an active options chain.")
            st.stop()
    
    current_price = st.session_state['underlying_price']
    options_df = st.session_state['options_df']

    # UI Layout
    col1, col2 = st.columns([1, 3])

    with col1:
        st.subheader("Leg Selection")
        # Filter for closest expiration or let user choose
        expirations = sorted(options_df['expiration_date'].unique())
        selected_exp = st.selectbox("Expiration Date", expirations)
        
        filtered_chain = options_df[options_df['expiration_date'] == selected_exp].sort_values('strike_price')
        
        # Strategy Builder UI
        legs = []
        if strategy_type == "Bull Call Spread":
            calls = filtered_chain[filtered_chain['contract_type'] == 'call']
            if not calls.empty:
                atm_call = calls.iloc[(calls['strike_price'] - current_price).abs().argsort()[:1]]
                otm_call = calls[calls['strike_price'] > current_price].iloc[2:3] if len(calls[calls['strike_price'] > current_price]) > 2 else calls.tail(1)
                
                legs.append({"type": "call", "side": "Long", "strike": atm_call['strike_price'].values[0], "premium": (atm_call['bid'].values[0] + atm_call['ask'].values[0])/2})
                legs.append({"type": "call", "side": "Short", "strike": otm_call['strike_price'].values[0], "premium": (otm_call['bid'].values[0] + otm_call['ask'].values[0])/2})

        elif strategy_type == "Iron Condor":
            calls = filtered_chain[filtered_chain['contract_type'] == 'call']
            puts = filtered_chain[filtered_chain['contract_type'] == 'put']
            
            if not calls.empty and not puts.empty:
                legs.append({"type": "put", "side": "Long", "strike": puts[puts['strike_price'] < current_price * 0.95]['strike_price'].max() or puts['strike_price'].min(), "premium": 0.5})
                legs.append({"type": "put", "side": "Short", "strike": puts[puts['strike_price'] < current_price * 0.98]['strike_price'].max() or puts['strike_price'].min(), "premium": 1.2})
                legs.append({"type": "call", "side": "Short", "strike": calls[calls['strike_price'] > current_price * 1.02]['strike_price'].min() or calls['strike_price'].max(), "premium": 1.1})
                legs.append({"type": "call", "side": "Long", "strike": calls[calls['strike_price'] > current_price * 1.05]['strike_price'].min() or calls['strike_price'].max(), "premium": 0.4})

        # Manual Adjustment
        st.info("Adjust Legs Below:")
        final_legs = []
        for i, leg in enumerate(legs):
            with st.expander(f"Leg {i+1}: {leg['side']} {leg['strike']} {leg['type'].upper()}"):
                l_side = st.selectbox(f"Side##{i}", ["Long", "Short"], index=0 if leg['side']=="Long" else 1)
                l_strike = st.number_input(f"Strike##{i}", value=float(leg['strike']))
                l_type = st.selectbox(f"Type##{i}", ["call", "put"], index=0 if leg['type']=="call" else 1)
                l_premium = st.number_input(f"Premium##{i}", value=float(leg['premium']))
                final_legs.append({"side": l_side, "strike": l_strike, "type": l_type, "premium": l_premium})

    with col2:
        # --- PnL Simulation ---
        st.subheader("Strategy PnL Profile")
        
        # Price Range for X-axis (±20% of current price)
        price_range = np.linspace(current_price * 0.8, current_price * 1.2, 100)
        
        total_pnl = np.zeros_like(price_range)
        net_premium = 0
        
        for leg in final_legs:
            leg_pnl = calculate_option_pnl(leg['strike'], leg['premium'], leg['type'], leg['side'], price_range)
            total_pnl += leg_pnl
            cost = leg['premium'] * 100 * (1 if leg['side'] == "Long" else -1)
            net_premium += cost
            
        total_pnl = total_pnl * contract_qty
        
        # Metrics
        m1, m2, m3 = st.columns(3)
        m1.metric("Current Price", f"${current_price:.2f}")
        m2.metric("Net Cost/Credit", f"${net_premium * contract_qty:,.2f}", delta_color="inverse")
        m3.metric("Max Profit", f"${np.max(total_pnl):,.2f}")

        # Plotly PnL Chart
        fig = go.Figure()
        
        # Zero line
        fig.add_hline(y=0, line_dash="dash", line_color="white", opacity=0.5)
        
        # PnL Curve
        fig.add_trace(go.Scatter(
            x=price_range, y=total_pnl,
            mode='lines',
            line=dict(color='#00d1ff', width=4),
            fill='tozeroy',
            fillcolor='rgba(0, 209, 255, 0.1)',
            name="PnL at Expiry"
        ))
        
        # Current Price Marker
        fig.add_vline(x=current_price, line_dash="dot", line_color="#ffaa00", annotation_text="Spot")

        fig.update_layout(
            template="plotly_dark",
            xaxis_title="Price at Expiration",
            yaxis_title="Profit / Loss ($)",
            hovermode='x unified',
            height=500
        )
        st.plotly_chart(fig, use_container_width=True)

        # Strategy Greeks
        st.subheader("Portfolio Snapshot")
        display_cols = ['strike_price', 'contract_type', 'bid', 'ask', 'implied_volatility', 'delta', 'theta']
        st.dataframe(filtered_chain[display_cols].head(15), use_container_width=True)

    # AI Context
    ctx = f"Option spread analysis for {ticker}. Strategy: {strategy_type}. Spot: {current_price}. Total PnL max: ${np.max(total_pnl):,.2f}."
    run_sidebar_chatbot(ctx)

else:
    st.info("Enter a stock ticker (e.g., SPY, AAPL) and click 'Fetch Chain' to begin analysis.")

render_data_source_footer()

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from scipy.stats import norm
from datetime import datetime
from src.data_engine import fetch_massive_data, fetch_options_chain, format_massive_ticker, render_data_source_footer
from src.chatbot import run_sidebar_chatbot

st.set_page_config(page_title="Advanced Spread Analyzer", layout="wide")

st.title("🦅 Advanced Spread & Risk Analyzer")
st.markdown("Model complex option strategies, calculate Probability of Profit (PoP), and aggregate Net Portfolio Greeks.")

# --- SIDEBAR CONFIGURATION ---
with st.sidebar:
    st.header("Strategy Configuration")
    with st.form("strategy_settings"):
        ticker_input = st.text_input("Underlying Ticker (Stocks/ETFs)", value="SPY")
        strategy_type = st.selectbox(
            "Strategy Type", 
            ["Bull Call Spread", "Bear Put Spread", "Iron Condor", "Straddle", "Strangle", "Custom"]
        )
        contract_qty = st.number_input("Number of Contracts", min_value=1, value=1)
        submit_fetch = st.form_submit_button("🔍 Fetch Chain & Analyze")

# --- HELPER FUNCTIONS ---
def calculate_option_pnl(strike, premium, type, side, price_at_expiry):
    """Calculates vectorized PnL for a single leg at expiration."""
    mult = 1 if side == "Long" else -1
    if type == "call":
        payoff = np.maximum(price_at_expiry - strike, 0)
    else:
        payoff = np.maximum(strike - price_at_expiry, 0)
    return (payoff - premium) * mult * 100

def estimate_probability_of_profit(current_price, breakevens, implied_vol, days_to_expiry, strategy):
    """
    Estimates Probability of Profit (PoP) using a log-normal distribution approximation.
    Assumes standard options pricing mechanics.
    """
    if days_to_expiry <= 0 or not implied_vol or implied_vol == 0:
        return 0.0
        
    time_sqrt = np.sqrt(days_to_expiry / 365.0)
    std_dev = current_price * implied_vol * time_sqrt
    
    pops = []
    for be in breakevens:
        # Z-score of the breakeven
        z = (be - current_price) / std_dev
        prob_below = norm.cdf(z)
        pops.append(prob_below)
        
    # Simplify PoP based on strategy type
    if strategy == "Bull Call Spread":
        return (1 - pops[0]) * 100 if pops else 0
    elif strategy == "Bear Put Spread":
        return pops[0] * 100 if pops else 0
    elif strategy == "Iron Condor":
        if len(pops) >= 2:
            return (pops[1] - pops[0]) * 100 # Prob between the two breakevens
        return 0
    else:
        return 50.0 # Fallback for complex/custom without clear single bounds

# --- STATE MANAGEMENT ---
ticker = format_massive_ticker(ticker_input)

if ":" in ticker or "ERCOT" in ticker.upper():
    st.error("🚨 Invalid Ticker Type: Options analysis is only supported for Equities (Stocks/ETFs).")
    st.stop()

if 'last_ticker_opt' not in st.session_state:
    st.session_state.last_ticker_opt = None

if ticker != st.session_state.last_ticker_opt:
    if 'options_df' in st.session_state: del st.session_state['options_df']
    if 'underlying_price' in st.session_state: del st.session_state['underlying_price']
    st.session_state.last_ticker_opt = ticker

# --- MAIN EXECUTION ---
if submit_fetch or 'options_df' in st.session_state:
    
    if 'underlying_price' not in st.session_state or submit_fetch:
        underlying_df = fetch_massive_data(ticker, 5)
        if underlying_df is not None and not underlying_df.empty:
            st.session_state['underlying_price'] = underlying_df['Close'].iloc[-1]
        else:
            st.error(f"Could not fetch underlying price for {ticker}.")
            st.stop()
            
    if 'options_df' not in st.session_state or submit_fetch:
        options_df = fetch_options_chain(ticker)
        if options_df is not None and not options_df.empty:
            st.session_state['options_df'] = options_df
        else:
            st.error(f"No options data available for {ticker}.")
            st.stop()
            
    current_price = st.session_state['underlying_price']
    df = st.session_state['options_df']

    # --- UI LAYOUT ---
    col_setup, col_analysis = st.columns([1, 2.5])

    with col_setup:
        st.subheader("1. Setup & Legs")
        
        all_expirations = sorted(df['expiration_date'].dropna().unique())
        if not all_expirations:
            st.error("Chain missing valid expiration dates.")
            st.stop()
            
        primary_exp = st.selectbox("Target Expiration Date", all_expirations, index=0)
        
        # Calculate DTE
        dte = (pd.to_datetime(primary_exp) - pd.Timestamp.now()).days
        dte = max(1, dte) # Prevent division by zero
        
        chain = df[df['expiration_date'] == primary_exp].sort_values('strike_price')
        avg_iv = chain['implied_volatility'].mean()
        
        # Strategy Builder Logic
        legs = []
        if strategy_type == "Bull Call Spread":
            calls = chain[chain['contract_type'] == 'call']
            if not calls.empty:
                atm = calls.iloc[(calls['strike_price'] - current_price).abs().argsort()[:1]]
                otm = calls[calls['strike_price'] > current_price].iloc[1:2] if len(calls[calls['strike_price'] > current_price]) > 1 else calls.tail(1)
                
                legs.append({"type": "call", "side": "Long", "strike": atm['strike_price'].values[0], "premium": (atm['bid'].values[0] + atm['ask'].values[0])/2})
                legs.append({"type": "call", "side": "Short", "strike": otm['strike_price'].values[0], "premium": (otm['bid'].values[0] + otm['ask'].values[0])/2})

        elif strategy_type == "Iron Condor":
            calls = chain[chain['contract_type'] == 'call']
            puts = chain[chain['contract_type'] == 'put']
            
            if not calls.empty and not puts.empty:
                legs.append({"type": "put", "side": "Long", "strike": puts[puts['strike_price'] < current_price * 0.95]['strike_price'].max() or puts['strike_price'].min(), "premium": 0.5})
                legs.append({"type": "put", "side": "Short", "strike": puts[puts['strike_price'] < current_price * 0.98]['strike_price'].max() or puts['strike_price'].min(), "premium": 1.2})
                legs.append({"type": "call", "side": "Short", "strike": calls[calls['strike_price'] > current_price * 1.02]['strike_price'].min() or calls['strike_price'].max(), "premium": 1.1})
                legs.append({"type": "call", "side": "Long", "strike": calls[calls['strike_price'] > current_price * 1.05]['strike_price'].min() or calls['strike_price'].max(), "premium": 0.4})
        else:
             # Default fallback
             legs.append({"type": "call", "side": "Long", "strike": current_price, "premium": 1.0})

        st.info("Adjust Strategy Legs:")
        final_legs = []
        for i, leg in enumerate(legs):
            with st.expander(f"Leg {i+1}: {leg['side']} {leg['strike']} {leg['type'].upper()}", expanded=True):
                c1, c2 = st.columns(2)
                l_side = c1.selectbox(f"Side##{i}", ["Long", "Short"], index=0 if leg['side']=="Long" else 1)
                l_type = c2.selectbox(f"Type##{i}", ["call", "put"], index=0 if leg['type']=="call" else 1)
                l_strike = c1.number_input(f"Strike##{i}", value=float(leg['strike']), step=1.0)
                l_premium = c2.number_input(f"Price##{i}", value=float(leg['premium']), step=0.05)
                final_legs.append({"side": l_side, "strike": l_strike, "type": l_type, "premium": l_premium})

    with col_analysis:
        st.subheader("2. Risk & Payoff Analysis")
        
        # --- PnL & Risk Math ---
        price_range = np.linspace(current_price * 0.85, current_price * 1.15, 200)
        total_pnl = np.zeros_like(price_range)
        net_premium = 0
        net_delta = 0.0
        net_theta = 0.0
        
        for leg in final_legs:
            # 1. PnL
            leg_pnl = calculate_option_pnl(leg['strike'], leg['premium'], leg['type'], leg['side'], price_range)
            total_pnl += leg_pnl
            
            # Cost
            mult = 1 if leg['side'] == "Long" else -1
            net_premium += (leg['premium'] * 100 * mult)
            
            # 2. Greeks Extraction
            # Find the closest matching contract in the chain to estimate Greeks
            match = chain[(chain['strike_price'] == leg['strike']) & (chain['contract_type'] == leg['type'])]
            if not match.empty:
                d = match['delta'].values[0] or 0.0
                t = match['theta'].values[0] or 0.0
                net_delta += (d * 100 * mult)
                net_theta += (t * 100 * mult)
            
        total_pnl = total_pnl * contract_qty
        net_premium = net_premium * contract_qty
        net_delta = net_delta * contract_qty
        net_theta = net_theta * contract_qty
        
        max_profit = np.max(total_pnl)
        max_loss = np.min(total_pnl)
        
        # Approximate Breakevens (Where PnL crosses 0)
        zero_crossings = np.where(np.diff(np.sign(total_pnl)))[0]
        breakevens = [price_range[i] for i in zero_crossings]
        
        pop = estimate_probability_of_profit(current_price, breakevens, avg_iv, dte, strategy_type)

        # --- Metrics Row ---
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Net Cost / Credit", f"${-net_premium:,.2f}", "Credit" if net_premium < 0 else "Debit", delta_color="off")
        m2.metric("Max Profit", f"${max_profit:,.2f}")
        m3.metric("Max Risk (Loss)", f"${max_loss:,.2f}")
        m4.metric("Prob. of Profit (PoP)", f"{pop:.1f}%")
        
        st.divider()
        g1, g2, g3 = st.columns(3)
        g1.metric("Underlying Spot", f"${current_price:.2f}")
        g2.metric("Net Portfolio Delta", f"{net_delta:.2f}", help="Directional exposure of the total spread.")
        g3.metric("Net Portfolio Theta", f"${net_theta:.2f}/day", help="Time decay impact per day.")

        # --- Plotly PnL Chart ---
        fig = go.Figure()
        
        # Profit Zone (Green Fill)
        fig.add_trace(go.Scatter(
            x=price_range, y=np.where(total_pnl >= 0, total_pnl, 0),
            fill='tozeroy', fillcolor='rgba(0, 255, 150, 0.2)', line=dict(color='rgba(255,255,255,0)'), name='Profit Zone', hoverinfo='skip'
        ))
        # Loss Zone (Red Fill)
        fig.add_trace(go.Scatter(
            x=price_range, y=np.where(total_pnl < 0, total_pnl, 0),
            fill='tozeroy', fillcolor='rgba(255, 75, 75, 0.2)', line=dict(color='rgba(255,255,255,0)'), name='Loss Zone', hoverinfo='skip'
        ))
        
        # Main PnL Line
        fig.add_trace(go.Scatter(x=price_range, y=total_pnl, mode='lines', line=dict(color='#00d1ff', width=3), name="PnL at Expiry"))
        
        # Markers
        fig.add_hline(y=0, line_dash="solid", line_color="white", opacity=0.5)
        fig.add_vline(x=current_price, line_dash="dot", line_color="#ffaa00", annotation_text="Spot")
        
        for be in breakevens:
            fig.add_vline(x=be, line_dash="dash", line_color="#ad7fff", annotation_text="Breakeven")

        fig.update_layout(
            template="plotly_dark", height=450, margin=dict(t=20, b=0, l=0, r=0),
            xaxis_title=f"Price at Expiration ({primary_exp} - {dte} DTE)",
            yaxis_title="Profit / Loss ($)", hovermode='x unified'
        )
        st.plotly_chart(fig, use_container_width=True)

    # AI Context
    ctx = (f"Spread Analysis for {ticker} ({strategy_type}). Spot: ${current_price:.2f}. "
           f"Net Cost: ${net_premium:.2f}. Max Profit: ${max_profit:.2f}. PoP: {pop:.1f}%. Net Delta: {net_delta:.2f}.")
    run_sidebar_chatbot(ctx)

else:
    st.info("Enter an Equity ticker and click 'Fetch Chain' to begin advanced spread analysis.")

render_data_source_footer()

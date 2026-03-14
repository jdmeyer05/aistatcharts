import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from scipy.stats import norm
from datetime import datetime
from src.data_engine import get_expiration_dates, fetch_massive_data, fetch_options_chain, format_massive_ticker, render_data_source_footer
from src.chatbot import run_sidebar_chatbot
from src.auth import check_auth

st.set_page_config(page_title="Advanced Spread Analyzer", layout="wide")
check_auth()

st.title("🦅 Advanced Spread & Risk Analyzer")
st.markdown("Model complex option strategies, calculate Probability of Profit (PoP), and aggregate Net Portfolio Greeks.")

# --- SIDEBAR CONFIGURATION ---
with st.sidebar:
    st.header("Strategy Configuration")
    raw_ticker = st.text_input("Underlying Ticker (Stocks/ETFs)", value="SPY")
    ticker = format_massive_ticker(raw_ticker)
    
    expirations = get_expiration_dates(ticker)
    
    if expirations:
        selected_exp = st.selectbox("🎯 Target Expiration Date", expirations)
        strategy_type = st.selectbox("Strategy Type", ["Bull Call Spread", "Bear Put Spread", "Iron Condor", "Straddle", "Strangle", "Custom"])
        contract_qty = st.number_input("Number of Contracts", min_value=1, value=1)
        submit_fetch = st.button("🔍 Fetch & Analyze", type="primary", use_container_width=True)
    else:
        st.warning("No expirations found.")
        submit_fetch = False
        selected_exp = None

# --- HELPER FUNCTIONS ---
def calculate_option_pnl(strike, premium, type, side, price_at_expiry):
    mult = 1 if side == "Long" else -1
    if type == "call": payoff = np.maximum(price_at_expiry - strike, 0)
    else: payoff = np.maximum(strike - price_at_expiry, 0)
    return (payoff - premium) * mult * 100

def estimate_probability_of_profit(current_price, breakevens, implied_vol, days_to_expiry, strategy):
    if days_to_expiry <= 0 or not implied_vol or implied_vol == 0: return 0.0
    time_sqrt = np.sqrt(days_to_expiry / 365.0)
    std_dev = current_price * implied_vol * time_sqrt
    pops = [norm.cdf((be - current_price) / std_dev) for be in breakevens]
    
    if strategy == "Bull Call Spread": return (1 - pops[0]) * 100 if pops else 0
    elif strategy == "Bear Put Spread": return pops[0] * 100 if pops else 0
    elif strategy == "Iron Condor": return (pops[1] - pops[0]) * 100 if len(pops) >= 2 else 0
    else: return 50.0

# --- MAIN EXECUTION ---
if submit_fetch and selected_exp:
    with st.spinner("Crunching legs..."):
        underlying_df = fetch_massive_data(ticker, 5)
        current_price = underlying_df['Close'].iloc[-1] if underlying_df is not None else 100
        chain = fetch_options_chain(ticker, selected_exp)

        col_setup, col_analysis = st.columns([1, 2.5])

        with col_setup:
            st.subheader(f"1. Setup ({selected_exp})")
            dte = max(1, (pd.to_datetime(selected_exp) - pd.Timestamp.now()).days)
            avg_iv = chain['implied_volatility'].mean() if chain is not None else 0.2
            
            legs = []
            if chain is not None:
                if strategy_type == "Bull Call Spread":
                    calls = chain[chain['contract_type'] == 'call']
                    if not calls.empty:
                        atm = calls.iloc[(calls['strike_price'] - current_price).abs().argsort()[:1]]
                        otm = calls[calls['strike_price'] > current_price].iloc[1:2] if len(calls[calls['strike_price'] > current_price]) > 1 else calls.tail(1)
                        legs.append({"type": "call", "side": "Long", "strike": atm['strike_price'].values[0], "premium": (atm['bid'].values[0] + atm['ask'].values[0])/2})
                        legs.append({"type": "call", "side": "Short", "strike": otm['strike_price'].values[0], "premium": (otm['bid'].values[0] + otm['ask'].values[0])/2})
                else: legs.append({"type": "call", "side": "Long", "strike": current_price, "premium": 1.0})
            
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
            price_range = np.linspace(current_price * 0.85, current_price * 1.15, 200)
            total_pnl = np.zeros_like(price_range)
            net_premium, net_delta = 0, 0.0
            
            for leg in final_legs:
                leg_pnl = calculate_option_pnl(leg['strike'], leg['premium'], leg['type'], leg['side'], price_range)
                total_pnl += leg_pnl
                mult = 1 if leg['side'] == "Long" else -1
                net_premium += (leg['premium'] * 100 * mult)
                
                if chain is not None:
                    match = chain[(chain['strike_price'] == leg['strike']) & (chain['contract_type'] == leg['type'])]
                    if not match.empty: net_delta += ((match['delta'].values[0] or 0.0) * 100 * mult)
                
            total_pnl *= contract_qty
            net_premium *= contract_qty
            net_delta *= contract_qty
            
            max_profit, max_loss = np.max(total_pnl), np.min(total_pnl)
            breakevens = [price_range[i] for i in np.where(np.diff(np.sign(total_pnl)))[0]]
            pop = estimate_probability_of_profit(current_price, breakevens, avg_iv, dte, strategy_type)

            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Net Cost / Credit", f"${-net_premium:,.2f}")
            m2.metric("Max Profit", f"${max_profit:,.2f}")
            m3.metric("Max Risk", f"${max_loss:,.2f}")
            m4.metric("Prob. of Profit", f"{pop:.1f}%")
            
            st.divider()
            g1, g2 = st.columns(2)
            g1.metric("Underlying Spot", f"${current_price:.2f}")
            g2.metric("Net Portfolio Delta", f"{net_delta:.2f}")

            fig = go.Figure()
            fig.add_trace(go.Scatter(x=price_range, y=np.where(total_pnl >= 0, total_pnl, 0), fill='tozeroy', fillcolor='rgba(0, 255, 150, 0.2)', name='Profit'))
            fig.add_trace(go.Scatter(x=price_range, y=np.where(total_pnl < 0, total_pnl, 0), fill='tozeroy', fillcolor='rgba(255, 75, 75, 0.2)', name='Loss'))
            fig.add_trace(go.Scatter(x=price_range, y=total_pnl, mode='lines', line=dict(color='#00d1ff', width=3), name="PnL"))
            fig.add_hline(y=0, line_dash="solid", line_color="white", opacity=0.5)
            fig.add_vline(x=current_price, line_dash="dot", line_color="#ffaa00")
            for be in breakevens: fig.add_vline(x=be, line_dash="dash", line_color="#ad7fff")

            fig.update_layout(template="plotly_dark", height=450, xaxis_title=f"Price at Expiration", yaxis_title="PnL ($)")
            st.plotly_chart(fig, use_container_width=True)

        run_sidebar_chatbot(f"Spread Analysis for {ticker}. Cost: ${net_premium:.2f}. PoP: {pop:.1f}%.")

render_data_source_footer()

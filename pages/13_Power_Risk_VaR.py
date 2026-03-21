import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from src.data_engine import fetch_massive_data, format_massive_ticker
from src.chatbot import run_sidebar_chatbot

from src.layout import setup_page, error_boundary
setup_page("13_Power_Risk_VaR")

st.title("🛡️ Portfolio Risk & VaR Engine")

_c1, _c2, _c3, _c4 = st.columns([3, 2, 2, 1])
with _c1:
    raw_tickers = st.text_input("Portfolio Tickers (comma separated)", "TLT,USO,QQQ")
with _c2:
    portfolio_value = st.number_input("Total Portfolio Value ($)", value=100000, step=10000)
with _c3:
    lookback = st.slider("Historical Lookback (Days)", 90, 1000, 365)
with _c4:
    confidence_level = st.selectbox("Confidence Level", [0.95, 0.99])

# Parse tickers
ticker_list = [t.strip() for t in raw_tickers.split(",")]

# --- Fetch & Align Data ---
st.info("Aggregating portfolio data...")
all_data = {}
failed_tickers = []
for t in ticker_list:
    formatted_t = format_massive_ticker(t)
    df = fetch_massive_data(formatted_t, lookback)
    if df is not None:
        all_data[t] = df['Close']
    else:
        failed_tickers.append(t)

if failed_tickers:
    st.warning(f"Could not load data for: {', '.join(failed_tickers)}")

if all_data:
    portfolio_df = pd.DataFrame(all_data).dropna()
    
    # Calculate daily returns
    daily_returns = portfolio_df.pct_change().dropna()
    
    # Assume equal weighting for simplicity
    weights = np.full(len(ticker_list), 1.0 / len(ticker_list))
    portfolio_returns = daily_returns.dot(weights)
    
    # --- VaR & CVaR MATHEMATICS ---
    # We look at the historical worst days. If confidence is 95%, we find the 5th percentile return.
    percentile = (1 - confidence_level) * 100
    var_percent = np.percentile(portfolio_returns, percentile)
    var_dollar = portfolio_value * var_percent

    # Conditional VaR (Expected Shortfall) — average loss beyond VaR
    tail_returns = portfolio_returns[portfolio_returns <= var_percent]
    cvar_percent = tail_returns.mean() if len(tail_returns) > 0 else var_percent
    cvar_dollar = portfolio_value * cvar_percent
    
    # --- CHART: RETURNS DISTRIBUTION ---
    st.subheader("Daily Returns Distribution")
    
    fig_hist = px.histogram(
        portfolio_returns, 
        nbins=50, 
        title="Historical Portfolio Returns",
        color_discrete_sequence=['#00d1ff']
    )
    
    # Draw the VaR threshold line
    fig_hist.add_vline(
        x=var_percent, 
        line_dash="dash", 
        line_color="red", 
        annotation_text=f"{int(confidence_level*100)}% VaR",
        annotation_position="top left"
    )
    
    fig_hist.update_layout(template="plotly_dark", showlegend=False, xaxis_title="Daily Return", yaxis_title="Frequency")
    st.plotly_chart(fig_hist, use_container_width=True)
    
    # --- METRICS ---
    st.divider()
    c1, c2, c3 = st.columns(3)
    c1.metric(f"1-Day {int(confidence_level*100)}% VaR (%)", f"{var_percent*100:.2f}%")
    c2.metric(f"1-Day {int(confidence_level*100)}% VaR ($)", f"${abs(var_dollar):,.2f}", delta="Max Expected Loss", delta_color="inverse")
    c3.metric(f"1-Day {int(confidence_level*100)}% CVaR ($)", f"${abs(cvar_dollar):,.2f}", delta="Avg Loss Beyond VaR", delta_color="inverse")

    st.caption(f"**VaR:** There is a {int(confidence_level*100)}% probability that the portfolio will not lose more than **${abs(var_dollar):,.2f}** in a single trading day. "
               f"**CVaR:** If losses exceed VaR, the average expected loss is **${abs(cvar_dollar):,.2f}**.")

    # Chatbot Context
    ctx = f"The 1-Day {int(confidence_level*100)}% VaR for a ${portfolio_value} portfolio containing {raw_tickers} is ${abs(var_dollar):.2f}."
    run_sidebar_chatbot(context_data=ctx)
else:
    st.error("Could not load data for the requested tickers.")

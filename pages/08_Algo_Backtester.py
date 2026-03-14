import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from src.data_engine import fetch_massive_data, format_massive_ticker
from src.chatbot import run_sidebar_chatbot

st.title("⚙️ Algorithmic Trade Backtester")

with st.sidebar:
    st.header("Strategy Builder")
    raw_ticker = st.text_input("Test Ticker", value="SPY").upper()
    lookback = st.slider("Test Period (Days)", 365, 1825, 1095)
    
    st.subheader("Moving Average Crossover")
    fast_ma = st.number_input("Fast MA Length", min_value=5, max_value=50, value=20)
    slow_ma = st.number_input("Slow MA Length", min_value=20, max_value=200, value=50)

ticker = format_massive_ticker(raw_ticker)
data = fetch_massive_data(ticker, lookback)

if data is not None and not data.empty:
    df = pd.DataFrame(data['Close'])
    
    # --- STRATEGY ENGINE ---
    df['Fast_MA'] = df['Close'].rolling(window=fast_ma).mean()
    df['Slow_MA'] = df['Close'].rolling(window=slow_ma).mean()
    
    # Signal: 1 if Fast > Slow (Buy/Hold), 0 if Fast < Slow (Sell/Cash)
    df['Signal'] = np.where(df['Fast_MA'] > df['Slow_MA'], 1, 0)
    
    # Calculate Returns
    df['Daily_Return'] = df['Close'].pct_change()
    # Strategy return is the daily return multiplied by the signal from the DAY BEFORE
    df['Strategy_Return'] = df['Signal'].shift(1) * df['Daily_Return']
    
    # Cumulative Equity Curves
    df['Buy_Hold_Equity'] = (1 + df['Daily_Return']).cumprod() * 100
    df['Strategy_Equity'] = (1 + df['Strategy_Return']).cumprod() * 100
    
    # --- PERFORMANCE TEARSHEET METRICS ---
    # Total Return
    strat_total_ret = (df['Strategy_Equity'].iloc[-1] / 100) - 1
    bh_total_ret = (df['Buy_Hold_Equity'].iloc[-1] / 100) - 1
    
    # Max Drawdown
    rolling_max = df['Strategy_Equity'].cummax()
    drawdown = (df['Strategy_Equity'] - rolling_max) / rolling_max
    max_dd = drawdown.min()
    
    # Sharpe Ratio (Assuming 0% risk-free rate for simplicity)
    strat_mean = df['Strategy_Return'].mean() * 252
    strat_std = df['Strategy_Return'].std() * np.sqrt(252)
    sharpe_ratio = strat_mean / strat_std if strat_std != 0 else 0
    
    # Win Rate (Percentage of days the strategy made money when active)
    active_days = df[df['Signal'].shift(1) == 1]
    win_rate = len(active_days[active_days['Daily_Return'] > 0]) / len(active_days) if len(active_days) > 0 else 0

    # --- RENDER DASHBOARD ---
    st.subheader(f"Equity Curve: Strategy vs Buy & Hold ({raw_ticker})")
    
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df.index, y=df['Buy_Hold_Equity'], name="Buy & Hold", line=dict(color='gray', dash='dot')))
    fig.add_trace(go.Scatter(x=df.index, y=df['Strategy_Equity'], name="Algo Strategy", line=dict(color='#00d1ff', width=3)))
    
    fig.update_layout(template="plotly_dark", yaxis_title="Portfolio Value (Starts at $100)", hovermode="x unified")
    st.plotly_chart(fig, use_container_width=True)

    # Performance Tearsheet
    st.subheader("📊 Performance Tearsheet")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Return", f"{strat_total_ret*100:.2f}%", f"vs B&H: {bh_total_ret*100:.2f}%")
    c2.metric("Sharpe Ratio", f"{sharpe_ratio:.2f}")
    c3.metric("Max Drawdown", f"{max_dd*100:.2f}%", delta_color="inverse")
    c4.metric("Win Rate", f"{win_rate*100:.1f}%")

    # Chatbot Context
    ctx = (f"Backtest results for {raw_ticker} using a {fast_ma}/{slow_ma} MA Crossover: "
           f"Total Return {strat_total_ret*100:.2f}%, Sharpe Ratio {sharpe_ratio:.2f}, Max Drawdown {max_dd*100:.2f}%. "
           f"Analyze if this strategy is viable or if it suffers from overfitting.")
    run_sidebar_chatbot(context_data=ctx)

else:
    st.warning("No data found to run the backtest. Check your data engine connection.")

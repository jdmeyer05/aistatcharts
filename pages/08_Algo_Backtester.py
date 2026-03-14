import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from src.data_engine import fetch_massive_data, format_massive_ticker, render_data_source_footer
from src.chatbot import run_sidebar_chatbot

st.set_page_config(page_title="Algo Backtester", layout="wide")

st.title("⚙️ Algorithmic Strategy Backtester")
st.markdown("Test quantitative trading strategies with institutional-grade risk metrics, drawdown analysis, and trade logging.")

# --- SIDEBAR CONFIGURATION ---
with st.sidebar:
    st.header("Backtest Parameters")
    with st.form("backtest_settings"):
        raw_ticker = st.text_input("Ticker", value="SPY")
        lookback = st.slider("Lookback (Days)", 365, 3650, 1000, step=365)
        
        st.divider()
        strategy = st.selectbox("Trading Strategy", ["EMA Crossover", "RSI Mean Reversion"])
        
        # Strategy specific params
        if strategy == "EMA Crossover":
            fast_window = st.number_input("Fast EMA", value=20)
            slow_window = st.number_input("Slow EMA", value=50)
        else:
            rsi_window = st.number_input("RSI Period", value=14)
            rsi_ob = st.number_input("Overbought Threshold", value=70)
            rsi_os = st.number_input("Oversold Threshold", value=30)
            
        st.divider()
        st.caption("Friction & Risk")
        commission = st.number_input("Commission per Trade (%)", value=0.05, step=0.01) / 100.0
        
        submit = st.form_submit_button("🚀 Run Backtest")

# --- BACKTEST ENGINE ---
ticker = format_massive_ticker(raw_ticker)

if submit or 'bt_data' not in st.session_state or st.session_state.get('bt_ticker') != ticker:
    with st.spinner("Fetching data and running backtest..."):
        df = fetch_massive_data(ticker, lookback)
        if df is None or df.empty:
            st.error(f"Failed to fetch data for {ticker}.")
            st.stop()
            
        df['Daily_Return'] = df['Close'].pct_change()
        
        # 1. GENERATE SIGNALS
        if strategy == "EMA Crossover":
            df['Fast_MA'] = df['Close'].ewm(span=fast_window, adjust=False).mean()
            df['Slow_MA'] = df['Close'].ewm(span=slow_window, adjust=False).mean()
            # 1 if Fast > Slow (Bullish), else 0
            df['Signal'] = np.where(df['Fast_MA'] > df['Slow_MA'], 1.0, 0.0)
            
        elif strategy == "RSI Mean Reversion":
            delta = df['Close'].diff()
            gain = delta.where(delta > 0, 0.0).ewm(alpha=1/rsi_window, adjust=False).mean()
            loss = (-delta.where(delta < 0, 0.0)).ewm(alpha=1/rsi_window, adjust=False).mean()
            rs = gain / loss
            df['RSI'] = 100 - (100 / (1 + rs))
            
            # 1 if RSI < Oversold, 0 if RSI > Overbought, hold previous state otherwise
            conditions = [
                (df['RSI'] < rsi_os),
                (df['RSI'] > rsi_ob)
            ]
            choices = [1.0, 0.0]
            df['Raw_Signal'] = np.select(conditions, choices, default=np.nan)
            df['Signal'] = df['Raw_Signal'].ffill().fillna(0.0)

        # 2. CALCULATE POSITIONS & RETURNS
        # Shift signal by 1 so we trade at the close of the signal day, realizing return on the next day
        df['Position'] = df['Signal'].shift(1).fillna(0)
        
        # Calculate trades to apply commission
        df['Trade'] = df['Position'].diff().fillna(0).abs()
        
        # Strategy Return = (Daily Return * Position) - (Commission * Trade)
        df['Strat_Return'] = (df['Daily_Return'] * df['Position']) - (commission * df['Trade'])
        
        # Cumulative Equity Curves
        df['BnH_Equity'] = (1 + df['Daily_Return'].fillna(0)).cumprod()
        df['Strat_Equity'] = (1 + df['Strat_Return'].fillna(0)).cumprod()
        
        # Drawdown calculations
        df['Strat_Peak'] = df['Strat_Equity'].cummax()
        df['Drawdown'] = (df['Strat_Equity'] - df['Strat_Peak']) / df['Strat_Peak']
        
        st.session_state.bt_data = df
        st.session_state.bt_ticker = ticker
        st.session_state.bt_strategy = strategy

# --- RENDER DASHBOARD ---
if 'bt_data' in st.session_state:
    df = st.session_state.bt_data
    
    # Calculate Key Metrics
    total_bnh = (df['BnH_Equity'].iloc[-1] - 1) * 100
    total_strat = (df['Strat_Equity'].iloc[-1] - 1) * 100
    
    # Annualized Volatility and Sharpe (assuming 252 trading days)
    strat_vol = df['Strat_Return'].std() * np.sqrt(252)
    sharpe_ratio = (df['Strat_Return'].mean() * 252) / strat_vol if strat_vol > 0 else 0
    max_drawdown = df['Drawdown'].min() * 100
    
    # Count trades
    total_trades = int(df['Trade'].sum())
    
    # Top Metrics Row
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Strategy Return", f"{total_strat:.2f}%", f"vs B&H: {total_bnh:.2f}%")
    c2.metric("Sharpe Ratio", f"{sharpe_ratio:.2f}")
    c3.metric("Max Drawdown", f"{max_drawdown:.2f}%")
    c4.metric("Total Trades", f"{total_trades}")
    
    # --- VISUALIZATIONS ---
    st.subheader("Performance & Drawdown Analysis")
    
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, 
                        vertical_spacing=0.05, row_heights=[0.7, 0.3])
    
    # Top Plot: Equity Curves
    fig.add_trace(go.Scatter(x=df.index, y=df['BnH_Equity'], name="Buy & Hold", line=dict(color='gray', width=1.5)), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['Strat_Equity'], name="Strategy Equity", line=dict(color='#00ff96', width=2.5)), row=1, col=1)
    
    # Bottom Plot: Underwater Drawdown
    fig.add_trace(go.Scatter(x=df.index, y=df['Drawdown'], name="Drawdown", fill='tozeroy', line=dict(color='#ff4b4b', width=1)), row=2, col=1)
    
    fig.update_layout(template="plotly_dark", height=500, hovermode='x unified', margin=dict(t=20, b=0, l=0, r=0))
    fig.update_yaxes(title_text="Cumulative Return", row=1, col=1, tickformat=".2f")
    fig.update_yaxes(title_text="Drawdown", row=2, col=1, tickformat=".1%")
    st.plotly_chart(fig, use_container_width=True)
    
    # --- TRADE LOG ---
    st.subheader("Recent Trade Log & Signals")
    
    # Filter for days where a trade occurred (Position changed)
    trades_df = df[df['Trade'] > 0].copy()
    trades_df['Action'] = np.where(trades_df['Position'] > 0, "BUY", "SELL / CLOSE")
    
    display_cols = ['Close', 'Action', 'Strat_Return', 'Strat_Equity']
    st.dataframe(trades_df[display_cols].tail(15).sort_index(ascending=False), use_container_width=True)

    # AI Analyst Context
    ctx = f"Backtest for {st.session_state.bt_ticker} using {st.session_state.bt_strategy}. Total Return: {total_strat:.2f}%. Sharpe: {sharpe_ratio:.2f}. Max Drawdown: {max_drawdown:.2f}%. Total Trades: {total_trades}."
    run_sidebar_chatbot(ctx)

render_data_source_footer()

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from src.data_engine import fetch_massive_data, format_massive_ticker
from src.auth import check_auth

st.set_page_config(page_title="Algo Backtester", layout="wide")
check_auth() # The firewall

st.title("⚡ Vectorized Algo Backtester")
st.markdown("Instantly test 10+ quantitative trading strategies using pure vectorized Pandas logic.")

# --- SIDEBAR CONFIGURATION ---
with st.sidebar:
    st.header("Backtest Parameters")
    raw_ticker = st.text_input("Ticker", value="SPY")
    lookback = st.slider("Historical Data (Days)", 252, 2520, 1260, step=252)
    
    strategy = st.selectbox("Algorithmic Strategy", [
        "1. Fast SMA Crossover (10/21)",
        "2. Golden/Death Cross (50/200)",
        "3. Fast EMA Crossover (9/21)",
        "4. MACD Momentum",
        "5. RSI Mean Reversion (14-Day)",
        "6. Bollinger Band Mean Reversion",
        "7. Bollinger Band Breakout",
        "8. Donchian Channel Breakout (20-Day)",
        "9. Z-Score Mean Reversion",
        "10. Simple Momentum (Close > SMA 20)"
    ])
    
    run_test = st.button("🚀 Run Backtest", type="primary", use_container_width=True)

ticker = format_massive_ticker(raw_ticker)

# --- MATH & STRATEGY ENGINE ---
def calculate_rsi(data, periods=14):
    delta = data.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=periods).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=periods).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def run_strategy(df, strat_name):
    """Calculates indicators and returns a vectorized position series (1 for Long, -1 for Short, 0 for Cash)"""
    df = df.copy()
    c = df['Close']
    df['Position'] = 0
    
    if strat_name == "1. Fast SMA Crossover (10/21)":
        df['Fast'] = c.rolling(10).mean()
        df['Slow'] = c.rolling(21).mean()
        df['Position'] = np.where(df['Fast'] > df['Slow'], 1, -1)
        
    elif strat_name == "2. Golden/Death Cross (50/200)":
        df['Fast'] = c.rolling(50).mean()
        df['Slow'] = c.rolling(200).mean()
        df['Position'] = np.where(df['Fast'] > df['Slow'], 1, -1)
        
    elif strat_name == "3. Fast EMA Crossover (9/21)":
        df['Fast'] = c.ewm(span=9, adjust=False).mean()
        df['Slow'] = c.ewm(span=21, adjust=False).mean()
        df['Position'] = np.where(df['Fast'] > df['Slow'], 1, -1)
        
    elif strat_name == "4. MACD Momentum":
        ema12 = c.ewm(span=12, adjust=False).mean()
        ema26 = c.ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        signal = macd.ewm(span=9, adjust=False).mean()
        df['Position'] = np.where(macd > signal, 1, -1)
        
    elif strat_name == "5. RSI Mean Reversion (14-Day)":
        df['RSI'] = calculate_rsi(c, 14)
        # Buy < 30, Sell > 70. Hold position until opposite signal hits.
        df.loc[df['RSI'] < 30, 'Signal'] = 1
        df.loc[df['RSI'] > 70, 'Signal'] = -1
        df['Position'] = df['Signal'].ffill().fillna(0) # Forward fill states
        
    elif strat_name == "6. Bollinger Band Mean Reversion":
        sma20 = c.rolling(20).mean()
        std20 = c.rolling(20).std()
        upper = sma20 + (std20 * 2)
        lower = sma20 - (std20 * 2)
        # Buy at lower band, Short at upper band
        df.loc[c < lower, 'Signal'] = 1
        df.loc[c > upper, 'Signal'] = -1
        df['Position'] = df['Signal'].ffill().fillna(0)
        
    elif strat_name == "7. Bollinger Band Breakout":
        sma20 = c.rolling(20).mean()
        std20 = c.rolling(20).std()
        upper = sma20 + (std20 * 2)
        lower = sma20 - (std20 * 2)
        # Buy breakout above upper, Short breakdown below lower
        df.loc[c > upper, 'Signal'] = 1
        df.loc[c < lower, 'Signal'] = -1
        df['Position'] = df['Signal'].ffill().fillna(0)
        
    elif strat_name == "8. Donchian Channel Breakout (20-Day)":
        df['Upper'] = c.rolling(20).max().shift(1)
        df['Lower'] = c.rolling(20).min().shift(1)
        df.loc[c > df['Upper'], 'Signal'] = 1
        df.loc[c < df['Lower'], 'Signal'] = -1
        df['Position'] = df['Signal'].ffill().fillna(0)
        
    elif strat_name == "9. Z-Score Mean Reversion":
        df['Z'] = (c - c.rolling(20).mean()) / c.rolling(20).std()
        # Buy when Z < -2 (Oversold), Sell when Z > 2 (Overbought)
        df.loc[df['Z'] < -2, 'Signal'] = 1
        df.loc[df['Z'] > 2, 'Signal'] = -1
        df['Position'] = df['Signal'].ffill().fillna(0)
        
    elif strat_name == "10. Simple Momentum (Close > SMA 20)":
        sma20 = c.rolling(20).mean()
        df['Position'] = np.where(c > sma20, 1, -1)

    return df['Position']

# --- EXECUTION ---
if run_test or 'bt_data' not in st.session_state:
    with st.spinner(f"Fetching data and vectorizing {strategy} logic..."):
        df = fetch_massive_data(ticker, lookback + 250) # Buffer for 200-day moving averages
        if df is None or df.empty:
            st.error("Failed to load data.")
            st.stop()
            
        # 1. Calculate Daily Log Returns
        df['Returns'] = np.log(df['Close'] / df['Close'].shift(1))
        
        # 2. Apply Strategy Vector
        df['Position'] = run_strategy(df, strategy)
        
        # 3. Calculate Strategy Returns (Shift position by 1 to avoid lookahead bias!)
        df['Strat_Returns'] = df['Position'].shift(1) * df['Returns']
        
        # 4. Drop the warm-up period (NaNs from moving averages)
        df = df.dropna()
        # Slice to exact lookback window requested
        df = df.tail(lookback) 
        
        # 5. Calculate Cumulative Equity Curves (Base 100)
        df['Cum_Hold'] = np.exp(df['Returns'].cumsum()) * 100
        df['Cum_Strat'] = np.exp(df['Strat_Returns'].cumsum()) * 100
        
        st.session_state.bt_data = df
        st.session_state.bt_ticker = ticker
        st.session_state.bt_strat = strategy

# --- RENDER DASHBOARD ---
if 'bt_data' in st.session_state:
    df = st.session_state.bt_data
    
    # Calculate Performance Metrics
    days = len(df)
    years = days / 252
    
    hold_return = (df['Cum_Hold'].iloc[-1] / 100) - 1
    strat_return = (df['Cum_Strat'].iloc[-1] / 100) - 1
    
    hold_cagr = (df['Cum_Hold'].iloc[-1] / 100) ** (1 / years) - 1 if years > 0 else 0
    strat_cagr = (df['Cum_Strat'].iloc[-1] / 100) ** (1 / years) - 1 if years > 0 else 0
    
    # Max Drawdown (Vectorized)
    roll_max_strat = df['Cum_Strat'].cummax()
    dd_strat = (df['Cum_Strat'] / roll_max_strat) - 1
    max_dd_strat = dd_strat.min()
    
    roll_max_hold = df['Cum_Hold'].cummax()
    dd_hold = (df['Cum_Hold'] / roll_max_hold) - 1
    max_dd_hold = dd_hold.min()
    
    # Sharpe Ratio (Assuming 0% risk-free rate for simplicity)
    strat_sharpe = (df['Strat_Returns'].mean() / df['Strat_Returns'].std()) * np.sqrt(252)
    hold_sharpe = (df['Returns'].mean() / df['Returns'].std()) * np.sqrt(252)

    st.subheader(f"Performance: {st.session_state.bt_strat} vs Buy & Hold ({st.session_state.bt_ticker})")
    
    # Metrics Row 1
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Strategy Return", f"{strat_return * 100:.2f}%", f"{(strat_return - hold_return) * 100:.2f}% vs B&H")
    c2.metric("Strategy CAGR", f"{strat_cagr * 100:.2f}%")
    c3.metric("Max Drawdown", f"{max_dd_strat * 100:.2f}%", f"{(max_dd_strat - max_dd_hold) * 100:.2f}% vs B&H", delta_color="inverse")
    c4.metric("Sharpe Ratio", f"{strat_sharpe:.2f}")
    
    # Plotly Equity Curve
    fig = go.Figure()
    
    fig.add_trace(go.Scatter(
        x=df.index, y=df['Cum_Hold'],
        mode='lines', name='Buy & Hold', line=dict(color='white', width=2, dash='dot')
    ))
    
    fig.add_trace(go.Scatter(
        x=df.index, y=df['Cum_Strat'],
        mode='lines', name='Strategy Equity', line=dict(color='#00d1ff', width=3)
    ))
    
    # Add drawdown shading underwater
    fig.add_trace(go.Scatter(
        x=df.index, y=roll_max_strat,
        mode='lines', line=dict(color='rgba(255,0,0,0)'), showlegend=False, hoverinfo='skip'
    ))
    fig.add_trace(go.Scatter(
        x=df.index, y=df['Cum_Strat'],
        mode='lines', fill='tonexty', fillcolor='rgba(255, 0, 0, 0.1)', line=dict(color='rgba(255,255,255,0)'),
        name="Drawdown Profile", hoverinfo='skip'
    ))

    fig.update_layout(
        template="plotly_dark", height=500, margin=dict(t=30, b=0, l=0, r=0),
        xaxis_title="Date", yaxis_title="Portfolio Value ($100 Base)", hovermode='x unified'
    )
    
    st.plotly_chart(fig, use_container_width=True)
    
    # Signal Distribution
    st.caption(f"Time in Market: {((df['Position'] != 0).sum() / len(df)) * 100:.1f}% | Longs: {(df['Position'] == 1).sum()} days | Shorts: {(df['Position'] == -1).sum()} days")

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import itertools
from src.data_engine import fetch_massive_data, format_massive_ticker
from src.auth import check_auth

st.set_page_config(page_title="Algo Backtester", layout="wide")
check_auth() # The firewall

st.title("⚡ Vectorized Algo Backtester & Optimizer")
st.markdown("Instantly test trading strategies, or run a Grid Search to find the mathematically optimal parameters.")

# --- SIDEBAR CONFIGURATION ---
with st.sidebar:
    st.header("Backtest Parameters")
    raw_ticker = st.text_input("Ticker", value="SPY")
    lookback = st.slider("Historical Data (Days)", 252, 2520, 1260, step=252)
    
    strategy = st.selectbox("Algorithmic Strategy", [
        "1. SMA Crossover",
        "2. Golden/Death Cross",
        "3. EMA Crossover",
        "4. MACD Momentum",
        "5. RSI Mean Reversion",
        "6. Bollinger Band Mean Reversion",
        "7. Bollinger Band Breakout",
        "8. Donchian Channel Breakout",
        "9. Z-Score Mean Reversion",
        "10. Simple Momentum (Price > SMA)"
    ])
    
    st.divider()
    c1, c2 = st.columns(2)
    run_test = c1.button("▶️ Run Standard", use_container_width=True)
    run_opt = c2.button("🎯 Optimize", type="primary", use_container_width=True)

ticker = format_massive_ticker(raw_ticker)

# --- MATH & STRATEGY ENGINE ---
def calculate_rsi(data, periods=14):
    delta = data.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=int(periods)).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=int(periods)).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def run_strategy(df, strat_name, p1=None, p2=None):
    """Calculates indicators and returns a vectorized position series."""
    df = df.copy()
    c = df['Close']
    df['Position'] = 0
    
    if strat_name == "1. SMA Crossover":
        p1, p2 = p1 or 10, p2 or 21
        df['Fast'] = c.rolling(int(p1)).mean()
        df['Slow'] = c.rolling(int(p2)).mean()
        df['Position'] = np.where(df['Fast'] > df['Slow'], 1, -1)
        
    elif strat_name == "2. Golden/Death Cross":
        p1, p2 = p1 or 50, p2 or 200
        df['Fast'] = c.rolling(int(p1)).mean()
        df['Slow'] = c.rolling(int(p2)).mean()
        df['Position'] = np.where(df['Fast'] > df['Slow'], 1, -1)
        
    elif strat_name == "3. EMA Crossover":
        p1, p2 = p1 or 9, p2 or 21
        df['Fast'] = c.ewm(span=int(p1), adjust=False).mean()
        df['Slow'] = c.ewm(span=int(p2), adjust=False).mean()
        df['Position'] = np.where(df['Fast'] > df['Slow'], 1, -1)
        
    elif strat_name == "4. MACD Momentum":
        p1, p2 = p1 or 12, p2 or 26
        ema_fast = c.ewm(span=int(p1), adjust=False).mean()
        ema_slow = c.ewm(span=int(p2), adjust=False).mean()
        macd = ema_fast - ema_slow
        signal = macd.ewm(span=9, adjust=False).mean() # Signal line fixed at 9
        df['Position'] = np.where(macd > signal, 1, -1)
        
    elif strat_name == "5. RSI Mean Reversion":
        p1, p2 = p1 or 14, p2 or 30
        df['RSI'] = calculate_rsi(c, p1)
        df.loc[df['RSI'] < p2, 'Signal'] = 1          # Oversold Buy
        df.loc[df['RSI'] > (100 - p2), 'Signal'] = -1 # Overbought Sell
        df['Position'] = df['Signal'].ffill().fillna(0)
        
    elif "Bollinger" in strat_name:
        p1, p2 = p1 or 20, p2 or 2.0
        sma = c.rolling(int(p1)).mean()
        std = c.rolling(int(p1)).std()
        upper = sma + (std * p2)
        lower = sma - (std * p2)
        if strat_name == "6. Bollinger Band Mean Reversion":
            df.loc[c < lower, 'Signal'] = 1
            df.loc[c > upper, 'Signal'] = -1
        else: # 7. Breakout
            df.loc[c > upper, 'Signal'] = 1
            df.loc[c < lower, 'Signal'] = -1
        df['Position'] = df['Signal'].ffill().fillna(0)
        
    elif strat_name == "8. Donchian Channel Breakout":
        p1 = p1 or 20
        df['Upper'] = c.rolling(int(p1)).max().shift(1)
        df['Lower'] = c.rolling(int(p1)).min().shift(1)
        df.loc[c > df['Upper'], 'Signal'] = 1
        df.loc[c < df['Lower'], 'Signal'] = -1
        df['Position'] = df['Signal'].ffill().fillna(0)
        
    elif strat_name == "9. Z-Score Mean Reversion":
        p1, p2 = p1 or 20, p2 or 2.0
        df['Z'] = (c - c.rolling(int(p1)).mean()) / c.rolling(int(p1)).std()
        df.loc[df['Z'] < -p2, 'Signal'] = 1
        df.loc[df['Z'] > p2, 'Signal'] = -1
        df['Position'] = df['Signal'].ffill().fillna(0)
        
    elif strat_name == "10. Simple Momentum (Price > SMA)":
        p1 = p1 or 20
        sma = c.rolling(int(p1)).mean()
        df['Position'] = np.where(c > sma, 1, -1)

    return df['Position']

def get_optimization_grid(strat_name):
    """Defines the parameter search space AND the parameter display names."""
    if "Crossover" in strat_name or "Cross" in strat_name or "MACD" in strat_name:
        return range(5, 50, 5), range(15, 100, 5), "Fast Period", "Slow Period"
    elif "RSI" in strat_name:
        return range(5, 30, 2), range(15, 45, 5), "RSI Period", "Oversold Boundary"
    elif "Bollinger" in strat_name or "Z-Score" in strat_name:
        return range(10, 60, 5), [1.5, 2.0, 2.5, 3.0], "Lookback Period", "StdDev Multiplier"
    elif "Donchian" in strat_name or "Simple Momentum" in strat_name:
        return range(10, 100, 5), [None], "Lookback Period", None
    return [None], [None], "Param 1", "Param 2"

# --- EXECUTION ---
if run_test or run_opt or 'bt_data' not in st.session_state or st.session_state.get('bt_ticker') != ticker:
    
    df_base = fetch_massive_data(ticker, lookback + 250)
    if df_base is None or df_base.empty:
        st.error("Failed to load data.")
        st.stop()
        
    df_base['Returns'] = np.log(df_base['Close'] / df_base['Close'].shift(1))
    
    p1_final, p2_final = None, None
    opt_msg = ""
    
    # 🎯 OPTIMIZATION LOGIC
    if run_opt:
        with st.spinner(f"Running Grid Search Optimization for {strategy}..."):
            grid_p1, grid_p2, name_p1, name_p2 = get_optimization_grid(strategy)
            best_ret = -np.inf
            
            progress_bar = st.progress(0)
            total_iterations = len(list(grid_p1)) * len(list(grid_p2))
            current_iter = 0
            
            for p1 in grid_p1:
                for p2 in grid_p2:
                    current_iter += 1
                    progress_bar.progress(current_iter / total_iterations)
                    
                    # Prevent illogical combinations (e.g. Fast MA > Slow MA)
                    if p2 is not None and type(p2) == int and p1 >= p2 and "Crossover" in strategy:
                        continue
                        
                    pos = run_strategy(df_base, strategy, p1, p2)
                    strat_ret = pos.shift(1) * df_base['Returns']
                    
                    # Evaluate on strictly sliced timeframe
                    temp_eval = pd.DataFrame({'ret': strat_ret}).dropna().tail(lookback)
                    cum_ret = np.exp(temp_eval['ret'].cumsum()).iloc[-1] if not temp_eval.empty else -np.inf
                    
                    if cum_ret > best_ret:
                        best_ret = cum_ret
                        p1_final, p2_final = p1, p2
            
            progress_bar.empty()
            
            # Format the output with the specific parameter names
            if p2_final is not None and name_p2 is not None:
                opt_msg = f"**Optimal Parameters Found:** {name_p1} = `{p1_final}` | {name_p2} = `{p2_final}`"
            else:
                opt_msg = f"**Optimal Parameters Found:** {name_p1} = `{p1_final}`"
                
            st.session_state.opt_msg = opt_msg
            
    else:
        # Clear optimization message if running standard
        st.session_state.opt_msg = None

    # 🚀 RUN FINAL BACKTEST (Using Optimized or Default Params)
    with st.spinner("Compiling Final Backtest..."):
        df = df_base.copy()
        df['Position'] = run_strategy(df, strategy, p1_final, p2_final)
        df['Strat_Returns'] = df['Position'].shift(1) * df['Returns']
        
        df = df.dropna().tail(lookback) 
        
        df['Cum_Hold'] = np.exp(df['Returns'].cumsum()) * 100
        df['Cum_Strat'] = np.exp(df['Strat_Returns'].cumsum()) * 100
        
        st.session_state.bt_data = df
        st.session_state.bt_ticker = ticker
        st.session_state.bt_strat = strategy

# --- RENDER DASHBOARD ---
if 'bt_data' in st.session_state:
    df = st.session_state.bt_data
    
    days = len(df)
    years = days / 252
    
    hold_return = (df['Cum_Hold'].iloc[-1] / 100) - 1
    strat_return = (df['Cum_Strat'].iloc[-1] / 100) - 1
    
    hold_cagr = (df['Cum_Hold'].iloc[-1] / 100) ** (1 / years) - 1 if years > 0 else 0
    strat_cagr = (df['Cum_Strat'].iloc[-1] / 100) ** (1 / years) - 1 if years > 0 else 0
    
    roll_max_strat = df['Cum_Strat'].cummax()
    max_dd_strat = ((df['Cum_Strat'] / roll_max_strat) - 1).min()
    
    roll_max_hold = df['Cum_Hold'].cummax()
    max_dd_hold = ((df['Cum_Hold'] / roll_max_hold) - 1).min()
    
    strat_sharpe = (df['Strat_Returns'].mean() / df['Strat_Returns'].std()) * np.sqrt(252) if df['Strat_Returns'].std() != 0 else 0

    st.subheader(f"Performance: {st.session_state.bt_strat} vs Buy & Hold ({st.session_state.bt_ticker})")
    
    if st.session_state.get('opt_msg'):
        st.success(st.session_state.opt_msg)
    
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Strategy Return", f"{strat_return * 100:.2f}%", f"{(strat_return - hold_return) * 100:.2f}% vs B&H")
    c2.metric("Strategy CAGR", f"{strat_cagr * 100:.2f}%")
    c3.metric("Max Drawdown", f"{max_dd_strat * 100:.2f}%", f"{(max_dd_strat - max_dd_hold) * 100:.2f}% vs B&H", delta_color="inverse")
    c4.metric("Sharpe Ratio", f"{strat_sharpe:.2f}")
    
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df.index, y=df['Cum_Hold'], mode='lines', name='Buy & Hold', line=dict(color='white', width=2, dash='dot')))
    fig.add_trace(go.Scatter(x=df.index, y=df['Cum_Strat'], mode='lines', name='Strategy Equity', line=dict(color='#00d1ff', width=3)))
    
    fig.add_trace(go.Scatter(x=df.index, y=roll_max_strat, mode='lines', line=dict(color='rgba(255,0,0,0)'), showlegend=False, hoverinfo='skip'))
    fig.add_trace(go.Scatter(x=df.index, y=df['Cum_Strat'], mode='lines', fill='tonexty', fillcolor='rgba(255, 0, 0, 0.1)', line=dict(color='rgba(255,255,255,0)'), name="Drawdown Profile", hoverinfo='skip'))

    fig.update_layout(template="plotly_dark", height=500, margin=dict(t=30, b=0, l=0, r=0), xaxis_title="Date", yaxis_title="Portfolio Value ($100 Base)", hovermode='x unified')
    st.plotly_chart(fig, use_container_width=True)
    
    st.caption(f"Time in Market: {((df['Position'] != 0).sum() / len(df)) * 100:.1f}% | Longs: {(df['Position'] == 1).sum()} days | Shorts: {(df['Position'] == -1).sum()} days")

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from src.data_engine import fetch_massive_data, format_massive_ticker
from src.chatbot import run_sidebar_chatbot

st.title("🔥 ERCOT Spark Spreads & Energy Tracker")

with st.sidebar:
    st.header("Spread Parameters")
    power_node = st.selectbox("Power Hub", ["HB_WEST", "HB_NORTH", "HB_SOUTH", "HB_HOUSTON"])
    # NG=F is the Yahoo Finance ticker for Henry Hub Natural Gas Futures
    gas_ticker = st.text_input("Natural Gas Proxy", value="NG=F") 
    heat_rate = st.slider("Plant Heat Rate (MMBtu/MWh)", 5.0, 12.0, 7.5, 0.1)
    lookback = st.slider("Lookback (Days)", 30, 365, 90)

# --- Fetch Data ---
node_formatted = format_massive_ticker(power_node)
power_data = fetch_massive_data(node_formatted, lookback)
gas_data = fetch_massive_data(gas_ticker, lookback)

if power_data is not None and gas_data is not None:
    # Align the dataframes by date
    df = pd.concat([power_data['Close'], gas_data['Close']], axis=1).dropna()
    df.columns = ['Power_Price', 'Gas_Price']
    
    # Mathematical Engine for Spark Spread
    # Formula: Power Price - (Gas Price * Heat Rate)
    df['Spark_Spread'] = df['Power_Price'] - (df['Gas_Price'] * heat_rate)
    
    # --- CHART 1: SPARK SPREAD ---
    st.subheader(f"Implied Spark Spread ({power_node} vs {gas_ticker})")
    
    fig_spread = go.Figure()
    # Color the line based on profitability (Green = Profitable, Red = Unprofitable)
    fig_spread.add_trace(go.Scatter(
        x=df.index, y=df['Spark_Spread'],
        fill='tozeroy',
        fillcolor='rgba(0, 209, 255, 0.1)',
        line=dict(color='#00d1ff', width=2),
        name="Spark Spread ($/MWh)"
    ))
    
    # Add a zero line for breakeven
    fig_spread.add_hline(y=0, line_dash="dash", line_color="red", annotation_text="Breakeven")
    
    fig_spread.update_layout(
        template="plotly_dark",
        yaxis_title="Spread ($/MWh)",
        hovermode="x unified",
        height=400
    )
    st.plotly_chart(fig_spread, use_container_width=True)

    # --- METRICS ---
    c1, c2, c3 = st.columns(3)
    curr_spread = df['Spark_Spread'].iloc[-1]
    avg_spread = df['Spark_Spread'].mean()
    c1.metric("Current Spark Spread", f"${curr_spread:.2f}")
    c2.metric(f"{lookback}-Day Average", f"${avg_spread:.2f}")
    c3.metric("Current Gas Price", f"${df['Gas_Price'].iloc[-1]:.2f}")

    # Chatbot Context
    ctx = f"The current spark spread for {power_node} using a {heat_rate} heat rate is ${curr_spread:.2f} per MWh."
    run_sidebar_chatbot(context_data=ctx)
else:
    st.warning("Could not fetch data for both Power and Natural Gas. Check tickers.")

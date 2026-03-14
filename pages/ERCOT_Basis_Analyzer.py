import streamlit as st
import plotly.graph_objects as go
from src.data_engine import fetch_massive_data, format_massive_ticker

from src.chatbot import run_sidebar_chatbot

# Run this at the start of every page script
run_sidebar_chatbot()

st.set_page_config(page_title="ERCOT Market Analysis", layout="wide")

# Sidebar
with st.sidebar:
    st.header("⚡ Node Selection")
    hub = st.selectbox("Select Hub", ["HB_WEST", "HB_NORTH", "HB_SOUTH", "HB_HOUSTON", "HB_PAN"])
    lookback = st.slider("Lookback Days", 30, 1095, 365)

# Execution
st.title(f"ERCOT Power Market: {hub}")
ticker = format_massive_ticker(hub)
data = fetch_massive_data(ticker, lookback)

if data is not None:
    # 1. Main Price Chart
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=data.index, y=data['Close'], line=dict(color='#00d1ff', width=2)))
    fig.update_layout(template="plotly_dark", title=f"Daily Settle Price History", yaxis_title="$/MWh")
    st.plotly_chart(fig, use_container_width=True)
    
    # 2. Key Stats
    c1, c2, c3 = st.columns(3)
    c1.metric("Current Settle", f"${data['Close'].iloc[-1]:.2f}")
    c2.metric("30-Day Avg", f"${data['Close'].tail(30).mean():.2f}")
    c3.metric("Volatility (Annual)", f"{(data['Close'].pct_change().std() * np.sqrt(252) * 100):.1f}%")
else:
    st.error("No ERCOT data found for this node. Check API connection.")

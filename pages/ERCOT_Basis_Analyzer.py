import streamlit as st
import plotly.graph_objects as go
from src.data_engine import fetch_massive_data, format_massive_ticker
from src.chatbot import run_sidebar_chatbot

# NO set_page_config here

st.title("⚡ ERCOT Basis & Power Analysis")

# --- Sidebar Controls ---
with st.sidebar:
    st.header("ERCOT Nodes")
    hub = st.selectbox("Select Hub", ["HB_WEST", "HB_NORTH", "HB_SOUTH", "HB_HOUSTON", "HB_PAN"])
    days = st.slider("History (Days)", 30, 730, 365)

# --- Logic & Rendering ---
ticker = format_massive_ticker(hub)
data = fetch_massive_data(ticker, days)

if data is not None:
    # Main Chart
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=data.index, y=data['Close'], line=dict(color='#00d1ff')))
    fig.update_layout(template="plotly_dark", title=f"Daily Settle: {hub}")
    st.plotly_chart(fig, use_container_width=True)
    
    # Stats columns
    c1, c2, c3 = st.columns(3)
    curr_price = data['Close'].iloc[-1]
    avg_price = data['Close'].mean()
    c1.metric("Current", f"${curr_price:.2f}")
    c2.metric("Period Avg", f"${avg_price:.2f}")

    # --- Activate Context-Aware Chatbot ---
    # AI now knows it is looking at ERCOT power prices
    ercot_context = (
        f"ERCOT Hub: {hub}. "
        f"Current Price: ${curr_price:.2f}. "
        f"Average Price over {days} days: ${avg_price:.2f}."
    )
    run_sidebar_chatbot(context_data=ercot_context)

else:
    st.error(f"No ERCOT data available for {hub}.")

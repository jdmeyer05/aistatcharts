import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
from src.data_engine import fetch_massive_data, format_massive_ticker
from src.simulation import run_monte_carlo_engine
from src.chatbot import run_sidebar_chatbot

# NO set_page_config here (it is already in app.py)

st.title("📈 Monte Carlo Simulation")

# --- Sidebar Controls ---
with st.sidebar:
    st.header("Settings")
    raw_ticker = st.text_input("Ticker", value="BTC-USD")
    lookback = st.slider("Lookback (Days)", 365, 1825, 1095)
    n_sims = st.slider("Simulations", 1000, 20000, 5000)
    drift = st.slider("Annual Drift %", -50.0, 50.0, 0.0)
    vol_mult = st.slider("Vol Multiplier", 0.5, 3.0, 1.0)
    method = st.selectbox("Method", ["bootstrap", "gaussian"])
    seasonal = st.checkbox("Use Seasonality", value=True)

# --- Logic & Rendering ---
ticker = format_massive_ticker(raw_ticker)
data = fetch_massive_data(ticker, lookback)

if data is not None:
    px_close = data['Close']
    
    # Run the Simulation from src/simulation.py
    p5, p50, p95, steps = run_monte_carlo_engine(
        px_close, n_sims, drift, vol_mult, method, seasonal
    )
    
    # Chart: Historical & Projection
    fig = go.Figure()
    # (Insert Plotly code for confidence intervals and median here)
    # For now, a simple placeholder line:
    fig.add_trace(go.Scatter(y=p50, name="Median Forecast", line=dict(color='#ffaa00')))
    fig.update_layout(template="plotly_dark", title=f"Projection for {raw_ticker}")
    st.plotly_chart(fig, use_container_width=True)

    # --- Activate Context-Aware Chatbot ---
    # We send the simulation results to the AI so it can discuss them
    mc_context = (
        f"Ticker: {raw_ticker}. "
        f"Median Year-End Projection: ${p50[-1]:,.2f}. "
        f"Drift: {drift}%, Method: {method}."
    )
    run_sidebar_chatbot(context_data=mc_context)

else:
    st.error("Could not load data. Check ticker or API keys.")

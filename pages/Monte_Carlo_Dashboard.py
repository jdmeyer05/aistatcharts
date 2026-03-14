import streamlit as st
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px_plot
from src.data_engine import fetch_massive_data, format_massive_ticker
from src.simulation import get_returns, run_monte_carlo_engine # Assuming these are in simulation.py
from src.chatbot import run_sidebar_chatbot

# Run this at the start of every page script
run_sidebar_chatbot()
# --- CONFIG ---
st.set_page_config(page_title="Monte Carlo & Seasonality", layout="wide")

st.markdown("""
    <style>
        #MainMenu {visibility: hidden;}
        footer {visibility: hidden;}
        header {visibility: hidden;}
        .block-container {padding-top: 2rem;}
    </style>
""", unsafe_allow_html=True)

# --- SIDEBAR ---
with st.sidebar:
    st.header("📈 Settings")
    raw_ticker = st.text_input("Ticker", value="BTC-USD")
    lookback_days = st.slider("Lookback (Days)", 365, 1825, 1095)
    n_sims = st.slider("Simulations", 1000, 20000, 5000)
    drift_bias = st.slider("Annual Drift %", -50.0, 50.0, 0.0)
    vol_mult = st.slider("Vol Multiplier", 0.5, 3.0, 1.0)
    mc_method = st.selectbox("Method", ["bootstrap", "gaussian"])
    use_seasonality = st.checkbox("Use Seasonality", value=True)

# --- DATA FETCHING ---
formatted_ticker = format_massive_ticker(raw_ticker)
px_data = fetch_massive_data(formatted_ticker, lookback_days)

if px_data is not None:
    px = px_data['Close']
    st.title(f"📊 {raw_ticker} Analysis")
    st.caption(f"Loaded {len(px)} days of history from Massive API")

    # 1. YTD OVERLAY (DIFFERENTIATED COLORS)
    st.divider()
    st.subheader("Yearly YTD Performance Overlay")
    fig_ytd = go.Figure()
    years = sorted(px_data.index.year.unique())
    
    # Use Plasma scale: Oldest years = Deep Purple, Newest years = Yellow
    colors = px_plot.colors.sample_colorscale("plasma", [i/(len(years)-1) for i in range(len(years))]) if len(years) > 1 else ['#00d1ff']

    for i, y in enumerate(years):
        yr_data = px_data[px_data.index.year == y]['Close']
        ytd = (yr_data / yr_data.iloc[0]) - 1.0
        is_current = (y == years[-1])
        
        fig_ytd.add_trace(go.Scatter(
            x=list(range(len(ytd))), y=ytd.values,
            mode='lines',
            name=f"Year {y}",
            line=dict(color='#00d1ff' if is_current else colors[i], width=3 if is_current else 1.2),
            opacity=1.0 if is_current else 0.4,
            hovertemplate=f"Year {y}<br>Day %{{x}}: %{{y:.2%}}<extra></extra>"
        ))
    
    fig_ytd.update_layout(height=380, template="plotly_dark", hovermode="x unified", margin=dict(l=0,r=0,t=20,b=0))
    st.plotly_chart(fig_ytd, use_container_width=True)

    # 2. MONTE CARLO (AMBER CONTRAST)
    st.divider()
    st.subheader("Monte Carlo Projection to Year-End")
    
    # Assuming run_monte_carlo_engine is imported from src/simulation.py
    p5, p50, p95, steps = run_monte_carlo_engine(px, n_sims, drift_bias, vol_mult, mc_method, use_seasonality)
    x_axis = list(range(1, steps + 1))

    fig_mc = go.Figure()
    
    # Confidence Interval
    fig_mc.add_trace(go.Scatter(
        x=x_axis + x_axis[::-1], y=list(p95) + list(p5)[::-1], 
        fill='toself', fillcolor='rgba(255, 170, 0, 0.1)', line=dict(color='rgba(255,255,255,0)'),
        name='90% Confidence Interval', hoverinfo="skip"
    ))

    # Median Line (Neon Amber/Orange)
    fig_mc.add_trace(go.Scatter(
        x=x_axis, y=p50, mode='lines',
        line=dict(color='#ffaa00', width=3),
        name='Median Forecast',
        hovertemplate='<b>Week %{x}</b><br>Price: $%{y:,.2f}<extra></extra>'
    ))

    fig_mc.update_layout(height=400, template="plotly_dark", margin=dict(l=20,r=20,t=20,b=20),
        xaxis=dict(title="Weeks from Today", showgrid=False),
        yaxis=dict(title="Price (USD)", showgrid=True, gridcolor='rgba(255,255,255,0.05)', tickprefix="$"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )
    
    st.plotly_chart(fig_mc, use_container_width=True)
    st.metric("Expected Year-End Price", f"${p50[-1]:,.2f}", f"{((p50[-1]/px.iloc[-1])-1)*100:.2f}%")

else:
    st.warning("No data found for this ticker.")

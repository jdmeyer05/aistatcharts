import streamlit as st
import plotly.graph_objects as go
import pandas as pd
import numpy as np
from src.data_engine import fetch_massive_data, format_massive_ticker, render_data_source_footer
from src.simulation import simulate_to_year_end_weekly
from src.chatbot import run_sidebar_chatbot

st.title("📈 Seasonal Monte Carlo to Year-End")

# --- Sidebar Controls (Matching Colab UI) ---
with st.sidebar:
    st.header("Simulation Settings")
    raw_ticker = st.text_input("Ticker", value="BTC-USD")
    n_sims = st.slider("Simulations", 1000, 100000, 10000, step=1000)
    lookback = st.slider("Lookback (Days)", 60, 2000, 365, step=5)
    method = st.selectbox("MC Method", ["bootstrap", "gaussian"])
    drift = st.slider("Drift Bias (Annual %)", -50.0, 50.0, 0.0, step=0.5)
    vol_mult = st.slider("Vol Multiplier", 0.2, 3.0, 1.0, step=0.05)
    seed = st.number_input("Random Seed", value=42)
    seasonal = st.checkbox("Use ISO Week Seasonality", value=True)

# --- Data & Execution ---
ticker = format_massive_ticker(raw_ticker)
# We fetch a larger base history (e.g. 5 years = 1825 days) to ensure we have deep seasonality data,
# even if the simulation lookback is shorter.
data = fetch_massive_data(ticker, 1825)

if data is not None and not data.empty:
    px_close = data['Close'].astype(float).squeeze()
    
    # Run the new weekly engine
    paths, week_dates = simulate_to_year_end_weekly(
        px_close=px_close,
        n_sims=n_sims,
        lookback_days=lookback,
        method=method,
        drift_bias_annual_pct=drift,
        vol_mult=vol_mult,
        seed=seed,
        use_seasonality=seasonal
    )

    if paths.size > 0:
        # Calculate Percentiles
        p5 = np.percentile(paths, 5, axis=0)
        p50 = np.percentile(paths, 50, axis=0)
        p95 = np.percentile(paths, 95, axis=0)
        
        last_price = float(px_close.iloc[-1])
        final_p50 = p50[-1]
        exp_return = ((final_p50 / last_price) - 1.0) * 100

        # --- Plotly Chart ---
        fig = go.Figure()

        # Historical Context (~6 months back for visual scaling)
        hist = px_close.tail(180)
        fig.add_trace(go.Scatter(
            x=hist.index, y=hist.values,
            name="History",
            line=dict(color='#00d1ff', width=2)
        ))

        # 90% Confidence Interval (Fan)
        fig.add_trace(go.Scatter(
            x=week_dates.tolist() + week_dates.tolist()[::-1],
            y=p95.tolist() + p5.tolist()[::-1],
            fill='toself',
            fillcolor='rgba(255, 170, 0, 0.1)',
            line=dict(color='rgba(255,255,255,0)'),
            name='5% - 95% Range'
        ))

        # Median Forecast
        fig.add_trace(go.Scatter(
            x=week_dates, y=p50,
            name="Median Projection",
            line=dict(color='#ffaa00', width=3, dash='dot')
        ))

        fig.update_layout(
            template="plotly_dark",
            title=f"Weekly Seasonal Projection for {ticker} (Target: Year-End)",
            xaxis_title="Date",
            yaxis_title="Price ($)",
            hovermode='x unified',
            height=550,
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
        )
        st.plotly_chart(fig, use_container_width=True)

        # --- Metrics Table ---
        st.subheader("📊 End of Year Summary")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Current Price", f"${last_price:,.2f}")
        c2.metric("Median Target (P50)", f"${final_p50:,.2f}")
        c3.metric("Expected Return", f"{exp_return:.2f}%")
        c4.metric("Weeks Simulated", len(week_dates))

        st.caption(f"**Bear Case (P5):** ${p5[-1]:,.2f} | **Bull Case (P95):** ${p95[-1]:,.2f}")

        # AI Chatbot integration
        ctx = f"Simulation for {ticker}. Expected return to year end: {exp_return:.2f}%. Target: ${final_p50:,.2f}."
        run_sidebar_chatbot(ctx)

    else:
        st.info("The year is almost over; not enough weeks remaining to run a meaningful year-end projection.")
        
    render_data_source_footer()
else:
    st.warning("No data found to run the simulation.")

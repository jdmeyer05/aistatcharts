import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from src.data_engine import fetch_massive_data, format_massive_ticker
from src.layout import setup_page
setup_page("12_Monte_Carlo")

st.title("🎲 Monte Carlo Stochastic Simulator")
st.markdown("Forecast terminal price distributions using Geometric Brownian Motion (GBM).")

# --- SIDEBAR CONFIGURATION ---
with st.sidebar:
    st.header("Simulation Parameters")
    raw_ticker = st.text_input("Ticker", value="SPY")
    
    st.divider()
    st.caption("Historical Data Tuning")
    lookback = st.slider("Lookback Window (Days)", 252, 1260, 252, step=252, help="Used to calculate historical drift and volatility.")
    
    st.divider()
    st.caption("Future Path Tuning")
    sim_days = st.number_input("Days to Simulate", min_value=10, max_value=500, value=252, step=10)
    sim_count = st.selectbox("Number of Simulations", [100, 500, 1000, 5000], index=2)
    
    run_sim = st.button("🚀 Run Simulation", type="primary", use_container_width=True)

ticker = format_massive_ticker(raw_ticker)

if run_sim or 'mc_data' not in st.session_state or st.session_state.get('mc_ticker') != ticker:
    with st.spinner(f"Fetching historical data for {ticker}..."):
        df = fetch_massive_data(ticker, lookback)
        
        if df is None or df.empty:
            st.error("Failed to load data.")
            st.stop()
            
        # --- 1. HISTORICAL METRICS ---
        df['Returns'] = np.log(df['Close'] / df['Close'].shift(1))
        df = df.dropna()
        
        S0 = df['Close'].iloc[-1]
        mu = df['Returns'].mean()
        sigma = df['Returns'].std()
        
        # --- 2. VECTORIZED GBM ENGINE ---
        with st.spinner("Calculating stochastic probability matrix..."):
            # Calculate the drift component
            drift = mu - (0.5 * sigma**2)
            
            # Generate the random shock matrix (sim_days x sim_count)
            rng = np.random.default_rng(42)
            Z = rng.normal(0, 1, (sim_days, sim_count))
            
            # Combine drift and shocks into daily return multipliers
            daily_returns_sim = np.exp(drift + sigma * Z)
            
            # Vectorized cumulative product to instantly calculate all 1000 paths
            # np.vstack adds the starting price S0 to the top of every path
            price_paths = np.vstack([np.ones(sim_count), np.cumprod(daily_returns_sim, axis=0)]) * S0
            
            # Save results to session state
            st.session_state.mc_paths = price_paths
            st.session_state.mc_S0 = S0
            st.session_state.mc_ticker = ticker
            st.session_state.mc_history = df['Close']

# --- RENDER DASHBOARD ---
if 'mc_paths' in st.session_state:
    price_paths = st.session_state.mc_paths
    S0 = st.session_state.mc_S0
    hist = st.session_state.mc_history
    
    # Extract Terminal Values (the very last simulated day)
    terminal_prices = price_paths[-1, :]
    
    # Calculate Risk Metrics
    mean_terminal = np.mean(terminal_prices)
    median_terminal = np.median(terminal_prices)
    pct_5 = np.percentile(terminal_prices, 5)
    pct_95 = np.percentile(terminal_prices, 95)
    
    prob_higher = np.sum(terminal_prices > S0) / len(terminal_prices) * 100
    
    st.subheader(f"Terminal Distribution Profile: {st.session_state.mc_ticker}")
    
    # Metrics Row
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Current Spot Price", f"${S0:,.2f}")
    c2.metric("Expected Mean Price", f"${mean_terminal:,.2f}", f"{(mean_terminal/S0 - 1)*100:.2f}%")
    c3.metric("Probability of Profit", f"{prob_higher:.1f}%")
    c4.metric("95% Confidence Interval", f"${pct_5:,.0f} - ${pct_95:,.0f}")
    
    # --- CHARTING ---
    tab1, tab2 = st.tabs(["Stochastic Path Fan", "Terminal Histogram"])
    
    with tab1:
        fig_paths = go.Figure()
        
        # Plot a subset of historical data (last 60 days) to anchor the chart
        hist_plot = hist.tail(60)
        x_hist = np.arange(-len(hist_plot)+1, 1)
        
        fig_paths.add_trace(go.Scatter(
            x=x_hist, y=hist_plot,
            mode='lines', line=dict(color='white', width=2), name="History"
        ))
        
        # Plot a subset of the simulated paths (max 100 lines so the browser doesn't crash)
        x_sim = np.arange(0, len(price_paths))
        paths_to_plot = min(100, price_paths.shape[1])
        
        for i in range(paths_to_plot):
            fig_paths.add_trace(go.Scatter(
                x=x_sim, y=price_paths[:, i],
                mode='lines', line=dict(color='#00d1ff', width=1,  # You can tweak color
                ), opacity=0.1, showlegend=False, hoverinfo='skip'
            ))
            
        # Plot Mean Path
        mean_path = np.mean(price_paths, axis=1)
        fig_paths.add_trace(go.Scatter(
            x=x_sim, y=mean_path,
            mode='lines', line=dict(color='yellow', width=3, dash='dash'), name="Mean Path"
        ))

        fig_paths.update_layout(
            template="plotly_dark", height=500, margin=dict(t=30, b=0, l=0, r=0),
            xaxis_title="Trading Days", yaxis_title="Price ($)", showlegend=True
        )
        st.plotly_chart(fig_paths, use_container_width=True)
        
    with tab2:
        fig_hist = go.Figure()
        
        fig_hist.add_trace(go.Histogram(
            x=terminal_prices, nbinsx=50,
            marker_color='#00d1ff', opacity=0.75, name="Distribution"
        ))
        
        # Add Reference Lines
        fig_hist.add_vline(x=S0, line_dash="solid", line_color="white", annotation_text="Current Price")
        fig_hist.add_vline(x=mean_terminal, line_dash="dash", line_color="yellow", annotation_text="Expected Mean")
        fig_hist.add_vline(x=pct_5, line_dash="dot", line_color="red", annotation_text="5th Pct (Worst Case)")
        fig_hist.add_vline(x=pct_95, line_dash="dot", line_color="green", annotation_text="95th Pct (Best Case)")
        
        fig_hist.update_layout(
            template="plotly_dark", height=500, margin=dict(t=30, b=0, l=0, r=0),
            xaxis_title="Terminal Price at Expiration ($)", yaxis_title="Frequency",
            bargap=0.05
        )
        st.plotly_chart(fig_hist, use_container_width=True)

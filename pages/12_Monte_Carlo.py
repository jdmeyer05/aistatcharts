import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from src.data_engine import fetch_massive_data, format_massive_ticker
from src.layout import setup_page, get_active_ticker, set_active_ticker, fun_loader
setup_page("12_Monte_Carlo")

st.title("🎯 Monte Carlo Simulator")
st.markdown("Forecast terminal price distributions using multiple simulation methods.")

# --- Controls ---
_c1, _c2, _c3, _c4, _c5 = st.columns([2, 2, 2, 2, 1])
with _c1:
    raw_ticker = st.text_input("Ticker", value=get_active_ticker())
with _c2:
    lookback = st.slider("Lookback (Days)", 252, 1260, 252, step=252, help="Historical drift and volatility window.")
with _c3:
    sim_days = st.number_input("Days to Simulate", min_value=10, max_value=500, value=252, step=10)
with _c4:
    sim_count = st.selectbox("Simulations", [100, 500, 1000, 5000], index=2)
with _c5:
    st.markdown("<br>", unsafe_allow_html=True)
    run_sim = st.button("Run Simulation", type="primary", use_container_width=True)

sim_method = st.radio("Simulation Method", ["GBM (Normal)", "Student-t (Fat Tails)", "Empirical Bootstrap"],
                      horizontal=True, index=1,
                      help="GBM assumes normal returns. Student-t captures fat tails. Empirical bootstrap uses actual historical return distribution.")

ticker = format_massive_ticker(raw_ticker)
set_active_ticker(ticker)

if run_sim or 'mc_data' not in st.session_state or st.session_state.get('mc_ticker') != ticker:
    with fun_loader("data"):
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
        
        # --- 2. SIMULATION ENGINE ---
        with fun_loader("compute"):
            rng = np.random.default_rng(42)
            historical_rets = df['Returns'].dropna().values

            if sim_method == "GBM (Normal)":
                drift = mu - (0.5 * sigma**2)
                Z = rng.normal(0, 1, (sim_days, sim_count))
                daily_returns_sim = np.exp(drift + sigma * Z)

            elif sim_method == "Student-t (Fat Tails)":
                from scipy.stats import t as t_dist
                # Fit Student-t to historical returns
                params = t_dist.fit(historical_rets)
                df_t, loc_t, scale_t = params
                # Generate t-distributed shocks centered on the GBM drift
                # (loc from fit captures the mean; use GBM drift instead to avoid double-counting)
                drift_t = mu - 0.5 * scale_t**2
                t_shocks = t_dist.rvs(df_t, loc=drift_t, scale=scale_t, size=(sim_days, sim_count), random_state=42)
                daily_returns_sim = np.exp(t_shocks)

            elif sim_method == "Empirical Bootstrap":
                # Block bootstrap (preserve autocorrelation)
                block_size = max(5, int(np.sqrt(len(historical_rets))))
                daily_log_rets = np.zeros((sim_days, sim_count))
                for col in range(sim_count):
                    idx = 0
                    while idx < sim_days:
                        start = rng.integers(0, len(historical_rets) - block_size + 1)
                        block_len = min(block_size, sim_days - idx)
                        daily_log_rets[idx:idx + block_len, col] = historical_rets[start:start + block_len]
                        idx += block_len
                daily_returns_sim = np.exp(daily_log_rets)

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

    # Distribution quality metrics
    st.divider()
    from scipy.stats import kurtosis as _kurt, skew as _skew
    hist_rets = st.session_state.mc_history.pct_change().dropna()
    dm1, dm2, dm3, dm4 = st.columns(4)
    dm1.metric("Historical Skewness", f"{_skew(hist_rets):.2f}", help="Negative = left tail (crash risk)")
    dm2.metric("Historical Kurtosis", f"{_kurt(hist_rets) + 3:.2f}", help="Normal = 3. Higher = fatter tails")
    dm3.metric("Simulation Method", sim_method.split(" (")[0])
    dm4.metric("VaR (5%)", f"${pct_5:,.2f}", f"{(pct_5/S0-1)*100:+.1f}%")

    if sim_method == "GBM (Normal)" and _kurt(hist_rets) + 3 > 4:
        st.warning(
            f"**Historical kurtosis is {_kurt(hist_rets)+3:.1f} (normal = 3).** GBM assumes normal returns "
            f"and will understate tail risk. Switch to **Student-t** or **Empirical Bootstrap** for "
            f"more realistic crash/rally scenarios."
        )

    # Regime context
    st.divider()
    st.subheader("Regime Context")
    st.caption(
        "The simulation uses a single volatility estimate from the training window. "
        "If the current regime differs from the historical average, results may be misleading."
    )
    _rv_20 = float(hist_rets.tail(20).std() * np.sqrt(252))
    _rv_full = float(hist_rets.std() * np.sqrt(252))
    _rv_ratio = _rv_20 / _rv_full if _rv_full > 0 else 1

    rc1, rc2, rc3 = st.columns(3)
    rc1.metric("20-Day Realized Vol", f"{_rv_20*100:.1f}%")
    rc2.metric(f"{lookback}D Historical Vol", f"{_rv_full*100:.1f}%")
    _regime = "High Vol" if _rv_ratio > 1.3 else ("Low Vol" if _rv_ratio < 0.7 else "Normal")
    _regime_color = "#ff4444" if _regime == "High Vol" else ("#00ff88" if _regime == "Low Vol" else "#ffaa00")
    rc3.markdown(
        f'<div style="text-align:center;padding:8px;">'
        f'<div style="font-size:0.7rem;color:#888;text-transform:uppercase;">Regime</div>'
        f'<div style="font-size:1.2rem;font-weight:700;color:{_regime_color};">{_regime}</div>'
        f'<div style="font-size:0.7rem;color:#888;">{_rv_ratio:.2f}x historical avg</div>'
        f'</div>', unsafe_allow_html=True,
    )

    if _regime == "High Vol":
        st.warning(
            f"Current vol ({_rv_20*100:.0f}%) is {_rv_ratio:.1f}x the historical average ({_rv_full*100:.0f}%). "
            f"The simulation uses the historical average — actual uncertainty is likely **higher** than shown. "
            f"Consider widening confidence intervals by {(_rv_ratio - 1)*100:.0f}%."
        )
    elif _regime == "Low Vol":
        st.info(
            f"Current vol ({_rv_20*100:.0f}%) is only {_rv_ratio:.1f}x the historical average. "
            f"The simulation may **overstate** uncertainty. Current market is calmer than what the model assumes."
        )

    from src.data_engine import render_data_source_footer
    render_data_source_footer()

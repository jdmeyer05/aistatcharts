import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
from src.data_engine import fetch_massive_data, format_massive_ticker
from src.simulation import run_monte_carlo_engine
from src.chatbot import run_sidebar_chatbot
import logging

# Setup logging
logger = logging.getLogger(__name__)

# Set unique page ID for session state isolation
if 'page_id' not in st.session_state:
    st.session_state.page_id = 'monte_carlo'

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
try:
    ticker = format_massive_ticker(raw_ticker)
    data = fetch_massive_data(ticker, lookback)

    if data is not None and not data.empty:
        # Validate data structure
        if 'Close' not in data.columns:
            st.error("❌ Error: Data does not contain 'Close' column. Check data source.")
            logger.error(f"Invalid data structure for {ticker}: missing 'Close' column")
        elif len(data) < 10:
            st.warning(f"⚠️ Warning: Only {len(data)} data points available. Results may be unreliable (minimum 10 recommended).")
        else:
            px_close = data['Close']
            
            # Run the Simulation from src/simulation.py
            try:
                p5, p50, p95, steps = run_monte_carlo_engine(
                    px_close, n_sims, drift, vol_mult, method, seasonal
                )
                
                # Chart: Historical & Projection with confidence intervals
                fig = go.Figure()
                
                # Add historical data
                fig.add_trace(go.Scatter(
                    x=data.index,
                    y=data['Close'],
                    name="Historical Data",
                    line=dict(color='#1f77b4', width=2),
                    hovertemplate='<b>Date:</b> %{x}<br><b>Price:</b> $%{y:,.2f}<extra></extra>'
                ))
                
                # Add projection lines (percentiles)
                steps_range = list(range(len(p50)))
                
                fig.add_trace(go.Scatter(
                    y=p95,
                    name="95th Percentile (Upside)",
                    line=dict(color='#00aa00', dash='dash', width=1),
                    opacity=0.7,
                    hovertemplate='<b>95th Percentile:</b> $%{y:,.2f}<extra></extra>'
                ))
                
                fig.add_trace(go.Scatter(
                    y=p50, 
                    name="Median Forecast", 
                    line=dict(color='#ffaa00', width=3),
                    hovertemplate='<b>Median:</b> $%{y:,.2f}<extra></extra>'
                ))
                
                fig.add_trace(go.Scatter(
                    y=p5,
                    name="5th Percentile (Downside)",
                    line=dict(color='#ff0000', dash='dash', width=1),
                    opacity=0.7,
                    fill='tonexty',
                    hovertemplate='<b>5th Percentile:</b> $%{y:,.2f}<extra></extra>'
                ))
                
                fig.update_layout(
                    template="plotly_dark", 
                    title=f"📊 {ticker} - Monte Carlo Forecast ({lookback}-day lookback, {n_sims} simulations)",
                    xaxis_title="Time Period",
                    yaxis_title="Price ($)",
                    hovermode='x unified',
                    height=600
                )
                st.plotly_chart(fig, use_container_width=True)

                # Display summary statistics
                current_price = px_close.iloc[-1]
                median_forecast = p50[-1]
                change_pct = ((median_forecast - current_price) / current_price) * 100
                
                col1, col2, col3, col4 = st.columns(4)
                with col1:
                    st.metric("Current Price", f"${current_price:,.2f}")
                with col2:
                    st.metric("Median Forecast", f"${median_forecast:,.2f}")
                with col3:
                    st.metric("Expected Change", f"{change_pct:+.2f}%", 
                             delta=f"{change_pct:+.2f}%")
                with col4:
                    st.metric("Confidence Range", 
                             f"${p5[-1]:,.0f} - ${p95[-1]:,.0f}")

                st.divider()

                # --- Activate Context-Aware Chatbot ---
                # We send the simulation results to the AI so it can discuss them
                mc_context = (
                    f"Ticker: {ticker}. "
                    f"Current Price: ${current_price:,.2f}. "
                    f"Median Year-End Projection: ${median_forecast:,.2f}. "
                    f"5th Percentile (Bear Case): ${p5[-1]:,.2f}. "
                    f"95th Percentile (Bull Case): ${p95[-1]:,.2f}. "
                    f"Expected Change: {change_pct:+.2f}%. "
                    f"Drift: {drift}%, Method: {method}, Seasonality: {seasonal}. "
                    f"Data Points Used: {len(data)}"
                )
                run_sidebar_chatbot(context_data=mc_context)
                
            except Exception as sim_err:
                st.error(f"❌ Error running Monte Carlo simulation: {str(sim_err)}")
                logger.error(f"Simulation error for {ticker}: {str(sim_err)}", exc_info=True)
    else:
        st.error("❌ Could not load data. Check ticker symbol or API keys.")
        logger.error(f"Data fetch failed for ticker: {raw_ticker}")
        
except Exception as e:
    st.error(f"❌ An unexpected error occurred: {str(e)}")
    logger.error(f"Unexpected error in Monte Carlo Dashboard: {str(e)}", exc_info=True)

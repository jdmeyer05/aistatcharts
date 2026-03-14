import streamlit as st
import plotly.graph_objects as go
import logging
import pandas as pd
import numpy as np
from src.data_engine import fetch_massive_data, format_massive_ticker
from src.simulation import run_monte_carlo_engine
from src.chatbot import run_sidebar_chatbot

# Setup logging
logger = logging.getLogger(__name__)

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
        else:
            # THE FIX: .squeeze() forces the matrix into a flat 1D Pandas Series
            px_close = data['Close'].astype(float).squeeze()
            
            # Clean data (remove NaNs)
            px_close = px_close.dropna()
            
            if len(px_close) < 10:
                st.error(f"❌ Error: Not enough valid data points ({len(px_close)}).")
            else:
                # ---------------------------------------------------------
                # RUN SIMULATION ENGINE
                # ---------------------------------------------------------
                p5, p50, p95, steps = run_monte_carlo_engine(
                    px_close, n_sims, drift, vol_mult, method, seasonal
                )
                
                # ---------------------------------------------------------
                # CHART 1: STANDARD SHORT-TERM PROJECTION
                # ---------------------------------------------------------
                fig1 = go.Figure()
                
                # Historical Data
                fig1.add_trace(go.Scatter(
                    x=px_close.index, y=px_close.values,
                    name="Historical",
                    line=dict(color='#00d1ff', width=2)
                ))
                
                # Projection Dates
                last_date = px_close.index[-1]
                future_dates = pd.date_range(start=last_date, periods=len(p50), freq='D')
                
                # Confidence Interval (Shaded Area)
                fig1.add_trace(go.Scatter(
                    x=future_dates.tolist() + future_dates.tolist()[::-1],
                    y=p95.tolist() + p5.tolist()[::-1],
                    fill='toself',
                    fillcolor='rgba(255, 170, 0, 0.1)',
                    line=dict(color='rgba(255,255,255,0)'),
                    name='90% Confidence Interval'
                ))

                # Median Forecast
                fig1.add_trace(go.Scatter(
                    x=future_dates, y=p50,
                    name="Median Forecast",
                    line=dict(color='#ffaa00', width=3)
                ))

                fig1.update_layout(
                    template="plotly_dark",
                    title=f"Standard Projection for {ticker}",
                    xaxis_title="Date",
                    yaxis_title="Price ($)",
                    hovermode='x unified',
                    height=500
                )
                
                st.plotly_chart(fig1, use_container_width=True)

                # ---------------------------------------------------------
                # CHART 2: FULL YEAR STITCHED VIEW (YTD + REST OF YEAR)
                # ---------------------------------------------------------
                st.divider()
                st.subheader("🗓️ Full Year Perspective (YTD Actuals + Year-End Forecast)")
                
                current_year = pd.Timestamp.now().year
                ytd_data = px_close[px_close.index.year == current_year]
                
                if not ytd_data.empty:
                    end_of_year = pd.Timestamp(year=current_year, month=12, day=31)
                    last_actual_date = ytd_data.index[-1]
                    
                    # Create dates for the rest of the year
                    rest_of_year_dates = pd.date_range(start=last_actual_date, end=end_of_year, freq='D')
                    days_left = len(rest_of_year_dates)
                    
                    if days_left > 1:
                        # Slice the simulation arrays to match exactly the days remaining in the year
                        slice_idx = min(days_left, len(p50))
                        ytd_p50 = p50[:slice_idx]
                        ytd_p95 = p95[:slice_idx]
                        ytd_p5 = p5[:slice_idx]
                        plot_dates = rest_of_year_dates[:slice_idx]
                        
                        # Anchor the simulation to the exact last closing price to prevent visual gaps
                        anchor_price = ytd_data.iloc[-1]
                        offset = anchor_price - ytd_p50[0]
                        
                        fig_ytd = go.Figure()

                        # Plot YTD Actuals
                        fig_ytd.add_trace(go.Scatter(
                            x=ytd_data.index, y=ytd_data.values,
                            name=f"{current_year} Actuals",
                            line=dict(color='#00d1ff', width=3)
                        ))

                        # Plot Forecast Confidence Interval
                        fig_ytd.add_trace(go.Scatter(
                            x=plot_dates.tolist() + plot_dates.tolist()[::-1],
                            y=(ytd_p95 + offset).tolist() + (ytd_p5 + offset).tolist()[::-1],
                            fill='toself',
                            fillcolor='rgba(255, 170, 0, 0.1)',
                            line=dict(color='rgba(255,255,255,0)'),
                            name='90% Range (Forecast)'
                        ))

                        # Plot Forecast Median
                        fig_ytd.add_trace(go.Scatter(
                            x=plot_dates, y=(ytd_p50 + offset),
                            name="Rest of Year (Median)",
                            line=dict(color='#ffaa00', width=3, dash='dot')
                        ))

                        fig_ytd.update_layout(
                            template="plotly_dark",
                            title=f"{ticker} - {current_year} YTD vs Year-End Projection",
                            xaxis=dict(title="Date", range=[f"{current_year}-01-01", f"{current_year}-12-31"]),
                            yaxis=dict(title="Price ($)", tickprefix="$"),
                            hovermode='x unified',
                            height=500,
                            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
                        )
                        
                        st.plotly_chart(fig_ytd, use_container_width=True)
                        
                        # Display Key Metrics
                        c1, c2, c3 = st.columns(3)
                        ytd_perf = (anchor_price / ytd_data.iloc[0]) - 1
                        exp_total_perf = ((ytd_p50[-1] + offset) / ytd_data.iloc[0]) - 1
                        
                        c1.metric("YTD Performance", f"{ytd_perf*100:.1f}%")
                        c2.metric("Current Price", f"${anchor_price:,.2f}")
                        c3.metric("Expected Year-End", f"${(ytd_p50[-1] + offset):,.2f}", f"{exp_total_perf*100:.1f}% Total Year")
                    else:
                        st.info("The year is almost over; not enough days left to plot a meaningful YTD forecast.")
                else:
                    st.warning(f"No data available for the current year ({current_year}) to plot YTD actuals.")

                # ---------------------------------------------------------
                # CHATBOT INTEGRATION
                # ---------------------------------------------------------
                mc_context = (
                    f"Ticker: {ticker}. Projected Median: ${p50[-1]:,.2f}. "
                    f"Annual Drift: {drift}%. Current YTD performance is active."
                )
                run_sidebar_chatbot(context_data=mc_context)
    else:
        st.warning("No data found. Check ticker or API limit.")

except Exception as e:
    st.error(f"An error occurred: {e}")
    logger.exception("Error in Monte Carlo dashboard:")

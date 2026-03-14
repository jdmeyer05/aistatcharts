import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from src.data_engine import fetch_massive_data, format_massive_ticker, render_data_source_footer
from src.simulation import predict_30d_random_forest
from src.chatbot import run_sidebar_chatbot
from src.auth import check_auth

st.set_page_config(page_title="ML Stock Predictor", layout="wide")
check_auth() # The firewall

st.title("🤖 ML Tactical Forecast (30-Day)")
st.markdown("Stochastic Recursive Random Forest: Projects 1 day ahead dynamically to generate a realistic, volatility-adjusted price path.")

# --- SIDEBAR CONFIGURATION ---
with st.sidebar:
    st.header("Forecast Parameters")
    with st.form("ml_settings"):
        raw_ticker = st.text_input("Ticker", value="SPY")
        
        st.divider()
        st.caption("Hyperparameters")
        n_trees = st.slider("Random Forest Estimators", 50, 500, 200, step=50)
        lookback = st.slider("Training Lookback (Days)", 500, 2520, 1000, step=250)
        
        submit = st.form_submit_button("🚀 Run Neural Forecast")

ticker = format_massive_ticker(raw_ticker)

# --- EXECUTION ---
if submit or 'ml_forecast' not in st.session_state or st.session_state.get('ml_ticker') != ticker:
    with st.spinner(f"Training ML Engine on {ticker} and running stochastic projections..."):
        # Fetch data
        df = fetch_massive_data(ticker, lookback + 150) # Buffer for rolling indicators
        if df is None or df.empty:
            st.error(f"Failed to fetch data for {ticker}.")
            st.stop()
            
        px_close = df['Close'].dropna()
        
        # Run the new Stochastic Recursive Engine
        forecast_data, future_dates = predict_30d_random_forest(px_close, n_estimators=n_trees, lookback_days=lookback)
        
        if len(future_dates) == 0:
            st.error("Not enough data to run the forecast.")
            st.stop()
            
        st.session_state.ml_df = px_close
        st.session_state.ml_forecast = forecast_data
        st.session_state.ml_dates = future_dates
        st.session_state.ml_ticker = ticker

# --- RENDER DASHBOARD ---
if 'ml_forecast' in st.session_state:
    px_close = st.session_state.ml_df
    forecast_data = st.session_state.ml_forecast
    future_dates = st.session_state.ml_dates
    
    current_price = px_close.iloc[-1]
    predicted_mean_price = forecast_data['mean'][-1]
    predicted_return = (predicted_mean_price / current_price) - 1
    
    # Calculate expected volatility range
    upper_bound = forecast_data['upper'][-1]
    lower_bound = forecast_data['lower'][-1]
    upside = (upper_bound / current_price) - 1
    downside = (lower_bound / current_price) - 1
    
    # --- METRICS ROW ---
    c1, c2, c3 = st.columns(3)
    c1.metric("Current Spot", f"${current_price:,.2f}")
    c2.metric("Target (30-Day)", f"${predicted_mean_price:,.2f}", f"{predicted_return * 100:.2f}%")
    c3.metric("90% Confidence Interval", f"${lower_bound:,.2f} - ${upper_bound:,.2f}")
    
    # --- PLOTLY CHART ---
    # Slice the last 60 days of history so the chart isn't zoomed out too far
    hist_plot = px_close.tail(60)
    
    fig = go.Figure()
    
    # 1. Historical Data
    fig.add_trace(go.Scatter(
        x=hist_plot.index, y=hist_plot.values,
        mode='lines', name='Historical', line=dict(color='white', width=2)
    ))
    
    # 2. Confidence Interval Shading (Lower to Upper)
    fig.add_trace(go.Scatter(
        x=np.concatenate([future_dates, future_dates[::-1]]),
        y=np.concatenate([forecast_data['upper'], forecast_data['lower'][::-1]]),
        fill='toself', fillcolor='rgba(0, 209, 255, 0.15)',
        line=dict(color='rgba(255,255,255,0)'), hoverinfo="skip", showlegend=True, name="90% Confidence Band"
    ))
    
    # 3. The Jagged Stochastic Mean Path
    # Connect the last historical point to the first prediction point so the line doesn't break
    connect_x = [hist_plot.index[-1], future_dates[0]]
    connect_y = [hist_plot.values[-1], forecast_data['mean'][0]]
    fig.add_trace(go.Scatter(x=connect_x, y=connect_y, mode='lines', line=dict(color='#00d1ff', width=2), showlegend=False))
    
    fig.add_trace(go.Scatter(
        x=future_dates, y=forecast_data['mean'],
        mode='lines', name='Stochastic Path (Median)', line=dict(color='#00d1ff', width=2.5)
    ))
    
    fig.update_layout(
        template="plotly_dark", height=500, margin=dict(t=30, b=0, l=0, r=0),
        xaxis_title="Date", yaxis_title="Price ($)", hovermode='x unified'
    )
    
    st.plotly_chart(fig, use_container_width=True)
    
    # AI Analyst Context
    ctx = (f"30-Day ML Forecast for {ticker}. Current: ${current_price:.2f}. "
           f"Target: ${predicted_mean_price:.2f} ({predicted_return * 100:.2f}%). "
           f"Range: ${lower_bound:.2f} to ${upper_bound:.2f}.")
    run_sidebar_chatbot(ctx)

render_data_source_footer()

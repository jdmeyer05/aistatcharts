import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
from src.data_engine import fetch_massive_data, format_massive_ticker
from src.chatbot import run_sidebar_chatbot

# NO set_page_config (inherited from app.py)

st.title("📅 Historical Seasonality & YTD Overlay")

# --- Sidebar ---
with st.sidebar:
    st.header("Analysis Settings")
    raw_ticker = st.text_input("Ticker", value="BTC-USD")
    lookback = st.slider("Historical Depth (Days)", 365, 3650, 1825) # Up to 10 years

# --- Data Fetching ---
ticker = format_massive_ticker(raw_ticker)
data = fetch_massive_data(ticker, lookback)

if data is not None and not data.empty:
    # 1. YEARLY YTD OVERLAY
    st.subheader("Yearly YTD Performance Overlay")
    years = sorted(data.index.year.unique())
    fig_ytd = go.Figure()
    
    # Generate colorscale for years
    colors = px.colors.sample_colorscale("plasma", [i/(len(years)-1) for i in range(len(years))])

    for i, y in enumerate(years):
        yr_data = data[data.index.year == y]['Close']
        ytd_returns = (yr_data / yr_data.iloc[0]) - 1.0
        is_curr = (y == years[-1])
        
        fig_ytd.add_trace(go.Scatter(
            x=list(range(len(ytd_returns))), 
            y=ytd_returns.values,
            name=str(y),
            line=dict(color='#00d1ff' if is_curr else colors[i], width=3 if is_curr else 1),
            opacity=1.0 if is_curr else 0.4
        ))

    fig_ytd.update_layout(
        template="plotly_dark", 
        xaxis_title="Trading Days from Jan 1",
        yaxis_title="Return %",
        hovermode="x unified"
    )
    st.plotly_chart(fig_ytd, use_container_width=True)

    # 2. MONTHLY SEASONALITY HEATMAP
    st.divider()
    st.subheader("Average Monthly Returns")
    
    data['Month'] = data.index.month
    data['Pct_Change'] = data['Close'].pct_change()
    monthly_stats = data.groupby('Month')['Pct_Change'].mean() * 100
    
    fig_heat = px.bar(
        x=monthly_stats.index, 
        y=monthly_stats.values,
        labels={'x': 'Month', 'y': 'Avg Return %'},
        color=monthly_stats.values,
        color_continuous_scale='RdYlGn'
    )
    fig_heat.update_layout(template="plotly_dark")
    st.plotly_chart(fig_heat, use_container_width=True)

    # --- Sidebar Assistant ---
    run_sidebar_chatbot(context_data=f"Analyzing historical seasonality for {ticker} over {lookback} days.")

else:
    st.error("Could not load historical data.")

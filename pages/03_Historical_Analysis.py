import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
from src.data_engine import fetch_massive_data, format_massive_ticker, render_data_source_footer
from src.chatbot import run_sidebar_chatbot

st.set_page_config(page_title="Historical & Seasonal Analysis", layout="wide")

from src.auth import check_auth
check_auth()

st.title("🕰️ Historical & Seasonal Analysis")
st.markdown("Analyze multi-year seasonality, Year-to-Date (YTD) trajectories, and monthly return distributions.")

# --- SIDEBAR CONFIGURATION ---
with st.sidebar:
    st.header("Analysis Settings")
    with st.form("historical_settings"):
        raw_ticker = st.text_input("Ticker", value="SPY")
        st.caption("Data is locked to a 5-year lookback to ensure accurate seasonal profiling and color gradient mapping.")
        submit = st.form_submit_button("🚀 Run Analysis")

ticker = format_massive_ticker(raw_ticker)

# Fetch roughly 5.5 years of data to ensure we get 5 full calendar years
lookback_days = 2000 

if submit or 'hist_df' not in st.session_state or st.session_state.get('hist_ticker') != ticker:
    with st.spinner(f"Processing 5-year historical surface for {ticker}..."):
        df = fetch_massive_data(ticker, lookback_days)
        
        if df is None or df.empty:
            st.error(f"Failed to fetch data for {ticker}.")
            st.stop()
            
        st.session_state.hist_df = df
        st.session_state.hist_ticker = ticker

# --- RENDER DASHBOARD ---
if 'hist_df' in st.session_state:
    df = st.session_state.hist_df.copy()
    
    # 1. Data Prep: Daily Returns & Timeline
    df['Daily_Return'] = df['Close'].pct_change()
    df['Year'] = df.index.year
    df['DOY'] = df.index.dayofyear
    
    # Filter to exactly the current year and the 5 preceding years
    current_year = pd.Timestamp.now().year
    valid_years = list(range(current_year - 5, current_year + 1))
    df = df[df['Year'].isin(valid_years)]
    
    # Define the Thermal Color Gradient (Hot to Cold)
    # 0 = Current Year (Hot Red), 5 = Oldest Year (Deep Purple)
    thermal_gradient = ['#ff2a2a', '#ff7f00', '#ffd700', '#00d1ff', '#118ab2', '#ad7fff']
    year_colors = {year: thermal_gradient[i] for i, year in enumerate(sorted(valid_years, reverse=True))}

    # 2. Data Prep: Monthly Returns for Box Plots & Heatmap
    # Resample to end of month to get exact monthly closing prices
    monthly_px = df['Close'].resample('ME').last()
    monthly_ret = monthly_px.pct_change().dropna()
    m_df = pd.DataFrame({'Return': monthly_ret})
    m_df['Year'] = m_df.index.year
    m_df['Month_Num'] = m_df.index.month
    m_df['Month_Name'] = m_df.index.strftime('%b')
    
    month_order = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

    # --- 2x2 CHART GRID ---
    r1c1, r1c2 = st.columns(2)
    
    # --- CHART 1: YTD Cumulative Return Comparison (Top Left) ---
    with r1c1:
        st.subheader("1. YTD Trajectory Comparison (5-Year)")
        fig_ytd = go.Figure()
        
        for year in sorted(valid_years):
            year_data = df[df['Year'] == year]
            if not year_data.empty:
                # Calculate cumulative return from January 1st of that year
                cum_ret = (1 + year_data['Daily_Return'].fillna(0)).cumprod() - 1
                
                # Highlight current year with a thicker line
                line_width = 3.5 if year == current_year else 1.5
                opacity = 1.0 if year == current_year else 0.7
                
                fig_ytd.add_trace(go.Scatter(
                    x=year_data['DOY'], 
                    y=cum_ret,
                    name=str(year),
                    mode='lines',
                    line=dict(color=year_colors[year], width=line_width),
                    opacity=opacity
                ))
                
        fig_ytd.update_layout(
            template="plotly_dark", height=380, margin=dict(t=20, b=0, l=0, r=0),
            xaxis_title="Day of Year", yaxis_title="Cumulative YTD Return",
            yaxis=dict(tickformat=".1%"), hovermode='x unified'
        )
        st.plotly_chart(fig_ytd, use_container_width=True)

    # --- CHART 2: Monthly Return Distribution Box Plots (Top Right) ---
    with r1c2:
        st.subheader("2. Monthly Seasonality (Box Plots)")
        fig_box = go.Figure()
        
        for month in month_order:
            month_data = m_df[m_df['Month_Name'] == month]
            fig_box.add_trace(go.Box(
                y=month_data['Return'],
                name=month,
                marker_color='#ad7fff', # Unified deep purple for the boxes
                boxpoints='all', # Show all underlying data points next to the box
                jitter=0.3,
                pointpos=-1.8
            ))
            
        # Add a zero line for quick visual reference of positive/negative months
        fig_box.add_hline(y=0, line_dash="solid", line_color="white", opacity=0.3)
            
        fig_box.update_layout(
            template="plotly_dark", height=380, margin=dict(t=20, b=0, l=0, r=0),
            yaxis_title="Monthly Return", yaxis=dict(tickformat=".1%"),
            showlegend=False
        )
        st.plotly_chart(fig_box, use_container_width=True)

    r2c1, r2c2 = st.columns(2)
    
    # --- CHART 3: Monthly Heatmap (Bottom Left) ---
    with r2c1:
        st.subheader("3. Monthly Returns Heatmap")
        
        # Pivot data for heatmap
        heatmap_data = m_df.pivot(index='Year', columns='Month_Name', values='Return')
        # Reorder columns to standard calendar order
        heatmap_data = heatmap_data.reindex(columns=month_order)
        # Sort years descending so current year is on top
        heatmap_data = heatmap_data.sort_index(ascending=False)
        
        # Format text for annotations
        text_data = heatmap_data.applymap(lambda x: f"{x:.1%}" if pd.notnull(x) else "")
        
        fig_heat = go.Figure(data=go.Heatmap(
            z=heatmap_data.values,
            x=heatmap_data.columns,
            y=heatmap_data.index,
            text=text_data.values,
            texttemplate="%{text}",
            colorscale="RdYlGn", # Red (Negative) to Green (Positive)
            zmid=0 # Force 0 to be the exact middle (yellow)
        ))
        
        fig_heat.update_layout(
            template="plotly_dark", height=350, margin=dict(t=20, b=0, l=0, r=0),
            yaxis=dict(type='category', autorange='reversed') # Keep newest year at top
        )
        st.plotly_chart(fig_heat, use_container_width=True)

    # --- CHART 4: Annual Returns (Bottom Right) ---
    with r2c2:
        st.subheader("4. Total Annual Returns")
        
        # Group by year and calculate compounded annual return
        annual_ret = df.groupby('Year')['Daily_Return'].apply(lambda x: (1 + x.fillna(0)).cumprod().iloc[-1] - 1).reset_index()
        
        # Map our thermal colors to the bar chart
        annual_ret['Color'] = annual_ret['Year'].map(year_colors)
        
        fig_annual = go.Figure()
        fig_annual.add_trace(go.Bar(
            x=annual_ret['Year'].astype(str),
            y=annual_ret['Daily_Return'],
            marker_color=annual_ret['Color'],
            text=annual_ret['Daily_Return'].apply(lambda x: f"{x:.1%}"),
            textposition='auto'
        ))
        
        fig_annual.update_layout(
            template="plotly_dark", height=350, margin=dict(t=20, b=0, l=0, r=0),
            yaxis_title="Total Return", yaxis=dict(tickformat=".1%")
        )
        st.plotly_chart(fig_annual, use_container_width=True)

    # --- AI CONTEXT INJECTION ---
    current_ytd = df[df['Year'] == current_year]['Daily_Return'].fillna(0).add(1).cumprod().iloc[-1] - 1 if not df[df['Year'] == current_year].empty else 0
    best_month = m_df.groupby('Month_Name')['Return'].mean().idxmax()
    worst_month = m_df.groupby('Month_Name')['Return'].mean().idxmin()
    
    ctx = (f"Historical Analysis for {ticker}. Current {current_year} YTD Return: {current_ytd:.2%}. "
           f"Over the last 5 years, the historically best performing month is {best_month} and the worst is {worst_month}.")
    run_sidebar_chatbot(ctx)

render_data_source_footer()

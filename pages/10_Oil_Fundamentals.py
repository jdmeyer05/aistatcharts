import os
import streamlit as st
import pandas as pd
import requests
import plotly.graph_objects as go
from src.auth import check_auth

st.set_page_config(page_title="Oil Fundamentals", layout="wide")
check_auth() # The firewall

st.title("🛢️ US Oil Fundamentals")
st.markdown("Live macroeconomic supply data directly from the Energy Information Administration (EIA).")

@st.cache_data(ttl=3600) # Cache for 1 hour to prevent spamming the API
def fetch_eia_data(series_id):
    """Fetches data from the EIA API v2."""
    api_key = os.environ.get("EIA_API_KEY")
    if not api_key:
        try:
            api_key = st.secrets["EIA_API_KEY"]
        except Exception:
            return None
            
    # Using the EIA v2 Series API endpoint
    url = f"https://api.eia.gov/v2/seriesid/{series_id}?api_key={api_key}"
    
    try:
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        
        # Extract and clean the timeseries data
        raw_data = data['response']['data']
        df = pd.DataFrame(raw_data)
        df['period'] = pd.to_datetime(df['period'])
        df = df.sort_values('period')
        df['value'] = pd.to_numeric(df['value'])
        
        # Calculate Week-over-Week change
        df['wow_change'] = df['value'].diff()
        
        return df.tail(156) # Return the last 3 years of weekly data
    except Exception as e:
        st.error(f"Failed to fetch EIA data: {e}")
        return None

# --- FETCH DATA ---
with st.spinner("Connecting to EIA Database..."):
    # PET.WCESTUS1.W = Weekly U.S. Ending Stocks of Commercial Crude Oil (Thousand Barrels)
    df_inv = fetch_eia_data("PET.WCESTUS1.W")
    
    # PET.WCRFPUS2.W = Weekly U.S. Field Production of Crude Oil (Thousand Barrels per Day)
    df_prod = fetch_eia_data("PET.WCRFPUS2.W")

# --- DASHBOARD RENDER ---
if df_inv is not None and df_prod is not None and not df_inv.empty:
    
    # Extract latest metrics
    latest_inv = df_inv.iloc[-1]
    latest_prod = df_prod.iloc[-1]
    
    # Format the metrics for the display (EIA reports in thousands, we display in millions for readability)
    inv_mb = latest_inv['value'] / 1000
    inv_wow = latest_inv['wow_change'] / 1000
    
    prod_mbpd = latest_prod['value'] / 1000
    prod_wow = latest_prod['wow_change'] / 1000

    st.subheader(f"Latest EIA Weekly Report: {latest_inv['period'].strftime('%Y-%m-%d')}")
    
    c1, c2, c3, c4 = st.columns(4)
    # A negative change in inventory is a "Draw" (Bullish), positive is a "Build" (Bearish)
    c1.metric("Commercial Inventories", f"{inv_mb:.1f}M bbls", f"{inv_wow:+.2f}M bbls (WoW)", delta_color="inverse")
    c2.metric("US Field Production", f"{prod_mbpd:.1f}M bpd", f"{prod_wow:+.2f}M bpd (WoW)")
    
    st.divider()
    
    # --- CHARTING ---
    tab1, tab2 = st.tabs(["Commercial Inventories", "US Production"])
    
    with tab1:
        fig_inv = go.Figure()
        fig_inv.add_trace(go.Scatter(
            x=df_inv['period'], y=df_inv['value'] / 1000, 
            mode='lines', line=dict(color='#ff9900', width=2), fill='tozeroy', fillcolor='rgba(255, 153, 0, 0.1)'
        ))
        fig_inv.update_layout(
            template="plotly_dark", height=400, margin=dict(t=10, b=0, l=0, r=0),
            yaxis_title="Millions of Barrels", hovermode="x unified"
        )
        st.plotly_chart(fig_inv, use_container_width=True)
        
    with tab2:
        fig_prod = go.Figure()
        fig_prod.add_trace(go.Scatter(
            x=df_prod['period'], y=df_prod['value'] / 1000, 
            mode='lines', line=dict(color='#00d1ff', width=2)
        ))
        fig_prod.update_layout(
            template="plotly_dark", height=400, margin=dict(t=10, b=0, l=0, r=0),
            yaxis_title="Millions of Barrels Per Day (bpd)", hovermode="x unified"
        )
        st.plotly_chart(fig_prod, use_container_width=True)

else:
    st.warning("EIA API Key is missing or invalid. Check your Google Cloud Run Environment Variables.")

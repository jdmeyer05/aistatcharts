import os
import streamlit as st
import pandas as pd
import requests
import plotly.graph_objects as go
from src.auth import check_auth

st.set_page_config(page_title="Natural Gas Fundamentals", layout="wide")
check_auth() # The firewall

st.title("🔥 Natural Gas Fundamentals")
st.markdown("Live weekly Working Gas in Underground Storage data from the Energy Information Administration (EIA).")

@st.cache_data(ttl=3600)
def fetch_eia_ng_data(series_id):
    """Fetches Natural Gas data from the EIA API v2."""
    api_key = os.environ.get("EIA_API_KEY")
    if not api_key:
        try:
            api_key = st.secrets["EIA_API_KEY"]
        except Exception:
            return None
            
    url = f"https://api.eia.gov/v2/seriesid/{series_id}?api_key={api_key}"
    
    try:
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        
        raw_data = data['response']['data']
        df = pd.DataFrame(raw_data)
        df['period'] = pd.to_datetime(df['period'])
        df = df.sort_values('period')
        df['value'] = pd.to_numeric(df['value'])
        
        # Calculate Week-over-Week change (Injections / Withdrawals)
        df['wow_change'] = df['value'].diff()
        
        # Return the last 5 years to clearly see the seasonal cycles
        return df.tail(260) 
    except Exception as e:
        st.error(f"Failed to fetch EIA data: {e}")
        return None

# --- FETCH DATA ---
with st.spinner("Connecting to EIA Database for Natural Gas..."):
    # NG.NW2_EPG0_SWO_R48_BCF.W = Weekly Lower 48 States Working Gas in Underground Storage (Bcf)
    df_storage = fetch_eia_ng_data("NG.NW2_EPG0_SWO_R48_BCF.W")

# --- DASHBOARD RENDER ---
if df_storage is not None and not df_storage.empty:
    
    latest_report = df_storage.iloc[-1]
    
    storage_bcf = latest_report['value']
    wow_bcf = latest_report['wow_change']
    
    # Logic for display terminology
    flow_type = "Injection" if wow_bcf > 0 else "Withdrawal"
    delta_color = "normal" if wow_bcf > 0 else "inverse"

    st.subheader(f"Latest EIA Thursday Report: {latest_report['period'].strftime('%Y-%m-%d')}")
    
    c1, c2, c3 = st.columns(3)
    c1.metric("Lower 48 Working Gas", f"{storage_bcf:,.0f} Bcf")
    c2.metric(f"Weekly Net {flow_type}", f"{wow_bcf:+.0f} Bcf", delta_color=delta_color)
    
    st.divider()
    
    # --- CHARTING ---
    tab1, tab2 = st.tabs(["Total Working Gas (Seasonality)", "Weekly Injections / Withdrawals"])
    
    with tab1:
        fig_total = go.Figure()
        fig_total.add_trace(go.Scatter(
            x=df_storage['period'], y=df_storage['value'], 
            mode='lines', line=dict(color='#ff4b4b', width=2), fill='tozeroy', fillcolor='rgba(255, 75, 75, 0.1)'
        ))
        fig_total.update_layout(
            template="plotly_dark", height=450, margin=dict(t=10, b=0, l=0, r=0),
            yaxis_title="Billion Cubic Feet (Bcf)", hovermode="x unified"
        )
        st.plotly_chart(fig_total, use_container_width=True)
        
    with tab2:
        fig_flow = go.Figure()
        
        # Color array: Green for Injections (>0), Red for Withdrawals (<0)
        colors = ['#00FF00' if val > 0 else '#FF0000' for val in df_storage['wow_change']]
        
        fig_flow.add_trace(go.Bar(
            x=df_storage['period'], y=df_storage['wow_change'], 
            marker_color=colors,
            hovertemplate="Date: %{x}<br>Net Flow: %{y} Bcf<extra></extra>"
        ))
        fig_flow.update_layout(
            template="plotly_dark", height=450, margin=dict(t=10, b=0, l=0, r=0),
            yaxis_title="Net Change (Bcf)",
            shapes=[dict(type='line', y0=0, y1=0, x0=df_storage['period'].min(), x1=df_storage['period'].max(), line=dict(color='white', width=1))]
        )
        st.plotly_chart(fig_flow, use_container_width=True)

else:
    st.warning("EIA API Key is missing or invalid. Check your Google Cloud Run Environment Variables.")

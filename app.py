import streamlit as st
import requests
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime

# --- SET PAGE CONFIG ---
st.set_page_config(page_title="ERCOT Grid Monitor", layout="wide")

st.title("⚡ ERCOT Live Grid Monitor")
st.subheader("Real-Time Load vs. Operating Reserves")

# --- DATA FETCHING ---
@st.cache_data(ttl=300)  # Refresh data every 5 minutes
def fetch_ercot_data():
    # Public ERCOT Dashboard API for Grid Conditions
    url = "https://www.ercot.com/api/1/services/read/dashboards/daily-prc.json"
    try:
        response = requests.get(url, timeout=10)
        data = response.json()
        
        # Extract the current condition object
        current = data.get("current_condition", {})
        
        # Example values (Actual keys may vary slightly based on ERCOT's API response)
        # Note: ERCOT usually returns strings with commas, so we clean them.
        reserves = float(current.get("prc_value", "0").replace(',', ''))
        
        # For Load and Capacity, we use their secondary endpoint
        load_url = "https://www.ercot.com/api/1/services/read/dashboards/supply-and-demand.json"
        load_resp = requests.get(load_url, timeout=10)
        load_data = load_resp.json()
        
        # Get the latest data point from the supply/demand graph
        latest_point = load_data['data'][0]['datasets'][-1]['data'][-1]
        current_load = float(latest_point.get('y', 0))
        
        return {
            "reserves": reserves,
            "load": current_load,
            "status": current.get("title", "Unknown"),
            "note": current.get("condition_note", ""),
            "last_update": datetime.now().strftime("%H:%M:%S")
        }
    except Exception as e:
        st.error(f"Error fetching data: {e}")
        return None

# --- UI LAYOUT ---
data = fetch_ercot_data()

if data:
    # Top Level Metrics
    col1, col2, col3 = st.columns(3)
    col1.metric("Current Load", f"{data['load']:,} MW")
    col2.metric("Operating Reserves (PRC)", f"{data['reserves']:,} MW", delta_color="inverse")
    col3.metric("Grid Status", data['status'])

    st.info(f"💡 **Current Condition:** {data['note']}")

    # Gauge Chart for Reserves
    fig = go.Figure(go.Indicator(
        mode = "gauge+number",
        value = data['reserves'],
        domain = {'x': [0, 1], 'y': [0, 1]},
        title = {'text': "Operating Reserves (MW)"},
        gauge = {
            'axis': {'range': [0, 10000]},
            'steps': [
                {'range': [0, 2500], 'color': "red"},
                {'range': [2500, 3000], 'color': "orange"},
                {'range': [3000, 10000], 'color': "green"}
            ],
            'threshold': {
                'line': {'color': "black", 'width': 4},
                'thickness': 0.75,
                'value': 2500
            }
        }
    ))
    
    st.plotly_chart(fig, use_container_width=True)

st.caption(f"Last successful update: {data['last_update'] if data else 'N/A'}")

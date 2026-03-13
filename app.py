import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import os
from massive import RESTClient  # Ensure 'massive-api-client' is in requirements.txt

# --- PAGE CONFIG ---
st.set_page_config(page_title="ERCOT Massive Analytics", layout="wide")

# --- SECURE AUTHENTICATION ---
# This pulls the key you just added to Google Cloud Environment Variables
api_key = os.environ.get("MASSIVE_API_KEY")

if not api_key:
    st.error("❌ MASSIVE_API_KEY not found in environment variables. Please check Google Cloud settings.")
    st.stop()

# Initialize Massive Client
client = RESTClient(api_key)

# --- UI HEADER ---
st.title("⚡ ERCOT Basis & Spread Analyzer")
st.subheader("Data-Driven Insights for Power Trading")

# --- SIDEBAR CONTROLS ---
st.sidebar.header("Analysis Settings")
lookback = st.sidebar.slider("Lookback Period (Days)", 1, 30, 7)
interval = st.sidebar.selectbox("Data Interval", ["hour", "minute", "day"])

# --- MAIN DASHBOARD ---
col1, col2 = st.columns([1, 1])

with col1:
    st.write("### Hub Selection")
    hub_a = st.selectbox("Select Hub A (Source)", ["HB_WEST", "HB_NORTH", "HB_SOUTH", "HB_HOUSTON"])
    hub_b = st.selectbox("Select Hub B (Sink)", ["HB_HOUSTON", "HB_WEST", "HB_NORTH", "HB_SOUTH"], index=0)

with col2:
    st.write("### Market Narrative")
    st.info(f"Analyzing the spread between **{hub_a}** and **{hub_b}**. "
            "Large spreads often indicate transmission congestion or localized demand spikes "
            "from AI Data Center clusters in the West.")

# --- FETCH & PLOT DATA ---
if st.button("Generate Spread Analysis"):
    try:
        # Placeholder for Massive API logic 
        # In production, you'd use: client.list_aggs(ticker=f"ERCOT.{hub_a}", ...)
        st.write(f"🔄 Querying Massive for {hub_a} and {hub_b}...")
        
        # Example Data Visualization
        # We'll create a mock spread chart until you verify your specific Massive tickers
        dates = pd.date_range(end=pd.Timestamp.now(), periods=100, freq='H')
        prices_a = [30 + (i % 10) for i in range(100)]
        prices_b = [25 + (i % 15) for i in range(100)]
        spread = [a - b for a, b in zip(prices_a, prices_b)]

        fig = go.Figure()
        
        # Add Spread Line
        fig.add_trace(go.Scatter(x=dates, y=spread, name="Spread (A-B)", 
                                 line=dict(color='cyan', width=2),
                                 fill='tozeroy'))
        
        fig.update_layout(
            title=f"Real-Time Basis Spread: {hub_a} vs {hub_b}",
            xaxis_title="Time",
            yaxis_title="USD/MWh",
            template="plotly_dark",
            hovermode="x unified"
        )
        
        st.plotly_chart(fig, use_container_width=True)
        
    except Exception as e:
        st.error(f"Error fetching data from Massive: {e}")

# --- RAW DATA PREVIEW ---
with st.expander("View Data Center Load Forecast Notes"):
    st.write("""
    **Current Market Context:**
    * West Zone congestion is increasing due to 70GW+ of AI/LFL (Large Flexible Load) queue entries.
    * Monitor HB_WEST volatility during low wind/high solar ramp periods.
    """)

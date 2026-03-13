import streamlit as st

st.set_page_config(page_title="AI Stat Charts - Energy Portal", layout="centered")

st.title("⚡ AI Stat Charts")
st.subheader("Advanced Analytics for ERCOT & Global Markets")

st.markdown("""
Welcome to the internal analytics portal. Use the sidebar on the left to navigate between our professional trading tools:

* **ERCOT Basis Analyzer:** Real-time Hub spreads and nodal price monitoring for the Texas grid.
* **Monte Carlo Dashboard:** Seasonality analysis and price simulations for commodities and crypto.
""")

st.info("👈 Open the sidebar to select a dashboard.")

# Optional: Add a small market summary or image here
st.image("https://www.ercot.com/content/wcm/connect/e542289f-24d3-494b-9e45-1c3f1e91240c/ERCOT_Logo_Horizontal_RGB.png?MOD=AJPERES&CACHEID=ROOTWORKSPACE-e542289f-24d3-494b-9e45-1c3f1e91240c-oN-v.6v", width=200)

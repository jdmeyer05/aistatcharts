import streamlit as st
import logging
import sys

# --- 1. CENTRAL LOGGING CONFIG ---
# This makes logs appear in the Streamlit Cloud "Manage App" console
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

# Silence noisy background libraries
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("yfinance").setLevel(logging.ERROR)

logger = logging.getLogger(__name__)
logger.info("App starting up...")

# MUST be the first Streamlit command
st.set_page_config(page_title="AI Stat Charts", layout="wide")

st.title("🚀 AI Stat Charts Dashboard")
st.markdown("""
Welcome to your unified trading and power market dashboard. 
Use the sidebar to navigate between specialized tools.
""")

st.info("👈 Select a tool from the sidebar to get started.")

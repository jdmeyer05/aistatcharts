import streamlit as st
import os
from supabase import create_client, Client
import logging

logger = logging.getLogger(__name__)

@st.cache_resource
def init_supabase() -> Client:
    """Initializes the Supabase client for Auth."""
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        st.error("🚨 Supabase credentials missing. Check environment variables.")
        st.stop()
    return create_client(url, key)

def check_auth():
    """
    Blocks access to the page if the user is not logged in.
    Drop this at the very top of every script in the pages/ folder.
    """
    if 'authenticated' not in st.session_state or not st.session_state['authenticated']:
        st.warning("🔒 This page is protected. Please log in from the Home page.")
        st.stop() # Immediately halts execution of the rest of the page

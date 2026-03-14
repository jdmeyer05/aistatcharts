import os
import streamlit as st
from supabase import create_client, Client

@st.cache_resource
def init_supabase() -> Client:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        url = st.secrets["SUPABASE_URL"]
        key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

def check_auth():
    """Security firewall. Drop this at the top of every page in the /pages folder."""
    if not st.session_state.get('authenticated', False):
        st.switch_page("app.py")

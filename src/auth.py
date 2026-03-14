import streamlit as st
from supabase import create_client, Client

@st.cache_resource
def init_supabase() -> Client:
    """Initialize and cache the Supabase client."""
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)

def check_auth():
    """
    Security firewall and UI override. 
    Drop this at the top of every page in the /pages folder.
    """
    # 1. Inject the CSS override on every page the firewall protects
    st.markdown(
        """
        <style>
        ul[data-testid="stSidebarNavItems"] li:nth-child(1) span { display: none; }
        ul[data-testid="stSidebarNavItems"] li:nth-child(1) a::after {
            content: "🏠 Home Page"; font-weight: 400; margin-left: 5px;
        }
        </style>
        """,
        unsafe_allow_html=True
    )
    
    # 2. Check if the user is actually logged in
    if not st.session_state.get('authenticated', False):
        st.switch_page("app.py")

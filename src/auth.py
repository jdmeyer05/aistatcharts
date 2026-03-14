import os
import streamlit as st
import stripe
import extra_streamlit_components as stx
from supabase import create_client, Client

@st.cache_resource
def init_supabase() -> Client:
    """Initialize and cache the Supabase client."""
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    
    if not url or not key:
        url = st.secrets["SUPABASE_URL"]
        key = st.secrets["SUPABASE_KEY"]
        
    return create_client(url, key)

def check_auth():
    """Security firewall that correctly yields to the browser to fetch cookies."""
    # 1. Fast Pass: Already authenticated in this active tab
    if st.session_state.get('authenticated', False):
        return

    # Initialize the React component on the frontend
    cookie_manager = stx.CookieManager()
    
    # Try to grab the cookie
    cached_email = cookie_manager.get(cookie="quant_user_session")

    if cached_email:
        # Success! The browser delivered the cookie.
        st.session_state['authenticated'] = True
        st.session_state['user_email'] = cached_email
        st.session_state['cookie_check_count'] = 0 
        st.rerun() # Refresh silently to load the engine
        
    else:
        # Initialize a counter to track our attempts
        if 'cookie_check_count' not in st.session_state:
            st.session_state['cookie_check_count'] = 0

        if st.session_state['cookie_check_count'] == 0:
            # First pass: We MUST yield control to the browser!
            st.session_state['cookie_check_count'] += 1
            st.markdown("<br><br><h3 style='text-align: center; color: #00d1ff;'>🔄 Reconnecting secure session...</h3>", unsafe_allow_html=True)
            
            # CRITICAL FIX: st.stop() halts Python so the frontend can render the component.
            # Once the component renders and finds the cookie, it will auto-rerun the script!
            st.stop() 
            
        else:
            # Second pass: If we reach here, the component rendered, auto-reran, and genuinely found no cookie.
            st.session_state['cookie_check_count'] = 0
            st.switch_page("app.py")


def verify_subscription(email: str, user_id: str):
    """
    JIT Clearing Engine: Cross-references user email with Stripe API 
    and updates the Supabase ledger automatically.
    """
    stripe_key = os.environ.get("STRIPE_SECRET_KEY")
    if not stripe_key:
        try:
            stripe_key = st.secrets["STRIPE_SECRET_KEY"]
        except:
            return "free"
            
    stripe.api_key = stripe_key

    try:
        customers = stripe.Customer.search(query=f"email:'{email}'")
        if not customers.data:
            return "free" 
            
        customer_id = customers.data[0].id
        subscriptions = stripe.Subscription.list(customer=customer_id, status="active")
        
        if subscriptions.data:
            supabase = init_supabase()
            supabase.table("subscriptions").upsert({
                "user_id": user_id,
                "email": email,
                "stripe_customer_id": customer_id,
                "status": "active",
                "plan_type": "basic"
            }).execute()
            
            return "active"
            
    except Exception as e:
        print(f"Stripe API Error: {e}")
        
    return "free"

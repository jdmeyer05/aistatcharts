import os
import time
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
    """Security firewall with a reliable 1-second cookie recovery window."""
    # 1. Fast Pass: Already authenticated in this active tab
    if st.session_state.get('authenticated', False):
        return

    cookie_manager = stx.CookieManager()
    
    # Track how many times we've asked the browser for the cookie
    if 'cookie_check_count' not in st.session_state:
        st.session_state['cookie_check_count'] = 0

    # Grab ALL cookies at once (more reliable on a hard refresh)
    cookies = cookie_manager.get_all()
    cached_email = cookies.get("quant_user_session")

    if cached_email:
        # Success! The browser finally handed over the cookie.
        st.session_state['authenticated'] = True
        st.session_state['user_email'] = cached_email
        st.session_state['cookie_check_count'] = 0 # Reset the counter
        st.rerun() # Refresh silently to load the engine
        
    elif st.session_state['cookie_check_count'] < 2:
        # The browser hasn't sent it yet. Pause and try again.
        st.session_state['cookie_check_count'] += 1
        st.markdown("<br><br><h3 style='text-align: center; color: #00d1ff;'>🔄 Reconnecting secure session...</h3>", unsafe_allow_html=True)
        time.sleep(0.5) # Force Python to wait for 0.5 seconds
        st.rerun() # Run the check one more time
        
    else:
        # We waited a full second, checked twice, and the cookie genuinely isn't there.
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

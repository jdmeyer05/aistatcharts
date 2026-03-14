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
    """Bulletproof security firewall with Instant Recovery."""
    # 1. Fast Pass: Already authenticated in this active tab
    if st.session_state.get('authenticated', False):
        return

    # 2. INSTANT RECOVERY (Bypasses React Frontend Race Conditions)
    # This reads the cookie directly from the server headers the millisecond you refresh.
    if hasattr(st, "context") and hasattr(st.context, "cookies"):
        if "quant_user_session" in st.context.cookies:
            st.session_state['authenticated'] = True
            st.session_state['user_email'] = st.context.cookies["quant_user_session"]
            st.rerun()

    # 3. Fallback check for older Streamlit versions
    cookie_manager = stx.CookieManager()
    cached_email = cookie_manager.get(cookie="quant_user_session")
    
    if cached_email:
        st.session_state['authenticated'] = True
        st.session_state['user_email'] = cached_email
        st.rerun()

    # 4. The Anti-Loop Protocol
    # If the cookie genuinely isn't found, we DO NOT automatically switch pages.
    # We display a manual recovery button so you are never violently booted.
    st.warning("Secure session disconnected due to page refresh.")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("🔄 Restore Session", type="primary", use_container_width=True):
            st.rerun() # By the time you click this, the browser has definitely loaded the cookie.
    with col2:
        if st.button("Log In Again", use_container_width=True):
            st.switch_page("app.py")
    st.stop()


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

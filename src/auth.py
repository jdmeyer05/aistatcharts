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
    """Security firewall with persistent cookie recovery across page refreshes."""
    # 1. Fast Pass: If already authenticated in this session, let them through instantly
    if st.session_state.get('authenticated', False):
        return

    # 2. Mount the cookie manager to check the browser's persistent storage
    cookie_manager = stx.CookieManager(key="firewall")
    cached_email = cookie_manager.get(cookie="quant_user_session")

    # 3. If the cookie is found, restore the session state immediately
    if cached_email:
        st.session_state['authenticated'] = True
        st.session_state['user_email'] = cached_email
        st.rerun() # Refresh the page silently to clear the loading state

    # 4. The Streamlit Race Condition Fix:
    # Third-party components take a split second to retrieve data from the browser.
    # If we check the cookie immediately on a fresh reload, it will always be None.
    # We must pause the script execution for one cycle to allow the cookie to arrive.
    if not st.session_state.get("cookie_loading_delay", False):
        st.session_state["cookie_loading_delay"] = True
        st.markdown("<br><br><h3 style='text-align: center; color: #00d1ff;'>🔄 Reconnecting secure session...</h3>", unsafe_allow_html=True)
        st.stop() # Halts the boot to wait for the cookie to trigger a rerun!

    # 5. If we reach this line, the delay finished and there is genuinely no cookie.
    st.session_state["cookie_loading_delay"] = False
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

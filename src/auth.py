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
    """Bulletproof security firewall with Automated Yield Recovery."""
    # 1. Fast Pass: Already authenticated in this active tab
    if st.session_state.get('authenticated', False):
        return

    # 2. INSTANT RECOVERY (Native Streamlit feature, completely bypasses the bug)
    if hasattr(st, "context") and hasattr(st.context, "cookies"):
        if "quant_user_session" in st.context.cookies:
            st.session_state['authenticated'] = True
            st.session_state['user_email'] = st.context.cookies["quant_user_session"]
            st.rerun()

    # 3. Fallback check using the component
    cookie_manager = stx.CookieManager()
    cached_email = cookie_manager.get(cookie="quant_user_session")
    
    if cached_email:
        st.session_state['authenticated'] = True
        st.session_state['user_email'] = cached_email
        if 'auth_yielded' in st.session_state:
            del st.session_state['auth_yielded']
        st.rerun()

    # 4. The Auto-Yield Protocol (Replaces the broken manual button!)
    # We yield control to the browser EXACTLY ONCE to let it load.
    if 'auth_yielded' not in st.session_state:
        st.session_state['auth_yielded'] = True
        st.markdown("<br><br><h3 style='text-align: center; color: #00d1ff;'>🔄 Synchronizing secure connection...</h3>", unsafe_allow_html=True)
        # st.stop() halts Python so the frontend can render and grab the cookie.
        st.stop() 

    # 5. The True Kick
    # If the cookie genuinely isn't there, boot to login.
    st.session_state['auth_yielded'] = False
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

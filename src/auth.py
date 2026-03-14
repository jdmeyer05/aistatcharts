import os
import streamlit as st
import stripe
from supabase import create_client, Client

@st.cache_resource
def init_supabase() -> Client:
    """Initialize and cache the Supabase client."""
    # Look for Cloud Run environment variables first
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    
    # Fallback for local development
    if not url or not key:
        url = st.secrets["SUPABASE_URL"]
        key = st.secrets["SUPABASE_KEY"]
        
    return create_client(url, key)

def check_auth():
    """Security firewall. Drop this at the top of every page in the /pages folder."""
    if not st.session_state.get('authenticated', False):
        st.switch_page("app.py")

def verify_subscription(email: str, user_id: str):
    """
    JIT Clearing Engine: Cross-references user email with Stripe API 
    and updates the Supabase ledger automatically.
    """
    # 1. Initialize Stripe Keys
    stripe_key = os.environ.get("STRIPE_SECRET_KEY")
    if not stripe_key:
        # Fallback to local secrets if environment variable isn't found
        try:
            stripe_key = st.secrets["STRIPE_SECRET_KEY"]
        except:
            return "free"
            
    stripe.api_key = stripe_key

    try:
        # 2. Search Stripe for this exact email
        customers = stripe.Customer.search(query=f"email:'{email}'")
        if not customers.data:
            return "free" # No Stripe account exists yet
            
        customer_id = customers.data[0].id
        
        # 3. Check if this customer has an active recurring payment
        subscriptions = stripe.Subscription.list(customer=customer_id, status="active")
        
        if subscriptions.data:
            # 4. Payment confirmed. Log it in the Supabase ledger.
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

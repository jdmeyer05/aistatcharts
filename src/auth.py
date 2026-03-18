import os
import logging
import streamlit as st
import stripe
from supabase import create_client, Client

logger = logging.getLogger(__name__)

_supabase_client = None


def _is_local_dev() -> bool:
    if os.environ.get("LOCAL_DEV", "").lower() == "true":
        return True
    try:
        return st.secrets.get("LOCAL_DEV", "").lower() == "true"
    except Exception:
        return False


def init_supabase() -> Client:
    """Initialize and cache the Supabase client."""
    if _is_local_dev():
        return None

    global _supabase_client
    if _supabase_client is not None:
        return _supabase_client

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")

    if not url or not key:
        try:
            url = st.secrets["SUPABASE_URL"]
            key = st.secrets["SUPABASE_KEY"]
        except Exception:
            logger.warning("Supabase credentials not found in env vars or secrets.toml")
            return None

    _supabase_client = create_client(url, key)
    return _supabase_client


def check_auth():
    """Auth firewall — recovers session from Supabase client on refresh."""
    supabase = init_supabase()

    # 0. Skip auth entirely if Supabase isn't configured (local dev)
    if supabase is None:
        st.session_state['authenticated'] = True
        st.session_state['user_email'] = "local-dev@preview"
        return

    # 1. Fast Pass: Already authenticated in this active tab
    if st.session_state.get('authenticated', False):
        return

    # 2. Recover from Supabase session (persists across refreshes via cached client)
    try:
        session = supabase.auth.get_session()
        if session:
            st.session_state['authenticated'] = True
            st.session_state['user_email'] = session.user.email
            return
    except Exception as e:
        logger.debug(f"Session recovery failed: {e}")

    # 3. No valid session — redirect to login
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
        except Exception:
            return "free"

    stripe.api_key = stripe_key

    try:
        customers = stripe.Customer.search(query=f"email:'{email}'")
        if not customers.data:
            return "free"

        customer_id = customers.data[0].id
        subscriptions = stripe.Subscription.list(customer=customer_id, status="active")

        if subscriptions.data:
            plan_type = subscriptions.data[0].get("items", {}).get("data", [{}])[0].get("price", {}).get("lookup_key", "basic")
            supabase = init_supabase()
            supabase.table("subscriptions").upsert({
                "user_id": user_id,
                "email": email,
                "stripe_customer_id": customer_id,
                "status": "active",
                "plan_type": plan_type or "basic"
            }).execute()

            return "active"

    except Exception as e:
        logger.error(f"Stripe API Error: {e}")

    return "free"

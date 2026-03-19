import os
import logging
import streamlit as st
import stripe
from supabase import create_client, Client

logger = logging.getLogger(__name__)

_supabase_client = None

# ─────────────────────────────────────────────
# SUBSCRIPTION TIERS
# ─────────────────────────────────────────────
TIERS = {
    "free": {
        "name": "Free",
        "pages": [
            "01_Summary", "05_Historical_Analysis", "06_Options_Analysis",
            "07_Options_Flow", "08_Options_Lab", "09_ML_Stock_Predictor",
            "10_Tech_Screener", "11_Algo_Backtester", "12_Monte_Carlo",
            "13_Power_Risk_VaR", "14_Oil_Fundamentals", "15_NatGas_Fundamentals",
            "16_ERCOT_Power", "17_ERCOT_Capacity", "18_Economic_Calendar",
            "19_Iran_Conflict", "20_Futures",
        ],
        "daily_ai_analyses": 0,
        "ai_models": [],
        "rl_enabled": False,
    },
    "pro": {
        "name": "Pro",
        "pages": "__all__",  # all pages
        "daily_ai_analyses": 5,
        "ai_models": ["openai"],  # GPT-4o only
        "rl_enabled": False,
    },
    "premium": {
        "name": "Premium",
        "pages": "__all__",
        "daily_ai_analyses": 50,
        "ai_models": ["grok", "openai", "gemini", "claude"],
        "rl_enabled": True,
    },
    "institutional": {
        "name": "Institutional",
        "pages": "__all__",
        "daily_ai_analyses": -1,  # unlimited
        "ai_models": ["grok", "openai", "gemini", "claude"],
        "rl_enabled": True,
    },
}

# Admin emails always get institutional access
ADMIN_EMAILS = {"jdmeyer05@gmail.com", "local-dev@preview"}


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


def get_user_tier() -> str:
    """Get the current user's subscription tier. Checks admin list, then Stripe, then defaults to free."""
    email = st.session_state.get("user_email", "")

    # Admin override
    if email in ADMIN_EMAILS:
        return "institutional"

    # Check cached tier in session state
    if "user_tier" in st.session_state:
        return st.session_state["user_tier"]

    # Check Stripe
    tier = verify_subscription(email, email)
    if tier == "active":
        st.session_state["user_tier"] = "premium"
        return "premium"

    st.session_state["user_tier"] = "free"
    return "free"


def get_tier_config(tier: str = None) -> dict:
    """Get the configuration for a tier."""
    if tier is None:
        tier = get_user_tier()
    return TIERS.get(tier, TIERS["free"])


def check_page_access(page_key: str) -> bool:
    """Check if the current user's tier allows access to this page."""
    tier = get_user_tier()
    config = TIERS.get(tier, TIERS["free"])
    if config["pages"] == "__all__":
        return True
    return page_key in config["pages"]


def check_ai_quota() -> bool:
    """Check if user has AI analysis quota remaining today."""
    tier = get_user_tier()
    config = TIERS.get(tier, TIERS["free"])
    limit = config["daily_ai_analyses"]
    if limit == -1:
        return True
    if limit == 0:
        return False
    from datetime import date
    today = date.today().isoformat()
    used = st.session_state.get(f"ai_usage_{today}", 0)
    return used < limit


def increment_ai_usage():
    """Increment the daily AI analysis counter."""
    from datetime import date
    today = date.today().isoformat()
    key = f"ai_usage_{today}"
    st.session_state[key] = st.session_state.get(key, 0) + 1


def get_allowed_models() -> list:
    """Get the list of AI models this user's tier allows."""
    config = get_tier_config()
    return config["ai_models"]


def render_upgrade_prompt(feature_name: str = "this feature"):
    """Render a styled upgrade prompt when a user hits a tier gate."""
    tier = get_user_tier()
    st.markdown(
        f'<div style="background:rgba(0,209,255,0.08);border:1px solid #00d1ff;'
        f'border-radius:8px;padding:20px;text-align:center;margin:20px 0;">'
        f'<h3 style="color:#00d1ff;margin:0 0 8px 0;">Upgrade Required</h3>'
        f'<p style="color:#aaa;margin:0 0 12px 0;">'
        f'{feature_name} is not available on the <strong>{TIERS[tier]["name"]}</strong> plan.</p>'
        f'<p style="color:#888;font-size:0.85rem;">'
        f'Upgrade to <strong>Pro</strong> ($29/mo) or <strong>Premium</strong> ($79/mo) '
        f'for full access to AI-powered analysis.</p>'
        f'</div>',
        unsafe_allow_html=True,
    )

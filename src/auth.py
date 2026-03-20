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
        "pages": "__all__",
        "daily_ai_analyses": 5,
        "ai_models": ["grok", "gemini", "claude"],
        "rl_enabled": False,
    },
    "premium": {
        "name": "Premium",
        "pages": "__all__",
        "daily_ai_analyses": 20,
        "ai_models": ["grok", "gemini", "claude"],
        "rl_enabled": True,
    },
    "platinum": {
        "name": "Platinum",
        "pages": "__all__",
        "daily_ai_analyses": 50,
        "ai_models": ["grok", "openai", "gemini", "claude"],
        "rl_enabled": True,
    },
}

# ─────────────────────────────────────────────
# TOKEN SYSTEM
# ─────────────────────────────────────────────
TOKEN_PACKS = {
    "starter": {"name": "Starter", "tokens": 50, "price": 5.00, "per_token": 0.10},
    "power": {"name": "Power", "tokens": 200, "price": 15.00, "per_token": 0.075},
    "elite": {"name": "Elite", "tokens": 500, "price": 30.00, "per_token": 0.06},
}

# Admin emails always get platinum access
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


# Stripe price lookup_key → tier mapping
# Set these lookup_keys when creating prices in Stripe Dashboard
STRIPE_TIER_MAP = {
    "pro": "pro",
    "pro_monthly": "pro",
    "pro_yearly": "pro",
    "premium": "premium",
    "premium_monthly": "premium",
    "premium_yearly": "premium",
    "platinum": "platinum",
    "platinum_monthly": "platinum",
    "platinum_yearly": "platinum",
}


def verify_subscription(email: str, user_id: str) -> str:
    """
    Cross-references user email with Stripe API to determine subscription tier.
    Updates Supabase ledger. Returns the tier name (free/pro/premium/platinum).
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

        if not subscriptions.data:
            return "free"

        # Get the lookup_key from the active subscription's price
        sub = subscriptions.data[0]
        items = sub.get("items", {}).get("data", [])
        lookup_key = ""
        price_id = ""
        if items:
            price = items[0].get("price", {})
            lookup_key = price.get("lookup_key", "")
            price_id = price.get("id", "")

        # Map lookup_key to tier
        tier = STRIPE_TIER_MAP.get(lookup_key, "pro")  # default to pro if unknown active sub

        # Sync to Supabase
        supabase = init_supabase()
        supabase.table("subscriptions").upsert({
            "user_id": user_id,
            "email": email,
            "stripe_customer_id": customer_id,
            "stripe_price_id": price_id,
            "status": "active",
            "plan_type": tier,
        }).execute()

        return tier

    except Exception as e:
        logger.error(f"Stripe API Error: {e}")

    return "free"


def get_user_tier() -> str:
    """Get the current user's subscription tier. Checks admin list, then Stripe, then defaults to free."""
    email = st.session_state.get("user_email", "")

    # Admin override
    if email in ADMIN_EMAILS:
        return "platinum"

    # Check cached tier in session state
    if "user_tier" in st.session_state:
        return st.session_state["user_tier"]

    # Check Stripe
    tier = verify_subscription(email, email)
    st.session_state["user_tier"] = tier
    return tier


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


def get_daily_usage() -> int:
    """Get today's AI analysis usage count."""
    from datetime import date
    return st.session_state.get(f"ai_usage_{date.today().isoformat()}", 0)


def get_daily_limit() -> int:
    """Get the daily AI analysis limit for the current tier."""
    config = TIERS.get(get_user_tier(), TIERS["free"])
    return config["daily_ai_analyses"]


def get_token_balance() -> int:
    """Get the user's purchased token balance."""
    return st.session_state.get("token_balance", 0)


def check_ai_quota() -> bool:
    """Check if user has AI analysis quota remaining (daily allowance OR tokens)."""
    tier = get_user_tier()
    config = TIERS.get(tier, TIERS["free"])
    limit = config["daily_ai_analyses"]

    if limit == 0:
        # Free tier — can still use purchased tokens
        return get_token_balance() > 0

    # Check daily included allowance first
    from datetime import date
    today = date.today().isoformat()
    used = st.session_state.get(f"ai_usage_{today}", 0)
    if used < limit:
        return True

    # Daily limit reached — check token balance
    return get_token_balance() > 0


def increment_ai_usage():
    """Use one AI analysis — deducts from daily allowance first, then tokens."""
    from datetime import date
    today = date.today().isoformat()
    key = f"ai_usage_{today}"
    used = st.session_state.get(key, 0)

    tier = get_user_tier()
    limit = TIERS.get(tier, TIERS["free"])["daily_ai_analyses"]

    if used < limit:
        # Still within daily included allowance
        st.session_state[key] = used + 1
    else:
        # Deduct from token balance
        balance = get_token_balance()
        if balance > 0:
            st.session_state["token_balance"] = balance - 1


def add_tokens(amount: int):
    """Add purchased tokens to the user's balance."""
    current = get_token_balance()
    st.session_state["token_balance"] = current + amount


def get_usage_summary() -> dict:
    """Get a summary of today's usage and remaining capacity."""
    from datetime import date
    today = date.today().isoformat()
    used = st.session_state.get(f"ai_usage_{today}", 0)
    limit = get_daily_limit()
    tokens = get_token_balance()
    tier = get_user_tier()

    daily_remaining = max(0, limit - used) if limit > 0 else 0
    total_remaining = daily_remaining + tokens

    return {
        "tier": tier,
        "daily_used": used,
        "daily_limit": limit,
        "daily_remaining": daily_remaining,
        "tokens": tokens,
        "total_remaining": total_remaining,
        "source": "included" if daily_remaining > 0 else ("tokens" if tokens > 0 else "none"),
    }


def get_allowed_models() -> list:
    """Get the list of AI models this user's tier allows."""
    config = get_tier_config()
    return config["ai_models"]


def render_upgrade_prompt(feature_name: str = "this feature"):
    """Render a styled upgrade prompt when a user hits a tier gate."""
    tier = get_user_tier()
    tokens = get_token_balance()

    st.markdown(
        f'<div style="background:rgba(0,209,255,0.08);border:1px solid #00d1ff;'
        f'border-radius:8px;padding:20px;text-align:center;margin:20px 0;">'
        f'<h3 style="color:#00d1ff;margin:0 0 8px 0;">Upgrade Required</h3>'
        f'<p style="color:#aaa;margin:0 0 12px 0;">'
        f'{feature_name} is not available on the <strong>{TIERS[tier]["name"]}</strong> plan.</p>'
        f'<p style="color:#888;font-size:0.85rem;">'
        f'Upgrade to <strong>Pro</strong> ($12/mo), <strong>Premium</strong> ($29/mo), '
        f'or <strong>Platinum</strong> ($79/mo) for full access.</p>'
        f'</div>',
        unsafe_allow_html=True,
    )


def render_quota_exceeded():
    """Show when a user has used all daily analyses and has no tokens."""
    summary = get_usage_summary()
    st.markdown(
        f'<div style="background:rgba(255,170,0,0.08);border:1px solid #ffaa00;'
        f'border-radius:8px;padding:16px;text-align:center;margin:12px 0;">'
        f'<div style="color:#ffaa00;font-weight:bold;font-size:16px;">Daily Limit Reached</div>'
        f'<p style="color:#aaa;margin:6px 0;">You\'ve used {summary["daily_used"]}/{summary["daily_limit"]} '
        f'included analyses today and have {summary["tokens"]} tokens remaining.</p>'
        f'<p style="color:#888;font-size:0.85rem;">Buy tokens below to continue, or wait until tomorrow for your allowance to reset.</p>'
        f'</div>',
        unsafe_allow_html=True,
    )
    render_token_purchase()


def render_token_purchase():
    """Render the token purchase UI."""
    st.markdown("#### Buy Analysis Tokens")
    st.caption("Tokens let you run AI analyses beyond your daily included allowance. They never expire.")

    balance = get_token_balance()
    st.markdown(f'<div style="text-align:center; padding:8px; border:1px solid #00d1ff; border-radius:6px; margin-bottom:12px;">'
                f'<span style="color:#888;">Current Balance:</span> '
                f'<span style="font-size:20px; font-weight:bold; color:#00d1ff;">{balance} tokens</span></div>',
                unsafe_allow_html=True)

    pack_cols = st.columns(len(TOKEN_PACKS))
    for col, (pack_id, pack) in zip(pack_cols, TOKEN_PACKS.items()):
        with col:
            st.markdown(f"""<div style="text-align:center; padding:12px; border:1px solid #30363d; border-radius:8px;">
                <div style="font-size:16px; font-weight:bold; color:#e0e0e0;">{pack['name']}</div>
                <div style="font-size:28px; font-weight:bold; color:#00d1ff; margin:8px 0;">{pack['tokens']}</div>
                <div style="color:#888; font-size:12px;">tokens</div>
                <div style="font-size:18px; font-weight:bold; color:#00ff96; margin:8px 0;">${pack['price']:.0f}</div>
                <div style="color:#888; font-size:11px;">${pack['per_token']:.3f}/token</div>
            </div>""", unsafe_allow_html=True)
            if st.button(f"Buy {pack['name']}", key=f"buy_{pack_id}", use_container_width=True):
                # In production, this would create a Stripe Checkout Session
                # For now, add tokens directly (dev mode)
                add_tokens(pack['tokens'])
                st.success(f"Added {pack['tokens']} tokens! Balance: {get_token_balance()}")
                st.rerun()


def render_quota_status():
    """Render a compact usage/quota indicator."""
    summary = get_usage_summary()
    tier = summary["tier"]
    if tier == "free" and summary["tokens"] == 0:
        return  # Don't show for free users with no tokens

    daily_str = f"{summary['daily_used']}/{summary['daily_limit']}" if summary['daily_limit'] > 0 else "—"
    token_str = f"{summary['tokens']} tokens" if summary['tokens'] > 0 else ""

    parts = [f"Today: {daily_str}"]
    if token_str:
        parts.append(token_str)

    color = "#00ff96" if summary['total_remaining'] > 5 else "#ffaa00" if summary['total_remaining'] > 0 else "#ff4444"

    st.markdown(f'<div style="font-size:11px; color:{color}; text-align:right;">'
                f'{" | ".join(parts)}</div>', unsafe_allow_html=True)

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
            "19_Iran_Conflict", "20_Futures", "21_Fed_Macro_Drivers",
            "22_Smart_Money", "23_Power_Analytics", "24_Energy_Sector",
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
        "ai_models": ["grok", "gemini", "claude"],
        "rl_enabled": True,
    },
}

# ─────────────────────────────────────────────
# TOKEN SYSTEM
# ─────────────────────────────────────────────
TOKEN_PACKS = {
    "starter": {"name": "Starter", "tokens": 50, "price": 8.00, "per_token": 0.16},
    "power": {"name": "Power", "tokens": 200, "price": 25.00, "per_token": 0.125},
    "elite": {"name": "Elite", "tokens": 500, "price": 50.00, "per_token": 0.10},
}

# ─────────────────────────────────────────────
# STRIPE PAYMENT LINKS
# Replace these with real Stripe Payment Link URLs from your dashboard
# ─────────────────────────────────────────────
STRIPE_LINKS = {
    # Subscription plans
    "pro": "https://buy.stripe.com/dRm8wIcVmdGgbzcaWpasg00",
    "premium": "https://buy.stripe.com/eVq4gs3kM45G32G0hLasg01",
    "platinum": "https://buy.stripe.com/5kQ7sEf3u0TueLo4y1asg02",
    # Token packs
    "tokens_starter": "https://buy.stripe.com/fZu4gs8F67hS8n07Kdasg05",
    "tokens_power": "https://buy.stripe.com/bJedR27B2au49r49Slasg04",
    "tokens_elite": "https://buy.stripe.com/7sY8wI8F6au4gTw8Ohasg03",
    # Customer portal (manage subscription)
    "portal": "https://billing.stripe.com/p/login/dRm8wIcVmdGgbzcaWpasg00",
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


def _sanitize_token(token: str) -> str:
    """Strip characters that could break cookie value or enable JS injection."""
    import re
    return re.sub(r'[^A-Za-z0-9_\-.]', '', token)


def set_auth_cookie(refresh_token: str):
    """Store Supabase refresh token in a browser cookie (30-day persistence)."""
    import streamlit.components.v1 as components
    safe_token = _sanitize_token(refresh_token)
    components.html(
        f'<script>document.cookie="sb_refresh={safe_token};path=/;max-age={30*24*3600};SameSite=Strict;Secure";</script>',
        height=0,
    )


def set_auth_cookie_session(refresh_token: str):
    """Store Supabase refresh token in a session cookie (expires when browser closes)."""
    import streamlit.components.v1 as components
    safe_token = _sanitize_token(refresh_token)
    components.html(
        f'<script>document.cookie="sb_refresh={safe_token};path=/;SameSite=Strict;Secure";</script>',
        height=0,
    )


def clear_auth_cookie():
    """Clear the auth cookie (call on logout)."""
    import streamlit.components.v1 as components
    components.html(
        '<script>document.cookie="sb_refresh=;path=/;max-age=0";</script>',
        height=0,
    )


def check_session_timeout():
    """Show a warning toast if the session is about to expire."""
    from datetime import datetime
    auth_time = st.session_state.get("_auth_timestamp")
    if not auth_time:
        return
    elapsed_min = (datetime.now() - auth_time).total_seconds() / 60
    # Supabase access tokens expire after ~60 min; warn at 50 min
    if 50 <= elapsed_min < 60:
        st.toast("Session expires soon — save your work. The page will re-authenticate automatically.", icon="⏰")
    elif elapsed_min >= 60:
        # Try silent re-auth via browser cookie (per-user)
        supabase = init_supabase()
        if supabase:
            try:
                refresh_token = st.context.cookies.get("sb_refresh")
                if refresh_token:
                    response = supabase.auth.refresh_session(refresh_token)
                    if response and response.session:
                        st.session_state["_auth_timestamp"] = datetime.now()
                        set_auth_cookie(response.session.refresh_token)
                        return
            except Exception:
                pass
        st.toast("Session expired — refreshing authentication...", icon="🔒")


def check_auth():
    """Auth firewall — recovers session from Supabase client on refresh.
    Falls back to browser cookie refresh token for mobile session recovery."""
    # OPEN BETA: Allow all visitors without login — remove this block when ready to require auth
    st.session_state['authenticated'] = True
    st.session_state.setdefault('user_email', "guest@open-beta")
    return

    from datetime import datetime
    supabase = init_supabase()

    # 0. Skip auth entirely if Supabase isn't configured (local dev)
    if supabase is None:
        st.session_state['authenticated'] = True
        st.session_state['user_email'] = "local-dev@preview"
        return

    # 1. Fast Pass: Already authenticated in this active tab
    if st.session_state.get('authenticated', False):
        check_session_timeout()
        return

    # 2. Recover from browser cookie (per-user, not shared across sessions)
    try:
        refresh_token = st.context.cookies.get("sb_refresh")
        if refresh_token:
            response = supabase.auth.refresh_session(refresh_token)
            if response and response.session:
                st.session_state['authenticated'] = True
                st.session_state['user_email'] = response.session.user.email
                st.session_state['_auth_timestamp'] = datetime.now()
                set_auth_cookie(response.session.refresh_token)
                return
    except Exception as e:
        logger.debug(f"Cookie session recovery failed: {e}")

    # 4. No valid session — redirect to login
    st.switch_page("app.py")


# Stripe tier mapping — checks price metadata, lookup_key, and product name
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

        # Determine tier from active subscription price
        sub = subscriptions.data[0]
        items = sub.get("items", {}).get("data", [])
        tier = "pro"  # default if we can't determine
        price_id = ""
        if items:
            price = items[0].get("price", {})
            price_id = price.get("id", "")
            metadata = price.get("metadata", {})

            # Priority 1: price metadata "tier" field
            if metadata.get("tier"):
                tier = STRIPE_TIER_MAP.get(metadata["tier"], metadata["tier"])
            # Priority 2: lookup_key
            elif price.get("lookup_key"):
                tier = STRIPE_TIER_MAP.get(price["lookup_key"], "pro")
            # Priority 3: check product name for tier keywords
            else:
                product_id = price.get("product", "")
                if product_id:
                    try:
                        product = stripe.Product.retrieve(product_id)
                        pname = product.get("name", "").lower()
                        if "platinum" in pname:
                            tier = "platinum"
                        elif "premium" in pname:
                            tier = "premium"
                        elif "pro" in pname:
                            tier = "pro"
                    except Exception:
                        pass

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
    """Get the current user's subscription tier.
    Priority: admin list → session cache → Supabase (set by webhook) → Stripe API → free."""
    # OPEN BETA: All users get full access — remove this line when ready to monetize
    return "platinum"

    email = st.session_state.get("user_email", "")

    # Admin override
    if email in ADMIN_EMAILS:
        return "platinum"

    # Check cached tier in session state
    if "user_tier" in st.session_state:
        return st.session_state["user_tier"]

    # Check Supabase first (updated by webhook in real-time)
    supabase = init_supabase()
    if supabase and email:
        try:
            result = supabase.table("subscriptions").select("plan_type").eq("email", email).eq("status", "active").execute()
            if result.data:
                tier = result.data[0]["plan_type"]
                if tier in TIERS:
                    st.session_state["user_tier"] = tier
                    return tier
        except Exception:
            pass

    # Fallback: check Stripe API directly
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
    """Get the user's purchased token balance. Loads from Supabase on first call."""
    if "token_balance" not in st.session_state:
        # Try loading from Supabase
        email = st.session_state.get("user_email", "")
        supabase = init_supabase()
        if supabase and email:
            try:
                result = supabase.table("user_tokens").select("balance").eq("email", email).execute()
                if result.data:
                    st.session_state["token_balance"] = result.data[0]["balance"]
                else:
                    st.session_state["token_balance"] = 0
            except Exception:
                st.session_state["token_balance"] = 0
        else:
            st.session_state["token_balance"] = 0
    return st.session_state.get("token_balance", 0)


def check_ai_quota() -> bool:
    """Check if user has AI analysis quota remaining (daily allowance OR tokens)."""
    # OPEN BETA: Unlimited AI analyses — remove this line when ready to monetize
    return True
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
        st.session_state[key] = used + 1
    else:
        # Deduct from token balance and persist
        balance = get_token_balance()
        if balance > 0:
            new_balance = balance - 1
            st.session_state["token_balance"] = new_balance
            email = st.session_state.get("user_email", "")
            supabase = init_supabase()
            if supabase and email:
                try:
                    supabase.table("user_tokens").upsert({
                        "email": email,
                        "balance": new_balance,
                    }, on_conflict="email").execute()
                except Exception:
                    pass


def add_tokens(amount: int):
    """Add purchased tokens to the user's balance. Persists to Supabase."""
    current = get_token_balance()
    new_balance = current + amount
    st.session_state["token_balance"] = new_balance

    # Persist to Supabase
    email = st.session_state.get("user_email", "")
    supabase = init_supabase()
    if supabase and email:
        try:
            supabase.table("user_tokens").upsert({
                "email": email,
                "balance": new_balance,
            }, on_conflict="email").execute()
        except Exception as e:
            logger.warning(f"Failed to persist token balance: {e}")


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
    # OPEN BETA: No upgrade prompts — remove this line when ready to monetize
    return
    tier = get_user_tier()

    st.markdown(
        f'<div style="background:rgba(0,209,255,0.08);border:1px solid #00d1ff;'
        f'border-radius:8px;padding:20px;text-align:center;margin:20px 0;">'
        f'<h3 style="color:#00d1ff;margin:0 0 8px 0;">Upgrade Required</h3>'
        f'<p style="color:#aaa;margin:0 0 16px 0;">'
        f'{feature_name} is not available on the <strong>{TIERS[tier]["name"]}</strong> plan.</p>'
        f'</div>',
        unsafe_allow_html=True,
    )
    render_pricing_cards(current_tier=tier)


def render_pricing_cards(current_tier: str = "free"):
    """Render subscription plan cards with Stripe checkout links."""
    # OPEN BETA: No pricing cards — remove this line when ready to monetize
    return
    plans = [
        {"key": "pro", "name": "Pro", "price": "$12", "period": "/mo",
         "features": ["All 20 pages", "5 AI analyses/day", "3 AI models", "Unlimited chat (Gemini)"],
         "color": "#00d1ff"},
        {"key": "premium", "name": "Premium", "price": "$29", "period": "/mo",
         "features": ["All 20 pages", "20 AI analyses/day", "3 AI models", "RL Trading", "Unlimited chat"],
         "color": "#ffaa00"},
        {"key": "platinum", "name": "Platinum", "price": "$79", "period": "/mo",
         "features": ["All 20 pages", "50 AI analyses/day", "3 AI models + Claude Opus", "RL Trading", "Unlimited chat"],
         "color": "#00ff96"},
    ]

    cols = st.columns(len(plans))
    for col, plan in zip(cols, plans):
        is_current = plan["key"] == current_tier
        border = plan["color"] if not is_current else "#30363d"
        with col:
            features_html = "".join(f'<div style="color:#ccc; font-size:12px; padding:2px 0;">&#10003; {f}</div>' for f in plan["features"])
            badge = '<div style="color:#888; font-size:10px; margin-top:4px;">CURRENT PLAN</div>' if is_current else ""
            st.markdown(
                f'<div style="text-align:center; padding:16px; border:1px solid {border}; border-radius:8px;">'
                f'<div style="font-size:18px; font-weight:bold; color:{plan["color"]};">{plan["name"]}</div>'
                f'<div style="font-size:28px; font-weight:bold; color:white; margin:8px 0;">{plan["price"]}<span style="font-size:14px; color:#888;">{plan["period"]}</span></div>'
                f'{features_html}{badge}</div>',
                unsafe_allow_html=True,
            )
            if not is_current:
                link = STRIPE_LINKS.get(plan["key"], "#")
                st.link_button(f"Subscribe to {plan['name']}", link, use_container_width=True)


def render_quota_exceeded():
    """Show when a user has used all daily analyses and has no tokens."""
    # OPEN BETA: No quota limits — remove this line when ready to monetize
    return
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
    """Render the token purchase UI with Stripe links."""
    # OPEN BETA: No token purchases — remove this line when ready to monetize
    return
    st.markdown("#### Buy Analysis Tokens")
    st.caption("Tokens let you run AI analyses beyond your daily included allowance. They never expire.")

    balance = get_token_balance()
    st.markdown(
        f'<div style="text-align:center; padding:8px; border:1px solid #00d1ff; border-radius:6px; margin-bottom:12px;">'
        f'<span style="color:#888;">Current Balance:</span> '
        f'<span style="font-size:20px; font-weight:bold; color:#00d1ff;">{balance} tokens</span></div>',
        unsafe_allow_html=True,
    )

    pack_cols = st.columns(len(TOKEN_PACKS))
    stripe_keys = {"starter": "tokens_starter", "power": "tokens_power", "elite": "tokens_elite"}
    for col, (pack_id, pack) in zip(pack_cols, TOKEN_PACKS.items()):
        with col:
            st.markdown(
                f'<div style="text-align:center; padding:12px; border:1px solid #30363d; border-radius:8px;">'
                f'<div style="font-size:16px; font-weight:bold; color:#e0e0e0;">{pack["name"]}</div>'
                f'<div style="font-size:28px; font-weight:bold; color:#00d1ff; margin:8px 0;">{pack["tokens"]}</div>'
                f'<div style="color:#888; font-size:12px;">tokens</div>'
                f'<div style="font-size:18px; font-weight:bold; color:#00ff96; margin:8px 0;">${pack["price"]:.0f}</div>'
                f'<div style="color:#888; font-size:11px;">${pack["per_token"]:.3f}/token</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
            link = STRIPE_LINKS.get(stripe_keys.get(pack_id, ""), "#")
            if _is_local_dev():
                # Dev mode — add tokens directly for testing
                if st.button(f"Buy {pack['name']} (dev)", key=f"buy_{pack_id}", use_container_width=True):
                    add_tokens(pack['tokens'])
                    st.success(f"Added {pack['tokens']} tokens!")
                    st.rerun()
            else:
                st.link_button(f"Buy {pack['name']}", link, use_container_width=True)


def check_payment_failures() -> bool:
    """Check if the user has unresolved payment failures. Returns True if there's a problem."""
    email = st.session_state.get("user_email", "")
    supabase = init_supabase()
    if not supabase or not email:
        return False
    try:
        result = supabase.table("payment_failures").select("*").eq("email", email).eq("resolved", False).execute()
        if result.data:
            st.markdown(
                '<div style="background:rgba(255,68,68,0.1);border:1px solid #ff4444;'
                'border-radius:8px;padding:12px;text-align:center;margin:8px 0;">'
                '<span style="color:#ff4444;font-weight:bold;">Payment Failed</span> — '
                'Your last payment could not be processed. Please update your payment method to avoid losing access. '
                f'<a href="{STRIPE_LINKS.get("portal", "#")}" target="_blank" style="color:#00d1ff;">Update Payment</a>'
                '</div>',
                unsafe_allow_html=True,
            )
            return True
    except Exception:
        pass
    return False


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

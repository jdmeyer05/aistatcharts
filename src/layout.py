"""Shared layout helpers for AI Statcharts — sidebar, status bar, error boundaries, page setup."""
import streamlit as st
import logging
from datetime import datetime, timezone
from contextlib import contextmanager
from src.styles import COLORS, APP_VERSION, inject_global_css
from src.ticker_tape import render_ticker_tape
from src.auth import check_auth, check_page_access, render_upgrade_prompt, get_user_tier, get_tier_config

logger = logging.getLogger(__name__)

# Page registry: filename key → (title, icon)
PAGE_CONFIG = {
    "01_Summary":            ("Summary | AI Statcharts", "📈"),
    "02_Scenario_Analysis":  ("Scenario Analysis | AI Statcharts", "🔬"),
    "03_Stock_Analysis":     ("Stock Analysis | AI Statcharts", "🔍"),
    "04_RL_Trading":         ("RL Trading | AI Statcharts", "🧠"),
    "05_Historical_Analysis":("Historical Analysis | AI Statcharts", "📊"),
    "06_Options_Analysis":   ("Options Analysis | AI Statcharts", "📉"),
    "07_Options_Flow":       ("Options Flow | AI Statcharts", "🌊"),
    "08_Options_Lab":        ("Options Lab | AI Statcharts", "🧪"),
    "09_ML_Stock_Predictor": ("ML Predictor | AI Statcharts", "🤖"),
    "10_Tech_Screener":      ("Tech Screener | AI Statcharts", "📡"),
    "11_Algo_Backtester":    ("Algo Backtester | AI Statcharts", "⚙️"),
    "12_Monte_Carlo":        ("Monte Carlo | AI Statcharts", "🎲"),
    "13_Power_Risk_VaR":     ("Portfolio VaR | AI Statcharts", "🛡️"),
    "14_Oil_Fundamentals":   ("Oil Fundamentals | AI Statcharts", "🛢️"),
    "15_NatGas_Fundamentals":("NatGas Fundamentals | AI Statcharts", "🔥"),
    "16_ERCOT_Power":        ("ERCOT Power | AI Statcharts", "⚡"),
    "17_ERCOT_Capacity":     ("ERCOT Capacity | AI Statcharts", "🏗️"),
    "18_Economic_Calendar":  ("Economic Calendar | AI Statcharts", "📅"),
    "19_Iran_Conflict":      ("Iran Conflict | AI Statcharts", "🌍"),
    "20_Futures":            ("Futures | AI Statcharts", "📋"),
}


def setup_page(page_key: str, layout: str = "wide", sidebar_state: str = "collapsed"):
    """Universal page setup. Call this as the FIRST thing on every page.
    Handles: page_config, auth, global CSS, sidebar brand, ticker tape, status bar, tier gating."""
    title, icon = PAGE_CONFIG.get(page_key, ("AI Statcharts", "📊"))
    st.set_page_config(page_title=title, page_icon=icon, layout=layout,
                       initial_sidebar_state=sidebar_state)
    check_auth()
    inject_global_css()
    render_sidebar_brand()
    render_ticker_tape()
    render_status_bar()
    render_background_notifications()

    # Tier-based page access gating
    if not check_page_access(page_key):
        page_title = PAGE_CONFIG.get(page_key, (page_key, ""))[0].split(" | ")[0]
        render_upgrade_prompt(page_title)
        st.stop()


def render_sidebar_brand():
    """Render branded sidebar header and footer with tier badge."""
    tier = get_user_tier()
    config = get_tier_config(tier)
    tier_colors = {"free": "#888", "pro": "#00d1ff", "premium": "#ffaa00", "institutional": "#00ff96"}
    tier_color = tier_colors.get(tier, "#888")

    st.sidebar.markdown(f"""<div class="sidebar-brand">
<svg width="48" height="48" viewBox="0 0 48 48" xmlns="http://www.w3.org/2000/svg" style="margin-bottom:6px;">
  <rect width="48" height="48" rx="10" fill="#0e1117" stroke="#00d1ff" stroke-width="1.5"/>
  <polyline points="8,36 16,28 22,32 30,18 38,22" fill="none" stroke="#00d1ff" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
  <polyline points="8,38 16,34 22,36 30,26 38,30" fill="none" stroke="#00ff96" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" opacity="0.5"/>
  <circle cx="30" cy="18" r="3" fill="#00d1ff" opacity="0.8"/>
  <circle cx="38" cy="22" r="2" fill="#00d1ff" opacity="0.6"/>
  <text x="10" y="15" font-size="10" font-weight="700" fill="#00d1ff" font-family="sans-serif">AI</text>
</svg>
<h2>AI STATCHARTS</h2>
<p>Quantitative Analysis Platform</p>
<span style="display:inline-block;margin-top:6px;padding:2px 10px;border:1px solid {tier_color};
border-radius:10px;font-size:0.7rem;color:{tier_color};letter-spacing:0.5px;">{config['name']}</span>
</div>""", unsafe_allow_html=True)

    st.sidebar.markdown(
        f'<div class="sidebar-footer">v{APP_VERSION} · Data auto-refreshes</div>',
        unsafe_allow_html=True,
    )


def render_status_bar():
    """Render a data freshness status strip below the ticker tape."""
    now = datetime.now()

    sources = {
        "Market Data": "last_market_data_update",
        "FRED": "last_fred_update",
        "Grok AI": "last_grok_update",
        "StockTwits": "last_stocktwits_update",
        "Polymarket": "last_polymarket_update",
    }

    items = []
    for label, key in sources.items():
        ts = st.session_state.get(key)
        if ts:
            try:
                if isinstance(ts, str):
                    ts = datetime.fromisoformat(ts)
                age_min = (now - ts).total_seconds() / 60
                if age_min < 15:
                    dot_class = "status-fresh"
                    age_str = f"{age_min:.0f}m ago"
                elif age_min < 60:
                    dot_class = "status-stale"
                    age_str = f"{age_min:.0f}m ago"
                else:
                    dot_class = "status-error"
                    hours = age_min / 60
                    age_str = f"{hours:.1f}h ago"
            except Exception:
                dot_class = "status-error"
                age_str = "error"
        else:
            dot_class = "status-error"
            age_str = "—"

        items.append(f'<span class="status-dot {dot_class}"></span>{label}: {age_str}')

    # Market status
    hour = now.hour
    weekday = now.weekday()
    if weekday >= 5:
        mkt = "Closed (Weekend)"
    elif hour < 9 or (hour == 9 and now.minute < 30):
        mkt = "Pre-Market"
    elif hour < 16:
        mkt = "Open"
    else:
        mkt = "After Hours"

    items.append(f'<span class="status-dot {"status-fresh" if mkt == "Open" else "status-stale"}"></span>NYSE: {mkt}')

    html = '<div class="status-bar">' + '&nbsp;&nbsp;'.join(items) + '</div>'
    st.markdown(html, unsafe_allow_html=True)


def render_background_notifications():
    """Check for completed background tasks and show notifications."""
    # RL Training notification
    rl_status = st.session_state.get("rl_bg_status")
    if rl_status == "running":
        ticker = st.session_state.get("rl_bg_ticker", "")
        progress = st.session_state.get("rl_bg_progress", 0)
        st.markdown(
            f'<div style="background:rgba(0,209,255,0.1);border:1px solid {COLORS["accent"]};'
            f'border-radius:6px;padding:8px 14px;margin-bottom:8px;font-size:0.85rem;">'
            f'🧠 RL agent training <strong>{ticker}</strong> in background... '
            f'{progress:.0f}% complete</div>',
            unsafe_allow_html=True,
        )
    elif rl_status == "done":
        ticker = st.session_state.get("rl_bg_ticker", "")
        nc1, nc2 = st.columns([6, 1])
        with nc1:
            st.markdown(
                f'<div style="background:rgba(0,255,150,0.1);border:1px solid {COLORS["success"]};'
                f'border-radius:6px;padding:8px 14px;font-size:0.85rem;">'
                f'✅ RL training complete for <strong>{ticker}</strong>! '
                f'Navigate to RL Trading page to view results.</div>',
                unsafe_allow_html=True,
            )
        with nc2:
            if st.button("Dismiss", key="dismiss_rl_bg"):
                st.session_state["rl_bg_status"] = None
                st.rerun()
    elif rl_status == "error":
        error_msg = st.session_state.get("rl_bg_error", "Unknown error")
        nc1, nc2 = st.columns([6, 1])
        with nc1:
            st.markdown(
                f'<div style="background:rgba(255,68,68,0.1);border:1px solid {COLORS["danger"]};'
                f'border-radius:6px;padding:8px 14px;font-size:0.85rem;">'
                f'❌ RL training failed: {error_msg[:100]}</div>',
                unsafe_allow_html=True,
            )
        with nc2:
            if st.button("Dismiss", key="dismiss_rl_err"):
                st.session_state["rl_bg_status"] = None
                st.rerun()


def card_header(title: str, icon: str = ""):
    """Render a styled card header inside a st.container(border=True)."""
    icon_html = f'<span class="icon">{icon}</span>' if icon else ""
    st.markdown(f'<div class="card-header">{icon_html}{title}</div>', unsafe_allow_html=True)


@contextmanager
def error_boundary(section_name: str):
    """Context manager that catches exceptions and shows a styled error card instead of a traceback."""
    try:
        yield
    except Exception as e:
        logger.error(f"Error in {section_name}: {e}", exc_info=True)
        st.markdown(
            f'<div class="error-card">'
            f'<h4>Unable to load: {section_name}</h4>'
            f'<p>Something went wrong loading this section. The error has been logged. '
            f'Try refreshing the page.</p>'
            f'<p style="font-family:monospace;font-size:0.75rem;color:#666;margin-top:8px;">{type(e).__name__}: {str(e)[:200]}</p>'
            f'</div>',
            unsafe_allow_html=True,
        )

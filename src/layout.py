"""Shared layout helpers for AI Statcharts — sidebar, status bar, error boundaries, page setup."""
import streamlit as st
import logging
import json
import os
from datetime import datetime, timezone
from contextlib import contextmanager
from src.styles import COLORS, APP_VERSION, inject_global_css
from src.ticker_tape import _get_ticker_tape_data
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
    Handles: page_config, auth, global CSS, header, ticker tape, tier gating."""
    title, icon = PAGE_CONFIG.get(page_key, ("AI Statcharts", "📊"))
    st.set_page_config(page_title=title, page_icon=icon, layout=layout,
                       initial_sidebar_state=sidebar_state)
    # Hide sidebar immediately to prevent flash of unstyled default nav
    st.markdown("""<style>
        section[data-testid="stSidebar"] > div:first-child { opacity: 0 !important; }
    </style>""", unsafe_allow_html=True)
    check_auth()
    inject_global_css()
    render_sidebar_brand()
    render_header(page_key)
    render_background_notifications()

    # Tier-based page access gating
    if not check_page_access(page_key):
        page_title = PAGE_CONFIG.get(page_key, (page_key, ""))[0].split(" | ")[0]
        render_upgrade_prompt(page_title)
        st.stop()


def render_header(current_page: str):
    """Render consolidated header as a single HTML bar + nav row, then market ticker."""
    tier = get_user_tier()
    tier_cfg = get_tier_config(tier)
    tier_colors = {"free": "#888", "pro": "#00d1ff", "premium": "#ffaa00", "platinum": "#00ff96"}
    tier_color = tier_colors.get(tier, "#888")

    # Market status
    now = datetime.now()
    hour, weekday = now.hour, now.weekday()
    if weekday >= 5:
        mkt_status, mkt_color = "CLOSED", "#888"
    elif hour < 9 or (hour == 9 and now.minute < 30):
        mkt_status, mkt_color = "PRE-MKT", "#ffaa00"
    elif hour < 16:
        mkt_status, mkt_color = "LIVE", "#00ff96"
    else:
        mkt_status, mkt_color = "AFTER-HRS", "#ffaa00"

    # ── Row 1: Brand bar (pure HTML — fully responsive) ──
    st.markdown(f"""<div class="site-header">
        <div class="site-header-brand">
            <span style="font-size:clamp(13px, 1.8vw, 22px); font-weight:800; color:{COLORS['accent']}; letter-spacing:1.5px;">AI STATCHARTS</span>
        </div>
        <div class="site-header-badges">
            <span class="header-badge" style="color:{mkt_color}; border-color:{mkt_color};">{mkt_status}</span>
            <span class="header-badge" style="color:{tier_color}; border-color:{tier_color};">{tier_cfg['name']}</span>
            <span style="font-size:10px; color:{COLORS['text_muted']};">{now.strftime('%I:%M %p')}</span>
        </div>
    </div>""", unsafe_allow_html=True)

    # ── Row 2: Nav dropdowns (Streamlit widgets for working links) ──
    nav_groups = [
        ("Summary", [("01_Summary", "Summary", "pages/01_Summary.py")]),
        ("AI Analysis", [
            ("02_Scenario_Analysis", "Scenario Analysis", "pages/02_Scenario_Analysis.py"),
            ("03_Stock_Analysis", "Stock Analysis", "pages/03_Stock_Analysis.py"),
            ("04_RL_Trading", "RL Trading", "pages/04_RL_Trading.py"),
            ("19_Iran_Conflict", "Iran Conflict", "pages/19_Iran_Conflict.py"),
        ]),
        ("Options", [
            ("06_Options_Analysis", "Options Analysis", "pages/06_Options_Analysis.py"),
            ("07_Options_Flow", "Options Flow", "pages/07_Options_Flow.py"),
            ("08_Options_Lab", "Options Lab", "pages/08_Options_Lab.py"),
        ]),
        ("Tools", [
            ("05_Historical_Analysis", "Historical", "pages/05_Historical_Analysis.py"),
            ("09_ML_Stock_Predictor", "ML Predictor", "pages/09_ML_Stock_Predictor.py"),
            ("10_Tech_Screener", "Tech Screener", "pages/10_Tech_Screener.py"),
            ("11_Algo_Backtester", "Algo Backtester", "pages/11_Algo_Backtester.py"),
            ("12_Monte_Carlo", "Monte Carlo", "pages/12_Monte_Carlo.py"),
            ("13_Power_Risk_VaR", "Portfolio VaR", "pages/13_Power_Risk_VaR.py"),
        ]),
        ("Energy & Macro", [
            ("14_Oil_Fundamentals", "Oil", "pages/14_Oil_Fundamentals.py"),
            ("15_NatGas_Fundamentals", "Natural Gas", "pages/15_NatGas_Fundamentals.py"),
            ("16_ERCOT_Power", "ERCOT Power", "pages/16_ERCOT_Power.py"),
            ("17_ERCOT_Capacity", "ERCOT Capacity", "pages/17_ERCOT_Capacity.py"),
            ("18_Economic_Calendar", "Economic Calendar", "pages/18_Economic_Calendar.py"),
            ("20_Futures", "Futures", "pages/20_Futures.py"),
        ]),
    ]

    nav_cols = st.columns(len(nav_groups))
    for col, (group_name, pages) in zip(nav_cols, nav_groups):
        with col:
            if len(pages) == 1:
                key, label, path = pages[0]
                if st.button(group_name, use_container_width=True, key=f"nav_{key}"):
                    st.switch_page(path)
            else:
                with st.popover(group_name, use_container_width=True):
                    for key, label, path in pages:
                        st.page_link(path, label=label, icon=PAGE_CONFIG.get(key, ("", "📊"))[1])

    # ── Row 2: Market ticker strip (replaces ticker tape + threat dashboard) ──
    ticker_data = _get_ticker_tape_data()
    esc_data = _get_escalation_data()
    war_start = datetime(2026, 2, 28)
    days_of_conflict = (now - war_start).days
    esc_score = esc_data["score"]
    esc_level = esc_data["level"]
    esc_color = "#ff4444" if esc_score >= 8 else "#ff6b35" if esc_score >= 6 else "#ffaa00" if esc_score >= 4 else "#00ff96" if esc_score > 0 else "#888"

    items = []
    for sym in ["^GSPC", "QQQ", "CL=F", "GC=F", "^VIX", "DX-Y.NYB", "TLT", "BTC-USD"]:
        d = ticker_data.get(sym, {})
        if not d:
            continue
        price = d.get("price", 0)
        chg = d.get("change", 0)
        label = d.get("label", sym)
        if sym == "^VIX":
            color = "#ff4444" if chg > 0 else "#00ff96"
        else:
            color = "#00ff96" if chg >= 0 else "#ff4444"
        arrow = "▲" if chg >= 0 else "▼"

        if sym == "^GSPC":
            pstr = f"{price:,.0f}"
        elif sym == "BTC-USD":
            pstr = f"${price:,.0f}"
        elif sym == "GC=F":
            pstr = f"${price:,.0f}"
        elif sym in ("^VIX", "DX-Y.NYB"):
            pstr = f"{price:.1f}"
        else:
            pstr = f"${price:.2f}"

        items.append(f'<span style="color:{color};">{label} {pstr} {arrow}{abs(chg):.1f}%</span>')

    # Append threat indicators
    items.append(f'<span style="color:#ffaa00;">FED 3.50-3.75%</span>')
    items.append(f'<span style="color:{esc_color};">IRAN DAY {days_of_conflict} ({esc_score}/10)</span>')
    items.append(f'<span style="color:#555;">updated {now.strftime("%I:%M %p")}</span>')

    separator = '&nbsp;&nbsp;&nbsp;│&nbsp;&nbsp;&nbsp;'
    single_tape = separator.join(items)
    # Repeat 3x for seamless loop — scroll shifts by 33.33% (one copy width)
    full_tape = f"{single_tape}{separator}{single_tape}{separator}{single_tape}"

    import time
    elapsed = time.time() % 40

    st.markdown(
        f"""<div style="overflow:hidden; white-space:nowrap; background:rgba(0,0,0,0.6); padding:6px 0; margin-bottom:6px; border-radius:4px; font-size:13px; font-weight:500; border:1px solid {COLORS['card_border']};">
<div style="display:inline-block; animation:tickerscroll 40s linear infinite; animation-delay:-{elapsed:.1f}s;">
{full_tape}
</div></div>
<style>
@keyframes tickerscroll {{
    0% {{ transform: translateX(0); }}
    100% {{ transform: translateX(-33.33%); }}
}}
</style>""",
        unsafe_allow_html=True,
    )


def _get_escalation_data() -> dict:
    """Pull the latest AI escalation score and conflict day count from history."""
    history_file = os.path.join(os.path.dirname(__file__), "iran_conflict_history.json")
    try:
        if os.path.exists(history_file):
            with open(history_file, "r") as f:
                history = json.load(f)
            if history:
                latest = history[-1]
                blended = latest.get("blended", {})
                esc = blended.get("escalation_risk", {})
                return {
                    "score": esc.get("score", 0),
                    "level": esc.get("level", "Unknown"),
                }
    except Exception:
        pass
    return {"score": 0, "level": "No Data"}




def render_sidebar_brand():
    """Render branded sidebar header and footer with tier badge."""
    tier = get_user_tier()
    config = get_tier_config(tier)
    tier_colors = {"free": "#888", "pro": "#00d1ff", "premium": "#ffaa00", "platinum": "#00ff96"}
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
<div style="font-size:0.65rem;color:{COLORS['text_muted']};margin-top:4px;">v{APP_VERSION}</div>
</div>""", unsafe_allow_html=True)

    # Footer is part of the brand div so it stays at top — move version into the brand block instead
    pass




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

"""Shared layout helpers for AI Statcharts — sidebar, status bar, error boundaries, page setup."""
import sys, os
# Ensure project root is always on sys.path so 'from src.*' imports never break
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import streamlit as st
import logging
import json
from datetime import datetime
from contextlib import contextmanager
from src.styles import COLORS, APP_VERSION, inject_global_css
from src.auth import check_auth, check_page_access, render_upgrade_prompt, render_token_purchase, clear_auth_cookie, get_user_tier, get_tier_config, get_usage_summary, get_token_balance

logger = logging.getLogger(__name__)

# ── Pages disabled from nav and access (code preserved, toggle back by removing from set) ──
DISABLED_PAGES = {
    "05_Historical_Analysis",   # Redundant — Stock Analysis covers price history
    # "07_Options_Flow",        # Re-enabled — Live Tick Flow with real trade data
    "09_ML_Stock_Predictor",    # Redundant — overlaps with RL Trading + Stock Analysis AI
    "10_Tech_Screener",         # Redundant — Signal Scanner is far superior
    "12_Monte_Carlo",           # Low-impact — lightweight niche tool
    "13_Power_Risk_VaR",        # Low-impact — very basic VaR
    "40_Power_Strategies",      # Merged into 23_Power_Analytics
}

# Base64-encode logo once at import time
_LOGO_B64 = ""
try:
    import base64 as _b64
    _logo_path = os.path.join(_project_root, "static", "logo.png")
    if os.path.exists(_logo_path):
        with open(_logo_path, "rb") as _f:
            _LOGO_B64 = _b64.b64encode(_f.read()).decode()
except Exception:
    pass

# Page registry: filename key → (title, icon)
PAGE_CONFIG = {
    "00_Daily_Briefing":     ("Daily Briefing | AI Statcharts", "📋"),
    "01_Summary":            ("Summary | AI Statcharts", "🎯"),
    "02_Scenario_Analysis":  ("Scenario Analysis | AI Statcharts", "🔮"),
    "03_Stock_Analysis":     ("Stock Analysis | AI Statcharts", "🧠"),
    "04_RL_Trading":         ("RL Trading | AI Statcharts", "🦾"),
    "05_Historical_Analysis":("Historical Analysis | AI Statcharts", "🕰️"),
    "06_Options_Analysis":   ("Options Analysis | AI Statcharts", "💎"),
    "07_Options_Flow":       ("Options Flow | AI Statcharts", "💧"),
    "08_Options_Lab":        ("Options Lab | AI Statcharts", "🧫"),
    "09_ML_Stock_Predictor": ("ML Predictor | AI Statcharts", "🎲"),
    "10_Tech_Screener":      ("Tech Screener | AI Statcharts", "🛰️"),
    "11_Algo_Backtester":    ("Algo Backtester | AI Statcharts", "🏗️"),
    "12_Monte_Carlo":        ("Monte Carlo | AI Statcharts", "🎯"),
    "13_Power_Risk_VaR":     ("Portfolio VaR | AI Statcharts", "🛡️"),
    "14_Oil_Fundamentals":   ("Oil Fundamentals | AI Statcharts", "🔥"),
    "15_NatGas_Fundamentals":("NatGas Fundamentals | AI Statcharts", "♨️"),
    "16_ERCOT_Power":        ("ERCOT Power | AI Statcharts", "⚡"),
    "17_ERCOT_Capacity":     ("ERCOT Capacity | AI Statcharts", "🏗️"),
    "18_Economic_Calendar":  ("Economic Calendar | AI Statcharts", "🏛️"),
    "19_Iran_Conflict":      ("Iran Conflict | AI Statcharts", "🎖️"),
    "20_Futures":            ("Futures | AI Statcharts", "📈"),
    "21_Fed_Macro_Drivers":  ("Fed & Macro Drivers | AI Statcharts", "🏦"),
    "22_Smart_Money":        ("Smart Money | AI Statcharts", "🏛️"),
    "23_Power_Analytics":    ("Power Analytics | AI Statcharts", "⚡"),
    "24_Sector_Analysis":    ("Sector Analysis | AI Statcharts", "📊"),
    "35_Correlation":        ("Cross-Asset Correlation | AI Statcharts", "🔗"),
    "36_Quant_Lab":          ("Quant Lab | AI Statcharts", "🔬"),
    "37_Factor_Decomposition": ("Factor Decomposition | AI Statcharts", "🧬"),
    "38_Portfolio_Optimizer": ("Portfolio Optimizer | AI Statcharts", "🎯"),
    "39_Signal_Scanner":     ("Signal Scanner | AI Statcharts", "📡"),
    "40_Power_Strategies":   ("Power Strategies | AI Statcharts", "⚡"),
    "41_Meta_Analysis":      ("Meta Analysis | AI Statcharts", "📊"),
    "42_Calendar_Spreads":   ("Calendar Spreads | AI Statcharts", "📅"),
    "43_Vol_Surface":        ("Vol Surface | AI Statcharts", "🌊"),
    "44_Portfolio_Greeks":   ("Portfolio Greeks | AI Statcharts", "📐"),
    "45_Universe_Portfolio": ("Universe Portfolio | AI Statcharts", "🌐"),
    "46_Market_Expectations": ("Market Expectations | AI Statcharts", "🔮"),
    "47_Track_Record":       ("Track Record | AI Statcharts", "🎯"),
    "48_Vol_Landscape":      ("Vol Landscape | AI Statcharts", "🌋"),
    "49_Higher_Greeks":      ("Higher-Order Greeks | AI Statcharts", "🧮"),
}


def setup_page(page_key: str, layout: str = "wide", sidebar_state: str = "collapsed"):
    """Universal page setup. Call this as the FIRST thing on every page.
    Handles: page_config, auth, global CSS, header, tier gating."""
    title, icon = PAGE_CONFIG.get(page_key, ("AI Statcharts", "📊"))
    st.set_page_config(page_title=title, page_icon=icon, layout=layout,
                       initial_sidebar_state=sidebar_state)
    check_auth()
    # Load theme preference before CSS injection
    from src.styles import load_theme_preference
    load_theme_preference()
    inject_global_css()
    render_header(page_key)
    _inject_mobile_session_guard()
    _inject_footer()
    render_background_notifications()

    # Block disabled pages (even via direct URL)
    if page_key in DISABLED_PAGES:
        st.info("This page is currently unavailable.")
        st.stop()

    # Tier-based page access gating
    if not check_page_access(page_key):
        page_title = PAGE_CONFIG.get(page_key, (page_key, ""))[0].split(" | ")[0]
        render_upgrade_prompt(page_title)
        st.stop()

    # Disclaimer on pages with actionable trading signals
    _SIGNAL_PAGES = {
        "02_Scenario_Analysis", "03_Stock_Analysis", "04_RL_Trading",
        "09_ML_Stock_Predictor", "11_Algo_Backtester", "12_Monte_Carlo",
        "13_Power_Risk_VaR", "42_Calendar_Spreads", "44_Portfolio_Greeks",
        "45_Universe_Portfolio",
    }
    if page_key in _SIGNAL_PAGES:
        st.markdown(
            f'<div style="background:rgba(255,170,0,0.06);border:1px solid rgba(255,170,0,0.2);'
            f'border-radius:6px;padding:6px 12px;font-size:0.72rem;color:#888;margin-bottom:8px;">'
            f'Past performance and backtested results do not guarantee future returns. '
            f'AI outputs are probabilistic estimates, not financial advice. '
            f'All strategies are subject to model risk, overfitting, and market regime changes. '
            f'Consult a qualified advisor before making investment decisions.</div>',
            unsafe_allow_html=True,
        )




def _inject_mobile_session_guard():
    """Auto-reload when a mobile user returns from a stale/backgrounded session.
    Shows a refresh banner for short stale periods, auto-reloads for long ones."""
    import streamlit.components.v1 as components
    components.html("""
    <script>
    (function() {
        if (window._staleGuardInit) return;
        window._staleGuardInit = true;
        var hiddenAt = null;
        var SOFT_STALE_MS = 15000;  // 15s — show refresh banner
        var HARD_STALE_MS = 60000;  // 60s — auto-reload
        var IDLE_STALE_MS = 300000; // 5 min idle — show refresh banner
        var lastInteraction = Date.now();

        ['click','scroll','touchstart','keydown'].forEach(function(evt) {
            document.addEventListener(evt, function() { lastInteraction = Date.now(); }, {passive:true});
        });

        function showBanner() {
            if (document.getElementById('stale-banner')) return;
            var b = document.createElement('div');
            b.id = 'stale-banner';
            b.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:9999;background:#1a1a2e;' +
                'border-bottom:2px solid #ffaa00;padding:10px 16px;text-align:center;font-size:14px;' +
                'color:#ffaa00;cursor:pointer;font-family:sans-serif;';
            b.textContent = 'Data may be stale — tap to refresh';
            b.onclick = function() { window.location.reload(); };
            document.body.prepend(b);
        }

        document.addEventListener('visibilitychange', function() {
            if (document.hidden) {
                hiddenAt = Date.now();
            } else if (hiddenAt) {
                var elapsed = Date.now() - hiddenAt;
                hiddenAt = null;
                if (elapsed > HARD_STALE_MS) {
                    window.location.reload();
                } else if (elapsed > SOFT_STALE_MS) {
                    showBanner();
                }
            }
        });

        // Idle detection — check every 60s
        setInterval(function() {
            if (!document.hidden && (Date.now() - lastInteraction > IDLE_STALE_MS)) {
                showBanner();
            }
        }, 60000);
    })();
    </script>
    """, height=0)


def _inject_footer():
    """Inject a fixed footer on every page using JS to append to the parent document."""
    from datetime import datetime
    import streamlit.components.v1 as components
    year = datetime.now().year
    # Use JS to inject the footer into the parent Streamlit document (escapes the iframe)
    components.html(
        f"""<script>
        (function() {{
            if (window.parent.document.getElementById('app-footer')) return;
            var footer = window.parent.document.createElement('div');
            footer.id = 'app-footer';
            footer.style.cssText = 'position:fixed;bottom:0;left:0;right:0;z-index:998;'
                + 'background:linear-gradient(180deg, transparent, {COLORS["bg_primary"]} 30%);'
                + 'padding:20px 0 8px 0;text-align:center;font-size:0.7rem;'
                + 'color:{COLORS["text_muted"]};font-family:sans-serif;';
            footer.innerHTML = 'AI Statcharts v{APP_VERSION} · Quantitative Analysis Platform · Not financial advice · '
                + '<a href="mailto:jdmeyer05@gmail.com" style="color:{COLORS["accent"]};text-decoration:none;">Contact</a> · '
                + '&copy; {year} AI Statcharts. All rights reserved.';
            window.parent.document.body.appendChild(footer);
        }})();
        </script>""",
        height=0,
    )



def render_header(current_page: str):
    """Render consolidated header as a single HTML bar + nav row."""
    logo_html = f'<img src="data:image/png;base64,{_LOGO_B64}" width="28" height="28" style="vertical-align:middle;margin-right:8px;border-radius:6px;"/>' if _LOGO_B64 else ''
    header_html = (
        f'<div class="site-header">'
        f'<div class="site-header-brand">'
        f'{logo_html}'
        f'<span style="font-size:20px; font-weight:800; color:{COLORS["accent"]}; letter-spacing:1.5px;">AI STATCHARTS</span>'
        f'</div>'
        f'</div>'
    )
    st.markdown(header_html, unsafe_allow_html=True)

    # ── Row 2: Nav dropdowns (Streamlit widgets for working links) ──
    nav_groups = [
        ("Summary", [
            ("00_Daily_Briefing", "Daily Briefing", "pages/00_Daily_Briefing.py"),
            ("01_Summary", "Dashboard", "pages/01_Summary.py"),
        ]),
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
            ("42_Calendar_Spreads", "Calendar Spreads", "pages/42_Calendar_Spreads.py"),
            ("43_Vol_Surface", "Vol Surface", "pages/43_Vol_Surface.py"),
            ("44_Portfolio_Greeks", "Portfolio Greeks", "pages/44_Portfolio_Greeks.py"),
        ]),
        ("Tools", [
            ("05_Historical_Analysis", "Historical", "pages/05_Historical_Analysis.py"),
            ("09_ML_Stock_Predictor", "ML Predictor", "pages/09_ML_Stock_Predictor.py"),
            ("10_Tech_Screener", "Tech Screener", "pages/10_Tech_Screener.py"),
            ("11_Algo_Backtester", "Algo Backtester", "pages/11_Algo_Backtester.py"),
            ("12_Monte_Carlo", "Monte Carlo", "pages/12_Monte_Carlo.py"),
            ("13_Power_Risk_VaR", "Portfolio VaR", "pages/13_Power_Risk_VaR.py"),
            ("35_Correlation", "Correlation", "pages/35_Correlation.py"),
            ("36_Quant_Lab", "Quant Lab", "pages/36_Quant_Lab.py"),
            ("37_Factor_Decomposition", "Factors", "pages/37_Factor_Decomposition.py"),
            ("38_Portfolio_Optimizer", "Optimizer", "pages/38_Portfolio_Optimizer.py"),
            ("39_Signal_Scanner", "Signals", "pages/39_Signal_Scanner.py"),
            ("41_Meta_Analysis", "Meta Analysis", "pages/41_Meta_Analysis.py"),
            ("45_Universe_Portfolio", "Universe Portfolio", "pages/45_Universe_Portfolio.py"),
            ("46_Market_Expectations", "Market Expectations", "pages/46_Market_Expectations.py"),
            ("47_Track_Record", "Track Record", "pages/47_Track_Record.py"),
            ("48_Vol_Landscape", "Vol Landscape", "pages/48_Vol_Landscape.py"),
            ("49_Higher_Greeks", "Higher-Order Greeks", "pages/49_Higher_Greeks.py"),
        ]),
        ("Sectors", [("24_Sector_Analysis", "Sector Analysis", "pages/24_Sector_Analysis.py")]),
        ("Energy", [
            ("14_Oil_Fundamentals", "Oil", "pages/14_Oil_Fundamentals.py"),
            ("15_NatGas_Fundamentals", "Natural Gas", "pages/15_NatGas_Fundamentals.py"),
            ("16_ERCOT_Power", "ERCOT Power", "pages/16_ERCOT_Power.py"),
            ("17_ERCOT_Capacity", "ERCOT Capacity", "pages/17_ERCOT_Capacity.py"),
            ("23_Power_Analytics", "Power Analytics", "pages/23_Power_Analytics.py"),
            ("20_Futures", "Futures", "pages/20_Futures.py"),
            ("40_Power_Strategies", "Power Strategies", "pages/40_Power_Strategies.py"),
        ]),
        ("Macro", [
            ("18_Economic_Calendar", "Economic Calendar", "pages/18_Economic_Calendar.py"),
            ("21_Fed_Macro_Drivers", "Fed & Macro", "pages/21_Fed_Macro_Drivers.py"),
            ("22_Smart_Money", "Smart Money", "pages/22_Smart_Money.py"),
        ]),
    ]

    # Filter out disabled pages from nav
    nav_groups = [
        (group_name, [p for p in pages if p[0] not in DISABLED_PAGES])
        for group_name, pages in nav_groups
    ]
    nav_groups = [(g, p) for g, p in nav_groups if p]  # Drop empty groups

    nav_cols = st.columns(len(nav_groups) + 1)
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

    # Settings popover
    with nav_cols[-1]:
        with st.popover("Settings", use_container_width=True):
            email = st.session_state.get("user_email", "")
            tier = get_user_tier()
            tier_cfg = get_tier_config(tier)
            tier_colors = {"free": "#888", "pro": "#00d1ff", "premium": "#ffaa00", "platinum": "#00ff96"}
            t_color = tier_colors.get(tier, "#888")

            # ── Account ──
            st.markdown(f'<div style="font-size:0.7rem;color:{COLORS["text_muted"]};text-transform:uppercase;letter-spacing:1px;margin-bottom:4px;">Account</div>', unsafe_allow_html=True)
            _is_guest = not email or email in ("local-dev@preview", "guest@open-beta")
            if not _is_guest:
                st.markdown(
                    f'<div style="font-size:0.82rem;color:{COLORS["text_primary"]};">{email}</div>'
                    f'<div style="font-size:0.75rem;margin-top:2px;">'
                    f'<span style="color:{t_color};font-weight:600;">{tier_cfg["name"]}</span> plan</div>',
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    f'<div style="font-size:0.82rem;color:{COLORS["text_primary"]};">Guest</div>'
                    f'<div style="font-size:0.75rem;margin-top:2px;color:{COLORS["text_muted"]};">'
                    f'Open Beta — all features unlocked</div>',
                    unsafe_allow_html=True,
                )

            # ── AI Usage ──
            summary = get_usage_summary()
            if summary["daily_limit"] > 0 or summary["tokens"] > 0:
                st.markdown(f'<div style="font-size:0.7rem;color:{COLORS["text_muted"]};text-transform:uppercase;letter-spacing:1px;margin:10px 0 4px 0;">AI Usage</div>', unsafe_allow_html=True)
                used = summary["daily_used"]
                limit = summary["daily_limit"]
                tokens = summary["tokens"]
                if limit > 0:
                    pct = min(used / limit, 1.0) if limit > 0 else 0
                    bar_color = COLORS["success"] if pct < 0.7 else COLORS["warning"] if pct < 0.9 else COLORS["danger"]
                    st.markdown(
                        f'<div style="font-size:0.78rem;color:{COLORS["text_primary"]};margin-bottom:3px;">'
                        f'Daily: <b>{used}</b> / {limit} analyses</div>'
                        f'<div style="background:#222;border-radius:3px;height:6px;overflow:hidden;">'
                        f'<div style="width:{pct*100:.0f}%;height:100%;background:{bar_color};border-radius:3px;"></div></div>',
                        unsafe_allow_html=True,
                    )
                if tokens > 0:
                    st.markdown(
                        f'<div style="font-size:0.78rem;color:{COLORS["text_primary"]};margin-top:4px;">'
                        f'Tokens: <b style="color:{COLORS["accent"]};">{tokens}</b> remaining</div>',
                        unsafe_allow_html=True,
                    )
            elif tier == "free":
                st.markdown(
                    f'<div style="font-size:0.75rem;color:{COLORS["text_muted"]};margin-top:6px;">'
                    f'Open Beta — all features unlocked</div>',
                    unsafe_allow_html=True,
                )

            # ── Market Status (Polygon API with hardcoded fallback) ──
            st.markdown(f'<div style="font-size:0.7rem;color:{COLORS["text_muted"]};text-transform:uppercase;letter-spacing:1px;margin:10px 0 4px 0;">Market</div>', unsafe_allow_html=True)
            mkt_status, mkt_color = "Unknown", "#888"
            now = datetime.now()
            try:
                from src.data_engine import fetch_market_status
                _ms = fetch_market_status()
                if _ms.get("is_open"):
                    mkt_status, mkt_color = "Market Open", "#00ff96"
                elif _ms.get("market") == "closed":
                    mkt_status, mkt_color = "Closed", "#888"
                elif _ms.get("market") == "extended-hours":
                    mkt_status, mkt_color = "Extended Hours", "#ffaa00"
                elif _ms.get("market"):
                    mkt_status, mkt_color = _ms["market"].title(), "#ffaa00"
            except Exception:
                pass
            if mkt_status == "Unknown":
                # Fallback to hardcoded logic
                now = datetime.now()
                hour, weekday = now.hour, now.weekday()
                if weekday >= 5:
                    mkt_status, mkt_color = "Closed (Weekend)", "#888"
                elif hour < 4:
                    mkt_status, mkt_color = "Closed", "#888"
                elif hour < 9 or (hour == 9 and now.minute < 30):
                    mkt_status, mkt_color = "Pre-Market", "#ffaa00"
                elif hour < 16:
                    mkt_status, mkt_color = "Market Open", "#00ff96"
                elif hour < 20:
                    mkt_status, mkt_color = "After Hours", "#ffaa00"
                else:
                    mkt_status, mkt_color = "Closed", "#888"
            st.markdown(
                f'<div style="font-size:0.78rem;">'
                f'<span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:{mkt_color};margin-right:5px;vertical-align:middle;"></span>'
                f'<span style="color:{mkt_color};">{mkt_status}</span>'
                f'<span style="color:{COLORS["text_muted"]};margin-left:8px;">{now.strftime("%I:%M %p ET")}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )

            # ── App Info ──
            st.markdown(f'<div style="font-size:0.7rem;color:{COLORS["text_muted"]};text-transform:uppercase;letter-spacing:1px;margin:10px 0 4px 0;">App</div>', unsafe_allow_html=True)
            st.markdown(
                f'<div style="font-size:0.75rem;color:{COLORS["text_muted"]};">'
                f'Version {APP_VERSION}<br>'
                f'Data: Polygon, EIA, ERCOT<br>'
                f'AI: Grok, Gemini, Claude</div>',
                unsafe_allow_html=True,
            )

            # ── Theme Toggle ──
            st.divider()
            from src.styles import render_theme_toggle
            render_theme_toggle()

            # ── Actions ──
            if _is_guest:
                st.markdown(
                    f'<div style="font-size:0.75rem;color:{COLORS["text_muted"]};margin-bottom:6px;">'
                    f'Create an account to save preferences and track usage.</div>',
                    unsafe_allow_html=True,
                )
                st.page_link("pages/99_Login.py", label="Log In / Register", icon="🔒", use_container_width=True)
            else:
                if st.button("Log Out", key="header_logout", use_container_width=True):
                    clear_auth_cookie()
                    for key in list(st.session_state.keys()):
                        del st.session_state[key]
                    st.switch_page("pages/01_Summary.py")



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


def get_active_ticker(default: str = "SPY") -> str:
    """Get the active ticker from query params, session state, or Supabase prefs.
    Use as the default value for ticker inputs to enable cross-page linking."""
    # Priority: query param > session state > saved pref > default
    qp_ticker = st.query_params.get("ticker", "").strip().upper()
    if qp_ticker:
        st.session_state["active_ticker"] = qp_ticker
        return qp_ticker
    if "active_ticker" in st.session_state:
        return st.session_state["active_ticker"]
    # Load from Supabase prefs
    try:
        from src.user_prefs import load_pref
        saved = load_pref("active_ticker")
        if saved:
            st.session_state["active_ticker"] = saved
            return saved
    except Exception:
        pass
    return default


def set_active_ticker(ticker: str):
    """Set the active ticker in session state and persist to Supabase."""
    ticker = ticker.strip().upper()
    if ticker:
        old = st.session_state.get("active_ticker", "")
        st.session_state["active_ticker"] = ticker
        # Persist to Supabase (only on change to avoid excess writes)
        if ticker != old:
            try:
                from src.user_prefs import save_pref
                save_pref("active_ticker", ticker)
                # Track recent tickers
                recent = st.session_state.get("_recent_tickers", [])
                if ticker not in recent:
                    recent = [ticker] + recent[:19]
                    st.session_state["_recent_tickers"] = recent
                    save_pref("recent_tickers", recent)
            except Exception:
                pass


@contextmanager
def error_boundary(section_name: str):
    """Context manager that catches exceptions and shows a styled error card instead of a traceback."""
    try:
        yield
    except Exception as e:
        import traceback, html
        logger.error(f"Error in {section_name}: {e}", exc_info=True)
        tb_str = html.escape(traceback.format_exc())
        st.markdown(
            f'<div class="error-card">'
            f'<h4>Unable to load: {section_name}</h4>'
            f'<p>Something went wrong loading this section. Try refreshing the page.</p>'
            f'</div>',
            unsafe_allow_html=True,
        )
        with st.expander("Error details", expanded=False):
            st.code(traceback.format_exc(), language="python")


_LOADING_QUIPS = [
    "Crunching numbers at light speed...",
    "Teaching the AI to count past ten...",
    "Asking the market nicely for data...",
    "Caffeinating the algorithms...",
    "Reticulating financial splines...",
    "Consulting the crystal ball API...",
    "Polishing the neural networks...",
    "Warming up the prediction engine...",
    "Downloading more RAM... just kidding.",
    "Shaking the magic 8-ball...",
    "Bribing the data gods...",
    "Running regressions in flip-flops...",
    "Summoning the quant spirits...",
    "Untangling spaghetti correlations...",
    "Herding stochastic cats...",
    "Negotiating with the Fed API...",
    "Calculating the meaning of alpha...",
    "Factoring in vibes (very important)...",
    "Asking Buffett... he's not answering...",
    "Stress-testing your patience...",
]


@contextmanager
def fun_loader(category: str = "data"):
    """Drop-in replacement for st.spinner with animated spinner, funny messages,
    elapsed timer, and milestone-based progress updates.

    category: 'ai', 'data', or 'compute' — picks accent color and timing profile.
    """
    import random
    import streamlit.components.v1 as components

    quips = random.sample(_LOADING_QUIPS, min(6, len(_LOADING_QUIPS)))
    accent = {"ai": "#ad7fff", "data": "#00d1ff", "compute": "#00ff96"}.get(category, "#00d1ff")

    # Milestone-based progress messages with expected times
    milestones = {
        "ai": [
            (0, "Sending prompts to AI models..."),
            (5, "Models are thinking..."),
            (15, "Generating analysis..."),
            (30, "Almost there, wrapping up..."),
            (45, "Final model finishing up..."),
            (60, "Taking longer than usual..."),
            (90, "Still working — complex analysis..."),
        ],
        "data": [
            (0, "Connecting to data sources..."),
            (3, "Downloading market data..."),
            (8, "Processing and caching..."),
            (15, "Almost ready..."),
            (25, "Retrying slow sources..."),
        ],
        "compute": [
            (0, "Initializing model..."),
            (5, "Training in progress..."),
            (20, "Running simulations..."),
            (45, "Optimizing parameters..."),
            (70, "Validating results..."),
            (90, "Finishing up..."),
        ],
    }.get(category, [(0, "Loading...")])

    expected = {"ai": 40, "data": 10, "compute": 60}.get(category, 10)
    warn_sec = {"ai": 60, "data": 25, "compute": 100}.get(category, 25)

    html = f"""
    <html><body style="margin:0;background:transparent;overflow:hidden;font-family:sans-serif;">
    <div style="display:flex;flex-direction:column;align-items:center;justify-content:center;padding:20px 16px;gap:8px;">
        <div style="width:36px;height:36px;border:3px solid #30363d;border-top:3px solid {accent};
                    border-radius:50%;animation:funSpin 0.8s linear infinite;"></div>
        <div id="statusText" style="color:#ccc;font-size:0.85rem;text-align:center;min-height:1.2em;"></div>
        <div id="quipText" style="color:#555;font-size:0.78rem;font-style:italic;text-align:center;min-height:1.1em;"></div>
        <div style="width:200px;margin-top:4px;">
            <div style="display:flex;justify-content:space-between;font-size:0.65rem;color:#555;margin-bottom:2px;">
                <span id="timerText">0s</span>
                <span id="etaText">~{expected}s</span>
            </div>
            <div style="background:#30363d;border-radius:3px;height:6px;overflow:hidden;border:1px solid #444;">
                <div id="progressBar" style="width:0%;height:100%;background:{accent};border-radius:3px;transition:width 1s linear;box-shadow:0 0 6px {accent}80;"></div>
            </div>
        </div>
    </div>
    <style>
    @keyframes funSpin {{ 0%{{transform:rotate(0deg)}} 100%{{transform:rotate(360deg)}} }}
    </style>
    <script>
    var quips = {json.dumps(quips)};
    var milestones = {json.dumps(milestones)};
    var expected = {expected};
    var warnSec = {warn_sec};
    var startTime = Date.now();
    var quipIdx = 0;

    var statusEl = document.getElementById('statusText');
    var quipEl = document.getElementById('quipText');
    var timerEl = document.getElementById('timerText');
    var etaEl = document.getElementById('etaText');
    var barEl = document.getElementById('progressBar');

    statusEl.textContent = milestones[0][1];
    quipEl.textContent = quips[0];

    setInterval(function() {{
        quipIdx = (quipIdx + 1) % quips.length;
        quipEl.textContent = quips[quipIdx];
    }}, 3000);

    setInterval(function() {{
        var sec = Math.floor((Date.now() - startTime) / 1000);
        timerEl.textContent = sec + 's';

        // Update status based on milestones
        var currentStatus = milestones[0][1];
        for (var i = milestones.length - 1; i >= 0; i--) {{
            if (sec >= milestones[i][0]) {{
                currentStatus = milestones[i][1];
                break;
            }}
        }}
        statusEl.textContent = currentStatus;

        // Progress bar — eases toward 90% then slows
        var pct = Math.min(90, (sec / expected) * 80);
        if (sec > expected) pct = 90 + Math.min(9, (sec - expected) / 10);
        barEl.style.width = pct + '%';

        // ETA update
        var remaining = Math.max(0, expected - sec);
        if (sec > warnSec) {{
            etaEl.textContent = 'almost done';
            etaEl.style.color = '#ffaa00';
            timerEl.style.color = '#ffaa00';
        }} else if (remaining > 0) {{
            etaEl.textContent = '~' + remaining + 's left';
        }} else {{
            etaEl.textContent = 'finishing...';
        }}
    }}, 1000);
    </script>
    </body></html>
    """
    placeholder = st.empty()
    with placeholder:
        components.html(html, height=130)
    try:
        yield
    finally:
        placeholder.empty()


@contextmanager
def page_error_boundary(page_name: str):
    """Top-level error boundary for entire page content. Use when a page doesn't
    have granular section-level error boundaries."""
    try:
        yield
    except Exception as e:
        import traceback, html
        logger.error(f"Page error in {page_name}: {e}", exc_info=True)
        tb_str = html.escape(traceback.format_exc())
        st.markdown(
            f'<div class="error-card" style="margin-top:24px;">'
            f'<h4>Something went wrong on this page</h4>'
            f'<p>An unexpected error occurred while loading <strong>{page_name}</strong>. '
            f'Try refreshing the page or navigating to a different section.</p>'
            f'</div>',
            unsafe_allow_html=True,
        )
        with st.expander("Error details", expanded=False):
            st.code(traceback.format_exc(), language="python")

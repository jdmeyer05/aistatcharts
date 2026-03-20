"""Shared layout helpers for AI Statcharts — sidebar, status bar, error boundaries, page setup."""
import sys, os
# Ensure project root is always on sys.path so 'from src.*' imports never break
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import streamlit as st
import logging
import json
from datetime import datetime, timezone
from contextlib import contextmanager
from src.styles import COLORS, APP_VERSION, inject_global_css
from src.ticker_tape import _get_ticker_tape_data
from src.auth import check_auth, check_page_access, render_upgrade_prompt, get_user_tier, get_tier_config, get_usage_summary, render_token_purchase, clear_auth_cookie

logger = logging.getLogger(__name__)

# Page registry: filename key → (title, icon)
PAGE_CONFIG = {
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
}


def setup_page(page_key: str, layout: str = "wide", sidebar_state: str = "collapsed"):
    """Universal page setup. Call this as the FIRST thing on every page.
    Handles: page_config, auth, global CSS, header, ticker tape, tier gating."""
    title, icon = PAGE_CONFIG.get(page_key, ("AI Statcharts", "📊"))
    st.set_page_config(page_title=title, page_icon=icon, layout=layout,
                       initial_sidebar_state=sidebar_state)
    # Hide sidebar content + default nav immediately to prevent flash.
    # This CSS loads as the very first element, before auth or any rendering.
    st.markdown("""<style>
        section[data-testid="stSidebar"] > div:first-child {
            opacity: 0 !important;
            visibility: hidden !important;
        }
        [data-testid="stSidebarNav"],
        [data-testid="stSidebarNavItems"],
        [data-testid="stSidebarNavSeparator"],
        [data-testid="stSidebarNavLink"],
        section[data-testid="stSidebar"] nav,
        section[data-testid="stSidebar"] ul {
            display: none !important;
            height: 0 !important;
            overflow: hidden !important;
        }
        /* Kill all top gaps */
        header[data-testid="stHeader"] { background: transparent !important; height: 0 !important; min-height: 0 !important; padding: 0 !important; margin: 0 !important; overflow: visible !important; }
        .stApp > div:first-child { margin-top: 0 !important; padding-top: 0 !important; }
        [data-testid="stAppViewContainer"] { padding-top: 0 !important; margin-top: 0 !important; }
        .stMainBlockContainer { padding-top: 0 !important; margin-top: 0 !important; }
        .block-container { padding-top: 0 !important; margin-top: 0 !important; }
        div[data-testid="stVerticalBlock"] > div:first-child { margin-top: 0 !important; }
    </style>""", unsafe_allow_html=True)
    check_auth()
    inject_global_css()
    render_sidebar_brand()
    render_header(page_key)
    _inject_mobile_session_guard()
    _inject_footer()
    render_background_notifications()

    # Tier-based page access gating
    if not check_page_access(page_key):
        page_title = PAGE_CONFIG.get(page_key, (page_key, ""))[0].split(" | ")[0]
        render_upgrade_prompt(page_title)
        st.stop()


def _build_usage_badge() -> str:
    """Build an HTML badge showing AI usage / tokens remaining."""
    summary = get_usage_summary()
    if summary["tier"] == "free" and summary["tokens"] == 0:
        return ""

    remaining = summary["total_remaining"]
    color = "#00ff96" if remaining > 10 else "#ffaa00" if remaining > 0 else "#ff4444"

    parts = []
    if summary["daily_limit"] > 0:
        parts.append(f"{summary['daily_remaining']}/{summary['daily_limit']}")
    if summary["tokens"] > 0:
        parts.append(f"+{summary['tokens']}tk")

    label = " ".join(parts) if parts else str(remaining)
    return f'<span class="header-badge" style="color:{color}; border-color:{color};">{label}</span>'


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


def _is_market_hours() -> bool:
    """Check if US equity markets are currently open (ET approximation)."""
    now = datetime.now()
    return now.weekday() < 5 and ((now.hour == 9 and now.minute >= 30) or (10 <= now.hour < 16))


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

    # Stale data detection
    import time as _time
    ticker_data = _get_ticker_tape_data()
    fetched_at = ticker_data.get("_fetched_at", _time.time())
    data_age_sec = _time.time() - fetched_at
    data_age_min = data_age_sec / 60
    is_delayed = data_age_min > 15

    # ── Row 1: Brand bar ──
    usage_badge = _build_usage_badge()
    delayed_badge = (
        f'<span class="header-badge" style="color:#ff4444; border-color:#ff4444;">DELAYED {data_age_min:.0f}m</span>'
        if is_delayed else ""
    )
    header_html = (
        f'<div class="site-header">'
        f'<div class="site-header-brand">'
        f'<img src="./static/logo.png" width="28" height="28" style="vertical-align:middle;margin-right:8px;border-radius:6px;"/>'
        f'<span style="font-size:20px; font-weight:800; color:{COLORS["accent"]}; letter-spacing:1.5px;">AI STATCHARTS</span>'
        f'</div>'
        f'<div class="site-header-badges">'
        f'<span class="header-badge" style="color:{mkt_color}; border-color:{mkt_color};">{mkt_status}</span>'
        f'{delayed_badge}'
        f'<span class="header-badge" style="color:{tier_color}; border-color:{tier_color};">{tier_cfg["name"]}</span>'
        f'{usage_badge}'
        f'<span style="font-size:10px; color:{COLORS["text_muted"]};">{now.strftime("%I:%M %p")}</span>'
        f'</div>'
        f'</div>'
    )
    st.markdown(header_html, unsafe_allow_html=True)

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
            ("21_Fed_Macro_Drivers", "Fed & Macro", "pages/21_Fed_Macro_Drivers.py"),
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

    # ── Recently visited pages (quick-access row) ──
    _track_recent_page(current_page)
    _render_recent_pages(current_page)

    # ── Market ticker strip ──
    # Auto-refresh every 5 min during market hours via st.fragment
    _render_ticker_strip(ticker_data, now)


def _track_recent_page(page_key: str):
    """Track recently visited pages in session state (max 5, no duplicates)."""
    if "recent_pages" not in st.session_state:
        st.session_state["recent_pages"] = []
    recent = st.session_state["recent_pages"]
    # Remove if already present, then prepend
    recent = [p for p in recent if p != page_key]
    recent.insert(0, page_key)
    st.session_state["recent_pages"] = recent[:8]  # keep last 8


def _render_recent_pages(current_page: str):
    """Render a compact row of recently visited pages for quick navigation."""
    recent = st.session_state.get("recent_pages", [])
    # Show pages other than the current one, up to 5
    others = [p for p in recent if p != current_page][:5]
    if not others:
        return

    links = []
    for key in others:
        title, icon = PAGE_CONFIG.get(key, (key, "📊"))
        short = title.split(" | ")[0]
        path = f"pages/{key}.py"
        links.append((key, f"{icon} {short}", path))

    rc = st.columns(len(links) + 1)
    with rc[0]:
        st.markdown(
            f'<div style="font-size:10px;color:{COLORS["text_muted"]};padding-top:6px;">Recent:</div>',
            unsafe_allow_html=True,
        )
    for i, (key, label, path) in enumerate(links):
        with rc[i + 1]:
            st.page_link(path, label=label)


@st.fragment(run_every=300)
def _render_ticker_strip(ticker_data: dict = None, render_time: datetime = None):
    """Render scrolling ticker strip. Auto-refreshes every 5 min.
    During off-hours the cached data is served without hitting yfinance."""
    import time as _time

    # Re-fetch inside fragment on auto-refresh cycles
    if ticker_data is None:
        ticker_data = _get_ticker_tape_data()
    if render_time is None:
        render_time = datetime.now()

    now = render_time
    esc_data = _get_escalation_data()
    war_start = datetime(2026, 2, 28)
    days_of_conflict = (now - war_start).days
    esc_score = esc_data["score"]
    esc_color = "#ff4444" if esc_score >= 8 else "#ff6b35" if esc_score >= 6 else "#ffaa00" if esc_score >= 4 else "#00ff96" if esc_score > 0 else "#888"

    # Data age indicator
    fetched_at = ticker_data.get("_fetched_at", _time.time())
    age_min = (_time.time() - fetched_at) / 60
    if age_min > 15:
        age_str = f'<span style="color:#ff4444;">⚠ {age_min:.0f}m ago</span>'
    elif age_min > 5:
        age_str = f'<span style="color:#ffaa00;">{age_min:.0f}m ago</span>'
    else:
        age_str = f'<span style="color:#555;">{now.strftime("%I:%M %p")}</span>'

    items = []
    for sym in ["^GSPC", "QQQ", "CL=F", "GC=F", "^VIX", "DX-Y.NYB", "TLT", "BTC-USD"]:
        d = ticker_data.get(sym, {})
        if not d or not isinstance(d, dict):
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

        items.append(
            f'<span style="color:#999;font-weight:600;">{label}</span> '
            f'<span style="color:#e0e0e0;">{pstr}</span> '
            f'<span style="color:{color};text-shadow:0 0 6px {color}40;">{arrow}{abs(chg):.1f}%</span>'
        )

    # Append threat indicators
    items.append(f'<span style="color:#999;font-weight:600;">FED</span> <span style="color:#ffaa00;">3.50-3.75%</span>')
    esc_level = "CRITICAL" if esc_score >= 8 else "HIGH" if esc_score >= 6 else "ELEVATED" if esc_score >= 4 else "MOD"
    items.append(
        f'<span style="color:#999;font-weight:600;">IRAN WAR</span> '
        f'<span style="color:{esc_color};text-shadow:0 0 8px {esc_color}50;">Day {days_of_conflict} · {esc_score}/10 {esc_level}</span>'
    )
    items.append(age_str)

    separator = '&nbsp;&nbsp;&nbsp;<span style="color:#30363d;opacity:0.6;">◆</span>&nbsp;&nbsp;&nbsp;'
    single_tape = separator.join(items)
    full_tape = f"{single_tape}{separator}{single_tape}{separator}{single_tape}"

    elapsed = _time.time() % 40

    # ── Append social posts to the same tape ──
    # Use watchlist tickers if user opted in, otherwise default macro tickers
    use_watchlist = st.session_state.get("scroll_use_watchlist", False)
    if use_watchlist:
        wl = st.session_state.get("watchlist", {})
        wl_symbols = list(wl.keys()) if wl else []
        symbols_key = ",".join(wl_symbols) if wl_symbols else ""
    else:
        symbols_key = ""
    social_posts = _get_social_feed(symbols_key) if symbols_key or not use_watchlist else []
    if social_posts:
        for p in social_posts:
            sym = p["symbol"]
            body = p["body"].replace("<", "&lt;").replace(">", "&gt;")
            user_handle = p.get("user", "")
            sent = p.get("sentiment", "")
            if sent == "Bullish":
                sc = "#00ff96"
                si = "▲"
            elif sent == "Bearish":
                sc = "#ff4444"
                si = "▼"
            else:
                sc = "#888"
                si = "●"
            handle_html = f'<span style="color:#666;">{user_handle}</span> ' if user_handle else ""
            items.append(
                f'<span style="color:{sc};">{si}</span> '
                f'<span style="color:{COLORS["accent"]};font-weight:600;">${sym}</span> '
                f'{handle_html}'
                f'<span style="color:#bbb;">{body}</span>'
            )

    # Rebuild tape with combined items
    separator = '&nbsp;&nbsp;&nbsp;<span style="color:#30363d;opacity:0.6;">◆</span>&nbsp;&nbsp;&nbsp;'
    single_tape = separator.join(items)
    full_tape = f"{single_tape}{separator}{single_tape}{separator}{single_tape}"

    # Slower scroll to accommodate more content
    scroll_duration = 40 + (len(social_posts) * 3 if social_posts else 0)
    elapsed = _time.time() % scroll_duration

    st.markdown(
        f"""<div style="position:relative; overflow:hidden; white-space:nowrap;
            background:linear-gradient(180deg, rgba(0,0,0,0.7) 0%, rgba(10,14,20,0.85) 100%);
            padding:8px 0; margin-bottom:6px; border-radius:4px; font-size:13px; font-weight:500;
            letter-spacing:0.3px; word-spacing:2px;
            border:1px solid {COLORS['card_border']};
            box-shadow: 0 2px 12px rgba(0,209,255,0.06), inset 0 1px 0 rgba(255,255,255,0.03);">
<div style="position:absolute;left:0;top:0;bottom:0;width:40px;z-index:2;
            background:linear-gradient(90deg, rgba(10,14,20,0.95), transparent);pointer-events:none;"></div>
<div style="position:absolute;right:0;top:0;bottom:0;width:40px;z-index:2;
            background:linear-gradient(-90deg, rgba(10,14,20,0.95), transparent);pointer-events:none;"></div>
<div style="display:inline-block; animation:tickerscroll {scroll_duration}s linear infinite; animation-delay:-{elapsed:.1f}s;">
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


import re as _re

# Roots that match standalone or as part of compounds (e.g. "bullshit", "bullshitter", "fucked")
_PROFANITY_ROOTS = [
    "fuck", "shit", "cunt", "cock", "dick", "pussy", "faggot", "nigger", "nigga", "retard",
]
# Exact-match words only
_PROFANITY_EXACT = {
    "damn", "bitch", "ass", "stfu", "gtfo", "lmao", "lmfao", "wtf", "af", "bs",
    "fk", "fck", "shite", "azz", "b1tch", "f*ck", "sh*t", "a$$",
}
# Pre-compile root patterns: match the root + any suffix (fucked, fucker, fucking, bullshit, bullshitter, etc.)
_PROFANITY_RE = _re.compile(
    r'\b(?:' + '|'.join(
        # Also match common prefixes like "bull" + root
        f'(?:bull|mother|horse|dumb|half)?{root}\\w*'
        for root in _PROFANITY_ROOTS
    ) + r')\b',
    _re.IGNORECASE,
)


def _is_clean_post(body: str) -> bool:
    """Filter out low-quality social posts: hashtag spam, ticker-only, profanity, all-caps."""
    if not body or len(body.strip()) < 20:
        return False
    text = body.strip()
    # Hashtag spam: >40% hashtags
    hashtag_chars = sum(len(w) for w in text.split() if w.startswith("#"))
    if len(text) > 0 and hashtag_chars > len(text) * 0.4:
        return False
    # Ticker/mention-only: strip $tickers, #tags, @mentions — need 3+ real words left
    real_words = [w for w in text.split() if not w.startswith(("$", "#", "@")) and len(w) > 1]
    if len(real_words) < 3:
        return False
    # Profanity — regex roots (catches bullshit, bullshitter, fucked, shitty, etc.)
    if _PROFANITY_RE.search(text):
        return False
    # Profanity — exact match words
    text_lower = f" {text.lower()} "
    for word in _PROFANITY_EXACT:
        if f" {word} " in text_lower:
            return False
    # All-caps shouting
    alpha = [c for c in text if c.isalpha()]
    if len(alpha) > 10 and sum(1 for c in alpha if c.isupper()) / len(alpha) > 0.8:
        return False
    return True


def _clean_tweet_tickers(body: str, primary_symbol: str) -> str:
    """Strip all $TICKER symbols except the primary one being fetched."""
    primary_upper = primary_symbol.upper()
    def _replace_ticker(match):
        ticker = match.group(0)
        if ticker[1:].upper() == primary_upper:
            return ticker
        return ""
    cleaned = _re.sub(r'\$[A-Za-z]{1,5}', _replace_ticker, body)
    # Collapse multiple spaces left behind
    cleaned = _re.sub(r'  +', ' ', cleaned).strip()
    return cleaned


_DEFAULT_SOCIAL_SYMBOLS = ["SPY", "QQQ", "AAPL", "TSLA", "NVDA", "OIL", "GLD", "BTC.X", "VIX"]
_MIN_FOLLOWERS = 1000000  # Minimum followers for a trusted source


@st.cache_data(ttl=600, show_spinner=False)
def _get_social_feed(_symbols_key: str = "") -> list:
    """Fetch trending StockTwits posts, filtered for quality and trusted sources.
    _symbols_key is a comma-joined string used as cache key (hashable)."""
    symbols = _symbols_key.split(",") if _symbols_key else _DEFAULT_SOCIAL_SYMBOLS
    posts = []
    try:
        from curl_cffi import requests as cffi_requests
    except ImportError:
        return posts
    for sym in symbols:
        sym = sym.strip()
        if not sym:
            continue
        # StockTwits uses .X suffix for crypto
        api_sym = sym if "." in sym else (f"{sym}.X" if sym in ("BTC", "ETH", "SOL", "DOGE") else sym)
        try:
            r = cffi_requests.get(
                f"https://api.stocktwits.com/api/2/streams/symbol/{api_sym}.json?limit=15",
                impersonate="chrome", timeout=8,
            )
            if r.status_code != 200:
                continue
            data = r.json()
            clean_sym = sym.replace(".X", "")
            for m in data.get("messages", []):
                # Trust filter: require minimum follower count
                user = m.get("user", {})
                followers = user.get("followers", 0)
                is_official = user.get("official", False)
                if not is_official and followers < _MIN_FOLLOWERS:
                    continue
                body = m.get("body", "").replace("\n", " ").strip()
                if not _is_clean_post(body):
                    continue
                body = _clean_tweet_tickers(body, clean_sym)
                if len(body) < 15 or len(body) > 100:
                    continue
                sent = (m.get("entities", {}).get("sentiment") or {}).get("basic", "")
                likes = m.get("likes", {}).get("total", 0)
                username = user.get("username", "")
                posts.append({
                    "symbol": clean_sym,
                    "body": body[:100],
                    "sentiment": sent,
                    "likes": likes,
                    "user": f"@{username}" if username else "",
                    "followers": followers,
                })
        except Exception:
            continue
    # Sort by likes descending, take top 15
    posts.sort(key=lambda p: p.get("likes", 0), reverse=True)
    return posts[:15]


@st.cache_data(ttl=300, show_spinner=False)
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

    # Scroll feed source toggle
    st.sidebar.markdown("---")
    wl = st.session_state.get("watchlist", {})
    wl_count = len(wl)
    scroll_options = ["Market Feed"]
    if wl_count > 0:
        scroll_options.append(f"My Watchlist ({wl_count})")
    scroll_choice = st.sidebar.radio(
        "Social Feed", scroll_options, key="scroll_feed_radio",
        horizontal=True, label_visibility="collapsed",
    )
    st.session_state["scroll_use_watchlist"] = scroll_choice != "Market Feed"
    st.sidebar.caption("Social feed in ticker tape")

    # Logout button
    st.sidebar.markdown("---")
    email = st.session_state.get("user_email", "")
    if email and email != "local-dev@preview":
        st.sidebar.caption(f"Signed in as **{email}**")
        if st.sidebar.button("Log Out", key="sidebar_logout", use_container_width=True):
            clear_auth_cookie()
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            st.switch_page("app.py")




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
    """Get the active ticker from query params or session state.
    Use as the default value for ticker inputs to enable cross-page linking."""
    # Priority: query param > session state > default
    qp_ticker = st.query_params.get("ticker", "").strip().upper()
    if qp_ticker:
        st.session_state["active_ticker"] = qp_ticker
        return qp_ticker
    return st.session_state.get("active_ticker", default)


def set_active_ticker(ticker: str):
    """Set the active ticker in session state for cross-page linking."""
    ticker = ticker.strip().upper()
    if ticker:
        st.session_state["active_ticker"] = ticker


def render_skeleton_cards(count: int = 4, cols: int = 4):
    """Render shimmer skeleton placeholder cards for loading states."""
    card_html = (
        '<div class="skeleton skeleton-card">'
        '<div class="skeleton-line title"></div>'
        '<div class="skeleton-line price"></div>'
        '<div class="skeleton-line chart"></div>'
        '</div>'
    )
    col_list = st.columns(cols)
    for i in range(count):
        with col_list[i % cols]:
            st.markdown(card_html, unsafe_allow_html=True)


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
        logger.error(f"Page error in {page_name}: {e}", exc_info=True)
        st.markdown(
            f'<div class="error-card">'
            f'<h4>Something went wrong on this page</h4>'
            f'<p>An unexpected error occurred while loading <strong>{page_name}</strong>. '
            f'Try refreshing the page or navigating to a different section.</p>'
            f'<p style="font-family:monospace;font-size:0.75rem;color:#666;margin-top:8px;">{type(e).__name__}: {str(e)[:200]}</p>'
            f'</div>',
            unsafe_allow_html=True,
        )

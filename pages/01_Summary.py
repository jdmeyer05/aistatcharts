import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import json
import os
import logging
from datetime import datetime, date, timedelta
from src.auth import init_supabase, get_user_tier, get_tier_config, TIERS
from src.layout import setup_page, error_boundary, freshness_bar
from src.styles import COLORS
from src.data_engine import polygon_batch_snapshot, polygon_symbol

setup_page("01_Summary")
logger = logging.getLogger(__name__)

supabase = init_supabase()
user_email = st.session_state.get("user_email", "User")
tier = get_user_tier()
tier_config = get_tier_config(tier)

# Post-checkout confirmation
query_params = st.query_params
if query_params.get("checkout") == "success":
    plan = query_params.get("plan", "")
    st.success(f"Welcome to **{plan.title()}**! Your subscription is now active." if plan else "Payment successful!")
    st.query_params.clear()
elif query_params.get("checkout") == "tokens":
    st.success("Tokens purchased! Your balance has been updated.")
    st.query_params.clear()

from src.auth import check_payment_failures
check_payment_failures()


# ═══════════════════════════════════════════════
# ROW 0: MARKET PULSE BAR (auto-refresh 2 min)
# ═══════════════════════════════════════════════

PULSE_TICKERS = [
    ("SPY", "S&P 500"), ("QQQ", "Nasdaq"), ("IWM", "Russell"),
    ("^VIX", "VIX"), ("GLD", "Gold"), ("USO", "Crude"),
    ("TLT", "20Y Bond"), ("DX-Y.NYB", "Dollar"),
]

FUTURES_TICKERS = [
    ("ES=F", "ES"), ("NQ=F", "NQ"), ("YM=F", "Dow"),
    ("CL=F", "Crude"), ("GC=F", "Gold"), ("SI=F", "Silver"),
    ("NG=F", "NatGas"), ("ZB=F", "30Y Bond"), ("ZN=F", "10Y Note"),
    ("6E=F", "Euro FX"), ("BTC-USD", "Bitcoin"),
]

@st.fragment(run_every=120)
def _market_pulse():
    st.session_state["_prices_last_update"] = datetime.now()
    tickers = [t for t, _ in PULSE_TICKERS]
    snaps = polygon_batch_snapshot(tickers)

    cells = []
    for ticker, label in PULSE_TICKERS:
        snap = snaps.get(ticker)
        if not snap or not snap.get("price"):
            continue
        price = snap["price"]
        chg = snap.get("change", 0)
        color = COLORS["success"] if chg >= 0 else COLORS["danger"]
        arrow = "▲" if chg >= 0 else "▼"

        if ticker in ("^VIX", "^TNX", "DX-Y.NYB"):
            p_str = f"{price:.2f}"
        elif price >= 100:
            p_str = f"${price:,.0f}"
        else:
            p_str = f"${price:.2f}"

        cells.append(
            f'<div style="flex:1 1 100px;text-align:center;padding:6px 4px;">'
            f'<div style="font-size:0.6rem;color:{COLORS["text_muted"]};text-transform:uppercase;">{label}</div>'
            f'<div style="font-size:1rem;font-weight:700;">{p_str}</div>'
            f'<div style="font-size:0.75rem;color:{color};font-weight:600;">{arrow}{abs(chg):.2f}%</div>'
            f'</div>'
        )

    if cells:
        st.markdown(
            f'<div style="display:flex;flex-wrap:wrap;gap:2px;border:1px solid {COLORS["card_border"]};'
            f'border-radius:8px;background:{COLORS["card_bg"]};margin-bottom:12px;">{"".join(cells)}</div>',
            unsafe_allow_html=True,
        )

_market_pulse()

# Futures bar
@st.fragment(run_every=120)
def _futures_pulse():
    tickers = [t for t, _ in FUTURES_TICKERS]
    snaps = polygon_batch_snapshot(tickers)

    cells = []
    for ticker, label in FUTURES_TICKERS:
        snap = snaps.get(ticker)
        if not snap or not snap.get("price"):
            continue
        price = snap["price"]
        chg = snap.get("change", 0)
        color = COLORS["success"] if chg >= 0 else COLORS["danger"]
        arrow = "▲" if chg >= 0 else "▼"

        if ticker == "BTC-USD":
            p_str = f"${price:,.0f}"
        elif price >= 1000:
            p_str = f"{price:,.0f}"
        elif price >= 10:
            p_str = f"{price:.1f}"
        else:
            p_str = f"{price:.3f}"

        cells.append(
            f'<div style="flex:1 1 85px;text-align:center;padding:5px 3px;">'
            f'<div style="font-size:0.55rem;color:{COLORS["text_muted"]};text-transform:uppercase;">{label}</div>'
            f'<div style="font-size:0.9rem;font-weight:700;">{p_str}</div>'
            f'<div style="font-size:0.7rem;color:{color};font-weight:600;">{arrow}{abs(chg):.2f}%</div>'
            f'</div>'
        )

    if cells:
        st.markdown(
            f'<div style="display:flex;flex-wrap:wrap;gap:2px;border:1px solid {COLORS["card_border"]};'
            f'border-radius:8px;background:{COLORS["card_bg"]};margin-bottom:12px;">'
            f'<div style="padding:6px 10px;display:flex;align-items:center;">'
            f'<span style="font-size:0.6rem;color:{COLORS["text_muted"]};writing-mode:vertical-rl;'
            f'text-orientation:mixed;letter-spacing:1px;">FUTURES</span></div>'
            f'{"".join(cells)}</div>',
            unsafe_allow_html=True,
        )

_futures_pulse()

# Data freshness bar for market data
freshness_bar(
    ("Prices", st.session_state.get("_prices_last_update"), 5, 15),
    ("Signals", st.session_state.get("_signals_last_update"), 2, 10),
    ("Positions", st.session_state.get("_positions_last_update"), 5, 15),
)


# ═══════════════════════════════════════════════
# ROW 0.5: AI MARKET INTELLIGENCE
# ═══════════════════════════════════════════════

st.markdown("##### Market Intelligence")

_mi_left, _mi_right = st.columns([3, 2])

with _mi_left:
    with st.container(border=True):
        with error_boundary("Market News"):
            _news_content = None
            _news_age = None
            try:
                from src.ai_cache import get_cached_ai
                # Try current hour, then previous hour
                _now = datetime.now()
                _hour_key = f"market_news_{_now.strftime('%Y%m%d_%H')}"
                _news_content = get_cached_ai(_hour_key)
                if _news_content:
                    _news_age = "< 1 hour"
                else:
                    _prev = _now - timedelta(hours=1)
                    _hour_key_prev = f"market_news_{_prev.strftime('%Y%m%d_%H')}"
                    _news_content = get_cached_ai(_hour_key_prev)
                    if _news_content:
                        _news_age = "1-2 hours"
            except Exception:
                pass

            if _news_content:
                _age_color = COLORS["success"] if _news_age == "< 1 hour" else COLORS["warning"]
                st.markdown(
                    f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">'
                    f'<span style="font-size:0.7rem;color:{COLORS["text_muted"]};text-transform:uppercase;">Grok Market Scan</span>'
                    f'<span style="font-size:0.65rem;color:{_age_color};">{_news_age} ago</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
                # Render with st.markdown (handles markdown natively, no HTML injection risk)
                st.markdown(_news_content, unsafe_allow_html=False)
            else:
                st.markdown(
                    f'<div style="font-size:0.7rem;color:{COLORS["text_muted"]};text-transform:uppercase;margin-bottom:6px;">Grok Market Scan</div>',
                    unsafe_allow_html=True,
                )
                st.caption("Market news scan not yet available. It updates hourly during market hours, or click below to scan now.")

            # On-demand refresh button
            @st.fragment
            def _refresh_news():
                if st.button("Refresh News", key="refresh_market_news", use_container_width=True):
                    from src.api_keys import get_secret
                    _grok_key = get_secret("GROK_API_KEY")
                    if not _grok_key:
                        st.warning("Grok API key not configured.")
                        return
                    with st.spinner("Scanning X/Twitter for market news..."):
                        try:
                            from openai import OpenAI as _OAI
                            _client = _OAI(api_key=_grok_key, base_url="https://api.x.ai/v1")
                            _now_str = datetime.now().strftime("%B %d, %Y %I:%M %p ET")
                            _weekday = datetime.now().strftime("%A")
                            _resp = _client.chat.completions.create(
                                model="grok-4-1-fast-reasoning",
                                messages=[
                                    {"role": "system", "content": (
                                        "You are a senior market intelligence analyst. "
                                        "Scan X/Twitter and news for developments moving markets right now. "
                                        "Be direct, specific, quantitative. No boilerplate."
                                    )},
                                    {"role": "user", "content": (
                                        f"TODAY: {_weekday}, {_now_str}. Search X/Twitter and financial news RIGHT NOW.\n\n"
                                        "Report the most market-moving developments from the LAST 4 HOURS.\n\n"
                                        "COVER (skip categories with nothing notable):\n"
                                        "1. MACRO & FED — data releases, Fed speakers, rate expectations\n"
                                        "2. EARNINGS — beats/misses, guidance, pre-market movers\n"
                                        "3. GEOPOLITICAL — trade, sanctions, conflicts, tariffs\n"
                                        "4. SECTOR MOVES — rotation, breakouts, breakdowns\n"
                                        "5. COMMODITIES & FX — oil/gold/dollar/crypto with catalysts\n"
                                        "6. OPTIONS & FLOW — unusual volume, VIX, skew shifts\n\n"
                                        "SOURCES: @DeItaone, @Fxhedgers, @unusual_whales, @spotgamma, "
                                        "@NickTimiraos, @LiveSquawk, @CNBC, @Bloomberg\n\n"
                                        "Lead with the biggest story. Bullet the rest. 200-300 words. "
                                        "Name tickers and numbers. If markets are quiet, say so briefly.\n\n"
                                        "ACCURACY: Only confirmed developments. No speculation."
                                    )},
                                ],
                                max_tokens=1200,
                                temperature=0.2,
                            )
                            _fresh_news = _resp.choices[0].message.content.strip()
                            if _fresh_news:
                                from src.ai_cache import cache_ai_response
                                _hk = f"market_news_{datetime.now().strftime('%Y%m%d_%H')}"
                                cache_ai_response(_hk, _fresh_news, model="grok-4-1-fast",
                                                   source_page="market_news", ticker="MARKET",
                                                   ttl_hours=1.5, prompt_summary="On-demand market news scan")
                                st.markdown(_fresh_news, unsafe_allow_html=False)
                                st.success("News updated.")
                        except Exception as e:
                            st.error(f"Scan failed: {e}")
            _refresh_news()

with _mi_right:
    with st.container(border=True):
        with error_boundary("Upcoming Events"):
            st.markdown(
                f'<div style="font-size:0.7rem;color:{COLORS["text_muted"]};text-transform:uppercase;margin-bottom:6px;">Upcoming Events</div>',
                unsafe_allow_html=True,
            )
            try:
                from src.economic_calendar import find_events_near_date, get_upcoming_fomc, FOMC_SEP_DATES
                _today = date.today()
                _events = find_events_near_date(_today.strftime("%Y-%m-%d"), window_days=14)
                _fomc_dates = get_upcoming_fomc(3)

                _ev_items = []
                for ev in (_events or []):
                    days = ev.get("days_away", 0)
                    if days < 0:
                        continue
                    urgency = COLORS["danger"] if days == 0 else COLORS["warning"] if days <= 2 else COLORS["text_muted"]
                    when = "TODAY" if days == 0 else f"in {days}d"
                    _ev_items.append((days, ev["name"], when, urgency, ev.get("date", "")))

                _event_dates = {e.get("date", "") for e in (_events or [])}
                for fd in (_fomc_dates or []):
                    if fd not in _event_dates:
                        fd_dt = pd.to_datetime(fd).date()
                        days = (fd_dt - _today).days
                        if days > 0:
                            _is_sep = fd in FOMC_SEP_DATES
                            _label = "FOMC + SEP/Dots" if _is_sep else "FOMC Meeting"
                            urgency = COLORS["danger"] if days <= 1 else COLORS["warning"] if days <= 7 else COLORS["text_muted"]
                            _ev_items.append((days, _label, f"in {days}d", urgency, fd))

                _ev_items.sort(key=lambda x: x[0])

                if _ev_items:
                    _ev_html = ""
                    for _, name, when, color, dt_str in _ev_items[:8]:
                        _ev_html += (
                            f'<div style="display:flex;justify-content:space-between;padding:3px 0;'
                            f'border-bottom:1px solid {COLORS["card_border"]};">'
                            f'<span style="font-size:0.78rem;">{name}</span>'
                            f'<div style="display:flex;gap:8px;">'
                            f'<span style="font-size:0.68rem;color:{COLORS["text_muted"]};">{dt_str}</span>'
                            f'<span style="font-size:0.68rem;font-weight:600;color:{color};">{when}</span>'
                            f'</div></div>'
                        )
                    st.markdown(
                        f'<div style="padding:2px 0;">{_ev_html}</div>',
                        unsafe_allow_html=True,
                    )
                else:
                    st.caption("No major events in the next 14 days.")
            except Exception as e:
                logger.debug(f"Events load failed: {e}")
                st.caption("Event calendar unavailable.")

    # Risk snapshot below events (compact)
    with st.container(border=True):
        with error_boundary("Risk Snapshot"):
            _risk_cols = st.columns(3)
            # Iran conflict score
            with _risk_cols[0]:
                try:
                    _conflict_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), "src", "iran_conflict_history.json")
                    if os.path.exists(_conflict_file):
                        with open(_conflict_file, "r") as f:
                            _cd = json.load(f)
                        if _cd:
                            _bl = _cd[-1].get("blended", {})
                            _esc = _bl.get("escalation_risk", {})
                            _sc = _esc.get("score", 0)
                            _ec = "#ff4444" if _sc >= 8 else "#ff6b35" if _sc >= 6 else "#ffaa00" if _sc >= 4 else "#00ff96"
                            st.markdown(
                                f'<div style="text-align:center;">'
                                f'<div style="font-size:0.6rem;color:{COLORS["text_muted"]};text-transform:uppercase;">Iran</div>'
                                f'<div style="font-size:1.3rem;font-weight:800;color:{_ec};">{_sc}/10</div>'
                                f'</div>',
                                unsafe_allow_html=True,
                            )
                    else:
                        st.markdown(f'<div style="text-align:center;font-size:0.7rem;color:{COLORS["text_muted"]};">Iran: N/A</div>', unsafe_allow_html=True)
                except Exception:
                    st.markdown(f'<div style="text-align:center;font-size:0.7rem;color:{COLORS["text_muted"]};">Iran: N/A</div>', unsafe_allow_html=True)

            # Macro regime
            with _risk_cols[1]:
                try:
                    _grok_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), "src", "grok_regime_history.json")
                    if os.path.exists(_grok_file):
                        with open(_grok_file, "r") as f:
                            _gd = json.load(f)
                        if _gd:
                            _regs = sorted(_gd[-1].get("regimes", []), key=lambda r: r.get("probability", 0), reverse=True)
                            if _regs:
                                _rn = _regs[0].get("name", "?")
                                _rp = _regs[0].get("probability", 0)
                                _regime_colors = {"Stagflation": "#ff4444", "Recession": "#ff8c00", "Soft Landing": "#00cc66",
                                                  "Financial Crisis": "#ff0066", "Re-Acceleration": "#00d1ff", "Goldilocks": "#aa66ff"}
                                _rc2 = _regime_colors.get(_rn, "#888")
                                st.markdown(
                                    f'<div style="text-align:center;">'
                                    f'<div style="font-size:0.6rem;color:{COLORS["text_muted"]};text-transform:uppercase;">Macro</div>'
                                    f'<div style="font-size:0.85rem;font-weight:700;color:{_rc2};">{_rn}</div>'
                                    f'<div style="font-size:0.65rem;color:{COLORS["text_muted"]};">{_rp}%</div>'
                                    f'</div>',
                                    unsafe_allow_html=True,
                                )
                    else:
                        st.markdown(f'<div style="text-align:center;font-size:0.7rem;color:{COLORS["text_muted"]};">Macro: N/A</div>', unsafe_allow_html=True)
                except Exception:
                    st.markdown(f'<div style="text-align:center;font-size:0.7rem;color:{COLORS["text_muted"]};">Macro: N/A</div>', unsafe_allow_html=True)

            # Vol regime (SPY)
            with _risk_cols[2]:
                try:
                    from src.metrics_store import get_latest_snapshot
                    _spy_snap = get_latest_snapshot("SPY")
                    if _spy_snap and _spy_snap.get("atm_iv") is not None:
                        _iv = _spy_snap["atm_iv"]
                        _iv_label = "High" if _iv > 0.25 else "Low" if _iv < 0.15 else "Normal"
                        _iv_color = COLORS["danger"] if _iv > 0.25 else COLORS["success"] if _iv < 0.15 else COLORS["warning"]
                        st.markdown(
                            f'<div style="text-align:center;">'
                            f'<div style="font-size:0.6rem;color:{COLORS["text_muted"]};text-transform:uppercase;">SPY Vol</div>'
                            f'<div style="font-size:0.85rem;font-weight:700;color:{_iv_color};">{_iv:.0%} ({_iv_label})</div>'
                            f'</div>',
                            unsafe_allow_html=True,
                        )
                    else:
                        st.markdown(f'<div style="text-align:center;font-size:0.7rem;color:{COLORS["text_muted"]};">Vol: N/A</div>', unsafe_allow_html=True)
                except Exception:
                    st.markdown(f'<div style="text-align:center;font-size:0.7rem;color:{COLORS["text_muted"]};">Vol: N/A</div>', unsafe_allow_html=True)


# ═══════════════════════════════════════════════
# ROW 1: THREE-COLUMN LIVE DASHBOARD
# ═══════════════════════════════════════════════

dash_c1, dash_c2, dash_c3 = st.columns(3)

# ── Column 1: Signal Composite ──
with dash_c1:
    with st.container(border=True):
        with error_boundary("Signal Dashboard"):
            st.markdown(f'<div style="font-size:0.7rem;color:{COLORS["text_muted"]};text-transform:uppercase;margin-bottom:4px;">Signal Engine</div>', unsafe_allow_html=True)
            try:
                from src.signal_engine import get_signal_summary, get_top_trade_ideas

                @st.fragment(run_every=60)
                def _signals_card():
                    st.session_state["_signals_last_update"] = datetime.now()
                    sig = get_signal_summary()
                    if sig["n_tickers"] > 0:
                        m1, m2 = st.columns(2)
                        m1.metric("Bullish", sig["n_bullish"])
                        m2.metric("Bearish", sig["n_bearish"])

                        top = get_top_trade_ideas(5)
                        for t in top:
                            icon = "🟢" if t["overall_direction"] == "bull" else ("🔴" if t["overall_direction"] == "bear" else "⚪")
                            st.markdown(
                                f'<div style="font-size:0.8rem;padding:2px 0;">'
                                f'{icon} <strong>{t["ticker"]}</strong> '
                                f'<span style="color:{COLORS["text_muted"]};">{t["overall_conviction"]:.0%} · {t["n_signals"]} src</span>'
                                f'</div>', unsafe_allow_html=True,
                            )
                    else:
                        st.caption("Load analysis pages to generate signals.")
                _signals_card()
            except Exception:
                st.caption("Signal engine loading...")

# ── Column 2: Vol Regime ──
with dash_c2:
    with st.container(border=True):
        with error_boundary("Vol Regime"):
            st.markdown(f'<div style="font-size:0.7rem;color:{COLORS["text_muted"]};text-transform:uppercase;margin-bottom:4px;">Vol Regime</div>', unsafe_allow_html=True)
            try:
                from src.metrics_store import get_latest_snapshot, percentile_ranks_all
                spy_snap = get_latest_snapshot("SPY")
                if spy_snap:
                    iv = spy_snap.get("atm_iv")
                    skew = spy_snap.get("put_skew")
                    vrp = spy_snap.get("vrp")
                    hv20 = spy_snap.get("hv20")

                    if iv:
                        vol_level = "High" if iv > 0.30 else ("Low" if iv < 0.15 else "Normal")
                        vol_color = COLORS["danger"] if vol_level == "High" else (COLORS["success"] if vol_level == "Low" else COLORS["warning"])
                        st.markdown(
                            f'<div style="font-size:1.3rem;font-weight:800;color:{vol_color};">{vol_level}</div>'
                            f'<div style="font-size:0.75rem;color:{COLORS["text_muted"]};">ATM IV: {iv:.0%}</div>',
                            unsafe_allow_html=True,
                        )

                    mc1, mc2 = st.columns(2)
                    if vrp is not None:
                        vrp_label = "Rich" if vrp > 0.04 else ("Cheap" if vrp < -0.02 else "Fair")
                        mc1.metric("VRP", f"{vrp:+.1%}", vrp_label)
                    if skew:
                        mc2.metric("Put Skew", f"{skew:.2f}x")

                    # Percentiles
                    pcts = percentile_ranks_all("SPY")
                    pct_parts = []
                    for key, label in [("atm_iv", "IV"), ("vrp", "VRP"), ("put_skew", "Skew")]:
                        p = pcts.get(key)
                        if p is not None:
                            c = COLORS["danger"] if p > 80 else (COLORS["success"] if p < 20 else COLORS["text_muted"])
                            pct_parts.append(f'<span style="color:{c};">{label} {p:.0f}th</span>')
                    if pct_parts:
                        st.markdown(f'<div style="font-size:0.65rem;color:{COLORS["text_muted"]};">{"  ·  ".join(pct_parts)}</div>', unsafe_allow_html=True)
                else:
                    st.caption("Load Vol Surface page to populate metrics.")

                if st.button("Vol Surface →", key="goto_vol", use_container_width=True):
                    st.switch_page("pages/43_Vol_Surface.py")
            except Exception:
                st.caption("Metrics loading...")

# ── Column 3: Position Book ──
with dash_c3:
    with st.container(border=True):
        with error_boundary("Positions"):
            st.markdown(f'<div style="font-size:0.7rem;color:{COLORS["text_muted"]};text-transform:uppercase;margin-bottom:4px;">Position Book</div>', unsafe_allow_html=True)
            try:
                from src.position_book import get_portfolio_summary

                @st.fragment(run_every=120)
                def _positions_card():
                    st.session_state["_positions_last_update"] = datetime.now()
                    pb = get_portfolio_summary()
                    if pb.get("n_positions", 0) > 0:
                        pc1, pc2 = st.columns(2)
                        pc1.metric("Positions", pb["n_positions"])
                        pnl_color = "normal" if pb["total_pnl"] >= 0 else "inverse"
                        pc2.metric("P&L", f"${pb['total_pnl']:+,.0f}", delta_color=pnl_color)

                        for a in pb.get("alerts", [])[:3]:
                            if a["severity"] == "breach":
                                st.error(f"**{a['ticker']}** {a['alert_type']}: {a['current']}")
                            else:
                                st.warning(f"**{a['ticker']}** {a['alert_type']}: {a['current']}")

                        for p in pb.get("positions", [])[:4]:
                            pnl = p.get("pnl", 0)
                            icon = "🟢" if pnl >= 0 else "🔴"
                            st.markdown(
                                f'<div style="font-size:0.8rem;padding:2px 0;">'
                                f'{icon} <strong>{p["ticker"]}</strong> {p["type"]} '
                                f'<span style="color:{COLORS["success"] if pnl >= 0 else COLORS["danger"]};">${pnl:+,.0f}</span>'
                                f'</div>', unsafe_allow_html=True,
                            )
                    else:
                        st.caption("No open positions.")
                    if st.button("Portfolio Greeks →", key="goto_greeks", use_container_width=True):
                        st.switch_page("pages/44_Portfolio_Greeks.py")
                _positions_card()
            except Exception:
                st.caption("Position book loading...")


# ═══════════════════════════════════════════════
# ROW 2: MARKET HEATMAP
# ═══════════════════════════════════════════════

PERF_LISTS = {
    "Sectors": [
        ("XLK", "Technology", "#00e5ff"), ("XLF", "Financials", "#00ff87"),
        ("XLE", "Energy", "#ff3333"), ("XLV", "Healthcare", "#bf6fff"),
        ("XLY", "Consumer Disc.", "#ffbb00"), ("XLP", "Cons. Staples", "#ff5ecf"),
        ("XLI", "Industrials", "#a0e515"), ("XLB", "Materials", "#00e0d0"),
        ("XLU", "Utilities", "#ffe100"), ("XLRE", "Real Estate", "#ff2277"),
        ("XLC", "Comms", "#33dd55"),
    ],
    "Equity Indices": [
        ("SPY", "S&P 500", "#00e5ff"), ("QQQ", "Nasdaq 100", "#00ff87"),
        ("DIA", "Dow 30", "#ffbb00"), ("IWM", "Russell 2000", "#ff5ecf"),
        ("EFA", "Developed Intl", "#bf6fff"), ("EEM", "Emerging Mkts", "#ff2277"),
        ("VGK", "Europe", "#00e0d0"), ("EWJ", "Japan", "#ffe100"), ("FXI", "China", "#ff3333"),
    ],
    "Fixed Income": [
        ("AGG", "US Agg Bond", "#00e5ff"), ("TLT", "20Y Treasury", "#bf6fff"),
        ("IEF", "7-10Y Treasury", "#8888ff"), ("SHY", "1-3Y Treasury", "#00e0d0"),
        ("TIP", "TIPS", "#a0e515"), ("LQD", "IG Corporate", "#00ccff"),
        ("HYG", "High Yield", "#ffbb00"), ("EMB", "EM Bonds", "#ff2277"),
    ],
    "Commodities": [
        ("GLD", "Gold", "#ffe100"), ("SLV", "Silver", "#ccddee"),
        ("USO", "Crude Oil", "#ff3333"), ("UNG", "Natural Gas", "#ff8800"),
        ("CPER", "Copper", "#ff2277"), ("DBA", "Agriculture", "#00ff87"),
        ("URA", "Uranium", "#bf6fff"),
    ],
    "Mega Caps": [
        ("AAPL", "Apple", "#cccccc"), ("MSFT", "Microsoft", "#00e5ff"),
        ("NVDA", "Nvidia", "#00ff87"), ("AMZN", "Amazon", "#ff8800"),
        ("GOOGL", "Google", "#ff3333"), ("META", "Meta", "#5588ff"),
        ("TSLA", "Tesla", "#ff2277"), ("BRK.B", "Berkshire", "#bf6fff"),
        ("JPM", "JPMorgan", "#00e0d0"), ("V", "Visa", "#ffe100"),
    ],
}


@st.cache_data(ttl=300, show_spinner=False)
def _fetch_grouped_daily(target_date: str) -> dict:
    from src.api_keys import get_secret
    import requests
    key = get_secret("MASSIVE_API_KEY")
    if not key:
        return {}
    try:
        r = requests.get(
            f"https://api.polygon.io/v2/aggs/grouped/locale/us/market/stocks/{target_date}",
            params={"apiKey": key, "adjusted": "true"}, timeout=15,
        )
        if r.status_code == 200:
            return {t["T"]: t["c"] for t in r.json().get("results", []) if t.get("c")}
    except Exception:
        pass
    return {}


@st.cache_data(ttl=300, show_spinner=False)
def _fetch_perf_returns(tickers_key: str, period: str) -> dict:
    tickers = tickers_key.split(",")
    snaps = polygon_batch_snapshot(tickers)

    if period == "1d":
        return {sym: {"pct": snap.get("change", 0), "price": snap["price"]}
                for sym, snap in snaps.items() if snap and snap.get("price")}

    if period == "1w":
        target = date.today() - timedelta(days=7)
    elif period == "ytd":
        target = date(date.today().year, 1, 2)
    else:
        days_map = {"1mo": 30, "3mo": 90, "6mo": 180, "1y": 365}
        target = date.today() - timedelta(days=days_map.get(period, 30))

    historical = {}
    for offset in range(7):
        d = target + timedelta(days=offset)
        if d.weekday() >= 5:
            continue
        historical = _fetch_grouped_daily(d.isoformat())
        if historical:
            break

    result = {}
    for sym in tickers:
        snap = snaps.get(sym)
        if not snap or not snap.get("price"):
            continue
        current = snap["price"]
        poly_sym = polygon_symbol(sym)
        old_price = historical.get(poly_sym) or historical.get(sym)
        pct = ((current / old_price) - 1) * 100 if old_price and old_price > 0 else snap.get("change", 0)
        result[sym] = {"pct": round(pct, 2), "price": current}
    return result


with error_boundary("Market Heatmap"):
    # Load saved heatmap preferences
    try:
        from src.user_prefs import load_pref, save_pref as _save_hm_pref
        _saved_list = load_pref("hm_list", "Sectors")
        _saved_period = load_pref("hm_period", "1D")
    except Exception:
        _saved_list, _saved_period = "Sectors", "1D"

    _list_options = list(PERF_LISTS.keys())
    _period_options = ["1D", "1W", "1M", "3M", "YTD", "1Y"]
    _list_idx = _list_options.index(_saved_list) if _saved_list in _list_options else 0
    _period_idx = _period_options.index(_saved_period) if _saved_period in _period_options else 0

    hm1, hm2, hm3 = st.columns([3, 1, 1])
    with hm1:
        st.markdown("##### Market Heatmap")
    with hm2:
        hm_list = st.selectbox("List", _list_options, key="hm_list", index=_list_idx, label_visibility="collapsed")
    with hm3:
        hm_period_label = st.selectbox("Period", _period_options,
                                        key="hm_period", index=_period_idx, label_visibility="collapsed")

    # Save if changed
    try:
        if hm_list != _saved_list:
            _save_hm_pref("hm_list", hm_list)
        if hm_period_label != _saved_period:
            _save_hm_pref("hm_period", hm_period_label)
    except Exception:
        pass
    period_map = {"1D": "1d", "1W": "1w", "1M": "1mo", "3M": "3mo", "YTD": "ytd", "1Y": "1y"}
    hm_period = period_map[hm_period_label]

    perf_list = PERF_LISTS[hm_list]
    all_tickers = [t for t, _, _ in perf_list]
    returns = _fetch_perf_returns(",".join(all_tickers), hm_period)

    if not returns:
        st.caption("Market data loading — prices will appear on next refresh.")
    else:
        cells = []
        for ticker, label, color in perf_list:
            data = returns.get(ticker)
            if not data:
                continue
            pct = data["pct"]
            price = data["price"]
            if pct >= 0:
                bg = f"rgba(0,{min(int(abs(pct) * 20 + 30), 140)},0,0.3)"
                txt = "#00ff96"
            else:
                bg = f"rgba({min(int(abs(pct) * 20 + 30), 140)},0,0,0.3)"
                txt = "#ff4444"
            arrow = "▲" if pct >= 0 else "▼"
            p_str = f"${price:,.0f}" if price >= 100 else f"${price:.2f}"
            cells.append(
                f'<div style="text-align:center;padding:6px 4px;background:{bg};border-radius:4px;'
                f'border-left:2px solid {txt};">'
                f'<div style="font-size:0.6rem;color:#ccc;font-weight:700;">{ticker}</div>'
                f'<div style="font-size:0.55rem;color:{COLORS["text_muted"]};">{label}</div>'
                f'<div style="font-size:0.9rem;font-weight:800;color:{txt};">{arrow}{abs(pct):.1f}%</div>'
                f'<div style="font-size:0.5rem;color:#777;">{p_str}</div>'
                f'</div>'
            )
        n_cols = min(len(cells), 11)
        st.markdown(
            f'<div style="display:grid;grid-template-columns:repeat({n_cols},1fr);gap:3px;">{"".join(cells)}</div>',
            unsafe_allow_html=True,
        )


# ═══════════════════════════════════════════════
# ROW 3: SIGNAL TRACK RECORD (collapsed)
# ═══════════════════════════════════════════════

with st.expander("Signal Track Record (30-day accuracy)"):
    with error_boundary("Track Record"):
        try:
            from src.prediction_tracker import get_track_record, get_all_sources
            sources = get_all_sources()
            if sources:
                _tr_cols = st.columns(min(len(sources), 4))
                for i, src_name in enumerate(sources[:8]):
                    tr = get_track_record(src_name, horizon=30)
                    with _tr_cols[i % min(len(sources), 4)]:
                        if tr["evaluated"] > 0 and tr["accuracy"] is not None:
                            acc = tr["accuracy"] * 100
                            acc_color = COLORS["success"] if acc >= 55 else (COLORS["danger"] if acc < 45 else COLORS["warning"])
                            st.markdown(
                                f'<div style="text-align:center;padding:4px;">'
                                f'<div style="font-size:0.7rem;color:{COLORS["text_muted"]};">{src_name.replace("_", " ").title()}</div>'
                                f'<div style="font-size:1.1rem;font-weight:700;color:{acc_color};">{acc:.0f}%</div>'
                                f'<div style="font-size:0.6rem;color:{COLORS["text_muted"]};">{tr["evaluated"]} evaluated</div>'
                                f'</div>', unsafe_allow_html=True,
                            )
                        elif tr["total_predictions"] > 0:
                            st.markdown(
                                f'<div style="text-align:center;padding:4px;">'
                                f'<div style="font-size:0.7rem;color:{COLORS["text_muted"]};">{src_name.replace("_", " ").title()}</div>'
                                f'<div style="font-size:0.8rem;color:{COLORS["text_muted"]};">{tr["total_predictions"]} pending</div>'
                                f'</div>', unsafe_allow_html=True,
                            )
                if st.button("Full Track Record →", key="goto_track", use_container_width=True):
                    st.switch_page("pages/47_Track_Record.py")
            else:
                st.caption("Predictions will appear after using analysis pages.")
        except Exception:
            st.caption("Track record loading...")


# ═══════════════════════════════════════════════
# ROW 4: FEATURE SHOWCASE (live data from each tool)
# ═══════════════════════════════════════════════

st.markdown("##### Platform Tools")

def _feature_card(icon, title, description, page, live_data=None):
    """Render a feature card with optional live data."""
    st.markdown(
        f'<div style="text-align:center;">'
        f'<div style="font-size:1.3rem;">{icon}</div>'
        f'<div style="font-weight:700;font-size:0.85rem;">{title}</div>'
        f'<div style="font-size:0.65rem;color:{COLORS["text_muted"]};min-height:28px;">{description}</div>'
        f'</div>', unsafe_allow_html=True,
    )
    if live_data:
        st.markdown(f'<div style="text-align:center;font-size:0.75rem;margin-top:4px;">{live_data}</div>', unsafe_allow_html=True)
    if st.button("Open", key=f"fc_{title.replace(' ', '_')}", use_container_width=True):
        st.switch_page(page)

fc1, fc2, fc3, fc4 = st.columns(4)
with fc1:
    with st.container(border=True):
        _feature_card("🧠", "Stock Analysis", "3-model AI consensus scoring", "pages/03_Stock_Analysis.py")
with fc2:
    with st.container(border=True):
        _feature_card("🌊", "Vol Surface", "3D IV surface, skew, Gemini trade ideas", "pages/43_Vol_Surface.py")
with fc3:
    with st.container(border=True):
        _feature_card("🎖️", "Iran Conflict", "3-model AI war analysis & OSINT", "pages/19_Iran_Conflict.py")
with fc4:
    with st.container(border=True):
        _feature_card("📡", "Signal Scanner", "Multi-factor cross-asset ranking", "pages/39_Signal_Scanner.py")

fc5, fc6, fc7, fc8 = st.columns(4)
with fc5:
    with st.container(border=True):
        _feature_card("🏦", "Fed & Macro", "FOMC diff, inflation, yield curve", "pages/21_Fed_Macro_Drivers.py")
with fc6:
    with st.container(border=True):
        _feature_card("🏗️", "Backtester", "Walk-forward algo strategy testing", "pages/11_Algo_Backtester.py")
with fc7:
    with st.container(border=True):
        _feature_card("⚖️", "Portfolio Optimizer", "HRP, Black-Litterman, risk parity", "pages/38_Portfolio_Optimizer.py")
with fc8:
    with st.container(border=True):
        _feature_card("🦾", "RL Trading", "DQN ensemble with walk-forward", "pages/04_RL_Trading.py")

fc9, fc10, fc11, fc12 = st.columns(4)
with fc9:
    with st.container(border=True):
        _feature_card("📊", "Correlation", "Cross-asset correlation & drawdown analysis", "pages/35_Correlation.py")
with fc10:
    with st.container(border=True):
        _feature_card("📅", "Economic Calendar", "FRED releases, earnings, Treasury auctions", "pages/18_Economic_Calendar.py")
with fc11:
    with st.container(border=True):
        _feature_card("🎯", "Track Record", "Prediction accuracy & accountability", "pages/47_Track_Record.py")
with fc12:
    with st.container(border=True):
        _feature_card("📈", "Calendar Spreads", "Term structure trades, roll optimization", "pages/42_Calendar_Spreads.py")


# ═══════════════════════════════════════════════
# ROW 5: WATCHLIST
# ═══════════════════════════════════════════════
with error_boundary("Watchlist"):
    if "watchlist" not in st.session_state:
        # Load saved watchlist from Supabase
        try:
            from src.user_prefs import load_pref
            saved_wl = load_pref("watchlist", {})
            st.session_state["watchlist"] = saved_wl if isinstance(saved_wl, dict) else {}
        except Exception:
            st.session_state["watchlist"] = {}
    watchlist = st.session_state["watchlist"]

    with st.expander(f"Watchlist ({len(watchlist)} tickers)"):
        if watchlist:
            wl_cols = st.columns(min(len(watchlist), 4))
            for i, (wl_t, wl_cfg) in enumerate(watchlist.items()):
                with wl_cols[i % min(len(watchlist), 4)]:
                    snap = polygon_batch_snapshot([wl_t]).get(wl_t)
                    if snap and snap.get("price"):
                        chg = snap.get("change", 0)
                        chg_color = COLORS["success"] if chg >= 0 else COLORS["danger"]
                        arrow = "▲" if chg >= 0 else "▼"
                        price = snap["price"]
                        p_str = f"${price:,.0f}" if price >= 100 else f"${price:.2f}"
                        st.markdown(
                            f'<div style="border:1px solid {COLORS["card_border"]};border-radius:6px;padding:8px;text-align:center;">'
                            f'<div style="font-weight:700;">{wl_t}</div>'
                            f'<div style="font-size:1.1rem;">{p_str}</div>'
                            f'<div style="color:{chg_color};font-size:0.8rem;">{arrow}{abs(chg):.2f}%</div>'
                            f'</div>', unsafe_allow_html=True,
                        )
                    else:
                        st.markdown(f'<div style="text-align:center;padding:8px;"><strong>{wl_t}</strong><br><span style="color:{COLORS["text_muted"]};">Loading...</span></div>', unsafe_allow_html=True)
                    if st.button("Remove", key=f"wl_rm_{wl_t}", use_container_width=True):
                        del st.session_state["watchlist"][wl_t]
                        try:
                            from src.user_prefs import save_pref
                            save_pref("watchlist", st.session_state["watchlist"])
                        except Exception:
                            pass
                        st.rerun()

        st.markdown("---")
        wl_c1, wl_c2, wl_c3, wl_c4 = st.columns([2, 2, 2, 1])
        with wl_c1:
            wl_new = st.text_input("Ticker", key="wl_add_t", placeholder="AAPL")
        with wl_c2:
            wl_above = st.number_input("Alert above $", key="wl_above", value=0.0, step=1.0, format="%.2f")
        with wl_c3:
            wl_below = st.number_input("Alert below $", key="wl_below", value=0.0, step=1.0, format="%.2f")
        with wl_c4:
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("Add", key="wl_add_btn", use_container_width=True):
                t = wl_new.strip().upper()
                if t:
                    st.session_state["watchlist"][t] = {
                        "above": wl_above if wl_above > 0 else None,
                        "below": wl_below if wl_below > 0 else None,
                        "move_pct": 5,
                    }
                    try:
                        from src.user_prefs import save_pref
                        save_pref("watchlist", st.session_state["watchlist"])
                    except Exception:
                        pass
                    st.rerun()


# ═══════════════════════════════════════════════
# ROW 6: ACCOUNT
# ═══════════════════════════════════════════════
with error_boundary("Account"):
    with st.expander("Account"):
        tier_colors = {"free": "#888", "pro": "#00d1ff", "premium": "#ffaa00", "platinum": "#00ff96"}
        t_color = tier_colors.get(tier, "#888")

        from src.auth import get_usage_summary, get_token_balance, render_token_purchase

        summary = get_usage_summary()
        tokens = get_token_balance()

        ac1, ac2, ac3, ac4 = st.columns(4)
        ac1.markdown(
            f'<div style="text-align:center;padding:8px;border:1px solid {t_color};border-radius:6px;">'
            f'<div style="font-size:0.7rem;color:{COLORS["text_muted"]};">Plan</div>'
            f'<div style="font-size:1.1rem;font-weight:700;color:{t_color};">{tier_config["name"]}</div></div>',
            unsafe_allow_html=True,
        )
        daily_str = f"{summary['daily_used']}/{summary['daily_limit']}" if summary['daily_limit'] > 0 else "0/0"
        ac2.markdown(
            f'<div style="text-align:center;padding:8px;border:1px solid {COLORS["card_border"]};border-radius:6px;">'
            f'<div style="font-size:0.7rem;color:{COLORS["text_muted"]};">AI Today</div>'
            f'<div style="font-size:1.1rem;font-weight:700;">{daily_str}</div></div>',
            unsafe_allow_html=True,
        )
        token_color = "#00ff96" if tokens > 10 else "#ffaa00" if tokens > 0 else "#888"
        ac3.markdown(
            f'<div style="text-align:center;padding:8px;border:1px solid {token_color};border-radius:6px;">'
            f'<div style="font-size:0.7rem;color:{COLORS["text_muted"]};">Tokens</div>'
            f'<div style="font-size:1.1rem;font-weight:700;color:{token_color};">{tokens}</div></div>',
            unsafe_allow_html=True,
        )
        ac4.markdown(
            f'<div style="text-align:center;padding:8px;border:1px solid {COLORS["card_border"]};border-radius:6px;">'
            f'<div style="font-size:0.7rem;color:{COLORS["text_muted"]};">AI Models</div>'
            f'<div style="font-size:0.8rem;font-weight:600;">{len(tier_config["ai_models"])} models</div></div>',
            unsafe_allow_html=True,
        )

        if summary["daily_limit"] > 0:
            usage_pct = min(summary["daily_used"] / summary["daily_limit"], 1.0)
            gauge_color = COLORS["success"] if usage_pct < 0.7 else COLORS["warning"] if usage_pct < 1.0 else COLORS["danger"]
            st.markdown(
                f'<div style="margin:8px 0;">'
                f'<div style="display:flex;justify-content:space-between;font-size:0.75rem;color:{COLORS["text_muted"]};">'
                f'<span>Daily AI Usage</span><span>{summary["daily_used"]}/{summary["daily_limit"]}</span></div>'
                f'<div style="background:{COLORS["card_border"]};border-radius:4px;height:8px;overflow:hidden;">'
                f'<div style="width:{usage_pct*100:.0f}%;height:100%;background:{gauge_color};border-radius:4px;"></div></div></div>',
                unsafe_allow_html=True,
            )

        auth_ts = st.session_state.get("_auth_timestamp")
        if auth_ts:
            session_min = (datetime.now() - auth_ts).total_seconds() / 60
            session_str = f"{session_min:.0f}m" if session_min < 60 else f"{session_min/60:.1f}h"
            st.caption(f"Session: {session_str} · {user_email}")

        with st.expander("Buy Analysis Tokens"):
            render_token_purchase()

        from src.auth import render_pricing_cards, STRIPE_LINKS
        with st.expander("Upgrade Plan"):
            render_pricing_cards(current_tier=tier)

        portal_link = STRIPE_LINKS.get("portal", "#")
        if tier != "free":
            st.link_button("Manage Subscription", portal_link, use_container_width=True)

        pw_c, lo_c = st.columns(2)
        with pw_c:
            with st.expander("Change Password"):
                with st.form("pw_form"):
                    new_pw = st.text_input("New Password", type="password")
                    if st.form_submit_button("Update"):
                        if supabase:
                            try:
                                refresh_token = st.context.cookies.get("sb_refresh")
                                if refresh_token:
                                    supabase.auth.refresh_session(refresh_token)
                                    supabase.auth.update_user({"password": new_pw})
                                    st.success("Password updated.")
                                else:
                                    st.error("Session expired. Log out and back in.")
                            except Exception as e:
                                st.error(f"Failed: {e}")
        with lo_c:
            if st.button("Log Out", type="secondary", use_container_width=True):
                from src.auth import clear_auth_cookie
                clear_auth_cookie()
                for key in list(st.session_state.keys()):
                    del st.session_state[key]
                st.switch_page("app.py")

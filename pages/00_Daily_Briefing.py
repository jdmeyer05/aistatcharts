"""Daily Briefing — morning note for traders. Change-focused, time-aware, scannable in 60 seconds."""

import streamlit as st
import pandas as pd
import html as _html
import os
import json
import logging
from datetime import datetime, date, timedelta

from src.layout import setup_page, error_boundary
from src.styles import COLORS
from src.data_engine import polygon_batch_snapshot
from src.signal_engine import get_signal_summary, get_top_trade_ideas
from src.metrics_store import get_latest_snapshot, percentile_ranks_all
from src.position_book import get_portfolio_summary, get_portfolio_greeks
from src.prediction_tracker import get_track_record, get_all_sources
from src.economic_calendar import get_upcoming_fomc, is_fomc_week, find_events_near_date

logger = logging.getLogger(__name__)

setup_page("00_Daily_Briefing")

# ═══════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def _market_status():
    """Return market phase and display label."""
    from datetime import timezone
    now_utc = datetime.now(timezone.utc)
    et_offset = timedelta(hours=-4)  # EDT approximation
    now_et = now_utc + et_offset
    h, m = now_et.hour, now_et.minute
    wd = now_et.weekday()
    if wd >= 5:
        return "closed", "Weekend"
    if h < 4:
        return "closed", "Overnight"
    if h < 9 or (h == 9 and m < 30):
        return "premarket", "Pre-Market"
    if h < 16:
        return "open", "Market Open"
    if h < 20:
        return "afterhours", "After Hours"
    return "closed", "Closed"


def _load_conflict_data():
    """Load latest conflict analysis from JSON history."""
    path = os.path.join(_project_root, "src", "iran_conflict_history.json")
    try:
        if os.path.exists(path):
            with open(path, "r") as f:
                data = json.load(f)
            if data:
                return data[-1]
    except Exception:
        pass
    return None


def _load_grok_regime():
    """Load latest Grok regime analysis from JSON history."""
    path = os.path.join(_project_root, "src", "grok_regime_history.json")
    try:
        if os.path.exists(path):
            with open(path, "r") as f:
                data = json.load(f)
            if data:
                return data[-1]
    except Exception:
        pass
    return None


def _chg_arrow(val):
    """Color-coded arrow for a % change value."""
    if val is None or pd.isna(val):
        return f'<span style="color:{COLORS["text_muted"]};">—</span>'
    color = COLORS["success"] if val > 0 else COLORS["danger"] if val < 0 else COLORS["text_muted"]
    arrow = "▲" if val > 0 else "▼" if val < 0 else "—"
    return f'<span style="color:{color};font-weight:600;">{arrow} {val:+.2f}%</span>'


def _section_header(title, icon=""):
    """Consistent section header."""
    st.markdown(
        f'<div style="font-size:0.72rem;color:{COLORS["text_muted"]};text-transform:uppercase;'
        f'letter-spacing:1px;margin:20px 0 8px 0;border-bottom:1px solid {COLORS["card_border"]};'
        f'padding-bottom:4px;">{icon} {title}</div>',
        unsafe_allow_html=True,
    )


# ═══════════════════════════════════════════════
# HEADER: DATE + MARKET STATUS + COUNTDOWN
# ═══════════════════════════════════════════════

phase, phase_label = _market_status()
today = date.today()

# Next FOMC countdown
next_fomc = get_upcoming_fomc(1)
fomc_str = ""
if next_fomc:
    fomc_dt = pd.to_datetime(next_fomc[0]).date()
    days_to_fomc = (fomc_dt - today).days
    if days_to_fomc <= 0:
        fomc_str = "FOMC TODAY"
    elif days_to_fomc <= 7:
        fomc_str = f"FOMC in {days_to_fomc}d"
    else:
        fomc_str = f"FOMC {fomc_dt.strftime('%b %d')}"

fomc_warning = is_fomc_week()

# Phase colors
phase_colors = {
    "premarket": "#ffaa00", "open": "#00ff96",
    "afterhours": "#00d1ff", "closed": "#888888",
}
pc = phase_colors.get(phase, "#888888")

st.markdown(
    f'<div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:4px;">'
    f'<div style="font-size:1.4rem;font-weight:800;color:{COLORS["accent"]};">Daily Briefing</div>'
    f'<div style="display:flex;gap:16px;align-items:center;">'
    f'<span style="font-size:0.8rem;color:{COLORS["text_muted"]};">{today.strftime("%A, %B %d, %Y")}</span>'
    f'<span style="font-size:0.75rem;font-weight:600;color:{pc};background:rgba({",".join(str(int(pc.lstrip("#")[i:i+2], 16)) for i in (0,2,4))},0.12);'
    f'padding:2px 8px;border-radius:4px;">{phase_label}</span>'
    + (f'<span style="font-size:0.75rem;font-weight:600;color:{"#ff4444" if fomc_warning else COLORS["text_muted"]};">{fomc_str}</span>' if fomc_str else '')
    + f'</div></div>',
    unsafe_allow_html=True,
)


# ═══════════════════════════════════════════════
# SECTION 1: MARKET SNAPSHOT (overnight moves)
# ═══════════════════════════════════════════════

_section_header("Market Snapshot", "")

# Module-level defaults so later sections can safely reference these
spy_metrics = None
spy_pctiles = None

with error_boundary("Market Snapshot"):
    @st.cache_data(ttl=300)
    def _fetch_briefing_snapshot():
        tickers = ["SPY", "QQQ", "IWM", "I:VIX", "GLD", "USO", "TLT", "UUP",
                    "ES=F", "NQ=F", "CL=F"]
        return polygon_batch_snapshot(tickers)

    snap = _fetch_briefing_snapshot()

    # Key moves row
    key_assets = [
        ("S&P 500", "SPY"), ("Nasdaq", "QQQ"), ("Russell", "IWM"), ("VIX", "I:VIX"),
        ("Gold", "GLD"), ("Oil", "USO"), ("Bonds", "TLT"), ("Dollar", "UUP"),
    ]

    cells = []
    for label, tk in key_assets:
        info = snap.get(tk, {})
        price = info.get("price")
        chg = info.get("change_pct")
        if price is None:
            cells.append(
                f'<div style="text-align:center;min-width:80px;">'
                f'<div style="font-size:0.65rem;color:{COLORS["text_muted"]};">{label}</div>'
                f'<div style="font-size:0.78rem;color:{COLORS["text_muted"]};">—</div></div>'
            )
            continue
        color = COLORS["success"] if chg and chg > 0 else COLORS["danger"] if chg and chg < 0 else COLORS["text_muted"]
        # VIX: higher = bad
        if tk == "I:VIX":
            color = COLORS["danger"] if chg and chg > 0 else COLORS["success"] if chg and chg < 0 else COLORS["text_muted"]
        fmt_price = f"${price:,.2f}" if price < 1000 else f"${price:,.0f}"
        if tk == "I:VIX":
            fmt_price = f"{price:.2f}"
        chg_str = f"{chg:+.2f}%" if chg is not None else "—"
        cells.append(
            f'<div style="text-align:center;min-width:80px;">'
            f'<div style="font-size:0.65rem;color:{COLORS["text_muted"]};">{label}</div>'
            f'<div style="font-size:0.88rem;font-weight:700;color:{COLORS.get("text", "#e0e0e0")};">{fmt_price}</div>'
            f'<div style="font-size:0.78rem;font-weight:600;color:{color};">{chg_str}</div></div>'
        )

    st.markdown(
        f'<div style="display:flex;justify-content:space-between;flex-wrap:wrap;gap:6px;'
        f'background:{COLORS["card_bg"]};border:1px solid {COLORS["card_border"]};'
        f'border-radius:8px;padding:12px 16px;">{"".join(cells)}</div>',
        unsafe_allow_html=True,
    )

    # Vol regime quick read
    spy_metrics = get_latest_snapshot("SPY")
    spy_pctiles = percentile_ranks_all("SPY")
    if spy_metrics:
        iv = spy_metrics.get("atm_iv")
        vrp = spy_metrics.get("vrp")
        iv_pctile = spy_pctiles.get("atm_iv") if spy_pctiles else None
        vrp_pctile = spy_pctiles.get("vrp") if spy_pctiles else None

        vol_parts = []
        if iv is not None:
            level = "High" if iv > 0.25 else "Low" if iv < 0.15 else "Normal"
            lc = COLORS["danger"] if level == "High" else COLORS["success"] if level == "Low" else COLORS["text_muted"]
            pctile_str = f" ({iv_pctile:.0f}th %ile)" if iv_pctile is not None else ""
            vol_parts.append(f'IV: <span style="color:{lc};font-weight:600;">{iv:.1%} {level}</span>{pctile_str}')
        if vrp is not None:
            vc = COLORS["success"] if vrp > 0.03 else COLORS["danger"] if vrp < -0.02 else COLORS["text_muted"]
            vol_parts.append(f'VRP: <span style="color:{vc};font-weight:600;">{vrp:+.1%}</span>')

        if vol_parts:
            st.markdown(
                f'<div style="font-size:0.78rem;color:{COLORS["text_muted"]};margin-top:6px;">'
                f'SPY Vol Regime: {" · ".join(vol_parts)}</div>',
                unsafe_allow_html=True,
            )


# ═══════════════════════════════════════════════
# SECTION 2: POSITIONS NEEDING ATTENTION
# ═══════════════════════════════════════════════

with error_boundary("Positions"):
    portfolio = get_portfolio_summary()
    n_pos = portfolio.get("n_positions", 0)
    alerts = portfolio.get("alerts", [])
    positions = portfolio.get("positions", [])

    if n_pos > 0:
        _section_header("Position Book", "")

        # Summary metrics
        total_pnl = portfolio.get("total_pnl", 0)
        pnl_color = COLORS["success"] if total_pnl > 0 else COLORS["danger"] if total_pnl < 0 else COLORS["text_muted"]

        greeks = get_portfolio_greeks()
        net_delta = greeks.get("net_delta", 0) if greeks else 0

        mc = st.columns(4)
        mc[0].metric("Positions", n_pos)
        mc[1].metric("Total P&L", f"${total_pnl:+,.0f}")
        mc[2].metric("Net Delta $", f"${net_delta:+,.0f}")
        mc[3].metric("Alerts", len(alerts) if alerts else 0)

        # Show alerts first (most urgent)
        if alerts:
            for alert in alerts[:3]:
                alert_str = _html.escape(str(alert))
                a_color = COLORS["danger"] if "breach" in alert_str.lower() else COLORS["warning"]
                st.markdown(
                    f'<div style="font-size:0.78rem;color:{a_color};padding:4px 10px;'
                    f'background:rgba({",".join(str(int(a_color.lstrip("#")[i:i+2], 16)) for i in (0,2,4))},0.08);'
                    f'border-left:3px solid {a_color};border-radius:4px;margin:3px 0;">{alert_str}</div>',
                    unsafe_allow_html=True,
                )

        # Top movers (sorted by absolute P&L)
        sorted_pos = sorted(positions, key=lambda p: abs(p.get("pnl", 0)), reverse=True)
        if sorted_pos:
            rows_html = ""
            for p in sorted_pos[:5]:
                tk = p.get("ticker", "?")
                pnl = p.get("pnl", 0)
                pnl_pct = p.get("pnl_pct", 0)
                qty = p.get("qty", 0)
                ptype = p.get("type", "stock")
                pnl_c = COLORS["success"] if pnl > 0 else COLORS["danger"] if pnl < 0 else COLORS["text_muted"]
                rows_html += (
                    f'<div style="display:flex;justify-content:space-between;align-items:center;'
                    f'padding:4px 0;border-bottom:1px solid {COLORS["card_border"]};">'
                    f'<span style="font-size:0.82rem;font-weight:600;">{tk}</span>'
                    f'<span style="font-size:0.72rem;color:{COLORS["text_muted"]};">{qty} {ptype}</span>'
                    f'<span style="font-size:0.82rem;font-weight:600;color:{pnl_c};">${pnl:+,.0f} ({pnl_pct:+.1f}%)</span>'
                    f'</div>'
                )
            st.markdown(
                f'<div style="background:{COLORS["card_bg"]};border:1px solid {COLORS["card_border"]};'
                f'border-radius:8px;padding:10px 14px;margin-top:6px;">{rows_html}</div>',
                unsafe_allow_html=True,
            )


# ═══════════════════════════════════════════════
# SECTION 3: SIGNAL SPOTLIGHT
# ═══════════════════════════════════════════════

_section_header("Signal Spotlight", "")

with error_boundary("Signal Spotlight"):
    summary = get_signal_summary()
    top_ideas = get_top_trade_ideas(n=5)

    sc1, sc2 = st.columns([1, 2])

    with sc1:
        n_bull = summary.get("n_bullish", 0)
        n_bear = summary.get("n_bearish", 0)
        n_tickers = summary.get("n_tickers", 0)
        avg_conv = summary.get("avg_conviction", 0)

        st.markdown(
            f'<div style="background:{COLORS["card_bg"]};border:1px solid {COLORS["card_border"]};'
            f'border-radius:8px;padding:12px 16px;">'
            f'<div style="font-size:0.72rem;color:{COLORS["text_muted"]};margin-bottom:6px;">SIGNAL CONSENSUS</div>'
            f'<div style="display:flex;gap:16px;">'
            f'<div><div style="font-size:1.4rem;font-weight:800;color:{COLORS["success"]};">{n_bull}</div>'
            f'<div style="font-size:0.65rem;color:{COLORS["text_muted"]};">Bullish</div></div>'
            f'<div><div style="font-size:1.4rem;font-weight:800;color:{COLORS["danger"]};">{n_bear}</div>'
            f'<div style="font-size:0.65rem;color:{COLORS["text_muted"]};">Bearish</div></div>'
            f'<div><div style="font-size:1.4rem;font-weight:800;color:{COLORS["accent"]};">{avg_conv:.0%}</div>'
            f'<div style="font-size:0.65rem;color:{COLORS["text_muted"]};">Avg Conviction</div></div>'
            f'</div></div>',
            unsafe_allow_html=True,
        )

    with sc2:
        if top_ideas:
            idea_rows = ""
            for idea in top_ideas[:5]:
                tk = idea.get("ticker", "?")
                direction = idea.get("overall_direction", "neutral")
                conv = idea.get("overall_conviction", 0)
                n_sig = idea.get("n_signals", 0)
                agree = idea.get("signal_agreement", 0)

                dir_color = COLORS["success"] if direction == "bull" else COLORS["danger"] if direction == "bear" else COLORS["text_muted"]
                dir_label = "BUY" if direction == "bull" else "SELL" if direction == "bear" else "HOLD"

                # Conviction bar
                bar_w = min(conv * 100, 100)
                idea_rows += (
                    f'<div style="display:flex;align-items:center;gap:8px;padding:3px 0;'
                    f'border-bottom:1px solid {COLORS["card_border"]};">'
                    f'<span style="flex:0 0 50px;font-size:0.82rem;font-weight:700;">{tk}</span>'
                    f'<span style="flex:0 0 32px;font-size:0.68rem;font-weight:700;color:{dir_color};'
                    f'background:rgba({",".join(str(int(dir_color.lstrip("#")[i:i+2], 16)) for i in (0,2,4))},0.12);'
                    f'padding:1px 4px;border-radius:3px;text-align:center;">{dir_label}</span>'
                    f'<div style="flex:1;background:{COLORS["card_border"]};border-radius:3px;height:5px;">'
                    f'<div style="width:{bar_w:.0f}%;height:100%;background:{dir_color};border-radius:3px;"></div></div>'
                    f'<span style="flex:0 0 36px;font-size:0.72rem;color:{COLORS["text_muted"]};text-align:right;">{conv:.0%}</span>'
                    f'<span style="flex:0 0 20px;font-size:0.65rem;color:{COLORS["text_muted"]};">{n_sig}s</span>'
                    f'</div>'
                )
            st.markdown(
                f'<div style="background:{COLORS["card_bg"]};border:1px solid {COLORS["card_border"]};'
                f'border-radius:8px;padding:10px 14px;">'
                f'<div style="font-size:0.72rem;color:{COLORS["text_muted"]};margin-bottom:6px;">TOP TRADE IDEAS</div>'
                f'{idea_rows}</div>',
                unsafe_allow_html=True,
            )
        else:
            st.caption("No active signals. Run analysis pages to generate signals.")


# ═══════════════════════════════════════════════
# SECTION 4: RISK RADAR
# ═══════════════════════════════════════════════

_section_header("Risk Radar", "")

rc1, rc2, rc3 = st.columns(3)

with rc1:
    with st.container(border=True):
        with error_boundary("Conflict Risk"):
            st.markdown(f'<div style="font-size:0.68rem;color:{COLORS["text_muted"]};text-transform:uppercase;">Iran Conflict</div>', unsafe_allow_html=True)
            conflict_data = _load_conflict_data()
            if conflict_data and conflict_data.get("blended"):
                blended = conflict_data["blended"]
                esc = blended.get("escalation_risk", {})
                score = esc.get("score", 0)
                level = esc.get("level", "Unknown")
                esc_color = "#ff4444" if score >= 8 else "#ff6b35" if score >= 6 else "#ffaa00" if score >= 4 else "#00ff96"
                days = (datetime.now() - datetime(2026, 2, 28)).days

                st.markdown(
                    f'<div style="font-size:1.6rem;font-weight:800;color:{esc_color};">{score}/10</div>'
                    f'<div style="font-size:0.75rem;color:{COLORS["text_muted"]};">{level} · Day {days}</div>',
                    unsafe_allow_html=True,
                )
                oil = blended.get("oil_impact", {})
                if oil.get("price_range"):
                    st.markdown(f'<div style="font-size:0.72rem;">Oil: **{oil["price_range"]}**</div>')
                situation = _html.escape(blended.get("situation_summary", ""))
                if situation:
                    st.caption(situation[:150] + ("..." if len(situation) > 150 else ""))
            else:
                st.caption("No conflict data available.")

with rc2:
    with st.container(border=True):
        with error_boundary("Macro Regime"):
            st.markdown(f'<div style="font-size:0.68rem;color:{COLORS["text_muted"]};text-transform:uppercase;">Macro Regime</div>', unsafe_allow_html=True)
            grok_data = _load_grok_regime()
            if grok_data and grok_data.get("regimes"):
                regimes = sorted(grok_data["regimes"], key=lambda r: r.get("probability", 0), reverse=True)
                top = regimes[0]
                regime_colors = {
                    "Stagflation": "#ff4444", "Recession": "#ff8c00", "Soft Landing": "#00cc66",
                    "Financial Crisis": "#ff0066", "Re-Acceleration": "#00d1ff", "Goldilocks": "#aa66ff",
                }
                name = top.get("name", "Unknown")
                prob = top.get("probability", 0)
                rc_color = regime_colors.get(name, "#888")

                st.markdown(
                    f'<div style="font-size:1.1rem;font-weight:800;color:{rc_color};">{name}</div>'
                    f'<div style="font-size:0.75rem;color:{COLORS["text_muted"]};">{prob}% probability</div>',
                    unsafe_allow_html=True,
                )
                # Show top 3 regimes as mini bars
                for r in regimes[1:4]:
                    rn = r.get("name", "")
                    rcolor = regime_colors.get(rn, "#888")
                    w = r.get("probability", 0)
                    st.markdown(
                        f'<div style="display:flex;align-items:center;gap:4px;margin:2px 0;">'
                        f'<div style="flex:0 0 70px;font-size:0.65rem;color:{COLORS["text_muted"]};">{rn}</div>'
                        f'<div style="flex:1;background:{COLORS["card_border"]};border-radius:3px;height:5px;">'
                        f'<div style="width:{w}%;height:100%;background:{rcolor};border-radius:3px;"></div></div>'
                        f'<div style="flex:0 0 24px;font-size:0.65rem;color:{rcolor};text-align:right;">{w}%</div>'
                        f'</div>', unsafe_allow_html=True,
                    )
            else:
                st.caption("Run Scenario Analysis to generate regime data.")

with rc3:
    with st.container(border=True):
        with error_boundary("Vol Snapshot"):
            st.markdown(f'<div style="font-size:0.68rem;color:{COLORS["text_muted"]};text-transform:uppercase;">Vol Snapshot</div>', unsafe_allow_html=True)
            # Reuse metrics from section 1 if available, otherwise reload
            _spy = spy_metrics if spy_metrics else get_latest_snapshot("SPY")
            _pctiles = spy_pctiles if spy_pctiles else percentile_ranks_all("SPY")

            if _spy:
                metrics_display = [
                    ("ATM IV", _spy.get("atm_iv"), _pctiles.get("atm_iv") if _pctiles else None, True),
                    ("HV20", _spy.get("hv20"), _pctiles.get("hv20") if _pctiles else None, True),
                    ("Put Skew", _spy.get("put_skew"), _pctiles.get("put_skew") if _pctiles else None, False),
                    ("P/C Ratio", _spy.get("pc_ratio"), None, False),
                ]
                for label, val, pctile, as_pct in metrics_display:
                    if val is None:
                        continue
                    val_str = f"{val:.1%}" if as_pct else f"{val:.2f}"
                    pctile_str = ""
                    if pctile is not None:
                        pc_color = COLORS["danger"] if pctile > 80 else COLORS["warning"] if pctile > 60 else COLORS["success"] if pctile < 20 else COLORS["text_muted"]
                        pctile_str = f' <span style="color:{pc_color};font-size:0.65rem;">({pctile:.0f}th)</span>'
                    st.markdown(
                        f'<div style="display:flex;justify-content:space-between;font-size:0.78rem;padding:2px 0;">'
                        f'<span style="color:{COLORS["text_muted"]};">{label}</span>'
                        f'<span style="font-weight:600;">{val_str}{pctile_str}</span></div>',
                        unsafe_allow_html=True,
                    )
            else:
                st.caption("No vol metrics available. Visit Vol Surface to generate.")


# ═══════════════════════════════════════════════
# SECTION 5: CALENDAR — WHAT'S COMING
# ═══════════════════════════════════════════════

_section_header("Upcoming Events", "")

with error_boundary("Calendar"):
    events = find_events_near_date(today.strftime("%Y-%m-%d"), window_days=14)

    # FOMC dates
    fomc_dates = get_upcoming_fomc(3)

    if events or fomc_dates:
        ev_items = []
        # Add macro events from calendar
        for ev in events:
            days = ev.get("days_away", 0)
            if days < 0:
                continue  # skip past events
            urgency = COLORS["danger"] if days == 0 else COLORS["warning"] if days <= 2 else COLORS["text_muted"]
            when = "TODAY" if days == 0 else f"in {days}d"
            ev_items.append((days, ev["name"], when, urgency, ev.get("date", "")))

        # Add FOMC dates not already in events
        event_dates = {e.get("date", "") for e in events}
        for fd in fomc_dates:
            if fd not in event_dates:
                fd_dt = pd.to_datetime(fd).date()
                days = (fd_dt - today).days
                if days > 0:
                    urgency = COLORS["warning"] if days <= 7 else COLORS["text_muted"]
                    ev_items.append((days, "FOMC Meeting", f"in {days}d", urgency, fd))

        ev_items.sort(key=lambda x: x[0])

        ev_html = ""
        for _, name, when, color, dt_str in ev_items[:6]:
            ev_html += (
                f'<div style="display:flex;justify-content:space-between;padding:4px 0;'
                f'border-bottom:1px solid {COLORS["card_border"]};">'
                f'<span style="font-size:0.8rem;">{name}</span>'
                f'<div style="display:flex;gap:10px;">'
                f'<span style="font-size:0.72rem;color:{COLORS["text_muted"]};">{dt_str}</span>'
                f'<span style="font-size:0.72rem;font-weight:600;color:{color};">{when}</span>'
                f'</div></div>'
            )

        st.markdown(
            f'<div style="background:{COLORS["card_bg"]};border:1px solid {COLORS["card_border"]};'
            f'border-radius:8px;padding:10px 14px;">{ev_html}</div>',
            unsafe_allow_html=True,
        )
    else:
        st.caption("No major events in the next 14 days.")


# ═══════════════════════════════════════════════
# SECTION 6: TRACK RECORD — ARE WE ANY GOOD?
# ═══════════════════════════════════════════════

_section_header("Signal Track Record", "")

with error_boundary("Track Record"):
    sources = get_all_sources()
    if sources:
        records = []
        for source in sources:
            try:
                rec = get_track_record(source=source, horizon=30)
                if rec and rec.get("with_direction", 0) > 0:
                    records.append({
                        "source": source.replace("_", " ").title(),
                        "accuracy": rec["accuracy"],
                        "n": rec["with_direction"],
                        "correct": rec["correct"],
                    })
            except Exception:
                pass

        if records:
            records.sort(key=lambda r: r["accuracy"], reverse=True)
            tr_html = ""
            for r in records[:6]:
                acc = r["accuracy"]
                acc_color = COLORS["success"] if acc > 0.55 else COLORS["danger"] if acc < 0.45 else COLORS["warning"]
                bar_w = acc * 100
                tr_html += (
                    f'<div style="display:flex;align-items:center;gap:8px;padding:3px 0;'
                    f'border-bottom:1px solid {COLORS["card_border"]};">'
                    f'<span style="flex:0 0 110px;font-size:0.75rem;color:{COLORS["text_muted"]};">{r["source"]}</span>'
                    f'<div style="flex:1;background:{COLORS["card_border"]};border-radius:3px;height:5px;">'
                    f'<div style="width:{bar_w:.0f}%;height:100%;background:{acc_color};border-radius:3px;"></div></div>'
                    f'<span style="flex:0 0 44px;font-size:0.78rem;font-weight:600;color:{acc_color};text-align:right;">{acc:.0%}</span>'
                    f'<span style="flex:0 0 30px;font-size:0.65rem;color:{COLORS["text_muted"]};">{r["correct"]}/{r["n"]}</span>'
                    f'</div>'
                )
            st.markdown(
                f'<div style="background:{COLORS["card_bg"]};border:1px solid {COLORS["card_border"]};'
                f'border-radius:8px;padding:10px 14px;">'
                f'<div style="font-size:0.72rem;color:{COLORS["text_muted"]};margin-bottom:6px;">30-DAY PREDICTION ACCURACY</div>'
                f'{tr_html}</div>',
                unsafe_allow_html=True,
            )
        else:
            st.caption("No evaluated predictions yet. Predictions are evaluated at T+30 days.")
    else:
        st.caption("No predictions recorded yet. Run analysis pages to start tracking.")


# ═══════════════════════════════════════════════
# SECTION 7: QUICK NAVIGATION
# ═══════════════════════════════════════════════

st.markdown("")
nc = st.columns(5)
nav_items = [
    ("Stock Analysis", "pages/03_Stock_Analysis.py"),
    ("Vol Surface", "pages/43_Vol_Surface.py"),
    ("Signal Scanner", "pages/39_Signal_Scanner.py"),
    ("Iran Conflict", "pages/19_Iran_Conflict.py"),
    ("Portfolio Greeks", "pages/44_Portfolio_Greeks.py"),
]
for i, (label, page) in enumerate(nav_items):
    if nc[i].button(label, key=f"nav_{i}", use_container_width=True):
        st.switch_page(page)

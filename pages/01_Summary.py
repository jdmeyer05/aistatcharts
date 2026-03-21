import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from src.data_engine import polygon_snapshot, polygon_history, polygon_intraday
from plotly.subplots import make_subplots
import json
import os
import logging
from datetime import datetime, date
from src.auth import init_supabase, get_user_tier, get_tier_config, TIERS
from src.layout import setup_page, error_boundary, render_skeleton_cards, fun_loader
from src.styles import COLORS

setup_page("01_Summary")
logger = logging.getLogger(__name__)

supabase = init_supabase()
user_email = st.session_state.get("user_email", "User")
tier = get_user_tier()
tier_config = get_tier_config(tier)

# ── Post-checkout confirmation ──
query_params = st.query_params
if query_params.get("checkout") == "success":
    plan = query_params.get("plan", "")
    if plan:
        st.success(f"Welcome to **{plan.title()}**! Your subscription is now active. Refresh the page to unlock your new features.")
    else:
        st.success("Payment successful! Your account has been updated.")
    st.query_params.clear()
elif query_params.get("checkout") == "tokens":
    st.success("Tokens purchased! Your balance has been updated.")
    st.query_params.clear()

from src.auth import check_payment_failures
check_payment_failures()

# ── History file paths ──
GROK_HISTORY = os.path.join(os.path.dirname(os.path.dirname(__file__)), "src", "grok_regime_history.json")
CONFLICT_HISTORY = os.path.join(os.path.dirname(os.path.dirname(__file__)), "src", "iran_conflict_history.json")


# ═══════════════════════════════════════════════
# DATA FETCHING
# ═══════════════════════════════════════════════
@st.cache_data(ttl=120, show_spinner=False)
def fetch_market_data(ticker: str) -> dict:
    try:
        # Get daily history for context
        daily = polygon_history(ticker, 30)
        if daily.empty or len(daily) < 2:
            return None
        prev_close = float(daily["Close"].iloc[-2])
        last = float(daily["Close"].iloc[-1])

        # Try intraday (5-min bars) for mini-chart, fallback to hourly, then daily
        intra = polygon_intraday(ticker, interval_min=5, bars=80)
        if intra.empty or len(intra) < 3:
            intra = polygon_intraday(ticker, interval_min=60, bars=24)
        if not intra.empty and len(intra) >= 3:
            chart_close = intra["Close"]
            chart_dates = intra.index
            last = float(chart_close.iloc[-1])
        else:
            chart_close = daily["Close"].tail(20)
            chart_dates = daily.index[-20:]
        day_chg = (last / prev_close - 1) * 100 if prev_close > 0 else 0
        d_close = daily["Close"]
        week_chg = (last / float(d_close.iloc[-min(6, len(d_close))]) - 1) * 100 if len(d_close) > 1 else 0
        month_chg = (last / float(d_close.iloc[0]) - 1) * 100
        return {
            "price": last, "day_chg": day_chg, "week_chg": week_chg,
            "month_chg": month_chg, "prev_close": prev_close,
            "close": chart_close, "dates": chart_dates,
        }
    except Exception:
        return None


MARKET_TICKERS = [
    ("SPY", "S&P 500"), ("QQQ", "Nasdaq 100"), ("DIA", "Dow Jones"), ("IWM", "Russell 2000"),
    ("CL=F", "Crude Oil"), ("NG=F", "Natural Gas"), ("GC=F", "Gold"), ("BTC-USD", "Bitcoin"),
    ("^TNX", "10Y Yield"), ("^TYX", "30Y Yield"), ("DX-Y.NYB", "Dollar Index"), ("^VIX", "VIX"),
]


def _fmt_price(ticker, price):
    if ticker in ("^TNX", "^TYX", "^VIX", "DX-Y.NYB"):
        return f"{price:.2f}"
    elif ticker == "BTC-USD":
        return f"${price:,.0f}"
    elif ticker == "GC=F":
        return f"${price:,.0f}"
    else:
        return f"${price:.2f}"


def _mini_chart(data, height=70):
    """Render a tiny area chart for a market card."""
    above = data["day_chg"] >= 0
    line_color = "#00ff96" if above else "#ff4444"
    fill_color = "rgba(0,255,150,0.15)" if above else "rgba(255,68,68,0.15)"
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=data["dates"], y=data["close"], mode="lines",
        line=dict(color=line_color, width=1.5),
        fill="tozeroy", fillcolor=fill_color,
        hovertemplate="%{y:.2f}<extra></extra>", showlegend=False,
    ))
    y_min, y_max = float(data["close"].min()), float(data["close"].max())
    y_pad = (y_max - y_min) * 0.1 if y_max > y_min else 1
    fig.update_layout(
        template="plotly_dark", height=height,
        margin=dict(l=0, r=0, t=0, b=0),
        xaxis=dict(visible=False), yaxis=dict(visible=False, range=[y_min - y_pad, y_max + y_pad]),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", showlegend=False,
    )
    return fig


# ═══════════════════════════════════════════════
# PREFETCH ALL DATA
# ═══════════════════════════════════════════════
with error_boundary("Data Prefetch"):
    _prefetch_slot = st.empty()
    _prefetch_slot.markdown(
        '<div class="skeleton skeleton-card" style="text-align:center;padding:20px;">'
        '<div class="skeleton-line title" style="margin:0 auto;"></div>'
        '<div style="color:#888;font-size:0.8rem;margin-top:10px;">Loading market data...</div>'
        '</div>', unsafe_allow_html=True,
    )
    _market_cache = {}
    for _t, _ in MARKET_TICKERS:
        _market_cache[_t] = fetch_market_data(_t)
    _prefetch_slot.empty()

    # Load AI history
    grok_data = None
    try:
        if os.path.exists(GROK_HISTORY):
            with open(GROK_HISTORY, "r") as f:
                grok_all = json.load(f)
            if grok_all:
                grok_data = grok_all[-1]
    except Exception:
        pass

    conflict_data = None
    try:
        if os.path.exists(CONFLICT_HISTORY):
            with open(CONFLICT_HISTORY, "r") as f:
                conflict_all = json.load(f)
            if conflict_all:
                conflict_data = conflict_all[-1]
    except Exception:
        pass


# ═══════════════════════════════════════════════
# ROW 1: DAILY BRIEFING
# ═══════════════════════════════════════════════
with error_boundary("Daily Briefing"):
    spy_d = _market_cache.get("SPY")
    qqq_d = _market_cache.get("QQQ")
    oil_d = _market_cache.get("CL=F")
    vix_d = _market_cache.get("^VIX")
    btc_d = _market_cache.get("BTC-USD")
    gold_d = _market_cache.get("GC=F")

    def _stat_card(label, value, sub, color="#e0e0e0", border_color=None):
        bc = border_color or COLORS["card_border"]
        return (
            f'<div style="flex:1 1 100px;min-width:90px;text-align:center;padding:8px 6px;'
            f'border:1px solid {bc};border-radius:6px;background:rgba(255,255,255,0.02);">'
            f'<div style="font-size:0.65rem;color:{COLORS["text_muted"]};text-transform:uppercase;letter-spacing:0.5px;">{label}</div>'
            f'<div style="font-size:1.1rem;font-weight:700;color:{color};margin:2px 0;">{value}</div>'
            f'<div style="font-size:0.7rem;color:{COLORS["text_muted"]};">{sub}</div>'
            f'</div>'
        )

    cards = []
    if spy_d:
        c = COLORS["success"] if spy_d["day_chg"] >= 0 else COLORS["danger"]
        arrow = "▲" if spy_d["day_chg"] >= 0 else "▼"
        cards.append(_stat_card("S&amp;P 500", f'{spy_d["price"]:,.0f}', f'{arrow} {abs(spy_d["day_chg"]):.1f}%', c))
    if qqq_d:
        c = COLORS["success"] if qqq_d["day_chg"] >= 0 else COLORS["danger"]
        arrow = "▲" if qqq_d["day_chg"] >= 0 else "▼"
        cards.append(_stat_card("Nasdaq", f'{qqq_d["price"]:.2f}', f'{arrow} {abs(qqq_d["day_chg"]):.1f}%', c))
    if vix_d:
        vp = vix_d["price"]
        vc = COLORS["danger"] if vp > 25 else COLORS["warning"] if vp > 18 else COLORS["success"]
        vl = "extreme" if vp > 30 else "elevated" if vp > 20 else "calm" if vp < 14 else ""
        cards.append(_stat_card("VIX", f'{vp:.1f}', vl, vc, vc))
    if oil_d:
        c = COLORS["success"] if oil_d["day_chg"] >= 0 else COLORS["danger"]
        arrow = "▲" if oil_d["day_chg"] >= 0 else "▼"
        cards.append(_stat_card("Crude", f'${oil_d["price"]:.2f}', f'{arrow} {abs(oil_d["day_chg"]):.1f}%', c))
    if gold_d:
        c = COLORS["success"] if gold_d["day_chg"] >= 0 else COLORS["danger"]
        arrow = "▲" if gold_d["day_chg"] >= 0 else "▼"
        cards.append(_stat_card("Gold", f'${gold_d["price"]:,.0f}', f'{arrow} {abs(gold_d["day_chg"]):.1f}%', c))
    if btc_d:
        c = COLORS["success"] if btc_d["day_chg"] >= 0 else COLORS["danger"]
        arrow = "▲" if btc_d["day_chg"] >= 0 else "▼"
        cards.append(_stat_card("BTC", f'${btc_d["price"]:,.0f}', f'{arrow} {abs(btc_d["day_chg"]):.1f}%', c))
    # Row 2: IWM, DIA, NatGas, 10Y, 30Y, DXY
    iwm_d = _market_cache.get("IWM")
    if iwm_d:
        c = COLORS["success"] if iwm_d["day_chg"] >= 0 else COLORS["danger"]
        arrow = "▲" if iwm_d["day_chg"] >= 0 else "▼"
        cards.append(_stat_card("Russell", f'${iwm_d["price"]:.2f}', f'{arrow} {abs(iwm_d["day_chg"]):.1f}%', c))
    dia_d = _market_cache.get("DIA")
    if dia_d:
        c = COLORS["success"] if dia_d["day_chg"] >= 0 else COLORS["danger"]
        arrow = "▲" if dia_d["day_chg"] >= 0 else "▼"
        cards.append(_stat_card("Dow", f'${dia_d["price"]:.2f}', f'{arrow} {abs(dia_d["day_chg"]):.1f}%', c))
    ng_d = _market_cache.get("NG=F")
    if ng_d:
        c = COLORS["success"] if ng_d["day_chg"] >= 0 else COLORS["danger"]
        arrow = "▲" if ng_d["day_chg"] >= 0 else "▼"
        cards.append(_stat_card("NatGas", f'${ng_d["price"]:.2f}', f'{arrow} {abs(ng_d["day_chg"]):.1f}%', c))
    tnx_d = _market_cache.get("^TNX")
    if tnx_d:
        # Yields rising = red for equities, falling = green
        c = COLORS["danger"] if tnx_d["day_chg"] > 0 else COLORS["success"]
        arrow = "▲" if tnx_d["day_chg"] >= 0 else "▼"
        cards.append(_stat_card("10Y Yield", f'{tnx_d["price"]:.2f}%', f'{arrow} {abs(tnx_d["day_chg"]):.1f}%', c))
    tyx_d = _market_cache.get("^TYX")
    if tyx_d:
        c = COLORS["danger"] if tyx_d["day_chg"] > 0 else COLORS["success"]
        arrow = "▲" if tyx_d["day_chg"] >= 0 else "▼"
        cards.append(_stat_card("30Y Yield", f'{tyx_d["price"]:.2f}%', f'{arrow} {abs(tyx_d["day_chg"]):.1f}%', c))
    dxy_d = _market_cache.get("DX-Y.NYB")
    if dxy_d:
        c = COLORS["success"] if dxy_d["day_chg"] >= 0 else COLORS["danger"]
        arrow = "▲" if dxy_d["day_chg"] >= 0 else "▼"
        cards.append(_stat_card("Dollar", f'{dxy_d["price"]:.2f}', f'{arrow} {abs(dxy_d["day_chg"]):.1f}%', c))
    if grok_data:
        regimes = grok_data.get("regimes", [])
        if regimes:
            top = max(regimes, key=lambda r: r.get("probability", 0))
            regime_colors = {
                "Stagflation": "#ff4444", "Recession": "#ff8c00", "Soft Landing": "#00cc66",
                "Financial Crisis": "#ff0066", "Re-Acceleration": "#00d1ff", "Goldilocks": "#aa66ff",
            }
            rc = regime_colors.get(top["name"], COLORS["accent"])
            cards.append(_stat_card("Regime", top["name"], f'{top["probability"]}%', rc, rc))
    if conflict_data:
        esc = conflict_data.get("blended", {}).get("escalation_risk", {})
        esc_score = esc.get("score", 0)
        if esc_score > 0:
            ec = COLORS["danger"] if esc_score >= 7 else COLORS["warning"] if esc_score >= 5 else COLORS["success"]
            cards.append(_stat_card("Iran", f'{esc_score}/10', esc.get("level", ""), ec, ec))

    if cards:
        st.markdown(
            f'<div style="display:flex;flex-wrap:wrap;gap:6px;">{"".join(cards)}</div>',
            unsafe_allow_html=True,
        )


# ═══════════════════════════════════════════════
# ROW 2: MARKET DASHBOARD (compact 4x3 grid)
# ═══════════════════════════════════════════════
with error_boundary("Market Dashboard"):
    st.markdown("##### Markets")
    for row_start in range(0, len(MARKET_TICKERS), 4):
        row_tickers = MARKET_TICKERS[row_start:row_start + 4]
        cols = st.columns(4)
        for col, (ticker, name) in zip(cols, row_tickers):
            with col:
                data = _market_cache.get(ticker)
                if data:
                    chg_color = COLORS["success"] if data["day_chg"] >= 0 else COLORS["danger"]
                    arrow = "▲" if data["day_chg"] >= 0 else "▼"
                    st.markdown(
                        f'<div style="font-size:0.75rem;color:{COLORS["text_muted"]};">{name}</div>'
                        f'<span style="font-size:1.15rem;font-weight:700;">{_fmt_price(ticker, data["price"])}</span> '
                        f'<span style="color:{chg_color};font-size:0.8rem;">{arrow}{abs(data["day_chg"]):.1f}%</span>',
                        unsafe_allow_html=True,
                    )
                    st.plotly_chart(_mini_chart(data, 70), use_container_width=True, config={"displayModeBar": False})
                else:
                    st.markdown(f'<div style="font-size:0.75rem;color:{COLORS["text_muted"]};">{name}</div>', unsafe_allow_html=True)
                    st.caption("—")


# ═══════════════════════════════════════════════
# ROW 2.5: RELATIVE PERFORMANCE CHART
# ═══════════════════════════════════════════════
SECTOR_SPYDERS = [
    ("XLK", "Technology", "#00d1ff"),
    ("XLF", "Financials", "#00ff96"),
    ("XLE", "Energy", "#ff4444"),
    ("XLV", "Healthcare", "#ad7fff"),
    ("XLY", "Consumer Disc.", "#ffaa00"),
    ("XLP", "Consumer Staples", "#ff69b4"),
    ("XLI", "Industrials", "#8bc34a"),
    ("XLB", "Materials", "#00bcd4"),
    ("XLU", "Utilities", "#ffd700"),
    ("XLRE", "Real Estate", "#e91e63"),
    ("XLC", "Communications", "#4caf50"),
]

ASSET_CLASS_CONFIG = [
    # Equity
    ("SPY", "S&P 500", "#00d1ff"), ("QQQ", "Nasdaq", "#00ff96"),
    # Govt Debt
    ("TLT", "20Y Treasury", "#ad7fff"), ("IEF", "7-10Y Treasury", "#9575cd"),
    # Corp Debt
    ("LQD", "IG Corporate", "#4dd0e1"), ("VCIT", "Intermediate Corp", "#26c6da"),
    # Junk Debt
    ("HYG", "High Yield", "#ff9800"), ("JNK", "Junk Bonds", "#ffb74d"),
    # Emerging Markets
    ("EEM", "EM Equity", "#e91e63"), ("EMB", "EM Bonds", "#f06292"),
    # Commodities
    ("DBC", "Commodities (DBC)", "#ff4444"), ("GSG", "Broad Commodity", "#ff6659"),
    # Crypto
    ("BTC-USD", "Bitcoin", "#ff9900"), ("ETH-USD", "Ethereum", "#627eea"),
]


@st.cache_data(ttl=300, show_spinner=False)
def _fetch_relative_data(tickers_key: str, period: str = "1mo"):
    """Fetch daily data for relative performance. Filters to weekdays only."""
    days_map = {"1d": 5, "5d": 10, "1mo": 35, "3mo": 95, "6mo": 185, "1y": 370}
    days = days_map.get(period, 35)
    tickers = tickers_key.split(",")
    result = {}
    for sym in tickers:
        try:
            hist = polygon_history(sym, days)
            if not hist.empty and len(hist) >= 2:
                hist = hist[hist.index.weekday < 5]
                if len(hist) >= 2:
                    result[sym] = hist[["Close"]].copy()
        except Exception:
            pass
    return result


with error_boundary("Relative Performance"):
    rp_c1, rp_c2, rp_c3 = st.columns([3, 1, 1])
    with rp_c1:
        st.markdown("##### Relative Performance")
    with rp_c2:
        rel_mode = st.selectbox("Mode", ["Assets", "Sectors"],
                                key="rel_perf_mode", label_visibility="collapsed")
    with rp_c3:
        rel_period_label = st.selectbox("Period", ["1M", "3M", "YTD", "1Y"],
                                        key="rel_perf_period", index=0, label_visibility="collapsed")
    period_map = {"1M": "1mo", "3M": "3mo", "YTD": "ytd", "1Y": "1y"}
    rel_period = period_map[rel_period_label]

    if rel_mode == "Sectors":
        chart_tickers = [(t, n, c) for t, n, c in SECTOR_SPYDERS]
    else:
        chart_tickers = [(t, n, c) for t, n, c in ASSET_CLASS_CONFIG]

    tickers_key = ",".join(t for t, _, _ in chart_tickers)
    rel_data = _fetch_relative_data(tickers_key, rel_period)

    # Calculate all returns and rank them
    perf_list = []
    for ticker, name, color in chart_tickers:
        if ticker in rel_data and len(rel_data[ticker]) >= 2:
            close = rel_data[ticker]["Close"]
            base = float(close.iloc[0])
            if base > 0:
                pct = ((close / base) - 1) * 100
                last_pct = float(pct.iloc[-1])
                perf_list.append({
                    "ticker": ticker, "name": name, "color": color,
                    "close": close, "pct": pct, "last_pct": last_pct,
                })

    # Sort by performance
    perf_list.sort(key=lambda x: x["last_pct"], reverse=True)

    fig_rel = go.Figure()

    # Add gradient fill for #1 (best) and last (worst)
    if len(perf_list) >= 2:
        best = perf_list[0]
        worst = perf_list[-1]

        # Best performer — subtle green fill
        fig_rel.add_trace(go.Scatter(
            x=best["close"].index, y=best["pct"],
            mode="lines", name=best["name"], showlegend=False,
            line=dict(color=best["color"], width=0),
            fill="tozeroy", fillcolor=f"rgba({int(best['color'][1:3],16)},{int(best['color'][3:5],16)},{int(best['color'][5:7],16)},0.08)",
            hoverinfo="skip",
        ))
        # Worst performer — subtle red fill
        fig_rel.add_trace(go.Scatter(
            x=worst["close"].index, y=worst["pct"],
            mode="lines", name=worst["name"], showlegend=False,
            line=dict(color=worst["color"], width=0),
            fill="tozeroy", fillcolor=f"rgba({int(worst['color'][1:3],16)},{int(worst['color'][3:5],16)},{int(worst['color'][5:7],16)},0.08)",
            hoverinfo="skip",
        ))

    # Draw all lines
    for i, p in enumerate(perf_list):
        is_top = (i == 0)
        is_bottom = (i == len(perf_list) - 1)
        line_width = 3 if is_top or is_bottom else 1.5
        opacity = 1.0 if is_top or is_bottom else 0.7

        fig_rel.add_trace(go.Scatter(
            x=p["close"].index, y=p["pct"],
            mode="lines", name=p["name"], showlegend=False,
            line=dict(color=p["color"], width=line_width),
            opacity=opacity,
            hovertemplate=f"{p['name']}: %{{y:.1f}}%<extra></extra>",
        ))

    fig_rel.add_hline(y=0, line_color="rgba(255,255,255,0.12)", line_width=1, line_dash="dash")

    fig_rel.update_layout(
        template="plotly_dark", height=350,
        margin=dict(t=5, b=5, l=2, r=0),
        yaxis=dict(title="", ticksuffix="%", gridcolor="rgba(255,255,255,0.04)",
                   zeroline=False, automargin=True),
        xaxis=dict(gridcolor="rgba(255,255,255,0.04)", automargin=True,
                   tickformat="%b %d"),
        hovermode="x unified",
        showlegend=False,
    )
    st.plotly_chart(fig_rel, use_container_width=True, config={"displayModeBar": False, "responsive": True})

    # HTML legend — wraps cleanly at any width
    legend_items = []
    for p in perf_list:
        pct_c = COLORS["success"] if p["last_pct"] >= 0 else COLORS["danger"]
        legend_items.append(
            f'<span style="display:inline-flex;align-items:center;margin:2px 6px;white-space:nowrap;font-size:0.75rem;">'
            f'<span style="width:10px;height:3px;background:{p["color"]};border-radius:1px;margin-right:4px;flex-shrink:0;"></span>'
            f'<span style="color:{p["color"]};">{p["name"]}</span>'
            f'<span style="color:{pct_c};margin-left:3px;">{p["last_pct"]:+.1f}%</span>'
            f'</span>'
        )
    st.markdown(
        f'<div style="display:flex;flex-wrap:wrap;justify-content:center;gap:0;">{"".join(legend_items)}</div>',
        unsafe_allow_html=True,
    )


# ═══════════════════════════════════════════════
# ROW 3: AI INTELLIGENCE — 3-column layout
# Macro Regime | Conflict Risk | Alerts
# ═══════════════════════════════════════════════
st.markdown("##### AI Intelligence")
ai_c1, ai_c2, ai_c3 = st.columns(3)

# ── Column 1: Macro Regime ──
with ai_c1:
    with st.container(border=True):
        with error_boundary("Macro Regime"):
            st.markdown(f'<div style="font-size:0.8rem;color:{COLORS["text_muted"]};margin-bottom:6px;">MACRO REGIME</div>', unsafe_allow_html=True)
            if grok_data and grok_data.get("regimes"):
                regimes = sorted(grok_data["regimes"], key=lambda r: r.get("probability", 0), reverse=True)
                top = regimes[0]
                regime_colors = {
                    "Stagflation": "#ff4444", "Recession": "#ff8c00", "Soft Landing": "#00cc66",
                    "Financial Crisis": "#ff0066", "Re-Acceleration": "#00d1ff", "Goldilocks": "#aa66ff",
                }
                top_color = regime_colors.get(top["name"], "#888")
                st.markdown(
                    f'<div style="font-size:1.3rem;font-weight:800;color:{top_color};">{top["name"]}</div>'
                    f'<div style="font-size:0.85rem;color:{COLORS["text_muted"]};">{top["probability"]}% probability</div>',
                    unsafe_allow_html=True,
                )

                # Mini horizontal bars
                fig_r = go.Figure()
                names = [r["name"] for r in regimes]
                probs = [r.get("probability", 0) for r in regimes]
                r_colors = [regime_colors.get(n, "#888") for n in names]
                fig_r.add_trace(go.Bar(x=probs, y=names, orientation="h", marker_color=r_colors,
                                       text=[f"{p}%" for p in probs], textposition="auto"))
                fig_r.update_layout(template="plotly_dark", height=150, margin=dict(l=0, r=0, t=0, b=0),
                                    xaxis=dict(visible=False, range=[0, max(probs) * 1.3]),
                                    yaxis=dict(autorange="reversed", tickfont=dict(size=10)), showlegend=False)
                st.plotly_chart(fig_r, use_container_width=True, config={"displayModeBar": False})

                sent = grok_data.get("sentiment_summary", "")
                if sent:
                    st.caption(sent[:120])

                if st.button("Full Analysis", key="goto_scenario", use_container_width=True):
                    st.switch_page("pages/02_Scenario_Analysis.py")
            else:
                st.caption("Run Scenario Analysis to generate macro regime data.")
                if st.button("Go to Scenario Analysis", key="goto_scenario2", use_container_width=True):
                    st.switch_page("pages/02_Scenario_Analysis.py")

# ── Column 2: Conflict Risk ──
with ai_c2:
    with st.container(border=True):
        with error_boundary("Conflict Risk"):
            st.markdown(f'<div style="font-size:0.8rem;color:{COLORS["text_muted"]};margin-bottom:6px;">IRAN CONFLICT</div>', unsafe_allow_html=True)
            if conflict_data and conflict_data.get("blended"):
                blended = conflict_data["blended"]
                esc = blended.get("escalation_risk", {})
                score = esc.get("score", 0)
                level = esc.get("level", "Unknown")
                esc_color = "#ff4444" if score >= 8 else "#ff6b35" if score >= 6 else "#ffaa00" if score >= 4 else "#00ff96"

                war_start = datetime(2026, 2, 28)
                days = (datetime.now() - war_start).days

                st.markdown(
                    f'<div style="font-size:2rem;font-weight:800;color:{esc_color};">{score}/10</div>'
                    f'<div style="font-size:0.85rem;color:{COLORS["text_muted"]};">{level} · Day {days}</div>',
                    unsafe_allow_html=True,
                )

                # Oil impact
                oil_imp = blended.get("oil_impact", {})
                if oil_imp:
                    price_range = oil_imp.get("price_range", "")
                    if price_range:
                        st.markdown(f'<div style="font-size:0.8rem;margin-top:6px;">Oil forecast: **{price_range}**</div>', unsafe_allow_html=True)

                # Situation summary
                situation = blended.get("situation_summary", "")
                if situation:
                    st.caption(situation[:150] + ("..." if len(situation) > 150 else ""))

                if st.button("Full Analysis", key="goto_conflict", use_container_width=True):
                    st.switch_page("pages/19_Iran_Conflict.py")
            else:
                st.caption("Run Iran Conflict analysis to generate risk data.")
                if st.button("Go to Iran Conflict", key="goto_conflict2", use_container_width=True):
                    st.switch_page("pages/19_Iran_Conflict.py")

# ── Column 3: Alerts & Watchlist ──
with ai_c3:
    with st.container(border=True):
        with error_boundary("Alerts"):
            st.markdown(f'<div style="font-size:0.8rem;color:{COLORS["text_muted"]};margin-bottom:6px;">ALERTS</div>', unsafe_allow_html=True)
            alerts = []

            # Regime shifts
            try:
                if os.path.exists(GROK_HISTORY):
                    with open(GROK_HISTORY, "r") as f:
                        history = json.load(f)
                    if len(history) >= 2:
                        latest = {r["name"]: r.get("probability", 0) for r in history[-1].get("regimes", [])}
                        prev = {r["name"]: r.get("probability", 0) for r in history[-2].get("regimes", [])}
                        for regime, prob in latest.items():
                            diff = prob - prev.get(regime, prob)
                            if abs(diff) >= 5:
                                direction = "rose" if diff > 0 else "fell"
                                alerts.append(f"**{regime}** {direction} {abs(diff)}pp to {prob}%")
            except Exception:
                pass

            # VIX
            vix_data = _market_cache.get("^VIX")
            if vix_data and vix_data["price"] > 25:
                alerts.append(f"**VIX {vix_data['price']:.1f}** — elevated fear")
            if vix_data and vix_data["day_chg"] > 10:
                alerts.append(f"**VIX spiked {vix_data['day_chg']:+.1f}%**")

            # Watchlist
            watchlist = st.session_state.get("watchlist", {})
            for wl_t, wl_cfg in watchlist.items():
                wl_d = fetch_market_data(wl_t)
                if wl_d:
                    if wl_cfg.get("above") and wl_d["price"] >= wl_cfg["above"]:
                        alerts.append(f"**{wl_t}** above ${wl_cfg['above']:.2f}")
                    if wl_cfg.get("below") and wl_d["price"] <= wl_cfg["below"]:
                        alerts.append(f"**{wl_t}** below ${wl_cfg['below']:.2f}")
                    if abs(wl_d["day_chg"]) >= wl_cfg.get("move_pct", 5):
                        alerts.append(f"**{wl_t}** moved {wl_d['day_chg']:+.1f}%")

            if alerts:
                for alert in alerts[:6]:
                    st.markdown(
                        f'<div style="border-left:3px solid {COLORS["warning"]};padding:4px 10px;margin-bottom:4px;'
                        f'font-size:0.8rem;background:rgba(255,170,0,0.06);border-radius:2px;">{alert}</div>',
                        unsafe_allow_html=True,
                    )
            else:
                st.caption("No active alerts.")


# ═══════════════════════════════════════════════
# ROW 4: QUICK ACCESS — feature previews with nav
# ═══════════════════════════════════════════════
st.markdown("##### Quick Access")
qa1, qa2, qa3, qa4 = st.columns(4)

with qa1:
    with st.container(border=True):
        st.markdown(
            f'<div style="text-align:center;">'
            f'<div style="font-size:1.5rem;">🧠</div>'
            f'<div style="font-weight:700;font-size:0.9rem;">Stock Analysis</div>'
            f'<div style="font-size:0.7rem;color:{COLORS["text_muted"]};">Multi-model AI scoring, price targets, sentiment</div>'
            f'</div>', unsafe_allow_html=True,
        )
        # Show last analyzed ticker if available
        last_stock = st.session_state.get("active_ticker", "")
        if last_stock and st.session_state.get(f"stock_analysis_{last_stock}"):
            cached = st.session_state[f"stock_analysis_{last_stock}"]
            blended = cached.get("blended", {})
            rec = blended.get("recommendation", "")
            composite = blended.get("composite_score", 0)
            if rec:
                rec_color = COLORS["success"] if "buy" in rec.lower() else COLORS["danger"] if "sell" in rec.lower() else COLORS["warning"]
                st.markdown(
                    f'<div style="text-align:center;margin-top:4px;">'
                    f'<span style="font-weight:700;">{last_stock}</span> · '
                    f'<span style="color:{rec_color};">{rec}</span> · '
                    f'<span style="color:{COLORS["accent"]};">{composite}/10</span>'
                    f'</div>', unsafe_allow_html=True,
                )
        if st.button("Open", key="qa_stock", use_container_width=True):
            st.switch_page("pages/03_Stock_Analysis.py")

with qa2:
    with st.container(border=True):
        st.markdown(
            f'<div style="text-align:center;">'
            f'<div style="font-size:1.5rem;">💎</div>'
            f'<div style="font-weight:700;font-size:0.9rem;">Options</div>'
            f'<div style="font-size:0.7rem;color:{COLORS["text_muted"]};">IV surface, GEX, max pain, unusual flow</div>'
            f'</div>', unsafe_allow_html=True,
        )
        if st.button("Open", key="qa_options", use_container_width=True):
            st.switch_page("pages/06_Options_Analysis.py")

with qa3:
    with st.container(border=True):
        st.markdown(
            f'<div style="text-align:center;">'
            f'<div style="font-size:1.5rem;">🦾</div>'
            f'<div style="font-weight:700;font-size:0.9rem;">RL Trading</div>'
            f'<div style="font-size:0.7rem;color:{COLORS["text_muted"]};">Deep Q-Network ensemble, walk-forward validation</div>'
            f'</div>', unsafe_allow_html=True,
        )
        if not tier_config["rl_enabled"]:
            st.caption("Pro+ required")
        if st.button("Open", key="qa_rl", use_container_width=True):
            st.switch_page("pages/04_RL_Trading.py")

with qa4:
    with st.container(border=True):
        st.markdown(
            f'<div style="text-align:center;">'
            f'<div style="font-size:1.5rem;">🔥</div>'
            f'<div style="font-weight:700;font-size:0.9rem;">Energy & Macro</div>'
            f'<div style="font-size:0.7rem;color:{COLORS["text_muted"]};">Oil, NatGas, ERCOT, economic calendar</div>'
            f'</div>', unsafe_allow_html=True,
        )
        if st.button("Open", key="qa_energy", use_container_width=True):
            st.switch_page("pages/14_Oil_Fundamentals.py")

# Second row of quick access
qa5, qa6, qa7, qa8 = st.columns(4)
with qa5:
    with st.container(border=True):
        st.markdown(
            f'<div style="text-align:center;">'
            f'<div style="font-size:1.5rem;">🎲</div>'
            f'<div style="font-weight:700;font-size:0.9rem;">ML Predictor</div>'
            f'<div style="font-size:0.7rem;color:{COLORS["text_muted"]};">Random forest 30-day price forecasts</div>'
            f'</div>', unsafe_allow_html=True,
        )
        if st.button("Open", key="qa_ml", use_container_width=True):
            st.switch_page("pages/09_ML_Stock_Predictor.py")

with qa6:
    with st.container(border=True):
        st.markdown(
            f'<div style="text-align:center;">'
            f'<div style="font-size:1.5rem;">🏗️</div>'
            f'<div style="font-weight:700;font-size:0.9rem;">Backtester</div>'
            f'<div style="font-size:0.7rem;color:{COLORS["text_muted"]};">Algo strategies with grid search optimization</div>'
            f'</div>', unsafe_allow_html=True,
        )
        if st.button("Open", key="qa_backtest", use_container_width=True):
            st.switch_page("pages/11_Algo_Backtester.py")

with qa7:
    with st.container(border=True):
        st.markdown(
            f'<div style="text-align:center;">'
            f'<div style="font-size:1.5rem;">🎯</div>'
            f'<div style="font-weight:700;font-size:0.9rem;">Monte Carlo</div>'
            f'<div style="font-size:0.7rem;color:{COLORS["text_muted"]};">GBM stochastic simulations & VaR</div>'
            f'</div>', unsafe_allow_html=True,
        )
        if st.button("Open", key="qa_mc", use_container_width=True):
            st.switch_page("pages/12_Monte_Carlo.py")

with qa8:
    with st.container(border=True):
        st.markdown(
            f'<div style="text-align:center;">'
            f'<div style="font-size:1.5rem;">📈</div>'
            f'<div style="font-weight:700;font-size:0.9rem;">Futures</div>'
            f'<div style="font-size:0.7rem;color:{COLORS["text_muted"]};">Term structure, contango/backwardation</div>'
            f'</div>', unsafe_allow_html=True,
        )
        if st.button("Open", key="qa_futures", use_container_width=True):
            st.switch_page("pages/20_Futures.py")


# ═══════════════════════════════════════════════
# ROW 5: WATCHLIST
# ═══════════════════════════════════════════════
with error_boundary("Watchlist"):
    if "watchlist" not in st.session_state:
        st.session_state["watchlist"] = {}
    watchlist = st.session_state["watchlist"]

    with st.expander(f"Watchlist ({len(watchlist)} tickers)"):
        if watchlist:
            wl_cols = st.columns(min(len(watchlist), 4))
            for i, (wl_t, wl_cfg) in enumerate(watchlist.items()):
                with wl_cols[i % min(len(watchlist), 4)]:
                    wl_data = fetch_market_data(wl_t)
                    if wl_data:
                        chg_color = COLORS["success"] if wl_data["day_chg"] >= 0 else COLORS["danger"]
                        arrow = "▲" if wl_data["day_chg"] >= 0 else "▼"
                        st.markdown(
                            f'<div style="border:1px solid {COLORS["card_border"]};border-radius:6px;padding:8px;text-align:center;">'
                            f'<div style="font-weight:700;">{wl_t}</div>'
                            f'<div style="font-size:1.1rem;">{_fmt_price(wl_t, wl_data["price"])}</div>'
                            f'<div style="color:{chg_color};font-size:0.8rem;">{arrow}{abs(wl_data["day_chg"]):.1f}%</div>'
                            f'</div>', unsafe_allow_html=True,
                        )
                    else:
                        st.markdown(f"**{wl_t}** — no data")
                    if st.button("Remove", key=f"wl_rm_{wl_t}", use_container_width=True):
                        del st.session_state["watchlist"][wl_t]
                        st.rerun()

        st.markdown("---")
        wl_c1, wl_c2, wl_c3, wl_c4 = st.columns([2, 2, 2, 1])
        with wl_c1:
            wl_new_ticker = st.text_input("Ticker", key="wl_add_ticker", placeholder="AAPL")
        with wl_c2:
            wl_above = st.number_input("Alert above $", key="wl_above", value=0.0, step=1.0, format="%.2f")
        with wl_c3:
            wl_below = st.number_input("Alert below $", key="wl_below", value=0.0, step=1.0, format="%.2f")
        with wl_c4:
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("Add", key="wl_add_btn", use_container_width=True):
                t = wl_new_ticker.strip().upper()
                if t:
                    st.session_state["watchlist"][t] = {
                        "above": wl_above if wl_above > 0 else None,
                        "below": wl_below if wl_below > 0 else None,
                        "move_pct": 5,
                    }
                    st.rerun()
        st.caption("Price thresholds trigger alerts. Leave at 0 for none. Moves >5% always alert.")


# ═══════════════════════════════════════════════
# ROW 6: ACCOUNT
# ═══════════════════════════════════════════════
with error_boundary("Account"):
    st.markdown("##### Account")
    tier_colors = {"free": "#888", "pro": "#00d1ff", "premium": "#ffaa00", "platinum": "#00ff96"}
    t_color = tier_colors.get(tier, "#888")

    from src.auth import get_usage_summary, get_token_balance, render_token_purchase

    summary = get_usage_summary()
    tokens = get_token_balance()

    ac1, ac2, ac3, ac4, ac5 = st.columns(5)
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
    ac5.markdown(
        f'<div style="text-align:center;padding:8px;border:1px solid {COLORS["card_border"]};border-radius:6px;">'
        f'<div style="font-size:0.7rem;color:{COLORS["text_muted"]};">RL Trading</div>'
        f'<div style="font-size:1.1rem;font-weight:700;">{"✓" if tier_config["rl_enabled"] else "✗"}</div></div>',
        unsafe_allow_html=True,
    )

    # Usage gauge
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
        st.caption(f"Session active for {session_str} · {user_email}")

    with st.expander("Buy Analysis Tokens"):
        render_token_purchase()

    from src.auth import render_pricing_cards, STRIPE_LINKS
    with st.expander("Upgrade Plan"):
        render_pricing_cards(current_tier=tier)

    portal_link = STRIPE_LINKS.get("portal", "#")
    if tier != "free":
        st.link_button("Manage Subscription", portal_link, use_container_width=True)

    act1, act2 = st.columns(2)
    with act1:
        with st.expander("Change Password"):
            with st.form("pw_form"):
                new_pw = st.text_input("New Password", type="password")
                if st.form_submit_button("Update"):
                    if supabase:
                        try:
                            supabase.auth.update_user({"password": new_pw})
                            st.success("Password updated.")
                        except Exception as e:
                            st.error(f"Failed: {e}")
                    else:
                        st.info("Not available in local dev mode.")
    with act2:
        if st.button("Log Out", type="secondary", use_container_width=True):
            if supabase:
                supabase.auth.sign_out()
            from src.auth import clear_auth_cookie
            clear_auth_cookie()
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            st.switch_page("app.py")

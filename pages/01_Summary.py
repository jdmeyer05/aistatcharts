import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import json
import os
import logging
from datetime import datetime, date
from src.auth import init_supabase, get_user_tier, get_tier_config, TIERS
from src.layout import setup_page, error_boundary
from src.styles import COLORS

setup_page("01_Summary")
logger = logging.getLogger(__name__)

supabase = init_supabase()
user_email = st.session_state.get("user_email", "User")
tier = get_user_tier()
tier_config = get_tier_config(tier)

st.title("📊 Dashboard")
st.caption(f"Welcome back, **{user_email}**")


# ═══════════════════════════════════════════════
# DATA FETCHING
# ═══════════════════════════════════════════════
@st.cache_data(ttl=300, show_spinner=False)
def fetch_market_data(ticker: str, period: str = "1mo") -> dict:
    try:
        tk = yf.Ticker(ticker)
        hist = tk.history(period=period)
        if hist.empty or len(hist) < 2:
            return None
        close = hist["Close"]
        last = close.iloc[-1]
        prev = close.iloc[-2]
        day_chg = (last / prev - 1) * 100

        week_chg = (last / close.iloc[-6] - 1) * 100 if len(close) > 6 else 0
        month_chg = (last / close.iloc[0] - 1) * 100

        return {
            "price": last, "day_chg": day_chg, "week_chg": week_chg,
            "month_chg": month_chg,
            "open": hist["Open"], "high": hist["High"],
            "low": hist["Low"], "close": close,
            "volume": hist["Volume"], "dates": hist.index,
        }
    except Exception:
        return None


MARKET_GROUPS = {
    "Indices": [
        ("SPY", "S&P 500", COLORS["accent"]),
        ("QQQ", "Nasdaq 100", "#00ff96"),
        ("DIA", "Dow Jones", "#ffaa00"),
        ("IWM", "Russell 2000", "#ad7fff"),
    ],
    "Commodities & Crypto": [
        ("CL=F", "Crude Oil", "#ff4444"),
        ("NG=F", "Natural Gas", "#ff69b4"),
        ("GC=F", "Gold", "#ffd700"),
        ("BTC-USD", "Bitcoin", "#ff9900"),
    ],
    "Rates & FX": [
        ("^TNX", "10Y Yield", "#00bcd4"),
        ("^TYX", "30Y Yield", "#4caf50"),
        ("DX-Y.NYB", "Dollar Index", "#8bc34a"),
        ("^VIX", "VIX", "#f44336"),
    ],
}


# ═══════════════════════════════════════════════
# MARKET DASHBOARD
# ═══════════════════════════════════════════════
with error_boundary("Market Dashboard"):
    for group_name, tickers in MARKET_GROUPS.items():
        st.markdown(f"##### {group_name}")
        cols = st.columns(len(tickers))

        for col, (ticker, name, color) in zip(cols, tickers):
            with col:
                data = fetch_market_data(ticker)
                if data:
                    # Price and daily change
                    chg_color = COLORS["success"] if data["day_chg"] >= 0 else COLORS["danger"]
                    arrow = "▲" if data["day_chg"] >= 0 else "▼"

                    if ticker in ("^TNX", "^TYX", "^VIX"):
                        price_str = f"{data['price']:.2f}"
                    elif ticker == "BTC-USD":
                        price_str = f"${data['price']:,.0f}"
                    else:
                        price_str = f"${data['price']:.2f}"

                    st.markdown(f"**{name}**")
                    st.markdown(
                        f'<span style="font-size:1.3rem;font-weight:700;">{price_str}</span> '
                        f'<span style="color:{chg_color};font-size:0.85rem;">{arrow}{abs(data["day_chg"]):.1f}%</span>',
                        unsafe_allow_html=True,
                    )

                    # Mini candlestick chart
                    fig = go.Figure(go.Candlestick(
                        x=data["dates"],
                        open=data["open"], high=data["high"],
                        low=data["low"], close=data["close"],
                        increasing_line_color=COLORS["success"],
                        decreasing_line_color=COLORS["danger"],
                        increasing_fillcolor=COLORS["success"],
                        decreasing_fillcolor=COLORS["danger"],
                    ))
                    fig.update_layout(
                        template="plotly_dark", height=90, margin=dict(l=0, r=0, t=0, b=0),
                        xaxis=dict(visible=False, rangeslider=dict(visible=False)),
                        yaxis=dict(visible=False),
                        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                        showlegend=False,
                    )
                    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

                    # Period returns
                    periods = {"1W": data["week_chg"], "1M": data["month_chg"]}
                    period_parts = []
                    for label, val in periods.items():
                        pc = COLORS["success"] if val >= 0 else COLORS["danger"]
                        period_parts.append(f'<span style="color:{pc};font-size:0.7rem;">{label} {val:+.1f}%</span>')
                    st.markdown(" · ".join(period_parts), unsafe_allow_html=True)
                else:
                    st.markdown(f"**{name}**")
                    st.caption("Data unavailable")

    st.divider()


# ═══════════════════════════════════════════════
# GROK MACRO PULSE (from Scenario Analysis)
# ═══════════════════════════════════════════════
with error_boundary("Macro Pulse"):
    GROK_HISTORY = os.path.join(os.path.dirname(os.path.dirname(__file__)), "src", "grok_regime_history.json")

    grok_data = None
    try:
        if os.path.exists(GROK_HISTORY):
            with open(GROK_HISTORY, "r") as f:
                history = json.load(f)
                if history:
                    grok_data = history[-1]
    except Exception:
        pass

    if grok_data:
        st.markdown("##### Macro Regime Pulse")
        ts = grok_data.get("timestamp", "")
        try:
            age_min = (datetime.now() - datetime.fromisoformat(ts)).total_seconds() / 60
            age_str = f"{age_min:.0f}m ago" if age_min < 60 else f"{age_min/60:.1f}h ago"
        except Exception:
            age_str = ""

        regimes = grok_data.get("regimes", [])
        if regimes:
            # Sort by probability
            regimes_sorted = sorted(regimes, key=lambda r: r.get("probability", 0), reverse=True)
            top = regimes_sorted[0]

            regime_colors = {
                "Stagflation": "#ff4444", "Recession": "#ff8c00", "Soft Landing": "#00cc66",
                "Financial Crisis": "#ff0066", "Re-Acceleration": "#00d1ff", "Goldilocks": "#aa66ff",
            }

            # Top regime highlight
            top_color = regime_colors.get(top["name"], "#888")
            st.markdown(
                f'<div style="background:rgba({int(top_color[1:3],16)},{int(top_color[3:5],16)},'
                f'{int(top_color[5:7],16)},0.1);border-left:4px solid {top_color};padding:10px 14px;'
                f'border-radius:4px;margin-bottom:10px;">'
                f'<strong style="color:{top_color};font-size:1.1rem;">{top["name"]} — {top["probability"]}%</strong>'
                f'<span style="color:#888;font-size:0.75rem;margin-left:12px;">Updated {age_str}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )

            # Mini bar chart of all regimes
            fig_regime = go.Figure()
            names = [r["name"] for r in regimes_sorted]
            probs = [r.get("probability", 0) for r in regimes_sorted]
            colors = [regime_colors.get(n, "#888") for n in names]

            fig_regime.add_trace(go.Bar(
                x=probs, y=names, orientation="h",
                marker_color=colors,
                text=[f"{p}%" for p in probs], textposition="auto",
            ))
            fig_regime.update_layout(
                template="plotly_dark", height=180, margin=dict(l=0, r=0, t=0, b=0),
                xaxis=dict(visible=False, range=[0, max(probs) * 1.3]),
                yaxis=dict(autorange="reversed"),
                showlegend=False,
            )
            st.plotly_chart(fig_regime, use_container_width=True, config={"displayModeBar": False})

            # Sentiment
            sentiment = grok_data.get("sentiment_summary", "")
            if sentiment:
                st.caption(f"**Sentiment:** {sentiment}")

            change = grok_data.get("change_summary", "")
            if change:
                st.caption(f"**Change:** {change}")

        st.divider()
    else:
        st.caption("Macro pulse will appear after the first Grok analysis runs on the Scenario Analysis page.")
        st.divider()


# ═══════════════════════════════════════════════
# AI ALERTS
# ═══════════════════════════════════════════════
with error_boundary("Alerts"):
    alerts = []

    # Check for regime probability shifts
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
                        alerts.append(f"**{regime}** probability {direction} {abs(diff)}pp → {prob}%")
    except Exception:
        pass

    # Check VIX level
    vix_data = fetch_market_data("^VIX", "5d")
    if vix_data and vix_data["price"] > 25:
        alerts.append(f"**VIX at {vix_data['price']:.1f}** — elevated fear/volatility")
    if vix_data and vix_data["day_chg"] > 10:
        alerts.append(f"**VIX spiked {vix_data['day_chg']:+.1f}%** today — sharp risk-off move")

    if alerts:
        st.markdown("##### Alerts")
        for alert in alerts:
            st.markdown(
                f'<div style="background:rgba(255,170,0,0.08);border-left:3px solid {COLORS["warning"]};'
                f'padding:6px 12px;margin-bottom:6px;border-radius:3px;font-size:0.85rem;">{alert}</div>',
                unsafe_allow_html=True,
            )
        st.divider()


# ═══════════════════════════════════════════════
# PORTFOLIO SNAPSHOT (from Scenario Analysis)
# ═══════════════════════════════════════════════
with error_boundary("Portfolio Snapshot"):
    scenario_tickers = st.session_state.get("scenario_tickers")
    if scenario_tickers:
        st.markdown("##### Portfolio Snapshot")
        port_val = st.session_state.get("scenario_value", 100000)
        st.caption(f"Tickers: {', '.join(scenario_tickers)} · Value: ${port_val:,.0f}")

        # Show regime-weighted EV if available
        grok_regime = st.session_state.get("grok_regime_result")
        if grok_regime and grok_regime.get("success"):
            probs = {r["name"]: r["probability"] for r in grok_regime.get("regimes", [])}
            st.caption(f"Top regime: **{max(probs, key=probs.get)}** ({max(probs.values())}%)")

        st.divider()


# ═══════════════════════════════════════════════
# ACCOUNT & SUBSCRIPTION
# ═══════════════════════════════════════════════
with error_boundary("Account"):
    st.markdown("##### Account")

    tier_colors = {"free": "#888", "pro": "#00d1ff", "premium": "#ffaa00", "institutional": "#00ff96"}
    t_color = tier_colors.get(tier, "#888")

    ac1, ac2, ac3, ac4 = st.columns(4)
    ac1.markdown(
        f'<div style="text-align:center;padding:8px;border:1px solid {t_color};border-radius:6px;">'
        f'<div style="font-size:0.7rem;color:{COLORS["text_muted"]};">Plan</div>'
        f'<div style="font-size:1.1rem;font-weight:700;color:{t_color};">{tier_config["name"]}</div></div>',
        unsafe_allow_html=True,
    )

    # AI usage today
    today_key = f"ai_usage_{date.today().isoformat()}"
    used = st.session_state.get(today_key, 0)
    limit = tier_config["daily_ai_analyses"]
    limit_str = "∞" if limit == -1 else str(limit)
    ac2.markdown(
        f'<div style="text-align:center;padding:8px;border:1px solid {COLORS["card_border"]};border-radius:6px;">'
        f'<div style="font-size:0.7rem;color:{COLORS["text_muted"]};">AI Analyses Today</div>'
        f'<div style="font-size:1.1rem;font-weight:700;">{used} / {limit_str}</div></div>',
        unsafe_allow_html=True,
    )

    # Models available
    models = tier_config["ai_models"]
    models_str = ", ".join(models) if models else "None"
    ac3.markdown(
        f'<div style="text-align:center;padding:8px;border:1px solid {COLORS["card_border"]};border-radius:6px;">'
        f'<div style="font-size:0.7rem;color:{COLORS["text_muted"]};">AI Models</div>'
        f'<div style="font-size:0.8rem;font-weight:600;">{models_str if len(models_str) < 25 else f"{len(models)} models"}</div></div>',
        unsafe_allow_html=True,
    )

    ac4.markdown(
        f'<div style="text-align:center;padding:8px;border:1px solid {COLORS["card_border"]};border-radius:6px;">'
        f'<div style="font-size:0.7rem;color:{COLORS["text_muted"]};">RL Trading</div>'
        f'<div style="font-size:1.1rem;font-weight:700;">{"✓" if tier_config["rl_enabled"] else "✗"}</div></div>',
        unsafe_allow_html=True,
    )

    # Account actions
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
            st.session_state["authenticated"] = False
            st.session_state["user_email"] = None
            if "user_tier" in st.session_state:
                del st.session_state["user_tier"]
            st.switch_page("app.py")

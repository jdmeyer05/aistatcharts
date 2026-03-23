import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from src.data_engine import polygon_history, polygon_intraday
import json
import os
import logging
from datetime import datetime, date
from src.auth import init_supabase, get_user_tier, get_tier_config, TIERS
from src.layout import setup_page, error_boundary, fun_loader
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


MARKET_TICKERS = [("^VIX", "VIX")]


def _fmt_price(ticker, price):
    if ticker in ("^TNX", "^TYX", "^VIX", "DX-Y.NYB"):
        return f"{price:.2f}"
    elif ticker == "BTC-USD":
        return f"${price:,.0f}"
    elif ticker == "GC=F":
        return f"${price:,.0f}"
    else:
        return f"${price:.2f}"


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
    from concurrent.futures import ThreadPoolExecutor
    _market_cache = {}
    _tickers_to_fetch = [t for t, _ in MARKET_TICKERS]
    with ThreadPoolExecutor(max_workers=6) as pool:
        _results = list(pool.map(fetch_market_data, _tickers_to_fetch))
    for (_t, _), _r in zip(MARKET_TICKERS, _results):
        _market_cache[_t] = _r
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
# ROW 1: RELATIVE PERFORMANCE — CARDS + CHART
# ═══════════════════════════════════════════════
PERF_LISTS = {
    "Sectors": [
        ("XLK", "Technology", "#00e5ff"),
        ("XLF", "Financials", "#00ff87"),
        ("XLE", "Energy", "#ff3333"),
        ("XLV", "Healthcare", "#bf6fff"),
        ("XLY", "Consumer Disc.", "#ffbb00"),
        ("XLP", "Cons. Staples", "#ff5ecf"),
        ("XLI", "Industrials", "#a0e515"),
        ("XLB", "Materials", "#00e0d0"),
        ("XLU", "Utilities", "#ffe100"),
        ("XLRE", "Real Estate", "#ff2277"),
        ("XLC", "Comms", "#33dd55"),
    ],
    "Equity Indices": [
        ("SPY", "S&P 500", "#00e5ff"),
        ("QQQ", "Nasdaq 100", "#00ff87"),
        ("DIA", "Dow 30", "#ffbb00"),
        ("IWM", "Russell 2000", "#ff5ecf"),
        ("MDY", "S&P 400 Mid", "#a0e515"),
        ("EFA", "Developed Intl", "#bf6fff"),
        ("EEM", "Emerging Mkts", "#ff2277"),
        ("VGK", "Europe", "#00e0d0"),
        ("EWJ", "Japan", "#ffe100"),
        ("FXI", "China", "#ff3333"),
    ],
    "Fixed Income": [
        ("AGG", "US Agg Bond", "#00e5ff"),
        ("TLT", "20Y Treasury", "#bf6fff"),
        ("IEF", "7-10Y Treasury", "#8888ff"),
        ("SHY", "1-3Y Treasury", "#00e0d0"),
        ("TIP", "TIPS", "#a0e515"),
        ("LQD", "IG Corporate", "#00ccff"),
        ("VCIT", "Interm. Corp", "#33bbee"),
        ("HYG", "High Yield", "#ffbb00"),
        ("JNK", "Junk Bonds", "#ff8800"),
        ("EMB", "EM Bonds", "#ff2277"),
    ],
    "Commodities": [
        ("GLD", "Gold", "#ffe100"),
        ("SLV", "Silver", "#ccddee"),
        ("USO", "Crude Oil", "#ff3333"),
        ("UNG", "Natural Gas", "#ff8800"),
        ("CPER", "Copper", "#ff2277"),
        ("WEAT", "Wheat", "#a0e515"),
        ("DBA", "Agriculture", "#00ff87"),
        ("URA", "Uranium", "#bf6fff"),
    ],
    "Mega Caps": [
        ("AAPL", "Apple", "#cccccc"),
        ("MSFT", "Microsoft", "#00e5ff"),
        ("NVDA", "Nvidia", "#00ff87"),
        ("AMZN", "Amazon", "#ff8800"),
        ("GOOGL", "Google", "#ff3333"),
        ("META", "Meta", "#5588ff"),
        ("TSLA", "Tesla", "#ff2277"),
        ("BRK.B", "Berkshire", "#bf6fff"),
        ("JPM", "JPMorgan", "#00e0d0"),
        ("V", "Visa", "#ffe100"),
    ],
}



# Top holdings per group — (ticker, weight) — weight ~ relative market cap
TREEMAP_HOLDINGS = {
    "Sectors": {
        "Technology": [
            ("AAPL", 40), ("MSFT", 38), ("NVDA", 35), ("AVGO", 10), ("ADBE", 6),
            ("CRM", 6), ("AMD", 5), ("ORCL", 5), ("CSCO", 5), ("ACN", 5),
        ],
        "Financials": [
            ("JPM", 15), ("BAC", 8), ("WFC", 6), ("GS", 5), ("MS", 4),
            ("BLK", 4), ("SCHW", 3), ("AXP", 3), ("C", 3), ("PGR", 3),
        ],
        "Energy": [
            ("XOM", 14), ("CVX", 10), ("COP", 5), ("SLB", 3), ("EOG", 3),
            ("MPC", 3), ("PSX", 2), ("OXY", 2), ("VLO", 2), ("WMB", 2),
        ],
        "Healthcare": [
            ("LLY", 18), ("UNH", 14), ("JNJ", 10), ("MRK", 8), ("ABBV", 8),
            ("ABT", 6), ("TMO", 5), ("PFE", 4), ("AMGN", 4), ("ISRG", 4),
        ],
        "Consumer Disc.": [
            ("AMZN", 25), ("TSLA", 12), ("HD", 8), ("MCD", 5), ("NKE", 4),
            ("LOW", 4), ("SBUX", 3), ("TJX", 3), ("BKNG", 3), ("CMG", 2),
        ],
        "Cons. Staples": [
            ("PG", 10), ("COST", 8), ("WMT", 8), ("KO", 7), ("PEP", 6),
            ("PM", 4), ("CL", 3), ("MO", 2), ("MDLZ", 3), ("KHC", 2),
        ],
        "Industrials": [
            ("GE", 6), ("CAT", 5), ("RTX", 5), ("HON", 5), ("UNP", 5),
            ("BA", 4), ("DE", 4), ("LMT", 4), ("UPS", 3), ("MMM", 2),
        ],
        "Materials": [
            ("LIN", 6), ("APD", 3), ("SHW", 3), ("ECL", 2), ("FCX", 3),
            ("NEM", 2), ("NUE", 2), ("DOW", 2),
        ],
        "Utilities": [
            ("NEE", 5), ("SO", 3), ("DUK", 3), ("D", 2), ("AEP", 2),
            ("SRE", 2), ("EXC", 2), ("ED", 2),
        ],
        "Real Estate": [
            ("PLD", 4), ("AMT", 3), ("EQIX", 3), ("SPG", 2), ("CCI", 2),
            ("O", 2), ("PSA", 2), ("WELL", 2),
        ],
        "Comms": [
            ("META", 16), ("GOOGL", 20), ("NFLX", 6), ("DIS", 4), ("CMCSA", 4),
            ("T", 3), ("VZ", 3), ("TMUS", 4),
        ],
    },
    "Equity Indices": {
        "S&P 500": [
            ("AAPL", 7), ("MSFT", 7), ("NVDA", 6), ("AMZN", 4), ("META", 3),
            ("GOOGL", 3), ("BRK.B", 2), ("LLY", 2), ("AVGO", 2), ("JPM", 2),
        ],
        "Nasdaq 100": [
            ("AAPL", 9), ("MSFT", 8), ("NVDA", 7), ("AMZN", 5), ("META", 4),
            ("AVGO", 4), ("GOOGL", 4), ("TSLA", 3), ("COST", 3), ("NFLX", 2),
        ],
        "Dow 30": [
            ("UNH", 8), ("GS", 7), ("MSFT", 6), ("HD", 6), ("CAT", 5),
            ("AMGN", 5), ("MCD", 4), ("V", 4), ("CRM", 4), ("TRV", 3),
        ],
        "Russell 2000": [
            ("SMCI", 2), ("MSTR", 2), ("INSM", 1), ("FN", 1), ("ANF", 1),
            ("CORT", 1), ("FTDR", 1), ("PCVX", 1), ("SPR", 1), ("DUOL", 1),
        ],
        "Developed Intl": [
            ("NOVO-B", 3), ("ASML", 3), ("AZN", 2), ("SAP", 2), ("SHEL", 2),
            ("NESN", 2), ("TTE", 2), ("ROG", 2), ("ULVR", 2), ("LVMH", 2),
        ],
        "Emerging Mkts": [
            ("TSM", 8), ("BABA", 4), ("TCEHY", 3), ("RELIANCE", 2), ("PDD", 2),
            ("INFY", 2), ("VALE", 2), ("ITUB", 1), ("NU", 1), ("JD", 1),
        ],
    },
    "Fixed Income": {
        "US Agg Bond": [("AGG", 30)],
        "20Y Treasury": [("TLT", 20)],
        "7-10Y Treasury": [("IEF", 15)],
        "1-3Y Treasury": [("SHY", 12)],
        "TIPS": [("TIP", 10)],
        "IG Corporate": [("LQD", 18)],
        "Interm. Corp": [("VCIT", 12)],
        "High Yield": [("HYG", 15)],
        "Junk Bonds": [("JNK", 10)],
        "EM Bonds": [("EMB", 10)],
    },
    "Commodities": {
        "Gold": [("GLD", 30)],
        "Silver": [("SLV", 12)],
        "Crude Oil": [("USO", 15)],
        "Natural Gas": [("UNG", 8)],
        "Copper": [("CPER", 6)],
        "Wheat": [("WEAT", 5)],
        "Agriculture": [("DBA", 6)],
        "Uranium": [("URA", 8)],
    },
    "Mega Caps": {
        "Tech": [
            ("AAPL", 35), ("MSFT", 32), ("NVDA", 30), ("AVGO", 10),
        ],
        "Internet": [
            ("AMZN", 22), ("GOOGL", 20), ("META", 16), ("NFLX", 6),
        ],
        "Finance": [
            ("BRK.B", 10), ("JPM", 8), ("V", 7), ("MA", 6),
        ],
        "Other": [
            ("TSLA", 12), ("LLY", 10), ("UNH", 9), ("WMT", 6),
        ],
    },
}


@st.cache_data(ttl=300, show_spinner=False)
def _fetch_treemap_returns(tickers_key: str, period: str) -> dict:
    """Fetch period returns + metadata for treemap. Returns {ticker: {pct, price, day_chg, volume}}."""
    if period == "ytd":
        days = (date.today() - date(date.today().year, 1, 1)).days + 5
    else:
        days_map = {"1mo": 35, "3mo": 95, "6mo": 185, "1y": 370}
        days = days_map.get(period, 35)
    tickers = tickers_key.split(",")

    def _get_return(sym):
        try:
            hist = polygon_history(sym, days)
            if not hist.empty and len(hist) >= 2:
                first = float(hist["Close"].iloc[0])
                last = float(hist["Close"].iloc[-1])
                if first > 0:
                    pct = ((last / first) - 1) * 100
                    prev = float(hist["Close"].iloc[-2])
                    day_chg = ((last / prev) - 1) * 100 if prev > 0 else 0
                    vol = int(hist["Volume"].iloc[-1]) if "Volume" in hist.columns else 0
                    return sym, {"pct": pct, "price": last, "day_chg": day_chg, "volume": vol}
        except Exception:
            pass
        return sym, None

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(_get_return, tickers))
    return {sym: data for sym, data in results if data is not None}


def _render_treemap(list_name: str, period: str, period_label: str):
    """Render a Finviz-style treemap heatmap for the selected list."""
    holdings = TREEMAP_HOLDINGS.get(list_name)
    if not holdings:
        return

    # Collect all unique tickers
    all_tickers = set()
    for group_stocks in holdings.values():
        for ticker, _ in group_stocks:
            all_tickers.add(ticker)

    tickers_key = ",".join(sorted(all_tickers))
    returns = _fetch_treemap_returns(tickers_key, period)

    if not returns:
        return

    # Group stocks with their return data
    group_data = {}
    for group_name, stocks in holdings.items():
        group_total = 0
        group_rets = []
        children = []
        for ticker, weight in stocks:
            data = returns.get(ticker)
            if data is None:
                continue
            children.append((ticker, weight, data))
            group_total += weight
            group_rets.append(data["pct"])
        if children:
            group_data[group_name] = {
                "total": group_total,
                "avg_ret": sum(group_rets) / len(group_rets),
                "children": children,
            }

    if not group_data:
        return

    # Render HTML cards grouped by sector/category
    all_html = []
    for group_name, gd in group_data.items():
        avg = gd["avg_ret"]
        avg_color = "#00ff96" if avg >= 0 else "#ff4444"
        avg_arrow = "▲" if avg >= 0 else "▼"

        # Group header
        all_html.append(
            f'<div style="font-size:0.62rem;color:#888;text-transform:uppercase;letter-spacing:0.5px;'
            f'margin:8px 0 3px 2px;display:flex;justify-content:space-between;">'
            f'<span>{group_name}</span>'
            f'<span style="color:{avg_color};">{avg_arrow}{abs(avg):.1f}%</span></div>'
        )

        # Stock cards
        n = len(gd["children"])
        cols = min(n, 10)
        cells = []
        for ticker, weight, data in gd["children"]:
            pct = data["pct"]
            price = data["price"]
            day_chg = data["day_chg"]
            if pct >= 0:
                bg = f"rgba(0,{min(int(abs(pct) * 20 + 30), 140)},0,0.3)"
                txt = "#00ff96"
            else:
                bg = f"rgba({min(int(abs(pct) * 20 + 30), 140)},0,0,0.3)"
                txt = "#ff4444"
            arrow = "▲" if pct >= 0 else "▼"
            d_arrow = "▲" if day_chg >= 0 else "▼"
            d_color = "#00ff96" if day_chg >= 0 else "#ff4444"
            p_str = f"${price:,.0f}" if price >= 1000 else f"${price:.2f}" if price >= 1 else f"${price:.4f}"
            cells.append(
                f'<div style="text-align:center;padding:5px 3px;background:{bg};border-radius:4px;'
                f'border-left:2px solid {txt};" title="{ticker} | {p_str} | Day: {d_arrow}{abs(day_chg):.2f}%">'
                f'<div style="font-size:0.6rem;color:#ccc;font-weight:700;">{ticker}</div>'
                f'<div style="font-size:0.82rem;font-weight:800;color:{txt};">{arrow}{abs(pct):.1f}%</div>'
                f'<div style="font-size:0.5rem;color:#777;">{p_str}</div>'
                f'</div>'
            )
        all_html.append(
            f'<div style="display:grid;grid-template-columns:repeat({cols},1fr);gap:3px;">{"".join(cells)}</div>'
        )

    st.markdown(
        f'<div style="margin-top:4px;">{"".join(all_html)}</div>',
        unsafe_allow_html=True,
    )


with error_boundary("Relative Performance"):
    rp_c1, rp_c2, rp_c3 = st.columns([3, 1, 1])
    with rp_c1:
        st.markdown("##### Market Heatmap")
    with rp_c2:
        rel_mode = st.selectbox("List", list(PERF_LISTS.keys()),
                                key="rel_perf_mode", label_visibility="collapsed")
    with rp_c3:
        rel_period_label = st.selectbox("Period", ["1M", "3M", "YTD", "1Y"],
                                        key="rel_perf_period", index=0, label_visibility="collapsed")
    period_map = {"1M": "1mo", "3M": "3mo", "YTD": "ytd", "1Y": "1y"}
    rel_period = period_map[rel_period_label]

    _render_treemap(rel_mode, rel_period, rel_period_label)


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
                            # Re-authenticate with cookie to ensure we update the correct user
                            refresh_token = st.context.cookies.get("sb_refresh")
                            if refresh_token:
                                supabase.auth.refresh_session(refresh_token)
                                supabase.auth.update_user({"password": new_pw})
                                st.success("Password updated.")
                            else:
                                st.error("Session expired. Please log out and log in again.")
                        except Exception as e:
                            st.error(f"Failed: {e}")
                    else:
                        st.info("Not available in local dev mode.")
    with act2:
        if st.button("Log Out", type="secondary", use_container_width=True):
            from src.auth import clear_auth_cookie
            clear_auth_cookie()
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            st.switch_page("app.py")

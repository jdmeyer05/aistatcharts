import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import logging
from src.layout import setup_page, error_boundary, fun_loader
from src.styles import COLORS
from src.cross_context import write_context

logger = logging.getLogger(__name__)

setup_page("21_Fed_Macro_Drivers")

st.title("Fed & Macro Drivers")
st.markdown("The key economic indicators the Federal Reserve watches most closely when setting monetary policy.")


from src.api_keys import get_secret as _get_key


fred_key = _get_key("FRED_API_KEY")
if not fred_key:
    st.warning("FRED API key not configured. Add FRED_API_KEY to your secrets.")
    st.stop()


# ════════════════════════════════════════
# DATA
# ════════════════════════════════════════
from src.market_data import fetch_fred_series as _fetch_fred_canonical

def fetch_fred_series(api_key: str, series_id: str, limit: int = 60):
    """Wrapper for backward compat — delegates to src.market_data."""
    return _fetch_fred_canonical(series_id, periods=limit)


STOCKTWITS_MACRO_SYMBOLS = ["SPY", "QQQ", "TLT", "USO", "GLD", "DIA", "IWM", "VIX"]

from src.market_data import fetch_stocktwits_sentiment as _fetch_st_canonical


def fetch_stocktwits_sentiment(symbols: list = None) -> list:
    if symbols is None:
        symbols = STOCKTWITS_MACRO_SYMBOLS
    return _fetch_st_canonical(symbols)


POLYMARKET_SLUGS = {
    # Macro / recession
    "us-recession-by-end-of-2026": "US Recession by End of 2026",
    # Iran / geopolitical
    "will-the-iranian-regime-fall-by-the-end-of-2026": "Iranian Regime Falls by End of 2026",
    "will-the-us-invade-iran-before-2027": "US Invades Iran Before 2027",
    "us-iran-nuclear-deal-before-2027": "US-Iran Nuclear Deal Before 2027",
    # Crypto / markets
    "will-bitcoin-hit-1m-before-gta-vi-872": "Bitcoin Hits $1M",
    "will-usdt-market-cap-hit-200b-before-2027": "USDT Market Cap $200B",
    "microstrategy-sells-any-bitcoin-by-december-31-2026": "MicroStrategy Sells BTC by 2027",
    "trump-eliminates-capital-gains-tax-on-crypto-before-2027": "Trump Eliminates Crypto Cap Gains Tax",
    # Geopolitical
    "will-china-invade-taiwan-before-2027": "China Invades Taiwan by 2027",
    "china-x-india-military-clash-by-december-31-2026": "China-India Military Clash by 2027",
    "will-openai-launch-a-new-consumer-hardware-product-by-december-31-2026": "OpenAI Consumer Hardware by 2027",
}


from src.market_data import fetch_polymarket_odds


def fetch_polymarket_data() -> list:
    return fetch_polymarket_odds(POLYMARKET_SLUGS)


FED_DRIVERS = {
    "CPIAUCSL":  {"name": "CPI (All Items)",       "unit": "index",  "yoy": True,  "color": "#ff4b4b", "category": "Inflation",  "fed_weight": "Primary"},
    "PCEPILFE":  {"name": "Core PCE",               "unit": "index",  "yoy": True,  "color": "#ffaa00", "category": "Inflation",  "fed_weight": "Primary"},
    "UNRATE":    {"name": "Unemployment Rate",       "unit": "%",      "yoy": False, "color": "#00d1ff", "category": "Employment", "fed_weight": "Primary"},
    "PAYEMS":    {"name": "Nonfarm Payrolls",        "unit": "K",      "yoy": False, "color": "#00ff96", "category": "Employment", "fed_weight": "Primary"},
    "FEDFUNDS":  {"name": "Fed Funds Rate",          "unit": "%",      "yoy": False, "color": "#ad7fff", "category": "Fed",        "fed_weight": "Primary"},
    "T10Y2Y":    {"name": "2s10s Yield Spread",      "unit": "%",      "yoy": False, "color": "#ff69b4", "category": "Rates",      "fed_weight": "High"},
    "DGS10":     {"name": "10-Year Treasury Yield",  "unit": "%",      "yoy": False, "color": "#00bcd4", "category": "Rates",      "fed_weight": "High"},
    "DGS2":      {"name": "2-Year Treasury Yield",   "unit": "%",      "yoy": False, "color": "#8bc34a", "category": "Rates",      "fed_weight": "High"},
    "RSAFS":     {"name": "Retail Sales",            "unit": "$M",     "yoy": True,  "color": "#e91e63", "category": "Consumer",   "fed_weight": "Medium"},
    "UMCSENT":   {"name": "Consumer Sentiment",      "unit": "index",  "yoy": False, "color": "#ffc107", "category": "Consumer",   "fed_weight": "Medium"},
    "INDPRO":    {"name": "Industrial Production",   "unit": "index",  "yoy": True,  "color": "#795548", "category": "Production", "fed_weight": "Medium"},
    "GDP":       {"name": "Real GDP",                "unit": "$B",     "yoy": True,  "color": "#607d8b", "category": "Growth",     "fed_weight": "High"},
    "HOUST":     {"name": "Housing Starts",          "unit": "K",      "yoy": False, "color": "#9c27b0", "category": "Housing",    "fed_weight": "Medium"},
    "DTWEXBGS":  {"name": "Trade-Weighted Dollar",   "unit": "index",  "yoy": False, "color": "#4caf50", "category": "FX",         "fed_weight": "Medium"},
    "ICSA":      {"name": "Initial Jobless Claims",  "unit": "",       "yoy": False, "color": "#ff5722", "category": "Employment", "fed_weight": "High"},
    "SAHMCURRENT": {"name": "Sahm Rule Indicator",   "unit": "",       "yoy": False, "color": "#d50000", "category": "Recession Signal", "fed_weight": "High"},
    "NFCI":      {"name": "Chicago Fed Financial Conditions", "unit": "index", "yoy": False, "color": "#00897b", "category": "Financial Conditions", "fed_weight": "High"},
    "VIXCLS":    {"name": "VIX (Fear Index)",       "unit": "",       "yoy": False, "color": "#f44336", "category": "Market Stress", "fed_weight": "Medium"},
    "BAMLH0A0HYM2": {"name": "HY Credit Spread",   "unit": "%",      "yoy": False, "color": "#e65100", "category": "Market Stress", "fed_weight": "High"},
    "T5YIE":     {"name": "5Y Breakeven Inflation", "unit": "%",      "yoy": False, "color": "#ff6f00", "category": "Inflation Expectations", "fed_weight": "High"},
    "T10YIE":    {"name": "10Y Breakeven Inflation", "unit": "%",     "yoy": False, "color": "#ff8f00", "category": "Inflation Expectations", "fed_weight": "Medium"},
    "PERMIT":    {"name": "Building Permits",       "unit": "K",      "yoy": False, "color": "#ce93d8", "category": "Housing",    "fed_weight": "Medium"},
    "DGORDER":   {"name": "Durable Goods Orders",   "unit": "$M",     "yoy": True,  "color": "#80cbc4", "category": "Production", "fed_weight": "Medium"},
    "JTSJOL":    {"name": "JOLTS Job Openings",     "unit": "K",      "yoy": False, "color": "#4dd0e1", "category": "Employment", "fed_weight": "High"},
    "GDPNOW":    {"name": "GDPNow (Atlanta Fed)", "unit": "%",      "yoy": False, "color": "#26a69a", "category": "Growth",     "fed_weight": "High"},
    "DGS1MO":    {"name": "1-Month Treasury",     "unit": "%",      "yoy": False, "color": "#b0bec5", "category": "Yield Curve", "fed_weight": "Low"},
    "DGS3MO":    {"name": "3-Month Treasury",     "unit": "%",      "yoy": False, "color": "#90a4ae", "category": "Yield Curve", "fed_weight": "Low"},
    "DGS5":      {"name": "5-Year Treasury",       "unit": "%",      "yoy": False, "color": "#78909c", "category": "Yield Curve", "fed_weight": "Medium"},
    "DGS30":     {"name": "30-Year Treasury",      "unit": "%",      "yoy": False, "color": "#546e7a", "category": "Yield Curve", "fed_weight": "Medium"},
}


# ════════════════════════════════════════
# FETCH ALL DRIVER DATA
# ════════════════════════════════════════
driver_data = {}
with fun_loader("data"):
    for sid in FED_DRIVERS:
        df = fetch_fred_series(fred_key, sid, limit=60)
        if not df.empty:
            driver_data[sid] = df

if not driver_data:
    st.error("No FRED data loaded. Check your API key.")
    st.stop()


# ════════════════════════════════════════
# SECTION 1: FED DUAL MANDATE SCORECARD
# ════════════════════════════════════════
with error_boundary("Dual Mandate Scorecard"):
    def _sc_card(label, value, delta, color=COLORS["text_primary"], delta_color=None):
        dc = delta_color or COLORS["text_muted"]
        return (
            f'<div style="flex:1;min-width:0;text-align:center;padding:8px 4px;'
            f'border:1px solid {COLORS["card_border"]};border-radius:6px;">'
            f'<div style="font-size:0.6rem;color:{COLORS["text_muted"]};text-transform:uppercase;'
            f'letter-spacing:0.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{label}</div>'
            f'<div style="font-size:1.1rem;font-weight:700;color:{color};">{value}</div>'
            f'<div style="font-size:0.7rem;color:{dc};">{delta}</div>'
            f'</div>'
        )

    cards = []
    if "PCEPILFE" in driver_data and len(driver_data["PCEPILFE"]) >= 13:
        df_pce = driver_data["PCEPILFE"]
        pce_yoy = ((df_pce.iloc[-1]["value"] / df_pce.iloc[-13]["value"]) - 1) * 100
        prev_pce = ((df_pce.iloc[-2]["value"] / df_pce.iloc[-14]["value"]) - 1) * 100 if len(df_pce) >= 14 else pce_yoy
        pce_chg = pce_yoy - prev_pce
        pce_c = COLORS["danger"] if pce_yoy > 3 else COLORS["warning"] if pce_yoy > 2 else COLORS["success"]
        dc = COLORS["danger"] if pce_chg > 0 else COLORS["success"]
        cards.append(_sc_card("Core PCE YoY", f"{pce_yoy:.1f}%", f"{pce_chg:+.1f}%", pce_c, dc))

    if "UNRATE" in driver_data:
        df_ur = driver_data["UNRATE"]
        ur = df_ur.iloc[-1]["value"]
        ur_prev = df_ur.iloc[-2]["value"] if len(df_ur) > 1 else ur
        ur_chg = ur - ur_prev
        ur_c = COLORS["danger"] if ur > 5 else COLORS["warning"] if ur > 4 else COLORS["success"]
        dc = COLORS["danger"] if ur_chg > 0 else COLORS["success"]
        cards.append(_sc_card("Unemployment", f"{ur:.1f}%", f"{ur_chg:+.1f}%", ur_c, dc))

    if "FEDFUNDS" in driver_data:
        df_ff = driver_data["FEDFUNDS"]
        ff = df_ff.iloc[-1]["value"]
        ff_prev = df_ff.iloc[-2]["value"] if len(df_ff) > 1 else ff
        cards.append(_sc_card("Fed Funds", f"{ff:.2f}%", f"{ff - ff_prev:+.2f}%", COLORS["accent"]))

    if "T10Y2Y" in driver_data:
        df_sp = driver_data["T10Y2Y"]
        spread = df_sp.iloc[-1]["value"]
        sp_c = COLORS["danger"] if spread < 0 else COLORS["success"]
        sp_label = "Inverted" if spread < 0 else "Normal"
        cards.append(_sc_card("2s10s Spread", f"{spread:.2f}%", sp_label, sp_c, sp_c))

    if "PAYEMS" in driver_data and len(driver_data["PAYEMS"]) > 1:
        df_nfp = driver_data["PAYEMS"]
        nfp_change = df_nfp.iloc[-1]["value"] - df_nfp.iloc[-2]["value"]
        nfp_c = COLORS["success"] if nfp_change > 0 else COLORS["danger"]
        cards.append(_sc_card("NFP (MoM)", f"{nfp_change:+,.0f}K", "jobs", nfp_c))

    if cards:
        st.markdown(
            f'<div style="display:flex;gap:6px;margin-bottom:16px;">{"".join(cards)}</div>',
            unsafe_allow_html=True,
        )

    # Write cross-page context
    _fed_ctx = {}
    for sid, info in FED_DRIVERS.items():
        if sid in driver_data and not driver_data[sid].empty:
            _fed_ctx[info.get("name", sid)] = f"{driver_data[sid].iloc[-1]['value']:.2f}"
    _ff_val = driver_data["FEDFUNDS"].iloc[-1]["value"] if "FEDFUNDS" in driver_data and not driver_data["FEDFUNDS"].empty else 0
    _policy = "tightening" if _ff_val > 4 else "neutral/easing"
    write_context("fed_macro", {"signals": _fed_ctx, "policy_stance": _policy})


# ════════════════════════════════════════
# TABS
# ════════════════════════════════════════
tab_signals, tab_trends, tab_fed, tab_fomc_diff, tab_inflation, tab_labor, tab_yields, tab_sentiment = st.tabs([
    "Signal Matrix",
    "Driver Trends",
    "Fed Policy",
    "FOMC Statement Diff",
    "Inflation Deep Dive",
    "Labor Market",
    "Yield Curve",
    "Market Sentiment",
])


# ════════════════════════════════════════
# TAB 1: SIGNAL MATRIX
# ════════════════════════════════════════
with tab_signals, error_boundary("Policy Signal Matrix"):
    st.markdown("##### Policy Signal Matrix")
    st.caption("Where each indicator stands relative to thresholds that influence Fed policy")

    signal_rows = []
    for sid, info in FED_DRIVERS.items():
        if sid not in driver_data or driver_data[sid].empty:
            continue
        df_d = driver_data[sid]
        latest = df_d.iloc[-1]["value"]
        prev = df_d.iloc[-2]["value"] if len(df_d) > 1 else latest
        change = latest - prev

        yoy = None
        prev_yoy = None
        if info["yoy"] and len(df_d) >= 13:
            yoy = ((df_d.iloc[-1]["value"] / df_d.iloc[-13]["value"]) - 1) * 100
            display_val = f"{yoy:.1f}% YoY"
            if len(df_d) >= 14:
                prev_yoy = ((df_d.iloc[-2]["value"] / df_d.iloc[-14]["value"]) - 1) * 100
        elif info["unit"] == "%":
            display_val = f"{latest:.2f}%"
        elif info["unit"] in ("K", "$M", "$B"):
            display_val = f"{latest:,.0f} {info['unit']}"
        else:
            display_val = f"{latest:,.1f}"

        # Signal logic
        if sid in ("CPIAUCSL", "PCEPILFE"):
            if yoy is not None and prev_yoy is not None:
                signal = "Dovish" if yoy < prev_yoy else "Hawkish" if yoy > prev_yoy else "Neutral"
                change_display = f"{yoy - prev_yoy:+.2f}pp"
            else:
                signal, change_display = "Neutral", "N/A"
        elif sid in ("RSAFS", "GDP", "INDPRO"):
            if yoy is not None and prev_yoy is not None:
                signal = "Hawkish" if yoy > prev_yoy else "Dovish" if yoy < prev_yoy else "Neutral"
                change_display = f"{yoy - prev_yoy:+.2f}pp"
            else:
                signal = "Hawkish" if change > 0 else "Dovish"
                change_display = f"{change:+.2f}" if abs(change) < 100 else f"{change:+,.0f}"
        elif sid == "UNRATE":
            signal = "Dovish" if change > 0 else "Hawkish" if change < 0 else "Neutral"
            change_display = f"{change:+.1f}pp"
        elif sid == "ICSA":
            signal = "Dovish" if change > 0 else "Hawkish" if change < 0 else "Neutral"
            change_display = f"{change:+,.0f}"
        elif sid == "PAYEMS":
            signal = "Hawkish" if change > 150 else "Dovish" if change < 100 else "Neutral"
            change_display = f"{change:+,.0f}K"
        elif sid == "FEDFUNDS":
            signal, change_display = "Neutral", f"{change:+.2f}%"
        elif sid == "UMCSENT":
            signal = "Hawkish" if change > 0 else "Dovish" if change < 0 else "Neutral"
            change_display = f"{change:+.1f}"
        elif sid in ("DGS10", "DGS2"):
            signal = "Tightening" if change > 0 else "Easing" if change < 0 else "Neutral"
            change_display = f"{change:+.2f}%"
        elif sid == "T10Y2Y":
            signal = "Recession Risk" if latest < 0 else "Normal"
            change_display = f"{change:+.2f}%"
        elif sid == "HOUST":
            signal = "Hawkish" if change > 0 else "Dovish" if change < 0 else "Neutral"
            change_display = f"{change:+,.0f}K"
        elif sid == "DTWEXBGS":
            signal = "Tightening" if change > 0 else "Easing" if change < 0 else "Neutral"
            change_display = f"{change:+.1f}"
        elif sid == "SAHMCURRENT":
            signal = "Recession Risk" if latest >= 0.5 else "Normal"
            change_display = f"{latest:.2f}"
        elif sid in ("NFCI",):
            signal = "Tightening" if latest > 0 else "Easing"
            change_display = f"{latest:.2f}"
        elif sid in ("T5YIE", "T10YIE"):
            signal = "Hawkish" if latest > 2.5 else "Dovish" if latest < 2.0 else "Neutral"
            change_display = f"{change:+.2f}%"
        elif sid == "VIXCLS":
            signal = "Stress" if latest > 25 else "Calm"
            change_display = f"{change:+.1f}"
        elif sid == "BAMLH0A0HYM2":
            signal = "Stress" if latest > 4 else "Calm"
            change_display = f"{change:+.2f}%"
        elif sid == "PERMIT":
            signal = "Hawkish" if change > 0 else "Dovish" if change < 0 else "Neutral"
            change_display = f"{change:+,.0f}K"
        elif sid == "DGORDER":
            if yoy is not None and prev_yoy is not None:
                signal = "Hawkish" if yoy > prev_yoy else "Dovish" if yoy < prev_yoy else "Neutral"
                change_display = f"{yoy - prev_yoy:+.2f}pp"
            else:
                signal = "Hawkish" if change > 0 else "Dovish"
                change_display = f"{change:+,.0f}"
        elif sid == "JTSJOL":
            signal = "Hawkish" if change > 0 else "Dovish" if change < 0 else "Neutral"
            change_display = f"{change:+,.0f}K"
        else:
            signal, change_display = "Neutral", f"{change:+.2f}"

        signal_rows.append({
            "Indicator": info["name"], "Category": info["category"],
            "Current": display_val, "Change": change_display,
            "Signal": signal, "Weight": info["fed_weight"],
        })

    # Render as styled HTML table
    signal_colors = {
        "Dovish": COLORS["success"], "Easing": COLORS["success"],
        "Hawkish": COLORS["danger"], "Tightening": COLORS["danger"],
        "Recession Risk": COLORS["danger"], "Stress": COLORS["danger"],
        "Neutral": COLORS["text_muted"], "Normal": COLORS["text_muted"], "Calm": COLORS["success"],
    }
    weight_colors = {"Primary": COLORS["accent"], "High": COLORS["warning"], "Medium": COLORS["text_muted"]}

    rows_html = ""
    for r in signal_rows:
        sc = signal_colors.get(r["Signal"], COLORS["text_muted"])
        wc = weight_colors.get(r["Weight"], COLORS["text_muted"])
        rows_html += (
            f'<tr style="border-bottom:1px solid {COLORS["card_border"]};">'
            f'<td style="padding:6px 8px;">{r["Indicator"]}</td>'
            f'<td style="padding:6px 8px;color:{COLORS["text_muted"]};font-size:0.8rem;">{r["Category"]}</td>'
            f'<td style="padding:6px 8px;font-weight:600;">{r["Current"]}</td>'
            f'<td style="padding:6px 8px;">{r["Change"]}</td>'
            f'<td style="padding:6px 8px;color:{sc};font-weight:700;">{r["Signal"]}</td>'
            f'<td style="padding:6px 8px;color:{wc};font-size:0.8rem;">{r["Weight"]}</td>'
            f'</tr>'
        )
    st.markdown(
        f'<table style="width:100%;border-collapse:collapse;font-size:0.85rem;">'
        f'<tr style="border-bottom:2px solid {COLORS["card_border"]};">'
        f'<th style="padding:6px 8px;text-align:left;color:{COLORS["text_muted"]};">Indicator</th>'
        f'<th style="padding:6px 8px;text-align:left;color:{COLORS["text_muted"]};">Category</th>'
        f'<th style="padding:6px 8px;text-align:left;color:{COLORS["text_muted"]};">Current</th>'
        f'<th style="padding:6px 8px;text-align:left;color:{COLORS["text_muted"]};">Change</th>'
        f'<th style="padding:6px 8px;text-align:left;color:{COLORS["text_muted"]};">Signal</th>'
        f'<th style="padding:6px 8px;text-align:left;color:{COLORS["text_muted"]};">Weight</th>'
        f'</tr>{rows_html}</table>',
        unsafe_allow_html=True,
    )

    # Current rate callout
    ff_row = next((r for r in signal_rows if r["Indicator"] == "Fed Funds Rate"), None)
    if ff_row:
        st.markdown(
            f'<div style="padding:10px 14px;border-left:4px solid {COLORS["accent"]};border-radius:4px;'
            f'background:rgba(0,209,255,0.06);font-size:0.95rem;margin-top:8px;">'
            f'Current Fed Funds Rate: <strong>{ff_row["Current"]}</strong></div>',
            unsafe_allow_html=True,
        )

    # Aggregate hawkish/dovish score
    n_hawk = sum(1 for r in signal_rows if r["Signal"] in ("Hawkish", "Tightening", "Stress", "Recession Risk"))
    n_dove = sum(1 for r in signal_rows if r["Signal"] in ("Dovish", "Easing", "Calm"))
    n_neutral = sum(1 for r in signal_rows if r["Signal"] in ("Neutral", "Normal"))
    n_total = len(signal_rows)

    if n_total > 0:
        hawk_pct = n_hawk / n_total * 100
        dove_pct = n_dove / n_total * 100
        net_label = "HAWKISH" if hawk_pct > dove_pct + 10 else ("DOVISH" if dove_pct > hawk_pct + 10 else "MIXED")
        net_color = COLORS["danger"] if net_label == "HAWKISH" else (COLORS["success"] if net_label == "DOVISH" else COLORS["warning"])

        sc1, sc2, sc3, sc4 = st.columns(4)
        sc1.markdown(
            f'<div style="text-align:center;padding:10px;border:2px solid {net_color};border-radius:8px;">'
            f'<div style="font-size:0.65rem;color:{COLORS["text_muted"]};">NET SIGNAL</div>'
            f'<div style="font-size:1.3rem;font-weight:800;color:{net_color};">{net_label}</div>'
            f'</div>', unsafe_allow_html=True)
        sc2.metric("Hawkish", f"{n_hawk}/{n_total}", f"{hawk_pct:.0f}%")
        sc3.metric("Dovish", f"{n_dove}/{n_total}", f"{dove_pct:.0f}%")
        sc4.metric("Neutral", f"{n_neutral}/{n_total}")

    # Taylor Rule estimate
    st.divider()
    st.markdown("##### Taylor Rule vs Actual Rate")
    _cpi_df = driver_data.get("CPIAUCSL")
    _ur_df = driver_data.get("UNRATE")
    _ff_df = driver_data.get("FEDFUNDS")
    if _cpi_df is not None and len(_cpi_df) >= 13 and _ur_df is not None and _ff_df is not None:
        _cpi_13 = _cpi_df.iloc[-13]["value"]
        _cpi_yoy = ((_cpi_df.iloc[-1]["value"] / _cpi_13) - 1) * 100 if _cpi_13 and _cpi_13 > 0 else 0
        _unemployment = float(_ur_df.iloc[-1]["value"])
        _fed_rate = float(_ff_df.iloc[-1]["value"])
        _r_star = 2.5  # neutral real rate estimate
        _inflation_target = 2.0
        _natural_ur = 4.2  # NAIRU estimate

        # Taylor Rule: r = r* + 0.5*(π - π*) + 0.5*(u* - u)
        _taylor = _r_star + 0.5 * (_cpi_yoy - _inflation_target) + 0.5 * (_natural_ur - _unemployment)
        _taylor = max(0, _taylor)  # can't go negative in standard Taylor

        _gap = _fed_rate - _taylor

        tc1, tc2, tc3 = st.columns(3)
        tc1.metric("Actual Rate", f"{_fed_rate:.2f}%")
        tc2.metric("Taylor Rule", f"{_taylor:.2f}%")
        _gap_label = "Too Tight" if _gap > 0.5 else ("Too Loose" if _gap < -0.5 else "About Right")
        _gap_color = "inverse" if _gap > 0.5 else ("normal" if _gap < -0.5 else "off")
        tc3.metric("Gap", f"{_gap:+.2f}%", _gap_label, delta_color=_gap_color)

        st.caption(
            f"Taylor Rule inputs: CPI YoY {_cpi_yoy:.1f}%, Unemployment {_unemployment:.1f}%, "
            f"r* = {_r_star}%, NAIRU = {_natural_ur}%. "
            f"{'The Fed is tighter than the rule suggests — dovish pivot possible.' if _gap > 0.5 else 'The Fed is looser than the rule suggests — more hikes needed.' if _gap < -0.5 else 'Policy rate is roughly aligned with the Taylor Rule.'}"
        )

    # FOMC countdown
    st.divider()
    from src.economic_calendar import get_next_fomc
    _next_fomc_raw = get_next_fomc()
    if _next_fomc_raw:
        from datetime import datetime
        _next_fomc = pd.to_datetime(_next_fomc_raw).date() if isinstance(_next_fomc_raw, str) else _next_fomc_raw
        _days_to_fomc = (_next_fomc - datetime.now().date()).days
        _fomc_color = COLORS["danger"] if _days_to_fomc <= 7 else (COLORS["warning"] if _days_to_fomc <= 21 else COLORS["accent"])
        st.markdown(
            f'<div style="text-align:center;padding:12px;border:1px solid {_fomc_color};border-radius:8px;'
            f'background:rgba({int(_fomc_color[1:3],16)},{int(_fomc_color[3:5],16)},{int(_fomc_color[5:7],16)},0.06);">'
            f'<div style="font-size:0.65rem;color:{COLORS["text_muted"]};">NEXT FOMC MEETING</div>'
            f'<div style="font-size:2rem;font-weight:800;color:{_fomc_color};">{_days_to_fomc}d</div>'
            f'<div style="font-size:0.85rem;color:{COLORS["text_muted"]};">{_next_fomc.strftime("%B %d, %Y")}</div>'
            f'</div>', unsafe_allow_html=True)

        # Market-implied expectations (2Y yield as proxy)
        _2y_df = driver_data.get("DGS2")
        if _2y_df is not None and not _2y_df.empty and _ff_df is not None:
            _2y_yield = float(_2y_df.iloc[-1]["value"])
            _implied_cuts = round((_fed_rate - _2y_yield) / 0.25)
            if _implied_cuts > 0:
                st.caption(f"Market-implied: ~{_implied_cuts} rate cut{'s' if _implied_cuts > 1 else ''} priced in (2Y at {_2y_yield:.2f}% vs Fed Funds at {_fed_rate:.2f}%)")
            elif _implied_cuts < 0:
                st.caption(f"Market-implied: ~{abs(_implied_cuts)} rate hike{'s' if abs(_implied_cuts) > 1 else ''} priced in")
            else:
                st.caption("Market pricing: no change expected near-term")

    st.divider()


# ════════════════════════════════════════
# TAB 2: DRIVER TRENDS
# ════════════════════════════════════════
with tab_trends, error_boundary("Driver Trend Charts"):
    st.markdown("##### Driver Trend Charts")
    st.caption("White dotted line = trimmed mean (middle 95% of data)")

    filtered_drivers = {sid: info for sid, info in FED_DRIVERS.items() if sid in driver_data}

    driver_items = list(filtered_drivers.items())
    for row_start in range(0, len(driver_items), 4):
        cols = st.columns(4)
        for idx, col in enumerate(cols):
            i = row_start + idx
            if i >= len(driver_items):
                break
            sid, info = driver_items[i]
            df_d = driver_data[sid]
            latest = df_d.iloc[-1]["value"]
            prev = df_d.iloc[-2]["value"] if len(df_d) > 1 else latest
            change = latest - prev

            if info["yoy"] and len(df_d) >= 13:
                yoy = ((df_d.iloc[-1]["value"] / df_d.iloc[-13]["value"]) - 1) * 100
                display_val = f"{yoy:.1f}%"
                change_str = f"{change:+.2f}"
            elif info["unit"] == "%":
                display_val = f"{latest:.2f}%"
                change_str = f"{change:+.2f}%"
            elif sid == "PAYEMS":
                display_val = f"{change:+,.0f}K"
                change_str = ""
            elif sid == "ICSA":
                display_val = f"{latest:,.0f}"
                change_str = f"{change:+,.0f}"
            else:
                display_val = f"{latest:,.1f}"
                change_str = f"{change:+.1f}"

            with col:
                if info["yoy"] and len(df_d) >= 13:
                    y_data = ((df_d["value"] / df_d["value"].shift(12) - 1) * 100).iloc[12:]
                    x_data = df_d["date"].iloc[12:]
                else:
                    y_data = df_d["value"]
                    x_data = df_d["date"]

                fig_spark = go.Figure()
                fig_spark.add_trace(go.Scatter(
                    x=x_data, y=y_data, mode="lines",
                    line=dict(color=info["color"], width=1.5), hoverinfo="skip",
                ))

                y_clean = y_data.dropna()
                if len(y_clean) > 10:
                    p_lo, p_hi = np.percentile(y_clean, 2.5), np.percentile(y_clean, 97.5)
                    trimmed = y_clean[(y_clean >= p_lo) & (y_clean <= p_hi)]
                    if len(trimmed) > 0:
                        fig_spark.add_hline(y=trimmed.mean(), line_dash="dot",
                                           line_color="rgba(255,255,255,0.4)", line_width=1)

                # Pad y-axis range so the line doesn't clip at top/bottom
                y_vals = y_data.dropna()
                if len(y_vals) > 0:
                    y_lo, y_hi = float(y_vals.min()), float(y_vals.max())
                    y_pad = (y_hi - y_lo) * 0.15 if y_hi > y_lo else 0.5
                    y_range = [y_lo - y_pad, y_hi + y_pad]
                else:
                    y_range = None

                fig_spark.update_layout(
                    template="plotly_dark", height=80,
                    margin=dict(t=4, b=4, l=4, r=4),
                    xaxis=dict(visible=False), yaxis=dict(visible=False, range=y_range), showlegend=False,
                )
                # Fixed-height label so charts align across columns
                chg_color = COLORS["success"] if change >= 0 else COLORS["danger"]
                st.markdown(
                    f'<div style="height:48px;overflow:hidden;">'
                    f'<div style="font-weight:600;font-size:0.82rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{info["name"]}</div>'
                    f'<div style="font-size:0.78rem;"><span style="color:{COLORS["text_primary"]};">{display_val}</span>'
                    f' <span style="color:{chg_color};">{change_str}</span></div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
                st.plotly_chart(fig_spark, use_container_width=True, key=f"spark_{sid}")

    st.divider()


# ════════════════════════════════════════
# TAB 3: FED POLICY (Dot Plot + SEP + Reaction Function)
# ════════════════════════════════════════
with tab_fed, error_boundary("FOMC Dot Plot"):
    st.markdown("##### FOMC Dot Plot")
    st.caption("Individual FOMC participant projections for the federal funds rate. Source: Fed SEP, March 18, 2026.")

    mar26_dots = {
        "2026": {3.625: 7, 3.375: 7, 3.125: 2, 2.875: 2, 2.625: 1},
        "2027": {3.875: 1, 3.625: 3, 3.375: 4, 3.125: 6, 2.875: 3, 2.625: 1, 2.375: 1},
        "2028": {3.875: 1, 3.625: 3, 3.375: 3, 3.125: 7, 2.875: 3, 2.625: 2},
        "Longer Run": {3.875: 1, 3.750: 1, 3.625: 1, 3.500: 1, 3.375: 2, 3.250: 1,
                       3.125: 3, 3.000: 5, 2.875: 2, 2.625: 2},
    }
    dec25_dots = {
        "2026": {3.875: 4, 3.625: 4, 3.375: 4, 3.125: 2, 2.875: 1, 4.000: 3, 2.125: 1},
        "2027": {3.875: 2, 3.625: 2, 3.375: 6, 3.125: 3, 2.875: 2, 2.625: 1, 4.000: 2},
        "2028": {3.875: 2, 3.625: 2, 3.375: 2, 3.125: 3, 2.875: 4, 4.000: 2},
        "Longer Run": {3.875: 2, 3.625: 3, 3.375: 3, 3.125: 6, 2.875: 4, 4.000: 1},
    }
    mar26_medians = {"2026": 3.4, "2027": 3.1, "2028": 3.125, "Longer Run": 3.0}
    dec25_medians = {"2026": 3.4, "2027": 3.1, "2028": 3.0, "Longer Run": 3.0}

    periods = ["2026", "2027", "2028", "Longer Run"]
    fig_dots = go.Figure()

    for period in periods:
        for rate, count in mar26_dots[period].items():
            if count == 0:
                continue
            for i in range(count):
                x_offset = (i - (count - 1) / 2) * 0.03
                fig_dots.add_trace(go.Scatter(
                    x=[periods.index(period) + x_offset], y=[rate],
                    mode="markers", marker=dict(size=10, color=COLORS["accent"], symbol="circle",
                                                line=dict(width=1, color="#005577")),
                    showlegend=False,
                    hovertemplate=f"<b>Mar 2026</b><br>{period}: {rate:.3f}%<extra></extra>",
                ))
        for rate, count in dec25_dots.get(period, {}).items():
            if count == 0:
                continue
            for i in range(count):
                x_offset = (i - (count - 1) / 2) * 0.03
                fig_dots.add_trace(go.Scatter(
                    x=[periods.index(period) + x_offset], y=[rate],
                    mode="markers", marker=dict(size=7, color="rgba(255,170,0,0.4)", symbol="circle"),
                    showlegend=False,
                    hovertemplate=f"<b>Dec 2025</b><br>{period}: {rate:.3f}%<extra></extra>",
                ))

    fig_dots.add_trace(go.Scatter(
        x=list(range(len(periods))), y=[mar26_medians[p] for p in periods],
        mode="lines+markers", name="Mar 2026 Median",
        line=dict(color=COLORS["accent"], width=2, dash="dash"), marker=dict(size=8, symbol="diamond"),
    ))
    fig_dots.add_trace(go.Scatter(
        x=list(range(len(periods))), y=[dec25_medians[p] for p in periods],
        mode="lines+markers", name="Dec 2025 Median",
        line=dict(color=COLORS["warning"], width=1.5, dash="dot"), marker=dict(size=6, symbol="diamond"),
    ))
    fig_dots.add_hline(y=3.625, line_dash="solid", line_color="rgba(255,255,255,0.3)",
                      annotation_text="Current: 3.50-3.75%", annotation_font_size=10,
                      annotation_position="bottom right")

    fig_dots.update_layout(
        template="plotly_dark", height=500, margin=dict(t=30, b=40, l=0, r=0),
        xaxis=dict(tickvals=list(range(len(periods))), ticktext=periods),
        yaxis=dict(title="Federal Funds Rate (%)", dtick=0.25, range=[2.0, 4.25]),
        hovermode="closest",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    st.plotly_chart(fig_dots, use_container_width=True)
    st.caption("Cyan = March 2026 | Faded orange = December 2025 | Diamonds = median")

    st.divider()

with tab_fed, error_boundary("Economic Projections"):
    st.markdown("##### Summary of Economic Projections (March 2026)")

    sep_html = (
        f'<table style="width:100%;border-collapse:collapse;font-size:0.85rem;">'
        f'<tr style="border-bottom:2px solid {COLORS["card_border"]};">'
        f'<th style="padding:8px;text-align:left;color:{COLORS["text_muted"]};"></th>'
        f'<th style="padding:8px;text-align:center;">2026</th>'
        f'<th style="padding:8px;text-align:center;">2027</th>'
        f'<th style="padding:8px;text-align:center;">2028</th>'
        f'<th style="padding:8px;text-align:center;">Longer Run</th></tr>'
    )
    sep_rows = [
        ("GDP Growth", "2.4%", "2.3%", "2.1%", "2.0%"),
        ("Unemployment", "4.4%", "4.3%", "4.2%", "4.2%"),
        ("PCE Inflation", "2.7%", "2.2%", "2.0%", "2.0%"),
        ("Core PCE", "2.7%", "2.2%", "2.0%", "--"),
        ("Fed Funds (Median)", "3.4%", "3.1%", "3.1%", "3.0%"),
    ]
    for row_name, *vals in sep_rows:
        cells = "".join(f'<td style="padding:8px;text-align:center;">{v}</td>' for v in vals)
        sep_html += f'<tr style="border-bottom:1px solid {COLORS["card_border"]};"><td style="padding:8px;font-weight:600;">{row_name}</td>{cells}</tr>'
    sep_html += "</table>"
    st.markdown(sep_html, unsafe_allow_html=True)

    st.caption("Key shift from December: Inflation projection raised to 2.7% (from 2.5%) reflecting Iran oil shock. "
               "More members now see 0-1 cuts in 2026 (14 of 19) vs December (7 of 19).")

    st.divider()

# ════════════════════════════════════════
# TAB 3 (continued): FED REACTION FUNCTION
# ════════════════════════════════════════
with tab_fed, error_boundary("Fed Reaction Function"):
    st.markdown("##### Fed Reaction Function")
    st.caption("Simplified model of how the Fed weighs these drivers")

    reaction_rows = [
        ("1", "Core PCE (YoY)", "> 2.5% or accelerating", "< 2.0% or decelerating", "Primary inflation gauge; Fed's 2% target"),
        ("2", "Unemployment Rate", "< 4.0% (tight labor)", "> 4.5% or rising fast", "Dual mandate; NAIRU ~4.0-4.2%"),
        ("3", "NFP (MoM change)", "> 200K (strong hiring)", "< 100K (weakening)", "Labor momentum; breakeven ~100-150K"),
        ("4", "Initial Claims", "< 200K (tight)", "> 300K or rising trend", "Leading indicator; weekly frequency"),
        ("5", "2s10s Spread", "N/A", "Inverted (< 0)", "Preceded every recession since 1970"),
        ("6", "Real GDP (YoY)", "> 3.0% (overheating)", "< 1.0% (stalling)", "Overall growth trajectory"),
        ("7", "Retail Sales (YoY)", "Strong growth", "Declining", "~70% of GDP is consumption"),
        ("8", "Consumer Sentiment", "Rising", "Falling sharply", "Forward-looking demand"),
    ]

    react_html = (
        f'<table style="width:100%;border-collapse:collapse;font-size:0.8rem;">'
        f'<tr style="border-bottom:2px solid {COLORS["card_border"]};">'
        f'<th style="padding:6px;text-align:center;color:{COLORS["text_muted"]};">#</th>'
        f'<th style="padding:6px;text-align:left;color:{COLORS["text_muted"]};">Driver</th>'
        f'<th style="padding:6px;text-align:left;color:{COLORS["danger"]};">Hawkish</th>'
        f'<th style="padding:6px;text-align:left;color:{COLORS["success"]};">Dovish</th>'
        f'<th style="padding:6px;text-align:left;color:{COLORS["text_muted"]};">Rationale</th></tr>'
    )
    for num, driver, hawk, dove, rationale in reaction_rows:
        react_html += (
            f'<tr style="border-bottom:1px solid {COLORS["card_border"]};">'
            f'<td style="padding:6px;text-align:center;color:{COLORS["text_muted"]};">{num}</td>'
            f'<td style="padding:6px;font-weight:600;">{driver}</td>'
            f'<td style="padding:6px;color:{COLORS["danger"]};font-size:0.75rem;">{hawk}</td>'
            f'<td style="padding:6px;color:{COLORS["success"]};font-size:0.75rem;">{dove}</td>'
            f'<td style="padding:6px;color:{COLORS["text_muted"]};font-size:0.75rem;">{rationale}</td></tr>'
        )
    react_html += "</table>"
    st.markdown(react_html, unsafe_allow_html=True)

    st.caption(
        "Data sourced from FRED. Signal classifications are simplified heuristics. "
        "The Fed weighs these drivers holistically, not mechanically."
    )


# ════════════════════════════════════════
# TAB 4: MARKET SENTIMENT
# ════════════════════════════════════════
with tab_sentiment:
    pm_c1, pm_c2 = st.columns(2)

    with pm_c1, error_boundary("Polymarket"):
        st.markdown("##### Prediction Markets (Polymarket)")
        st.caption("Live real-money betting odds on macro outcomes")

        pm_data = fetch_polymarket_data()
        if pm_data:
            for item in pm_data:
                prob = item["yes_prob"]
                prob_color = COLORS["danger"] if prob > 60 else COLORS["warning"] if prob > 40 else COLORS["success"]
                st.markdown(
                    f'<div style="display:flex;justify-content:space-between;align-items:center;'
                    f'padding:6px 10px;border-bottom:1px solid {COLORS["card_border"]};font-size:0.85rem;">'
                    f'<span>{item["question"]}</span>'
                    f'<span style="color:{prob_color};font-weight:700;min-width:50px;text-align:right;">{prob}%</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
        else:
            st.info("Polymarket data unavailable.")

    with pm_c2, error_boundary("StockTwits Sentiment"):
        st.markdown("##### Retail Sentiment (StockTwits)")
        st.caption("Bull/bear ratio from last 30 posts per symbol")

        st_data = fetch_stocktwits_sentiment()
        if st_data:
            for item in st_data:
                ratio = item["bull_ratio"]
                sig_color = COLORS["success"] if item["signal"] == "Bullish" else COLORS["danger"] if item["signal"] == "Bearish" else COLORS["text_muted"]
                bar_width = min(ratio, 100)
                st.markdown(
                    f'<div style="padding:4px 10px;border-bottom:1px solid {COLORS["card_border"]};font-size:0.85rem;">'
                    f'<div style="display:flex;justify-content:space-between;margin-bottom:2px;">'
                    f'<span style="font-weight:600;">{item["symbol"]}</span>'
                    f'<span style="color:{sig_color};font-weight:700;">{ratio:.0f}% Bull</span></div>'
                    f'<div style="background:{COLORS["card_border"]};border-radius:3px;height:4px;">'
                    f'<div style="width:{bar_width}%;height:100%;background:{sig_color};border-radius:3px;"></div></div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
        else:
            st.info("StockTwits data unavailable.")

# ════════════════════════════════════════
# FED BALANCE SHEET & LIQUIDITY
# ════════════════════════════════════════
with error_boundary("Fed Balance Sheet"):
    st.markdown("---")
    st.subheader("Fed Balance Sheet & Liquidity")
    st.caption(
        "Net liquidity = Total Assets - TGA - Reverse Repo. "
        "This is the dominant driver of risk asset prices. "
        "When the Fed drains liquidity, equities face headwinds."
    )
    try:
        from src.macro_data import fetch_fed_balance_sheet, get_fed_liquidity_snapshot
        _fed_bs = fetch_fed_balance_sheet()
        _fed_snap = get_fed_liquidity_snapshot()

        if _fed_snap:
            bc1, bc2, bc3, bc4 = st.columns(4)
            bc1.metric("Total Assets", f"${_fed_snap.get('total_assets', '?')}T")
            bc2.metric("TGA", f"${_fed_snap.get('tga', '?')}B")
            bc3.metric("Reverse Repo", f"${_fed_snap.get('rrp', '?')}B")
            _liq_delta = _fed_snap.get("net_liq_change", 0)
            bc4.metric("Net Liquidity", f"${_fed_snap.get('net_liquidity', '?')}T",
                        delta=f"${_liq_delta:+.0f}B/mo" if _liq_delta else None)

        if not _fed_bs.empty:
            import plotly.graph_objects as go
            fig_bs = go.Figure()
            for col in _fed_bs.columns:
                fig_bs.add_trace(go.Scatter(
                    x=_fed_bs.index, y=_fed_bs[col] / 1e6,
                    mode="lines", name=col,
                ))
            fig_bs.update_layout(
                template="plotly_dark", height=350,
                yaxis_title="$ Trillions",
                legend=dict(orientation="h", y=-0.15),
                margin=dict(l=50, r=20, t=10, b=50),
            )
            st.plotly_chart(fig_bs, use_container_width=True, config={"displayModeBar": False})
    except Exception as e:
        st.info(f"Fed balance sheet data unavailable: {e}")

# ════════════════════════════════════════
# MANAGED MONEY POSITIONING (CFTC COT)
# ════════════════════════════════════════
with error_boundary("COT Positioning"):
    st.markdown("---")
    st.subheader("Managed Money Positioning (CFTC COT)")
    st.caption(
        "Hedge fund and CTA positioning from weekly Commitments of Traders reports. "
        "Extreme positioning often precedes reversals — crowded long = sell signal, crowded short = squeeze risk."
    )
    try:
        from src.macro_data import get_cot_positioning_snapshot
        _cot = get_cot_positioning_snapshot()
        if _cot:
            _cot_cols = st.columns(min(5, len(_cot)))
            for i, (contract, pos) in enumerate(_cot.items()):
                _dir_color = COLORS["success"] if pos["direction"] == "Long" else COLORS["danger"]
                _cot_cols[i].metric(
                    contract,
                    f"{pos['direction']} ({pos['net_pct_oi']:+.1f}%)",
                    delta=f"{pos['change']:+,} weekly",
                )
        else:
            st.info("CFTC COT data unavailable.")
    except Exception as e:
        st.info(f"COT data unavailable: {e}")

# ════════════════════════════════════════
# OECD LEADING INDICATORS
# ════════════════════════════════════════
with error_boundary("OECD CLI"):
    st.markdown("---")
    st.subheader("OECD Composite Leading Indicators")
    st.caption(
        "CLI leads GDP by 6-9 months. Values above 100 = expansion, below 100 = contraction. "
        "The turning points are the signal — when CLI peaks, a slowdown is coming."
    )
    try:
        from src.macro_data import fetch_oecd_cli
        _cli = fetch_oecd_cli(["USA", "GBR", "DEU", "JPN", "CHN", "OECD"])
        if not _cli.empty:
            import plotly.graph_objects as go
            fig_cli = go.Figure()
            for col in _cli.columns:
                fig_cli.add_trace(go.Scatter(
                    x=_cli.index, y=_cli[col], mode="lines", name=col,
                ))
            fig_cli.add_hline(y=100, line_dash="dash", line_color=COLORS["text_muted"],
                               annotation_text="Expansion threshold")
            fig_cli.update_layout(
                template="plotly_dark", height=350,
                yaxis_title="CLI (100 = trend)",
                legend=dict(orientation="h", y=-0.15),
                margin=dict(l=50, r=20, t=10, b=50),
            )
            st.plotly_chart(fig_cli, use_container_width=True, config={"displayModeBar": False})

            # US CLI assessment
            if "USA" in _cli.columns and len(_cli["USA"].dropna()) >= 2:
                _us_last = float(_cli["USA"].dropna().iloc[-1])
                _us_prev = float(_cli["USA"].dropna().iloc[-2])
                _us_trend = "rising" if _us_last > _us_prev else "falling"
                if _us_last > 100:
                    st.success(f"US CLI: {_us_last:.1f} ({_us_trend}) — above trend, expansion.")
                else:
                    st.warning(f"US CLI: {_us_last:.1f} ({_us_trend}) — below trend, contraction risk.")
        else:
            st.info("OECD CLI data unavailable.")
    except Exception as e:
        st.info(f"OECD data unavailable: {e}")


# ════════════════════════════════════════
# TAB 4: FOMC STATEMENT DIFF
# ════════════════════════════════════════

# FOMC statements — policy-relevant paragraphs (8 meetings)
FOMC_STATEMENTS = {
    "March 18-19, 2026": {
        "text": "The Committee seeks to achieve maximum employment and inflation at the rate of 2 percent over the longer run. Uncertainty around the economic outlook has increased. The Committee is attentive to the risks to both sides of its dual mandate.\n\nRecent indicators suggest that economic activity has continued to expand at a solid pace. The unemployment rate has stabilized at a low level in recent months, and labor market conditions remain solid. Inflation remains somewhat elevated.\n\nIn support of its goals, the Committee decided to maintain the target range for the federal funds rate at 3-1/2 to 3-3/4 percent. In considering the extent and timing of additional adjustments to the target range for the federal funds rate, the Committee will carefully assess incoming data, the evolving outlook, and the balance of risks. The Committee is prepared to adjust the stance of monetary policy as appropriate if risks emerge that could impede the attainment of the Committee's goals.\n\nIn assessing the appropriate stance of monetary policy, the Committee will continue to monitor the implications of incoming information for the economic outlook. The Committee would be prepared to adjust the stance of monetary policy as appropriate if risks emerge that could impede the attainment of the Committee's goals. The Committee's assessments will take into account a wide range of information, including readings on labor market conditions, inflation pressures and inflation expectations, and financial and international developments.",
        "rate": "3.50-3.75%", "action": "Hold", "vote": "11-1", "dissent": "Waller (preferred cut)",
        "forward_guidance": "The Committee is prepared to adjust the stance of monetary policy as appropriate if risks emerge.",
    },
    "January 28-29, 2026": {
        "text": "The Committee seeks to achieve maximum employment and inflation at the rate of 2 percent over the longer run. The Committee judges that the risks to achieving its employment and inflation goals are roughly in balance. The economic outlook is uncertain, and the Committee is attentive to the risks to both sides of its dual mandate.\n\nRecent indicators suggest that economic activity has continued to expand at a solid pace. The unemployment rate has stabilized at a low level in recent months, and labor market conditions remain solid. Inflation has made progress toward the Committee's 2 percent objective but remains somewhat elevated.\n\nIn support of its goals, the Committee decided to maintain the target range for the federal funds rate at 3-1/2 to 3-3/4 percent. In considering the extent and timing of additional adjustments to the target range for the federal funds rate, the Committee will carefully assess incoming data, the evolving outlook, and the balance of risks. The Committee does not expect it will be appropriate to reduce the target range until it has gained greater confidence that inflation is moving sustainably toward 2 percent.\n\nIn assessing the appropriate stance of monetary policy, the Committee will continue to monitor the implications of incoming information for the economic outlook. The Committee would be prepared to adjust the stance of monetary policy as appropriate if risks emerge that could impede the attainment of the Committee's goals. The Committee's assessments will take into account a wide range of information, including readings on labor market conditions, inflation pressures and inflation expectations, and financial and international developments.",
        "rate": "3.50-3.75%", "action": "Hold", "vote": "12-0", "dissent": "None",
        "forward_guidance": "The Committee does not expect it will be appropriate to reduce the target range until it has gained greater confidence that inflation is moving sustainably toward 2 percent.",
    },
    "December 17-18, 2025": {
        "text": "The Committee seeks to achieve maximum employment and inflation at the rate of 2 percent over the longer run. The Committee judges that the risks to achieving its employment and inflation goals are roughly in balance. The economic outlook is uncertain, and the Committee is attentive to the risks to both sides of its dual mandate.\n\nRecent indicators suggest that economic activity has continued to expand at a solid pace. Labor market conditions have generally eased, and the unemployment rate has moved up but remains low. Inflation has made further progress toward the Committee's 2 percent objective but remains somewhat elevated.\n\nIn support of its goals, the Committee decided to lower the target range for the federal funds rate by 1/4 percentage point to 3-1/2 to 3-3/4 percent. In considering the extent and timing of additional adjustments to the target range for the federal funds rate, the Committee will carefully assess incoming data, the evolving outlook, and the balance of risks. The Committee judges that the risks to achieving its employment and inflation goals are roughly in balance.\n\nIn assessing the appropriate stance of monetary policy, the Committee will continue to monitor the implications of incoming information for the economic outlook. The Committee would be prepared to adjust the stance of monetary policy as appropriate if risks emerge that could impede the attainment of the Committee's goals. The Committee's assessments will take into account a wide range of information, including readings on labor market conditions, inflation pressures and inflation expectations, and financial and international developments.",
        "rate": "3.50-3.75%", "action": "Cut 25bp", "vote": "11-1", "dissent": "Hammack (preferred hold)",
        "forward_guidance": "The Committee judges that the risks to achieving its employment and inflation goals are roughly in balance.",
    },
    "November 6-7, 2025": {
        "text": "The Committee seeks to achieve maximum employment and inflation at the rate of 2 percent over the longer run. The Committee judges that the risks to achieving its employment and inflation goals are roughly in balance. The economic outlook is uncertain, and the Committee is attentive to the risks to both sides of its dual mandate.\n\nRecent indicators suggest that economic activity has continued to expand at a solid pace. Labor market conditions have generally eased, and the unemployment rate has moved up but remains low. Inflation has made progress toward the Committee's 2 percent objective but remains somewhat elevated.\n\nIn support of its goals, the Committee decided to lower the target range for the federal funds rate by 1/4 percentage point to 3-3/4 to 4 percent. In considering additional adjustments to the target range for the federal funds rate, the Committee will carefully assess incoming data, the evolving outlook, and the balance of risks.",
        "rate": "3.75-4.00%", "action": "Cut 25bp", "vote": "12-0", "dissent": "None",
        "forward_guidance": "In considering additional adjustments to the target range, the Committee will carefully assess incoming data.",
    },
    "September 17-18, 2025": {
        "text": "The Committee seeks to achieve maximum employment and inflation at the rate of 2 percent over the longer run. The Committee has gained greater confidence that inflation is moving sustainably toward 2 percent, and judges that the risks to achieving its employment and inflation goals are roughly in balance.\n\nRecent indicators suggest that economic activity has continued to expand at a solid pace. Job gains have slowed, and the unemployment rate has moved up but remains low. Inflation has made further progress toward the Committee's 2 percent objective but remains somewhat elevated.\n\nIn light of the progress on inflation and the balance of risks, the Committee decided to lower the target range for the federal funds rate by 1/2 percentage point to 4 to 4-1/4 percent. In considering additional adjustments to the target range for the federal funds rate, the Committee will carefully assess incoming data, the evolving outlook, and the balance of risks.",
        "rate": "4.00-4.25%", "action": "Cut 50bp", "vote": "11-1", "dissent": "Bowman (preferred 25bp cut)",
        "forward_guidance": "The Committee has gained greater confidence that inflation is moving sustainably toward 2 percent.",
    },
    "July 30-31, 2025": {
        "text": "The Committee seeks to achieve maximum employment and inflation at the rate of 2 percent over the longer run. The Committee judges that the risks to achieving its employment and inflation goals continue to move into better balance. The economic outlook is uncertain, and the Committee is attentive to the risks to both sides of its dual mandate.\n\nRecent indicators suggest that economic activity has continued to expand at a solid pace. Job gains have moderated, and the unemployment rate has moved up but remains low. Inflation has made some further progress toward the Committee's 2 percent objective but remains somewhat elevated.\n\nThe Committee decided to maintain the target range for the federal funds rate at 4-1/4 to 4-1/2 percent. In considering any adjustments to the target range for the federal funds rate, the Committee will carefully assess incoming data, the evolving outlook, and the balance of risks. The Committee does not expect it will be appropriate to reduce the target range until it has gained greater confidence that inflation is moving sustainably toward 2 percent.",
        "rate": "4.25-4.50%", "action": "Hold", "vote": "12-0", "dissent": "None",
        "forward_guidance": "The Committee does not expect it will be appropriate to reduce the target range until it has gained greater confidence.",
    },
    "June 11-12, 2025": {
        "text": "The Committee seeks to achieve maximum employment and inflation at the rate of 2 percent over the longer run. The Committee judges that the risks to achieving its employment and inflation goals have moved toward better balance over the past year. The economic outlook is uncertain, and the Committee remains highly attentive to inflation risks.\n\nRecent indicators suggest that economic activity has continued to expand at a solid pace. Job gains have remained strong, and the unemployment rate has remained low. Inflation has eased over the past year but remains elevated.\n\nThe Committee decided to maintain the target range for the federal funds rate at 4-1/4 to 4-1/2 percent. In considering any adjustments to the target range for the federal funds rate, the Committee will carefully assess incoming data, the evolving outlook, and the balance of risks. The Committee does not expect it will be appropriate to reduce the target range until it has gained greater confidence that inflation is moving sustainably toward 2 percent.",
        "rate": "4.25-4.50%", "action": "Hold", "vote": "12-0", "dissent": "None",
        "forward_guidance": "The Committee remains highly attentive to inflation risks.",
    },
    "May 6-7, 2025": {
        "text": "The Committee seeks to achieve maximum employment and inflation at the rate of 2 percent over the longer run. Uncertainty about the economic outlook has increased further. The Committee is attentive to the risks to both sides of its dual mandate and judges that the risks of higher unemployment and higher inflation have risen.\n\nRecent indicators suggest that economic activity has continued to expand at a solid pace. The unemployment rate has stabilized at a low level in recent months, and labor market conditions remain solid. Inflation remains somewhat elevated.\n\nThe Committee decided to maintain the target range for the federal funds rate at 4-1/4 to 4-1/2 percent. In considering the extent and timing of additional adjustments to the target range for the federal funds rate, the Committee will carefully assess incoming data, the evolving outlook, and the balance of risks.",
        "rate": "4.25-4.50%", "action": "Hold", "vote": "12-0", "dissent": "None",
        "forward_guidance": "The risks of higher unemployment and higher inflation have risen.",
    },
}

# Hawkish/Dovish word lists for scoring
_HAWKISH_WORDS = ["elevated", "restrictive", "tightening", "attentive to inflation", "higher inflation",
                   "not expect it will be appropriate to reduce", "uncertainty has increased",
                   "risks have risen", "highly attentive to inflation risks"]
_DOVISH_WORDS = ["progress", "eased", "greater confidence", "moving sustainably toward 2 percent",
                  "roughly in balance", "better balance", "lower the target range", "decided to lower",
                  "gained greater confidence"]

# Meeting metadata for market reaction
FOMC_METADATA = {
    "March 18-19, 2026": {"date": "2026-03-19", "spy_1d": -1.2, "tlt_1d": 0.8, "dxy_1d": -0.3},
    "January 28-29, 2026": {"date": "2026-01-29", "spy_1d": 0.5, "tlt_1d": -0.4, "dxy_1d": 0.2},
    "December 17-18, 2025": {"date": "2025-12-18", "spy_1d": -2.9, "tlt_1d": -1.8, "dxy_1d": 1.1},
    "November 6-7, 2025": {"date": "2025-11-07", "spy_1d": 0.7, "tlt_1d": 0.3, "dxy_1d": -0.5},
    "September 17-18, 2025": {"date": "2025-09-18", "spy_1d": 1.7, "tlt_1d": 1.1, "dxy_1d": -0.8},
    "July 30-31, 2025": {"date": "2025-07-31", "spy_1d": 1.6, "tlt_1d": -0.3, "dxy_1d": 0.1},
    "June 11-12, 2025": {"date": "2025-06-12", "spy_1d": 0.9, "tlt_1d": 0.2, "dxy_1d": -0.4},
    "May 6-7, 2025": {"date": "2025-05-07", "spy_1d": 0.4, "tlt_1d": -0.1, "dxy_1d": 0.3},
}

with tab_fomc_diff:
    with error_boundary("FOMC Statement Diff"):
        st.subheader("FOMC Statement Language Analysis")

        dates = list(FOMC_STATEMENTS.keys())  # newest first

        # ── Dissent tracker + rate path ──
        st.markdown("#### Meeting History & Dissents")
        _hist_rows = []
        for d in dates:
            m = FOMC_STATEMENTS[d]
            meta = FOMC_METADATA.get(d, {})
            _hist_rows.append({
                "Meeting": d.split(",")[0],
                "Action": m["action"],
                "Rate": m["rate"],
                "Vote": m["vote"],
                "Dissent": m["dissent"],
                "SPY 1D": f"{meta.get('spy_1d', 0):+.1f}%" if meta.get("spy_1d") else "—",
                "TLT 1D": f"{meta.get('tlt_1d', 0):+.1f}%" if meta.get("tlt_1d") else "—",
                "DXY 1D": f"{meta.get('dxy_1d', 0):+.1f}%" if meta.get("dxy_1d") else "—",
            })
        st.dataframe(pd.DataFrame(_hist_rows), use_container_width=True, hide_index=True)

        # ── Hawkish/Dovish score over time ──
        st.divider()
        st.markdown("#### Hawkish / Dovish Tone Score")
        st.caption("Counts hawkish vs dovish signal words in each statement. Higher = more hawkish.")

        _tone_data = []
        for d in reversed(dates):
            text = FOMC_STATEMENTS[d]["text"].lower()
            hawk = sum(1 for w in _HAWKISH_WORDS if w.lower() in text)
            dove = sum(1 for w in _DOVISH_WORDS if w.lower() in text)
            score = hawk - dove
            _tone_data.append({"meeting": d.split(",")[0], "hawk": hawk, "dove": dove, "score": score})

        _tone_df = pd.DataFrame(_tone_data)
        fig_tone = go.Figure()
        fig_tone.add_trace(go.Bar(
            x=_tone_df["meeting"], y=_tone_df["hawk"],
            name="Hawkish", marker_color=COLORS["danger"], opacity=0.7,
        ))
        fig_tone.add_trace(go.Bar(
            x=_tone_df["meeting"], y=-_tone_df["dove"],
            name="Dovish", marker_color=COLORS["success"], opacity=0.7,
        ))
        fig_tone.add_trace(go.Scatter(
            x=_tone_df["meeting"], y=_tone_df["score"],
            mode="lines+markers", name="Net Score",
            line=dict(color=COLORS["accent"], width=3), marker=dict(size=8),
        ))
        fig_tone.add_hline(y=0, line_color="white", line_width=0.5)
        fig_tone.update_layout(
            template="plotly_dark", height=300, barmode="overlay",
            margin=dict(t=10, b=0, l=0, r=0), yaxis_title="Word Count",
            hovermode="x unified", legend=dict(orientation="h", y=-0.2),
        )
        st.plotly_chart(fig_tone, use_container_width=True)

        # ── Forward guidance evolution ──
        st.divider()
        st.markdown("#### Forward Guidance Evolution")
        st.caption("The single most important sentence traders read first — tracked across meetings.")
        for d in dates:
            m = FOMC_STATEMENTS[d]
            _fg = m.get("forward_guidance", "")
            _act = m["action"]
            _act_color = COLORS["success"] if "Cut" in _act else (COLORS["danger"] if "Hike" in _act else COLORS["warning"])
            st.markdown(
                f'<div style="padding:6px 12px;margin:4px 0;border-left:3px solid {_act_color};'
                f'background:rgba({int(_act_color[1:3],16)},{int(_act_color[3:5],16)},{int(_act_color[5:7],16)},0.06);'
                f'border-radius:0 4px 4px 0;font-size:0.82rem;">'
                f'<strong style="color:{_act_color};">{d.split(",")[0]} ({_act})</strong><br>'
                f'<span style="color:#ddd;">"{_fg}"</span>'
                f'</div>', unsafe_allow_html=True,
            )

        # ── Word-level diff ──
        st.divider()
        st.markdown("#### Statement Diff")
        st.caption("Green = new language. Red = removed. Every word change signals a shift.")

        dc1, dc2 = st.columns(2)
        with dc1:
            newer_date = st.selectbox("Current Statement", dates[:-1], index=0, key="fomc_newer")
        with dc2:
            newer_idx = dates.index(newer_date)
            older_options = dates[newer_idx + 1:]
            older_date = st.selectbox("Previous Statement", older_options, index=0, key="fomc_older")

        older_text = FOMC_STATEMENTS[older_date]["text"]
        newer_text = FOMC_STATEMENTS[newer_date]["text"]

        # Word-level diff
        import difflib

        older_words = older_text.split()
        newer_words = newer_text.split()
        matcher = difflib.SequenceMatcher(None, older_words, newer_words)

        diff_html = ""
        for op, i1, i2, j1, j2 in matcher.get_opcodes():
            if op == "equal":
                diff_html += " ".join(older_words[i1:i2]) + " "
            elif op == "delete":
                removed = " ".join(older_words[i1:i2])
                diff_html += f'<span style="background:rgba(255,68,68,0.25);color:#ff6666;text-decoration:line-through;padding:1px 3px;border-radius:2px;">{removed}</span> '
            elif op == "insert":
                added = " ".join(newer_words[j1:j2])
                diff_html += f'<span style="background:rgba(0,255,150,0.2);color:#00ff96;font-weight:600;padding:1px 3px;border-radius:2px;">{added}</span> '
            elif op == "replace":
                removed = " ".join(older_words[i1:i2])
                added = " ".join(newer_words[j1:j2])
                diff_html += f'<span style="background:rgba(255,68,68,0.25);color:#ff6666;text-decoration:line-through;padding:1px 3px;border-radius:2px;">{removed}</span> '
                diff_html += f'<span style="background:rgba(0,255,150,0.2);color:#00ff96;font-weight:600;padding:1px 3px;border-radius:2px;">{added}</span> '

        st.markdown(
            f'<div style="padding:16px 20px;border:1px solid {COLORS["card_border"]};border-radius:8px;'
            f'background:{COLORS["card_bg"]};line-height:1.8;font-size:0.85rem;">'
            f'{diff_html}</div>',
            unsafe_allow_html=True,
        )

        # Key phrase tracker
        st.divider()
        st.markdown("#### Key Phrase Tracker")
        st.caption("How critical Fed phrases have evolved across statements.")

        KEY_PHRASES = [
            ("inflation", "Inflation characterization"),
            ("labor market", "Labor market assessment"),
            ("risks", "Risk balance language"),
            ("additional adjustments", "Rate path guidance"),
            ("confidence", "Inflation confidence"),
            ("uncertain", "Uncertainty language"),
            ("prepared to adjust", "Policy flexibility"),
        ]

        phrase_rows = []
        for phrase, label in KEY_PHRASES:
            for stmt_date, stmt_obj in FOMC_STATEMENTS.items():
                stmt_text = stmt_obj["text"] if isinstance(stmt_obj, dict) else stmt_obj
                sentences = [s.strip() for s in stmt_text.split(".") if phrase.lower() in s.lower()]
                if sentences:
                    phrase_rows.append({
                        "Phrase": label,
                        "Meeting": stmt_date.split(",")[0],
                        "Context": sentences[0][:120] + "..." if len(sentences[0]) > 120 else sentences[0],
                    })

        if phrase_rows:
            _pdf = pd.DataFrame(phrase_rows)
            # Pivot: phrase × meeting
            for phrase_label in [p[1] for p in KEY_PHRASES]:
                subset = _pdf[_pdf["Phrase"] == phrase_label]
                if not subset.empty:
                    with st.expander(f"**{phrase_label}**", expanded=False):
                        for _, row in subset.iterrows():
                            st.markdown(f'**{row["Meeting"]}:** {row["Context"]}')

        # AI interpretation
        st.divider()
        st.markdown("#### AI Interpretation")

        gemini_key = _get_key("GEMINI_API_KEY")
        # Store current selections in session_state so the fragment reads fresh values
        st.session_state["_fomc_older"] = older_date
        st.session_state["_fomc_newer"] = newer_date
        st.session_state["_fomc_older_text"] = older_text
        st.session_state["_fomc_newer_text"] = newer_text

        if gemini_key:
            @st.fragment
            def _fomc_ai_analysis():
                # Read from session_state (not closure) to avoid stale values
                _od = st.session_state.get("_fomc_older", "")
                _nd = st.session_state.get("_fomc_newer", "")
                _ot = st.session_state.get("_fomc_older_text", "")
                _nt = st.session_state.get("_fomc_newer_text", "")

                if st.button("Analyze Changes with Gemini", type="primary",
                             use_container_width=True, key="fomc_diff_ai"):
                    from src.ai_cache import get_cached_ai, cache_ai_response, build_cache_key
                    _cache_key = build_cache_key("fomc_diff", f"{_od}_{_nd}", "")

                    cached = get_cached_ai(_cache_key)
                    if cached:
                        st.session_state["fomc_diff_result"] = cached
                        st.toast("Loaded from AI cache")
                    else:
                        with fun_loader("ai"):
                            try:
                                from google import genai
                                from google.genai import types

                                prompt = f"""You are a Fed watcher and fixed income strategist. Analyze the language changes between two consecutive FOMC statements.

PREVIOUS ({_od}):
{_ot}

CURRENT ({_nd}):
{_nt}

Provide a structured analysis:

## Key Language Changes
For each meaningful change, explain:
- What was removed/added
- What it signals about Fed thinking
- Market implications (rates, equities, dollar, gold)

## Hawkish vs Dovish Shift
Rate the overall shift on a scale: Very Dovish (-2) to Very Hawkish (+2)
Explain why.

## Trading Implications
- Fixed income: duration positioning
- Equities: sector rotation implications
- FX: dollar direction
- Commodities: gold/oil implications

## Next Meeting Expectations
Based on this language evolution, what should we expect at the next meeting?

Be specific and actionable. Reference exact phrases that changed."""

                                client = genai.Client(api_key=gemini_key)
                                response = client.models.generate_content(
                                    model="gemini-2.5-pro",
                                    contents=prompt,
                                    config=types.GenerateContentConfig(
                                        max_output_tokens=5000,
                                        temperature=0.3,
                                    ),
                                )
                                result = response.text
                                st.session_state["fomc_diff_result"] = result
                                cache_ai_response(_cache_key, result, model="gemini-2.5-pro",
                                                   source_page="fed_macro", ticker="FED",
                                                   ttl_hours=24, cost_estimate=0.03,
                                                   prompt_summary=f"FOMC diff {_od} vs {_nd}")
                            except Exception as e:
                                st.error(f"Gemini error: {e}")

                if "fomc_diff_result" in st.session_state:
                    st.markdown(st.session_state["fomc_diff_result"])
            _fomc_ai_analysis()
        else:
            st.info("Add GEMINI_API_KEY to enable AI interpretation of FOMC changes.")


# ════════════════════════════════════════
# TAB 5: INFLATION DEEP DIVE
# ════════════════════════════════════════

_INFLATION_SERIES = [
    ("CPIAUCSL", "CPI All Items", COLORS["danger"]),
    ("CPILFESL", "Core CPI (ex Food & Energy)", COLORS["warning"]),
    ("PCEPILFE", "Core PCE (Fed's preferred)", COLORS["accent"]),
    ("CUUR0000SAH1", "Shelter", "#ad7fff"),
    ("CUUR0000SAF1", "Food", "#00ff87"),
    ("CUUR0000SETB01", "Gasoline", "#ff6b35"),
    ("CUSR0000SETA02", "Used Cars", "#ff2277"),
    ("CUSR0000SAM1", "Medical Care", "#00e0d0"),
]

with tab_inflation:
    with error_boundary("Inflation Deep Dive"):
        st.subheader("Inflation Decomposition")
        st.caption("Which components are driving inflation — and which are falling.")

        _inf_data = {}
        for sid, label, color in _INFLATION_SERIES:
            df = fetch_fred_series(fred_key, sid, limit=36)
            if not df.empty and len(df) >= 13:
                _inf_data[sid] = {"df": df, "label": label, "color": color}

        if _inf_data:
            # YoY chart
            fig_inf = go.Figure()
            for sid, info in _inf_data.items():
                df = info["df"]
                yoy = (df["value"] / df["value"].shift(12) - 1) * 100
                fig_inf.add_trace(go.Scatter(
                    x=df["date"].iloc[12:], y=yoy.iloc[12:],
                    mode="lines", name=info["label"],
                    line=dict(color=info["color"], width=2),
                ))
            fig_inf.add_hline(y=2.0, line_dash="dash", line_color=COLORS["success"],
                              annotation_text="Fed 2% Target")
            fig_inf.update_layout(template="plotly_dark", height=450,
                                   margin=dict(t=10, b=0, l=0, r=0),
                                   yaxis_title="Year-over-Year (%)", hovermode="x unified",
                                   legend=dict(orientation="h", y=-0.15))
            st.plotly_chart(fig_inf, use_container_width=True)

            # Current readings table
            st.markdown("#### Current Readings")
            inf_rows = []
            for sid, info in _inf_data.items():
                df = info["df"]
                yoy = ((df.iloc[-1]["value"] / df.iloc[-13]["value"]) - 1) * 100 if df.iloc[-13]["value"] != 0 else 0
                prev_yoy = ((df.iloc[-2]["value"] / df.iloc[-14]["value"]) - 1) * 100 if len(df) >= 14 and df.iloc[-14]["value"] != 0 else yoy
                mom = (df.iloc[-1]["value"] / df.iloc[-2]["value"] - 1) * 100 if len(df) > 1 else 0
                direction = "Falling" if yoy < prev_yoy else ("Rising" if yoy > prev_yoy else "Flat")
                inf_rows.append({
                    "Component": info["label"],
                    "YoY (%)": f"{yoy:.1f}%",
                    "MoM (%)": f"{mom:.2f}%",
                    "Direction": direction,
                    "Annualized MoM": f"{mom * 12:.1f}%",
                })
            inf_df = pd.DataFrame(inf_rows)
            st.dataframe(
                inf_df.style.apply(
                    lambda row: ["background-color: rgba(0,255,150,0.08)"] * len(row)
                    if "Falling" in str(row.get("Direction", "")) else
                    (["background-color: rgba(255,68,68,0.08)"] * len(row)
                     if "Rising" in str(row.get("Direction", "")) else [""] * len(row)),
                    axis=1),
                use_container_width=True, hide_index=True)

            # Sticky vs flexible inflation
            st.divider()
            st.markdown("#### Sticky vs Flexible Inflation")
            st.caption("Shelter and medical care are 'sticky' — slow to change. Energy and used cars are 'flexible' — quick to move.")

            _sticky = [s for s in ("CUUR0000SAH1", "CUSR0000SAM1") if s in _inf_data]
            _flex = [s for s in ("CUUR0000SETB01", "CUSR0000SETA02") if s in _inf_data]

            if _sticky and _flex:
                sc1, sc2 = st.columns(2)
                with sc1:
                    st.markdown("**Sticky Components**")
                    for sid in _sticky:
                        df = _inf_data[sid]["df"]
                        yoy = ((df.iloc[-1]["value"] / df.iloc[-13]["value"]) - 1) * 100 if df.iloc[-13]["value"] != 0 else 0
                        st.metric(_inf_data[sid]["label"], f"{yoy:.1f}% YoY")
                with sc2:
                    st.markdown("**Flexible Components**")
                    for sid in _flex:
                        df = _inf_data[sid]["df"]
                        yoy = ((df.iloc[-1]["value"] / df.iloc[-13]["value"]) - 1) * 100 if df.iloc[-13]["value"] != 0 else 0
                        st.metric(_inf_data[sid]["label"], f"{yoy:.1f}% YoY")
        else:
            st.warning("Insufficient inflation data from FRED.")


# ════════════════════════════════════════
# TAB 6: LABOR MARKET DEEP DIVE
# ════════════════════════════════════════

_LABOR_SERIES = [
    ("PAYEMS", "Nonfarm Payrolls", "change"),
    ("UNRATE", "Unemployment Rate", "level"),
    ("ICSA", "Initial Jobless Claims", "level"),
    ("JTSJOL", "JOLTS Job Openings", "level"),
    ("JTSQUR", "JOLTS Quits Rate", "level"),
    ("CES0500000003", "Avg Hourly Earnings", "level"),
    ("LNS12300060", "Prime-Age EPOP (25-54)", "level"),
    ("CIVPART", "Labor Force Participation", "level"),
]

with tab_labor:
    with error_boundary("Labor Market"):
        st.subheader("Labor Market Dashboard")
        st.caption("The indicators the Fed watches to assess 'maximum employment.'")

        _labor_data = {}
        for sid, label, calc_type in _LABOR_SERIES:
            df = fetch_fred_series(fred_key, sid, limit=60)
            if not df.empty:
                _labor_data[sid] = {"df": df, "label": label, "type": calc_type}

        if _labor_data:
            # Key metrics row
            lm1, lm2, lm3, lm4 = st.columns(4)
            if "UNRATE" in _labor_data:
                _ur = float(_labor_data["UNRATE"]["df"].iloc[-1]["value"])
                _ur_prev = float(_labor_data["UNRATE"]["df"].iloc[-2]["value"]) if len(_labor_data["UNRATE"]["df"]) > 1 else _ur
                lm1.metric("Unemployment", f"{_ur:.1f}%", f"{_ur - _ur_prev:+.1f}%")
            if "PAYEMS" in _labor_data:
                _nfp_df = _labor_data["PAYEMS"]["df"]
                _nfp_change = float(_nfp_df.iloc[-1]["value"] - _nfp_df.iloc[-2]["value"]) if len(_nfp_df) > 1 else 0
                lm2.metric("NFP Change", f"{_nfp_change:+,.0f}K")
            if "ICSA" in _labor_data:
                _claims = float(_labor_data["ICSA"]["df"].iloc[-1]["value"])
                lm3.metric("Initial Claims", f"{_claims:,.0f}")
            if "CES0500000003" in _labor_data:
                _ahe = float(_labor_data["CES0500000003"]["df"].iloc[-1]["value"])
                lm4.metric("Avg Hourly Earnings", f"${_ahe:.2f}")

            st.divider()

            # NFP monthly bars
            if "PAYEMS" in _labor_data:
                st.markdown("#### Monthly Payroll Changes")
                _nfp_df = _labor_data["PAYEMS"]["df"]
                _nfp_df["change"] = _nfp_df["value"].diff()
                _nfp_plot = _nfp_df.dropna(subset=["change"])
                if not _nfp_plot.empty:
                    fig_nfp = go.Figure()
                    _nfp_colors = [COLORS["success"] if v > 0 else COLORS["danger"] for v in _nfp_plot["change"]]
                    fig_nfp.add_trace(go.Bar(x=_nfp_plot["date"], y=_nfp_plot["change"],
                                             marker_color=_nfp_colors))
                    fig_nfp.add_hline(y=0, line_color="white", line_width=0.5)
                    fig_nfp.add_hline(y=150, line_dash="dot", line_color=COLORS["warning"],
                                      annotation_text="Strong (150K+)")
                    fig_nfp.update_layout(template="plotly_dark", height=300,
                                           margin=dict(t=10, b=0, l=0, r=0),
                                           yaxis_title="Monthly Change (Thousands)")
                    st.plotly_chart(fig_nfp, use_container_width=True)

            # JOLTS: openings vs quits
            if "JTSJOL" in _labor_data:
                st.markdown("#### Job Openings (JOLTS)")
                st.caption("Falling job openings = cooling labor market. The Fed watches this closely for demand-side slack.")
                _jolts_df = _labor_data["JTSJOL"]["df"]
                fig_jolts = go.Figure()
                fig_jolts.add_trace(go.Scatter(x=_jolts_df["date"], y=_jolts_df["value"],
                                               mode="lines", line=dict(color=COLORS["accent"], width=2),
                                               name="Job Openings"))
                if "JTSQUR" in _labor_data:
                    _quits = _labor_data["JTSQUR"]["df"]
                    fig_jolts.add_trace(go.Scatter(x=_quits["date"], y=_quits["value"] * 1000,
                                                   mode="lines", line=dict(color=COLORS["warning"], width=2),
                                                   name="Quits Rate (scaled)", yaxis="y2"))
                    fig_jolts.update_layout(yaxis2=dict(overlaying="y", side="right", showgrid=False,
                                                        title="Quits Rate"))
                fig_jolts.update_layout(template="plotly_dark", height=300,
                                         margin=dict(t=10, b=0, l=0, r=0),
                                         yaxis_title="Thousands", hovermode="x unified")
                st.plotly_chart(fig_jolts, use_container_width=True)

            # 2x2 grid for remaining indicators
            st.markdown("#### Additional Indicators")
            _grid_series = [s for s in ("LNS12300060", "CIVPART", "CES0500000003", "ICSA") if s in _labor_data]
            if _grid_series:
                _gc = st.columns(min(len(_grid_series), 2))
                for i, sid in enumerate(_grid_series):
                    with _gc[i % 2]:
                        info = _labor_data[sid]
                        df = info["df"]
                        fig = go.Figure()
                        fig.add_trace(go.Scatter(x=df["date"], y=df["value"],
                                                  mode="lines", line=dict(color=COLORS["accent"], width=2)))
                        fig.update_layout(template="plotly_dark", height=200,
                                           margin=dict(t=25, b=0, l=0, r=0),
                                           title=dict(text=info["label"], font=dict(size=11)),
                                           hovermode="x unified")
                        st.plotly_chart(fig, use_container_width=True)
        else:
            st.warning("Insufficient labor market data from FRED.")


# ════════════════════════════════════════
# TAB 7: YIELD CURVE & FINANCIAL CONDITIONS
# ════════════════════════════════════════

_YIELD_SERIES = [
    ("DGS1MO", "1M"), ("DGS3MO", "3M"), ("DGS6MO", "6M"), ("DGS1", "1Y"),
    ("DGS2", "2Y"), ("DGS3", "3Y"), ("DGS5", "5Y"), ("DGS7", "7Y"),
    ("DGS10", "10Y"), ("DGS20", "20Y"), ("DGS30", "30Y"),
]

with tab_yields:
    with error_boundary("Yield Curve"):
        st.subheader("Yield Curve & Financial Conditions")

        # Fetch yield data
        _yield_data = {}
        for sid, label in _YIELD_SERIES:
            df = fetch_fred_series(fred_key, sid, limit=260)
            if not df.empty:
                _yield_data[label] = df

        if _yield_data:
            # Current curve
            st.markdown("#### US Treasury Yield Curve")
            tenor_labels = [label for _, label in _YIELD_SERIES if label in _yield_data]
            current_yields = [float(_yield_data[label].iloc[-1]["value"]) for label in tenor_labels]

            fig_yc = go.Figure()

            # Historical curves for comparison
            for offset, name, color, dash in [(22, "1M Ago", "#888", "dot"), (66, "3M Ago", COLORS["warning"], "dot"),
                                               (252, "1Y Ago", "#ad7fff", "dot")]:
                hist_y = []
                for label in tenor_labels:
                    df = _yield_data[label]
                    if len(df) > offset:
                        hist_y.append(float(df.iloc[-(offset+1)]["value"]))
                    else:
                        hist_y.append(None)
                if any(v is not None for v in hist_y):
                    fig_yc.add_trace(go.Scatter(x=tenor_labels, y=hist_y, mode="lines+markers",
                                                 name=name, line=dict(color=color, width=1.5, dash=dash),
                                                 marker=dict(size=5)))

            fig_yc.add_trace(go.Scatter(x=tenor_labels, y=current_yields, mode="lines+markers",
                                         name="Current", line=dict(color=COLORS["accent"], width=3),
                                         marker=dict(size=8)))
            fig_yc.update_layout(template="plotly_dark", height=400,
                                  margin=dict(t=10, b=0, l=0, r=0),
                                  xaxis_title="Maturity", yaxis_title="Yield (%)",
                                  hovermode="x unified")
            st.plotly_chart(fig_yc, use_container_width=True)

            # Key spread metrics
            _2y = float(_yield_data["2Y"].iloc[-1]["value"]) if "2Y" in _yield_data else 0
            _10y = float(_yield_data["10Y"].iloc[-1]["value"]) if "10Y" in _yield_data else 0
            _30y = float(_yield_data["30Y"].iloc[-1]["value"]) if "30Y" in _yield_data else 0
            _3m = float(_yield_data["3M"].iloc[-1]["value"]) if "3M" in _yield_data else 0
            _2s10s = _10y - _2y
            _3m10y = _10y - _3m

            yc1, yc2, yc3, yc4 = st.columns(4)
            yc1.metric("2Y", f"{_2y:.2f}%")
            yc2.metric("10Y", f"{_10y:.2f}%")
            _inv_color = "inverse" if _2s10s < 0 else "normal"
            yc3.metric("2s10s Spread", f"{_2s10s:.2f}%", delta_color=_inv_color)
            _3m10y_color = "inverse" if _3m10y < 0 else "normal"
            yc4.metric("3M-10Y Spread", f"{_3m10y:.2f}%", delta_color=_3m10y_color)

            if _2s10s < 0:
                st.warning(f"**Yield curve inverted** (2s10s at {_2s10s:.2f}%). Historically precedes recessions by 12-18 months.")
            if _3m10y < 0:
                st.error(f"**3M-10Y inverted** ({_3m10y:.2f}%) — the Fed's preferred recession indicator is triggered.")

            # 2s10s spread over time
            st.markdown("#### 2s10s Spread History")
            _spread_df = fetch_fred_series(fred_key, "T10Y2Y", limit=260)
            if not _spread_df.empty:
                fig_sp = go.Figure()
                fig_sp.add_trace(go.Scatter(x=_spread_df["date"], y=_spread_df["value"],
                                             mode="lines", line=dict(color=COLORS["accent"], width=2)))
                fig_sp.add_hline(y=0, line_color="white", line_width=1)
                fig_sp.add_hrect(y0=-5, y1=0, fillcolor="rgba(255,68,68,0.1)", line_width=0)
                fig_sp.update_layout(template="plotly_dark", height=250,
                                      margin=dict(t=10, b=0, l=0, r=0),
                                      yaxis_title="Spread (%)", hovermode="x unified")
                st.plotly_chart(fig_sp, use_container_width=True)

        # Financial Conditions
        st.divider()
        st.markdown("#### Financial Conditions Index (NFCI)")
        st.caption("Positive = tight conditions (restrictive). Negative = loose conditions (accommodative). Source: Chicago Fed.")
        _nfci_df = fetch_fred_series(fred_key, "NFCI", limit=260)
        if not _nfci_df.empty:
            fig_nfci = go.Figure()
            fig_nfci.add_trace(go.Scatter(x=_nfci_df["date"], y=_nfci_df["value"],
                                           mode="lines", line=dict(color=COLORS["accent"], width=2),
                                           fill="tozeroy",
                                           fillcolor="rgba(0,209,255,0.1)"))
            fig_nfci.add_hline(y=0, line_color="white", line_width=1)
            fig_nfci.update_layout(template="plotly_dark", height=300,
                                    margin=dict(t=10, b=0, l=0, r=0),
                                    yaxis_title="NFCI", hovermode="x unified")
            st.plotly_chart(fig_nfci, use_container_width=True)

            _nfci_val = float(_nfci_df.iloc[-1]["value"])
            if _nfci_val > 0:
                st.warning(f"Financial conditions are **tight** (NFCI: {_nfci_val:.2f}). Credit stress elevated — dovish for the Fed.")
            else:
                st.success(f"Financial conditions are **loose** (NFCI: {_nfci_val:.2f}). Markets accommodative — less urgency to cut.")

        # Sahm Rule
        st.divider()
        st.markdown("#### Sahm Rule Recession Indicator")
        _sahm_df = fetch_fred_series(fred_key, "SAHMCURRENT", limit=60)
        if not _sahm_df.empty:
            _sahm_val = float(_sahm_df.iloc[-1]["value"])
            fig_sahm = go.Figure()
            fig_sahm.add_trace(go.Scatter(x=_sahm_df["date"], y=_sahm_df["value"],
                                           mode="lines", line=dict(color="#ff2277", width=2)))
            fig_sahm.add_hline(y=0.5, line_dash="dash", line_color=COLORS["danger"],
                                annotation_text="Recession Threshold (0.5)")
            fig_sahm.update_layout(template="plotly_dark", height=250,
                                    margin=dict(t=10, b=0, l=0, r=0),
                                    yaxis_title="Sahm Rule Indicator", hovermode="x unified")
            st.plotly_chart(fig_sahm, use_container_width=True)
            if _sahm_val >= 0.5:
                st.error(f"**Sahm Rule triggered** ({_sahm_val:.2f} ≥ 0.5). Historically 100% accurate recession indicator.")
            else:
                st.info(f"Sahm Rule: {_sahm_val:.2f} (below 0.5 threshold — no recession signal)")

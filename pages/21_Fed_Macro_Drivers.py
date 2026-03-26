import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import logging
from src.layout import setup_page, error_boundary, fun_loader
from src.styles import COLORS

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


# ════════════════════════════════════════
# TABS
# ════════════════════════════════════════
tab_signals, tab_trends, tab_fed, tab_sentiment = st.tabs([
    "Signal Matrix",
    "Driver Trends",
    "Fed Policy",
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

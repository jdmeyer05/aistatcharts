import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import requests
import logging
from datetime import date, timedelta, datetime
from src.layout import setup_page, error_boundary, fun_loader
from src.styles import COLORS

logger = logging.getLogger(__name__)

setup_page("18_Economic_Calendar")

st.title("Economic Calendar")
st.markdown("Upcoming macro releases, earnings, Treasury auctions, yield curve, and inflation data.")


from src.api_keys import get_secret as _get_key


# ============================
# FRED CONFIG
# ============================
FRED_RELEASES = {
    # High impact
    10: {"name": "CPI", "series": "CPIAUCSL", "impact": "High", "category": "Inflation"},
    50: {"name": "Nonfarm Payrolls (NFP)", "series": "PAYEMS", "impact": "High", "category": "Employment"},
    53: {"name": "GDP", "series": "GDP", "impact": "High", "category": "Growth"},
    21: {"name": "FOMC Minutes/Data Release", "series": "FEDFUNDS", "impact": "High", "category": "Fed"},
    9: {"name": "Retail Sales", "series": "RSAFS", "impact": "High", "category": "Consumer"},
    46: {"name": "PPI", "series": "PPIFIS", "impact": "High", "category": "Inflation"},
    # Medium impact
    13: {"name": "Industrial Production", "series": "INDPRO", "impact": "Medium", "category": "Production"},
    18: {"name": "Housing Starts", "series": "HOUST", "impact": "Medium", "category": "Housing"},
    11: {"name": "Employment Cost Index", "series": "ECI", "impact": "Medium", "category": "Employment"},
    327: {"name": "Consumer Sentiment (UMich)", "series": "UMCSENT", "impact": "Medium", "category": "Consumer"},
    22: {"name": "Existing Home Sales", "series": "EXHOSLUSM495S", "impact": "Medium", "category": "Housing"},
    86: {"name": "New Home Sales", "series": "HSN1F", "impact": "Medium", "category": "Housing"},
    15: {"name": "Durable Goods Orders", "series": "DGORDER", "impact": "Medium", "category": "Production"},
    29: {"name": "PCE Price Index", "series": "PCEPI", "impact": "High", "category": "Inflation"},
    61: {"name": "ISM Manufacturing", "series": "MANEMP", "impact": "High", "category": "Production"},
    65: {"name": "Initial Jobless Claims", "series": "ICSA", "impact": "Medium", "category": "Employment"},
    20: {"name": "Trade Balance", "series": "BOPGSTB", "impact": "Medium", "category": "Trade"},
    31: {"name": "Personal Income", "series": "PI", "impact": "Medium", "category": "Consumer"},
    14: {"name": "Capacity Utilization", "series": "TCU", "impact": "Medium", "category": "Production"},
    17: {"name": "Building Permits", "series": "PERMIT", "impact": "Medium", "category": "Housing"},
    83: {"name": "Consumer Confidence (CB)", "series": "CSCICP03USM665S", "impact": "Medium", "category": "Consumer"},
}

# Actual FOMC meeting decision dates (FRED only tracks minutes/data publication dates)
FOMC_MEETINGS_2026 = [
    "2026-01-29", "2026-03-19", "2026-05-07", "2026-06-18",
    "2026-07-30", "2026-09-17", "2026-10-29", "2026-12-10",
]

YIELD_TENORS = [
    ("DGS1MO", "1M"), ("DGS3MO", "3M"), ("DGS6MO", "6M"), ("DGS1", "1Y"),
    ("DGS2", "2Y"), ("DGS3", "3Y"), ("DGS5", "5Y"), ("DGS7", "7Y"),
    ("DGS10", "10Y"), ("DGS20", "20Y"), ("DGS30", "30Y"),
]

# Big-cap earnings filter — only show market-moving names by default
BIG_CAP_SYMBOLS = {
    "AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "NVDA", "META", "TSLA", "BRK.B", "UNH",
    "JNJ", "V", "XOM", "JPM", "WMT", "MA", "PG", "LLY", "HD", "CVX",
    "MRK", "ABBV", "KO", "PEP", "AVGO", "COST", "TMO", "MCD", "CSCO", "ACN",
    "ABT", "DHR", "NEE", "LIN", "WFC", "TXN", "PM", "AMD", "UNP", "CRM",
    "MS", "GS", "BA", "CAT", "HON", "IBM", "GE", "NFLX", "DIS", "NKE",
    "INTC", "QCOM", "AMAT", "SBUX", "LOW", "INTU", "ADP", "SYK", "BLK", "CI",
}


# ============================
# DATA FETCHERS
# ============================
@st.cache_data(ttl=3600)
def fetch_fred_calendar(fred_key: str):
    today_str = date.today().strftime("%Y-%m-%d")
    events = []
    for rid, info in FRED_RELEASES.items():
        try:
            r = requests.get(
                "https://api.stlouisfed.org/fred/release/dates",
                params={
                    "release_id": rid, "api_key": fred_key, "file_type": "json",
                    "sort_order": "asc", "include_release_dates_with_no_data": "true",
                    "realtime_start": today_str, "limit": 3,
                },
                timeout=10,
            )
            for d in r.json().get("release_dates", []):
                events.append({
                    "date": d["date"], "event": info["name"], "impact": info["impact"],
                    "category": info["category"], "series": info["series"],
                })
        except Exception as e:
            logger.warning(f"FRED release fetch failed for {info['name']}: {e}")
    # Inject actual FOMC meeting decision dates
    for fomc_date in FOMC_MEETINGS_2026:
        if fomc_date >= today_str:
            events.append({
                "date": fomc_date, "event": "FOMC Rate Decision",
                "impact": "High", "category": "Fed", "series": "FEDFUNDS",
            })
    return pd.DataFrame(events)


from src.market_data import fetch_fred_series as _fetch_fred_canonical

def fetch_fred_series(fred_key: str, series_id: str, limit: int = 60):
    """Wrapper for backward compat — delegates to src.market_data."""
    return _fetch_fred_canonical(series_id, periods=limit)


@st.cache_data(ttl=3600)
def fetch_yield_curve(fred_key: str):
    current = {}
    for sid, label in YIELD_TENORS:
        df = fetch_fred_series(fred_key, sid, limit=260)
        if not df.empty:
            current[label] = {"current": df.iloc[-1]["value"], "df": df}
    return current


@st.cache_data(ttl=3600)
def fetch_earnings_calendar(finnhub_key: str = None, from_date: str = "", to_date: str = ""):
    if not finnhub_key:
        finnhub_key = _get_key("FINNHUB_API_KEY")
    if not finnhub_key:
        return pd.DataFrame()
    try:
        r = requests.get(
            "https://finnhub.io/api/v1/calendar/earnings",
            params={"from": from_date, "to": to_date, "token": finnhub_key},
            timeout=15,
        )
        data = r.json().get("earningsCalendar", [])
        return pd.DataFrame(data) if data else pd.DataFrame()
    except Exception as e:
        logger.error(f"Finnhub earnings fetch failed: {e}")
        return pd.DataFrame()


@st.cache_data(ttl=3600)
def fetch_treasury_auctions():
    try:
        r = requests.get(
            "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/od/upcoming_auctions",
            params={"sort": "auction_date", "page[size]": 50},
            timeout=15,
        )
        data = r.json().get("data", [])
        return pd.DataFrame(data) if data else pd.DataFrame()
    except Exception as e:
        logger.error(f"Treasury auction fetch failed: {e}")
        return pd.DataFrame()


# ============================
# FETCH ALL DATA
# ============================
fred_key = _get_key("FRED_API_KEY")
finnhub_key = _get_key("FINNHUB_API_KEY")

today = date.today()
now = datetime.now()
week_end = today + timedelta(days=30)

with fun_loader("data"):
    df_econ = fetch_fred_calendar(fred_key) if fred_key else pd.DataFrame()
    df_earnings = fetch_earnings_calendar(finnhub_key, today.strftime("%Y-%m-%d"), week_end.strftime("%Y-%m-%d")) if finnhub_key else pd.DataFrame()
    df_auctions = fetch_treasury_auctions()


# ============================
# HELPER: countdown
# ============================
def _make_countdown(event_date):
    days = (event_date - today).days
    if days < 0:
        return None
    if days == 0:
        return "TODAY"
    if days == 1:
        return "Tomorrow"
    return f"in {days}d"


def _countdown_detailed(event_date):
    """Return a more detailed countdown with hours."""
    dt = datetime.combine(event_date, datetime.min.time().replace(hour=8, minute=30))
    diff = dt - now
    if diff.total_seconds() < 0:
        return "NOW", COLORS["accent"]
    hours = int(diff.total_seconds() // 3600)
    if hours < 24:
        return f"{hours}h", COLORS["danger"]
    days = hours // 24
    remaining_h = hours % 24
    if days <= 2:
        return f"{days}d {remaining_h}h", COLORS["warning"]
    return f"{days}d", COLORS["text_muted"]


# ═══════════════════════════════════════════════
# TODAY'S HERO SECTION — before tabs
# ═══════════════════════════════════════════════
with error_boundary("Today Hero"):
    today_events = []

    # Economic releases today
    if not df_econ.empty:
        df_econ_parsed = df_econ.copy()
        df_econ_parsed["date"] = pd.to_datetime(df_econ_parsed["date"])
        today_econ = df_econ_parsed[df_econ_parsed["date"].dt.date == today]
        for _, row in today_econ.iterrows():
            today_events.append({"event": row["event"], "type": "Macro", "impact": row["impact"]})

    # Earnings today — show big-cap individually, summarize the rest
    if not df_earnings.empty:
        df_earn_p = df_earnings.copy()
        df_earn_p["date"] = pd.to_datetime(df_earn_p["date"])
        today_earn = df_earn_p[df_earn_p["date"].dt.date == today]
        big_today = []
        small_count = 0
        for _, row in today_earn.iterrows():
            sym = row.get("symbol", "")
            if sym in BIG_CAP_SYMBOLS:
                big_today.append(sym)
                today_events.append({"event": f"{sym} Earnings", "type": "Earnings", "impact": "High"})
            else:
                small_count += 1
        if small_count > 0:
            today_events.append({"event": f"+{small_count} more earnings", "type": "Earnings", "impact": "Low"})

    # Treasury auctions today
    if not df_auctions.empty:
        df_auc_p = df_auctions.copy()
        df_auc_p["auction_date"] = pd.to_datetime(df_auc_p["auction_date"])
        today_auc = df_auc_p[df_auc_p["auction_date"].dt.date == today]
        for _, row in today_auc.iterrows():
            today_events.append({
                "event": f"Treasury {row['security_type']} {row['security_term']}",
                "type": "Auction", "impact": "Medium",
            })

    # Next major release countdown
    next_major = None
    if not df_econ.empty:
        df_future = df_econ_parsed[
            (df_econ_parsed["date"].dt.date >= today) &
            (df_econ_parsed["impact"] == "High")
        ].sort_values("date")
        if not df_future.empty:
            next_row = df_future.iloc[0]
            next_cd, next_color = _countdown_detailed(next_row["date"].date())
            next_major = {"event": next_row["event"], "date": next_row["date"], "countdown": next_cd, "color": next_color}

    # ── Stat cards row: Fed Rate, Latest CPI, Unemployment, 2s10s Spread ──
    if fred_key:
        def _card(label, value, sub, color=COLORS["text_primary"], border=COLORS["card_border"]):
            return (
                f'<div style="flex:1 1 120px;text-align:center;padding:10px 8px;'
                f'border:1px solid {border};border-radius:6px;background:rgba(255,255,255,0.02);">'
                f'<div style="font-size:0.65rem;color:{COLORS["text_muted"]};text-transform:uppercase;letter-spacing:0.5px;">{label}</div>'
                f'<div style="font-size:1.2rem;font-weight:700;color:{color};margin:2px 0;">{value}</div>'
                f'<div style="font-size:0.7rem;color:{COLORS["text_muted"]};">{sub}</div>'
                f'</div>'
            )
        stat_cards = []
        # Fed Funds
        df_ff_quick = fetch_fred_series(fred_key, "FEDFUNDS", limit=2)
        if not df_ff_quick.empty:
            ff_val = df_ff_quick.iloc[-1]["value"]
            stat_cards.append(_card("FED FUNDS", f"{ff_val:.2f}%", "Current target", COLORS["accent"], COLORS["accent"]))
        # CPI YoY
        df_cpi_quick = fetch_fred_series(fred_key, "CPIAUCSL", limit=14)
        if not df_cpi_quick.empty and len(df_cpi_quick) >= 13:
            cpi_yoy = ((df_cpi_quick.iloc[-1]["value"] / df_cpi_quick.iloc[-13]["value"]) - 1) * 100
            cpi_c = COLORS["danger"] if cpi_yoy > 3 else COLORS["warning"] if cpi_yoy > 2 else COLORS["success"]
            stat_cards.append(_card("CPI", f"{cpi_yoy:.1f}%", "Year-over-Year", cpi_c, cpi_c))
        # Unemployment
        df_ur_quick = fetch_fred_series(fred_key, "UNRATE", limit=2)
        if not df_ur_quick.empty:
            ur_val = df_ur_quick.iloc[-1]["value"]
            ur_c = COLORS["danger"] if ur_val > 5 else COLORS["warning"] if ur_val > 4 else COLORS["success"]
            stat_cards.append(_card("UNEMPLOYMENT", f"{ur_val:.1f}%", "Latest reading", ur_c))
        # 2s10s spread
        df_sp_quick = fetch_fred_series(fred_key, "T10Y2Y", limit=2)
        if not df_sp_quick.empty:
            sp_val = df_sp_quick.iloc[-1]["value"]
            sp_c = COLORS["danger"] if sp_val < 0 else COLORS["success"]
            sp_label = "INVERTED" if sp_val < 0 else "Normal"
            stat_cards.append(_card("2s10s SPREAD", f"{sp_val:.2f}%", sp_label, sp_c, sp_c))

        if stat_cards:
            st.markdown(
                f'<div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:10px;">{"".join(stat_cards)}</div>',
                unsafe_allow_html=True,
            )

    # ── Today's events + next major countdown ──
    type_colors_h = {"Macro": COLORS["danger"], "Earnings": COLORS["accent"], "Auction": COLORS["warning"]}
    type_icons = {"Macro": "📊", "Earnings": "💰", "Auction": "🏛️"}

    hero_cols = st.columns([3, 2])

    with hero_cols[0]:
        if today_events:
            cards_html = ""
            for ev in today_events:
                ec = type_colors_h.get(ev["type"], "#888")
                ei = type_icons.get(ev["type"], "•")
                imp_badge = f'<span style="color:{COLORS["danger"]};font-size:0.6rem;font-weight:700;border:1px solid {COLORS["danger"]};padding:1px 5px;border-radius:3px;margin-left:6px;">HIGH</span>' if ev["impact"] == "High" else ""
                cards_html += (
                    f'<div style="display:inline-block;padding:6px 12px;margin:3px;border:1px solid {ec};'
                    f'border-radius:6px;background:rgba({int(ec[1:3],16)},{int(ec[3:5],16)},{int(ec[5:7],16)},0.08);">'
                    f'{ei} <strong style="color:{ec};">{ev["event"]}</strong>{imp_badge}</div>'
                )
            st.markdown(
                f'<div style="font-size:0.75rem;color:{COLORS["text_muted"]};margin-bottom:4px;">TODAY</div>'
                f'<div style="display:flex;flex-wrap:wrap;">{cards_html}</div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f'<div style="font-size:0.75rem;color:{COLORS["text_muted"]};margin-bottom:4px;">TODAY</div>'
                f'<div style="color:{COLORS["text_muted"]};font-size:0.85rem;padding:4px 0;">No major events scheduled.</div>',
                unsafe_allow_html=True,
            )

        # Upcoming this week (next 2-3 high-impact events, not today)
        if not df_econ.empty:
            upcoming_high = df_econ_parsed[
                (df_econ_parsed["date"].dt.date > today) &
                (df_econ_parsed["date"].dt.date <= today + timedelta(days=7)) &
                (df_econ_parsed["impact"] == "High")
            ].sort_values("date").head(3)
            if not upcoming_high.empty:
                upcoming_html = ""
                for _, row in upcoming_high.iterrows():
                    cd, cd_color = _countdown_detailed(row["date"].date())
                    upcoming_html += (
                        f'<div style="display:inline-block;padding:4px 10px;margin:2px;border:1px solid {COLORS["card_border"]};'
                        f'border-radius:4px;font-size:0.8rem;">'
                        f'<span style="color:{cd_color};font-weight:700;margin-right:6px;">{cd}</span>'
                        f'{row["event"]}'
                        f'</div>'
                    )
                st.markdown(
                    f'<div style="font-size:0.7rem;color:{COLORS["text_muted"]};margin-top:8px;margin-bottom:3px;">COMING UP</div>'
                    f'<div style="display:flex;flex-wrap:wrap;">{upcoming_html}</div>',
                    unsafe_allow_html=True,
                )

    with hero_cols[1]:
        if next_major:
            st.markdown(
                f'<div style="text-align:center;padding:12px;border:1px solid {next_major["color"]};border-radius:8px;'
                f'background:rgba({int(next_major["color"][1:3],16)},{int(next_major["color"][3:5],16)},{int(next_major["color"][5:7],16)},0.05);">'
                f'<div style="font-size:0.65rem;color:{COLORS["text_muted"]};letter-spacing:0.5px;">NEXT MAJOR RELEASE</div>'
                f'<div style="font-size:2rem;font-weight:800;color:{next_major["color"]};text-shadow:0 0 10px {next_major["color"]}30;">{next_major["countdown"]}</div>'
                f'<div style="font-size:0.95rem;font-weight:600;color:#e0e0e0;">{next_major["event"]}</div>'
                f'<div style="font-size:0.7rem;color:{COLORS["text_muted"]};">{next_major["date"].strftime("%A, %b %d")}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

        # Week event count summary
        week_end_check = today + timedelta(days=6 - today.weekday())
        if not df_econ.empty:
            week_macro = len(df_econ_parsed[
                (df_econ_parsed["date"].dt.date >= today) &
                (df_econ_parsed["date"].dt.date <= week_end_check)
            ])
        else:
            week_macro = 0
        week_earn = 0
        if not df_earnings.empty:
            df_ep = df_earnings.copy()
            df_ep["date"] = pd.to_datetime(df_ep["date"])
            week_earn = len(df_ep[
                (df_ep["date"].dt.date >= today) &
                (df_ep["date"].dt.date <= week_end_check) &
                (df_ep["symbol"].isin(BIG_CAP_SYMBOLS))
            ])
        week_auc = 0
        if not df_auctions.empty:
            df_ap = df_auctions.copy()
            df_ap["auction_date"] = pd.to_datetime(df_ap["auction_date"])
            week_auc = len(df_ap[
                (df_ap["auction_date"].dt.date >= today) &
                (df_ap["auction_date"].dt.date <= week_end_check)
            ])

        st.markdown(
            f'<div style="display:flex;gap:8px;margin-top:8px;justify-content:center;">'
            f'<div style="text-align:center;padding:4px 10px;border:1px solid {COLORS["card_border"]};border-radius:4px;">'
            f'<div style="font-size:1rem;font-weight:700;color:{COLORS["danger"]};">{week_macro}</div>'
            f'<div style="font-size:0.6rem;color:{COLORS["text_muted"]};">Macro</div></div>'
            f'<div style="text-align:center;padding:4px 10px;border:1px solid {COLORS["card_border"]};border-radius:4px;">'
            f'<div style="font-size:1rem;font-weight:700;color:{COLORS["accent"]};">{week_earn}</div>'
            f'<div style="font-size:0.6rem;color:{COLORS["text_muted"]};">Earnings</div></div>'
            f'<div style="text-align:center;padding:4px 10px;border:1px solid {COLORS["card_border"]};border-radius:4px;">'
            f'<div style="font-size:1rem;font-weight:700;color:{COLORS["warning"]};">{week_auc}</div>'
            f'<div style="font-size:0.6rem;color:{COLORS["text_muted"]};">Auctions</div></div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    # Yield curve inversion alert
    if fred_key:
        yield_data = fetch_yield_curve(fred_key)
        if yield_data and "2Y" in yield_data and "10Y" in yield_data:
            spread_2_10 = yield_data["10Y"]["current"] - yield_data["2Y"]["current"]
            if spread_2_10 < 0:
                st.markdown(
                    f'<div style="background:rgba(255,68,68,0.08);border:1px solid {COLORS["danger"]};'
                    f'border-radius:6px;padding:8px 14px;margin-top:6px;">'
                    f'<strong style="color:{COLORS["danger"]};">Yield Curve Inverted</strong> — '
                    f'2s10s spread at <strong>{spread_2_10:.2f}%</strong>. '
                    f'Historically signals elevated recession risk.</div>',
                    unsafe_allow_html=True,
                )

    st.divider()


# ============================
# TABS
# ============================
tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8, tab9 = st.tabs([
    "Week at a Glance",
    "Economic Releases",
    "Yield Curve",
    "Inflation Dashboard",
    "Labor Market",
    "Macro Dashboard",
    "Earnings Calendar",
    "Treasury Auctions",
    "Surprise Tracker",
])


# ---- TAB 1: Week at a Glance ----
with tab1, error_boundary("Week at a Glance"):
    st.subheader("This Week — All Events")

    this_week_end = today + timedelta(days=(6 - today.weekday()))
    today_ts = pd.Timestamp(today)
    week_end_ts = pd.Timestamp(this_week_end)

    week_events = []

    # Economic releases
    if not df_econ.empty:
        df_econ_parsed = df_econ.copy()
        df_econ_parsed["date"] = pd.to_datetime(df_econ_parsed["date"])
        this_week_econ = df_econ_parsed[
            (df_econ_parsed["date"] >= today_ts) &
            (df_econ_parsed["date"] <= week_end_ts) &
            (df_econ_parsed["date"].dt.weekday < 5)
        ]
        for _, row in this_week_econ.iterrows():
            cd = _make_countdown(row["date"].date())
            if cd is None:
                continue
            week_events.append({
                "date_raw": row["date"],
                "Day": row["date"].strftime("%a"),
                "Date": row["date"].strftime("%b %d"),
                "Event": row["event"],
                "Type": "Macro",
                "Impact": row["impact"],
                "Countdown": cd,
            })

    # Earnings this week
    if not df_earnings.empty:
        df_earn_parsed = df_earnings.copy()
        df_earn_parsed["date"] = pd.to_datetime(df_earn_parsed["date"])
        this_week_earn = df_earn_parsed[
            (df_earn_parsed["date"] >= today_ts) &
            (df_earn_parsed["date"] <= week_end_ts) &
            (df_earn_parsed["date"].dt.weekday < 5)
        ]
        # Filter to big-cap by default
        big_earn = this_week_earn[this_week_earn["symbol"].isin(BIG_CAP_SYMBOLS)]
        if big_earn.empty and "revenueEstimate" in this_week_earn.columns:
            big_earn = this_week_earn.dropna(subset=["revenueEstimate"]).nlargest(10, "revenueEstimate")
        for _, row in big_earn.iterrows():
            cd = _make_countdown(row["date"].date())
            if cd is None:
                continue
            week_events.append({
                "date_raw": row["date"],
                "Day": row["date"].strftime("%a"),
                "Date": row["date"].strftime("%b %d"),
                "Event": f"{row['symbol']} Earnings",
                "Type": "Earnings",
                "Impact": "High" if row["symbol"] in BIG_CAP_SYMBOLS else "Medium",
                "Countdown": cd,
            })

    # Treasury auctions
    if not df_auctions.empty:
        df_auc_parsed = df_auctions.copy()
        df_auc_parsed["auction_date"] = pd.to_datetime(df_auc_parsed["auction_date"])
        this_week_auc = df_auc_parsed[
            (df_auc_parsed["auction_date"] >= today_ts) &
            (df_auc_parsed["auction_date"] <= week_end_ts) &
            (df_auc_parsed["auction_date"].dt.weekday < 5)
        ]
        for _, row in this_week_auc.iterrows():
            cd = _make_countdown(row["auction_date"].date())
            if cd is None:
                continue
            offering = row.get("offering_amt", "")
            offering_str = f" (${float(offering)/1e3:.0f}B)" if offering and str(offering).replace(".", "").isdigit() and float(offering) > 0 else ""
            week_events.append({
                "date_raw": row["auction_date"],
                "Day": row["auction_date"].strftime("%a"),
                "Date": row["auction_date"].strftime("%b %d"),
                "Event": f"Treasury {row['security_type']} {row['security_term']}{offering_str}",
                "Type": "Auction",
                "Impact": "Low",
                "Countdown": cd,
            })

    if week_events:
        df_week = pd.DataFrame(week_events).sort_values("date_raw")

        # Color-coded HTML table (no spinner)
        type_c = {"Macro": COLORS["danger"], "Earnings": COLORS["accent"], "Auction": COLORS["warning"]}
        impact_c = {"High": COLORS["danger"], "Medium": COLORS["warning"], "Low": "#888"}
        rows_html = ""
        for _, r in df_week[["Day", "Date", "Event", "Type", "Impact", "Countdown"]].iterrows():
            tc = type_c.get(r["Type"], "#888")
            ic = impact_c.get(r["Impact"], "#888")
            cd_color = COLORS["danger"] if r["Countdown"] == "TODAY" else COLORS["warning"] if r["Countdown"] == "Tomorrow" else COLORS["text_muted"]
            rows_html += (
                f'<tr style="border-bottom:1px solid {COLORS["card_border"]};">'
                f'<td style="padding:6px 8px;color:{COLORS["text_muted"]};">{r["Day"]}</td>'
                f'<td style="padding:6px 8px;">{r["Date"]}</td>'
                f'<td style="padding:6px 8px;font-weight:600;">{r["Event"]}</td>'
                f'<td style="padding:6px 8px;color:{tc};">{r["Type"]}</td>'
                f'<td style="padding:6px 8px;color:{ic};font-weight:600;">{r["Impact"]}</td>'
                f'<td style="padding:6px 8px;color:{cd_color};font-weight:700;">{r["Countdown"]}</td>'
                f'</tr>'
            )
        st.markdown(
            f'<table style="width:100%;border-collapse:collapse;font-size:0.85rem;">'
            f'<tr style="border-bottom:2px solid {COLORS["card_border"]};">'
            f'<th style="padding:6px 8px;text-align:left;color:{COLORS["text_muted"]};">Day</th>'
            f'<th style="padding:6px 8px;text-align:left;color:{COLORS["text_muted"]};">Date</th>'
            f'<th style="padding:6px 8px;text-align:left;color:{COLORS["text_muted"]};">Event</th>'
            f'<th style="padding:6px 8px;text-align:left;color:{COLORS["text_muted"]};">Type</th>'
            f'<th style="padding:6px 8px;text-align:left;color:{COLORS["text_muted"]};">Impact</th>'
            f'<th style="padding:6px 8px;text-align:left;color:{COLORS["text_muted"]};">When</th>'
            f'</tr>{rows_html}</table>',
            unsafe_allow_html=True,
        )

        # Day-by-day visual layout
        st.subheader("Week View")
        type_colors = {"Macro": COLORS["danger"], "Earnings": COLORS["accent"], "Auction": COLORS["warning"]}

        weekdays = [today + timedelta(days=i) for i in range((this_week_end - today).days + 1)
                    if (today + timedelta(days=i)).weekday() < 5]
        day_cols = st.columns(len(weekdays)) if weekdays else []

        for col, day in zip(day_cols, weekdays):
            day_label = day.strftime("%a %b %d")
            day_events_df = df_week[df_week["date_raw"].dt.date == day]

            with col:
                if day == today:
                    st.markdown(f"**:blue[{day_label}]**")
                else:
                    st.markdown(f"**{day_label}**")

                if day_events_df.empty:
                    st.caption("No events")
                else:
                    for _, ev in day_events_df.iterrows():
                        color = type_colors.get(ev["Type"], "#888")
                        icon = {"Macro": "📊", "Earnings": "💰", "Auction": "🏛️"}.get(ev["Type"], "•")
                        impact_dot = f'<span style="color:{COLORS["danger"]};">●</span> ' if ev["Impact"] == "High" else ""
                        st.markdown(
                            f'{impact_dot}{icon} {ev["Event"]}',
                            unsafe_allow_html=True,
                        )
    else:
        st.info("No events this week.")


# ---- TAB 2: Economic Releases ----
with tab2, error_boundary("Economic Releases"):
    if not df_econ.empty:
        df_econ_t2 = df_econ.copy()
        df_econ_t2["date"] = pd.to_datetime(df_econ_t2["date"])
        df_econ_t2 = df_econ_t2[df_econ_t2["date"].dt.weekday < 5].sort_values("date")

        fc1, fc2 = st.columns(2)
        with fc1:
            impact_filter = st.multiselect("Impact", ["High", "Medium"], default=["High", "Medium"])
        with fc2:
            categories = df_econ_t2["category"].unique().tolist()
            cat_filter = st.multiselect("Category", categories, default=categories)

        df_filtered = df_econ_t2[
            (df_econ_t2["impact"].isin(impact_filter)) &
            (df_econ_t2["category"].isin(cat_filter))
        ]

        if not df_filtered.empty:
            display = df_filtered[["date", "event", "impact", "category"]].copy()

            def _countdown(d):
                days = (d.date() - today).days
                if days < 0:
                    return f"{abs(days)}d ago"
                if days == 0:
                    return "TODAY"
                if days == 1:
                    return "Tomorrow"
                return f"in {days}d"

            display["countdown"] = display["date"].apply(_countdown)
            display["date"] = display["date"].dt.strftime("%a, %b %d")
            display.columns = ["Date", "Event", "Impact", "Category", "Countdown"]
            st.dataframe(display, use_container_width=True, hide_index=True)

        # Timeline — next 7 weekdays
        st.subheader("Next 7 Days")
        seven_days = pd.Timestamp(today + timedelta(days=7))
        df_7d = df_filtered[
            (df_filtered["date"] >= pd.Timestamp(today)) &
            (df_filtered["date"] <= seven_days)
        ].copy()
        df_7d = df_7d[df_7d["date"].dt.weekday < 5]

        if not df_7d.empty:
            impact_colors = {"High": COLORS["danger"], "Medium": COLORS["warning"]}
            fig_tl = go.Figure()

            plot_days = [today + timedelta(days=i) for i in range(8) if (today + timedelta(days=i)).weekday() < 5]
            for i, day in enumerate(plot_days):
                day_label = day.strftime("%a\n%b %d")
                bg_color = "rgba(0, 209, 255, 0.08)" if day == today else "rgba(255,255,255,0.03)"
                fig_tl.add_vrect(x0=i - 0.4, x1=i + 0.4, fillcolor=bg_color, line_width=0)
                fig_tl.add_annotation(
                    x=i, y=-0.3, text=day_label, showarrow=False,
                    font=dict(size=11, color="white" if day == today else "#888"),
                )

            day_counts = {}
            for _, row in df_7d.iterrows():
                day_offset = None
                for i, pd_day in enumerate(plot_days):
                    if row["date"].date() == pd_day:
                        day_offset = i
                        break
                if day_offset is None:
                    continue
                stack_idx = day_counts.get(day_offset, 0)
                day_counts[day_offset] = stack_idx + 1
                y_pos = stack_idx * 0.8 + 0.3

                color = impact_colors.get(row["impact"], "#888")
                fig_tl.add_trace(go.Scatter(
                    x=[day_offset], y=[y_pos],
                    mode="markers+text",
                    marker=dict(size=16, color=color, symbol="diamond"),
                    text=[row["event"]],
                    textposition="middle right",
                    textfont=dict(size=11, color=color),
                    showlegend=False,
                    hovertemplate=f"<b>{row['event']}</b><br>{row['date'].strftime('%a %b %d')}<br>Impact: {row['impact']}<extra></extra>",
                ))

            max_stack = max(day_counts.values()) if day_counts else 1
            fig_tl.update_layout(
                template="plotly_dark", height=max(150, 80 + max_stack * 60),
                margin=dict(t=10, b=40, l=10, r=10),
                yaxis=dict(visible=False, range=[-0.5, max_stack * 0.8 + 0.5]),
                xaxis=dict(visible=False, range=[-0.8, len(plot_days) - 0.2]),
                showlegend=False,
            )
            st.plotly_chart(fig_tl, use_container_width=True)
        else:
            st.info("No releases in the next 7 days.")

        # Historical release — actual vs estimate (surprise chart)
        st.subheader("Recent Release History")
        selected_event = st.selectbox("Select Indicator", df_filtered["event"].unique())
        series_id = df_filtered[df_filtered["event"] == selected_event]["series"].iloc[0]

        df_hist = fetch_fred_series(fred_key, series_id, limit=24)
        if not df_hist.empty:
            df_hist["change"] = df_hist["value"].diff()
            df_hist["pct_change"] = df_hist["value"].pct_change() * 100

            fig_hist = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.6, 0.4],
                                     vertical_spacing=0.05)

            # Level
            fig_hist.add_trace(go.Scatter(
                x=df_hist["date"], y=df_hist["value"],
                mode="lines+markers", line=dict(color=COLORS["accent"], width=2),
                marker=dict(size=6), name="Value",
            ), row=1, col=1)

            # Change bars
            colors_chg = [COLORS["success"] if v >= 0 else COLORS["danger"] for v in df_hist["pct_change"].fillna(0)]
            fig_hist.add_trace(go.Bar(
                x=df_hist["date"], y=df_hist["pct_change"],
                marker_color=colors_chg, name="% Change",
            ), row=2, col=1)
            fig_hist.add_hline(y=0, line_color="white", line_width=0.5, row=2, col=1)

            fig_hist.update_layout(
                template="plotly_dark", height=400, margin=dict(t=10, b=0, l=0, r=0),
                hovermode="x unified", showlegend=False,
            )
            fig_hist.update_yaxes(title_text="Value", row=1, col=1)
            fig_hist.update_yaxes(title_text="% Change", row=2, col=1)
            st.plotly_chart(fig_hist, use_container_width=True)
    else:
        st.warning("FRED API key not configured.")


# ---- TAB 3: Yield Curve ----
with tab3, error_boundary("Yield Curve"):
    if fred_key:
        st.subheader("US Treasury Yield Curve")

        yield_data = fetch_yield_curve(fred_key)
        if yield_data:
            tenor_labels = [label for _, label in YIELD_TENORS if label in yield_data]
            current_yields = [yield_data[label]["current"] for label in tenor_labels]

            fig_yc = go.Figure()

            historical_periods = [
                (22, "1 Month Ago", "#888888", "dot"),
                (66, "3 Months Ago", "#ffaa00", "dot"),
                (252, "1 Year Ago", "#ad7fff", "dot"),
            ]

            for offset, label, color, dash in historical_periods:
                hist_yields = []
                for tenor_label in tenor_labels:
                    df_t = yield_data[tenor_label]["df"]
                    if len(df_t) > offset:
                        hist_yields.append(df_t.iloc[-(offset + 1)]["value"])
                    else:
                        hist_yields.append(None)

                if any(v is not None for v in hist_yields):
                    fig_yc.add_trace(go.Scatter(
                        x=tenor_labels, y=hist_yields,
                        mode="lines+markers", name=label,
                        line=dict(color=color, width=1.5, dash=dash),
                        marker=dict(size=5),
                    ))

            fig_yc.add_trace(go.Scatter(
                x=tenor_labels, y=current_yields,
                mode="lines+markers", name="Current",
                line=dict(color=COLORS["accent"], width=3),
                marker=dict(size=8),
            ))

            fig_yc.update_layout(
                template="plotly_dark", height=450, margin=dict(t=10, b=0, l=0, r=0),
                xaxis_title="Maturity", yaxis_title="Yield (%)", hovermode="x unified",
            )
            st.plotly_chart(fig_yc, use_container_width=True)

            # Yield metrics
            yc1, yc2, yc3, yc4 = st.columns(4)
            y2 = yield_data.get("2Y", {}).get("current", 0)
            y10 = yield_data.get("10Y", {}).get("current", 0)
            y30 = yield_data.get("30Y", {}).get("current", 0)
            spread_2_10 = y10 - y2

            yc1.metric("2-Year", f"{y2:.2f}%")
            yc2.metric("10-Year", f"{y10:.2f}%")
            spread_color = "inverse" if spread_2_10 < 0 else "normal"
            yc3.metric("2s10s Spread", f"{spread_2_10:.2f}%", delta_color=spread_color)
            yc4.metric("30-Year", f"{y30:.2f}%")

            # Inversion alert inline
            if spread_2_10 < 0:
                st.markdown(
                    f'<div style="background:rgba(255,68,68,0.08);border-left:3px solid {COLORS["danger"]};'
                    f'padding:6px 12px;border-radius:3px;font-size:0.85rem;color:{COLORS["danger"]};">'
                    f'Yield curve is <strong>INVERTED</strong> — 2s10s at {spread_2_10:.2f}%. '
                    f'This has preceded every US recession since 1970.</div>',
                    unsafe_allow_html=True,
                )

            # 2s10s spread over time
            st.subheader("2s10s Spread History")
            df_spread = fetch_fred_series(fred_key, "T10Y2Y", limit=260)
            if not df_spread.empty:
                fig_spread = go.Figure()
                fig_spread.add_trace(go.Scatter(
                    x=df_spread["date"], y=df_spread["value"],
                    mode="lines", line=dict(color=COLORS["accent"], width=2),
                ))
                fig_spread.add_hline(y=0, line_dash="solid", line_color="white", line_width=1)
                fig_spread.add_hrect(y0=-5, y1=0, fillcolor="rgba(255, 75, 75, 0.1)", line_width=0)
                fig_spread.update_layout(
                    template="plotly_dark", height=300, margin=dict(t=10, b=0, l=0, r=0),
                    yaxis_title="Spread (%)", hovermode="x unified",
                )
                st.plotly_chart(fig_spread, use_container_width=True)
                st.caption("Shaded red = inverted yield curve (historically signals recession)")

            # Fed Funds Rate path
            st.subheader("Fed Funds Rate Path")
            df_ff = fetch_fred_series(fred_key, "FEDFUNDS", limit=120)
            if not df_ff.empty:
                fig_ff = go.Figure()
                fig_ff.add_trace(go.Scatter(
                    x=df_ff["date"], y=df_ff["value"],
                    mode="lines", line=dict(color=COLORS["accent"], width=2.5, shape="hv"),
                    name="Fed Funds Rate",
                ))
                if not df_econ.empty:
                    fomc_dates = df_econ[df_econ["event"] == "FOMC Rate Decision"]["date"].tolist()
                    for fd in fomc_dates:
                        fig_ff.add_vline(x=fd, line_dash="dot", line_color="rgba(255,170,0,0.5)")

                # Market-implied rate expectations (simple proxy from 2Y yield)
                if "2Y" in yield_data:
                    current_rate = df_ff.iloc[-1]["value"]
                    implied_terminal = yield_data["2Y"]["current"]
                    implied_cuts = round((current_rate - implied_terminal) / 0.25)
                    if implied_cuts != 0:
                        direction = "cuts" if implied_cuts > 0 else "hikes"
                        st.caption(f"Market-implied: ~{abs(implied_cuts)} rate {direction} priced in (2Y yield at {implied_terminal:.2f}% vs Fed Funds at {current_rate:.2f}%)")

                fig_ff.update_layout(
                    template="plotly_dark", height=300, margin=dict(t=10, b=0, l=0, r=0),
                    yaxis_title="Rate (%)", hovermode="x unified",
                )
                st.plotly_chart(fig_ff, use_container_width=True)
                st.caption("Orange dotted lines = upcoming FOMC meeting dates")
    else:
        st.warning("FRED API key not configured.")


# ---- TAB 4: Inflation Dashboard ----
with tab4, error_boundary("Inflation Dashboard"):
    if fred_key:
        st.subheader("Inflation Dashboard")

        inflation_series = [
            ("CPIAUCSL", "CPI (All Items)", COLORS["danger"]),
            ("CPILFESL", "Core CPI (ex Food & Energy)", COLORS["warning"]),
            ("PCEPI", "PCE Price Index", COLORS["accent"]),
            ("PCEPILFE", "Core PCE", COLORS["success"]),
            ("PPIFIS", "PPI (Final Demand)", "#ad7fff"),
        ]

        inf_cols = st.columns(len(inflation_series))
        yoy_data = {}

        for col, (sid, label, color) in zip(inf_cols, inflation_series):
            df_inf = fetch_fred_series(fred_key, sid, limit=24)
            if not df_inf.empty and len(df_inf) >= 13:
                latest = df_inf.iloc[-1]["value"]
                year_ago = df_inf.iloc[-13]["value"]
                yoy = ((latest / year_ago) - 1) * 100
                prev_latest = df_inf.iloc[-2]["value"]
                prev_year_ago = df_inf.iloc[-14]["value"] if len(df_inf) >= 14 else df_inf.iloc[0]["value"]
                prev_yoy = ((prev_latest / prev_year_ago) - 1) * 100
                change = yoy - prev_yoy
                col.metric(label.split("(")[0].strip(), f"{yoy:.1f}% YoY", f"{change:+.1f}%",
                           delta_color="inverse")
                yoy_data[sid] = {"df": df_inf, "label": label, "color": color}

        st.divider()

        # YoY inflation chart
        fig_inf = go.Figure()
        for sid, info in yoy_data.items():
            df_i = info["df"]
            if len(df_i) >= 13:
                yoy_series = (df_i["value"] / df_i["value"].shift(12) - 1) * 100
                fig_inf.add_trace(go.Scatter(
                    x=df_i["date"].iloc[12:], y=yoy_series.iloc[12:],
                    mode="lines", name=info["label"],
                    line=dict(color=info["color"], width=2),
                ))

        fig_inf.add_hline(y=2.0, line_dash="dash", line_color=COLORS["success"],
                          annotation_text="Fed 2% Target")
        fig_inf.update_layout(
            template="plotly_dark", height=450, margin=dict(t=10, b=0, l=0, r=0),
            yaxis_title="Year-over-Year (%)", hovermode="x unified",
        )
        st.plotly_chart(fig_inf, use_container_width=True)

        # MoM inflation
        st.subheader("Month-over-Month Change")
        fig_mom = go.Figure()

        df_cpi = yoy_data.get("CPIAUCSL", {}).get("df", pd.DataFrame())
        if not df_cpi.empty:
            mom = df_cpi["value"].pct_change() * 100
            colors_mom = [COLORS["success"] if v <= 0.2 else COLORS["warning"] if v <= 0.4 else COLORS["danger"]
                          for v in mom.fillna(0)]
            fig_mom.add_trace(go.Bar(x=df_cpi["date"], y=mom, marker_color=colors_mom, name="CPI MoM"))
            fig_mom.add_hline(y=0.167, line_dash="dot", line_color=COLORS["success"],
                              annotation_text="~2% annualized")
            fig_mom.update_layout(
                template="plotly_dark", height=300, margin=dict(t=10, b=0, l=0, r=0),
                yaxis_title="MoM Change (%)", hovermode="x unified",
            )
            st.plotly_chart(fig_mom, use_container_width=True)
            st.caption("Green < 0.2% | Yellow 0.2-0.4% | Red > 0.4%")

        # CPI components
        st.subheader("CPI Breakdown")
        cpi_components = [
            ("CUUR0000SA0", "All Items", COLORS["danger"]),
            ("CUUR0000SA0L1E", "Core (ex Food & Energy)", COLORS["warning"]),
            ("CUUR0000SAF1", "Food", COLORS["accent"]),
            ("CUUR0000SEHE01", "Shelter", "#ad7fff"),
            ("CUUR0000SETB01", "Gasoline", COLORS["success"]),
        ]
        fig_cpi_comp = go.Figure()
        for sid, label, color in cpi_components:
            df_c = fetch_fred_series(fred_key, sid, limit=24)
            if not df_c.empty and len(df_c) >= 13:
                yoy_c = (df_c["value"] / df_c["value"].shift(12) - 1) * 100
                fig_cpi_comp.add_trace(go.Scatter(
                    x=df_c["date"].iloc[12:], y=yoy_c.iloc[12:],
                    mode="lines", name=label, line=dict(color=color, width=2),
                ))

        fig_cpi_comp.add_hline(y=2.0, line_dash="dash", line_color="white", line_width=0.5)
        fig_cpi_comp.update_layout(
            template="plotly_dark", height=400, margin=dict(t=10, b=0, l=0, r=0),
            yaxis_title="YoY (%)", hovermode="x unified",
        )
        st.plotly_chart(fig_cpi_comp, use_container_width=True)
    else:
        st.warning("FRED API key not configured.")


# ---- TAB 5: Labor Market ----
with tab5, error_boundary("Labor Market"):
    if fred_key:
        st.subheader("Labor Market Dashboard")

        labor_metrics = [
            ("PAYEMS", "Nonfarm Payrolls", "K", 1),
            ("UNRATE", "Unemployment Rate", "%", 0),
            ("ICSA", "Initial Jobless Claims", "K", 1),
            ("CES0500000003", "Avg Hourly Earnings", "$", 0),
        ]

        lc = st.columns(len(labor_metrics))
        for col, (sid, label, unit, is_diff) in zip(lc, labor_metrics):
            df_l = fetch_fred_series(fred_key, sid, limit=3)
            if not df_l.empty:
                latest = df_l.iloc[-1]["value"]
                prev = df_l.iloc[-2]["value"] if len(df_l) > 1 else latest
                if is_diff:
                    change = latest - prev
                    col.metric(label, f"{latest:,.0f}{unit}", f"{change:+,.0f}")
                else:
                    change = latest - prev
                    col.metric(label, f"{latest:.1f}{unit}" if unit == "%" else f"${latest:.2f}",
                               f"{change:+.1f}{unit}" if unit == "%" else f"${change:+.2f}")

        st.divider()

        # NFP monthly change
        st.subheader("Monthly Payroll Changes")
        df_nfp = fetch_fred_series(fred_key, "PAYEMS", limit=36)
        if not df_nfp.empty:
            df_nfp["change"] = df_nfp["value"].diff()
            df_nfp_plot = df_nfp.dropna(subset=["change"])

            fig_nfp = go.Figure()
            colors_nfp = [COLORS["success"] if v > 0 else COLORS["danger"] for v in df_nfp_plot["change"]]
            fig_nfp.add_trace(go.Bar(
                x=df_nfp_plot["date"], y=df_nfp_plot["change"],
                marker_color=colors_nfp,
                hovertemplate="Date: %{x|%b %Y}<br>Change: %{y:,.0f}K<extra></extra>",
            ))
            fig_nfp.add_hline(y=0, line_color="white", line_width=1)
            fig_nfp.update_layout(
                template="plotly_dark", height=350, margin=dict(t=10, b=0, l=0, r=0),
                yaxis_title="Monthly Change (Thousands)", hovermode="x unified",
            )
            st.plotly_chart(fig_nfp, use_container_width=True)

        # Labor charts 2x2
        labor_charts = [
            ("UNRATE", "Unemployment Rate (%)", COLORS["danger"]),
            ("ICSA", "Weekly Initial Jobless Claims", COLORS["warning"]),
            ("JTSJOL", "Job Openings (JOLTS, Thousands)", COLORS["accent"]),
            ("CES0500000003", "Average Hourly Earnings ($)", COLORS["success"]),
        ]

        lc1, lc2 = st.columns(2)
        lc3, lc4 = st.columns(2)
        labor_cols = [lc1, lc2, lc3, lc4]

        for col, (sid, title, color) in zip(labor_cols, labor_charts):
            with col:
                limit = 104 if sid == "ICSA" else 60
                df_lc = fetch_fred_series(fred_key, sid, limit=limit)
                if not df_lc.empty:
                    fig = go.Figure()
                    fig.add_trace(go.Scatter(
                        x=df_lc["date"], y=df_lc["value"],
                        mode="lines", line=dict(color=color, width=2),
                    ))
                    fig.update_layout(
                        template="plotly_dark", height=280,
                        margin=dict(t=30, b=0, l=0, r=0),
                        title=dict(text=title, font=dict(size=12)),
                        hovermode="x unified",
                    )
                    st.plotly_chart(fig, use_container_width=True)
    else:
        st.warning("FRED API key not configured.")


# ---- TAB 6: Macro Dashboard ----
with tab6, error_boundary("Macro Dashboard"):
    if fred_key:
        st.subheader("Key Economic Indicators")

        key_series = [
            ("FEDFUNDS", "Fed Funds Rate", "%"),
            ("UNRATE", "Unemployment", "%"),
            ("CPIAUCSL", "CPI Index", ""),
            ("GDP", "GDP", "$B"),
        ]

        cols = st.columns(len(key_series))
        for col, (sid, label, unit) in zip(cols, key_series):
            df_s = fetch_fred_series(fred_key, sid, limit=2)
            if not df_s.empty:
                latest = df_s.iloc[-1]["value"]
                prev = df_s.iloc[-2]["value"] if len(df_s) > 1 else latest
                change = latest - prev
                if unit == "%":
                    col.metric(label, f"{latest:.1f}%", f"{change:+.1f}%")
                elif unit == "$B":
                    col.metric(label, f"${latest:,.0f}B", f"{change:+,.0f}")
                else:
                    col.metric(label, f"{latest:,.1f}", f"{change:+,.1f}")

        st.divider()

        chart_series = [
            ("FEDFUNDS", "Fed Funds Rate (%)", COLORS["accent"]),
            ("UNRATE", "Unemployment Rate (%)", COLORS["danger"]),
            ("CPIAUCSL", "CPI (All Urban Consumers)", COLORS["warning"]),
            ("T10Y2Y", "10Y-2Y Treasury Spread (%)", COLORS["success"]),
        ]

        r1c1, r1c2 = st.columns(2)
        r2c1, r2c2 = st.columns(2)
        chart_cols = [r1c1, r1c2, r2c1, r2c2]

        for col, (sid, title, color) in zip(chart_cols, chart_series):
            with col:
                df_s = fetch_fred_series(fred_key, sid, limit=60)
                if not df_s.empty:
                    fig = go.Figure()
                    fig.add_trace(go.Scatter(
                        x=df_s["date"], y=df_s["value"],
                        mode="lines", line=dict(color=color, width=2),
                    ))
                    fig.update_layout(
                        template="plotly_dark", height=280,
                        margin=dict(t=30, b=0, l=0, r=0),
                        title=dict(text=title, font=dict(size=13)),
                        hovermode="x unified",
                    )
                    st.plotly_chart(fig, use_container_width=True)

        with st.expander("More Indicators"):
            extra_series = [
                ("PAYEMS", "Total Nonfarm Payrolls (Thousands)"),
                ("RSAFS", "Retail Sales (Millions $)"),
                ("INDPRO", "Industrial Production Index"),
                ("HOUST", "Housing Starts (Thousands)"),
                ("UMCSENT", "Consumer Sentiment"),
                ("DTWEXBGS", "Trade-Weighted Dollar Index"),
            ]
            for sid, title in extra_series:
                df_s = fetch_fred_series(fred_key, sid, limit=36)
                if not df_s.empty:
                    fig = go.Figure()
                    fig.add_trace(go.Scatter(
                        x=df_s["date"], y=df_s["value"],
                        mode="lines+markers", line=dict(color=COLORS["accent"], width=1.5),
                    ))
                    fig.update_layout(
                        template="plotly_dark", height=250,
                        margin=dict(t=30, b=0, l=0, r=0),
                        title=dict(text=title, font=dict(size=13)),
                        hovermode="x unified",
                    )
                    st.plotly_chart(fig, use_container_width=True)
    else:
        st.warning("FRED API key not configured.")


# ---- TAB 7: Earnings Calendar ----
with tab7, error_boundary("Earnings Calendar"):
    if not df_earnings.empty:
        st.subheader(f"Upcoming Earnings ({today.strftime('%b %d')} - {week_end.strftime('%b %d')})")

        # Filter toggle
        show_all = st.checkbox("Show all earnings (including small-cap)", value=False)

        df_earnings_t7 = df_earnings.copy()
        df_earnings_t7["date"] = pd.to_datetime(df_earnings_t7["date"])
        df_earnings_t7 = df_earnings_t7[df_earnings_t7["date"].dt.weekday < 5].sort_values(["date", "symbol"])

        if not show_all:
            # Filter to big-cap or companies with revenue estimates
            big_cap = df_earnings_t7[df_earnings_t7["symbol"].isin(BIG_CAP_SYMBOLS)]
            if big_cap.empty and "revenueEstimate" in df_earnings_t7.columns:
                big_cap = df_earnings_t7.dropna(subset=["revenueEstimate"]).nlargest(30, "revenueEstimate")
            df_earnings_t7 = big_cap if not big_cap.empty else df_earnings_t7.head(30)

        display_earn = df_earnings_t7[["date", "symbol", "epsEstimate", "epsActual", "revenueEstimate", "revenueActual", "hour"]].copy()
        display_earn["date"] = display_earn["date"].dt.strftime("%a, %b %d")
        display_earn["epsEstimate"] = display_earn["epsEstimate"].apply(lambda x: f"${x:.2f}" if pd.notna(x) else "-")
        display_earn["epsActual"] = display_earn["epsActual"].apply(lambda x: f"${x:.2f}" if pd.notna(x) else "-")
        display_earn["revenueEstimate"] = display_earn["revenueEstimate"].apply(
            lambda x: f"${x/1e9:.2f}B" if pd.notna(x) and x > 1e9 else (f"${x/1e6:.0f}M" if pd.notna(x) and x > 0 else "-"))
        display_earn["revenueActual"] = display_earn["revenueActual"].apply(
            lambda x: f"${x/1e9:.2f}B" if pd.notna(x) and x > 1e9 else (f"${x/1e6:.0f}M" if pd.notna(x) and x > 0 else "-"))

        # Beat/miss indicator
        if "epsActual" in df_earnings_t7.columns and "epsEstimate" in df_earnings_t7.columns:
            def _beat_miss(row):
                if pd.isna(row.get("epsActual")) or pd.isna(row.get("epsEstimate")):
                    return "-"
                if row["epsActual"] > row["epsEstimate"]:
                    return "BEAT"
                elif row["epsActual"] < row["epsEstimate"]:
                    return "MISS"
                return "MET"
            display_earn["Result"] = df_earnings_t7.apply(_beat_miss, axis=1)

        display_earn["hour"] = display_earn["hour"].replace({"bmo": "Pre-Market", "amc": "After-Close", "dmh": "During", "": "TBD"})
        display_earn.columns = ["Date", "Ticker", "EPS Est.", "EPS Actual", "Rev Est.", "Rev Actual", "Timing"] + (["Result"] if "Result" in display_earn.columns else [])

        st.dataframe(display_earn, use_container_width=True, hide_index=True)
        st.caption(f"Showing {len(display_earn)} earnings reports" + (" (big-cap only)" if not show_all else "") + ".")
    else:
        if not finnhub_key:
            st.warning("Finnhub API key not configured.")
        else:
            st.info("No earnings data available for this period.")


# ---- TAB 8: Treasury Auctions ----
with tab8, error_boundary("Treasury Auctions"):
    if not df_auctions.empty:
        st.subheader("Upcoming Treasury Auctions")

        # Add offering amount and format
        df_auc_display = df_auctions.copy()
        display_cols = ["auction_date", "security_type", "security_term", "announcemt_date", "issue_date"]
        if "offering_amt" in df_auc_display.columns:
            df_auc_display["offering_fmt"] = df_auc_display["offering_amt"].apply(
                lambda x: f"${float(x)/1e3:.0f}B" if x and str(x).replace(".", "").isdigit() and float(x) > 0 else "-"
            )
            display_cols.append("offering_fmt")
        display_cols.append("cusip")

        col_names = {
            "auction_date": "Auction Date", "security_type": "Type", "security_term": "Term",
            "announcemt_date": "Announcement", "issue_date": "Issue Date",
            "offering_fmt": "Size", "cusip": "CUSIP",
        }
        display_auc = df_auc_display[display_cols].rename(columns=col_names)
        st.dataframe(display_auc, use_container_width=True, hide_index=True)

        # Timeline chart
        df_auctions["auction_date"] = pd.to_datetime(df_auctions["auction_date"])
        type_colors = {
            "Bill": COLORS["accent"], "Note": COLORS["success"], "Bond": COLORS["warning"],
            "TIPS": "#ad7fff", "FRN": COLORS["danger"], "CMB": "#888888",
        }

        fig_auc = go.Figure()
        for sec_type in df_auctions["security_type"].unique():
            subset = df_auctions[df_auctions["security_type"] == sec_type]
            hover_text = []
            for _, row in subset.iterrows():
                offering = row.get("offering_amt", "")
                size_str = f"<br>Size: ${float(offering)/1e3:.0f}B" if offering and str(offering).replace(".", "").isdigit() and float(offering) > 0 else ""
                hover_text.append(f"{row['security_term']}{size_str}")
            fig_auc.add_trace(go.Scatter(
                x=subset["auction_date"], y=subset["security_type"],
                mode="markers", marker=dict(size=12, color=type_colors.get(sec_type, "#888")),
                name=sec_type,
                hovertemplate="%{x|%b %d}<br>%{y} %{text}<extra></extra>",
                text=hover_text,
            ))

        fig_auc.add_vline(x=today.isoformat(), line_dash="dot", line_color=COLORS["accent"])
        fig_auc.update_layout(
            template="plotly_dark", height=300, margin=dict(t=10, b=0, l=0, r=0),
            hovermode="closest",
        )
        st.plotly_chart(fig_auc, use_container_width=True)
    else:
        st.info("No upcoming Treasury auction data available.")


# ---- TAB 9: Surprise Tracker ----
with tab9, error_boundary("Surprise Tracker"):
    st.subheader("Economic Surprise Tracker")
    st.markdown(
        "Compares **actual economic releases** to consensus expectations. "
        "Positive surprises (beats) tend to be bullish for equities; negative surprises (misses) bearish. "
        "Persistent surprise streaks indicate economists are systematically under/over-estimating growth."
    )

    if fred_key:
        # Key indicators with clear units for surprise calculation
        surprise_indicators = [
            ("PAYEMS", "Nonfarm Payrolls", "K", "change"),     # Monthly change
            ("UNRATE", "Unemployment Rate", "%", "level"),
            ("CPIAUCSL", "CPI", "%", "yoy"),                   # YoY change
            ("RSAFS", "Retail Sales", "%", "mom"),              # MoM change
            ("INDPRO", "Industrial Production", "%", "mom"),
            ("UMCSENT", "Consumer Sentiment", "pts", "level"),
            ("HOUST", "Housing Starts", "K", "level"),
        ]

        surprise_rows = []
        for sid, name, unit, calc_type in surprise_indicators:
            df_s = fetch_fred_series(fred_key, sid, limit=26)
            min_required = 14 if calc_type == "yoy" else 4
            if df_s.empty or len(df_s) < min_required:
                continue
            df_s = df_s.sort_values("date").reset_index(drop=True)

            # Build surprise series: compare each release to "consensus" (3-month moving average prior)
            start_idx = 13 if calc_type == "yoy" else 3
            for i in range(start_idx, len(df_s)):
                if calc_type == "change":
                    actual = df_s.iloc[i]["value"] - df_s.iloc[i - 1]["value"]
                    consensus = np.mean([
                        df_s.iloc[j]["value"] - df_s.iloc[j - 1]["value"]
                        for j in range(max(1, i - 3), i)
                    ])
                elif calc_type == "yoy":
                    actual = (df_s.iloc[i]["value"] / df_s.iloc[i - 12]["value"] - 1) * 100
                    consensus = (df_s.iloc[i - 1]["value"] / df_s.iloc[i - 13]["value"] - 1) * 100
                elif calc_type == "mom":
                    actual = (df_s.iloc[i]["value"] / df_s.iloc[i - 1]["value"] - 1) * 100
                    consensus = np.mean([
                        (df_s.iloc[j]["value"] / df_s.iloc[j - 1]["value"] - 1) * 100
                        for j in range(max(1, i - 3), i)
                    ])
                else:  # level
                    actual = df_s.iloc[i]["value"]
                    consensus = np.mean([df_s.iloc[j]["value"] for j in range(i - 3, i)])

                surprise = actual - consensus
                surprise_rows.append({
                    "date": df_s.iloc[i]["date"],
                    "indicator": name,
                    "actual": actual,
                    "consensus": consensus,
                    "surprise": surprise,
                    "unit": unit,
                })

        if surprise_rows:
            df_surprise = pd.DataFrame(surprise_rows)
            df_surprise["date"] = pd.to_datetime(df_surprise["date"])

            # Latest surprise for each indicator
            st.subheader("Latest Release Surprises")
            latest = df_surprise.sort_values("date").groupby("indicator").last().reset_index()
            latest = latest.sort_values("surprise", ascending=False)

            sc = st.columns(min(len(latest), 4))
            for i, (_, row) in enumerate(latest.iterrows()):
                col = sc[i % len(sc)]
                beat_miss = "BEAT" if row["surprise"] > 0 else ("MISS" if row["surprise"] < 0 else "MET")
                # For unemployment, lower = better (invert signal)
                if row["indicator"] == "Unemployment Rate":
                    beat_miss = "BEAT" if row["surprise"] < 0 else ("MISS" if row["surprise"] > 0 else "MET")
                color = COLORS["success"] if beat_miss == "BEAT" else (COLORS["danger"] if beat_miss == "MISS" else COLORS["warning"])
                col.markdown(
                    f'<div style="text-align:center;padding:8px;border:1px solid {COLORS["card_border"]};border-radius:6px;">'
                    f'<div style="font-size:0.7rem;color:{COLORS["text_muted"]};">{row["indicator"]}</div>'
                    f'<div style="font-size:1.1rem;font-weight:700;color:{color};">{beat_miss}</div>'
                    f'<div style="font-size:0.75rem;color:{COLORS["text_muted"]};">'
                    f'Actual: {row["actual"]:.1f}{row["unit"]} | Exp: {row["consensus"]:.1f}{row["unit"]}</div>'
                    f'<div style="font-size:0.7rem;color:{color};">{row["surprise"]:+.2f} surprise</div>'
                    f'</div>', unsafe_allow_html=True,
                )

            st.divider()

            # Aggregate Surprise Index (sum of standardized surprises)
            st.subheader("Economic Surprise Index")
            st.caption(
                "Aggregates surprises across all indicators. Positive = economy beating expectations. "
                "A falling index means economists are catching up (or economy is weakening)."
            )

            # Standardize surprises per indicator
            for ind in df_surprise["indicator"].unique():
                mask = df_surprise["indicator"] == ind
                std = df_surprise.loc[mask, "surprise"].std()
                if std > 1e-10:
                    # Invert unemployment (lower = better)
                    mult = -1 if ind == "Unemployment Rate" else 1
                    df_surprise.loc[mask, "z_surprise"] = (df_surprise.loc[mask, "surprise"] / std) * mult
                else:
                    df_surprise.loc[mask, "z_surprise"] = 0
            df_surprise["z_surprise"] = df_surprise["z_surprise"].fillna(0)

            # Monthly average surprise index
            df_surprise["month"] = df_surprise["date"].dt.to_period("M")
            monthly_idx = df_surprise.groupby("month")["z_surprise"].mean().reset_index()
            monthly_idx["date"] = monthly_idx["month"].dt.to_timestamp()

            fig_surp = go.Figure()
            colors_surp = [COLORS["success"] if v > 0 else COLORS["danger"] for v in monthly_idx["z_surprise"]]
            fig_surp.add_trace(go.Bar(
                x=monthly_idx["date"], y=monthly_idx["z_surprise"],
                marker_color=colors_surp, name="Surprise Index",
                hovertemplate="Date: %{x|%b %Y}<br>Surprise Index: %{y:.2f}<extra></extra>",
            ))
            fig_surp.add_hline(y=0, line_color="white", line_width=1)
            fig_surp.update_layout(
                template="plotly_dark", height=350, margin=dict(t=10, b=0, l=0, r=0),
                yaxis_title="Surprise Index (z-score)", hovermode="x unified",
            )
            st.plotly_chart(fig_surp, use_container_width=True)

            # Current streak
            recent_z = monthly_idx["z_surprise"].iloc[-3:]
            if len(recent_z) >= 3:
                streak_positive = all(recent_z > 0)
                streak_negative = all(recent_z < 0)
                if streak_positive:
                    st.success(
                        "**3-month positive surprise streak.** Economy is consistently beating expectations. "
                        "Analysts may be behind the curve — consider positioning for continued upside."
                    )
                elif streak_negative:
                    st.warning(
                        "**3-month negative surprise streak.** Economy is consistently missing expectations. "
                        "Growth may be decelerating faster than consensus — consider defensive positioning."
                    )

            # Surprise heatmap by indicator
            st.subheader("Surprise Heatmap by Indicator")
            pivot = df_surprise.pivot_table(
                index="indicator", columns=df_surprise["date"].dt.strftime("%b %Y"),
                values="z_surprise", aggfunc="mean"
            )
            # Keep only last 6 months
            pivot = pivot[pivot.columns[-6:]] if len(pivot.columns) > 6 else pivot

            fig_heat = go.Figure(data=go.Heatmap(
                z=pivot.values,
                x=pivot.columns.tolist(),
                y=pivot.index.tolist(),
                colorscale="RdYlGn",
                zmid=0,
                text=np.round(pivot.values, 2),
                texttemplate="%{text}",
                hovertemplate="Indicator: %{y}<br>Month: %{x}<br>Surprise: %{z:.2f}σ<extra></extra>",
            ))
            fig_heat.update_layout(
                template="plotly_dark", height=300, margin=dict(t=10, b=0, l=0, r=0),
            )
            st.plotly_chart(fig_heat, use_container_width=True)

            st.caption(
                "**How to read:** Green = actual beat expectations. Red = missed. "
                "A row that is consistently one color suggests systematic mis-estimation. "
                "The surprise index uses the 3-month moving average as a proxy for consensus — "
                "actual Bloomberg/Reuters consensus surveys are behind paywalls."
            )
        else:
            st.info("Not enough historical data to compute surprises.")
    else:
        st.warning("FRED API key not configured.")

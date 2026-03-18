import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import requests
import os
import logging
from datetime import date, timedelta, datetime
from src.auth import check_auth

logger = logging.getLogger(__name__)

st.set_page_config(page_title="Economic Calendar", layout="wide", initial_sidebar_state="collapsed")
check_auth()

st.title("📅 Economic Calendar")
st.markdown("Upcoming macro releases, earnings, Treasury auctions, yield curve, and inflation data.")


def _get_key(name: str):
    key = os.environ.get(name)
    if not key:
        try:
            key = st.secrets[name]
        except Exception:
            pass
    return key


# ============================
# FRED CONFIG
# ============================
FRED_RELEASES = {
    10: {"name": "CPI", "series": "CPIAUCSL", "impact": "High", "category": "Inflation"},
    50: {"name": "Nonfarm Payrolls (NFP)", "series": "PAYEMS", "impact": "High", "category": "Employment"},
    53: {"name": "GDP", "series": "GDP", "impact": "High", "category": "Growth"},
    21: {"name": "FOMC Rate Decision", "series": "FEDFUNDS", "impact": "High", "category": "Fed"},
    9: {"name": "Retail Sales", "series": "RSAFS", "impact": "High", "category": "Consumer"},
    13: {"name": "Industrial Production", "series": "INDPRO", "impact": "Medium", "category": "Production"},
    46: {"name": "PPI", "series": "PPIFIS", "impact": "Medium", "category": "Inflation"},
    18: {"name": "Housing Starts", "series": "HOUST", "impact": "Medium", "category": "Housing"},
    11: {"name": "Employment Cost Index", "series": "ECI", "impact": "Medium", "category": "Employment"},
    327: {"name": "Consumer Sentiment (UMich)", "series": "UMCSENT", "impact": "Medium", "category": "Consumer"},
}

YIELD_TENORS = [
    ("DGS1MO", "1M"), ("DGS3MO", "3M"), ("DGS6MO", "6M"), ("DGS1", "1Y"),
    ("DGS2", "2Y"), ("DGS3", "3Y"), ("DGS5", "5Y"), ("DGS7", "7Y"),
    ("DGS10", "10Y"), ("DGS20", "20Y"), ("DGS30", "30Y"),
]


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
    return pd.DataFrame(events)


@st.cache_data(ttl=3600)
def fetch_fred_series(fred_key: str, series_id: str, limit: int = 60):
    try:
        r = requests.get(
            "https://api.stlouisfed.org/fred/series/observations",
            params={
                "series_id": series_id, "api_key": fred_key,
                "file_type": "json", "sort_order": "desc", "limit": limit,
            },
            timeout=10,
        )
        obs = r.json().get("observations", [])
        df = pd.DataFrame(obs)
        df["date"] = pd.to_datetime(df["date"])
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        return df.dropna(subset=["value"]).sort_values("date")
    except Exception as e:
        logger.error(f"FRED series fetch failed for {series_id}: {e}")
        return pd.DataFrame()


@st.cache_data(ttl=3600)
def fetch_yield_curve(fred_key: str):
    """Fetch current and historical yield curves."""
    current = {}
    for sid, label in YIELD_TENORS:
        df = fetch_fred_series(fred_key, sid, limit=260)
        if not df.empty:
            current[label] = {"current": df.iloc[-1]["value"], "df": df}
    return current


@st.cache_data(ttl=3600)
def fetch_earnings_calendar(finnhub_key: str, from_date: str, to_date: str):
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

with st.spinner("Loading economic calendar data..."):
    df_econ = fetch_fred_calendar(fred_key) if fred_key else pd.DataFrame()
    df_earnings = fetch_earnings_calendar(finnhub_key, today.strftime("%Y-%m-%d"), week_end.strftime("%Y-%m-%d")) if finnhub_key else pd.DataFrame()
    df_auctions = fetch_treasury_auctions()


# ============================
# TABS
# ============================
tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8 = st.tabs([
    "Week at a Glance",
    "Economic Releases",
    "Yield Curve",
    "Inflation Dashboard",
    "Labor Market",
    "Macro Dashboard",
    "Earnings Calendar",
    "Treasury Auctions",
])


# ---- TAB 1: Week at a Glance ----
with tab1:
    st.subheader("This Week — All Events")

    this_week_end = today + timedelta(days=(6 - today.weekday()))
    today_ts = pd.Timestamp(today)
    week_end_ts = pd.Timestamp(this_week_end)

    week_events = []

    def _make_countdown(event_date):
        days = (event_date - today).days
        if days < 0:
            return None  # filter out
        if days == 0:
            return "TODAY"
        if days == 1:
            return "Tomorrow"
        return f"in {days}d"

    # Economic releases (today through end of week only)
    if not df_econ.empty:
        df_econ_parsed = df_econ.copy()
        df_econ_parsed["date"] = pd.to_datetime(df_econ_parsed["date"])
        this_week_econ = df_econ_parsed[
            (df_econ_parsed["date"] >= today_ts) &
            (df_econ_parsed["date"] <= week_end_ts)
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

    # Earnings this week (today through end of week only)
    if not df_earnings.empty:
        df_earn_parsed = df_earnings.copy()
        df_earn_parsed["date"] = pd.to_datetime(df_earn_parsed["date"])
        this_week_earn = df_earn_parsed[
            (df_earn_parsed["date"] >= today_ts) &
            (df_earn_parsed["date"] <= week_end_ts)
        ]
        if "revenueEstimate" in this_week_earn.columns:
            top_earn = this_week_earn.dropna(subset=["revenueEstimate"]).nlargest(10, "revenueEstimate")
        else:
            top_earn = this_week_earn.head(10)
        for _, row in top_earn.iterrows():
            cd = _make_countdown(row["date"].date())
            if cd is None:
                continue
            week_events.append({
                "date_raw": row["date"],
                "Day": row["date"].strftime("%a"),
                "Date": row["date"].strftime("%b %d"),
                "Event": f"{row['symbol']} Earnings",
                "Type": "Earnings",
                "Impact": "Medium",
                "Countdown": cd,
            })

    # Treasury auctions this week (today through end of week only)
    if not df_auctions.empty:
        df_auc_parsed = df_auctions.copy()
        df_auc_parsed["auction_date"] = pd.to_datetime(df_auc_parsed["auction_date"])
        this_week_auc = df_auc_parsed[
            (df_auc_parsed["auction_date"] >= today_ts) &
            (df_auc_parsed["auction_date"] <= week_end_ts)
        ]
        for _, row in this_week_auc.iterrows():
            cd = _make_countdown(row["auction_date"].date())
            if cd is None:
                continue
            week_events.append({
                "date_raw": row["auction_date"],
                "Day": row["auction_date"].strftime("%a"),
                "Date": row["auction_date"].strftime("%b %d"),
                "Event": f"Treasury {row['security_type']} {row['security_term']}",
                "Type": "Auction",
                "Impact": "Low",
                "Countdown": cd,
            })

    if week_events:
        df_week = pd.DataFrame(week_events).sort_values("date_raw")

        # Display table (without raw date)
        st.dataframe(
            df_week[["Day", "Date", "Event", "Type", "Impact", "Countdown"]],
            use_container_width=True, hide_index=True,
        )

        # Day-by-day visual layout
        st.subheader("Week View")
        type_colors = {"Macro": "#ff4b4b", "Earnings": "#00d1ff", "Auction": "#ffaa00"}

        # Build columns for each remaining day of the week
        days_remaining = (this_week_end - today).days + 1
        day_cols = st.columns(min(days_remaining, 5))

        for i, col in enumerate(day_cols):
            day = today + timedelta(days=i)
            day_label = day.strftime("%a %b %d")
            day_events = df_week[df_week["date_raw"].dt.date == day]

            with col:
                if day == today:
                    st.markdown(f"**:blue[{day_label}]**")
                else:
                    st.markdown(f"**{day_label}**")

                if day_events.empty:
                    st.caption("No events")
                else:
                    for _, ev in day_events.iterrows():
                        color = type_colors.get(ev["Type"], "#888")
                        icon = {"Macro": "📊", "Earnings": "💰", "Auction": "🏛️"}.get(ev["Type"], "•")
                        st.markdown(f"{icon} {ev['Event']}")
    else:
        st.info("No events this week.")


# ---- TAB 2: Economic Releases with Countdown & Filters ----
with tab2:
    if not df_econ.empty:
        df_econ_t2 = df_econ.copy()
        df_econ_t2["date"] = pd.to_datetime(df_econ_t2["date"])
        df_econ_t2 = df_econ_t2.sort_values("date")

        # Filters
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
            # Add countdown — compute days from today
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

        # Timeline — next 7 days
        st.subheader("Next 7 Days")
        seven_days = pd.Timestamp(today + timedelta(days=7))
        df_7d = df_filtered[
            (df_filtered["date"] >= pd.Timestamp(today)) &
            (df_filtered["date"] <= seven_days)
        ].copy()

        if not df_7d.empty:
            impact_colors = {"High": "#ff4b4b", "Medium": "#ffaa00"}

            # Build a day-by-day layout with events stacked vertically per day
            fig_tl = go.Figure()

            # Background day columns
            for i in range(8):
                day = today + timedelta(days=i)
                day_label = day.strftime("%a\n%b %d")
                bg_color = "rgba(0, 209, 255, 0.08)" if i == 0 else "rgba(255,255,255,0.03)"
                fig_tl.add_vrect(
                    x0=i - 0.4, x1=i + 0.4, fillcolor=bg_color, line_width=0,
                )
                fig_tl.add_annotation(
                    x=i, y=-0.3, text=day_label, showarrow=False,
                    font=dict(size=11, color="white" if i == 0 else "#888"),
                )

            # Place events
            day_counts = {}  # track stacking per day
            for _, row in df_7d.iterrows():
                days_offset = (row["date"].date() - today).days
                if days_offset < 0 or days_offset > 7:
                    continue
                stack_idx = day_counts.get(days_offset, 0)
                day_counts[days_offset] = stack_idx + 1
                y_pos = stack_idx * 0.8 + 0.3

                color = impact_colors.get(row["impact"], "#888")
                fig_tl.add_trace(go.Scatter(
                    x=[days_offset], y=[y_pos],
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
                xaxis=dict(visible=False, range=[-0.8, 7.8]),
                showlegend=False,
            )
            st.plotly_chart(fig_tl, use_container_width=True)
        else:
            st.info("No releases in the next 7 days.")

        # Historical surprise chart for each major indicator
        st.subheader("Recent Release History")
        selected_event = st.selectbox("Select Indicator", df_filtered["event"].unique())
        series_id = df_filtered[df_filtered["event"] == selected_event]["series"].iloc[0]

        df_hist = fetch_fred_series(fred_key, series_id, limit=24)
        if not df_hist.empty:
            df_hist["change"] = df_hist["value"].diff()
            df_hist["pct_change"] = df_hist["value"].pct_change() * 100

            fig_hist = go.Figure()
            colors = ["#00ff96" if v >= 0 else "#ff4b4b" for v in df_hist["pct_change"].fillna(0)]
            fig_hist.add_trace(go.Bar(
                x=df_hist["date"], y=df_hist["pct_change"],
                marker_color=colors,
                hovertemplate="Date: %{x|%b %Y}<br>Change: %{y:.2f}%<extra></extra>",
            ))
            fig_hist.add_hline(y=0, line_color="white", line_width=1)
            fig_hist.update_layout(
                template="plotly_dark", height=300, margin=dict(t=10, b=0, l=0, r=0),
                yaxis_title="% Change (MoM)", hovermode="x unified",
            )
            st.plotly_chart(fig_hist, use_container_width=True)
    else:
        st.warning("FRED API key not configured.")


# ---- TAB 3: Yield Curve ----
with tab3:
    if fred_key:
        st.subheader("US Treasury Yield Curve")

        yield_data = fetch_yield_curve(fred_key)
        if yield_data:
            tenor_labels = [label for _, label in YIELD_TENORS if label in yield_data]
            current_yields = [yield_data[label]["current"] for label in tenor_labels]

            # Historical curves: 1 month ago, 3 months ago, 1 year ago
            fig_yc = go.Figure()

            # Get historical yields for comparison
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

            # Current curve (on top)
            fig_yc.add_trace(go.Scatter(
                x=tenor_labels, y=current_yields,
                mode="lines+markers", name="Current",
                line=dict(color="#00d1ff", width=3),
                marker=dict(size=8),
            ))

            fig_yc.update_layout(
                template="plotly_dark", height=450, margin=dict(t=10, b=0, l=0, r=0),
                xaxis_title="Maturity", yaxis_title="Yield (%)", hovermode="x unified",
            )
            st.plotly_chart(fig_yc, use_container_width=True)

            # Yield metrics
            yc1, yc2, yc3, yc4 = st.columns(4)
            yc1.metric("2-Year", f"{yield_data.get('2Y', {}).get('current', 0):.2f}%")
            yc2.metric("10-Year", f"{yield_data.get('10Y', {}).get('current', 0):.2f}%")
            spread_2_10 = yield_data.get("10Y", {}).get("current", 0) - yield_data.get("2Y", {}).get("current", 0)
            yc3.metric("2s10s Spread", f"{spread_2_10:.2f}%",
                       delta_color="normal" if spread_2_10 > 0 else "inverse")
            yc4.metric("30-Year", f"{yield_data.get('30Y', {}).get('current', 0):.2f}%")

            # 2s10s spread over time
            st.subheader("2s10s Spread History")
            df_spread = fetch_fred_series(fred_key, "T10Y2Y", limit=260)
            if not df_spread.empty:
                fig_spread = go.Figure()
                colors_sp = ["#00ff96" if v >= 0 else "#ff4b4b" for v in df_spread["value"]]
                fig_spread.add_trace(go.Scatter(
                    x=df_spread["date"], y=df_spread["value"],
                    mode="lines", line=dict(color="#00d1ff", width=2),
                ))
                fig_spread.add_hline(y=0, line_dash="solid", line_color="white", line_width=1)
                fig_spread.add_hrect(y0=-5, y1=0, fillcolor="rgba(255, 75, 75, 0.1)", line_width=0)
                fig_spread.update_layout(
                    template="plotly_dark", height=300, margin=dict(t=10, b=0, l=0, r=0),
                    yaxis_title="Spread (%)", hovermode="x unified",
                )
                st.plotly_chart(fig_spread, use_container_width=True)
                st.caption("Shaded red = inverted yield curve (historically signals recession)")

            # FOMC Rate path
            st.subheader("Fed Funds Rate Path")
            df_ff = fetch_fred_series(fred_key, "FEDFUNDS", limit=120)
            if not df_ff.empty:
                fig_ff = go.Figure()
                fig_ff.add_trace(go.Scatter(
                    x=df_ff["date"], y=df_ff["value"],
                    mode="lines", line=dict(color="#00d1ff", width=2.5, shape="hv"),
                    name="Fed Funds Rate",
                ))
                # Mark FOMC dates
                if not df_econ.empty:
                    fomc_dates = df_econ[df_econ["event"] == "FOMC Rate Decision"]["date"].tolist()
                    for fd in fomc_dates:
                        fig_ff.add_vline(x=fd, line_dash="dot", line_color="rgba(255,170,0,0.5)")

                fig_ff.update_layout(
                    template="plotly_dark", height=300, margin=dict(t=10, b=0, l=0, r=0),
                    yaxis_title="Rate (%)", hovermode="x unified",
                )
                st.plotly_chart(fig_ff, use_container_width=True)
                st.caption("Orange dotted lines = upcoming FOMC meeting dates")
    else:
        st.warning("FRED API key not configured.")


# ---- TAB 4: Inflation Dashboard ----
with tab4:
    if fred_key:
        st.subheader("Inflation Dashboard")

        # Compute YoY rates
        inflation_series = [
            ("CPIAUCSL", "CPI (All Items)", "#ff4b4b"),
            ("CPILFESL", "Core CPI (ex Food & Energy)", "#ffaa00"),
            ("PCEPI", "PCE Price Index", "#00d1ff"),
            ("PCEPILFE", "Core PCE", "#00ff96"),
            ("PPIFIS", "PPI (Final Demand)", "#ad7fff"),
        ]

        # Metrics row
        inf_cols = st.columns(len(inflation_series))
        yoy_data = {}

        for col, (sid, label, color) in zip(inf_cols, inflation_series):
            df_inf = fetch_fred_series(fred_key, sid, limit=24)
            if not df_inf.empty and len(df_inf) >= 13:
                latest = df_inf.iloc[-1]["value"]
                year_ago = df_inf.iloc[-13]["value"] if len(df_inf) >= 13 else df_inf.iloc[0]["value"]
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

        # Fed 2% target
        fig_inf.add_hline(y=2.0, line_dash="dash", line_color="#00ff96",
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
            colors_mom = ["#00ff96" if v <= 0.2 else "#ffaa00" if v <= 0.4 else "#ff4b4b"
                          for v in mom.fillna(0)]
            fig_mom.add_trace(go.Bar(
                x=df_cpi["date"], y=mom, marker_color=colors_mom, name="CPI MoM"
            ))
            fig_mom.add_hline(y=0.167, line_dash="dot", line_color="#00ff96",
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
            ("CUUR0000SA0", "All Items", "#ff4b4b"),
            ("CUUR0000SA0L1E", "Core (ex Food & Energy)", "#ffaa00"),
            ("CUUR0000SAF1", "Food", "#00d1ff"),
            ("CUUR0000SEHE01", "Shelter", "#ad7fff"),
            ("CUUR0000SETB01", "Gasoline", "#00ff96"),
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
with tab5:
    if fred_key:
        st.subheader("Labor Market Dashboard")

        # Metrics
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
            colors_nfp = ["#00ff96" if v > 0 else "#ff4b4b" for v in df_nfp_plot["change"]]
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

        # Unemployment & labor charts
        labor_charts = [
            ("UNRATE", "Unemployment Rate (%)", "#ff4b4b"),
            ("ICSA", "Weekly Initial Jobless Claims", "#ffaa00"),
            ("JTSJOL", "Job Openings (JOLTS, Thousands)", "#00d1ff"),
            ("CES0500000003", "Average Hourly Earnings ($)", "#00ff96"),
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
with tab6:
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
            ("FEDFUNDS", "Fed Funds Rate (%)", "#00d1ff"),
            ("UNRATE", "Unemployment Rate (%)", "#ff4b4b"),
            ("CPIAUCSL", "CPI (All Urban Consumers)", "#ffaa00"),
            ("T10Y2Y", "10Y-2Y Treasury Spread (%)", "#00ff96"),
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
                        mode="lines+markers", line=dict(color="#00d1ff", width=1.5),
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
with tab7:
    if not df_earnings.empty:
        st.subheader(f"Upcoming Earnings ({today.strftime('%b %d')} - {week_end.strftime('%b %d')})")

        df_earnings["date"] = pd.to_datetime(df_earnings["date"])
        df_earnings = df_earnings.sort_values(["date", "symbol"])

        display_earn = df_earnings[["date", "symbol", "epsEstimate", "epsActual", "revenueEstimate", "revenueActual", "hour"]].copy()
        display_earn["date"] = display_earn["date"].dt.strftime("%a, %b %d")
        display_earn["epsEstimate"] = display_earn["epsEstimate"].apply(lambda x: f"${x:.2f}" if pd.notna(x) else "-")
        display_earn["epsActual"] = display_earn["epsActual"].apply(lambda x: f"${x:.2f}" if pd.notna(x) else "-")
        display_earn["revenueEstimate"] = display_earn["revenueEstimate"].apply(
            lambda x: f"${x/1e9:.2f}B" if pd.notna(x) and x > 1e9 else (f"${x/1e6:.0f}M" if pd.notna(x) and x > 0 else "-"))
        display_earn["revenueActual"] = display_earn["revenueActual"].apply(
            lambda x: f"${x/1e9:.2f}B" if pd.notna(x) and x > 1e9 else (f"${x/1e6:.0f}M" if pd.notna(x) and x > 0 else "-"))
        display_earn["hour"] = display_earn["hour"].replace({"bmo": "Pre-Market", "amc": "After-Close", "dmh": "During", "": "TBD"})
        display_earn.columns = ["Date", "Ticker", "EPS Est.", "EPS Actual", "Rev Est.", "Rev Actual", "Timing"]

        st.dataframe(display_earn, use_container_width=True, hide_index=True)
        st.caption(f"Showing {len(df_earnings)} earnings reports.")
    else:
        if not finnhub_key:
            st.warning("Finnhub API key not configured.")
        else:
            st.info("No earnings data available for this period.")


# ---- TAB 8: Treasury Auctions ----
with tab8:
    if not df_auctions.empty:
        st.subheader("Upcoming Treasury Auctions")

        display_auc = df_auctions[["auction_date", "security_type", "security_term",
                                     "announcemt_date", "issue_date", "cusip"]].copy()
        display_auc.columns = ["Auction Date", "Type", "Term", "Announcement", "Issue Date", "CUSIP"]
        st.dataframe(display_auc, use_container_width=True, hide_index=True)

        df_auctions["auction_date"] = pd.to_datetime(df_auctions["auction_date"])
        type_colors = {
            "Bill": "#00d1ff", "Note": "#00ff96", "Bond": "#ffaa00",
            "TIPS": "#ad7fff", "FRN": "#ff4b4b", "CMB": "#888888",
        }

        fig_auc = go.Figure()
        for sec_type in df_auctions["security_type"].unique():
            subset = df_auctions[df_auctions["security_type"] == sec_type]
            fig_auc.add_trace(go.Scatter(
                x=subset["auction_date"], y=subset["security_type"],
                mode="markers", marker=dict(size=12, color=type_colors.get(sec_type, "#888")),
                name=sec_type,
                hovertemplate="%{x|%b %d}<br>%{y} %{text}<extra></extra>",
                text=subset["security_term"],
            ))

        fig_auc.add_vline(x=today.isoformat(), line_dash="dot", line_color="#00d1ff")
        fig_auc.update_layout(
            template="plotly_dark", height=300, margin=dict(t=10, b=0, l=0, r=0),
            hovermode="closest",
        )
        st.plotly_chart(fig_auc, use_container_width=True)
    else:
        st.info("No upcoming Treasury auction data available.")

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import requests
import os
import logging
import time
from datetime import date, timedelta
from src.auth import check_auth
from src.data_engine import fetch_massive_data

logger = logging.getLogger(__name__)

st.set_page_config(page_title="Iran Conflict Monitor", layout="wide", initial_sidebar_state="collapsed")
check_auth()

st.title("🛡️ Iran Conflict Monitor")
st.markdown("Geopolitical risk tracking via GDELT media intensity, oil price correlation, and defense/energy market impact.")


def _get_eia_key():
    key = os.environ.get("EIA_API_KEY")
    if not key:
        try:
            key = st.secrets["EIA_API_KEY"]
        except Exception:
            pass
    return key


@st.cache_data(ttl=3600)
def fetch_gdelt_timeline(query: str, timespan: str = "180d"):
    """Fetch media volume intensity timeline from GDELT."""
    try:
        r = requests.get(
            "https://api.gdeltproject.org/api/v2/doc/doc",
            params={
                "query": query,
                "mode": "TimelineVol",
                "format": "json",
                "TIMESPAN": timespan,
            },
            timeout=30,
        )
        if r.status_code == 200:
            data = r.json()
            tl = data.get("timeline", [])
            if tl and tl[0].get("data"):
                df = pd.DataFrame(tl[0]["data"])
                df["date"] = pd.to_datetime(df["date"])
                df = df.sort_values("date")
                return df
    except Exception as e:
        logger.warning(f"GDELT fetch failed for '{query}': {e}")
    return pd.DataFrame()


@st.cache_data(ttl=3600)
def fetch_gdelt_tone(query: str, timespan: str = "180d"):
    """Fetch media tone timeline from GDELT (positive/negative sentiment)."""
    try:
        r = requests.get(
            "https://api.gdeltproject.org/api/v2/doc/doc",
            params={
                "query": query,
                "mode": "TimelineTone",
                "format": "json",
                "TIMESPAN": timespan,
            },
            timeout=30,
        )
        if r.status_code == 200:
            data = r.json()
            tl = data.get("timeline", [])
            if tl:
                for series in tl:
                    if series.get("series") == "Tone":
                        df = pd.DataFrame(series["data"])
                        df["date"] = pd.to_datetime(df["date"])
                        df = df.sort_values("date")
                        return df
    except Exception as e:
        logger.warning(f"GDELT tone fetch failed for '{query}': {e}")
    return pd.DataFrame()


@st.cache_data(ttl=3600)
def fetch_eia_oil_price(eia_key: str):
    """Fetch WTI spot price from EIA."""
    try:
        r = requests.get(
            f"https://api.eia.gov/v2/seriesid/PET.RWTC.D?api_key={eia_key}",
            timeout=30,
        )
        data = r.json()
        raw = data["response"]["data"]
        df = pd.DataFrame(raw)
        df["period"] = pd.to_datetime(df["period"])
        df["value"] = pd.to_numeric(df["value"])
        df = df.sort_values("period")
        return df.tail(180)
    except Exception as e:
        logger.error(f"EIA oil price fetch failed: {e}")
        return pd.DataFrame()


# --- FETCH DATA ---
with st.spinner("Loading geopolitical data from GDELT..."):
    # Core conflict queries
    gdelt_queries = {
        "Iran Military/War": "Iran war military attack",
        "Iran-Israel": "Iran Israel strike",
        "Strait of Hormuz": "Strait Hormuz shipping oil",
        "Iran Nuclear": "Iran nuclear weapon program",
        "Iran Sanctions": "Iran sanctions oil embargo",
    }

    gdelt_data = {}
    for label, query in gdelt_queries.items():
        df = fetch_gdelt_timeline(query)
        if not df.empty:
            gdelt_data[label] = df
        time.sleep(1.5)  # GDELT rate limit

    # Tone for main conflict query
    df_tone = fetch_gdelt_tone("Iran war military attack")

    # Oil price
    eia_key = _get_eia_key()
    df_oil = fetch_eia_oil_price(eia_key) if eia_key else pd.DataFrame()

    # Defense & energy stocks
    defense_tickers = {"LMT": "Lockheed Martin", "RTX": "RTX Corp", "NOC": "Northrop Grumman"}
    energy_tickers = {"XLE": "Energy ETF", "USO": "Oil ETF", "XOP": "Oil & Gas E&P ETF"}

    stock_data = {}
    for ticker in list(defense_tickers.keys()) + list(energy_tickers.keys()):
        px = fetch_massive_data(ticker, 180)
        if px is not None and not px.empty:
            stock_data[ticker] = px


# --- ESCALATION METRICS ---
main_data = gdelt_data.get("Iran Military/War")

if main_data is not None and not main_data.empty:
    latest_vol = main_data["value"].iloc[-1]
    avg_7d = main_data["value"].tail(7).mean()
    avg_30d = main_data["value"].tail(30).mean()
    avg_90d = main_data["value"].tail(90).mean()
    trend_7v30 = ((avg_7d / avg_30d) - 1) * 100 if avg_30d > 0 else 0
    peak_vol = main_data["value"].max()
    peak_date = main_data.loc[main_data["value"].idxmax(), "date"]

    st.subheader("Escalation Dashboard")
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Today's Media Intensity", f"{latest_vol:.4f}")
    c2.metric("7-Day Average", f"{avg_7d:.4f}", f"{trend_7v30:+.1f}% vs 30d",
              delta_color="inverse")
    c3.metric("30-Day Average", f"{avg_30d:.4f}")
    c4.metric("90-Day Average", f"{avg_90d:.4f}")
    c5.metric("Peak Intensity", f"{peak_vol:.4f}", f"{peak_date.strftime('%b %d')}")

    # Escalation status
    if trend_7v30 > 50:
        st.error("ESCALATION ALERT: 7-day media intensity is significantly above 30-day baseline.")
    elif trend_7v30 > 20:
        st.warning("Elevated: Media intensity trending above baseline.")
    elif trend_7v30 < -20:
        st.success("De-escalation: Media intensity declining from baseline.")

    st.divider()


# --- TABS ---
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "Conflict Intensity",
    "Topic Tracker",
    "Oil Price Correlation",
    "Market Impact",
    "Sentiment Analysis",
])


# ---- TAB 1: Conflict Intensity Timeline ----
with tab1:
    if main_data is not None and not main_data.empty:
        st.subheader("Iran Conflict Media Intensity (6 Months)")

        fig_main = go.Figure()

        # Main intensity
        fig_main.add_trace(go.Scatter(
            x=main_data["date"], y=main_data["value"],
            mode="lines", name="Daily Intensity",
            line=dict(color="#ff4b4b", width=1.5),
            fill="tozeroy", fillcolor="rgba(255, 75, 75, 0.1)",
        ))

        # 7-day rolling average
        main_data_plot = main_data.copy()
        main_data_plot["ma7"] = main_data_plot["value"].rolling(7).mean()
        main_data_plot["ma30"] = main_data_plot["value"].rolling(30).mean()

        fig_main.add_trace(go.Scatter(
            x=main_data_plot["date"], y=main_data_plot["ma7"],
            mode="lines", name="7-Day MA",
            line=dict(color="#ffaa00", width=2),
        ))
        fig_main.add_trace(go.Scatter(
            x=main_data_plot["date"], y=main_data_plot["ma30"],
            mode="lines", name="30-Day MA",
            line=dict(color="#00d1ff", width=2, dash="dash"),
        ))

        fig_main.update_layout(
            template="plotly_dark", height=450, margin=dict(t=10, b=0, l=0, r=0),
            yaxis_title="GDELT Volume Intensity", hovermode="x unified",
        )
        st.plotly_chart(fig_main, use_container_width=True)

        # Dual axis with oil
        if not df_oil.empty:
            st.subheader("Conflict Intensity vs. WTI Crude Price")
            fig_dual = go.Figure()

            fig_dual.add_trace(go.Scatter(
                x=main_data["date"], y=main_data["value"],
                mode="lines", name="Conflict Intensity",
                line=dict(color="#ff4b4b", width=2), yaxis="y",
            ))

            fig_dual.add_trace(go.Scatter(
                x=df_oil["period"], y=df_oil["value"],
                mode="lines", name="WTI Crude ($/bbl)",
                line=dict(color="#00ff96", width=2), yaxis="y2",
            ))

            fig_dual.update_layout(
                template="plotly_dark", height=450, margin=dict(t=10, b=0, l=0, r=0),
                hovermode="x unified",
                yaxis=dict(title="Conflict Intensity", side="left", showgrid=False),
                yaxis2=dict(title="WTI ($/bbl)", side="right", overlaying="y", showgrid=False),
            )
            st.plotly_chart(fig_dual, use_container_width=True)
    else:
        st.warning("GDELT data unavailable. API may be rate-limited — try refreshing in a minute.")


# ---- TAB 2: Topic Tracker ----
with tab2:
    st.subheader("Sub-Topic Media Intensity")
    st.markdown("Track individual conflict dimensions to identify which risks are driving headlines.")

    if gdelt_data:
        topic_colors = {
            "Iran Military/War": "#ff4b4b",
            "Iran-Israel": "#00d1ff",
            "Strait of Hormuz": "#ffaa00",
            "Iran Nuclear": "#ad7fff",
            "Iran Sanctions": "#00ff96",
        }

        fig_topics = go.Figure()
        for label, df_t in gdelt_data.items():
            # 7-day smoothing for readability
            df_t_plot = df_t.copy()
            df_t_plot["smooth"] = df_t_plot["value"].rolling(7, min_periods=1).mean()
            fig_topics.add_trace(go.Scatter(
                x=df_t_plot["date"], y=df_t_plot["smooth"],
                mode="lines", name=label,
                line=dict(color=topic_colors.get(label, "white"), width=2),
            ))

        fig_topics.update_layout(
            template="plotly_dark", height=500, margin=dict(t=10, b=0, l=0, r=0),
            yaxis_title="Media Intensity (7d MA)", hovermode="x unified",
        )
        st.plotly_chart(fig_topics, use_container_width=True)

        # Topic comparison metrics
        st.subheader("Current Topic Intensity (7-Day Average)")
        topic_cols = st.columns(len(gdelt_data))
        for col, (label, df_t) in zip(topic_cols, gdelt_data.items()):
            avg_7 = df_t["value"].tail(7).mean()
            avg_30 = df_t["value"].tail(30).mean()
            change = ((avg_7 / avg_30) - 1) * 100 if avg_30 > 0 else 0
            col.metric(label.replace("Iran ", "").replace("Iran-", ""),
                       f"{avg_7:.4f}", f"{change:+.0f}% vs 30d",
                       delta_color="inverse")

        # Stacked area showing relative share
        st.subheader("Topic Share Over Time")
        # Merge all topics by date
        merged = None
        for label, df_t in gdelt_data.items():
            df_temp = df_t[["date", "value"]].copy()
            df_temp = df_temp.rename(columns={"value": label})
            df_temp["date"] = df_temp["date"].dt.date
            if merged is None:
                merged = df_temp
            else:
                merged = pd.merge(merged, df_temp, on="date", how="outer")

        if merged is not None:
            merged = merged.sort_values("date").fillna(0)
            merged["date"] = pd.to_datetime(merged["date"])

            fig_stack = go.Figure()
            for label in gdelt_data.keys():
                if label in merged.columns:
                    fig_stack.add_trace(go.Scatter(
                        x=merged["date"], y=merged[label].rolling(7, min_periods=1).mean(),
                        mode="lines", name=label, stackgroup="topics",
                        line=dict(width=0.5, color=topic_colors.get(label, "white")),
                    ))

            fig_stack.update_layout(
                template="plotly_dark", height=400, margin=dict(t=10, b=0, l=0, r=0),
                yaxis_title="Combined Intensity", hovermode="x unified",
            )
            st.plotly_chart(fig_stack, use_container_width=True)
    else:
        st.warning("No topic data available.")


# ---- TAB 3: Oil Price Correlation ----
with tab3:
    st.subheader("Conflict Impact on Oil Markets")

    if not df_oil.empty and main_data is not None and not main_data.empty:
        # Merge conflict and oil data
        df_conflict = main_data[["date", "value"]].copy()
        df_conflict["date"] = df_conflict["date"].dt.date

        df_oil_merge = df_oil[["period", "value"]].copy()
        df_oil_merge["date"] = df_oil_merge["period"].dt.date
        df_oil_merge = df_oil_merge.rename(columns={"value": "oil_price"})

        df_corr = pd.merge(
            df_conflict.rename(columns={"value": "conflict"}),
            df_oil_merge[["date", "oil_price"]],
            on="date", how="inner",
        )

        if not df_corr.empty:
            # Rolling correlation
            df_corr["date"] = pd.to_datetime(df_corr["date"])
            df_corr = df_corr.sort_values("date")
            df_corr["rolling_corr"] = df_corr["conflict"].rolling(30).corr(df_corr["oil_price"])

            overall_corr = df_corr["conflict"].corr(df_corr["oil_price"])

            oc1, oc2 = st.columns(2)
            oc1.metric("Overall Correlation", f"{overall_corr:.3f}")
            recent_corr = df_corr["rolling_corr"].iloc[-1] if not df_corr["rolling_corr"].isna().all() else 0
            oc2.metric("30-Day Rolling Correlation", f"{recent_corr:.3f}")

            # Rolling correlation chart
            fig_corr = go.Figure()
            fig_corr.add_trace(go.Scatter(
                x=df_corr["date"], y=df_corr["rolling_corr"],
                mode="lines", line=dict(color="#00d1ff", width=2),
                name="30-Day Rolling Correlation",
            ))
            fig_corr.add_hline(y=0, line_color="white", line_width=1)
            fig_corr.add_hrect(y0=0.3, y1=1, fillcolor="rgba(0, 255, 150, 0.05)", line_width=0)
            fig_corr.add_hrect(y0=-1, y1=-0.3, fillcolor="rgba(255, 75, 75, 0.05)", line_width=0)

            fig_corr.update_layout(
                template="plotly_dark", height=350, margin=dict(t=10, b=0, l=0, r=0),
                yaxis_title="Correlation", yaxis=dict(range=[-1, 1]),
                hovermode="x unified",
            )
            st.plotly_chart(fig_corr, use_container_width=True)

            # Scatter plot
            st.subheader("Conflict Intensity vs. Oil Price (Scatter)")
            fig_scatter = go.Figure()
            fig_scatter.add_trace(go.Scatter(
                x=df_corr["conflict"], y=df_corr["oil_price"],
                mode="markers", marker=dict(color="#ff4b4b", size=5, opacity=0.5),
                hovertemplate="Conflict: %{x:.4f}<br>Oil: $%{y:.2f}<extra></extra>",
            ))
            fig_scatter.update_layout(
                template="plotly_dark", height=400, margin=dict(t=10, b=0, l=0, r=0),
                xaxis_title="Conflict Media Intensity",
                yaxis_title="WTI Crude ($/bbl)",
            )
            st.plotly_chart(fig_scatter, use_container_width=True)
    else:
        st.warning("Oil price or conflict data unavailable.")


# ---- TAB 4: Market Impact ----
with tab4:
    st.subheader("Defense & Energy Sector Performance")

    if stock_data:
        # Normalize all stocks to % return from start of period
        st.markdown("**Defense Stocks**")
        fig_def = go.Figure()
        def_colors = {"LMT": "#00d1ff", "RTX": "#00ff96", "NOC": "#ffaa00"}

        for ticker, name in defense_tickers.items():
            if ticker in stock_data:
                px = stock_data[ticker]
                normalized = (px["Close"] / px["Close"].iloc[0] - 1) * 100
                fig_def.add_trace(go.Scatter(
                    x=normalized.index, y=normalized.values,
                    mode="lines", name=f"{ticker} ({name})",
                    line=dict(color=def_colors.get(ticker, "white"), width=2),
                ))

        fig_def.add_hline(y=0, line_color="white", line_width=1)
        fig_def.update_layout(
            template="plotly_dark", height=400, margin=dict(t=10, b=0, l=0, r=0),
            yaxis_title="Return (%)", hovermode="x unified",
        )
        st.plotly_chart(fig_def, use_container_width=True)

        # Defense metrics
        dc = st.columns(len(defense_tickers))
        for col, (ticker, name) in zip(dc, defense_tickers.items()):
            if ticker in stock_data:
                px = stock_data[ticker]
                ret_total = (px["Close"].iloc[-1] / px["Close"].iloc[0] - 1) * 100
                ret_30d = (px["Close"].iloc[-1] / px["Close"].iloc[-min(22, len(px))] - 1) * 100
                col.metric(f"{ticker}", f"${px['Close'].iloc[-1]:.2f}",
                           f"{ret_30d:+.1f}% (30d)")

        st.divider()
        st.markdown("**Energy Stocks & ETFs**")
        fig_energy = go.Figure()
        energy_colors = {"XLE": "#ff4b4b", "USO": "#ffaa00", "XOP": "#ad7fff"}

        for ticker, name in energy_tickers.items():
            if ticker in stock_data:
                px = stock_data[ticker]
                normalized = (px["Close"] / px["Close"].iloc[0] - 1) * 100
                fig_energy.add_trace(go.Scatter(
                    x=normalized.index, y=normalized.values,
                    mode="lines", name=f"{ticker} ({name})",
                    line=dict(color=energy_colors.get(ticker, "white"), width=2),
                ))

        fig_energy.add_hline(y=0, line_color="white", line_width=1)
        fig_energy.update_layout(
            template="plotly_dark", height=400, margin=dict(t=10, b=0, l=0, r=0),
            yaxis_title="Return (%)", hovermode="x unified",
        )
        st.plotly_chart(fig_energy, use_container_width=True)

        ec = st.columns(len(energy_tickers))
        for col, (ticker, name) in zip(ec, energy_tickers.items()):
            if ticker in stock_data:
                px = stock_data[ticker]
                ret_30d = (px["Close"].iloc[-1] / px["Close"].iloc[-min(22, len(px))] - 1) * 100
                col.metric(f"{ticker}", f"${px['Close'].iloc[-1]:.2f}",
                           f"{ret_30d:+.1f}% (30d)")

        # Overlay conflict with defense ETF
        st.divider()
        st.subheader("Conflict Intensity vs. Defense Sector")
        if main_data is not None and "LMT" in stock_data:
            fig_overlay = go.Figure()

            fig_overlay.add_trace(go.Scatter(
                x=main_data["date"], y=main_data["value"],
                mode="lines", name="Conflict Intensity",
                line=dict(color="#ff4b4b", width=2), yaxis="y",
            ))

            lmt_px = stock_data["LMT"]
            fig_overlay.add_trace(go.Scatter(
                x=lmt_px.index, y=lmt_px["Close"],
                mode="lines", name="LMT Price",
                line=dict(color="#00d1ff", width=2), yaxis="y2",
            ))

            fig_overlay.update_layout(
                template="plotly_dark", height=400, margin=dict(t=10, b=0, l=0, r=0),
                hovermode="x unified",
                yaxis=dict(title="Conflict Intensity", side="left", showgrid=False),
                yaxis2=dict(title="LMT ($)", side="right", overlaying="y", showgrid=False),
            )
            st.plotly_chart(fig_overlay, use_container_width=True)
    else:
        st.warning("Stock data unavailable.")


# ---- TAB 5: Sentiment Analysis ----
with tab5:
    st.subheader("Media Sentiment (GDELT Tone)")
    st.markdown("GDELT tone measures the average sentiment of global media coverage. **Negative** = more hostile/threatening coverage.")

    if df_tone is not None and not df_tone.empty:
        df_tone_plot = df_tone.copy()
        df_tone_plot["ma7"] = df_tone_plot["value"].rolling(7, min_periods=1).mean()

        latest_tone = df_tone_plot["value"].iloc[-1]
        avg_tone = df_tone_plot["value"].mean()
        min_tone = df_tone_plot["value"].min()
        min_tone_date = df_tone_plot.loc[df_tone_plot["value"].idxmin(), "date"]

        tc1, tc2, tc3 = st.columns(3)
        tc1.metric("Current Tone", f"{latest_tone:.2f}",
                    delta_color="normal" if latest_tone > avg_tone else "inverse")
        tc2.metric("6-Month Average", f"{avg_tone:.2f}")
        tc3.metric("Most Negative", f"{min_tone:.2f}", min_tone_date.strftime("%b %d"))

        fig_tone = go.Figure()

        # Color fill based on positive/negative
        fig_tone.add_trace(go.Scatter(
            x=df_tone_plot["date"], y=df_tone_plot["value"],
            mode="lines", name="Daily Tone",
            line=dict(color="#888888", width=1),
        ))
        fig_tone.add_trace(go.Scatter(
            x=df_tone_plot["date"], y=df_tone_plot["ma7"],
            mode="lines", name="7-Day MA",
            line=dict(color="#00d1ff", width=2.5),
        ))

        fig_tone.add_hline(y=0, line_color="white", line_width=1)
        fig_tone.add_hrect(y0=-15, y1=0, fillcolor="rgba(255, 75, 75, 0.05)", line_width=0)
        fig_tone.add_hrect(y0=0, y1=5, fillcolor="rgba(0, 255, 150, 0.05)", line_width=0)

        fig_tone.update_layout(
            template="plotly_dark", height=400, margin=dict(t=10, b=0, l=0, r=0),
            yaxis_title="Tone (negative = hostile)", hovermode="x unified",
        )
        st.plotly_chart(fig_tone, use_container_width=True)
        st.caption("Tone scale: Large negative values indicate hostile/threatening coverage. Values near 0 are neutral.")
    else:
        st.warning("Sentiment data unavailable. GDELT may be rate-limited — try refreshing.")

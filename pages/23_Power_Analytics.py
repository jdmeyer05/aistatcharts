import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import logging
from datetime import datetime, timedelta
from src.layout import setup_page, error_boundary, fun_loader
from src import ercot_api

logger = logging.getLogger(__name__)

setup_page("23_Power_Analytics")

st.title("⚡ Power Analytics")
st.markdown("Duck curve visualization, implied heat rates, spark spread analysis, and generation stack merit order.")

# ── CONFIG ──
from src.ercot_api import fetch_dashboard as fetch_ercot

# Typical heat rates by fuel type (BTU/kWh)
HEAT_RATES = {
    "Combined Cycle": 6_800,
    "Combustion Turbine": 9_500,
    "Steam Turbine": 10_200,
    "Coal": 10_400,
    "Nuclear": 10_500,
}

FUEL_COLORS = {
    "Nuclear": "#ad7fff",
    "Coal and Lignite": "#888888",
    "Natural Gas": "#ff9900",
    "Hydro": "#00ff96",
    "Wind": "#00d1ff",
    "Solar": "#ffdd00",
    "Power Storage": "#ff4b4b",
    "Other": "#666666",
}


# ── DATA FETCHING ──
from src.eia_helpers import fetch_henry_hub_spot as fetch_henry_hub, fetch_henry_hub_daily, fetch_eia_hourly_grid
from src.market_data import fetch_commodity_futures


def fetch_gas_futures() -> dict | None:
    """Fetch natural gas front-month futures."""
    return fetch_commodity_futures("NG=F", period="1mo")


def fetch_oil_futures() -> float | None:
    """Fetch WTI crude oil front-month price."""
    result = fetch_commodity_futures("CL=F", period="5d")
    return result["price"] if result else None


def fetch_ercot_spp() -> float | None:
    """Fetch latest ERCOT Hub Average Settlement Point Price."""
    data = fetch_ercot("systemWidePrices")
    if data:
        records = data.get("rtSppData", [])
        if records:
            hub_avg = records[-1].get("hbHubAvg")
            if hub_avg is not None:
                return float(hub_avg)
    return None


def fetch_ercot_spp_timeseries() -> pd.DataFrame | None:
    """Fetch full ERCOT RT SPP timeseries (15-min intervals) for price overlay."""
    data = fetch_ercot("systemWidePrices")
    if data:
        records = data.get("rtSppData", [])
        if records:
            df = pd.DataFrame(records)
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            df["hbHubAvg"] = pd.to_numeric(df["hbHubAvg"], errors="coerce")
            return df[["timestamp", "hbHubAvg"]].dropna().sort_values("timestamp")
    return None


# ── FETCH ALL DATA ──
with fun_loader("data"):
    fuel_mix = fetch_ercot("fuel-mix")
    supply_demand = fetch_ercot("supply-demand")
    load_forecast = fetch_ercot("loadForecastVsActual")
    gas_price = fetch_henry_hub()
    gas_price_daily = fetch_henry_hub_daily(days_back=30)
    gas_futures = fetch_gas_futures()
    oil_price = fetch_oil_futures()
    power_price = fetch_ercot_spp()
    spp_timeseries = fetch_ercot_spp_timeseries()
    eia_hourly = fetch_eia_hourly_grid("ERCO", days_back=31)

    # ERCOT Public API data (official, higher quality)
    _has_ercot_api = ercot_api.is_available()
    if _has_ercot_api:
        _today_str = datetime.now().strftime("%Y-%m-%d")
        _yesterday_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        ercot_load = ercot_api.fetch_actual_load(_yesterday_str)
        ercot_solar = ercot_api.fetch_solar_hourly(_today_str)
        ercot_wind = ercot_api.fetch_wind_hourly(_today_str)
        ercot_rt_spp = ercot_api.fetch_rt_spp(_today_str)
        ercot_dam_spp = ercot_api.fetch_dam_spp(_today_str)
        ercot_load_hist = ercot_api.fetch_load_history(days_back=30)
        ercot_sced_lambda = ercot_api.fetch_sced_lambda(_today_str)
        ercot_dam_lambda = ercot_api.fetch_dam_lambda(_today_str)
    else:
        ercot_load = ercot_solar = ercot_wind = None
        ercot_rt_spp = ercot_dam_spp = ercot_load_hist = None
        ercot_sced_lambda = ercot_dam_lambda = None

if not fuel_mix or not supply_demand:
    st.error("Failed to connect to ERCOT. The dashboard API may be temporarily unavailable.")
    st.stop()

# ── PARSE FUEL MIX TIMESERIES ──
fuel_data = fuel_mix.get("data", {})
fuel_types = fuel_mix.get("types", [])
capacity_data = fuel_mix.get("monthlyCapacity", {})

all_timestamps = []
for day_key, day_data in fuel_data.items():
    for ts_key, ts_data in day_data.items():
        row = {"timestamp": ts_key}
        for fuel, vals in ts_data.items():
            if isinstance(vals, dict):
                row[fuel] = vals.get("gen", 0)
        all_timestamps.append(row)

df_fuel = pd.DataFrame(all_timestamps)
if df_fuel.empty:
    st.error("No fuel mix data available.")
    st.stop()

df_fuel["timestamp"] = pd.to_datetime(df_fuel["timestamp"])
df_fuel = df_fuel.sort_values("timestamp")

# Compute total load and net load (load minus wind/solar = "duck belly")
# Storage can be negative (charging = grid load) — include as-is for correct demand approximation
# Clip other fuels at 0 for data quality (only storage should be negative)
fuel_cols_only = [c for c in df_fuel.columns if c not in ("timestamp", "Power Storage")]
df_fuel["total_gen"] = (
    df_fuel[fuel_cols_only].clip(lower=0).sum(axis=1)
    + df_fuel.get("Power Storage", pd.Series(0, index=df_fuel.index))
)
df_fuel["wind"] = df_fuel.get("Wind", pd.Series(0, index=df_fuel.index)).clip(lower=0)
df_fuel["solar"] = df_fuel.get("Solar", pd.Series(0, index=df_fuel.index)).clip(lower=0)
df_fuel["storage"] = df_fuel.get("Power Storage", pd.Series(0, index=df_fuel.index))
df_fuel["net_load"] = df_fuel["total_gen"] - df_fuel["wind"] - df_fuel["solar"]
df_fuel["hour"] = df_fuel["timestamp"].dt.hour + df_fuel["timestamp"].dt.minute / 60

# ── PARSE SUPPLY/DEMAND ──
sd_data = supply_demand.get("data", [])
df_sd = pd.DataFrame(sd_data)
if not df_sd.empty:
    df_sd["timestamp"] = pd.to_datetime(df_sd["timestamp"])

# ── METRICS ROW ──
latest = df_fuel.iloc[-1]
total_gen = latest["total_gen"]
net_load = latest["net_load"]
wind_mw = latest["wind"]
solar_mw = latest["solar"]
c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("Total Generation", f"{total_gen:,.0f} MW")
c2.metric("Net Load", f"{net_load:,.0f} MW")
c3.metric("Wind + Solar", f"{wind_mw + solar_mw:,.0f} MW",
          f"{(wind_mw + solar_mw) / total_gen * 100:.1f}% of mix" if total_gen > 0 else None)
if gas_futures:
    c4.metric("NG Futures", f"${gas_futures['price']:.2f}/MMBtu")
elif gas_price:
    c4.metric("Henry Hub", f"${gas_price:.2f}/MMBtu")
if oil_price:
    c5.metric("WTI Crude", f"${oil_price:.2f}/bbl")
if power_price:
    c6.metric("ERCOT Hub Avg", f"${power_price:.2f}/MWh")

st.divider()

# ── TABS ──
tab1, tab2, tab3, tab4 = st.tabs([
    "Duck Curve",
    "Heat Rate",
    "Spark Spread",
    "Stack Analysis",
])

# ════════════════════════════════════════════════
# TAB 1: DUCK CURVE
# ════════════════════════════════════════════════
with tab1:
    st.subheader("Duck Curve — Net Load Profile")
    st.caption("Net load = total demand minus wind and solar. The 'belly' shows midday renewable surplus; the 'neck' shows the steep evening ramp.")

    # ── Build historical averages ──
    # Prefer ERCOT API actual demand (NP6-345-CD) over EIA generation proxy
    hist_7d_avg = None
    hist_30d_avg = None
    hist_source = None
    eia_pivot = None

    if ercot_load_hist is not None and not ercot_load_hist.empty:
        # ERCOT API: actual hourly demand by weather zone — gold standard
        hist_source = "ERCOT API (actual demand)"
        hl = ercot_load_hist.copy()
        hl["hour"] = hl["hourEnding"].apply(lambda h: int(h.split(":")[0]) if isinstance(h, str) else int(h))
        hl["date"] = pd.to_datetime(hl["operatingDay"])
        latest = hl["date"].max()

        mask_7d = hl["date"] >= (latest - pd.Timedelta(days=7))
        if mask_7d.sum() > 10:
            hist_7d_avg = hl.loc[mask_7d].groupby("hour")["total"].mean()

        mask_30d = hl["date"] >= (latest - pd.Timedelta(days=30))
        if mask_30d.sum() > 10:
            hist_30d_avg = hl.loc[mask_30d].groupby("hour")["total"].mean()

    elif eia_hourly is not None and not eia_hourly.empty:
        # Fallback: EIA Hourly Grid Monitor (generation proxy, ~24hr lag)
        hist_source = "EIA Grid Monitor (generation proxy)"
        eia_pivot = eia_hourly.pivot_table(index="period", columns="fueltype", values="value", aggfunc="sum")
        eia_pivot = eia_pivot.sort_index()

        wind_cols = [c for c in eia_pivot.columns if "WND" in c.upper() or "WIND" in c.upper()]
        solar_cols = [c for c in eia_pivot.columns if "SUN" in c.upper() or "SOL" in c.upper()]
        eia_pivot["eia_total"] = eia_pivot.sum(axis=1)
        eia_pivot["eia_wind_solar"] = eia_pivot[wind_cols + solar_cols].sum(axis=1) if (wind_cols or solar_cols) else 0
        eia_pivot["eia_net_load"] = eia_pivot["eia_total"] - eia_pivot["eia_wind_solar"]
        eia_pivot["hour"] = eia_pivot.index.hour + eia_pivot.index.minute / 60

        now = eia_pivot.index.max()
        mask_7d = eia_pivot.index >= (now - pd.Timedelta(days=7))
        if mask_7d.sum() > 10:
            hist_7d_avg = eia_pivot.loc[mask_7d].groupby("hour")["eia_net_load"].mean()
        mask_30d = eia_pivot.index >= (now - pd.Timedelta(days=30))
        if mask_30d.sum() > 10:
            hist_30d_avg = eia_pivot.loc[mask_30d].groupby("hour")["eia_net_load"].mean()

    fig_duck = go.Figure()

    # Shaded area: total load
    fig_duck.add_trace(go.Scatter(
        x=df_fuel["timestamp"], y=df_fuel["total_gen"],
        mode="lines", name="Total Load",
        line=dict(color="#ff4b4b", width=2),
        fill="tozeroy", fillcolor="rgba(255, 75, 75, 0.08)",
    ))

    # Net load (the duck)
    fig_duck.add_trace(go.Scatter(
        x=df_fuel["timestamp"], y=df_fuel["net_load"],
        mode="lines", name="Net Load (Today)",
        line=dict(color="#ffaa00", width=3),
        fill="tozeroy", fillcolor="rgba(255, 170, 0, 0.15)",
    ))

    # Historical overlays — map hour-of-day averages onto today's timestamps
    if hist_7d_avg is not None:
        avg_mapped = df_fuel[["timestamp", "hour"]].copy()
        avg_mapped["hist_7d"] = avg_mapped["hour"].map(
            lambda h: hist_7d_avg.get(round(h), np.nan)
        )
        fig_duck.add_trace(go.Scatter(
            x=avg_mapped["timestamp"], y=avg_mapped["hist_7d"],
            mode="lines", name="7-Day Avg Net Load",
            line=dict(color="#ad7fff", width=2, dash="dash"),
        ))

    if hist_30d_avg is not None:
        avg_mapped_30 = df_fuel[["timestamp", "hour"]].copy()
        avg_mapped_30["hist_30d"] = avg_mapped_30["hour"].map(
            lambda h: hist_30d_avg.get(round(h), np.nan)
        )
        fig_duck.add_trace(go.Scatter(
            x=avg_mapped_30["timestamp"], y=avg_mapped_30["hist_30d"],
            mode="lines", name="30-Day Avg Net Load",
            line=dict(color="#00ff96", width=2, dash="dot"),
        ))

    # Wind + Solar band
    fig_duck.add_trace(go.Scatter(
        x=df_fuel["timestamp"], y=df_fuel["wind"] + df_fuel["solar"],
        mode="lines", name="Wind + Solar",
        line=dict(color="#00d1ff", width=1.5, dash="dot"),
    ))

    fig_duck.update_layout(
        template="plotly_dark", height=500, margin=dict(t=10, b=0, l=0, r=0),
        yaxis_title="Megawatts (MW)", hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )
    st.plotly_chart(fig_duck, use_container_width=True)

    if hist_7d_avg is None and hist_30d_avg is None:
        st.caption("Historical overlay unavailable — requires ERCOT API or EIA API key.")
    elif hist_source:
        st.caption(f"Historical overlay source: {hist_source}")

    # ── Filter to today only for single-day duck curve analytics ──
    today_date = df_fuel["timestamp"].dt.date.max()
    df_today = df_fuel[df_fuel["timestamp"].dt.date == today_date].copy()
    if df_today.empty:
        df_today = df_fuel.copy()  # fallback if date filtering fails

    # Ramp rate analysis (today only)
    df_today["ramp_rate"] = df_today["net_load"].diff() / (df_today["timestamp"].diff().dt.total_seconds() / 3600)
    max_ramp = df_today["ramp_rate"].max()
    min_ramp = df_today["ramp_rate"].min()
    belly = df_today["net_load"].min()
    peak = df_today["net_load"].max()

    belly_time = df_today.loc[df_today["net_load"].idxmin(), "timestamp"]
    peak_time = df_today.loc[df_today["net_load"].idxmax(), "timestamp"]

    r1, r2, r3, r4 = st.columns(4)
    r1.metric("Duck Belly (Min Net Load)", f"{belly:,.0f} MW",
              help=f"At {belly_time.strftime('%I:%M %p')}")
    r2.metric("Evening Peak (Max Net Load)", f"{peak:,.0f} MW",
              help=f"At {peak_time.strftime('%I:%M %p')}")
    r3.metric("Max Upward Ramp", f"{max_ramp:,.0f} MW/hr" if pd.notna(max_ramp) else "N/A")
    r4.metric("Max Downward Ramp", f"{min_ramp:,.0f} MW/hr" if pd.notna(min_ramp) else "N/A")

    # Ramp rate chart
    fig_ramp = go.Figure()
    ramp_colors = ["#00ff96" if v >= 0 else "#ff4b4b" for v in df_today["ramp_rate"].fillna(0)]
    fig_ramp.add_trace(go.Bar(
        x=df_today["timestamp"], y=df_today["ramp_rate"],
        marker_color=ramp_colors, name="Ramp Rate",
    ))
    fig_ramp.add_hline(y=0, line_color="white", line_width=0.5)
    fig_ramp.update_layout(
        template="plotly_dark", height=280, margin=dict(t=10, b=0, l=0, r=0),
        yaxis_title="Ramp Rate (MW/hr)", hovermode="x unified",
    )
    st.caption("Net Load Ramp Rate — positive = ramping up (evening), negative = ramping down (morning)")
    st.plotly_chart(fig_ramp, use_container_width=True)

    # ── Renewable Penetration % ──
    st.subheader("Renewable Penetration %")
    st.caption("Wind + solar as a percentage of total generation over the day — the normalized driver of duck curve depth.")

    df_today["renewable_pct"] = np.where(
        df_today["total_gen"] > 0,
        (df_today["wind"] + df_today["solar"]) / df_today["total_gen"] * 100,
        0,
    )

    fig_pct = go.Figure()
    fig_pct.add_trace(go.Scatter(
        x=df_today["timestamp"], y=df_today["renewable_pct"],
        mode="lines", name="Renewable %",
        line=dict(color="#00d1ff", width=2.5),
        fill="tozeroy", fillcolor="rgba(0, 209, 255, 0.12)",
        hovertemplate="%{y:.1f}%<extra></extra>",
    ))

    # Add historical avg penetration if available
    if eia_pivot is not None and "eia_total" in eia_pivot.columns:
        eia_pivot["eia_ren_pct"] = np.where(
            eia_pivot["eia_total"] > 0,
            eia_pivot["eia_wind_solar"] / eia_pivot["eia_total"] * 100,
            0,
        )
        mask_30d_pct = eia_pivot.index >= (eia_pivot.index.max() - pd.Timedelta(days=30))
        hist_pct_avg = eia_pivot.loc[mask_30d_pct].groupby("hour")["eia_ren_pct"].mean()
        if not hist_pct_avg.empty:
            avg_pct_mapped = df_today[["timestamp", "hour"]].copy()
            avg_pct_mapped["hist_pct"] = avg_pct_mapped["hour"].map(
                lambda h: hist_pct_avg.get(round(h), np.nan)
            )
            fig_pct.add_trace(go.Scatter(
                x=avg_pct_mapped["timestamp"], y=avg_pct_mapped["hist_pct"],
                mode="lines", name="30-Day Avg %",
                line=dict(color="#ad7fff", width=2, dash="dash"),
                hovertemplate="%{y:.1f}%<extra></extra>",
            ))

    fig_pct.update_layout(
        template="plotly_dark", height=320, margin=dict(t=10, b=0, l=0, r=0),
        yaxis_title="Renewable Penetration (%)", hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        yaxis=dict(range=[0, max(df_today["renewable_pct"].max() * 1.15, 10)]),
    )
    st.plotly_chart(fig_pct, use_container_width=True)

    pct_max = df_today["renewable_pct"].max()
    pct_min = df_today["renewable_pct"].min()
    pct_now = df_today["renewable_pct"].iloc[-1]
    p1, p2, p3 = st.columns(3)
    p1.metric("Current Penetration", f"{pct_now:.1f}%")
    p2.metric("Today's Peak", f"{pct_max:.1f}%")
    p3.metric("Today's Min", f"{pct_min:.1f}%")

    # ── Flexibility Requirement Metrics ──
    st.subheader("Flexibility Requirements")
    st.caption("Quantifies how much dispatchable capacity must ramp to accommodate renewable swings.")

    ramp_range = peak - belly
    ramp_to_peak_ratio = (ramp_range / peak * 100) if peak > 0 else 0

    # 3-hour max ramp: max directional net load change over any 3-hour window
    # This is the standard CAISO/ERCOT flexibility planning metric
    if len(df_today) > 1:
        interval_minutes = df_today["timestamp"].diff().dt.total_seconds().median() / 60
        intervals_3h = max(1, int(180 / interval_minutes))
        # Directional ramp: net_load(t) - net_load(t - 3h)
        ramp_3h_up = df_today["net_load"].diff(periods=intervals_3h)
        max_3h_ramp_up = ramp_3h_up.max()    # largest 3h upward ramp
        max_3h_ramp_down = ramp_3h_up.min()   # largest 3h downward ramp (negative)
        max_3h_ramp = max(abs(max_3h_ramp_up), abs(max_3h_ramp_down)) if pd.notna(max_3h_ramp_up) else 0
    else:
        max_3h_ramp = 0
        max_3h_ramp_up = 0
        max_3h_ramp_down = 0

    f1, f2, f3, f4 = st.columns(4)
    f1.metric(
        "Ramp Range (Peak − Belly)",
        f"{ramp_range:,.0f} MW",
        help="Total MW swing dispatchable resources must cover between midday minimum and evening peak.",
    )
    f2.metric(
        "3-Hr Max Upward Ramp",
        f"{max_3h_ramp_up:+,.0f} MW" if pd.notna(max_3h_ramp_up) else "N/A",
        help="Largest 3-hour net load increase (evening ramp-up) — standard flexibility planning metric.",
    )
    f3.metric(
        "3-Hr Max Downward Ramp",
        f"{max_3h_ramp_down:+,.0f} MW" if pd.notna(max_3h_ramp_down) else "N/A",
        help="Largest 3-hour net load decrease (morning solar ramp) — drives curtailment risk.",
    )
    f4.metric(
        "Ramp-to-Peak Ratio",
        f"{ramp_to_peak_ratio:.1f}%",
        help="Ramp range as % of peak net load. Higher = more 'ducky'. ERCOT spring norm is 50-65% on sunny days.",
    )

    # Duckiness assessment — calibrated to ERCOT seasonal norms
    # Spring 2026: 50-65% ramp-to-peak is typical on sunny days with 25+ GW solar
    if ramp_to_peak_ratio > 70:
        duck_grade = "Extreme"
        duck_color = "#ff4b4b"
        duck_note = "well beyond seasonal norms — potential curtailment or negative pricing"
    elif ramp_to_peak_ratio > 55:
        duck_grade = "Pronounced"
        duck_color = "#ff9900"
        duck_note = "steep but within range for high-solar spring days"
    elif ramp_to_peak_ratio > 40:
        duck_grade = "Moderate"
        duck_color = "#ffdd00"
        duck_note = "typical for days with moderate renewable output"
    elif ramp_to_peak_ratio > 25:
        duck_grade = "Mild"
        duck_color = "#00ff96"
        duck_note = "low renewable impact on net load shape"
    else:
        duck_grade = "Flat"
        duck_color = "#00ff96"
        duck_note = "minimal duck shape — low renewable penetration"

    st.markdown(
        f"**Duck Severity: <span style='color:{duck_color}'>{duck_grade}</span>** — "
        f"Dispatchable fleet must swing {ramp_range:,.0f} MW ({ramp_to_peak_ratio:.1f}% of peak). "
        f"{duck_note.capitalize()}.",
        unsafe_allow_html=True,
    )

    # ── Over-Generation Risk Index ──
    st.subheader("Over-Generation Risk")

    # Derive min gen constraint from data: sum of must-run baseload (nuclear + minimum gas/coal)
    # Nuclear is must-run; minimum thermal = ~30% of current gas+coal capacity as operational floor
    nuclear_mw = df_today.get("Nuclear", pd.Series(0, index=df_today.index)).median()
    coal_mw_min = df_today.get("Coal and Lignite", pd.Series(0, index=df_today.index)).clip(lower=0).quantile(0.05)
    gas_mw_min = df_today.get("Natural Gas", pd.Series(0, index=df_today.index)).clip(lower=0).quantile(0.05)
    hydro_mw = df_today.get("Hydro", pd.Series(0, index=df_today.index)).median()
    # Min gen = must-run nuclear + observed floor of thermal + hydro baseload
    ercot_min_gen = max(nuclear_mw + coal_mw_min + gas_mw_min + hydro_mw, 10_000)

    st.caption(
        f"Net load approaching the estimated minimum stable generation ({ercot_min_gen:,.0f} MW — "
        f"derived from nuclear baseload + observed thermal/hydro floor) signals curtailment risk."
    )

    overgen_margin = df_today["net_load"] - ercot_min_gen
    min_margin = overgen_margin.min()
    current_margin = overgen_margin.iloc[-1]
    # At-risk threshold: within 15% of min gen constraint
    at_risk_threshold = ercot_min_gen * 0.15
    hours_at_risk = (overgen_margin < at_risk_threshold).sum() * (interval_minutes / 60) if len(df_today) > 1 else 0

    # Risk tiers as % of min gen constraint
    if min_margin < 0:
        risk_level, risk_color = "CRITICAL — Below Min Gen", "#ff4b4b"
    elif min_margin < ercot_min_gen * 0.10:
        risk_level, risk_color = f"High — Within {ercot_min_gen * 0.10 / 1000:.1f} GW", "#ff9900"
    elif min_margin < ercot_min_gen * 0.25:
        risk_level, risk_color = f"Elevated — Within {ercot_min_gen * 0.25 / 1000:.1f} GW", "#ffdd00"
    else:
        risk_level, risk_color = "Normal", "#00ff96"

    o1, o2, o3 = st.columns(3)
    o1.metric("Min Gen Margin (Lowest Today)", f"{min_margin:,.0f} MW")
    o2.metric("Current Margin", f"{current_margin:,.0f} MW")
    o3.metric(f"Hours Within {at_risk_threshold / 1000:.1f} GW of Constraint", f"{hours_at_risk:.1f} hrs")

    # Duck curve with over-gen danger zone
    fig_overgen = go.Figure()

    # Danger zone shading
    fig_overgen.add_hrect(
        y0=0, y1=ercot_min_gen,
        fillcolor="rgba(255, 75, 75, 0.08)", line_width=0,
        annotation_text="Min Gen Constraint", annotation_position="top left",
    )
    fig_overgen.add_hrect(
        y0=ercot_min_gen, y1=ercot_min_gen + at_risk_threshold,
        fillcolor="rgba(255, 153, 0, 0.06)", line_width=0,
    )
    fig_overgen.add_hline(y=ercot_min_gen, line_color="#ff4b4b", line_width=1.5, line_dash="dash")

    fig_overgen.add_trace(go.Scatter(
        x=df_today["timestamp"], y=df_today["net_load"],
        mode="lines", name="Net Load",
        line=dict(color="#ffaa00", width=3),
    ))
    fig_overgen.add_trace(go.Scatter(
        x=df_today["timestamp"], y=overgen_margin,
        mode="lines", name="Margin Above Min Gen",
        line=dict(color="#00ff96", width=1.5, dash="dot"),
        yaxis="y2",
    ))

    fig_overgen.update_layout(
        template="plotly_dark", height=380, margin=dict(t=10, b=0, l=0, r=0),
        yaxis_title="Net Load (MW)", hovermode="x unified",
        yaxis2=dict(title="Margin (MW)", overlaying="y", side="right", showgrid=False),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )
    st.plotly_chart(fig_overgen, use_container_width=True)

    st.markdown(
        f"**Risk Level: <span style='color:{risk_color}'>{risk_level}</span>**",
        unsafe_allow_html=True,
    )

    # ── Storage Arbitrage Value + Price Overlay ──
    st.subheader("Storage Arbitrage & Price Shape")
    st.caption("RT price overlaid on net load shows the price signal for battery storage — charge at the belly, discharge at the peak.")

    if spp_timeseries is not None and not spp_timeseries.empty:
        fig_arb = go.Figure()

        # Net load on primary axis
        fig_arb.add_trace(go.Scatter(
            x=df_today["timestamp"], y=df_today["net_load"],
            mode="lines", name="Net Load (MW)",
            line=dict(color="#ffaa00", width=2.5),
            yaxis="y",
        ))

        # RT price on secondary axis
        fig_arb.add_trace(go.Scatter(
            x=spp_timeseries["timestamp"], y=spp_timeseries["hbHubAvg"],
            mode="lines", name="RT Hub Price ($/MWh)",
            line=dict(color="#00d1ff", width=2),
            yaxis="y2",
        ))

        fig_arb.update_layout(
            template="plotly_dark", height=420, margin=dict(t=10, b=0, l=0, r=0),
            hovermode="x unified",
            yaxis=dict(title="Net Load (MW)", side="left"),
            yaxis2=dict(title="RT Price ($/MWh)", overlaying="y", side="right", showgrid=False),
            legend=dict(orientation="h", yanchor="bottom", y=1.02),
        )
        st.plotly_chart(fig_arb, use_container_width=True)

        # Arbitrage metrics
        # Match price to nearest net load timestamp for belly/peak price
        spp_ts = spp_timeseries.set_index("timestamp")["hbHubAvg"]
        belly_time = df_today.loc[df_today["net_load"].idxmin(), "timestamp"]
        peak_time = df_today.loc[df_today["net_load"].idxmax(), "timestamp"]

        # Find nearest price to belly/peak times
        belly_price = spp_ts.iloc[spp_ts.index.get_indexer([belly_time], method="nearest")[0]] if len(spp_ts) > 0 else None
        peak_price = spp_ts.iloc[spp_ts.index.get_indexer([peak_time], method="nearest")[0]] if len(spp_ts) > 0 else None

        price_min = spp_ts.min()
        price_max = spp_ts.max()
        # Round-trip efficiency for grid-scale Li-ion BESS (NREL ATB 2024: 85-90%, using conservative end)
        rt_efficiency = 0.85
        gross_spread = price_max - price_min
        net_arb_value = price_max * rt_efficiency - price_min

        a1, a2, a3, a4 = st.columns(4)
        a1.metric("Min RT Price (Charge)", f"${price_min:.2f}/MWh")
        a2.metric("Max RT Price (Discharge)", f"${price_max:.2f}/MWh")
        a3.metric("Gross Spread", f"${gross_spread:.2f}/MWh")
        a4.metric("Net Arb Value (85% RT eff.)", f"${net_arb_value:.2f}/MWh",
                  delta=f"{'Profitable' if net_arb_value > 0 else 'Unprofitable'}",
                  delta_color="normal" if net_arb_value > 0 else "inverse")

        if belly_price is not None and peak_price is not None:
            st.markdown(
                f"**Belly price** (at min net load): ${belly_price:.2f}/MWh &nbsp;|&nbsp; "
                f"**Peak price** (at max net load): ${peak_price:.2f}/MWh &nbsp;|&nbsp; "
                f"**Shape spread**: ${peak_price - belly_price:.2f}/MWh"
            )
        # ── DAM vs RT Spread (ERCOT API) ──
        if ercot_dam_spp is not None and not ercot_dam_spp.empty and ercot_rt_spp is not None and not ercot_rt_spp.empty:
            st.markdown("---")
            st.markdown("**Day-Ahead vs Real-Time Price Spread**")
            st.caption("DAM-RT spread reveals systematic price shape differences. Positive = DAM priced higher than RT (over-forecast demand). Negative = RT spiked above DAM.")

            # Average RT price per hour
            rt_hourly = ercot_rt_spp.groupby("deliveryHour")["settlementPointPrice"].mean().reset_index()
            rt_hourly.columns = ["hour", "rt_price"]

            # DAM price per hour
            dam_hr = ercot_dam_spp.copy()
            dam_hr["hour"] = dam_hr["hourEnding"].apply(
                lambda h: int(h.split(":")[0]) if isinstance(h, str) else int(h)
            )
            dam_hourly = dam_hr.groupby("hour")["settlementPointPrice"].mean().reset_index()
            dam_hourly.columns = ["hour", "dam_price"]

            merged_prices = pd.merge(dam_hourly, rt_hourly, on="hour", how="inner")
            if not merged_prices.empty:
                merged_prices["spread"] = merged_prices["dam_price"] - merged_prices["rt_price"]

                fig_spread = go.Figure()
                fig_spread.add_trace(go.Scatter(
                    x=merged_prices["hour"], y=merged_prices["dam_price"],
                    mode="lines", name="DAM Price",
                    line=dict(color="#ffaa00", width=2),
                ))
                fig_spread.add_trace(go.Scatter(
                    x=merged_prices["hour"], y=merged_prices["rt_price"],
                    mode="lines", name="RT Avg Price",
                    line=dict(color="#00d1ff", width=2),
                ))
                fig_spread.add_trace(go.Bar(
                    x=merged_prices["hour"], y=merged_prices["spread"],
                    name="DAM-RT Spread",
                    marker_color=["#00ff96" if v >= 0 else "#ff4b4b" for v in merged_prices["spread"]],
                    opacity=0.5,
                ))

                fig_spread.update_layout(
                    template="plotly_dark", height=350, margin=dict(t=10, b=0, l=0, r=0),
                    yaxis_title="$/MWh", xaxis_title="Hour", xaxis=dict(dtick=2),
                    hovermode="x unified",
                    legend=dict(orientation="h", yanchor="bottom", y=1.02),
                )
                st.plotly_chart(fig_spread, use_container_width=True)

                avg_spread = merged_prices["spread"].mean()
                max_rt_spike = (merged_prices["rt_price"] - merged_prices["dam_price"]).max()
                st.caption(
                    f"Avg DAM-RT spread: ${avg_spread:+.2f}/MWh | "
                    f"Max RT spike above DAM: ${max_rt_spike:.2f}/MWh"
                )

        # ── Zonal Hub Price Comparison (ERCOT API) ──
        if ercot_rt_spp is not None and not ercot_rt_spp.empty:
            # Fetch additional hubs for comparison
            hub_names = {"HB_HUBAVG": "Hub Avg", "HB_HOUSTON": "Houston", "HB_NORTH": "North",
                         "HB_SOUTH": "South", "HB_WEST": "West"}
            hub_data = {}

            for hub_id, hub_label in hub_names.items():
                if hub_id == "HB_HUBAVG":
                    hub_data[hub_label] = ercot_rt_spp.groupby("deliveryHour")["settlementPointPrice"].mean()
                else:
                    df_hub = ercot_api.fetch_rt_spp(_today_str, settlement_point=hub_id)
                    if df_hub is not None and not df_hub.empty:
                        hub_data[hub_label] = df_hub.groupby("deliveryHour")["settlementPointPrice"].mean()

            if len(hub_data) > 1:
                st.markdown("---")
                st.markdown("**Zonal Hub Prices — Congestion Indicator**")
                st.caption("Hub price divergence signals transmission congestion. Large spreads between hubs = constrained power flow.")

                hub_colors = {"Hub Avg": "#ffaa00", "Houston": "#ff4b4b", "North": "#00d1ff",
                              "South": "#00ff96", "West": "#ad7fff"}

                fig_hubs = go.Figure()
                for hub_label, series in hub_data.items():
                    fig_hubs.add_trace(go.Scatter(
                        x=series.index, y=series.values,
                        mode="lines", name=hub_label,
                        line=dict(color=hub_colors.get(hub_label, "#ffffff"), width=2),
                    ))
                fig_hubs.update_layout(
                    template="plotly_dark", height=350, margin=dict(t=10, b=0, l=0, r=0),
                    yaxis_title="RT Price ($/MWh)", xaxis_title="Hour", xaxis=dict(dtick=2),
                    hovermode="x unified",
                    legend=dict(orientation="h", yanchor="bottom", y=1.02),
                )
                st.plotly_chart(fig_hubs, use_container_width=True)
    else:
        st.warning("ERCOT RT price timeseries unavailable — cannot compute storage arbitrage.")

    # ── Day-Ahead Forecast vs Actual ──
    st.subheader("Day-Ahead Forecast vs Actual Load")
    st.caption("Forecast error is a key risk metric — large misses drive real-time price volatility and ancillary service deployment.")

    if load_forecast:
        cd = load_forecast.get("currentDay", {}).get("data", [])
        if cd:
            df_fc = pd.DataFrame(cd)
            df_fc["timestamp"] = pd.to_datetime(df_fc["timestamp"])

            has_actual = "systemLoad" in df_fc.columns and (df_fc["systemLoad"] > 0).any()
            has_da = "dayAheadForecast" in df_fc.columns
            has_current_fc = "currentLoadForecast" in df_fc.columns

            if has_da:
                fig_fc = go.Figure()

                if has_actual:
                    actual_mask = df_fc["systemLoad"] > 0
                    fig_fc.add_trace(go.Scatter(
                        x=df_fc.loc[actual_mask, "timestamp"],
                        y=df_fc.loc[actual_mask, "systemLoad"],
                        mode="lines", name="Actual Load",
                        line=dict(color="#ff4b4b", width=2.5),
                    ))

                fig_fc.add_trace(go.Scatter(
                    x=df_fc["timestamp"], y=df_fc["dayAheadForecast"],
                    mode="lines", name="Day-Ahead Forecast",
                    line=dict(color="#ffaa00", width=2, dash="dash"),
                ))

                if has_current_fc:
                    fig_fc.add_trace(go.Scatter(
                        x=df_fc["timestamp"], y=df_fc["currentLoadForecast"],
                        mode="lines", name="Current Forecast",
                        line=dict(color="#00d1ff", width=1.5, dash="dot"),
                    ))

                fig_fc.update_layout(
                    template="plotly_dark", height=380, margin=dict(t=10, b=0, l=0, r=0),
                    yaxis_title="Load (MW)", hovermode="x unified",
                    legend=dict(orientation="h", yanchor="bottom", y=1.02),
                )
                st.plotly_chart(fig_fc, use_container_width=True)

                # Forecast error metrics
                if has_actual:
                    df_actual = df_fc[df_fc["systemLoad"] > 0].copy()
                    if not df_actual.empty and has_da:
                        df_actual["da_error"] = df_actual["systemLoad"] - df_actual["dayAheadForecast"]
                        mae = df_actual["da_error"].abs().mean()
                        mape = (df_actual["da_error"].abs() / df_actual["systemLoad"] * 100).mean()
                        max_over = df_actual["da_error"].max()
                        max_under = df_actual["da_error"].min()
                        bias = df_actual["da_error"].mean()

                        e1, e2, e3, e4, e5 = st.columns(5)
                        e1.metric("MAE", f"{mae:,.0f} MW")
                        e2.metric("MAPE", f"{mape:.2f}%")
                        e3.metric("Bias (Avg Error)", f"{bias:+,.0f} MW",
                                  help="Positive = actual > forecast (under-forecast), Negative = over-forecast")
                        e4.metric("Max Under-Forecast", f"{max_over:+,.0f} MW")
                        e5.metric("Max Over-Forecast", f"{max_under:+,.0f} MW")

                        # Error bar chart — thresholds relative to avg system load
                        avg_load = df_actual["systemLoad"].mean()
                        err_green = avg_load * 0.01   # < 1% of load
                        err_yellow = avg_load * 0.02  # < 2% of load
                        fig_err = go.Figure()
                        err_colors = ["#00ff96" if abs(v) < err_green else "#ffaa00" if abs(v) < err_yellow else "#ff4b4b"
                                      for v in df_actual["da_error"]]
                        fig_err.add_trace(go.Bar(
                            x=df_actual["timestamp"], y=df_actual["da_error"],
                            marker_color=err_colors, name="DA Forecast Error",
                        ))
                        fig_err.add_hline(y=0, line_color="white", line_width=0.5)
                        fig_err.update_layout(
                            template="plotly_dark", height=260, margin=dict(t=10, b=0, l=0, r=0),
                            yaxis_title="Error (MW)", hovermode="x unified",
                        )
                        st.caption(f"Day-Ahead Forecast Error — green < 1% of load ({err_green:,.0f} MW), yellow < 2% ({err_yellow:,.0f} MW), red > 2%")
                        st.plotly_chart(fig_err, use_container_width=True)
            else:
                st.info("Day-ahead forecast data not available in current ERCOT response.")
        else:
            st.info("No forecast data available for today.")
    else:
        st.warning("Load forecast data unavailable from ERCOT.")

    # ── Wind & Solar: Forecast vs Actual (ERCOT API) ──
    if ercot_solar is not None or ercot_wind is not None:
        st.subheader("Wind & Solar — Forecast vs Actual")
        st.caption("ERCOT official hourly generation with short-term forecast and capacity (HSL). Gap between capacity and actual indicates potential curtailment.")

        ws_col1, ws_col2 = st.columns(2)

        # Solar
        with ws_col1:
            if ercot_solar is not None and not ercot_solar.empty:
                # Get latest posted forecast (most recent postedDatetime per hour)
                df_sol = ercot_solar.sort_values("postedDatetime").drop_duplicates(
                    subset=["deliveryDate", "hourEnding"], keep="last"
                ).sort_values("hourEnding")
                df_sol = df_sol[df_sol["hourEnding"] <= 24]

                fig_sol = go.Figure()
                if "HSLSystemWide" in df_sol.columns:
                    fig_sol.add_trace(go.Scatter(
                        x=df_sol["hourEnding"], y=df_sol["HSLSystemWide"],
                        mode="lines", name="Capacity (HSL)",
                        line=dict(color="#666666", width=1, dash="dot"),
                        fill="tozeroy", fillcolor="rgba(102, 102, 102, 0.05)",
                    ))
                if "STPPFSystemWide" in df_sol.columns:
                    fig_sol.add_trace(go.Scatter(
                        x=df_sol["hourEnding"], y=df_sol["STPPFSystemWide"],
                        mode="lines", name="Forecast (STPPF)",
                        line=dict(color="#ff9900", width=2, dash="dash"),
                    ))
                actuals = df_sol[df_sol["genSystemWide"].notna() & (df_sol["genSystemWide"] > 0)]
                if not actuals.empty:
                    fig_sol.add_trace(go.Scatter(
                        x=actuals["hourEnding"], y=actuals["genSystemWide"],
                        mode="lines+markers", name="Actual Generation",
                        line=dict(color="#ffdd00", width=2.5),
                    ))

                fig_sol.update_layout(
                    template="plotly_dark", height=320, margin=dict(t=30, b=0, l=0, r=0),
                    title="Solar (System-Wide)", yaxis_title="MW",
                    hovermode="x unified", xaxis=dict(dtick=2, title="Hour Ending"),
                    legend=dict(orientation="h", yanchor="bottom", y=1.02),
                )
                st.plotly_chart(fig_sol, use_container_width=True)

                # Curtailment estimate: capacity - actual when actual < forecast
                if not actuals.empty and "COPHSLSystemWide" in actuals.columns:
                    merged = actuals[actuals["COPHSLSystemWide"].notna()].copy()
                    if not merged.empty:
                        merged["headroom"] = merged["COPHSLSystemWide"] - merged["genSystemWide"]
                        max_gen = actuals["genSystemWide"].max()
                        capacity = merged["COPHSLSystemWide"].max()
                        st.caption(f"Peak solar: {max_gen:,.0f} MW | Capacity (COP HSL): {capacity:,.0f} MW | Utilization: {max_gen / capacity * 100:.0f}%" if capacity > 0 else "")

        # Wind
        with ws_col2:
            if ercot_wind is not None and not ercot_wind.empty:
                df_wnd = ercot_wind.sort_values("postedDatetime").drop_duplicates(
                    subset=["deliveryDate", "hourEnding"], keep="last"
                ).sort_values("hourEnding")
                df_wnd = df_wnd[df_wnd["hourEnding"] <= 24]

                fig_wnd = go.Figure()
                if "COPHSLSystemWide" in df_wnd.columns:
                    fig_wnd.add_trace(go.Scatter(
                        x=df_wnd["hourEnding"], y=df_wnd["COPHSLSystemWide"],
                        mode="lines", name="Capacity (COP HSL)",
                        line=dict(color="#666666", width=1, dash="dot"),
                        fill="tozeroy", fillcolor="rgba(102, 102, 102, 0.05)",
                    ))
                if "STWPFSystemWide" in df_wnd.columns:
                    fig_wnd.add_trace(go.Scatter(
                        x=df_wnd["hourEnding"], y=df_wnd["STWPFSystemWide"],
                        mode="lines", name="Forecast (STWPF)",
                        line=dict(color="#ff9900", width=2, dash="dash"),
                    ))
                actuals_w = df_wnd[df_wnd["genSystemWide"].notna() & (df_wnd["genSystemWide"] > 0)]
                if not actuals_w.empty:
                    fig_wnd.add_trace(go.Scatter(
                        x=actuals_w["hourEnding"], y=actuals_w["genSystemWide"],
                        mode="lines+markers", name="Actual Generation",
                        line=dict(color="#00d1ff", width=2.5),
                    ))

                fig_wnd.update_layout(
                    template="plotly_dark", height=320, margin=dict(t=30, b=0, l=0, r=0),
                    title="Wind (System-Wide)", yaxis_title="MW",
                    hovermode="x unified", xaxis=dict(dtick=2, title="Hour Ending"),
                    legend=dict(orientation="h", yanchor="bottom", y=1.02),
                )
                st.plotly_chart(fig_wnd, use_container_width=True)

                if not actuals_w.empty and "COPHSLSystemWide" in actuals_w.columns:
                    merged_w = actuals_w[actuals_w["COPHSLSystemWide"].notna()].copy()
                    if not merged_w.empty:
                        max_gen_w = actuals_w["genSystemWide"].max()
                        capacity_w = merged_w["COPHSLSystemWide"].max()
                        st.caption(f"Peak wind: {max_gen_w:,.0f} MW | Capacity (COP HSL): {capacity_w:,.0f} MW | Utilization: {max_gen_w / capacity_w * 100:.0f}%" if capacity_w > 0 else "")
    elif _has_ercot_api:
        st.info("Wind/solar forecast data not yet available for today.")

    # ── Multi-ISO Duck Curve Comparison ──
    st.subheader("Multi-ISO Duck Curve Comparison")
    st.caption("Compare net load shapes across major ISOs using EIA Hourly Grid Monitor data. Each ISO has a different renewable mix driving its duck curve.")

    ISO_OPTIONS = {
        "ERCO": "ERCOT (Texas)",
        "CISO": "CAISO (California)",
        "MISO": "MISO",
        "PJM": "PJM",
        "SWPP": "SPP (Southwest Power Pool)",
        "NYIS": "NYISO (New York)",
    }

    selected_isos = st.multiselect(
        "Select ISOs to compare",
        options=list(ISO_OPTIONS.keys()),
        default=["ERCO", "CISO"],
        format_func=lambda x: ISO_OPTIONS[x],
    )

    if selected_isos:
        iso_colors = {"ERCO": "#ffaa00", "CISO": "#00d1ff", "MISO": "#00ff96",
                      "PJM": "#ad7fff", "SWPP": "#ff4b4b", "NYIS": "#ff9900"}

        fig_iso = go.Figure()
        iso_status = []

        for iso_code in selected_isos:
            if iso_code == "ERCO" and eia_hourly is not None:
                iso_data = eia_hourly
            else:
                iso_data = fetch_eia_hourly_grid(iso_code, days_back=3)

            if iso_data is None or iso_data.empty:
                iso_status.append(f"{ISO_OPTIONS[iso_code]}: no data")
                continue

            # Pivot and compute net load
            iso_piv = iso_data.pivot_table(index="period", columns="fueltype", values="value", aggfunc="sum")
            iso_piv = iso_piv.sort_index()

            w_cols = [c for c in iso_piv.columns if "WND" in c.upper() or "WIND" in c.upper()]
            s_cols = [c for c in iso_piv.columns if "SUN" in c.upper() or "SOL" in c.upper()]
            iso_piv["total"] = iso_piv.sum(axis=1)
            iso_piv["renewables"] = iso_piv[w_cols + s_cols].sum(axis=1) if (w_cols or s_cols) else 0
            iso_piv["net_load"] = iso_piv["total"] - iso_piv["renewables"]

            # Use only the most recent full day
            latest_date = iso_piv.index.max().date()
            day_mask = iso_piv.index.date == latest_date
            if day_mask.sum() < 5:
                # Try previous day if today has too few points
                prev_date = latest_date - timedelta(days=1)
                day_mask = iso_piv.index.date == prev_date
            day_data = iso_piv.loc[day_mask]

            if day_data.empty:
                iso_status.append(f"{ISO_OPTIONS[iso_code]}: no recent day data")
                continue

            # Plot by hour of day for comparison
            day_data = day_data.copy()
            day_data["hour"] = day_data.index.hour + day_data.index.minute / 60

            fig_iso.add_trace(go.Scatter(
                x=day_data["hour"], y=day_data["net_load"],
                mode="lines", name=ISO_OPTIONS[iso_code],
                line=dict(color=iso_colors.get(iso_code, "#ffffff"), width=2.5),
            ))
            iso_status.append(f"{ISO_OPTIONS[iso_code]}: {day_data['net_load'].min():,.0f}–{day_data['net_load'].max():,.0f} MW")

        fig_iso.update_layout(
            template="plotly_dark", height=450, margin=dict(t=10, b=0, l=0, r=0),
            xaxis_title="Hour of Day", yaxis_title="Net Load (MW)",
            hovermode="x unified",
            legend=dict(orientation="h", yanchor="bottom", y=1.02),
            xaxis=dict(dtick=2, range=[0, 24]),
        )
        st.plotly_chart(fig_iso, use_container_width=True)

        for s in iso_status:
            st.caption(s)
    else:
        st.info("Select at least one ISO to display.")


# ════════════════════════════════════════════════
# TAB 2: HEAT RATE
# ════════════════════════════════════════════════
with tab2:
    st.subheader("Implied Market Heat Rate")
    st.caption("Heat rate = power price / gas price × 1,000. Measures how efficiently the marginal generator converts fuel to electricity.")

    # Use best available gas price: yfinance futures (real-time) > EIA daily > EIA weekly
    if gas_futures is not None:
        _hr_gas = gas_futures["price"]
        _hr_gas_stale_days = (pd.Timestamp.now(tz="US/Central") - gas_futures["date"].tz_convert("US/Central")).days if gas_futures["date"].tzinfo else 0
        _hr_gas_label = f"NG Futures ({gas_futures['date'].strftime('%m/%d')})"
    elif gas_price_daily is not None and not gas_price_daily.empty:
        _hr_gas = float(gas_price_daily["value"].iloc[-1])
        _hr_gas_date_obj = gas_price_daily["period"].iloc[-1]
        _hr_gas_stale_days = (pd.Timestamp.now() - _hr_gas_date_obj).days
        _hr_gas_label = f"Gas (HH {_hr_gas_date_obj.strftime('%m/%d')})"
        if _hr_gas_stale_days > 3:
            _hr_gas_label += f" — {_hr_gas_stale_days}d old"
    elif gas_price:
        _hr_gas = gas_price
        _hr_gas_stale_days = 0
        _hr_gas_label = "Gas (HH weekly)"
    else:
        _hr_gas = None
        _hr_gas_stale_days = 0
        _hr_gas_label = "Gas"

    # Use ERCOT API avg RT price when available (more representative than single 15-min)
    if ercot_rt_spp is not None and not ercot_rt_spp.empty:
        _hr_power = ercot_rt_spp["settlementPointPrice"].mean()
        _hr_power_label = "Avg RT Price (today)"
    else:
        _hr_power = power_price
        _hr_power_label = "Latest RT Price (15-min)"

    if _hr_gas and _hr_power:
        if _hr_gas_stale_days > 5:
            st.warning(f"Gas price data is {_hr_gas_stale_days} days old (EIA lag). Heat rate calculations may not reflect current fuel costs.")

        implied_hr = (_hr_power / _hr_gas) * 1000
        hr_efficiency = "Efficient" if implied_hr < 8000 else "Average" if implied_hr < 10000 else "Stressed"

        h1, h2, h3, h4 = st.columns(4)
        h1.metric("Implied Heat Rate", f"{implied_hr:,.0f} BTU/kWh")
        h2.metric("Market Condition", hr_efficiency)
        h3.metric(_hr_gas_label, f"${_hr_gas:.2f}/MMBtu",
                  help="ERCOT generators price off Houston Ship Channel / Waha — basis may differ ±$0.25–1.50/MMBtu.")
        h4.metric("Marginal Fuel Cost", f"${_hr_gas * implied_hr / 1000:.2f}/MWh")

        # ── Reference heat rate comparison ──
        fig_hr = go.Figure()
        hr_names = list(HEAT_RATES.keys())
        hr_values = list(HEAT_RATES.values())
        hr_colors = ["#00d1ff"] * len(hr_names)
        hr_names.insert(0, "Market Implied")
        hr_values.insert(0, implied_hr)
        hr_colors.insert(0, "#ffaa00")

        fig_hr.add_trace(go.Bar(
            x=hr_names, y=hr_values,
            marker_color=hr_colors,
            text=[f"{v:,.0f}" for v in hr_values],
            textposition="outside",
        ))
        fig_hr.update_layout(
            template="plotly_dark", height=350, margin=dict(t=30, b=0, l=0, r=0),
            yaxis_title="Heat Rate (BTU/kWh)",
        )
        st.plotly_chart(fig_hr, use_container_width=True)

        # ── Hourly Implied Heat Rate Curve ──
        if ercot_rt_spp is not None and not ercot_rt_spp.empty:
            st.subheader("Hourly Implied Heat Rate")
            st.caption("Heat rate by hour reveals which generator class is on the margin — efficient CCGTs at baseload, peakers at peak.")

            rt_hourly_hr = ercot_rt_spp.groupby("deliveryHour")["settlementPointPrice"].mean().reset_index()
            rt_hourly_hr.columns = ["hour", "rt_price"]
            rt_hourly_hr["implied_hr"] = (rt_hourly_hr["rt_price"] / _hr_gas) * 1000

            fig_hr_hourly = go.Figure()

            # Reference bands for plant types
            fig_hr_hourly.add_hrect(y0=6000, y1=7500, fillcolor="rgba(0, 255, 150, 0.06)", line_width=0,
                                     annotation_text="CCGT range", annotation_position="top left")
            fig_hr_hourly.add_hrect(y0=9000, y1=11000, fillcolor="rgba(255, 75, 75, 0.06)", line_width=0,
                                     annotation_text="Peaker range", annotation_position="top left")

            fig_hr_hourly.add_trace(go.Scatter(
                x=rt_hourly_hr["hour"], y=rt_hourly_hr["implied_hr"],
                mode="lines+markers", name="RT Implied Heat Rate",
                line=dict(color="#ffaa00", width=3),
                hovertemplate="HE%{x}: %{y:,.0f} BTU/kWh<extra></extra>",
            ))

            # DAM implied heat rate overlay
            if ercot_dam_spp is not None and not ercot_dam_spp.empty:
                dam_hr_df = ercot_dam_spp.copy()
                dam_hr_df["hour"] = dam_hr_df["hourEnding"].apply(
                    lambda h: int(h.split(":")[0]) if isinstance(h, str) else int(h)
                )
                dam_hourly_hr = dam_hr_df.groupby("hour")["settlementPointPrice"].mean().reset_index()
                dam_hourly_hr.columns = ["hour", "dam_price"]
                dam_hourly_hr["implied_hr"] = (dam_hourly_hr["dam_price"] / _hr_gas) * 1000

                fig_hr_hourly.add_trace(go.Scatter(
                    x=dam_hourly_hr["hour"], y=dam_hourly_hr["implied_hr"],
                    mode="lines", name="DAM Implied Heat Rate",
                    line=dict(color="#00d1ff", width=2, dash="dash"),
                ))

            fig_hr_hourly.update_layout(
                template="plotly_dark", height=400, margin=dict(t=10, b=0, l=0, r=0),
                xaxis_title="Hour", yaxis_title="Implied Heat Rate (BTU/kWh)",
                xaxis=dict(dtick=2), hovermode="x unified",
                legend=dict(orientation="h", yanchor="bottom", y=1.02),
            )
            st.plotly_chart(fig_hr_hourly, use_container_width=True)

            # Hourly heat rate metrics
            hr_min = rt_hourly_hr["implied_hr"].min()
            hr_max = rt_hourly_hr["implied_hr"].max()
            hr_min_hour = rt_hourly_hr.loc[rt_hourly_hr["implied_hr"].idxmin(), "hour"]
            hr_max_hour = rt_hourly_hr.loc[rt_hourly_hr["implied_hr"].idxmax(), "hour"]
            hm1, hm2, hm3 = st.columns(3)
            hm1.metric("Most Efficient Hour", f"HE{hr_min_hour:.0f}: {hr_min:,.0f} BTU/kWh")
            hm2.metric("Least Efficient Hour", f"HE{hr_max_hour:.0f}: {hr_max:,.0f} BTU/kWh")
            hm3.metric("Intraday Range", f"{hr_max - hr_min:,.0f} BTU/kWh")

        # ── System Lambda (ERCOT's actual marginal price) ──
        if ercot_sced_lambda is not None and not ercot_sced_lambda.empty:
            st.subheader("System Lambda — ERCOT Marginal Price")
            st.caption("System Lambda is the actual marginal energy offer price clearing each SCED interval (~5 min). This IS the marginal unit's cost — not an approximation.")

            fig_lambda = go.Figure()

            # SCED Lambda (5-min)
            fig_lambda.add_trace(go.Scatter(
                x=ercot_sced_lambda["SCEDTimestamp"], y=ercot_sced_lambda["systemLambda"],
                mode="lines", name="SCED Lambda (5-min)",
                line=dict(color="#ffaa00", width=1.5),
            ))

            # DAM Lambda overlay
            if ercot_dam_lambda is not None and not ercot_dam_lambda.empty:
                dam_lam = ercot_dam_lambda.copy()
                dam_lam["hour"] = dam_lam["hourEnding"].apply(
                    lambda h: int(h.split(":")[0]) if isinstance(h, str) else int(h)
                )
                # Map DAM hours onto today's timestamps for overlay
                # Match SCED Lambda timezone (ERCOT = US/Central)
                dam_lam["timestamp"] = (
                    pd.to_datetime(dam_lam["deliveryDate"])
                    + pd.to_timedelta(dam_lam["hour"], unit="h")
                ).dt.tz_localize("US/Central")
                fig_lambda.add_trace(go.Scatter(
                    x=dam_lam["timestamp"], y=dam_lam["systemLambda"],
                    mode="lines+markers", name="DAM Lambda (hourly)",
                    line=dict(color="#00d1ff", width=2, dash="dash"),
                ))

            fig_lambda.update_layout(
                template="plotly_dark", height=380, margin=dict(t=10, b=0, l=0, r=0),
                yaxis_title="System Lambda ($/MWh)", hovermode="x unified",
                legend=dict(orientation="h", yanchor="bottom", y=1.02),
            )
            st.plotly_chart(fig_lambda, use_container_width=True)

            # Lambda-derived heat rate
            sced_avg = ercot_sced_lambda["systemLambda"].mean()
            sced_implied_hr = (sced_avg / _hr_gas) * 1000
            lm1, lm2, lm3 = st.columns(3)
            lm1.metric("Avg SCED Lambda", f"${sced_avg:.2f}/MWh")
            lm2.metric("Lambda-Implied Avg Heat Rate", f"{sced_implied_hr:,.0f} BTU/kWh")
            lm3.metric("SCED Lambda Range",
                        f"${ercot_sced_lambda['systemLambda'].min():.2f} – ${ercot_sced_lambda['systemLambda'].max():.2f}/MWh")

        # ── Heat Rate vs Net Load Scatter ──
        if ercot_rt_spp is not None and not ercot_rt_spp.empty:
            st.subheader("Heat Rate vs Net Load")
            st.caption("Shows the dispatch curve slope — how fast marginal efficiency degrades as net load rises. Steeper = tighter supply.")

            # Build hourly heat rate + net load pairs
            rt_hourly_scatter = ercot_rt_spp.groupby("deliveryHour")["settlementPointPrice"].mean().reset_index()
            rt_hourly_scatter.columns = ["hour", "rt_price"]
            rt_hourly_scatter["implied_hr"] = (rt_hourly_scatter["rt_price"] / _hr_gas) * 1000

            # Match with net load by hour from df_today
            df_today_hourly = df_today.copy()
            df_today_hourly["hour_int"] = df_today_hourly["hour"].round().astype(int)
            net_load_hourly = df_today_hourly.groupby("hour_int")["net_load"].mean().reset_index()
            net_load_hourly.columns = ["hour", "net_load"]

            scatter_data = pd.merge(rt_hourly_scatter, net_load_hourly, on="hour", how="inner")

            if not scatter_data.empty:
                fig_scatter = go.Figure()
                fig_scatter.add_trace(go.Scatter(
                    x=scatter_data["net_load"], y=scatter_data["implied_hr"],
                    mode="markers+text", name="Hourly",
                    marker=dict(size=12, color=scatter_data["hour"],
                                colorscale="Turbo", showscale=True,
                                colorbar=dict(title="Hour")),
                    text=[f"HE{h:.0f}" for h in scatter_data["hour"]],
                    textposition="top center", textfont=dict(size=9),
                    hovertemplate="Net Load: %{x:,.0f} MW<br>Heat Rate: %{y:,.0f} BTU/kWh<extra></extra>",
                ))

                # Trend line
                if len(scatter_data) > 3:
                    z = np.polyfit(scatter_data["net_load"], scatter_data["implied_hr"], 1)
                    p = np.poly1d(z)
                    x_trend = np.linspace(scatter_data["net_load"].min(), scatter_data["net_load"].max(), 50)
                    fig_scatter.add_trace(go.Scatter(
                        x=x_trend, y=p(x_trend),
                        mode="lines", name="Trend",
                        line=dict(color="#ff4b4b", width=1.5, dash="dot"),
                    ))
                    slope = z[0]
                    st.caption(f"Slope: {slope:.2f} BTU/kWh per MW — each additional GW of net load raises the marginal heat rate by ~{slope * 1000:.0f} BTU/kWh.")

                fig_scatter.update_layout(
                    template="plotly_dark", height=420, margin=dict(t=10, b=0, l=0, r=0),
                    xaxis_title="Net Load (MW)", yaxis_title="Implied Heat Rate (BTU/kWh)",
                    hovermode="closest",
                )
                st.plotly_chart(fig_scatter, use_container_width=True)

        # ── Heat Rate Duration Curve ──
        if ercot_rt_spp is not None and not ercot_rt_spp.empty:
            st.subheader("Heat Rate Duration Curve")
            st.caption("Hours sorted by implied heat rate from low to high. Shows what fraction of the day is served by efficient vs stressed units.")

            # Use per-interval heat rates for finer granularity
            rt_all = ercot_rt_spp.copy()
            rt_all["implied_hr"] = (rt_all["settlementPointPrice"] / _hr_gas) * 1000
            sorted_hr = rt_all["implied_hr"].sort_values().reset_index(drop=True)
            sorted_hr.index = (sorted_hr.index / len(sorted_hr) * 100)  # percentile

            fig_duration = go.Figure()
            fig_duration.add_trace(go.Scatter(
                x=sorted_hr.index, y=sorted_hr.values,
                mode="lines", name="Heat Rate Duration",
                line=dict(color="#ffaa00", width=2.5),
                fill="tozeroy", fillcolor="rgba(255, 170, 0, 0.1)",
            ))

            # Reference lines for plant types
            for name, hr_val in HEAT_RATES.items():
                if name in ("Combined Cycle", "Combustion Turbine"):
                    fig_duration.add_hline(y=hr_val, line_dash="dot", line_width=1,
                                            line_color="#666",
                                            annotation_text=name,
                                            annotation_position="bottom right")

            fig_duration.update_layout(
                template="plotly_dark", height=350, margin=dict(t=10, b=0, l=0, r=0),
                xaxis_title="% of Intervals", yaxis_title="Implied Heat Rate (BTU/kWh)",
                hovermode="x unified",
            )
            st.plotly_chart(fig_duration, use_container_width=True)

            # Duration stats
            pct_efficient = (rt_all["implied_hr"] < 8000).mean() * 100
            pct_stressed = (rt_all["implied_hr"] > 10000).mean() * 100
            d1, d2, d3 = st.columns(3)
            d1.metric("% Hours Efficient (<8,000)", f"{pct_efficient:.0f}%")
            d2.metric("% Hours Stressed (>10,000)", f"{pct_stressed:.0f}%")
            d3.metric("Median Heat Rate", f"{rt_all['implied_hr'].median():,.0f} BTU/kWh")

        # ── Gas Price Trend ──
        _has_gas_trend = (gas_futures is not None) or (gas_price_daily is not None and not gas_price_daily.empty)
        if _has_gas_trend:
            st.subheader("Gas Price Trend (30-Day)")
            st.caption("Henry Hub natural gas pricing. ERCOT generators price off Houston Ship Channel / Waha — basis differential applies.")

            fig_gas = go.Figure()

            # yfinance futures history (most current)
            if gas_futures is not None and "history" in gas_futures:
                gf_hist = gas_futures["history"]
                fig_gas.add_trace(go.Scatter(
                    x=gf_hist.index, y=gf_hist["value"],
                    mode="lines+markers", name="NG Futures (front month)",
                    line=dict(color="#ffaa00", width=2.5),
                ))

            # EIA daily spot (may have longer history but lagged)
            if gas_price_daily is not None and not gas_price_daily.empty:
                fig_gas.add_trace(go.Scatter(
                    x=gas_price_daily["period"], y=gas_price_daily["value"],
                    mode="lines", name="EIA Henry Hub Daily",
                    line=dict(color="#ff9900", width=1.5, dash="dot"),
                ))

            fig_gas.update_layout(
                template="plotly_dark", height=300, margin=dict(t=10, b=0, l=0, r=0),
                yaxis_title="$/MMBtu", hovermode="x unified",
                legend=dict(orientation="h", yanchor="bottom", y=1.02),
            )
            st.plotly_chart(fig_gas, use_container_width=True)

        # Heat rate interpretation guide
        with st.expander("Heat Rate Guide"):
            st.markdown(f"""
| Range | Interpretation | Typical Plant |
|-------|---------------|--------------|
| < 7,000 BTU/kWh | Very efficient | New CCGT (H-class turbine) |
| 7,000 – 8,500 | Normal | Efficient gas fleet clearing |
| 8,500 – 10,000 | Elevated | Older CCGT or simple-cycle CT |
| > 10,000 | Stressed | Peakers / old steam units |

**Current implied: {implied_hr:,.0f} BTU/kWh** — marginal generator at **{hr_efficiency.lower()}** efficiency.

*Note: Implied heat rate uses Henry Hub spot (${_hr_gas:.2f}/MMBtu). ERCOT generators price off
Houston Ship Channel (coastal) and Waha (West Texas). Typical basis: ±$0.25–1.50/MMBtu.*
""")
    else:
        missing = []
        if not _hr_gas:
            missing.append("Henry Hub gas price (EIA API key required)")
        if not _hr_power:
            missing.append("ERCOT settlement point price")
        st.warning(f"Heat rate calculation requires: {', '.join(missing)}")

        st.subheader("Reference Heat Rates by Plant Type")
        fig_hr_ref = go.Figure()
        fig_hr_ref.add_trace(go.Bar(
            x=list(HEAT_RATES.keys()), y=list(HEAT_RATES.values()),
            marker_color="#00d1ff",
            text=[f"{v:,.0f}" for v in HEAT_RATES.values()],
            textposition="outside",
        ))
        fig_hr_ref.update_layout(
            template="plotly_dark", height=400, margin=dict(t=30, b=0, l=0, r=0),
            yaxis_title="Heat Rate (BTU/kWh)",
        )
        st.plotly_chart(fig_hr_ref, use_container_width=True)


# ════════════════════════════════════════════════
# TAB 3: SPARK SPREAD
# ════════════════════════════════════════════════

# Variable O&M by plant type ($/MWh) — EIA AEO 2024 / NREL ATB estimates
VOM_COSTS = {
    "Combined Cycle": 3.5,
    "Combustion Turbine": 6.0,
    "Steam Turbine": 5.0,
    "Coal": 5.5,
    "Nuclear": 3.0,
}

with tab3:
    st.subheader("Spark Spread Analysis")
    st.caption("Spark spread = power price − fuel cost. Net margin = spark spread − VOM. Positive net margin means the plant covers both fuel and variable operating costs.")

    # Use best available gas price: yfinance > EIA daily > EIA weekly (consistent with heat rate tab)
    if gas_futures is not None:
        _sp_gas = gas_futures["price"]
        _sp_gas_stale_days = 0
        _sp_gas_label = f"NG Futures ({gas_futures['date'].strftime('%m/%d')})"
    elif gas_price_daily is not None and not gas_price_daily.empty:
        _sp_gas = float(gas_price_daily["value"].iloc[-1])
        _sp_gas_date = gas_price_daily["period"].iloc[-1]
        _sp_gas_stale_days = (pd.Timestamp.now() - _sp_gas_date).days
        _sp_gas_label = f"Gas (HH {_sp_gas_date.strftime('%m/%d')})"
        if _sp_gas_stale_days > 3:
            _sp_gas_label += f" — {_sp_gas_stale_days}d old"
    elif gas_price:
        _sp_gas = gas_price
        _sp_gas_stale_days = 0
        _sp_gas_label = "Gas (HH weekly)"
    else:
        _sp_gas = None
        _sp_gas_stale_days = 0
        _sp_gas_label = "Gas"

    if _sp_gas and power_price:
        # Use ERCOT API avg RT price for today if available
        if ercot_rt_spp is not None and not ercot_rt_spp.empty:
            _spark_power_price = ercot_rt_spp["settlementPointPrice"].mean()
            _price_label = "Avg RT Price (today)"
        else:
            _spark_power_price = power_price
            _price_label = "Latest RT Price (15-min)"

        # Calculate spreads for all plant types
        coal_price_mmbtu = 1.50   # PRB/lignite delivered (EIA Coal Markets Report)
        nuc_price_mmbtu = 0.65    # Uranium fuel equivalent (NEI: $0.50–0.70/MMBtu range)

        FUEL_PRICES = {
            "Combined Cycle": (_sp_gas, "gas"),
            "Combustion Turbine": (_sp_gas, "gas"),
            "Steam Turbine": (_sp_gas, "gas"),
            "Coal": (coal_price_mmbtu, "coal"),
            "Nuclear": (nuc_price_mmbtu, "uranium"),
        }

        spreads = {}
        for plant, hr in HEAT_RATES.items():
            fuel_px, fuel_label = FUEL_PRICES[plant]
            fuel_cost = fuel_px * hr / 1000
            spark = _spark_power_price - fuel_cost
            vom = VOM_COSTS[plant]
            net_margin = spark - vom
            spreads[plant] = {
                "heat_rate": hr, "fuel_cost": fuel_cost, "spark": spark,
                "vom": vom, "net_margin": net_margin,
                "fuel_price": fuel_px, "fuel_label": fuel_label,
            }

        cc = spreads["Combined Cycle"]
        ct = spreads["Combustion Turbine"]

        if _sp_gas_stale_days > 5:
            st.warning(f"Gas price data is {_sp_gas_stale_days} days old (EIA lag). Spark spreads may not reflect current fuel costs.")

        s1, s2, s3, s4, s5 = st.columns(5)
        s1.metric(_price_label, f"${_spark_power_price:.2f}/MWh")
        s2.metric(_sp_gas_label, f"${_sp_gas:.2f}/MMBtu")
        s3.metric("CCGT Spark", f"${cc['spark']:.2f}/MWh")
        s4.metric("CCGT Net Margin", f"${cc['net_margin']:.2f}/MWh",
                  help=f"Spark ${cc['spark']:.2f} − VOM ${cc['vom']:.1f} = ${cc['net_margin']:.2f}",
                  delta=f"{'In money' if cc['net_margin'] > 0 else 'Out of money'}",
                  delta_color="normal" if cc["net_margin"] > 0 else "inverse")
        s5.metric("CT Net Margin", f"${ct['net_margin']:.2f}/MWh",
                  delta=f"{'In money' if ct['net_margin'] > 0 else 'Out of money'}",
                  delta_color="normal" if ct["net_margin"] > 0 else "inverse")

        # ── Stacked margin waterfall ──
        fig_spark = go.Figure()
        plant_names = list(spreads.keys())
        fuel_costs = [s["fuel_cost"] for s in spreads.values()]
        vom_vals = [s["vom"] for s in spreads.values()]
        net_margins = [s["net_margin"] for s in spreads.values()]

        fig_spark.add_trace(go.Bar(
            x=plant_names, y=fuel_costs, name="Fuel Cost",
            marker_color="#ff9900",
            text=[f"${v:.1f}" for v in fuel_costs], textposition="inside",
        ))
        fig_spark.add_trace(go.Bar(
            x=plant_names, y=vom_vals, name="VOM",
            marker_color="#888888",
            text=[f"${v:.1f}" for v in vom_vals], textposition="inside",
        ))
        fig_spark.add_trace(go.Bar(
            x=plant_names, y=net_margins, name="Net Margin",
            marker_color=["#00ff96" if v > 0 else "#ff4b4b" for v in net_margins],
            text=[f"${v:+.1f}" for v in net_margins], textposition="inside",
        ))
        fig_spark.add_hline(y=_spark_power_price, line_dash="dot", line_color="#00d1ff",
                            annotation_text=f"Power ${_spark_power_price:.1f}/MWh")
        fig_spark.update_layout(
            template="plotly_dark", height=420, margin=dict(t=30, b=0, l=0, r=0),
            barmode="stack", yaxis_title="$/MWh", hovermode="x unified",
            legend=dict(orientation="h", yanchor="bottom", y=1.02),
        )
        st.plotly_chart(fig_spark, use_container_width=True)

        # ── Detailed table ──
        df_spreads = pd.DataFrame([
            {
                "Plant Type": plant,
                "Heat Rate": f"{d['heat_rate']:,}",
                "Fuel ($/MMBtu)": f"${d['fuel_price']:.2f} ({d['fuel_label']})",
                "Fuel Cost ($/MWh)": f"${d['fuel_cost']:.2f}",
                "Spark Spread": f"${d['spark']:+.2f}",
                "VOM ($/MWh)": f"${d['vom']:.1f}",
                "Net Margin": f"${d['net_margin']:+.2f}",
                "Status": "In money" if d["net_margin"] > 0 else "Out of money",
            }
            for plant, d in spreads.items()
        ])
        st.dataframe(df_spreads, use_container_width=True, hide_index=True)

        # ── Hourly Spark Spread Curve ──
        if ercot_rt_spp is not None and not ercot_rt_spp.empty:
            st.subheader("Hourly Spark Spread")
            st.caption("CCGT and CT spark spreads by hour. Green = profitable, red = out of money. Shows which hours each plant type can profitably run.")

            rt_hourly_sp = ercot_rt_spp.groupby("deliveryHour")["settlementPointPrice"].mean().reset_index()
            rt_hourly_sp.columns = ["hour", "rt_price"]

            cc_hr = HEAT_RATES["Combined Cycle"]
            ct_hr = HEAT_RATES["Combustion Turbine"]
            cc_fuel = _sp_gas * cc_hr / 1000
            ct_fuel = _sp_gas * ct_hr / 1000
            cc_vom = VOM_COSTS["Combined Cycle"]
            ct_vom = VOM_COSTS["Combustion Turbine"]

            rt_hourly_sp["ccgt_margin"] = rt_hourly_sp["rt_price"] - cc_fuel - cc_vom
            rt_hourly_sp["ct_margin"] = rt_hourly_sp["rt_price"] - ct_fuel - ct_vom

            fig_hourly_sp = go.Figure()

            # CCGT margin bars
            fig_hourly_sp.add_trace(go.Bar(
                x=rt_hourly_sp["hour"], y=rt_hourly_sp["ccgt_margin"],
                name="CCGT Net Margin",
                marker_color=["#00ff96" if v > 0 else "#ff4b4b" for v in rt_hourly_sp["ccgt_margin"]],
                opacity=0.7,
            ))
            # CT margin line
            fig_hourly_sp.add_trace(go.Scatter(
                x=rt_hourly_sp["hour"], y=rt_hourly_sp["ct_margin"],
                mode="lines+markers", name="CT Net Margin",
                line=dict(color="#ad7fff", width=2),
            ))
            fig_hourly_sp.add_hline(y=0, line_color="white", line_width=0.5)

            fig_hourly_sp.update_layout(
                template="plotly_dark", height=380, margin=dict(t=10, b=0, l=0, r=0),
                xaxis_title="Hour", yaxis_title="Net Margin ($/MWh)",
                xaxis=dict(dtick=2), hovermode="x unified",
                legend=dict(orientation="h", yanchor="bottom", y=1.02),
            )
            st.plotly_chart(fig_hourly_sp, use_container_width=True)

            # Profitability hours
            ccgt_profitable_hrs = (rt_hourly_sp["ccgt_margin"] > 0).sum()
            ct_profitable_hrs = (rt_hourly_sp["ct_margin"] > 0).sum()
            total_hrs = len(rt_hourly_sp)
            ccgt_avg_margin = rt_hourly_sp["ccgt_margin"].mean()
            ct_avg_margin = rt_hourly_sp["ct_margin"].mean()

            ph1, ph2, ph3, ph4 = st.columns(4)
            ph1.metric("CCGT Profitable Hours", f"{ccgt_profitable_hrs}/{total_hrs}")
            ph2.metric("CCGT Avg Net Margin", f"${ccgt_avg_margin:.2f}/MWh")
            ph3.metric("CT Profitable Hours", f"{ct_profitable_hrs}/{total_hrs}")
            ph4.metric("CT Avg Net Margin", f"${ct_avg_margin:.2f}/MWh")

            # ── DAM vs RT Spark Spread ──
            if ercot_dam_spp is not None and not ercot_dam_spp.empty:
                st.subheader("DAM vs RT Spark Spread (CCGT)")
                st.caption("Compares expected profitability (day-ahead) vs realized (real-time). Divergence = the market mispriced which unit would clear.")

                dam_sp = ercot_dam_spp.copy()
                dam_sp["hour"] = dam_sp["hourEnding"].apply(
                    lambda h: int(h.split(":")[0]) if isinstance(h, str) else int(h)
                )
                dam_hourly_sp = dam_sp.groupby("hour")["settlementPointPrice"].mean().reset_index()
                dam_hourly_sp.columns = ["hour", "dam_price"]
                dam_hourly_sp["dam_ccgt_margin"] = dam_hourly_sp["dam_price"] - cc_fuel - cc_vom

                merged_sp = pd.merge(
                    rt_hourly_sp[["hour", "ccgt_margin"]],
                    dam_hourly_sp[["hour", "dam_ccgt_margin"]],
                    on="hour", how="inner"
                )

                if not merged_sp.empty:
                    fig_dam_rt = go.Figure()
                    fig_dam_rt.add_trace(go.Scatter(
                        x=merged_sp["hour"], y=merged_sp["dam_ccgt_margin"],
                        mode="lines", name="DAM CCGT Margin",
                        line=dict(color="#ffaa00", width=2, dash="dash"),
                    ))
                    fig_dam_rt.add_trace(go.Scatter(
                        x=merged_sp["hour"], y=merged_sp["ccgt_margin"],
                        mode="lines", name="RT CCGT Margin",
                        line=dict(color="#00d1ff", width=2.5),
                    ))
                    fig_dam_rt.add_hline(y=0, line_color="white", line_width=0.5)

                    fig_dam_rt.update_layout(
                        template="plotly_dark", height=340, margin=dict(t=10, b=0, l=0, r=0),
                        xaxis_title="Hour", yaxis_title="CCGT Net Margin ($/MWh)",
                        xaxis=dict(dtick=2), hovermode="x unified",
                        legend=dict(orientation="h", yanchor="bottom", y=1.02),
                    )
                    st.plotly_chart(fig_dam_rt, use_container_width=True)

            # ── System Lambda vs Fuel Cost ──
            if ercot_sced_lambda is not None and not ercot_sced_lambda.empty:
                st.subheader("System Lambda vs Fuel Cost")
                st.caption("ERCOT's actual marginal clearing price vs plant fuel costs. Lambda above a fuel cost line = that plant type is profitable.")

                fig_lam_fc = go.Figure()
                fig_lam_fc.add_trace(go.Scatter(
                    x=ercot_sced_lambda["SCEDTimestamp"],
                    y=ercot_sced_lambda["systemLambda"],
                    mode="lines", name="SCED Lambda",
                    line=dict(color="#ffaa00", width=1.5),
                ))

                # Fuel cost + VOM lines for each plant type
                fc_lines = {
                    "CCGT (fuel+VOM)": cc_fuel + cc_vom,
                    "CT (fuel+VOM)": ct_fuel + ct_vom,
                    "Coal (fuel+VOM)": coal_price_mmbtu * HEAT_RATES["Coal"] / 1000 + VOM_COSTS.get("Coal", 5.5),
                }
                fc_colors = {"CCGT (fuel+VOM)": "#00d1ff", "CT (fuel+VOM)": "#ad7fff", "Coal (fuel+VOM)": "#888888"}

                for label, cost in fc_lines.items():
                    fig_lam_fc.add_hline(
                        y=cost, line_dash="dot", line_width=1.5,
                        line_color=fc_colors[label],
                        annotation_text=f"{label}: ${cost:.1f}",
                        annotation_position="bottom right",
                    )

                fig_lam_fc.update_layout(
                    template="plotly_dark", height=380, margin=dict(t=10, b=0, l=0, r=0),
                    yaxis_title="$/MWh", hovermode="x unified",
                    legend=dict(orientation="h", yanchor="bottom", y=1.02),
                )
                st.plotly_chart(fig_lam_fc, use_container_width=True)

                # Lambda profitability stats
                lam_vals = ercot_sced_lambda["systemLambda"]
                lam_above_cc = (lam_vals > cc_fuel + cc_vom).mean() * 100
                lam_above_ct = (lam_vals > ct_fuel + ct_vom).mean() * 100
                ll1, ll2, ll3 = st.columns(3)
                ll1.metric("Lambda > CCGT Cost", f"{lam_above_cc:.0f}% of intervals")
                ll2.metric("Lambda > CT Cost", f"{lam_above_ct:.0f}% of intervals")
                ll3.metric("Avg Lambda", f"${lam_vals.mean():.2f}/MWh")

            # ── Spark Spread Duration Curve ──
            st.subheader("Spark Spread Duration Curve")
            st.caption("CCGT net margin sorted highest to lowest. Area above zero = profit-hours, below = loss-hours.")

            # Per-interval granularity
            rt_all_sp = ercot_rt_spp.copy()
            rt_all_sp["ccgt_margin"] = rt_all_sp["settlementPointPrice"] - cc_fuel - cc_vom
            sorted_margin = rt_all_sp["ccgt_margin"].sort_values(ascending=False).reset_index(drop=True)
            sorted_margin.index = (sorted_margin.index / len(sorted_margin) * 100)

            fig_dur_sp = go.Figure()
            fig_dur_sp.add_trace(go.Scatter(
                x=sorted_margin.index, y=sorted_margin.values,
                mode="lines", name="CCGT Net Margin",
                line=dict(color="#00d1ff", width=2.5),
                fill="tozeroy",
                fillcolor="rgba(0, 209, 255, 0.1)",
            ))
            fig_dur_sp.add_hline(y=0, line_color="white", line_width=1)

            fig_dur_sp.update_layout(
                template="plotly_dark", height=320, margin=dict(t=10, b=0, l=0, r=0),
                xaxis_title="% of Intervals (sorted best → worst)",
                yaxis_title="CCGT Net Margin ($/MWh)",
                hovermode="x unified",
            )
            st.plotly_chart(fig_dur_sp, use_container_width=True)

            # Duration stats
            pct_positive = (rt_all_sp["ccgt_margin"] > 0).mean() * 100
            avg_profit = rt_all_sp.loc[rt_all_sp["ccgt_margin"] > 0, "ccgt_margin"].mean() if pct_positive > 0 else 0
            avg_loss = rt_all_sp.loc[rt_all_sp["ccgt_margin"] <= 0, "ccgt_margin"].mean() if pct_positive < 100 else 0
            ds1, ds2, ds3 = st.columns(3)
            ds1.metric("In-Money Intervals", f"{pct_positive:.0f}%")
            ds2.metric("Avg Profit (when in-money)", f"${avg_profit:.2f}/MWh" if avg_profit else "N/A")
            ds3.metric("Avg Loss (when out)", f"${avg_loss:.2f}/MWh" if avg_loss else "N/A")

        # Dark spread note
        coal_cost = coal_price_mmbtu * HEAT_RATES["Coal"] / 1000
        dark_spread = _spark_power_price - coal_cost
        dark_net = dark_spread - VOM_COSTS["Coal"]
        st.markdown(
            f"**Dark Spread (Coal):** ${dark_spread:+.2f}/MWh spark, ${dark_net:+.2f}/MWh net margin — "
            f"using ${coal_price_mmbtu:.2f}/MMBtu PRB/lignite + ${VOM_COSTS['Coal']:.1f}/MWh VOM"
        )
    else:
        missing = []
        if not _sp_gas:
            missing.append("Henry Hub gas price (EIA API key required)")
        if not power_price:
            missing.append("ERCOT settlement point price")
        st.warning(f"Spark spread calculation requires: {', '.join(missing)}")


# ════════════════════════════════════════════════
# TAB 4: STACK ANALYSIS (MERIT ORDER)
# ════════════════════════════════════════════════
with tab4:
    st.subheader("Generation Stack — Merit Order")
    st.caption("Dispatch stack ranked by marginal cost. Cheapest generators run first (baseload), most expensive last (peakers).")

    # Build stack from ERCOT capacity data + current generation
    latest_fuel = {}
    for ft in fuel_types:
        if ft in df_fuel.columns:
            val = df_fuel[ft].iloc[-1]
            latest_fuel[ft] = val  # include negative (storage charging)

    # Marginal costs: fuel cost + VOM for full short-run marginal cost (SRMC)
    # Consistent with spark spread tab methodology
    nuc_marginal = 0.65 * HEAT_RATES["Nuclear"] / 1000 + VOM_COSTS.get("Nuclear", 3.0)
    coal_marginal = 1.50 * HEAT_RATES["Coal"] / 1000 + VOM_COSTS.get("Coal", 5.5)
    # Use best gas price: yfinance > EIA daily > EIA weekly > fallback
    if gas_futures:
        _stack_gas = gas_futures["price"]
    elif gas_price_daily is not None and not gas_price_daily.empty:
        _stack_gas = float(gas_price_daily["value"].iloc[-1])
    elif gas_price:
        _stack_gas = gas_price
    else:
        _stack_gas = 3.50  # EIA Henry Hub 5-yr avg fallback
    gas_ccgt_marginal = _stack_gas * HEAT_RATES["Combined Cycle"] / 1000 + VOM_COSTS.get("Combined Cycle", 3.5)
    gas_ct_marginal = _stack_gas * HEAT_RATES["Combustion Turbine"] / 1000 + VOM_COSTS.get("Combustion Turbine", 6.0)
    gas_steam_marginal = _stack_gas * HEAT_RATES["Steam Turbine"] / 1000 + VOM_COSTS.get("Steam Turbine", 5.0)
    # Storage opportunity cost = avg RT price (charge cost that must be recovered on discharge)
    # Fallback: CT marginal cost (storage typically bids near peaker cost to capture peak spreads)
    if spp_timeseries is not None and not spp_timeseries.empty:
        storage_marginal = spp_timeseries["hbHubAvg"].mean()
    else:
        storage_marginal = gas_ct_marginal

    # Hydro VOM: NREL ATB 2024 existing hydro variable O&M ~$2-5/MWh; use midpoint
    hydro_marginal = 3.5

    # "Other" in ERCOT is primarily biomass/landfill gas — marginal costs similar to gas steam
    # (EIA AEO 2024: biomass variable cost ~$40-50/MWh including fuel)
    other_marginal = gas_steam_marginal

    MERIT_ORDER = {
        "Nuclear":          {"marginal_cost": nuc_marginal,      "color": "#ad7fff"},
        "Wind":             {"marginal_cost": 0,                 "color": "#00d1ff"},
        "Solar":            {"marginal_cost": 0,                 "color": "#ffdd00"},
        "Hydro":            {"marginal_cost": hydro_marginal,    "color": "#00ff96"},
        "Coal and Lignite": {"marginal_cost": coal_marginal,     "color": "#888888"},
        "Natural Gas":      {"marginal_cost": gas_ccgt_marginal, "color": "#ff9900"},
        "Power Storage":    {"marginal_cost": storage_marginal,  "color": "#ff4b4b"},
        "Other":            {"marginal_cost": other_marginal,    "color": "#666666"},
    }

    # Build stack data
    stack_data = []
    cumulative_mw = 0
    for fuel in ["Wind", "Solar", "Nuclear", "Hydro", "Coal and Lignite", "Natural Gas", "Other", "Power Storage"]:
        gen_mw = latest_fuel.get(fuel, 0)
        cap_mw = capacity_data.get(fuel, max(0, gen_mw))
        if cap_mw <= 0 and gen_mw == 0:
            continue
        info = MERIT_ORDER.get(fuel, {"marginal_cost": gas_ccgt_marginal, "color": "#666"})
        stack_data.append({
            "fuel": fuel, "gen_mw": gen_mw, "capacity_mw": cap_mw,
            "marginal_cost": info["marginal_cost"], "color": info["color"],
            "start_mw": cumulative_mw,
        })
        cumulative_mw += cap_mw

    # ── Capacity vs Generation (horizontal bars) ──
    fig_stack = go.Figure()
    for item in stack_data:
        fig_stack.add_trace(go.Bar(
            y=[item["fuel"]], x=[item["capacity_mw"]],
            orientation="h", marker_color=item["color"], marker_opacity=0.3,
            showlegend=False, text=f"{item['capacity_mw']:,.0f} MW cap", textposition="inside",
        ))
        fig_stack.add_trace(go.Bar(
            y=[item["fuel"]], x=[item["gen_mw"]],
            orientation="h", name=item["fuel"], marker_color=item["color"],
            text=f"{item['gen_mw']:,.0f} MW", textposition="inside",
        ))
    fig_stack.update_layout(
        template="plotly_dark", height=420, margin=dict(t=10, b=0, l=0, r=0),
        barmode="overlay", xaxis_title="Megawatts (MW)",
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        yaxis=dict(categoryorder="array",
                   categoryarray=list(reversed([s["fuel"] for s in stack_data]))),
    )
    st.plotly_chart(fig_stack, use_container_width=True)

    # ── Utilization table with SRMC and profitability ──
    if not df_sd.empty:
        df_sd["demand"] = pd.to_numeric(df_sd["demand"], errors="coerce")
        current_demand = df_sd["demand"].iloc[-1]
    else:
        current_demand = total_gen

    def _fmt_util(gen, cap, fuel):
        if fuel == "Power Storage" and gen < 0:
            return f"Charging ({abs(gen):,.0f} MW)"
        if cap > 0:
            return f"{max(0, gen) / cap * 100:.1f}%"
        return "N/A"

    df_stack = pd.DataFrame([
        {
            "Fuel": s["fuel"],
            "Capacity (MW)": f"{s['capacity_mw']:,.0f}",
            "Generation (MW)": f"{s['gen_mw']:,.0f}",
            "Utilization": _fmt_util(s["gen_mw"], s["capacity_mw"], s["fuel"]),
            "SRMC (fuel+VOM)": f"${s['marginal_cost']:.1f}/MWh",
            "Margin vs Price": f"${power_price - s['marginal_cost']:+.1f}/MWh" if power_price else "N/A",
        }
        for s in stack_data
    ])
    st.dataframe(df_stack, use_container_width=True, hide_index=True)

    # ── Supply Curve with Gas Fleet Disaggregation ──
    st.subheader("Supply Curve — Dispatch Order")
    st.caption(
        "Merit order reflecting current available supply. "
        "Renewables and storage use current output (weather/charge-dependent). "
        "Conventional plants use installed capacity (per-fuel outage data not available)."
    )

    # Build supply curve with realistic capacity per fuel type:
    # - Wind/Solar: use current generation (non-dispatchable, output = available)
    # - Storage: only include when discharging (gen > 0); exclude when charging (it's load)
    # - Conventional (nuclear, coal, gas, hydro): use installed capacity (best available proxy)
    NON_DISPATCHABLE = {"Wind", "Solar", "Power Storage"}

    # Disaggregate gas fleet by efficiency tranche
    # ERCOT CDR 2024: CCGT ~65% of gas capacity, Gas ST ~11%, CT/Peaker ~24%
    supply_data = []
    for item in stack_data:
        if item["fuel"] in NON_DISPATCHABLE:
            # Use current generation as available capacity
            if item["gen_mw"] <= 0:
                continue  # not producing (or charging) — not supply
            supply_data.append({
                **item,
                "capacity_mw": max(0, item["gen_mw"]),  # available = what's producing
            })
        elif item["fuel"] == "Natural Gas":
            gas_cap = item["capacity_mw"]
            gas_gen = item["gen_mw"]
            tranches = [
                ("Gas — CCGT", 0.65, gas_ccgt_marginal, "#ff9900"),
                ("Gas — Steam", 0.11, gas_steam_marginal, "#cc7700"),
                ("Gas — CT/Peaker", 0.24, gas_ct_marginal, "#994400"),
            ]
            gen_remaining = gas_gen
            for t_name, t_frac, t_cost, t_color in tranches:
                t_cap = gas_cap * t_frac
                t_gen = min(gen_remaining, t_cap)
                gen_remaining = max(0, gen_remaining - t_gen)
                supply_data.append({
                    "fuel": t_name, "capacity_mw": t_cap, "gen_mw": t_gen,
                    "marginal_cost": t_cost, "color": t_color, "start_mw": 0,
                })
        else:
            supply_data.append({**item})

    # Sort by marginal cost for proper merit order
    supply_data.sort(key=lambda x: x["marginal_cost"])
    cum = 0
    for item in supply_data:
        item["start_mw"] = cum
        cum += item["capacity_mw"]

    fig_supply = go.Figure()
    for item in supply_data:
        x0 = item["start_mw"]
        x1 = x0 + item["capacity_mw"]
        fig_supply.add_trace(go.Scatter(
            x=[x0, x1, x1, x0, x0],
            y=[0, 0, item["marginal_cost"], item["marginal_cost"], 0],
            fill="toself", fillcolor=item["color"],
            opacity=0.6, line=dict(color=item["color"], width=1),
            name=item["fuel"],
            hovertemplate=(
                f"{item['fuel']}<br>Capacity: {item['capacity_mw']:,.0f} MW<br>"
                f"Generation: {item['gen_mw']:,.0f} MW<br>"
                f"Marginal Cost: ${item['marginal_cost']:.1f}/MWh<extra></extra>"
            ),
        ))

    fig_supply.add_vline(x=current_demand, line_dash="dash", line_color="#ff4b4b", line_width=2,
                         annotation_text=f"Demand: {current_demand:,.0f} MW")
    if power_price:
        fig_supply.add_hline(y=power_price, line_dash="dot", line_color="#00d1ff", line_width=1,
                             annotation_text=f"Price: ${power_price:.1f}/MWh")

    fig_supply.update_layout(
        template="plotly_dark", height=450, margin=dict(t=10, b=0, l=0, r=0),
        xaxis_title="Cumulative Capacity (MW)", yaxis_title="Marginal Cost ($/MWh)",
        hovermode="closest",
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )
    st.plotly_chart(fig_supply, use_container_width=True)

    # ── Inframarginal Rent ──
    if power_price:
        st.subheader("Inframarginal Rent by Fuel Type")
        st.caption("Revenue above marginal cost for each dispatched fuel type. This is the gross margin captured by generators clearing below the market price.")

        rent_data = []
        for item in stack_data:
            if item["gen_mw"] > 0 and power_price > item["marginal_cost"]:
                rent_per_mwh = power_price - item["marginal_cost"]
                total_rent = rent_per_mwh * item["gen_mw"]  # $/hr
                rent_data.append({
                    "fuel": item["fuel"], "gen_mw": item["gen_mw"],
                    "marginal_cost": item["marginal_cost"],
                    "rent_per_mwh": rent_per_mwh, "rent_per_hr": total_rent,
                    "color": item["color"],
                })

        if rent_data:
            fig_rent = go.Figure()
            fig_rent.add_trace(go.Bar(
                x=[r["fuel"] for r in rent_data],
                y=[r["rent_per_hr"] / 1000 for r in rent_data],
                marker_color=[r["color"] for r in rent_data],
                text=[f"${r['rent_per_mwh']:.1f}/MWh × {r['gen_mw']:,.0f} MW" for r in rent_data],
                textposition="outside",
            ))
            fig_rent.update_layout(
                template="plotly_dark", height=350, margin=dict(t=30, b=0, l=0, r=0),
                yaxis_title="Inframarginal Rent ($K/hr)",
            )
            st.plotly_chart(fig_rent, use_container_width=True)

            total_rent_hr = sum(r["rent_per_hr"] for r in rent_data)
            st.caption(f"Total inframarginal rent: ${total_rent_hr / 1000:,.0f}K/hr (${total_rent_hr / 1000 * 24:,.0f}K/day at current dispatch)")

    # ── Fuel Mix Through the Day (stacked area) ──
    st.subheader("Fuel Mix Through the Day")
    st.caption("Stacked area showing generation by fuel type over 24 hours. Reveals how the dispatch stack shifts as renewables ramp and load changes.")

    # Use today's data from df_today
    area_fuels = [f for f in ["Nuclear", "Coal and Lignite", "Natural Gas", "Hydro", "Wind", "Solar", "Power Storage", "Other"]
                  if f in df_today.columns]

    if area_fuels:
        fig_area = go.Figure()
        # Stack order: baseload at bottom, renewables on top
        stack_order = ["Nuclear", "Coal and Lignite", "Natural Gas", "Hydro", "Other", "Power Storage", "Wind", "Solar"]
        ordered = [f for f in stack_order if f in area_fuels]

        def _hex_to_rgba(hex_color, alpha=0.7):
            """Convert hex color to rgba string for fill opacity."""
            h = hex_color.lstrip("#")
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
            return f"rgba({r}, {g}, {b}, {alpha})"

        for fuel in ordered:
            color = FUEL_COLORS.get(fuel, "#666666")
            fig_area.add_trace(go.Scatter(
                x=df_today["timestamp"], y=df_today[fuel].clip(lower=0),
                mode="lines", name=fuel, stackgroup="fuel",
                line=dict(width=0.5, color=color),
                fillcolor=_hex_to_rgba(color, 0.7),
            ))

        # Demand line overlay
        if not df_sd.empty:
            df_sd_area = df_sd[df_sd["timestamp"].dt.date == today_date].copy()
            if not df_sd_area.empty:
                df_sd_area["demand"] = pd.to_numeric(df_sd_area["demand"], errors="coerce")
                fig_area.add_trace(go.Scatter(
                    x=df_sd_area["timestamp"], y=df_sd_area["demand"],
                    mode="lines", name="System Demand",
                    line=dict(color="white", width=2, dash="dot"),
                ))

        fig_area.update_layout(
            template="plotly_dark", height=450, margin=dict(t=10, b=0, l=0, r=0),
            yaxis_title="Generation (MW)", hovermode="x unified",
            legend=dict(orientation="h", yanchor="bottom", y=1.02),
        )
        st.plotly_chart(fig_area, use_container_width=True)

    # ── Belly vs Peak Comparison ──
    st.subheader("Generation Mix: Belly vs Peak")
    st.caption("Side-by-side fuel mix at the duck curve belly (min net load) and evening peak (max net load).")

    belly_idx = df_today["net_load"].idxmin()
    peak_idx = df_today["net_load"].idxmax()
    belly_row = df_today.loc[belly_idx]
    peak_row = df_today.loc[peak_idx]

    compare_fuels = [f for f in fuel_types if f in df_today.columns]
    if compare_fuels:
        belly_vals = [max(0, belly_row.get(f, 0)) for f in compare_fuels]
        peak_vals = [max(0, peak_row.get(f, 0)) for f in compare_fuels]
        fuel_colors_list = [FUEL_COLORS.get(f, "#666") for f in compare_fuels]

        bp1, bp2 = st.columns(2)
        with bp1:
            fig_belly = go.Figure(go.Pie(
                labels=compare_fuels, values=belly_vals,
                marker=dict(colors=fuel_colors_list),
                hole=0.4, textinfo="label+percent",
                textfont=dict(size=10),
            ))
            fig_belly.update_layout(
                template="plotly_dark", height=320, margin=dict(t=30, b=0, l=0, r=0),
                title=f"Belly ({belly_row['timestamp'].strftime('%I:%M %p')}): {belly_row['net_load']:,.0f} MW net load",
                showlegend=False,
            )
            st.plotly_chart(fig_belly, use_container_width=True)

        with bp2:
            fig_peak = go.Figure(go.Pie(
                labels=compare_fuels, values=peak_vals,
                marker=dict(colors=fuel_colors_list),
                hole=0.4, textinfo="label+percent",
                textfont=dict(size=10),
            ))
            fig_peak.update_layout(
                template="plotly_dark", height=320, margin=dict(t=30, b=0, l=0, r=0),
                title=f"Peak ({peak_row['timestamp'].strftime('%I:%M %p')}): {peak_row['net_load']:,.0f} MW net load",
                showlegend=False,
            )
            st.plotly_chart(fig_peak, use_container_width=True)

        # Show the swing by fuel
        swing_data = []
        for i, f in enumerate(compare_fuels):
            diff = peak_vals[i] - belly_vals[i]
            if abs(diff) > 50:
                swing_data.append({"Fuel": f, "Belly (MW)": belly_vals[i], "Peak (MW)": peak_vals[i], "Swing (MW)": diff})
        if swing_data:
            df_swing = pd.DataFrame(swing_data)
            df_swing = df_swing.sort_values("Swing (MW)", key=abs, ascending=False)
            df_swing["Belly (MW)"] = df_swing["Belly (MW)"].apply(lambda x: f"{x:,.0f}")
            df_swing["Peak (MW)"] = df_swing["Peak (MW)"].apply(lambda x: f"{x:,.0f}")
            df_swing["Swing (MW)"] = df_swing["Swing (MW)"].apply(lambda x: f"{x:+,.0f}")
            st.dataframe(df_swing, use_container_width=True, hide_index=True)

    # ── Reserve Margin Through the Day ──
    if not df_sd.empty:
        st.subheader("Reserve Margin Through the Day")
        st.caption("Physical Responsive Capability (PRC) proxy = available capacity − demand. ERCOT EEA thresholds: EEA1 at PRC < 3,000 MW, EEA2 at PRC < 1,750 MW.")

        df_sd_today = df_sd[df_sd["timestamp"].dt.date == today_date].copy()
        if not df_sd_today.empty and "capacity" in df_sd_today.columns and "demand" in df_sd_today.columns:
            df_sd_today["capacity"] = pd.to_numeric(df_sd_today["capacity"], errors="coerce")
            df_sd_today["demand"] = pd.to_numeric(df_sd_today["demand"], errors="coerce")
            df_sd_today["reserves_mw"] = df_sd_today["capacity"] - df_sd_today["demand"]
            df_sd_today["reserve_pct"] = df_sd_today["reserves_mw"] / df_sd_today["demand"] * 100

            fig_rm = go.Figure()
            # ERCOT Operating Procedure thresholds (absolute MW)
            fig_rm.add_hline(y=3000, line_dash="dash", line_color="#ff9900", line_width=1.5,
                              annotation_text="EEA1 Threshold (3,000 MW)")
            fig_rm.add_hline(y=1750, line_dash="dash", line_color="#ff4b4b", line_width=1.5,
                              annotation_text="EEA2 Threshold (1,750 MW)")
            fig_rm.add_hrect(y0=0, y1=3000, fillcolor="rgba(255, 153, 0, 0.06)", line_width=0)
            fig_rm.add_hrect(y0=0, y1=1750, fillcolor="rgba(255, 75, 75, 0.08)", line_width=0)

            fig_rm.add_trace(go.Scatter(
                x=df_sd_today["timestamp"], y=df_sd_today["reserves_mw"],
                mode="lines", name="Available Reserves (MW)",
                line=dict(color="#00ff96", width=2.5),
                fill="tozeroy", fillcolor="rgba(0, 255, 150, 0.06)",
            ))
            fig_rm.update_layout(
                template="plotly_dark", height=350, margin=dict(t=10, b=0, l=0, r=0),
                yaxis_title="Available Reserves (MW)", hovermode="x unified",
            )
            st.plotly_chart(fig_rm, use_container_width=True)

            rm_min_mw = df_sd_today["reserves_mw"].min()
            rm_now_mw = df_sd_today["reserves_mw"].iloc[-1]
            rm_now_pct = df_sd_today["reserve_pct"].iloc[-1]
            rm_min_pct = df_sd_today["reserve_pct"].min()
            rm1, rm2, rm3, rm4 = st.columns(4)
            rm1.metric("Current Reserves", f"{rm_now_mw:,.0f} MW", f"{rm_now_pct:.1f}%")
            rm2.metric("Today's Minimum", f"{rm_min_mw:,.0f} MW", f"{rm_min_pct:.1f}%")
            rm3.metric("Current Capacity", f"{df_sd_today['capacity'].iloc[-1]:,.0f} MW")
            rm4.metric("Current Demand", f"{df_sd_today['demand'].iloc[-1]:,.0f} MW")

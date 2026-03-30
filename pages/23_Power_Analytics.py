import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import logging
from datetime import datetime, timedelta
from src.layout import setup_page, error_boundary, fun_loader
from src import ercot_api
from src.eia_helpers import fetch_henry_hub_spot as fetch_henry_hub, fetch_henry_hub_daily, fetch_eia_hourly_grid
from src.market_data import fetch_commodity_futures
from src.ercot_api import fetch_dashboard as fetch_ercot
from src.styles import COLORS

logger = logging.getLogger(__name__)

PLOTLY_NOBAR = {"displayModeBar": False}


def _parse_hour(h):
    """Safely extract integer hour from ERCOT hourEnding (handles NaN, str, int, float)."""
    if h is None or (isinstance(h, float) and np.isnan(h)):
        return 0
    if isinstance(h, str):
        try:
            return int(h.split(":")[0])
        except (ValueError, IndexError):
            return 0
    try:
        return int(h)
    except (ValueError, TypeError):
        return 0


setup_page("23_Power_Analytics")

st.title("Power Analytics & Trading Strategies")
st.markdown("Duck curve, spark spreads, generation stack, peak/off-peak, RT/DAM arb, similar day forecast, and strategy backtest.")

# ── CONFIG ──

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

# Variable O&M by plant type ($/MWh) — EIA AEO 2024 / NREL ATB estimates
VOM_COSTS = {
    "Combined Cycle": 3.5,
    "Combustion Turbine": 6.0,
    "Steam Turbine": 5.0,
    "Coal": 5.5,
    "Nuclear": 3.0,
}


# ── DATA FETCHING ──

def fetch_gas_futures() -> dict | None:
    """Fetch natural gas front-month futures."""
    return fetch_commodity_futures("NG=F", period="1mo")


def fetch_gas_futures_3mo() -> dict | None:
    """Fetch natural gas front-month futures with 3-month history."""
    return fetch_commodity_futures("NG=F", period="3mo")


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
    sys_prices = fetch_ercot("systemWidePrices")
    gas_price = fetch_henry_hub()
    gas_price_daily = fetch_henry_hub_daily(days_back=30)
    gas_futures = fetch_gas_futures()
    oil_price = fetch_oil_futures()
    power_price = fetch_ercot_spp()
    spp_timeseries = fetch_ercot_spp_timeseries()
    eia_hourly = fetch_eia_hourly_grid("ERCO", days_back=31)

    # Gas data for page-40 tabs (3mo history for backtest)
    gas_data_3mo = fetch_gas_futures_3mo()
    gas_history = gas_data_3mo["history"] if gas_data_3mo else None

    # RT data from dashboard for page-40 tabs
    rt_data = []
    latest_hub = None
    if sys_prices:
        rt_data = sys_prices.get("rtSppData", [])
        if rt_data:
            _lh = rt_data[-1].get("hbHubAvg")
            if _lh:
                latest_hub = float(_lh)

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
        _today_str = datetime.now().strftime("%Y-%m-%d")
        _yesterday_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        ercot_load = ercot_solar = ercot_wind = None
        ercot_rt_spp = ercot_dam_spp = ercot_load_hist = None
        ercot_sced_lambda = ercot_dam_lambda = None

# Aliases for page-40 tab compatibility
_has_api = _has_ercot_api
ercot_rt = ercot_rt_spp if _has_ercot_api else None
ercot_dam = ercot_dam_spp if _has_ercot_api else None
ercot_sced = ercot_sced_lambda if _has_ercot_api else None

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
tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
    "Duck Curve",
    "Spark Spread",
    "Stack Analysis",
    "Peak / Off-Peak",
    "RT vs DAM",
    "Similar Day Forecast",
    "Strategy Backtest",
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
        hl["hour"] = hl["hourEnding"].apply(_parse_hour)
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
                _parse_hour
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
# TAB 2: SPARK SPREAD
# ════════════════════════════════════════════════
with tab2:
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
        nuc_price_mmbtu = 0.65    # Uranium fuel equivalent (NEI: $0.50-0.70/MMBtu range)

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
                    _parse_hour
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
# TAB 3: STACK ANALYSIS (MERIT ORDER)
# ════════════════════════════════════════════════
with tab3:
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
            df_sd_today["reserve_pct"] = df_sd_today["reserves_mw"] / df_sd_today["demand"].replace(0, np.nan) * 100

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


# ════════════════════════════════════════════════
# TAB 4: PEAK / OFF-PEAK (from page 40)
# ════════════════════════════════════════════════
with tab4, error_boundary("Peak / Off-Peak"):
    st.subheader("Peak vs Off-Peak Spread")

    with st.expander("How to read this tab", expanded=False):
        st.markdown(
            "Power prices follow daily patterns driven by demand:\n\n"
            "- **On-peak** (HE 7-22, Mon-Fri): high demand, expensive\n"
            "- **Off-peak** (HE 1-6, 23-24, weekends): low demand, cheap\n\n"
            "The **peak/off-peak spread** is the difference. Traders monetize this by:\n"
            "- Running peakers only during peak hours\n"
            "- Charging batteries off-peak, discharging on-peak\n"
            "- Buying off-peak power forwards, selling peak power forwards\n\n"
            "A **widening** spread = more storage/peaker value. "
            "A **narrowing** spread = flatter demand curve (more renewables, milder weather)."
        )

    st.caption("ERCOT on-peak: Hour Ending 7-22 (6am-10pm CPT), weekdays only. "
               "Off-peak: HE 1-6 and HE 23-24, plus all weekend hours.")

    if rt_data:
        po_rows = []
        for row in rt_data:
            try:
                ts = row.get("timestamp") or row.get("time")
                hub_val = row.get("hbHubAvg")
                if ts and hub_val is not None:
                    hub = float(hub_val)
                    dt = pd.to_datetime(ts)
                    hour = dt.hour
                    # ERCOT on-peak: HE 7-22 weekdays (hour ending, so clock hour 6-21)
                    is_peak = 6 <= hour <= 21 and dt.weekday() < 5
                    po_rows.append({"time": dt, "price": hub, "period": "Peak" if is_peak else "Off-Peak", "hour": hour})
            except (ValueError, TypeError):
                pass

        if po_rows:
            po_df = pd.DataFrame(po_rows).sort_values("time")

            # Hourly price profile
            hourly_avg = po_df.groupby("hour")["price"].mean()

            fig_profile = go.Figure()
            fig_profile.add_trace(go.Bar(
                x=hourly_avg.index, y=hourly_avg.values,
                marker_color=["#ff6b6b" if 7 <= h <= 22 else "#00d1ff" for h in hourly_avg.index],
                text=[f"${v:.0f}" for v in hourly_avg.values], textposition="outside",
            ))
            fig_profile.add_vrect(x0=6.5, x1=22.5, fillcolor="rgba(255,107,107,0.05)", line_width=0,
                                  annotation_text="Peak Hours", annotation_position="top left")
            fig_profile.update_layout(template="plotly_dark", height=380,
                                       title="Average Price by Hour of Day",
                                       xaxis_title="Hour (HE)", yaxis_title="$/MWh",
                                       xaxis=dict(dtick=1),
                                       margin=dict(l=0, r=0, t=40, b=0))
            st.plotly_chart(fig_profile, use_container_width=True, config=PLOTLY_NOBAR)

            # Peak vs off-peak metrics
            peak_avg = po_df[po_df["period"] == "Peak"]["price"].mean()
            offpeak_avg = po_df[po_df["period"] == "Off-Peak"]["price"].mean()
            spread = peak_avg - offpeak_avg
            ratio = peak_avg / offpeak_avg if offpeak_avg > 0 else 0

            pp1, pp2, pp3, pp4 = st.columns(4)
            pp1.metric("Peak Avg", f"${peak_avg:.2f}/MWh")
            pp2.metric("Off-Peak Avg", f"${offpeak_avg:.2f}/MWh")
            pp3.metric("Spread", f"${spread:.2f}/MWh")
            pp4.metric("Peak/Off-Peak Ratio", f"{ratio:.2f}x")

            # Storage arbitrage value
            st.markdown("---")
            st.subheader("Battery Storage Arbitrage Value")
            st.caption("Revenue from charging during cheapest hours and discharging during most expensive hours.")

            sa_c1, sa_c2, sa_c3 = st.columns(3)
            with sa_c1:
                batt_capacity = st.number_input("Capacity (MW)", value=100, step=10, key="ps_batt_mw")
            with sa_c2:
                batt_duration = st.selectbox("Duration (hours)", [1, 2, 4, 6], index=2, key="ps_batt_dur")
            with sa_c3:
                efficiency = st.slider("Round-trip Efficiency (%)", 75, 95, 85, 5, key="ps_batt_eff") / 100

            sorted_prices = po_df.sort_values("price")
            charge_hours = min(batt_duration, len(sorted_prices) // 4)
            discharge_hours = min(batt_duration, len(sorted_prices) // 4)

            charge_cost = sorted_prices.head(charge_hours)["price"].mean()
            discharge_rev = sorted_prices.tail(discharge_hours)["price"].mean()
            storage_spread = discharge_rev * efficiency - charge_cost
            daily_energy = batt_capacity * batt_duration  # MWh
            daily_revenue = storage_spread * daily_energy
            annual_est = daily_revenue * 365

            sm1, sm2, sm3, sm4 = st.columns(4)
            sm1.metric("Avg Charge Price", f"${charge_cost:.2f}/MWh",
                       help=f"Average of cheapest {charge_hours} hours")
            sm2.metric("Avg Discharge Price", f"${discharge_rev:.2f}/MWh",
                       help=f"Average of most expensive {discharge_hours} hours")
            sm3.metric("Net Spread", f"${storage_spread:.2f}/MWh",
                       delta="Profitable" if storage_spread > 0 else "Unprofitable",
                       delta_color="normal" if storage_spread > 0 else "inverse")
            sm4.metric("Est. Daily Revenue", f"${daily_revenue:,.0f}",
                       help=f"{batt_capacity}MW × {batt_duration}hr × ${storage_spread:.2f}/MWh")

            st.caption(f"Annualized estimate: **${annual_est:,.0f}** "
                       f"({batt_capacity}MW / {batt_duration}hr BESS at {efficiency:.0%} RTE). "
                       "Excludes degradation, capacity payments, ancillary services revenue, and maintenance costs.")
    else:
        st.warning("Need ERCOT RT price data for peak/off-peak analysis.")


# ════════════════════════════════════════════════
# TAB 5: RT vs DAM ARBITRAGE (from page 40)
# ════════════════════════════════════════════════
with tab5, error_boundary("RT vs DAM Arb"):
    st.subheader("Real-Time vs Day-Ahead Price Convergence")

    with st.expander("How to read this tab", expanded=False):
        st.markdown(
            "Power is traded in two markets:\n"
            "- **Day-Ahead (DAM)**: Clears ~24 hours before delivery. Less volatile.\n"
            "- **Real-Time (RT)**: Clears at delivery. More volatile, reflects actual conditions.\n\n"
            "The **RT-DAM spread** = RT price − DAM price:\n"
            "- **Positive**: RT was more expensive → generators who sold DAM missed upside\n"
            "- **Negative**: RT was cheaper → DAM buyers overpaid\n\n"
            "**Virtual trading** (convergence bidding) profits from predicting this spread:\n"
            "- If you think RT > DAM: buy DAM, sell RT (virtual supply)\n"
            "- If you think RT < DAM: sell DAM, buy RT (virtual demand)\n\n"
            "Persistent positive spreads signal **supply tightness** or **forecast errors**. "
            "Persistent negative spreads signal **overcommitment** in the DAM."
        )

    # Use ERCOT API for true RT vs DAM comparison when available
    if _has_api and ercot_rt is not None and ercot_dam is not None:
        try:
            rt_api_df = pd.DataFrame(ercot_rt) if isinstance(ercot_rt, list) else ercot_rt
            dam_api_df = pd.DataFrame(ercot_dam) if isinstance(ercot_dam, list) else ercot_dam

            if rt_api_df is not None and dam_api_df is not None and len(rt_api_df) > 3 and len(dam_api_df) > 3:
                st.caption("Using ERCOT Public API — true RT vs DAM settlement point prices.")

                # Find price columns dynamically
                rt_price_col = next((c for c in rt_api_df.columns if "spp" in c.lower() or "price" in c.lower()), None)
                dam_price_col = next((c for c in dam_api_df.columns if "spp" in c.lower() or "price" in c.lower()), None)
                rt_time_col = next((c for c in rt_api_df.columns if "deliveryHour" in c or "hour" in c.lower() or "interval" in c.lower() or "time" in c.lower()), None)
                dam_time_col = next((c for c in dam_api_df.columns if "deliveryHour" in c or "hour" in c.lower() or "interval" in c.lower() or "time" in c.lower()), None)

                if rt_price_col and dam_price_col:
                    rt_api_df[rt_price_col] = pd.to_numeric(rt_api_df[rt_price_col], errors="coerce")
                    dam_api_df[dam_price_col] = pd.to_numeric(dam_api_df[dam_price_col], errors="coerce")

                    def _parse_hour_series(series):
                        """Parse hour from various ERCOT formats: numeric (1-24), string ('01:00'), etc."""
                        parsed = pd.to_numeric(series, errors="coerce")
                        if parsed.isna().all():
                            # Try parsing as time string like "01:00" or "1:00"
                            parsed = series.astype(str).str.extract(r"(\d+)")[0].astype(float)
                        return parsed

                    # Aggregate RT to hourly if it's 15-min intervals
                    if rt_time_col and rt_time_col in rt_api_df.columns:
                        rt_api_df["_hour"] = _parse_hour_series(rt_api_df[rt_time_col])
                        rt_api_df = rt_api_df.dropna(subset=["_hour"])
                        rt_hourly = rt_api_df.groupby("_hour")[rt_price_col].mean().reset_index()
                        rt_hourly.columns = ["hour", "rt_price"]
                    else:
                        rt_hourly = pd.DataFrame({"hour": range(1, len(rt_api_df) + 1), "rt_price": rt_api_df[rt_price_col].values})

                    if dam_time_col and dam_time_col in dam_api_df.columns:
                        dam_api_df["_hour"] = _parse_hour_series(dam_api_df[dam_time_col])
                        dam_api_df = dam_api_df.dropna(subset=["_hour"])
                        dam_hourly = dam_api_df.groupby("_hour")[dam_price_col].mean().reset_index()
                        dam_hourly.columns = ["hour", "dam_price"]
                    else:
                        dam_hourly = pd.DataFrame({"hour": range(1, len(dam_api_df) + 1), "dam_price": dam_api_df[dam_price_col].values})

                    # Merge on hour
                    merged_prices = rt_hourly.merge(dam_hourly, on="hour", how="inner")
                    merged_prices["spread"] = merged_prices["rt_price"] - merged_prices["dam_price"]

                    if not merged_prices.empty:
                        # Spread chart
                        fig_rtdam = make_subplots(rows=2, cols=1, shared_xaxes=True,
                                                  row_heights=[0.5, 0.5], vertical_spacing=0.05)
                        fig_rtdam.add_trace(go.Scatter(x=merged_prices["hour"], y=merged_prices["rt_price"],
                                                        mode="lines+markers", name="RT Price",
                                                        line=dict(color="#00d1ff", width=2)), row=1, col=1)
                        fig_rtdam.add_trace(go.Scatter(x=merged_prices["hour"], y=merged_prices["dam_price"],
                                                        mode="lines+markers", name="DAM Price",
                                                        line=dict(color="#ffaa00", width=2)), row=1, col=1)
                        fig_rtdam.add_trace(go.Bar(x=merged_prices["hour"], y=merged_prices["spread"],
                                                    marker_color=["#00ff88" if v > 0 else "#ff4444" for v in merged_prices["spread"]],
                                                    name="RT − DAM Spread"), row=2, col=1)
                        fig_rtdam.add_hline(y=0, line_dash="dash", line_color="#555", row=2, col=1)
                        fig_rtdam.update_layout(template="plotly_dark", height=480,
                                                 title="RT vs DAM Settlement Point Prices (Hub Average)",
                                                 legend=dict(orientation="h", y=-0.08),
                                                 margin=dict(l=0, r=0, t=40, b=0))
                        fig_rtdam.update_yaxes(title_text="$/MWh", row=1, col=1)
                        fig_rtdam.update_yaxes(title_text="Spread ($/MWh)", row=2, col=1)
                        fig_rtdam.update_xaxes(title_text="Hour Ending", row=2, col=1)
                        st.plotly_chart(fig_rtdam, use_container_width=True, config=PLOTLY_NOBAR)

                        # Metrics
                        avg_spread = merged_prices["spread"].mean()
                        pct_rt_higher = (merged_prices["spread"] > 0).mean() * 100
                        max_abs = merged_prices["spread"].abs().max()
                        virtual_pnl = merged_prices["spread"].sum()

                        cv1, cv2, cv3, cv4 = st.columns(4)
                        cv1.metric("Avg RT−DAM Spread", f"${avg_spread:+.2f}/MWh")
                        cv2.metric("% Hours RT > DAM", f"{pct_rt_higher:.0f}%")
                        cv3.metric("Max |Spread|", f"${max_abs:.2f}/MWh")
                        cv4.metric("Virtual P&L (1MW)", f"${virtual_pnl:+.0f}",
                                   help="Sum of hourly spreads — what a perfect virtual trader would earn on 1 MW")

                        if avg_spread > 1:
                            st.info(f"**Positive bias** (${avg_spread:+.2f}/MWh) — RT consistently above DAM. "
                                    "Virtual supply strategy (buy DAM, sell RT) would have been profitable.")
                        elif avg_spread < -1:
                            st.info(f"**Negative bias** (${avg_spread:+.2f}/MWh) — DAM consistently above RT. "
                                    "Virtual demand strategy (sell DAM, buy RT) would have been profitable.")
                        else:
                            st.info("Markets well-converged — limited virtual trading opportunity today.")
                    else:
                        st.info("Could not align RT and DAM prices by hour.")
                else:
                    st.info(f"Could not find price columns. RT cols: {list(rt_api_df.columns)}, DAM cols: {list(dam_api_df.columns)}")
            else:
                st.info("RT or DAM data is empty from ERCOT API.")
        except Exception as e:
            st.warning(f"Error processing ERCOT API data: {e}")
            logger.warning(f"RT vs DAM error: {e}")

    elif rt_data and len(rt_data) > 10:
        # Fallback: dashboard RT data only (no DAM available)
        st.caption("ERCOT API not available — showing RT price analysis from dashboard data.")

        rt_prices = []
        for row in rt_data:
            try:
                hub_val = row.get("hbHubAvg")
                ts = row.get("timestamp") or row.get("time")
                if hub_val is not None and ts:
                    rt_prices.append({"time": pd.to_datetime(ts), "price": float(hub_val)})
            except (ValueError, TypeError):
                pass

        if rt_prices:
            rt_price_df = pd.DataFrame(rt_prices).sort_values("time")
            avg_price = rt_price_df["price"].mean()
            rt_price_df["deviation"] = rt_price_df["price"] - avg_price

            fig_rtdam = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.6, 0.4],
                                      vertical_spacing=0.05)
            fig_rtdam.add_trace(go.Scatter(x=rt_price_df["time"], y=rt_price_df["price"], mode="lines",
                                            name="RT Hub Avg", line=dict(color="#00d1ff", width=2)), row=1, col=1)
            fig_rtdam.add_hline(y=avg_price, line_dash="dash", line_color="#ffaa00",
                                annotation_text=f"Avg: ${avg_price:.0f}", row=1, col=1)
            fig_rtdam.add_trace(go.Bar(x=rt_price_df["time"], y=rt_price_df["deviation"],
                                        marker_color=["#00ff88" if v > 0 else "#ff4444" for v in rt_price_df["deviation"]],
                                        name="Deviation from Avg"), row=2, col=1)
            fig_rtdam.add_hline(y=0, line_dash="dash", line_color="#555", row=2, col=1)
            fig_rtdam.update_layout(template="plotly_dark", height=450,
                                     title="RT Price & Deviation from Average",
                                     margin=dict(l=0, r=0, t=40, b=0),
                                     legend=dict(orientation="h", y=-0.08))
            fig_rtdam.update_yaxes(title_text="$/MWh", row=1, col=1)
            fig_rtdam.update_yaxes(title_text="Deviation", row=2, col=1)
            st.plotly_chart(fig_rtdam, use_container_width=True, config=PLOTLY_NOBAR)

            cv1, cv2, cv3 = st.columns(3)
            cv1.metric("Avg Price", f"${avg_price:.2f}/MWh")
            cv2.metric("Price Volatility", f"${rt_price_df['price'].std():.2f}/MWh")
            cv3.metric("Price Range", f"${rt_price_df['price'].min():.0f} — ${rt_price_df['price'].max():.0f}")

            neg_hours = (rt_price_df["price"] < 0).sum()
            if neg_hours > 0:
                st.warning(f"**{neg_hours} intervals with negative prices** — virtual demand opportunity.")
    else:
        st.info("Need ERCOT RT price data. Check that the ERCOT dashboard is accessible.")


# ════════════════════════════════════════════════
# TAB 6: SIMILAR DAY FORECAST (from page 40)
# ════════════════════════════════════════════════

with tab6, error_boundary("Similar Day Forecast"):
    st.subheader("Similar Day Price Forecast")
    st.caption(
        "Matches tomorrow's weather, wind, load shape, and calendar profile against historical days. "
        "Uses hourly temperature curve matching, bootstrap confidence bands, hub basis adjustment, "
        "spike-robust estimation, and rolling marginal heat rates."
    )

    import requests as _sim_req
    import numpy as _np
    from datetime import date as _date_cls, timedelta as _td, datetime as _dt

    # ── Weather Nodes (population-weighted for ERCOT demand centers) ──
    _WEATHER_NODES = [
        {"name": "Houston", "lat": 29.76, "lon": -95.37, "weight": 0.35},
        {"name": "Dallas", "lat": 32.78, "lon": -96.80, "weight": 0.30},
        {"name": "San Antonio", "lat": 29.42, "lon": -98.49, "weight": 0.15},
        {"name": "Austin", "lat": 30.27, "lon": -97.74, "weight": 0.10},
        {"name": "Corpus Christi", "lat": 27.80, "lon": -97.40, "weight": 0.10},
    ]

    _HOLIDAYS = {
        (1, 1), (7, 4), (12, 25), (11, 28), (11, 29),
        (9, 1), (5, 26), (1, 20),
    }

    def _is_holiday(d):
        return (d.month, d.day) in _HOLIDAYS

    def _heat_index(temp_f, humidity):
        if temp_f < 80:
            return temp_f
        hi = (-42.379 + 2.04901523 * temp_f + 10.14333127 * humidity
              - 0.22475541 * temp_f * humidity - 0.00683783 * temp_f**2
              - 0.05481717 * humidity**2 + 0.00122874 * temp_f**2 * humidity
              + 0.00085282 * temp_f * humidity**2 - 0.00000199 * temp_f**2 * humidity**2)
        return max(hi, temp_f)

    def _hourly_curve_similarity(curve_a, curve_b):
        """Correlation-based similarity between two 24-hour profiles. Returns 0-1."""
        a = _np.array(curve_a[:24], dtype=float)
        b = _np.array(curve_b[:24], dtype=float)
        if len(a) < 20 or len(b) < 20:
            return 0.0
        if len(a) < 24:
            a = _np.pad(a, (0, 24 - len(a)), mode="edge")
        if len(b) < 24:
            b = _np.pad(b, (0, 24 - len(b)), mode="edge")
        a_std, b_std = _np.std(a), _np.std(b)
        if a_std < 0.01 or b_std < 0.01:
            return 0.5
        corr = float(_np.corrcoef(a, b)[0, 1])
        return max(0.0, (corr + 1) / 2)

    def _robust_weighted_mean(profiles, weights, spike_threshold=150):
        """Trimmed mean for spike hours, weighted mean for normal hours."""
        arr = _np.array(profiles)
        w = _np.array(weights, dtype=float)
        _ws = w.sum()
        w_norm = w / _ws if _ws > 0 else _np.ones_like(w) / max(len(w), 1)
        result = _np.zeros(24)
        for h in range(24):
            hour_vals = arr[:, h]
            has_spike = _np.any(hour_vals > spike_threshold) or _np.any(hour_vals < -20)
            if has_spike and len(hour_vals) >= 5:
                # Trimmed mean: drop top and bottom 20%
                trim_n = max(1, int(len(hour_vals) * 0.2))
                sorted_idx = _np.argsort(hour_vals)
                keep_idx = sorted_idx[trim_n:-trim_n] if trim_n < len(sorted_idx) // 2 else sorted_idx[1:-1]
                if len(keep_idx) > 0:
                    kept_w = w_norm[keep_idx]
                    _kws = kept_w.sum()
                    kept_w = kept_w / _kws if _kws > 0 else _np.ones_like(kept_w) / max(len(kept_w), 1)
                    result[h] = float(_np.average(hour_vals[keep_idx], weights=kept_w))
                else:
                    result[h] = float(_np.median(hour_vals))
            else:
                result[h] = float(_np.average(hour_vals, weights=w_norm))
        return result

    def _bootstrap_confidence(profiles, weights, n_boot=500, ci_levels=(0.80, 0.95)):
        """Bootstrap resampling for proper confidence intervals."""
        arr = _np.array(profiles)
        w = _np.array(weights, dtype=float)
        _ws = w.sum()
        w_prob = w / _ws if _ws > 0 else _np.ones_like(w) / max(len(w), 1)
        n = len(profiles)
        boot_means = _np.zeros((n_boot, 24))
        rng = _np.random.default_rng(42)
        for b in range(n_boot):
            idx = rng.choice(n, size=n, replace=True, p=w_prob)
            boot_means[b] = _np.mean(arr[idx], axis=0)
        bands = {}
        for ci in ci_levels:
            lo = (1 - ci) / 2 * 100
            hi = (1 + ci) / 2 * 100
            bands[ci] = {
                "lower": _np.percentile(boot_means, lo, axis=0),
                "upper": _np.percentile(boot_means, hi, axis=0),
            }
        return bands

    @st.cache_data(ttl=3600, show_spinner=False)
    def _fetch_wx_forecast():
        results = {}
        for node in _WEATHER_NODES:
            try:
                r = _sim_req.get("https://api.open-meteo.com/v1/forecast", params={
                    "latitude": node["lat"], "longitude": node["lon"],
                    "hourly": "temperature_2m,wind_speed_10m,cloud_cover,relative_humidity_2m",
                    "temperature_unit": "fahrenheit", "wind_speed_unit": "mph",
                    "timezone": "America/Chicago", "forecast_days": 3,
                }, timeout=10)
                data = r.json().get("hourly", {})
                if data:
                    results[node["name"]] = {**data, "weight": node["weight"]}
            except Exception:
                pass
        return results

    @st.cache_data(ttl=86400, show_spinner=False)
    def _fetch_wx_history(start_date, end_date):
        results = {}
        for node in _WEATHER_NODES:
            try:
                r = _sim_req.get("https://archive-api.open-meteo.com/v1/archive", params={
                    "latitude": node["lat"], "longitude": node["lon"],
                    "hourly": "temperature_2m,wind_speed_10m,cloud_cover,relative_humidity_2m",
                    "temperature_unit": "fahrenheit", "wind_speed_unit": "mph",
                    "timezone": "America/Chicago",
                    "start_date": start_date, "end_date": end_date,
                }, timeout=30)
                data = r.json().get("hourly", {})
                if data:
                    results[node["name"]] = data
            except Exception:
                pass
        return results

    @st.cache_data(ttl=3600, show_spinner=False)
    def _fetch_gas_history(days):
        from src.data_engine import fetch_massive_data
        df = fetch_massive_data("UNG", days)
        if df is not None and not df.empty:
            return df
        return None

    @st.cache_data(ttl=3600, show_spinner=False)
    def _fetch_load_history_cached(days_back):
        """Fetch ERCOT historical load for demand-based matching."""
        try:
            _load_hist = ercot_api.fetch_load_history(days_back=days_back)
            return _load_hist
        except Exception:
            return None

    # ── Load forecast data ──
    with st.spinner("Loading weather forecast from 5 nodes..."):
        _forecast = _fetch_wx_forecast()

    if not _forecast:
        st.warning("Could not fetch weather forecast from Open-Meteo.")
    else:
        # Build tomorrow's profile — WEIGHTED BLEND across all nodes (Task #6)
        _tm = {"temps": [], "winds": [], "clouds": [], "humids": []}
        _node_weights_sum = sum(nd["weight"] for nd in _forecast.values()) or 1.0
        for _, nd in _forecast.items():
            w = nd["weight"] / _node_weights_sum  # normalize so weights sum to 1
            for key, src in [("temps", "temperature_2m"), ("winds", "wind_speed_10m"),
                              ("clouds", "cloud_cover"), ("humids", "relative_humidity_2m")]:
                vals = nd.get(src, [])
                tomorrow_vals = vals[24:48] if len(vals) >= 48 else vals[-24:]
                _tm[key].append([v * w for v in tomorrow_vals])

        _tm_temp = _np.sum(_tm["temps"], axis=0)
        _tm_wind = _np.sum(_tm["winds"], axis=0)
        _tm_cloud = _np.sum(_tm["clouds"], axis=0)
        _tm_humid = _np.sum(_tm["humids"], axis=0) if _tm["humids"] and len(_tm["humids"][0]) > 0 else _np.full(24, 50.0)

        # Heat index uses properly blended humidity (Task #6)
        _tm_hi = [_heat_index(t, h) for t, h in zip(_tm_temp, _tm_humid)]

        _temp_high = float(max(_tm_temp))
        _temp_low = float(min(_tm_temp))
        _hi_high = float(max(_tm_hi))
        _wind_avg = float(_np.mean(_tm_wind))
        _cloud_avg = float(_np.mean(_tm_cloud))

        _tomorrow = _date_cls.today() + _td(days=1)
        _dow = _tomorrow.weekday()
        _month = _tomorrow.month
        _is_weekend = _dow >= 5 or _is_holiday(_tomorrow)

        # Gas price
        _gas_df = _fetch_gas_history(365)
        _gas_today = float(_gas_df.iloc[-1]["Close"]) if _gas_df is not None and not _gas_df.empty else None

        # ── Tomorrow's forecast display ──
        st.markdown("#### Tomorrow's Forecast")
        fc1, fc2, fc3, fc4, fc5, fc6 = st.columns(6)
        fc1.metric("Date", _tomorrow.strftime("%a %b %d"))
        fc2.metric("High / Low", f"{_temp_high:.0f}°F / {_temp_low:.0f}°F")
        fc3.metric("Heat Index", f"{_hi_high:.0f}°F", help="Feels-like temperature (temp + humidity) — blended across 5 weather nodes")
        fc4.metric("Avg Wind", f"{_wind_avg:.0f} mph")
        fc5.metric("Cloud Cover", f"{_cloud_avg:.0f}%")
        _day_type = "Holiday" if _is_holiday(_tomorrow) else ("Weekend" if _dow >= 5 else "Weekday")
        fc6.metric("Day Type", _day_type)

        # ── Multi-node weather map (Task #11) ──
        with st.expander("Weather Station Detail (5 Nodes)", expanded=False):
            _node_cols = st.columns(len(_forecast))
            for i, (nname, nd) in enumerate(_forecast.items()):
                with _node_cols[i]:
                    _nd_temps = nd.get("temperature_2m", [])
                    _nd_winds = nd.get("wind_speed_10m", [])
                    _nd_humids = nd.get("relative_humidity_2m", [])
                    _tm_nd = _nd_temps[24:48] if len(_nd_temps) >= 48 else _nd_temps[-24:]
                    _wn_nd = _nd_winds[24:48] if len(_nd_winds) >= 48 else _nd_winds[-24:]
                    _hu_nd = _nd_humids[24:48] if len(_nd_humids) >= 48 else _nd_humids[-24:]
                    if _tm_nd:
                        st.markdown(f"**{nname}** (wt: {nd['weight']:.0%})")
                        st.metric("High", f"{max(_tm_nd):.0f}°F")
                        st.metric("Wind", f"{_np.mean(_wn_nd):.0f} mph")
                        st.metric("Humidity", f"{_np.mean(_hu_nd):.0f}%")

            # Spatial divergence chart
            fig_nodes = go.Figure()
            _node_colors = {"Houston": "#ff6b35", "Dallas": "#00d1ff", "San Antonio": "#ff2277",
                            "Austin": "#00ff88", "Corpus Christi": "#ffdd00"}
            for nname, nd in _forecast.items():
                _nd_temps = nd.get("temperature_2m", [])
                _tm_nd = _nd_temps[24:48] if len(_nd_temps) >= 48 else _nd_temps[-24:]
                if _tm_nd:
                    fig_nodes.add_trace(go.Scatter(
                        x=list(range(1, len(_tm_nd) + 1)), y=_tm_nd, mode="lines",
                        name=nname, line=dict(color=_node_colors.get(nname, "#888"), width=2),
                    ))
            fig_nodes.update_layout(template="plotly_dark", height=200,
                                     margin=dict(t=10, b=0, l=0, r=0),
                                     xaxis_title="Hour", yaxis_title="°F",
                                     hovermode="x unified", legend=dict(orientation="h", y=-0.25))
            st.plotly_chart(fig_nodes, use_container_width=True, config={"displayModeBar": False})

            # Flag spatial divergence
            _node_highs = []
            for nname, nd in _forecast.items():
                _nd_temps = nd.get("temperature_2m", [])
                _tm_nd = _nd_temps[24:48] if len(_nd_temps) >= 48 else _nd_temps[-24:]
                if _tm_nd:
                    _node_highs.append(max(_tm_nd))
            if _node_highs and (max(_node_highs) - min(_node_highs)) > 10:
                st.warning(f"Spatial divergence: {max(_node_highs) - min(_node_highs):.0f}°F spread across nodes. "
                           f"Hub-specific pricing may vary significantly.")

        # Hourly chart with heat index
        fig_wx = go.Figure()
        _hrs = list(range(1, 25))
        fig_wx.add_trace(go.Scatter(x=_hrs, y=_tm_temp, mode="lines+markers",
                                     name="Temperature", line=dict(color="#ff6b35", width=2)))
        fig_wx.add_trace(go.Scatter(x=_hrs, y=_tm_hi, mode="lines",
                                     name="Heat Index", line=dict(color="#ff2277", width=1.5, dash="dot")))
        fig_wx.add_trace(go.Scatter(x=_hrs, y=_tm_wind, mode="lines",
                                     name="Wind (mph)", line=dict(color="#00d1ff", width=2), yaxis="y2"))
        fig_wx.update_layout(template="plotly_dark", height=250,
                              margin=dict(t=10, b=0, l=0, r=50),
                              xaxis_title="Hour", yaxis_title="°F",
                              yaxis2=dict(title="Wind (mph)", overlaying="y", side="right"),
                              hovermode="x unified", legend=dict(orientation="h", y=-0.2))
        st.plotly_chart(fig_wx, use_container_width=True, config={"displayModeBar": False})

        # ── Similar day matching ──
        st.divider()
        st.markdown("#### Similar Day Matching")

        sc1, sc2, sc3, sc4 = st.columns(4)
        with sc1:
            _lookback = st.slider("Lookback (months)", 3, 24, 12, key="sd_lb")
        with sc2:
            _n_sim = st.slider("Similar days", 3, 15, 7, key="sd_n")
        with sc3:
            _hub = st.selectbox("Settlement Point", ["HB_HUBAVG", "HB_HOUSTON", "HB_NORTH", "HB_SOUTH", "HB_WEST"], key="sd_hub")
        with sc4:
            _match_mode = st.selectbox("Match Mode", ["Weather + Hourly Curve", "Weather Only", "Load Shape"], key="sd_mode")

        _hist_start = (_date_cls.today() - _td(days=_lookback * 30)).isoformat()
        _hist_end = (_date_cls.today() - _td(days=1)).isoformat()

        with st.spinner(f"Loading {_lookback} months of weather history from 5 nodes..."):
            _hist_wx = _fetch_wx_history(_hist_start, _hist_end)

        # Fetch historical load for demand-based matching (Task #2)
        _hist_load = None
        if _match_mode == "Load Shape" and _has_api:
            with st.spinner("Loading ERCOT historical load data..."):
                _hist_load = _fetch_load_history_cached(_lookback * 30)

        if _hist_wx:
            # Population-weighted blend of historical weather across nodes
            _all_node_data = list(_hist_wx.values())
            _ref = _all_node_data[0]
            _h_times = _ref.get("time", [])
            _n_raw = len(_ref.get("temperature_2m", []))

            # Weighted blend of historical weather across all available nodes
            _node_w = [1.0 / len(_all_node_data)] * len(_all_node_data)
            for i, nd in enumerate(_all_node_data):
                for node in _WEATHER_NODES:
                    if len(nd.get("temperature_2m", [])) == _n_raw:
                        _node_w[i] = node["weight"]
                        break
            _nw_sum = sum(_node_w[:len(_all_node_data)]) or 1.0

            _h_temps = _np.zeros(_n_raw)
            _h_winds = _np.zeros(_n_raw)
            _h_clouds = _np.zeros(_n_raw)
            _h_humids = _np.zeros(_n_raw)
            for i, nd in enumerate(_all_node_data):
                w = _node_w[i] / _nw_sum
                t = nd.get("temperature_2m", [])
                wi = nd.get("wind_speed_10m", [])
                c = nd.get("cloud_cover", [])
                hu = nd.get("relative_humidity_2m", [])
                _min_len = min(len(t), _n_raw)
                _h_temps[:_min_len] += _np.array(t[:_min_len]) * w
                if wi:
                    _h_winds[:min(len(wi), _n_raw)] += _np.array(wi[:min(len(wi), _n_raw)]) * w
                if c:
                    _h_clouds[:min(len(c), _n_raw)] += _np.array(c[:min(len(c), _n_raw)]) * w
                if hu:
                    _h_humids[:min(len(hu), _n_raw)] += _np.array(hu[:min(len(hu), _n_raw)]) * w

            _h_temps = _h_temps.tolist()
            _h_winds = _h_winds.tolist()
            _h_clouds = _h_clouds.tolist()
            _h_humids = _h_humids.tolist()

            if len(_h_temps) >= 48:
                _n_hist_days = len(_h_temps) // 24
                _daily = []

                # Build historical load profiles for demand matching (Task #2)
                _load_profiles = {}
                if _hist_load is not None and not _hist_load.empty:
                    _total_col_l = next((c for c in _hist_load.columns if c.lower() == "total"), None)
                    _day_col_l = next((c for c in _hist_load.columns if "operatingday" in c.lower() or "date" in c.lower()), None)
                    _hr_col_l = next((c for c in _hist_load.columns if "hourending" in c.lower() or "hour" in c.lower()), None)
                    if not _total_col_l:
                        _zone_cols_l = [c for c in _hist_load.columns
                                        if c.lower() in ("coast", "east", "farwest", "north", "northc", "southern", "southc", "west")]
                        if _zone_cols_l:
                            _hist_load["_total"] = _hist_load[_zone_cols_l].sum(axis=1)
                            _total_col_l = "_total"
                    if _total_col_l and _day_col_l and _hr_col_l:
                        _hist_load["_load_val"] = pd.to_numeric(_hist_load[_total_col_l], errors="coerce")
                        _hist_load["_hour_val"] = pd.to_numeric(_hist_load[_hr_col_l], errors="coerce")
                        for _ld_date, _ld_grp in _hist_load.groupby(_day_col_l):
                            _ld_sorted = _ld_grp.sort_values("_hour_val")
                            _lp = _ld_sorted["_load_val"].dropna().values
                            if len(_lp) >= 20:
                                _lp = _lp[:24] if len(_lp) > 24 else _np.pad(_lp, (0, 24 - len(_lp)), mode="edge")
                                _load_profiles[str(_ld_date)[:10]] = _lp

                for i in range(_n_hist_days):
                    s, e = i * 24, (i + 1) * 24
                    dt_str = _h_times[s][:10] if s < len(_h_times) else ""
                    try:
                        dt = _dt.strptime(dt_str, "%Y-%m-%d").date()
                    except Exception:
                        continue

                    d_temps = _h_temps[s:e]
                    d_winds = _h_winds[s:e]
                    d_clouds = _h_clouds[s:e] if _h_clouds else [50] * 24
                    d_humids = _h_humids[s:e] if _h_humids else [50] * 24

                    t_high = max(d_temps) if d_temps else 70
                    t_low = min(d_temps) if d_temps else 50
                    hi_high = max(_heat_index(t, h) for t, h in zip(d_temps, d_humids)) if d_temps else 70
                    w_avg = sum(d_winds) / max(len(d_winds), 1)
                    c_avg = sum(d_clouds) / max(len(d_clouds), 1)
                    is_wknd = 1 if dt.weekday() >= 5 or _is_holiday(dt) else 0

                    gas_on_date = None
                    if _gas_df is not None and not _gas_df.empty:
                        _gas_mask = _gas_df.index.date <= dt
                        if _gas_mask.any():
                            gas_on_date = float(_gas_df[_gas_mask].iloc[-1]["Close"])

                    _daily.append({
                        "date": dt, "date_str": dt_str,
                        "temp_high": t_high, "temp_low": t_low, "hi_high": hi_high,
                        "wind_avg": w_avg, "cloud_avg": c_avg,
                        "dow": dt.weekday(), "month": dt.month, "is_weekend": is_wknd,
                        "gas_price": gas_on_date,
                        "hourly_temps": d_temps,
                        "hourly_winds": d_winds,
                        "load_profile": _load_profiles.get(dt_str),
                    })

                if _daily:
                    # ── Matching logic (Task #1, #2) ──
                    _target = _np.array([_hi_high, _temp_high, _temp_low, _wind_avg, _cloud_avg,
                                         _dow, _month, 1 if _is_weekend else 0])
                    _norm = _np.array([30, 30, 30, 15, 50, 3, 6, 1]) + 1e-6
                    _wts = _np.array([4.0, 3.0, 2.0, 2.5, 1.0, 1.5, 2.0, 3.0])

                    _tm_temp_hourly = list(_tm_temp[:24])

                    # Build estimated tomorrow load profile for demand matching
                    _tm_load_est = None
                    if _match_mode == "Load Shape" and _load_profiles:
                        # Use recent similar-temp days' load as proxy
                        _recent_loads = []
                        for d in _daily[-30:]:
                            if d["load_profile"] is not None and abs(d["temp_high"] - _temp_high) < 10:
                                _recent_loads.append(d["load_profile"])
                        if _recent_loads:
                            _tm_load_est = _np.mean(_recent_loads, axis=0)

                    for d in _daily:
                        # Aggregate feature distance
                        _f = _np.array([d["hi_high"], d["temp_high"], d["temp_low"],
                                        d["wind_avg"], d["cloud_avg"],
                                        d["dow"], d["month"], d["is_weekend"]])
                        _diff = (_target - _f) / _norm
                        _agg_dist = float(_np.sqrt((_diff * _wts) @ (_diff * _wts)))

                        if _match_mode == "Weather + Hourly Curve":
                            # Task #1: Hourly temperature profile matching
                            _curve_sim = _hourly_curve_similarity(_tm_temp_hourly, d["hourly_temps"])
                            _curve_penalty = (1.0 - _curve_sim) * 3.0  # high weight on curve shape
                            d["distance"] = _agg_dist + _curve_penalty
                            d["curve_match"] = round(_curve_sim * 100, 1)
                        elif _match_mode == "Load Shape" and d["load_profile"] is not None and _tm_load_est is not None:
                            # Task #2: Demand-based matching
                            _load_sim = _hourly_curve_similarity(_tm_load_est, d["load_profile"])
                            _load_penalty = (1.0 - _load_sim) * 4.0
                            d["distance"] = _agg_dist * 0.4 + _load_penalty  # load dominates
                            d["curve_match"] = round(_load_sim * 100, 1)
                        else:
                            d["distance"] = _agg_dist
                            d["curve_match"] = None

                        d["similarity"] = round(100 / (1 + d["distance"]), 1)

                    _daily.sort(key=lambda d: d["distance"])
                    _sim_days = _daily[:_n_sim]

                    # Display table
                    _sim_rows = []
                    for d in _sim_days:
                        row = {
                            "Date": d["date"].strftime("%Y-%m-%d (%a)"),
                            "High": f"{d['temp_high']:.0f}°F",
                            "Heat Idx": f"{d['hi_high']:.0f}°F",
                            "Wind": f"{d['wind_avg']:.0f} mph",
                            "Gas": f"${d['gas_price']:.2f}" if d["gas_price"] else "N/A",
                            "Similarity": f"{d['similarity']:.0f}%",
                        }
                        if d.get("curve_match") is not None:
                            row["Curve Match"] = f"{d['curve_match']:.0f}%"
                        _sim_rows.append(row)
                    st.dataframe(pd.DataFrame(_sim_rows), use_container_width=True, hide_index=True)

                    # ── Fetch ERCOT prices + build forecast ──
                    st.divider()
                    st.markdown("#### Day-Ahead Price Estimate")

                    if _has_api:
                        _profiles = []
                        _profile_dates = []
                        _profile_gas = []
                        _profile_weights = []
                        _rt_profiles = []  # Task #7: DAM-RT basis

                        for d in _sim_days:
                            try:
                                _dam = ercot_api.fetch_dam_spp(d["date_str"], settlement_point=_hub)
                                if _dam is None or _dam.empty:
                                    continue
                                _pc = next((c for c in _dam.columns if "price" in c.lower() or "spp" in c.lower()), None)
                                if not _pc:
                                    continue
                                prices = pd.to_numeric(_dam[_pc], errors="coerce").dropna().values
                                if len(prices) < 20:
                                    continue
                                if len(prices) > 24:
                                    prices = prices[:24]
                                elif len(prices) < 24:
                                    prices = _np.pad(prices, (0, 24 - len(prices)), mode="edge")

                                # Gas adjustment
                                gas_adj = 1.0
                                if _gas_today and d.get("gas_price") and d["gas_price"] > 0:
                                    gas_adj = _gas_today / d["gas_price"]
                                    gas_adj = max(0.5, min(2.0, gas_adj))

                                _profiles.append(prices * gas_adj)
                                _profile_dates.append(d["date_str"])
                                _profile_gas.append(gas_adj)
                                _profile_weights.append(1.0 / (d["distance"] + 0.1))

                                # Task #7: Fetch RT prices for DAM-RT basis
                                try:
                                    _rt_d = ercot_api.fetch_rt_spp(d["date_str"], settlement_point=_hub)
                                    if _rt_d is not None and not _rt_d.empty:
                                        _rt_pc = next((c for c in _rt_d.columns if "price" in c.lower() or "spp" in c.lower()), None)
                                        _rt_hc = next((c for c in _rt_d.columns if "hour" in c.lower()), None)
                                        if _rt_pc and _rt_hc:
                                            _rt_d["_hr"] = pd.to_numeric(_rt_d[_rt_hc], errors="coerce")
                                            _rt_d[_rt_pc] = pd.to_numeric(_rt_d[_rt_pc], errors="coerce")
                                            _rt_hourly_d = _rt_d.dropna(subset=["_hr", _rt_pc]).groupby("_hr")[_rt_pc].mean()
                                            if len(_rt_hourly_d) >= 20:
                                                _rt_vals = _rt_hourly_d.sort_index().values[:24]
                                                if len(_rt_vals) < 24:
                                                    _rt_vals = _np.pad(_rt_vals, (0, 24 - len(_rt_vals)), mode="edge")
                                                _rt_profiles.append((_rt_vals - prices) * gas_adj)
                                except Exception:
                                    pass
                            except Exception:
                                pass

                        if _profiles:
                            _prof_arr = _np.array(_profiles)
                            _w_arr = _np.array(_profile_weights, dtype=float)
                            _w_sum = _w_arr.sum()
                            _w_norm = _w_arr / _w_sum if _w_sum > 0 else _np.ones_like(_w_arr) / max(len(_w_arr), 1)

                            # Task #4: Robust weighted mean (spike-resistant)
                            _wt_mean = _robust_weighted_mean(_profiles, _profile_weights)

                            # Task #12: Bootstrap confidence intervals
                            _boot_bands = _bootstrap_confidence(_profiles, _profile_weights)
                            _ci80 = _boot_bands[0.80]
                            _ci95 = _boot_bands[0.95]
                            _min_prof = _ci95["lower"]
                            _max_prof = _ci95["upper"]

                            # Confidence: CV of weighted profiles
                            _wt_std = _np.sqrt(_np.average((_prof_arr - _wt_mean)**2, axis=0, weights=_w_norm))
                            _avg_cv = float(_np.mean(_wt_std / (_np.abs(_wt_mean) + 1)))
                            _confidence = "High" if _avg_cv < 0.3 else ("Medium" if _avg_cv < 0.6 else "Low")
                            _conf_color = COLORS["success"] if _confidence == "High" else (COLORS["warning"] if _confidence == "Medium" else COLORS["danger"])

                            # Task #3: Hub basis adjustment
                            _basis_adj = _np.zeros(24)
                            _basis_label = ""
                            if _hub != "HB_HUBAVG":
                                try:
                                    _hub_profiles = []
                                    _hubavg_profiles = []
                                    for d in _sim_days[:5]:
                                        _h_dam = ercot_api.fetch_dam_spp(d["date_str"], settlement_point=_hub)
                                        _a_dam = ercot_api.fetch_dam_spp(d["date_str"], settlement_point="HB_HUBAVG")
                                        if _h_dam is not None and _a_dam is not None:
                                            _h_pc = next((c for c in _h_dam.columns if "price" in c.lower()), None)
                                            _a_pc = next((c for c in _a_dam.columns if "price" in c.lower()), None)
                                            if _h_pc and _a_pc:
                                                _hp = pd.to_numeric(_h_dam[_h_pc], errors="coerce").dropna().values
                                                _ap = pd.to_numeric(_a_dam[_a_pc], errors="coerce").dropna().values
                                                if len(_hp) >= 20 and len(_ap) >= 20:
                                                    _hp = _hp[:24] if len(_hp) > 24 else _np.pad(_hp, (0, 24 - len(_hp)), mode="edge")
                                                    _ap = _ap[:24] if len(_ap) > 24 else _np.pad(_ap, (0, 24 - len(_ap)), mode="edge")
                                                    _hub_profiles.append(_hp)
                                                    _hubavg_profiles.append(_ap)
                                    if _hub_profiles:
                                        _avg_hub = _np.mean(_hub_profiles, axis=0)
                                        _avg_hubavg = _np.mean(_hubavg_profiles, axis=0)
                                        _basis_adj = _avg_hub - _avg_hubavg
                                        _basis_label = f" | Avg basis vs HUBAVG: ${_np.mean(_basis_adj):+.2f}/MWh"
                                except Exception:
                                    pass

                            # ── Forecast chart with bootstrap bands ──
                            fig_fc = go.Figure()

                            # 95% CI band
                            fig_fc.add_trace(go.Scatter(
                                x=_hrs + _hrs[::-1],
                                y=list(_ci95["upper"]) + list(_ci95["lower"][::-1]),
                                fill="toself", fillcolor="rgba(0,209,255,0.06)",
                                line=dict(color="rgba(0,0,0,0)"), name="95% CI", hoverinfo="skip",
                            ))
                            # 80% CI band
                            fig_fc.add_trace(go.Scatter(
                                x=_hrs + _hrs[::-1],
                                y=list(_ci80["upper"]) + list(_ci80["lower"][::-1]),
                                fill="toself", fillcolor="rgba(0,209,255,0.12)",
                                line=dict(color="rgba(0,0,0,0)"), name="80% CI", hoverinfo="skip",
                            ))

                            # Individual profiles (faded)
                            for prof, dt, ga in zip(_profiles, _profile_dates, _profile_gas):
                                _ga_label = f" (gas adj {ga:.2f}x)" if abs(ga - 1.0) > 0.05 else ""
                                fig_fc.add_trace(go.Scatter(
                                    x=_hrs, y=prof, mode="lines",
                                    line=dict(color="#444", width=1), opacity=0.3,
                                    name=f"{dt}{_ga_label}", showlegend=False,
                                ))

                            # Weighted mean
                            fig_fc.add_trace(go.Scatter(
                                x=_hrs, y=_wt_mean, mode="lines+markers",
                                line=dict(color="#00d1ff", width=3), marker=dict(size=5),
                                name="Forecast (Robust Weighted)",
                            ))

                            fig_fc.update_layout(
                                template="plotly_dark", height=400,
                                margin=dict(t=10, b=0, l=0, r=0),
                                xaxis_title="Hour Ending", yaxis_title=f"Price ($/MWh) — {_hub}",
                                hovermode="x unified", legend=dict(orientation="h", y=-0.15),
                            )
                            st.plotly_chart(fig_fc, use_container_width=True, config={"displayModeBar": False})

                            # ── Metrics ──
                            _peak = _wt_mean[6:22]
                            _offpk = _np.concatenate([_wt_mean[:6], _wt_mean[22:]])
                            _superpeak = _wt_mean[13:19]  # HE14-19 (Task #10)

                            pm1, pm2, pm3, pm4, pm5 = st.columns(5)
                            pm1.metric("Avg Price", f"${_np.mean(_wt_mean):.2f}/MWh")
                            pm2.metric("Peak (HE7-22)", f"${_np.mean(_peak):.2f}/MWh")
                            pm3.metric("Off-Peak", f"${_np.mean(_offpk):.2f}/MWh")
                            pm4.metric("Peak HE", f"${max(_wt_mean):.0f} (HE{_np.argmax(_wt_mean)+1})")
                            pm5.markdown(
                                f'<div style="text-align:center;padding:8px;border:1px solid {_conf_color};border-radius:6px;">'
                                f'<div style="font-size:0.65rem;color:#888;">CONFIDENCE</div>'
                                f'<div style="font-size:1.1rem;font-weight:700;color:{_conf_color};">{_confidence}</div>'
                                f'<div style="font-size:0.6rem;color:#888;">CV: {_avg_cv:.2f}</div>'
                                f'</div>', unsafe_allow_html=True)

                            _caption_parts = [f"**Peak spread:** ${_np.mean(_peak) - _np.mean(_offpk):.2f}/MWh"]
                            if _gas_today:
                                _caption_parts.append(f"**Gas adjustment:** Applied (UNG ${_gas_today:.2f})")
                            if _basis_label:
                                _caption_parts.append(f"**Basis:** {_basis_label}")
                            st.caption(" | ".join(_caption_parts))

                            # ── Alerts ──
                            if _hi_high > 100:
                                st.error(f"**Extreme heat:** Heat index {_hi_high:.0f}°F. Expect scarcity pricing HE14-18. "
                                         f"Similar hot days peaked at ${max(_wt_mean):.0f}/MWh.")
                            elif _hi_high > 95:
                                st.warning(f"**Heat alert:** Heat index {_hi_high:.0f}°F. Watch for elevated afternoon prices.")
                            if _wind_avg > 18:
                                st.info(f"**High wind:** {_wind_avg:.0f} mph avg. Potential negative prices overnight (HE1-6).")
                            if _confidence == "Low":
                                st.warning("**Low confidence:** Similar days had widely varying prices. Use this forecast as a rough guide only.")

                            # ── Block-Level Product View (Task #10) ──
                            st.divider()
                            st.markdown("#### Block Products & P&L Scenarios")

                            _blocks = {
                                "On-Peak (HE7-22)": (6, 22),
                                "Off-Peak (HE1-6, 23-24)": (None, None),  # special
                                "Super-Peak (HE14-19)": (13, 19),
                                "Evening Ramp (HE17-21)": (16, 21),
                                "Overnight (HE1-6)": (0, 6),
                            }
                            _block_rows = []
                            for bname, (bs, be) in _blocks.items():
                                if bs is None:
                                    _bslice = _np.concatenate([_wt_mean[:6], _wt_mean[22:]])
                                    _b80l = _np.concatenate([_ci80["lower"][:6], _ci80["lower"][22:]])
                                    _b80u = _np.concatenate([_ci80["upper"][:6], _ci80["upper"][22:]])
                                else:
                                    _bslice = _wt_mean[bs:be]
                                    _b80l = _ci80["lower"][bs:be]
                                    _b80u = _ci80["upper"][bs:be]
                                _bavg = float(_np.mean(_bslice))
                                _block_rows.append({
                                    "Block": bname,
                                    "Forecast Avg": f"${_bavg:.2f}/MWh",
                                    "80% CI Low": f"${_np.mean(_b80l):.2f}",
                                    "80% CI High": f"${_np.mean(_b80u):.2f}",
                                    "Spread vs ATC": f"${_bavg - _np.mean(_wt_mean):+.2f}",
                                    "Hours": f"{len(_bslice)}",
                                })
                            st.dataframe(pd.DataFrame(_block_rows), use_container_width=True, hide_index=True)

                            # P&L scenarios
                            st.markdown("##### Entry Price Scenarios (per MWh)")
                            _atc_avg = float(_np.mean(_wt_mean))
                            _entries = [round(_atc_avg * m, 2) for m in [0.85, 0.90, 0.95, 1.0, 1.05, 1.10]]
                            _scenario_rows = []
                            for ep in _entries:
                                _pnl_peak = (float(_np.mean(_peak)) - ep) * 16  # 16 peak hours
                                _pnl_offpk = (float(_np.mean(_offpk)) - ep) * 8  # 8 off-peak hours
                                _pnl_atc = (_atc_avg - ep) * 24
                                _scenario_rows.append({
                                    "Entry Price": f"${ep:.2f}/MWh",
                                    "vs Forecast": f"{(ep / _atc_avg - 1) * 100:+.1f}%",
                                    "ATC P&L (24h)": f"${_pnl_atc:+.2f}",
                                    "Peak P&L (16h)": f"${_pnl_peak:+.2f}",
                                    "Off-Peak P&L (8h)": f"${_pnl_offpk:+.2f}",
                                })
                            st.dataframe(pd.DataFrame(_scenario_rows), use_container_width=True, hide_index=True)
                            st.caption("P&L per MWh position. Multiply by position size (MW) for total dollar P&L.")

                            # ── Hourly breakdown table ──
                            st.divider()
                            st.markdown("#### Hourly Price Forecast")
                            _tbl_rows = []
                            for h in range(24):
                                _pk = "Super-Peak" if 13 <= h < 19 else ("Peak" if 6 <= h < 22 else "Off-Peak")
                                _tbl_rows.append({
                                    "HE": h + 1,
                                    "Forecast": f"${_wt_mean[h]:.2f}",
                                    "80% Low": f"${_ci80['lower'][h]:.2f}",
                                    "80% High": f"${_ci80['upper'][h]:.2f}",
                                    "95% Low": f"${_ci95['lower'][h]:.2f}",
                                    "95% High": f"${_ci95['upper'][h]:.2f}",
                                    "Period": _pk,
                                })
                            st.dataframe(pd.DataFrame(_tbl_rows), use_container_width=True, hide_index=True, height=300)

                            # ── Seasonal Marginal Heat Rate & Spark Spread (Task #8) ──
                            st.divider()
                            st.markdown("#### Implied Spark Spread (Rolling Marginal Heat Rate)")
                            st.caption("Uses rolling 30-day regression of power prices on gas prices per hub — "
                                       "more accurate than flat 7.0 HR, especially in shoulder months with high renewables.")

                            _rolling_hr = 7.0  # default fallback
                            if _gas_today and _gas_today > 0:
                                # Task #8: Compute rolling marginal heat rate from similar days
                                try:
                                    _hr_pairs = []
                                    for d in _daily[:60]:  # last ~60 days
                                        if d.get("gas_price") and d["gas_price"] > 0:
                                            try:
                                                _d_dam = ercot_api.fetch_dam_spp(d["date_str"], settlement_point=_hub)
                                                if _d_dam is not None and not _d_dam.empty:
                                                    _d_pc = next((c for c in _d_dam.columns if "price" in c.lower()), None)
                                                    if _d_pc:
                                                        _d_prices = pd.to_numeric(_d_dam[_d_pc], errors="coerce").dropna()
                                                        if not _d_prices.empty:
                                                            _d_avg = float(_d_prices.mean())
                                                            _hr_pairs.append((_d_avg, d["gas_price"]))
                                            except Exception:
                                                pass
                                        if len(_hr_pairs) >= 20:
                                            break

                                    if len(_hr_pairs) >= 5:
                                        _pwr_vals = _np.array([p[0] for p in _hr_pairs])
                                        _gas_vals = _np.array([p[1] for p in _hr_pairs])
                                        # Simple linear regression: power = HR * gas + intercept
                                        _gas_mean = _np.mean(_gas_vals)
                                        _pwr_mean = _np.mean(_pwr_vals)
                                        _cov = _np.sum((_gas_vals - _gas_mean) * (_pwr_vals - _pwr_mean))
                                        _var = _np.sum((_gas_vals - _gas_mean) ** 2)
                                        if _var > 0:
                                            _rolling_hr = float(_cov / _var)
                                            _rolling_hr = max(4.0, min(15.0, _rolling_hr))  # sanity bounds
                                except Exception:
                                    pass

                                _fuel_cost = _gas_today * _rolling_hr
                                _spark = _wt_mean - _fuel_cost

                                fig_spark_sd = go.Figure()
                                _spark_colors = [COLORS["success"] if v > 0 else COLORS["danger"] for v in _spark]
                                fig_spark_sd.add_trace(go.Bar(
                                    x=_hrs, y=_spark, marker_color=_spark_colors, name="Spark Spread",
                                ))
                                fig_spark_sd.add_hline(y=0, line_color="white", line_width=0.5)
                                fig_spark_sd.update_layout(
                                    template="plotly_dark", height=250,
                                    margin=dict(t=10, b=0, l=0, r=0),
                                    xaxis_title="Hour Ending", yaxis_title="Spark Spread ($/MWh)",
                                    hovermode="x unified",
                                )
                                st.plotly_chart(fig_spark_sd, use_container_width=True, config={"displayModeBar": False})

                                _pos_hours = sum(1 for v in _spark if v > 0)
                                st.caption(
                                    f"Fuel cost: ~${_fuel_cost:.2f}/MWh (UNG ${_gas_today:.2f} × "
                                    f"**{_rolling_hr:.1f} rolling HR**). "
                                    f"**{_pos_hours}/24 hours** profitable for gas generation."
                                )
                                if abs(_rolling_hr - 7.0) > 1.0:
                                    st.info(f"Rolling marginal heat rate ({_rolling_hr:.1f}) differs significantly from "
                                            f"standard 7.0 — likely due to {'high renewable output suppressing prices' if _rolling_hr < 7.0 else 'tight supply conditions'}.")
                            else:
                                st.info("Gas price unavailable — cannot compute spark spread.")

                            # ── DAM-RT Basis Spread Forecast (Task #7) ──
                            if _rt_profiles:
                                st.divider()
                                st.markdown("#### DAM-RT Basis Forecast")
                                st.caption("Expected RT premium/discount vs DAM based on similar day historical patterns. "
                                           "Positive = RT traded above DAM (under-scheduled demand or unit trips).")

                                _rt_basis_avg = _np.mean(_rt_profiles, axis=0)
                                fig_basis_fc = go.Figure()
                                _basis_colors = [COLORS["success"] if v > 0 else COLORS["danger"] for v in _rt_basis_avg]
                                fig_basis_fc.add_trace(go.Bar(
                                    x=_hrs, y=_rt_basis_avg, marker_color=_basis_colors, name="RT-DAM Spread",
                                ))
                                fig_basis_fc.add_hline(y=0, line_color="white", line_width=0.5)
                                fig_basis_fc.update_layout(
                                    template="plotly_dark", height=220,
                                    margin=dict(t=10, b=0, l=0, r=0),
                                    xaxis_title="Hour Ending", yaxis_title="RT − DAM ($/MWh)",
                                    hovermode="x unified",
                                )
                                st.plotly_chart(fig_basis_fc, use_container_width=True, config={"displayModeBar": False})

                                _rt_avg_basis = float(_np.mean(_rt_basis_avg))
                                _rt_peak_basis = float(_np.mean(_rt_basis_avg[6:22]))
                                st.caption(
                                    f"Avg RT-DAM spread: ${_rt_avg_basis:+.2f}/MWh | "
                                    f"Peak hours: ${_rt_peak_basis:+.2f}/MWh | "
                                    f"Based on {len(_rt_profiles)} similar days"
                                )

                            # ── ERCOT Reserve Margin Context (Task #9) ──
                            if _has_api:
                                st.divider()
                                st.markdown("#### Grid Conditions & Reserve Margin")
                                try:
                                    from src.ercot_api import fetch_dashboard
                                    _sd_data = fetch_dashboard("supply-demand")
                                    if _sd_data:
                                        _sd_current = _sd_data.get("currentCondition", {})
                                        _capacity = _sd_current.get("totalCapGenRez")
                                        _demand = _sd_current.get("totalLoadMW") or _sd_current.get("totalForecast")
                                        _reserves = None

                                        if _capacity and _demand:
                                            try:
                                                _cap_mw = float(_capacity)
                                                _dem_mw = float(_demand)
                                                _reserves = _cap_mw - _dem_mw
                                            except (ValueError, TypeError):
                                                pass

                                        gc1, gc2, gc3, gc4 = st.columns(4)
                                        if _capacity:
                                            gc1.metric("Total Capacity", f"{float(_capacity):,.0f} MW")
                                        if _demand:
                                            gc2.metric("Current Demand", f"{float(_demand):,.0f} MW")
                                        if _reserves is not None:
                                            _res_color = "normal" if _reserves > 5000 else "inverse"
                                            gc3.metric("Reserve Margin", f"{_reserves:,.0f} MW",
                                                       delta="Adequate" if _reserves > 5000 else "TIGHT",
                                                       delta_color=_res_color)
                                            if _reserves > 0 and _dem_mw > 0:
                                                gc4.metric("Reserve %", f"{_reserves / _dem_mw * 100:.1f}%")

                                        if _reserves is not None and _reserves < 3000:
                                            st.error(f"**TIGHT RESERVES:** Only {_reserves:,.0f} MW of reserve margin. "
                                                     f"Scarcity pricing risk is HIGH. ORDC adder likely active.")
                                        elif _reserves is not None and _reserves < 5000:
                                            st.warning(f"**Watch reserves:** {_reserves:,.0f} MW margin. "
                                                       f"Prices may spike if a large unit trips or demand exceeds forecast.")

                                    # Ancillary services context
                                    _as_data = fetch_dashboard("ancillary-services")
                                    if _as_data:
                                        _as_info = _as_data.get("currentFrequency", {})
                                        _freq = _as_info.get("currentFrequency")
                                        if _freq:
                                            try:
                                                _freq_val = float(_freq)
                                                _freq_dev = abs(_freq_val - 60.0)
                                                if _freq_dev > 0.03:
                                                    st.warning(f"Grid frequency: {_freq_val:.3f} Hz "
                                                               f"(deviation: {_freq_dev:.3f} Hz) — elevated stress.")
                                            except (ValueError, TypeError):
                                                pass
                                except Exception:
                                    pass

                            # ── ERCOT load context ──
                            if _has_api:
                                st.divider()
                                st.markdown("#### ERCOT Load Context")
                                try:
                                    _yday_load = None
                                    _load_date_label = ""
                                    for _dback in [1, 2, 3]:
                                        _ld = (_date_cls.today() - _td(days=_dback)).isoformat()
                                        _yday_load = ercot_api.fetch_actual_load(_ld)
                                        if _yday_load is not None and not _yday_load.empty:
                                            _load_date_label = _ld
                                            break
                                    if _yday_load is not None and not _yday_load.empty:
                                        _total_col = next((c for c in _yday_load.columns if c.lower() == "total"), None)
                                        if not _total_col:
                                            _zone_cols = [c for c in _yday_load.columns
                                                          if c.lower() in ("coast", "east", "farwest", "north", "northc",
                                                                           "southern", "southc", "west")]
                                            if _zone_cols:
                                                _yday_load["_total"] = _yday_load[_zone_cols].sum(axis=1)
                                                _total_col = "_total"

                                        if _total_col:
                                            _load_vals = pd.to_numeric(_yday_load[_total_col], errors="coerce").dropna()
                                            if not _load_vals.empty:
                                                _peak_load = float(_load_vals.max())
                                                _avg_load = float(_load_vals.mean())
                                                lc1, lc2 = st.columns(2)
                                                lc1.metric(f"Peak Load ({_load_date_label})", f"{_peak_load:,.0f} MW")
                                                lc2.metric(f"Avg Load ({_load_date_label})", f"{_avg_load:,.0f} MW")
                                                if _hi_high > 95 and _peak_load < 60000:
                                                    st.warning(f"Tomorrow's heat index ({_hi_high:.0f}°F) is higher than yesterday — "
                                                               f"expect load to exceed {_peak_load:,.0f} MW peak.")
                                            else:
                                                st.caption("Load data returned but values could not be parsed.")
                                        else:
                                            st.caption(f"Load data columns: {list(_yday_load.columns)} — no total found.")
                                    else:
                                        st.caption("ERCOT load data unavailable for yesterday.")
                                except Exception as e:
                                    st.caption(f"Load context error: {e}")

                            # ── Rolling MAPE Tracker (Task #5) ──
                            st.divider()
                            st.markdown("#### Forecast Accuracy Tracker")
                            try:
                                from src.db import get_client
                                _db_acc = get_client()
                                _yesterday_dt = _date_cls.today() - _td(days=1)
                                _today_mape = None

                                if _db_acc and _has_api:
                                    import json as _jacc

                                    # Evaluate yesterday's forecast
                                    _prev_fc = _db_acc.table("ai_response_cache").select("response")\
                                        .eq("input_hash", f"simday_{_yesterday_dt.isoformat()}_{_hub}").limit(1).execute()
                                    if _prev_fc.data:
                                        _prev_data = _prev_fc.data[0]["response"]
                                        if isinstance(_prev_data, str):
                                            _prev_data = _jacc.loads(_prev_data)
                                        _prev_forecast = _prev_data.get("forecast", [])

                                        if _prev_forecast:
                                            _yday_dam = ercot_api.fetch_dam_spp(_yesterday_dt.isoformat(), settlement_point=_hub)
                                            if _yday_dam is not None and not _yday_dam.empty:
                                                _pc = next((c for c in _yday_dam.columns if "price" in c.lower() or "spp" in c.lower()), None)
                                                if _pc:
                                                    _actual = pd.to_numeric(_yday_dam[_pc], errors="coerce").dropna().values
                                                    if len(_actual) >= 20:
                                                        if len(_actual) > 24:
                                                            _actual = _actual[:24]
                                                        elif len(_actual) < 24:
                                                            _actual = _np.pad(_actual, (0, 24 - len(_actual)), mode="edge")
                                                        _prev_fc_arr = _np.array(_prev_forecast[:24])
                                                        if len(_prev_fc_arr) == 24:
                                                            _errors = _np.abs(_actual - _prev_fc_arr)
                                                            _today_mape = float(_np.mean(_errors / (_np.abs(_actual) + 1)) * 100)
                                                            _mae = float(_np.mean(_errors))

                                                            # Save daily MAPE to Supabase
                                                            try:
                                                                _db_acc.table("ai_response_cache").upsert({
                                                                    "input_hash": f"simday_mape_{_yesterday_dt.isoformat()}_{_hub}",
                                                                    "model": "similar_day_accuracy",
                                                                    "source_page": "power_strategies",
                                                                    "ticker": _hub,
                                                                    "response": _jacc.dumps({
                                                                        "date": _yesterday_dt.isoformat(),
                                                                        "mape": _today_mape,
                                                                        "mae": _mae,
                                                                        "hub": _hub,
                                                                    }),
                                                                    "prompt_summary": f"MAPE {_hub} {_yesterday_dt}",
                                                                    "expires_at": (_dt.now() + _td(days=90)).isoformat(),
                                                                }, on_conflict="input_hash").execute()
                                                            except Exception:
                                                                pass

                                                            # Yesterday's accuracy chart
                                                            fig_acc = go.Figure()
                                                            fig_acc.add_trace(go.Scatter(x=_hrs, y=_actual, mode="lines+markers",
                                                                                         name="Actual", line=dict(color=COLORS["success"], width=2)))
                                                            fig_acc.add_trace(go.Scatter(x=_hrs, y=_prev_fc_arr, mode="lines+markers",
                                                                                         name="Forecast", line=dict(color=COLORS["accent"], width=2, dash="dash")))
                                                            fig_acc.update_layout(template="plotly_dark", height=300,
                                                                                   margin=dict(t=10, b=0, l=0, r=0),
                                                                                   xaxis_title="Hour Ending", yaxis_title="$/MWh",
                                                                                   hovermode="x unified")
                                                            st.plotly_chart(fig_acc, use_container_width=True, config={"displayModeBar": False})

                                                            ac1, ac2, ac3 = st.columns(3)
                                                            _mape_color = COLORS["success"] if _today_mape < 15 else (COLORS["warning"] if _today_mape < 30 else COLORS["danger"])
                                                            ac1.metric("MAPE (Yesterday)", f"{_today_mape:.1f}%",
                                                                       help="Mean Absolute Percentage Error. <15% = good, >30% = poor")
                                                            ac2.metric("MAE (Yesterday)", f"${_mae:.2f}/MWh")
                                                            ac3.markdown(
                                                                f'<div style="text-align:center;padding:8px;border:1px solid {_mape_color};border-radius:6px;">'
                                                                f'<div style="font-size:0.65rem;color:#888;">ACCURACY</div>'
                                                                f'<div style="font-size:1.1rem;font-weight:700;color:{_mape_color};">'
                                                                f'{"Good" if _today_mape < 15 else "Fair" if _today_mape < 30 else "Poor"}</div>'
                                                                f'</div>', unsafe_allow_html=True)

                                    # Rolling MAPE trend (Task #5)
                                    if _db_acc:
                                        try:
                                            _mape_history = _db_acc.table("ai_response_cache").select("response")\
                                                .eq("model", "similar_day_accuracy").eq("ticker", _hub)\
                                                .order("created_at", desc=True).limit(30).execute()
                                            if _mape_history.data and len(_mape_history.data) >= 3:
                                                _mape_points = []
                                                for rec in _mape_history.data:
                                                    rd = rec["response"]
                                                    if isinstance(rd, str):
                                                        rd = _jacc.loads(rd)
                                                    if "mape" in rd and "date" in rd:
                                                        _mape_points.append({"date": rd["date"], "mape": rd["mape"], "mae": rd.get("mae", 0)})

                                                if len(_mape_points) >= 3:
                                                    _mape_points.sort(key=lambda x: x["date"])
                                                    _mp_dates = [p["date"] for p in _mape_points]
                                                    _mp_vals = [p["mape"] for p in _mape_points]

                                                    # Calculate rolling averages
                                                    _mp_arr = _np.array(_mp_vals)
                                                    _roll7 = []
                                                    _roll30 = []
                                                    for i in range(len(_mp_arr)):
                                                        _r7 = _mp_arr[max(0, i - 6):i + 1]
                                                        _roll7.append(float(_np.mean(_r7)))
                                                        _r30 = _mp_arr[max(0, i - 29):i + 1]
                                                        _roll30.append(float(_np.mean(_r30)))

                                                    st.markdown("##### Rolling Accuracy Trend")
                                                    fig_mape_trend = go.Figure()
                                                    fig_mape_trend.add_trace(go.Scatter(
                                                        x=_mp_dates, y=_mp_vals, mode="markers",
                                                        name="Daily MAPE", marker=dict(color="#888", size=4),
                                                    ))
                                                    fig_mape_trend.add_trace(go.Scatter(
                                                        x=_mp_dates, y=_roll7, mode="lines",
                                                        name="7-Day Avg", line=dict(color=COLORS["accent"], width=2),
                                                    ))
                                                    fig_mape_trend.add_trace(go.Scatter(
                                                        x=_mp_dates, y=_roll30, mode="lines",
                                                        name="30-Day Avg", line=dict(color=COLORS["success"], width=2, dash="dash"),
                                                    ))
                                                    fig_mape_trend.add_hline(y=15, line_dash="dot", line_color=COLORS["warning"],
                                                                              annotation_text="Good (<15%)")
                                                    fig_mape_trend.add_hline(y=30, line_dash="dot", line_color=COLORS["danger"],
                                                                              annotation_text="Poor (>30%)")
                                                    fig_mape_trend.update_layout(
                                                        template="plotly_dark", height=250,
                                                        margin=dict(t=10, b=0, l=0, r=0),
                                                        yaxis_title="MAPE %", hovermode="x unified",
                                                        legend=dict(orientation="h", y=-0.2),
                                                    )
                                                    st.plotly_chart(fig_mape_trend, use_container_width=True, config={"displayModeBar": False})

                                                    _latest_7 = _roll7[-1] if _roll7 else None
                                                    _latest_30 = _roll30[-1] if _roll30 else None
                                                    if _latest_7 is not None:
                                                        st.caption(
                                                            f"**7-day avg MAPE:** {_latest_7:.1f}% | "
                                                            f"**30-day avg MAPE:** {_latest_30:.1f}% | "
                                                            f"**Days tracked:** {len(_mape_points)}"
                                                        )
                                        except Exception:
                                            pass

                                    if (not _prev_fc.data) if _db_acc else True:
                                        st.caption("No forecast was saved for yesterday. Accuracy tracking starts after the first forecast.")
                            except Exception:
                                st.caption("Accuracy tracking unavailable.")

                            # ── Save forecast to Supabase for tracking ──
                            try:
                                from src.db import get_client
                                _db_fc = get_client()
                                if _db_fc:
                                    import json
                                    _db_fc.table("ai_response_cache").upsert({
                                        "input_hash": f"simday_{_tomorrow.isoformat()}_{_hub}",
                                        "model": "similar_day",
                                        "source_page": "power_strategies",
                                        "ticker": _hub,
                                        "response": json.dumps({
                                            "date": _tomorrow.isoformat(),
                                            "hub": _hub,
                                            "forecast": _wt_mean.tolist(),
                                            "confidence": _confidence,
                                            "n_days": len(_profiles),
                                            "gas_price": _gas_today,
                                            "rolling_hr": _rolling_hr,
                                            "match_mode": _match_mode,
                                        }),
                                        "prompt_summary": f"SimDay {_hub} {_tomorrow}",
                                        "expires_at": (_dt.now() + _td(hours=24)).isoformat(),
                                    }, on_conflict="input_hash").execute()
                            except Exception:
                                pass

                            # ── CSV Export ──
                            st.divider()
                            _csv_data = pd.DataFrame({
                                "Hour Ending": _hrs,
                                "Forecast ($/MWh)": [round(v, 2) for v in _wt_mean],
                                "80% CI Low": [round(v, 2) for v in _ci80["lower"]],
                                "80% CI High": [round(v, 2) for v in _ci80["upper"]],
                                "95% CI Low": [round(v, 2) for v in _ci95["lower"]],
                                "95% CI High": [round(v, 2) for v in _ci95["upper"]],
                                "Period": ["Off-Peak" if i < 6 or i >= 22 else ("Super-Peak" if 13 <= i < 19 else "Peak") for i in range(24)],
                            })
                            st.download_button(
                                "Download Forecast CSV",
                                _csv_data.to_csv(index=False),
                                file_name=f"simday_forecast_{_hub}_{_tomorrow.isoformat()}.csv",
                                mime="text/csv",
                                use_container_width=True,
                            )

                            # ── Multi-Day Forecast Heatmap (3-day) ──
                            st.divider()
                            st.markdown("#### 3-Day Price Heatmap")
                            st.caption("Extends the similar-day method to the next 3 days using the extended weather forecast.")

                            try:
                                _multi_forecasts = {}
                                _multi_forecasts[_tomorrow.isoformat()] = _wt_mean

                                for _day_offset, _hr_start in [(2, 48), (3, 72)]:
                                    _fut_date = _date_cls.today() + _td(days=_day_offset)
                                    _fut_temps = []
                                    _fut_humids = []
                                    for _, nd in _forecast.items():
                                        w = nd["weight"] / _node_weights_sum
                                        temps = nd.get("temperature_2m", [])
                                        humids = nd.get("relative_humidity_2m", [])
                                        if len(temps) > _hr_start:
                                            _fut_temps.append([t * w for t in temps[_hr_start:_hr_start + 24]])
                                        if len(humids) > _hr_start:
                                            _fut_humids.append([h * w for h in humids[_hr_start:_hr_start + 24]])

                                    if _fut_temps and len(_fut_temps[0]) >= 20:
                                        _ft = _np.sum(_fut_temps, axis=0)[:24]
                                        _fhu = _np.sum(_fut_humids, axis=0)[:24] if _fut_humids else [50] * 24
                                        _ft_hi = [_heat_index(t, h) for t, h in zip(_ft, _fhu)]
                                        _ft_high = float(max(_ft_hi))
                                        _ft_low = float(min(_ft))
                                        _ft_wknd = 1 if _fut_date.weekday() >= 5 or _is_holiday(_fut_date) else 0

                                        _ft_target = _np.array([_ft_high, float(max(_ft)), _ft_low, _wind_avg, _cloud_avg,
                                                                 _fut_date.weekday(), _fut_date.month, _ft_wknd])
                                        for d in _daily:
                                            _f = _np.array([d["hi_high"], d["temp_high"], d["temp_low"],
                                                            d["wind_avg"], d["cloud_avg"],
                                                            d["dow"], d["month"], d["is_weekend"]])
                                            _diff = (_ft_target - _f) / _norm
                                            d["_tmp_dist"] = float(_np.sqrt((_diff * _wts) @ (_diff * _wts)))

                                        _daily.sort(key=lambda d: d["_tmp_dist"])
                                        _fut_sim = _daily[:_n_sim]

                                        _fut_profiles = []
                                        _fut_weights = []
                                        for d in _fut_sim:
                                            try:
                                                _fdam = ercot_api.fetch_dam_spp(d["date_str"], settlement_point=_hub)
                                                if _fdam is None or _fdam.empty:
                                                    continue
                                                _fpc = next((c for c in _fdam.columns if "price" in c.lower() or "spp" in c.lower()), None)
                                                if not _fpc:
                                                    continue
                                                fp = pd.to_numeric(_fdam[_fpc], errors="coerce").dropna().values
                                                if len(fp) >= 20:
                                                    fp = fp[:24] if len(fp) > 24 else _np.pad(fp, (0, 24 - len(fp)), mode="edge")
                                                    ga = 1.0
                                                    if _gas_today and d.get("gas_price") and d["gas_price"] > 0:
                                                        ga = max(0.5, min(2.0, _gas_today / d["gas_price"]))
                                                    _fut_profiles.append(fp * ga)
                                                    _fut_weights.append(1.0 / (d["_tmp_dist"] + 0.1))
                                            except Exception:
                                                pass

                                        if _fut_profiles:
                                            _multi_forecasts[_fut_date.isoformat()] = _robust_weighted_mean(
                                                _fut_profiles, _fut_weights)

                                if len(_multi_forecasts) >= 2:
                                    _hm_dates = sorted(_multi_forecasts.keys())
                                    _hm_z = [_multi_forecasts[d].tolist() for d in _hm_dates]
                                    _hm_labels = [_dt.strptime(d, "%Y-%m-%d").strftime("%a %b %d") for d in _hm_dates]

                                    fig_hm = go.Figure(go.Heatmap(
                                        x=_hrs, y=_hm_labels, z=_hm_z,
                                        colorscale=[[0, "#0d47a1"], [0.3, "#00838f"], [0.5, "#ffab00"],
                                                    [0.7, "#ff6d00"], [1.0, "#d50000"]],
                                        colorbar=dict(title="$/MWh", thickness=12),
                                        text=_np.round(_hm_z, 0).astype(int),
                                        texttemplate="%{text}",
                                        textfont=dict(size=9),
                                        hovertemplate="HE%{x}<br>%{y}<br>$%{z:.1f}/MWh<extra></extra>",
                                    ))
                                    fig_hm.update_layout(
                                        template="plotly_dark", height=200,
                                        margin=dict(t=10, b=0, l=0, r=0),
                                        xaxis_title="Hour Ending",
                                    )
                                    st.plotly_chart(fig_hm, use_container_width=True, config={"displayModeBar": False})
                                else:
                                    st.caption("Extended forecast unavailable (Open-Meteo provides 2 days by default).")
                            except Exception:
                                st.caption("Multi-day forecast unavailable.")

                            # ── RT Price Overlay (if market is open) ──
                            if _has_api:
                                try:
                                    _today_str_sd = _date_cls.today().isoformat()
                                    _today_rt = ercot_api.fetch_rt_spp(_today_str_sd, settlement_point=_hub)
                                    if _today_rt is not None and not _today_rt.empty:
                                        _rt_pc = next((c for c in _today_rt.columns if "price" in c.lower() or "spp" in c.lower()), None)
                                        _rt_hc = next((c for c in _today_rt.columns if "hour" in c.lower() or "deliveryhour" in c.lower()), None)
                                        if _rt_pc and _rt_hc:
                                            _today_rt["_hour"] = pd.to_numeric(_today_rt[_rt_hc], errors="coerce")
                                            _today_rt[_rt_pc] = pd.to_numeric(_today_rt[_rt_pc], errors="coerce")
                                            _rt_hourly = _today_rt.dropna(subset=["_hour", _rt_pc]).groupby("_hour")[_rt_pc].mean()

                                            if len(_rt_hourly) >= 3:
                                                st.divider()
                                                st.markdown("#### Today's RT Prices vs Tomorrow's Forecast")
                                                fig_rt = go.Figure()
                                                fig_rt.add_trace(go.Scatter(
                                                    x=_rt_hourly.index.astype(int).tolist(),
                                                    y=_rt_hourly.values,
                                                    mode="lines+markers", name="Today RT (actual)",
                                                    line=dict(color=COLORS["success"], width=2),
                                                ))
                                                fig_rt.add_trace(go.Scatter(
                                                    x=_hrs, y=_wt_mean, mode="lines",
                                                    name="Tomorrow Forecast",
                                                    line=dict(color=COLORS["accent"], width=2, dash="dash"),
                                                ))
                                                fig_rt.update_layout(
                                                    template="plotly_dark", height=300,
                                                    margin=dict(t=10, b=0, l=0, r=0),
                                                    xaxis_title="Hour Ending", yaxis_title="$/MWh",
                                                    hovermode="x unified",
                                                )
                                                st.plotly_chart(fig_rt, use_container_width=True, config={"displayModeBar": False})

                                                _rt_avg = float(_rt_hourly.mean())
                                                _fc_avg = float(_np.mean(_wt_mean))
                                                _diff_pct = (_fc_avg / _rt_avg - 1) * 100 if _rt_avg > 0 else 0
                                                st.caption(
                                                    f"Today's RT avg: ${_rt_avg:.2f}/MWh | "
                                                    f"Tomorrow forecast: ${_fc_avg:.2f}/MWh | "
                                                    f"{'Higher' if _diff_pct > 0 else 'Lower'} by {abs(_diff_pct):.1f}%"
                                                )
                                except Exception:
                                    pass

                            # ── Wind/Solar Generation Context ──
                            if _has_api:
                                try:
                                    _wind_gen = ercot_api.fetch_wind_hourly(
                                        (_date_cls.today() - _td(days=2)).isoformat(),
                                        (_date_cls.today() - _td(days=1)).isoformat())
                                    _solar_gen = ercot_api.fetch_solar_hourly(
                                        (_date_cls.today() - _td(days=2)).isoformat(),
                                        (_date_cls.today() - _td(days=1)).isoformat())

                                    if (_wind_gen is not None and not _wind_gen.empty) or \
                                       (_solar_gen is not None and not _solar_gen.empty):
                                        st.divider()
                                        st.markdown("#### Recent Renewable Generation")

                                        rc1, rc2 = st.columns(2)
                                        if _wind_gen is not None and not _wind_gen.empty:
                                            _wg_col = next((c for c in _wind_gen.columns if "gensystemwide" in c.lower() or "gen" in c.lower()), None)
                                            if _wg_col:
                                                _wg_vals = pd.to_numeric(_wind_gen[_wg_col], errors="coerce").dropna()
                                                if not _wg_vals.empty:
                                                    rc1.metric("Wind Generation (recent)", f"{_wg_vals.mean():,.0f} MW avg",
                                                               f"Peak: {_wg_vals.max():,.0f} MW")
                                                    if _wind_avg < 8:
                                                        st.warning(f"Tomorrow's wind forecast ({_wind_avg:.0f} mph) is LOW. "
                                                                   f"Wind generation likely below {_wg_vals.mean():,.0f} MW average → higher prices.")

                                        if _solar_gen is not None and not _solar_gen.empty:
                                            _sg_col = next((c for c in _solar_gen.columns if "gensystemwide" in c.lower() or "gen" in c.lower()), None)
                                            if _sg_col:
                                                _sg_vals = pd.to_numeric(_solar_gen[_sg_col], errors="coerce").dropna()
                                                if not _sg_vals.empty:
                                                    rc2.metric("Solar Generation (recent)", f"{_sg_vals.mean():,.0f} MW avg",
                                                               f"Peak: {_sg_vals.max():,.0f} MW")
                                                    if _cloud_avg > 70:
                                                        st.info(f"Tomorrow's cloud cover ({_cloud_avg:.0f}%) is HIGH. "
                                                                f"Solar output likely below {_sg_vals.max():,.0f} MW peak.")
                                except Exception:
                                    pass

                        else:
                            st.warning("Could not fetch ERCOT prices for similar days.")
                    else:
                        st.info("ERCOT API not configured. Connect ERCOT API for price estimates.")
                else:
                    st.warning("Could not build daily features from weather history.")
            else:
                st.warning("Not enough weather history (need 2+ days).")
        else:
            st.warning("Could not fetch historical weather.")


# ════════════════════════════════════════════════
# TAB 7: STRATEGY BACKTEST (from page 40)
# ════════════════════════════════════════════════
with tab7, error_boundary("Strategy Backtest"):
    st.subheader("Power Strategy Backtester")

    with st.expander("How to read this tab", expanded=False):
        st.markdown(
            "Backtests each power trading strategy using historical natural gas and proxy power prices. "
            "Uses de Prado methods to guard against overfitting:\n\n"
            "- **Walk-forward validation**: train on past, test on future — no look-ahead\n"
            "- **Sequential bootstrap**: honest p-values that respect serial dependence\n"
            "- **Deflated Sharpe Ratio**: adjusts for multiple testing (trying many strategies)\n"
            "- **Triple barrier exits**: profit-take, stop-loss, and time-expiry labels\n\n"
            "Each strategy generates daily P&L from the spread between power and fuel. "
            "We proxy ERCOT power prices using historical gas × heat rate when full power price history isn't available."
        )

    # We need historical gas prices for backtesting
    if gas_history is not None and not gas_history.empty:
        gas_hist = gas_history.copy()
        gas_hist.columns = ["gas"]
        gas_hist = gas_hist.dropna()

        if len(gas_hist) < 30:
            st.warning("Not enough gas price history for backtesting. Need at least 30 days.")
        else:
            st.caption(f"Using {len(gas_hist)} days of NG=F history for backtesting.")

            # ── Strategy signal generation ──
            gas_ret = gas_hist["gas"].pct_change().dropna()
            gas_ma_short = gas_hist["gas"].rolling(5).mean()
            gas_ma_long = gas_hist["gas"].rolling(20).mean()
            gas_vol = gas_ret.rolling(20).std()
            gas_z = (gas_hist["gas"] - gas_hist["gas"].rolling(63).mean()) / gas_hist["gas"].rolling(63).std().replace(0, np.nan)

            strategies = {}
            idx = gas_hist.index

            # Reindex all indicators to the common index, fill NaN with neutral (0)
            gas_z_clean = gas_z.reindex(idx).fillna(0)
            gas_ma_short_clean = gas_ma_short.reindex(idx)
            gas_ma_long_clean = gas_ma_long.reindex(idx)
            gas_vol_clean = gas_vol.reindex(idx).fillna(0)
            gas_roc = gas_hist["gas"].pct_change(5).reindex(idx).fillna(0)

            # Define all available strategies
            all_strategies = {}

            sig1 = np.where(gas_z_clean < -1, 1.0, np.where(gas_z_clean > 1, -1.0, 0.0))
            all_strategies["Spark MR"] = pd.Series(sig1, index=idx)

            ma_valid = gas_ma_short_clean.notna() & gas_ma_long_clean.notna()
            sig2 = np.where(~ma_valid, 0.0, np.where(gas_ma_short_clean > gas_ma_long_clean, 1.0, -1.0))
            all_strategies["Gas Momentum"] = pd.Series(sig2, index=idx)

            vol_median = gas_vol_clean[gas_vol_clean > 0].median() if (gas_vol_clean > 0).any() else 0.01
            sig3 = np.where(gas_vol_clean > vol_median * 1.5, 1.0, 0.0)
            all_strategies["Vol Breakout"] = pd.Series(sig3, index=idx)

            sig4 = np.where(gas_roc < -0.03, 1.0, np.where(gas_roc > 0.03, -1.0, 0.0))
            all_strategies["Calendar Spread"] = pd.Series(sig4, index=idx)

            months = idx.month
            sig5 = np.where(np.isin(months, [10, 11, 12, 1, 2]), 1.0,
                            np.where(np.isin(months, [4, 5, 6, 7, 8, 9]), -1.0, 0.0))
            all_strategies["Seasonal"] = pd.Series(sig5, index=idx)

            # User selects which strategies to analyze
            STRATEGY_DESCRIPTIONS = {
                "Spark MR": "Mean reversion on gas z-score → spark spread direction",
                "Gas Momentum": "5D/20D MA crossover on gas → power price direction",
                "Vol Breakout": "High gas volatility → trade spreads (long vol)",
                "Calendar Spread": "Gas rate of change → contango/backwardation signal",
                "Seasonal": "Long heating season (Oct-Feb), short shoulder (Apr-Sep)",
            }

            selected_strats = st.multiselect(
                "Select strategies to analyze",
                list(all_strategies.keys()),
                default=list(all_strategies.keys()),
                format_func=lambda s: f"{s} — {STRATEGY_DESCRIPTIONS.get(s, '')}",
                key="ps_strat_select",
            )

            if not selected_strats:
                st.warning("Select at least one strategy.")
                st.stop()

            strategies = {k: v for k, v in all_strategies.items() if k in selected_strats}

            # ── Backtest each strategy ──
            st.subheader("Individual Strategy Results")

            results_all = {}
            from src.quant_features import avg_uniqueness, sequential_bootstrap_sharpe

            for strat_name, signal in strategies.items():
                # Align signal with returns
                common = gas_ret.index.intersection(signal.index)
                sig_aligned = signal.loc[common].shift(1).fillna(0)  # trade on next day's signal
                strat_ret = sig_aligned * gas_ret.loc[common]
                strat_ret = strat_ret.dropna()

                if len(strat_ret) < 30:
                    continue

                # Metrics
                ann_ret = strat_ret.mean() * 252 * 100
                ann_vol = strat_ret.std() * np.sqrt(252) * 100
                sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
                cum = (1 + strat_ret).cumprod()
                dd = ((cum / cum.cummax()) - 1).min() * 100
                win_rate = (strat_ret > 0).mean() * 100

                # Sequential bootstrap p-value
                uniq = avg_uniqueness(strat_ret, window=10)
                try:
                    std_sh, seq_sh = sequential_bootstrap_sharpe(strat_ret, uniq, n_bootstrap=500)
                    seq_p = np.mean([s >= sharpe for s in seq_sh]) if seq_sh else 1.0
                except Exception:
                    seq_p = 1.0

                # Deflated Sharpe Ratio (adjust for trying multiple strategies)
                n_strategies = len(strategies)
                sr_se = np.sqrt((1 + 0.5 * sharpe**2) / max(len(strat_ret) - 1, 1))
                from scipy.stats import norm
                expected_max_sr = sr_se * (norm.ppf(1 - 1 / (2 * n_strategies)) if n_strategies > 1 else 0)
                dsr = norm.cdf((sharpe - expected_max_sr) / sr_se) if sr_se > 0 else 0

                results_all[strat_name] = {
                    "returns": strat_ret, "cum": cum,
                    "ann_ret": ann_ret, "ann_vol": ann_vol, "sharpe": sharpe,
                    "dd": dd, "win_rate": win_rate, "seq_p": seq_p, "dsr": dsr,
                    "n_obs": len(strat_ret),
                }

            if results_all:
                # Equity curves
                fig_eq = go.Figure()
                strat_colors = ["#00d1ff", "#00ff88", "#ffaa00", "#ff6b6b", "#ff00ff"]
                for i, (name, res) in enumerate(results_all.items()):
                    fig_eq.add_trace(go.Scatter(
                        x=res["cum"].index, y=res["cum"] * 100, mode="lines",
                        name=name, line=dict(color=strat_colors[i % len(strat_colors)], width=2),
                    ))
                fig_eq.add_hline(y=100, line_dash="dash", line_color="#333")
                fig_eq.update_layout(template="plotly_dark", height=420,
                                      title="Strategy Equity Curves (base=100)",
                                      yaxis_title="Portfolio Value",
                                      legend=dict(orientation="h", y=-0.12),
                                      margin=dict(l=0, r=0, t=40, b=0))
                st.plotly_chart(fig_eq, use_container_width=True, config=PLOTLY_NOBAR)

                # Results table with de Prado metrics
                metrics_table = []
                for name, res in results_all.items():
                    sig_level = "Yes" if res["seq_p"] < 0.05 else "Marginal" if res["seq_p"] < 0.10 else "No"
                    dsr_level = "Pass" if res["dsr"] > 0.95 else "Marginal" if res["dsr"] > 0.50 else "Fail"
                    metrics_table.append({
                        "Strategy": name,
                        "Ann. Return": f"{res['ann_ret']:.1f}%",
                        "Ann. Vol": f"{res['ann_vol']:.1f}%",
                        "Sharpe": f"{res['sharpe']:.2f}",
                        "Max DD": f"{res['dd']:.1f}%",
                        "Win Rate": f"{res['win_rate']:.0f}%",
                        "Seq. Bootstrap p": f"{res['seq_p']:.3f}",
                        "Significant?": sig_level,
                        "Deflated SR": f"{res['dsr']:.3f}",
                        "DSR Pass?": dsr_level,
                    })
                st.dataframe(pd.DataFrame(metrics_table), use_container_width=True, hide_index=True)

                st.caption("**Seq. Bootstrap p**: probability of achieving this Sharpe by chance (lower = better, <0.05 = significant). "
                           "**Deflated SR**: probability the Sharpe is genuine after adjusting for trying multiple strategies (>0.95 = pass).")

                # Walk-forward for best strategy
                st.markdown("---")
                best_strat = max(results_all, key=lambda k: results_all[k]["sharpe"])
                st.subheader(f"Walk-Forward: {best_strat}")
                st.caption("Splits data into 4 folds. Each fold: train on past, test on unseen future. "
                           "Shows whether the strategy's edge persists out-of-sample.")

                best_ret = results_all[best_strat]["returns"]
                n_total = len(best_ret)
                fold_size = n_total // 4

                wf_results = []
                for fold in range(4):
                    train_end = (fold + 1) * fold_size
                    test_start = train_end
                    test_end = min(test_start + fold_size, n_total)
                    if test_start >= n_total:
                        break

                    train_ret = best_ret.iloc[:train_end]
                    test_ret = best_ret.iloc[test_start:test_end]

                    if len(test_ret) < 10:
                        continue

                    train_sharpe = train_ret.mean() / train_ret.std() * np.sqrt(252) if train_ret.std() > 0 else 0
                    test_sharpe = test_ret.mean() / test_ret.std() * np.sqrt(252) if test_ret.std() > 0 else 0
                    test_ann_ret = test_ret.mean() * 252 * 100

                    wf_results.append({
                        "Fold": fold + 1,
                        "Train Period": f"{train_ret.index[0].strftime('%Y-%m-%d')} to {train_ret.index[-1].strftime('%Y-%m-%d')}",
                        "Test Period": f"{test_ret.index[0].strftime('%Y-%m-%d')} to {test_ret.index[-1].strftime('%Y-%m-%d')}",
                        "Train Sharpe": f"{train_sharpe:.2f}",
                        "Test Sharpe": f"{test_sharpe:.2f}",
                        "Test Return": f"{test_ann_ret:.1f}%",
                        "Degradation": f"{(test_sharpe - train_sharpe):+.2f}",
                    })

                if wf_results:
                    st.dataframe(pd.DataFrame(wf_results), use_container_width=True, hide_index=True)
                    avg_degradation = np.mean([float(r["Degradation"]) for r in wf_results])
                    if avg_degradation > -0.3:
                        st.success(f"Avg Sharpe degradation: {avg_degradation:+.2f} — strategy holds up out-of-sample.")
                    elif avg_degradation > -0.7:
                        st.warning(f"Avg Sharpe degradation: {avg_degradation:+.2f} — moderate decay, use with caution.")
                    else:
                        st.error(f"Avg Sharpe degradation: {avg_degradation:+.2f} — significant overfit detected.")
    else:
        st.warning("Need historical gas price data (NG=F) for strategy backtesting.")

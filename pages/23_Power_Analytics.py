import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import requests
import os
import logging
from src.layout import setup_page, error_boundary, fun_loader

logger = logging.getLogger(__name__)

setup_page("23_Power_Analytics")

st.title("⚡ Power Analytics")
st.markdown("Duck curve visualization, implied heat rates, spark spread analysis, and generation stack merit order.")

# ── CONFIG ──
ERCOT_BASE = "https://www.ercot.com/api/1/services/read/dashboards"

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
@st.cache_data(ttl=300)
def fetch_ercot(endpoint: str):
    try:
        r = requests.get(f"{ERCOT_BASE}/{endpoint}.json", timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.error(f"ERCOT fetch failed for {endpoint}: {e}")
        return None


@st.cache_data(ttl=3600)
def fetch_henry_hub() -> float | None:
    """Fetch latest Henry Hub spot price from EIA API v2."""
    api_key = os.environ.get("EIA_API_KEY")
    if not api_key:
        try:
            api_key = st.secrets["EIA_API_KEY"]
        except Exception:
            return None
    try:
        url = f"https://api.eia.gov/v2/seriesid/NG.RNGWHHD.W?api_key={api_key}"
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        data = r.json()["response"]["data"]
        if data:
            df = pd.DataFrame(data).sort_values("period")
            return float(df["value"].iloc[-1])
    except Exception as e:
        logger.error(f"EIA Henry Hub fetch failed: {e}")
    return None


@st.cache_data(ttl=300)
def fetch_ercot_spp() -> float | None:
    """Fetch latest ERCOT Hub Average Settlement Point Price."""
    try:
        r = requests.get(f"{ERCOT_BASE}/real-time-spp.json", timeout=15)
        r.raise_for_status()
        data = r.json()
        records = data.get("data", [])
        if records:
            df = pd.DataFrame(records)
            # Look for HB_HUBAVG or system-wide average
            hub_rows = df[df["settlementPoint"].str.contains("HB_HUBAVG|HB_HOUSTON|HB_NORTH", case=False, na=False)]
            if not hub_rows.empty:
                return float(hub_rows["price"].iloc[-1])
            return float(df["price"].mean())
    except Exception as e:
        logger.error(f"ERCOT SPP fetch failed: {e}")
    return None


# ── FETCH ALL DATA ──
with fun_loader("data"):
    fuel_mix = fetch_ercot("fuel-mix")
    supply_demand = fetch_ercot("supply-demand")
    load_forecast = fetch_ercot("loadForecastVsActual")
    gas_price = fetch_henry_hub()
    power_price = fetch_ercot_spp()

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
df_fuel["total_gen"] = df_fuel[[c for c in df_fuel.columns if c != "timestamp"]].clip(lower=0).sum(axis=1)
df_fuel["wind"] = df_fuel.get("Wind", pd.Series(0, index=df_fuel.index)).clip(lower=0)
df_fuel["solar"] = df_fuel.get("Solar", pd.Series(0, index=df_fuel.index)).clip(lower=0)
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
renewable_curtailment = max(0, (wind_mw + solar_mw) - total_gen * 0.6)  # rough proxy

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Total Generation", f"{total_gen:,.0f} MW")
c2.metric("Net Load", f"{net_load:,.0f} MW")
c3.metric("Wind + Solar", f"{wind_mw + solar_mw:,.0f} MW",
          f"{(wind_mw + solar_mw) / total_gen * 100:.1f}% of mix" if total_gen > 0 else None)
if gas_price:
    c4.metric("Henry Hub ($/MMBtu)", f"${gas_price:.2f}")
if power_price:
    c5.metric("ERCOT Hub Avg ($/MWh)", f"${power_price:.2f}")

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
        mode="lines", name="Net Load (Duck)",
        line=dict(color="#ffaa00", width=3),
        fill="tozeroy", fillcolor="rgba(255, 170, 0, 0.15)",
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

    # Ramp rate analysis
    df_fuel["ramp_rate"] = df_fuel["net_load"].diff() / (df_fuel["timestamp"].diff().dt.total_seconds() / 3600)
    max_ramp = df_fuel["ramp_rate"].max()
    min_ramp = df_fuel["ramp_rate"].min()
    belly = df_fuel["net_load"].min()
    peak = df_fuel["net_load"].max()

    r1, r2, r3, r4 = st.columns(4)
    r1.metric("Duck Belly (Min Net Load)", f"{belly:,.0f} MW")
    r2.metric("Evening Peak (Max Net Load)", f"{peak:,.0f} MW")
    r3.metric("Max Upward Ramp", f"{max_ramp:,.0f} MW/hr" if pd.notna(max_ramp) else "N/A")
    r4.metric("Max Downward Ramp", f"{min_ramp:,.0f} MW/hr" if pd.notna(min_ramp) else "N/A")

    # Ramp rate chart
    fig_ramp = go.Figure()
    ramp_colors = ["#00ff96" if v >= 0 else "#ff4b4b" for v in df_fuel["ramp_rate"].fillna(0)]
    fig_ramp.add_trace(go.Bar(
        x=df_fuel["timestamp"], y=df_fuel["ramp_rate"],
        marker_color=ramp_colors, name="Ramp Rate",
    ))
    fig_ramp.add_hline(y=0, line_color="white", line_width=0.5)
    fig_ramp.update_layout(
        template="plotly_dark", height=280, margin=dict(t=10, b=0, l=0, r=0),
        yaxis_title="Ramp Rate (MW/hr)", hovermode="x unified",
    )
    st.caption("Net Load Ramp Rate — positive = ramping up (evening), negative = ramping down (morning)")
    st.plotly_chart(fig_ramp, use_container_width=True)


# ════════════════════════════════════════════════
# TAB 2: HEAT RATE
# ════════════════════════════════════════════════
with tab2:
    st.subheader("Implied Market Heat Rate")
    st.caption("Heat rate = power price / gas price x 1,000. Measures how efficiently the marginal generator converts fuel to electricity (BTU/kWh).")

    if gas_price and power_price:
        implied_hr = (power_price / gas_price) * 1000
        hr_efficiency = "Efficient" if implied_hr < 8000 else "Average" if implied_hr < 10000 else "Stressed"

        h1, h2, h3 = st.columns(3)
        h1.metric("Implied Heat Rate", f"{implied_hr:,.0f} BTU/kWh")
        h2.metric("Market Condition", hr_efficiency)
        h3.metric("Marginal Fuel Cost", f"${gas_price * implied_hr / 1000:.2f}/MWh")

        # Heat rate comparison chart
        fig_hr = go.Figure()

        # Reference heat rates
        hr_names = list(HEAT_RATES.keys())
        hr_values = list(HEAT_RATES.values())
        hr_colors = ["#00d1ff"] * len(hr_names)

        # Add implied as first bar
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
            template="plotly_dark", height=400, margin=dict(t=30, b=0, l=0, r=0),
            yaxis_title="Heat Rate (BTU/kWh)",
        )
        st.plotly_chart(fig_hr, use_container_width=True)

        # Heat rate interpretation
        with st.expander("Heat Rate Guide"):
            st.markdown(f"""
| Range | Interpretation |
|-------|---------------|
| < 7,000 BTU/kWh | Very efficient — new CCGT on the margin |
| 7,000 - 8,500 | Normal — efficient gas fleet clearing |
| 8,500 - 10,000 | Elevated — less efficient units dispatched |
| > 10,000 | Stressed — peakers / old steam units on margin |

**Current implied heat rate: {implied_hr:,.0f} BTU/kWh** — the market is pricing the marginal
generator at roughly **{hr_efficiency.lower()}** efficiency levels.
""")
    else:
        missing = []
        if not gas_price:
            missing.append("Henry Hub gas price (EIA API key required)")
        if not power_price:
            missing.append("ERCOT settlement point price")
        st.warning(f"Heat rate calculation requires: {', '.join(missing)}")

        # Still show reference heat rates
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
with tab3:
    st.subheader("Spark Spread Analysis")
    st.caption("Spark spread = power price - (gas price x heat rate / 1,000). Positive spread means gas plants are profitable.")

    if gas_price and power_price:
        # Calculate spark spreads for different plant types
        spreads = {}
        for plant, hr in HEAT_RATES.items():
            fuel_cost = gas_price * hr / 1000
            spread = power_price - fuel_cost
            spreads[plant] = {"heat_rate": hr, "fuel_cost": fuel_cost, "spread": spread}

        # Metrics for key plant types
        cc_spread = spreads["Combined Cycle"]["spread"]
        ct_spread = spreads["Combustion Turbine"]["spread"]

        s1, s2, s3, s4 = st.columns(4)
        s1.metric("Power Price", f"${power_price:.2f}/MWh")
        s2.metric("Gas Price", f"${gas_price:.2f}/MMBtu")
        s3.metric("CCGT Spark Spread", f"${cc_spread:.2f}/MWh",
                  delta_color="normal" if cc_spread > 0 else "inverse")
        s4.metric("CT Spark Spread", f"${ct_spread:.2f}/MWh",
                  delta_color="normal" if ct_spread > 0 else "inverse")

        # Spark spread waterfall
        fig_spark = go.Figure()

        plant_names = list(spreads.keys())
        spread_vals = [s["spread"] for s in spreads.values()]
        fuel_costs = [s["fuel_cost"] for s in spreads.values()]

        # Stacked bar: fuel cost + spread = power price
        fig_spark.add_trace(go.Bar(
            x=plant_names, y=fuel_costs,
            name="Fuel Cost ($/MWh)",
            marker_color="#ff9900",
            text=[f"${v:.2f}" for v in fuel_costs],
            textposition="inside",
        ))
        fig_spark.add_trace(go.Bar(
            x=plant_names, y=spread_vals,
            name="Spark Spread ($/MWh)",
            marker_color=["#00ff96" if v > 0 else "#ff4b4b" for v in spread_vals],
            text=[f"${v:+.2f}" for v in spread_vals],
            textposition="inside",
        ))

        fig_spark.add_hline(y=power_price, line_dash="dot", line_color="#00d1ff",
                            annotation_text=f"Power Price ${power_price:.2f}/MWh")

        fig_spark.update_layout(
            template="plotly_dark", height=450, margin=dict(t=30, b=0, l=0, r=0),
            barmode="stack", yaxis_title="$/MWh", hovermode="x unified",
            legend=dict(orientation="h", yanchor="bottom", y=1.02),
        )
        st.plotly_chart(fig_spark, use_container_width=True)

        # Detailed table
        df_spreads = pd.DataFrame([
            {
                "Plant Type": plant,
                "Heat Rate (BTU/kWh)": f"{d['heat_rate']:,}",
                "Fuel Cost ($/MWh)": f"${d['fuel_cost']:.2f}",
                "Spark Spread ($/MWh)": f"${d['spread']:+.2f}",
                "Profitable": "Yes" if d["spread"] > 0 else "No",
            }
            for plant, d in spreads.items()
        ])
        st.dataframe(df_spreads, use_container_width=True, hide_index=True)

        # Dark spread comparison (coal)
        coal_hr = HEAT_RATES["Coal"]
        # Approx coal price: ~$2.50/MMBtu equivalent
        coal_price_mmbtu = 2.50
        dark_spread = power_price - (coal_price_mmbtu * coal_hr / 1000)
        st.markdown(f"**Dark Spread (Coal):** ${dark_spread:+.2f}/MWh — using ~${coal_price_mmbtu:.2f}/MMBtu coal equivalent")
    else:
        missing = []
        if not gas_price:
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
            if val > 0:
                latest_fuel[ft] = val

    # Merit order: nuclear -> wind/solar -> hydro -> coal -> gas -> storage -> other
    MERIT_ORDER = {
        "Nuclear":          {"marginal_cost": 2,  "color": "#ad7fff"},
        "Wind":             {"marginal_cost": 0,  "color": "#00d1ff"},
        "Solar":            {"marginal_cost": 0,  "color": "#ffdd00"},
        "Hydro":            {"marginal_cost": 5,  "color": "#00ff96"},
        "Coal and Lignite": {"marginal_cost": 25, "color": "#888888"},
        "Natural Gas":      {"marginal_cost": 35, "color": "#ff9900"},
        "Power Storage":    {"marginal_cost": 50, "color": "#ff4b4b"},
        "Other":            {"marginal_cost": 45, "color": "#666666"},
    }

    # Update gas marginal cost if we have a live price
    if gas_price:
        MERIT_ORDER["Natural Gas"]["marginal_cost"] = gas_price * 6.8  # avg CCGT heat rate

    # Build stack data
    stack_data = []
    cumulative_mw = 0
    for fuel in ["Wind", "Solar", "Nuclear", "Hydro", "Coal and Lignite", "Natural Gas", "Other", "Power Storage"]:
        gen_mw = latest_fuel.get(fuel, 0)
        cap_mw = capacity_data.get(fuel, gen_mw)
        if cap_mw <= 0 and gen_mw <= 0:
            continue

        info = MERIT_ORDER.get(fuel, {"marginal_cost": 40, "color": "#666"})
        stack_data.append({
            "fuel": fuel,
            "gen_mw": gen_mw,
            "capacity_mw": cap_mw,
            "marginal_cost": info["marginal_cost"],
            "color": info["color"],
            "start_mw": cumulative_mw,
        })
        cumulative_mw += cap_mw

    # Stack chart — horizontal bars showing capacity, with generation overlay
    fig_stack = go.Figure()

    for item in stack_data:
        # Capacity bar (lighter)
        fig_stack.add_trace(go.Bar(
            y=[item["fuel"]], x=[item["capacity_mw"]],
            orientation="h", name=f"{item['fuel']} Capacity",
            marker_color=item["color"], marker_opacity=0.3,
            showlegend=False,
            text=f"{item['capacity_mw']:,.0f} MW cap",
            textposition="inside",
        ))
        # Current generation bar (solid)
        fig_stack.add_trace(go.Bar(
            y=[item["fuel"]], x=[item["gen_mw"]],
            orientation="h", name=f"{item['fuel']}",
            marker_color=item["color"],
            text=f"{item['gen_mw']:,.0f} MW",
            textposition="inside",
        ))

    fig_stack.update_layout(
        template="plotly_dark", height=450, margin=dict(t=10, b=0, l=0, r=0),
        barmode="overlay", xaxis_title="Megawatts (MW)",
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
        yaxis=dict(categoryorder="array",
                   categoryarray=list(reversed([s["fuel"] for s in stack_data]))),
    )
    st.plotly_chart(fig_stack, use_container_width=True)

    # Supply curve (merit order dispatch)
    st.subheader("Supply Curve — Dispatch Order")
    fig_supply = go.Figure()

    x_vals = []
    y_vals = []
    colors = []
    labels = []
    running_mw = 0

    for item in stack_data:
        # Each block: horizontal step from running_mw to running_mw + capacity
        x_vals.extend([running_mw, running_mw + item["capacity_mw"]])
        y_vals.extend([item["marginal_cost"], item["marginal_cost"]])
        colors.append(item["color"])
        labels.append(item["fuel"])
        running_mw += item["capacity_mw"]

    # Draw as filled steps
    for i, item in enumerate(stack_data):
        x0 = item["start_mw"]
        x1 = x0 + item["capacity_mw"]
        fig_supply.add_trace(go.Scatter(
            x=[x0, x1, x1, x0, x0],
            y=[0, 0, item["marginal_cost"], item["marginal_cost"], 0],
            fill="toself", fillcolor=item["color"],
            opacity=0.6, line=dict(color=item["color"], width=1),
            name=item["fuel"],
            hovertemplate=f"{item['fuel']}<br>Capacity: {item['capacity_mw']:,.0f} MW<br>Marginal Cost: ${item['marginal_cost']:.1f}/MWh<extra></extra>",
        ))

    # Demand line
    current_demand = df_sd["demand"].iloc[-1] if not df_sd.empty else total_gen
    fig_supply.add_vline(x=current_demand, line_dash="dash", line_color="#ff4b4b", line_width=2,
                         annotation_text=f"Demand: {current_demand:,.0f} MW")

    # Power price line
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

    # Utilization table
    df_stack = pd.DataFrame([
        {
            "Fuel": s["fuel"],
            "Capacity (MW)": f"{s['capacity_mw']:,.0f}",
            "Generation (MW)": f"{s['gen_mw']:,.0f}",
            "Utilization": f"{s['gen_mw'] / s['capacity_mw'] * 100:.1f}%" if s["capacity_mw"] > 0 else "N/A",
            "Est. Marginal Cost": f"${s['marginal_cost']:.1f}/MWh",
        }
        for s in stack_data
    ])
    st.dataframe(df_stack, use_container_width=True, hide_index=True)

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import logging
from src.layout import setup_page, error_boundary, fun_loader

logger = logging.getLogger(__name__)

setup_page("16_ERCOT_Power")

st.title("⚡ ERCOT Power Dashboard")
st.markdown("Live grid conditions, generation mix, load forecasts, and reserve data from the Electric Reliability Council of Texas.")

from src.ercot_api import fetch_dashboard as fetch_ercot


# --- FETCH ALL DATA (parallelized) ---
with fun_loader("data"):
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=4) as _ex:
        _f_fuel = _ex.submit(fetch_ercot, "fuel-mix")
        _f_sd = _ex.submit(fetch_ercot, "supply-demand")
        _f_lf = _ex.submit(fetch_ercot, "loadForecastVsActual")
        _f_anc = _ex.submit(fetch_ercot, "ancillary-services")
    fuel_mix = _f_fuel.result()
    supply_demand = _f_sd.result()
    load_forecast = _f_lf.result()
    ancillary = _f_anc.result()

if not fuel_mix or not supply_demand:
    st.error("Failed to connect to ERCOT. The dashboard API may be temporarily unavailable.")
    st.stop()

# --- PARSE SUPPLY/DEMAND ---
sd_data = supply_demand.get("data", [])
df_sd = pd.DataFrame(sd_data)
df_sd['timestamp'] = pd.to_datetime(df_sd['timestamp'])

# Current conditions
latest_sd = df_sd.iloc[-1] if not df_sd.empty else None
current_demand = latest_sd['demand'] if latest_sd is not None else 0
current_capacity = latest_sd['capacity'] if latest_sd is not None else 0
reserve_margin = ((current_capacity - current_demand) / current_demand * 100) if current_demand > 0 else 0

# --- PARSE FUEL MIX ---
# Get latest 5-minute snapshot
fuel_data = fuel_mix.get("data", {})
capacity_data = fuel_mix.get("monthlyCapacity", {})
fuel_types = fuel_mix.get("types", [])

# Flatten latest fuel mix data
latest_fuel = {}
if fuel_data:
    # Data is nested: {date: {timestamp: {fuel: {gen: value}}}}
    all_timestamps = []
    for day_key, day_data in fuel_data.items():
        for ts_key, ts_data in day_data.items():
            row = {"timestamp": ts_key}
            for fuel, vals in ts_data.items():
                if isinstance(vals, dict):
                    row[fuel] = vals.get("gen", 0)
            all_timestamps.append(row)

    df_fuel = pd.DataFrame(all_timestamps)
    df_fuel['timestamp'] = pd.to_datetime(df_fuel['timestamp'])
    df_fuel = df_fuel.sort_values('timestamp')

    if not df_fuel.empty:
        latest_row = df_fuel.iloc[-1]
        for ft in fuel_types:
            if ft in latest_row:
                latest_fuel[ft] = latest_row[ft]

total_gen = sum(v for v in latest_fuel.values() if v > 0)

# --- PARSE LOAD FORECAST ---
load_current = None
load_previous = None
if load_forecast:
    cd = load_forecast.get("currentDay", {}).get("data", [])
    pd_data = load_forecast.get("previousDay", {}).get("data", [])
    if cd:
        load_current = pd.DataFrame(cd)
        load_current['timestamp'] = pd.to_datetime(load_current['timestamp'])
    if pd_data:
        load_previous = pd.DataFrame(pd_data)
        load_previous['timestamp'] = pd.to_datetime(load_previous['timestamp'])

# --- PARSE ANCILLARY SERVICES ---
grid_freq = None
reserves_data = None
if ancillary:
    freq_data = ancillary.get("data", [])
    if freq_data:
        grid_freq = freq_data[-1].get("currentFrequency")

    reserves_data = {
        "Reg Up (Deployed)": ancillary.get("lastDeployedRegUp", 0),
        "Reg Up (Undeployed)": ancillary.get("lastUndeployedRegUp", 0),
        "Reg Down (Deployed)": ancillary.get("lastDeployedRegDown", 0),
        "Reg Down (Undeployed)": ancillary.get("lastUndeployedRegDown", 0),
        "RRS": ancillary.get("lastRrs", 0),
        "Non-Spin": ancillary.get("lastNsrs", 0),
        "ECRS": ancillary.get("lastEcrs", 0),
    }

# --- PARSE FORECAST (next day) ---
forecast_data = supply_demand.get("forecast", [])
df_forecast = pd.DataFrame(forecast_data) if forecast_data else pd.DataFrame()
if not df_forecast.empty:
    df_forecast['timestamp'] = pd.to_datetime(df_forecast['timestamp'])

# --- METRICS ROW ---
st.subheader(f"Grid Snapshot — {fuel_mix.get('lastUpdated', 'N/A')}")

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("System Demand", f"{current_demand:,.0f} MW")
c2.metric("Available Capacity", f"{current_capacity:,.0f} MW")
c3.metric("Reserve Margin", f"{reserve_margin:.1f}%",
          delta_color="normal" if reserve_margin > 10 else "inverse")
c4.metric("Total Generation", f"{total_gen:,.0f} MW")
if grid_freq:
    freq_delta = grid_freq - 60.0
    c5.metric("Grid Frequency", f"{grid_freq:.3f} Hz", f"{freq_delta:+.3f} Hz")

# Secondary metrics — renewable share
wind_gen = latest_fuel.get("Wind", 0)
solar_gen = latest_fuel.get("Solar", 0)
gas_gen = latest_fuel.get("Natural Gas", 0)
renewable_pct = ((wind_gen + solar_gen) / total_gen * 100) if total_gen > 0 else 0

m1, m2, m3, m4 = st.columns(4)
m1.metric("Wind Generation", f"{wind_gen:,.0f} MW",
          f"{wind_gen/total_gen*100:.1f}% of mix" if total_gen > 0 else None)
m2.metric("Solar Generation", f"{max(0, solar_gen):,.0f} MW",
          f"{max(0,solar_gen)/total_gen*100:.1f}% of mix" if total_gen > 0 else None)
m3.metric("Natural Gas", f"{gas_gen:,.0f} MW",
          f"{gas_gen/total_gen*100:.1f}% of mix" if total_gen > 0 else None)
m4.metric("Renewable Share", f"{renewable_pct:.1f}%")

st.divider()

# --- CHARTING TABS ---
tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "Generation Mix (Live)",
    "Supply vs. Demand",
    "Load Forecast vs. Actual",
    "Reserve & Capacity Forecast",
    "Ancillary Services",
    "Grid Frequency"
])

# ---- TAB 1: Fuel Mix Over Time ----
with tab1:
    if 'df_fuel' in dir() and df_fuel is not None and not df_fuel.empty:
        fuel_colors = {
            "Natural Gas": "#ff9900",
            "Wind": "#00d1ff",
            "Solar": "#ffdd00",
            "Nuclear": "#ad7fff",
            "Coal and Lignite": "#888888",
            "Hydro": "#00ff96",
            "Power Storage": "#ff4b4b",
            "Other": "#666666",
        }

        fig_fuel = go.Figure()
        # Stack order: baseload first, then variable
        stack_order = ["Nuclear", "Coal and Lignite", "Natural Gas", "Hydro", "Other", "Wind", "Solar", "Power Storage"]
        for fuel in stack_order:
            if fuel in df_fuel.columns:
                fig_fuel.add_trace(go.Scatter(
                    x=df_fuel['timestamp'], y=df_fuel[fuel].clip(lower=0),
                    mode='lines', name=fuel, stackgroup='gen',
                    line=dict(width=0.5, color=fuel_colors.get(fuel, 'white')),
                    fillcolor=fuel_colors.get(fuel, 'white'),
                ))

        fig_fuel.update_layout(
            template="plotly_dark", height=500, margin=dict(t=10, b=0, l=0, r=0),
            yaxis_title="Generation (MW)", hovermode="x unified",
        )
        st.plotly_chart(fig_fuel, use_container_width=True)

        # Current mix pie chart
        st.subheader("Current Generation Mix")
        pie_data = {k: v for k, v in latest_fuel.items() if v > 0}
        fig_pie = go.Figure(data=[go.Pie(
            labels=list(pie_data.keys()),
            values=list(pie_data.values()),
            marker_colors=[fuel_colors.get(k, 'white') for k in pie_data.keys()],
            textinfo='label+percent', hole=0.4,
        )])
        fig_pie.update_layout(template="plotly_dark", height=400, margin=dict(t=10, b=0, l=0, r=0))
        st.plotly_chart(fig_pie, use_container_width=True)

        # Installed capacity table
        with st.expander("Installed Capacity by Fuel Type"):
            cap_df = pd.DataFrame([
                {"Fuel Type": k, "Capacity (MW)": f"{v:,.0f}",
                 "Current Gen (MW)": f"{latest_fuel.get(k, 0):,.0f}",
                 "Utilization": f"{latest_fuel.get(k, 0)/v*100:.1f}%" if v > 0 else "N/A"}
                for k, v in capacity_data.items()
            ])
            st.dataframe(cap_df, use_container_width=True, hide_index=True)
    else:
        st.warning("Fuel mix data unavailable.")

# ---- TAB 2: Supply vs. Demand ----
with tab2:
    if not df_sd.empty:
        fig_sd = go.Figure()

        fig_sd.add_trace(go.Scatter(
            x=df_sd['timestamp'], y=df_sd['capacity'],
            mode='lines', name='Available Capacity',
            line=dict(color='#00ff96', width=2)
        ))
        fig_sd.add_trace(go.Scatter(
            x=df_sd['timestamp'], y=df_sd['demand'],
            mode='lines', name='System Demand',
            line=dict(color='#ff4b4b', width=2), fill='tozeroy', fillcolor='rgba(255, 75, 75, 0.1)'
        ))

        # Reserve margin shading
        fig_sd.add_trace(go.Scatter(
            x=pd.concat([df_sd['timestamp'], df_sd['timestamp'][::-1]]),
            y=pd.concat([df_sd['capacity'], df_sd['demand'][::-1]]),
            fill='toself', fillcolor='rgba(0, 255, 150, 0.1)',
            line=dict(color='rgba(255,255,255,0)'), showlegend=True, name='Reserve Margin', hoverinfo='skip'
        ))

        fig_sd.update_layout(
            template="plotly_dark", height=500, margin=dict(t=10, b=0, l=0, r=0),
            yaxis_title="Megawatts (MW)", hovermode="x unified"
        )
        st.plotly_chart(fig_sd, use_container_width=True)

        # Reserve margin over time
        df_sd['reserve_pct'] = (df_sd['capacity'] - df_sd['demand']) / df_sd['demand'] * 100
        fig_res = go.Figure()
        colors_res = ['#00ff96' if v > 10 else '#ffaa00' if v > 5 else '#ff4b4b' for v in df_sd['reserve_pct']]
        fig_res.add_trace(go.Bar(
            x=df_sd['timestamp'], y=df_sd['reserve_pct'],
            marker_color=colors_res
        ))
        fig_res.add_hline(y=10, line_dash="dot", line_color="#ffaa00", annotation_text="10% Threshold")
        fig_res.update_layout(
            template="plotly_dark", height=300, margin=dict(t=10, b=0, l=0, r=0),
            yaxis_title="Reserve Margin (%)", hovermode="x unified"
        )
        st.plotly_chart(fig_res, use_container_width=True)
    else:
        st.warning("Supply/demand data unavailable.")

# ---- TAB 3: Load Forecast vs. Actual ----
with tab3:
    if load_current is not None and not load_current.empty:
        fig_load = go.Figure()

        # Previous day actual
        if load_previous is not None and not load_previous.empty:
            fig_load.add_trace(go.Scatter(
                x=load_previous['hourEnding'], y=load_previous['systemLoad'],
                mode='lines', name='Previous Day Actual',
                line=dict(color='#888888', width=1.5, dash='dot')
            ))

        # Current day
        if 'systemLoad' in load_current.columns:
            actual = load_current[load_current['systemLoad'] > 0]
            if not actual.empty:
                fig_load.add_trace(go.Scatter(
                    x=actual['hourEnding'], y=actual['systemLoad'],
                    mode='lines+markers', name='Actual Load',
                    line=dict(color='#ff4b4b', width=2.5)
                ))

        fig_load.add_trace(go.Scatter(
            x=load_current['hourEnding'], y=load_current['currentLoadForecast'],
            mode='lines', name='Current Forecast',
            line=dict(color='#00d1ff', width=2, dash='dash')
        ))

        fig_load.add_trace(go.Scatter(
            x=load_current['hourEnding'], y=load_current['dayAheadForecast'],
            mode='lines', name='Day-Ahead Forecast',
            line=dict(color='#ffaa00', width=1.5, dash='dash')
        ))

        # HSL (High Sustained Limit) — only available for completed hours
        if 'currentDayHsl' in load_current.columns:
            fig_load.add_trace(go.Scatter(
                x=load_current['hourEnding'], y=load_current['currentDayHsl'],
                mode='lines', name='Capacity (HSL)',
                line=dict(color='#00ff96', width=1, dash='dot')
            ))
        elif 'dayAheadHsl' in load_current.columns:
            fig_load.add_trace(go.Scatter(
                x=load_current['hourEnding'], y=load_current['dayAheadHsl'],
                mode='lines', name='DA Capacity (HSL)',
                line=dict(color='#00ff96', width=1, dash='dot')
            ))

        fig_load.update_layout(
            template="plotly_dark", height=500, margin=dict(t=10, b=0, l=0, r=0),
            xaxis_title="Hour Ending", yaxis_title="Megawatts (MW)", hovermode="x unified"
        )
        st.plotly_chart(fig_load, use_container_width=True)

        # Forecast error
        if 'systemLoad' in load_current.columns:
            actual_hrs = load_current[load_current['systemLoad'] > 0].copy()
            if not actual_hrs.empty:
                actual_hrs['forecast_error'] = actual_hrs['systemLoad'] - actual_hrs['currentLoadForecast']
                fig_err = go.Figure()
                err_colors = ['#00ff96' if abs(v) < 500 else '#ffaa00' if abs(v) < 1000 else '#ff4b4b'
                              for v in actual_hrs['forecast_error']]
                fig_err.add_trace(go.Bar(
                    x=actual_hrs['hourEnding'], y=actual_hrs['forecast_error'],
                    marker_color=err_colors
                ))
                fig_err.add_hline(y=0, line_color="white", line_width=1)
                fig_err.update_layout(
                    template="plotly_dark", height=250, margin=dict(t=10, b=0, l=0, r=0),
                    xaxis_title="Hour Ending", yaxis_title="Forecast Error (MW)", hovermode="x unified"
                )
                st.caption("Forecast Error: Actual - Forecast (positive = under-forecast)")
                st.plotly_chart(fig_err, use_container_width=True)
    else:
        st.warning("Load forecast data unavailable.")

# ---- TAB 4: Reserve & Capacity Forecast (Next Day) ----
with tab4:
    if not df_forecast.empty:
        fig_fc = go.Figure()

        fig_fc.add_trace(go.Scatter(
            x=df_forecast['hourEnding'], y=df_forecast['availCapGen'],
            mode='lines+markers', name='Available Capacity',
            line=dict(color='#00ff96', width=2)
        ))
        fig_fc.add_trace(go.Scatter(
            x=df_forecast['hourEnding'], y=df_forecast['forecastedDemand'],
            mode='lines+markers', name='Forecasted Demand',
            line=dict(color='#ff4b4b', width=2)
        ))

        # Reserve band
        fig_fc.add_trace(go.Scatter(
            x=pd.concat([df_forecast['hourEnding'], df_forecast['hourEnding'][::-1]]),
            y=pd.concat([df_forecast['availCapGen'], df_forecast['forecastedDemand'][::-1]]),
            fill='toself', fillcolor='rgba(0, 255, 150, 0.1)',
            line=dict(color='rgba(255,255,255,0)'), showlegend=True, name='Reserve Band', hoverinfo='skip'
        ))

        delivery_date = df_forecast['deliveryDate'].iloc[0] if 'deliveryDate' in df_forecast.columns else "Next Day"
        fig_fc.update_layout(
            template="plotly_dark", height=500, margin=dict(t=10, b=0, l=0, r=0),
            xaxis_title="Hour Ending", yaxis_title="Megawatts (MW)", hovermode="x unified",
            title=dict(text=f"Forecast: {delivery_date}", font=dict(size=14))
        )
        st.plotly_chart(fig_fc, use_container_width=True)

        # Min reserve margin for next day
        df_forecast['reserve_mw'] = df_forecast['availCapGen'] - df_forecast['forecastedDemand']
        df_forecast['reserve_pct'] = df_forecast['reserve_mw'] / df_forecast['forecastedDemand'] * 100
        min_reserve = df_forecast['reserve_pct'].min()
        min_reserve_hr = df_forecast.loc[df_forecast['reserve_pct'].idxmin(), 'hourEnding']
        peak_demand = df_forecast['forecastedDemand'].max()
        peak_hr = df_forecast.loc[df_forecast['forecastedDemand'].idxmax(), 'hourEnding']

        fc1, fc2, fc3 = st.columns(3)
        fc1.metric("Peak Forecasted Demand", f"{peak_demand:,.0f} MW", f"Hour {peak_hr}")
        fc2.metric("Minimum Reserve Margin", f"{min_reserve:.1f}%", f"Hour {min_reserve_hr}",
                    delta_color="normal" if min_reserve > 10 else "inverse")
        fc3.metric("Available at Peak", f"{df_forecast.loc[df_forecast['forecastedDemand'].idxmax(), 'availCapGen']:,.0f} MW")
    else:
        st.warning("Forecast data unavailable.")

# ---- TAB 5: Ancillary Services ----
with tab5:
    if reserves_data:
        st.subheader("Current Ancillary Service Reserves")

        # Reserves bar chart
        fig_anc = go.Figure()
        reserve_names = list(reserves_data.keys())
        reserve_vals = list(reserves_data.values())
        reserve_colors = ['#00d1ff', '#0090b0', '#00ff96', '#009060',
                          '#ffaa00', '#ad7fff', '#ff4b4b']

        fig_anc.add_trace(go.Bar(
            x=reserve_names, y=reserve_vals,
            marker_color=reserve_colors[:len(reserve_names)],
            text=[f"{v:,.0f} MW" for v in reserve_vals],
            textposition='outside'
        ))
        fig_anc.update_layout(
            template="plotly_dark", height=400, margin=dict(t=30, b=0, l=0, r=0),
            yaxis_title="Megawatts (MW)"
        )
        st.plotly_chart(fig_anc, use_container_width=True)

        # ASCAP monitoring timeline
        ascap = ancillary.get("ascapmon", [])
        if ascap:
            df_ascap = pd.DataFrame(ascap)
            df_ascap['timestamp'] = pd.to_datetime(df_ascap['timestamp'])

            fig_ascap = go.Figure()
            ascap_series = {
                'deployedRegUp': ('Reg Up Deployed', '#00d1ff'),
                'undeployedRegUp': ('Reg Up Undeployed', '#0090b0'),
                'deployedRegDown': ('Reg Down Deployed', '#00ff96'),
                'undeployedRegDown': ('Reg Down Undeployed', '#009060'),
                'rrs': ('RRS', '#ffaa00'),
                'nsrs': ('Non-Spin', '#ad7fff'),
                'ecrs': ('ECRS', '#ff4b4b'),
            }

            for col, (label, color) in ascap_series.items():
                if col in df_ascap.columns:
                    fig_ascap.add_trace(go.Scatter(
                        x=df_ascap['timestamp'], y=df_ascap[col],
                        mode='lines', name=label, line=dict(color=color, width=1.5)
                    ))

            fig_ascap.update_layout(
                template="plotly_dark", height=400, margin=dict(t=10, b=0, l=0, r=0),
                yaxis_title="Megawatts (MW)", hovermode="x unified"
            )
            st.plotly_chart(fig_ascap, use_container_width=True)
    else:
        st.warning("Ancillary services data unavailable.")

# ---- TAB 6: Grid Frequency ----
with tab6:
    if ancillary:
        freq_data = ancillary.get("data", [])
        if freq_data:
            df_freq = pd.DataFrame(freq_data)
            df_freq['timestamp'] = pd.to_datetime(df_freq['timestamp'])

            fig_freq = go.Figure()
            fig_freq.add_trace(go.Scatter(
                x=df_freq['timestamp'], y=df_freq['currentFrequency'],
                mode='lines', name='Grid Frequency',
                line=dict(color='#00d1ff', width=1.5)
            ))

            # Reference lines
            fig_freq.add_hline(y=60.0, line_dash="solid", line_color="#00ff96", line_width=1,
                               annotation_text="60 Hz Nominal")
            fig_freq.add_hline(y=59.95, line_dash="dot", line_color="#ffaa00",
                               annotation_text="Low Threshold")
            fig_freq.add_hline(y=60.05, line_dash="dot", line_color="#ffaa00",
                               annotation_text="High Threshold")

            fig_freq.update_layout(
                template="plotly_dark", height=400, margin=dict(t=10, b=0, l=0, r=0),
                yaxis_title="Frequency (Hz)", hovermode="x unified",
                yaxis=dict(range=[59.9, 60.1])
            )
            st.plotly_chart(fig_freq, use_container_width=True)

            # Stats
            f1, f2, f3 = st.columns(3)
            f1.metric("Current", f"{df_freq['currentFrequency'].iloc[-1]:.3f} Hz")
            f2.metric("Min (Session)", f"{df_freq['currentFrequency'].min():.3f} Hz")
            f3.metric("Max (Session)", f"{df_freq['currentFrequency'].max():.3f} Hz")
        else:
            st.warning("Frequency data unavailable.")
    else:
        st.warning("Ancillary services data unavailable.")

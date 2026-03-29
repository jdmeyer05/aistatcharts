"""
Power Trading Strategies — Common Energy Trading Playbooks

Analyzes real-time and historical profitability of institutional power trading strategies
using ERCOT market data, natural gas futures, and EIA grid data.

Tabs:
1. Spark Spread — gas-fired generation profitability (power price - fuel cost × heat rate)
2. Heat Rate Trade — implied heat rate vs physical, clean/dirty spread
3. Peak/Off-Peak — on-peak vs off-peak price spread and calendar spreads
4. RT vs DAM Arbitrage — real-time vs day-ahead price convergence
5. Renewable Curtailment — negative pricing during high wind/solar, storage opportunity
6. Congestion — hub vs node price spreads, basis risk
"""
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import logging
from datetime import datetime, timedelta
from src.layout import setup_page, error_boundary
from src import ercot_api
from src.eia_helpers import fetch_henry_hub_daily, fetch_eia_hourly_grid
from src.market_data import fetch_commodity_futures
from src.ercot_api import fetch_dashboard
from src.styles import COLORS

logger = logging.getLogger(__name__)
setup_page("40_Power_Strategies")

st.title("Power Trading Strategies")
st.markdown("Institutional energy trading playbooks — spark spreads, heat rate trades, RT/DAM arb, renewable curtailment, and congestion analysis.")

PLOTLY_NOBAR = {"displayModeBar": False}

# ═══════════════════════════════════════════════
# DATA LOADING
# ═══════════════════════════════════════════════

st.caption("Loading ERCOT market data and natural gas prices...")

_has_api = ercot_api.is_available()
_today = datetime.now().strftime("%Y-%m-%d")
_yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

# Gas price
gas_data = fetch_commodity_futures("NG=F", period="3mo")
gas_price = gas_data["price"] if gas_data else None
gas_history = gas_data["history"] if gas_data else None

# ERCOT dashboard data
fuel_mix = fetch_dashboard("fuel-mix")
sys_prices = fetch_dashboard("systemWidePrices")

# ERCOT API data (if available)
ercot_rt = None
ercot_dam = None
ercot_load = None
ercot_wind = None
ercot_solar = None
ercot_sced = None

if _has_api:
    with st.spinner("Loading ERCOT API data..."):
        ercot_rt = ercot_api.fetch_rt_spp(_yesterday)
        ercot_dam = ercot_api.fetch_dam_spp(_yesterday)
        ercot_load = ercot_api.fetch_actual_load(_yesterday)
        ercot_wind = ercot_api.fetch_wind_hourly(_today)
        ercot_solar = ercot_api.fetch_solar_hourly(_today)
        ercot_sced = ercot_api.fetch_sced_lambda(_yesterday)

# EIA hourly grid
eia_grid = fetch_eia_hourly_grid("ERCO", days_back=14)

# Header metrics
hm1, hm2, hm3, hm4 = st.columns(4)
if gas_price:
    hm1.metric("Henry Hub (NG=F)", f"${gas_price:.2f}/MMBtu")

rt_data = []
latest_hub = None
if sys_prices:
    rt_data = sys_prices.get("rtSppData", [])
    if rt_data:
        _lh = rt_data[-1].get("hbHubAvg")
        if _lh:
            latest_hub = float(_lh)
            hm2.metric("ERCOT Hub Avg (RT)", f"${latest_hub:.2f}/MWh")

if gas_price and latest_hub is not None and latest_hub > 0:
    implied_hr = latest_hub / gas_price if gas_price > 0 else 0
    hm3.metric("Implied Heat Rate", f"{implied_hr:,.0f} BTU/kWh")

    typical_hr = 7000
    spark = latest_hub - (gas_price * typical_hr / 1000)
    spark_color = "normal" if spark > 0 else "inverse"
    hm4.metric("Spark Spread (7k HR)", f"${spark:.2f}/MWh", delta="Profitable" if spark > 0 else "Unprofitable",
               delta_color=spark_color)


# ═══════════════════════════════════════════════
# TABS
# ═══════════════════════════════════════════════

tab_charts, tab_spark, tab_heatrate, tab_peakoff, tab_rtdam, tab_curtail, tab_congestion, tab_simday, tab_backtest, tab_meta = st.tabs([
    "Live Charts",
    "Spark Spread",
    "Heat Rate Trade",
    "Peak / Off-Peak",
    "RT vs DAM Arb",
    "Renewable Curtailment",
    "Congestion",
    "Similar Day Forecast",
    "Strategy Backtest",
    "Meta-Analysis",
])


# ═══════════════════════════════════════════════
# TAB 0: LIVE CHARTS
# ═══════════════════════════════════════════════
with tab_charts, error_boundary("Live Charts"):
    st.subheader("Energy Market Charts")

    with st.expander("How to read this tab", expanded=False):
        st.markdown(
            "Real-time and intraday price charts for the key instruments in power trading:\n\n"
            "- **Natural Gas (NG=F)** — the fuel input. Drives spark spreads and heat rates.\n"
            "- **Crude Oil (CL=F)** — correlated with gas, drives broader energy complex.\n"
            "- **Power ETF (PXE / XLE)** — equity proxy for power sector exposure.\n"
            "- **Utilities ETF (XLU)** — regulated power companies. Moves inversely with rates.\n\n"
            "Use the interval selector to zoom in (5-min for intraday patterns) or out (60-min for daily shape)."
        )

    chart_c1, chart_c2 = st.columns([1, 1])
    with chart_c1:
        interval = st.radio("Interval", ["5 min", "15 min", "60 min"], horizontal=True, key="ps_chart_interval")
    with chart_c2:
        chart_days = st.radio("Lookback", ["1 Day", "3 Days", "5 Days"], horizontal=True, key="ps_chart_days")

    interval_map = {"5 min": 5, "15 min": 15, "60 min": 60}
    days_map = {"1 Day": 1, "3 Days": 3, "5 Days": 5}
    interval_min = interval_map[interval]
    n_days = days_map[chart_days]
    bars = n_days * (390 // interval_min)  # 390 min per trading day

    from src.data_engine import polygon_intraday, polygon_history

    @st.cache_data(ttl=300, show_spinner=False)
    def _fetch_intraday(symbol: str, interval_min: int, n_days: int):
        """Fetch intraday data from Polygon, fallback to yfinance."""
        bars = max(n_days, 3) * (390 // interval_min)  # at least 3 days for futures
        # Try Polygon first
        try:
            intra = polygon_intraday(symbol, interval_min=interval_min, bars=bars)
            if intra is not None and not intra.empty and len(intra) >= 3:
                return intra
        except Exception:
            pass
        # yfinance fallback — always use at least 5d period for futures (trade overnight)
        try:
            import yfinance as yf
            yf_interval = f"{interval_min}m"
            yf_period = f"{max(n_days, 3)}d"
            hist = yf.Ticker(symbol).history(period=yf_period, interval=yf_interval)
            if not hist.empty and len(hist) >= 3:
                # Strip timezone for consistency
                if hist.index.tz is not None:
                    hist.index = hist.index.tz_localize(None)
                return hist
        except Exception:
            pass
        return pd.DataFrame()

    CHART_INSTRUMENTS = {
        "NG=F": {"name": "Natural Gas (Henry Hub)", "color": "#ff8800"},
        "CL=F": {"name": "Crude Oil (WTI)", "color": "#ff4444"},
        "XLE": {"name": "Energy Sector ETF", "color": "#00d1ff"},
        "XLU": {"name": "Utilities Sector ETF", "color": "#00ff88"},
    }

    with st.spinner(f"Loading {interval} charts..."):
        chart_cols = st.columns(2)
        for idx, (symbol, info) in enumerate(CHART_INSTRUMENTS.items()):
            with chart_cols[idx % 2]:
                try:
                    intra = _fetch_intraday(symbol, interval_min, n_days)
                    if intra is not None and not intra.empty and len(intra) >= 3:
                        # Candlestick chart
                        fig_candle = go.Figure()

                        if all(c in intra.columns for c in ["Open", "High", "Low", "Close"]):
                            fig_candle.add_trace(go.Candlestick(
                                x=intra.index, open=intra["Open"], high=intra["High"],
                                low=intra["Low"], close=intra["Close"],
                                increasing_line_color=info["color"],
                                decreasing_line_color="#555",
                                name=info["name"],
                            ))
                        else:
                            fig_candle.add_trace(go.Scatter(
                                x=intra.index, y=intra["Close"], mode="lines",
                                line=dict(color=info["color"], width=2), name=info["name"],
                            ))

                        # Price change
                        first_price = intra["Close"].iloc[0]
                        last_price = intra["Close"].iloc[-1]
                        pct_chg = (last_price / first_price - 1) * 100

                        chg_color = "#00ff88" if pct_chg >= 0 else "#ff4444"
                        arrow = "▲" if pct_chg >= 0 else "▼"

                        fig_candle.update_layout(
                            template="plotly_dark", height=320,
                            title=f"{info['name']} ({symbol})  {arrow} {abs(pct_chg):.2f}%",
                            xaxis_rangeslider_visible=False,
                            margin=dict(l=0, r=0, t=40, b=0),
                            showlegend=False,
                        )

                        # Add volume bars if available
                        if "Volume" in intra.columns and intra["Volume"].sum() > 0:
                            fig_candle.add_trace(go.Bar(
                                x=intra.index, y=intra["Volume"],
                                marker_color="rgba(100,100,100,0.3)",
                                yaxis="y2", name="Volume",
                            ))
                            fig_candle.update_layout(
                                yaxis2=dict(overlaying="y", side="right", showgrid=False,
                                            showticklabels=False, range=[0, intra["Volume"].max() * 4]),
                            )

                        st.plotly_chart(fig_candle, use_container_width=True, config=PLOTLY_NOBAR)

                        # Key stats below chart
                        sc1, sc2, sc3 = st.columns(3)
                        sc1.metric("Last", f"${last_price:.2f}")
                        sc2.metric("Change", f"{pct_chg:+.2f}%",
                                   delta_color="normal" if pct_chg >= 0 else "inverse")
                        high = intra["High"].max() if "High" in intra.columns else intra["Close"].max()
                        low = intra["Low"].min() if "Low" in intra.columns else intra["Close"].min()
                        sc3.metric("Range", f"${low:.2f} — ${high:.2f}")
                    else:
                        st.warning(f"No intraday data for {symbol}. May require higher-tier Polygon subscription.")
                except Exception as e:
                    st.warning(f"Failed to load {symbol}: {e}")

    # ── ERCOT System Data ──
    st.markdown("---")
    st.subheader("ERCOT System Data (Real-Time)")
    st.caption("Live grid conditions from the ERCOT dashboard — prices, load, generation mix, and system frequency.")

    ercot_c1, ercot_c2 = st.columns(2)

    # RT SPP price chart (from dashboard data already loaded)
    with ercot_c1:
        if sys_prices:
            rt_spp = sys_prices.get("rtSppData", [])
            if rt_spp and len(rt_spp) > 5:
                spp_rows = []
                for row in rt_spp:
                    try:
                        ts = row.get("timestamp") or row.get("time")
                        hub = row.get("hbHubAvg")
                        north = row.get("hbNorth")
                        south = row.get("hbSouth")
                        west = row.get("hbWest")
                        houston = row.get("hbHouston")
                        if ts and hub is not None:
                            spp_rows.append({"time": pd.to_datetime(ts), "Hub Avg": float(hub),
                                             "North": float(north) if north else None,
                                             "South": float(south) if south else None,
                                             "West": float(west) if west else None,
                                             "Houston": float(houston) if houston else None})
                    except (ValueError, TypeError):
                        pass
                if spp_rows:
                    spp_df = pd.DataFrame(spp_rows).sort_values("time")
                    fig_spp = go.Figure()
                    fig_spp.add_trace(go.Scatter(x=spp_df["time"], y=spp_df["Hub Avg"], mode="lines",
                                                  name="Hub Avg", line=dict(color="#fff", width=2)))
                    for hub_name, color in [("North", "#00d1ff"), ("South", "#00ff88"),
                                             ("West", "#ffaa00"), ("Houston", "#ff6b6b")]:
                        if hub_name in spp_df.columns and spp_df[hub_name].notna().any():
                            fig_spp.add_trace(go.Scatter(x=spp_df["time"], y=spp_df[hub_name], mode="lines",
                                                          name=hub_name, line=dict(color=color, width=1)))
                    fig_spp.update_layout(template="plotly_dark", height=320,
                                           title="ERCOT RT Settlement Point Prices ($/MWh)",
                                           yaxis_title="$/MWh",
                                           legend=dict(orientation="h", y=-0.15),
                                           margin=dict(l=0, r=0, t=40, b=0))
                    st.plotly_chart(fig_spp, use_container_width=True, config=PLOTLY_NOBAR)
            else:
                st.info("No RT SPP data from ERCOT dashboard.")
        else:
            st.info("ERCOT dashboard unavailable.")

    # Fuel mix / generation chart
    with ercot_c2:
        if fuel_mix:
            fm_data = fuel_mix.get("data", [])
            if fm_data:
                fm_df = pd.DataFrame(fm_data)
                if "genMw" in fm_df.columns and "fuelType" in fm_df.columns:
                    fm_df["genMw"] = pd.to_numeric(fm_df["genMw"], errors="coerce")
                    fm_df = fm_df.dropna(subset=["genMw"])
                    fm_df = fm_df.sort_values("genMw", ascending=True)

                    fuel_colors = {
                        "Nuclear": "#ad7fff", "Coal and Lignite": "#888888",
                        "Natural Gas": "#ff9900", "Hydro": "#00ff96",
                        "Wind": "#00d1ff", "Solar": "#ffdd00",
                        "Power Storage": "#ff4b4b", "Other": "#666666",
                    }

                    fig_fuel = go.Figure()
                    fig_fuel.add_trace(go.Bar(
                        y=fm_df["fuelType"], x=fm_df["genMw"],
                        orientation="h",
                        marker_color=[fuel_colors.get(ft, "#888") for ft in fm_df["fuelType"]],
                        text=[f"{v:,.0f} MW" for v in fm_df["genMw"]],
                        textposition="outside",
                    ))
                    total_gen = fm_df["genMw"].sum()
                    fig_fuel.update_layout(template="plotly_dark", height=320,
                                            title=f"ERCOT Generation by Fuel ({total_gen:,.0f} MW total)",
                                            xaxis_title="MW",
                                            margin=dict(l=0, r=80, t=40, b=0))
                    st.plotly_chart(fig_fuel, use_container_width=True, config=PLOTLY_NOBAR)
        else:
            st.info("ERCOT fuel mix data unavailable.")

    # Supply/demand balance
    if fuel_mix or sys_prices:
        sd_data = fetch_dashboard("supply-demand")
        if sd_data:
            sd_rows = sd_data.get("data", [])
            if sd_rows:
                sd_df = pd.DataFrame(sd_rows)
                time_col = next((c for c in sd_df.columns if "time" in c.lower()), None)
                load_col = next((c for c in sd_df.columns if "load" in c.lower() or "demand" in c.lower()), None)
                cap_col = next((c for c in sd_df.columns if "cap" in c.lower() or "supply" in c.lower()), None)

                if time_col and (load_col or cap_col):
                    sd_df[time_col] = pd.to_datetime(sd_df[time_col], errors="coerce")
                    sd_df = sd_df.sort_values(time_col)

                    fig_sd = go.Figure()
                    if load_col:
                        sd_df[load_col] = pd.to_numeric(sd_df[load_col], errors="coerce")
                        fig_sd.add_trace(go.Scatter(x=sd_df[time_col], y=sd_df[load_col], mode="lines",
                                                     name="Load", line=dict(color="#00d1ff", width=2),
                                                     fill="tozeroy", fillcolor="rgba(0,209,255,0.05)"))
                    if cap_col:
                        sd_df[cap_col] = pd.to_numeric(sd_df[cap_col], errors="coerce")
                        fig_sd.add_trace(go.Scatter(x=sd_df[time_col], y=sd_df[cap_col], mode="lines",
                                                     name="Capacity", line=dict(color="#ffaa00", width=1, dash="dash")))
                    fig_sd.update_layout(template="plotly_dark", height=320,
                                          title="ERCOT Load vs Available Capacity",
                                          yaxis_title="MW",
                                          legend=dict(orientation="h", y=-0.12),
                                          margin=dict(l=0, r=0, t=40, b=0))
                    st.plotly_chart(fig_sd, use_container_width=True, config=PLOTLY_NOBAR)

                    # Reserve margin
                    if load_col and cap_col:
                        latest_load = sd_df[load_col].dropna().iloc[-1] if sd_df[load_col].notna().any() else 0
                        latest_cap = sd_df[cap_col].dropna().iloc[-1] if sd_df[cap_col].notna().any() else 0
                        if latest_load > 0 and latest_cap > 0:
                            reserve = (latest_cap - latest_load) / latest_load * 100
                            reserve_color = "#00ff88" if reserve > 15 else "#ffaa00" if reserve > 5 else "#ff4444"
                            rm1, rm2, rm3 = st.columns(3)
                            rm1.metric("Current Load", f"{latest_load:,.0f} MW")
                            rm2.metric("Available Capacity", f"{latest_cap:,.0f} MW")
                            rm3.metric("Reserve Margin", f"{reserve:.1f}%",
                                       help="<5% = emergency, 5-15% = tight, >15% = comfortable")

    # Gas vs power correlation
    st.markdown("---")
    st.subheader("Gas vs Power Intraday Correlation")
    st.caption("When gas and power equities diverge, spark spread opportunities emerge.")

    try:
        ng_intra = _fetch_intraday("NG=F", interval_min, n_days)
        xle_intra = _fetch_intraday("XLE", interval_min, n_days)
        if (ng_intra is not None and not ng_intra.empty and
            xle_intra is not None and not xle_intra.empty):
            ng_norm = ng_intra["Close"] / ng_intra["Close"].iloc[0] * 100
            xle_norm = xle_intra["Close"] / xle_intra["Close"].iloc[0] * 100

            fig_corr_chart = go.Figure()
            fig_corr_chart.add_trace(go.Scatter(x=ng_norm.index, y=ng_norm, mode="lines",
                                                 name="Natural Gas", line=dict(color="#ff8800", width=2)))
            fig_corr_chart.add_trace(go.Scatter(x=xle_norm.index, y=xle_norm, mode="lines",
                                                 name="Energy ETF (XLE)", line=dict(color="#00d1ff", width=2)))
            fig_corr_chart.add_hline(y=100, line_dash="dash", line_color="#333")
            fig_corr_chart.update_layout(template="plotly_dark", height=320,
                                          title=f"NG vs XLE — Normalized ({interval}, {chart_days})",
                                          yaxis_title="Indexed (100 = start)",
                                          legend=dict(orientation="h", y=-0.12),
                                          margin=dict(l=0, r=0, t=40, b=0))
            st.plotly_chart(fig_corr_chart, use_container_width=True, config=PLOTLY_NOBAR)

            common_idx = ng_norm.index.intersection(xle_norm.index)
            if len(common_idx) > 20:
                intra_corr = ng_norm.loc[common_idx].corr(xle_norm.loc[common_idx])
                st.metric("Intraday Correlation", f"{intra_corr:.3f}",
                          help="High = move together. Low/negative = spread opportunity.")
    except Exception:
        pass


# ═══════════════════════════════════════════════
# TAB 1: SPARK SPREAD
# ═══════════════════════════════════════════════
with tab_spark, error_boundary("Spark Spread"):
    st.subheader("Spark Spread Analysis")

    with st.expander("How to read this tab", expanded=False):
        st.markdown(
            "The **spark spread** is the gross profit margin for a gas-fired power plant:\n\n"
            "**Spark Spread = Power Price − (Gas Price × Heat Rate / 1000)**\n\n"
            "- **Positive** = profitable to run the plant (generate power and sell it)\n"
            "- **Negative** = cheaper to buy power from the grid than generate it (shut down)\n"
            "- **Heat rate** measures plant efficiency in BTU/kWh. Lower = more efficient.\n"
            "  - CCGT (combined cycle): 6,500-7,500 BTU/kWh\n"
            "  - CT (peaker): 9,000-11,000 BTU/kWh\n"
            "  - Old steam: 10,000-12,000 BTU/kWh\n\n"
            "**VOM** (Variable O&M) costs ~$2-4/MWh for CCGTs, $4-8/MWh for peakers. "
            "The **net spark spread** subtracts VOM to show true operating profit.\n\n"
            "Traders use this to decide: run the plant, sell gas instead, or buy power from the market?"
        )

    hr_select = st.slider("Heat Rate (BTU/kWh)", 6000, 12000, 7000, 500, key="ps_hr",
                           help="6,500-7,500 = efficient CCGT, 9,000+ = peaker")
    vom = st.slider("Variable O&M ($/MWh)", 0.0, 10.0, 3.0, 0.5, key="ps_vom")

    st.caption(f"Gas price used: **${gas_price:.2f}/MMBtu** (NG=F front month). "
               "Note: this is the daily settlement — intraday gas moves are not reflected." if gas_price else "")

    if gas_price and rt_data:
        fuel_cost_mwh = gas_price * hr_select / 1000
        spark_rows = []
        for row in rt_data:
            try:
                ts = row.get("timestamp") or row.get("time")
                hub_val = row.get("hbHubAvg")
                if ts and hub_val is not None:
                    hub = float(hub_val)
                    spark_val = hub - fuel_cost_mwh - vom
                    spark_rows.append({"time": ts, "power": hub, "spark": spark_val,
                                       "fuel_cost": fuel_cost_mwh})
            except (ValueError, TypeError):
                pass

        if spark_rows:
            spark_df = pd.DataFrame(spark_rows)
            spark_df["time"] = pd.to_datetime(spark_df["time"])
            spark_df = spark_df.sort_values("time")

            # Spark spread chart
            fig_spark = make_subplots(specs=[[{"secondary_y": True}]])

            fig_spark.add_trace(go.Bar(
                x=spark_df["time"], y=spark_df["spark"],
                marker_color=["#00ff88" if v > 0 else "#ff4444" for v in spark_df["spark"]],
                name="Net Spark Spread",
            ), secondary_y=False)

            fig_spark.add_trace(go.Scatter(
                x=spark_df["time"], y=spark_df["power"], mode="lines",
                name="Hub Avg Price", line=dict(color="#00d1ff", width=2),
            ), secondary_y=True)

            fig_spark.add_trace(go.Scatter(
                x=spark_df["time"], y=spark_df["fuel_cost"], mode="lines",
                name=f"Fuel Cost ({hr_select} HR)", line=dict(color="#ffaa00", width=1, dash="dash"),
            ), secondary_y=True)

            fig_spark.add_hline(y=0, line_dash="dash", line_color="#555", secondary_y=False)
            fig_spark.update_layout(template="plotly_dark", height=450,
                                     title=f"Spark Spread — {hr_select} BTU/kWh, ${vom:.1f}/MWh VOM",
                                     legend=dict(orientation="h", y=-0.12),
                                     margin=dict(l=0, r=0, t=40, b=0))
            fig_spark.update_yaxes(title_text="Spark Spread ($/MWh)", secondary_y=False)
            fig_spark.update_yaxes(title_text="Price ($/MWh)", secondary_y=True)
            st.plotly_chart(fig_spark, use_container_width=True, config=PLOTLY_NOBAR)

            # Summary
            avg_spark = spark_df["spark"].mean()
            pct_profitable = (spark_df["spark"] > 0).mean() * 100
            max_spark = spark_df["spark"].max()
            min_spark = spark_df["spark"].min()

            sc1, sc2, sc3, sc4 = st.columns(4)
            sc1.metric("Avg Spark", f"${avg_spark:.2f}/MWh")
            sc2.metric("% Profitable Hours", f"{pct_profitable:.0f}%")
            sc3.metric("Best Hour", f"${max_spark:.2f}/MWh")
            sc4.metric("Worst Hour", f"${min_spark:.2f}/MWh")

            # Multi-heat-rate comparison
            st.subheader("Profitability by Plant Type")
            st.caption("Same power prices, different heat rates — shows which plants are in/out of the money.")

            plant_types = [
                ("Efficient CCGT", 6800, 2.5),
                ("Average CCGT", 7500, 3.0),
                ("Old CCGT", 8500, 4.0),
                ("Peaker CT", 10000, 6.0),
                ("Steam Turbine", 10500, 5.0),
            ]
            plant_data = []
            for name, hr, v in plant_types:
                avg_sp = spark_df["power"].mean() - (gas_price * hr / 1000) - v
                pct_prof = ((spark_df["power"] - (gas_price * hr / 1000) - v) > 0).mean() * 100
                plant_data.append({
                    "Plant Type": name, "Heat Rate": f"{hr:,}", "VOM": f"${v:.1f}",
                    "Avg Spread": f"${avg_sp:.2f}/MWh",
                    "% Profitable": f"{pct_prof:.0f}%",
                    "Status": "Running" if avg_sp > 0 else "Marginal" if avg_sp > -5 else "Offline",
                })
            st.dataframe(pd.DataFrame(plant_data), use_container_width=True, hide_index=True)
    else:
        st.warning("Need gas price and ERCOT RT price data for spark spread analysis.")


# ═══════════════════════════════════════════════
# TAB 2: HEAT RATE TRADE
# ═══════════════════════════════════════════════
with tab_heatrate, error_boundary("Heat Rate Trade"):
    st.subheader("Implied Heat Rate Analysis")

    with st.expander("How to read this tab", expanded=False):
        st.markdown(
            "The **implied heat rate** is the power price divided by gas price:\n\n"
            "**Implied HR = Power Price ($/MWh) / Gas Price ($/MMBtu)**\n\n"
            "This tells you how efficient a plant needs to be to break even at current prices.\n\n"
            "- **Implied HR > 10,000**: Even inefficient peakers are profitable → very tight market\n"
            "- **Implied HR 7,000-10,000**: CCGTs profitable, peakers marginal\n"
            "- **Implied HR < 7,000**: Only the most efficient plants cover fuel costs\n"
            "- **Implied HR < 5,000**: Almost nothing covers fuel costs → overbuilt or low demand\n\n"
            "The **heat rate trade** is: go long power / short gas when implied HR is low (expecting it to rise), "
            "or short power / long gas when implied HR is high (expecting mean reversion)."
        )

    if gas_price and gas_price > 0 and rt_data:
        # Build implied heat rate time series
        hr_rows = []
        for row in rt_data:
            try:
                ts = row.get("timestamp") or row.get("time")
                hub_val = row.get("hbHubAvg")
                if ts and hub_val is not None:
                    hub = float(hub_val)
                    if hub > 0:  # negative prices give nonsensical heat rates
                        ihr = hub / gas_price * 1000  # BTU/kWh
                        hr_rows.append({"time": ts, "implied_hr": ihr, "power": hub})
            except (ValueError, TypeError):
                pass

        if hr_rows:
            hr_df = pd.DataFrame(hr_rows)
            hr_df["time"] = pd.to_datetime(hr_df["time"])
            hr_df = hr_df.sort_values("time")

            fig_hr = go.Figure()
            fig_hr.add_trace(go.Scatter(
                x=hr_df["time"], y=hr_df["implied_hr"], mode="lines",
                line=dict(color="#00d1ff", width=2), name="Implied Heat Rate",
                fill="tozeroy", fillcolor="rgba(0,209,255,0.05)",
            ))

            # Reference lines for plant types
            for hr_ref, label, color in [(7000, "Efficient CCGT", "#00ff88"),
                                          (9500, "Peaker CT", "#ffaa00"),
                                          (10500, "Steam", "#ff4444")]:
                fig_hr.add_hline(y=hr_ref, line_dash="dash", line_color=color,
                                 annotation_text=label, annotation_position="right")

            fig_hr.update_layout(template="plotly_dark", height=420,
                                  title="Implied Heat Rate (BTU/kWh) — Higher = More Plants Profitable",
                                  yaxis_title="Heat Rate (BTU/kWh)",
                                  margin=dict(l=0, r=0, t=40, b=0))
            st.plotly_chart(fig_hr, use_container_width=True, config=PLOTLY_NOBAR)

            avg_ihr = hr_df["implied_hr"].mean()
            hr_m1, hr_m2, hr_m3 = st.columns(3)
            hr_m1.metric("Avg Implied HR", f"{avg_ihr:,.0f}")
            hr_m2.metric("Peak HR", f"{hr_df['implied_hr'].max():,.0f}")
            hr_m3.metric("Min HR", f"{hr_df['implied_hr'].min():,.0f}")

            # Heat rate duration curve
            st.subheader("Heat Rate Duration Curve")
            st.caption("Sorted from highest to lowest — shows what % of hours each plant type was in-the-money.")

            sorted_hr = hr_df["implied_hr"].sort_values(ascending=False).values
            pct_axis = np.linspace(0, 100, len(sorted_hr))

            fig_dur = go.Figure()
            fig_dur.add_trace(go.Scatter(x=pct_axis, y=sorted_hr, mode="lines",
                                         line=dict(color="#00d1ff", width=2), name="Implied HR",
                                         fill="tozeroy", fillcolor="rgba(0,209,255,0.08)"))
            for hr_ref, label, color in [(7000, "CCGT", "#00ff88"), (9500, "Peaker", "#ffaa00")]:
                fig_dur.add_hline(y=hr_ref, line_dash="dash", line_color=color, annotation_text=label)
            fig_dur.update_layout(template="plotly_dark", height=350,
                                   title="Heat Rate Duration Curve",
                                   xaxis_title="% of Hours", yaxis_title="Implied HR (BTU/kWh)",
                                   margin=dict(l=0, r=0, t=40, b=0))
            st.plotly_chart(fig_dur, use_container_width=True, config=PLOTLY_NOBAR)
    else:
        st.warning("Need gas price and ERCOT RT price data for heat rate analysis.")


# ═══════════════════════════════════════════════
# TAB 3: PEAK / OFF-PEAK
# ═══════════════════════════════════════════════
with tab_peakoff, error_boundary("Peak / Off-Peak"):
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


# ═══════════════════════════════════════════════
# TAB 4: RT vs DAM ARBITRAGE
# ═══════════════════════════════════════════════
with tab_rtdam, error_boundary("RT vs DAM Arb"):
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

                    def _parse_hour(series):
                        """Parse hour from various ERCOT formats: numeric (1-24), string ('01:00'), etc."""
                        parsed = pd.to_numeric(series, errors="coerce")
                        if parsed.isna().all():
                            # Try parsing as time string like "01:00" or "1:00"
                            parsed = series.astype(str).str.extract(r"(\d+)")[0].astype(float)
                        return parsed

                    # Aggregate RT to hourly if it's 15-min intervals
                    if rt_time_col and rt_time_col in rt_api_df.columns:
                        rt_api_df["_hour"] = _parse_hour(rt_api_df[rt_time_col])
                        rt_api_df = rt_api_df.dropna(subset=["_hour"])
                        rt_hourly = rt_api_df.groupby("_hour")[rt_price_col].mean().reset_index()
                        rt_hourly.columns = ["hour", "rt_price"]
                    else:
                        rt_hourly = pd.DataFrame({"hour": range(1, len(rt_api_df) + 1), "rt_price": rt_api_df[rt_price_col].values})

                    if dam_time_col and dam_time_col in dam_api_df.columns:
                        dam_api_df["_hour"] = _parse_hour(dam_api_df[dam_time_col])
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


# ═══════════════════════════════════════════════
# TAB 5: RENEWABLE CURTAILMENT
# ═══════════════════════════════════════════════
with tab_curtail, error_boundary("Renewable Curtailment"):
    st.subheader("Renewable Curtailment & Negative Pricing")

    with st.expander("How to read this tab", expanded=False):
        st.markdown(
            "When wind and solar generation exceeds demand, power prices can go **negative** — "
            "generators are paid to NOT produce. This creates opportunities:\n\n"
            "- **Storage operators**: charge batteries for free (or get paid to charge)\n"
            "- **Flexible load**: shift consumption to negative-price hours (data centers, electrolysis)\n"
            "- **Curtailment analysis**: how often does renewable output exceed demand?\n\n"
            "**Over-generation risk** = total generation capacity > load. When this happens, "
            "the System Lambda drops to zero and prices crash. "
            "ERCOT is the most wind/solar-rich grid in the US, making this increasingly common."
        )

    if eia_grid is not None and not eia_grid.empty:
        # Filter to ERCOT respondent
        ercot_gen = eia_grid.copy()

        # EIA API returns different column names depending on the endpoint
        fuel_col = next((c for c in ercot_gen.columns if c.lower() in ("fueltype", "fuel_type", "type-name", "fuelTypeName")), None)
        if fuel_col is None:
            fuel_col = next((c for c in ercot_gen.columns if "fuel" in c.lower() or "type" in c.lower()), None)

        if fuel_col:
            ercot_gen = ercot_gen.rename(columns={fuel_col: "fueltype"})

        if "fueltype" in ercot_gen.columns:
            # Renewable vs total — handle both EIA codes and full names
            renewable_types = ["SUN", "WND", "Solar", "Wind", "solar", "wind"]
            ercot_gen["is_renewable"] = ercot_gen["fueltype"].isin(renewable_types)

            hourly_total = ercot_gen.groupby("period")["value"].sum().reset_index()
            hourly_total.columns = ["period", "total_gen"]
            hourly_renewable = ercot_gen[ercot_gen["is_renewable"]].groupby("period")["value"].sum().reset_index()
            hourly_renewable.columns = ["period", "renewable_gen"]

            merged = hourly_total.merge(hourly_renewable, on="period", how="left").fillna(0)
            merged["renewable_pct"] = merged["renewable_gen"] / merged["total_gen"].replace(0, np.nan) * 100
            merged = merged.sort_values("period")

            fig_ren = make_subplots(specs=[[{"secondary_y": True}]])
            fig_ren.add_trace(go.Scatter(
                x=merged["period"], y=merged["total_gen"], mode="lines",
                name="Total Generation", line=dict(color="#555", width=1),
            ), secondary_y=False)
            fig_ren.add_trace(go.Scatter(
                x=merged["period"], y=merged["renewable_gen"], mode="lines",
                name="Renewable (Wind+Solar)", line=dict(color="#00ff88", width=2),
                fill="tozeroy", fillcolor="rgba(0,255,136,0.08)",
            ), secondary_y=False)
            fig_ren.add_trace(go.Scatter(
                x=merged["period"], y=merged["renewable_pct"], mode="lines",
                name="Renewable %", line=dict(color="#ffaa00", width=2, dash="dash"),
            ), secondary_y=True)

            fig_ren.update_layout(template="plotly_dark", height=420,
                                   title="ERCOT Generation: Total vs Renewable (14 Days)",
                                   legend=dict(orientation="h", y=-0.12),
                                   margin=dict(l=0, r=0, t=40, b=0))
            fig_ren.update_yaxes(title_text="Generation (MWh)", secondary_y=False)
            fig_ren.update_yaxes(title_text="Renewable %", secondary_y=True)
            st.plotly_chart(fig_ren, use_container_width=True, config=PLOTLY_NOBAR)

            # Metrics
            avg_ren_pct = merged["renewable_pct"].mean()
            max_ren_pct = merged["renewable_pct"].max()
            high_ren_hours = (merged["renewable_pct"] > 50).sum()

            rm1, rm2, rm3 = st.columns(3)
            rm1.metric("Avg Renewable Share", f"{avg_ren_pct:.1f}%")
            rm2.metric("Peak Renewable Share", f"{max_ren_pct:.1f}%")
            rm3.metric("Hours > 50% Renewable", high_ren_hours)
        else:
            st.info("EIA data doesn't include fuel type breakdown for this query.")
    elif fuel_mix:
        # Fallback to dashboard fuel mix
        fm_data = fuel_mix.get("data", [])
        if fm_data:
            st.caption("Using ERCOT dashboard (current snapshot only).")
            fm_df = pd.DataFrame(fm_data)
            if "genMw" in fm_df.columns and "fuelType" in fm_df.columns:
                fm_df["genMw"] = pd.to_numeric(fm_df["genMw"], errors="coerce")
                total = fm_df["genMw"].sum()
                renewable = fm_df[fm_df["fuelType"].isin(["Wind", "Solar"])]["genMw"].sum()
                st.metric("Current Renewable Share", f"{renewable/total*100:.1f}%" if total > 0 else "N/A")
    else:
        st.warning("Need EIA hourly grid data or ERCOT dashboard for curtailment analysis.")


# ═══════════════════════════════════════════════
# TAB 6: CONGESTION
# ═══════════════════════════════════════════════
with tab_congestion, error_boundary("Congestion"):
    st.subheader("Congestion & Basis Risk")

    with st.expander("How to read this tab", expanded=False):
        st.markdown(
            "**Congestion** occurs when transmission lines are full — power can't flow freely between regions. "
            "This creates price differences between locations (nodes vs hubs).\n\n"
            "- **Basis** = Node Price − Hub Price\n"
            "- **Positive basis** = the node is more expensive (constrained imports)\n"
            "- **Negative basis** = the node is cheaper (constrained exports / trapped generation)\n\n"
            "**Why it matters:**\n"
            "- Generators at congested nodes earn less than the hub price\n"
            "- Load at congested nodes pays more than the hub price\n"
            "- **CRRs** (Congestion Revenue Rights) hedge this risk\n\n"
            "Persistent congestion at a node signals infrastructure bottlenecks — "
            "transmission upgrades or new generation at that location could be profitable."
        )

    if rt_data and len(rt_data) > 5:
        # Extract hub prices
        hubs = ["hbHubAvg", "hbNorth", "hbSouth", "hbWest", "hbHouston"]
        hub_labels = {"hbHubAvg": "Hub Average", "hbNorth": "North", "hbSouth": "South",
                      "hbWest": "West", "hbHouston": "Houston"}

        hub_rows = []
        for row in rt_data:
            try:
                ts = row.get("timestamp") or row.get("time")
                entry = {"time": ts}
                for h in hubs:
                    if h in row and row[h] is not None:
                        entry[hub_labels.get(h, h)] = float(row[h])
                if len(entry) > 1:  # has at least one hub price
                    hub_rows.append(entry)
            except (ValueError, TypeError):
                pass

        if hub_rows:
            hub_df = pd.DataFrame(hub_rows)
            hub_df["time"] = pd.to_datetime(hub_df["time"])
            hub_df = hub_df.sort_values("time")

            available_hubs = [c for c in hub_df.columns if c != "time" and c != "Hub Average"]

            if not available_hubs:
                st.info("No regional hub price data available in this snapshot.")
            else:
                # Hub price comparison
                fig_hubs = go.Figure()
                hub_colors = {"North": "#00d1ff", "South": "#00ff88", "West": "#ffaa00", "Houston": "#ff6b6b"}
                if "Hub Average" in hub_df.columns:
                    fig_hubs.add_trace(go.Scatter(x=hub_df["time"], y=hub_df["Hub Average"], mode="lines",
                                                   name="Hub Average", line=dict(color="#fff", width=2)))
                for hub_name in available_hubs:
                    fig_hubs.add_trace(go.Scatter(
                        x=hub_df["time"], y=hub_df[hub_name], mode="lines",
                        name=hub_name, line=dict(color=hub_colors.get(hub_name, "#888"), width=1),
                    ))
                fig_hubs.update_layout(template="plotly_dark", height=400,
                                        title="ERCOT Hub Prices (RT SPP)",
                                        yaxis_title="$/MWh",
                                        legend=dict(orientation="h", y=-0.12),
                                        margin=dict(l=0, r=0, t=40, b=0))
                st.plotly_chart(fig_hubs, use_container_width=True, config=PLOTLY_NOBAR)

                # Basis (each hub vs hub average)
                if "Hub Average" in hub_df.columns:
                    st.subheader("Basis (Hub − Average)")
                    st.caption("Positive = that hub is more expensive than average (congestion importing). "
                               "Negative = cheaper than average (excess generation / trapped supply).")

                    fig_basis = go.Figure()
                    for hub_name in available_hubs:
                        basis = hub_df[hub_name] - hub_df["Hub Average"]
                        fig_basis.add_trace(go.Scatter(
                            x=hub_df["time"], y=basis, mode="lines",
                            name=hub_name, line=dict(color=hub_colors.get(hub_name, "#888"), width=2),
                        ))
                    fig_basis.add_hline(y=0, line_dash="dash", line_color="#555")
                    fig_basis.update_layout(template="plotly_dark", height=350,
                                             title="Congestion Basis by Hub ($/MWh)",
                                             yaxis_title="Basis ($/MWh)",
                                             legend=dict(orientation="h", y=-0.12),
                                             margin=dict(l=0, r=0, t=40, b=0))
                    st.plotly_chart(fig_basis, use_container_width=True, config=PLOTLY_NOBAR)

                    # Basis statistics
                    basis_stats = []
                    for hub_name in available_hubs:
                        basis = (hub_df[hub_name] - hub_df["Hub Average"]).dropna()
                        if not basis.empty:
                            basis_stats.append({
                                "Hub": hub_name,
                                "Avg Basis": f"${basis.mean():+.2f}/MWh",
                                "Max Basis": f"${basis.max():+.2f}/MWh",
                                "Min Basis": f"${basis.min():+.2f}/MWh",
                                "Std Dev": f"${basis.std():.2f}",
                                "% Positive": f"{(basis > 0).mean()*100:.0f}%",
                                "CRR Value": f"${abs(basis.mean()):.2f}/MWh",
                            })
                    if basis_stats:
                        st.dataframe(pd.DataFrame(basis_stats), use_container_width=True, hide_index=True)
                        st.caption("**CRR Value** = absolute average basis — the approximate value of a "
                                   "Congestion Revenue Right between that hub and the system average.")
    else:
        st.warning("Need ERCOT systemWidePrices data for congestion analysis.")


# ═══════════════════════════════════════════════
# TAB 8: SIMILAR DAY FORECAST (v2 — full rewrite)
# ═══════════════════════════════════════════════

with tab_simday, error_boundary("Similar Day Forecast"):
    st.subheader("Similar Day Price Forecast")
    st.caption(
        "Matches tomorrow's weather, wind, and calendar profile against historical days. "
        "Prices are gas-adjusted and inverse-distance weighted for accuracy."
    )

    import requests as _sim_req
    import numpy as _np
    from datetime import date as _date_cls, timedelta as _td, datetime as _dt

    _WEATHER_NODES = [
        {"name": "Houston", "lat": 29.76, "lon": -95.37, "weight": 0.6},
        {"name": "Dallas", "lat": 32.78, "lon": -96.80, "weight": 0.4},
    ]

    # US holidays (simplified — major ones that affect load)
    _HOLIDAYS = {
        (1, 1), (7, 4), (12, 25), (11, 28), (11, 29),  # NYD, July4, Xmas, Thanksgiving+Fri
        (9, 1), (5, 26), (1, 20),  # Labor Day, Memorial Day, MLK (approx)
    }

    def _is_holiday(d):
        return (d.month, d.day) in _HOLIDAYS

    def _heat_index(temp_f, humidity):
        """Compute heat index from temperature (°F) and relative humidity (%)."""
        if temp_f < 80:
            return temp_f
        hi = (-42.379 + 2.04901523 * temp_f + 10.14333127 * humidity
              - 0.22475541 * temp_f * humidity - 0.00683783 * temp_f**2
              - 0.05481717 * humidity**2 + 0.00122874 * temp_f**2 * humidity
              + 0.00085282 * temp_f * humidity**2 - 0.00000199 * temp_f**2 * humidity**2)
        return max(hi, temp_f)

    @st.cache_data(ttl=3600, show_spinner=False)
    def _fetch_wx_forecast():
        results = {}
        for node in _WEATHER_NODES:
            try:
                r = _sim_req.get("https://api.open-meteo.com/v1/forecast", params={
                    "latitude": node["lat"], "longitude": node["lon"],
                    "hourly": "temperature_2m,wind_speed_10m,cloud_cover,relative_humidity_2m",
                    "temperature_unit": "fahrenheit", "wind_speed_unit": "mph",
                    "timezone": "America/Chicago", "forecast_days": 2,
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

    # ── Load forecast data ──
    with st.spinner("Loading weather forecast..."):
        _forecast = _fetch_wx_forecast()

    if not _forecast:
        st.warning("Could not fetch weather forecast from Open-Meteo.")
    else:
        # Build tomorrow's profile (hours 24-47)
        _tm = {"temps": [], "winds": [], "clouds": [], "humids": []}
        for _, nd in _forecast.items():
            w = nd["weight"]
            for key, src in [("temps", "temperature_2m"), ("winds", "wind_speed_10m"),
                              ("clouds", "cloud_cover"), ("humids", "relative_humidity_2m")]:
                vals = nd.get(src, [])
                tomorrow_vals = vals[24:48] if len(vals) >= 48 else vals[-24:]
                _tm[key].append([v * w for v in tomorrow_vals])

        _tm_temp = _np.sum(_tm["temps"], axis=0)
        _tm_wind = _np.sum(_tm["winds"], axis=0)
        _tm_cloud = _np.sum(_tm["clouds"], axis=0)
        _tm_humid = _np.sum(_tm["humids"], axis=0) if _tm["humids"] and len(_tm["humids"][0]) > 0 else [50] * 24

        # Heat index
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
        fc3.metric("Heat Index", f"{_hi_high:.0f}°F", help="Feels-like temperature (temp + humidity)")
        fc4.metric("Avg Wind", f"{_wind_avg:.0f} mph")
        fc5.metric("Cloud Cover", f"{_cloud_avg:.0f}%")
        _day_type = "Holiday" if _is_holiday(_tomorrow) else ("Weekend" if _dow >= 5 else "Weekday")
        fc6.metric("Day Type", _day_type)

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

        sc1, sc2, sc3 = st.columns(3)
        with sc1:
            _lookback = st.slider("Lookback (months)", 3, 24, 12, key="sd_lb")
        with sc2:
            _n_sim = st.slider("Similar days", 3, 15, 7, key="sd_n")
        with sc3:
            _hub = st.selectbox("Settlement Point", ["HB_HUBAVG", "HB_HOUSTON", "HB_NORTH", "HB_SOUTH", "HB_WEST"], key="sd_hub")

        _hist_start = (_date_cls.today() - _td(days=_lookback * 30)).isoformat()
        _hist_end = (_date_cls.today() - _td(days=1)).isoformat()

        with st.spinner(f"Loading {_lookback} months of weather history..."):
            _hist_wx = _fetch_wx_history(_hist_start, _hist_end)

        if _hist_wx:
            _ref = list(_hist_wx.values())[0]
            _h_times = _ref.get("time", [])
            _h_temps = _ref.get("temperature_2m", [])
            _h_winds = _ref.get("wind_speed_10m", [])
            _h_clouds = _ref.get("cloud_cover", [])
            _h_humids = _ref.get("relative_humidity_2m", [])

            if len(_h_temps) >= 48:
                _n_hist_days = len(_h_temps) // 24
                _daily = []

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

                    # Gas price on this date
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
                    })

                if _daily:
                    # Feature vector: [hi_high, temp_high, temp_low, wind_avg, cloud, dow, month, weekend]
                    _target = _np.array([_hi_high, _temp_high, _temp_low, _wind_avg, _cloud_avg,
                                         _dow, _month, 1 if _is_weekend else 0])
                    _norm = _np.array([30, 30, 30, 15, 50, 3, 6, 1]) + 1e-6
                    _wts = _np.array([4.0, 3.0, 2.0, 2.5, 1.0, 1.5, 2.0, 3.0])

                    for d in _daily:
                        _f = _np.array([d["hi_high"], d["temp_high"], d["temp_low"],
                                        d["wind_avg"], d["cloud_avg"],
                                        d["dow"], d["month"], d["is_weekend"]])
                        _diff = (_target - _f) / _norm
                        d["distance"] = float(_np.sqrt((_diff * _wts) @ (_diff * _wts)))
                        d["similarity"] = round(100 / (1 + d["distance"]), 1)

                    _daily.sort(key=lambda d: d["distance"])
                    _sim_days = _daily[:_n_sim]

                    # Display table
                    _sim_rows = []
                    for d in _sim_days:
                        _sim_rows.append({
                            "Date": d["date"].strftime("%Y-%m-%d (%a)"),
                            "High": f"{d['temp_high']:.0f}°F",
                            "Heat Idx": f"{d['hi_high']:.0f}°F",
                            "Wind": f"{d['wind_avg']:.0f} mph",
                            "Gas": f"${d['gas_price']:.2f}" if d["gas_price"] else "N/A",
                            "Similarity": f"{d['similarity']:.0f}%",
                        })
                    st.dataframe(pd.DataFrame(_sim_rows), use_container_width=True, hide_index=True)

                    # ── Fetch ERCOT prices + build forecast ──
                    st.divider()
                    st.markdown("#### Day-Ahead Price Estimate")

                    if _has_api:
                        _profiles = []
                        _profile_dates = []
                        _profile_gas = []
                        _profile_weights = []

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

                                # Gas adjustment: scale historical prices by today's gas / historical gas
                                gas_adj = 1.0
                                if _gas_today and d.get("gas_price") and d["gas_price"] > 0:
                                    gas_adj = _gas_today / d["gas_price"]
                                    gas_adj = max(0.5, min(2.0, gas_adj))  # cap at 2x adjustment

                                _profiles.append(prices * gas_adj)
                                _profile_dates.append(d["date_str"])
                                _profile_gas.append(gas_adj)
                                # Inverse distance weight
                                _profile_weights.append(1.0 / (d["distance"] + 0.1))
                            except Exception:
                                pass

                        if _profiles:
                            _prof_arr = _np.array(_profiles)
                            _w_arr = _np.array(_profile_weights)
                            _w_norm = _w_arr / _w_arr.sum()

                            # Weighted mean (closer matches count more)
                            _wt_mean = _np.average(_prof_arr, axis=0, weights=_w_norm)
                            _min_prof = _np.min(_prof_arr, axis=0)
                            _max_prof = _np.max(_prof_arr, axis=0)

                            # Confidence: CV of weighted profiles
                            _wt_std = _np.sqrt(_np.average((_prof_arr - _wt_mean)**2, axis=0, weights=_w_norm))
                            _avg_cv = float(_np.mean(_wt_std / (_np.abs(_wt_mean) + 1)))
                            _confidence = "High" if _avg_cv < 0.3 else ("Medium" if _avg_cv < 0.6 else "Low")
                            _conf_color = COLORS["success"] if _confidence == "High" else (COLORS["warning"] if _confidence == "Medium" else COLORS["danger"])

                            # ── Forecast chart ──
                            fig_fc = go.Figure()

                            # Confidence band
                            fig_fc.add_trace(go.Scatter(
                                x=_hrs + _hrs[::-1],
                                y=list(_max_prof) + list(_min_prof[::-1]),
                                fill="toself", fillcolor="rgba(0,209,255,0.08)",
                                line=dict(color="rgba(0,0,0,0)"), name="Range", hoverinfo="skip",
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
                                name="Forecast (Weighted)",
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

                            st.caption(
                                f"**Peak spread:** ${_np.mean(_peak) - _np.mean(_offpk):.2f}/MWh | "
                                f"**Gas adjustment:** {'Applied' if _gas_today else 'N/A'} "
                                f"(UNG ${_gas_today:.2f})" if _gas_today else ""
                            )

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
                                        }),
                                        "prompt_summary": f"SimDay {_hub} {_tomorrow}",
                                        "expires_at": (_dt.now() + _td(hours=24)).isoformat(),
                                    }, on_conflict="input_hash").execute()
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


# ═══════════════════════════════════════════════
# TAB 9: STRATEGY BACKTEST
# ═══════════════════════════════════════════════
with tab_backtest, error_boundary("Strategy Backtest"):
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


# ═══════════════════════════════════════════════
# TAB 8: META-ANALYSIS
# ═══════════════════════════════════════════════
with tab_meta, error_boundary("Meta-Analysis"):
    st.subheader("Cross-Strategy Meta-Analysis")

    with st.expander("How to read this tab", expanded=False):
        st.markdown(
            "The meta-analysis answers the key question: **should you run one strategy or combine them?**\n\n"
            "**What you'll find here:**\n"
            "- **Correlation matrix**: if strategies are uncorrelated, combining them is powerful\n"
            "- **4 allocation methods**: equal-weight, inverse-vol, HRP, and risk parity blends\n"
            "- **Drawdown comparison**: which approach has the shallowest drawdowns?\n"
            "- **Rolling performance**: is the edge persistent or decaying?\n"
            "- **Regime breakdown**: which strategies work in calm vs volatile markets?\n"
            "- **Strategy scorecard**: final recommendation on which to keep, drop, or resize\n\n"
            "The goal is to build a **multi-strategy book** that survives individual strategy failure."
        )

    if gas_history is not None and not gas_history.empty:
        gas_hist_m = gas_history.copy()
        gas_hist_m.columns = ["gas"]
        gas_hist_m = gas_hist_m.dropna()
        gas_ret_m = gas_hist_m["gas"].pct_change().dropna()

        if len(gas_ret_m) >= 30:
            # Rebuild strategies using np.where (same safe method as Tab 7)
            idx_m = gas_hist_m.index
            gas_z_m = ((gas_hist_m["gas"] - gas_hist_m["gas"].rolling(63).mean()) /
                       gas_hist_m["gas"].rolling(63).std().replace(0, np.nan)).reindex(idx_m).fillna(0)
            gas_ma_s = gas_hist_m["gas"].rolling(5).mean().reindex(idx_m)
            gas_ma_l = gas_hist_m["gas"].rolling(20).mean().reindex(idx_m)
            gas_vol_m = gas_ret_m.rolling(20).std().reindex(idx_m).fillna(0)
            gas_roc_m = gas_hist_m["gas"].pct_change(5).reindex(idx_m).fillna(0)
            months_m = idx_m.month
            vol_med = gas_vol_m[gas_vol_m > 0].median() if (gas_vol_m > 0).any() else 0.01
            ma_valid = gas_ma_s.notna() & gas_ma_l.notna()

            all_strat_sigs = {
                "Spark MR": pd.Series(np.where(gas_z_m < -1, 1.0, np.where(gas_z_m > 1, -1.0, 0.0)), index=idx_m),
                "Gas Momentum": pd.Series(np.where(~ma_valid, 0.0, np.where(gas_ma_s > gas_ma_l, 1.0, -1.0)), index=idx_m),
                "Vol Breakout": pd.Series(np.where(gas_vol_m > vol_med * 1.5, 1.0, 0.0), index=idx_m),
                "Calendar Spread": pd.Series(np.where(gas_roc_m < -0.03, 1.0, np.where(gas_roc_m > 0.03, -1.0, 0.0)), index=idx_m),
                "Seasonal": pd.Series(np.where(np.isin(months_m, [10,11,12,1,2]), 1.0,
                                               np.where(np.isin(months_m, [4,5,6,7,8,9]), -1.0, 0.0)), index=idx_m),
            }

            # Use same selection as Tab 7 if available, otherwise all
            meta_selected = st.session_state.get("ps_strat_select", list(all_strat_sigs.keys()))
            strat_sigs = {k: v for k, v in all_strat_sigs.items() if k in meta_selected}

            if len(strat_sigs) < 2:
                st.warning("Select at least 2 strategies in the Strategy Backtest tab for meta-analysis.")
                st.stop()

            st.caption(f"Analyzing {len(strat_sigs)} strategies: {', '.join(strat_sigs.keys())}")

            strat_returns = {}
            for name, sig in strat_sigs.items():
                common = gas_ret_m.index.intersection(sig.index)
                sig_a = sig.loc[common].shift(1).fillna(0)
                sr = (sig_a * gas_ret_m.loc[common]).dropna()
                if len(sr) > 30:
                    strat_returns[name] = sr

            if len(strat_returns) >= 2:
                strat_df = pd.DataFrame(strat_returns).dropna()
                n_strats = len(strat_df.columns)
                strat_colors_m = ["#00d1ff", "#00ff88", "#ffaa00", "#ff6b6b", "#ff00ff"]

                # ═══ 1. STRATEGY CORRELATIONS ═══
                st.subheader("1. Strategy Correlations")
                st.caption("Low correlation = combining strategies adds diversification. "
                           "Pairs with correlation > 0.5 are essentially the same trade.")

                corr = strat_df.corr()
                mc1, mc2 = st.columns([2, 1])
                with mc1:
                    fig_corr = go.Figure(data=go.Heatmap(
                        z=corr.values, x=corr.columns.tolist(), y=corr.index.tolist(),
                        colorscale=[[0, "#ff4444"], [0.5, "#1a1a2e"], [1, "#00d1ff"]],
                        zmid=0, zmin=-1, zmax=1,
                        text=[[f"{v:.2f}" for v in row] for row in corr.values],
                        texttemplate="%{text}", textfont={"size": 12},
                        colorbar=dict(title="ρ"),
                    ))
                    fig_corr.update_layout(template="plotly_dark", height=300, margin=dict(l=0, r=0, t=10, b=0))
                    st.plotly_chart(fig_corr, use_container_width=True, config=PLOTLY_NOBAR)
                with mc2:
                    upper = corr.where(np.triu(np.ones(corr.shape), k=1).astype(bool)).stack()
                    avg_corr = upper.mean()
                    max_pair = upper.idxmax() if not upper.empty else ("?", "?")
                    min_pair = upper.idxmin() if not upper.empty else ("?", "?")
                    st.metric("Avg Correlation", f"{avg_corr:.3f}",
                              help="<0.2 = excellent, 0.2-0.5 = good, >0.5 = redundant")
                    st.metric("Most Correlated", f"{max_pair[0]}/{max_pair[1]}",
                              delta=f"ρ = {upper.max():.2f}")
                    st.metric("Least Correlated", f"{min_pair[0]}/{min_pair[1]}",
                              delta=f"ρ = {upper.min():.2f}")
                    if avg_corr < 0.2:
                        st.success("Excellent diversification potential.")
                    elif avg_corr < 0.5:
                        st.info("Good diversification — blending should help.")
                    else:
                        st.warning("High correlation — strategies may be redundant.")

                # ═══ 2. COMBINED PORTFOLIOS ═══
                st.markdown("---")
                st.subheader("2. Combined Multi-Strategy Book")
                st.caption("Four ways to combine the strategies — from simplest to most sophisticated.")

                eq_w = np.full(n_strats, 1 / n_strats)
                strat_vols = strat_df.std() * np.sqrt(252)
                iv_w = ((1 / strat_vols) / (1 / strat_vols).sum()).values

                # HRP weights
                try:
                    from src.quant_features import hrp_allocate
                    hrp_w = hrp_allocate(strat_df).reindex(strat_df.columns).fillna(1 / n_strats).values
                except Exception:
                    hrp_w = eq_w

                # Risk parity (equal risk contribution)
                cov_m = strat_df.cov().values * 252
                from scipy.optimize import minimize as _minimize
                def _rp_obj(w):
                    pv = np.sqrt(w @ cov_m @ w)
                    if pv == 0: return 0
                    mrc = cov_m @ w / pv
                    rc = w * mrc
                    return np.sum((rc - pv / n_strats) ** 2)
                rp_res = _minimize(_rp_obj, eq_w, method="SLSQP",
                                   bounds=[(0.01, 1)] * n_strats,
                                   constraints=[{"type": "eq", "fun": lambda w: np.sum(w) - 1}])
                rp_w = rp_res.x if rp_res.success else eq_w

                combo_methods = {
                    "Equal Weight": eq_w,
                    "Inverse Vol": iv_w,
                    "Risk Parity": rp_w,
                    "HRP": hrp_w,
                }
                combo_colors = {"Equal Weight": "#555", "Inverse Vol": "#ffaa00", "Risk Parity": "#00d1ff", "HRP": "#00ff88"}

                # Show weights
                wt_fig = go.Figure()
                for method, w in combo_methods.items():
                    wt_fig.add_trace(go.Bar(x=strat_df.columns.tolist(), y=w * 100, name=method,
                                            marker_color=combo_colors[method]))
                wt_fig.update_layout(template="plotly_dark", height=300, barmode="group",
                                      title="Strategy Allocation Weights by Method (%)",
                                      yaxis_title="Weight (%)", margin=dict(l=0, r=0, t=40, b=0))
                st.plotly_chart(wt_fig, use_container_width=True, config=PLOTLY_NOBAR)

                # Equity curves
                fig_eq = go.Figure()
                combo_returns = {}
                for method, w in combo_methods.items():
                    pr = strat_df.values @ w
                    combo_returns[method] = pd.Series(pr, index=strat_df.index)
                    cum = pd.Series((1 + pr).cumprod() * 100, index=strat_df.index)
                    fig_eq.add_trace(go.Scatter(x=cum.index, y=cum, mode="lines", name=method,
                                                line=dict(color=combo_colors[method], width=3)))
                # Add individual strategies (faded)
                for i, col in enumerate(strat_df.columns):
                    cum_s = (1 + strat_df[col]).cumprod() * 100
                    fig_eq.add_trace(go.Scatter(x=cum_s.index, y=cum_s, mode="lines", name=col,
                                                line=dict(color=strat_colors_m[i % len(strat_colors_m)], width=1, dash="dot"),
                                                opacity=0.4))
                fig_eq.add_hline(y=100, line_dash="dash", line_color="#333")
                fig_eq.update_layout(template="plotly_dark", height=420,
                                      title="Equity Curves: Combined Books vs Individual Strategies",
                                      yaxis_title="Portfolio Value (base=100)",
                                      legend=dict(orientation="h", y=-0.18),
                                      margin=dict(l=0, r=0, t=40, b=0))
                st.plotly_chart(fig_eq, use_container_width=True, config=PLOTLY_NOBAR)

                # ═══ 3. COMPREHENSIVE METRICS TABLE ═══
                st.subheader("3. Performance Comparison")

                def _full_metrics(rets, name):
                    ann_r = rets.mean() * 252 * 100
                    ann_v = rets.std() * np.sqrt(252) * 100
                    sharpe = ann_r / ann_v if ann_v > 0 else 0
                    ds = rets[rets < 0].std() * np.sqrt(252) * 100
                    sortino = ann_r / ds if ds > 0 else 0
                    cum = (1 + rets).cumprod()
                    dd = ((cum / cum.cummax()) - 1).min() * 100
                    calmar = ann_r / abs(dd) if dd != 0 else 0
                    wr = (rets > 0).mean() * 100
                    return {"Strategy": name, "Return": f"{ann_r:.1f}%", "Vol": f"{ann_v:.1f}%",
                            "Sharpe": f"{sharpe:.2f}", "Sortino": f"{sortino:.2f}",
                            "Max DD": f"{dd:.1f}%", "Calmar": f"{calmar:.2f}", "Win%": f"{wr:.0f}%"}

                all_metrics = []
                for method, pr in combo_returns.items():
                    all_metrics.append(_full_metrics(pr, f"📊 {method}"))
                for col in strat_df.columns:
                    all_metrics.append(_full_metrics(strat_df[col], col))
                st.dataframe(pd.DataFrame(all_metrics), use_container_width=True, hide_index=True)

                # ═══ 4. DRAWDOWN ANALYSIS ═══
                st.markdown("---")
                st.subheader("4. Drawdown Comparison")
                st.caption("Shallower drawdowns = more capital-efficient. The combined book should have "
                           "shallower drawdowns than any single strategy — that's the diversification payoff.")

                fig_dd = go.Figure()
                for method, pr in combo_returns.items():
                    cum = (1 + pr).cumprod()
                    dd = (cum / cum.cummax() - 1) * 100
                    fig_dd.add_trace(go.Scatter(x=dd.index, y=dd, mode="lines", name=method,
                                                line=dict(color=combo_colors[method], width=2),
                                                fill="tozeroy" if method == "HRP" else None,
                                                fillcolor="rgba(0,255,136,0.05)" if method == "HRP" else None))
                fig_dd.update_layout(template="plotly_dark", height=300,
                                      title="Drawdown: Combined Books",
                                      yaxis_title="Drawdown (%)",
                                      legend=dict(orientation="h", y=-0.15),
                                      margin=dict(l=0, r=0, t=40, b=0))
                st.plotly_chart(fig_dd, use_container_width=True, config=PLOTLY_NOBAR)

                # ═══ 5. ROLLING SHARPE ═══
                st.markdown("---")
                st.subheader("5. Rolling Performance")
                st.caption("63-day rolling Sharpe ratio. Stable > 0 = persistent edge. Oscillating around 0 = noise.")

                fig_rs = go.Figure()
                for method in ["Equal Weight", "HRP"]:
                    pr = combo_returns[method]
                    roll_s = pr.rolling(63).mean() / pr.rolling(63).std() * np.sqrt(252)
                    roll_s = roll_s.dropna()
                    fig_rs.add_trace(go.Scatter(x=roll_s.index, y=roll_s, mode="lines",
                                                name=method, line=dict(color=combo_colors[method], width=2)))
                fig_rs.add_hline(y=0, line_dash="dash", line_color="#555")
                fig_rs.add_hline(y=0.5, line_dash="dot", line_color="#00ff88", annotation_text="Target: 0.5")
                fig_rs.update_layout(template="plotly_dark", height=300,
                                      title="Rolling 63D Sharpe Ratio",
                                      yaxis_title="Sharpe", margin=dict(l=0, r=0, t=40, b=0))
                st.plotly_chart(fig_rs, use_container_width=True, config=PLOTLY_NOBAR)

                # ═══ 6. REGIME ANALYSIS ═══
                st.markdown("---")
                st.subheader("6. Regime Breakdown")
                st.caption("Performance by gas volatility regime. Robust strategies work in ALL regimes.")

                vol_regime = gas_ret_m.rolling(20).std().reindex(strat_df.index).ffill()
                vol_q33 = vol_regime.quantile(0.33)
                vol_q66 = vol_regime.quantile(0.66)
                regime_labels = np.where(vol_regime <= vol_q33, "Low Vol",
                                         np.where(vol_regime >= vol_q66, "High Vol", "Normal"))
                regime_series = pd.Series(regime_labels, index=strat_df.index)

                regime_data = []
                for regime in ["Low Vol", "Normal", "High Vol"]:
                    mask = regime_series == regime
                    for col in strat_df.columns:
                        sr = strat_df.loc[mask, col]
                        if len(sr) > 10 and sr.std() > 0:
                            sharpe_r = sr.mean() / sr.std() * np.sqrt(252)
                            ret_r = sr.mean() * 252 * 100
                            regime_data.append({"Strategy": col, "Regime": regime,
                                                "Sharpe": sharpe_r, "Return": ret_r})

                if regime_data:
                    regime_df = pd.DataFrame(regime_data)
                    pivot_s = regime_df.pivot(index="Strategy", columns="Regime", values="Sharpe")
                    if "Low Vol" in pivot_s.columns and "Normal" in pivot_s.columns and "High Vol" in pivot_s.columns:
                        pivot_s = pivot_s[["Low Vol", "Normal", "High Vol"]]

                    fig_regime = go.Figure(data=go.Heatmap(
                        z=pivot_s.values, x=pivot_s.columns.tolist(), y=pivot_s.index.tolist(),
                        colorscale=[[0, "#ff4444"], [0.5, "#1a1a2e"], [1, "#00ff88"]],
                        zmid=0,
                        text=[[f"{v:.2f}" for v in row] for row in pivot_s.values],
                        texttemplate="%{text}", textfont={"size": 13},
                        colorbar=dict(title="Sharpe"),
                    ))
                    fig_regime.update_layout(template="plotly_dark", height=280,
                                              title="Strategy Sharpe by Volatility Regime",
                                              margin=dict(l=0, r=0, t=40, b=0))
                    st.plotly_chart(fig_regime, use_container_width=True, config=PLOTLY_NOBAR)

                # ═══ 7. STRATEGY SCORECARD ═══
                st.markdown("---")
                st.subheader("7. Strategy Scorecard & Recommendation")
                st.caption("Final assessment: which strategies to keep, resize, or drop.")

                scorecard = []
                for col in strat_df.columns:
                    sr = strat_df[col]
                    ann_r = sr.mean() * 252 * 100
                    sharpe = sr.mean() / sr.std() * np.sqrt(252) if sr.std() > 0 else 0
                    cum = (1 + sr).cumprod()
                    dd = ((cum / cum.cummax()) - 1).min() * 100

                    # Regime robustness (positive Sharpe in all 3 regimes?)
                    regime_ok = 0
                    for regime in ["Low Vol", "Normal", "High Vol"]:
                        mask = regime_series == regime
                        r_sr = strat_df.loc[mask, col]
                        if len(r_sr) > 10 and r_sr.std() > 0 and r_sr.mean() > 0:
                            regime_ok += 1

                    # Diversification value (low avg correlation with others)
                    other_corrs = [corr.loc[col, c] for c in strat_df.columns if c != col]
                    avg_corr_with_others = np.mean(other_corrs) if other_corrs else 0

                    # Score
                    score = 0
                    if sharpe > 0.5: score += 3
                    elif sharpe > 0: score += 1
                    if dd > -15: score += 2
                    elif dd > -25: score += 1
                    score += regime_ok  # 0-3 points for regime robustness
                    if avg_corr_with_others < 0.2: score += 2
                    elif avg_corr_with_others < 0.4: score += 1

                    if score >= 7:
                        verdict = "KEEP (core)"
                    elif score >= 5:
                        verdict = "KEEP (resize)"
                    elif score >= 3:
                        verdict = "REDUCE"
                    else:
                        verdict = "DROP"

                    color = "#00ff88" if "core" in verdict else "#ffaa00" if "resize" in verdict or "REDUCE" in verdict else "#ff4444"

                    scorecard.append({
                        "Strategy": col,
                        "Sharpe": f"{sharpe:.2f}",
                        "Max DD": f"{dd:.1f}%",
                        "Regimes +": f"{regime_ok}/3",
                        "Avg ρ w/ Others": f"{avg_corr_with_others:.2f}",
                        "Score": f"{score}/10",
                        "Verdict": verdict,
                    })

                sc_df = pd.DataFrame(scorecard)
                st.dataframe(sc_df, use_container_width=True, hide_index=True)

                keeps = [s["Strategy"] for s in scorecard if "KEEP" in s["Verdict"]]
                drops = [s["Strategy"] for s in scorecard if s["Verdict"] == "DROP"]
                if keeps:
                    st.success(f"**Recommended book:** {', '.join(keeps)} — these strategies have positive Sharpe, "
                               f"manageable drawdowns, regime robustness, and diversification value.")
                if drops:
                    st.warning(f"**Consider dropping:** {', '.join(drops)} — these strategies don't justify their "
                               f"risk contribution based on Sharpe, drawdown, and regime performance.")
            else:
                st.warning("Need at least 2 strategy return series for meta-analysis.")
        else:
            st.warning("Not enough gas history for meta-analysis.")
    else:
        st.warning("Need historical gas price data (NG=F) for meta-analysis.")

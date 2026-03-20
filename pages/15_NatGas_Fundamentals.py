import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from src.eia_helpers import fetch_eia_data
from src.layout import setup_page
setup_page("15_NatGas_Fundamentals")

st.title("♨️ Natural Gas Fundamentals")
st.markdown("Live weekly Working Gas in Underground Storage data from the Energy Information Administration (EIA).")

# --- FETCH ALL DATA ---
with st.spinner("Connecting to EIA Database for Natural Gas..."):
    # Lower 48 working gas — fetch 6 years for 5-year average calc
    df_storage = fetch_eia_data("NG.NW2_EPG0_SWO_R48_BCF.W", tail_rows=520)

    # Regional storage
    regions = {
        'East': fetch_eia_data("NG.NW2_EPG0_SWO_R31_BCF.W", tail_rows=260),
        'Midwest': fetch_eia_data("NG.NW2_EPG0_SWO_R32_BCF.W", tail_rows=260),
        'Mountain': fetch_eia_data("NG.NW2_EPG0_SWO_R33_BCF.W", tail_rows=260),
        'Pacific': fetch_eia_data("NG.NW2_EPG0_SWO_R34_BCF.W", tail_rows=260),
        'South Central': fetch_eia_data("NG.NW2_EPG0_SWO_R35_BCF.W", tail_rows=260),
    }

    # Henry Hub weekly spot price
    df_hh = fetch_eia_data("NG.RNGWHHD.W", tail_rows=260)

    # Monthly consumption for days of supply
    df_consumption = fetch_eia_data("NG.N9140US2.M", tail_rows=60)

# --- DASHBOARD RENDER ---
if df_storage is not None and not df_storage.empty:

    latest_report = df_storage.iloc[-1]
    storage_bcf = latest_report['value']
    wow_bcf = latest_report['wow_change']

    flow_type = "Injection" if wow_bcf > 0 else "Withdrawal"
    delta_color = "normal" if wow_bcf > 0 else "inverse"

    # --- 5-Year Average Calculation ---
    df_storage['week'] = df_storage['period'].dt.isocalendar().week.astype(int)
    df_storage['year'] = df_storage['period'].dt.year
    current_year = df_storage['year'].max()
    hist_years = df_storage[df_storage['year'].between(current_year - 5, current_year - 1)]
    five_yr_stats = hist_years.groupby('week')['value'].agg(['mean', 'min', 'max']).reset_index()
    five_yr_stats.columns = ['week', 'avg_5yr', 'min_5yr', 'max_5yr']

    current_week = latest_report['period'].isocalendar().week
    five_yr_avg_now = five_yr_stats.loc[five_yr_stats['week'] == current_week, 'avg_5yr']
    delta_vs_avg = storage_bcf - five_yr_avg_now.values[0] if not five_yr_avg_now.empty else None

    # --- Days of Supply ---
    days_of_supply = None
    if df_consumption is not None and not df_consumption.empty:
        latest_consumption_mmcf = df_consumption['value'].iloc[-1]
        daily_consumption_bcf = latest_consumption_mmcf / 1000 / 30  # MMcf -> Bcf, monthly -> daily
        if daily_consumption_bcf > 0:
            days_of_supply = storage_bcf / daily_consumption_bcf

    # --- METRICS ROW ---
    st.subheader(f"Latest EIA Thursday Report: {latest_report['period'].strftime('%Y-%m-%d')}")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Lower 48 Working Gas", f"{storage_bcf:,.0f} Bcf")
    c2.metric(f"Weekly Net {flow_type}", f"{wow_bcf:+.0f} Bcf", delta_color=delta_color)
    if delta_vs_avg is not None:
        sign = "Above" if delta_vs_avg > 0 else "Below"
        c3.metric("vs 5-Year Average", f"{abs(delta_vs_avg):,.0f} Bcf {sign}", f"{delta_vs_avg:+,.0f} Bcf")
    if days_of_supply is not None:
        c4.metric("Implied Days of Supply", f"{days_of_supply:,.0f} days")

    st.divider()

    # --- CHARTING TABS ---
    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "Storage + 5-Year Band",
        "Year-over-Year Seasonality",
        "Weekly Injections / Withdrawals",
        "Regional Breakdown",
        "Henry Hub Price Overlay"
    ])

    # ---- TAB 1: Total Working Gas with 5-Year Band ----
    with tab1:
        # Show last 260 weeks (5 years) of actual data
        df_recent = df_storage[df_storage['year'] >= current_year - 4].copy()

        fig_band = go.Figure()

        # 5-year band — map week numbers to the current year for display
        band_dates = pd.to_datetime(five_yr_stats['week'].apply(
            lambda w: f"{current_year}-W{int(w):02d}-1"
        ), format='%G-W%V-%u', errors='coerce').dropna()
        valid_mask = band_dates.notna()
        band_dates = band_dates[valid_mask]
        band_stats = five_yr_stats[valid_mask]

        # Min-Max shading
        fig_band.add_trace(go.Scatter(
            x=pd.concat([band_dates, band_dates[::-1]]),
            y=pd.concat([band_stats['max_5yr'], band_stats['min_5yr'][::-1]]),
            fill='toself', fillcolor='rgba(255, 255, 255, 0.08)',
            line=dict(color='rgba(255,255,255,0)'), showlegend=True, name='5-Year Range', hoverinfo='skip'
        ))

        # 5-year average line
        fig_band.add_trace(go.Scatter(
            x=band_dates, y=band_stats['avg_5yr'],
            mode='lines', name='5-Year Average', line=dict(color='#ffaa00', width=2, dash='dash')
        ))

        # Current storage
        fig_band.add_trace(go.Scatter(
            x=df_recent['period'], y=df_recent['value'],
            mode='lines', name=f'Actual Storage', line=dict(color='#ff4b4b', width=2.5)
        ))

        fig_band.update_layout(
            template="plotly_dark", height=500, margin=dict(t=10, b=0, l=0, r=0),
            yaxis_title="Billion Cubic Feet (Bcf)", hovermode="x unified"
        )
        st.plotly_chart(fig_band, use_container_width=True)

    # ---- TAB 2: Year-over-Year Seasonality ----
    with tab2:
        fig_yoy = go.Figure()
        colors = ['#ff4b4b', '#00d1ff', '#00ff96', '#ffaa00', '#ad7fff']

        years_to_plot = sorted(df_storage['year'].unique())[-5:]
        for i, yr in enumerate(years_to_plot):
            yr_data = df_storage[df_storage['year'] == yr].sort_values('week')
            is_current = yr == current_year
            fig_yoy.add_trace(go.Scatter(
                x=yr_data['week'], y=yr_data['value'],
                mode='lines', name=str(yr),
                line=dict(color=colors[i % len(colors)], width=3 if is_current else 1.5),
                opacity=1.0 if is_current else 0.7
            ))

        fig_yoy.update_layout(
            template="plotly_dark", height=500, margin=dict(t=10, b=0, l=0, r=0),
            xaxis_title="Week of Year", yaxis_title="Billion Cubic Feet (Bcf)", hovermode="x unified"
        )
        st.plotly_chart(fig_yoy, use_container_width=True)

    # ---- TAB 3: Weekly Injections / Withdrawals ----
    with tab3:
        df_plot = df_storage.tail(260)
        fig_flow = go.Figure()
        colors_flow = ['#00FF00' if val > 0 else '#FF0000' for val in df_plot['wow_change']]

        fig_flow.add_trace(go.Bar(
            x=df_plot['period'], y=df_plot['wow_change'],
            marker_color=colors_flow,
            hovertemplate="Date: %{x}<br>Net Flow: %{y} Bcf<extra></extra>"
        ))
        fig_flow.update_layout(
            template="plotly_dark", height=500, margin=dict(t=10, b=0, l=0, r=0),
            yaxis_title="Net Change (Bcf)",
            shapes=[dict(type='line', y0=0, y1=0, x0=df_plot['period'].min(), x1=df_plot['period'].max(), line=dict(color='white', width=1))]
        )
        st.plotly_chart(fig_flow, use_container_width=True)

    # ---- TAB 4: Regional Breakdown ----
    with tab4:
        fig_region = go.Figure()
        region_colors = {
            'East': '#00d1ff',
            'Midwest': '#00ff96',
            'Mountain': '#ffaa00',
            'Pacific': '#ad7fff',
            'South Central': '#ff4b4b',
        }

        valid_regions = {k: v for k, v in regions.items() if v is not None and not v.empty}
        for name, df_r in valid_regions.items():
            fig_region.add_trace(go.Scatter(
                x=df_r['period'], y=df_r['value'],
                mode='lines', name=name, stackgroup='one',
                line=dict(color=region_colors.get(name, 'white'), width=0.5)
            ))

        fig_region.update_layout(
            template="plotly_dark", height=500, margin=dict(t=10, b=0, l=0, r=0),
            yaxis_title="Billion Cubic Feet (Bcf)", hovermode="x unified"
        )
        st.plotly_chart(fig_region, use_container_width=True)

        # Regional latest values table
        if valid_regions:
            region_latest = {}
            for name, df_r in valid_regions.items():
                latest = df_r.iloc[-1]
                region_latest[name] = {
                    'Storage (Bcf)': f"{latest['value']:,.0f}",
                    'Weekly Change': f"{latest['wow_change']:+,.0f}",
                }
            st.dataframe(pd.DataFrame(region_latest).T, use_container_width=True)

    # ---- TAB 5: Henry Hub Price Overlay ----
    with tab5:
        if df_hh is not None and not df_hh.empty:
            df_hh_plot = df_hh.tail(260)
            df_storage_plot = df_storage.tail(260)

            fig_hh = go.Figure()

            # Storage on left y-axis
            fig_hh.add_trace(go.Scatter(
                x=df_storage_plot['period'], y=df_storage_plot['value'],
                mode='lines', name='Working Gas Storage',
                line=dict(color='#ff4b4b', width=2), yaxis='y'
            ))

            # Henry Hub on right y-axis
            fig_hh.add_trace(go.Scatter(
                x=df_hh_plot['period'], y=df_hh_plot['value'],
                mode='lines', name='Henry Hub Spot ($/MMBtu)',
                line=dict(color='#00ff96', width=2), yaxis='y2'
            ))

            fig_hh.update_layout(
                template="plotly_dark", height=500, margin=dict(t=10, b=0, l=0, r=0),
                hovermode="x unified",
                yaxis=dict(title="Storage (Bcf)", side='left', showgrid=False),
                yaxis2=dict(title="Henry Hub ($/MMBtu)", side='right', overlaying='y', showgrid=False),
            )
            st.plotly_chart(fig_hh, use_container_width=True)

            # Price metrics
            hh_latest = df_hh_plot.iloc[-1]['value']
            hh_prev = df_hh_plot.iloc[-2]['value'] if len(df_hh_plot) > 1 else hh_latest
            hh_change = hh_latest - hh_prev
            pc1, pc2 = st.columns(2)
            pc1.metric("Henry Hub Spot", f"${hh_latest:,.2f}/MMBtu", f"${hh_change:+,.2f}")
        else:
            st.warning("Henry Hub price data unavailable.")

else:
    st.warning("EIA API Key is missing or invalid. Check your Google Cloud Run Environment Variables.")

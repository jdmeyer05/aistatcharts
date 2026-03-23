import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from src.eia_helpers import fetch_eia_data
from src.layout import setup_page, error_boundary, fun_loader
setup_page("14_Oil_Fundamentals")

st.title("🔥 Oil Fundamentals")
st.markdown("Live macroeconomic supply data directly from the Energy Information Administration (EIA).")

# --- FETCH ALL DATA ---
with fun_loader("data"):
    from concurrent.futures import ThreadPoolExecutor
    _eia_series = [
        ("PET.WCESTUS1.W", 520),   # Commercial crude inventories
        ("PET.WCRFPUS2.W", 260),   # US field production
        ("PET.WCRSTUS1.W", 260),   # Cushing, OK storage
        ("PET.WPULEUS3.W", 260),   # Refinery utilization
        ("PET.WCEIMUS2.W", 260),   # Imports
        ("PET.WCREXUS2.W", 260),   # Exports
        ("PET.RWTC.W", 260),       # WTI spot price
        ("PET.WGTSTUS1.W", 260),   # Gasoline inventories
        ("PET.WDISTUS1.W", 260),   # Distillate inventories
        ("PET.WRPUPUS2.W", 260),   # Product supplied
    ]
    with ThreadPoolExecutor(max_workers=6) as pool:
        _eia_results = list(pool.map(lambda args: fetch_eia_data(*args), _eia_series))
    df_inv, df_prod, df_cushing, df_refinery, df_imports, df_exports, \
        df_wti, df_gasoline, df_distillate, df_supplied = _eia_results

# --- DASHBOARD RENDER ---
if df_inv is not None and not df_inv.empty:

    latest_inv = df_inv.iloc[-1]
    inv_mb = latest_inv['value'] / 1000
    inv_wow = latest_inv['wow_change'] / 1000

    # --- 5-Year Average Calculation ---
    df_inv['week'] = df_inv['period'].dt.isocalendar().week.astype(int)
    df_inv['year'] = df_inv['period'].dt.year
    current_year = df_inv['year'].max()
    hist_years = df_inv[df_inv['year'].between(current_year - 5, current_year - 1)]
    five_yr_stats = hist_years.groupby('week')['value'].agg(['mean', 'min', 'max']).reset_index()
    five_yr_stats.columns = ['week', 'avg_5yr', 'min_5yr', 'max_5yr']
    # Convert to millions
    five_yr_stats[['avg_5yr', 'min_5yr', 'max_5yr']] = five_yr_stats[['avg_5yr', 'min_5yr', 'max_5yr']] / 1000

    current_week = latest_inv['period'].isocalendar().week
    five_yr_avg_now = five_yr_stats.loc[five_yr_stats['week'] == current_week, 'avg_5yr']
    delta_vs_avg = inv_mb - five_yr_avg_now.values[0] if not five_yr_avg_now.empty else None

    # --- Days of Supply ---
    days_of_supply = None
    if df_supplied is not None and not df_supplied.empty:
        daily_consumption_mb = df_supplied.iloc[-1]['value']  # already in MBBL/D (thousands bbl/day)
        if daily_consumption_mb > 0:
            days_of_supply = (latest_inv['value']) / daily_consumption_mb  # both in thousands

    # --- METRICS ROW ---
    st.subheader(f"Latest EIA Weekly Report: {latest_inv['period'].strftime('%Y-%m-%d')}")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Commercial Inventories", f"{inv_mb:.1f}M bbls", f"{inv_wow:+.2f}M bbls (WoW)", delta_color="inverse")

    if df_prod is not None and not df_prod.empty:
        latest_prod = df_prod.iloc[-1]
        prod_mbpd = latest_prod['value'] / 1000
        prod_wow = latest_prod['wow_change'] / 1000
        c2.metric("US Field Production", f"{prod_mbpd:.1f}M bpd", f"{prod_wow:+.2f}M bpd (WoW)")

    if delta_vs_avg is not None:
        sign = "Above" if delta_vs_avg > 0 else "Below"
        c3.metric("vs 5-Year Average", f"{abs(delta_vs_avg):.1f}M bbls {sign}", f"{delta_vs_avg:+.1f}M bbls")

    if days_of_supply is not None:
        c4.metric("Days of Supply", f"{days_of_supply:.0f} days")

    # Secondary metrics row
    mc1, mc2, mc3, mc4 = st.columns(4)
    if df_wti is not None and not df_wti.empty:
        wti_latest = df_wti.iloc[-1]['value']
        wti_wow = df_wti.iloc[-1]['wow_change']
        mc1.metric("WTI Spot Price", f"${wti_latest:.2f}/bbl", f"${wti_wow:+.2f} (WoW)")

    if df_cushing is not None and not df_cushing.empty:
        cushing_mb = df_cushing.iloc[-1]['value'] / 1000
        cushing_wow = df_cushing.iloc[-1]['wow_change'] / 1000
        mc2.metric("Cushing, OK Storage", f"{cushing_mb:.1f}M bbls", f"{cushing_wow:+.2f}M (WoW)", delta_color="inverse")

    if df_refinery is not None and not df_refinery.empty:
        ref_util = df_refinery.iloc[-1]['value']
        ref_wow = df_refinery.iloc[-1]['wow_change']
        mc3.metric("Refinery Utilization", f"{ref_util:.1f}%", f"{ref_wow:+.1f}% (WoW)")

    if df_imports is not None and df_exports is not None and not df_imports.empty and not df_exports.empty:
        net_imports = df_imports.iloc[-1]['value'] - df_exports.iloc[-1]['value']
        mc4.metric("Net Crude Imports", f"{net_imports/1000:.1f}M bpd")

    st.divider()

    # --- CHARTING TABS ---
    tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8 = st.tabs([
        "Inventories + 5-Year Band",
        "YoY Seasonality",
        "Weekly Builds / Draws",
        "WTI Price Overlay",
        "Cushing Storage",
        "Imports / Exports",
        "Refinery Utilization",
        "Product Inventories"
    ])

    # ---- TAB 1: Inventories + 5-Year Band ----
    with tab1:
        df_recent = df_inv[df_inv['year'] >= current_year - 4].copy()

        fig_band = go.Figure()

        band_dates = pd.to_datetime(five_yr_stats['week'].apply(
            lambda w: f"{current_year}-W{int(w):02d}-1"
        ), format='%G-W%V-%u', errors='coerce')
        valid_mask = band_dates.notna()
        band_dates = band_dates[valid_mask]
        band_stats = five_yr_stats[valid_mask]

        fig_band.add_trace(go.Scatter(
            x=pd.concat([band_dates, band_dates[::-1]]),
            y=pd.concat([band_stats['max_5yr'], band_stats['min_5yr'][::-1]]),
            fill='toself', fillcolor='rgba(255, 255, 255, 0.08)',
            line=dict(color='rgba(255,255,255,0)'), showlegend=True, name='5-Year Range', hoverinfo='skip'
        ))

        fig_band.add_trace(go.Scatter(
            x=band_dates, y=band_stats['avg_5yr'],
            mode='lines', name='5-Year Average', line=dict(color='#ffaa00', width=2, dash='dash')
        ))

        fig_band.add_trace(go.Scatter(
            x=df_recent['period'], y=df_recent['value'] / 1000,
            mode='lines', name='Actual Inventories', line=dict(color='#ff9900', width=2.5)
        ))

        fig_band.update_layout(
            template="plotly_dark", height=500, margin=dict(t=10, b=0, l=0, r=0),
            yaxis_title="Millions of Barrels", hovermode="x unified"
        )
        st.plotly_chart(fig_band, use_container_width=True)

    # ---- TAB 2: Year-over-Year Seasonality ----
    with tab2:
        fig_yoy = go.Figure()
        colors_yoy = ['#ff9900', '#00d1ff', '#00ff96', '#ffaa00', '#ad7fff']

        years_to_plot = sorted(df_inv['year'].unique())[-5:]
        for i, yr in enumerate(years_to_plot):
            yr_data = df_inv[df_inv['year'] == yr].sort_values('week')
            is_current = yr == current_year
            fig_yoy.add_trace(go.Scatter(
                x=yr_data['week'], y=yr_data['value'] / 1000,
                mode='lines', name=str(yr),
                line=dict(color=colors_yoy[i % len(colors_yoy)], width=3 if is_current else 1.5),
                opacity=1.0 if is_current else 0.7
            ))

        fig_yoy.update_layout(
            template="plotly_dark", height=500, margin=dict(t=10, b=0, l=0, r=0),
            xaxis_title="Week of Year", yaxis_title="Millions of Barrels", hovermode="x unified"
        )
        st.plotly_chart(fig_yoy, use_container_width=True)

    # ---- TAB 3: Weekly Builds / Draws ----
    with tab3:
        df_bd = df_inv.tail(260)
        fig_bd = go.Figure()
        # Draws (negative change) are bullish/green, builds (positive) are bearish/red
        colors_bd = ['#FF0000' if val > 0 else '#00FF00' for val in df_bd['wow_change']]

        fig_bd.add_trace(go.Bar(
            x=df_bd['period'], y=df_bd['wow_change'] / 1000,
            marker_color=colors_bd,
            hovertemplate="Date: %{x}<br>Change: %{y:.2f}M bbls<extra></extra>"
        ))
        fig_bd.update_layout(
            template="plotly_dark", height=500, margin=dict(t=10, b=0, l=0, r=0),
            yaxis_title="Weekly Change (Millions of Barrels)",
            shapes=[dict(type='line', y0=0, y1=0, x0=df_bd['period'].min(), x1=df_bd['period'].max(), line=dict(color='white', width=1))]
        )
        st.plotly_chart(fig_bd, use_container_width=True)
        st.caption("🟢 Green = Draw (Bullish) | 🔴 Red = Build (Bearish)")

    # ---- TAB 4: WTI Price Overlay ----
    with tab4:
        if df_wti is not None and not df_wti.empty:
            df_inv_plot = df_inv.tail(260)
            df_wti_plot = df_wti.tail(260)

            fig_wti = go.Figure()

            fig_wti.add_trace(go.Scatter(
                x=df_inv_plot['period'], y=df_inv_plot['value'] / 1000,
                mode='lines', name='Commercial Inventories',
                line=dict(color='#ff9900', width=2), yaxis='y'
            ))

            fig_wti.add_trace(go.Scatter(
                x=df_wti_plot['period'], y=df_wti_plot['value'],
                mode='lines', name='WTI Spot ($/bbl)',
                line=dict(color='#00ff96', width=2), yaxis='y2'
            ))

            fig_wti.update_layout(
                template="plotly_dark", height=500, margin=dict(t=10, b=0, l=0, r=0),
                hovermode="x unified",
                yaxis=dict(title="Inventories (M bbls)", side='left', showgrid=False),
                yaxis2=dict(title="WTI ($/bbl)", side='right', overlaying='y', showgrid=False),
            )
            st.plotly_chart(fig_wti, use_container_width=True)
        else:
            st.warning("WTI price data unavailable.")

    # ---- TAB 5: Cushing Storage ----
    with tab5:
        if df_cushing is not None and not df_cushing.empty:
            df_cush_plot = df_cushing.tail(260)

            fig_cush = go.Figure()
            fig_cush.add_trace(go.Scatter(
                x=df_cush_plot['period'], y=df_cush_plot['value'] / 1000,
                mode='lines', name='Cushing Storage',
                line=dict(color='#ad7fff', width=2), fill='tozeroy', fillcolor='rgba(173, 127, 255, 0.1)'
            ))
            fig_cush.update_layout(
                template="plotly_dark", height=500, margin=dict(t=10, b=0, l=0, r=0),
                yaxis_title="Millions of Barrels", hovermode="x unified"
            )
            st.plotly_chart(fig_cush, use_container_width=True)

            # Weekly change bars
            colors_cush = ['#FF0000' if val > 0 else '#00FF00' for val in df_cush_plot['wow_change']]
            fig_cush_flow = go.Figure()
            fig_cush_flow.add_trace(go.Bar(
                x=df_cush_plot['period'], y=df_cush_plot['wow_change'] / 1000,
                marker_color=colors_cush,
                hovertemplate="Date: %{x}<br>Change: %{y:.2f}M bbls<extra></extra>"
            ))
            fig_cush_flow.update_layout(
                template="plotly_dark", height=300, margin=dict(t=10, b=0, l=0, r=0),
                yaxis_title="Weekly Change (M bbls)",
                shapes=[dict(type='line', y0=0, y1=0, x0=df_cush_plot['period'].min(), x1=df_cush_plot['period'].max(), line=dict(color='white', width=1))]
            )
            st.plotly_chart(fig_cush_flow, use_container_width=True)
        else:
            st.warning("Cushing storage data unavailable.")

    # ---- TAB 6: Imports / Exports ----
    with tab6:
        if df_imports is not None and df_exports is not None and not df_imports.empty and not df_exports.empty:
            df_imp_plot = df_imports.tail(260)
            df_exp_plot = df_exports.tail(260)

            # Merge on period for net calculation
            df_trade = pd.merge(
                df_imp_plot[['period', 'value']].rename(columns={'value': 'imports'}),
                df_exp_plot[['period', 'value']].rename(columns={'value': 'exports'}),
                on='period', how='inner'
            )
            df_trade['net_imports'] = df_trade['imports'] - df_trade['exports']

            fig_trade = go.Figure()

            fig_trade.add_trace(go.Scatter(
                x=df_trade['period'], y=df_trade['imports'] / 1000,
                mode='lines', name='Imports', line=dict(color='#00d1ff', width=2)
            ))
            fig_trade.add_trace(go.Scatter(
                x=df_trade['period'], y=df_trade['exports'] / 1000,
                mode='lines', name='Exports', line=dict(color='#ff4b4b', width=2)
            ))
            fig_trade.add_trace(go.Scatter(
                x=df_trade['period'], y=df_trade['net_imports'] / 1000,
                mode='lines', name='Net Imports', line=dict(color='#ffaa00', width=2, dash='dash')
            ))

            fig_trade.add_hline(y=0, line_dash="solid", line_color="white", opacity=0.3)

            fig_trade.update_layout(
                template="plotly_dark", height=500, margin=dict(t=10, b=0, l=0, r=0),
                yaxis_title="Millions of Barrels Per Day", hovermode="x unified"
            )
            st.plotly_chart(fig_trade, use_container_width=True)
        else:
            st.warning("Import/Export data unavailable.")

    # ---- TAB 7: Refinery Utilization ----
    with tab7:
        if df_refinery is not None and not df_refinery.empty:
            df_ref_plot = df_refinery.tail(260)

            fig_ref = go.Figure()
            fig_ref.add_trace(go.Scatter(
                x=df_ref_plot['period'], y=df_ref_plot['value'],
                mode='lines', name='Utilization Rate',
                line=dict(color='#00ff96', width=2), fill='tozeroy', fillcolor='rgba(0, 255, 150, 0.1)'
            ))

            fig_ref.add_hline(y=90, line_dash="dot", line_color="#ffaa00", annotation_text="90% Threshold")

            fig_ref.update_layout(
                template="plotly_dark", height=500, margin=dict(t=10, b=0, l=0, r=0),
                yaxis_title="Utilization Rate (%)", hovermode="x unified",
                yaxis=dict(range=[75, 100])
            )
            st.plotly_chart(fig_ref, use_container_width=True)
        else:
            st.warning("Refinery utilization data unavailable.")

    # ---- TAB 8: Product Inventories (Gasoline & Distillate) ----
    with tab8:
        col_gas, col_dist = st.columns(2)

        with col_gas:
            st.subheader("Gasoline Inventories")
            if df_gasoline is not None and not df_gasoline.empty:
                df_gas_plot = df_gasoline.tail(260)
                gas_latest = df_gas_plot.iloc[-1]
                st.metric("Total Motor Gasoline", f"{gas_latest['value']/1000:.1f}M bbls",
                          f"{gas_latest['wow_change']/1000:+.2f}M (WoW)", delta_color="inverse")

                fig_gas = go.Figure()
                fig_gas.add_trace(go.Scatter(
                    x=df_gas_plot['period'], y=df_gas_plot['value'] / 1000,
                    mode='lines', line=dict(color='#00d1ff', width=2), fill='tozeroy', fillcolor='rgba(0, 209, 255, 0.1)'
                ))
                fig_gas.update_layout(
                    template="plotly_dark", height=350, margin=dict(t=10, b=0, l=0, r=0),
                    yaxis_title="Millions of Barrels", hovermode="x unified"
                )
                st.plotly_chart(fig_gas, use_container_width=True)
            else:
                st.warning("Gasoline data unavailable.")

        with col_dist:
            st.subheader("Distillate Inventories")
            if df_distillate is not None and not df_distillate.empty:
                df_dist_plot = df_distillate.tail(260)
                dist_latest = df_dist_plot.iloc[-1]
                st.metric("Distillate Fuel Oil", f"{dist_latest['value']/1000:.1f}M bbls",
                          f"{dist_latest['wow_change']/1000:+.2f}M (WoW)", delta_color="inverse")

                fig_dist = go.Figure()
                fig_dist.add_trace(go.Scatter(
                    x=df_dist_plot['period'], y=df_dist_plot['value'] / 1000,
                    mode='lines', line=dict(color='#ff4b4b', width=2), fill='tozeroy', fillcolor='rgba(255, 75, 75, 0.1)'
                ))
                fig_dist.update_layout(
                    template="plotly_dark", height=350, margin=dict(t=10, b=0, l=0, r=0),
                    yaxis_title="Millions of Barrels", hovermode="x unified"
                )
                st.plotly_chart(fig_dist, use_container_width=True)
            else:
                st.warning("Distillate data unavailable.")

else:
    st.warning("EIA API Key is missing or invalid. Check your Google Cloud Run Environment Variables.")

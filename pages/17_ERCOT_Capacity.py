import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import requests
import io
import logging
from datetime import date
from src.layout import setup_page, error_boundary, fun_loader

logger = logging.getLogger(__name__)

setup_page("17_ERCOT_Capacity")

st.title("🏗️ ERCOT Capacity Pipeline")
st.markdown("Planned generation additions by fuel type from ERCOT's Interconnection Resource queue.")

# File URL pattern
ERCOT_FILE_BASE = "https://www.ercot.com/files/docs"

# Build URLs for recent months (date_path, label)
MONTHS = [
    ("2026/03/05", "February_2026"),
    ("2026/02/05", "January_2026"),
    ("2025/11/06", "October_2025"),
    ("2025/10/08", "September_2025"),
    ("2025/09/09", "August_2025"),
    ("2025/08/08", "July_2025"),
    ("2025/07/09", "June_2025"),
    ("2025/06/06", "May_2025"),
    ("2025/05/09", "April_2025"),
    ("2025/04/04", "March_2025"),
]

COL_MAP = {8: "INR", 9: "project_name", 10: "county", 11: "projected_cod",
           12: "ia_signed", 13: "fuel", 14: "technology", 15: "capacity_mw",
           16: "year", 17: "financial_security"}

FUEL_COLORS = {
    "Wind": "#00d1ff",
    "Solar": "#ffdd00",
    "Battery": "#ad7fff",
    "Gas": "#ff9900",
}

SHEET_FUEL_MAP = {
    "Wind Chart": "Wind",
    "Solar Chart": "Solar",
    "Battery Chart": "Battery",
    "Gas-Combined Cycle Chart": "Gas",
    "Gas-Other Chart": "Gas",
}


@st.cache_data(ttl=3600)
def fetch_capacity_file(date_path: str, month_label: str, planned_only: bool = False):
    """Download and parse an ERCOT capacity changes Excel file."""
    suffix = f"_PlannedMonthly" if planned_only else ""
    url = f"{ERCOT_FILE_BASE}/{date_path}/Capacity-Changes-by-Fuel-Type-Charts_{month_label}{suffix}.xlsx"

    try:
        r = requests.get(url, timeout=30)
        if r.status_code != 200:
            return pd.DataFrame()

        xls = pd.ExcelFile(io.BytesIO(r.content))
        all_data = []

        for sheet in xls.sheet_names:
            fuel_type = SHEET_FUEL_MAP.get(sheet)
            if not fuel_type:
                continue

            df = pd.read_excel(xls, sheet_name=sheet, header=None)
            # Select only the data columns (8-17) and skip header row
            df = df[[c for c in COL_MAP.keys() if c in df.columns]].copy()
            df.columns = [COL_MAP[c] for c in df.columns]
            df = df.iloc[1:]  # Skip header row
            df = df.dropna(subset=["project_name"])

            # Parse types
            df["capacity_mw"] = pd.to_numeric(df["capacity_mw"], errors="coerce")
            df["projected_cod"] = pd.to_datetime(df["projected_cod"], errors="coerce")
            df["ia_signed"] = pd.to_datetime(df["ia_signed"], errors="coerce")
            df["year"] = pd.to_numeric(df["year"], errors="coerce")
            df["fuel_type"] = fuel_type
            df["technology"] = df["technology"].astype(str)

            # Add tech detail for gas
            if sheet == "Gas-Combined Cycle Chart":
                df["fuel_detail"] = "Gas-CC"
            elif sheet == "Gas-Other Chart":
                df["fuel_detail"] = "Gas-CT/Other"
            else:
                df["fuel_detail"] = fuel_type

            all_data.append(df)

        if all_data:
            return pd.concat(all_data, ignore_index=True).dropna(subset=["capacity_mw"])
        return pd.DataFrame()
    except Exception as e:
        logger.error(f"Failed to fetch ERCOT capacity file: {e}")
        return pd.DataFrame()


# --- SIDEBAR ---
with st.sidebar:
    st.header("Data Selection")
    month_options = {label: path for path, label in MONTHS}
    selected_month = st.selectbox("Report Month", list(month_options.keys()))
    show_planned_only = st.checkbox("Planned Only (Financial Security Posted)", value=False)

# --- FETCH DATA ---
date_path = month_options[selected_month]
with fun_loader("data"):
    df = fetch_capacity_file(date_path, selected_month, show_planned_only)

if df.empty:
    st.error("Failed to load capacity data. The file may not be available for this month.")
    st.stop()

# --- METRICS ---
total_pipeline = df["capacity_mw"].sum()
total_by_fuel = df.groupby("fuel_type")["capacity_mw"].sum().sort_values(ascending=False)

st.subheader(f"Pipeline Summary — {selected_month} Report")
mc = st.columns(5)
mc[0].metric("Total Pipeline", f"{total_pipeline:,.0f} MW")
for i, (fuel, mw) in enumerate(total_by_fuel.items()):
    if i < 4:
        mc[i + 1].metric(fuel, f"{mw:,.0f} MW", f"{mw/total_pipeline*100:.0f}%")

st.divider()

# --- TABS ---
tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "By Fuel Type",
    "Timeline / COD Schedule",
    "Project Details",
    "By County",
    "Financial Security",
    "Month-over-Month Tracking",
])


# ---- TAB 1: By Fuel Type ----
with tab1:
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Capacity by Fuel Type")
        fig_fuel = go.Figure()
        for fuel in total_by_fuel.index:
            fig_fuel.add_trace(go.Bar(
                x=[fuel], y=[total_by_fuel[fuel]],
                name=fuel, marker_color=FUEL_COLORS.get(fuel, "#888"),
                text=[f"{total_by_fuel[fuel]:,.0f} MW"],
                textposition="outside",
            ))
        fig_fuel.update_layout(
            template="plotly_dark", height=400, margin=dict(t=10, b=0, l=0, r=0),
            yaxis_title="Megawatts (MW)", showlegend=False,
        )
        st.plotly_chart(fig_fuel, use_container_width=True)

    with col2:
        st.subheader("Fuel Mix Share")
        fig_pie = go.Figure(data=[go.Pie(
            labels=total_by_fuel.index,
            values=total_by_fuel.values,
            marker_colors=[FUEL_COLORS.get(f, "#888") for f in total_by_fuel.index],
            textinfo="label+percent", hole=0.4,
        )])
        fig_pie.update_layout(
            template="plotly_dark", height=400, margin=dict(t=10, b=0, l=0, r=0),
        )
        st.plotly_chart(fig_pie, use_container_width=True)

    # Detailed breakdown (Gas-CC vs Gas-CT)
    st.subheader("Detailed Technology Breakdown")
    detail_by_tech = df.groupby("fuel_detail")["capacity_mw"].agg(["sum", "count"]).sort_values("sum", ascending=False)
    detail_by_tech.columns = ["Total MW", "# Projects"]
    detail_by_tech["Avg Size (MW)"] = detail_by_tech["Total MW"] / detail_by_tech["# Projects"]
    detail_by_tech = detail_by_tech.round(0)
    st.dataframe(detail_by_tech, use_container_width=True)

    # Project count by fuel
    st.subheader("Number of Projects by Fuel Type")
    proj_count = df.groupby("fuel_type").size().sort_values(ascending=False)
    fig_count = go.Figure()
    fig_count.add_trace(go.Bar(
        x=proj_count.index, y=proj_count.values,
        marker_color=[FUEL_COLORS.get(f, "#888") for f in proj_count.index],
        text=proj_count.values, textposition="outside",
    ))
    fig_count.update_layout(
        template="plotly_dark", height=350, margin=dict(t=10, b=0, l=0, r=0),
        yaxis_title="Number of Projects", showlegend=False,
    )
    st.plotly_chart(fig_count, use_container_width=True)


# ---- TAB 2: Timeline / COD Schedule ----
with tab2:
    st.subheader("Planned Capacity Additions by Year")

    by_year_fuel = df.groupby(["year", "fuel_type"])["capacity_mw"].sum().unstack(fill_value=0)
    by_year_fuel = by_year_fuel.sort_index()

    fig_year = go.Figure()
    for fuel in by_year_fuel.columns:
        fig_year.add_trace(go.Bar(
            x=by_year_fuel.index.astype(str), y=by_year_fuel[fuel],
            name=fuel, marker_color=FUEL_COLORS.get(fuel, "#888"),
        ))

    fig_year.update_layout(
        template="plotly_dark", height=450, margin=dict(t=10, b=0, l=0, r=0),
        barmode="stack", yaxis_title="Megawatts (MW)", hovermode="x unified",
    )
    st.plotly_chart(fig_year, use_container_width=True)

    # Monthly COD timeline for near-term projects
    st.subheader("Monthly COD Schedule (Next 24 Months)")
    df_near = df[df["projected_cod"] <= pd.Timestamp.now() + pd.Timedelta(days=730)].copy()
    df_near["cod_month"] = df_near["projected_cod"].dt.to_period("M").dt.to_timestamp()

    if not df_near.empty:
        monthly = df_near.groupby(["cod_month", "fuel_type"])["capacity_mw"].sum().unstack(fill_value=0)

        fig_monthly = go.Figure()
        for fuel in monthly.columns:
            fig_monthly.add_trace(go.Bar(
                x=monthly.index, y=monthly[fuel],
                name=fuel, marker_color=FUEL_COLORS.get(fuel, "#888"),
            ))

        # Today marker
        fig_monthly.add_vline(x=pd.Timestamp.now().isoformat(), line_dash="dot", line_color="white")

        fig_monthly.update_layout(
            template="plotly_dark", height=450, margin=dict(t=10, b=0, l=0, r=0),
            barmode="stack", yaxis_title="Megawatts (MW)", hovermode="x unified",
        )
        st.plotly_chart(fig_monthly, use_container_width=True)

    # Cumulative additions
    st.subheader("Cumulative Planned Additions")
    df_cum = df.sort_values("projected_cod").copy()
    df_cum["cumulative_mw"] = df_cum["capacity_mw"].cumsum()

    fig_cum = go.Figure()
    for fuel in df_cum["fuel_type"].unique():
        fuel_df = df_cum[df_cum["fuel_type"] == fuel].copy()
        fuel_df["fuel_cum"] = fuel_df["capacity_mw"].cumsum()
        fig_cum.add_trace(go.Scatter(
            x=fuel_df["projected_cod"], y=fuel_df["fuel_cum"],
            mode="lines", name=fuel, line=dict(color=FUEL_COLORS.get(fuel, "#888"), width=2),
        ))

    fig_cum.add_vline(x=pd.Timestamp.now().isoformat(), line_dash="dot", line_color="white")
    fig_cum.update_layout(
        template="plotly_dark", height=400, margin=dict(t=10, b=0, l=0, r=0),
        yaxis_title="Cumulative MW", hovermode="x unified",
    )
    st.plotly_chart(fig_cum, use_container_width=True)


# ---- TAB 3: Project Details ----
with tab3:
    st.subheader("All Projects in Pipeline")

    # Filters
    fc1, fc2, fc3 = st.columns(3)
    with fc1:
        fuel_filter = st.multiselect("Fuel Type", df["fuel_type"].unique().tolist(),
                                      default=df["fuel_type"].unique().tolist())
    with fc2:
        year_options = sorted(df["year"].dropna().unique().astype(int).tolist())
        year_filter = st.multiselect("COD Year", year_options, default=year_options)
    with fc3:
        fs_filter = st.multiselect("Financial Security", ["Yes", "No"],
                                    default=["Yes", "No"])

    df_filtered = df[
        (df["fuel_type"].isin(fuel_filter)) &
        (df["year"].isin(year_filter)) &
        (df["financial_security"].isin(fs_filter))
    ].sort_values("projected_cod")

    st.metric("Filtered Projects", f"{len(df_filtered)} projects, {df_filtered['capacity_mw'].sum():,.0f} MW")

    display_df = df_filtered[["project_name", "fuel_type", "fuel_detail", "capacity_mw",
                               "projected_cod", "county", "financial_security"]].copy()
    display_df["projected_cod"] = display_df["projected_cod"].dt.strftime("%Y-%m-%d")
    display_df["capacity_mw"] = display_df["capacity_mw"].apply(lambda x: f"{x:,.1f}")
    display_df.columns = ["Project", "Fuel", "Technology", "MW", "Projected COD", "County", "Fin. Security"]

    st.dataframe(display_df, use_container_width=True, hide_index=True)

    # Top 20 largest projects
    st.subheader("Top 20 Largest Projects")
    top20 = df_filtered.nlargest(20, "capacity_mw")
    fig_top = go.Figure()
    fig_top.add_trace(go.Bar(
        y=top20["project_name"], x=top20["capacity_mw"],
        orientation="h",
        marker_color=[FUEL_COLORS.get(f, "#888") for f in top20["fuel_type"]],
        text=[f"{mw:,.0f} MW" for mw in top20["capacity_mw"]],
        textposition="outside",
    ))
    fig_top.update_layout(
        template="plotly_dark", height=max(400, len(top20) * 30),
        margin=dict(t=10, b=0, l=200, r=50),
        xaxis_title="Capacity (MW)", yaxis=dict(autorange="reversed"),
    )
    st.plotly_chart(fig_top, use_container_width=True)


# ---- TAB 4: By County ----
with tab4:
    st.subheader("Capacity by County")

    by_county = df.groupby("county")["capacity_mw"].agg(["sum", "count"]).sort_values("sum", ascending=False)
    by_county.columns = ["total_mw", "projects"]
    top_counties = by_county.head(20)

    fig_county = go.Figure()
    fig_county.add_trace(go.Bar(
        y=top_counties.index, x=top_counties["total_mw"],
        orientation="h", marker_color="#00d1ff",
        text=[f"{mw:,.0f} MW ({n} projects)" for mw, n in zip(top_counties["total_mw"], top_counties["projects"])],
        textposition="outside",
    ))
    fig_county.update_layout(
        template="plotly_dark", height=max(400, len(top_counties) * 30),
        margin=dict(t=10, b=0, l=120, r=80),
        xaxis_title="Capacity (MW)", yaxis=dict(autorange="reversed"),
    )
    st.plotly_chart(fig_county, use_container_width=True)

    # County fuel mix
    st.subheader("County Fuel Mix (Top 15)")
    top15_counties = by_county.head(15).index.tolist()
    county_fuel = df[df["county"].isin(top15_counties)].groupby(["county", "fuel_type"])["capacity_mw"].sum().unstack(fill_value=0)
    county_fuel = county_fuel.loc[top15_counties]

    fig_cf = go.Figure()
    for fuel in county_fuel.columns:
        fig_cf.add_trace(go.Bar(
            y=county_fuel.index, x=county_fuel[fuel],
            name=fuel, orientation="h",
            marker_color=FUEL_COLORS.get(fuel, "#888"),
        ))
    fig_cf.update_layout(
        template="plotly_dark", height=max(400, len(top15_counties) * 30),
        margin=dict(t=10, b=0, l=120, r=50),
        barmode="stack", xaxis_title="Capacity (MW)",
        yaxis=dict(autorange="reversed"),
    )
    st.plotly_chart(fig_cf, use_container_width=True)


# ---- TAB 5: Financial Security ----
with tab5:
    st.subheader("Financial Security Status")
    st.markdown("Projects that have posted financial security are more likely to be built on schedule.")

    fs_summary = df.groupby(["fuel_type", "financial_security"])["capacity_mw"].sum().unstack(fill_value=0)

    # Metrics
    total_secured = df[df["financial_security"] == "Yes"]["capacity_mw"].sum()
    total_unsecured = df[df["financial_security"] == "No"]["capacity_mw"].sum()
    secured_pct = total_secured / total_pipeline * 100 if total_pipeline > 0 else 0

    fc1, fc2, fc3 = st.columns(3)
    fc1.metric("Secured Pipeline", f"{total_secured:,.0f} MW", f"{secured_pct:.0f}%")
    fc2.metric("Unsecured Pipeline", f"{total_unsecured:,.0f} MW")
    fc3.metric("Security Rate", f"{secured_pct:.1f}%")

    # Grouped bar chart
    fig_fs = go.Figure()
    if "Yes" in fs_summary.columns:
        fig_fs.add_trace(go.Bar(
            x=fs_summary.index, y=fs_summary.get("Yes", 0),
            name="Secured", marker_color="#00ff96",
        ))
    if "No" in fs_summary.columns:
        fig_fs.add_trace(go.Bar(
            x=fs_summary.index, y=fs_summary.get("No", 0),
            name="Unsecured", marker_color="#ff4b4b",
        ))
    fig_fs.update_layout(
        template="plotly_dark", height=400, margin=dict(t=10, b=0, l=0, r=0),
        barmode="group", yaxis_title="Megawatts (MW)",
    )
    st.plotly_chart(fig_fs, use_container_width=True)

    # Security rate by year
    st.subheader("Financial Security Rate by COD Year")
    fs_year = df.groupby(["year", "financial_security"])["capacity_mw"].sum().unstack(fill_value=0)
    if "Yes" in fs_year.columns and "No" in fs_year.columns:
        fs_year["rate"] = fs_year["Yes"] / (fs_year["Yes"] + fs_year["No"]) * 100
    elif "Yes" in fs_year.columns:
        fs_year["rate"] = 100.0
    else:
        fs_year["rate"] = 0.0

    fig_rate = go.Figure()
    colors_rate = ["#00ff96" if v > 50 else "#ffaa00" if v > 25 else "#ff4b4b" for v in fs_year["rate"]]
    fig_rate.add_trace(go.Bar(
        x=fs_year.index.astype(str), y=fs_year["rate"],
        marker_color=colors_rate,
        text=[f"{v:.0f}%" for v in fs_year["rate"]],
        textposition="outside",
    ))
    fig_rate.add_hline(y=50, line_dash="dot", line_color="white")
    fig_rate.update_layout(
        template="plotly_dark", height=350, margin=dict(t=10, b=0, l=0, r=0),
        yaxis_title="Security Rate (%)", yaxis=dict(range=[0, 105]),
    )
    st.plotly_chart(fig_rate, use_container_width=True)


# ---- TAB 6: Month-over-Month Tracking ----
with tab6:
    st.subheader("Pipeline Changes Over Time")
    st.markdown("Track how the ERCOT capacity pipeline evolves month to month — total MW, fuel mix shifts, and individual project changes.")

    # Load all available months
    all_months_data = {}
    month_labels_ordered = []

    with fun_loader("data"):
        for date_path_m, label_m in MONTHS:
            df_m = fetch_capacity_file(date_path_m, label_m, planned_only=False)
            if not df_m.empty:
                all_months_data[label_m] = df_m
                month_labels_ordered.append(label_m)

    if len(all_months_data) < 2:
        st.warning("Need at least 2 months of data for comparison.")
    else:
        # --- Total Pipeline Over Time ---
        st.subheader("Total Pipeline MW Over Time")
        monthly_totals = []
        for label_m in month_labels_ordered:
            df_m = all_months_data[label_m]
            row = {"month": label_m, "total_mw": df_m["capacity_mw"].sum(), "projects": len(df_m)}
            for fuel in ["Wind", "Solar", "Battery", "Gas"]:
                row[fuel] = df_m[df_m["fuel_type"] == fuel]["capacity_mw"].sum()
            monthly_totals.append(row)

        df_totals = pd.DataFrame(monthly_totals)
        # Reverse so oldest is first
        df_totals = df_totals.iloc[::-1].reset_index(drop=True)

        fig_total_trend = go.Figure()
        fig_total_trend.add_trace(go.Scatter(
            x=df_totals["month"], y=df_totals["total_mw"],
            mode="lines+markers", name="Total Pipeline",
            line=dict(color="white", width=3), marker=dict(size=8),
        ))
        fig_total_trend.update_layout(
            template="plotly_dark", height=350, margin=dict(t=10, b=0, l=0, r=0),
            yaxis_title="Total Pipeline (MW)", hovermode="x unified",
        )
        st.plotly_chart(fig_total_trend, use_container_width=True)

        # MoM change
        df_totals["mom_change"] = df_totals["total_mw"].diff()
        df_totals["mom_pct"] = df_totals["total_mw"].pct_change() * 100

        if len(df_totals) > 1:
            latest_change = df_totals["mom_change"].iloc[-1]
            latest_pct = df_totals["mom_pct"].iloc[-1]
            tc1, tc2, tc3 = st.columns(3)
            tc1.metric("Latest Pipeline", f"{df_totals['total_mw'].iloc[-1]:,.0f} MW")
            tc2.metric("MoM Change", f"{latest_change:+,.0f} MW" if pd.notna(latest_change) else "N/A")
            tc3.metric("MoM % Change", f"{latest_pct:+.1f}%" if pd.notna(latest_pct) else "N/A",
                        delta_color="normal")

        # --- Fuel Mix Over Time (Stacked Area) ---
        st.subheader("Fuel Mix Evolution")
        fig_fuel_trend = go.Figure()
        for fuel in ["Gas", "Battery", "Solar", "Wind"]:
            if fuel in df_totals.columns:
                fig_fuel_trend.add_trace(go.Scatter(
                    x=df_totals["month"], y=df_totals[fuel],
                    mode="lines", name=fuel, stackgroup="fuel",
                    line=dict(width=0.5, color=FUEL_COLORS.get(fuel, "#888")),
                ))

        fig_fuel_trend.update_layout(
            template="plotly_dark", height=400, margin=dict(t=10, b=0, l=0, r=0),
            yaxis_title="Capacity (MW)", hovermode="x unified",
        )
        st.plotly_chart(fig_fuel_trend, use_container_width=True)

        # --- Fuel MW change month over month ---
        st.subheader("Monthly Change by Fuel Type")
        fuel_changes = df_totals[["month", "Wind", "Solar", "Battery", "Gas"]].copy()
        for fuel in ["Wind", "Solar", "Battery", "Gas"]:
            fuel_changes[f"{fuel}_chg"] = fuel_changes[fuel].diff()

        fig_fuel_chg = go.Figure()
        for fuel in ["Wind", "Solar", "Battery", "Gas"]:
            col = f"{fuel}_chg"
            fig_fuel_chg.add_trace(go.Bar(
                x=fuel_changes["month"], y=fuel_changes[col],
                name=fuel, marker_color=FUEL_COLORS.get(fuel, "#888"),
            ))

        fig_fuel_chg.add_hline(y=0, line_color="white", line_width=1)
        fig_fuel_chg.update_layout(
            template="plotly_dark", height=400, margin=dict(t=10, b=0, l=0, r=0),
            barmode="group", yaxis_title="MoM Change (MW)", hovermode="x unified",
        )
        st.plotly_chart(fig_fuel_chg, use_container_width=True)

        # --- Project-Level Diff (Compare two months) ---
        st.subheader("Project-Level Comparison")
        dc1, dc2 = st.columns(2)
        with dc1:
            compare_from = st.selectbox("From Month", month_labels_ordered[1:], index=0)
        with dc2:
            compare_to = st.selectbox("To Month", month_labels_ordered,
                                       index=0 if month_labels_ordered[0] != compare_from else 1)

        if compare_from != compare_to and compare_from in all_months_data and compare_to in all_months_data:
            df_from = all_months_data[compare_from]
            df_to = all_months_data[compare_to]

            # Match on INR (project ID)
            from_inrs = set(df_from["INR"].dropna())
            to_inrs = set(df_to["INR"].dropna())

            added_inrs = to_inrs - from_inrs
            removed_inrs = from_inrs - to_inrs
            common_inrs = from_inrs & to_inrs

            df_added = df_to[df_to["INR"].isin(added_inrs)]
            df_removed = df_from[df_from["INR"].isin(removed_inrs)]

            # Check for COD changes or capacity changes in common projects
            df_from_common = df_from[df_from["INR"].isin(common_inrs)].set_index("INR")
            df_to_common = df_to[df_to["INR"].isin(common_inrs)].set_index("INR")

            cod_changes = []
            cap_changes = []
            for inr in common_inrs:
                if inr in df_from_common.index and inr in df_to_common.index:
                    from_row = df_from_common.loc[inr]
                    to_row = df_to_common.loc[inr]
                    # Handle duplicate INRs
                    if isinstance(from_row, pd.DataFrame):
                        from_row = from_row.iloc[0]
                    if isinstance(to_row, pd.DataFrame):
                        to_row = to_row.iloc[0]

                    if from_row["projected_cod"] != to_row["projected_cod"]:
                        cod_changes.append({
                            "Project": to_row["project_name"],
                            "Fuel": to_row["fuel_type"],
                            "MW": to_row["capacity_mw"],
                            "Old COD": from_row["projected_cod"].strftime("%Y-%m-%d") if pd.notna(from_row["projected_cod"]) else "?",
                            "New COD": to_row["projected_cod"].strftime("%Y-%m-%d") if pd.notna(to_row["projected_cod"]) else "?",
                        })
                    if from_row["capacity_mw"] != to_row["capacity_mw"]:
                        cap_changes.append({
                            "Project": to_row["project_name"],
                            "Fuel": to_row["fuel_type"],
                            "Old MW": from_row["capacity_mw"],
                            "New MW": to_row["capacity_mw"],
                            "Change": to_row["capacity_mw"] - from_row["capacity_mw"],
                        })

            # Summary metrics
            sm1, sm2, sm3, sm4 = st.columns(4)
            sm1.metric("Projects Added", f"{len(added_inrs)}",
                       f"+{df_added['capacity_mw'].sum():,.0f} MW" if not df_added.empty else "0 MW")
            sm2.metric("Projects Removed", f"{len(removed_inrs)}",
                       f"-{df_removed['capacity_mw'].sum():,.0f} MW" if not df_removed.empty else "0 MW",
                       delta_color="inverse")
            sm3.metric("COD Changes", f"{len(cod_changes)}")
            sm4.metric("Capacity Revisions", f"{len(cap_changes)}")

            # Added projects
            if not df_added.empty:
                st.markdown(f"**New Projects Added ({compare_from} → {compare_to})**")
                disp_added = df_added[["project_name", "fuel_type", "capacity_mw", "projected_cod", "county"]].copy()
                disp_added["projected_cod"] = disp_added["projected_cod"].dt.strftime("%Y-%m-%d")
                disp_added["capacity_mw"] = disp_added["capacity_mw"].apply(lambda x: f"{x:,.1f}")
                disp_added.columns = ["Project", "Fuel", "MW", "COD", "County"]
                st.dataframe(disp_added, use_container_width=True, hide_index=True)

            # Removed projects
            if not df_removed.empty:
                st.markdown(f"**Projects Removed ({compare_from} → {compare_to})**")
                disp_removed = df_removed[["project_name", "fuel_type", "capacity_mw", "projected_cod", "county"]].copy()
                disp_removed["projected_cod"] = disp_removed["projected_cod"].dt.strftime("%Y-%m-%d")
                disp_removed["capacity_mw"] = disp_removed["capacity_mw"].apply(lambda x: f"{x:,.1f}")
                disp_removed.columns = ["Project", "Fuel", "MW", "COD", "County"]
                st.dataframe(disp_removed, use_container_width=True, hide_index=True)

            # COD changes
            if cod_changes:
                st.markdown(f"**COD Schedule Changes ({compare_from} → {compare_to})**")
                st.dataframe(pd.DataFrame(cod_changes), use_container_width=True, hide_index=True)

            # Capacity revisions
            if cap_changes:
                st.markdown(f"**Capacity Revisions ({compare_from} → {compare_to})**")
                df_cap_chg = pd.DataFrame(cap_changes)
                df_cap_chg["Old MW"] = df_cap_chg["Old MW"].apply(lambda x: f"{x:,.1f}")
                df_cap_chg["New MW"] = df_cap_chg["New MW"].apply(lambda x: f"{x:,.1f}")
                df_cap_chg["Change"] = df_cap_chg["Change"].apply(lambda x: f"{x:+,.1f}")
                st.dataframe(df_cap_chg, use_container_width=True, hide_index=True)

            if not added_inrs and not removed_inrs and not cod_changes and not cap_changes:
                st.success("No changes detected between these two months.")

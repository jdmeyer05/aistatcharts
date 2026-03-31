"""
Shared sector analysis template.

Usage:
    from src.sector_analysis import SectorConfig, render_sector_page

    config = SectorConfig(
        page_id="25_Financials_Sector",
        title="Financials Sector Analysis",
        subtitle="Top 10 US financial companies — ...",
        etf="XLF",
        companies={"JPM": "JPMorgan Chase", ...},
        subsectors={"Banks": ["JPM", "BAC", "WFC", "C"], ...},
        guidance_snapshot={"date": "...", "data": [...]},
        macro_overlay={"fred_series": "DFF", "label": "Fed Funds Rate (%)"},
        factor_proxies=["SPY", "XLF", "TLT", "UUP"],
        cot_commodities=None,  # or [("Crude Oil", "crude_oil"), ...]
    )
    render_sector_page(config)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from src.edgar import (
    fetch_sector_financials,
    fetch_sector_analyst_estimates,
    fetch_sector_revenue_history,
    fetch_sector_capex,
    fetch_sector_capex_history,
    fetch_sector_margin_history,
    fetch_sector_cashflow,
)
from src.market_data import (
    fetch_energy_valuation_data as fetch_valuation_data,
    fetch_energy_earnings_surprises as fetch_earnings_surprises,
    fetch_energy_price_history as fetch_price_history,
    fetch_fred_series,
    fetch_cftc_cot,
    fetch_momentum_data,
    fetch_eps_revisions,
    fetch_insider_summary,
)
from src.layout import error_boundary

logger = logging.getLogger(__name__)

COLOR_CYCLE = ["#00d1ff", "#ffaa00", "#ff6b6b", "#00ff88", "#ff00ff",
               "#88ccff", "#ffcc00", "#ff8866", "#66ffcc", "#cc88ff"]
PLOTLY_NOBAR = {"displayModeBar": False}


@dataclass
class SectorConfig:
    page_id: str
    title: str
    subtitle: str
    etf: str
    companies: dict[str, str]
    subsectors: dict[str, list[str]]
    guidance_snapshot: dict
    # Macro overlay for Tab 7
    macro_overlay: dict = field(default_factory=lambda: {
        "fred_series": "DCOILWTICO",
        "label": "WTI Crude ($/bbl)",
    })
    # Factor ETFs for regression in Tab 5
    factor_proxies: list[str] = field(default_factory=lambda: ["SPY", "USO", "UUP", "TLT"])
    # CFTC COT commodities for Tab 7 — list of (display_name, cot_key) or None
    cot_commodities: list[tuple[str, str]] | None = None
    # Subsector color mapping — auto-generated if not provided
    subsector_colors: dict[str, str] | None = None
    # Tab-specific explainer overrides — keys are tab names
    tab_explainers: dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        if self.subsector_colors is None:
            colors = ["#00d1ff", "#00ff88", "#ffaa00", "#ff6b6b", "#ff00ff",
                      "#88ccff", "#ffcc00", "#ff8866", "#66ffcc", "#cc88ff"]
            self.subsector_colors = {
                name: colors[i % len(colors)]
                for i, name in enumerate(self.subsectors)
            }

    @property
    def tickers(self) -> list[str]:
        return list(self.companies.keys())

    @property
    def ticker_subsector(self) -> dict[str, str]:
        return {t: ss for ss, tks in self.subsectors.items() for t in tks}

    @property
    def session_key(self) -> str:
        return f"{self.etf.lower()}_loaded"

    @property
    def key_prefix(self) -> str:
        return self.etf.lower()


def render_sector_page(cfg: SectorConfig):
    """Render the full 8-tab sector analysis page."""
    st.title(cfg.title)
    st.markdown(cfg.subtitle)

    if st.button(f"Load {cfg.etf} Data", type="primary", key=f"load_{cfg.key_prefix}"):
        st.session_state[cfg.session_key] = True

    if not st.session_state.get(cfg.session_key):
        st.info(f"Click **Load {cfg.etf} Data** to fetch financials, estimates, and market data for all {len(cfg.companies)} companies.")
        st.stop()

    # Load core data
    with st.spinner("Loading sector data..."):
        fin_df = fetch_sector_financials(cfg.companies)
        forecasts = fetch_sector_analyst_estimates(cfg.companies)
        rev_hist = fetch_sector_revenue_history(cfg.companies)

    if fin_df.empty:
        st.error("Failed to fetch company financials.")
        st.stop()
    if not rev_hist.empty:
        rev_hist["date"] = pd.to_datetime(rev_hist["date"])
        rev_hist = rev_hist[rev_hist["date"] >= "2024-01-01"].copy()
        rev_hist["q_label"] = rev_hist["date"].dt.strftime("%Y-Q") + ((rev_hist["date"].dt.month - 1) // 3 + 1).astype(str)

    # Metrics header
    avg_margin = fin_df["net_margin"].mean()
    avg_roe = fin_df["roe"].mean()
    total_rev = fin_df["revenue"].sum()
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Companies", len(fin_df))
    m2.metric("Combined Revenue", f"${total_rev / 1e12:.1f}T" if pd.notna(total_rev) else "N/A")
    m3.metric("Avg Net Margin", f"{avg_margin:.1f}%" if pd.notna(avg_margin) else "N/A")
    m4.metric("Avg ROE", f"{avg_roe:.1f}%" if pd.notna(avg_roe) else "N/A")

    # Sub-tabs
    tab_overview, tab_capex, tab_valuation, tab_alpha, tab_risk, tab_guidance, tab_market, tab_pairs = st.tabs([
        "Overview & Revenue",
        "CapEx Analysis",
        "Valuation & Returns",
        "Alpha Signals",
        "Risk Analytics",
        "Guidance & Estimates",
        "Market & Positioning",
        "Pairs & Correlation",
    ])

    kp = cfg.key_prefix  # short alias for session state keys

    # ═══════════════════════════════════════════════
    # TAB 1: OVERVIEW & REVENUE
    # ═══════════════════════════════════════════════
    with tab_overview, error_boundary("Overview"):
        _render_overview_tab(cfg, fin_df, rev_hist, forecasts, kp)

    # ═══════════════════════════════════════════════
    # TAB 2: CAPEX ANALYSIS
    # ═══════════════════════════════════════════════
    with tab_capex, error_boundary("CapEx"):
        _render_capex_tab(cfg, rev_hist, kp)

    # ═══════════════════════════════════════════════
    # TAB 3: VALUATION & RETURNS
    # ═══════════════════════════════════════════════
    with tab_valuation, error_boundary("Valuation"):
        _render_valuation_tab(cfg, kp)

    # ═══════════════════════════════════════════════
    # TAB 4: ALPHA SIGNALS
    # ═══════════════════════════════════════════════
    with tab_alpha, error_boundary("Alpha Signals"):
        _render_alpha_tab(cfg, kp)

    # ═══════════════════════════════════════════════
    # TAB 5: RISK ANALYTICS
    # ═══════════════════════════════════════════════
    with tab_risk, error_boundary("Risk Analytics"):
        _render_risk_tab(cfg, kp)

    # ═══════════════════════════════════════════════
    # TAB 6: GUIDANCE & ESTIMATES
    # ═══════════════════════════════════════════════
    with tab_guidance, error_boundary("Guidance"):
        _render_guidance_tab(cfg, kp)

    # ═══════════════════════════════════════════════
    # TAB 7: MARKET & POSITIONING
    # ═══════════════════════════════════════════════
    with tab_market, error_boundary("Market"):
        _render_market_tab(cfg, rev_hist, kp)

    # ═══════════════════════════════════════════════
    # TAB 8: PAIRS & CORRELATION
    # ═══════════════════════════════════════════════
    with tab_pairs, error_boundary("Pairs & Correlation"):
        _render_pairs_tab(cfg, kp)


# ─────────────────────────────────────────────────
# TAB 1: OVERVIEW & REVENUE
# ─────────────────────────────────────────────────

def _render_overview_tab(cfg: SectorConfig, fin_df, rev_hist, forecasts, kp):
    # Revenue ranking
    rev_sorted = fin_df.dropna(subset=["revenue"]).sort_values("revenue", ascending=True)
    if not rev_sorted.empty:
        fig_rev = go.Figure()
        fig_rev.add_trace(go.Bar(
            y=rev_sorted["ticker"], x=rev_sorted["revenue"] / 1e9,
            orientation="h", marker_color="#00d1ff",
            text=[f"${v / 1e9:,.0f}B" for v in rev_sorted["revenue"]],
            textposition="outside",
            hovertemplate="%{y}: $%{x:,.0f}B<extra></extra>",
        ))
        fig_rev.update_layout(template="plotly_dark", height=400,
                              title="Annual Revenue ($B)", xaxis_title="Revenue ($B)",
                              margin=dict(l=0, r=80, t=40, b=0))
        st.plotly_chart(fig_rev, use_container_width=True, config=PLOTLY_NOBAR)

    # Profitability: Margin + ROE
    col_margin, col_roe = st.columns(2)
    with col_margin:
        mg = fin_df.dropna(subset=["net_margin"]).sort_values("net_margin", ascending=True)
        if not mg.empty:
            fig_mg = go.Figure()
            fig_mg.add_trace(go.Bar(
                y=mg["ticker"], x=mg["net_margin"], orientation="h",
                marker_color=["#ff6b6b" if v < 0 else "#00d1ff" for v in mg["net_margin"]],
                text=[f"{v:.1f}%" for v in mg["net_margin"]], textposition="outside",
            ))
            fig_mg.update_layout(template="plotly_dark", height=380, title="Net Margin (%)",
                                xaxis_title="%", margin=dict(l=0, r=60, t=40, b=0))
            st.plotly_chart(fig_mg, use_container_width=True, config=PLOTLY_NOBAR)

    with col_roe:
        roe = fin_df.dropna(subset=["roe"]).sort_values("roe", ascending=True)
        if not roe.empty:
            fig_roe = go.Figure()
            fig_roe.add_trace(go.Bar(
                y=roe["ticker"], x=roe["roe"], orientation="h", marker_color="#ffaa00",
                text=[f"{v:.1f}%" for v in roe["roe"]], textposition="outside",
            ))
            fig_roe.update_layout(template="plotly_dark", height=380, title="Return on Equity (%)",
                                xaxis_title="%", margin=dict(l=0, r=60, t=40, b=0))
            st.plotly_chart(fig_roe, use_container_width=True, config=PLOTLY_NOBAR)

    # Quarterly revenue trend
    if not rev_hist.empty:
        rev_mode = st.radio("Revenue view", ["Absolute ($B)", "Indexed (Q1 2024 = 100)"],
                            horizontal=True, key=f"{kp}_rev_mode")

        fig_trend = go.Figure()
        ticker_colors = {}
        for ci, ticker in enumerate(sorted(rev_hist["ticker"].unique())):
            sub = rev_hist[rev_hist["ticker"] == ticker].sort_values("date")
            if sub.empty:
                continue
            color = COLOR_CYCLE[ci % len(COLOR_CYCLE)]
            ticker_colors[ticker] = color
            if rev_mode == "Absolute ($B)":
                y_vals = sub["revenue"] / 1e9
            else:
                base = sub["revenue"].iloc[0]
                y_vals = (sub["revenue"] / base * 100) if base and base > 0 else sub["revenue"] * 0
            fig_trend.add_trace(go.Scatter(
                x=sub["q_label"], y=y_vals, mode="lines+markers", name=ticker,
                line=dict(width=2, color=color), legendgroup=ticker,
            ))

        # Projections
        if not forecasts.empty and rev_mode == "Absolute ($B)":
            for _, frow in forecasts.iterrows():
                t = frow["ticker"]
                rev_q = frow.get("rev_est_q")
                if pd.notna(rev_q) and rev_q and t in ticker_colors:
                    sub = rev_hist[rev_hist["ticker"] == t].sort_values("date")
                    if sub.empty:
                        continue
                    fig_trend.add_trace(go.Scatter(
                        x=[sub["q_label"].iloc[-1], "2026-Q1 (est)"],
                        y=[sub["revenue"].iloc[-1] / 1e9, rev_q / 1e9],
                        mode="lines+markers",
                        line=dict(width=2, color=ticker_colors[t], dash="dot"),
                        marker=dict(size=10, symbol="star"),
                        legendgroup=t, showlegend=False,
                    ))

        if rev_mode == "Indexed (Q1 2024 = 100)":
            fig_trend.add_hline(y=100, line_dash="dash", line_color="#555", annotation_text="Baseline")
        y_title = "Revenue ($B)" if rev_mode == "Absolute ($B)" else "Indexed (Q1 2024 = 100)"
        fig_trend.update_layout(
            template="plotly_dark", height=450,
            title=f"Quarterly Revenue — {rev_mode}" + (" (★ = estimate)" if rev_mode == "Absolute ($B)" else ""),
            yaxis_title=y_title, legend=dict(orientation="h", y=-0.15),
            margin=dict(l=0, r=0, t=40, b=0),
        )
        st.plotly_chart(fig_trend, use_container_width=True, config=PLOTLY_NOBAR)

    # Revenue Analytics
    st.markdown("---")
    st.subheader("Revenue Analytics")

    if not rev_hist.empty:
        # Revenue growth rates (QoQ)
        growth_rows = []
        for ticker in sorted(rev_hist["ticker"].unique()):
            sub = rev_hist[rev_hist["ticker"] == ticker].sort_values("date")
            if len(sub) < 2:
                continue
            sub = sub.copy()
            sub["qoq"] = sub["revenue"].pct_change() * 100
            sub["yoy"] = sub["revenue"].pct_change(periods=4) * 100 if len(sub) >= 5 else np.nan
            for _, r in sub.iterrows():
                growth_rows.append({
                    "ticker": ticker, "q_label": r["q_label"],
                    "qoq": r["qoq"], "yoy": r.get("yoy"),
                })

        if growth_rows:
            gdf = pd.DataFrame(growth_rows)

            gr_c1, gr_c2 = st.columns(2)
            with gr_c1:
                latest_g = gdf.groupby("ticker").last().reset_index().dropna(subset=["qoq"])
                latest_g = latest_g.sort_values("qoq", ascending=True)
                fig_qoq = go.Figure()
                fig_qoq.add_trace(go.Bar(
                    y=latest_g["ticker"], x=latest_g["qoq"], orientation="h",
                    marker_color=["#00d1ff" if v >= 0 else "#ff6b6b" for v in latest_g["qoq"]],
                    text=[f"{v:+.1f}%" for v in latest_g["qoq"]], textposition="outside",
                ))
                fig_qoq.add_vline(x=0, line_dash="dash", line_color="#555")
                fig_qoq.update_layout(template="plotly_dark", height=380,
                                      title="Latest QoQ Revenue Growth (%)",
                                      xaxis_title="% Change", margin=dict(l=0, r=60, t=40, b=0))
                st.plotly_chart(fig_qoq, use_container_width=True, config=PLOTLY_NOBAR)

            with gr_c2:
                latest_yoy = gdf.groupby("ticker").last().reset_index().dropna(subset=["yoy"])
                if not latest_yoy.empty:
                    latest_yoy = latest_yoy.sort_values("yoy", ascending=True)
                    fig_yoy = go.Figure()
                    fig_yoy.add_trace(go.Bar(
                        y=latest_yoy["ticker"], x=latest_yoy["yoy"], orientation="h",
                        marker_color=["#00d1ff" if v >= 0 else "#ff6b6b" for v in latest_yoy["yoy"]],
                        text=[f"{v:+.1f}%" for v in latest_yoy["yoy"]], textposition="outside",
                    ))
                    fig_yoy.add_vline(x=0, line_dash="dash", line_color="#555")
                    fig_yoy.update_layout(template="plotly_dark", height=380,
                                          title="Latest YoY Revenue Growth (%)",
                                          xaxis_title="% Change", margin=dict(l=0, r=60, t=40, b=0))
                    st.plotly_chart(fig_yoy, use_container_width=True, config=PLOTLY_NOBAR)

            # Revenue QoQ growth trend
            fig_gtrend = go.Figure()
            for ci, ticker in enumerate(sorted(gdf["ticker"].unique())):
                sub = gdf[gdf["ticker"] == ticker].dropna(subset=["qoq"])
                if not sub.empty:
                    fig_gtrend.add_trace(go.Scatter(
                        x=sub["q_label"], y=sub["qoq"], mode="lines+markers", name=ticker,
                        line=dict(width=2, color=COLOR_CYCLE[ci % len(COLOR_CYCLE)]),
                    ))
            fig_gtrend.add_hline(y=0, line_dash="dash", line_color="#555")
            fig_gtrend.update_layout(template="plotly_dark", height=380,
                                     title="QoQ Revenue Growth Trend (%)",
                                     yaxis_title="% Change",
                                     legend=dict(orientation="h", y=-0.15),
                                     margin=dict(l=0, r=0, t=40, b=0))
            st.plotly_chart(fig_gtrend, use_container_width=True, config=PLOTLY_NOBAR)

        # Revenue volatility
        vol_rows = []
        for ticker in sorted(rev_hist["ticker"].unique()):
            sub = rev_hist[rev_hist["ticker"] == ticker]
            if len(sub) >= 3:
                cv = sub["revenue"].std() / sub["revenue"].mean() * 100 if sub["revenue"].mean() > 0 else 0
                vol_rows.append({"ticker": ticker, "cv": cv})
        if vol_rows:
            vdf = pd.DataFrame(vol_rows).sort_values("cv", ascending=True)
            fig_vol = go.Figure()
            fig_vol.add_trace(go.Bar(
                y=vdf["ticker"], x=vdf["cv"], orientation="h",
                marker_color=["#00d1ff" if v < 10 else "#ffaa00" if v < 20 else "#ff6b6b" for v in vdf["cv"]],
                text=[f"{v:.1f}%" for v in vdf["cv"]], textposition="outside",
            ))
            fig_vol.update_layout(template="plotly_dark", height=360,
                                  title="Revenue Volatility (Coefficient of Variation %)",
                                  xaxis_title="CV %", margin=dict(l=0, r=60, t=40, b=0))
            st.plotly_chart(fig_vol, use_container_width=True, config=PLOTLY_NOBAR)

    # Margin Trend
    st.markdown("---")
    st.subheader("Margin & Earnings Quality")

    with st.spinner("Loading margin history..."):
        margin_hist = fetch_sector_margin_history(cfg.companies)

    if not margin_hist.empty and "revenue" in margin_hist.columns and "net_income" in margin_hist.columns:
        margin_hist["net_margin"] = margin_hist["net_income"] / margin_hist["revenue"] * 100
        margin_hist["q_label"] = margin_hist["date"].dt.strftime("%Y-Q") + ((margin_hist["date"].dt.month - 1) // 3 + 1).astype(str)

        fig_mt = go.Figure()
        for ci, ticker in enumerate(sorted(margin_hist["ticker"].unique())):
            sub = margin_hist[margin_hist["ticker"] == ticker].sort_values("date")
            if sub["net_margin"].notna().any():
                fig_mt.add_trace(go.Scatter(
                    x=sub["q_label"], y=sub["net_margin"], mode="lines+markers", name=ticker,
                    line=dict(width=2, color=COLOR_CYCLE[ci % len(COLOR_CYCLE)]),
                ))
        fig_mt.add_hline(y=0, line_dash="dash", line_color="#555")
        fig_mt.update_layout(template="plotly_dark", height=400,
                             title="Net Margin Trend (%)",
                             yaxis_title="Net Margin %",
                             legend=dict(orientation="h", y=-0.15),
                             margin=dict(l=0, r=0, t=40, b=0))
        st.plotly_chart(fig_mt, use_container_width=True, config=PLOTLY_NOBAR)

        # Operating leverage
        if "operating_income" in margin_hist.columns:
            ol_rows = []
            for ticker in sorted(margin_hist["ticker"].unique()):
                sub = margin_hist[margin_hist["ticker"] == ticker].sort_values("date")
                if len(sub) >= 2 and sub["operating_income"].notna().any() and sub["revenue"].notna().any():
                    rev_chg = sub["revenue"].pct_change()
                    oi_chg = sub["operating_income"].pct_change()
                    valid = (rev_chg.abs() > 0.01)
                    if valid.any():
                        ol = (oi_chg[valid] / rev_chg[valid]).median()
                        ol_rows.append({"ticker": ticker, "op_leverage": ol})
            if ol_rows:
                oldf = pd.DataFrame(ol_rows).sort_values("op_leverage", ascending=True)
                oldf = oldf[oldf["op_leverage"].between(-10, 10)]
                fig_ol = go.Figure()
                fig_ol.add_trace(go.Bar(
                    y=oldf["ticker"], x=oldf["op_leverage"], orientation="h",
                    marker_color=["#ff6b6b" if abs(v) > 3 else "#ffaa00" if abs(v) > 1.5 else "#00d1ff"
                                  for v in oldf["op_leverage"]],
                    text=[f"{v:.1f}x" for v in oldf["op_leverage"]], textposition="outside",
                ))
                fig_ol.add_vline(x=1, line_dash="dash", line_color="#555", annotation_text="1:1")
                fig_ol.update_layout(template="plotly_dark", height=380,
                                    title="Operating Leverage (median)",
                                    xaxis_title="ΔOI% / ΔRev%",
                                    margin=dict(l=0, r=60, t=40, b=0))
                st.plotly_chart(fig_ol, use_container_width=True, config=PLOTLY_NOBAR)

    # Earnings Quality
    with st.spinner("Loading cash flow data..."):
        cf_df = fetch_sector_cashflow(cfg.tickers)

    if not cf_df.empty:
        merged_eq = cf_df.merge(fin_df[["ticker", "net_income"]], on="ticker")
        merged_eq = merged_eq.dropna(subset=["operating_cf", "net_income"])
        merged_eq = merged_eq[merged_eq["net_income"] > 0]
        if not merged_eq.empty:
            merged_eq["eq_ratio"] = merged_eq["operating_cf"] / merged_eq["net_income"]
            merged_eq = merged_eq.sort_values("eq_ratio", ascending=True)
            fig_eq = go.Figure()
            fig_eq.add_trace(go.Bar(
                y=merged_eq["ticker"], x=merged_eq["eq_ratio"], orientation="h",
                marker_color=["#00d1ff" if v >= 1 else "#ff6b6b" for v in merged_eq["eq_ratio"]],
                text=[f"{v:.1f}x" for v in merged_eq["eq_ratio"]], textposition="outside",
            ))
            fig_eq.add_vline(x=1, line_dash="dash", line_color="#ffaa00", annotation_text="1.0x (breakeven)")
            fig_eq.update_layout(template="plotly_dark", height=380,
                                title="Earnings Quality (OpCF / Net Income)",
                                xaxis_title="Ratio",
                                margin=dict(l=0, r=60, t=40, b=0))
            st.plotly_chart(fig_eq, use_container_width=True, config=PLOTLY_NOBAR)

    # Composite Scorecard
    st.markdown("---")
    st.subheader("Composite Scorecard")
    st.caption("Weighted ranking across growth, profitability, valuation, leverage, and quality. Lower rank = better.")

    score_df = fin_df[["ticker", "net_margin", "roe"]].copy()

    if not rev_hist.empty:
        for ticker in score_df["ticker"]:
            sub = rev_hist[rev_hist["ticker"] == ticker].sort_values("date")
            if len(sub) >= 2:
                score_df.loc[score_df["ticker"] == ticker, "rev_growth"] = \
                    (sub["revenue"].iloc[-1] / sub["revenue"].iloc[0] - 1) * 100

    if not forecasts.empty:
        for _, frow in forecasts.iterrows():
            t = frow["ticker"]
            if pd.notna(frow.get("forward_pe")):
                score_df.loc[score_df["ticker"] == t, "fwd_pe"] = frow["forward_pe"]

    if not cf_df.empty:
        merged_eq2 = cf_df.merge(fin_df[["ticker", "net_income"]], on="ticker")
        merged_eq2 = merged_eq2[merged_eq2["net_income"] > 0]
        if not merged_eq2.empty:
            merged_eq2["eq"] = merged_eq2["operating_cf"] / merged_eq2["net_income"]
            for _, r in merged_eq2.iterrows():
                score_df.loc[score_df["ticker"] == r["ticker"], "earnings_quality"] = r["eq"]

    score_df = score_df.merge(fin_df[["ticker", "debt_to_equity"]], on="ticker", how="left")

    rank_cols = {
        "net_margin": False,
        "roe": False,
        "rev_growth": False,
        "fwd_pe": True,
        "earnings_quality": False,
        "debt_to_equity": True,
    }
    for col, ascending in rank_cols.items():
        if col in score_df.columns:
            score_df[f"{col}_rank"] = score_df[col].rank(ascending=ascending, na_option="bottom")

    rank_cols_avail = [f"{c}_rank" for c in rank_cols if f"{c}_rank" in score_df.columns]
    if rank_cols_avail:
        score_df["composite_rank"] = score_df[rank_cols_avail].mean(axis=1)
        score_df = score_df.sort_values("composite_rank")

        display_score = score_df[["ticker"]].copy()
        display_score["Composite"] = score_df["composite_rank"].apply(lambda v: f"{v:.1f}")
        for col in rank_cols:
            rank_col = f"{col}_rank"
            if col in score_df.columns and rank_col in score_df.columns:
                label = col.replace("_", " ").title()
                display_score[label] = score_df[rank_col].apply(
                    lambda v: f"#{int(v)}" if pd.notna(v) else "")
        display_score = display_score.rename(columns={"ticker": "Ticker", "Composite": "Score"})
        st.dataframe(display_score, use_container_width=True, hide_index=True)

        sc_sorted = score_df.sort_values("composite_rank").reset_index(drop=True)
        fig_sc = go.Figure()
        fig_sc.add_trace(go.Bar(
            y=sc_sorted["ticker"], x=sc_sorted["composite_rank"],
            orientation="h",
            marker_color=["#00d1ff" if i < 3 else "#ffaa00" if i < 7 else "#ff6b6b"
                          for i in range(len(sc_sorted))],
            text=[f"#{i+1}  ({v:.1f})" for i, v in enumerate(sc_sorted["composite_rank"])],
            textposition="outside",
            hovertemplate="%{y}: Avg Rank %{x:.1f}<extra></extra>",
        ))
        fig_sc.update_layout(template="plotly_dark", height=400,
                             title="Composite Ranking (lower score = better)",
                             xaxis_title="Avg Rank Score",
                             yaxis=dict(autorange="reversed"),
                             margin=dict(l=0, r=80, t=40, b=0))
        st.plotly_chart(fig_sc, use_container_width=True, config=PLOTLY_NOBAR)

    # Financial ratios table
    st.subheader("Financial Ratios")
    tbl = fin_df.copy()
    tbl["revenue"] = tbl["revenue"].apply(lambda x: f"${x / 1e9:,.1f}B" if pd.notna(x) else "")
    tbl["net_income"] = tbl["net_income"].apply(lambda x: f"${x / 1e9:,.1f}B" if pd.notna(x) else "")
    for col in ["net_margin", "operating_margin", "roe", "roa"]:
        tbl[col] = tbl[col].apply(lambda x: f"{x:.1f}%" if pd.notna(x) else "")
    for col in ["debt_to_equity", "current_ratio"]:
        tbl[col] = tbl[col].apply(lambda x: f"{x:.2f}" if pd.notna(x) else "")
    tbl["eps"] = tbl["eps"].apply(lambda x: f"${x:.2f}" if pd.notna(x) else "")
    tbl.columns = ["Ticker", "Company", "Revenue", "Net Income", "Net Margin",
                    "Op Margin", "ROE", "ROA", "D/E", "Current", "EPS"]
    st.dataframe(tbl, use_container_width=True, hide_index=True)


# ─────────────────────────────────────────────────
# TAB 2: CAPEX ANALYSIS
# ─────────────────────────────────────────────────

def _render_capex_tab(cfg: SectorConfig, rev_hist, kp):
    with st.spinner("Loading CapEx data..."):
        capex_df = fetch_sector_capex(cfg.companies)
        capex_hist = fetch_sector_capex_history(cfg.companies)

    if not capex_hist.empty:
        cx = capex_hist.copy()
        cx["date"] = pd.to_datetime(cx["date"])
        cx = cx[cx["date"] >= "2024-01-01"].sort_values(["ticker", "date"])
        cx["year"] = cx["date"].dt.year
        cx["quarter"] = (cx["date"].dt.month - 1) // 3 + 1

        # XBRL CapEx values are cumulative YTD in 10-Q filings.
        # 10-K filings report the full-year total.
        # To get quarterly values:
        # - For 10-Q: diff consecutive values within the same fiscal year
        # - For 10-K: Q4 = 10-K value minus the last 10-Q cumulative (Q3)
        # - If only 10-K exists for a year, it's the annual total (not quarterly)
        q_rows = []
        for ticker in cx["ticker"].unique():
            sub = cx[cx["ticker"] == ticker].sort_values("date")
            for year in sub["year"].unique():
                yr_data = sub[sub["year"] == year].sort_values("date")
                tenq = yr_data[yr_data["form"] == "10-Q"]
                tenk = yr_data[yr_data["form"] == "10-K"]

                # Extract quarterly values from 10-Q cumulative diffs
                if len(tenq) > 0:
                    prev_cum = 0
                    for _, row in tenq.iterrows():
                        q_val = row["capex"] - prev_cum
                        prev_cum = row["capex"]
                        q_rows.append({
                            "ticker": row["ticker"],
                            "company": row["company"],
                            "date": row["date"],
                            "q_capex": q_val,
                            "form": row["form"],
                            "year": year,
                            "quarter": int((row["date"].month - 1) // 3 + 1),
                        })

                    # If 10-K exists, derive Q4 = 10-K - last Q cumulative
                    if len(tenk) > 0 and len(tenq) > 0:
                        annual = tenk.iloc[-1]["capex"]
                        last_q_cum = tenq.iloc[-1]["capex"]
                        q4_val = annual - last_q_cum
                        if q4_val > 0:
                            q_rows.append({
                                "ticker": ticker,
                                "company": tenk.iloc[-1]["company"],
                                "date": tenk.iloc[-1]["date"],
                                "q_capex": q4_val,
                                "form": "10-K",
                                "year": year,
                                "quarter": 4,
                            })
                elif len(tenk) > 0:
                    # Only annual data — cannot split into quarters accurately
                    # Show as Q4 with the full annual value noted
                    q_rows.append({
                        "ticker": ticker,
                        "company": tenk.iloc[-1]["company"],
                        "date": tenk.iloc[-1]["date"],
                        "q_capex": tenk.iloc[-1]["capex"],
                        "form": "10-K (annual)",
                        "year": year,
                        "quarter": 4,
                    })

        if q_rows:
            cx = pd.DataFrame(q_rows)
            cx["date"] = pd.to_datetime(cx["date"])
        else:
            cx = pd.DataFrame()

        if not cx.empty:
            cx["q_label"] = cx["year"].astype(str) + "-Q" + cx["quarter"].astype(str)

        if not cx.empty:
            # Latest CapEx + Capital Intensity
            cx_c1, cx_c2 = st.columns(2)
            with cx_c1:
                latest_q = cx.groupby("ticker").last().reset_index().sort_values("q_capex", ascending=True)
                fig_cx = go.Figure()
                fig_cx.add_trace(go.Bar(
                    y=latest_q["ticker"], x=latest_q["q_capex"] / 1e9, orientation="h",
                    marker_color="#ff6b6b",
                    text=[f"${v / 1e9:,.1f}B" for v in latest_q["q_capex"]], textposition="outside",
                ))
                fig_cx.update_layout(template="plotly_dark", height=380, title="Latest Quarter CapEx ($B)",
                                    xaxis_title="CapEx ($B)", margin=dict(l=0, r=80, t=40, b=0))
                st.plotly_chart(fig_cx, use_container_width=True, config=PLOTLY_NOBAR)

            with cx_c2:
                if not rev_hist.empty:
                    latest_rev = rev_hist.groupby("ticker").last().reset_index()[["ticker", "revenue"]]
                    latest_cx = cx.groupby("ticker").last().reset_index()[["ticker", "q_capex"]]
                    intensity = latest_cx.merge(latest_rev, on="ticker").dropna()
                    if not intensity.empty:
                        intensity["pct"] = intensity["q_capex"] / intensity["revenue"] * 100
                        intensity = intensity.sort_values("pct", ascending=True)
                        fig_ci = go.Figure()
                        fig_ci.add_trace(go.Bar(
                            y=intensity["ticker"], x=intensity["pct"], orientation="h",
                            marker_color=["#ff6b6b" if v > 15 else "#ffaa00" if v > 8 else "#00d1ff" for v in intensity["pct"]],
                            text=[f"{v:.1f}%" for v in intensity["pct"]], textposition="outside",
                        ))
                        fig_ci.update_layout(template="plotly_dark", height=380,
                                            title="Capital Intensity (CapEx / Revenue %)",
                                            xaxis_title="%", margin=dict(l=0, r=60, t=40, b=0))
                        st.plotly_chart(fig_ci, use_container_width=True, config=PLOTLY_NOBAR)

            # CapEx trend
            trend_mode = st.radio("Trend view", ["Absolute ($B)", "Indexed (Q1 2024 = 100)"],
                                  horizontal=True, key=f"{kp}_capex_mode")

            fig_cx_trend = go.Figure()
            cx_colors = {}
            for ci, ticker in enumerate(sorted(cx["ticker"].unique())):
                sub = cx[cx["ticker"] == ticker].sort_values("date")
                if sub.empty:
                    continue
                color = COLOR_CYCLE[ci % len(COLOR_CYCLE)]
                cx_colors[ticker] = color
                if trend_mode == "Absolute ($B)":
                    y_vals = sub["q_capex"] / 1e9
                else:
                    base = sub["q_capex"].iloc[0]
                    y_vals = (sub["q_capex"] / base * 100) if base and base > 0 else sub["q_capex"] * 0
                fig_cx_trend.add_trace(go.Scatter(
                    x=sub["q_label"], y=y_vals, mode="lines+markers", name=ticker,
                    line=dict(width=2, color=color), legendgroup=ticker,
                ))

            # Guidance projections
            snap_map = {d["ticker"]: d for d in cfg.guidance_snapshot.get("data", [])}
            if trend_mode == "Absolute ($B)":
                for ticker, snap in snap_map.items():
                    cx_g = snap.get("capex_guidance")
                    if cx_g and ticker in cx_colors:
                        sub = cx[cx["ticker"] == ticker].sort_values("date")
                        if sub.empty:
                            continue
                        fig_cx_trend.add_trace(go.Scatter(
                            x=[sub["q_label"].iloc[-1], "2026-Q1 (est)"],
                            y=[sub["q_capex"].iloc[-1] / 1e9, cx_g / 4],
                            mode="lines+markers",
                            line=dict(width=2, color=cx_colors[ticker], dash="dot"),
                            marker=dict(size=10, symbol="star"),
                            legendgroup=ticker, showlegend=False,
                        ))

            if trend_mode == "Indexed (Q1 2024 = 100)":
                fig_cx_trend.add_hline(y=100, line_dash="dash", line_color="#555", annotation_text="Baseline")
            y_title = "CapEx ($B)" if trend_mode == "Absolute ($B)" else "Indexed (Q1 2024 = 100)"
            fig_cx_trend.update_layout(
                template="plotly_dark", height=450,
                title=f"Quarterly CapEx — {trend_mode}" + (" (★ = guidance)" if trend_mode == "Absolute ($B)" else ""),
                yaxis_title=y_title, legend=dict(orientation="h", y=-0.15),
                margin=dict(l=0, r=0, t=40, b=0),
            )
            st.plotly_chart(fig_cx_trend, use_container_width=True, config=PLOTLY_NOBAR)

            # YoY change + stacked sector
            cx_c3, cx_c4 = st.columns(2)
            with cx_c3:
                yoy_rows = []
                for ticker in cx["ticker"].unique():
                    sub = cx[cx["ticker"] == ticker].sort_values("date")
                    if len(sub) >= 4:
                        current = sub["q_capex"].iloc[-1]
                        year_ago = sub["q_capex"].iloc[-4]
                        if year_ago and year_ago > 0:
                            yoy_rows.append({"ticker": ticker, "yoy": (current - year_ago) / year_ago * 100})
                if yoy_rows:
                    yoy_df = pd.DataFrame(yoy_rows).sort_values("yoy", ascending=True)
                    fig_yoy = go.Figure()
                    fig_yoy.add_trace(go.Bar(
                        y=yoy_df["ticker"], x=yoy_df["yoy"], orientation="h",
                        marker_color=["#00d1ff" if v >= 0 else "#ff6b6b" for v in yoy_df["yoy"]],
                        text=[f"{v:+.1f}%" for v in yoy_df["yoy"]], textposition="outside",
                    ))
                    fig_yoy.add_vline(x=0, line_dash="dash", line_color="#555")
                    fig_yoy.update_layout(template="plotly_dark", height=380, title="CapEx YoY Change (%)",
                                         xaxis_title="% Change", margin=dict(l=0, r=60, t=40, b=0))
                    st.plotly_chart(fig_yoy, use_container_width=True, config=PLOTLY_NOBAR)

            with cx_c4:
                pivot = cx.pivot_table(index="q_label", columns="ticker", values="q_capex", aggfunc="sum").sort_index()
                est_label = "2026-Q1 (est)"
                est_row = {t: (snap_map.get(t, {}).get("capex_guidance") or 0) * 1e9 / 4 for t in pivot.columns}
                for t, sn in snap_map.items():
                    if t not in pivot.columns and sn.get("capex_guidance"):
                        est_row[t] = sn["capex_guidance"] * 1e9 / 4
                if any(v for v in est_row.values() if v):
                    pivot = pd.concat([pivot, pd.DataFrame([est_row], index=[est_label])])
                pivot = pivot.fillna(0)

                fig_stack = go.Figure()
                for i, col in enumerate(pivot.sum().sort_values(ascending=False).index):
                    is_est = [q == est_label for q in pivot.index]
                    fig_stack.add_trace(go.Bar(
                        x=pivot.index, y=pivot[col] / 1e9, name=col,
                        marker_color=COLOR_CYCLE[i % len(COLOR_CYCLE)],
                        marker_pattern_shape=["/" if e else "" for e in is_est],
                    ))
                fig_stack.update_layout(template="plotly_dark", height=400, barmode="stack",
                                       title="Sector CapEx by Quarter ($B)",
                                       yaxis_title="Total ($B)", legend=dict(orientation="h", y=-0.2),
                                       margin=dict(l=0, r=0, t=40, b=0))
                st.plotly_chart(fig_stack, use_container_width=True, config=PLOTLY_NOBAR)

            # Detail table
            st.subheader("CapEx Detail (2024+)")
            pivot_display = cx.pivot_table(index="ticker", columns="q_label", values="q_capex")
            pivot_display = pivot_display[sorted(pivot_display.columns)]
            for t in pivot_display.index:
                cx_g = snap_map.get(t, {}).get("capex_guidance")
                pivot_display.loc[t, "FY Guidance"] = cx_g * 1e9 if cx_g else None
            pivot_display = pivot_display.map(lambda v: f"${v / 1e9:,.1f}B" if pd.notna(v) else "")
            st.dataframe(pivot_display, use_container_width=True)
    else:
        st.warning("No CapEx data available.")


# ─────────────────────────────────────────────────
# TAB 3: VALUATION & RETURNS
# ─────────────────────────────────────────────────

def _render_valuation_tab(cfg: SectorConfig, kp):
    with st.spinner("Loading valuation data..."):
        val_df = fetch_valuation_data(cfg.tickers)

    if not val_df.empty:
        # P/E + EV/EBITDA
        v1, v2 = st.columns(2)
        with v1:
            vpe = val_df.dropna(subset=["forward_pe"]).sort_values("forward_pe")
            fig_pe = go.Figure()
            fig_pe.add_trace(go.Bar(
                y=vpe["ticker"], x=vpe["forward_pe"], orientation="h",
                marker_color=["#00d1ff" if v < 15 else "#ffaa00" if v < 20 else "#ff6b6b" for v in vpe["forward_pe"]],
                text=[f"{v:.1f}x" for v in vpe["forward_pe"]], textposition="outside",
            ))
            fig_pe.update_layout(template="plotly_dark", height=380, title="Forward P/E",
                                xaxis_title="P/E", margin=dict(l=0, r=60, t=40, b=0))
            st.plotly_chart(fig_pe, use_container_width=True, config=PLOTLY_NOBAR)

        with v2:
            vev = val_df.dropna(subset=["ev_ebitda"]).sort_values("ev_ebitda")
            fig_ev = go.Figure()
            fig_ev.add_trace(go.Bar(
                y=vev["ticker"], x=vev["ev_ebitda"], orientation="h",
                marker_color=["#00d1ff" if v < 8 else "#ffaa00" if v < 12 else "#ff6b6b" for v in vev["ev_ebitda"]],
                text=[f"{v:.1f}x" for v in vev["ev_ebitda"]], textposition="outside",
            ))
            fig_ev.update_layout(template="plotly_dark", height=380, title="EV / EBITDA",
                                xaxis_title="EV/EBITDA", margin=dict(l=0, r=60, t=40, b=0))
            st.plotly_chart(fig_ev, use_container_width=True, config=PLOTLY_NOBAR)

        # Dividend Yield + FCF Yield
        d1, d2 = st.columns(2)
        with d1:
            vdy = val_df.dropna(subset=["dividend_yield"]).sort_values("dividend_yield", ascending=True)
            fig_dy = go.Figure()
            fig_dy.add_trace(go.Bar(
                y=vdy["ticker"], x=vdy["dividend_yield"], orientation="h", marker_color="#00ff88",
                text=[f"{v:.1f}%" for v in vdy["dividend_yield"]], textposition="outside",
            ))
            fig_dy.update_layout(template="plotly_dark", height=380, title="Dividend Yield (%)",
                                xaxis_title="%", margin=dict(l=0, r=60, t=40, b=0))
            st.plotly_chart(fig_dy, use_container_width=True, config=PLOTLY_NOBAR)

        with d2:
            vfcf = val_df.dropna(subset=["fcf_yield"]).sort_values("fcf_yield", ascending=True)
            fig_fcf = go.Figure()
            fig_fcf.add_trace(go.Bar(
                y=vfcf["ticker"], x=vfcf["fcf_yield"], orientation="h", marker_color="#00d1ff",
                text=[f"{v:.1f}%" for v in vfcf["fcf_yield"]], textposition="outside",
            ))
            fig_fcf.update_layout(template="plotly_dark", height=380, title="Free Cash Flow Yield (%)",
                                xaxis_title="%", margin=dict(l=0, r=60, t=40, b=0))
            st.plotly_chart(fig_fcf, use_container_width=True, config=PLOTLY_NOBAR)

        # Debt + Beta
        db1, db2 = st.columns(2)
        with db1:
            vnd = val_df.dropna(subset=["net_debt_ebitda"]).sort_values("net_debt_ebitda", ascending=True)
            fig_nd = go.Figure()
            fig_nd.add_trace(go.Bar(
                y=vnd["ticker"], x=vnd["net_debt_ebitda"], orientation="h",
                marker_color=["#00d1ff" if v < 1 else "#ffaa00" if v < 2 else "#ff6b6b" for v in vnd["net_debt_ebitda"]],
                text=[f"{v:.1f}x" for v in vnd["net_debt_ebitda"]], textposition="outside",
            ))
            fig_nd.update_layout(template="plotly_dark", height=380, title="Net Debt / EBITDA",
                                xaxis_title="Leverage", margin=dict(l=0, r=60, t=40, b=0))
            st.plotly_chart(fig_nd, use_container_width=True, config=PLOTLY_NOBAR)

        with db2:
            vb = val_df.dropna(subset=["beta"]).sort_values("beta", ascending=True)
            fig_b = go.Figure()
            fig_b.add_trace(go.Bar(
                y=vb["ticker"], x=vb["beta"], orientation="h",
                marker_color=["#00d1ff" if v < 0.5 else "#ffaa00" if v < 0.8 else "#ff6b6b" for v in vb["beta"]],
                text=[f"{v:.2f}" for v in vb["beta"]], textposition="outside",
            ))
            fig_b.add_vline(x=1.0, line_dash="dash", line_color="#555", annotation_text="Market")
            fig_b.update_layout(template="plotly_dark", height=380, title="Beta",
                               xaxis_title="Beta", margin=dict(l=0, r=60, t=40, b=0))
            st.plotly_chart(fig_b, use_container_width=True, config=PLOTLY_NOBAR)

        # Summary table
        st.subheader("Valuation Summary")
        vt = val_df[["ticker"]].copy()
        vt["Mkt Cap"] = val_df["market_cap"].apply(lambda v: f"${v/1e9:,.0f}B" if pd.notna(v) else "")
        vt["Fwd P/E"] = val_df["forward_pe"].apply(lambda v: f"{v:.1f}x" if pd.notna(v) else "")
        vt["EV/EBITDA"] = val_df["ev_ebitda"].apply(lambda v: f"{v:.1f}x" if pd.notna(v) else "")
        vt["P/B"] = val_df["price_to_book"].apply(lambda v: f"{v:.1f}x" if pd.notna(v) else "")
        vt["Div Yield"] = val_df["dividend_yield"].apply(lambda v: f"{v:.1f}%" if pd.notna(v) else "")
        vt["FCF Yield"] = val_df["fcf_yield"].apply(lambda v: f"{v:.1f}%" if pd.notna(v) else "")
        vt["Debt/EBITDA"] = val_df["net_debt_ebitda"].apply(lambda v: f"{v:.1f}x" if pd.notna(v) else "")
        vt["Payout"] = val_df["payout_ratio"].apply(lambda v: f"{v*100:.0f}%" if pd.notna(v) else "")
        vt["Beta"] = val_df["beta"].apply(lambda v: f"{v:.2f}" if pd.notna(v) else "")
        vt.columns = ["Ticker", "Mkt Cap", "Fwd P/E", "EV/EBITDA", "P/B",
                       "Div Yield", "FCF Yield", "Debt/EBITDA", "Payout", "Beta"]
        st.dataframe(vt, use_container_width=True, hide_index=True)
    else:
        st.warning("Valuation data unavailable.")


# ─────────────────────────────────────────────────
# TAB 4: ALPHA SIGNALS
# ─────────────────────────────────────────────────

def _render_alpha_tab(cfg: SectorConfig, kp):
    tickers_list = cfg.tickers

    # Relative Value Scatter
    st.subheader("Relative Value Map")
    st.caption("FCF yield vs forward P/E — top-left quadrant is cheap + cash-generative (the sweet spot).")

    with st.spinner("Loading valuation data..."):
        val_df = fetch_valuation_data(tickers_list)

    if not val_df.empty:
        rv = val_df.dropna(subset=["forward_pe", "fcf_yield"]).copy()
        if not rv.empty:
            rv["subsector"] = rv["ticker"].map(cfg.ticker_subsector)
            fig_rv = go.Figure()
            for ss, color in cfg.subsector_colors.items():
                sub = rv[rv["subsector"] == ss]
                if not sub.empty:
                    fig_rv.add_trace(go.Scatter(
                        x=sub["forward_pe"], y=sub["fcf_yield"],
                        mode="markers+text", name=ss,
                        marker=dict(size=16, color=color, line=dict(width=1, color="#fff")),
                        text=sub["ticker"], textposition="top center",
                        textfont=dict(size=11, color="#e0e0e0"),
                        hovertemplate="%{text}<br>Fwd P/E: %{x:.1f}x<br>FCF Yield: %{y:.1f}%<extra></extra>",
                    ))

            med_pe = rv["forward_pe"].median()
            med_fcf = rv["fcf_yield"].median()
            fig_rv.add_vline(x=med_pe, line_dash="dash", line_color="#555",
                             annotation_text=f"Med P/E: {med_pe:.1f}x")
            fig_rv.add_hline(y=med_fcf, line_dash="dash", line_color="#555",
                             annotation_text=f"Med FCF: {med_fcf:.1f}%")
            fig_rv.add_annotation(x=rv["forward_pe"].min(), y=rv["fcf_yield"].max(),
                                  text="CHEAP + HIGH CASH", showarrow=False,
                                  font=dict(size=10, color="#00ff88"), xanchor="left")
            fig_rv.add_annotation(x=rv["forward_pe"].max(), y=rv["fcf_yield"].min(),
                                  text="EXPENSIVE + LOW CASH", showarrow=False,
                                  font=dict(size=10, color="#ff6b6b"), xanchor="right")

            fig_rv.update_layout(
                template="plotly_dark", height=480,
                title="Relative Value: FCF Yield vs Forward P/E",
                xaxis_title="Forward P/E (lower = cheaper)",
                yaxis_title="FCF Yield % (higher = better)",
                legend=dict(orientation="h", y=-0.12),
                margin=dict(l=0, r=0, t=40, b=0),
            )
            st.plotly_chart(fig_rv, use_container_width=True, config=PLOTLY_NOBAR)

    # Momentum Scoring
    st.markdown("---")
    st.subheader("Price Momentum")
    st.caption("1M / 3M / 6M / 12M total returns — momentum is the strongest documented equity factor.")

    with st.spinner("Loading momentum data..."):
        mom_df = fetch_momentum_data(tickers_list)

    if not mom_df.empty:
        mom_periods = [c for c in ["1M", "3M", "6M", "12M"] if c in mom_df.columns]
        if mom_periods:
            mom_sorted = mom_df.set_index("ticker")[mom_periods]
            mom_sorted["avg"] = mom_sorted.mean(axis=1)
            mom_sorted = mom_sorted.sort_values("avg", ascending=False).drop(columns=["avg"])

            fig_mom = go.Figure(data=go.Heatmap(
                z=mom_sorted.values, x=mom_periods, y=mom_sorted.index.tolist(),
                colorscale=[[0, "#ff6b6b"], [0.5, "#1a1a2e"], [1, "#00d1ff"]],
                zmid=0,
                text=[[f"{v:+.1f}%" if not pd.isna(v) else "" for v in row] for row in mom_sorted.values],
                texttemplate="%{text}", textfont={"size": 12},
                hovertemplate="%{y} %{x}: %{z:+.1f}%<extra></extra>",
                colorbar=dict(title="Return %"),
            ))
            fig_mom.update_layout(template="plotly_dark", height=420,
                                  title="Momentum Heatmap (sorted by avg momentum)",
                                  margin=dict(l=0, r=0, t=40, b=0))
            st.plotly_chart(fig_mom, use_container_width=True, config=PLOTLY_NOBAR)

        mom_period = st.radio("Period", mom_periods, horizontal=True, key=f"{kp}_mom_period")
        if mom_period in mom_df.columns:
            ms = mom_df.dropna(subset=[mom_period]).sort_values(mom_period, ascending=True)
            fig_mb = go.Figure()
            fig_mb.add_trace(go.Bar(
                y=ms["ticker"], x=ms[mom_period], orientation="h",
                marker_color=["#00d1ff" if v >= 0 else "#ff6b6b" for v in ms[mom_period]],
                text=[f"{v:+.1f}%" for v in ms[mom_period]], textposition="outside",
            ))
            fig_mb.add_vline(x=0, line_dash="dash", line_color="#555")
            fig_mb.update_layout(template="plotly_dark", height=380,
                                title=f"{mom_period} Price Return (%)",
                                xaxis_title="Return %",
                                margin=dict(l=0, r=60, t=40, b=0))
            st.plotly_chart(fig_mb, use_container_width=True, config=PLOTLY_NOBAR)

    # Estimate Revisions
    st.markdown("---")
    st.subheader("Analyst Estimate Revisions")
    st.caption("EPS revision direction in last 30 days — revisions predict returns better than the estimates themselves.")

    with st.spinner("Loading revision data..."):
        rev_df = fetch_eps_revisions(tickers_list)

    if not rev_df.empty:
        rev_df = rev_df.sort_values("net_30d", ascending=True)

        rc1, rc2 = st.columns(2)
        with rc1:
            fig_net = go.Figure()
            fig_net.add_trace(go.Bar(
                y=rev_df["ticker"], x=rev_df["net_30d"], orientation="h",
                marker_color=["#00d1ff" if v >= 0 else "#ff6b6b" for v in rev_df["net_30d"]],
                text=[f"{v:+d}" for v in rev_df["net_30d"]], textposition="outside",
                hovertemplate="%{y}: Net %{x:+d} revisions<extra></extra>",
            ))
            fig_net.add_vline(x=0, line_dash="dash", line_color="#555")
            fig_net.update_layout(template="plotly_dark", height=380,
                                  title="Net EPS Revisions (30 Days)",
                                  xaxis_title="Up - Down",
                                  margin=dict(l=0, r=60, t=40, b=0))
            st.plotly_chart(fig_net, use_container_width=True, config=PLOTLY_NOBAR)

        with rc2:
            fig_stack_rev = go.Figure()
            fig_stack_rev.add_trace(go.Bar(
                y=rev_df["ticker"], x=rev_df["up_30d"], orientation="h",
                name="Upgrades", marker_color="#00d1ff",
            ))
            fig_stack_rev.add_trace(go.Bar(
                y=rev_df["ticker"], x=-rev_df["down_30d"], orientation="h",
                name="Downgrades", marker_color="#ff6b6b",
            ))
            fig_stack_rev.update_layout(template="plotly_dark", height=380, barmode="relative",
                                        title="EPS Revision Breakdown (30 Days)",
                                        xaxis_title="Analysts",
                                        legend=dict(orientation="h", y=-0.15),
                                        margin=dict(l=0, r=0, t=40, b=0))
            st.plotly_chart(fig_stack_rev, use_container_width=True, config=PLOTLY_NOBAR)

    # Insider Activity
    st.markdown("---")
    st.subheader("Insider Activity (90 Days)")
    st.caption("Net insider buying/selling — insiders buying their own stock is a strong bullish signal.")

    with st.spinner("Loading insider data..."):
        ins_df = fetch_insider_summary(tickers_list)

    if not ins_df.empty:
        ins_df = ins_df.sort_values("net_value")

        ic1, ic2 = st.columns(2)
        with ic1:
            fig_ins = go.Figure()
            fig_ins.add_trace(go.Bar(
                y=ins_df["ticker"], x=ins_df["net_value"] / 1e6, orientation="h",
                marker_color=["#00d1ff" if v >= 0 else "#ff6b6b" for v in ins_df["net_value"]],
                text=[f"${v/1e6:+,.1f}M" for v in ins_df["net_value"]], textposition="outside",
            ))
            fig_ins.add_vline(x=0, line_dash="dash", line_color="#555")
            fig_ins.update_layout(template="plotly_dark", height=380,
                                  title="Net Insider Value ($M, 90 Days)",
                                  xaxis_title="$M (Buys - Sells)",
                                  margin=dict(l=0, r=80, t=40, b=0))
            st.plotly_chart(fig_ins, use_container_width=True, config=PLOTLY_NOBAR)

        with ic2:
            fig_count = go.Figure()
            fig_count.add_trace(go.Bar(y=ins_df["ticker"], x=ins_df["buy_count"],
                                       orientation="h", name="Buys", marker_color="#00d1ff"))
            fig_count.add_trace(go.Bar(y=ins_df["ticker"], x=-ins_df["sell_count"],
                                       orientation="h", name="Sells", marker_color="#ff6b6b"))
            fig_count.update_layout(template="plotly_dark", height=380, barmode="relative",
                                    title="Insider Transaction Count (90 Days)",
                                    xaxis_title="Transactions",
                                    legend=dict(orientation="h", y=-0.15),
                                    margin=dict(l=0, r=0, t=40, b=0))
            st.plotly_chart(fig_count, use_container_width=True, config=PLOTLY_NOBAR)


# ─────────────────────────────────────────────────
# TAB 5: RISK ANALYTICS
# ─────────────────────────────────────────────────

def _render_risk_tab(cfg: SectorConfig, kp):
    tickers_list = cfg.tickers
    sector_name = cfg.etf

    st.caption("Drawdown analysis, Value at Risk, risk-adjusted returns, and sector vs market performance.")

    with st.spinner("Loading price history..."):
        prices = fetch_price_history(tickers_list, period="2y")

    if not prices.empty and len(prices.columns) >= 2:
        returns = prices.pct_change().dropna()

        # Drawdown Chart
        st.subheader("Maximum Drawdown")
        dd_ticker = st.selectbox("Select ticker", sorted(prices.columns.tolist()),
                                 index=0, key=f"{kp}_dd_ticker")
        p = prices[dd_ticker]
        rolling_max = p.cummax()
        drawdown = (p - rolling_max) / rolling_max * 100

        fig_dd = go.Figure()
        fig_dd.add_trace(go.Scatter(
            x=drawdown.index, y=drawdown, mode="lines",
            line=dict(color="#ff6b6b", width=2),
            fill="tozeroy", fillcolor="rgba(255,107,107,0.1)",
            hovertemplate="%{x}<br>Drawdown: %{y:.1f}%<extra></extra>",
        ))
        max_dd = drawdown.min()
        max_dd_date = drawdown.idxmin()
        fig_dd.add_annotation(x=max_dd_date, y=max_dd,
                              text=f"Max: {max_dd:.1f}%", showarrow=True,
                              arrowhead=2, arrowcolor="#ff6b6b", font=dict(color="#ff6b6b"))
        fig_dd.update_layout(template="plotly_dark", height=350,
                             title=f"{dd_ticker} — Drawdown from Peak (%)",
                             yaxis_title="Drawdown %",
                             margin=dict(l=0, r=0, t=40, b=0))
        st.plotly_chart(fig_dd, use_container_width=True, config=PLOTLY_NOBAR)

        # Max drawdown comparison
        dd_rows = []
        for ticker in sorted(prices.columns):
            p = prices[ticker]
            dd = ((p - p.cummax()) / p.cummax() * 100).min()
            dd_rows.append({"ticker": ticker, "max_dd": dd})
        dd_df = pd.DataFrame(dd_rows).sort_values("max_dd")

        fig_dd_comp = go.Figure()
        fig_dd_comp.add_trace(go.Bar(
            y=dd_df["ticker"], x=dd_df["max_dd"], orientation="h",
            marker_color="#ff6b6b",
            text=[f"{v:.1f}%" for v in dd_df["max_dd"]], textposition="outside",
        ))
        fig_dd_comp.update_layout(template="plotly_dark", height=380,
                                  title="Max Drawdown Comparison (2Y)",
                                  xaxis_title="Max Drawdown %",
                                  margin=dict(l=0, r=60, t=40, b=0))
        st.plotly_chart(fig_dd_comp, use_container_width=True, config=PLOTLY_NOBAR)

        # Value at Risk
        st.markdown("---")
        st.subheader("Value at Risk (VaR)")

        var_rows = []
        for ticker in sorted(returns.columns):
            r = returns[ticker]
            mu, sigma = r.mean(), r.std()
            var_rows.append({
                "ticker": ticker,
                "var_95_param": -(mu + 1.645 * sigma) * 100,
                "var_99_param": -(mu + 2.326 * sigma) * 100,
                "var_95_hist": -r.quantile(0.05) * 100,
                "var_99_hist": -r.quantile(0.01) * 100,
                "daily_vol": sigma * 100,
                "annual_vol": sigma * np.sqrt(252) * 100,
            })
        var_df = pd.DataFrame(var_rows)

        vc1, vc2 = st.columns(2)
        with vc1:
            vs = var_df.sort_values("var_95_hist", ascending=True)
            fig_var = go.Figure()
            fig_var.add_trace(go.Bar(y=vs["ticker"], x=vs["var_95_hist"], orientation="h",
                                     name="95% VaR", marker_color="#ffaa00",
                                     text=[f"{v:.1f}%" for v in vs["var_95_hist"]], textposition="outside"))
            fig_var.add_trace(go.Bar(y=vs["ticker"], x=vs["var_99_hist"], orientation="h",
                                     name="99% VaR", marker_color="#ff6b6b",
                                     text=[f"{v:.1f}%" for v in vs["var_99_hist"]], textposition="outside"))
            fig_var.update_layout(template="plotly_dark", height=380, barmode="group",
                                  title="Historical VaR (Daily Loss %)",
                                  xaxis_title="% Loss", legend=dict(orientation="h", y=-0.15),
                                  margin=dict(l=0, r=60, t=40, b=0))
            st.plotly_chart(fig_var, use_container_width=True, config=PLOTLY_NOBAR)

        with vc2:
            avs = var_df.sort_values("annual_vol", ascending=True)
            fig_avol = go.Figure()
            fig_avol.add_trace(go.Bar(y=avs["ticker"], x=avs["annual_vol"], orientation="h",
                                      marker_color="#00d1ff",
                                      text=[f"{v:.0f}%" for v in avs["annual_vol"]], textposition="outside"))
            fig_avol.update_layout(template="plotly_dark", height=380,
                                   title="Annualized Volatility (%)",
                                   xaxis_title="Vol %", margin=dict(l=0, r=60, t=40, b=0))
            st.plotly_chart(fig_avol, use_container_width=True, config=PLOTLY_NOBAR)

        # VaR table
        var_tbl = var_df.copy()
        var_tbl["Daily Vol"] = var_tbl["daily_vol"].apply(lambda v: f"{v:.2f}%")
        var_tbl["Annual Vol"] = var_tbl["annual_vol"].apply(lambda v: f"{v:.0f}%")
        var_tbl["VaR 95%"] = var_tbl["var_95_hist"].apply(lambda v: f"{v:.2f}%")
        var_tbl["VaR 99%"] = var_tbl["var_99_hist"].apply(lambda v: f"{v:.2f}%")
        st.dataframe(var_tbl[["ticker", "Daily Vol", "Annual Vol", "VaR 95%", "VaR 99%"]].rename(
            columns={"ticker": "Ticker"}), use_container_width=True, hide_index=True)

        # Sharpe / Sortino
        st.markdown("---")
        st.subheader("Risk-Adjusted Returns")

        risk_rows = []
        for ticker in sorted(returns.columns):
            r = returns[ticker]
            ann_ret = r.mean() * 252 * 100
            ann_vol = r.std() * np.sqrt(252) * 100
            downside = r[r < 0].std() * np.sqrt(252) * 100 if (r < 0).any() else 0
            sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
            sortino = ann_ret / downside if downside > 0 else 0
            risk_rows.append({
                "ticker": ticker, "ann_return": ann_ret, "ann_vol": ann_vol,
                "sharpe": sharpe, "sortino": sortino,
            })
        risk_df = pd.DataFrame(risk_rows)

        rs1, rs2 = st.columns(2)
        with rs1:
            rs = risk_df.sort_values("sharpe", ascending=True)
            fig_sh = go.Figure()
            fig_sh.add_trace(go.Bar(
                y=rs["ticker"], x=rs["sharpe"], orientation="h",
                marker_color=["#00d1ff" if v > 0 else "#ff6b6b" for v in rs["sharpe"]],
                text=[f"{v:.2f}" for v in rs["sharpe"]], textposition="outside",
            ))
            fig_sh.add_vline(x=0, line_dash="dash", line_color="#555")
            fig_sh.update_layout(template="plotly_dark", height=380, title="Sharpe Ratio (2Y)",
                                 xaxis_title="Sharpe", margin=dict(l=0, r=60, t=40, b=0))
            st.plotly_chart(fig_sh, use_container_width=True, config=PLOTLY_NOBAR)

        with rs2:
            so = risk_df.sort_values("sortino", ascending=True)
            fig_so = go.Figure()
            fig_so.add_trace(go.Bar(
                y=so["ticker"], x=so["sortino"], orientation="h",
                marker_color=["#00d1ff" if v > 0 else "#ff6b6b" for v in so["sortino"]],
                text=[f"{v:.2f}" for v in so["sortino"]], textposition="outside",
            ))
            fig_so.add_vline(x=0, line_dash="dash", line_color="#555")
            fig_so.update_layout(template="plotly_dark", height=380, title="Sortino Ratio (2Y)",
                                 xaxis_title="Sortino", margin=dict(l=0, r=60, t=40, b=0))
            st.plotly_chart(fig_so, use_container_width=True, config=PLOTLY_NOBAR)

        # Sector vs S&P 500
        st.markdown("---")
        st.subheader(f"{sector_name} Sector vs S&P 500")

        with st.spinner("Loading SPY..."):
            spy_prices = fetch_price_history(["SPY"], period="2y")

        if not spy_prices.empty and "SPY" in spy_prices.columns:
            sector_idx = prices.pct_change().mean(axis=1).add(1).cumprod() * 100
            spy_idx = spy_prices["SPY"].pct_change().add(1).cumprod() * 100

            fig_vs = go.Figure()
            fig_vs.add_trace(go.Scatter(x=sector_idx.index, y=sector_idx, mode="lines",
                                        name=f"{sector_name} (Equal-Wt)", line=dict(color="#00d1ff", width=2)))
            fig_vs.add_trace(go.Scatter(x=spy_idx.index, y=spy_idx, mode="lines",
                                        name="S&P 500", line=dict(color="#ffaa00", width=2)))
            fig_vs.add_hline(y=100, line_dash="dash", line_color="#555")
            fig_vs.update_layout(template="plotly_dark", height=400,
                                 title=f"Cumulative Performance: {sector_name} Sector vs S&P 500 (2Y, base=100)",
                                 yaxis_title="Indexed (100 = start)",
                                 legend=dict(orientation="h", y=-0.12),
                                 margin=dict(l=0, r=0, t=40, b=0))
            st.plotly_chart(fig_vs, use_container_width=True, config=PLOTLY_NOBAR)

            rel_perf = sector_idx - spy_idx
            fig_rel = go.Figure()
            fig_rel.add_trace(go.Scatter(
                x=rel_perf.index, y=rel_perf, mode="lines",
                line=dict(color="#00d1ff", width=2),
                fill="tozeroy", fillcolor="rgba(0,209,255,0.08)",
            ))
            fig_rel.add_hline(y=0, line_dash="dash", line_color="#555", annotation_text="Parity")
            fig_rel.update_layout(template="plotly_dark", height=300,
                                  title=f"Relative Performance ({sector_name} − SPY)",
                                  yaxis_title="Excess Return (indexed pts)",
                                  margin=dict(l=0, r=0, t=40, b=0))
            st.plotly_chart(fig_rel, use_container_width=True, config=PLOTLY_NOBAR)
    else:
        st.warning("Price data unavailable for risk analysis.")

    # Sub-sector Performance
    if not prices.empty and len(prices.columns) >= 2:
        st.markdown("---")
        st.subheader("Sub-sector Decomposition")

        fig_ss = go.Figure()
        for ss_name, ss_tickers in cfg.subsectors.items():
            avail = [t for t in ss_tickers if t in prices.columns]
            if not avail:
                continue
            ss_ret = prices[avail].pct_change().mean(axis=1).add(1).cumprod() * 100
            fig_ss.add_trace(go.Scatter(
                x=ss_ret.index, y=ss_ret, mode="lines", name=ss_name,
                line=dict(width=3, color=cfg.subsector_colors[ss_name]),
            ))
        fig_ss.add_hline(y=100, line_dash="dash", line_color="#555")
        fig_ss.update_layout(template="plotly_dark", height=400,
                             title="Sub-sector Cumulative Performance (2Y, base=100)",
                             yaxis_title="Indexed", legend=dict(orientation="h", y=-0.12),
                             margin=dict(l=0, r=0, t=40, b=0))
        st.plotly_chart(fig_ss, use_container_width=True, config=PLOTLY_NOBAR)

        # Factor Exposure
        st.markdown("---")
        st.subheader("Factor Exposure")
        factor_label = ", ".join(cfg.factor_proxies)
        st.caption(f"Regression of each stock's daily returns on factor proxies: {factor_label}.")

        with st.spinner("Loading factor proxies..."):
            factor_prices = fetch_price_history(cfg.factor_proxies, period="2y")

        if not factor_prices.empty and len(factor_prices.columns) >= 2:
            factor_returns = factor_prices.pct_change().dropna()
            factor_names = [c for c in factor_returns.columns if c in cfg.factor_proxies]

            if factor_names:
                from numpy.linalg import lstsq

                beta_rows = []
                for ticker in sorted(prices.columns):
                    common = returns[ticker].dropna().index.intersection(factor_returns.index)
                    if len(common) < 60:
                        continue
                    y = returns[ticker].loc[common].values
                    X = factor_returns[factor_names].loc[common].values
                    X = np.column_stack([np.ones(len(X)), X])
                    try:
                        coeffs, _, _, _ = lstsq(X, y, rcond=None)
                        row = {"ticker": ticker, "alpha": coeffs[0] * 252 * 100}
                        for i, fn in enumerate(factor_names):
                            row[fn] = coeffs[i + 1]
                        y_pred = X @ coeffs
                        ss_res = np.sum((y - y_pred) ** 2)
                        ss_tot = np.sum((y - y.mean()) ** 2)
                        row["R2"] = 1 - ss_res / ss_tot if ss_tot > 0 else 0
                        beta_rows.append(row)
                    except Exception:
                        pass

                if beta_rows:
                    beta_df = pd.DataFrame(beta_rows)

                    heat_cols = [c for c in factor_names if c in beta_df.columns]
                    if heat_cols:
                        heat_data = beta_df.set_index("ticker")[heat_cols]

                        fig_factor = go.Figure(data=go.Heatmap(
                            z=heat_data.values, x=heat_cols, y=heat_data.index.tolist(),
                            colorscale=[[0, "#ff6b6b"], [0.5, "#1a1a2e"], [1, "#00d1ff"]],
                            zmid=0,
                            text=[[f"{v:.2f}" for v in row] for row in heat_data.values],
                            texttemplate="%{text}", textfont={"size": 12},
                            hovertemplate="%{y} β to %{x}: %{z:.3f}<extra></extra>",
                            colorbar=dict(title="Beta"),
                        ))
                        fig_factor.update_layout(template="plotly_dark", height=420,
                                                 title=f"Factor Betas ({factor_label})",
                                                 margin=dict(l=0, r=0, t=40, b=0))
                        st.plotly_chart(fig_factor, use_container_width=True, config=PLOTLY_NOBAR)

                    ft = beta_df.copy()
                    ft["Alpha (ann)"] = ft["alpha"].apply(lambda v: f"{v:+.1f}%")
                    for fn in factor_names:
                        if fn in ft.columns:
                            ft[f"β {fn}"] = ft[fn].apply(lambda v: f"{v:.2f}")
                    ft["R²"] = ft["R2"].apply(lambda v: f"{v:.2f}")
                    show_cols = ["ticker", "Alpha (ann)"] + [f"β {fn}" for fn in factor_names if fn in ft.columns] + ["R²"]
                    st.dataframe(ft[show_cols].rename(columns={"ticker": "Ticker"}),
                                use_container_width=True, hide_index=True)
        else:
            st.info("Factor proxy data unavailable.")


# ─────────────────────────────────────────────────
# TAB 6: GUIDANCE & ESTIMATES
# ─────────────────────────────────────────────────

@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_live_estimates(tickers_tuple: tuple) -> dict:
    """Fetch live analyst estimates from yfinance to overlay on static guidance."""
    import yfinance as yf
    result = {}
    for tk in tickers_tuple:
        try:
            info = yf.Ticker(tk).info or {}
            result[tk] = {
                "price_target": info.get("targetMeanPrice"),
                "target_low": info.get("targetLowPrice"),
                "target_high": info.get("targetHighPrice"),
                "fwd_pe": info.get("forwardPE"),
                "trailing_pe": info.get("trailingPE"),
                "rating": info.get("recommendationKey", "").replace("_", " ").title(),
                "n_analysts": info.get("numberOfAnalystOpinions"),
                "current_price": info.get("currentPrice") or info.get("regularMarketPrice"),
                "fwd_eps": info.get("forwardEps"),
                "trailing_eps": info.get("trailingEps"),
                "rev_growth": info.get("revenueGrowth"),
                "earnings_growth": info.get("earningsGrowth"),
            }
        except Exception:
            pass
    return result


def _render_guidance_tab(cfg: SectorConfig, kp):
    snap = cfg.guidance_snapshot

    # Fetch live estimates
    _tickers = tuple(d["ticker"] for d in snap.get("data", []))
    live = _fetch_live_estimates(_tickers) if _tickers else {}

    _has_live = len(live) > 0
    _snap_date_str = snap.get("date", "N/A")
    st.caption(
        f"Earnings call guidance (scraped {_snap_date_str}) "
        f"{'+ **live Wall Street consensus** (green = updated from API)' if _has_live else '+ analyst consensus'}."
    )
    # Staleness warning
    try:
        from datetime import datetime as _dt_snap
        _snap_dt = _dt_snap.strptime(_snap_date_str, "%Y-%m-%d").date()
        _days_old = (_dt_snap.now().date() - _snap_dt).days
        if _days_old > 90:
            st.warning(f"Guidance snapshot is **{_days_old} days old**. Data may not reflect recent earnings or guidance updates.")
        elif _days_old > 30:
            st.caption(f"Snapshot is {_days_old} days old — consider refreshing after next earnings season.")
    except Exception:
        pass

    # Validate guidance data against recent splits
    try:
        from src.data_engine import fetch_stock_splits
        for d in snap.get("data", []):
            splits = fetch_stock_splits(d["ticker"])
            if not splits.empty:
                recent = splits[splits["execution_date"] >= "2024-01-01"]
                if not recent.empty:
                    ratio = recent.iloc[0].get("split_to", 1) / max(recent.iloc[0].get("split_from", 1), 1)
                    if ratio > 2 and d.get("eps_est_y", 0) > d.get("price_target", 1):
                        _split_date = recent.iloc[0].get("execution_date", "")
                        _date_str = _split_date.strftime('%Y-%m-%d') if hasattr(_split_date, 'strftime') else str(_split_date)
                        st.warning(
                            f"**{d['ticker']}** had a {int(ratio)}:1 split on {_date_str}. "
                            f"EPS/price data may need split adjustment."
                        )
    except Exception:
        pass

    # Guidance table — overlay live data where available
    guidance_rows = []
    for d in snap.get("data", []):
        _live = live.get(d["ticker"], {})
        # Use live values when available, fall back to static
        _target = _live.get("price_target") or d.get("price_target", 0)
        _fwd_pe = _live.get("fwd_pe") or d.get("fwd_pe")
        _rating = _live.get("rating") or d.get("rating", "")
        _fwd_eps = _live.get("fwd_eps")
        _eps_y = _fwd_eps if _fwd_eps else d.get("eps_est_y", 0)
        _rev_growth = _live.get("rev_growth")
        _rev_growth_str = f"{_rev_growth*100:+.0f}%" if _rev_growth else d.get("rev_growth", "")

        row = {
            "Ticker": d["ticker"], "Company": d["company"],
            "Rev Est (Y)": f"${d['rev_est_y']:.0f}B",
            "Rev Growth": _rev_growth_str,
            "EPS (Y)": f"${_eps_y:.2f}",
            "EPS (NY)": f"${d['eps_est_ny']:.2f}",
            "Rating": _rating if _rating and _rating != "None" else d.get("rating", ""),
            "Target": f"${_target:,.0f}" if _target else "—",
            "Fwd P/E": f"{_fwd_pe:.1f}" if _fwd_pe else "—",
        }
        if d.get("capex_guidance"):
            row["CapEx"] = f"${d['capex_guidance']:.1f}B"
            row["CapEx Detail"] = d.get("capex_note") or ""
        if d.get("production"):
            row["Production"] = d["production"]
        guidance_rows.append(row)
    if guidance_rows:
        st.dataframe(pd.DataFrame(guidance_rows), use_container_width=True, hide_index=True)

    with st.expander("Company Outlook & Guidance Notes"):
        for d in snap.get("data", []):
            st.markdown(f"#### {d['ticker']} — {d['company']}")
            metrics = []
            if d.get("capex_guidance"):
                metrics.append(f"**CapEx:** ${d['capex_guidance']:.1f}B")
            if d.get("production"):
                metrics.append(f"**Production:** {d['production']}")
            metrics.append(f"**Rev Est:** ${d['rev_est_y']:.0f}B ({d['rev_growth']} YoY)")
            metrics.append(f"**EPS:** ${d['eps_est_y']:.2f}")
            metrics.append(f"**Rating:** {d['rating']}")
            st.markdown(" &nbsp;&bull;&nbsp; ".join(metrics))
            if d.get("capex_note"):
                st.markdown(f"> {d['capex_note']}")
            if d.get("outlook"):
                st.markdown(d["outlook"])
            st.markdown("---")

    # Earnings surprise heatmap
    st.subheader("Earnings Surprise Heatmap")
    st.caption("Blue = beat, Red = miss. Intensity shows magnitude.")

    with st.spinner("Loading earnings history..."):
        surp_df = fetch_earnings_surprises(cfg.tickers)

    if not surp_df.empty:
        surp_pivot = surp_df.pivot_table(index="ticker", columns="quarter", values="surprise_pct")
        surp_pivot = surp_pivot[sorted(surp_pivot.columns)]
        z_vals = surp_pivot.values * 100
        text = [[f"{v:+.1f}%" if not pd.isna(v) else "" for v in row] for row in z_vals]

        fig_hm = go.Figure(data=go.Heatmap(
            z=z_vals, x=[str(c)[:10] for c in surp_pivot.columns], y=surp_pivot.index.tolist(),
            colorscale=[[0, "#ff6b6b"], [0.5, "#1a1a2e"], [1, "#00d1ff"]],
            zmid=0, text=text, texttemplate="%{text}", textfont={"size": 11},
            hovertemplate="%{y} %{x}<br>Surprise: %{z:.1f}%<extra></extra>",
            colorbar=dict(title="Surprise %"),
        ))
        fig_hm.update_layout(template="plotly_dark", height=380,
                             title="EPS Surprise by Company & Quarter",
                             margin=dict(l=0, r=0, t=40, b=0))
        st.plotly_chart(fig_hm, use_container_width=True, config=PLOTLY_NOBAR)


# ─────────────────────────────────────────────────
# TAB 7: MARKET & POSITIONING
# ─────────────────────────────────────────────────

def _render_market_tab(cfg: SectorConfig, rev_hist, kp):
    # Revenue vs macro overlay
    overlay = cfg.macro_overlay
    st.subheader(f"Revenue vs {overlay['label']}")
    st.caption(f"Sector revenue overlaid with {overlay['label']} — shows macro sensitivity.")

    if not rev_hist.empty:
        macro_series = fetch_fred_series(overlay["fred_series"], periods=504)
        if not macro_series.empty:
            macro_q = macro_series.copy()
            macro_q["quarter"] = macro_q["date"].dt.to_period("Q")
            macro_quarterly = macro_q.groupby("quarter")["value"].mean().reset_index()
            macro_quarterly["date"] = macro_quarterly["quarter"].dt.start_time
            macro_quarterly = macro_quarterly[macro_quarterly["date"] >= "2024-01-01"]
            macro_quarterly["q_label"] = macro_quarterly["date"].dt.strftime("%Y-Q") + ((macro_quarterly["date"].dt.month - 1) // 3 + 1).astype(str)
            sector_rev = rev_hist.groupby("q_label")["revenue"].sum().reset_index()

            fig_macro = make_subplots(specs=[[{"secondary_y": True}]])
            fig_macro.add_trace(go.Bar(
                x=sector_rev["q_label"], y=sector_rev["revenue"] / 1e9,
                name="Sector Revenue ($B)", marker_color="rgba(0,209,255,0.5)",
            ), secondary_y=False)
            merged_macro = macro_quarterly[macro_quarterly["q_label"].isin(sector_rev["q_label"])]
            fig_macro.add_trace(go.Scatter(
                x=merged_macro["q_label"], y=merged_macro["value"],
                name=overlay["label"], mode="lines+markers",
                line=dict(color="#ffaa00", width=3), marker=dict(size=8),
            ), secondary_y=True)
            fig_macro.update_layout(template="plotly_dark", height=400,
                                    title=f"Sector Revenue vs {overlay['label']}",
                                    legend=dict(orientation="h", y=-0.15),
                                    margin=dict(l=0, r=0, t=40, b=0))
            fig_macro.update_yaxes(title_text="Revenue ($B)", secondary_y=False)
            fig_macro.update_yaxes(title_text=overlay["label"], secondary_y=True)
            st.plotly_chart(fig_macro, use_container_width=True, config=PLOTLY_NOBAR)
        else:
            st.info(f"Macro data ({overlay['fred_series']}) requires FRED_API_KEY.")

    # CFTC COT
    if cfg.cot_commodities:
        st.markdown("---")
        st.subheader("Futures Positioning (CFTC COT)")

        cot_names = [c[0] for c in cfg.cot_commodities]
        cot_keys = {c[0]: c[1] for c in cfg.cot_commodities}

        if len(cot_names) > 1:
            cot_commodity = st.radio("Commodity", cot_names, horizontal=True, key=f"{kp}_cot_commodity")
        else:
            cot_commodity = cot_names[0]
        cot_key = cot_keys[cot_commodity]
        cot = fetch_cftc_cot(cot_key, periods=52)

        if not cot.empty and len(cot) > 3:
            _render_cot_analysis(cot, cot_commodity, cot_key, kp)
        else:
            st.info(f"CFTC {cot_commodity} data unavailable or insufficient.")


def _render_cot_analysis(cot, cot_commodity, cot_key, kp):
    """Render full CFTC COT analysis charts."""
    cot["spec_total"] = cot["spec_long"] + cot["spec_short"]
    cot["spec_pct_long"] = cot["spec_long"] / cot["spec_total"] * 100
    cot["wow_change"] = cot["spec_net"].diff()
    cot["spec_net_ma4"] = cot["spec_net"].rolling(4, min_periods=1).mean()

    spec_net_min = cot["spec_net"].min()
    spec_net_max = cot["spec_net"].max()
    spec_net_range = spec_net_max - spec_net_min
    if spec_net_range > 0:
        cot["spec_net_pctile"] = (cot["spec_net"] - spec_net_min) / spec_net_range * 100
    else:
        cot["spec_net_pctile"] = 50

    latest = cot.iloc[-1]

    # Metrics row
    cm1, cm2, cm3, cm4, cm5 = st.columns(5)
    cm1.metric("Spec Net", f"{latest['spec_net']:,.0f}", delta=f"{latest['wow_change']:+,.0f} WoW")
    cm2.metric("Comm Net", f"{latest['comm_net']:,.0f}")
    cm3.metric("Spec % Long", f"{latest['spec_pct_long']:.0f}%")
    cm4.metric("Positioning Percentile", f"{latest['spec_net_pctile']:.0f}th",
               help="0 = most bearish in 52 weeks, 100 = most bullish")
    signal = "Extreme Bullish" if latest["spec_net_pctile"] > 85 else \
             "Bullish" if latest["spec_net_pctile"] > 60 else \
             "Neutral" if latest["spec_net_pctile"] > 40 else \
             "Bearish" if latest["spec_net_pctile"] > 15 else "Extreme Bearish"
    signal_color = "#00ff88" if "Bullish" in signal else "#ff6b6b" if "Bearish" in signal else "#888"
    cm5.markdown(f'<div style="text-align:center;padding-top:8px;"><span style="font-size:1.1rem;'
                 f'font-weight:700;color:{signal_color};">{signal}</span><br>'
                 f'<span style="font-size:0.7rem;color:#888;">52-Week Signal</span></div>',
                 unsafe_allow_html=True)

    # Net positioning trend + price overlay
    fred_map = {"crude_oil": "DCOILWTICO", "natural_gas": "DHHNGSP", "gold": "GOLDAMGBD228NLBM"}
    price_series_id = fred_map.get(cot_key)
    oil_price_series = fetch_fred_series(price_series_id, periods=365) if price_series_id else pd.DataFrame()

    fig_trend = make_subplots(specs=[[{"secondary_y": True}]])
    fig_trend.add_trace(go.Scatter(
        x=cot["date"], y=cot["spec_net"], mode="lines", name="Spec Net",
        line=dict(color="#00d1ff", width=2), fill="tozeroy",
        fillcolor="rgba(0,209,255,0.1)",
    ), secondary_y=False)
    fig_trend.add_trace(go.Scatter(
        x=cot["date"], y=cot["spec_net_ma4"], mode="lines", name="4W MA",
        line=dict(color="#00d1ff", width=1, dash="dot"),
    ), secondary_y=False)
    fig_trend.add_trace(go.Scatter(
        x=cot["date"], y=cot["comm_net"], mode="lines", name="Comm Net",
        line=dict(color="#ffaa00", width=2),
    ), secondary_y=False)

    price_label = cot_commodity
    if not oil_price_series.empty:
        fig_trend.add_trace(go.Scatter(
            x=oil_price_series["date"], y=oil_price_series["value"],
            mode="lines", name=price_label,
            line=dict(color="#ff6b6b", width=2),
        ), secondary_y=True)

    fig_trend.add_hline(y=0, line_dash="dash", line_color="#555", secondary_y=False)
    fig_trend.update_layout(
        template="plotly_dark", height=420,
        title=f"{cot_commodity} — Net Positioning vs Price (52 Weeks)",
        legend=dict(orientation="h", y=-0.15),
        margin=dict(l=0, r=0, t=40, b=0),
    )
    fig_trend.update_yaxes(title_text="Net Contracts", secondary_y=False)
    if not oil_price_series.empty:
        fig_trend.update_yaxes(title_text=price_label, secondary_y=True)
    st.plotly_chart(fig_trend, use_container_width=True, config=PLOTLY_NOBAR)

    # Gross longs/shorts + WoW change
    gs_c1, gs_c2 = st.columns(2)
    with gs_c1:
        fig_gross = go.Figure()
        fig_gross.add_trace(go.Scatter(x=cot["date"], y=cot["spec_long"], mode="lines",
                                       name="Spec Long", line=dict(color="#00d1ff", width=2), fill="tonexty"))
        fig_gross.add_trace(go.Scatter(x=cot["date"], y=cot["spec_short"], mode="lines",
                                       name="Spec Short", line=dict(color="#ff6b6b", width=2)))
        fig_gross.add_trace(go.Scatter(x=cot["date"], y=cot["comm_long"], mode="lines",
                                       name="Comm Long", line=dict(color="#ffaa00", width=1, dash="dash")))
        fig_gross.add_trace(go.Scatter(x=cot["date"], y=cot["comm_short"], mode="lines",
                                       name="Comm Short", line=dict(color="#ff8866", width=1, dash="dash")))
        fig_gross.update_layout(template="plotly_dark", height=360, title="Gross Long/Short Breakdown",
                               yaxis_title="Contracts", legend=dict(orientation="h", y=-0.18),
                               margin=dict(l=0, r=0, t=40, b=0))
        st.plotly_chart(fig_gross, use_container_width=True, config=PLOTLY_NOBAR)

    with gs_c2:
        wow = cot.dropna(subset=["wow_change"]).tail(26)
        if not wow.empty:
            fig_wow = go.Figure()
            fig_wow.add_trace(go.Bar(
                x=wow["date"], y=wow["wow_change"],
                marker_color=["#00d1ff" if v >= 0 else "#ff6b6b" for v in wow["wow_change"]],
            ))
            fig_wow.add_hline(y=0, line_dash="dash", line_color="#555")
            fig_wow.update_layout(template="plotly_dark", height=360,
                                  title="Week-over-Week Change (Spec Net)",
                                  yaxis_title="Contracts", margin=dict(l=0, r=0, t=40, b=0))
            st.plotly_chart(fig_wow, use_container_width=True, config=PLOTLY_NOBAR)

    # Positioning extremes + Spec % long
    ex_c1, ex_c2 = st.columns(2)
    with ex_c1:
        fig_pctile = go.Figure()
        fig_pctile.add_trace(go.Scatter(
            x=cot["date"], y=cot["spec_net_pctile"], mode="lines+markers",
            line=dict(color="#00d1ff", width=2), marker=dict(size=4),
        ))
        fig_pctile.add_hrect(y0=85, y1=100, fillcolor="rgba(0,255,136,0.08)", line_width=0,
                             annotation_text="Extreme Bullish", annotation_position="top left")
        fig_pctile.add_hrect(y0=0, y1=15, fillcolor="rgba(255,107,107,0.08)", line_width=0,
                             annotation_text="Extreme Bearish", annotation_position="bottom left")
        fig_pctile.add_hline(y=50, line_dash="dash", line_color="#555")
        fig_pctile.update_layout(template="plotly_dark", height=360,
                                 title="Positioning Percentile (52-Week Range)",
                                 yaxis_title="Percentile", yaxis=dict(range=[0, 100]),
                                 margin=dict(l=0, r=0, t=40, b=0))
        st.plotly_chart(fig_pctile, use_container_width=True, config=PLOTLY_NOBAR)

    with ex_c2:
        fig_pctlong = go.Figure()
        fig_pctlong.add_trace(go.Scatter(
            x=cot["date"], y=cot["spec_pct_long"], mode="lines+markers",
            line=dict(color="#ffaa00", width=2), marker=dict(size=4),
        ))
        fig_pctlong.add_hline(y=50, line_dash="dash", line_color="#555", annotation_text="Neutral")
        fig_pctlong.update_layout(template="plotly_dark", height=360,
                                  title="Speculator % Long (Sentiment Gauge)",
                                  yaxis_title="% Long", yaxis=dict(range=[0, 100]),
                                  margin=dict(l=0, r=0, t=40, b=0))
        st.plotly_chart(fig_pctlong, use_container_width=True, config=PLOTLY_NOBAR)

    # Spec vs Commercial divergence
    fig_div = go.Figure()
    divergence = cot["spec_net"] - cot["comm_net"]
    fig_div.add_trace(go.Bar(
        x=cot["date"], y=divergence,
        marker_color=["#00d1ff" if v >= 0 else "#ff6b6b" for v in divergence],
        name="Spec - Comm Divergence",
    ))
    fig_div.add_hline(y=0, line_dash="dash", line_color="#555")
    fig_div.update_layout(template="plotly_dark", height=320,
                          title="Speculator vs Commercial Divergence",
                          yaxis_title="Contracts", margin=dict(l=0, r=0, t=40, b=0))
    st.plotly_chart(fig_div, use_container_width=True, config=PLOTLY_NOBAR)


# ─────────────────────────────────────────────────
# TAB 8: PAIRS & CORRELATION
# ─────────────────────────────────────────────────

def _render_pairs_tab(cfg: SectorConfig, kp):
    st.subheader("Pairs Analysis & Correlation")
    st.caption("2-year daily returns — correlation matrix, pair scatter, rolling correlation, and spread analysis.")

    with st.spinner("Loading price history..."):
        prices = fetch_price_history(cfg.tickers, period="2y")

    if not prices.empty and len(prices.columns) >= 2:
        returns = prices.pct_change().dropna()
        corr = returns.corr()
        tickers_avail = sorted(corr.columns.tolist())

        # Pairs Plot Matrix
        n = len(tickers_avail)
        fig_pairs = make_subplots(
            rows=n, cols=n,
            shared_xaxes=False, shared_yaxes=False,
            horizontal_spacing=0.015, vertical_spacing=0.015,
        )

        ret_sampled = returns[tickers_avail]
        if len(ret_sampled) > 200:
            ret_sampled = ret_sampled.sample(200, random_state=42)

        for i, ti in enumerate(tickers_avail):
            for j, tj in enumerate(tickers_avail):
                row, col = i + 1, j + 1
                if i == j:
                    fig_pairs.add_trace(go.Histogram(
                        x=returns[ti], nbinsx=40,
                        marker_color="#00d1ff", opacity=0.7, showlegend=False,
                    ), row=row, col=col)
                elif i < j:
                    fig_pairs.add_trace(go.Scatter(
                        x=ret_sampled[tj], y=ret_sampled[ti],
                        mode="markers",
                        marker=dict(size=2, color="#00d1ff", opacity=0.3),
                        showlegend=False, hoverinfo="skip",
                    ), row=row, col=col)
                else:
                    rho = corr.loc[ti, tj]
                    color = "#00d1ff" if rho > 0.7 else "#ffaa00" if rho > 0.4 else "#ff6b6b"
                    fig_pairs.add_trace(go.Scatter(
                        x=[0.5], y=[0.5], mode="text",
                        text=[f"{rho:.2f}"],
                        textfont=dict(size=max(10, int(abs(rho) * 20)), color=color),
                        showlegend=False, hoverinfo="skip",
                    ), row=row, col=col)
                    fig_pairs.update_xaxes(range=[0, 1], showticklabels=False, showgrid=False, row=row, col=col)
                    fig_pairs.update_yaxes(range=[0, 1], showticklabels=False, showgrid=False, row=row, col=col)

                if col == 1 and i != j:
                    fig_pairs.update_yaxes(title_text=ti, title_font=dict(size=9), row=row, col=col)
                if row == n and i != j:
                    fig_pairs.update_xaxes(title_text=tj, title_font=dict(size=9), row=row, col=col)
                if col > 1:
                    fig_pairs.update_yaxes(showticklabels=False, row=row, col=col)
                if row < n:
                    fig_pairs.update_xaxes(showticklabels=False, row=row, col=col)

        for i, ti in enumerate(tickers_avail):
            fig_pairs.update_xaxes(title_text=ti, title_font=dict(size=10), row=i + 1, col=i + 1)

        fig_pairs.update_layout(
            template="plotly_dark", height=120 * n,
            title="Pairs Plot — Distributions (diagonal) · Scatter (upper) · Correlation (lower)",
            margin=dict(l=40, r=10, t=40, b=30), showlegend=False,
        )
        st.plotly_chart(fig_pairs, use_container_width=True, config=PLOTLY_NOBAR)

        # Pair selector
        st.markdown("---")
        st.markdown("##### Pair Deep Dive")
        pc1, pc2 = st.columns(2)
        with pc1:
            pair_a = st.selectbox("Ticker A", tickers_avail,
                                  index=0, key=f"{kp}_pair_a")
        with pc2:
            pair_b = st.selectbox("Ticker B", tickers_avail,
                                  index=min(1, len(tickers_avail) - 1), key=f"{kp}_pair_b")

        if pair_a != pair_b and pair_a in returns.columns and pair_b in returns.columns:
            ret_a = returns[pair_a]
            ret_b = returns[pair_b]
            pair_corr = ret_a.corr(ret_b)

            pm1, pm2, pm3, pm4 = st.columns(4)
            pm1.metric("Correlation", f"{pair_corr:.3f}")
            pm2.metric("R²", f"{pair_corr**2:.3f}")
            beta = ret_a.cov(ret_b) / ret_a.var() if ret_a.var() > 0 else 0
            pm3.metric(f"Beta ({pair_b} vs {pair_a})", f"{beta:.2f}")
            price_a = prices[pair_a] / prices[pair_a].iloc[0] * 100
            price_b = prices[pair_b] / prices[pair_b].iloc[0] * 100
            spread = price_a - price_b
            pm4.metric("Spread Z-Score", f"{(spread.iloc[-1] - spread.mean()) / spread.std():.2f}"
                       if spread.std() > 0 else "N/A")

            # Scatter + Rolling correlation
            sc_c1, sc_c2 = st.columns(2)
            with sc_c1:
                fig_scatter = go.Figure()
                fig_scatter.add_trace(go.Scatter(
                    x=ret_a, y=ret_b, mode="markers",
                    marker=dict(size=3, color="#00d1ff", opacity=0.4),
                    hovertemplate=f"{pair_a}: %{{x:.2%}}<br>{pair_b}: %{{y:.2%}}<extra></extra>",
                ))
                if len(ret_a) > 10:
                    coeffs = np.polyfit(ret_a, ret_b, 1)
                    x_line = np.linspace(ret_a.min(), ret_a.max(), 100)
                    fig_scatter.add_trace(go.Scatter(
                        x=x_line, y=np.polyval(coeffs, x_line),
                        mode="lines", line=dict(color="#ff6b6b", width=2, dash="dash"),
                        name=f"β={coeffs[0]:.2f}",
                    ))
                fig_scatter.update_layout(
                    template="plotly_dark", height=400,
                    title=f"{pair_a} vs {pair_b} — Daily Returns (ρ = {pair_corr:.3f})",
                    xaxis_title=f"{pair_a} Return", yaxis_title=f"{pair_b} Return",
                    margin=dict(l=0, r=0, t=40, b=0),
                )
                st.plotly_chart(fig_scatter, use_container_width=True, config=PLOTLY_NOBAR)

            with sc_c2:
                fig_roll = go.Figure()
                for window, color, dash in [(21, "#00d1ff", "solid"), (63, "#ffaa00", "dash")]:
                    if len(ret_a) < window:
                        continue
                    roll = ret_a.rolling(window).corr(ret_b).dropna()
                    fig_roll.add_trace(go.Scatter(
                        x=roll.index, y=roll.values, mode="lines",
                        name=f"{window}D Rolling ρ",
                        line=dict(color=color, width=2, dash=dash),
                    ))
                fig_roll.add_hline(y=pair_corr, line_dash="dot", line_color="#555",
                                   annotation_text=f"Full period: {pair_corr:.2f}")
                fig_roll.add_hline(y=0, line_dash="dash", line_color="#333")
                fig_roll.update_layout(
                    template="plotly_dark", height=400,
                    title=f"Rolling Correlation — {pair_a} vs {pair_b}",
                    yaxis_title="Correlation", yaxis=dict(range=[-0.5, 1.1]),
                    legend=dict(orientation="h", y=-0.15),
                    margin=dict(l=0, r=0, t=40, b=0),
                )
                st.plotly_chart(fig_roll, use_container_width=True, config=PLOTLY_NOBAR)

            # Normalized price + spread
            sp_c1, sp_c2 = st.columns(2)
            with sp_c1:
                fig_norm = go.Figure()
                fig_norm.add_trace(go.Scatter(x=price_a.index, y=price_a, mode="lines",
                                              name=pair_a, line=dict(color="#00d1ff", width=2)))
                fig_norm.add_trace(go.Scatter(x=price_b.index, y=price_b, mode="lines",
                                              name=pair_b, line=dict(color="#ffaa00", width=2)))
                fig_norm.update_layout(template="plotly_dark", height=360,
                                       title="Normalized Price (base = 100)",
                                       yaxis_title="Indexed Price",
                                       legend=dict(orientation="h", y=-0.15),
                                       margin=dict(l=0, r=0, t=40, b=0))
                st.plotly_chart(fig_norm, use_container_width=True, config=PLOTLY_NOBAR)

            with sp_c2:
                spread_z = (spread - spread.rolling(63).mean()) / spread.rolling(63).std()
                fig_spread = go.Figure()
                fig_spread.add_trace(go.Scatter(
                    x=spread_z.index, y=spread_z, mode="lines",
                    line=dict(color="#00d1ff", width=2),
                    fill="tozeroy", fillcolor="rgba(0,209,255,0.08)",
                ))
                fig_spread.add_hline(y=2, line_dash="dash", line_color="#ff6b6b",
                                     annotation_text="+2σ (Overextended)")
                fig_spread.add_hline(y=-2, line_dash="dash", line_color="#00ff88",
                                     annotation_text="-2σ (Underextended)")
                fig_spread.add_hline(y=0, line_dash="dash", line_color="#555")
                fig_spread.update_layout(template="plotly_dark", height=360,
                                         title=f"Spread Z-Score ({pair_a} − {pair_b})",
                                         yaxis_title="Z-Score",
                                         margin=dict(l=0, r=0, t=40, b=0))
                st.plotly_chart(fig_spread, use_container_width=True, config=PLOTLY_NOBAR)

            # Return distribution comparison
            fig_dist = go.Figure()
            for ticker, color in [(pair_a, "#00d1ff"), (pair_b, "#ffaa00")]:
                fig_dist.add_trace(go.Histogram(
                    x=returns[ticker], name=ticker, marker_color=color,
                    opacity=0.6, nbinsx=80,
                    hovertemplate=f"{ticker}<br>Return: %{{x:.2%}}<br>Count: %{{y}}<extra></extra>",
                ))
            fig_dist.update_layout(
                template="plotly_dark", height=320, barmode="overlay",
                title=f"Return Distribution — {pair_a} vs {pair_b}",
                xaxis_title="Daily Return", yaxis_title="Frequency",
                legend=dict(orientation="h", y=-0.15),
                margin=dict(l=0, r=0, t=40, b=0),
            )
            st.plotly_chart(fig_dist, use_container_width=True, config=PLOTLY_NOBAR)
        elif pair_a == pair_b:
            st.info("Select two different tickers to analyze the pair.")

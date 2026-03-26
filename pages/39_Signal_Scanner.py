"""
Systematic Signal Scanner — Cross-Sectional Factor Signals

Scans the investable universe for systematic trading signals:
1. Momentum — 12-1 month cross-sectional momentum (Jegadeesh & Titman)
2. Mean Reversion — RSI extremes, Bollinger Band deviation, z-score
3. Value — P/E, FCF yield, dividend yield relative to sector median
4. Carry — dividend yield spread, term structure carry
5. Quality — ROE, margin stability, earnings consistency
6. Composite — combined multi-factor rank

Tabs:
1. Signal Dashboard — all signals on one heatmap
2. Momentum — detailed momentum analysis
3. Mean Reversion — oversold/overbought detection
4. Composite Ranking — multi-factor combined score
"""
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import logging
from src.layout import setup_page, error_boundary
from src.market_data import (
    fetch_energy_price_history as fetch_price_history,
    fetch_energy_valuation_data as fetch_valuation_data,
    fetch_momentum_data,
)
from src.styles import COLORS

logger = logging.getLogger(__name__)
setup_page("39_Signal_Scanner")

st.title("Systematic Signal Scanner")
st.markdown("Cross-sectional momentum, mean reversion, value, and quality signals across the investable universe.")

PLOTLY_NOBAR = {"displayModeBar": False}

# ═══════════════════════════════════════════════
# UNIVERSE DEFINITIONS
# ═══════════════════════════════════════════════

UNIVERSES = {
    "S&P 500 Sectors": ["XLK", "XLF", "XLE", "XLV", "XLI", "XLU", "XLP", "XLY", "XLC", "XLB", "XLRE"],
    "Mega Caps": ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "BRK-B", "JPM", "V",
                  "UNH", "LLY", "XOM", "JNJ", "PG", "MA", "HD", "COST", "ABBV", "MRK"],
    "Multi-Asset": ["SPY", "QQQ", "IWM", "EFA", "EEM", "TLT", "IEF", "LQD", "HYG",
                    "GLD", "SLV", "USO", "UNG", "DBA", "VNQ", "VIXY"],
    "Custom": [],
}

# ═══════════════════════════════════════════════
# CONTROLS
# ═══════════════════════════════════════════════

c1, c2, c3 = st.columns([2, 1, 1])
with c1:
    universe_name = st.selectbox("Universe", list(UNIVERSES.keys()), key="ss_universe")
    if universe_name == "Custom":
        custom_raw = st.text_input("Custom tickers (comma-separated)", "SPY,QQQ,TLT,GLD", key="ss_custom")
        tickers = [t.strip().upper() for t in custom_raw.split(",") if t.strip()]
    else:
        tickers = UNIVERSES[universe_name]
with c2:
    ss_lookback = st.selectbox("Lookback", ["6M", "1Y", "2Y"], index=1, key="ss_lookback")
    lookback_map = {"6M": "6mo", "1Y": "1y", "2Y": "2y"}
with c3:
    st.markdown("<br>", unsafe_allow_html=True)
    ss_load = st.button("Scan Signals", type="primary", use_container_width=True, key="ss_load")

if ss_load:
    st.session_state["ss_loaded"] = True
if not st.session_state.get("ss_loaded"):
    st.info(f"Select a universe ({len(tickers)} assets) and click **Scan Signals**.")
    st.stop()

if len(tickers) < 3:
    st.error("Need at least 3 tickers.")
    st.stop()


# ═══════════════════════════════════════════════
# DATA LOADING
# ═══════════════════════════════════════════════

with st.spinner(f"Loading {len(tickers)} assets..."):
    prices = fetch_price_history(tickers, period=lookback_map[ss_lookback])

if prices.empty or len(prices.columns) < 3:
    st.error("Insufficient price data.")
    st.stop()

returns = prices.pct_change().dropna()
avail_tickers = sorted(prices.columns.tolist())
n = len(avail_tickers)


# ═══════════════════════════════════════════════
# COMPUTE ALL SIGNALS
# ═══════════════════════════════════════════════

signals = pd.DataFrame(index=avail_tickers)

# ── MOMENTUM ──
for period, days in [("1M", 21), ("3M", 63), ("6M", 126), ("12M", 252)]:
    if len(prices) >= days:
        mom = (prices.iloc[-1] / prices.iloc[-days] - 1) * 100
        signals[f"Mom_{period}"] = mom

# 12-1 month momentum (skip most recent month)
if len(prices) >= 252:
    mom_12_1 = (prices.iloc[-21] / prices.iloc[-252] - 1) * 100
    signals["Mom_12-1"] = mom_12_1

# ── MEAN REVERSION ──
# RSI-14
for t in avail_tickers:
    delta = prices[t].diff()
    gain = delta.where(delta > 0, 0.0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(14).mean().replace(0, np.nan)
    rs = gain / loss
    rsi = (100 - 100 / (1 + rs)).iloc[-1]
    signals.loc[t, "RSI_14"] = rsi if not np.isnan(rsi) else np.nan

# Bollinger Band position (0-100: 0=lower band, 100=upper band)
for t in avail_tickers:
    ma = prices[t].rolling(20).mean()
    std = prices[t].rolling(20).std()
    if std.iloc[-1] > 0:
        bb_pos = (prices[t].iloc[-1] - (ma.iloc[-1] - 2 * std.iloc[-1])) / (4 * std.iloc[-1]) * 100
        signals.loc[t, "BB_Position"] = np.clip(bb_pos, 0, 100)
    else:
        signals.loc[t, "BB_Position"] = 50

# Z-score (current price vs 63D mean/std)
for t in avail_tickers:
    mu_63 = prices[t].rolling(63).mean().iloc[-1]
    std_63 = prices[t].rolling(63).std().iloc[-1]
    if std_63 > 0:
        signals.loc[t, "Z_Score_63D"] = (prices[t].iloc[-1] - mu_63) / std_63
    else:
        signals.loc[t, "Z_Score_63D"] = np.nan

# ── VOLATILITY ──
for t in avail_tickers:
    signals.loc[t, "Vol_20D"] = returns[t].tail(20).std() * np.sqrt(252) * 100
    if len(returns) >= 63:
        vol_20 = returns[t].tail(20).std()
        vol_63 = returns[t].tail(63).std()
        signals.loc[t, "Vol_Ratio"] = vol_20 / vol_63 if vol_63 > 0 else 1

# ── DRAWDOWN ──
for t in avail_tickers:
    cum = prices[t]
    dd = ((cum / cum.cummax()) - 1).iloc[-1] * 100
    signals.loc[t, "Drawdown"] = dd

# ── VALUATION (if available) ──
with st.spinner("Loading valuation data..."):
    val_df = fetch_valuation_data(avail_tickers)
if not val_df.empty:
    for _, row in val_df.iterrows():
        t = row["ticker"]
        if t in signals.index:
            if pd.notna(row.get("forward_pe")):
                signals.loc[t, "Fwd_PE"] = row["forward_pe"]
            if pd.notna(row.get("dividend_yield")):
                signals.loc[t, "Div_Yield"] = row["dividend_yield"]
            if pd.notna(row.get("fcf_yield")):
                signals.loc[t, "FCF_Yield"] = row["fcf_yield"]

# ═══════════════════════════════════════════════
# COMPUTE RANKS (cross-sectional percentile)
# ═══════════════════════════════════════════════

ranks = pd.DataFrame(index=avail_tickers)

# Momentum: higher = better rank
for col in [c for c in signals.columns if c.startswith("Mom_")]:
    ranks[col] = signals[col].rank(pct=True) * 100

# RSI: middle is neutral, extremes are signals
ranks["RSI_Signal"] = signals["RSI_14"].apply(
    lambda v: 100 if v < 30 else 0 if v > 70 else 50 if pd.notna(v) else np.nan
)

# Z-Score: negative = oversold (buy signal)
ranks["MeanRev"] = (-signals["Z_Score_63D"]).rank(pct=True) * 100

# Value: lower PE = better, higher yield = better
if "Fwd_PE" in signals.columns:
    ranks["Value_PE"] = (-signals["Fwd_PE"]).rank(pct=True) * 100
if "FCF_Yield" in signals.columns:
    ranks["Value_FCF"] = signals["FCF_Yield"].rank(pct=True) * 100
if "Div_Yield" in signals.columns:
    ranks["Carry"] = signals["Div_Yield"].rank(pct=True) * 100

# Volatility: lower = better (quality proxy)
ranks["LowVol"] = (-signals["Vol_20D"]).rank(pct=True) * 100

# Composite
rank_cols = [c for c in ranks.columns]
if rank_cols:
    ranks["Composite"] = ranks[rank_cols].mean(axis=1, skipna=True)


# ═══════════════════════════════════════════════
# TABS
# ═══════════════════════════════════════════════

tab_dashboard, tab_momentum, tab_meanrev, tab_composite = st.tabs([
    "Signal Dashboard",
    "Momentum",
    "Mean Reversion",
    "Composite Ranking",
])


# ═══════════════════════════════════════════════
# TAB 1: SIGNAL DASHBOARD
# ═══════════════════════════════════════════════
with tab_dashboard, error_boundary("Signal Dashboard"):
    st.subheader("Multi-Factor Signal Heatmap")

    with st.expander("How to read this tab", expanded=False):
        st.markdown(
            "Each cell shows a **cross-sectional percentile rank** (0-100) for that signal.\n\n"
            "- **Green (high rank)**: strong signal in that factor's direction\n"
            "- **Red (low rank)**: weak/negative signal\n"
            "- **Momentum columns**: higher = stronger price trend\n"
            "- **RSI Signal**: 100 = oversold (buy), 0 = overbought (sell)\n"
            "- **MeanRev**: 100 = most oversold vs 63D mean (mean reversion buy)\n"
            "- **Value/Carry**: 100 = cheapest/highest yield\n"
            "- **LowVol**: 100 = lowest volatility (quality proxy)\n"
            "- **Composite**: equal-weight average of all available signals\n\n"
            "Assets with high composite rank across multiple factors are the strongest candidates."
        )

    # Sort by composite
    display_cols = [c for c in ranks.columns if c != "Composite"] + ["Composite"]
    display_ranks = ranks[display_cols].sort_values("Composite", ascending=False)

    fig_hm = go.Figure(data=go.Heatmap(
        z=display_ranks.values,
        x=display_cols,
        y=display_ranks.index.tolist(),
        colorscale=[[0, "#ff4444"], [0.5, "#1a1a2e"], [1, "#00ff88"]],
        zmid=50, zmin=0, zmax=100,
        text=[[f"{v:.0f}" for v in row] for row in display_ranks.values],
        texttemplate="%{text}", textfont={"size": 10},
        hovertemplate="%{y} — %{x}: %{z:.0f}th percentile<extra></extra>",
        colorbar=dict(title="Rank"),
    ))
    fig_hm.update_layout(template="plotly_dark", height=max(400, n * 28),
                          title=f"Cross-Sectional Signal Ranks — {universe_name} ({n} assets)",
                          margin=dict(l=0, r=0, t=40, b=0))
    st.plotly_chart(fig_hm, use_container_width=True, config=PLOTLY_NOBAR)

    # Raw signal values table
    with st.expander("Raw Signal Values"):
        raw_display = signals.copy()
        for col in raw_display.columns:
            raw_display[col] = raw_display[col].apply(
                lambda v: f"{v:.1f}" if pd.notna(v) else "—"
            )
        st.dataframe(raw_display, use_container_width=True)


# ═══════════════════════════════════════════════
# TAB 2: MOMENTUM
# ═══════════════════════════════════════════════
with tab_momentum, error_boundary("Momentum"):
    st.subheader("Cross-Sectional Momentum")

    with st.expander("How to read this tab", expanded=False):
        st.markdown(
            "**Cross-sectional momentum** ranks assets by their past returns relative to each other. "
            "The strongest documented equity anomaly (Jegadeesh & Titman, 1993).\n\n"
            "**12-1 month momentum** (skip the most recent month) is the standard academic signal — "
            "the last month often reverses (short-term mean reversion), so skipping it improves the signal.\n\n"
            "**Momentum heatmap** shows return ranks across timeframes. "
            "Assets that rank highly across ALL timeframes have persistent momentum. "
            "Assets with divergent ranks (e.g., strong 1M but weak 12M) may be experiencing a reversal.\n\n"
            "**Momentum spread** (long top quintile, short bottom quintile) shows whether "
            "the momentum factor is currently working in this universe."
        )

    mom_cols = [c for c in signals.columns if c.startswith("Mom_")]
    if mom_cols:
        mom_data = signals[mom_cols].sort_values(mom_cols[-1] if mom_cols else mom_cols[0], ascending=False)

        fig_mom = go.Figure(data=go.Heatmap(
            z=mom_data.values,
            x=[c.replace("Mom_", "") for c in mom_cols],
            y=mom_data.index.tolist(),
            colorscale=[[0, "#ff4444"], [0.5, "#1a1a2e"], [1, "#00d1ff"]],
            zmid=0,
            text=[[f"{v:+.1f}%" for v in row] for row in mom_data.values],
            texttemplate="%{text}", textfont={"size": 11},
            colorbar=dict(title="Return %"),
        ))
        fig_mom.update_layout(template="plotly_dark", height=max(350, n * 25),
                              title="Momentum Returns by Timeframe",
                              margin=dict(l=0, r=0, t=40, b=0))
        st.plotly_chart(fig_mom, use_container_width=True, config=PLOTLY_NOBAR)

        # Top/bottom momentum
        if "Mom_12-1" in signals.columns:
            mom_col = "Mom_12-1"
        elif "Mom_6M" in signals.columns:
            mom_col = "Mom_6M"
        else:
            mom_col = mom_cols[-1]

        sorted_mom = signals[mom_col].sort_values(ascending=True)

        fig_bar = go.Figure()
        fig_bar.add_trace(go.Bar(
            y=sorted_mom.index, x=sorted_mom.values, orientation="h",
            marker_color=["#00d1ff" if v >= 0 else "#ff4444" for v in sorted_mom],
            text=[f"{v:+.1f}%" for v in sorted_mom], textposition="outside",
        ))
        fig_bar.add_vline(x=0, line_dash="dash", line_color="#555")
        fig_bar.update_layout(template="plotly_dark", height=max(350, n * 25),
                              title=f"{mom_col} Momentum Ranking",
                              xaxis_title="Return (%)",
                              margin=dict(l=0, r=80, t=40, b=0))
        st.plotly_chart(fig_bar, use_container_width=True, config=PLOTLY_NOBAR)

        # Momentum spread (long top quintile - short bottom quintile)
        if len(avail_tickers) >= 5 and len(returns) >= 126:
            st.subheader("Momentum Spread (L/S)")
            st.caption("Cumulative return of going long the top 20% momentum and short the bottom 20%. "
                       "Rising line = momentum factor is working. Flat or declining = momentum is broken.")

            quintile_size = max(1, n // 5)
            # Rebalance monthly
            rebal_dates = returns.resample("ME").last().index
            ls_returns = []
            for i in range(max(6, len(returns) // 21), len(rebal_dates)):
                rd = rebal_dates[i]
                # Rank by trailing 12-1 month momentum
                rd_loc = returns.index.get_loc(rd)
                if rd_loc < 252:
                    continue
                trail_ret = (prices.iloc[rd_loc - 21] / prices.iloc[rd_loc - 252] - 1)
                ranked = trail_ret.sort_values(ascending=False)
                longs = ranked.head(quintile_size).index.tolist()
                shorts = ranked.tail(quintile_size).index.tolist()

                # Next month return
                if i < len(rebal_dates) - 1:
                    next_rd = rebal_dates[i + 1]
                    period_ret = returns.loc[rd:next_rd]
                else:
                    period_ret = returns.loc[rd:]

                for dt, row in period_ret.iterrows():
                    long_ret = row[longs].mean() if longs else 0
                    short_ret = row[shorts].mean() if shorts else 0
                    ls_returns.append({"date": dt, "return": long_ret - short_ret})

            if ls_returns:
                ls_df = pd.DataFrame(ls_returns).set_index("date")
                ls_df = ls_df[~ls_df.index.duplicated(keep="first")]
                ls_cum = (1 + ls_df["return"]).cumprod() * 100

                fig_ls = go.Figure()
                fig_ls.add_trace(go.Scatter(x=ls_cum.index, y=ls_cum, mode="lines",
                                            line=dict(color="#00d1ff", width=2), name="Momentum L/S"))
                fig_ls.add_hline(y=100, line_dash="dash", line_color="#333")
                fig_ls.update_layout(template="plotly_dark", height=300,
                                     title="Momentum Long/Short Spread (top 20% - bottom 20%)",
                                     yaxis_title="Indexed (100=start)",
                                     margin=dict(l=0, r=0, t=40, b=0))
                st.plotly_chart(fig_ls, use_container_width=True, config=PLOTLY_NOBAR)

                ls_metrics = {
                    "Ann. Return": f"{ls_df['return'].mean() * 252 * 100:.1f}%",
                    "Ann. Vol": f"{ls_df['return'].std() * np.sqrt(252) * 100:.1f}%",
                    "Sharpe": f"{ls_df['return'].mean() / ls_df['return'].std() * np.sqrt(252):.2f}" if ls_df['return'].std() > 0 else "N/A",
                }
                lm1, lm2, lm3 = st.columns(3)
                lm1.metric("Spread Return", ls_metrics["Ann. Return"])
                lm2.metric("Spread Vol", ls_metrics["Ann. Vol"])
                lm3.metric("Spread Sharpe", ls_metrics["Sharpe"])


# ═══════════════════════════════════════════════
# TAB 3: MEAN REVERSION
# ═══════════════════════════════════════════════
with tab_meanrev, error_boundary("Mean Reversion"):
    st.subheader("Mean Reversion Signals")

    with st.expander("How to read this tab", expanded=False):
        st.markdown(
            "Mean reversion signals identify assets that have deviated from their recent average "
            "and may snap back.\n\n"
            "**RSI < 30** = oversold (potential buy). **RSI > 70** = overbought (potential sell).\n\n"
            "**Bollinger Band position** 0-100: near 0 = at lower band (oversold), near 100 = at upper band (overbought).\n\n"
            "**Z-Score** measures how many standard deviations the current price is from its 63-day mean. "
            "|Z| > 2 is extreme. Negative Z = below average (potential long). Positive Z = above average (potential short).\n\n"
            "**Combine signals**: assets that are oversold on RSI AND have negative Z-score AND are near the lower "
            "Bollinger Band have the strongest mean reversion setup."
        )

    mr_c1, mr_c2, mr_c3 = st.columns(3)

    with mr_c1:
        rsi_sorted = signals["RSI_14"].sort_values()
        fig_rsi = go.Figure()
        fig_rsi.add_trace(go.Bar(
            y=rsi_sorted.index, x=rsi_sorted.values, orientation="h",
            marker_color=["#00ff88" if v < 30 else "#ff4444" if v > 70 else "#555" for v in rsi_sorted],
            text=[f"{v:.0f}" for v in rsi_sorted], textposition="outside",
        ))
        fig_rsi.add_vline(x=30, line_dash="dash", line_color="#00ff88", annotation_text="Oversold")
        fig_rsi.add_vline(x=70, line_dash="dash", line_color="#ff4444", annotation_text="Overbought")
        fig_rsi.update_layout(template="plotly_dark", height=max(300, n * 22),
                              title="RSI-14", xaxis=dict(range=[0, 100]),
                              margin=dict(l=0, r=40, t=40, b=0))
        st.plotly_chart(fig_rsi, use_container_width=True, config=PLOTLY_NOBAR)

    with mr_c2:
        bb_sorted = signals["BB_Position"].sort_values()
        fig_bb = go.Figure()
        fig_bb.add_trace(go.Bar(
            y=bb_sorted.index, x=bb_sorted.values, orientation="h",
            marker_color=["#00ff88" if v < 20 else "#ff4444" if v > 80 else "#555" for v in bb_sorted],
            text=[f"{v:.0f}" for v in bb_sorted], textposition="outside",
        ))
        fig_bb.add_vline(x=20, line_dash="dash", line_color="#00ff88")
        fig_bb.add_vline(x=80, line_dash="dash", line_color="#ff4444")
        fig_bb.update_layout(template="plotly_dark", height=max(300, n * 22),
                              title="Bollinger Position (0-100)", xaxis=dict(range=[0, 100]),
                              margin=dict(l=0, r=40, t=40, b=0))
        st.plotly_chart(fig_bb, use_container_width=True, config=PLOTLY_NOBAR)

    with mr_c3:
        z_sorted = signals["Z_Score_63D"].sort_values()
        fig_z = go.Figure()
        fig_z.add_trace(go.Bar(
            y=z_sorted.index, x=z_sorted.values, orientation="h",
            marker_color=["#00ff88" if v < -2 else "#ff4444" if v > 2 else "#00d1ff" if v < 0 else "#ffaa00"
                          for v in z_sorted],
            text=[f"{v:+.1f}" for v in z_sorted], textposition="outside",
        ))
        fig_z.add_vline(x=-2, line_dash="dash", line_color="#00ff88", annotation_text="-2σ")
        fig_z.add_vline(x=2, line_dash="dash", line_color="#ff4444", annotation_text="+2σ")
        fig_z.add_vline(x=0, line_dash="dash", line_color="#333")
        fig_z.update_layout(template="plotly_dark", height=max(300, n * 22),
                              title="Price Z-Score (63D)", margin=dict(l=0, r=40, t=40, b=0))
        st.plotly_chart(fig_z, use_container_width=True, config=PLOTLY_NOBAR)

    # Oversold/overbought alerts
    st.subheader("Signal Alerts")
    oversold = signals[(signals["RSI_14"].fillna(50) < 30) | (signals["Z_Score_63D"].fillna(0) < -2)]
    overbought = signals[(signals["RSI_14"].fillna(50) > 70) | (signals["Z_Score_63D"].fillna(0) > 2)]

    al1, al2 = st.columns(2)
    with al1:
        st.markdown("**Oversold (potential longs)**")
        if not oversold.empty:
            for t in oversold.index:
                rsi = signals.loc[t, "RSI_14"]
                z = signals.loc[t, "Z_Score_63D"]
                dd = signals.loc[t, "Drawdown"]
                rsi_s = f"{rsi:.0f}" if pd.notna(rsi) else "N/A"
                z_s = f"{z:+.1f}" if pd.notna(z) else "N/A"
                dd_s = f"{dd:.1f}%" if pd.notna(dd) else "N/A"
                st.markdown(f"- **{t}**: RSI={rsi_s}, Z={z_s}, DD={dd_s}")
        else:
            st.caption("No oversold assets detected.")
    with al2:
        st.markdown("**Overbought (potential shorts/trims)**")
        if not overbought.empty:
            for t in overbought.index:
                rsi = signals.loc[t, "RSI_14"]
                z = signals.loc[t, "Z_Score_63D"]
                rsi_s = f"{rsi:.0f}" if pd.notna(rsi) else "N/A"
                z_s = f"{z:+.1f}" if pd.notna(z) else "N/A"
                st.markdown(f"- **{t}**: RSI={rsi_s}, Z={z_s}")
        else:
            st.caption("No overbought assets detected.")


# ═══════════════════════════════════════════════
# TAB 4: COMPOSITE RANKING
# ═══════════════════════════════════════════════
with tab_composite, error_boundary("Composite Ranking"):
    st.subheader("Multi-Factor Composite Score")

    with st.expander("How to read this tab", expanded=False):
        st.markdown(
            "The composite score equally weights all available factor signals into a single rank.\n\n"
            "**Top-ranked assets** score highly across momentum, value, carry, low-vol, and mean reversion — "
            "they're the strongest multi-factor candidates.\n\n"
            "**Factor profile** shows which factors drive each asset's composite score. "
            "An asset ranked #1 by momentum but #20 by value has a different risk profile than one ranked "
            "in the middle on everything.\n\n"
            "**Use this for portfolio construction**: long the top quintile, or tilt HRP/risk parity weights "
            "toward high-composite assets."
        )

    if "Composite" in ranks.columns:
        comp_sorted = ranks["Composite"].sort_values(ascending=True)

        fig_comp = go.Figure()
        fig_comp.add_trace(go.Bar(
            y=comp_sorted.index, x=comp_sorted.values, orientation="h",
            marker_color=["#00ff88" if v > 70 else "#00d1ff" if v > 50 else "#ffaa00" if v > 30 else "#ff4444"
                          for v in comp_sorted],
            text=[f"{v:.0f}" for v in comp_sorted], textposition="outside",
        ))
        fig_comp.add_vline(x=50, line_dash="dash", line_color="#555", annotation_text="Median")
        fig_comp.update_layout(template="plotly_dark", height=max(400, n * 28),
                               title="Composite Multi-Factor Score (0-100)",
                               xaxis_title="Score", xaxis=dict(range=[0, 100]),
                               margin=dict(l=0, r=50, t=40, b=0))
        st.plotly_chart(fig_comp, use_container_width=True, config=PLOTLY_NOBAR)

        # Factor profile for top assets
        st.subheader("Factor Profile — Top 5")
        st.caption("Which factors drive each top asset's ranking.")
        top5 = ranks.sort_values("Composite", ascending=False).head(5)
        profile_cols = [c for c in top5.columns if c != "Composite"]

        fig_profile = go.Figure(data=go.Heatmap(
            z=top5[profile_cols].values,
            x=profile_cols, y=top5.index.tolist(),
            colorscale=[[0, "#ff4444"], [0.5, "#1a1a2e"], [1, "#00ff88"]],
            zmid=50, zmin=0, zmax=100,
            text=[[f"{v:.0f}" for v in row] for row in top5[profile_cols].values],
            texttemplate="%{text}", textfont={"size": 11},
            colorbar=dict(title="Rank"),
        ))
        fig_profile.update_layout(template="plotly_dark", height=250,
                                   title="Factor Rank Profile — Top 5 Assets",
                                   margin=dict(l=0, r=0, t=40, b=0))
        st.plotly_chart(fig_profile, use_container_width=True, config=PLOTLY_NOBAR)

        # Signal conviction table
        st.subheader("Full Ranking Table")
        full_table = pd.DataFrame({
            "Ticker": ranks.index,
            "Composite": ranks["Composite"].values,
        })
        for col in ["Mom_12-1", "Mom_3M"]:
            if col in signals.columns:
                full_table[col] = signals[col].values
        full_table["RSI"] = signals["RSI_14"].values
        full_table["Z-Score"] = signals["Z_Score_63D"].values
        full_table["Vol (20D)"] = signals["Vol_20D"].values
        full_table["Drawdown"] = signals["Drawdown"].values
        if "Fwd_PE" in signals.columns:
            full_table["Fwd P/E"] = signals["Fwd_PE"].values

        full_table = full_table.sort_values("Composite", ascending=False)
        # Format
        full_table["Composite"] = full_table["Composite"].apply(lambda v: f"{v:.0f}")
        for col in [c for c in full_table.columns if c.startswith("Mom_")]:
            full_table[col] = full_table[col].apply(lambda v: f"{v:+.1f}%" if pd.notna(v) else "—")
        full_table["RSI"] = full_table["RSI"].apply(lambda v: f"{v:.0f}")
        full_table["Z-Score"] = full_table["Z-Score"].apply(lambda v: f"{v:+.1f}")
        full_table["Vol (20D)"] = full_table["Vol (20D)"].apply(lambda v: f"{v:.0f}%")
        full_table["Drawdown"] = full_table["Drawdown"].apply(lambda v: f"{v:.1f}%")
        if "Fwd P/E" in full_table.columns:
            full_table["Fwd P/E"] = full_table["Fwd P/E"].apply(lambda v: f"{v:.1f}x" if pd.notna(v) else "—")

        st.dataframe(full_table, use_container_width=True, hide_index=True)

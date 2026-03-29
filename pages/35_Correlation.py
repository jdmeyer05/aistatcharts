import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import logging
from src.layout import setup_page, error_boundary
from src.market_data import fetch_energy_price_history as fetch_price_history
from src.styles import COLORS

logger = logging.getLogger(__name__)
setup_page("35_Correlation")

st.title("Cross-Asset Correlation")
st.markdown("Rolling correlations, regime analysis, clustering, PCA decomposition, and correlation breakdown alerts across asset classes.")

# ═══════════════════════════════════════════════
# ASSET UNIVERSE
# ═══════════════════════════════════════════════

ASSET_CLASSES = {
    "US Equities": {
        "SPY": "S&P 500", "QQQ": "Nasdaq 100", "IWM": "Russell 2000",
        "DIA": "Dow 30", "MDY": "S&P 400 Mid",
    },
    "Sectors": {
        "XLK": "Technology", "XLF": "Financials", "XLE": "Energy",
        "XLV": "Healthcare", "XLI": "Industrials", "XLU": "Utilities",
        "XLP": "Staples", "XLY": "Discretionary", "XLC": "Comms",
        "XLB": "Materials", "XLRE": "Real Estate",
    },
    "Fixed Income": {
        "TLT": "20Y Treasury", "IEF": "7-10Y Treasury", "SHY": "1-3Y Treasury",
        "LQD": "IG Corporate", "HYG": "High Yield", "TIP": "TIPS",
        "EMB": "EM Bonds",
    },
    "Commodities": {
        "GLD": "Gold", "SLV": "Silver", "USO": "Crude Oil",
        "UNG": "Natural Gas", "DBA": "Agriculture", "CPER": "Copper",
    },
    "International": {
        "EFA": "Developed Intl", "EEM": "Emerging Mkts",
        "FXI": "China", "EWJ": "Japan", "VGK": "Europe",
    },
    "Volatility & Alt": {
        "VIXY": "VIX Short-Term", "GDX": "Gold Miners",
        "XBI": "Biotech", "ARKK": "Innovation",
    },
}

# Flatten
ALL_ASSETS = {}
ASSET_CLASS_MAP = {}
for cls, tickers in ASSET_CLASSES.items():
    for t, name in tickers.items():
        ALL_ASSETS[t] = name
        ASSET_CLASS_MAP[t] = cls

CLASS_COLORS = {
    "US Equities": "#00d1ff", "Sectors": "#00ff88", "Fixed Income": "#ffaa00",
    "Commodities": "#ff6b6b", "International": "#ff00ff", "Volatility & Alt": "#88ccff",
}

PLOTLY_NOBAR = {"displayModeBar": False}


# ═══════════════════════════════════════════════
# CONTROLS
# ═══════════════════════════════════════════════

c1, c2, c3 = st.columns([2, 1, 1])
with c1:
    selected_classes = st.multiselect(
        "Asset Classes", list(ASSET_CLASSES.keys()),
        default=["US Equities", "Fixed Income", "Commodities", "Volatility & Alt"],
        key="corr_classes",
    )
with c2:
    lookback_label = st.selectbox("Lookback", ["6M", "1Y", "2Y", "3Y"], index=1, key="corr_lookback")
    lookback_map = {"6M": "6mo", "1Y": "1y", "2Y": "2y", "3Y": "3y"}
    lookback = lookback_map[lookback_label]
with c3:
    st.markdown("<br>", unsafe_allow_html=True)
    load_btn = st.button("Load Data", type="primary", use_container_width=True, key="corr_load")

# Build ticker list from selected classes
selected_tickers = []
for cls in selected_classes:
    selected_tickers.extend(ASSET_CLASSES[cls].keys())

if load_btn:
    st.session_state["corr_loaded"] = True

if not st.session_state.get("corr_loaded"):
    st.info(f"Select asset classes and click **Load Data** to fetch {len(selected_tickers)} assets.")
    st.stop()


# ═══════════════════════════════════════════════
# DATA LOADING
# ═══════════════════════════════════════════════

with st.spinner(f"Loading {len(selected_tickers)} assets..."):
    prices = fetch_price_history(selected_tickers, period=lookback)

if prices.empty or len(prices.columns) < 3:
    st.error("Insufficient price data. Try different asset classes or a shorter lookback.")
    st.stop()

returns = prices.pct_change().dropna()
tickers_avail = sorted(returns.columns.tolist())
n = len(tickers_avail)

# Metrics header
m1, m2, m3, m4 = st.columns(4)
corr_matrix = returns.corr()
upper = corr_matrix.where(np.triu(np.ones(corr_matrix.shape), k=1).astype(bool))
upper_stacked = upper.stack().dropna()
m1.metric("Assets Loaded", n)
if not upper_stacked.empty:
    avg_corr = upper_stacked.mean()
    max_pair = upper_stacked.idxmax()
    min_pair = upper_stacked.idxmin()
    m2.metric("Avg Correlation", f"{avg_corr:.3f}")
    m3.metric("Most Correlated", f"{max_pair[0]}/{max_pair[1]}", delta=f"{upper_stacked.loc[max_pair]:.3f}")
    m4.metric("Least Correlated", f"{min_pair[0]}/{min_pair[1]}", delta=f"{upper_stacked.loc[min_pair]:.3f}")


# ═══════════════════════════════════════════════
# TABS
# ═══════════════════════════════════════════════

# Sort tickers by asset class for visual grouping (used across tabs)
sorted_tickers = []
for cls in selected_classes:
    sorted_tickers.extend([t for t in ASSET_CLASSES[cls] if t in corr_matrix.columns])

tab_matrix, tab_regime, tab_rolling, tab_cluster, tab_alerts, tab_pca = st.tabs([
    "Correlation Matrix",
    "Regime Correlations",
    "Rolling Correlation",
    "Clustering",
    "Breakdown Alerts",
    "PCA / Factor Structure",
])


# ═══════════════════════════════════════════════
# TAB 1: CORRELATION MATRIX
# ═══════════════════════════════════════════════
with tab_matrix, error_boundary("Correlation Matrix"):

    window = st.radio("Window", ["Full Period", "21D Rolling (Latest)", "63D Rolling (Latest)"],
                      horizontal=True, key="corr_window")

    if window == "Full Period":
        cm = returns.corr()
    elif window == "21D Rolling (Latest)":
        cm = returns.tail(21).corr()
    else:
        cm = returns.tail(63).corr()

    cm = cm.loc[sorted_tickers, sorted_tickers]

    fig_cm = go.Figure(data=go.Heatmap(
        z=cm.values,
        x=sorted_tickers, y=sorted_tickers,
        colorscale=[[0, "#ff4444"], [0.5, "#1a1a2e"], [1, "#00d1ff"]],
        zmid=0, zmin=-1, zmax=1,
        text=[[f"{v:.2f}" for v in row] for row in cm.values],
        texttemplate="%{text}", textfont={"size": 9},
        hovertemplate="%{x} vs %{y}<br>Correlation: %{z:.3f}<extra></extra>",
        colorbar=dict(title="Corr"),
    ))

    # Add class boundary lines
    boundaries = []
    prev_cls = None
    for i, t in enumerate(sorted_tickers):
        cls = ASSET_CLASS_MAP.get(t, "")
        if cls != prev_cls and prev_cls is not None:
            boundaries.append(i - 0.5)
        prev_cls = cls

    for b in boundaries:
        fig_cm.add_hline(y=b, line=dict(color="#555", width=1))
        fig_cm.add_vline(x=b, line=dict(color="#555", width=1))

    fig_cm.update_layout(
        template="plotly_dark", height=max(500, n * 32),
        title=f"Correlation Matrix — {window} ({lookback_label} data)",
        margin=dict(l=0, r=0, t=40, b=0),
        xaxis=dict(tickangle=45),
    )
    st.plotly_chart(fig_cm, use_container_width=True, config=PLOTLY_NOBAR)

    # Top/bottom pairs table
    st.subheader("Strongest Correlations")
    pairs = []
    for i, t1 in enumerate(sorted_tickers):
        for j, t2 in enumerate(sorted_tickers):
            if i < j:
                pairs.append({
                    "Pair": f"{t1} / {t2}",
                    "Asset A": ALL_ASSETS.get(t1, t1),
                    "Asset B": ALL_ASSETS.get(t2, t2),
                    "Class A": ASSET_CLASS_MAP.get(t1, ""),
                    "Class B": ASSET_CLASS_MAP.get(t2, ""),
                    "Correlation": cm.loc[t1, t2],
                    "Cross-Class": ASSET_CLASS_MAP.get(t1) != ASSET_CLASS_MAP.get(t2),
                })
    pairs_df = pd.DataFrame(pairs).sort_values("Correlation", key=abs, ascending=False)

    pc1, pc2 = st.columns(2)
    with pc1:
        st.markdown("**Most Correlated (same direction)**")
        top = pairs_df.head(10).copy()
        top["Correlation"] = top["Correlation"].apply(lambda v: f"{v:+.3f}")
        st.dataframe(top[["Pair", "Correlation", "Class A", "Class B"]], use_container_width=True, hide_index=True)
    with pc2:
        st.markdown("**Most Inversely Correlated (hedge pairs)**")
        bottom = pairs_df.tail(10).sort_values("Correlation").copy()
        bottom["Correlation"] = bottom["Correlation"].apply(lambda v: f"{v:+.3f}")
        st.dataframe(bottom[["Pair", "Correlation", "Class A", "Class B"]], use_container_width=True, hide_index=True)

    # Cross-class only
    st.markdown("**Strongest Cross-Class Correlations** (diversification check)")
    cross = pairs_df[pairs_df["Cross-Class"]].head(10).copy()
    cross["Correlation"] = cross["Correlation"].apply(lambda v: f"{v:+.3f}")
    st.dataframe(cross[["Pair", "Correlation", "Class A", "Class B"]], use_container_width=True, hide_index=True)


# ═══════════════════════════════════════════════
# TAB 2: REGIME CORRELATIONS
# ═══════════════════════════════════════════════
with tab_regime, error_boundary("Regime Correlations"):

    st.caption("Correlations tend to spike toward 1.0 during crises — the 'correlation breakdown' that destroys diversification when you need it most.")

    # Use VIX proxy (VIXY) or SPY vol for regime detection
    vol_proxy = None
    for vt in ["VIXY", "SPY"]:
        if vt in returns.columns:
            vol_proxy = vt
            break

    if vol_proxy:
        # Use SPY realized vol as the regime indicator (consistent regardless of proxy)
        if "SPY" in returns.columns:
            vol_series = returns["SPY"].rolling(20).std() * np.sqrt(252) * 100
        else:
            vol_series = returns[vol_proxy].rolling(20).std() * np.sqrt(252) * 100

        vol_series = vol_series.dropna()
        common_idx = returns.index.intersection(vol_series.index)
        vol_aligned = vol_series.loc[common_idx]

        # Define regimes by vol quartiles
        q25 = vol_aligned.quantile(0.25)
        q75 = vol_aligned.quantile(0.75)

        calm_mask = vol_aligned <= q25
        normal_mask = (vol_aligned > q25) & (vol_aligned <= q75)
        stress_mask = vol_aligned > q75

        regimes = {"Calm (Low Vol)": calm_mask, "Normal": normal_mask, "Stress (High Vol)": stress_mask}
        regime_colors = {"Calm (Low Vol)": "#00ff88", "Normal": "#ffaa00", "Stress (High Vol)": "#ff4444"}

        # Compute correlation matrix per regime
        regime_corrs = {}
        for rname, rmask in regimes.items():
            idx = common_idx[rmask.values]
            if len(idx) > 20:
                regime_corrs[rname] = returns.loc[idx].corr()

        if len(regime_corrs) >= 2:
            # Side-by-side heatmaps
            cols = st.columns(len(regime_corrs))
            for col, (rname, rcorr) in zip(cols, regime_corrs.items()):
                with col:
                    rcorr_sorted = rcorr.loc[sorted_tickers, sorted_tickers]
                    avg = rcorr_sorted.where(np.triu(np.ones(rcorr_sorted.shape), k=1).astype(bool)).stack().mean()
                    st.markdown(f"**{rname}**")
                    st.metric("Avg Corr", f"{avg:.3f}")
                    fig_r = go.Figure(data=go.Heatmap(
                        z=rcorr_sorted.values,
                        x=sorted_tickers, y=sorted_tickers,
                        colorscale=[[0, "#ff4444"], [0.5, "#1a1a2e"], [1, "#00d1ff"]],
                        zmid=0, zmin=-1, zmax=1,
                        text=[[f"{v:.2f}" for v in row] for row in rcorr_sorted.values],
                        texttemplate="%{text}", textfont={"size": 7},
                        showscale=False,
                    ))
                    fig_r.update_layout(template="plotly_dark", height=max(350, n * 22),
                                        margin=dict(l=0, r=0, t=10, b=0),
                                        xaxis=dict(tickangle=45, tickfont=dict(size=8)),
                                        yaxis=dict(tickfont=dict(size=8)))
                    st.plotly_chart(fig_r, use_container_width=True, config=PLOTLY_NOBAR)

            # Correlation change: stress vs calm
            st.subheader("Correlation Shift: Stress vs Calm")
            st.caption("Positive = correlation increases during stress (diversification fails). Negative = becomes more hedging.")

            if "Calm (Low Vol)" in regime_corrs and "Stress (High Vol)" in regime_corrs:
                diff = regime_corrs["Stress (High Vol)"] - regime_corrs["Calm (Low Vol)"]
                diff_sorted = diff.loc[sorted_tickers, sorted_tickers]

                fig_diff = go.Figure(data=go.Heatmap(
                    z=diff_sorted.values,
                    x=sorted_tickers, y=sorted_tickers,
                    colorscale=[[0, "#00d1ff"], [0.5, "#1a1a2e"], [1, "#ff4444"]],
                    zmid=0,
                    text=[[f"{v:+.2f}" for v in row] for row in diff_sorted.values],
                    texttemplate="%{text}", textfont={"size": 9},
                    hovertemplate="%{x} vs %{y}<br>Shift: %{z:+.3f}<extra></extra>",
                    colorbar=dict(title="Shift"),
                ))
                fig_diff.update_layout(template="plotly_dark", height=max(450, n * 28),
                                       title="Correlation Change (Stress − Calm)",
                                       margin=dict(l=0, r=0, t=40, b=0), xaxis=dict(tickangle=45))
                st.plotly_chart(fig_diff, use_container_width=True, config=PLOTLY_NOBAR)

                # Biggest shifts
                shift_pairs = []
                for i, t1 in enumerate(sorted_tickers):
                    for j, t2 in enumerate(sorted_tickers):
                        if i < j:
                            shift_pairs.append({
                                "Pair": f"{t1} / {t2}",
                                "Calm": regime_corrs["Calm (Low Vol)"].loc[t1, t2],
                                "Stress": regime_corrs["Stress (High Vol)"].loc[t1, t2],
                                "Shift": diff_sorted.loc[t1, t2],
                            })
                shift_df = pd.DataFrame(shift_pairs).sort_values("Shift", ascending=False)

                sh1, sh2 = st.columns(2)
                with sh1:
                    st.markdown("**Biggest Correlation Spikes (diversification killers)**")
                    top_shift = shift_df.head(8).copy()
                    for c in ["Calm", "Stress", "Shift"]:
                        top_shift[c] = top_shift[c].apply(lambda v: f"{v:+.3f}")
                    st.dataframe(top_shift, use_container_width=True, hide_index=True)
                with sh2:
                    st.markdown("**Best Stress Hedges (correlation drops in crisis)**")
                    bot_shift = shift_df.tail(8).sort_values("Shift").copy()
                    for c in ["Calm", "Stress", "Shift"]:
                        bot_shift[c] = bot_shift[c].apply(lambda v: f"{v:+.3f}")
                    st.dataframe(bot_shift, use_container_width=True, hide_index=True)
            # Drawdown performance — what each asset did during worst equity drops
            st.markdown("---")
            st.subheader("Performance During Drawdowns")
            st.caption("Shows how each asset performed during the 5 worst SPY drawdown periods. "
                        "Negative correlation during drawdowns = effective hedge.")

            if "SPY" in returns.columns:
                _spy = returns["SPY"]
                _spy_cum = (1 + _spy).cumprod()
                _spy_dd = (_spy_cum / _spy_cum.cummax() - 1)

                # Find the 5 worst drawdown troughs
                _dd_threshold = _spy_dd.quantile(0.05)  # bottom 5% of drawdowns
                _in_drawdown = _spy_dd < _dd_threshold
                _dd_periods = []
                _start = None
                for idx, val in _in_drawdown.items():
                    if val and _start is None:
                        _start = idx
                    elif not val and _start is not None:
                        _dd_periods.append((_start, idx))
                        _start = None
                if _start:
                    _dd_periods.append((_start, _spy_dd.index[-1]))

                # Take up to 5 periods
                _dd_periods = _dd_periods[:5]

                if _dd_periods:
                    dd_perf = []
                    for start, end in _dd_periods:
                        period_rets = returns.loc[start:end]
                        if len(period_rets) < 3:
                            continue
                        row = {"Period": f"{start.strftime('%Y-%m-%d')} to {end.strftime('%Y-%m-%d')}",
                               "Days": len(period_rets)}
                        spy_ret = (1 + period_rets["SPY"]).prod() - 1
                        row["SPY"] = f"{spy_ret*100:+.1f}%"
                        for tk in sorted_tickers:
                            if tk != "SPY" and tk in period_rets.columns:
                                tk_ret = (1 + period_rets[tk]).prod() - 1
                                row[tk] = f"{tk_ret*100:+.1f}%"
                        dd_perf.append(row)

                    if dd_perf:
                        _dd_df = pd.DataFrame(dd_perf)
                        st.dataframe(_dd_df, use_container_width=True, hide_index=True)

                        # Find best hedges during drawdowns
                        _hedge_scores = {}
                        for tk in sorted_tickers:
                            if tk == "SPY" or tk not in returns.columns:
                                continue
                            _avg_dd_ret = 0
                            _count = 0
                            for start, end in _dd_periods:
                                pr = returns.loc[start:end]
                                if tk in pr.columns and len(pr) >= 3:
                                    _avg_dd_ret += (1 + pr[tk]).prod() - 1
                                    _count += 1
                            if _count > 0:
                                _hedge_scores[tk] = _avg_dd_ret / _count

                        if _hedge_scores:
                            _best = sorted(_hedge_scores.items(), key=lambda x: -x[1])[:3]
                            _worst = sorted(_hedge_scores.items(), key=lambda x: x[1])[:3]
                            hc1, hc2 = st.columns(2)
                            with hc1:
                                st.markdown("**Best performers during SPY drawdowns:**")
                                for tk, ret in _best:
                                    st.caption(f"**{tk}**: avg {ret*100:+.1f}% during drawdowns")
                            with hc2:
                                st.markdown("**Worst performers (fall with SPY):**")
                                for tk, ret in _worst:
                                    st.caption(f"**{tk}**: avg {ret*100:+.1f}% during drawdowns")

    else:
        st.warning("Need VIXY or SPY in selected assets for regime detection.")


# ═══════════════════════════════════════════════
# TAB 3: ROLLING CORRELATION
# ═══════════════════════════════════════════════
with tab_rolling, error_boundary("Rolling Correlation"):

    rc1, rc2 = st.columns(2)
    with rc1:
        pair_a = st.selectbox("Asset A", tickers_avail,
                              index=tickers_avail.index("SPY") if "SPY" in tickers_avail else 0,
                              key="rc_pair_a")
    with rc2:
        default_b = tickers_avail.index("TLT") if "TLT" in tickers_avail else min(1, len(tickers_avail) - 1)
        pair_b = st.selectbox("Asset B", tickers_avail, index=default_b, key="rc_pair_b")

    if pair_a != pair_b:
        windows = [21, 63, 126, 252]
        window_labels = ["21D (1M)", "63D (3M)", "126D (6M)", "252D (1Y)"]
        window_colors = ["#00d1ff", "#ffaa00", "#ff6b6b", "#00ff88"]

        fig_rc = go.Figure()
        for w, label, color in zip(windows, window_labels, window_colors):
            if len(returns) >= w:
                roll = returns[pair_a].rolling(w).corr(returns[pair_b]).dropna()
                fig_rc.add_trace(go.Scatter(
                    x=roll.index, y=roll.values, mode="lines",
                    name=label, line=dict(color=color, width=2 if w == 63 else 1),
                ))

        # Full-period correlation line
        full_corr = returns[pair_a].corr(returns[pair_b])
        fig_rc.add_hline(y=full_corr, line_dash="dot", line_color="#555",
                         annotation_text=f"Full: {full_corr:.3f}")
        fig_rc.add_hline(y=0, line_dash="dash", line_color="#333")

        # Shade danger zones
        fig_rc.add_hrect(y0=0.8, y1=1.0, fillcolor="rgba(255,68,68,0.05)", line_width=0,
                         annotation_text="High corr (no diversification)", annotation_position="top left")
        fig_rc.add_hrect(y0=-1.0, y1=-0.5, fillcolor="rgba(0,209,255,0.05)", line_width=0,
                         annotation_text="Strong hedge", annotation_position="bottom left")

        fig_rc.update_layout(
            template="plotly_dark", height=450,
            title=f"Rolling Correlation: {pair_a} ({ALL_ASSETS.get(pair_a, '')}) vs {pair_b} ({ALL_ASSETS.get(pair_b, '')})",
            yaxis_title="Correlation", yaxis=dict(range=[-1.05, 1.05]),
            legend=dict(orientation="h", y=-0.12),
            margin=dict(l=0, r=0, t=40, b=0),
        )
        st.plotly_chart(fig_rc, use_container_width=True, config=PLOTLY_NOBAR)

        # Rolling correlation statistics
        st.subheader("Correlation Statistics")
        if len(returns) >= 63:
            roll_63 = returns[pair_a].rolling(63).corr(returns[pair_b]).dropna()
            if not roll_63.empty:
                stat_c1, stat_c2, stat_c3, stat_c4, stat_c5 = st.columns(5)
                stat_c1.metric("Current (63D)", f"{roll_63.iloc[-1]:.3f}")
                stat_c2.metric("Mean", f"{roll_63.mean():.3f}")
                stat_c3.metric("Std Dev", f"{roll_63.std():.3f}")
                stat_c4.metric("Min", f"{roll_63.min():.3f}")
                stat_c5.metric("Max", f"{roll_63.max():.3f}")

            # Z-score of current correlation
            _last_corr = roll_63.iloc[-1] if len(roll_63) > 0 else np.nan
            if pd.notna(_last_corr) and roll_63.std() > 0:
                z_score = (_last_corr - roll_63.mean()) / roll_63.std()
                if abs(z_score) > 2:
                    st.warning(f"Current correlation Z-score: **{z_score:+.1f}** — this is extreme ({'>2' if z_score > 0 else '<-2'} standard deviations from mean).")

        # Correlation vs returns scatter
        st.subheader("Correlation-Return Relationship")
        if len(returns) >= 63:
            roll_corr = returns[pair_a].rolling(63).corr(returns[pair_b])
            roll_ret_a = returns[pair_a].rolling(63).mean() * 252 * 100
            common = pd.DataFrame({"corr": roll_corr, "ret": roll_ret_a}).dropna()

            fig_scatter = go.Figure()
            fig_scatter.add_trace(go.Scatter(
                x=common["corr"], y=common["ret"],
                mode="markers", marker=dict(size=3, color="#00d1ff", opacity=0.3),
                hovertemplate=f"Corr: %{{x:.3f}}<br>{pair_a} Ann Ret: %{{y:.1f}}%<extra></extra>",
            ))
            fig_scatter.add_vline(x=0, line_dash="dash", line_color="#333")
            fig_scatter.add_hline(y=0, line_dash="dash", line_color="#333")
            fig_scatter.update_layout(
                template="plotly_dark", height=350,
                title=f"63D Rolling Correlation vs {pair_a} Annualized Return",
                xaxis_title=f"Correlation ({pair_a} vs {pair_b})",
                yaxis_title=f"{pair_a} Ann. Return (%)",
                margin=dict(l=0, r=0, t=40, b=0),
            )
            st.plotly_chart(fig_scatter, use_container_width=True, config=PLOTLY_NOBAR)
    else:
        st.info("Select two different assets.")


# ═══════════════════════════════════════════════
# TAB 4: HIERARCHICAL CLUSTERING
# ═══════════════════════════════════════════════
with tab_cluster, error_boundary("Clustering"):

    st.caption("Assets that cluster together move together — use this to identify redundant positions and find diversifiers.")

    from scipy.cluster.hierarchy import linkage, fcluster, dendrogram as scipy_dendro
    from scipy.spatial.distance import squareform

    # Distance matrix: 1 - |correlation| (so highly correlated assets are "close")
    dist_matrix = 1 - corr_matrix.abs()
    np.fill_diagonal(dist_matrix.values, 0)
    # Ensure symmetry and non-negative
    dist_matrix = (dist_matrix + dist_matrix.T) / 2
    dist_matrix = dist_matrix.clip(lower=0)

    condensed = squareform(dist_matrix.values, checks=False)
    linkage_matrix = linkage(condensed, method="ward")

    # Number of clusters
    n_clusters = st.slider("Number of clusters", 2, min(8, n), value=min(4, n), key="corr_n_clusters")
    cluster_labels = fcluster(linkage_matrix, n_clusters, criterion="maxclust")

    # Build dendrogram data
    dendro = scipy_dendro(linkage_matrix, labels=tickers_avail, no_plot=True,
                          color_threshold=linkage_matrix[-n_clusters + 1, 2] if n_clusters < n else 0)

    # Plotly dendrogram
    fig_dendro = go.Figure()
    for i, (xs, ys) in enumerate(zip(dendro["icoord"], dendro["dcoord"])):
        color = dendro["color_list"][i]
        # Map scipy colors to our palette
        color_map = {"C0": "#00d1ff", "C1": "#00ff88", "C2": "#ffaa00", "C3": "#ff6b6b",
                     "C4": "#ff00ff", "C5": "#88ccff", "C6": "#ffcc00", "C7": "#ff8866",
                     "C8": "#66ffcc", "C9": "#cc88ff"}
        plot_color = color_map.get(color, "#888")
        fig_dendro.add_trace(go.Scatter(
            x=xs, y=ys, mode="lines", line=dict(color=plot_color, width=2),
            showlegend=False, hoverinfo="skip",
        ))

    # X-axis labels from dendrogram order
    leaf_labels = [tickers_avail[int(i)] for i in dendro["leaves"]]
    tick_positions = [5 + 10 * i for i in range(len(leaf_labels))]
    fig_dendro.update_layout(
        template="plotly_dark", height=400,
        title=f"Hierarchical Clustering (Ward Linkage, {n_clusters} clusters)",
        xaxis=dict(tickvals=tick_positions, ticktext=leaf_labels, tickangle=45, tickfont=dict(size=10)),
        yaxis_title="Distance (1 - |correlation|)",
        margin=dict(l=0, r=0, t=40, b=0),
    )
    st.plotly_chart(fig_dendro, use_container_width=True, config=PLOTLY_NOBAR)

    # Cluster composition table
    st.subheader("Cluster Composition")
    cluster_data = []
    for i in range(1, n_clusters + 1):
        members = [tickers_avail[j] for j in range(n) if cluster_labels[j] == i]
        if not members:
            continue
        # Intra-cluster avg correlation
        if len(members) > 1:
            sub_corr = corr_matrix.loc[members, members]
            sub_upper = sub_corr.where(np.triu(np.ones(sub_corr.shape), k=1).astype(bool))
            intra_avg = sub_upper.stack().mean()
        else:
            intra_avg = 1.0
        classes = set(ASSET_CLASS_MAP.get(m, "?") for m in members)
        cluster_data.append({
            "Cluster": i,
            "Members": ", ".join(members),
            "Size": len(members),
            "Avg Intra-Corr": f"{intra_avg:.3f}",
            "Asset Classes": ", ".join(sorted(classes)),
        })
    st.dataframe(pd.DataFrame(cluster_data), use_container_width=True, hide_index=True)

    # Reordered correlation matrix by cluster
    st.subheader("Correlation Matrix (Reordered by Cluster)")
    ordered = [tickers_avail[i] for i in dendro["leaves"]]
    cm_ordered = corr_matrix.loc[ordered, ordered]

    fig_co = go.Figure(data=go.Heatmap(
        z=cm_ordered.values, x=ordered, y=ordered,
        colorscale=[[0, "#ff4444"], [0.5, "#1a1a2e"], [1, "#00d1ff"]],
        zmid=0, zmin=-1, zmax=1,
        text=[[f"{v:.2f}" for v in row] for row in cm_ordered.values],
        texttemplate="%{text}", textfont={"size": 8},
        colorbar=dict(title="Corr"),
    ))
    fig_co.update_layout(template="plotly_dark", height=max(450, n * 28),
                         title="Cluster-Ordered Correlation Matrix",
                         margin=dict(l=0, r=0, t=40, b=0), xaxis=dict(tickangle=45))
    st.plotly_chart(fig_co, use_container_width=True, config=PLOTLY_NOBAR)


# ═══════════════════════════════════════════════
# TAB 5: BREAKDOWN ALERTS
# ═══════════════════════════════════════════════
with tab_alerts, error_boundary("Breakdown Alerts"):

    st.caption("Flags pairs whose recent correlation deviates significantly from their long-run average — signals regime changes, structural breaks, or temporary dislocations.")

    if len(returns) >= 126:
        # Compare recent (21D) vs long-run (full) correlation
        recent_corr = returns.tail(21).corr()
        long_corr = returns.corr()

        alerts = []
        for i, t1 in enumerate(tickers_avail):
            for j, t2 in enumerate(tickers_avail):
                if i >= j:
                    continue
                recent = recent_corr.loc[t1, t2]
                long_run = long_corr.loc[t1, t2]
                shift = recent - long_run

                # Calculate Z-score using rolling correlation std
                roll = returns[t1].rolling(63).corr(returns[t2]).dropna()
                if len(roll) > 30 and roll.std() > 0.01:
                    z = (recent - roll.mean()) / roll.std()
                else:
                    z = 0

                if pd.notna(z) and pd.notna(recent) and abs(z) >= 1.5:
                    alerts.append({
                        "Pair": f"{t1} / {t2}",
                        "Recent (21D)": recent,
                        "Long-Run": long_run,
                        "Shift": shift,
                        "Z-Score": z,
                        "Signal": "BREAKDOWN" if z < -1.5 else ("SPIKE" if z > 1.5 else "SHIFT"),
                        "Class A": ASSET_CLASS_MAP.get(t1, ""),
                        "Class B": ASSET_CLASS_MAP.get(t2, ""),
                    })

        if alerts:
            alert_df = pd.DataFrame(alerts).sort_values("Z-Score", key=abs, ascending=False)

            # Write cross-page context
            try:
                from src.cross_context import write_context
                _bd_list = [f"{r['Pair']}: {r['Signal']} (Z={r['Z-Score']:+.1f})"
                            for _, r in alert_df.head(5).iterrows()]
                write_context("correlation", {"breakdowns": _bd_list, "n_alerts": len(alert_df)})
            except Exception:
                pass

            # Summary metrics
            breakdowns = len(alert_df[alert_df["Signal"] == "BREAKDOWN"])
            spikes = len(alert_df[alert_df["Signal"] == "SPIKE"])
            ac1, ac2, ac3 = st.columns(3)
            ac1.metric("Total Alerts", len(alert_df))
            ac2.metric("Breakdowns (corr dropped)", breakdowns)
            ac3.metric("Spikes (corr surged)", spikes)

            # Display
            display = alert_df.copy()
            for c in ["Recent (21D)", "Long-Run", "Shift"]:
                display[c] = display[c].apply(lambda v: f"{v:+.3f}")
            display["Z-Score"] = display["Z-Score"].apply(lambda v: f"{v:+.1f}")

            st.dataframe(display[["Pair", "Signal", "Recent (21D)", "Long-Run", "Shift", "Z-Score", "Class A", "Class B"]],
                         use_container_width=True, hide_index=True)

            # Visualize top alerts
            if len(alert_df) > 0:
                st.subheader("Top Alert Detail")
                top_alert = alert_df.iloc[0]
                ta, tb = top_alert["Pair"].split(" / ")

                roll_detail = returns[ta].rolling(63).corr(returns[tb]).dropna()
                fig_alert = go.Figure()
                fig_alert.add_trace(go.Scatter(
                    x=roll_detail.index, y=roll_detail.values, mode="lines",
                    line=dict(color="#00d1ff", width=2), name="63D Rolling Corr",
                ))
                fig_alert.add_hline(y=long_corr.loc[ta, tb], line_dash="dot", line_color="#ffaa00",
                                    annotation_text=f"Long-run: {long_corr.loc[ta, tb]:.3f}")
                fig_alert.add_hline(y=recent_corr.loc[ta, tb], line_dash="dash", line_color="#ff4444",
                                    annotation_text=f"Recent: {recent_corr.loc[ta, tb]:.3f}")
                fig_alert.update_layout(
                    template="plotly_dark", height=350,
                    title=f"Alert: {ta} / {tb} — {top_alert['Signal']} (Z={top_alert['Z-Score']:+.1f})",
                    yaxis_title="Correlation", yaxis=dict(range=[-1.05, 1.05]),
                    margin=dict(l=0, r=0, t=40, b=0),
                )
                st.plotly_chart(fig_alert, use_container_width=True, config=PLOTLY_NOBAR)
        else:
            st.success("No correlation breakdowns detected. All pairs within normal range.")
    else:
        st.warning("Need at least 6 months of data for breakdown detection.")


# ═══════════════════════════════════════════════
# TAB 6: PCA / FACTOR STRUCTURE
# ═══════════════════════════════════════════════
with tab_pca, error_boundary("PCA"):

    st.caption("Principal Component Analysis reveals the hidden factors driving asset returns. PC1 is typically 'risk-on/risk-off', PC2 is often rates or sector rotation.")

    # Standardize returns (drop assets with zero variance)
    valid_cols = [c for c in returns.columns if returns[c].std() > 0]
    ret_std = (returns[valid_cols] - returns[valid_cols].mean()) / returns[valid_cols].std()
    ret_std = ret_std.dropna(axis=1)
    available = ret_std.columns.tolist()

    if len(available) >= 3:
        cov_matrix = ret_std.cov()
        eigenvalues, eigenvectors = np.linalg.eigh(cov_matrix.values)

        # Sort descending
        idx = np.argsort(eigenvalues)[::-1]
        eigenvalues = eigenvalues[idx]
        eigenvectors = eigenvectors[:, idx]

        # Variance explained
        total_var = eigenvalues.sum()
        var_explained = eigenvalues / total_var * 100
        cumulative_var = np.cumsum(var_explained)

        n_components = min(10, len(available))

        # Scree plot
        fig_scree = make_subplots(specs=[[{"secondary_y": True}]])
        fig_scree.add_trace(go.Bar(
            x=[f"PC{i+1}" for i in range(n_components)],
            y=var_explained[:n_components],
            name="Individual", marker_color="#00d1ff",
            text=[f"{v:.1f}%" for v in var_explained[:n_components]],
            textposition="outside",
        ), secondary_y=False)
        fig_scree.add_trace(go.Scatter(
            x=[f"PC{i+1}" for i in range(n_components)],
            y=cumulative_var[:n_components],
            name="Cumulative", mode="lines+markers",
            line=dict(color="#ffaa00", width=2), marker=dict(size=6),
        ), secondary_y=True)
        fig_scree.add_hline(y=80, line_dash="dash", line_color="#555", secondary_y=True,
                           annotation_text="80% explained")
        fig_scree.update_layout(template="plotly_dark", height=380,
                                title="Scree Plot — Variance Explained by Principal Component",
                                margin=dict(l=0, r=0, t=40, b=0))
        fig_scree.update_yaxes(title_text="Individual (%)", secondary_y=False)
        fig_scree.update_yaxes(title_text="Cumulative (%)", secondary_y=True)
        st.plotly_chart(fig_scree, use_container_width=True, config=PLOTLY_NOBAR)

        # Key metrics
        pc_m1, pc_m2, pc_m3, pc_m4 = st.columns(4)
        pc_m1.metric("PC1 Explains", f"{var_explained[0]:.1f}%")
        pc_m2.metric("PC1+PC2", f"{cumulative_var[1]:.1f}%")
        pcs_for_80 = int(np.argmax(cumulative_var >= 80) + 1) if cumulative_var[-1] >= 80 else len(available)
        pc_m3.metric("PCs for 80%", pcs_for_80)
        pc_m4.metric("Effective Dimension", f"{(total_var**2) / (eigenvalues**2).sum():.1f}",
                      help="Participation ratio — how many independent risk factors really exist")

        # Factor loadings heatmap (top 5 PCs)
        st.subheader("Factor Loadings")
        st.caption("How each asset loads onto the principal components. High absolute loading = strong exposure to that factor.")

        n_show = min(5, n_components)
        loadings = pd.DataFrame(
            eigenvectors[:, :n_show],
            index=available,
            columns=[f"PC{i+1}" for i in range(n_show)],
        )

        # Sort by PC1 loading for readability
        loadings = loadings.sort_values("PC1", ascending=False)

        fig_load = go.Figure(data=go.Heatmap(
            z=loadings.values,
            x=loadings.columns.tolist(),
            y=loadings.index.tolist(),
            colorscale=[[0, "#ff4444"], [0.5, "#1a1a2e"], [1, "#00d1ff"]],
            zmid=0,
            text=[[f"{v:.2f}" for v in row] for row in loadings.values],
            texttemplate="%{text}", textfont={"size": 10},
            hovertemplate="%{y} on %{x}: %{z:.3f}<extra></extra>",
            colorbar=dict(title="Loading"),
        ))
        fig_load.update_layout(template="plotly_dark", height=max(400, len(available) * 24),
                               title="Principal Component Loadings",
                               margin=dict(l=0, r=0, t=40, b=0))
        st.plotly_chart(fig_load, use_container_width=True, config=PLOTLY_NOBAR)

        # Factor interpretation
        st.subheader("Factor Interpretation")
        for i in range(min(3, n_show)):
            pc_name = f"PC{i+1}"
            pc_loads = loadings[pc_name].sort_values(ascending=False)
            top_pos = pc_loads.head(3)
            top_neg = pc_loads.tail(3).sort_values()

            pos_str = ", ".join([f"{t} ({ALL_ASSETS.get(t, t)}: {v:+.2f})" for t, v in top_pos.items()])
            neg_str = ", ".join([f"{t} ({ALL_ASSETS.get(t, t)}: {v:+.2f})" for t, v in top_neg.items()])

            with st.expander(f"**{pc_name}** — {var_explained[i]:.1f}% of variance", expanded=(i == 0)):
                st.markdown(f"**Positive loadings:** {pos_str}")
                st.markdown(f"**Negative loadings:** {neg_str}")

                if i == 0:
                    # Check if PC1 is risk-on/risk-off
                    equity_loads = [loadings.loc[t, pc_name] for t in available if ASSET_CLASS_MAP.get(t) in ("US Equities", "Sectors")]
                    bond_loads = [loadings.loc[t, pc_name] for t in available if ASSET_CLASS_MAP.get(t) == "Fixed Income"]
                    if equity_loads and bond_loads:
                        eq_avg = np.mean(equity_loads)
                        bd_avg = np.mean(bond_loads)
                        if eq_avg * bd_avg < 0:
                            st.info(f"PC1 appears to be a **risk-on/risk-off** factor (equities {eq_avg:+.2f} vs bonds {bd_avg:+.2f}).")
                        else:
                            st.info(f"PC1 is a **level** factor — equities and bonds load in the same direction ({eq_avg:+.2f}, {bd_avg:+.2f}).")

        # PC1 vs PC2 scatter (asset map)
        st.subheader("Asset Map (PC1 vs PC2)")
        st.caption("Assets close together in PC space have similar risk profiles. Distance = dissimilarity.")

        fig_map = go.Figure()
        for cls, color in CLASS_COLORS.items():
            cls_tickers = [t for t in available if ASSET_CLASS_MAP.get(t) == cls]
            if not cls_tickers:
                continue
            x_vals = [loadings.loc[t, "PC1"] for t in cls_tickers]
            y_vals = [loadings.loc[t, "PC2"] for t in cls_tickers]
            fig_map.add_trace(go.Scatter(
                x=x_vals, y=y_vals, mode="markers+text",
                name=cls, text=cls_tickers, textposition="top center",
                marker=dict(size=12, color=color, line=dict(width=1, color="#fff")),
                textfont=dict(size=10, color="#ddd"),
            ))

        fig_map.add_vline(x=0, line_dash="dash", line_color="#333")
        fig_map.add_hline(y=0, line_dash="dash", line_color="#333")
        fig_map.update_layout(
            template="plotly_dark", height=500,
            title="Asset Map — PC1 (Risk) vs PC2 (Rotation)",
            xaxis_title=f"PC1 ({var_explained[0]:.1f}% var)",
            yaxis_title=f"PC2 ({var_explained[1]:.1f}% var)",
            legend=dict(orientation="h", y=-0.12),
            margin=dict(l=0, r=0, t=40, b=0),
        )
        st.plotly_chart(fig_map, use_container_width=True, config=PLOTLY_NOBAR)
    else:
        st.warning("Need at least 3 assets for PCA decomposition.")

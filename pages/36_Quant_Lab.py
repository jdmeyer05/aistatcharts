"""
Quant Lab — Marcos Lopez de Prado Methods

Implements core techniques from "Advances in Financial Machine Learning" (AFML)
and "Machine Learning for Asset Managers" (MLAM).

Tabs:
1. Fractional Differentiation — minimum-d stationarity with memory preservation
2. Structural Breaks — CUSUM filter, SADF bubble detection
3. Triple Barrier & Meta-Labeling — event-driven labeling + bet sizing
4. Sample Weights & Bootstrap — average uniqueness, sequential bootstrap
5. Feature Importance — MDI, MDA, SFI with purged K-fold CV
6. HRP — Hierarchical Risk Parity portfolio allocation
7. Microstructure — VPIN, Kyle's Lambda, Amihud illiquidity, tick imbalance
8. Entropy — Shannon, plug-in, Lempel-Ziv complexity
"""
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import logging
from src.layout import setup_page, error_boundary
from src.data_engine import polygon_history, format_massive_ticker
from src.market_data import fetch_energy_price_history as fetch_price_history
from src.styles import COLORS

logger = logging.getLogger(__name__)
setup_page("36_Quant_Lab")

st.title("Quant Lab")
st.markdown("Institutional-grade quantitative methods from Lopez de Prado's *Advances in Financial Machine Learning*.")

PLOTLY_NOBAR = {"displayModeBar": False}

# ═══════════════════════════════════════════════
# CONTROLS
# ═══════════════════════════════════════════════
c1, c2, c3 = st.columns([2, 1, 1])
with c1:
    raw_ticker = st.text_input("Ticker", value="SPY", key="ql_ticker")
    ticker = format_massive_ticker(raw_ticker.strip().upper())
with c2:
    lookback_days = st.selectbox("Lookback", [504, 756, 1260, 2520],
                                  format_func=lambda x: f"{x // 252}Y ({x}D)",
                                  index=1, key="ql_lookback")
with c3:
    st.markdown("<br>", unsafe_allow_html=True)
    load_btn = st.button("Run Analysis", type="primary", use_container_width=True, key="ql_load")

if load_btn:
    st.session_state["ql_loaded"] = True

if not st.session_state.get("ql_loaded"):
    st.info("Enter a ticker and click **Run Analysis**.")
    st.stop()

# ═══════════════════════════════════════════════
# DATA
# ═══════════════════════════════════════════════
with st.spinner(f"Loading {ticker} ({lookback_days} days)..."):
    df = polygon_history(ticker, lookback_days)

if df is None or df.empty or len(df) < 100:
    st.error(f"Insufficient data for {ticker}. Need at least 100 trading days.")
    st.stop()

close = df["Close"].copy()
log_prices = np.log(close)
log_returns = log_prices.diff().dropna()
volume = df["Volume"].copy() if "Volume" in df.columns else pd.Series(dtype=float)

m1, m2, m3, m4 = st.columns(4)
m1.metric("Observations", len(close))
m2.metric("Date Range", f"{close.index[0].strftime('%Y-%m-%d')} to {close.index[-1].strftime('%Y-%m-%d')}")
m3.metric("Ann. Return", f"{log_returns.mean() * 252 * 100:.1f}%")
m4.metric("Ann. Vol", f"{log_returns.std() * np.sqrt(252) * 100:.1f}%")

# ═══════════════════════════════════════════════
# TABS
# ═══════════════════════════════════════════════
tab_frac, tab_breaks, tab_barrier, tab_weights, tab_importance, tab_hrp, tab_micro, tab_entropy = st.tabs([
    "Frac. Diff.",
    "Structural Breaks",
    "Triple Barrier",
    "Sample Weights",
    "Feature Importance",
    "HRP",
    "Microstructure",
    "Entropy",
])


# ═══════════════════════════════════════════════
# TAB 1: FRACTIONAL DIFFERENTIATION
# ═══════════════════════════════════════════════
with tab_frac, error_boundary("Fractional Differentiation"):
    st.subheader("Fractional Differentiation")
    st.caption("AFML Ch. 5 — Find the minimum *d* that makes a price series stationary while preserving maximum memory.")

    with st.expander("How to read this tab", expanded=False):
        st.markdown(
            "**The problem:** Most ML models require stationary inputs (constant mean/variance). "
            "Taking first differences (d=1) makes prices stationary but destroys all memory — "
            "today's differenced value tells you nothing about yesterday's.\n\n"
            "**The solution:** Fractional differencing uses a non-integer *d* between 0 and 1. "
            "At d=0 you have the original (non-stationary) series with full memory. "
            "At d=1 you have returns (stationary, no memory). The optimal *d* is the smallest value "
            "where the ADF test rejects the unit root at 5% significance.\n\n"
            "**How to use it:**\n"
            "- The **red line** (ADF p-value) must drop below the yellow 5% threshold for stationarity\n"
            "- The **blue line** (correlation) shows how much memory is preserved — higher is better\n"
            "- The **green vertical line** marks the optimal d\n"
            "- Use the fractionally differenced series as ML input instead of raw prices or returns\n\n"
            "**Weight threshold** controls how many lagged terms are used. Lower = more terms = more memory preserved but slower. "
            "Default 1e-5 is the AFML recommendation.\n\n"
            "**Reference:** Lopez de Prado, *Advances in Financial Machine Learning*, Chapter 5."
        )

    def _frac_diff_weights(d: float, size: int, thresh: float = 1e-5) -> np.ndarray:
        """Compute fractional differencing weights (expanding window)."""
        w = [1.0]
        for k in range(1, size):
            w_ = -w[-1] * (d - k + 1) / k
            if abs(w_) < thresh:
                break
            w.append(w_)
        return np.array(w)

    def _frac_diff(series: pd.Series, d: float, thresh: float = 1e-5) -> pd.Series:
        """Apply fractional differencing of order d to a series."""
        w = _frac_diff_weights(d, len(series), thresh)
        width = len(w)
        result = pd.Series(index=series.index, dtype=float)
        for i in range(width - 1, len(series)):
            result.iloc[i] = np.dot(w, series.iloc[i - width + 1:i + 1].values[::-1])
        return result.dropna()

    def _adf_test(series: pd.Series) -> dict:
        """Augmented Dickey-Fuller test for stationarity."""
        from statsmodels.tsa.stattools import adfuller
        clean = series.dropna()
        if len(clean) < 30:
            return {"statistic": np.nan, "pvalue": 1.0, "critical": {}}
        result = adfuller(clean, autolag="AIC")
        return {
            "statistic": result[0],
            "pvalue": result[1],
            "critical": result[4],
        }

    # Scan d values
    d_values = np.arange(0.0, 1.05, 0.05)
    thresh = st.slider("Weight threshold", 1e-6, 1e-3, 1e-5, format="%.6f", key="ql_frac_thresh")

    adf_results = []
    corr_results = []
    with st.spinner("Scanning d values..."):
        for d in d_values:
            if d == 0:
                fd = log_prices
            else:
                fd = _frac_diff(log_prices, d, thresh)
            if len(fd) < 30:
                continue
            adf = _adf_test(fd)
            # Correlation with original series (memory preservation)
            common_idx = log_prices.index.intersection(fd.index)
            corr = log_prices.loc[common_idx].corr(fd.loc[common_idx])
            adf_results.append({"d": d, "adf_stat": adf["statistic"], "pvalue": adf["pvalue"]})
            corr_results.append({"d": d, "corr": corr})

    adf_df = pd.DataFrame(adf_results)
    corr_df = pd.DataFrame(corr_results)

    if not adf_df.empty:
        # Find minimum d for stationarity
        stationary = adf_df[adf_df["pvalue"] < 0.05]
        min_d = stationary["d"].min() if not stationary.empty else 1.0

        st.metric("Minimum d for Stationarity (p < 0.05)", f"{min_d:.2f}",
                  help="The smallest fractional differencing order that achieves ADF stationarity at 5% level")

        # Dual-axis chart: ADF p-value + correlation vs d
        fig_frac = make_subplots(specs=[[{"secondary_y": True}]])

        fig_frac.add_trace(go.Scatter(
            x=adf_df["d"], y=adf_df["pvalue"], mode="lines+markers",
            name="ADF p-value", line=dict(color="#ff6b6b", width=2),
            marker=dict(size=5),
        ), secondary_y=False)

        if not corr_df.empty:
            fig_frac.add_trace(go.Scatter(
                x=corr_df["d"], y=corr_df["corr"], mode="lines+markers",
                name="Correlation with original", line=dict(color="#00d1ff", width=2),
                marker=dict(size=5),
            ), secondary_y=True)

        fig_frac.add_hline(y=0.05, line_dash="dash", line_color="#ffaa00",
                           annotation_text="5% significance", secondary_y=False)
        fig_frac.add_vline(x=min_d, line_dash="dash", line_color="#00ff88",
                           annotation_text=f"Min d = {min_d:.2f}")

        fig_frac.update_layout(template="plotly_dark", height=420,
                               title="Stationarity vs Memory Preservation",
                               legend=dict(orientation="h", y=-0.15),
                               margin=dict(l=0, r=0, t=40, b=0))
        fig_frac.update_yaxes(title_text="ADF p-value", secondary_y=False, type="log")
        fig_frac.update_yaxes(title_text="Correlation", secondary_y=True, range=[0, 1.05])
        st.plotly_chart(fig_frac, use_container_width=True, config=PLOTLY_NOBAR)

        # Show the fractionally differenced series
        fd_optimal = _frac_diff(log_prices, min_d, thresh) if min_d > 0 else log_prices
        fd_c1, fd_c2 = st.columns(2)
        with fd_c1:
            fig_orig = go.Figure()
            fig_orig.add_trace(go.Scatter(x=log_prices.index, y=log_prices, mode="lines",
                                          line=dict(color="#555", width=1), name="log(price)"))
            fig_orig.update_layout(template="plotly_dark", height=250,
                                   title="Original (Non-Stationary)", margin=dict(l=0, r=0, t=30, b=0))
            st.plotly_chart(fig_orig, use_container_width=True, config=PLOTLY_NOBAR)
        with fd_c2:
            fig_fd = go.Figure()
            fig_fd.add_trace(go.Scatter(x=fd_optimal.index, y=fd_optimal, mode="lines",
                                        line=dict(color="#00d1ff", width=1), name=f"d={min_d:.2f}"))
            fig_fd.update_layout(template="plotly_dark", height=250,
                                 title=f"Frac. Diff. d={min_d:.2f} (Stationary + Memory)",
                                 margin=dict(l=0, r=0, t=30, b=0))
            st.plotly_chart(fig_fd, use_container_width=True, config=PLOTLY_NOBAR)

        # ── Autocorrelation comparison ──
        st.markdown("---")
        st.subheader("Autocorrelation Structure")
        st.caption("The ACF (autocorrelation function) shows how correlated a value is with its own past. "
                   "d=0 has slowly decaying ACF (long memory, non-stationary). d=1 has near-zero ACF (no memory). "
                   "The optimal d should have fast-decaying but non-trivial autocorrelation — the best of both worlds.")

        acf_c1, acf_c2, acf_c3 = st.columns(3)
        acf_lags = 40
        for col, (d_label, d_series) in zip(
            [acf_c1, acf_c2, acf_c3],
            [("d=0 (original)", log_prices),
             (f"d={min_d:.2f} (optimal)", fd_optimal),
             ("d=1 (returns)", log_returns)]
        ):
            with col:
                clean = d_series.dropna()
                if len(clean) > acf_lags + 10:
                    from statsmodels.tsa.stattools import acf as compute_acf
                    acf_vals, acf_ci = compute_acf(clean, nlags=acf_lags, alpha=0.05)[:2]
                    ci_upper = acf_ci[1:, 1] - acf_vals[1:]
                    fig_acf = go.Figure()
                    fig_acf.add_trace(go.Bar(x=list(range(1, acf_lags + 1)), y=acf_vals[1:],
                                            marker_color="#00d1ff", name="ACF"))
                    fig_acf.add_hline(y=1.96 / np.sqrt(len(clean)), line_dash="dash", line_color="#ffaa00")
                    fig_acf.add_hline(y=-1.96 / np.sqrt(len(clean)), line_dash="dash", line_color="#ffaa00")
                    fig_acf.update_layout(template="plotly_dark", height=220, title=d_label,
                                          yaxis=dict(range=[-0.3, 1.05]),
                                          margin=dict(l=0, r=0, t=30, b=0), showlegend=False)
                    st.plotly_chart(fig_acf, use_container_width=True, config=PLOTLY_NOBAR)

        # Weight kernel visualization
        st.subheader("Differencing Kernel Weights")
        st.caption("Each fractional diff value is a weighted sum of all past prices. "
                   "Lower d = more weights = longer memory. The weight threshold truncates "
                   "insignificant past values for computational efficiency.")
        for d_show in [0.3, min_d, 0.7, 1.0]:
            if d_show > 0:
                w = _frac_diff_weights(d_show, 100, thresh)
                st.markdown(f"**d={d_show:.2f}**: {len(w)} non-zero weights, slowest decay = {w[-1]:.6f}")

        # ADF detail table
        with st.expander("Full ADF Scan Results"):
            display_adf = adf_df.copy()
            display_adf["Stationary"] = display_adf["pvalue"] < 0.05
            display_adf["d"] = display_adf["d"].apply(lambda v: f"{v:.2f}")
            display_adf["adf_stat"] = display_adf["adf_stat"].apply(lambda v: f"{v:.3f}")
            display_adf["pvalue"] = display_adf["pvalue"].apply(lambda v: f"{v:.4f}")
            st.dataframe(display_adf, use_container_width=True, hide_index=True)


# ═══════════════════════════════════════════════
# TAB 2: STRUCTURAL BREAKS
# ═══════════════════════════════════════════════
with tab_breaks, error_boundary("Structural Breaks"):
    st.subheader("Structural Break Detection")
    st.caption("AFML Ch. 17 — CUSUM filter for event-driven sampling, and SADF test for explosive behavior (bubble detection).")

    with st.expander("How to read this tab", expanded=False):
        st.markdown(
            "**CUSUM Filter** (top section)\n\n"
            "Traditional ML uses fixed-interval bars (daily, hourly). But markets don't generate information "
            "at fixed intervals — some days nothing happens, other days everything moves. The CUSUM filter "
            "only samples when cumulative returns exceed a threshold, creating *event-driven* observations.\n\n"
            "- **S+** (green) tracks cumulative upward moves. When it crosses the threshold → bullish event\n"
            "- **S-** (red) tracks cumulative downward moves. When it crosses → bearish event\n"
            "- **Compression** shows what % of time bars become events — lower = more selective\n"
            "- Adjust the **threshold** (in standard deviations) to control sensitivity. "
            "Lower threshold = more events but more noise. Higher = fewer but more significant events.\n\n"
            "---\n\n"
            "**SADF Bubble Test** (bottom section)\n\n"
            "The Supremum Augmented Dickey-Fuller test detects *explosive* behavior in prices — "
            "periods where prices grow faster than a random walk (i.e., bubbles).\n\n"
            "- When the **ADF statistic** (blue) crosses above the **critical value** (red dashed), "
            "the price is in an explosive regime\n"
            "- Shaded regions on the price chart mark detected bubble periods\n"
            "- This is a *retrospective* indicator — it confirms bubbles but doesn't predict when they'll pop\n\n"
            "**Reference:** Lopez de Prado, AFML Ch. 17; Phillips, Shi & Yu (2015) for SADF theory."
        )

    # ── CUSUM Filter ──
    st.markdown("#### CUSUM Filter")
    st.caption("The CUSUM tracks cumulative deviations from zero. S+ accumulates positive returns; "
               "S- accumulates negative returns. When either exceeds the threshold, an event is triggered "
               "and the counter resets. The result is a set of timestamps where 'something meaningful happened' — "
               "use these as entry points for ML models instead of fixed daily bars.")

    cusum_h = st.slider("CUSUM threshold (std devs)", 0.5, 5.0, 2.0, 0.25, key="ql_cusum_h")
    sigma = log_returns.std()
    h = cusum_h * sigma

    # Symmetric CUSUM
    s_pos, s_neg = 0.0, 0.0
    cusum_events = []
    cusum_pos_vals = []
    cusum_neg_vals = []
    for i, (dt, r) in enumerate(log_returns.items()):
        s_pos = max(0, s_pos + r)
        s_neg = min(0, s_neg + r)
        cusum_pos_vals.append(s_pos)
        cusum_neg_vals.append(s_neg)
        if s_pos > h:
            cusum_events.append({"date": dt, "type": "Up", "value": s_pos})
            s_pos = 0
        elif s_neg < -h:
            cusum_events.append({"date": dt, "type": "Down", "value": s_neg})
            s_neg = 0

    cusum_df = pd.DataFrame(cusum_events)
    n_events = len(cusum_df)
    compression = n_events / len(log_returns) * 100 if len(log_returns) > 0 else 0

    cm1, cm2, cm3 = st.columns(3)
    cm1.metric("CUSUM Events", n_events)
    cm2.metric("Compression", f"{compression:.1f}%", help="Events as % of total observations")
    cm3.metric("Threshold", f"{h:.4f}", help=f"{cusum_h:.1f} standard deviations")

    # CUSUM chart
    fig_cusum = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.6, 0.4],
                              vertical_spacing=0.05)

    fig_cusum.add_trace(go.Scatter(x=close.index, y=close, mode="lines",
                                   line=dict(color="#555", width=1), name="Price"), row=1, col=1)
    if not cusum_df.empty:
        ups = cusum_df[cusum_df["type"] == "Up"]
        downs = cusum_df[cusum_df["type"] == "Down"]
        if not ups.empty:
            fig_cusum.add_trace(go.Scatter(
                x=ups["date"], y=close.loc[ups["date"]],
                mode="markers", marker=dict(color="#00ff88", size=6, symbol="triangle-up"),
                name=f"Up ({len(ups)})",
            ), row=1, col=1)
        if not downs.empty:
            fig_cusum.add_trace(go.Scatter(
                x=downs["date"], y=close.loc[downs["date"]],
                mode="markers", marker=dict(color="#ff4444", size=6, symbol="triangle-down"),
                name=f"Down ({len(downs)})",
            ), row=1, col=1)

    fig_cusum.add_trace(go.Scatter(x=log_returns.index, y=cusum_pos_vals, mode="lines",
                                   line=dict(color="#00ff88", width=1), name="S+"), row=2, col=1)
    fig_cusum.add_trace(go.Scatter(x=log_returns.index, y=cusum_neg_vals, mode="lines",
                                   line=dict(color="#ff4444", width=1), name="S-"), row=2, col=1)
    fig_cusum.add_hline(y=h, line_dash="dash", line_color="#ffaa00", row=2, col=1)
    fig_cusum.add_hline(y=-h, line_dash="dash", line_color="#ffaa00", row=2, col=1)

    fig_cusum.update_layout(template="plotly_dark", height=500,
                            title=f"CUSUM Filter (h={cusum_h:.1f}σ) — {n_events} events from {len(close)} observations",
                            margin=dict(l=0, r=0, t=40, b=0), showlegend=True,
                            legend=dict(orientation="h", y=-0.08))
    fig_cusum.update_yaxes(title_text="Price", row=1, col=1)
    fig_cusum.update_yaxes(title_text="CUSUM", row=2, col=1)
    st.plotly_chart(fig_cusum, use_container_width=True, config=PLOTLY_NOBAR)

    # ── SADF Test (Supremum ADF for bubble detection) ──
    st.markdown("---")
    st.markdown("#### SADF Bubble Detection")
    st.caption("Runs ADF tests over expanding windows from each start point. The supremum (maximum) "
               "of all these test statistics is the SADF statistic. When it exceeds the critical value, "
               "the price series is growing explosively — faster than a random walk can explain. "
               "Red shaded regions on the price chart mark periods where this explosive behavior is detected.")

    def _rolling_adf(series: pd.Series, min_window: int = 63) -> pd.DataFrame:
        """Compute rolling ADF statistic (SADF-style) with expanding window."""
        from statsmodels.tsa.stattools import adfuller
        results = []
        n = len(series)
        for end in range(min_window, n):
            window = series.iloc[:end + 1]
            try:
                stat = adfuller(window.values, autolag="AIC", regression="c")[0]
                results.append({"date": series.index[end], "adf_stat": stat, "window": end + 1})
            except Exception:
                pass
        return pd.DataFrame(results)

    with st.spinner("Computing rolling ADF (this may take a moment)..."):
        sadf_df = _rolling_adf(log_prices, min_window=max(63, len(log_prices) // 10))

    if not sadf_df.empty:
        sadf_stat = sadf_df["adf_stat"].max()
        sadf_date = sadf_df.loc[sadf_df["adf_stat"].idxmax(), "date"]
        # Critical values for SADF depend on sample size T (Phillips, Shi & Yu 2015, Table 2)
        # Interpolated from PSY simulated critical values for constant+trend regression
        T = len(log_prices)
        if T >= 800:
            cv_95 = 0.60
        elif T >= 400:
            cv_95 = 0.40
        elif T >= 200:
            cv_95 = 0.20
        else:
            cv_95 = 0.0
        is_bubble = sadf_stat > cv_95

        sb1, sb2 = st.columns(2)
        sb1.metric("SADF Statistic", f"{sadf_stat:.3f}", delta="BUBBLE" if is_bubble else "No bubble",
                   delta_color="inverse")
        sb2.metric("Peak Date", sadf_date.strftime("%Y-%m-%d") if hasattr(sadf_date, 'strftime') else str(sadf_date))

        fig_sadf = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.5, 0.5], vertical_spacing=0.05)
        fig_sadf.add_trace(go.Scatter(x=close.index, y=close, mode="lines",
                                      line=dict(color="#555", width=1), name="Price"), row=1, col=1)
        fig_sadf.add_trace(go.Scatter(x=sadf_df["date"], y=sadf_df["adf_stat"], mode="lines",
                                      line=dict(color="#00d1ff", width=2), name="ADF stat"), row=2, col=1)
        fig_sadf.add_hline(y=cv_95, line_dash="dash", line_color="#ff4444", row=2, col=1,
                           annotation_text=f"95% CV ({cv_95})")
        # Shade bubble periods (handle non-contiguous segments)
        bubble_mask = sadf_df["adf_stat"] > cv_95
        if bubble_mask.any():
            bubble_dates = sadf_df.loc[bubble_mask, "date"].tolist()
            # Find contiguous segments
            segments = []
            seg_start = bubble_dates[0]
            for k in range(1, len(bubble_dates)):
                gap = (bubble_dates[k] - bubble_dates[k - 1]).days if hasattr(bubble_dates[k], 'days') else 10
                if gap > 5:  # new segment if gap > 5 trading days
                    segments.append((seg_start, bubble_dates[k - 1]))
                    seg_start = bubble_dates[k]
            segments.append((seg_start, bubble_dates[-1]))
            for seg_s, seg_e in segments:
                seg_close = close.loc[seg_s:seg_e]
                if not seg_close.empty:
                    fig_sadf.add_trace(go.Scatter(
                        x=seg_close.index, y=seg_close,
                        mode="lines", fill="tozeroy", fillcolor="rgba(255,68,68,0.1)",
                        line=dict(color="#ff4444", width=2), name="Bubble",
                        showlegend=(seg_s == segments[0][0]),
                    ), row=1, col=1)

        fig_sadf.update_layout(template="plotly_dark", height=500, title="SADF Bubble Test",
                               margin=dict(l=0, r=0, t=40, b=0), legend=dict(orientation="h", y=-0.08))
        fig_sadf.update_yaxes(title_text="Price", row=1, col=1)
        fig_sadf.update_yaxes(title_text="ADF Statistic", row=2, col=1)
        st.plotly_chart(fig_sadf, use_container_width=True, config=PLOTLY_NOBAR)

    # ── Chow Breakpoint Test ──
    st.markdown("---")
    st.markdown("#### Chow Breakpoint Test")
    st.caption("Fits a linear model to the full sample and to two sub-samples split at each candidate date. "
               "If the sub-models fit significantly better than the full model (high F-statistic), "
               "the data-generating process changed at that point — a structural break. "
               "Red vertical lines on the price chart mark the top 5 most significant breakpoints.")

    def _chow_test(series: pd.Series, min_segment: int = 30) -> pd.DataFrame:
        """Rolling Chow test: scan all candidate breakpoints and return F-statistic."""
        y = series.values
        x = np.arange(len(y)).reshape(-1, 1)
        n = len(y)
        results = []
        for bp in range(min_segment, n - min_segment):
            # Full model
            X_full = np.column_stack([np.ones(n), x])
            beta_full = np.linalg.lstsq(X_full, y, rcond=None)[0]
            rss_full = np.sum((y - X_full @ beta_full) ** 2)

            # Two sub-models
            X1, y1 = X_full[:bp], y[:bp]
            X2, y2 = X_full[bp:], y[bp:]
            beta1 = np.linalg.lstsq(X1, y1, rcond=None)[0]
            beta2 = np.linalg.lstsq(X2, y2, rcond=None)[0]
            rss_sub = np.sum((y1 - X1 @ beta1) ** 2) + np.sum((y2 - X2 @ beta2) ** 2)

            k = X_full.shape[1]
            f_stat = ((rss_full - rss_sub) / k) / (rss_sub / (n - 2 * k)) if rss_sub > 0 else 0
            results.append({"date": series.index[bp], "f_stat": f_stat})
        return pd.DataFrame(results)

    with st.spinner("Computing Chow breakpoints..."):
        chow_df = _chow_test(log_returns, min_segment=max(30, len(log_returns) // 20))

    if not chow_df.empty:
        from scipy.stats import f as f_dist
        n_obs = len(log_returns)
        cv_chow = f_dist.ppf(0.99, 2, n_obs - 4)  # 99% F critical value

        top_break = chow_df.loc[chow_df["f_stat"].idxmax()]
        st.metric("Strongest Breakpoint", top_break["date"].strftime("%Y-%m-%d"),
                  delta=f"F={top_break['f_stat']:.1f}" if top_break['f_stat'] > cv_chow else "Not significant")

        fig_chow = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.5, 0.5], vertical_spacing=0.05)
        fig_chow.add_trace(go.Scatter(x=close.index, y=close, mode="lines",
                                       line=dict(color="#555", width=1), name="Price"), row=1, col=1)
        fig_chow.add_trace(go.Scatter(x=chow_df["date"], y=chow_df["f_stat"], mode="lines",
                                       line=dict(color="#00d1ff", width=2), name="F-stat"), row=2, col=1)
        fig_chow.add_hline(y=cv_chow, line_dash="dash", line_color="#ff4444", row=2, col=1,
                           annotation_text=f"99% CV ({cv_chow:.1f})")

        # Mark significant breaks on price chart
        sig_breaks = chow_df[chow_df["f_stat"] > cv_chow]
        if not sig_breaks.empty:
            for _, sb in sig_breaks.nlargest(5, "f_stat").iterrows():
                fig_chow.add_vline(x=sb["date"], line_dash="dot", line_color="#ff4444",
                                    line_width=1, row=1, col=1)

        fig_chow.update_layout(template="plotly_dark", height=450, title="Chow Breakpoint Test",
                               margin=dict(l=0, r=0, t=40, b=0), legend=dict(orientation="h", y=-0.08))
        fig_chow.update_yaxes(title_text="Price", row=1, col=1)
        fig_chow.update_yaxes(title_text="F-Statistic", row=2, col=1)
        st.plotly_chart(fig_chow, use_container_width=True, config=PLOTLY_NOBAR)


# ═══════════════════════════════════════════════
# TAB 3: TRIPLE BARRIER & META-LABELING
# ═══════════════════════════════════════════════
with tab_barrier, error_boundary("Triple Barrier"):
    st.subheader("Triple Barrier Labeling")
    st.caption("AFML Ch. 3 — Labels each event with {1, 0, -1} based on which barrier is hit first.")

    with st.expander("How to read this tab", expanded=False):
        st.markdown(
            "**The problem:** Standard ML labels (next-day return sign) ignore risk management. "
            "A 0.01% gain and a 5% gain both get label=1, and you never know when to exit.\n\n"
            "**The solution:** The Triple Barrier method labels each entry based on *which barrier is hit first*:\n"
            "- **Profit-take** (upper barrier): price rises by X ATRs → label = +1\n"
            "- **Stop-loss** (lower barrier): price falls by X ATRs → label = -1\n"
            "- **Time expiry** (vertical barrier): neither hit within N days → label = 0\n\n"
            "**ATR** (Average True Range) scales barriers to current volatility — "
            "wider in volatile markets, tighter in calm ones.\n\n"
            "**How to interpret:**\n"
            "- A high % of profit-takes with low avg holding = strong trending behavior\n"
            "- A high % of time expiry = mean-reverting / range-bound market\n"
            "- If stop-losses dominate, the market is trending against you\n\n"
            "---\n\n"
            "**Meta-Labeling** extends this by training a secondary model to predict "
            "*how confident* we should be in each label. This confidence becomes the **bet size** (0 to 1). "
            "Small bet on low-confidence signals, full bet on high-confidence ones.\n\n"
            "**Kelly Criterion** shows the theoretically optimal fraction of capital to risk per bet, "
            "given the win rate and win/loss ratio. **Half Kelly** is standard practice (lower drawdowns).\n\n"
            "**Reference:** Lopez de Prado, AFML Ch. 3."
        )

    bc1, bc2, bc3 = st.columns(3)
    with bc1:
        pt_mult = st.slider("Profit-take (ATR mult)", 0.5, 5.0, 2.0, 0.25, key="ql_pt")
    with bc2:
        sl_mult = st.slider("Stop-loss (ATR mult)", 0.5, 5.0, 2.0, 0.25, key="ql_sl")
    with bc3:
        max_holding = st.slider("Max holding period (days)", 5, 60, 20, 5, key="ql_hold")

    # ATR for barrier widths
    if "High" in df.columns and "Low" in df.columns:
        tr = pd.concat([
            df["High"] - df["Low"],
            (df["High"] - df["Close"].shift(1)).abs(),
            (df["Low"] - df["Close"].shift(1)).abs(),
        ], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
    else:
        atr = close.rolling(20).std()  # daily price volatility as ATR proxy

    # Apply triple barrier
    labels = []
    for i in range(len(close) - max_holding):
        entry = close.iloc[i]
        entry_atr = atr.iloc[i]
        if pd.isna(entry_atr) or entry_atr <= 0:
            continue

        upper = entry + pt_mult * entry_atr
        lower = entry - sl_mult * entry_atr

        # Scan forward
        label = 0  # time expiry (no barrier hit)
        exit_idx = min(i + max_holding, len(close) - 1)
        exit_date = close.index[exit_idx]
        for j in range(i + 1, min(i + max_holding + 1, len(close))):
            if close.iloc[j] >= upper:
                label = 1
                exit_idx = j
                exit_date = close.index[j]
                break
            elif close.iloc[j] <= lower:
                label = -1
                exit_idx = j
                exit_date = close.index[j]
                break

        ret = (close.iloc[exit_idx] / entry - 1) * 100
        hold_days = exit_idx - i
        labels.append({
            "entry_date": close.index[i], "entry_price": entry,
            "exit_date": exit_date, "exit_price": close.iloc[exit_idx],
            "label": label, "return_pct": ret, "hold_days": hold_days,
            "barrier": {1: "Profit-Take", -1: "Stop-Loss", 0: "Time Expiry"}[label],
        })

    label_df = pd.DataFrame(labels)

    if not label_df.empty:
        # Summary
        lm1, lm2, lm3, lm4, lm5 = st.columns(5)
        n_labels = len(label_df)
        wins = (label_df["label"] == 1).sum()
        losses = (label_df["label"] == -1).sum()
        timeouts = (label_df["label"] == 0).sum()
        lm1.metric("Total Events", n_labels)
        lm2.metric("Profit-Take", f"{wins} ({wins/n_labels*100:.0f}%)")
        lm3.metric("Stop-Loss", f"{losses} ({losses/n_labels*100:.0f}%)")
        lm4.metric("Time Expiry", f"{timeouts} ({timeouts/n_labels*100:.0f}%)")
        lm5.metric("Avg Return", f"{label_df['return_pct'].mean():+.2f}%")

        # Label distribution
        tb_c1, tb_c2 = st.columns(2)
        with tb_c1:
            fig_dist = go.Figure()
            for lbl, color, name in [(1, "#00ff88", "Profit-Take"), (-1, "#ff4444", "Stop-Loss"), (0, "#ffaa00", "Time Expiry")]:
                sub = label_df[label_df["label"] == lbl]
                fig_dist.add_trace(go.Histogram(x=sub["return_pct"], name=name, marker_color=color, opacity=0.7, nbinsx=50))
            fig_dist.update_layout(template="plotly_dark", height=350, barmode="overlay",
                                   title="Return Distribution by Barrier Hit",
                                   xaxis_title="Return (%)", yaxis_title="Count",
                                   margin=dict(l=0, r=0, t=40, b=0))
            st.plotly_chart(fig_dist, use_container_width=True, config=PLOTLY_NOBAR)

        with tb_c2:
            fig_hold = go.Figure()
            for lbl, color, name in [(1, "#00ff88", "Profit-Take"), (-1, "#ff4444", "Stop-Loss"), (0, "#ffaa00", "Time Expiry")]:
                sub = label_df[label_df["label"] == lbl]
                fig_hold.add_trace(go.Histogram(x=sub["hold_days"], name=name, marker_color=color, opacity=0.7, nbinsx=max_holding))
            fig_hold.update_layout(template="plotly_dark", height=350, barmode="overlay",
                                   title="Holding Period by Barrier Hit",
                                   xaxis_title="Days Held", yaxis_title="Count",
                                   margin=dict(l=0, r=0, t=40, b=0))
            st.plotly_chart(fig_hold, use_container_width=True, config=PLOTLY_NOBAR)

        # ── Meta-Labeling / Bet Sizing ──
        st.markdown("---")
        st.subheader("Meta-Labeling & Bet Sizing")
        st.caption("The primary model decides *direction* (long/short). The meta-label model decides *how much* to bet. "
                   "Here we use a rolling 50-period win rate as a simple meta-label proxy. "
                   "When the rolling win rate is high, bet more. When it's low, reduce exposure. "
                   "The Kelly Criterion gives the theoretically optimal bet fraction — half-Kelly is standard practice "
                   "because it reduces drawdowns at the cost of only slightly lower returns.")

        # Simple meta-label: use rolling win rate as confidence proxy
        label_df["entry_date"] = pd.to_datetime(label_df["entry_date"])
        label_df = label_df.set_index("entry_date").sort_index()
        label_df["win"] = (label_df["label"] == 1).astype(float)
        label_df["meta_prob"] = label_df["win"].rolling(50, min_periods=10).mean()
        label_df["bet_size"] = label_df["meta_prob"].clip(0, 1)

        # Kelly fraction: f* = p - q/b where p=win prob, q=1-p, b=avg win/avg loss
        avg_win = label_df[label_df["label"] == 1]["return_pct"].mean() if wins > 0 else 0
        avg_loss = abs(label_df[label_df["label"] == -1]["return_pct"].mean()) if losses > 0 else 1
        b = avg_win / avg_loss if avg_loss > 0 and avg_win > 0 else 1
        p = wins / n_labels if n_labels > 0 else 0.5
        kelly = max(0, p - (1 - p) / b) if b > 0 else 0  # clip negative Kelly to 0
        half_kelly = kelly / 2

        km1, km2, km3 = st.columns(3)
        km1.metric("Full Kelly", f"{kelly:.1%}", help="f* = p - q/b")
        km2.metric("Half Kelly (recommended)", f"{half_kelly:.1%}")
        km3.metric("Win/Loss Ratio", f"{b:.2f}x")

        fig_meta = go.Figure()
        fig_meta.add_trace(go.Scatter(
            x=label_df.index, y=label_df["bet_size"], mode="lines",
            line=dict(color="#00d1ff", width=2), name="Bet Size (rolling win rate)",
            fill="tozeroy", fillcolor="rgba(0,209,255,0.08)",
        ))
        fig_meta.add_hline(y=half_kelly, line_dash="dash", line_color="#00ff88",
                           annotation_text=f"Half Kelly: {half_kelly:.1%}")
        fig_meta.update_layout(template="plotly_dark", height=300,
                               title="Meta-Label Bet Sizing Over Time",
                               yaxis_title="Bet Size (0-1)", yaxis=dict(range=[0, 1.05]),
                               margin=dict(l=0, r=0, t=40, b=0))
        st.plotly_chart(fig_meta, use_container_width=True, config=PLOTLY_NOBAR)

        # ── Strategy Equity Curve ──
        st.markdown("---")
        st.subheader("Triple Barrier Strategy Backtest")
        st.caption("Simulates a long-only strategy that enters at each observation and exits at whichever barrier hits first. "
                   "The equity curve shows cumulative P&L — compare against buy-and-hold to assess whether "
                   "the barrier-based exits add value over simply holding. Meta-label sizing should reduce drawdowns "
                   "by scaling down during losing streaks.")

        eq_mode = st.radio("Position sizing", ["Equal Size", "Meta-Label Sized"], horizontal=True, key="ql_eq_mode")

        # Build equity curve
        strat_returns = []
        for _, row in label_df.iterrows():
            if row["label"] == 1 or row["label"] == -1:
                size = row.get("bet_size", 1.0) if eq_mode == "Meta-Label Sized" else 1.0
                if pd.isna(size):
                    size = 0.5
                direction = 1  # long-only for now
                strat_returns.append({"date": row.name if hasattr(row, 'name') else row.get("exit_date"),
                                      "return": row["return_pct"] / 100 * size * direction})
        if strat_returns:
            strat_df = pd.DataFrame(strat_returns).set_index("date").sort_index()
            cum_strat = (1 + strat_df["return"]).cumprod() * 100

            # Buy and hold benchmark
            bh_start = label_df.index[0] if hasattr(label_df.index, 'min') else label_df.index.min()
            bh_end = label_df.index[-1] if hasattr(label_df.index, 'max') else label_df.index.max()
            bh = close.loc[bh_start:bh_end]
            bh_indexed = bh / bh.iloc[0] * 100

            fig_eq = go.Figure()
            fig_eq.add_trace(go.Scatter(x=cum_strat.index, y=cum_strat, mode="lines",
                                        name=f"Triple Barrier ({eq_mode})", line=dict(color="#00d1ff", width=2)))
            fig_eq.add_trace(go.Scatter(x=bh_indexed.index, y=bh_indexed, mode="lines",
                                        name="Buy & Hold", line=dict(color="#555", width=1)))
            fig_eq.add_hline(y=100, line_dash="dash", line_color="#333")
            fig_eq.update_layout(template="plotly_dark", height=380,
                                 title="Strategy Equity Curve vs Buy & Hold (base=100)",
                                 yaxis_title="Portfolio Value",
                                 legend=dict(orientation="h", y=-0.12),
                                 margin=dict(l=0, r=0, t=40, b=0))
            st.plotly_chart(fig_eq, use_container_width=True, config=PLOTLY_NOBAR)

            # Strategy metrics
            strat_ann_ret = strat_df["return"].mean() * 252 * 100
            strat_ann_vol = strat_df["return"].std() * np.sqrt(252) * 100
            strat_sharpe = strat_ann_ret / strat_ann_vol if strat_ann_vol > 0 else 0
            strat_dd = ((1 + strat_df["return"]).cumprod() / (1 + strat_df["return"]).cumprod().cummax() - 1).min() * 100
            sm1, sm2, sm3, sm4 = st.columns(4)
            sm1.metric("Ann. Return", f"{strat_ann_ret:.1f}%")
            sm2.metric("Ann. Vol", f"{strat_ann_vol:.1f}%")
            sm3.metric("Sharpe", f"{strat_sharpe:.2f}")
            sm4.metric("Max Drawdown", f"{strat_dd:.1f}%")


# ═══════════════════════════════════════════════
# TAB 4: SAMPLE WEIGHTS & BOOTSTRAP
# ═══════════════════════════════════════════════
with tab_weights, error_boundary("Sample Weights"):
    st.subheader("Sample Uniqueness & Sequential Bootstrap")
    st.caption("AFML Ch. 4 — Honest confidence intervals for financial data.")

    with st.expander("How to read this tab", expanded=False):
        st.markdown(
            "**The problem:** Standard IID bootstrap assumes each observation is independent. "
            "But in finance, labels overlap — a 20-day return starting on Monday shares 19 days "
            "with the return starting on Tuesday. This overlap means bootstrap samples are correlated, "
            "making Sharpe ratios look more significant than they are.\n\n"
            "**Average Uniqueness** measures how independent each observation is. "
            "If 10 labels overlap at time *t*, the uniqueness at *t* is 1/10 = 0.1. "
            "The sum of uniqueness gives the effective number of independent observations — "
            "always less than the total sample count.\n\n"
            "**Sequential Bootstrap** samples observations with probability proportional to their uniqueness. "
            "Unique observations (those that don't overlap much) are sampled more often. "
            "This produces **wider, more honest confidence intervals** than standard bootstrap.\n\n"
            "**How to interpret the chart:**\n"
            "- The **red** distribution (standard bootstrap) is typically narrower — it's *overconfident*\n"
            "- The **blue** distribution (sequential) is wider — it's *honest*\n"
            "- If your observed Sharpe falls in the left tail of the sequential distribution, "
            "the strategy may not be genuinely profitable\n\n"
            "**Reference:** Lopez de Prado, AFML Ch. 4."
        )

    # Compute average uniqueness
    window = st.slider("Overlap window (days)", 5, 60, 20, 5, key="ql_overlap_window")

    def _avg_uniqueness(returns: pd.Series, window: int) -> pd.Series:
        """Compute average uniqueness for each observation.
        Uniqueness = 1 / (number of concurrent labels at time t)."""
        n = len(returns)
        concurrency = np.ones(n)
        for i in range(n):
            start = max(0, i - window + 1)
            end = min(n, i + window)
            concurrency[i] = end - start
        uniqueness = 1.0 / concurrency
        return pd.Series(uniqueness, index=returns.index, name="uniqueness")

    uniqueness = _avg_uniqueness(log_returns, window)

    uw1, uw2, uw3 = st.columns(3)
    uw1.metric("Avg Uniqueness", f"{uniqueness.mean():.3f}")
    uw2.metric("Min Uniqueness", f"{uniqueness.min():.3f}")
    uw3.metric("Effective Samples", f"{uniqueness.sum():.0f}", help="Sum of uniqueness = effective independent observations")

    fig_uniq = go.Figure()
    fig_uniq.add_trace(go.Scatter(x=uniqueness.index, y=uniqueness, mode="lines",
                                  line=dict(color="#00d1ff", width=1), name="Uniqueness"))
    fig_uniq.add_hline(y=uniqueness.mean(), line_dash="dash", line_color="#ffaa00",
                       annotation_text=f"Mean: {uniqueness.mean():.3f}")
    fig_uniq.update_layout(template="plotly_dark", height=300,
                           title=f"Average Uniqueness (window={window}D)",
                           yaxis_title="Uniqueness (0-1)",
                           margin=dict(l=0, r=0, t=40, b=0))
    st.plotly_chart(fig_uniq, use_container_width=True, config=PLOTLY_NOBAR)

    # Sequential bootstrap vs standard
    st.markdown("---")
    st.subheader("Sequential vs Standard Bootstrap")
    st.caption("Both methods resample the return series and compute the Sharpe ratio for each resample. "
               "The standard bootstrap treats all observations equally (overconfident). "
               "The sequential bootstrap weights each observation by its uniqueness — "
               "overlapping observations get downweighted. The resulting distribution is wider and more honest. "
               "If the two distributions look similar, overlap isn't a major issue for this dataset.")

    n_bootstrap = st.slider("Bootstrap iterations", 500, 5000, 1000, 500, key="ql_n_bootstrap")

    rng = np.random.default_rng(42)
    ret_vals = log_returns.values
    uniq_vals = uniqueness.reindex(log_returns.index).values
    uniq_probs = uniq_vals / uniq_vals.sum()
    ann_factor = np.sqrt(252)

    with st.spinner(f"Running {n_bootstrap} bootstrap iterations..."):
        standard_sharpes = []
        sequential_sharpes = []
        for _ in range(n_bootstrap):
            # Standard IID bootstrap
            idx = rng.integers(0, len(ret_vals), len(ret_vals))
            sample = ret_vals[idx]
            if sample.std() > 0:
                standard_sharpes.append(sample.mean() / sample.std() * ann_factor)

            # Sequential bootstrap (weighted by uniqueness)
            idx_seq = rng.choice(len(ret_vals), size=len(ret_vals), replace=True, p=uniq_probs)
            sample_seq = ret_vals[idx_seq]
            if sample_seq.std() > 0:
                sequential_sharpes.append(sample_seq.mean() / sample_seq.std() * ann_factor)

    fig_boot = go.Figure()
    fig_boot.add_trace(go.Histogram(x=standard_sharpes, name="Standard Bootstrap",
                                    marker_color="#ff6b6b", opacity=0.6, nbinsx=50))
    fig_boot.add_trace(go.Histogram(x=sequential_sharpes, name="Sequential Bootstrap",
                                    marker_color="#00d1ff", opacity=0.6, nbinsx=50))
    fig_boot.update_layout(template="plotly_dark", height=380, barmode="overlay",
                           title="Sharpe Ratio Distribution: Standard vs Sequential Bootstrap",
                           xaxis_title="Annualized Sharpe Ratio",
                           margin=dict(l=0, r=0, t=40, b=0))
    st.plotly_chart(fig_boot, use_container_width=True, config=PLOTLY_NOBAR)

    bs1, bs2, bs3, bs4 = st.columns(4)
    bs1.metric("Standard Mean SR", f"{np.mean(standard_sharpes):.3f}")
    bs2.metric("Sequential Mean SR", f"{np.mean(sequential_sharpes):.3f}")
    bs3.metric("Standard Std", f"{np.std(standard_sharpes):.3f}")
    bs4.metric("Sequential Std", f"{np.std(sequential_sharpes):.3f}",
               help="Wider = more honest uncertainty. Sequential typically has wider CIs.")


# ═══════════════════════════════════════════════
# TAB 5: FEATURE IMPORTANCE
# ═══════════════════════════════════════════════
with tab_importance, error_boundary("Feature Importance"):
    st.subheader("Feature Importance (MDI, MDA, SFI)")
    st.caption("AFML Ch. 8 — Three methods to assess which features matter.")

    with st.expander("How to read this tab", expanded=False):
        st.markdown(
            "**Why three methods?** Each has different biases:\n\n"
            "| Method | Speed | Bias | Best For |\n"
            "|--------|-------|------|----------|\n"
            "| **MDI** (Mean Decrease Impurity) | Fast | Biased toward high-cardinality features | Quick screening |\n"
            "| **MDA** (Mean Decrease Accuracy) | Medium | Unbiased but noisy | Production feature selection |\n"
            "| **SFI** (Single Feature Importance) | Slow | Misses interactions | Isolated feature value |\n\n"
            "**MDI** measures how much each feature reduces impurity (Gini) when used in tree splits. "
            "It's fast but favors continuous features over binary ones, and uses in-sample data.\n\n"
            "**MDA** shuffles each feature's values and measures how much accuracy drops. "
            "Uses out-of-sample data (time-series split), so it's unbiased. The gold standard.\n\n"
            "**SFI** trains a separate model using only one feature at a time. "
            "Shows each feature's standalone predictive power, but misses interactions between features.\n\n"
            "**How to interpret the heatmap:**\n"
            "- Features with high scores across all 3 methods (bright across the row) are robust\n"
            "- Features ranked highly by MDI but low by MDA may be in-sample artifacts (overfit)\n"
            "- The **Rank Std** column in the agreement table shows consistency — lower = more reliable\n\n"
            "**Features used:** Returns (1/5/20D), volatility (20/60D), RSI-14, MACD, Bollinger width, "
            "rolling skewness, rolling kurtosis, volume ratio, OBV slope.\n\n"
            "**Target:** Sign of 5-day forward return (binary classification).\n\n"
            "**Reference:** Lopez de Prado, AFML Ch. 8."
        )

    # Build features
    feat_df = pd.DataFrame(index=close.index)
    feat_df["ret_1"] = log_returns
    feat_df["ret_5"] = log_prices.diff(5)
    feat_df["ret_20"] = log_prices.diff(20)
    feat_df["vol_20"] = log_returns.rolling(20).std()
    feat_df["vol_60"] = log_returns.rolling(60).std()
    _rsi_gain = log_returns.clip(lower=0).rolling(14).mean()
    _rsi_loss = log_returns.clip(upper=0).abs().rolling(14).mean()
    _rsi_rs = np.where(_rsi_loss > 0, _rsi_gain / _rsi_loss, 0)
    feat_df["rsi"] = pd.Series(np.where(_rsi_loss > 0, 100 - 100 / (1 + _rsi_rs), 50), index=_rsi_gain.index)
    feat_df["macd"] = close.ewm(span=12).mean() - close.ewm(span=26).mean()
    feat_df["bb_width"] = (close.rolling(20).std() * 2) / close.rolling(20).mean() * 100
    feat_df["skew_20"] = log_returns.rolling(20).skew()
    feat_df["kurt_20"] = log_returns.rolling(20).kurt()
    if not volume.empty and volume.notna().any():
        feat_df["volume_ratio"] = volume / volume.rolling(20).mean()
        feat_df["obv_slope"] = (volume * np.sign(log_returns)).rolling(20).mean()

    # Target: forward 5-day return sign
    feat_df["target"] = np.sign(log_prices.shift(-5) - log_prices)
    feat_df = feat_df.dropna()

    feature_cols = [c for c in feat_df.columns if c != "target"]

    if len(feat_df) > 100 and len(feature_cols) >= 3:
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.model_selection import cross_val_score

        X = feat_df[feature_cols].values
        y = (feat_df["target"] > 0).astype(int).values

        with st.spinner("Computing feature importance (3 methods)..."):
            # 1. MDI — Mean Decrease Impurity
            rf = RandomForestClassifier(n_estimators=100, max_depth=5, random_state=42, n_jobs=-1)
            rf.fit(X, y)
            mdi = pd.Series(rf.feature_importances_, index=feature_cols, name="MDI").sort_values(ascending=False)

            # 2. MDA — Mean Decrease Accuracy (permutation importance with purged CV)
            from sklearn.inspection import permutation_importance
            # Use time-series split (no future leakage)
            split = len(X) * 3 // 4
            X_train, X_test = X[:split], X[split:]
            y_train, y_test = y[:split], y[split:]
            rf_mda = RandomForestClassifier(n_estimators=100, max_depth=5, random_state=42, n_jobs=-1)
            rf_mda.fit(X_train, y_train)
            perm = permutation_importance(rf_mda, X_test, y_test, n_repeats=10, random_state=42, n_jobs=-1)
            mda = pd.Series(perm.importances_mean, index=feature_cols, name="MDA").sort_values(ascending=False)

            # 3. SFI — Single Feature Importance
            from sklearn.model_selection import TimeSeriesSplit
            sfi_scores = {}
            ts_cv = TimeSeriesSplit(n_splits=5)
            for feat in feature_cols:
                X_single = feat_df[[feat]].values[:split]
                rf_sfi = RandomForestClassifier(n_estimators=50, max_depth=3, random_state=42)
                score = cross_val_score(rf_sfi, X_single, y[:split], cv=ts_cv, scoring="accuracy").mean()
                sfi_scores[feat] = score - 0.5  # subtract random baseline
            sfi = pd.Series(sfi_scores, name="SFI").sort_values(ascending=False)

        # Combined heatmap
        importance_df = pd.DataFrame({"MDI": mdi, "MDA": mda, "SFI": sfi})
        # Normalize each to 0-1 for comparability
        for col in importance_df.columns:
            rng_val = importance_df[col].max() - importance_df[col].min()
            if rng_val > 0:
                importance_df[col] = (importance_df[col] - importance_df[col].min()) / rng_val

        # Sort by average importance
        importance_df["avg"] = importance_df.mean(axis=1)
        importance_df = importance_df.sort_values("avg", ascending=False)

        fig_imp = go.Figure(data=go.Heatmap(
            z=importance_df[["MDI", "MDA", "SFI"]].values,
            x=["MDI (Impurity)", "MDA (Accuracy)", "SFI (Isolated)"],
            y=importance_df.index.tolist(),
            colorscale=[[0, "#1a1a2e"], [0.5, "#ffaa00"], [1, "#00ff88"]],
            zmin=0, zmax=1,
            text=[[f"{v:.2f}" for v in row] for row in importance_df[["MDI", "MDA", "SFI"]].values],
            texttemplate="%{text}", textfont={"size": 11},
            colorbar=dict(title="Normalized"),
        ))
        fig_imp.update_layout(template="plotly_dark", height=max(350, len(feature_cols) * 28),
                              title="Feature Importance — 3 Methods (Normalized 0-1)",
                              margin=dict(l=0, r=0, t=40, b=0))
        st.plotly_chart(fig_imp, use_container_width=True, config=PLOTLY_NOBAR)

        # Model accuracy
        st.metric("OOS Accuracy (Random Forest)", f"{rf_mda.score(X_test, y_test):.1%}",
                  help="Out-of-sample accuracy on the last 25% of data")

        # Feature agreement
        st.subheader("Method Agreement")
        st.caption("Each method ranks features independently. Low Rank Std means all three methods agree — "
                   "the feature is genuinely important regardless of how you measure it. "
                   "High Rank Std means the feature's importance depends on methodology — treat with caution. "
                   "Features in the top 3 by Avg Rank with low Rank Std are the most robust candidates for production models.")
        rank_df = pd.DataFrame({
            "MDI Rank": mdi.rank(ascending=False),
            "MDA Rank": mda.rank(ascending=False),
            "SFI Rank": sfi.rank(ascending=False),
        })
        rank_df["Avg Rank"] = rank_df.mean(axis=1)
        rank_df["Rank Std"] = rank_df[["MDI Rank", "MDA Rank", "SFI Rank"]].std(axis=1)
        rank_df = rank_df.sort_values("Avg Rank")
        display_rank = rank_df.copy()
        for c in display_rank.columns:
            display_rank[c] = display_rank[c].apply(lambda v: f"{v:.1f}")
        st.dataframe(display_rank, use_container_width=True)

        # ── SHAP Values ──
        st.markdown("---")
        st.subheader("SHAP Feature Impact")
        st.caption("SHAP decomposes each prediction into contributions from each feature. "
                   "The bar chart shows mean |SHAP| — overall importance. "
                   "The scatter plots below show *how* each feature affects predictions: "
                   "if points slope upward, higher feature values push toward a positive prediction. "
                   "If they slope downward, higher values push negative. "
                   "Non-linear patterns (curves, splits) reveal threshold effects that linear models miss.")

        try:
            import shap
            with st.spinner("Computing SHAP values (this may take a moment)..."):
                explainer = shap.TreeExplainer(rf_mda)
                shap_values = explainer.shap_values(X_test)

                # Handle different shap output formats:
                # - list of [class_0_array, class_1_array] (older shap)
                # - 3D array (n_samples, n_features, n_classes) (newer shap)
                # - 2D array (n_samples, n_features) (regression or single-output)
                if isinstance(shap_values, list):
                    sv = shap_values[1]  # class 1 (positive return)
                elif isinstance(shap_values, np.ndarray) and shap_values.ndim == 3:
                    sv = shap_values[:, :, 1]  # class 1 slice from 3D array
                else:
                    sv = shap_values

                # Mean absolute SHAP
                mean_shap = pd.Series(np.abs(sv).mean(axis=0), index=feature_cols).sort_values(ascending=True)

                fig_shap = go.Figure()
                fig_shap.add_trace(go.Bar(
                    y=mean_shap.index, x=mean_shap.values,
                    orientation="h", marker_color="#00d1ff",
                    text=[f"{v:.4f}" for v in mean_shap.values], textposition="outside",
                ))
                fig_shap.update_layout(template="plotly_dark", height=max(300, len(feature_cols) * 25),
                                       title="Mean |SHAP| — Feature Impact on Positive Return Prediction",
                                       xaxis_title="Mean |SHAP value|",
                                       margin=dict(l=0, r=80, t=40, b=0))
                st.plotly_chart(fig_shap, use_container_width=True, config=PLOTLY_NOBAR)

                # SHAP direction heatmap (top features)
                top_feats = mean_shap.tail(6).index.tolist()
                if top_feats:
                    shap_dir = pd.DataFrame(sv, columns=feature_cols)[top_feats]
                    feat_vals = pd.DataFrame(X_test, columns=feature_cols)[top_feats]

                    dir_c1, dir_c2 = st.columns(2)
                    for i, feat in enumerate(top_feats[:4]):
                        with [dir_c1, dir_c2][i % 2]:
                            fig_dep = go.Figure()
                            fig_dep.add_trace(go.Scatter(
                                x=feat_vals[feat], y=shap_dir[feat],
                                mode="markers", marker=dict(size=3, color="#00d1ff", opacity=0.3),
                            ))
                            fig_dep.add_hline(y=0, line_dash="dash", line_color="#333")
                            fig_dep.update_layout(template="plotly_dark", height=250,
                                                   title=f"SHAP: {feat}",
                                                   xaxis_title=feat, yaxis_title="SHAP value",
                                                   margin=dict(l=0, r=0, t=30, b=0))
                            st.plotly_chart(fig_dep, use_container_width=True, config=PLOTLY_NOBAR)
        except Exception as e:
            st.info(f"SHAP analysis unavailable: {e}")
    else:
        st.warning("Insufficient data or features for importance analysis.")


# ═══════════════════════════════════════════════
# TAB 6: HRP — HIERARCHICAL RISK PARITY
# ═══════════════════════════════════════════════
with tab_hrp, error_boundary("HRP"):
    st.subheader("Hierarchical Risk Parity")
    st.caption("MLAM Ch. 16 — Lopez de Prado's alternative to Markowitz.")

    with st.expander("How to read this tab", expanded=False):
        st.markdown(
            "**The problem with Markowitz:** Mean-variance optimization inverts the covariance matrix, "
            "which is numerically unstable — small estimation errors in correlations produce wildly different "
            "optimal weights. The result: concentrated, unstable portfolios that blow up out of sample.\n\n"
            "**HRP solves this in 3 steps:**\n"
            "1. **Tree clustering** — group assets by correlation similarity (no matrix inversion needed)\n"
            "2. **Quasi-diagonalization** — reorder the covariance matrix so correlated assets are adjacent\n"
            "3. **Recursive bisection** — split the tree at each node and allocate inversely proportional "
            "to cluster variance. Low-variance clusters get more weight.\n\n"
            "**How to interpret:**\n"
            "- The **bar chart** compares HRP weights to equal-weight and inverse-vol baselines\n"
            "- HRP will underweight highly correlated asset groups (avoids concentration)\n"
            "- HRP will overweight uncorrelated, low-vol assets (natural diversifiers)\n"
            "- The **backtest** shows cumulative performance — HRP typically has lower drawdowns than equal-weight\n\n"
            "**When to use HRP over Markowitz:**\n"
            "- When you have more assets than observations (N > T)\n"
            "- When correlations are unstable (regime changes)\n"
            "- When you want a robust, stable allocation that doesn't change drastically with new data\n\n"
            "**Reference:** Lopez de Prado, *Machine Learning for Asset Managers*, Ch. 16; "
            "Lopez de Prado (2016), 'Building Diversified Portfolios that Outperform Out of Sample', "
            "*Journal of Portfolio Management*."
        )

    # Multi-asset input
    hrp_tickers_input = st.text_input("Portfolio tickers (comma-separated)",
                                       value="SPY,TLT,GLD,EFA,IWM,USO,HYG,XLK",
                                       key="ql_hrp_tickers")
    hrp_tickers = [t.strip().upper() for t in hrp_tickers_input.split(",") if t.strip()]

    if len(hrp_tickers) >= 3:
        with st.spinner(f"Loading {len(hrp_tickers)} assets..."):
            hrp_prices = fetch_price_history(hrp_tickers, period="2y")

        if not hrp_prices.empty and len(hrp_prices.columns) >= 3:
            hrp_returns = hrp_prices.pct_change().dropna()
            hrp_cov = hrp_returns.cov() * 252
            hrp_corr = hrp_returns.corr()
            hrp_tickers_avail = hrp_returns.columns.tolist()

            from scipy.cluster.hierarchy import linkage as hrp_linkage, leaves_list
            from scipy.spatial.distance import squareform as hrp_squareform

            def _hrp_allocate(cov: pd.DataFrame, corr: pd.DataFrame) -> pd.Series:
                """Hierarchical Risk Parity allocation (de Prado)."""
                tickers = cov.columns.tolist()
                n = len(tickers)

                # 1. Tree clustering
                dist = ((1 - corr) / 2.0).clip(lower=0) ** 0.5
                np.fill_diagonal(dist.values, 0)
                dist = (dist + dist.T) / 2
                condensed = hrp_squareform(dist.values, checks=False)
                link = hrp_linkage(condensed, method="single")

                # 2. Quasi-diagonalization
                sort_idx = leaves_list(link).tolist()
                sorted_tickers = [tickers[i] for i in sort_idx]

                # 3. Recursive bisection
                weights = pd.Series(1.0, index=sorted_tickers)

                def _get_cluster_var(cov_sub, tickers_sub):
                    """Inverse-variance allocation within a cluster."""
                    ivp = 1.0 / np.diag(cov_sub.loc[tickers_sub, tickers_sub].values)
                    ivp /= ivp.sum()
                    return np.dot(ivp, np.dot(cov_sub.loc[tickers_sub, tickers_sub].values, ivp))

                clusters = [sorted_tickers]
                while len(clusters) > 0:
                    new_clusters = []
                    for cluster in clusters:
                        if len(cluster) <= 1:
                            continue
                        mid = len(cluster) // 2
                        left = cluster[:mid]
                        right = cluster[mid:]

                        var_left = _get_cluster_var(cov, left)
                        var_right = _get_cluster_var(cov, right)
                        total_var = var_left + var_right
                        alpha = 1 - var_left / total_var if total_var > 0 else 0.5

                        weights[left] *= alpha
                        weights[right] *= (1 - alpha)

                        if len(left) > 1:
                            new_clusters.append(left)
                        if len(right) > 1:
                            new_clusters.append(right)
                    clusters = new_clusters

                return weights / weights.sum()

            hrp_weights = _hrp_allocate(hrp_cov, hrp_corr)

            # Compare with equal weight and inverse vol
            eq_weights = pd.Series(1.0 / len(hrp_tickers_avail), index=hrp_tickers_avail)
            vol = hrp_returns.std() * np.sqrt(252)
            iv_weights = (1 / vol) / (1 / vol).sum()

            # Display weights
            fig_w = go.Figure()
            fig_w.add_trace(go.Bar(x=hrp_weights.index, y=hrp_weights.values * 100,
                                   name="HRP", marker_color="#00d1ff"))
            fig_w.add_trace(go.Bar(x=eq_weights.index, y=eq_weights.values * 100,
                                   name="Equal Weight", marker_color="#555"))
            fig_w.add_trace(go.Bar(x=iv_weights.index, y=iv_weights.reindex(hrp_weights.index).values * 100,
                                   name="Inverse Vol", marker_color="#ffaa00"))
            fig_w.update_layout(template="plotly_dark", height=380, barmode="group",
                                title="Portfolio Weights: HRP vs Equal Weight vs Inverse Vol",
                                yaxis_title="Weight (%)",
                                margin=dict(l=0, r=0, t=40, b=0))
            st.plotly_chart(fig_w, use_container_width=True, config=PLOTLY_NOBAR)

            # Backtest comparison
            st.subheader("Backtest Comparison")
            st.caption("Static backtest: weights computed once from the full dataset and held constant. "
                       "This has look-ahead bias — use the walk-forward version below for realistic results.")
            port_hrp = (hrp_returns * hrp_weights).sum(axis=1)
            port_eq = (hrp_returns * eq_weights).sum(axis=1)
            port_iv = (hrp_returns * iv_weights.reindex(hrp_tickers_avail)).sum(axis=1)

            cum_hrp = (1 + port_hrp).cumprod() * 100
            cum_eq = (1 + port_eq).cumprod() * 100
            cum_iv = (1 + port_iv).cumprod() * 100

            fig_bt = go.Figure()
            fig_bt.add_trace(go.Scatter(x=cum_hrp.index, y=cum_hrp, mode="lines",
                                        name="HRP", line=dict(color="#00d1ff", width=3)))
            fig_bt.add_trace(go.Scatter(x=cum_eq.index, y=cum_eq, mode="lines",
                                        name="Equal Weight", line=dict(color="#555", width=1)))
            fig_bt.add_trace(go.Scatter(x=cum_iv.index, y=cum_iv, mode="lines",
                                        name="Inverse Vol", line=dict(color="#ffaa00", width=1, dash="dash")))
            fig_bt.add_hline(y=100, line_dash="dash", line_color="#333")
            fig_bt.update_layout(template="plotly_dark", height=400,
                                 title="Cumulative Performance (base=100)",
                                 yaxis_title="Portfolio Value",
                                 legend=dict(orientation="h", y=-0.12),
                                 margin=dict(l=0, r=0, t=40, b=0))
            st.plotly_chart(fig_bt, use_container_width=True, config=PLOTLY_NOBAR)

            # Risk metrics
            def _portfolio_metrics(port_returns, name):
                ann_ret = port_returns.mean() * 252 * 100
                ann_vol = port_returns.std() * np.sqrt(252) * 100
                sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
                dd = ((1 + port_returns).cumprod() / (1 + port_returns).cumprod().cummax() - 1).min() * 100
                return {"Method": name, "Ann. Return": f"{ann_ret:.1f}%", "Ann. Vol": f"{ann_vol:.1f}%",
                        "Sharpe": f"{sharpe:.2f}", "Max DD": f"{dd:.1f}%"}

            metrics = [
                _portfolio_metrics(port_hrp, "HRP"),
                _portfolio_metrics(port_eq, "Equal Weight"),
                _portfolio_metrics(port_iv, "Inverse Vol"),
            ]
            st.dataframe(pd.DataFrame(metrics), use_container_width=True, hide_index=True)

            # ── Walk-Forward Rebalanced Backtest ──
            st.markdown("---")
            st.subheader("Walk-Forward Rebalanced HRP")
            st.caption("At each rebalance date, HRP weights are computed using only the trailing 252 trading days "
                       "of return data — no future information is used. The portfolio is then held until the next rebalance. "
                       "This is the realistic, deployable version. Compare Sharpe and max drawdown with the static version above. "
                       "The weight evolution chart shows how HRP adapts to changing market conditions over time.")

            rebal_freq = st.radio("Rebalance frequency", ["Monthly", "Quarterly"], horizontal=True, key="ql_hrp_rebal")
            rebal_period = "ME" if rebal_freq == "Monthly" else "QE"

            # Get rebalance dates
            rebal_dates = hrp_returns.resample(rebal_period).last().index
            estimation_window = 252

            wf_returns = []
            wf_weights_history = []
            prev_weights = eq_weights  # start with equal weight
            for i in range(len(rebal_dates)):
                rd = rebal_dates[i]
                # Estimation window: trailing 252 days
                est_end = rd
                est_start_idx = max(0, hrp_returns.index.get_loc(est_end) - estimation_window)
                est_returns = hrp_returns.iloc[est_start_idx:hrp_returns.index.get_loc(est_end) + 1]

                if len(est_returns) >= 60:
                    est_cov = est_returns.cov() * 252
                    est_corr = est_returns.corr()
                    try:
                        new_weights = _hrp_allocate(est_cov, est_corr)
                        prev_weights = new_weights
                    except Exception:
                        new_weights = prev_weights
                else:
                    new_weights = prev_weights

                wf_weights_history.append({"date": rd, **{t: new_weights.get(t, 0) for t in hrp_tickers_avail}})

                # Apply weights until next rebalance
                if i < len(rebal_dates) - 1:
                    period_returns = hrp_returns.loc[rd:rebal_dates[i + 1]]
                else:
                    period_returns = hrp_returns.loc[rd:]

                for dt, row in period_returns.iterrows():
                    wf_returns.append({"date": dt, "return": (row * new_weights).sum()})

            if wf_returns:
                wf_df = pd.DataFrame(wf_returns).set_index("date")
                wf_df = wf_df[~wf_df.index.duplicated(keep="first")]
                cum_wf = (1 + wf_df["return"]).cumprod() * 100

                fig_wf = go.Figure()
                fig_wf.add_trace(go.Scatter(x=cum_wf.index, y=cum_wf, mode="lines",
                                            name=f"HRP Walk-Forward ({rebal_freq})", line=dict(color="#00ff88", width=3)))
                fig_wf.add_trace(go.Scatter(x=cum_hrp.index, y=cum_hrp, mode="lines",
                                            name="HRP Static", line=dict(color="#00d1ff", width=1, dash="dash")))
                fig_wf.add_trace(go.Scatter(x=cum_eq.index, y=cum_eq, mode="lines",
                                            name="Equal Weight", line=dict(color="#555", width=1)))
                fig_wf.add_hline(y=100, line_dash="dash", line_color="#333")
                fig_wf.update_layout(template="plotly_dark", height=400,
                                     title=f"Walk-Forward HRP ({rebal_freq} Rebalance, 252D Window)",
                                     yaxis_title="Portfolio Value",
                                     legend=dict(orientation="h", y=-0.12),
                                     margin=dict(l=0, r=0, t=40, b=0))
                st.plotly_chart(fig_wf, use_container_width=True, config=PLOTLY_NOBAR)

                wf_metrics = _portfolio_metrics(wf_df["return"], f"HRP WF ({rebal_freq})")
                st.dataframe(pd.DataFrame([wf_metrics, _portfolio_metrics(port_hrp, "HRP Static"),
                                           _portfolio_metrics(port_eq, "Equal Weight")]),
                             use_container_width=True, hide_index=True)

            # Weight evolution over time
            if wf_weights_history:
                wh_df = pd.DataFrame(wf_weights_history).set_index("date")
                fig_we = go.Figure()
                for t in hrp_tickers_avail:
                    if t in wh_df.columns:
                        fig_we.add_trace(go.Scatter(x=wh_df.index, y=wh_df[t] * 100, mode="lines",
                                                     name=t, stackgroup="one"))
                fig_we.update_layout(template="plotly_dark", height=350,
                                     title="HRP Weight Evolution Over Time",
                                     yaxis_title="Weight (%)", yaxis=dict(range=[0, 100]),
                                     legend=dict(orientation="h", y=-0.15),
                                     margin=dict(l=0, r=0, t=40, b=0))
                st.plotly_chart(fig_we, use_container_width=True, config=PLOTLY_NOBAR)
        else:
            st.warning("Could not load enough price data for the selected tickers.")
    else:
        st.info("Enter at least 3 tickers for HRP allocation.")


# ═══════════════════════════════════════════════
# TAB 7: MICROSTRUCTURE
# ═══════════════════════════════════════════════
with tab_micro, error_boundary("Microstructure"):
    st.subheader("Market Microstructure Features")
    st.caption("AFML Ch. 19 — Information-driven features from trade/volume data.")

    with st.expander("How to read this tab", expanded=False):
        st.markdown(
            "Microstructure features extract information from *how* assets trade, not just *where* prices go.\n\n"
            "**Amihud Illiquidity** (top-left)\n"
            "Measures price impact per dollar traded: |return| / dollar volume. "
            "Higher values = less liquid = larger price impact per trade. "
            "Spikes indicate liquidity crises or market stress. "
            "Useful as a risk factor — illiquid assets earn a premium but crash harder.\n\n"
            "**VPIN — Volume-Synchronized Probability of Informed Trading** (top-right)\n"
            "Estimates the fraction of volume from informed traders (insiders, algorithms with private information). "
            "Uses buy/sell volume imbalance over rolling windows. "
            "VPIN > 0.5 signals elevated toxicity — market makers widen spreads, liquidity dries up. "
            "VPIN spiked before the 2010 Flash Crash.\n\n"
            "**Kyle's Lambda** (bottom-left)\n"
            "From Kyle (1985) — measures permanent price impact per unit of order flow. "
            "High lambda = each trade moves the price more = less liquid market. "
            "Computed as the regression slope of |price change| on volume.\n\n"
            "**Volume Clock** (bottom-right)\n"
            "Instead of sampling at fixed time intervals (daily bars), volume bars sample after "
            "a fixed amount of volume has traded. This produces more bars during active periods "
            "and fewer during quiet ones — better for ML models. "
            "Green dots show where volume bars would fall on the price chart.\n\n"
            "**Reference:** Lopez de Prado, AFML Ch. 19; Kyle (1985); Easley et al. (2012) for VPIN."
        )

    if volume.empty or volume.isna().all():
        st.warning(f"No volume data available for {ticker}. Microstructure analysis requires volume.")
    else:
        vol = volume.reindex(close.index).fillna(0)
        ret = log_returns.reindex(close.index).fillna(0)

        # 1. Amihud Illiquidity (|return| / dollar volume)
        dollar_vol = vol * close
        amihud = (ret.abs() / dollar_vol.replace(0, np.nan)).dropna()
        amihud_20 = amihud.rolling(20).mean()

        # 2. Kyle's Lambda (price impact per unit volume)
        # Regress |delta_price| on volume over rolling window
        kyle_lambda = []
        for i in range(60, len(close)):
            window_ret = ret.iloc[i - 60:i].values
            window_vol = vol.iloc[i - 60:i].values
            if window_vol.std() > 0:
                beta = np.cov(np.abs(window_ret), window_vol)[0, 1] / np.var(window_vol)
                kyle_lambda.append({"date": close.index[i], "lambda": abs(beta)})
        kyle_df = pd.DataFrame(kyle_lambda).set_index("date") if kyle_lambda else pd.DataFrame()

        # 3. VPIN (Volume-Synchronized Probability of Informed Trading)
        # Simplified: use tick rule to classify buys/sells
        tick_sign = np.sign(ret).replace(0, np.nan).ffill().fillna(1)
        buy_vol = vol * (tick_sign == 1).astype(float)
        sell_vol = vol * (tick_sign == -1).astype(float)
        # Rolling VPIN
        vpin_window = 50
        vpin = (buy_vol.rolling(vpin_window).sum() - sell_vol.rolling(vpin_window).sum()).abs() / \
               vol.rolling(vpin_window).sum().replace(0, np.nan)
        vpin = vpin.dropna()

        # 4. Volume Clock (bars per unit volume vs per unit time)
        cum_vol = vol.cumsum()
        total_vol = cum_vol.iloc[-1]
        n_vol_bars = 50
        vol_per_bar = total_vol / n_vol_bars
        vol_bar_dates = []
        threshold = vol_per_bar
        for i in range(len(cum_vol)):
            if cum_vol.iloc[i] >= threshold:
                vol_bar_dates.append(cum_vol.index[i])
                threshold += vol_per_bar

        # Charts
        mic_c1, mic_c2 = st.columns(2)
        with mic_c1:
            fig_amihud = go.Figure()
            fig_amihud.add_trace(go.Scatter(x=amihud_20.index, y=amihud_20 * 1e6, mode="lines",
                                            line=dict(color="#00d1ff", width=2), name="Amihud (20D)"))
            fig_amihud.update_layout(template="plotly_dark", height=300,
                                     title="Amihud Illiquidity (x10^6)", yaxis_title="Illiquidity",
                                     margin=dict(l=0, r=0, t=40, b=0))
            st.plotly_chart(fig_amihud, use_container_width=True, config=PLOTLY_NOBAR)

        with mic_c2:
            if not vpin.empty:
                fig_vpin = go.Figure()
                fig_vpin.add_trace(go.Scatter(x=vpin.index, y=vpin, mode="lines",
                                              line=dict(color="#ff6b6b", width=2), name="VPIN"))
                fig_vpin.add_hline(y=0.5, line_dash="dash", line_color="#ffaa00",
                                   annotation_text="Elevated toxicity")
                fig_vpin.update_layout(template="plotly_dark", height=300,
                                       title="VPIN (Flow Toxicity)", yaxis_title="VPIN (0-1)",
                                       yaxis=dict(range=[0, 1.05]),
                                       margin=dict(l=0, r=0, t=40, b=0))
                st.plotly_chart(fig_vpin, use_container_width=True, config=PLOTLY_NOBAR)

        mic_c3, mic_c4 = st.columns(2)
        with mic_c3:
            if not kyle_df.empty:
                fig_kyle = go.Figure()
                fig_kyle.add_trace(go.Scatter(x=kyle_df.index, y=kyle_df["lambda"] * 1e6, mode="lines",
                                              line=dict(color="#ffaa00", width=2), name="Kyle's Lambda"))
                fig_kyle.update_layout(template="plotly_dark", height=300,
                                       title="Kyle's Lambda (Price Impact, x10^6)", yaxis_title="Lambda",
                                       margin=dict(l=0, r=0, t=40, b=0))
                st.plotly_chart(fig_kyle, use_container_width=True, config=PLOTLY_NOBAR)

        with mic_c4:
            # Volume clock vs time clock
            fig_clock = go.Figure()
            fig_clock.add_trace(go.Scatter(x=close.index, y=close, mode="lines",
                                           line=dict(color="#555", width=1), name="Time bars"))
            if vol_bar_dates:
                vol_prices = close.reindex(vol_bar_dates, method="nearest")
                fig_clock.add_trace(go.Scatter(
                    x=vol_prices.index, y=vol_prices, mode="markers",
                    marker=dict(color="#00ff88", size=5), name=f"Volume bars ({len(vol_bar_dates)})",
                ))
            fig_clock.update_layout(template="plotly_dark", height=300,
                                    title="Volume Clock vs Time Clock",
                                    margin=dict(l=0, r=0, t=40, b=0))
            st.plotly_chart(fig_clock, use_container_width=True, config=PLOTLY_NOBAR)

        # ── Dollar Bars + Tick Imbalance Bars ──
        st.markdown("---")
        st.subheader("Alternative Bar Types")
        st.caption("Time bars (daily) generate one observation per calendar day regardless of activity. "
                   "Volume bars trigger after a fixed volume trades — more bars on active days, fewer on quiet ones. "
                   "Dollar bars trigger after a fixed dollar amount trades — normalizes for price level changes. "
                   "The return distribution chart on the right shows that alternative bars tend to produce "
                   "more normally distributed returns (better for ML models that assume Gaussian inputs).")

        # Dollar bars
        cum_dollar = (vol * close).cumsum()
        total_dollar = cum_dollar.iloc[-1]
        n_dollar_bars = 50
        dollar_per_bar = total_dollar / n_dollar_bars
        dollar_bar_dates = []
        d_threshold = dollar_per_bar
        for i in range(len(cum_dollar)):
            if cum_dollar.iloc[i] >= d_threshold:
                dollar_bar_dates.append(cum_dollar.index[i])
                d_threshold += dollar_per_bar

        bar_c1, bar_c2 = st.columns(2)
        with bar_c1:
            fig_bars = go.Figure()
            fig_bars.add_trace(go.Scatter(x=close.index, y=close, mode="lines",
                                          line=dict(color="#555", width=1), name="Time bars"))
            if vol_bar_dates:
                vol_prices = close.reindex(vol_bar_dates, method="nearest")
                fig_bars.add_trace(go.Scatter(x=vol_prices.index, y=vol_prices, mode="markers",
                                              marker=dict(color="#00ff88", size=4), name=f"Volume ({len(vol_bar_dates)})"))
            if dollar_bar_dates:
                dollar_prices = close.reindex(dollar_bar_dates, method="nearest")
                fig_bars.add_trace(go.Scatter(x=dollar_prices.index, y=dollar_prices, mode="markers",
                                              marker=dict(color="#ffaa00", size=4, symbol="diamond"),
                                              name=f"Dollar ({len(dollar_bar_dates)})"))
            fig_bars.update_layout(template="plotly_dark", height=320,
                                   title="Bar Type Comparison on Price Chart",
                                   margin=dict(l=0, r=0, t=40, b=0))
            st.plotly_chart(fig_bars, use_container_width=True, config=PLOTLY_NOBAR)

        with bar_c2:
            # Return distribution comparison
            fig_ret_dist = go.Figure()
            # Time bar returns
            time_rets = log_returns.values
            fig_ret_dist.add_trace(go.Histogram(x=time_rets, name=f"Time ({len(time_rets)})", marker_color="#555",
                                                opacity=0.5, nbinsx=50))
            # Volume bar returns
            if len(vol_bar_dates) > 5:
                vb_prices = close.reindex(vol_bar_dates, method="nearest")
                vb_rets = np.log(vb_prices / vb_prices.shift(1)).dropna().values
                fig_ret_dist.add_trace(go.Histogram(x=vb_rets, name=f"Volume ({len(vb_rets)})", marker_color="#00ff88",
                                                    opacity=0.5, nbinsx=50))
            fig_ret_dist.update_layout(template="plotly_dark", height=320, barmode="overlay",
                                       title="Return Distribution by Bar Type",
                                       xaxis_title="Log Return", yaxis_title="Count",
                                       margin=dict(l=0, r=0, t=40, b=0))
            st.plotly_chart(fig_ret_dist, use_container_width=True, config=PLOTLY_NOBAR)

        # ── Corwin-Schultz Spread Estimator ──
        if "High" in df.columns and "Low" in df.columns:
            st.markdown("---")
            st.subheader("Corwin-Schultz Spread Estimator")
            st.caption("Uses the relationship between daily high-low ranges over consecutive days to back out "
                       "the effective bid-ask spread. The key insight: the high-low range reflects both volatility and "
                       "the spread. By comparing 1-day and 2-day ranges, volatility cancels out and the spread remains. "
                       "Rising spread = deteriorating liquidity = wider trading costs = higher market-maker risk perception.")

            high = df["High"]
            low = df["Low"]
            # Corwin-Schultz (2012): spread from 2-day high-low range
            beta = (np.log(high / low)) ** 2
            beta_sum = beta.rolling(2).sum()
            gamma = (np.log(high.rolling(2).max() / low.rolling(2).min())) ** 2
            alpha = (np.sqrt(2 * beta_sum) - np.sqrt(beta_sum)) / (3 - 2 * np.sqrt(2)) - np.sqrt(gamma / (3 - 2 * np.sqrt(2)))
            cs_spread = (2 * (np.exp(alpha) - 1) / (1 + np.exp(alpha))).clip(lower=0)
            cs_spread_20 = cs_spread.rolling(20).mean()

            fig_cs = go.Figure()
            fig_cs.add_trace(go.Scatter(x=cs_spread_20.index, y=cs_spread_20 * 100, mode="lines",
                                        line=dict(color="#ff6b6b", width=2), name="CS Spread (20D, %)"))
            fig_cs.update_layout(template="plotly_dark", height=280,
                                 title="Corwin-Schultz Effective Spread (%)",
                                 yaxis_title="Spread (%)",
                                 margin=dict(l=0, r=0, t=40, b=0))
            st.plotly_chart(fig_cs, use_container_width=True, config=PLOTLY_NOBAR)

            latest_cs = cs_spread_20.iloc[-1] * 100 if not cs_spread_20.dropna().empty else 0
            st.metric("Current Spread (20D avg)", f"{latest_cs:.3f}%",
                      help="Estimated effective bid-ask spread from OHLC data")

        # Summary table
        st.subheader("Microstructure Summary")
        st.caption("Current snapshot of all microstructure indicators. "
                   "High Amihud + high VPIN + high Lambda together signal a dangerous liquidity environment — "
                   "the cost of trading is high and informed traders are active.")
        latest_amihud = amihud_20.iloc[-1] * 1e6 if not amihud_20.empty else 0
        latest_vpin = vpin.iloc[-1] if not vpin.empty else 0
        latest_kyle = kyle_df["lambda"].iloc[-1] * 1e6 if not kyle_df.empty else 0
        sm1, sm2, sm3, sm4 = st.columns(4)
        sm1.metric("Amihud (x10^6)", f"{latest_amihud:.2f}", help="Higher = less liquid")
        sm2.metric("VPIN", f"{latest_vpin:.3f}", help="Higher = more informed trading")
        sm3.metric("Kyle's Lambda (x10^6)", f"{latest_kyle:.2f}", help="Higher = more price impact per trade")
        sm4.metric("Vol/Dollar Bars", f"{len(vol_bar_dates)} / {len(dollar_bar_dates)}")


# ═══════════════════════════════════════════════
# TAB 8: ENTROPY
# ═══════════════════════════════════════════════
with tab_entropy, error_boundary("Entropy"):
    st.subheader("Information Content & Entropy")
    st.caption("AFML Ch. 18 — Measuring predictability and market efficiency.")

    with st.expander("How to read this tab", expanded=False):
        st.markdown(
            "Entropy quantifies how much *information* is in a return series. "
            "High entropy = random = efficient market = no alpha. "
            "Low entropy = structured = patterns exist = potential alpha.\n\n"
            "**Shannon Entropy** (in bits)\n"
            "Discretizes returns into bins and measures the uniformity of the distribution. "
            "If all bins have equal probability → maximum entropy (random). "
            "If returns cluster in a few bins → low entropy (predictable). "
            "Reported in bits — maximum is log2(n_bins).\n\n"
            "**Normalized Entropy** (0 to 1)\n"
            "Shannon divided by maximum possible entropy. "
            "Makes it comparable across different bin counts. "
            "Values >0.95 = effectively random. Values <0.7 = meaningful structure.\n\n"
            "**Plugin Entropy** (bias-corrected)\n"
            "Shannon entropy is biased downward for small samples — it always looks more predictable "
            "than it really is. The Miller-Madow correction adds (m-1)/(2n·ln2) where m=bins, n=observations.\n\n"
            "**Lempel-Ziv Complexity** (0 to 1)\n"
            "Treats the return series as a binary string (up=1, down=0) and measures how compressible it is. "
            "Highly compressible (low LZ) = repetitive patterns = predictable. "
            "Incompressible (LZ near 1) = random. This captures sequential patterns that Shannon misses.\n\n"
            "**Rolling Entropy** shows how predictability changes over time. "
            "Periods of low rolling entropy may correspond to trending regimes where momentum strategies work.\n\n"
            "**Cross-Timeframe Comparison** — if entropy is lower at weekly vs daily, "
            "patterns are stronger at that horizon. This guides strategy timeframe selection.\n\n"
            "**Reference:** Lopez de Prado, AFML Ch. 18; Kontoyiannis et al. (1998) for Lempel-Ziv."
        )

    # Discretize returns into bins
    n_bins = st.slider("Discretization bins", 3, 20, 10, key="ql_entropy_bins")
    ret_clean = log_returns.dropna()
    bins = pd.qcut(ret_clean, n_bins, labels=False, duplicates="drop")

    # 1. Shannon Entropy
    probs = bins.value_counts(normalize=True).sort_index()
    shannon = -np.sum(probs * np.log2(probs.replace(0, 1)))
    max_entropy = np.log2(n_bins)
    normalized_shannon = shannon / max_entropy if max_entropy > 0 else 0

    # 2. Plug-in Entropy (with bias correction)
    n_obs = len(bins)
    bias_correction = (n_bins - 1) / (2 * n_obs * np.log(2))
    plugin_entropy = shannon + bias_correction

    # 3. Lempel-Ziv Complexity
    def _lempel_ziv(sequence: np.ndarray) -> float:
        """Compute normalized Lempel-Ziv complexity."""
        s = "".join(str(int(x)) for x in sequence)
        n = len(s)
        if n == 0:
            return 0
        words = set()
        w = ""
        complexity = 0
        for c in s:
            wc = w + c
            if wc not in words:
                words.add(wc)
                complexity += 1
                w = ""
            else:
                w = wc
        if w:
            complexity += 1
        # Normalize by theoretical maximum
        max_c = n / np.log2(max(n, 2))
        return complexity / max_c if max_c > 0 else 0

    # Binary encoding for LZ: up=1, down=0
    binary_returns = (ret_clean > 0).astype(int).values
    lz_complexity = _lempel_ziv(binary_returns)

    # Metrics
    em1, em2, em3, em4 = st.columns(4)
    em1.metric("Shannon Entropy", f"{shannon:.3f} bits", help=f"Max possible: {max_entropy:.3f}")
    em2.metric("Normalized Entropy", f"{normalized_shannon:.3f}", help="0 = perfectly predictable, 1 = maximum randomness")
    em3.metric("Plugin Entropy", f"{plugin_entropy:.3f} bits", help="Bias-corrected (Miller-Madow)")
    em4.metric("Lempel-Ziv Complexity", f"{lz_complexity:.3f}", help="0 = simple pattern, 1 = incompressible (random)")

    # Interpretation
    if normalized_shannon > 0.95:
        st.info("Entropy is very high — this series is close to a random walk. EMH holds for this timeframe.")
    elif normalized_shannon > 0.85:
        st.info("Entropy is high but not maximal — some weak structure may exist.")
    elif normalized_shannon > 0.7:
        st.success("Moderate entropy — meaningful patterns may be extractable with ML.")
    else:
        st.success("Low entropy — significant predictability detected. Investigate for alpha signals.")

    # Distribution chart
    ec1, ec2 = st.columns(2)
    with ec1:
        fig_hist = go.Figure()
        fig_hist.add_trace(go.Bar(x=probs.index.astype(str), y=probs.values,
                                  marker_color="#00d1ff", name="Observed"))
        fig_hist.add_hline(y=1/n_bins, line_dash="dash", line_color="#ffaa00",
                           annotation_text="Uniform (max entropy)")
        fig_hist.update_layout(template="plotly_dark", height=300,
                               title="Return Bin Distribution",
                               xaxis_title="Bin", yaxis_title="Probability",
                               margin=dict(l=0, r=0, t=40, b=0))
        st.plotly_chart(fig_hist, use_container_width=True, config=PLOTLY_NOBAR)

    with ec2:
        # Rolling entropy
        window_ent = 63
        rolling_ent = []
        for i in range(window_ent, len(ret_clean)):
            window_data = ret_clean.iloc[i - window_ent:i]
            w_bins = pd.qcut(window_data, min(n_bins, len(window_data.unique())), labels=False, duplicates="drop")
            w_probs = w_bins.value_counts(normalize=True)
            h = -np.sum(w_probs * np.log2(w_probs.replace(0, 1)))
            rolling_ent.append({"date": ret_clean.index[i], "entropy": h / np.log2(max(len(w_probs), 2))})

        if rolling_ent:
            rent_df = pd.DataFrame(rolling_ent)
            fig_rent = go.Figure()
            fig_rent.add_trace(go.Scatter(x=rent_df["date"], y=rent_df["entropy"], mode="lines",
                                          line=dict(color="#00d1ff", width=2), name="Rolling Entropy"))
            fig_rent.add_hline(y=1.0, line_dash="dash", line_color="#555", annotation_text="Max (random)")
            fig_rent.add_hline(y=0.85, line_dash="dash", line_color="#ffaa00", annotation_text="Threshold")
            fig_rent.update_layout(template="plotly_dark", height=300,
                                   title=f"Rolling Normalized Entropy ({window_ent}D)",
                                   yaxis_title="Entropy (0-1)", yaxis=dict(range=[0.5, 1.05]),
                                   margin=dict(l=0, r=0, t=40, b=0))
            st.plotly_chart(fig_rent, use_container_width=True, config=PLOTLY_NOBAR)

    # Entropy comparison across timeframes
    st.subheader("Entropy Across Timeframes")
    st.caption("Compares normalized entropy at daily, weekly, and monthly horizons. "
               "If weekly entropy is lower than daily, weekly patterns are stronger — "
               "consider weekly-frequency strategies. If entropy decreases with horizon, "
               "the market mean-reverts at longer scales. If it increases, momentum dominates.")

    timeframes = {"Daily": ret_clean, "Weekly": ret_clean.resample("W").sum().dropna(),
                  "Monthly": ret_clean.resample("ME").sum().dropna()}
    tf_results = []
    for tf_name, tf_data in timeframes.items():
        if len(tf_data) < 30:
            continue
        tf_bins = pd.qcut(tf_data, min(n_bins, len(tf_data.unique())), labels=False, duplicates="drop")
        tf_probs = tf_bins.value_counts(normalize=True)
        tf_h = -np.sum(tf_probs * np.log2(tf_probs.replace(0, 1)))
        tf_max = np.log2(max(len(tf_probs), 2))
        tf_results.append({
            "Timeframe": tf_name, "Observations": len(tf_data),
            "Shannon": f"{tf_h:.3f}", "Normalized": f"{tf_h / tf_max:.3f}" if tf_max > 0 else "N/A",
        })
    if tf_results:
        st.dataframe(pd.DataFrame(tf_results), use_container_width=True, hide_index=True)

    # ── Conditional Entropy ──
    st.markdown("---")
    st.subheader("Conditional Entropy & Markov Structure")
    st.caption("Unconditional entropy H(X) measures randomness of returns in isolation. "
               "Conditional entropy H(X|X_{t-1}) measures randomness *given* yesterday's return. "
               "The difference is mutual information — how many bits of predictive power yesterday carries. "
               "The transition matrix shows P(today's bin | yesterday's bin). "
               "If the matrix is uniform (all rows identical), past returns are useless. "
               "If certain rows concentrate probability, there's exploitable Markov structure.")

    if len(ret_clean) > 100:
        # Discretize current and lagged returns
        n_states = min(n_bins, 5)  # fewer states for conditional to avoid sparse matrix
        current_bins = pd.qcut(ret_clean, n_states, labels=False, duplicates="drop")
        lagged_bins = current_bins.shift(1).dropna().astype(int)
        current_bins = current_bins.reindex(lagged_bins.index).astype(int)

        # Joint distribution
        joint = pd.crosstab(lagged_bins, current_bins, normalize=True)
        marginal = current_bins.value_counts(normalize=True).sort_index()

        # H(X) unconditional
        h_uncond = -np.sum(marginal * np.log2(marginal.replace(0, 1)))
        # H(X|Y) conditional
        h_cond = 0
        for lag_state in joint.index:
            p_lag = joint.loc[lag_state].sum()  # P(Y=y)
            if p_lag > 0:
                p_cond = joint.loc[lag_state] / p_lag  # P(X|Y=y)
                p_cond = p_cond[p_cond > 0]
                h_cond -= p_lag * np.sum(p_cond * np.log2(p_cond))

        # Mutual information
        mi = h_uncond - h_cond

        ce1, ce2, ce3 = st.columns(3)
        ce1.metric("H(X) Unconditional", f"{h_uncond:.3f} bits")
        ce2.metric("H(X|X_{t-1}) Conditional", f"{h_cond:.3f} bits")
        ce3.metric("Mutual Information", f"{mi:.4f} bits",
                   help="MI > 0 means past returns carry information about future returns. Higher = more predictable.")

        if mi > 0.01:
            st.success(f"Mutual information {mi:.4f} bits — past returns carry meaningful information. Markov structure exists.")
        else:
            st.info(f"Mutual information {mi:.4f} bits — weak serial dependence. Past returns add little predictive value.")

        # Transition matrix heatmap
        fig_trans = go.Figure(data=go.Heatmap(
            z=joint.values / joint.values.sum(axis=1, keepdims=True),  # normalize rows to conditional probs
            x=[f"Bin {i}" for i in joint.columns],
            y=[f"Lag {i}" for i in joint.index],
            colorscale=[[0, "#1a1a2e"], [1, "#00d1ff"]],
            text=[[f"{v:.2f}" for v in row] for row in (joint.values / joint.values.sum(axis=1, keepdims=True))],
            texttemplate="%{text}", textfont={"size": 11},
            colorbar=dict(title="P(X|Y)"),
        ))
        fig_trans.update_layout(template="plotly_dark", height=300,
                               title="Transition Matrix: P(Return Bin Today | Return Bin Yesterday)",
                               xaxis_title="Today's Bin", yaxis_title="Yesterday's Bin",
                               margin=dict(l=0, r=0, t=40, b=0))
        st.plotly_chart(fig_trans, use_container_width=True, config=PLOTLY_NOBAR)

    # ── Transfer Entropy (Cross-Asset) ──
    st.markdown("---")
    st.subheader("Transfer Entropy (Cross-Asset Information Flow)")
    st.caption("Transfer entropy measures how much the *past* of one asset reduces uncertainty about the *future* of another. "
               "Unlike correlation (which is symmetric), transfer entropy is directional — "
               "it answers 'does SPY lead TLT, or does TLT lead SPY?' "
               "Net positive flow means the primary ticker leads the comparison asset. "
               "This is useful for lead-lag strategies and understanding information transmission across markets.")

    te_ticker = st.text_input("Compare with", value="TLT", key="ql_te_ticker").strip().upper()
    if te_ticker and te_ticker != raw_ticker.strip().upper():
        with st.spinner(f"Loading {te_ticker}..."):
            te_prices = fetch_price_history([te_ticker], period="2y" if lookback_days <= 504 else "3y")

        if not te_prices.empty and te_ticker in te_prices.columns:
            te_returns = np.log(te_prices[te_ticker] / te_prices[te_ticker].shift(1)).dropna()
            common = ret_clean.index.intersection(te_returns.index)
            if len(common) > 100:
                x = ret_clean.loc[common].values
                y = te_returns.loc[common].values
                n_te_bins = 5

                x_bins = pd.qcut(pd.Series(x), n_te_bins, labels=False, duplicates="drop").values
                y_bins = pd.qcut(pd.Series(y), n_te_bins, labels=False, duplicates="drop").values

                def _transfer_entropy(source, target, lag=1):
                    """TE: information transferred from source to target."""
                    n = len(target)
                    src_lag = source[:-lag]
                    tgt_lag = target[:-lag]
                    tgt_now = target[lag:]
                    # H(tgt_now | tgt_lag) - H(tgt_now | tgt_lag, src_lag)
                    # Use joint frequency tables
                    joint_3 = {}  # (tgt_lag, src_lag, tgt_now) -> count
                    joint_2t = {}  # (tgt_lag, tgt_now) -> count
                    joint_2s = {}  # (tgt_lag, src_lag) -> count
                    marg_1 = {}   # tgt_lag -> count
                    for i in range(len(tgt_now)):
                        k3 = (int(tgt_lag[i]), int(src_lag[i]), int(tgt_now[i]))
                        k2t = (int(tgt_lag[i]), int(tgt_now[i]))
                        k2s = (int(tgt_lag[i]), int(src_lag[i]))
                        k1 = int(tgt_lag[i])
                        joint_3[k3] = joint_3.get(k3, 0) + 1
                        joint_2t[k2t] = joint_2t.get(k2t, 0) + 1
                        joint_2s[k2s] = joint_2s.get(k2s, 0) + 1
                        marg_1[k1] = marg_1.get(k1, 0) + 1
                    total = len(tgt_now)
                    te = 0
                    for k3, c3 in joint_3.items():
                        tl, sl, tn = k3
                        p3 = c3 / total
                        p2t = joint_2t.get((tl, tn), 1) / total
                        p2s = joint_2s.get((tl, sl), 1) / total
                        p1 = marg_1.get(tl, 1) / total
                        if p3 > 0 and p2s > 0 and p2t > 0 and p1 > 0:
                            te += p3 * np.log2((p3 * p1) / (p2t * p2s))
                    return max(0, te)

                te_xy = _transfer_entropy(x_bins, y_bins, lag=1)  # ticker -> te_ticker
                te_yx = _transfer_entropy(y_bins, x_bins, lag=1)  # te_ticker -> ticker
                net_te = te_xy - te_yx

                te1, te2, te3 = st.columns(3)
                te1.metric(f"TE: {raw_ticker.upper()} → {te_ticker}", f"{te_xy:.4f} bits",
                           help=f"Information flowing from {raw_ticker.upper()} to {te_ticker}")
                te2.metric(f"TE: {te_ticker} → {raw_ticker.upper()}", f"{te_yx:.4f} bits",
                           help=f"Information flowing from {te_ticker} to {raw_ticker.upper()}")
                te3.metric("Net Flow", f"{net_te:+.4f} bits",
                           help="Positive = ticker leads, Negative = comparison asset leads")

                if abs(net_te) > 0.005:
                    leader = raw_ticker.upper() if net_te > 0 else te_ticker
                    follower = te_ticker if net_te > 0 else raw_ticker.upper()
                    st.info(f"**{leader}** leads **{follower}** — {leader}'s past returns carry {abs(net_te):.4f} bits "
                            f"of information about {follower}'s future returns.")
                else:
                    st.info("No significant directional information flow between these assets.")

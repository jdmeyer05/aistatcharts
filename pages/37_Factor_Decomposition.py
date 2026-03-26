"""
Factor Decomposition — Fama-French + Momentum + Macro Factor Analysis

Decomposes any portfolio or stock into systematic factor exposures using
Fama-French 5 factors (Mkt-RF, SMB, HML, RMW, CMA) + Momentum.

Tabs:
1. Factor Returns — historical factor performance, correlations, regime analysis
2. Factor Exposure — regression betas for your portfolio/stock
3. Alpha Attribution — how much return is explained vs unexplained (alpha)
4. Factor Timing — rolling exposures, style drift detection
5. Risk Decomposition — what % of risk comes from each factor
"""
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import logging
import io
import zipfile
from src.layout import setup_page, error_boundary
from src.data_engine import polygon_history, format_massive_ticker
from src.market_data import fetch_energy_price_history as fetch_price_history
from src.styles import COLORS

logger = logging.getLogger(__name__)
setup_page("37_Factor_Decomposition")

st.title("Factor Decomposition")
st.markdown("Fama-French 5-factor + momentum decomposition. Identify systematic exposures, measure alpha, detect style drift.")

PLOTLY_NOBAR = {"displayModeBar": False}

FACTOR_COLORS = {
    "Mkt-RF": "#00d1ff", "SMB": "#00ff88", "HML": "#ffaa00",
    "RMW": "#ff6b6b", "CMA": "#ff00ff", "Mom": "#88ccff",
}
FACTOR_NAMES = {
    "Mkt-RF": "Market (Mkt-RF)", "SMB": "Size (SMB)", "HML": "Value (HML)",
    "RMW": "Profitability (RMW)", "CMA": "Investment (CMA)", "Mom": "Momentum (Mom)",
}


# ═══════════════════════════════════════════════
# DATA: FAMA-FRENCH FACTORS
# ═══════════════════════════════════════════════

@st.cache_data(ttl=86400, show_spinner=False)
def _load_ff_factors() -> pd.DataFrame:
    """Load Fama-French 5 factors + Momentum (daily) from Ken French data library."""
    import requests

    factors = pd.DataFrame()

    # FF5 factors
    try:
        url = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_Research_Data_5_Factors_2x3_daily_CSV.zip"
        r = requests.get(url, timeout=20)
        z = zipfile.ZipFile(io.BytesIO(r.content))
        raw = z.read(z.namelist()[0]).decode("utf-8")
        lines = raw.split("\n")
        start = next(i for i, l in enumerate(lines) if l.strip() and l.strip()[0].isdigit())
        header = [h.strip() for h in lines[start - 1].split(",")]
        data_lines = [l for l in lines[start:] if l.strip() and l.strip()[0].isdigit() and len(l.split(",")) >= 6]
        df = pd.DataFrame([l.split(",") for l in data_lines])
        df.columns = header[:len(df.columns)]
        df = df.rename(columns={"": "date"})
        df["date"] = pd.to_datetime(df["date"].str.strip(), format="%Y%m%d")
        for col in ["Mkt-RF", "SMB", "HML", "RMW", "CMA", "RF"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col].str.strip(), errors="coerce") / 100  # convert from % to decimal
        factors = df.set_index("date")
    except Exception as e:
        logger.error(f"FF5 factor load failed: {e}")
        return pd.DataFrame()

    # Momentum factor
    try:
        url_mom = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_Momentum_Factor_daily_CSV.zip"
        r = requests.get(url_mom, timeout=20)
        z = zipfile.ZipFile(io.BytesIO(r.content))
        raw = z.read(z.namelist()[0]).decode("utf-8")
        lines = raw.split("\n")
        start = next(i for i, l in enumerate(lines) if l.strip() and l.strip()[0].isdigit())
        data_lines = [l for l in lines[start:] if l.strip() and l.strip()[0].isdigit() and len(l.split(",")) >= 2]
        mom_df = pd.DataFrame([l.split(",") for l in data_lines], columns=["date", "Mom"])
        mom_df["date"] = pd.to_datetime(mom_df["date"].str.strip(), format="%Y%m%d")
        mom_df["Mom"] = pd.to_numeric(mom_df["Mom"].str.strip(), errors="coerce") / 100
        mom_df = mom_df.set_index("date")
        factors = factors.join(mom_df, how="left")
    except Exception as e:
        logger.warning(f"Momentum factor load failed: {e}")

    return factors


# ═══════════════════════════════════════════════
# CONTROLS
# ═══════════════════════════════════════════════

c1, c2, c3 = st.columns([2, 1, 1])
with c1:
    raw_tickers = st.text_input("Tickers (comma-separated, first = primary)",
                                value="SPY", key="fd_tickers")
with c2:
    fd_lookback = st.selectbox("Lookback", ["1Y", "2Y", "3Y", "5Y", "10Y"],
                                index=1, key="fd_lookback")
    lookback_map = {"1Y": "1y", "2Y": "2y", "3Y": "3y", "5Y": "5y", "10Y": "10y"}
with c3:
    st.markdown("<br>", unsafe_allow_html=True)
    fd_load = st.button("Run Decomposition", type="primary", use_container_width=True, key="fd_load")

if fd_load:
    st.session_state["fd_loaded"] = True
if not st.session_state.get("fd_loaded"):
    st.info("Enter tickers and click **Run Decomposition**.")
    st.stop()


# ═══════════════════════════════════════════════
# LOAD DATA
# ═══════════════════════════════════════════════

ticker_list = [t.strip().upper() for t in raw_tickers.split(",") if t.strip()]
primary_ticker = ticker_list[0]

with st.spinner("Loading factor data and price history..."):
    ff = _load_ff_factors()
    prices = fetch_price_history(ticker_list, period=lookback_map[fd_lookback])

if ff.empty:
    st.error("Failed to load Fama-French factor data.")
    st.stop()
if prices.empty:
    st.error("Failed to load price data.")
    st.stop()

# Compute portfolio returns
port_returns = prices.pct_change().dropna()
if len(ticker_list) == 1:
    asset_returns = port_returns[primary_ticker].rename("portfolio")
else:
    # Equal-weight portfolio
    asset_returns = port_returns.mean(axis=1).rename("portfolio")

# Align with factor data
factor_cols = [c for c in ["Mkt-RF", "SMB", "HML", "RMW", "CMA", "Mom"] if c in ff.columns]
rf_col = "RF" if "RF" in ff.columns else None
common_idx = asset_returns.index.intersection(ff.index)

if len(common_idx) < 60:
    st.error(f"Only {len(common_idx)} overlapping days between price data and factor data. Need at least 60.")
    st.stop()

Y = asset_returns.loc[common_idx]
X = ff.loc[common_idx, factor_cols]
rf = ff.loc[common_idx, rf_col] if rf_col else pd.Series(0, index=common_idx)
Y_excess = Y - rf  # excess returns over risk-free

n_obs = len(common_idx)

# Header metrics
hm1, hm2, hm3, hm4 = st.columns(4)
hm1.metric("Observations", n_obs)
hm2.metric("Date Range", f"{common_idx[0].strftime('%Y-%m-%d')} to {common_idx[-1].strftime('%Y-%m-%d')}")
hm3.metric("Ann. Return", f"{Y.mean() * 252 * 100:.1f}%")
hm4.metric("Ann. Vol", f"{Y.std() * np.sqrt(252) * 100:.1f}%")


# ═══════════════════════════════════════════════
# TABS
# ═══════════════════════════════════════════════

tab_returns, tab_exposure, tab_alpha, tab_timing, tab_risk = st.tabs([
    "Factor Returns",
    "Factor Exposure",
    "Alpha Attribution",
    "Factor Timing",
    "Risk Decomposition",
])


# ═══════════════════════════════════════════════
# TAB 1: FACTOR RETURNS
# ═══════════════════════════════════════════════
with tab_returns, error_boundary("Factor Returns"):
    st.subheader("Factor Return Analysis")

    with st.expander("How to read this tab", expanded=False):
        st.markdown(
            "**Fama-French factors** decompose equity returns into systematic risk premia:\n\n"
            "| Factor | Long | Short | Captures |\n"
            "|--------|------|-------|----------|\n"
            "| **Mkt-RF** | Market | T-bills | Equity risk premium |\n"
            "| **SMB** | Small caps | Large caps | Size premium |\n"
            "| **HML** | High B/M (value) | Low B/M (growth) | Value premium |\n"
            "| **RMW** | High profit | Low profit | Profitability premium |\n"
            "| **CMA** | Low invest | High invest | Investment premium |\n"
            "| **Mom** | Winners | Losers | Momentum premium |\n\n"
            "Each factor is a long-short portfolio. Positive return = the long side outperformed.\n\n"
            "**Cumulative chart** shows which factors have been rewarded over your lookback period. "
            "Flat or declining lines mean that factor premium was absent or negative.\n\n"
            "**Correlation matrix** shows how factors co-move. Low correlation = better diversification. "
            "HML and CMA are often highly correlated (value and investment overlap)."
        )

    # Cumulative factor returns
    cum_factors = (1 + X).cumprod() * 100
    fig_cum = go.Figure()
    for col in factor_cols:
        fig_cum.add_trace(go.Scatter(
            x=cum_factors.index, y=cum_factors[col], mode="lines",
            name=FACTOR_NAMES.get(col, col),
            line=dict(color=FACTOR_COLORS.get(col, "#888"), width=2),
        ))
    fig_cum.add_hline(y=100, line_dash="dash", line_color="#333")
    fig_cum.update_layout(template="plotly_dark", height=420,
                          title="Cumulative Factor Returns (base=100)",
                          yaxis_title="Indexed",
                          legend=dict(orientation="h", y=-0.12),
                          margin=dict(l=0, r=0, t=40, b=0))
    st.plotly_chart(fig_cum, use_container_width=True, config=PLOTLY_NOBAR)

    # Factor statistics table
    st.subheader("Factor Statistics")
    st.caption("Annualized return, volatility, Sharpe ratio, and max drawdown for each factor.")
    stats = []
    for col in factor_cols:
        f = X[col].dropna()
        ann_ret = f.mean() * 252 * 100
        ann_vol = f.std() * np.sqrt(252) * 100
        sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
        cum = (1 + f).cumprod()
        dd = ((cum / cum.cummax()) - 1).min() * 100
        stats.append({
            "Factor": FACTOR_NAMES.get(col, col),
            "Ann. Return": f"{ann_ret:.1f}%",
            "Ann. Vol": f"{ann_vol:.1f}%",
            "Sharpe": f"{sharpe:.2f}",
            "Max DD": f"{dd:.1f}%",
            "t-stat": f"{f.mean() / (f.std() / np.sqrt(len(f))):.2f}" if f.std() > 0 else "N/A",
        })
    st.dataframe(pd.DataFrame(stats), use_container_width=True, hide_index=True)

    # Factor correlation matrix
    st.subheader("Factor Correlations")
    st.caption("Low inter-factor correlation means each factor captures a distinct risk premium. "
               "HML-CMA correlation is typically high (value and investment are related).")
    fcorr = X.corr()
    fig_fc = go.Figure(data=go.Heatmap(
        z=fcorr.values, x=[FACTOR_NAMES.get(c, c) for c in fcorr.columns],
        y=[FACTOR_NAMES.get(c, c) for c in fcorr.index],
        colorscale=[[0, "#ff4444"], [0.5, "#1a1a2e"], [1, "#00d1ff"]],
        zmid=0, zmin=-1, zmax=1,
        text=[[f"{v:.2f}" for v in row] for row in fcorr.values],
        texttemplate="%{text}", textfont={"size": 11},
        colorbar=dict(title="Corr"),
    ))
    fig_fc.update_layout(template="plotly_dark", height=350,
                          margin=dict(l=0, r=0, t=10, b=0))
    st.plotly_chart(fig_fc, use_container_width=True, config=PLOTLY_NOBAR)


# ═══════════════════════════════════════════════
# TAB 2: FACTOR EXPOSURE
# ═══════════════════════════════════════════════
with tab_exposure, error_boundary("Factor Exposure"):
    st.subheader("Factor Exposure (Regression Betas)")

    with st.expander("How to read this tab", expanded=False):
        st.markdown(
            "Factor betas measure how sensitive your portfolio is to each systematic factor.\n\n"
            "**Beta = 1.0** on Mkt-RF means your portfolio moves 1:1 with the market. "
            "**Beta = 0.3** on SMB means a 1% rise in small-cap premium adds 0.3% to your portfolio.\n\n"
            "**Alpha (intercept)** is the return unexplained by any factor — your true 'skill' return. "
            "A positive, statistically significant alpha (t > 2) is the holy grail.\n\n"
            "**R-squared** shows what fraction of your portfolio's variance is explained by the factors. "
            "R² > 0.9 = you're basically a factor portfolio. R² < 0.5 = significant idiosyncratic risk.\n\n"
            "**Residual analysis** at the bottom checks whether the regression assumptions hold."
        )

    # Full-period OLS regression: R_excess = alpha + sum(beta_i * factor_i) + epsilon
    X_reg = X.values
    X_with_const = np.column_stack([np.ones(len(X_reg)), X_reg])
    y_reg = Y_excess.values

    coeffs, _, rank, _ = np.linalg.lstsq(X_with_const, y_reg, rcond=None)
    if rank < X_with_const.shape[1]:
        st.warning(f"Multicollinearity detected (rank {rank} < {X_with_const.shape[1]} factors). Some betas may be unstable.")
    y_pred = X_with_const @ coeffs
    residuals = y_reg - y_pred
    ss_res = np.sum(residuals ** 2)
    ss_tot = np.sum((y_reg - y_reg.mean()) ** 2)
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0

    alpha_daily = coeffs[0]
    alpha_ann = alpha_daily * 252 * 100
    betas = {col: coeffs[i + 1] for i, col in enumerate(factor_cols)}

    # Standard errors
    n_params = len(coeffs)
    residual_var = ss_res / max(1, n_obs - n_params)
    try:
        cov_matrix = residual_var * np.linalg.inv(X_with_const.T @ X_with_const)
        se = np.sqrt(np.diag(cov_matrix))
        t_stats = coeffs / se
    except Exception:
        se = np.full(n_params, np.nan)
        t_stats = np.full(n_params, np.nan)

    # Metrics
    em1, em2, em3, em4 = st.columns(4)
    em1.metric("Alpha (ann.)", f"{alpha_ann:+.2f}%",
               help=f"t-stat: {t_stats[0]:.2f}" if not np.isnan(t_stats[0]) else "")
    em2.metric("R-squared", f"{r2:.3f}")
    em3.metric("Mkt Beta", f"{betas.get('Mkt-RF', 0):.3f}")
    alpha_sig = abs(t_stats[0]) > 2 if not np.isnan(t_stats[0]) else False
    em4.metric("Alpha Significant?", "Yes (t > 2)" if alpha_sig else "No",
               delta=f"t = {t_stats[0]:.2f}" if not np.isnan(t_stats[0]) else "")

    # Beta bar chart
    beta_df = pd.DataFrame({
        "Factor": [FACTOR_NAMES.get(c, c) for c in factor_cols],
        "Beta": [betas[c] for c in factor_cols],
        "t-stat": [t_stats[i + 1] for i in range(len(factor_cols))],
        "Significant": [abs(t_stats[i + 1]) > 2 for i in range(len(factor_cols))],
    })

    fig_beta = go.Figure()
    fig_beta.add_trace(go.Bar(
        x=beta_df["Factor"], y=beta_df["Beta"],
        marker_color=[FACTOR_COLORS.get(c, "#888") for c in factor_cols],
        text=[f"{b:.3f}" + (" *" if s else "") for b, s in zip(beta_df["Beta"], beta_df["Significant"])],
        textposition="outside",
    ))
    fig_beta.add_hline(y=0, line_dash="dash", line_color="#333")
    fig_beta.update_layout(template="plotly_dark", height=380,
                           title="Factor Betas (* = statistically significant at 5%)",
                           yaxis_title="Beta",
                           margin=dict(l=0, r=0, t=40, b=0))
    st.plotly_chart(fig_beta, use_container_width=True, config=PLOTLY_NOBAR)

    # Regression detail table
    st.subheader("Regression Detail")
    reg_table = [{"Term": "Alpha (daily)", "Coefficient": f"{alpha_daily:.6f}",
                  "Std Error": f"{se[0]:.6f}" if not np.isnan(se[0]) else "N/A",
                  "t-stat": f"{t_stats[0]:.2f}" if not np.isnan(t_stats[0]) else "N/A",
                  "Significant": "Yes" if abs(t_stats[0]) > 2 else "No" if not np.isnan(t_stats[0]) else "N/A"}]
    for i, col in enumerate(factor_cols):
        reg_table.append({
            "Term": FACTOR_NAMES.get(col, col),
            "Coefficient": f"{coeffs[i + 1]:.4f}",
            "Std Error": f"{se[i + 1]:.4f}" if not np.isnan(se[i + 1]) else "N/A",
            "t-stat": f"{t_stats[i + 1]:.2f}" if not np.isnan(t_stats[i + 1]) else "N/A",
            "Significant": "Yes" if abs(t_stats[i + 1]) > 2 else "No" if not np.isnan(t_stats[i + 1]) else "N/A",
        })
    st.dataframe(pd.DataFrame(reg_table), use_container_width=True, hide_index=True)

    # Residual analysis
    st.subheader("Residual Analysis")
    st.caption("Residuals should be mean-zero, normally distributed, and serially uncorrelated. "
               "Patterns in residuals suggest missing factors or model mis-specification.")
    res_c1, res_c2 = st.columns(2)
    with res_c1:
        fig_res = go.Figure()
        fig_res.add_trace(go.Scatter(x=common_idx, y=residuals, mode="lines",
                                     line=dict(color="#00d1ff", width=1), name="Residuals"))
        fig_res.add_hline(y=0, line_dash="dash", line_color="#555")
        fig_res.update_layout(template="plotly_dark", height=280,
                              title="Residuals Over Time", yaxis_title="Residual",
                              margin=dict(l=0, r=0, t=30, b=0))
        st.plotly_chart(fig_res, use_container_width=True, config=PLOTLY_NOBAR)
    with res_c2:
        fig_res_hist = go.Figure()
        fig_res_hist.add_trace(go.Histogram(x=residuals, nbinsx=50, marker_color="#00d1ff", name="Residuals"))
        fig_res_hist.update_layout(template="plotly_dark", height=280,
                                   title="Residual Distribution", xaxis_title="Residual",
                                   margin=dict(l=0, r=0, t=30, b=0))
        st.plotly_chart(fig_res_hist, use_container_width=True, config=PLOTLY_NOBAR)


# ═══════════════════════════════════════════════
# TAB 3: ALPHA ATTRIBUTION
# ═══════════════════════════════════════════════
with tab_alpha, error_boundary("Alpha Attribution"):
    st.subheader("Return Attribution")

    with st.expander("How to read this tab", expanded=False):
        st.markdown(
            "Decomposes your total return into:\n"
            "- **Factor contributions**: how much each factor added/subtracted (beta × factor return)\n"
            "- **Alpha**: the unexplained return (your edge, or luck)\n"
            "- **Risk-free**: the baseline return from T-bills\n\n"
            "If most of your return comes from Mkt-RF, you're paying active fees for passive exposure. "
            "Alpha should be positive and significant to justify active management."
        )

    # Attribution: contribution of each factor = beta * cumulative factor return
    factor_contributions = {}
    for col in factor_cols:
        factor_contributions[col] = betas[col] * X[col].sum() * 100

    alpha_contribution = alpha_daily * n_obs * 100
    rf_contribution = rf.sum() * 100
    total_return = Y.sum() * 100

    # Waterfall chart
    labels = ["Total Return"] + [FACTOR_NAMES.get(c, c) for c in factor_cols] + ["Alpha", "Risk-Free"]
    values = [total_return] + [factor_contributions[c] for c in factor_cols] + [alpha_contribution, rf_contribution]
    colors = ["#00d1ff"] + [FACTOR_COLORS.get(c, "#888") for c in factor_cols] + ["#00ff88", "#555"]

    fig_attr = go.Figure(go.Waterfall(
        orientation="v",
        measure=["total"] + ["relative"] * (len(factor_cols) + 2),
        x=labels, y=values,
        connector={"line": {"color": "#333"}},
        decreasing={"marker": {"color": "#ff4444"}},
        increasing={"marker": {"color": "#00d1ff"}},
        totals={"marker": {"color": "#00ff88"}},
        text=[f"{v:+.1f}%" for v in values],
        textposition="outside",
    ))
    fig_attr.update_layout(template="plotly_dark", height=420,
                           title="Return Attribution (Total Period)",
                           yaxis_title="Return (%)",
                           margin=dict(l=0, r=0, t=40, b=0))
    st.plotly_chart(fig_attr, use_container_width=True, config=PLOTLY_NOBAR)

    # Attribution table
    attr_data = []
    for col in factor_cols:
        pct_of_total = factor_contributions[col] / total_return * 100 if abs(total_return) > 0.01 else 0
        attr_data.append({
            "Source": FACTOR_NAMES.get(col, col),
            "Beta": f"{betas[col]:.3f}",
            "Factor Return": f"{X[col].sum() * 100:+.1f}%",
            "Contribution": f"{factor_contributions[col]:+.1f}%",
            "% of Total": f"{pct_of_total:.0f}%",
        })
    attr_data.append({"Source": "Alpha", "Beta": "—", "Factor Return": "—",
                      "Contribution": f"{alpha_contribution:+.1f}%",
                      "% of Total": f"{alpha_contribution / total_return * 100:.0f}%" if abs(total_return) > 0.01 else "N/A"})
    st.dataframe(pd.DataFrame(attr_data), use_container_width=True, hide_index=True)

    # Cumulative attribution over time
    st.subheader("Cumulative Attribution Over Time")
    st.caption("Shows how each factor's contribution accumulates. "
               "Widening gaps between the portfolio line and factor lines reveal when alpha was generated.")

    fig_cum_attr = go.Figure()
    cum_total = Y.cumsum() * 100
    fig_cum_attr.add_trace(go.Scatter(x=common_idx, y=cum_total, mode="lines",
                                      name="Total Return", line=dict(color="#fff", width=3)))
    cum_explained = pd.Series(0, index=common_idx, dtype=float)
    for col in factor_cols:
        contrib_ts = betas[col] * X[col].cumsum() * 100
        cum_explained += betas[col] * X[col] * 100
        fig_cum_attr.add_trace(go.Scatter(x=common_idx, y=contrib_ts, mode="lines",
                                          name=FACTOR_NAMES.get(col, col),
                                          line=dict(color=FACTOR_COLORS.get(col, "#888"), width=1)))
    # Alpha line
    alpha_cum = pd.Series(np.arange(1, n_obs + 1) * alpha_daily * 100, index=common_idx)
    fig_cum_attr.add_trace(go.Scatter(x=common_idx, y=alpha_cum, mode="lines",
                                      name="Alpha", line=dict(color="#00ff88", width=2, dash="dash")))
    fig_cum_attr.add_hline(y=0, line_dash="dash", line_color="#333")
    fig_cum_attr.update_layout(template="plotly_dark", height=420,
                               title="Cumulative Factor Contributions",
                               yaxis_title="Cumulative Return (%)",
                               legend=dict(orientation="h", y=-0.12),
                               margin=dict(l=0, r=0, t=40, b=0))
    st.plotly_chart(fig_cum_attr, use_container_width=True, config=PLOTLY_NOBAR)


# ═══════════════════════════════════════════════
# TAB 4: FACTOR TIMING (Rolling Betas)
# ═══════════════════════════════════════════════
with tab_timing, error_boundary("Factor Timing"):
    st.subheader("Rolling Factor Exposures")

    with st.expander("How to read this tab", expanded=False):
        st.markdown(
            "Factor exposures aren't constant — they drift as the portfolio changes or market regimes shift.\n\n"
            "**Rolling betas** show how your exposure to each factor evolves over time. "
            "Stable betas = consistent style. Volatile betas = style drift or tactical allocation.\n\n"
            "**Style drift** is problematic if you're paying for a 'value fund' that quietly became a 'growth fund.' "
            "The drift score measures how much betas changed from the first half to the second half of the period.\n\n"
            "**Rolling alpha** shows when your portfolio generated (or destroyed) alpha. "
            "Persistent positive alpha = skill. Mean-reverting alpha = luck."
        )

    roll_window = st.radio("Rolling window", ["63D (3M)", "126D (6M)", "252D (1Y)"],
                           horizontal=True, key="fd_roll_window")
    window_days = {"63D (3M)": 63, "126D (6M)": 126, "252D (1Y)": 252}[roll_window]

    if n_obs >= window_days + 30:
        # Rolling regression
        rolling_betas = {col: [] for col in factor_cols}
        rolling_alpha = []
        rolling_r2 = []
        rolling_dates = []

        for end in range(window_days, n_obs):
            start = end - window_days
            y_w = Y_excess.iloc[start:end].values
            X_w = X.iloc[start:end].values
            X_w_c = np.column_stack([np.ones(len(X_w)), X_w])
            try:
                c, _, _, _ = np.linalg.lstsq(X_w_c, y_w, rcond=None)
                y_p = X_w_c @ c
                ss_r = np.sum((y_w - y_p) ** 2)
                ss_t = np.sum((y_w - y_w.mean()) ** 2)
                for i, col in enumerate(factor_cols):
                    rolling_betas[col].append(c[i + 1])
                rolling_alpha.append(c[0] * 252 * 100)  # annualized
                rolling_r2.append(1 - ss_r / ss_t if ss_t > 0 else 0)
                rolling_dates.append(common_idx[end])
            except Exception:
                pass

        if rolling_dates:
            # Rolling betas chart
            fig_rb = go.Figure()
            for col in factor_cols:
                fig_rb.add_trace(go.Scatter(
                    x=rolling_dates, y=rolling_betas[col], mode="lines",
                    name=FACTOR_NAMES.get(col, col),
                    line=dict(color=FACTOR_COLORS.get(col, "#888"), width=2),
                ))
            fig_rb.add_hline(y=0, line_dash="dash", line_color="#333")
            fig_rb.update_layout(template="plotly_dark", height=420,
                                 title=f"Rolling Factor Betas ({roll_window})",
                                 yaxis_title="Beta",
                                 legend=dict(orientation="h", y=-0.12),
                                 margin=dict(l=0, r=0, t=40, b=0))
            st.plotly_chart(fig_rb, use_container_width=True, config=PLOTLY_NOBAR)

            # Style drift score
            st.subheader("Style Drift Detection")
            st.caption("Compares average betas from the first half vs second half of the period. "
                       "Large changes indicate the portfolio's factor profile has shifted.")
            mid = len(rolling_dates) // 2
            drift_data = []
            for col in factor_cols:
                first_half = np.mean(rolling_betas[col][:mid])
                second_half = np.mean(rolling_betas[col][mid:])
                drift_data.append({
                    "Factor": FACTOR_NAMES.get(col, col),
                    "First Half Beta": f"{first_half:.3f}",
                    "Second Half Beta": f"{second_half:.3f}",
                    "Change": f"{second_half - first_half:+.3f}",
                    "Drift": "HIGH" if abs(second_half - first_half) > 0.15 else
                             "MODERATE" if abs(second_half - first_half) > 0.05 else "LOW",
                })
            st.dataframe(pd.DataFrame(drift_data), use_container_width=True, hide_index=True)

            # Rolling alpha
            st.subheader("Rolling Alpha")
            st.caption("Annualized alpha estimated over rolling windows. "
                       "Persistently positive = genuine skill. Mean-reverting around zero = no edge.")
            fig_ra = go.Figure()
            fig_ra.add_trace(go.Scatter(x=rolling_dates, y=rolling_alpha, mode="lines",
                                        line=dict(color="#00ff88", width=2), name="Rolling Alpha (ann. %)"))
            fig_ra.add_hline(y=0, line_dash="dash", line_color="#555")
            fig_ra.add_hline(y=alpha_ann, line_dash="dot", line_color="#ffaa00",
                             annotation_text=f"Full-period: {alpha_ann:+.1f}%")
            fig_ra.update_layout(template="plotly_dark", height=300,
                                 title=f"Rolling Alpha ({roll_window})",
                                 yaxis_title="Alpha (ann. %)",
                                 margin=dict(l=0, r=0, t=40, b=0))
            st.plotly_chart(fig_ra, use_container_width=True, config=PLOTLY_NOBAR)

            # Rolling R²
            fig_rr2 = go.Figure()
            fig_rr2.add_trace(go.Scatter(x=rolling_dates, y=rolling_r2, mode="lines",
                                         line=dict(color="#00d1ff", width=2), name="Rolling R²"))
            fig_rr2.add_hline(y=r2, line_dash="dot", line_color="#ffaa00",
                              annotation_text=f"Full-period: {r2:.3f}")
            fig_rr2.update_layout(template="plotly_dark", height=250,
                                  title=f"Rolling R² ({roll_window})",
                                  yaxis_title="R²", yaxis=dict(range=[0, 1.05]),
                                  margin=dict(l=0, r=0, t=40, b=0))
            st.plotly_chart(fig_rr2, use_container_width=True, config=PLOTLY_NOBAR)
    else:
        st.warning(f"Need at least {window_days + 30} observations for rolling analysis.")


# ═══════════════════════════════════════════════
# TAB 5: RISK DECOMPOSITION
# ═══════════════════════════════════════════════
with tab_risk, error_boundary("Risk Decomposition"):
    st.subheader("Risk Attribution")

    with st.expander("How to read this tab", expanded=False):
        st.markdown(
            "Decomposes your portfolio's total variance into contributions from each factor plus idiosyncratic risk.\n\n"
            "**Systematic risk** is explained by factors — you're compensated for bearing it (risk premia). "
            "**Idiosyncratic risk** is unexplained — you're NOT compensated for it (diversifiable). "
            "A well-diversified portfolio has low idiosyncratic share.\n\n"
            "**Marginal contribution** shows how much removing one unit of factor exposure would reduce total risk. "
            "This guides which exposures to hedge first."
        )

    # Variance decomposition
    total_var = Y_excess.var()
    factor_var = np.var(y_pred)
    idio_var = np.var(residuals)

    # Per-factor contribution to systematic variance
    # Var(sum(beta_i * F_i)) = sum_i sum_j (beta_i * beta_j * Cov(F_i, F_j))
    factor_cov = X.cov()
    factor_var_contributions = {}
    for col in factor_cols:
        # Marginal contribution: beta_i^2 * Var(F_i) + 2*beta_i * sum_{j!=i}(beta_j * Cov(F_i, F_j))
        mc = 0
        for col2 in factor_cols:
            mc += betas[col] * betas[col2] * factor_cov.loc[col, col2]
        factor_var_contributions[col] = mc

    # Normalize to percentages
    total_explained = sum(factor_var_contributions.values())
    if total_var > 1e-10:
        pct_systematic = factor_var / total_var * 100
        pct_idio = idio_var / total_var * 100
    else:
        pct_systematic = pct_idio = 0
        st.warning("Near-zero variance — risk decomposition may be unreliable.")

    rm1, rm2, rm3 = st.columns(3)
    rm1.metric("Systematic Risk", f"{pct_systematic:.1f}%", help="Explained by factors")
    rm2.metric("Idiosyncratic Risk", f"{pct_idio:.1f}%", help="Unexplained — NOT compensated")
    rm3.metric("Total Ann. Vol", f"{Y.std() * np.sqrt(252) * 100:.1f}%")

    # Pie chart
    pie_labels = [FACTOR_NAMES.get(c, c) for c in factor_cols] + ["Idiosyncratic"]
    pie_values = [max(0, factor_var_contributions[c] / total_var * 100) for c in factor_cols] + [pct_idio]
    pie_colors = [FACTOR_COLORS.get(c, "#888") for c in factor_cols] + ["#555"]

    fig_pie = go.Figure(data=go.Pie(
        labels=pie_labels, values=pie_values,
        marker=dict(colors=pie_colors),
        hole=0.4, textinfo="label+percent",
        textfont=dict(size=11),
    ))
    fig_pie.update_layout(template="plotly_dark", height=400,
                          title="Variance Decomposition",
                          margin=dict(l=0, r=0, t=40, b=0))
    st.plotly_chart(fig_pie, use_container_width=True, config=PLOTLY_NOBAR)

    # Marginal risk contribution bar chart
    st.subheader("Marginal Risk Contribution")
    st.caption("How much each factor contributes to total portfolio volatility. "
               "Hedge the largest contributors first to reduce overall risk most efficiently.")

    mrc_data = []
    total_vol = Y.std() * np.sqrt(252) * 100
    for col in factor_cols:
        mrc = factor_var_contributions[col] / total_var * total_vol if total_var > 0 else 0
        mrc_data.append({"Factor": FACTOR_NAMES.get(col, col), "Contribution (vol %)": mrc})
    mrc_df = pd.DataFrame(mrc_data).sort_values("Contribution (vol %)", ascending=True)

    fig_mrc = go.Figure()
    fig_mrc.add_trace(go.Bar(
        y=mrc_df["Factor"], x=mrc_df["Contribution (vol %)"],
        orientation="h",
        marker_color=[FACTOR_COLORS.get(c.split("(")[1].rstrip(")").strip() if "(" in c else c, "#888")
                      for c in mrc_df["Factor"]],
        text=[f"{v:.1f}%" for v in mrc_df["Contribution (vol %)"]],
        textposition="outside",
    ))
    fig_mrc.update_layout(template="plotly_dark", height=300,
                          title="Marginal Volatility Contribution by Factor",
                          xaxis_title="Vol Contribution (%)",
                          margin=dict(l=0, r=60, t=40, b=0))
    st.plotly_chart(fig_mrc, use_container_width=True, config=PLOTLY_NOBAR)

    # Multi-asset comparison (if multiple tickers)
    if len(ticker_list) > 1:
        st.subheader("Per-Asset Factor Exposure")
        st.caption("Compares factor betas across individual assets in the portfolio.")

        asset_betas = {}
        for t in ticker_list:
            if t in port_returns.columns:
                t_ret = port_returns[t].loc[common_idx] - rf
                try:
                    c, _, _, _ = np.linalg.lstsq(X_with_const, t_ret.values, rcond=None)
                    asset_betas[t] = {col: c[i + 1] for i, col in enumerate(factor_cols)}
                    asset_betas[t]["Alpha"] = c[0] * 252 * 100
                except Exception:
                    pass

        if asset_betas:
            comp_df = pd.DataFrame(asset_betas).T
            fig_comp = go.Figure(data=go.Heatmap(
                z=comp_df[factor_cols].values,
                x=[FACTOR_NAMES.get(c, c) for c in factor_cols],
                y=comp_df.index.tolist(),
                colorscale=[[0, "#ff4444"], [0.5, "#1a1a2e"], [1, "#00d1ff"]],
                zmid=0,
                text=[[f"{v:.2f}" for v in row] for row in comp_df[factor_cols].values],
                texttemplate="%{text}", textfont={"size": 11},
                colorbar=dict(title="Beta"),
            ))
            fig_comp.update_layout(template="plotly_dark", height=max(250, len(ticker_list) * 30),
                                   title="Factor Betas by Asset",
                                   margin=dict(l=0, r=0, t=40, b=0))
            st.plotly_chart(fig_comp, use_container_width=True, config=PLOTLY_NOBAR)

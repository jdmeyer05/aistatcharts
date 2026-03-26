"""
Portfolio Optimizer — Institutional-Grade Allocation Engine

5 allocation methods compared head-to-head:
1. Mean-Variance (Markowitz) — efficient frontier + tangency portfolio
2. Minimum Variance — lowest possible volatility
3. Risk Parity — equal risk contribution from each asset
4. Maximum Diversification — maximize diversification ratio
5. HRP (de Prado) — hierarchical risk parity
6. Black-Litterman — blend market equilibrium with user views

Tabs:
1. Efficient Frontier — interactive frontier with all portfolios plotted
2. Optimal Weights — side-by-side weight comparison
3. Backtest — walk-forward out-of-sample performance
4. Risk Analysis — contribution, concentration, drawdown
5. Constraints & Views — Black-Litterman views, weight bounds
"""
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import logging
from scipy.optimize import minimize
from src.layout import setup_page, error_boundary
from src.market_data import fetch_energy_price_history as fetch_price_history
from src.quant_features import hrp_allocate
from src.styles import COLORS

logger = logging.getLogger(__name__)
setup_page("38_Portfolio_Optimizer")

st.title("Portfolio Optimizer")
st.markdown("Mean-variance, minimum variance, risk parity, maximum diversification, and HRP — compared head-to-head with walk-forward backtesting.")

PLOTLY_NOBAR = {"displayModeBar": False}

METHOD_COLORS = {
    "Tangency (Max Sharpe)": "#00d1ff",
    "Min Variance": "#00ff88",
    "Risk Parity": "#ffaa00",
    "Max Diversification": "#ff00ff",
    "HRP": "#88ccff",
    "Equal Weight": "#555",
    "Black-Litterman": "#ff6b6b",
}


# ═══════════════════════════════════════════════
# OPTIMIZATION FUNCTIONS
# ═══════════════════════════════════════════════

def _tangency_portfolio(mu: np.ndarray, cov: np.ndarray, rf: float = 0.0) -> np.ndarray:
    """Maximum Sharpe ratio portfolio (tangency)."""
    n = len(mu)
    w0 = np.full(n, 1 / n)
    constraints = [{"type": "eq", "fun": lambda w: np.sum(w) - 1}]
    bounds = [(0, 1)] * n  # long-only

    def neg_sharpe(w):
        ret = w @ mu
        vol = np.sqrt(w @ cov @ w)
        return -(ret - rf) / vol if vol > 1e-12 else 1e10

    result = minimize(neg_sharpe, w0, method="SLSQP", bounds=bounds, constraints=constraints)
    return result.x if result.success else w0


def _min_variance(cov: np.ndarray) -> np.ndarray:
    """Global minimum variance portfolio."""
    n = cov.shape[0]
    w0 = np.full(n, 1 / n)
    constraints = [{"type": "eq", "fun": lambda w: np.sum(w) - 1}]
    bounds = [(0, 1)] * n

    def portfolio_vol(w):
        return np.sqrt(w @ cov @ w)

    result = minimize(portfolio_vol, w0, method="SLSQP", bounds=bounds, constraints=constraints)
    return result.x if result.success else w0


def _risk_parity(cov: np.ndarray) -> np.ndarray:
    """Risk parity — equal risk contribution from each asset."""
    n = cov.shape[0]
    w0 = np.full(n, 1 / n)

    def risk_budget_obj(w):
        port_vol = np.sqrt(w @ cov @ w)
        if port_vol == 0:
            return 0
        mrc = cov @ w / port_vol  # marginal risk contribution
        rc = w * mrc  # risk contribution
        target = port_vol / n  # equal risk budget
        return np.sum((rc - target) ** 2)

    constraints = [{"type": "eq", "fun": lambda w: np.sum(w) - 1}]
    bounds = [(0.01, 1)] * n  # small floor to prevent zero weights

    result = minimize(risk_budget_obj, w0, method="SLSQP", bounds=bounds, constraints=constraints)
    if not result.success:
        logger.warning(f"Risk Parity optimization did not converge: {result.message}")
    return result.x if result.success else w0


def _max_diversification(cov: np.ndarray) -> np.ndarray:
    """Maximum diversification ratio: maximize weighted avg vol / portfolio vol."""
    n = cov.shape[0]
    vols = np.sqrt(np.diag(cov))
    w0 = np.full(n, 1 / n)
    constraints = [{"type": "eq", "fun": lambda w: np.sum(w) - 1}]
    bounds = [(0, 1)] * n

    def neg_div_ratio(w):
        port_vol = np.sqrt(w @ cov @ w)
        weighted_vol = w @ vols
        return -weighted_vol / port_vol if port_vol > 0 else 0

    result = minimize(neg_div_ratio, w0, method="SLSQP", bounds=bounds, constraints=constraints)
    return result.x if result.success else w0


def _efficient_frontier(mu: np.ndarray, cov: np.ndarray, n_points: int = 50) -> list:
    """Compute the efficient frontier — set of portfolios with minimum vol for each target return."""
    n = len(mu)
    min_ret = mu.min()
    max_ret = mu.max()
    # Keep targets within achievable bounds (no extrapolation beyond observed returns)
    target_rets = np.linspace(min_ret, max_ret, n_points)
    frontier = []

    for target in target_rets:
        w0 = np.full(n, 1 / n)
        constraints = [
            {"type": "eq", "fun": lambda w: np.sum(w) - 1},
            {"type": "eq", "fun": lambda w, t=target: w @ mu - t},
        ]
        bounds = [(0, 1)] * n

        def port_vol(w):
            return np.sqrt(w @ cov @ w)

        result = minimize(port_vol, w0, method="SLSQP", bounds=bounds, constraints=constraints)
        if result.success:
            vol = np.sqrt(result.x @ cov @ result.x)
            ret = result.x @ mu
            frontier.append({"return": ret * 252 * 100, "vol": vol * np.sqrt(252) * 100, "weights": result.x})

    return frontier


def _portfolio_metrics(returns: pd.Series, name: str) -> dict:
    """Compute standard portfolio metrics."""
    ann_ret = returns.mean() * 252 * 100
    ann_vol = returns.std() * np.sqrt(252) * 100
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
    cum = (1 + returns).cumprod()
    dd = ((cum / cum.cummax()) - 1).min() * 100
    sortino_denom = returns[returns < 0].std() * np.sqrt(252) * 100
    sortino = ann_ret / sortino_denom if sortino_denom > 0 else 0
    calmar = ann_ret / abs(dd) if dd != 0 else 0
    return {
        "Method": name, "Ann. Return": f"{ann_ret:.1f}%", "Ann. Vol": f"{ann_vol:.1f}%",
        "Sharpe": f"{sharpe:.2f}", "Sortino": f"{sortino:.2f}",
        "Max DD": f"{dd:.1f}%", "Calmar": f"{calmar:.2f}",
    }


# ═══════════════════════════════════════════════
# CONTROLS
# ═══════════════════════════════════════════════

c1, c2, c3 = st.columns([3, 1, 1])
with c1:
    raw_tickers = st.text_input("Portfolio assets (comma-separated)",
                                value="SPY,TLT,GLD,EFA,IWM,USO,HYG,VNQ",
                                key="po_tickers")
with c2:
    po_lookback = st.selectbox("Estimation Window", ["1Y", "2Y", "3Y", "5Y"],
                                index=1, key="po_lookback")
with c3:
    st.markdown("<br>", unsafe_allow_html=True)
    po_load = st.button("Optimize", type="primary", use_container_width=True, key="po_load")

if po_load:
    st.session_state["po_loaded"] = True
if not st.session_state.get("po_loaded"):
    st.info("Enter asset tickers and click **Optimize**.")
    st.stop()

ticker_list = [t.strip().upper() for t in raw_tickers.split(",") if t.strip()]
if len(ticker_list) < 3:
    st.error("Need at least 3 assets for portfolio optimization.")
    st.stop()

lookback_map = {"1Y": "1y", "2Y": "2y", "3Y": "3y", "5Y": "5y"}

with st.spinner(f"Loading {len(ticker_list)} assets..."):
    prices = fetch_price_history(ticker_list, period=lookback_map[po_lookback])

if prices.empty or len(prices.columns) < 3:
    st.error("Insufficient price data. Try different tickers or a shorter lookback.")
    st.stop()

returns = prices.pct_change().dropna()
tickers = returns.columns.tolist()
n_assets = len(tickers)
mu = returns.mean().values
cov = returns.cov().values
ann_mu = mu * 252
ann_cov = cov * 252

# ═══════════════════════════════════════════════
# COMPUTE ALL ALLOCATIONS
# ═══════════════════════════════════════════════

with st.spinner("Computing optimal portfolios..."):
    w_tangency = _tangency_portfolio(mu, cov)
    w_minvar = _min_variance(cov)
    w_riskparity = _risk_parity(cov)
    w_maxdiv = _max_diversification(cov)
    w_hrp = hrp_allocate(returns).reindex(tickers).fillna(0).values
    w_equal = np.full(n_assets, 1 / n_assets)

allocations = {
    "Tangency (Max Sharpe)": w_tangency,
    "Min Variance": w_minvar,
    "Risk Parity": w_riskparity,
    "Max Diversification": w_maxdiv,
    "HRP": w_hrp,
    "Equal Weight": w_equal,
}

# Header metrics for tangency portfolio
t_ret = w_tangency @ ann_mu * 100
t_vol = np.sqrt(w_tangency @ ann_cov @ w_tangency) * 100
t_sharpe = t_ret / t_vol if t_vol > 0 else 0
hm1, hm2, hm3, hm4 = st.columns(4)
hm1.metric("Assets", n_assets)
hm2.metric("Max Sharpe Return", f"{t_ret:.1f}%")
hm3.metric("Max Sharpe Vol", f"{t_vol:.1f}%")
hm4.metric("Max Sharpe Ratio", f"{t_sharpe:.2f}")


# ═══════════════════════════════════════════════
# TABS
# ═══════════════════════════════════════════════

tab_frontier, tab_weights, tab_backtest, tab_risk, tab_views = st.tabs([
    "Efficient Frontier",
    "Optimal Weights",
    "Walk-Forward Backtest",
    "Risk Analysis",
    "Black-Litterman",
])


# ═══════════════════════════════════════════════
# TAB 1: EFFICIENT FRONTIER
# ═══════════════════════════════════════════════
with tab_frontier, error_boundary("Efficient Frontier"):
    st.subheader("Mean-Variance Efficient Frontier")

    with st.expander("How to read this tab", expanded=False):
        st.markdown(
            "The **efficient frontier** is the set of portfolios offering the highest return for each level of risk. "
            "Any portfolio below the frontier is suboptimal — you could get more return for the same risk, or less risk for the same return.\n\n"
            "**Tangency portfolio** (star) is the point where a line from the risk-free rate touches the frontier — "
            "it has the highest Sharpe ratio.\n\n"
            "**Individual assets** (circles) typically sit below the frontier — diversification creates portfolios "
            "that outperform any single asset on a risk-adjusted basis.\n\n"
            "**Limitations:** Mean-variance is sensitive to estimation errors in expected returns. "
            "Small changes in inputs can produce wildly different optimal portfolios. "
            "Use the Risk Parity or HRP tabs for more stable alternatives."
        )

    frontier = _efficient_frontier(mu, cov, n_points=60)

    fig_ef = go.Figure()

    # Frontier curve
    if frontier:
        fig_ef.add_trace(go.Scatter(
            x=[p["vol"] for p in frontier], y=[p["return"] for p in frontier],
            mode="lines", name="Efficient Frontier",
            line=dict(color="#00d1ff", width=3),
        ))

    # Individual assets
    for i, t in enumerate(tickers):
        a_ret = mu[i] * 252 * 100
        a_vol = np.sqrt(cov[i, i]) * np.sqrt(252) * 100
        fig_ef.add_trace(go.Scatter(
            x=[a_vol], y=[a_ret], mode="markers+text",
            marker=dict(size=10, color="#555", line=dict(width=1, color="#fff")),
            text=[t], textposition="top center", textfont=dict(size=10, color="#aaa"),
            showlegend=False,
        ))

    # Plot each allocation method
    for method, w in allocations.items():
        p_ret = w @ ann_mu * 100
        p_vol = np.sqrt(w @ ann_cov @ w) * 100
        color = METHOD_COLORS.get(method, "#888")
        symbol = "star" if "Tangency" in method else "diamond" if "Min" in method else "square"
        fig_ef.add_trace(go.Scatter(
            x=[p_vol], y=[p_ret], mode="markers",
            marker=dict(size=14, color=color, symbol=symbol, line=dict(width=2, color="#fff")),
            name=method,
        ))

    fig_ef.update_layout(template="plotly_dark", height=500,
                          title="Efficient Frontier with Optimal Portfolios",
                          xaxis_title="Annualized Volatility (%)",
                          yaxis_title="Annualized Return (%)",
                          legend=dict(orientation="h", y=-0.15),
                          margin=dict(l=0, r=0, t=40, b=0))
    st.plotly_chart(fig_ef, use_container_width=True, config=PLOTLY_NOBAR)

    # Frontier metrics table
    frontier_data = []
    for method, w in allocations.items():
        p_ret = w @ ann_mu * 100
        p_vol = np.sqrt(w @ ann_cov @ w) * 100
        p_sharpe = p_ret / p_vol if p_vol > 0 else 0
        frontier_data.append({
            "Method": method, "Exp. Return": f"{p_ret:.1f}%",
            "Exp. Vol": f"{p_vol:.1f}%", "Sharpe": f"{p_sharpe:.2f}",
            "Max Weight": f"{w.max() * 100:.0f}%",
            "Active Positions": f"{(w > 0.01).sum()}/{n_assets}",
        })
    st.dataframe(pd.DataFrame(frontier_data), use_container_width=True, hide_index=True)


# ═══════════════════════════════════════════════
# TAB 2: OPTIMAL WEIGHTS
# ═══════════════════════════════════════════════
with tab_weights, error_boundary("Optimal Weights"):
    st.subheader("Weight Comparison")

    with st.expander("How to read this tab", expanded=False):
        st.markdown(
            "Compares portfolio weights across all optimization methods.\n\n"
            "**Tangency** tends to concentrate in high-Sharpe assets — unstable, high turnover. "
            "**Min Variance** concentrates in low-vol assets (bonds, utilities). "
            "**Risk Parity** spreads risk evenly — overweights low-vol, underweights high-vol. "
            "**Max Diversification** maximizes the diversification ratio — favors uncorrelated assets. "
            "**HRP** uses hierarchical clustering — stable, no matrix inversion needed.\n\n"
            "Watch for **concentration**: if any method puts >40% in one asset, it's fragile."
        )

    # Grouped bar chart
    fig_w = go.Figure()
    for method, w in allocations.items():
        fig_w.add_trace(go.Bar(
            x=tickers, y=w * 100, name=method,
            marker_color=METHOD_COLORS.get(method, "#888"),
        ))
    fig_w.update_layout(template="plotly_dark", height=420, barmode="group",
                         title="Portfolio Weights by Method (%)",
                         yaxis_title="Weight (%)",
                         legend=dict(orientation="h", y=-0.15),
                         margin=dict(l=0, r=0, t=40, b=0))
    st.plotly_chart(fig_w, use_container_width=True, config=PLOTLY_NOBAR)

    # Weight table
    weight_df = pd.DataFrame({method: pd.Series(w, index=tickers) for method, w in allocations.items()})
    display_w = weight_df.map(lambda v: f"{v * 100:.1f}%")
    st.dataframe(display_w, use_container_width=True)

    # Concentration metrics
    st.subheader("Concentration Analysis")
    conc_data = []
    for method, w in allocations.items():
        hhi = np.sum(w ** 2)  # Herfindahl-Hirschman Index
        eff_n = 1 / hhi if hhi > 0 else n_assets  # effective number of assets
        conc_data.append({
            "Method": method, "HHI": f"{hhi:.3f}",
            "Effective N": f"{eff_n:.1f}",
            "Max Weight": f"{w.max() * 100:.0f}%",
            "Non-Zero": f"{(w > 0.01).sum()}",
        })
    st.dataframe(pd.DataFrame(conc_data), use_container_width=True, hide_index=True)


# ═══════════════════════════════════════════════
# TAB 3: WALK-FORWARD BACKTEST
# ═══════════════════════════════════════════════
with tab_backtest, error_boundary("Walk-Forward Backtest"):
    st.subheader("Walk-Forward Out-of-Sample Backtest")

    with st.expander("How to read this tab", expanded=False):
        st.markdown(
            "Optimizes weights using only **past data** (trailing window), then holds for one period before re-optimizing. "
            "This eliminates look-ahead bias — the most common source of backtest overfit.\n\n"
            "**Rebalance frequency** controls how often weights are updated. "
            "Monthly is standard for institutional portfolios. "
            "**Estimation window** is how many trailing days of data are used to estimate covariances and returns.\n\n"
            "The comparison shows which method actually performs best **out of sample** — "
            "often different from which looks best in-sample."
        )

    rebal = st.radio("Rebalance", ["Monthly", "Quarterly"], horizontal=True, key="po_rebal")
    est_window = st.slider("Estimation window (days)", 126, 504, 252, 63, key="po_est_window")

    rebal_period = "ME" if rebal == "Monthly" else "QE"
    rebal_dates = returns.resample(rebal_period).last().index
    rebal_dates = [d for d in rebal_dates if returns.index.get_loc(d) >= est_window]

    if len(rebal_dates) < 4:
        st.warning("Not enough data for walk-forward backtest with this window. Try a shorter estimation window.")
    else:
        with st.spinner("Running walk-forward backtest..."):
            wf_results = {method: [] for method in allocations}
            wf_results["Equal Weight"] = []

            for i in range(len(rebal_dates)):
                rd = rebal_dates[i]
                rd_loc = returns.index.get_loc(rd)
                est_ret = returns.iloc[rd_loc - est_window:rd_loc]
                est_mu = est_ret.mean().values
                est_cov = est_ret.cov().values

                # Compute weights from estimation window
                methods_w = {
                    "Tangency (Max Sharpe)": _tangency_portfolio(est_mu, est_cov),
                    "Min Variance": _min_variance(est_cov),
                    "Risk Parity": _risk_parity(est_cov),
                    "Max Diversification": _max_diversification(est_cov),
                    "HRP": hrp_allocate(est_ret).reindex(tickers).fillna(0).values,
                    "Equal Weight": np.full(n_assets, 1 / n_assets),
                }

                # Out-of-sample period
                if i < len(rebal_dates) - 1:
                    oos_ret = returns.loc[rd:rebal_dates[i + 1]]
                else:
                    oos_ret = returns.loc[rd:]

                for method, w in methods_w.items():
                    port_ret = (oos_ret.values @ w)
                    for j, dt in enumerate(oos_ret.index):
                        wf_results[method].append({"date": dt, "return": port_ret[j]})

        # Build cumulative returns
        fig_bt = go.Figure()
        bt_metrics = []
        for method in wf_results:
            df_m = pd.DataFrame(wf_results[method]).set_index("date")
            df_m = df_m[~df_m.index.duplicated(keep="first")]
            cum = (1 + df_m["return"]).cumprod() * 100
            color = METHOD_COLORS.get(method, "#888")
            width = 3 if method in ("Tangency (Max Sharpe)", "HRP") else 1
            fig_bt.add_trace(go.Scatter(
                x=cum.index, y=cum, mode="lines", name=method,
                line=dict(color=color, width=width),
            ))
            bt_metrics.append(_portfolio_metrics(df_m["return"], method))

        fig_bt.add_hline(y=100, line_dash="dash", line_color="#333")
        fig_bt.update_layout(template="plotly_dark", height=450,
                              title=f"Walk-Forward Backtest ({rebal}, {est_window}D Window, base=100)",
                              yaxis_title="Portfolio Value",
                              legend=dict(orientation="h", y=-0.15),
                              margin=dict(l=0, r=0, t=40, b=0))
        st.plotly_chart(fig_bt, use_container_width=True, config=PLOTLY_NOBAR)

        st.subheader("Out-of-Sample Performance")
        st.dataframe(pd.DataFrame(bt_metrics), use_container_width=True, hide_index=True)


# ═══════════════════════════════════════════════
# TAB 4: RISK ANALYSIS
# ═══════════════════════════════════════════════
with tab_risk, error_boundary("Risk Analysis"):
    st.subheader("Risk Contribution Analysis")

    with st.expander("How to read this tab", expanded=False):
        st.markdown(
            "Shows how risk is distributed across assets for each allocation method.\n\n"
            "**Risk contribution** = asset weight × marginal risk contribution. "
            "In a risk parity portfolio, all bars are equal height. "
            "In tangency or min-var, risk is concentrated in a few assets.\n\n"
            "**Diversification ratio** = weighted average vol / portfolio vol. "
            "Higher = more diversification benefit. A ratio of 1.0 means no diversification (perfectly correlated)."
        )

    method_select = st.selectbox("Method", list(allocations.keys()), key="po_risk_method")
    w_sel = allocations[method_select]

    port_vol = np.sqrt(w_sel @ ann_cov @ w_sel)
    if port_vol > 1e-10:
        mrc = ann_cov @ w_sel / port_vol  # marginal risk contribution
        rc = w_sel * mrc  # risk contribution
        rc_pct = rc / port_vol * 100  # as % of total vol
    else:
        rc_pct = np.full(n_assets, np.nan)
        st.warning("Portfolio has near-zero volatility — risk decomposition is undefined.")

    # Risk contribution bar chart
    fig_rc = go.Figure()
    fig_rc.add_trace(go.Bar(
        x=tickers, y=rc_pct,
        marker_color=METHOD_COLORS.get(method_select, "#00d1ff"),
        text=[f"{v:.1f}%" for v in rc_pct], textposition="outside",
    ))
    target_rc = 100 / n_assets
    fig_rc.add_hline(y=target_rc, line_dash="dash", line_color="#ffaa00",
                     annotation_text=f"Equal risk: {target_rc:.1f}%")
    fig_rc.update_layout(template="plotly_dark", height=380,
                          title=f"Risk Contribution — {method_select}",
                          yaxis_title="% of Total Vol",
                          margin=dict(l=0, r=0, t=40, b=0))
    st.plotly_chart(fig_rc, use_container_width=True, config=PLOTLY_NOBAR)

    # Diversification metrics
    vols = np.sqrt(np.diag(ann_cov))
    div_ratio = (w_sel @ vols) / port_vol if port_vol > 0 else 1
    hhi = np.sum(w_sel ** 2)

    dr1, dr2, dr3, dr4 = st.columns(4)
    dr1.metric("Portfolio Vol", f"{port_vol * 100:.1f}%")
    dr2.metric("Diversification Ratio", f"{div_ratio:.2f}", help=">1 = diversification is working")
    dr3.metric("HHI (Concentration)", f"{hhi:.3f}", help="0 = perfectly spread, 1 = single asset")
    dr4.metric("Effective N", f"{1/hhi:.1f}" if hhi > 0 else "N/A")

    # Correlation contribution
    st.subheader("Correlation Impact")
    st.caption("Shows how correlations between assets affect portfolio risk. "
               "High correlation between large positions amplifies risk.")

    corr_matrix = returns.corr()
    fig_corr = go.Figure(data=go.Heatmap(
        z=corr_matrix.values, x=tickers, y=tickers,
        colorscale=[[0, "#ff4444"], [0.5, "#1a1a2e"], [1, "#00d1ff"]],
        zmid=0, zmin=-1, zmax=1,
        text=[[f"{v:.2f}" for v in row] for row in corr_matrix.values],
        texttemplate="%{text}", textfont={"size": 10},
        colorbar=dict(title="Corr"),
    ))
    fig_corr.update_layout(template="plotly_dark", height=max(300, n_assets * 30),
                            margin=dict(l=0, r=0, t=10, b=0))
    st.plotly_chart(fig_corr, use_container_width=True, config=PLOTLY_NOBAR)


# ═══════════════════════════════════════════════
# TAB 5: BLACK-LITTERMAN
# ═══════════════════════════════════════════════
with tab_views, error_boundary("Black-Litterman"):
    st.subheader("Black-Litterman Model")

    with st.expander("How to read this tab", expanded=False):
        st.markdown(
            "Black-Litterman blends **market equilibrium returns** (implied by market cap weights) "
            "with your **personal views** on specific assets.\n\n"
            "**Without views**, BL produces the market portfolio (similar to cap-weighted). "
            "**With views**, it tilts toward your beliefs — but tempered by your confidence level.\n\n"
            "**How to use:**\n"
            "1. Enter a view: e.g., 'SPY will return 12% annualized'\n"
            "2. Set confidence (tau): lower = more deference to the market, higher = trust your view more\n"
            "3. BL computes a new set of expected returns that blend market equilibrium with your view\n"
            "4. The resulting portfolio tilts toward your view proportionally to your confidence\n\n"
            "**This is how institutional asset allocators work** — they don't throw out market prices, "
            "they blend their research with the market's collective wisdom."
        )

    # Market equilibrium returns (reverse optimization from equal-weight as proxy)
    risk_aversion = st.slider("Risk aversion (delta)", 1.0, 5.0, 2.5, 0.5, key="po_bl_delta")
    tau = st.slider("Confidence in views (tau)", 0.01, 0.5, 0.05, 0.01, key="po_bl_tau")

    # Implied equilibrium returns
    pi = risk_aversion * ann_cov @ w_equal  # equilibrium excess returns

    st.caption("**Equilibrium expected returns** (implied from market):")
    eq_df = pd.DataFrame({"Asset": tickers, "Equilibrium Return (ann.)": [f"{r*100:.1f}%" for r in pi]})
    st.dataframe(eq_df, use_container_width=True, hide_index=True)

    # User views
    st.markdown("#### Your Views")
    st.caption("Enter up to 3 absolute views (e.g., 'I think SPY will return 15% annualized').")

    view_cols = st.columns(3)
    views_P = []
    views_Q = []
    for i, vc in enumerate(view_cols):
        with vc:
            v_ticker = st.selectbox(f"View {i+1} Asset", ["(none)"] + tickers, key=f"po_bl_v{i}_t")
            v_return = st.number_input(f"Expected Return (%)", value=0.0, step=1.0, key=f"po_bl_v{i}_r")
            if v_ticker != "(none)" and v_return != 0:
                p_row = np.zeros(n_assets)
                p_row[tickers.index(v_ticker)] = 1
                views_P.append(p_row)
                views_Q.append(v_return / 100)

    if views_P:
        P = np.array(views_P)
        Q = np.array(views_Q)
        # Omega: uncertainty of views (proportional to variance of the view portfolio)
        omega = np.diag(np.diag(tau * P @ ann_cov @ P.T))

        # Black-Litterman posterior
        try:
            inv_tau_cov = np.linalg.inv(tau * ann_cov)
            inv_omega = np.linalg.inv(omega)
            bl_cov_post = np.linalg.inv(inv_tau_cov + P.T @ inv_omega @ P)
            bl_mu_post = bl_cov_post @ (inv_tau_cov @ pi + P.T @ inv_omega @ Q)
            w_bl = _tangency_portfolio(bl_mu_post / 252, (ann_cov + bl_cov_post) / 252)
        except np.linalg.LinAlgError:
            st.warning("Black-Litterman posterior is singular — views may be inconsistent. Using tangency portfolio.")
            bl_mu_post = pi
            bl_cov_post = ann_cov * tau
            w_bl = w_tangency

        st.subheader("Black-Litterman Results")

        # Compare equilibrium vs posterior returns
        bl_ret_df = pd.DataFrame({
            "Asset": tickers,
            "Equilibrium": [f"{r*100:.1f}%" for r in pi],
            "BL Posterior": [f"{r*100:.1f}%" for r in bl_mu_post],
            "Shift": [f"{(bl_mu_post[i]-pi[i])*100:+.1f}%" for i in range(n_assets)],
        })
        st.dataframe(bl_ret_df, use_container_width=True, hide_index=True)

        # BL weights vs tangency
        fig_bl = go.Figure()
        fig_bl.add_trace(go.Bar(x=tickers, y=w_bl * 100, name="Black-Litterman",
                                marker_color="#ff6b6b"))
        fig_bl.add_trace(go.Bar(x=tickers, y=w_tangency * 100, name="Tangency (no views)",
                                marker_color="#00d1ff"))
        fig_bl.add_trace(go.Bar(x=tickers, y=w_equal * 100, name="Equal Weight",
                                marker_color="#555"))
        fig_bl.update_layout(template="plotly_dark", height=380, barmode="group",
                              title="Black-Litterman Weights vs Tangency vs Equal",
                              yaxis_title="Weight (%)",
                              margin=dict(l=0, r=0, t=40, b=0))
        st.plotly_chart(fig_bl, use_container_width=True, config=PLOTLY_NOBAR)

        # BL portfolio metrics (in-sample)
        bl_ret_ts = returns @ w_bl
        bl_metrics = _portfolio_metrics(bl_ret_ts, "Black-Litterman")
        tang_metrics = _portfolio_metrics(returns @ w_tangency, "Tangency (no views)")
        st.dataframe(pd.DataFrame([bl_metrics, tang_metrics]), use_container_width=True, hide_index=True)
    else:
        st.info("Enter at least one view above to compute the Black-Litterman posterior.")

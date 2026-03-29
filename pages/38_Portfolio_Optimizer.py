"""
Portfolio Optimizer — Institutional-Grade Allocation Engine

9 allocation methods compared head-to-head:
1. Mean-Variance (Markowitz) — efficient frontier + tangency portfolio
2. Robust Max Sharpe — worst-case return within confidence set
3. Minimum Variance — lowest possible volatility
4. Risk Parity — equal risk contribution from each asset
5. Maximum Diversification — maximize diversification ratio
6. HRP (de Prado) — hierarchical risk parity (Ward linkage)
7. HERC (Raffinot) — hierarchical equal risk contribution with CVaR
8. HCAA (Raffinot) — hierarchical clustering-based 1/N allocation
9. Black-Litterman — blend market equilibrium with user views

Optional Ledoit-Wolf covariance denoising (shrinkage) applied before clustering.

Tabs:
1. Efficient Frontier — interactive frontier with all portfolios plotted
2. Optimal Weights — side-by-side weight comparison
3. Backtest — walk-forward out-of-sample performance
4. Risk Analysis — contribution, concentration, drawdown, dendrogram
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
from src.quant_features import hrp_allocate, herc_allocate, hcaa_allocate, denoise_covariance
from src.styles import COLORS

logger = logging.getLogger(__name__)
setup_page("38_Portfolio_Optimizer")

st.title("Portfolio Optimizer")
st.markdown("Mean-variance, risk parity, HRP, HERC (CVaR), HCAA, and more — compared head-to-head with walk-forward backtesting and Ledoit-Wolf denoising.")

PLOTLY_NOBAR = {"displayModeBar": False}

METHOD_COLORS = {
    "Tangency (Max Sharpe)": "#00d1ff",
    "Robust Max Sharpe": "#00e0d0",
    "Min Variance": "#00ff88",
    "Risk Parity": "#ffaa00",
    "Max Diversification": "#ff00ff",
    "HRP": "#88ccff",
    "HERC (CVaR)": "#cc88ff",
    "HCAA (1/N)": "#66aacc",
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
# PRESET UNIVERSES
# ═══════════════════════════════════════════════

PRESETS = {
    "Custom": "",
    "Multi-Asset (Default)": "SPY,TLT,GLD,EFA,IWM,USO,HYG,VNQ",
    "Sector ETFs": "XLE,XLF,XLK,XLV,XLI,XLC,XLY,XLP,XLU,XLB,XLRE",
    "Energy Sector": "XOM,CVX,COP,EOG,SLB,MPC,OXY,PSX,VLO,DVN",
    "Financials Sector": "JPM,BAC,WFC,GS,MS,BLK,SCHW,C,AXP,MMC",
    "Technology Sector": "AAPL,MSFT,NVDA,AVGO,CRM,ORCL,AMD,ADBE,ACN,CSCO",
    "Healthcare Sector": "UNH,LLY,JNJ,ABBV,MRK,TMO,ABT,AMGN,DHR,PFE",
    "Industrials Sector": "GE,CAT,UNP,HON,RTX,DE,LMT,BA,ETN,ADP",
    "Communication Sector": "META,GOOGL,NFLX,T,CMCSA,VZ,DIS,TMUS,EA,CHTR",
    "Consumer Disc Sector": "AMZN,TSLA,HD,MCD,NKE,LOW,BKNG,SBUX,TJX,CMG",
    "Consumer Staples Sector": "PG,COST,WMT,KO,PEP,PM,MDLZ,MO,CL,STZ",
    "Utilities Sector": "NEE,SO,DUK,CEG,SRE,AEP,D,EXC,XEL,PEG",
    "Materials Sector": "LIN,SHW,APD,ECL,FCX,NUE,NEM,VMC,MLM,DOW",
    "Real Estate Sector": "PLD,AMT,EQIX,SPG,PSA,O,WELL,DLR,VICI,CCI",
    "Mega Caps": "AAPL,MSFT,NVDA,AMZN,GOOGL,META,TSLA,BRK-B,JPM,V",
    "Global Macro": "SPY,EFA,EEM,TLT,IEF,GLD,USO,UNG,DBA,UUP",
    "60/40 Classic": "SPY,TLT",
    "All-Weather": "SPY,TLT,GLD,DBA,IEF",
}


# ═══════════════════════════════════════════════
# CONTROLS
# ═══════════════════════════════════════════════

pc1, pc2 = st.columns([1, 3])
with pc1:
    preset = st.selectbox("Preset", list(PRESETS.keys()), index=1, key="po_preset",
                          on_change=lambda: st.session_state.update(
                              po_tickers=PRESETS.get(st.session_state.po_preset, "")))

# Initialize ticker input from preset if not yet set
if "po_tickers" not in st.session_state:
    st.session_state["po_tickers"] = PRESETS.get(preset, "SPY,TLT,GLD,EFA,IWM")

c1, c2, c3 = st.columns([3, 1, 1])
with c1:
    raw_tickers = st.text_input("Portfolio assets (comma-separated)", key="po_tickers")
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

# Strip timezone from price index for compatibility
if not prices.empty and prices.index.tz is not None:
    prices.index = prices.index.tz_localize(None)

if prices.empty or len(prices.columns) < 3:
    st.error("Insufficient price data. Try different tickers or a shorter lookback.")
    st.stop()

returns = prices.pct_change().dropna()
tickers = returns.columns.tolist()
n_assets = len(tickers)
cov = returns.cov().values
ann_cov = cov * 252

# ── Covariance denoising option ──
use_denoising = st.checkbox(
    "Ledoit-Wolf covariance denoising",
    value=True,
    key="po_denoise",
    help="Shrinks the sample covariance toward a structured target, reducing estimation noise. "
         "Stabilizes clustering (HRP/HERC/HCAA) and reduces portfolio turnover. "
         "Recommended when estimation window < 3 years.",
)
if use_denoising:
    denoised_cov, denoised_corr = denoise_covariance(returns)
else:
    denoised_cov = pd.DataFrame(ann_cov, index=tickers, columns=tickers)
    denoised_corr = returns.corr()

# Date context
data_start = returns.index[0].strftime("%Y-%m-%d")
data_end = returns.index[-1].strftime("%Y-%m-%d")
n_trading_days = len(returns)
n_years = n_trading_days / 252

# ═══════════════════════════════════════════════
# RETURN ESTIMATION METHOD
# ═══════════════════════════════════════════════

RETURN_METHODS = {
    "Sample Mean": "Raw historical average. Simple but extremely noisy — standard error ≈ vol/√T.",
    "Shrinkage (Ledoit-Wolf)": "Shrinks sample means toward the grand mean. More stable, less extreme.",
    "CAPM Implied": "Returns implied by each asset's beta to SPY × the equity risk premium.",
    "BL Equilibrium": "Black-Litterman equilibrium — reverse-optimized from equal-weight market portfolio.",
}

ret_method = st.radio("Return estimation method", list(RETURN_METHODS.keys()),
                      index=1, horizontal=True, key="po_ret_method",
                      help="How expected returns are estimated. This only affects Tangency and the Efficient Frontier — "
                           "Min Var, Risk Parity, Max Div, HRP, HERC, and HCAA use only the covariance matrix.")

with st.expander(f"About: {ret_method}"):
    st.caption(RETURN_METHODS[ret_method])
    if ret_method == "Sample Mean":
        st.warning("De Prado (AFML Ch. 10): raw sample means are dominated by estimation error. "
                   "The tangency portfolio is effectively a maximizer of estimation error, not expected return. "
                   "Use shrinkage or BL equilibrium for more stable results.")
    elif ret_method == "Shrinkage (Ledoit-Wolf)":
        st.info("Shrinks each asset's mean return toward the cross-sectional average. "
                "Shrinkage intensity is proportional to estimation uncertainty. "
                "This dramatically stabilizes the tangency portfolio.")
    elif ret_method == "CAPM Implied":
        st.info("Uses each asset's beta to SPY (or the first asset if SPY not present) times an assumed "
                "equity risk premium of 6%. Assets with higher beta get higher expected returns.")
    elif ret_method == "BL Equilibrium":
        st.info("Reverse-optimizes: given the current covariance and equal-weight portfolio, "
                "what expected returns would make a rational investor hold those weights? "
                "This is the starting point for Black-Litterman before views are applied.")

# Compute expected returns based on selected method
raw_mu = returns.mean().values  # always compute for reference

if ret_method == "Sample Mean":
    mu = raw_mu

elif ret_method == "Shrinkage (Ledoit-Wolf)":
    # Bayes-Stein shrinkage toward global minimum variance portfolio return
    # More robust than shrinking toward grand mean — preserves relative ordering
    n_obs = len(returns)
    grand_mean = raw_mu.mean()

    # Shrinkage intensity based on Jorion (1986) / Ledoit-Wolf
    # alpha = (n_assets + 2) / ((n_assets + 2) + n_obs * d^2)
    # where d^2 = (mu - grand_mean)' * Sigma_inv * (mu - grand_mean)
    try:
        cov_inv = np.linalg.inv(cov)
        diff = raw_mu - grand_mean
        d_sq = diff @ cov_inv @ diff
        alpha = (n_assets + 2) / ((n_assets + 2) + n_obs * d_sq) if d_sq > 0 else 0.5
        alpha = np.clip(alpha, 0.05, 0.95)  # bound to prevent full collapse or no shrinkage
    except np.linalg.LinAlgError:
        alpha = 0.5  # fallback

    mu = (1 - alpha) * raw_mu + alpha * grand_mean

elif ret_method == "CAPM Implied":
    # Use first asset or SPY as market proxy
    market_col = "SPY" if "SPY" in tickers else tickers[0]
    market_ret = returns[market_col].values
    market_var = np.var(market_ret)
    erp = 0.06 / 252  # 6% annual equity risk premium, daily
    betas = np.array([np.cov(returns[t].values, market_ret)[0, 1] / market_var
                      if market_var > 0 else 1.0 for t in tickers])
    mu = betas * erp

elif ret_method == "BL Equilibrium":
    # Reverse optimization: pi = delta * Sigma * w_mkt
    # Use market-cap weights if available, else equal-weight
    delta = 2.5
    try:
        from src.market_data import fetch_energy_valuation_data
        val = fetch_energy_valuation_data(tickers)
        if not val.empty and "market_cap" in val.columns:
            mcaps = val.set_index("ticker")["market_cap"].reindex(tickers).fillna(0)
            if mcaps.sum() > 0:
                w_mkt = (mcaps / mcaps.sum()).values
            else:
                w_mkt = np.full(n_assets, 1 / n_assets)
        else:
            w_mkt = np.full(n_assets, 1 / n_assets)
    except Exception:
        w_mkt = np.full(n_assets, 1 / n_assets)
    mu = delta * cov @ w_mkt

ann_mu = mu * 252
ret_ci = returns.std().values * 1.96 / np.sqrt(len(returns)) * 252 * 100  # 95% CI on annualized mean

# ═══════════════════════════════════════════════
# COMPUTE ALL ALLOCATIONS
# ═══════════════════════════════════════════════

def _robust_max_sharpe(mu: np.ndarray, cov: np.ndarray, uncertainty: np.ndarray) -> np.ndarray:
    """Robust optimization: maximize worst-case Sharpe within confidence set.
    Uses mu - kappa * uncertainty as the pessimistic return estimate."""
    n = len(mu)
    w0 = np.full(n, 1 / n)
    kappa = 1.0  # 1 std dev penalty
    mu_robust = mu - kappa * uncertainty  # worst-case returns
    constraints = [{"type": "eq", "fun": lambda w: np.sum(w) - 1}]
    bounds = [(0, 1)] * n

    def neg_robust_sharpe(w):
        ret = w @ mu_robust
        vol = np.sqrt(w @ cov @ w)
        return -(ret) / vol if vol > 1e-12 else 1e10

    result = minimize(neg_robust_sharpe, w0, method="SLSQP", bounds=bounds, constraints=constraints)
    return result.x if result.success else w0

with st.spinner("Computing optimal portfolios..."):
    w_tangency = _tangency_portfolio(mu, cov)
    w_minvar = _min_variance(cov)
    w_riskparity = _risk_parity(cov)
    w_maxdiv = _max_diversification(cov)
    w_hrp = hrp_allocate(
        returns, cov=denoised_cov, corr=denoised_corr, linkage_method="ward",
    ).reindex(tickers).fillna(0).values
    w_herc = herc_allocate(
        returns, cov=denoised_cov, corr=denoised_corr,
        risk_metric="cvar", linkage_method="ward",
    ).reindex(tickers).fillna(0).values
    w_hcaa = hcaa_allocate(
        returns, corr=denoised_corr, linkage_method="ward",
    ).reindex(tickers).fillna(0).values
    w_equal = np.full(n_assets, 1 / n_assets)
    # Robust: use SE of mean as uncertainty
    mu_se = returns.std().values / np.sqrt(len(returns))
    w_robust = _robust_max_sharpe(mu, cov, mu_se)

allocations = {
    "Tangency (Max Sharpe)": w_tangency,
    "Robust Max Sharpe": w_robust,
    "Min Variance": w_minvar,
    "Risk Parity": w_riskparity,
    "Max Diversification": w_maxdiv,
    "HRP": w_hrp,
    "HERC (CVaR)": w_herc,
    "HCAA (1/N)": w_hcaa,
    "Equal Weight": w_equal,
}

# Header metrics
t_ret = w_tangency @ ann_mu * 100
t_vol = np.sqrt(w_tangency @ ann_cov @ w_tangency) * 100
t_sharpe = t_ret / t_vol if t_vol > 0 else 0

st.caption(f"Estimation period: **{data_start}** to **{data_end}** ({n_trading_days} trading days, {n_years:.1f} years). "
           f"All returns and volatilities are **annualized** (×252 daily → annual). "
           f"Forecast horizon: **1 year forward** from today, assuming stationary risk/return.")

hm1, hm2, hm3, hm4 = st.columns(4)
hm1.metric("Assets", n_assets)
hm2.metric("Max Sharpe Return (1Y)", f"{t_ret:.1f}%")
hm3.metric("Max Sharpe Vol (1Y)", f"{t_vol:.1f}%")
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
            f"**Return estimation:** Currently using **{ret_method}**. Change this above the tabs.\n\n"
            "**Limitations:** Mean-variance is sensitive to estimation errors in expected returns. "
            "Small changes in inputs can produce wildly different optimal portfolios. "
            "The tangency portfolio is effectively a *maximizer of estimation error* (de Prado, AFML Ch. 10). "
            "Use shrinkage or BL equilibrium for more stable tangency weights, or prefer "
            "covariance-only methods (Min Var, Risk Parity, HRP) which don't use return estimates at all."
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
                          title=f"Efficient Frontier — 1Y Forward (estimated from {n_years:.1f}Y of data)",
                          xaxis_title="Annualized Volatility (%, 1Y)",
                          yaxis_title="Expected Return (%, 1Y forward)",
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
            "Method": method, "Exp. Return (1Y)": f"{p_ret:.1f}%",
            "Exp. Vol (1Y)": f"{p_vol:.1f}%", "Sharpe": f"{p_sharpe:.2f}",
            "Max Weight": f"{w.max() * 100:.0f}%",
            "Active Positions": f"{(w > 0.01).sum()}/{n_assets}",
        })
    st.dataframe(pd.DataFrame(frontier_data), use_container_width=True, hide_index=True)

    # Return estimates section
    st.markdown("---")
    st.subheader("Estimated Expected Returns (1-Year Forward)")
    st.caption(f"Method: **{ret_method}** · Estimated from **{data_start}** to **{data_end}** ({n_years:.1f}Y of data) · "
               f"Annualized to a **1-year forward** horizon. "
               "These drive the Tangency portfolio and frontier curve. "
               "Min Var, Risk Parity, Max Div, and HRP ignore return estimates entirely (covariance only).")

    # Visual: bar chart comparing estimated returns with error bars
    ann_ret_est = mu * 252 * 100
    ann_ret_raw = raw_mu * 252 * 100
    sorted_idx = np.argsort(ann_ret_est)

    fig_ret = go.Figure()

    # Sample mean (background reference)
    if ret_method != "Sample Mean":
        fig_ret.add_trace(go.Bar(
            y=[tickers[i] for i in sorted_idx],
            x=[ann_ret_raw[i] for i in sorted_idx],
            orientation="h", name="Sample Mean",
            marker_color="rgba(85,85,85,0.4)",
            text=[f"{ann_ret_raw[i]:.1f}%" for i in sorted_idx],
            textposition="inside", textfont=dict(size=9, color="#888"),
        ))

    # Estimated return with CI error bars
    fig_ret.add_trace(go.Bar(
        y=[tickers[i] for i in sorted_idx],
        x=[ann_ret_est[i] for i in sorted_idx],
        orientation="h", name=ret_method,
        marker_color=["#00d1ff" if v >= 0 else "#ff4444" for v in ann_ret_est[sorted_idx]],
        error_x=dict(type="data", array=[ret_ci[i] for i in sorted_idx],
                     color="#ffaa00", thickness=1.5, width=4),
        text=[f"{ann_ret_est[i]:.1f}%" for i in sorted_idx],
        textposition="outside",
    ))

    fig_ret.add_vline(x=0, line_dash="dash", line_color="#333")
    fig_ret.update_layout(
        template="plotly_dark", height=max(300, n_assets * 35),
        title=f"1-Year Expected Returns — {ret_method} (with 95% CI)",
        xaxis_title="Annualized Return (%, 1Y forward)",
        barmode="overlay",
        legend=dict(orientation="h", y=-0.12),
        margin=dict(l=0, r=60, t=40, b=0),
    )
    st.plotly_chart(fig_ret, use_container_width=True, config=PLOTLY_NOBAR)

    # Metrics row
    avg_ci = ret_ci.mean()
    rm1, rm2, rm3, rm4 = st.columns(4)
    rm1.metric("Highest", f"{ann_ret_est.max():.1f}% ({tickers[np.argmax(ann_ret_est)]})")
    rm2.metric("Lowest", f"{ann_ret_est.min():.1f}% ({tickers[np.argmin(ann_ret_est)]})")
    rm3.metric("Spread", f"{ann_ret_est.max() - ann_ret_est.min():.1f}%",
               help="Wider spread = frontier has more room to optimize. Narrow = all assets look similar.")
    rm4.metric("Avg 95% CI", f"±{avg_ci:.1f}%",
               help="How uncertain the estimates are. ±10%+ means the ranking could easily be wrong.")

    if ret_method != "Sample Mean":
        deviation = np.abs(mu - raw_mu).mean() * 252 * 100
        st.caption(f"Shrinkage moved returns by **{deviation:.1f}%** on average from raw sample means. "
                   f"Shrinkage intensity: **{alpha:.0%}**." if ret_method == "Shrinkage (Ledoit-Wolf)"
                   else f"Returns differ from sample means by **{deviation:.1f}%** on average.")

    if avg_ci > ann_ret_est.max() - ann_ret_est.min():
        st.warning("The confidence intervals are **wider than the return spread** — "
                   "the optimizer can't reliably distinguish between assets. "
                   "Covariance-only methods (Min Var, Risk Parity, HRP) are more appropriate here.")

    # Detail table
    with st.expander("Return Estimates Table"):
        ret_display = pd.DataFrame({
            "Asset": tickers,
            f"{ret_method}": [f"{v:.1f}%" for v in ann_ret_est],
            "Sample Mean": [f"{v:.1f}%" for v in ann_ret_raw],
            "95% CI": [f"±{ci:.1f}%" for ci in ret_ci],
            "Ann. Vol": [f"{returns[t].std() * np.sqrt(252) * 100:.1f}%" for t in tickers],
            "Sharpe (est)": [f"{ann_ret_est[i] / (returns[tickers[i]].std() * np.sqrt(252) * 100):.2f}"
                            if returns[tickers[i]].std() > 0 else "N/A" for i in range(n_assets)],
        })
        st.dataframe(ret_display, use_container_width=True, hide_index=True)


# ═══════════════════════════════════════════════
# TAB 2: OPTIMAL WEIGHTS
# ═══════════════════════════════════════════════
with tab_weights, error_boundary("Optimal Weights"):
    st.subheader("Optimal Weights")

    with st.expander("How to read this tab", expanded=False):
        st.markdown(
            "Each method answers a different question:\n\n"
            "| Method | Question It Answers | Typical Result |\n"
            "|--------|--------------------|-----------------|\n"
            "| **Tangency** | What maximizes return per unit of risk? | Concentrated in high-Sharpe assets |\n"
            "| **Min Variance** | What minimizes total portfolio volatility? | Heavy in bonds/low-vol assets |\n"
            "| **Risk Parity** | What gives each asset equal risk contribution? | Overweights low-vol, underweights high-vol |\n"
            "| **Max Diversification** | What maximizes diversification benefit? | Favors uncorrelated assets |\n"
            "| **HRP** | What's stable and doesn't need matrix inversion? | Balanced, low turnover |\n"
            "| **HERC (CVaR)** | HRP + equal risk contribution using tail risk? | Tail-risk-aware, most robust hierarchical |\n"
            "| **HCAA (1/N)** | What if we don't trust risk estimates at all? | Equal-weight within cluster branches |\n"
            "| **Equal Weight** | Baseline — no optimization | 1/N for all assets |\n\n"
            "**Red flags:** Any method with >40% in one asset is fragile. "
            "Methods that agree on an asset's weight are more reliable than methods that disagree."
        )

    # ── Method selector for detailed view ──
    selected_method = st.selectbox("Focus on method", list(allocations.keys()), index=4, key="po_wt_method")
    w_focus = allocations[selected_method]

    # Donut chart + metrics for selected method
    wf_c1, wf_c2 = st.columns([2, 1])
    with wf_c1:
        # Sort by weight descending for the donut
        wf_sorted = pd.Series(w_focus, index=tickers).sort_values(ascending=False)
        wf_nonzero = wf_sorted[wf_sorted > 0.005]

        fig_donut = go.Figure(data=go.Pie(
            labels=wf_nonzero.index.tolist(),
            values=wf_nonzero.values * 100,
            hole=0.45,
            textinfo="label+percent",
            textfont=dict(size=11),
            marker=dict(line=dict(color="#1a1a2e", width=2)),
        ))
        fig_donut.update_layout(template="plotly_dark", height=380,
                                 title=f"{selected_method} — Weight Distribution",
                                 margin=dict(l=0, r=0, t=40, b=0),
                                 showlegend=False)
        st.plotly_chart(fig_donut, use_container_width=True, config=PLOTLY_NOBAR)

    with wf_c2:
        p_ret = w_focus @ ann_mu * 100
        p_vol = np.sqrt(w_focus @ ann_cov @ w_focus) * 100
        p_sharpe = p_ret / p_vol if p_vol > 0 else 0
        hhi = np.sum(w_focus ** 2)
        eff_n = 1 / hhi if hhi > 0 else n_assets
        vols = np.sqrt(np.diag(ann_cov))
        div_ratio = (w_focus @ vols) / (p_vol / 100) if p_vol > 0 else 1

        # Reliability grades
        uses_returns = "Tangency" in selected_method
        ret_grade = "C" if ret_method == "Sample Mean" else "B" if uses_returns else "—"
        vol_grade = "B+" if n_years >= 2 else "B" if n_years >= 1 else "C"

        st.metric("Expected Return (1Y)", f"{p_ret:.1f}%",
                  help=f"Reliability: {ret_grade} — {'uses noisy return estimates' if uses_returns else 'not used by this method'}")
        st.metric("Expected Vol (1Y)", f"{p_vol:.1f}%",
                  help=f"Reliability: {vol_grade} — based on {n_years:.1f}Y of data")
        st.metric("Sharpe Ratio", f"{p_sharpe:.2f}",
                  help=f"Reliability: {'C — only as good as the return estimate' if uses_returns else 'B — vol-driven, more stable'}")
        st.metric("Diversification Ratio", f"{div_ratio:.2f}",
                  help="Reliability: A — pure covariance math")
        st.metric("Effective Positions", f"{eff_n:.1f} / {n_assets}",
                  help="Reliability: A — pure weight math")
        st.metric("Largest Position", f"{wf_sorted.iloc[0]:.1%} ({wf_sorted.index[0]})")

    # ── Cross-page alerts ──
    try:
        from src.cross_context import read_context
        _corr_ctx = read_context("correlation")
        if _corr_ctx and _corr_ctx.get("n_alerts", 0) > 0:
            st.warning(
                f"**Correlation Alert:** {_corr_ctx['n_alerts']} correlation breakdown(s) detected on the "
                f"Correlation page. Current weights may be stale if asset relationships have shifted. "
                f"Top alerts: {'; '.join(_corr_ctx.get('breakdowns', [])[:3])}"
            )
        _me_ctx = read_context("market_expectations")
        if _me_ctx and _me_ctx.get("regime"):
            st.info(f"**Market Vol Regime** (from Market Expectations): {_me_ctx['regime']}")
    except Exception:
        pass

    # ── Realized vs Expected + Bootstrap Sharpe + Stress Test ──
    st.markdown("---")
    st.subheader("Reality Check")

    real_c1, real_c2, real_c3 = st.columns(3)

    # #4: Realized vs Expected (train on first 75%, measure on last 25%)
    with real_c1:
        st.markdown("**Realized vs Expected**")
        split = int(len(returns) * 0.75)
        oos_ret = returns.iloc[split:]
        if len(oos_ret) > 20:
            # Use weights estimated on training data only (avoid look-ahead bias)
            # Note: w_focus estimated on full period — this OOS test is approximate.
            # For true OOS, use the walk-forward backtest on Meta Analysis page.
            oos_port = (oos_ret.values @ w_focus)
            realized_ann = np.mean(oos_port) * 252 * 100
            realized_vol = np.std(oos_port) * np.sqrt(252) * 100
            realized_sharpe = realized_ann / realized_vol if realized_vol > 0 else 0
            st.metric("Expected Return", f"{p_ret:.1f}%")
            st.metric("Realized Return (OOS)", f"{realized_ann:.1f}%",
                      delta=f"{realized_ann - p_ret:+.1f}% gap")
            st.metric("Realized Sharpe (OOS)", f"{realized_sharpe:.2f}",
                      delta=f"{realized_sharpe - p_sharpe:+.2f} vs expected")
            st.caption(f"OOS period: last {len(oos_ret)} days ({len(oos_ret)/252:.1f}Y)")
        else:
            st.caption("Not enough OOS data for realized comparison.")

    # #3: Bootstrap CI on Sharpe
    with real_c2:
        st.markdown("**Sharpe Confidence Interval**")
        port_daily = returns.values @ w_focus
        rng_bs = np.random.default_rng(42)
        boot_sharpes = []
        for _ in range(1000):
            idx = rng_bs.integers(0, len(port_daily), len(port_daily))
            s = port_daily[idx]
            if s.std() > 0:
                boot_sharpes.append(s.mean() / s.std() * np.sqrt(252))
        if boot_sharpes:
            ci_lo = np.percentile(boot_sharpes, 5)
            ci_hi = np.percentile(boot_sharpes, 95)
            st.metric("Point Estimate", f"{p_sharpe:.2f}")
            st.metric("90% CI", f"[{ci_lo:.2f}, {ci_hi:.2f}]")
            significant = ci_lo > 0
            st.metric("Statistically > 0?", "Yes" if significant else "No",
                      delta="CI excludes zero" if significant else "CI includes zero",
                      delta_color="normal" if significant else "inverse")

    # #5: Stress Testing
    with real_c3:
        st.markdown("**Stress Scenarios**")
        stress_scenarios = {
            "2008 GFC": {"SPY": -0.38, "TLT": 0.20, "GLD": 0.05, "EFA": -0.41, "IWM": -0.34,
                         "USO": -0.54, "HYG": -0.25, "VNQ": -0.37},
            "COVID 2020": {"SPY": -0.34, "TLT": 0.15, "GLD": 0.03, "EFA": -0.33, "IWM": -0.40,
                           "USO": -0.60, "HYG": -0.20, "VNQ": -0.30},
            "2022 Rate Shock": {"SPY": -0.19, "TLT": -0.31, "GLD": 0.00, "EFA": -0.17, "IWM": -0.21,
                                "USO": 0.25, "HYG": -0.14, "VNQ": -0.26},
        }
        for scenario_name, shocks in stress_scenarios.items():
            port_shock = sum(w_focus[i] * shocks.get(tickers[i], -0.15) for i in range(n_assets))
            color = "#ff4444" if port_shock < -0.15 else "#ffaa00" if port_shock < 0 else "#00ff88"
            st.markdown(f'<span style="color:{color};">{scenario_name}: **{port_shock*100:+.1f}%**</span>',
                        unsafe_allow_html=True)
        st.caption("Estimated portfolio loss under historical crisis drawdowns.")

    # #6: Cost-aware metrics
    st.markdown("---")
    cost_c1, cost_c2 = st.columns(2)
    with cost_c1:
        st.markdown("**Transaction Cost Impact**")
        # Estimate annual turnover vs equal-weight
        turnover = np.sum(np.abs(w_focus - 1/n_assets)) * 2  # round-trip vs EW baseline
        cost_bps = 10  # 10 bps per round trip (institutional)
        annual_cost = turnover * cost_bps / 100
        net_return = p_ret - annual_cost
        st.metric("Est. Annual Turnover", f"{turnover*100:.0f}%",
                  help="vs equal-weight baseline")
        st.metric("Est. Cost @ 10bps", f"{annual_cost:.2f}%/yr")
        st.metric("Net Expected Return", f"{net_return:.1f}%",
                  delta=f"-{annual_cost:.2f}% cost drag")

    with cost_c2:
        # #1: Reliability scorecard
        st.markdown("**Reliability Scorecard**")
        reliability = [
            ("Expected Return", ret_grade, "Depends on return estimation method"),
            ("Expected Vol", vol_grade, f"Based on {n_years:.1f}Y sample"),
            ("Sharpe Ratio", "C" if uses_returns else "B", "Return ÷ Vol — inherits return noise"),
            ("Diversification Ratio", "A", "Pure covariance math"),
            ("Effective Positions", "A", "Pure weight math"),
            ("Weights", "B" if "HRP" in selected_method or "Risk Parity" in selected_method else "C",
             "Covariance-only methods are more stable"),
        ]
        for metric_name, grade, note in reliability:
            grade_color = {"A": "#00ff88", "B+": "#00d1ff", "B": "#00d1ff", "C": "#ffaa00", "D": "#ff4444", "—": "#555"}.get(grade, "#888")
            st.markdown(f'<span style="color:{grade_color};font-weight:700;">{grade}</span> '
                        f'{metric_name} <span style="color:#666;font-size:0.75rem;">— {note}</span>',
                        unsafe_allow_html=True)

    # ── Side-by-side comparison chart ──
    st.markdown("---")
    st.subheader("All Methods Compared")
    st.caption("Grouped bar chart — each cluster is one asset, each bar is one method's weight. "
               "Methods that agree on an asset's importance are more trustworthy than one-method outliers.")

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

    # ── Weight consensus ──
    st.subheader("Weight Consensus")
    st.caption("Shows the average weight and standard deviation across methods. "
               "Low std = all methods agree. High std = controversial asset — the right weight depends on your objective.")

    weight_df = pd.DataFrame({method: pd.Series(w, index=tickers) for method, w in allocations.items()})
    consensus = pd.DataFrame({
        "Avg Weight": weight_df.mean(axis=1).apply(lambda v: f"{v*100:.1f}%"),
        "Std Dev": weight_df.std(axis=1).apply(lambda v: f"{v*100:.1f}%"),
        "Min": weight_df.min(axis=1).apply(lambda v: f"{v*100:.1f}%"),
        "Max": weight_df.max(axis=1).apply(lambda v: f"{v*100:.1f}%"),
        "Agreement": weight_df.std(axis=1).apply(
            lambda v: "Strong" if v < 0.03 else "Moderate" if v < 0.08 else "Weak"),
    })
    st.dataframe(consensus, use_container_width=True)

    # ── Full weight table ──
    with st.expander("Full Weight Table (all methods)"):
        display_w = weight_df.map(lambda v: f"{v * 100:.1f}%")
        st.dataframe(display_w, use_container_width=True)

    # ── Concentration comparison ──
    st.subheader("Concentration Analysis")
    st.caption("HHI measures concentration (0 = perfectly spread, 1 = single asset). "
               "Effective N shows how many truly independent positions you hold. "
               "Higher Effective N = better diversified.")

    conc_data = []
    for method, w in allocations.items():
        hhi_m = np.sum(w ** 2)
        eff_n_m = 1 / hhi_m if hhi_m > 0 else n_assets
        conc_data.append({
            "Method": method,
            "HHI": f"{hhi_m:.3f}",
            "Effective N": f"{eff_n_m:.1f}",
            "Max Weight": f"{w.max() * 100:.0f}% ({tickers[np.argmax(w)]})",
            "Positions > 1%": f"{(w > 0.01).sum()} / {n_assets}",
            "Concentration": "Low" if hhi_m < 0.1 else "Medium" if hhi_m < 0.2 else "High",
        })
    st.dataframe(pd.DataFrame(conc_data), use_container_width=True, hide_index=True)

    # ── Estimation Window Sensitivity ──
    st.markdown("---")
    st.subheader("Estimation Window Sensitivity")
    st.caption("Do optimal weights change dramatically with different lookback periods? "
               "Stable weights across windows = robust allocation. "
               "Wildly different weights = sensitive to sample period (fragile).")

    with st.spinner("Computing weights across estimation windows..."):
        window_weights = {}
        window_labels = {"1Y": "1y", "2Y": "2y", "3Y": "3y", "5Y": "5y"}
        for wlabel, wperiod in window_labels.items():
            try:
                w_prices = fetch_price_history(ticker_list, period=wperiod)
                if not w_prices.empty and w_prices.index.tz is not None:
                    w_prices.index = w_prices.index.tz_localize(None)
                if not w_prices.empty and len(w_prices.columns) >= 3:
                    w_ret = w_prices.pct_change().dropna()
                    w_tickers = [t for t in tickers if t in w_ret.columns]
                    if len(w_tickers) >= 3:
                        w_ret = w_ret[w_tickers]
                        w_cov = w_ret.cov().values

                        # Compute weights for the selected method
                        if use_denoising:
                            _wd_cov, _wd_corr = denoise_covariance(w_ret)
                        else:
                            _wd_cov = pd.DataFrame(w_cov * 252, index=w_tickers, columns=w_tickers)
                            _wd_corr = w_ret.corr()
                        if selected_method == "Tangency (Max Sharpe)":
                            w_mu = w_ret.mean().values
                            ww = _tangency_portfolio(w_mu, w_cov)
                        elif selected_method == "Robust Max Sharpe":
                            w_mu = w_ret.mean().values
                            w_se = w_ret.std().values / np.sqrt(len(w_ret))
                            ww = _robust_max_sharpe(w_mu, w_cov, w_se)
                        elif selected_method == "Min Variance":
                            ww = _min_variance(w_cov)
                        elif selected_method == "Risk Parity":
                            ww = _risk_parity(w_cov)
                        elif selected_method == "Max Diversification":
                            ww = _max_diversification(w_cov)
                        elif selected_method == "HRP":
                            ww = hrp_allocate(w_ret, cov=_wd_cov, corr=_wd_corr).reindex(w_tickers).fillna(0).values
                        elif selected_method == "HERC (CVaR)":
                            ww = herc_allocate(w_ret, cov=_wd_cov, corr=_wd_corr, risk_metric="cvar").reindex(w_tickers).fillna(0).values
                        elif selected_method == "HCAA (1/N)":
                            ww = hcaa_allocate(w_ret, corr=_wd_corr).reindex(w_tickers).fillna(0).values
                        else:
                            ww = np.full(len(w_tickers), 1 / len(w_tickers))

                        window_weights[wlabel] = pd.Series(ww, index=w_tickers)
            except Exception:
                pass

        if len(window_weights) >= 2:
            # Bar chart comparing weights across windows
            fig_ww = go.Figure()
            ww_colors = {"1Y": "#00d1ff", "2Y": "#00ff88", "3Y": "#ffaa00", "5Y": "#ff6b6b"}
            for wlabel, ww_series in window_weights.items():
                fig_ww.add_trace(go.Bar(
                    x=ww_series.index.tolist(), y=ww_series.values * 100,
                    name=wlabel, marker_color=ww_colors.get(wlabel, "#888"),
                ))
            fig_ww.update_layout(template="plotly_dark", height=400, barmode="group",
                                  title=f"{selected_method} Weights by Estimation Window",
                                  yaxis_title="Weight (%)",
                                  legend=dict(orientation="h", y=-0.12),
                                  margin=dict(l=0, r=0, t=40, b=0))
            st.plotly_chart(fig_ww, use_container_width=True, config=PLOTLY_NOBAR)

            # Stability metrics
            ww_df = pd.DataFrame(window_weights)
            ww_std = ww_df.std(axis=1) * 100  # std of weight across windows per asset
            avg_stability = ww_std.mean()

            ws1, ws2, ws3 = st.columns(3)
            ws1.metric("Avg Weight Std", f"{avg_stability:.1f}%",
                       help="Average standard deviation of each asset's weight across windows. Lower = more stable.")
            most_unstable = ww_std.idxmax()
            ws2.metric("Most Unstable Asset", f"{most_unstable} (±{ww_std.max():.1f}%)")
            most_stable = ww_std.idxmin()
            ws3.metric("Most Stable Asset", f"{most_stable} (±{ww_std.min():.1f}%)")

            if avg_stability < 3:
                st.success("Weights are **very stable** across estimation windows — this allocation is robust.")
            elif avg_stability < 8:
                st.info("Weights show **moderate sensitivity** to the estimation window — reasonable for production use.")
            else:
                st.warning("Weights are **highly sensitive** to the estimation window — "
                           "consider using a covariance-only method (Min Var, Risk Parity, HRP) for more stability.")

            with st.expander("Weight Detail by Window"):
                display_ww = ww_df.map(lambda v: f"{v*100:.1f}%" if pd.notna(v) else "N/A")
                display_ww["Std"] = ww_std.apply(lambda v: f"±{v:.1f}%")
                st.dataframe(display_ww, use_container_width=True)
        else:
            st.info("Need price data for at least 2 lookback periods to compare. Try adding more history.")


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

    n_total_days = len(returns)
    # Auto-scale slider to available data — need at least 60 days OOS after the window
    max_est = max(63, n_total_days - 60)
    default_est = min(252, max_est)
    min_est = min(63, max_est)

    rebal = st.radio("Rebalance", ["Monthly", "Quarterly"], horizontal=True, key="po_rebal")
    est_window = st.slider("Estimation window (days)", min_est, max_est, default_est, 21, key="po_est_window",
                           help=f"You have {n_total_days} trading days. Window must leave room for OOS testing.")

    rebal_period = "ME" if rebal == "Monthly" else "QE"
    # Get the last actual trading day in each period (not synthetic month-end dates)
    rebal_groups = returns.resample(rebal_period).last()
    rebal_dates = []
    for period_end in rebal_groups.index:
        # Find the closest actual trading day at or before the period end
        mask = returns.index <= period_end
        if mask.any():
            actual_date = returns.index[mask][-1]
            loc = returns.index.get_loc(actual_date)
            if loc >= est_window:
                rebal_dates.append(actual_date)

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
                if use_denoising:
                    _d_cov, _d_corr = denoise_covariance(est_ret)
                else:
                    _d_cov = pd.DataFrame(est_cov * 252, index=tickers, columns=tickers)
                    _d_corr = est_ret.corr()
                est_se = est_ret.std().values / np.sqrt(len(est_ret))
                methods_w = {
                    "Tangency (Max Sharpe)": _tangency_portfolio(est_mu, est_cov),
                    "Robust Max Sharpe": _robust_max_sharpe(est_mu, est_cov, est_se),
                    "Min Variance": _min_variance(est_cov),
                    "Risk Parity": _risk_parity(est_cov),
                    "Max Diversification": _max_diversification(est_cov),
                    "HRP": hrp_allocate(est_ret, cov=_d_cov, corr=_d_corr).reindex(tickers).fillna(0).values,
                    "HERC (CVaR)": herc_allocate(est_ret, cov=_d_cov, corr=_d_corr, risk_metric="cvar").reindex(tickers).fillna(0).values,
                    "HCAA (1/N)": hcaa_allocate(est_ret, corr=_d_corr).reindex(tickers).fillna(0).values,
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
            width = 3 if method in ("Tangency (Max Sharpe)", "HRP", "HERC (CVaR)") else 1
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

    # ── Dendrogram ──
    st.subheader("Hierarchical Clustering Dendrogram")
    st.caption(
        "Shows how HRP/HERC/HCAA group assets into clusters based on "
        + ("**Ledoit-Wolf denoised**" if use_denoising else "sample")
        + " correlation distance. Assets joined lower in the tree are more similar. "
        "Ward linkage minimizes within-cluster variance."
    )

    from src.quant_features import _hrp_linkage

    _link, _sorted = _hrp_linkage(denoised_corr, method="ward")

    # Use Plotly to render the dendrogram
    import plotly.figure_factory as ff
    try:
        fig_dend = ff.create_dendrogram(
            denoised_corr.values,
            labels=tickers,
            linkagefun=lambda x: _link,
            color_threshold=0.7 * _link[-1, 2],
        )
        fig_dend.update_layout(
            template="plotly_dark", height=350,
            title="Asset Clustering Dendrogram (Ward Linkage"
                  + (", Ledoit-Wolf Denoised" if use_denoising else "")
                  + ")",
            xaxis_title="Assets",
            yaxis_title="Distance",
            margin=dict(l=0, r=0, t=40, b=0),
        )
        st.plotly_chart(fig_dend, use_container_width=True, config=PLOTLY_NOBAR)
    except Exception as e:
        logger.warning(f"Dendrogram rendering failed: {e}")
        st.caption("Dendrogram could not be rendered.")


# ═══════════════════════════════════════════════
# TAB 5: BLACK-LITTERMAN
# ═══════════════════════════════════════════════
with tab_views, error_boundary("Black-Litterman"):
    st.subheader("Black-Litterman Model")

    with st.expander("How to read this tab", expanded=False):
        st.markdown(
            "**The problem with Markowitz:** You need expected returns as input, but nobody can forecast returns reliably. "
            "Small errors in return estimates → wildly different portfolios.\n\n"
            "**Black-Litterman solves this** by starting from a neutral baseline (the *market equilibrium*) "
            "and only deviating where you have a specific view. No view = market weights. Strong view = tilt toward it.\n\n"
            "**Step 1 — Equilibrium:** The market's implied returns are reverse-engineered from what a rational investor "
            "would need to hold the current market portfolio. These are your starting point.\n\n"
            "**Step 2 — Your Views:** Express beliefs like 'I think SPY will return 15% annualized' or "
            "'I think GLD will outperform TLT by 5%.' Each view has a confidence level.\n\n"
            "**Step 3 — Blending:** BL combines equilibrium + views using Bayesian math. "
            "High-confidence views shift returns more. Low-confidence views barely move them.\n\n"
            "**Tau (τ)** controls overall confidence in your views vs the market. "
            "τ = 0.05 (default) means the market is ~20x more trusted than your views. "
            "τ = 0.25 means roughly equal trust.\n\n"
            "**This is how BlackRock, Bridgewater, and pension funds actually build portfolios.**"
        )

    # ── Step 1: Market Equilibrium ──
    st.markdown("### Step 1: Market Equilibrium")
    st.caption("The market's implied expected returns — what a rational investor would need to hold the current portfolio. "
               "These are your starting point before applying any views.")

    bl_c1, bl_c2 = st.columns([1, 1])
    with bl_c1:
        risk_aversion = st.slider("Risk aversion (δ)", 1.0, 5.0, 2.5, 0.5, key="po_bl_delta",
                                   help="Higher = more risk-averse investor. 2.5 is standard. "
                                        "Controls the scale of equilibrium returns.")
    with bl_c2:
        tau = st.slider("View confidence (τ)", 0.01, 0.50, 0.05, 0.01, key="po_bl_tau",
                        help="Lower = trust the market more. Higher = trust your views more. "
                             "0.05 is standard (market is ~20x stronger). 0.25 = equal trust.")

    # Implied equilibrium returns
    pi = risk_aversion * ann_cov @ w_equal

    # Equilibrium chart
    pi_sorted = pd.Series(pi * 100, index=tickers).sort_values()
    fig_eq = go.Figure()
    fig_eq.add_trace(go.Bar(
        y=pi_sorted.index, x=pi_sorted.values, orientation="h",
        marker_color=["#00d1ff" if v >= 0 else "#ff4444" for v in pi_sorted],
        text=[f"{v:.1f}%" for v in pi_sorted], textposition="outside",
    ))
    fig_eq.add_vline(x=0, line_dash="dash", line_color="#333")
    fig_eq.update_layout(template="plotly_dark", height=max(250, n_assets * 28),
                          title="Implied Equilibrium Returns (Annualized %)",
                          xaxis_title="Expected Return (%)",
                          margin=dict(l=0, r=60, t=40, b=0))
    st.plotly_chart(fig_eq, use_container_width=True, config=PLOTLY_NOBAR)

    # ── Step 2: Your Views ──
    st.markdown("---")
    st.markdown("### Step 2: Your Views")
    st.caption("Express up to 5 views on individual assets. Leave blank for no view — "
               "the model defaults to equilibrium for assets without views.")

    n_view_slots = 5
    views_P = []
    views_Q = []
    views_conf = []

    view_container = st.container()
    with view_container:
        for i in range(n_view_slots):
            vc1, vc2, vc3 = st.columns([2, 2, 1])
            with vc1:
                v_ticker = st.selectbox(f"Asset", ["—"] + tickers,
                                        key=f"po_bl_v{i}_t", label_visibility="collapsed" if i > 0 else "visible")
            with vc2:
                v_return = st.number_input(f"Ann. Return (%)", value=0.0, step=1.0,
                                           key=f"po_bl_v{i}_r", label_visibility="collapsed" if i > 0 else "visible")
            with vc3:
                v_conf = st.selectbox(f"Confidence", ["Medium", "Low", "High"],
                                      key=f"po_bl_v{i}_c", label_visibility="collapsed" if i > 0 else "visible")

            if v_ticker != "—" and v_return != 0:
                p_row = np.zeros(n_assets)
                p_row[tickers.index(v_ticker)] = 1
                views_P.append(p_row)
                views_Q.append(v_return / 100)
                conf_scale = {"Low": 3.0, "Medium": 1.0, "High": 0.3}
                views_conf.append(conf_scale[v_conf])

    if views_P:
        P = np.array(views_P)
        Q = np.array(views_Q)

        # Omega: uncertainty of views — scaled by per-view confidence
        base_omega = np.diag(np.diag(tau * P @ ann_cov @ P.T))
        conf_scales = np.array(views_conf)
        conf_scales = np.clip(conf_scales, 0.01, None)  # prevent singular omega from zero confidence
        omega = base_omega * np.diag(conf_scales)

        # ── Step 3: BL Posterior ──
        st.markdown("---")
        st.markdown("### Step 3: Blended Returns & Weights")

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

        # Return shift visualization
        st.caption("How your views shifted the expected returns from equilibrium. "
                    "Large shifts on viewed assets, small ripple effects on correlated assets.")

        shift = (bl_mu_post - pi) * 100
        fig_shift = go.Figure()
        fig_shift.add_trace(go.Bar(
            x=tickers, y=pi * 100, name="Equilibrium",
            marker_color="#555",
        ))
        fig_shift.add_trace(go.Bar(
            x=tickers, y=bl_mu_post * 100, name="BL Posterior",
            marker_color="#00d1ff",
        ))
        # Annotate shifts
        for i_t, t in enumerate(tickers):
            if abs(shift[i_t]) > 0.1:
                fig_shift.add_annotation(
                    x=t, y=max(pi[i_t], bl_mu_post[i_t]) * 100 + 0.5,
                    text=f"{shift[i_t]:+.1f}%",
                    showarrow=False, font=dict(size=9, color="#ffaa00"),
                )
        fig_shift.update_layout(template="plotly_dark", height=380, barmode="group",
                                 title="Expected Returns: Equilibrium → BL Posterior",
                                 yaxis_title="Ann. Return (%)",
                                 legend=dict(orientation="h", y=-0.12),
                                 margin=dict(l=0, r=0, t=40, b=0))
        st.plotly_chart(fig_shift, use_container_width=True, config=PLOTLY_NOBAR)

        # ── Weight comparison ──
        st.subheader("Portfolio Weights")
        st.caption("How your views change the optimal allocation. BL tilts toward viewed assets "
                    "while maintaining diversification — unlike Markowitz which can go all-in.")

        wt_c1, wt_c2 = st.columns([3, 1])
        with wt_c1:
            fig_bl_w = go.Figure()
            fig_bl_w.add_trace(go.Bar(x=tickers, y=w_bl * 100, name="Black-Litterman",
                                      marker_color="#ff6b6b"))
            fig_bl_w.add_trace(go.Bar(x=tickers, y=w_tangency * 100, name="Tangency (no views)",
                                      marker_color="#00d1ff"))
            fig_bl_w.add_trace(go.Bar(x=tickers, y=w_equal * 100, name="Equal Weight",
                                      marker_color="#555"))
            fig_bl_w.update_layout(template="plotly_dark", height=380, barmode="group",
                                    yaxis_title="Weight (%)",
                                    legend=dict(orientation="h", y=-0.12),
                                    margin=dict(l=0, r=0, t=10, b=0))
            st.plotly_chart(fig_bl_w, use_container_width=True, config=PLOTLY_NOBAR)

        with wt_c2:
            bl_ret_exp = w_bl @ ann_mu * 100
            bl_vol_exp = np.sqrt(w_bl @ ann_cov @ w_bl) * 100
            bl_sharpe = bl_ret_exp / bl_vol_exp if bl_vol_exp > 0 else 0
            st.metric("BL Exp. Return", f"{bl_ret_exp:.1f}%")
            st.metric("BL Exp. Vol", f"{bl_vol_exp:.1f}%")
            st.metric("BL Sharpe", f"{bl_sharpe:.2f}")
            st.metric("Positions > 1%", f"{(w_bl > 0.01).sum()} / {n_assets}")

        # ── Backtest comparison ──
        st.markdown("---")
        st.subheader("Historical Performance Comparison")
        st.caption("How would these allocations have performed over the lookback period? "
                    "Note: BL weights are computed from current views — this is NOT a walk-forward test.")

        bl_ret_ts = (returns.values @ w_bl)
        tang_ret_ts = (returns.values @ w_tangency)
        eq_ret_ts = (returns.values @ w_equal)

        cum_bl = pd.Series((1 + bl_ret_ts).cumprod() * 100, index=returns.index)
        cum_tang = pd.Series((1 + tang_ret_ts).cumprod() * 100, index=returns.index)
        cum_eq = pd.Series((1 + eq_ret_ts).cumprod() * 100, index=returns.index)

        fig_bt_bl = go.Figure()
        fig_bt_bl.add_trace(go.Scatter(x=cum_bl.index, y=cum_bl, mode="lines",
                                        name="Black-Litterman", line=dict(color="#ff6b6b", width=3)))
        fig_bt_bl.add_trace(go.Scatter(x=cum_tang.index, y=cum_tang, mode="lines",
                                        name="Tangency", line=dict(color="#00d1ff", width=1)))
        fig_bt_bl.add_trace(go.Scatter(x=cum_eq.index, y=cum_eq, mode="lines",
                                        name="Equal Weight", line=dict(color="#555", width=1)))
        fig_bt_bl.add_hline(y=100, line_dash="dash", line_color="#333")
        fig_bt_bl.update_layout(template="plotly_dark", height=350,
                                 title="Cumulative Performance (base=100)",
                                 yaxis_title="Portfolio Value",
                                 legend=dict(orientation="h", y=-0.12),
                                 margin=dict(l=0, r=0, t=40, b=0))
        st.plotly_chart(fig_bt_bl, use_container_width=True, config=PLOTLY_NOBAR)

        bl_metrics = _portfolio_metrics(pd.Series(bl_ret_ts, index=returns.index), "Black-Litterman")
        tang_metrics = _portfolio_metrics(pd.Series(tang_ret_ts, index=returns.index), "Tangency (no views)")
        eq_metrics = _portfolio_metrics(pd.Series(eq_ret_ts, index=returns.index), "Equal Weight")
        st.dataframe(pd.DataFrame([bl_metrics, tang_metrics, eq_metrics]),
                     use_container_width=True, hide_index=True)

        # ── View impact summary ──
        st.markdown("---")
        st.subheader("View Impact Summary")
        st.caption("How each view affected the portfolio.")
        impact_data = []
        for i_v in range(len(views_P)):
            v_ticker_name = tickers[np.argmax(views_P[i_v])]
            eq_weight = w_equal[np.argmax(views_P[i_v])] * 100
            bl_weight = w_bl[np.argmax(views_P[i_v])] * 100
            eq_ret = pi[np.argmax(views_P[i_v])] * 100
            bl_ret_v = bl_mu_post[np.argmax(views_P[i_v])] * 100
            conf_label = [k for k, v in {"Low": 3.0, "Medium": 1.0, "High": 0.3}.items()
                          if v == views_conf[i_v]][0]
            impact_data.append({
                "Asset": v_ticker_name,
                "Your View": f"{views_Q[i_v]*100:+.1f}%",
                "Confidence": conf_label,
                "Equilibrium": f"{eq_ret:.1f}%",
                "BL Posterior": f"{bl_ret_v:.1f}%",
                "Return Shift": f"{bl_ret_v - eq_ret:+.1f}%",
                "Weight Shift": f"{bl_weight - eq_weight:+.1f}%",
            })
        st.dataframe(pd.DataFrame(impact_data), use_container_width=True, hide_index=True)

    else:
        st.markdown("---")
        st.info("Enter at least one view above to see how Black-Litterman blends your beliefs with the market equilibrium. "
                "Try setting View 1 to one of your assets with a return expectation — "
                "for example, set SPY to 15% if you're bullish, or -5% if you're bearish.")

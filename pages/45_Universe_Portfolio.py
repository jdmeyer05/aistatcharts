"""
Universe Portfolio — Multi-Group Portfolio Construction Engine

Backtests all 15 preset sector/asset groups independently, finds the best
allocation method per group, then constructs a two-layer hierarchical portfolio.
Full walk-forward analysis with equity curves, drawdowns, and statistical tests.
"""
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from scipy.optimize import minimize
from scipy.stats import norm
import logging

from src.layout import setup_page, error_boundary
from src.styles import COLORS
from src.quant_features import hrp_allocate, herc_allocate, hcaa_allocate, denoise_covariance

logger = logging.getLogger(__name__)
setup_page("45_Universe_Portfolio")

st.title("Universe Portfolio")
st.markdown(
    "Backtest 15 preset groups across 9 allocation methods, then build an "
    "optimized hierarchical portfolio with full performance analytics."
)

PLOTLY_NOBAR = {"displayModeBar": False}

BASE_METHODS = [
    "Tangency", "Robust Sharpe", "Min Variance", "Risk Parity",
    "Max Diversification", "HRP", "HERC (CVaR)", "HCAA (1/N)", "Equal Weight",
]

METHOD_COLORS = {
    "Tangency": "#00d1ff", "Robust Sharpe": "#00e0d0", "Min Variance": "#00ff88",
    "Risk Parity": "#ffaa00", "Max Diversification": "#ff00ff", "HRP": "#88ccff",
    "HERC (CVaR)": "#cc88ff", "HCAA (1/N)": "#66aacc", "Equal Weight": "#888888",
}

COLOR_CYCLE = ["#00d1ff", "#00ff88", "#ffaa00", "#ff6b6b", "#cc88ff",
               "#ff00ff", "#66aacc", "#88ccff", "#00e0d0", "#ff9966",
               "#66ffcc", "#ffcc66", "#9966ff", "#ff66cc", "#99ff66"]

PRESET_GROUPS = {
    "Multi-Asset": ["SPY", "TLT", "GLD", "EFA", "IWM", "USO", "HYG", "VNQ"],
    "Sector ETFs": ["XLE", "XLF", "XLK", "XLV", "XLI", "XLC", "XLY", "XLP", "XLU", "XLB", "XLRE"],
    "Energy": ["XOM", "CVX", "COP", "EOG", "SLB", "MPC", "OXY", "PSX", "VLO", "DVN"],
    "Technology": ["AAPL", "MSFT", "NVDA", "AVGO", "CRM", "ORCL", "AMD", "ADBE", "ACN", "CSCO"],
    "Financials": ["JPM", "BAC", "WFC", "GS", "MS", "BLK", "SCHW", "C", "AXP", "MMC"],
    "Healthcare": ["UNH", "LLY", "JNJ", "ABBV", "MRK", "TMO", "ABT", "AMGN", "DHR", "PFE"],
    "Industrials": ["GE", "CAT", "UNP", "HON", "RTX", "DE", "LMT", "BA", "ETN", "ADP"],
    "Communication": ["META", "GOOGL", "NFLX", "T", "CMCSA", "VZ", "DIS", "TMUS", "EA", "CHTR"],
    "Consumer Disc": ["AMZN", "TSLA", "HD", "MCD", "NKE", "LOW", "BKNG", "SBUX", "TJX", "CMG"],
    "Consumer Staples": ["PG", "COST", "WMT", "KO", "PEP", "PM", "MDLZ", "MO", "CL", "STZ"],
    "Utilities": ["NEE", "SO", "DUK", "CEG", "SRE", "AEP", "D", "EXC", "XEL", "PEG"],
    "Materials": ["LIN", "SHW", "APD", "ECL", "FCX", "NUE", "NEM", "VMC", "MLM", "DOW"],
    "Real Estate": ["PLD", "AMT", "EQIX", "SPG", "PSA", "O", "WELL", "DLR", "VICI", "CCI"],
    "Mega Caps": ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "BRK-B", "JPM", "V"],
    "Global Macro": ["SPY", "EFA", "EEM", "TLT", "IEF", "GLD", "USO", "UNG", "DBA", "UUP"],
}


# ═══════════════════════════════════════════════
# OPTIMIZATION FUNCTIONS
# ═══════════════════════════════════════════════

def _tangency(mu, cov):
    n = len(mu)
    w0 = np.full(n, 1 / n)
    def neg_sharpe(w):
        vol = np.sqrt(w @ cov @ w)
        return -(w @ mu) / vol if vol > 1e-12 else 1e10
    r = minimize(neg_sharpe, w0, method="SLSQP",
                 bounds=[(0, 1)] * n,
                 constraints=[{"type": "eq", "fun": lambda w: np.sum(w) - 1}])
    return r.x if r.success else w0

def _robust_sharpe(mu, cov, se):
    n = len(mu)
    w0 = np.full(n, 1 / n)
    mu_r = mu - se
    def obj(w):
        vol = np.sqrt(w @ cov @ w)
        return -(w @ mu_r) / vol if vol > 1e-12 else 1e10
    r = minimize(obj, w0, method="SLSQP",
                 bounds=[(0, 1)] * n,
                 constraints=[{"type": "eq", "fun": lambda w: np.sum(w) - 1}])
    return r.x if r.success else w0

def _min_var(cov):
    n = cov.shape[0]
    w0 = np.full(n, 1 / n)
    r = minimize(lambda w: np.sqrt(w @ cov @ w), w0, method="SLSQP",
                 bounds=[(0, 1)] * n,
                 constraints=[{"type": "eq", "fun": lambda w: np.sum(w) - 1}])
    return r.x if r.success else w0

def _risk_parity(cov):
    n = cov.shape[0]
    w0 = np.full(n, 1 / n)
    min_w = max(0.001, 1 / (n * 5))
    def obj(w):
        pv = np.sqrt(w @ cov @ w)
        if pv == 0: return 0
        rc = w * (cov @ w / pv)
        return np.sum((rc - pv / n) ** 2)
    r = minimize(obj, w0, method="SLSQP",
                 bounds=[(min_w, 1)] * n,
                 constraints=[{"type": "eq", "fun": lambda w: np.sum(w) - 1}])
    return r.x if r.success else w0

def _max_div(cov):
    n = cov.shape[0]
    vols = np.sqrt(np.diag(cov))
    w0 = np.full(n, 1 / n)
    def obj(w):
        pv = np.sqrt(w @ cov @ w)
        return -(w @ vols) / pv if pv > 0 else 0
    r = minimize(obj, w0, method="SLSQP",
                 bounds=[(0, 1)] * n,
                 constraints=[{"type": "eq", "fun": lambda w: np.sum(w) - 1}])
    return r.x if r.success else w0


def _compute_all_weights(est_ret, tickers, use_dn):
    n = len(tickers)
    est_mu = est_ret.mean().values
    est_cov = est_ret.cov().values
    mu_se = est_ret.std().values / np.sqrt(len(est_ret))
    if use_dn:
        dn_cov, dn_corr = denoise_covariance(est_ret)
    else:
        dn_cov = pd.DataFrame(est_cov * 252, index=tickers, columns=tickers)
        dn_corr = est_ret.corr()
    return {
        "Tangency": _tangency(est_mu, est_cov),
        "Robust Sharpe": _robust_sharpe(est_mu, est_cov, mu_se),
        "Min Variance": _min_var(est_cov),
        "Risk Parity": _risk_parity(est_cov),
        "Max Diversification": _max_div(est_cov),
        "HRP": hrp_allocate(est_ret, cov=dn_cov, corr=dn_corr).reindex(tickers).fillna(0).values,
        "HERC (CVaR)": herc_allocate(est_ret, cov=dn_cov, corr=dn_corr, risk_metric="cvar").reindex(tickers).fillna(0).values,
        "HCAA (1/N)": hcaa_allocate(est_ret, corr=dn_corr).reindex(tickers).fillna(0).values,
        "Equal Weight": np.full(n, 1 / n),
    }


def _portfolio_metrics(daily_returns, name, benchmark_returns=None):
    ann_ret = daily_returns.mean() * 252
    ann_vol = daily_returns.std() * np.sqrt(252)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
    cum = (1 + daily_returns).cumprod()
    max_dd = ((cum / cum.cummax()) - 1).min()
    neg_ret = daily_returns[daily_returns < 0]
    downside_vol = neg_ret.std() * np.sqrt(252) if len(neg_ret) > 1 else ann_vol
    sortino = ann_ret / downside_vol if downside_vol > 0 else 0
    calmar = ann_ret / abs(max_dd) if abs(max_dd) > 1e-10 else 0
    result = {"Method": name, "Ann. Return": ann_ret, "Ann. Vol": ann_vol,
              "Sharpe": sharpe, "Sortino": sortino, "Max DD": max_dd,
              "Calmar": calmar, "Win Rate": (daily_returns > 0).mean()}
    if benchmark_returns is not None:
        common = daily_returns.index.intersection(benchmark_returns.index)
        if len(common) > 20:
            p, b = daily_returns.loc[common], benchmark_returns.loc[common]
            excess = p - b
            te = excess.std() * np.sqrt(252)
            result["Info Ratio"] = excess.mean() * 252 / te if te > 0 else 0
            result["Tracking Error"] = te
            up = b > 0
            dn = b < 0
            result["Up Capture"] = p[up].mean() / b[up].mean() if up.sum() > 5 and abs(b[up].mean()) > 1e-10 else 1
            result["Down Capture"] = p[dn].mean() / b[dn].mean() if dn.sum() > 5 and abs(b[dn].mean()) > 1e-10 else 1
    return result


def _run_walkforward(returns, est_days, rebal_period, use_dn):
    tickers = returns.columns.tolist()
    if len(tickers) < 3 or len(returns) < est_days + 40:
        return {}
    rebal_groups = returns.resample(rebal_period).last()
    rebal_dates = []
    for pe in rebal_groups.index:
        mask = returns.index <= pe
        if mask.any():
            ad = returns.index[mask][-1]
            if returns.index.get_loc(ad) >= est_days:
                rebal_dates.append(ad)
    if len(rebal_dates) < 2:
        return {}
    raw = {m: [] for m in BASE_METHODS}
    for i in range(len(rebal_dates)):
        rd = rebal_dates[i]
        loc = returns.index.get_loc(rd)
        est_ret = returns.iloc[loc - est_days:loc]
        methods_w = _compute_all_weights(est_ret, tickers, use_dn)
        end = rebal_dates[i + 1] if i < len(rebal_dates) - 1 else returns.index[-1]
        oos = returns.loc[rd:end]
        for method, w in methods_w.items():
            port_ret = oos.values @ w
            for j, dt in enumerate(oos.index):
                raw[method].append({"date": dt, "return": port_ret[j]})
    result = {}
    for method, data in raw.items():
        if data:
            df = pd.DataFrame(data).set_index("date")
            df = df[~df.index.duplicated(keep="first")]
            result[method] = df["return"]
    return result


@st.cache_data(ttl=3600, show_spinner=False)
def _download_prices(tickers_tuple, period):
    import yfinance as yf
    data = yf.download(list(tickers_tuple), period=period, progress=False, threads=True)
    if data is None or data.empty:
        return pd.DataFrame()
    if isinstance(data.columns, pd.MultiIndex):
        return data["Close"]
    # Single ticker: yfinance returns flat columns
    if "Close" in data.columns:
        return data[["Close"]].rename(columns={"Close": tickers_tuple[0]})
    return pd.DataFrame()


# ═══════════════════════════════════════════════
# CONTROLS
# ═══════════════════════════════════════════════

with st.form("universe_portfolio_form", border=True):
    with st.expander("Configuration", expanded=True):
        uc1, uc2, uc3, uc4 = st.columns(4)
        with uc1:
            u_lookback = st.selectbox("Lookback", ["1Y", "2Y", "3Y", "5Y"], index=2, key="up_lb")
        with uc2:
            u_est = st.selectbox("Estimation Window", [126, 189, 252, 504], index=2,
                                  format_func=lambda d: f"{d}D ({d//21}mo)", key="up_est")
        with uc3:
            u_rebal = st.selectbox("Rebalance", ["Monthly", "Quarterly"], index=0, key="up_rebal")
        with uc4:
            u_denoise = st.checkbox("Ledoit-Wolf Denoising", value=True, key="up_dn")

    rebal_map = {"Monthly": "ME", "Quarterly": "QE"}
    lookback_map = {"1Y": "1y", "2Y": "2y", "3Y": "3y", "5Y": "5y"}
    rebal_period = rebal_map[u_rebal]

    run_btn = st.form_submit_button("Run Universe Analysis", type="primary", use_container_width=True)

if run_btn:
    st.session_state["up_loaded"] = True

if not st.session_state.get("up_loaded"):
    st.info(f"Click **Run Universe Analysis** to backtest all {len(PRESET_GROUPS)} groups "
            f"with {len(BASE_METHODS)} methods each.")
    st.stop()


# ═══════════════════════════════════════════════
# DATA LOADING & GRID BACKTEST
# ═══════════════════════════════════════════════

all_tickers = sorted(set(t for tks in PRESET_GROUPS.values() for t in tks))

with st.spinner(f"Downloading {len(all_tickers)} tickers..."):
    prices = _download_prices(tuple(all_tickers), lookback_map[u_lookback])

if prices.empty:
    st.error("Failed to download price data.")
    st.stop()

prices = prices.dropna(axis=1, how="all")
returns = prices.pct_change().dropna()
available = set(returns.columns)

# SPY benchmark
spy_ret = None
if "SPY" in returns.columns:
    spy_ret = returns["SPY"]

# Run walkforward for each group
grid_sharpe = {}
grid_return = {}
grid_maxdd = {}
grid_sortino = {}
group_wf_cache = {}  # cache full WF results per group
group_best = {}  # best method per group

progress = st.progress(0, text="Running grid analysis...")
group_names = list(PRESET_GROUPS.keys())
n_groups = len(group_names)

for gi, gname in enumerate(group_names):
    progress.progress((gi + 1) / n_groups, text=f"Backtesting {gname}...")
    tks = [t for t in PRESET_GROUPS[gname] if t in available]
    if len(tks) < 3:
        continue
    g_ret = returns[tks]
    wf = _run_walkforward(g_ret, u_est, rebal_period, u_denoise)
    if not wf:
        continue
    group_wf_cache[gname] = wf
    g_sharpes = {}
    for method, daily_ret in wf.items():
        m = _portfolio_metrics(daily_ret, method, spy_ret)
        grid_sharpe[(gname, method)] = m["Sharpe"]
        grid_return[(gname, method)] = m["Ann. Return"]
        grid_maxdd[(gname, method)] = m["Max DD"]
        grid_sortino[(gname, method)] = m["Sortino"]
        g_sharpes[method] = m["Sharpe"]
    group_best[gname] = max(g_sharpes, key=g_sharpes.get)

progress.empty()

if not grid_sharpe:
    st.error("No valid backtests completed.")
    st.stop()

groups_ok = sorted(set(g for g, _ in grid_sharpe.keys()))
methods_ok = sorted(set(m for _, m in grid_sharpe.keys()),
                     key=lambda m: BASE_METHODS.index(m) if m in BASE_METHODS else 99)

def _build_grid(data_dict):
    df = pd.DataFrame(index=groups_ok, columns=methods_ok, dtype=float)
    for (g, m), v in data_dict.items():
        if g in df.index and m in df.columns:
            df.loc[g, m] = v
    return df

sharpe_grid = _build_grid(grid_sharpe)
return_grid = _build_grid(grid_return)
maxdd_grid = _build_grid(grid_maxdd)
sortino_grid = _build_grid(grid_sortino)

# Build hierarchical portfolio
group_oos = {}
group_within = {}
for gname in groups_ok:
    wf = group_wf_cache.get(gname, {})
    bm = group_best.get(gname)
    if bm and bm in wf:
        group_oos[gname] = wf[bm]
        # Current within-group weights
        tks = [t for t in PRESET_GROUPS[gname] if t in available]
        g_ret = returns[tks]
        # Use min of estimation window and available data
        est_slice = min(u_est, len(g_ret))
        cw = _compute_all_weights(g_ret.iloc[-est_slice:], tks, u_denoise)
        group_within[gname] = pd.Series(cw[bm], index=tks)

# Meta-level optimization
hier_ret = None
hier_weights = None
meta_best = "N/A"
final_weights = {}
if len(group_oos) >= 2:
    group_ret_df = pd.DataFrame(group_oos).dropna()
    if len(group_ret_df) >= 63:
        meta_est = min(252, max(63, int(len(group_ret_df) * 0.4)))
        meta_wf = _run_walkforward(group_ret_df, meta_est, rebal_period, u_denoise)
        if meta_wf:
            meta_sharpes = {m: _portfolio_metrics(r, m)["Sharpe"] for m, r in meta_wf.items()}
            meta_best = max(meta_sharpes, key=meta_sharpes.get)
            hier_ret = meta_wf[meta_best]
            # Current group weights
            cw = _compute_all_weights(group_ret_df.iloc[-meta_est:], list(group_oos.keys()), u_denoise)
            hier_weights = pd.Series(cw[meta_best], index=list(group_oos.keys())).sort_values(ascending=False)
            # Final combined weights
            for gname in hier_weights.index:
                gw = hier_weights[gname]
                if gw < 0.001:
                    continue
                within = group_within.get(gname, pd.Series(dtype=float))
                for ticker, tw in within.items():
                    if tw > 0.001:
                        final_weights[ticker] = final_weights.get(ticker, 0) + gw * tw
            fw_total = sum(final_weights.values())
            if fw_total > 0:
                final_weights = {k: v / fw_total for k, v in final_weights.items()}

fw_series = pd.Series(final_weights).sort_values(ascending=False) if final_weights else pd.Series(dtype=float)


# ═══════════════════════════════════════════════
# TABS
# ═══════════════════════════════════════════════

tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
    "Universe Scan", "Group Analysis", "Portfolio Construction",
    "Allocations", "Performance", "Risk & Drawdown", "Statistical Tests",
])


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — UNIVERSE SCAN
# ══════════════════════════════════════════════════════════════════════════════

with tab1:
    with error_boundary("Universe Scan"):
        st.subheader("Sharpe Ratio — Universe x Method")
        with st.expander("How to use this tab"):
            st.markdown("""
Each cell shows the walk-forward Sharpe ratio for a specific universe + method combination.

**Bright green** = strong performance. **Red** = negative returns. Use this to identify:
- Which **sectors** are most investable right now
- Which **methods** work consistently across different universes
- Whether the best method is consistent or universe-dependent
""")

        # Sharpe heatmap
        fig_sg = go.Figure(go.Heatmap(
            z=sharpe_grid.values, x=sharpe_grid.columns.tolist(), y=sharpe_grid.index.tolist(),
            colorscale=[[0, "#ff4444"], [0.5, "#1a1a2e"], [1, "#00ff88"]], zmid=0,
            text=[[f"{v:.2f}" if pd.notna(v) else "" for v in row] for row in sharpe_grid.values],
            texttemplate="%{text}", textfont={"size": 9}, colorbar=dict(title="Sharpe"),
        ))
        fig_sg.update_layout(template="plotly_dark", height=max(400, len(groups_ok) * 28),
                              margin=dict(l=0, r=0, t=10, b=0))
        st.plotly_chart(fig_sg, use_container_width=True, config=PLOTLY_NOBAR)

        # Return + MaxDD + Sortino in a row
        hm_sel = st.selectbox("Additional heatmap", ["Ann. Return", "Max Drawdown", "Sortino"], key="up_hm2")
        hm_data = {"Ann. Return": (return_grid, 100, "%"), "Max Drawdown": (maxdd_grid, 100, "%"),
                    "Sortino": (sortino_grid, 1, "")}
        _hd, _scale, _suffix = hm_data[hm_sel]
        fig_hm2 = go.Figure(go.Heatmap(
            z=_hd.values * _scale, x=_hd.columns.tolist(), y=_hd.index.tolist(),
            colorscale=[[0, "#ff4444"], [0.5, "#1a1a2e"], [1, "#00ff88"]],
            zmid=-5 if hm_sel == "Max Drawdown" else 0,
            text=[[f"{v*_scale:.1f}{_suffix}" if pd.notna(v) else "" for v in row] for row in _hd.values],
            texttemplate="%{text}", textfont={"size": 9},
        ))
        fig_hm2.update_layout(template="plotly_dark", height=max(400, len(groups_ok) * 28),
                               title=hm_sel, margin=dict(l=0, r=0, t=40, b=0))
        st.plotly_chart(fig_hm2, use_container_width=True, config=PLOTLY_NOBAR)

        # Top 15 combos
        st.subheader("Top 15 Combinations")
        combo_rows = [{"Universe": g, "Method": m, "Sharpe": grid_sharpe[(g, m)],
                        "Return": grid_return.get((g, m), 0), "Max DD": grid_maxdd.get((g, m), 0),
                        "Sortino": grid_sortino.get((g, m), 0)}
                       for g, m in grid_sharpe]
        combo_df = pd.DataFrame(combo_rows).sort_values("Sharpe", ascending=False).head(15)
        combo_df["Rank"] = range(1, len(combo_df) + 1)
        cd = combo_df.copy()
        cd["Return"] = cd["Return"].apply(lambda v: f"{v*100:+.1f}%")
        cd["Max DD"] = cd["Max DD"].apply(lambda v: f"{v*100:.1f}%")
        cd["Sharpe"] = cd["Sharpe"].apply(lambda v: f"{v:.2f}")
        cd["Sortino"] = cd["Sortino"].apply(lambda v: f"{v:.2f}")
        st.dataframe(cd[["Rank", "Universe", "Method", "Sharpe", "Return", "Max DD", "Sortino"]],
                      use_container_width=True, hide_index=True)

        # Method consistency
        st.subheader("Method Consistency")
        avg_sharpe = sharpe_grid.mean(axis=0).sort_values(ascending=False)
        std_sharpe = sharpe_grid.std(axis=0)
        fig_mc = go.Figure(go.Bar(
            x=avg_sharpe.index, y=avg_sharpe.values,
            marker_color=[METHOD_COLORS.get(m, "#888") for m in avg_sharpe.index],
            error_y=dict(type="data", array=std_sharpe.loc[avg_sharpe.index].values,
                         color="#ffaa00", thickness=1.5, width=4),
            text=[f"{v:.2f}" for v in avg_sharpe.values], textposition="outside",
        ))
        fig_mc.update_layout(template="plotly_dark", height=350,
                              title="Avg Sharpe Across All Universes (error bars = std dev)",
                              yaxis_title="Avg Sharpe", margin=dict(l=0, r=0, t=40, b=0))
        st.plotly_chart(fig_mc, use_container_width=True, config=PLOTLY_NOBAR)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — GROUP ANALYSIS
# ══════════════════════════════════════════════════════════════════════════════

with tab2:
    with error_boundary("Group Analysis"):
        st.subheader("Cross-Group Analysis")
        with st.expander("How to use this tab"):
            st.markdown("""
Shows how sector groups relate to each other and what happens when you combine them.

- **Correlation matrix**: Low correlation = diversification benefit. High = redundant.
- **Incremental analysis**: Starting from Multi-Asset, what's the impact of adding each sector?
- Use this to decide which groups to include in your hierarchical portfolio.
""")

        # Cross-group correlation
        if len(group_oos) >= 3:
            st.markdown("#### Cross-Group Return Correlation")
            corr_df = pd.DataFrame(group_oos).corr()
            fig_gc = go.Figure(go.Heatmap(
                z=corr_df.values, x=corr_df.columns.tolist(), y=corr_df.index.tolist(),
                colorscale=[[0, "#00ff88"], [0.5, "#1a1a2e"], [1, "#ff4444"]], zmid=0.5,
                text=[[f"{v:.2f}" for v in row] for row in corr_df.values],
                texttemplate="%{text}", textfont={"size": 9},
            ))
            fig_gc.update_layout(template="plotly_dark", height=max(400, len(corr_df) * 32),
                                  margin=dict(l=0, r=0, t=10, b=0))
            st.plotly_chart(fig_gc, use_container_width=True, config=PLOTLY_NOBAR)

            # Best/worst pairs
            pairs = []
            for i in range(len(corr_df)):
                for j in range(i + 1, len(corr_df)):
                    pairs.append({"A": corr_df.index[i], "B": corr_df.columns[j],
                                   "Corr": corr_df.iloc[i, j]})
            if pairs:
                pdf = pd.DataFrame(pairs).sort_values("Corr")
                c1, c2 = st.columns(2)
                with c1:
                    st.markdown("**Best diversifiers (lowest corr):**")
                    for _, p in pdf.head(3).iterrows():
                        st.caption(f"{p['A']} / {p['B']}: {p['Corr']:.2f}")
                with c2:
                    st.markdown("**Most redundant (highest corr):**")
                    for _, p in pdf.tail(3).iterrows():
                        st.caption(f"{p['A']} / {p['B']}: {p['Corr']:.2f}")

        # Incremental analysis
        st.markdown("---")
        st.subheader("Incremental Analysis — Adding Sectors to Multi-Asset")
        base_group = "Multi-Asset"
        if base_group in group_wf_cache:
            base_wf = group_wf_cache[base_group]
            base_sharpes = {m: _portfolio_metrics(r, m)["Sharpe"] for m, r in base_wf.items()}
            best_base = max(base_sharpes, key=base_sharpes.get)
            base_s = base_sharpes[best_base]
            base_m = _portfolio_metrics(base_wf[best_base], best_base)

            incr = [{"Universe": "Multi-Asset (base)", "Sharpe": base_s,
                      "Return": base_m["Ann. Return"], "Max DD": base_m["Max DD"], "Delta": 0}]
            sectors = [g for g in groups_ok if g not in (base_group, "Mega Caps", "Global Macro")]
            for sg in sectors:
                combined_tks = sorted(set(
                    [t for t in PRESET_GROUPS[base_group] if t in available] +
                    [t for t in PRESET_GROUPS[sg] if t in available]
                ))
                if len(combined_tks) <= len([t for t in PRESET_GROUPS[base_group] if t in available]):
                    continue
                c_wf = _run_walkforward(returns[combined_tks], u_est, rebal_period, u_denoise)
                if not c_wf:
                    continue
                c_sharpes = {m: _portfolio_metrics(r, m)["Sharpe"] for m, r in c_wf.items()}
                c_best = max(c_sharpes, key=c_sharpes.get)
                c_m = _portfolio_metrics(c_wf[c_best], c_best)
                incr.append({"Universe": f"+ {sg}", "Sharpe": c_sharpes[c_best],
                              "Return": c_m["Ann. Return"], "Max DD": c_m["Max DD"],
                              "Delta": c_sharpes[c_best] - base_s})

            incr_df = pd.DataFrame(incr)
            fig_incr = go.Figure(go.Bar(
                x=incr_df["Universe"], y=incr_df["Delta"],
                marker_color=["#555" if d == 0 else "#00ff88" if d > 0 else "#ff4444" for d in incr_df["Delta"]],
                text=[f"{d:+.2f}" if d != 0 else "base" for d in incr_df["Delta"]], textposition="outside",
            ))
            fig_incr.add_hline(y=0, line_dash="dash", line_color="#555")
            fig_incr.update_layout(template="plotly_dark", height=380,
                                    title=f"Sharpe Impact (Base: {best_base} {base_s:.2f})",
                                    yaxis_title="Sharpe Change", margin=dict(l=0, r=0, t=40, b=100),
                                    xaxis_tickangle=-45)
            st.plotly_chart(fig_incr, use_container_width=True, config=PLOTLY_NOBAR)

            id = incr_df.copy()
            id["Sharpe"] = id["Sharpe"].apply(lambda v: f"{v:.2f}")
            id["Return"] = id["Return"].apply(lambda v: f"{v*100:+.1f}%")
            id["Max DD"] = id["Max DD"].apply(lambda v: f"{v*100:.1f}%")
            id["Delta"] = id["Delta"].apply(lambda v: f"{v:+.2f}" if v != 0 else "—")
            st.dataframe(id, use_container_width=True, hide_index=True)
        else:
            st.info("Multi-Asset group not available for incremental analysis.")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — PORTFOLIO CONSTRUCTION
# ══════════════════════════════════════════════════════════════════════════════

with tab3:
    with error_boundary("Portfolio Construction"):
        st.subheader("Two-Layer Hierarchical Portfolio")
        with st.expander("How this works"):
            st.markdown("""
**Layer 1:** Each qualifying sector group is treated as a single asset. The optimizer
allocates capital *between* groups based on their risk/return characteristics.

**Layer 2:** Within each group, the best-performing allocation method determines
how capital is split among individual tickers.

**Final weight** = Group allocation % x Within-group ticker weight %.
""")

        if hier_weights is None or fw_series.empty:
            st.warning("Need at least 2 qualifying groups for hierarchical portfolio.")
        else:
            # Group allocation
            st.markdown("#### Layer 1 — Group Allocation")
            gw_nz = hier_weights[hier_weights > 0.005]

            gc1, gc2 = st.columns([2, 1])
            with gc1:
                fig_gw = go.Figure(go.Pie(
                    labels=gw_nz.index.tolist(), values=(gw_nz.values * 100).round(1),
                    hole=0.45, textinfo="label+percent", textfont=dict(size=11),
                    marker=dict(line=dict(color="#1a1a2e", width=2)),
                ))
                fig_gw.update_layout(template="plotly_dark", height=380,
                                      title=f"Group Allocation — {meta_best}",
                                      margin=dict(l=0, r=0, t=40, b=0), showlegend=False)
                st.plotly_chart(fig_gw, use_container_width=True, config=PLOTLY_NOBAR)
            with gc2:
                for g in gw_nz.index:
                    st.markdown(f"**{g}** — {gw_nz[g]*100:.1f}% (via {group_best.get(g, '?')})")

            # Final portfolio
            st.markdown("#### Final Portfolio — Combined Weights")
            fw_nz = fw_series[fw_series > 0.002]

            if not fw_nz.empty:
                # Top positions
                top_cols = st.columns(min(len(fw_nz), 8))
                for qi, (qt, qw) in enumerate(fw_nz.head(8).items()):
                    top_cols[qi].metric(qt, f"{qw*100:.1f}%")

                fc1, fc2 = st.columns([2, 1])
                with fc1:
                    fig_fw = go.Figure(go.Pie(
                        labels=fw_nz.index.tolist(), values=(fw_nz.values * 100).round(2),
                        hole=0.45, textinfo="label+percent", textfont=dict(size=9),
                    ))
                    fig_fw.update_layout(template="plotly_dark", height=450,
                                          title="Final Hierarchical Portfolio",
                                          margin=dict(l=0, r=0, t=40, b=0), showlegend=False)
                    st.plotly_chart(fig_fw, use_container_width=True, config=PLOTLY_NOBAR)
                with fc2:
                    hhi = sum(v ** 2 for v in final_weights.values())
                    st.markdown(f"**{len(fw_nz)} positions** | Effective N: {1/hhi:.1f}" if hhi > 0 else "")
                    st.markdown("---")
                    for t in fw_nz.head(15).index:
                        st.markdown(f"**{t}** — {fw_nz[t]*100:.2f}%")

                # Download
                dl_w = pd.DataFrame({"Ticker": fw_nz.index, "Weight %": (fw_nz.values * 100).round(2)})
                st.download_button("Download Weights CSV", dl_w.to_csv(index=False),
                                    "universe_portfolio_weights.csv", "text/csv", key="up_dl")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 — ALLOCATIONS
# ══════════════════════════════════════════════════════════════════════════════

with tab4:
    with error_boundary("Allocations"):
        st.subheader("Portfolio Allocations — Detail View")
        with st.expander("How to use this tab"):
            st.markdown("""
Shows the full breakdown of how capital flows through both layers:
- **By group**: How much goes to each sector
- **By ticker**: Final individual stock weights
- **By sector exposure**: Aggregated by GICS sector for concentration checks
""")

        if fw_series.empty:
            st.info("Build a hierarchical portfolio first (see Portfolio Construction tab).")
        else:
            fw_nz = fw_series[fw_series > 0.002]

            # Layer breakdown table — spacious layout
            st.markdown("#### Layer-by-Layer Breakdown")
            for gname in (hier_weights[hier_weights > 0.005].index if hier_weights is not None else []):
                gw = hier_weights[gname]
                within = group_within.get(gname, pd.Series(dtype=float))
                within_nz = within[within > 0.005].sort_values(ascending=False)
                if within_nz.empty:
                    continue

                with st.container(border=True):
                    st.markdown(
                        f"**{gname}** — {gw*100:.1f}% of portfolio "
                        f"(via {group_best.get(gname, '?')})"
                    )
                    n_cols = min(len(within_nz), 6)
                    cols = st.columns(n_cols)
                    for i, (tk, tw) in enumerate(within_nz.head(6).items()):
                        final_w = gw * tw * 100
                        cols[i].metric(tk, f"{tw*100:.1f}%", delta=f"{final_w:.1f}% final")
                    if len(within_nz) > 6:
                        st.caption(f"+ {len(within_nz) - 6} more positions")

            # Concentration analysis
            st.markdown("#### Concentration Analysis")
            ca1, ca2, ca3 = st.columns(3)
            top5_w = fw_nz.head(5).sum() * 100
            top10_w = fw_nz.head(10).sum() * 100
            hhi = sum(v ** 2 for v in fw_nz.values) * 10000
            ca1.metric("Top 5 Concentration", f"{top5_w:.1f}%")
            ca2.metric("Top 10 Concentration", f"{top10_w:.1f}%")
            ca3.metric("HHI", f"{hhi:.0f}", help="<1500 = diversified, 1500-2500 = moderate, >2500 = concentrated")

            # Horizontal bar chart of all positions
            fig_alloc = go.Figure(go.Bar(
                y=fw_nz.index[::-1], x=(fw_nz.values[::-1] * 100),
                orientation="h",
                marker_color=[COLOR_CYCLE[i % len(COLOR_CYCLE)] for i in range(len(fw_nz))][::-1],
                text=[f"{v*100:.1f}%" for v in fw_nz.values[::-1]], textposition="outside",
            ))
            fig_alloc.update_layout(
                template="plotly_dark", height=max(400, len(fw_nz) * 22),
                xaxis_title="Weight (%)", margin=dict(l=0, r=60, t=10, b=0),
            )
            st.plotly_chart(fig_alloc, use_container_width=True, config=PLOTLY_NOBAR)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 5 — PERFORMANCE
# ══════════════════════════════════════════════════════════════════════════════

with tab5:
    with error_boundary("Performance"):
        st.subheader("Hierarchical Portfolio Performance")

        if hier_ret is None:
            st.info("Build a hierarchical portfolio first.")
        else:
            hier_m = _portfolio_metrics(hier_ret, "Hierarchical", spy_ret)

            # SPY comparison banner
            if spy_ret is not None:
                spy_m = _portfolio_metrics(spy_ret, "SPY")
                h_beats = hier_m["Sharpe"] > spy_m["Sharpe"]
                h_color = "#00ff88" if h_beats else "#ff4444"
                _n = COLORS["text_muted"]
                st.markdown(
                    f'<div style="background:{COLORS["card_bg"]};border:1px solid {h_color};'
                    f'border-radius:8px;padding:16px 20px;margin-bottom:16px;">'
                    f'<div style="color:{h_color};font-weight:700;font-size:1.1rem;margin-bottom:10px;">'
                    f'HIERARCHICAL PORTFOLIO {"OUTPERFORMS" if h_beats else "UNDERPERFORMS"} SPY</div>'
                    f'<table style="width:100%;border-collapse:collapse;color:{COLORS["text_primary"]};font-size:0.95rem;">'
                    f'<tr style="border-bottom:1px solid {COLORS["card_border"]};">'
                    f'<td></td><td><b>Portfolio</b></td><td style="color:{_n};">SPY</td><td style="color:{h_color};text-align:right;">Edge</td></tr>'
                    f'<tr style="border-bottom:1px solid {COLORS["card_border"]};">'
                    f'<td>Sharpe</td><td><b>{hier_m["Sharpe"]:.2f}</b></td><td style="color:{_n};">{spy_m["Sharpe"]:.2f}</td>'
                    f'<td style="color:{h_color};text-align:right;">{hier_m["Sharpe"]-spy_m["Sharpe"]:+.2f}</td></tr>'
                    f'<tr style="border-bottom:1px solid {COLORS["card_border"]};">'
                    f'<td>Return</td><td><b>{hier_m["Ann. Return"]*100:+.1f}%</b></td><td style="color:{_n};">{spy_m["Ann. Return"]*100:+.1f}%</td>'
                    f'<td style="color:{h_color};text-align:right;">{(hier_m["Ann. Return"]-spy_m["Ann. Return"])*100:+.1f}pp</td></tr>'
                    f'<tr>'
                    f'<td>Max DD</td><td><b>{hier_m["Max DD"]*100:.1f}%</b></td><td style="color:{_n};">{spy_m["Max DD"]*100:.1f}%</td>'
                    f'<td style="color:{h_color};text-align:right;">{(hier_m["Max DD"]-spy_m["Max DD"])*100:+.1f}pp</td></tr>'
                    f'</table></div>',
                    unsafe_allow_html=True,
                )

            # Equity curve
            st.markdown("#### Equity Curves")
            cum_hier = (1 + hier_ret).cumprod() * 100
            fig_eq = go.Figure()
            fig_eq.add_trace(go.Scatter(
                x=cum_hier.index, y=cum_hier.values,
                line=dict(color=COLORS["accent"], width=2), name="Hierarchical",
            ))
            if spy_ret is not None:
                common = cum_hier.index.intersection(spy_ret.index)
                cum_spy = (1 + spy_ret.loc[common]).cumprod() * 100
                fig_eq.add_trace(go.Scatter(
                    x=cum_spy.index, y=cum_spy.values,
                    line=dict(color=COLORS["text_muted"], width=1, dash="dash"), name="SPY",
                ))
            fig_eq.update_layout(template="plotly_dark", height=400,
                                  yaxis_title="Growth of $100",
                                  margin=dict(l=0, r=0, t=10, b=0))
            st.plotly_chart(fig_eq, use_container_width=True, config=PLOTLY_NOBAR)

            # Metrics table
            st.markdown("#### Full Metrics")
            metrics_rows = []
            for key in ["Ann. Return", "Ann. Vol", "Sharpe", "Sortino", "Max DD", "Calmar", "Win Rate"]:
                val = hier_m[key]
                fmt = f"{val*100:.1f}%" if key in ("Ann. Return", "Ann. Vol", "Max DD", "Win Rate") else f"{val:.2f}"
                row = {"Metric": key, "Hierarchical": fmt}
                if spy_ret is not None and key in spy_m:
                    sv = spy_m[key]
                    row["SPY"] = f"{sv*100:.1f}%" if key in ("Ann. Return", "Ann. Vol", "Max DD", "Win Rate") else f"{sv:.2f}"
                metrics_rows.append(row)
            if "Info Ratio" in hier_m:
                metrics_rows.append({"Metric": "Info Ratio", "Hierarchical": f"{hier_m['Info Ratio']:.2f}", "SPY": "—"})
            if "Up Capture" in hier_m:
                metrics_rows.append({"Metric": "Up Capture", "Hierarchical": f"{hier_m['Up Capture']:.2f}", "SPY": "1.00"})
                metrics_rows.append({"Metric": "Down Capture", "Hierarchical": f"{hier_m['Down Capture']:.2f}", "SPY": "1.00"})
            st.dataframe(pd.DataFrame(metrics_rows), use_container_width=True, hide_index=True)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 6 — RISK & DRAWDOWN
# ══════════════════════════════════════════════════════════════════════════════

with tab6:
    with error_boundary("Risk & Drawdown"):
        st.subheader("Drawdown & Risk Analysis")

        if hier_ret is None:
            st.info("Build a hierarchical portfolio first.")
        else:
            # Underwater chart
            cum = (1 + hier_ret).cumprod()
            dd = (cum / cum.cummax()) - 1

            fig_dd = go.Figure()
            fig_dd.add_trace(go.Scatter(
                x=dd.index, y=dd.values * 100,
                fill="tozeroy", fillcolor="rgba(255,68,68,0.15)",
                line=dict(color=COLORS["danger"], width=1),
                hovertemplate="Date: %{x}<br>Drawdown: %{y:.1f}%<extra></extra>",
            ))
            fig_dd.update_layout(template="plotly_dark", height=350,
                                  yaxis_title="Drawdown (%)",
                                  margin=dict(l=0, r=0, t=10, b=0))
            st.plotly_chart(fig_dd, use_container_width=True, config=PLOTLY_NOBAR)

            # Drawdown stats
            max_dd_val = dd.min() * 100
            max_dd_date = dd.idxmin()
            # Recovery
            recovery_days = 0
            if dd.min() < -0.001:  # meaningful drawdown threshold
                post_trough = dd.loc[max_dd_date:]
                # Recovered = first day after trough where DD is back to ~0
                recovered = post_trough.iloc[1:]  # skip the trough itself
                recovered = recovered[recovered >= -0.001]
                if not recovered.empty:
                    recovery_date = recovered.index[0]
                    recovery_days = (recovery_date - max_dd_date).days
                else:
                    recovery_days = None  # still underwater

            dd1, dd2, dd3 = st.columns(3)
            dd1.metric("Max Drawdown", f"{max_dd_val:.1f}%")
            dd2.metric("Trough Date", str(max_dd_date.date()) if hasattr(max_dd_date, "date") else str(max_dd_date))
            dd3.metric("Recovery", f"{recovery_days} days" if recovery_days is not None else "Not recovered")

            # Rolling vol
            st.markdown("#### Rolling Volatility (63-day)")
            roll_vol = hier_ret.rolling(63).std() * np.sqrt(252)
            fig_rv = go.Figure()
            fig_rv.add_trace(go.Scatter(
                x=roll_vol.index, y=roll_vol.values * 100,
                line=dict(color=COLORS["warning"], width=1),
                fill="tozeroy", fillcolor="rgba(255,170,0,0.1)",
            ))
            fig_rv.update_layout(template="plotly_dark", height=300,
                                  yaxis_title="Annualized Vol (%)",
                                  margin=dict(l=0, r=0, t=10, b=0))
            st.plotly_chart(fig_rv, use_container_width=True, config=PLOTLY_NOBAR)

            # Rolling Sharpe
            st.markdown("#### Rolling Sharpe (63-day)")
            roll_sharpe = (hier_ret.rolling(63).mean() * 252) / (hier_ret.rolling(63).std() * np.sqrt(252))
            fig_rs = go.Figure()
            fig_rs.add_trace(go.Scatter(
                x=roll_sharpe.index, y=roll_sharpe.values,
                line=dict(color=COLORS["accent"], width=1),
            ))
            fig_rs.add_hline(y=0, line_color=COLORS["text_muted"], line_width=0.5)
            fig_rs.add_hline(y=1, line_dash="dot", line_color=COLORS["success"], line_width=0.5,
                              annotation_text="Sharpe = 1")
            fig_rs.update_layout(template="plotly_dark", height=300,
                                  yaxis_title="Rolling Sharpe",
                                  margin=dict(l=0, r=0, t=10, b=0))
            st.plotly_chart(fig_rs, use_container_width=True, config=PLOTLY_NOBAR)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 7 — STATISTICAL TESTS
# ══════════════════════════════════════════════════════════════════════════════

with tab7:
    with error_boundary("Statistical Tests"):
        st.subheader("Statistical Significance — De Prado Framework")
        with st.expander("Why this matters"):
            st.markdown("""
These tests answer: **Is the portfolio's performance real or noise?**

- **Deflated Sharpe** adjusts for the number of strategies tested (multiple testing bias)
- **Bootstrap CI** resamples returns preserving serial dependence
- **Min Track Record** tells you how many days of data you need before trusting the Sharpe
""")

        if hier_ret is None or len(hier_ret) < 60:
            st.info("Need hierarchical portfolio with 60+ days of data.")
        else:
            n_obs = len(hier_ret)
            obs_sharpe = hier_ret.mean() / hier_ret.std() * np.sqrt(252) if hier_ret.std() > 0 else 0
            skew_val = float(hier_ret.skew())
            kurt_val = float(hier_ret.kurtosis())  # excess kurtosis (Fisher)

            # DSR
            n_trials = len(BASE_METHODS) * len(groups_ok)
            from scipy.stats import norm as _norm
            euler_m = 0.5772156649
            if n_trials > 1:
                e_max = (1 - euler_m) * _norm.ppf(1 - 1/n_trials) + euler_m * _norm.ppf(1 - 1/(n_trials * np.e))
            else:
                e_max = 0
            # Lo (2002) formula with excess kurtosis (pandas .kurtosis() is Fisher)
            se_sr = np.sqrt((1 + 0.5 * obs_sharpe**2 - skew_val * obs_sharpe +
                              kurt_val / 4 * obs_sharpe**2) / (n_obs - 1))
            dsr_stat = (obs_sharpe - e_max) / se_sr if se_sr > 0 else 0
            dsr_p = float(_norm.cdf(dsr_stat))

            # Bootstrap
            rng = np.random.default_rng(42)
            block_size = min(20, n_obs // 5)
            boot_sharpes = []
            if block_size >= 2:
                for _ in range(2000):
                    starts = rng.integers(0, max(1, n_obs - block_size), n_obs // block_size + 1)
                    sample = np.concatenate([hier_ret.values[s:s + block_size] for s in starts])[:n_obs]
                    if sample.std() > 0:
                        boot_sharpes.append(sample.mean() / sample.std() * np.sqrt(252))

            ci_lo = np.percentile(boot_sharpes, 5) if boot_sharpes else 0
            ci_hi = np.percentile(boot_sharpes, 95) if boot_sharpes else 0
            boot_p = np.mean([s > 0 for s in boot_sharpes]) if boot_sharpes else 0

            # Min track record
            z95 = _norm.ppf(0.95)
            if obs_sharpe > 0:
                min_trl = max(1, (1 + (1 - skew_val * obs_sharpe + kurt_val / 4 * obs_sharpe**2)) * (z95 / obs_sharpe)**2)
            else:
                min_trl = float("inf")

            s1, s2, s3, s4 = st.columns(4)
            s1.metric("Observed Sharpe", f"{obs_sharpe:.2f}")
            s2.metric("DSR p-value", f"{dsr_p:.3f}",
                       help=f"Tested {n_trials} strategy combos. >0.95 = significant.")
            s3.metric("Bootstrap 90% CI", f"[{ci_lo:.2f}, {ci_hi:.2f}]")
            s4.metric("Min Track Record", f"{min_trl:.0f} days",
                       help=f"Need {min_trl:.0f} days to trust Sharpe at 95% confidence. Have {n_obs}.")

            if dsr_p > 0.95:
                st.success(f"DSR {dsr_p:.3f} — portfolio Sharpe is statistically significant after adjusting for {n_trials} trials.")
            elif dsr_p > 0.80:
                st.warning(f"DSR {dsr_p:.3f} — marginal significance. More data or fewer trials would strengthen the case.")
            else:
                st.error(f"DSR {dsr_p:.3f} — Sharpe may be attributable to multiple testing bias.")

            if n_obs > min_trl:
                st.success(f"Track record ({n_obs} days) exceeds minimum ({min_trl:.0f} days) — sufficient data.")
            else:
                st.warning(f"Track record ({n_obs} days) is below minimum ({min_trl:.0f} days) — need more history.")


# ─── FOOTER ────────────────────────────────────────────────────────────────────

st.markdown("---")
st.caption(
    "Backtested results do not guarantee future performance. Walk-forward methodology "
    "prevents look-ahead bias but does not eliminate model risk. Not financial advice."
)

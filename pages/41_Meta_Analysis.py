"""
Meta Analysis — Institutional Portfolio Construction & Validation Engine

Walk-forward backtests 9 allocation methods + custom blends + SPY benchmark
on a configurable multi-group ticker universe with Ledoit-Wolf denoising.

Methods: Tangency, Robust Sharpe, Min Variance, Risk Parity, Max Diversification,
HRP (Ward), HERC (CVaR), HCAA (1/N), Equal Weight, custom blends, SPY Buy & Hold.

9 tabs:
1. Equity Curves — walk-forward P/L paths for all methods
2. Allocations — current optimal weights, rebalance history, turnover
3. Forecasts — analyst targets, EPS revisions, valuation, macro overlay
4. Performance — ranked table, key metrics comparison
5. Institutional — net-of-cost curves, regime analysis, capture ratios, stress tests, capacity
6. Statistical Tests — Deflated Sharpe, PBO (CPCV), sequential bootstrap, min track record
7. Drawdown — underwater curves, max DD, duration analysis
8. Rolling Analysis — rolling Sharpe, method correlation, excess vs EW
9. Universe Grid — all presets backtested, incremental analysis, hierarchical allocation
"""
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from scipy.optimize import minimize
import logging
from src.layout import setup_page, error_boundary
from src.styles import COLORS
from src.quant_features import (
    hrp_allocate, herc_allocate, hcaa_allocate, denoise_covariance,
)

logger = logging.getLogger(__name__)
setup_page("41_Meta_Analysis")

st.title("Meta Analysis")
st.markdown(
    "Walk-forward backtest of 9 allocation methods on a combined ticker universe. "
    "Compare equity curves, drawdowns, and rolling performance head-to-head."
)

PLOTLY_NOBAR = {"displayModeBar": False}

BASE_METHODS = [
    "Tangency", "Robust Sharpe", "Min Variance", "Risk Parity",
    "Max Diversification", "HRP", "HERC (CVaR)", "HCAA (1/N)", "Equal Weight",
]

METHOD_COLORS = {
    "Tangency": "#00d1ff",
    "Robust Sharpe": "#00e0d0",
    "Min Variance": "#00ff88",
    "Risk Parity": "#ffaa00",
    "Max Diversification": "#ff00ff",
    "HRP": "#88ccff",
    "HERC (CVaR)": "#cc88ff",
    "HCAA (1/N)": "#66aacc",
    "Equal Weight": "#888888",
}

# ── Blend presets ──
BLEND_PRESETS = {
    "Robust + MaxDiv (50/50)": {"Robust Sharpe": 0.5, "Max Diversification": 0.5},
    "HRP + HERC (50/50)": {"HRP": 0.5, "HERC (CVaR)": 0.5},
    "Risk Parity + HRP + HERC (1/3)": {"Risk Parity": 1/3, "HRP": 1/3, "HERC (CVaR)": 1/3},
    "Custom": {},
}


# ═══════════════════════════════════════════════
# TICKER UNIVERSE
# ═══════════════════════════════════════════════

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
# OPTIMIZATION FUNCTIONS (self-contained)
# ═══════════════════════════════════════════════

def _tangency(mu, cov):
    n = len(mu)
    w0 = np.full(n, 1 / n)
    cons = [{"type": "eq", "fun": lambda w: np.sum(w) - 1}]
    bounds = [(0, 1)] * n
    def neg_sharpe(w):
        vol = np.sqrt(w @ cov @ w)
        return -(w @ mu) / vol if vol > 1e-12 else 1e10
    r = minimize(neg_sharpe, w0, method="SLSQP", bounds=bounds, constraints=cons)
    return r.x if r.success else w0

def _robust_sharpe(mu, cov, se):
    n = len(mu)
    w0 = np.full(n, 1 / n)
    mu_r = mu - se
    cons = [{"type": "eq", "fun": lambda w: np.sum(w) - 1}]
    bounds = [(0, 1)] * n
    def obj(w):
        vol = np.sqrt(w @ cov @ w)
        return -(w @ mu_r) / vol if vol > 1e-12 else 1e10
    r = minimize(obj, w0, method="SLSQP", bounds=bounds, constraints=cons)
    return r.x if r.success else w0

def _min_var(cov):
    n = cov.shape[0]
    w0 = np.full(n, 1 / n)
    cons = [{"type": "eq", "fun": lambda w: np.sum(w) - 1}]
    bounds = [(0, 1)] * n
    r = minimize(lambda w: np.sqrt(w @ cov @ w), w0, method="SLSQP", bounds=bounds, constraints=cons)
    return r.x if r.success else w0

def _risk_parity(cov):
    n = cov.shape[0]
    w0 = np.full(n, 1 / n)
    # Scale min weight with universe size so constraints stay feasible
    min_w = max(0.001, 1 / (n * 5))
    def obj(w):
        pv = np.sqrt(w @ cov @ w)
        if pv == 0: return 0
        mrc = cov @ w / pv
        rc = w * mrc
        target = pv / n
        return np.sum((rc - target) ** 2)
    cons = [{"type": "eq", "fun": lambda w: np.sum(w) - 1}]
    bounds = [(min_w, 1)] * n
    r = minimize(obj, w0, method="SLSQP", bounds=bounds, constraints=cons)
    return r.x if r.success else w0

def _max_div(cov):
    n = cov.shape[0]
    vols = np.sqrt(np.diag(cov))
    w0 = np.full(n, 1 / n)
    cons = [{"type": "eq", "fun": lambda w: np.sum(w) - 1}]
    bounds = [(0, 1)] * n
    def obj(w):
        pv = np.sqrt(w @ cov @ w)
        return -(w @ vols) / pv if pv > 0 else 0
    r = minimize(obj, w0, method="SLSQP", bounds=bounds, constraints=cons)
    return r.x if r.success else w0


def _compute_all_weights(est_ret, tickers, use_dn, blends=None, forecast_mu=None):
    """Compute all base method weights + any blended portfolios.

    If forecast_mu is provided (daily returns), return-dependent methods
    (Tangency, Robust Sharpe) use it instead of sample mean.
    Covariance-only methods are unaffected.
    """
    n = len(tickers)
    est_mu = forecast_mu if forecast_mu is not None else est_ret.mean().values
    est_cov = est_ret.cov().values
    mu_se = est_ret.std().values / np.sqrt(len(est_ret))

    if use_dn:
        dn_cov, dn_corr = denoise_covariance(est_ret)
    else:
        dn_cov = pd.DataFrame(est_cov * 252, index=tickers, columns=tickers)
        dn_corr = est_ret.corr()

    base = {
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

    # Add blended portfolios
    if blends:
        for blend_name, components in blends.items():
            blended = np.zeros(n)
            for method, pct in components.items():
                if method in base:
                    blended += base[method] * pct
            total = blended.sum()
            if total > 0:
                blended /= total  # re-normalize to sum to 1
            base[blend_name] = blended

    return base


def _portfolio_metrics(daily_returns, name, benchmark_returns=None):
    """Compute standard + institutional portfolio metrics."""
    ann_ret = daily_returns.mean() * 252
    ann_vol = daily_returns.std() * np.sqrt(252)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
    cum = (1 + daily_returns).cumprod()
    max_dd = ((cum / cum.cummax()) - 1).min()
    neg_returns = daily_returns[daily_returns < 0]
    downside_vol = neg_returns.std() * np.sqrt(252) if len(neg_returns) > 1 else ann_vol
    sortino = ann_ret / downside_vol if downside_vol > 0 else 0
    calmar = ann_ret / abs(max_dd) if abs(max_dd) > 1e-10 else 0
    win_rate = (daily_returns > 0).mean()

    result = {
        "Method": name,
        "Ann. Return": ann_ret,
        "Ann. Vol": ann_vol,
        "Sharpe": sharpe,
        "Sortino": sortino,
        "Max DD": max_dd,
        "Calmar": calmar,
        "Win Rate": win_rate,
    }

    # Institutional metrics (vs benchmark)
    if benchmark_returns is not None:
        common = daily_returns.index.intersection(benchmark_returns.index)
        if len(common) > 20:
            p = daily_returns.loc[common]
            b = benchmark_returns.loc[common]
            excess = p - b
            te = excess.std() * np.sqrt(252)
            ir = excess.mean() * 252 / te if te > 0 else 0
            # Up/down capture
            up_days = b > 0
            down_days = b < 0
            up_cap = p[up_days].mean() / b[up_days].mean() if up_days.sum() > 5 and abs(b[up_days].mean()) > 1e-10 else 1
            dn_cap = p[down_days].mean() / b[down_days].mean() if down_days.sum() > 5 and abs(b[down_days].mean()) > 1e-10 else 1
            result["Info Ratio"] = ir
            result["Tracking Error"] = te
            result["Up Capture"] = up_cap
            result["Down Capture"] = dn_cap

    return result


# Cost-adjusted return series
COST_BPS = 10  # 10 bps per round-trip (institutional)

def _apply_transaction_costs(wf_results, weight_history, cost_bps=COST_BPS):
    """Subtract estimated transaction costs from walk-forward returns.

    At each rebalance, computes turnover from weight changes and deducts
    turnover * cost_bps from that period's returns (spread across days).
    """
    net_results = {}
    for method in wf_results:
        if not wf_results[method]:
            continue
        gross = pd.DataFrame(wf_results[method]).set_index("date")
        gross = gross[~gross.index.duplicated(keep="first")]

        hist = weight_history.get(method, [])
        # Build turnover at each rebalance
        rebal_costs = {}  # date -> cost drag (fraction)
        for j in range(1, len(hist)):
            prev_w = np.array(list(hist[j - 1]["weights"].values()))
            curr_w = np.array(list(hist[j]["weights"].values()))
            turnover = np.sum(np.abs(curr_w - prev_w)) / 2
            cost = turnover * cost_bps / 10000  # convert bps to fraction
            rebal_costs[hist[j]["date"]] = cost

        if not rebal_costs:
            net_results[method] = gross["return"]
            continue

        # Spread each rebalance cost over the following period's days
        net_ret = gross["return"].copy()
        rebal_dates_sorted = sorted(rebal_costs.keys())
        for k, rd in enumerate(rebal_dates_sorted):
            end = rebal_dates_sorted[k + 1] if k < len(rebal_dates_sorted) - 1 else net_ret.index[-1]
            period_mask = (net_ret.index >= rd) & (net_ret.index <= end)
            n_period_days = period_mask.sum()
            if n_period_days > 0:
                daily_drag = rebal_costs[rd] / n_period_days
                net_ret.loc[period_mask] -= daily_drag

        net_results[method] = net_ret
    return net_results


# ═══════════════════════════════════════════════
# DE PRADO STATISTICAL TESTS
# ═══════════════════════════════════════════════

def _deflated_sharpe_ratio(observed_sr, n_obs, n_trials, skew=0, kurtosis=3):
    """Deflated Sharpe Ratio (de Prado & Bailey, 2014).

    Adjusts the observed Sharpe for multiple testing bias. When you test N
    strategies, the best Sharpe is inflated by selection bias. DSR gives the
    probability that the observed Sharpe exceeds what you'd expect from pure
    noise given the number of trials.

    Returns: p-value (probability the Sharpe is real, not noise).
    Higher = more likely genuine. DSR > 0.95 = statistically significant.
    """
    from scipy.stats import norm

    # Expected max Sharpe under null (Euler-Mascheroni approximation)
    if n_trials <= 1:
        e_max_sr = 0
    else:
        euler_m = 0.5772156649
        e_max_sr = (1 - euler_m) * norm.ppf(1 - 1 / n_trials) + euler_m * norm.ppf(1 - 1 / (n_trials * np.e))

    # Standard error of Sharpe (accounting for non-normality)
    se_sr = np.sqrt((1 + 0.5 * observed_sr ** 2 - skew * observed_sr +
                     (kurtosis - 3) / 4 * observed_sr ** 2) / (n_obs - 1))

    if se_sr < 1e-10:
        return 0.0

    test_stat = (observed_sr - e_max_sr) / se_sr
    return float(norm.cdf(test_stat))


def _min_track_record(observed_sr, n_obs, skew=0, kurtosis=3, confidence=0.95):
    """Minimum Track Record Length (de Prado, AFML Ch. 14).

    How many observations (trading days) are needed to be confident the
    Sharpe ratio is genuinely positive at the given confidence level.

    Returns: minimum days needed. If actual n_obs > this, Sharpe is credible.
    """
    from scipy.stats import norm

    z = norm.ppf(confidence)
    if observed_sr <= 0:
        return float("inf")
    min_n = (1 + (1 - skew * observed_sr + (kurtosis - 3) / 4 * observed_sr ** 2)) * (z / observed_sr) ** 2
    return max(1, min_n)


def _pbo_cpcv(daily_returns_dict, n_splits=6):
    """Probability of Backtest Overfitting via Combinatorially Purged Cross-Validation.

    Splits the OOS return series into n_splits blocks. For each combination of
    train/test blocks, checks if the best in-sample method also wins out-of-sample.
    PBO = fraction of combinations where the best IS method underperforms OOS.

    PBO > 0.50 = more likely overfit than not. PBO < 0.25 = robust.
    """
    from itertools import combinations

    methods = list(daily_returns_dict.keys())
    if len(methods) < 2:
        return None, None

    # Align all methods to common dates
    aligned = pd.DataFrame(daily_returns_dict).dropna()
    if len(aligned) < n_splits * 10:
        return None, None

    block_size = len(aligned) // n_splits
    blocks = [aligned.iloc[i * block_size:(i + 1) * block_size] for i in range(n_splits)]

    n_train = n_splits // 2
    combos = list(combinations(range(n_splits), n_train))
    overfit_count = 0
    logits = []

    for train_idx in combos:
        test_idx = tuple(i for i in range(n_splits) if i not in train_idx)
        train_data = pd.concat([blocks[i] for i in train_idx])
        test_data = pd.concat([blocks[i] for i in test_idx])

        # In-sample Sharpe for each method
        is_sharpe = {m: train_data[m].mean() / train_data[m].std() * np.sqrt(252)
                     if train_data[m].std() > 0 else 0 for m in methods}
        # Out-of-sample Sharpe
        oos_sharpe = {m: test_data[m].mean() / test_data[m].std() * np.sqrt(252)
                      if test_data[m].std() > 0 else 0 for m in methods}

        best_is = max(is_sharpe, key=is_sharpe.get)
        # Rank of best-IS method in OOS
        oos_ranked = sorted(oos_sharpe, key=oos_sharpe.get, reverse=True)
        oos_rank = oos_ranked.index(best_is) if best_is in oos_ranked else 0

        # Overfit if best IS method ranks in bottom half OOS
        if oos_rank >= len(methods) // 2:
            overfit_count += 1

        # Logit for PBO distribution
        w_bar = oos_rank / (len(methods) - 1) if len(methods) > 1 else 0.5
        if 0 < w_bar < 1:
            logits.append(np.log(w_bar / (1 - w_bar)))

    pbo = overfit_count / len(combos) if combos else None
    return pbo, logits


def _sequential_bootstrap_ci(daily_returns, n_bootstrap=2000, confidence=0.90, seed=42):
    """Block bootstrap confidence interval preserving serial dependence.

    Uses overlapping blocks of ~20 days to maintain autocorrelation structure.
    Returns (sharpe_ci_low, sharpe_ci_high, p_value_positive).
    """
    rng = np.random.default_rng(seed)
    n = len(daily_returns)
    block_size = min(20, n // 5)
    if block_size < 2 or n < 40:
        return None, None, None

    boot_sharpes = []
    for _ in range(n_bootstrap):
        # Draw random block starts
        n_blocks = n // block_size + 1
        starts = rng.integers(0, n - block_size, n_blocks)
        sample = np.concatenate([daily_returns.values[s:s + block_size] for s in starts])[:n]
        if sample.std() > 0:
            boot_sharpes.append(sample.mean() / sample.std() * np.sqrt(252))

    if not boot_sharpes:
        return None, None, None

    alpha = (1 - confidence) / 2
    ci_lo = np.percentile(boot_sharpes, alpha * 100)
    ci_hi = np.percentile(boot_sharpes, (1 - alpha) * 100)
    p_positive = np.mean([s > 0 for s in boot_sharpes])
    return ci_lo, ci_hi, p_positive


def _run_walkforward(returns, est_days, rebal_period, use_dn, blends=None):
    """Run walk-forward backtest on a returns DataFrame for all methods.

    Returns dict of {method: pd.Series of daily returns (deduplicated)}.
    Returns empty dict if insufficient data.
    """
    tickers = returns.columns.tolist()
    if len(tickers) < 3 or len(returns) < est_days + 40:
        return {}

    rebal_groups = returns.resample(rebal_period).last()
    rebal_dates = []
    for period_end in rebal_groups.index:
        mask = returns.index <= period_end
        if mask.any():
            actual_date = returns.index[mask][-1]
            loc = returns.index.get_loc(actual_date)
            if loc >= est_days:
                rebal_dates.append(actual_date)

    if len(rebal_dates) < 2:
        return {}

    raw = {m: [] for m in BASE_METHODS + list((blends or {}).keys())}

    for i in range(len(rebal_dates)):
        rd = rebal_dates[i]
        rd_loc = returns.index.get_loc(rd)
        est_ret = returns.iloc[rd_loc - est_days:rd_loc]

        methods_w = _compute_all_weights(est_ret, tickers, use_dn, blends=blends)

        end = rebal_dates[i + 1] if i < len(rebal_dates) - 1 else returns.index[-1]
        oos = returns.loc[rd:end]

        for method, w in methods_w.items():
            port_ret = oos.values @ w
            for j, dt in enumerate(oos.index):
                raw[method].append({"date": dt, "return": port_ret[j]})

    result = {}
    for method, data in raw.items():
        if not data:
            continue
        df = pd.DataFrame(data).set_index("date")
        df = df[~df.index.duplicated(keep="first")]
        result[method] = df["return"]
    return result


# ═══════════════════════════════════════════════
# FORECAST FUNCTIONS
# ═══════════════════════════════════════════════

@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_forecasts(tickers_tuple):
    """Fetch analyst targets, growth estimates, and valuation for each ticker."""
    import yfinance as yf
    from concurrent.futures import ThreadPoolExecutor

    def _fetch_one(t):
        try:
            info = yf.Ticker(t).info or {}
            current = info.get("currentPrice") or info.get("regularMarketPrice")
            target = info.get("targetMeanPrice")
            return {
                "ticker": t,
                "current_price": current,
                "target_price": target,
                "target_low": info.get("targetLowPrice"),
                "target_high": info.get("targetHighPrice"),
                "implied_return": (target / current - 1) if target and current and current > 0 else None,
                "n_analysts": info.get("numberOfAnalystOpinions"),
                "rec_mean": info.get("recommendationMean"),  # 1=strong buy, 5=sell
                "forward_pe": info.get("forwardPE"),
                "trailing_pe": info.get("trailingPE"),
                "earnings_growth": (info.get("earningsGrowth") or 0) * 100 if info.get("earningsGrowth") else None,
                "revenue_growth": (info.get("revenueGrowth") or 0) * 100 if info.get("revenueGrowth") else None,
                "sector": info.get("sector"),
            }
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(_fetch_one, list(tickers_tuple)))
    rows = [r for r in results if r is not None]
    return pd.DataFrame(rows) if rows else pd.DataFrame()


@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_macro_context():
    """Fetch key macro indicators for forward outlook."""
    from src.market_data import fetch_fred_series
    data = {}
    for sid, label in [("T10Y2Y", "yield_curve"), ("VIXCLS", "vix"),
                        ("DFF", "fed_funds"), ("DGS10", "ten_year")]:
        try:
            s = fetch_fred_series(sid, 60)
            if s is not None and not s.empty and "value" in s.columns:
                vals = s["value"].dropna()
                if not vals.empty:
                    data[label] = float(vals.iloc[-1])
        except Exception:
            pass
    return data


def _build_forecast_returns(forecast_df, eps_df, tickers, hist_mu, macro):
    """Build blended annualized return forecasts for each ticker.

    Components (annual):
      1. Analyst implied return (target / price - 1)     40%
      2. EPS revision momentum                           30%
      3. Valuation signal (fwd PE vs median)             20%
      4. Macro overlay (yield curve, VIX)                10%

    Returns (forecast_annual, forecast_daily, component_df).
    """
    n = len(tickers)
    analyst_ret = np.full(n, np.nan)
    eps_signal = np.zeros(n)
    val_signal = np.zeros(n)

    # ── Component 1: Analyst implied return ──
    if not forecast_df.empty:
        fc_idx = forecast_df.set_index("ticker") if "ticker" in forecast_df.columns else forecast_df
        for i, t in enumerate(tickers):
            if t in fc_idx.index:
                ir = fc_idx.loc[t].get("implied_return") if not isinstance(fc_idx.loc[t], pd.DataFrame) else None
                if ir is not None and pd.notna(ir):
                    analyst_ret[i] = ir

    # ── Component 2: EPS revision momentum ──
    if not eps_df.empty:
        ep_idx = eps_df.set_index("ticker") if "ticker" in eps_df.columns else eps_df
        for i, t in enumerate(tickers):
            if t in ep_idx.index:
                net = ep_idx.loc[t].get("net_30d", 0) if not isinstance(ep_idx.loc[t], pd.DataFrame) else 0
                if pd.notna(net):
                    if net > 3:    eps_signal[i] = 0.08
                    elif net > 0:  eps_signal[i] = 0.04
                    elif net < -3: eps_signal[i] = -0.08
                    elif net < 0:  eps_signal[i] = -0.04

    # ── Component 3: Valuation signal ──
    if not forecast_df.empty and "forward_pe" in forecast_df.columns:
        fwd_pes = forecast_df.set_index("ticker")["forward_pe"].reindex(tickers)
        median_pe = fwd_pes.median()
        if pd.notna(median_pe) and median_pe > 0:
            for i, t in enumerate(tickers):
                pe = fwd_pes.get(t)
                if pd.notna(pe) and pe > 0:
                    ratio = pe / median_pe
                    if ratio < 0.5:   val_signal[i] = 0.06
                    elif ratio < 0.8: val_signal[i] = 0.03
                    elif ratio > 2.0: val_signal[i] = -0.06
                    elif ratio > 1.5: val_signal[i] = -0.03

    # ── Component 4: Macro overlay ──
    try:
        yc = float(macro.get("yield_curve", 0))
    except (TypeError, ValueError):
        yc = 0.0
    try:
        vix = float(macro.get("vix", 20))
    except (TypeError, ValueError):
        vix = 20.0
    if yc > 0.5 and vix < 20:
        macro_adj = 0.03
    elif yc > 0 and vix < 25:
        macro_adj = 0.01
    elif yc < 0 or vix > 30:
        macro_adj = -0.03
    else:
        macro_adj = 0.0

    # ── Blend ──
    # Fall back to historical mean (annualized) where analyst data is missing
    hist_annual = hist_mu * 252
    analyst_annual = np.where(np.isnan(analyst_ret), hist_annual, analyst_ret)

    forecast_annual = (
        0.40 * analyst_annual +
        0.30 * eps_signal +
        0.20 * val_signal +
        0.10 * macro_adj
    )
    forecast_annual = np.clip(forecast_annual, -0.50, 0.50)
    forecast_daily = forecast_annual / 252

    # Component breakdown for display
    components = pd.DataFrame({
        "Ticker": tickers,
        "Analyst Impl.": analyst_annual * 100,
        "EPS Momentum": eps_signal * 100,
        "Valuation": val_signal * 100,
        "Macro": macro_adj * 100,
        "Blended Forecast": forecast_annual * 100,
    })

    return forecast_annual, forecast_daily, components


# ═══════════════════════════════════════════════
# CONTROLS
# ═══════════════════════════════════════════════

with st.expander("Universe Selection", expanded=True):
    st.caption("Select preset groups and toggle individual tickers on/off.")

    # Group toggles
    group_cols = st.columns(5)
    default_groups = ["Multi-Asset", "Sector ETFs"]
    selected_groups = []
    for i, (gname, gtickers) in enumerate(PRESET_GROUPS.items()):
        col = group_cols[i % 5]
        if col.checkbox(gname, value=gname in default_groups, key=f"ma_g_{gname}"):
            selected_groups.append(gname)

    # Build ticker universe from selected groups
    group_tickers = []
    for g in selected_groups:
        group_tickers.extend(PRESET_GROUPS[g])
    all_tickers = sorted(set(group_tickers))

    # Sync multiselect when groups change — detect by comparing current
    # options to what was stored on the previous run
    _prev = st.session_state.get("_ma_prev_options", None)
    if _prev is None or set(all_tickers) != set(_prev):
        st.session_state["_ma_prev_options"] = all_tickers
        st.session_state["ma_tickers"] = all_tickers
        # Clear stale results so user must re-run with new universe
        st.session_state.pop("ma_loaded", None)

    # Individual toggle via multiselect (deselect tickers you don't want)
    active_tickers = st.multiselect(
        f"Active tickers ({len(all_tickers)} available)",
        options=all_tickers,
        key="ma_tickers",
    )

    # Custom additions
    custom_raw = st.text_input(
        "Add custom tickers (comma-separated)", "", key="ma_custom",
    )
    if custom_raw.strip():
        extra = [t.strip().upper() for t in custom_raw.split(",") if t.strip()]
        active_tickers = sorted(set(active_tickers + extra))

    st.caption(f"**{len(active_tickers)} tickers selected**")

# Walk-forward parameters
wf_c1, wf_c2, wf_c3 = st.columns([1, 1, 1])
with wf_c1:
    ma_lookback = st.selectbox("Lookback", ["1Y", "2Y", "3Y", "5Y"], index=1, key="ma_lookback")
with wf_c2:
    ma_rebal = st.selectbox("Rebalance", ["Monthly", "Quarterly"], key="ma_rebal")
with wf_c3:
    ma_est_days = st.selectbox("Estimation Window", [126, 189, 252, 504], index=2, key="ma_est",
                                format_func=lambda d: f"{d}D (~{d//21}M)")

opt_c1, opt_c2 = st.columns(2)
with opt_c1:
    ma_denoise = st.checkbox("Ledoit-Wolf Denoising", value=True, key="ma_denoise")
with opt_c2:
    use_forecasts = st.checkbox(
        "Use forward estimates for current weights",
        value=False, key="ma_use_forecasts",
        help="When enabled, Tangency and Robust Sharpe use analyst price targets, "
             "EPS revisions, valuation, and macro data instead of historical mean returns "
             "for the CURRENT portfolio. Walk-forward backtest is always historical (no look-ahead).",
    )

# ── Blended Portfolios ──
with st.expander("Blended Portfolios", expanded=False):
    st.caption(
        "Blend two or more methods into a single portfolio. "
        "Weights are applied to each method's output, then re-normalized."
    )
    blend_preset = st.selectbox(
        "Preset", list(BLEND_PRESETS.keys()), index=0, key="ma_blend_preset",
    )

    active_blends = {}
    if blend_preset == "Custom":
        st.markdown("**Build your blend:**")
        blend_methods = st.multiselect(
            "Methods to blend", BASE_METHODS,
            default=["Robust Sharpe", "Max Diversification"],
            key="ma_blend_methods",
        )
        if len(blend_methods) >= 2:
            blend_w = {}
            slider_cols = st.columns(len(blend_methods))
            for j, bm in enumerate(blend_methods):
                blend_w[bm] = slider_cols[j].slider(
                    bm, 0, 100, 100 // len(blend_methods), 5, key=f"ma_bw_{bm}",
                )
            total_bw = sum(blend_w.values())
            if total_bw > 0:
                norm_bw = {k: v / total_bw for k, v in blend_w.items()}
                blend_name = " + ".join(
                    f"{k} {norm_bw[k]*100:.0f}%" for k in norm_bw if norm_bw[k] > 0
                )
                active_blends[blend_name] = norm_bw
                st.caption(f"Blend: {blend_name}")
    else:
        components = BLEND_PRESETS[blend_preset]
        if components:
            active_blends[blend_preset] = components
            parts = " + ".join(f"{m} ({w*100:.0f}%)" for m, w in components.items())
            st.caption(f"Blend: {parts}")

# Assign colors to blends
for i, bname in enumerate(active_blends):
    if bname not in METHOD_COLORS:
        METHOD_COLORS[bname] = ["#ff8800", "#ff3388", "#33ff88", "#8833ff"][i % 4]

rank_metric = st.radio(
    "Rank by", ["Sharpe", "Ann. Return", "Sortino", "Calmar", "Max DD"],
    horizontal=True, key="ma_rank",
)

run_btn = st.button("Run Meta Analysis", type="primary", use_container_width=True, key="ma_run")

if run_btn:
    st.session_state["ma_loaded"] = True
if not st.session_state.get("ma_loaded"):
    st.info(f"Configure universe and click **Run Meta Analysis**.")
    st.stop()

if len(active_tickers) < 3:
    st.error("Need at least 3 tickers.")
    st.stop()


# ═══════════════════════════════════════════════
# DATA LOADING
# ═══════════════════════════════════════════════

lookback_map = {"1Y": "1y", "2Y": "2y", "3Y": "3y", "5Y": "5y"}

@st.cache_data(ttl=3600, show_spinner=False)
def _download_prices(tickers_tuple, period):
    import yfinance as yf
    data = yf.download(list(tickers_tuple), period=period, progress=False, threads=True)
    if data is None or data.empty:
        return pd.DataFrame()
    if isinstance(data.columns, pd.MultiIndex):
        return data["Close"]
    if "Close" in data.columns:
        return data[["Close"]].rename(columns={"Close": tickers_tuple[0]})
    return pd.DataFrame()

with st.spinner(f"Loading {len(active_tickers)} assets..."):
    prices = _download_prices(tuple(active_tickers), lookback_map[ma_lookback])

if prices.empty or prices.dropna(axis=1, how="all").shape[1] < 3:
    st.error("Insufficient price data returned.")
    st.stop()

prices = prices.dropna(axis=1, how="all")
returns = prices.pct_change().dropna()
tickers = returns.columns.tolist()
n_assets = len(tickers)

data_start = returns.index[0].strftime("%Y-%m-%d")
data_end = returns.index[-1].strftime("%Y-%m-%d")
n_days = len(returns)

st.caption(
    f"**{n_assets} assets** with data from **{data_start}** to **{data_end}** "
    f"({n_days} trading days). Rebalance: {ma_rebal}, Est. window: {ma_est_days}D."
)


# ═══════════════════════════════════════════════
# FORWARD ESTIMATES (if enabled)
# ═══════════════════════════════════════════════

forecast_daily = None  # None = use historical mean in optimizer
forecast_df = pd.DataFrame()
eps_df_forecast = pd.DataFrame()
macro_context = {}
forecast_components = pd.DataFrame()

if use_forecasts:
    with st.spinner("Fetching analyst targets, EPS revisions, and macro data..."):
        from src.market_data import fetch_eps_revisions as _fetch_eps_rev
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=3) as _ex:
            _fc_future = _ex.submit(_fetch_forecasts, tuple(tickers))
            _eps_future = _ex.submit(_fetch_eps_rev, tickers)
            _macro_future = _ex.submit(_fetch_macro_context)

        forecast_df = _fc_future.result()
        eps_df_forecast = _eps_future.result()
        macro_context = _macro_future.result()

        hist_mu = returns.mean().values
        forecast_annual, forecast_daily, forecast_components = _build_forecast_returns(
            forecast_df, eps_df_forecast, tickers, hist_mu, macro_context,
        )

    st.caption(
        "Forward estimates enabled — Tangency and Robust Sharpe use blended analyst/EPS/valuation/macro "
        "forecasts for **current weights only**. Walk-forward backtest remains purely historical."
    )


# ═══════════════════════════════════════════════
# WALK-FORWARD BACKTEST
# ═══════════════════════════════════════════════

rebal_period = "ME" if ma_rebal == "Monthly" else "QE"
rebal_groups = returns.resample(rebal_period).last()
rebal_dates = []
for period_end in rebal_groups.index:
    mask = returns.index <= period_end
    if mask.any():
        actual_date = returns.index[mask][-1]
        loc = returns.index.get_loc(actual_date)
        if loc >= ma_est_days:
            rebal_dates.append(actual_date)

if len(rebal_dates) < 3:
    st.error(
        f"Not enough data for walk-forward with {ma_est_days}D estimation window. "
        "Try a shorter window or longer lookback."
    )
    st.stop()

all_method_names = BASE_METHODS + list(active_blends.keys())
n_methods = len(all_method_names)

with st.spinner(f"Running walk-forward backtest ({len(rebal_dates)} rebalance points, {n_methods} methods)..."):
    wf_results = {m: [] for m in all_method_names}
    # Store weight snapshots: {method: [(date, {ticker: weight}), ...]}
    weight_history = {m: [] for m in all_method_names}

    for i in range(len(rebal_dates)):
        rd = rebal_dates[i]
        rd_loc = returns.index.get_loc(rd)
        est_ret = returns.iloc[rd_loc - ma_est_days:rd_loc]

        methods_w = _compute_all_weights(est_ret, tickers, ma_denoise, blends=active_blends)

        # Store weights for this rebalance
        for method, w in methods_w.items():
            weight_history[method].append({
                "date": rd,
                "weights": dict(zip(tickers, w)),
            })

        # Out-of-sample period
        if i < len(rebal_dates) - 1:
            oos = returns.loc[rd:rebal_dates[i + 1]]
        else:
            oos = returns.loc[rd:]

        for method, w in methods_w.items():
            port_ret = oos.values @ w
            for j, dt in enumerate(oos.index):
                wf_results[method].append({"date": dt, "return": port_ret[j]})

    # Compute current optimal weights (using all available data up to today)
    current_weights = _compute_all_weights(
        returns.iloc[-ma_est_days:], tickers, ma_denoise,
        blends=active_blends, forecast_mu=forecast_daily,
    )

# Build result DataFrames
cum_series = {}
method_daily = {}  # method -> pd.Series of daily returns (deduped)
for method in wf_results:
    if not wf_results[method]:
        continue
    df_m = pd.DataFrame(wf_results[method]).set_index("date")
    df_m = df_m[~df_m.index.duplicated(keep="first")]
    cum_series[method] = (1 + df_m["return"]).cumprod() * 100
    method_daily[method] = df_m["return"]

# ── SPY Buy & Hold benchmark ──
# Download SPY for the same period and align to the OOS date range
spy_prices = _download_prices(("SPY",), lookback_map[ma_lookback])
if not spy_prices.empty:
    spy_ret_full = spy_prices["SPY"].pct_change().dropna()
    # Align to the OOS period (same dates as walk-forward results)
    oos_dates = cum_series[list(cum_series.keys())[0]].index if cum_series else pd.DatetimeIndex([])
    spy_ret = spy_ret_full.reindex(oos_dates).dropna()
    if len(spy_ret) > 20:
        method_daily["SPY Buy & Hold"] = spy_ret
        cum_series["SPY Buy & Hold"] = (1 + spy_ret).cumprod() * 100
        METHOD_COLORS["SPY Buy & Hold"] = "#ffffff"

# Benchmark = Equal Weight (for IR / capture ratios)
benchmark_ret = method_daily.get("Equal Weight")

# Gross metrics (with institutional metrics vs benchmark)
metrics_list = []
for method, daily_ret in method_daily.items():
    metrics_list.append(_portfolio_metrics(daily_ret, method, benchmark_returns=benchmark_ret))

# Net-of-cost series
net_daily = _apply_transaction_costs(wf_results, weight_history)
# SPY buy & hold has zero transaction cost
if "SPY Buy & Hold" in method_daily:
    net_daily["SPY Buy & Hold"] = method_daily["SPY Buy & Hold"]
net_cum_series = {}
net_metrics_list = []
for method, net_ret in net_daily.items():
    net_cum_series[method] = (1 + net_ret).cumprod() * 100
    nm = _portfolio_metrics(net_ret, method, benchmark_returns=benchmark_ret)
    nm["Method"] = f"{method}"
    net_metrics_list.append(nm)

metrics_df = pd.DataFrame(metrics_list)

# Rank
rank_col_map = {
    "Sharpe": "Sharpe", "Ann. Return": "Ann. Return",
    "Sortino": "Sortino", "Calmar": "Calmar", "Max DD": "Max DD",
}
sort_col = rank_col_map[rank_metric]
ascending = True if sort_col == "Max DD" else False
metrics_df = metrics_df.sort_values(sort_col, ascending=not ascending if sort_col == "Max DD" else ascending)
ranked_methods = metrics_df["Method"].tolist()


# ═══════════════════════════════════════════════
# EXECUTIVE SUMMARY
# ═══════════════════════════════════════════════

best = metrics_df.iloc[0]
spy_row = metrics_df[metrics_df["Method"] == "SPY Buy & Hold"].iloc[0] if "SPY Buy & Hold" in metrics_df["Method"].values else None

# Header metrics
sm1, sm2, sm3, sm4, sm5 = st.columns(5)
sm1.metric("Best Method", best["Method"])
sm2.metric("Sharpe", f"{best['Sharpe']:.2f}")
sm3.metric("Ann. Return", f"{best['Ann. Return'] * 100:.1f}%")
sm4.metric("Max DD", f"{best['Max DD'] * 100:.1f}%")
sm5.metric("Sortino", f"{best['Sortino']:.2f}")

# Comparison to SPY
if spy_row is not None:
    spy_sharpe = spy_row["Sharpe"]
    spy_ret = spy_row["Ann. Return"]
    spy_dd = spy_row["Max DD"]
    sharpe_edge = best["Sharpe"] - spy_sharpe
    ret_edge = (best["Ann. Return"] - spy_ret) * 100
    dd_edge = (best["Max DD"] - spy_dd) * 100  # less negative = better

    beats_spy = best["Sharpe"] > spy_sharpe

    summary_color = "#00ff88" if beats_spy else "#ff4444"
    edge_color = "#00ff88" if beats_spy else "#ff4444"
    neutral = COLORS["text_muted"]
    st.markdown(
        f'<div style="background:{COLORS["card_bg"]};border:1px solid {summary_color};'
        f'border-radius:8px;padding:16px 20px;margin:8px 0 16px 0;">'
        f'<div style="color:{summary_color};font-weight:700;font-size:1.15rem;margin-bottom:10px;">'
        f'{"OUTPERFORMS" if beats_spy else "UNDERPERFORMS"} SPY BUY & HOLD</div>'
        f'<table style="width:100%;border-collapse:collapse;color:{COLORS["text_primary"]};font-size:0.95rem;">'
        f'<tr style="border-bottom:1px solid {COLORS["card_border"]};">'
        f'<td style="padding:6px 0;"><b>{best["Method"]}</b></td>'
        f'<td style="padding:6px 0;color:{neutral};">vs SPY</td>'
        f'<td style="padding:6px 0;color:{edge_color};text-align:right;">Edge</td></tr>'
        f'<tr style="border-bottom:1px solid {COLORS["card_border"]};">'
        f'<td style="padding:6px 0;">Sharpe <b>{best["Sharpe"]:.2f}</b></td>'
        f'<td style="padding:6px 0;color:{neutral};">{spy_sharpe:.2f}</td>'
        f'<td style="padding:6px 0;color:{edge_color};text-align:right;">{sharpe_edge:+.2f}</td></tr>'
        f'<tr style="border-bottom:1px solid {COLORS["card_border"]};">'
        f'<td style="padding:6px 0;">Return <b>{best["Ann. Return"]*100:+.1f}%</b></td>'
        f'<td style="padding:6px 0;color:{neutral};">{spy_ret*100:+.1f}%</td>'
        f'<td style="padding:6px 0;color:{edge_color};text-align:right;">{ret_edge:+.1f}pp</td></tr>'
        f'<tr>'
        f'<td style="padding:6px 0;">Max DD <b>{best["Max DD"]*100:.1f}%</b></td>'
        f'<td style="padding:6px 0;color:{neutral};">{spy_dd*100:.1f}%</td>'
        f'<td style="padding:6px 0;color:{edge_color};text-align:right;">{dd_edge:+.1f}pp</td></tr>'
        f'</table></div>',
        unsafe_allow_html=True,
    )

# Current best portfolio weights (quick view)
if best["Method"] in current_weights:
    best_w = pd.Series(current_weights[best["Method"]], index=tickers)
    best_w_nz = best_w[best_w > 0.005].sort_values(ascending=False)

    with st.expander(f"Current {best['Method']} Portfolio — {len(best_w_nz)} positions", expanded=True):
        pw_cols = st.columns(min(len(best_w_nz), 8))
        for i, (t, w) in enumerate(best_w_nz.head(8).items()):
            pw_cols[i].metric(t, f"{w*100:.1f}%")
        if len(best_w_nz) > 8:
            st.caption(f"+ {len(best_w_nz) - 8} more positions. See Allocations tab for full detail.")

        # Download button
        download_df = pd.DataFrame({
            "Ticker": best_w_nz.index,
            "Weight": best_w_nz.values,
            "Weight %": (best_w_nz.values * 100).round(2),
            "Dollar Alloc ($100)": (best_w_nz.values * 100).round(2),
        })
        csv = download_df.to_csv(index=False)
        st.download_button(
            f"Download {best['Method']} Weights (CSV)",
            csv, f"portfolio_weights_{best['Method'].replace(' ', '_').lower()}.csv",
            "text/csv", key="ma_download_weights",
        )


# ═══════════════════════════════════════════════
# TABS
# ═══════════════════════════════════════════════

tab_equity, tab_alloc, tab_forecast, tab_perf, tab_inst, tab_deprado, tab_dd, tab_rolling, tab_grid = st.tabs([
    "Equity Curves", "Allocations", "Forecasts", "Performance",
    "Institutional", "Statistical Tests", "Drawdown", "Rolling Analysis", "Universe Grid",
])


# ═══════════════════════════════════════════════
# TAB 1: EQUITY CURVES
# ═══════════════════════════════════════════════
with tab_equity, error_boundary("Equity Curves"):
    st.subheader("Walk-Forward Equity Curves (P/L)")

    with st.expander("How to read this tab", expanded=False):
        st.markdown(
            "Each line is a portfolio allocation method, rebalanced "
            f"**{ma_rebal.lower()}** using a trailing **{ma_est_days}-day** "
            "estimation window. All methods start at $100.\n\n"
            "No look-ahead bias — weights are computed using only data available "
            "at each rebalance date. This is a true out-of-sample test.\n\n"
            f"Top methods (by {rank_metric}) are drawn with thicker lines."
        )

    # Top N selector
    n_total_methods = len(ranked_methods)
    top_n = st.slider("Show top N methods", 3, n_total_methods, min(n_total_methods, 10), key="ma_topn")
    show_methods = ranked_methods[:top_n]

    fig_eq = go.Figure()
    for rank, method in enumerate(ranked_methods):
        cum = cum_series[method]
        is_top = method in show_methods
        fig_eq.add_trace(go.Scatter(
            x=cum.index, y=cum.values, mode="lines",
            name=f"#{rank+1} {method}",
            line=dict(
                color=METHOD_COLORS.get(method, "#888"),
                width=3 if rank < 3 else 1.5,
            ),
            visible=True if is_top else "legendonly",
        ))

    fig_eq.add_hline(y=100, line_dash="dash", line_color="#333")
    fig_eq.update_layout(
        template="plotly_dark", height=500,
        title=f"Walk-Forward Equity Curves — {n_assets} Assets, {ma_rebal} Rebalance",
        yaxis_title="Portfolio Value ($100 start)",
        legend=dict(orientation="h", y=-0.15),
        margin=dict(l=0, r=0, t=40, b=0),
    )
    st.plotly_chart(fig_eq, use_container_width=True, config=PLOTLY_NOBAR)

    # Final values
    final_vals = {m: cum_series[m].iloc[-1] for m in ranked_methods}
    fv_cols = st.columns(min(5, top_n))
    for i, method in enumerate(show_methods[:5]):
        fv = final_vals[method]
        fv_cols[i].metric(
            method,
            f"${fv:.0f}",
            delta=f"{fv - 100:+.1f}%",
        )


# ═══════════════════════════════════════════════
# TAB 2: ALLOCATIONS
# ═══════════════════════════════════════════════
with tab_alloc, error_boundary("Allocations"):
    st.subheader("Current Optimal Portfolio")

    with st.expander("How to read this tab", expanded=False):
        st.markdown(
            "**Current portfolio** shows the weights each method recommends today, "
            f"estimated from the trailing {ma_est_days}-day window ending {data_end}.\n\n"
            "**Rebalance history** shows how weights evolved at each rebalance point. "
            "Stable weights across periods = robust allocation. "
            "Wildly shifting weights = sensitive to recent data.\n\n"
            "Use the method selector to focus on a specific strategy."
        )

    # ── Method selector ──
    alloc_options = [m for m in ranked_methods if m in current_weights]
    alloc_method = st.selectbox(
        "Method", alloc_options, index=0, key="ma_alloc_method",
    )

    # ── Current weights ──
    cw = current_weights[alloc_method]
    cw_series = pd.Series(cw, index=tickers).sort_values(ascending=False)
    cw_nonzero = cw_series[cw_series > 0.005]

    ac1, ac2 = st.columns([2, 1])
    with ac1:
        fig_cw = go.Figure(data=go.Pie(
            labels=cw_nonzero.index.tolist(),
            values=(cw_nonzero.values * 100).round(1),
            hole=0.45,
            textinfo="label+percent",
            textfont=dict(size=11),
            marker=dict(line=dict(color="#1a1a2e", width=2)),
        ))
        fig_cw.update_layout(
            template="plotly_dark", height=380,
            title=f"Current {alloc_method} Portfolio (as of {data_end})",
            margin=dict(l=0, r=0, t=40, b=0),
            showlegend=False,
        )
        st.plotly_chart(fig_cw, use_container_width=True, config=PLOTLY_NOBAR)

    with ac2:
        st.markdown(f"**{alloc_method} — Current Weights**")
        for t in cw_nonzero.index:
            w_pct = cw_nonzero[t] * 100
            st.markdown(f"**{t}** — {w_pct:.1f}%")
        st.caption(f"Positions > 0.5%: {len(cw_nonzero)} / {n_assets}")
        st.caption(f"Largest: {cw_nonzero.index[0]} ({cw_nonzero.iloc[0]*100:.1f}%)")
        hhi = np.sum(cw ** 2)
        st.caption(f"Effective N: {1/hhi:.1f}" if hhi > 0 else "")

    # ── Current weights table (all methods side-by-side) ──
    st.subheader("Current Weights — All Methods")
    st.caption("Side-by-side comparison of what each method recommends today.")

    all_cw = pd.DataFrame(
        {m: pd.Series(current_weights[m], index=tickers) for m in ranked_methods if m in current_weights}
    )
    # Sort by the selected method's weight
    all_cw = all_cw.sort_values(alloc_method, ascending=False)
    # Filter to tickers with >0.5% in at least one method
    significant = all_cw.max(axis=1) > 0.005
    all_cw_display = all_cw[significant].copy()
    for col in all_cw_display.columns:
        all_cw_display[col] = all_cw_display[col].apply(
            lambda v: f"{v*100:.1f}%" if v > 0.005 else "—"
        )
    st.dataframe(all_cw_display, use_container_width=True)

    # ── Rebalance weight history ──
    st.markdown("---")
    st.subheader(f"Rebalance History — {alloc_method}")
    st.caption(
        f"Weight snapshots at each rebalance date. "
        f"Showing top holdings per period for {alloc_method}."
    )

    hist = weight_history[alloc_method]
    if hist:
        # Build a DataFrame: rows = tickers, columns = rebalance dates
        hist_df = pd.DataFrame(
            {h["date"].strftime("%Y-%m-%d"): pd.Series(h["weights"]) for h in hist}
        )
        # Sort by most recent weight
        last_col = hist_df.columns[-1]
        hist_df = hist_df.sort_values(last_col, ascending=False)

        # Filter to significant positions (>2% in at least one period)
        sig_mask = hist_df.max(axis=1) > 0.02
        hist_sig = hist_df[sig_mask]

        # Heatmap
        fig_hist = go.Figure(data=go.Heatmap(
            z=hist_sig.values * 100,
            x=hist_sig.columns.tolist(),
            y=hist_sig.index.tolist(),
            colorscale=[[0, "#1a1a2e"], [0.5, "#00d1ff"], [1, "#00ff88"]],
            zmin=0,
            text=[[f"{v*100:.1f}" if v > 0.005 else "" for v in row] for row in hist_sig.values],
            texttemplate="%{text}", textfont={"size": 9},
            colorbar=dict(title="Weight %"),
        ))
        fig_hist.update_layout(
            template="plotly_dark",
            height=max(350, len(hist_sig) * 22),
            title=f"{alloc_method} — Weight Evolution Across Rebalances",
            xaxis_title="Rebalance Date",
            margin=dict(l=0, r=0, t=40, b=0),
        )
        st.plotly_chart(fig_hist, use_container_width=True, config=PLOTLY_NOBAR)

        # Turnover between periods
        st.subheader("Portfolio Turnover")
        turnover_data = []
        for j in range(1, len(hist)):
            prev_w = np.array(list(hist[j-1]["weights"].values()))
            curr_w = np.array(list(hist[j]["weights"].values()))
            turnover = np.sum(np.abs(curr_w - prev_w)) / 2  # one-way turnover
            turnover_data.append({
                "date": hist[j]["date"],
                "turnover": turnover,
            })

        if turnover_data:
            to_df = pd.DataFrame(turnover_data)
            fig_to = go.Figure()
            fig_to.add_trace(go.Bar(
                x=to_df["date"], y=to_df["turnover"] * 100,
                marker_color=METHOD_COLORS.get(alloc_method, "#00d1ff"),
                text=[f"{v*100:.0f}%" for v in to_df["turnover"]],
                textposition="outside",
            ))
            fig_to.update_layout(
                template="plotly_dark", height=280,
                title=f"{alloc_method} — One-Way Turnover per Rebalance",
                yaxis_title="Turnover (%)",
                margin=dict(l=0, r=0, t=40, b=0),
            )
            st.plotly_chart(fig_to, use_container_width=True, config=PLOTLY_NOBAR)

            avg_to = np.mean([d["turnover"] for d in turnover_data])
            st.caption(
                f"Average one-way turnover: **{avg_to*100:.1f}%** per rebalance. "
                f"Estimated annual cost at 10bps: **{avg_to * (12 if ma_rebal == 'Monthly' else 4) * 10 / 100:.2f}%**."
            )

        # Full weight table (expandable)
        with st.expander("Full Weight History Table"):
            hist_display = hist_df.copy()
            for col in hist_display.columns:
                hist_display[col] = hist_display[col].apply(
                    lambda v: f"{v*100:.1f}%" if v > 0.005 else "—"
                )
            st.dataframe(hist_display, use_container_width=True)


# ═══════════════════════════════════════════════
# TAB 3: FORECASTS
# ═══════════════════════════════════════════════
with tab_forecast, error_boundary("Forecasts"):
    st.subheader("Forward Return Estimates")

    if not use_forecasts or forecast_components.empty:
        st.info(
            "Enable **Use forward estimates** in the controls above and click "
            "**Run Meta Analysis** to see forecasted returns.\n\n"
            "The forecast model blends:\n"
            "- **Analyst price targets** (40%) — consensus 12-month target vs current price\n"
            "- **EPS revision momentum** (30%) — net upgrades/downgrades in last 30 days\n"
            "- **Valuation signal** (20%) — forward P/E vs universe median\n"
            "- **Macro overlay** (10%) — yield curve slope and VIX regime"
        )
    else:
        with st.expander("How to read this tab", expanded=False):
            st.markdown(
                "Each ticker gets a **blended annualized return forecast** from 4 components:\n\n"
                "| Component | Weight | Source | Logic |\n"
                "|-----------|--------|--------|-------|\n"
                "| **Analyst Implied** | 40% | yfinance consensus target | (target / price) - 1 |\n"
                "| **EPS Momentum** | 30% | EPS revision counts | Net positive → outperformance |\n"
                "| **Valuation** | 20% | Forward P/E vs median | Cheap → positive, expensive → negative |\n"
                "| **Macro** | 10% | FRED yield curve + VIX | Risk-on / risk-off regime |\n\n"
                "Tickers without analyst coverage fall back to the historical mean return. "
                "Forecasts are capped at +/-50% annual.\n\n"
                "When **Use forward estimates** is on, Tangency and Robust Sharpe use these "
                "forecasts for **current weights only**. The walk-forward backtest is always historical."
            )

        # ── Macro context ──
        st.markdown("**Macro Context**")
        mc1, mc2, mc3, mc4 = st.columns(4)
        yc = macro_context.get("yield_curve", None)
        vix = macro_context.get("vix", None)
        ff = macro_context.get("fed_funds", None)
        ty = macro_context.get("ten_year", None)
        mc1.metric("Yield Curve (10Y-2Y)", f"{yc:.2f}%" if yc is not None else "N/A",
                    delta="Positive" if yc and yc > 0 else "Inverted" if yc and yc < 0 else None)
        mc2.metric("VIX", f"{vix:.1f}" if vix is not None else "N/A")
        mc3.metric("Fed Funds", f"{ff:.2f}%" if ff is not None else "N/A")
        mc4.metric("10Y Treasury", f"{ty:.2f}%" if ty is not None else "N/A")

        macro_adj = forecast_components["Macro"].iloc[0] if not forecast_components.empty else 0
        if macro_adj > 0:
            st.caption(f"Macro overlay: **+{macro_adj:.0f}%** (favorable — positive yield curve, low VIX)")
        elif macro_adj < 0:
            st.caption(f"Macro overlay: **{macro_adj:.0f}%** (unfavorable — inverted curve or elevated VIX)")
        else:
            st.caption("Macro overlay: **0%** (neutral conditions)")

        # ── Forecast bar chart ──
        st.markdown("---")
        st.subheader("Blended Forecast Returns (Annualized)")

        fc_sorted = forecast_components.set_index("Ticker").sort_values("Blended Forecast", ascending=True)

        fig_fc = go.Figure()
        fig_fc.add_trace(go.Bar(
            y=fc_sorted.index, x=fc_sorted["Blended Forecast"], orientation="h",
            marker_color=[
                "#00ff88" if v > 5 else "#00d1ff" if v > 0 else "#ff4444"
                for v in fc_sorted["Blended Forecast"]
            ],
            text=[f"{v:+.1f}%" for v in fc_sorted["Blended Forecast"]],
            textposition="outside",
        ))
        fig_fc.add_vline(x=0, line_dash="dash", line_color="#555")
        fig_fc.update_layout(
            template="plotly_dark", height=max(350, len(fc_sorted) * 22),
            title="Blended Annualized Return Forecast by Ticker",
            xaxis_title="Forecast Return (%)",
            margin=dict(l=0, r=60, t=40, b=0),
        )
        st.plotly_chart(fig_fc, use_container_width=True, config=PLOTLY_NOBAR)

        # ── Component breakdown ──
        st.subheader("Forecast Component Breakdown")

        fig_comp = go.Figure()
        for comp, color in [("Analyst Impl.", "#00d1ff"), ("EPS Momentum", "#00ff88"),
                             ("Valuation", "#ffaa00"), ("Macro", "#ff6b6b")]:
            fig_comp.add_trace(go.Bar(
                y=fc_sorted.index, x=fc_sorted[comp], orientation="h",
                name=comp, marker_color=color,
            ))
        fig_comp.update_layout(
            template="plotly_dark", height=max(350, len(fc_sorted) * 22),
            barmode="relative",
            title="Forecast Components (stacked contribution, %)",
            xaxis_title="Return Contribution (%)",
            legend=dict(orientation="h", y=-0.1),
            margin=dict(l=0, r=0, t=40, b=0),
        )
        st.plotly_chart(fig_comp, use_container_width=True, config=PLOTLY_NOBAR)

        # ── Historical vs Forecast comparison ──
        st.subheader("Historical Return vs Forecast")
        hist_ann = returns.mean() * 252 * 100
        fc_ann = forecast_components.set_index("Ticker")["Blended Forecast"]

        fig_hvf = go.Figure()
        comp_tickers = sorted(set(hist_ann.index) & set(fc_ann.index))
        fig_hvf.add_trace(go.Bar(
            x=comp_tickers, y=[hist_ann.get(t, 0) for t in comp_tickers],
            name="Historical", marker_color="#555",
        ))
        fig_hvf.add_trace(go.Bar(
            x=comp_tickers, y=[fc_ann.get(t, 0) for t in comp_tickers],
            name="Forecast", marker_color="#00d1ff",
        ))
        fig_hvf.update_layout(
            template="plotly_dark", height=380, barmode="group",
            title="Historical (Sample Mean) vs Blended Forecast — Annualized %",
            yaxis_title="Return (%)",
            legend=dict(orientation="h", y=-0.12),
            margin=dict(l=0, r=0, t=40, b=0),
        )
        st.plotly_chart(fig_hvf, use_container_width=True, config=PLOTLY_NOBAR)

        # ── Analyst coverage detail ──
        st.subheader("Analyst Coverage & Targets")
        if not forecast_df.empty:
            cov_df = forecast_df.copy()
            display_cols = {
                "ticker": "Ticker", "current_price": "Price", "target_price": "Target",
                "target_low": "Low", "target_high": "High",
                "implied_return": "Implied Ret", "n_analysts": "Analysts",
                "rec_mean": "Rec (1-5)", "forward_pe": "Fwd P/E",
                "earnings_growth": "Earn Growth %", "revenue_growth": "Rev Growth %",
            }
            avail_cols = [c for c in display_cols if c in cov_df.columns]
            cov_display = cov_df[avail_cols].rename(columns=display_cols)
            for col in ["Price", "Target", "Low", "High"]:
                if col in cov_display.columns:
                    cov_display[col] = cov_display[col].apply(
                        lambda v: f"${v:.2f}" if pd.notna(v) else "—"
                    )
            if "Implied Ret" in cov_display.columns:
                cov_display["Implied Ret"] = cov_display["Implied Ret"].apply(
                    lambda v: f"{v*100:+.1f}%" if pd.notna(v) else "—"
                )
            if "Rec (1-5)" in cov_display.columns:
                cov_display["Rec (1-5)"] = cov_display["Rec (1-5)"].apply(
                    lambda v: f"{v:.1f}" if pd.notna(v) else "—"
                )
            for col in ["Fwd P/E"]:
                if col in cov_display.columns:
                    cov_display[col] = cov_display[col].apply(
                        lambda v: f"{v:.1f}" if pd.notna(v) else "—"
                    )
            for col in ["Earn Growth %", "Rev Growth %"]:
                if col in cov_display.columns:
                    cov_display[col] = cov_display[col].apply(
                        lambda v: f"{v:+.1f}%" if pd.notna(v) else "—"
                    )
            st.dataframe(cov_display, use_container_width=True, hide_index=True)

        # ── Full forecast table ──
        with st.expander("Full Forecast Breakdown"):
            fc_display = forecast_components.copy()
            for col in fc_display.columns:
                if col != "Ticker":
                    fc_display[col] = fc_display[col].apply(
                        lambda v: f"{v:+.1f}%" if pd.notna(v) else "—"
                    )
            st.dataframe(fc_display, use_container_width=True, hide_index=True)


# ═══════════════════════════════════════════════
# TAB 4: PERFORMANCE
# ═══════════════════════════════════════════════
with tab_perf, error_boundary("Performance"):
    st.subheader("Performance Comparison")

    with st.expander("How to read this tab", expanded=False):
        st.markdown(
            f"All {len(ranked_methods)} methods ranked by **{rank_metric}** (descending). "
            "Metrics are computed from the full out-of-sample period.\n\n"
            "- **Sharpe**: return per unit of total risk\n"
            "- **Sortino**: return per unit of downside risk only\n"
            "- **Calmar**: return per unit of max drawdown\n"
            "- **Win Rate**: % of days with positive return"
        )

    # Format for display
    display_df = metrics_df.copy()
    display_df["Rank"] = range(1, len(display_df) + 1)
    display_df["Ann. Return"] = display_df["Ann. Return"].apply(lambda v: f"{v * 100:+.1f}%")
    display_df["Ann. Vol"] = display_df["Ann. Vol"].apply(lambda v: f"{v * 100:.1f}%")
    display_df["Sharpe"] = display_df["Sharpe"].apply(lambda v: f"{v:.2f}")
    display_df["Sortino"] = display_df["Sortino"].apply(lambda v: f"{v:.2f}")
    display_df["Max DD"] = display_df["Max DD"].apply(lambda v: f"{v * 100:.1f}%")
    display_df["Calmar"] = display_df["Calmar"].apply(lambda v: f"{v:.2f}")
    display_df["Win Rate"] = display_df["Win Rate"].apply(lambda v: f"{v * 100:.0f}%")

    col_order = ["Rank", "Method", "Ann. Return", "Ann. Vol", "Sharpe",
                 "Sortino", "Max DD", "Calmar", "Win Rate"]
    st.dataframe(display_df[col_order], use_container_width=True, hide_index=True)

    # Bar chart comparison
    st.subheader("Key Metrics Comparison")
    raw_metrics = pd.DataFrame(metrics_list).set_index("Method").loc[ranked_methods]

    fig_bars = go.Figure()
    bar_metrics = [("Sharpe", "#00d1ff"), ("Sortino", "#00ff88"), ("Calmar", "#ffaa00")]
    for metric_name, color in bar_metrics:
        fig_bars.add_trace(go.Bar(
            x=raw_metrics.index, y=raw_metrics[metric_name],
            name=metric_name, marker_color=color,
        ))
    fig_bars.update_layout(
        template="plotly_dark", height=380, barmode="group",
        title="Risk-Adjusted Return Metrics by Method",
        yaxis_title="Ratio",
        legend=dict(orientation="h", y=-0.15),
        margin=dict(l=0, r=0, t=40, b=0),
    )
    st.plotly_chart(fig_bars, use_container_width=True, config=PLOTLY_NOBAR)


# ═══════════════════════════════════════════════
# TAB 5: INSTITUTIONAL ANALYTICS
# ═══════════════════════════════════════════════
with tab_inst, error_boundary("Institutional"):
    st.subheader("Institutional Analytics")

    with st.expander("How to read this tab", expanded=False):
        st.markdown(
            "**Net-of-cost curves** subtract estimated transaction costs (turnover x 10bps) "
            "from walk-forward returns. High-turnover methods lose their edge after costs.\n\n"
            "**Regime analysis** splits performance by market regime (bull/bear/high-vol/low-vol). "
            "A method with great overall Sharpe but terrible bear-market returns is a liability.\n\n"
            "**Capture ratios** measure how much upside you keep vs how much downside you absorb, "
            "benchmarked to Equal Weight. Up capture > 100% and down capture < 100% = ideal.\n\n"
            "**Information ratio** = excess return / tracking error vs benchmark. "
            "IR > 0.5 is good, IR > 1.0 is exceptional."
        )

    # ═══════════════════════════════════════
    # 1. NET-OF-COST EQUITY CURVES
    # ═══════════════════════════════════════
    st.subheader("1. Net-of-Cost Equity Curves")
    st.caption(f"Gross returns minus estimated transaction costs ({COST_BPS}bps per round-trip turnover).")

    fig_net = go.Figure()
    for rank, method in enumerate(ranked_methods):
        if method in cum_series:
            fig_net.add_trace(go.Scatter(
                x=cum_series[method].index, y=cum_series[method].values, mode="lines",
                name=f"{method} (gross)",
                line=dict(color=METHOD_COLORS.get(method, "#888"), width=1, dash="dot"),
                legendgroup=method, showlegend=False,
            ))
        if method in net_cum_series:
            fig_net.add_trace(go.Scatter(
                x=net_cum_series[method].index, y=net_cum_series[method].values, mode="lines",
                name=f"{method} (net)",
                line=dict(color=METHOD_COLORS.get(method, "#888"), width=2 if rank < 3 else 1),
                legendgroup=method,
            ))
    fig_net.add_hline(y=100, line_dash="dash", line_color="#333")
    fig_net.update_layout(
        template="plotly_dark", height=450,
        title=f"Gross (dotted) vs Net-of-Cost (solid) — {COST_BPS}bps Round-Trip",
        yaxis_title="Portfolio Value ($100 start)",
        legend=dict(orientation="h", y=-0.15),
        margin=dict(l=0, r=0, t=40, b=0),
    )
    st.plotly_chart(fig_net, use_container_width=True, config=PLOTLY_NOBAR)

    # Cost drag table
    cost_rows = []
    for method in ranked_methods:
        gross_m = next((m for m in metrics_list if m["Method"] == method), None)
        net_m = next((m for m in net_metrics_list if m["Method"] == method), None)
        if gross_m and net_m:
            drag = (gross_m["Ann. Return"] - net_m["Ann. Return"]) * 100
            cost_rows.append({
                "Method": method,
                "Gross Return": f"{gross_m['Ann. Return']*100:+.1f}%",
                "Net Return": f"{net_m['Ann. Return']*100:+.1f}%",
                "Cost Drag": f"{drag:.2f}%",
                "Gross Sharpe": f"{gross_m['Sharpe']:.2f}",
                "Net Sharpe": f"{net_m['Sharpe']:.2f}",
            })
    if cost_rows:
        st.dataframe(pd.DataFrame(cost_rows), use_container_width=True, hide_index=True)

    # ═══════════════════════════════════════
    # 2. REGIME ANALYSIS
    # ═══════════════════════════════════════
    st.subheader("2. Regime Analysis")
    st.caption(
        "Performance split by market regime using Equal Weight as the market proxy. "
        "Bull = EW return > 0, Bear = EW return < 0, High Vol = 20D rolling vol > 75th percentile."
    )

    if benchmark_ret is not None and len(benchmark_ret) > 63:
        bm_roll_vol = benchmark_ret.rolling(20).std() * np.sqrt(252)
        vol_75 = bm_roll_vol.quantile(0.75)

        # Classify each day
        regime = pd.Series("Normal", index=benchmark_ret.index)
        regime[benchmark_ret > 0] = "Bull"
        regime[benchmark_ret <= 0] = "Bear"
        high_vol_mask = bm_roll_vol > vol_75
        regime[high_vol_mask & (benchmark_ret <= 0)] = "Crisis"
        regime[high_vol_mask & (benchmark_ret > 0)] = "Recovery"

        regime_methods = ranked_methods[:6]  # top 6 for readability
        regime_data = []
        for method in regime_methods:
            if method not in method_daily:
                continue
            m_ret = method_daily[method]
            common = m_ret.index.intersection(regime.index)
            m_ret_aligned = m_ret.loc[common]
            reg_aligned = regime.loc[common]

            for reg_name in ["Bull", "Bear", "Crisis", "Recovery"]:
                mask = reg_aligned == reg_name
                if mask.sum() < 10:
                    continue
                sub = m_ret_aligned[mask]
                regime_data.append({
                    "Method": method,
                    "Regime": reg_name,
                    "Ann. Return": sub.mean() * 252,
                    "Ann. Vol": sub.std() * np.sqrt(252),
                    "Sharpe": sub.mean() / sub.std() * np.sqrt(252) if sub.std() > 0 else 0,
                    "Days": mask.sum(),
                })

        if regime_data:
            regime_df = pd.DataFrame(regime_data)

            # Heatmap: methods x regimes, value = Sharpe
            regime_pivot = regime_df.pivot_table(
                index="Method", columns="Regime", values="Sharpe",
            ).reindex(columns=["Bull", "Recovery", "Bear", "Crisis"])

            fig_reg = go.Figure(data=go.Heatmap(
                z=regime_pivot.values,
                x=regime_pivot.columns.tolist(),
                y=regime_pivot.index.tolist(),
                colorscale=[[0, "#ff4444"], [0.5, "#1a1a2e"], [1, "#00ff88"]],
                zmid=0,
                text=[[f"{v:.2f}" if pd.notna(v) else "" for v in row] for row in regime_pivot.values],
                texttemplate="%{text}", textfont={"size": 10},
                colorbar=dict(title="Sharpe"),
            ))
            fig_reg.update_layout(
                template="plotly_dark",
                height=max(300, len(regime_pivot) * 30),
                title="Sharpe Ratio by Market Regime",
                margin=dict(l=0, r=0, t=40, b=0),
            )
            st.plotly_chart(fig_reg, use_container_width=True, config=PLOTLY_NOBAR)

            # Return by regime bar chart
            fig_regbar = go.Figure()
            regime_colors = {"Bull": "#00ff88", "Recovery": "#00d1ff", "Bear": "#ff4444", "Crisis": "#ffaa00"}
            for reg_name in ["Bull", "Recovery", "Bear", "Crisis"]:
                sub = regime_df[regime_df["Regime"] == reg_name]
                if not sub.empty:
                    fig_regbar.add_trace(go.Bar(
                        x=sub["Method"], y=sub["Ann. Return"] * 100,
                        name=reg_name, marker_color=regime_colors.get(reg_name, "#888"),
                    ))
            fig_regbar.update_layout(
                template="plotly_dark", height=380, barmode="group",
                title="Annualized Return by Regime (%)",
                yaxis_title="Return (%)",
                legend=dict(orientation="h", y=-0.15),
                margin=dict(l=0, r=0, t=40, b=0),
            )
            st.plotly_chart(fig_regbar, use_container_width=True, config=PLOTLY_NOBAR)

    # ═══════════════════════════════════════
    # 3. CAPTURE RATIOS & INFORMATION RATIO
    # ═══════════════════════════════════════
    st.subheader("3. Capture Ratios & Information Ratio")
    st.caption(
        "Benchmarked to Equal Weight. Up capture > 100% = beats benchmark in up markets. "
        "Down capture < 100% = protects in down markets. Both together = alpha."
    )

    ir_rows = []
    for m in metrics_list:
        if "Info Ratio" in m:
            ir_rows.append({
                "Method": m["Method"],
                "Info Ratio": m["Info Ratio"],
                "Tracking Error": m["Tracking Error"],
                "Up Capture": m.get("Up Capture", 1),
                "Down Capture": m.get("Down Capture", 1),
            })

    if ir_rows:
        ir_df = pd.DataFrame(ir_rows)

        # Capture ratio scatter
        fig_cap = go.Figure()
        for _, row in ir_df.iterrows():
            color = METHOD_COLORS.get(row["Method"], "#888")
            fig_cap.add_trace(go.Scatter(
                x=[row["Up Capture"] * 100], y=[row["Down Capture"] * 100],
                mode="markers+text",
                text=[row["Method"]], textposition="top center",
                textfont=dict(size=9, color=COLORS["text_primary"]),
                marker=dict(size=12, color=color, line=dict(width=1, color="#555")),
                showlegend=False,
            ))
        fig_cap.add_hline(y=100, line_dash="dash", line_color="#555")
        fig_cap.add_vline(x=100, line_dash="dash", line_color="#555")
        fig_cap.add_annotation(x=115, y=80, text="Ideal zone",
                                showarrow=False, font=dict(color="#00ff88", size=10))
        fig_cap.update_layout(
            template="plotly_dark", height=400,
            title="Up Capture vs Down Capture (vs Equal Weight)",
            xaxis_title="Up Capture (%)", yaxis_title="Down Capture (%)",
            margin=dict(l=0, r=0, t=40, b=0),
        )
        st.plotly_chart(fig_cap, use_container_width=True, config=PLOTLY_NOBAR)

        # IR bar chart
        ir_sorted = ir_df.sort_values("Info Ratio", ascending=True)
        fig_ir = go.Figure()
        fig_ir.add_trace(go.Bar(
            y=ir_sorted["Method"], x=ir_sorted["Info Ratio"], orientation="h",
            marker_color=[
                "#00ff88" if v > 0.5 else "#00d1ff" if v > 0 else "#ff4444"
                for v in ir_sorted["Info Ratio"]
            ],
            text=[f"{v:.2f}" for v in ir_sorted["Info Ratio"]], textposition="outside",
        ))
        fig_ir.add_vline(x=0, line_dash="dash", line_color="#555")
        fig_ir.add_vline(x=0.5, line_dash="dash", line_color="#00ff88",
                          annotation_text="Good (0.5)")
        fig_ir.update_layout(
            template="plotly_dark", height=max(250, len(ir_sorted) * 25),
            title="Information Ratio vs Equal Weight",
            xaxis_title="Information Ratio",
            margin=dict(l=0, r=60, t=40, b=0),
        )
        st.plotly_chart(fig_ir, use_container_width=True, config=PLOTLY_NOBAR)

        # Full table
        ir_display = ir_df.copy()
        ir_display["Info Ratio"] = ir_display["Info Ratio"].apply(lambda v: f"{v:.2f}")
        ir_display["Tracking Error"] = ir_display["Tracking Error"].apply(lambda v: f"{v*100:.1f}%")
        ir_display["Up Capture"] = ir_display["Up Capture"].apply(lambda v: f"{v*100:.0f}%")
        ir_display["Down Capture"] = ir_display["Down Capture"].apply(lambda v: f"{v*100:.0f}%")
        st.dataframe(ir_display, use_container_width=True, hide_index=True)

    # ═══════════════════════════════════════
    # 4. STRESS SCENARIOS
    # ═══════════════════════════════════════
    st.subheader("4. Stress Scenario Analysis")
    st.caption("Estimated portfolio loss under historical crisis drawdowns.")

    STRESS_SCENARIOS = {
        "2008 GFC": -0.38, "COVID Mar 2020": -0.34,
        "2022 Rate Shock": -0.19, "2018 Q4 Selloff": -0.14,
        "2015 China Deval": -0.10, "Flash Crash 2010": -0.07,
    }

    if benchmark_ret is not None and len(benchmark_ret) > 63:
        # Estimate each method's beta to EW benchmark
        stress_rows = []
        for method in ranked_methods[:6]:
            if method not in method_daily or method in ("Equal Weight", "SPY Buy & Hold"):
                continue
            m_ret = method_daily[method]
            common = m_ret.index.intersection(benchmark_ret.index)
            if len(common) < 40:
                continue
            m_a = m_ret.loc[common].values
            b_a = benchmark_ret.loc[common].values
            b_var = np.var(b_a)
            beta = np.cov(m_a, b_a)[0, 1] / b_var if b_var > 0 else 1.0

            row = {"Method": method, "Beta": beta}
            for scenario, mkt_draw in STRESS_SCENARIOS.items():
                row[scenario] = beta * mkt_draw * 100
            stress_rows.append(row)

        if stress_rows:
            stress_df = pd.DataFrame(stress_rows).set_index("Method")
            scenario_cols = list(STRESS_SCENARIOS.keys())

            fig_stress = go.Figure(data=go.Heatmap(
                z=stress_df[scenario_cols].values,
                x=scenario_cols,
                y=stress_df.index.tolist(),
                colorscale=[[0, "#ff4444"], [0.5, "#1a1a2e"], [1, "#00ff88"]],
                zmid=-15,
                text=[[f"{v:.1f}%" for v in row] for row in stress_df[scenario_cols].values],
                texttemplate="%{text}", textfont={"size": 10},
                colorbar=dict(title="Est. Loss %"),
            ))
            fig_stress.update_layout(
                template="plotly_dark",
                height=max(250, len(stress_df) * 30),
                title="Estimated Portfolio Loss Under Historical Stress Scenarios",
                margin=dict(l=0, r=0, t=40, b=0),
            )
            st.plotly_chart(fig_stress, use_container_width=True, config=PLOTLY_NOBAR)

            st.caption("Stress losses estimated as beta x market drawdown. "
                       "Methods with lower beta suffer less in crises.")

    # ═══════════════════════════════════════
    # 5. CAPACITY ESTIMATE
    # ═══════════════════════════════════════
    st.subheader("5. Capacity Estimate")
    st.caption(
        "Estimated max AUM before market impact degrades returns, based on "
        "average daily volume of holdings and concentration."
    )

    @st.cache_data(ttl=3600, show_spinner=False)
    def _fetch_avg_volume(tickers_tuple):
        import yfinance as yf
        data = yf.download(list(tickers_tuple), period="1mo", progress=False, threads=True)
        if isinstance(data.columns, pd.MultiIndex):
            return data["Volume"].mean()
        return pd.Series({tickers_tuple[0]: data["Volume"].mean()})

    avg_vol = _fetch_avg_volume(tuple(tickers))
    avg_price = prices.iloc[-1]  # last known price

    # Dollar volume = avg shares traded * price
    dollar_vol = (avg_vol * avg_price).dropna()

    if not dollar_vol.empty:
        # For each method's current weights, estimate capacity
        # Rule of thumb: can trade 1% of ADV without impact
        cap_rows = []
        for method in ranked_methods[:6]:
            if method not in current_weights:
                continue
            w = pd.Series(current_weights[method], index=tickers)
            w_nz = w[w > 0.005]
            if w_nz.empty:
                continue
            # Capacity = min across tickers of (1% of dollar_vol / weight)
            ticker_cap = {}
            for t in w_nz.index:
                if t in dollar_vol.index and dollar_vol[t] > 0:
                    ticker_cap[t] = (0.01 * dollar_vol[t]) / w_nz[t]
            if ticker_cap:
                bottleneck = min(ticker_cap, key=ticker_cap.get)
                capacity = ticker_cap[bottleneck]
                cap_rows.append({
                    "Method": method,
                    "Est. Capacity": f"${capacity/1e6:.0f}M" if capacity >= 1e6 else f"${capacity/1e3:.0f}K",
                    "Bottleneck": bottleneck,
                    "Bottleneck Weight": f"{w_nz[bottleneck]*100:.1f}%",
                    "Active Positions": len(w_nz),
                })

        if cap_rows:
            st.dataframe(pd.DataFrame(cap_rows), use_container_width=True, hide_index=True)
            st.caption(
                "Capacity = 1% of bottleneck ticker's avg daily dollar volume / its portfolio weight. "
                "More diversified methods (HRP, HERC, Risk Parity) typically have higher capacity."
            )
        else:
            st.caption("Volume data not available for capacity estimation.")
    else:
        st.caption("Volume data not available for capacity estimation.")


# ═══════════════════════════════════════════════
# TAB 6: STATISTICAL TESTS (DE PRADO)
# ═══════════════════════════════════════════════
with tab_deprado, error_boundary("Statistical Tests"):
    st.subheader("Statistical Rigor — Is This Real or Noise?")

    with st.expander("How to read this tab", expanded=False):
        st.markdown(
            "Standard backtesting overstates performance because of **multiple testing bias** — "
            "when you test 9+ methods and pick the best, you're selecting for luck, not skill.\n\n"
            "These tests from Lopez de Prado's *Advances in Financial Machine Learning* quantify "
            "exactly how much of the observed performance is genuine:\n\n"
            "| Test | What It Measures | Passing Grade |\n"
            "|------|-----------------|---------------|\n"
            "| **Deflated Sharpe Ratio** | P(Sharpe is real after adjusting for N trials) | DSR > 0.95 |\n"
            "| **Prob. of Backtest Overfitting** | P(best IS method fails OOS) via CPCV | PBO < 0.25 |\n"
            "| **Sequential Bootstrap** | Honest CI on Sharpe preserving autocorrelation | CI excludes 0 |\n"
            "| **Min Track Record** | Years of data needed to trust this Sharpe | Actual > Min |\n\n"
            "A strategy that passes ALL four tests is genuinely robust. "
            "Failing any test is a red flag for overfitting."
        )

    with st.spinner("Running de Prado statistical tests..."):
        from scipy.stats import skew as sp_skew, kurtosis as sp_kurtosis

        n_methods_tested = len([m for m in ranked_methods if m != "SPY Buy & Hold"])

        # Collect OOS daily returns per method
        method_oos_returns = {}
        for method in ranked_methods:
            if method in wf_results and wf_results[method]:
                df_m = pd.DataFrame(wf_results[method]).set_index("date")
                df_m = df_m[~df_m.index.duplicated(keep="first")]
                method_oos_returns[method] = df_m["return"]

    # ═══════════════════════════════════════
    # 1. DEFLATED SHARPE RATIO
    # ═══════════════════════════════════════
    st.subheader("1. Deflated Sharpe Ratio")
    st.caption(
        f"Adjusts each method's Sharpe for the fact that you tested "
        f"**{n_methods_tested} methods** and picked the best. "
        "DSR > 0.95 means the Sharpe survives the multiple-testing penalty."
    )

    dsr_rows = []
    for method in ranked_methods:
        if method not in method_oos_returns:
            continue
        rets = method_oos_returns[method]
        n_obs = len(rets)
        sr = rets.mean() / rets.std() * np.sqrt(252) if rets.std() > 0 else 0
        sk = float(sp_skew(rets.dropna())) if len(rets) > 10 else 0
        kt = float(sp_kurtosis(rets.dropna(), fisher=False)) if len(rets) > 10 else 3

        dsr = _deflated_sharpe_ratio(sr, n_obs, n_methods_tested, sk, kt)
        min_trl = _min_track_record(sr, n_obs, sk, kt)
        min_years = min_trl / 252

        dsr_rows.append({
            "Method": method,
            "Sharpe": sr,
            "DSR (p-value)": dsr,
            "Significant": dsr > 0.95,
            "Skew": sk,
            "Kurtosis": kt,
            "Min Track Record": min_trl,
            "Min Years": min_years,
            "Actual Days": n_obs,
            "Sufficient Data": n_obs > min_trl,
        })

    if not dsr_rows:
        st.warning("No methods produced valid OOS returns for statistical testing.")

    dsr_df = pd.DataFrame(dsr_rows) if dsr_rows else pd.DataFrame()

    # DSR bar chart
    if not dsr_df.empty:
        fig_dsr = go.Figure()
        fig_dsr.add_trace(go.Bar(
            x=dsr_df["Method"], y=dsr_df["DSR (p-value)"],
            marker_color=[
                "#00ff88" if v > 0.95 else "#ffaa00" if v > 0.80 else "#ff4444"
                for v in dsr_df["DSR (p-value)"]
            ],
            text=[f"{v:.2f}" for v in dsr_df["DSR (p-value)"]],
            textposition="outside",
        ))
        fig_dsr.add_hline(y=0.95, line_dash="dash", line_color="#00ff88",
                           annotation_text="95% significance")
        fig_dsr.update_layout(
            template="plotly_dark", height=350,
            title=f"Deflated Sharpe Ratio (adjusted for {n_methods_tested} trials)",
            yaxis_title="DSR p-value", yaxis=dict(range=[0, 1.1]),
            margin=dict(l=0, r=0, t=40, b=0),
        )
        st.plotly_chart(fig_dsr, use_container_width=True, config=PLOTLY_NOBAR)

    # ═══════════════════════════════════════
    # 2. PROBABILITY OF BACKTEST OVERFITTING
    # ═══════════════════════════════════════
    st.subheader("2. Probability of Backtest Overfitting (PBO)")
    st.caption(
        "CPCV: splits OOS data into blocks, tests if the best in-sample method "
        "also wins out-of-sample. PBO < 0.25 = robust. PBO > 0.50 = likely overfit."
    )

    pbo_val, pbo_logits = _pbo_cpcv(method_oos_returns, n_splits=6)

    if pbo_val is not None:
        pbo_verdict = "Robust" if pbo_val < 0.25 else "Borderline" if pbo_val < 0.50 else "Likely Overfit"

        pb1, pb2, pb3 = st.columns(3)
        pb1.metric("PBO", f"{pbo_val:.0%}")
        pb2.metric("Verdict", pbo_verdict)
        pb3.metric("CPCV Splits", "6 blocks")

        if pbo_logits:
            fig_pbo = go.Figure()
            fig_pbo.add_trace(go.Histogram(
                x=pbo_logits, nbinsx=20,
                marker_color="#00d1ff",
            ))
            fig_pbo.add_vline(x=0, line_dash="dash", line_color="#ff4444",
                               annotation_text="Overfit boundary")
            fig_pbo.update_layout(
                template="plotly_dark", height=280,
                title="PBO Logit Distribution (mass left of 0 = not overfit)",
                xaxis_title="Logit(rank)", yaxis_title="Count",
                margin=dict(l=0, r=0, t=40, b=0),
            )
            st.plotly_chart(fig_pbo, use_container_width=True, config=PLOTLY_NOBAR)
    else:
        st.warning("Insufficient data for PBO analysis. Need longer OOS history.")

    # ═══════════════════════════════════════
    # 3. SEQUENTIAL BOOTSTRAP
    # ═══════════════════════════════════════
    st.subheader("3. Sequential Bootstrap Confidence Intervals")
    st.caption(
        "Block bootstrap (20-day blocks) preserves serial dependence in returns. "
        "Gives honest confidence intervals — wider than naive bootstrap."
    )

    boot_rows = []
    for method in ranked_methods:
        if method not in method_oos_returns:
            continue
        rets = method_oos_returns[method]
        ci_lo, ci_hi, p_pos = _sequential_bootstrap_ci(rets, n_bootstrap=2000)
        if ci_lo is not None:
            boot_rows.append({
                "Method": method,
                "Sharpe": rets.mean() / rets.std() * np.sqrt(252) if rets.std() > 0 else 0,
                "90% CI Low": ci_lo,
                "90% CI High": ci_hi,
                "P(Sharpe > 0)": p_pos,
                "Significant": ci_lo > 0,
            })

    if boot_rows:
        boot_df = pd.DataFrame(boot_rows)

        fig_boot = go.Figure()
        for _, row in boot_df.iterrows():
            color = "#00ff88" if row["Significant"] else "#ff4444"
            fig_boot.add_trace(go.Scatter(
                x=[row["Method"]], y=[row["Sharpe"]],
                error_y=dict(
                    type="data",
                    symmetric=False,
                    array=[row["90% CI High"] - row["Sharpe"]],
                    arrayminus=[row["Sharpe"] - row["90% CI Low"]],
                    color=color, thickness=2, width=8,
                ),
                mode="markers",
                marker=dict(size=10, color=color),
                name=row["Method"],
                showlegend=False,
            ))
        fig_boot.add_hline(y=0, line_dash="dash", line_color="#555")
        fig_boot.update_layout(
            template="plotly_dark", height=350,
            title="Sharpe Ratio with 90% Block Bootstrap CI",
            yaxis_title="Sharpe Ratio",
            margin=dict(l=0, r=0, t=40, b=0),
        )
        st.plotly_chart(fig_boot, use_container_width=True, config=PLOTLY_NOBAR)

    # ═══════════════════════════════════════
    # 4. MINIMUM TRACK RECORD LENGTH
    # ═══════════════════════════════════════
    st.subheader("4. Minimum Track Record Length")
    st.caption(
        "How many trading days are needed to trust each method's Sharpe at 95% confidence. "
        "If actual data exceeds the minimum, the Sharpe is credible."
    )

    if dsr_rows:
        fig_trl = go.Figure()
        max_actual = max(r["Actual Days"] for r in dsr_rows)
        for row in sorted(dsr_rows, key=lambda r: r["Sharpe"], reverse=True):
            min_d = row["Min Track Record"]
            sufficient = row["Sufficient Data"]
            color = "#00ff88" if sufficient else "#ff4444"
            # Cap inf at 3x actual for display
            display_d = min(min_d, max_actual * 3) if np.isfinite(min_d) else max_actual * 3
            label = f"{min_d:.0f}D" if np.isfinite(min_d) else "INF"
            fig_trl.add_trace(go.Bar(
                x=[row["Method"]], y=[display_d],
                marker_color=color, name=row["Method"],
                text=[label], textposition="outside",
                showlegend=False,
            ))
        fig_trl.add_hline(
            y=dsr_rows[0]["Actual Days"], line_dash="dash", line_color="#00d1ff",
            annotation_text=f"Actual: {dsr_rows[0]['Actual Days']}D",
        )
        fig_trl.update_layout(
            template="plotly_dark", height=350,
            title="Minimum Track Record (days) vs Actual Data",
            yaxis_title="Days Required",
            margin=dict(l=0, r=0, t=40, b=0),
        )
        st.plotly_chart(fig_trl, use_container_width=True, config=PLOTLY_NOBAR)

    # ═══════════════════════════════════════
    # SUMMARY SCORECARD
    # ═══════════════════════════════════════
    st.subheader("Overfitting Scorecard")
    st.caption("Green = passes, Red = fails. A method must pass all tests to be considered robust.")

    if dsr_rows and boot_rows:
        score_rows = []
        boot_lookup = {r["Method"]: r for r in boot_rows}
        for row in dsr_rows:
            m = row["Method"]
            br = boot_lookup.get(m, {})
            passes = 0
            total = 4
            dsr_pass = row["DSR (p-value)"] > 0.95
            pbo_pass = pbo_val is not None and pbo_val < 0.50
            boot_pass = br.get("Significant", False)
            trl_pass = row["Sufficient Data"]
            passes = sum([dsr_pass, pbo_pass, boot_pass, trl_pass])

            score_rows.append({
                "Method": m,
                "Sharpe": f"{row['Sharpe']:.2f}",
                "DSR": "PASS" if dsr_pass else "FAIL",
                "PBO": "PASS" if pbo_pass else "FAIL",
                "Bootstrap CI": "PASS" if boot_pass else "FAIL",
                "Track Record": "PASS" if trl_pass else "FAIL",
                "Score": f"{passes}/{total}",
                "Verdict": "Robust" if passes == 4 else "Credible" if passes >= 3 else "Suspect" if passes >= 2 else "Unreliable",
            })

        score_df = pd.DataFrame(score_rows)
        st.dataframe(
            score_df.style.apply(
                lambda row: [
                    "" if col in ("Method", "Sharpe", "Score", "Verdict") else
                    "color: #00ff88" if val == "PASS" else "color: #ff4444"
                    for col, val in row.items()
                ],
                axis=1,
            ),
            use_container_width=True, hide_index=True,
        )

        # Final verdict
        best_method = ranked_methods[0]
        best_score = next((r for r in score_rows if r["Method"] == best_method), None)
        if best_score:
            verdict = best_score["Verdict"]
            if verdict == "Robust":
                st.success(
                    f"**{best_method}** passes all 4 statistical tests. "
                    "This performance is unlikely to be noise or overfitting."
                )
            elif verdict == "Credible":
                st.info(
                    f"**{best_method}** passes 3/4 tests. "
                    "Performance is likely genuine but not bulletproof."
                )
            elif verdict == "Suspect":
                st.warning(
                    f"**{best_method}** passes only 2/4 tests. "
                    "Performance may be partially driven by overfitting or noise."
                )
            else:
                st.error(
                    f"**{best_method}** fails most tests. "
                    "This performance is likely overfitting — do not trust it for live trading."
                )


# ═══════════════════════════════════════════════
# TAB 7: DRAWDOWN
# ═══════════════════════════════════════════════
with tab_dd, error_boundary("Drawdown"):
    st.subheader("Drawdown Analysis")

    with st.expander("How to read this tab", expanded=False):
        st.markdown(
            "Underwater equity curves show how far each method falls from its peak. "
            "The bottom of each curve is the max drawdown.\n\n"
            "Methods that recover quickly from drawdowns are more robust. "
            "Methods with shallow, brief drawdowns are preferable for live trading."
        )

    fig_dd = go.Figure()
    dd_data = {}
    for method in ranked_methods:
        cum = cum_series[method]
        dd = (cum / cum.cummax() - 1) * 100
        dd_data[method] = dd
        fig_dd.add_trace(go.Scatter(
            x=dd.index, y=dd.values, mode="lines",
            name=method,
            line=dict(color=METHOD_COLORS.get(method, "#888"), width=1.5),
            fill="tozeroy" if method == ranked_methods[0] else None,
        ))

    fig_dd.update_layout(
        template="plotly_dark", height=400,
        title="Underwater Equity Curves (Drawdown from Peak)",
        yaxis_title="Drawdown (%)",
        legend=dict(orientation="h", y=-0.15),
        margin=dict(l=0, r=0, t=40, b=0),
    )
    st.plotly_chart(fig_dd, use_container_width=True, config=PLOTLY_NOBAR)

    # Max drawdown comparison
    st.subheader("Max Drawdown Comparison")
    raw_metrics = pd.DataFrame(metrics_list).set_index("Method").loc[ranked_methods]
    dd_sorted = (raw_metrics["Max DD"] * 100).sort_values()

    fig_mdd = go.Figure()
    fig_mdd.add_trace(go.Bar(
        y=dd_sorted.index, x=dd_sorted.values, orientation="h",
        marker_color=[METHOD_COLORS.get(m, "#888") for m in dd_sorted.index],
        text=[f"{v:.1f}%" for v in dd_sorted], textposition="outside",
    ))
    fig_mdd.update_layout(
        template="plotly_dark", height=max(250, len(dd_sorted) * 30),
        title="Maximum Drawdown by Method",
        xaxis_title="Max Drawdown (%)",
        margin=dict(l=0, r=50, t=40, b=0),
    )
    st.plotly_chart(fig_mdd, use_container_width=True, config=PLOTLY_NOBAR)

    # Drawdown duration
    st.subheader("Drawdown Duration")
    dur_data = []
    for method in ranked_methods:
        dd = dd_data[method]
        in_dd = dd < -0.5  # consider >0.5% below peak as drawdown
        if in_dd.any():
            # Count consecutive drawdown days
            groups = (in_dd != in_dd.shift()).cumsum()
            dd_groups = groups[in_dd]
            if not dd_groups.empty:
                longest = dd_groups.value_counts().max()
                avg_dur = dd_groups.value_counts().mean()
                n_episodes = dd_groups.nunique()
            else:
                longest = avg_dur = n_episodes = 0
        else:
            longest = avg_dur = n_episodes = 0
        dur_data.append({
            "Method": method,
            "Longest DD (days)": int(longest),
            "Avg DD Duration": f"{avg_dur:.0f} days",
            "DD Episodes": int(n_episodes),
        })
    st.dataframe(pd.DataFrame(dur_data), use_container_width=True, hide_index=True)


# ═══════════════════════════════════════════════
# TAB 8: ROLLING ANALYSIS
# ═══════════════════════════════════════════════
with tab_rolling, error_boundary("Rolling Analysis"):
    st.subheader("Rolling Performance")

    with st.expander("How to read this tab", expanded=False):
        st.markdown(
            "**Rolling Sharpe** shows how each method's risk-adjusted return evolves over time. "
            "A method that maintains consistent Sharpe across time is more reliable.\n\n"
            "**Method correlation** shows how similar the methods' returns are. "
            "High correlation means two methods are doing essentially the same thing — "
            "you only need one. Low correlation means true diversification of approach."
        )

    # Rolling Sharpe (63D)
    roll_window = 63
    fig_rsharpe = go.Figure()
    for method in ranked_methods[:5]:  # top 5 only for readability
        if method not in wf_results or not wf_results[method]:
            continue
        df_m = pd.DataFrame(wf_results[method]).set_index("date")
        df_m = df_m[~df_m.index.duplicated(keep="first")]
        if len(df_m) > roll_window:
            roll_ret = df_m["return"].rolling(roll_window).mean() * 252
            roll_vol = df_m["return"].rolling(roll_window).std() * np.sqrt(252)
            roll_sharpe = (roll_ret / roll_vol.replace(0, np.nan)).dropna()
            roll_sharpe = roll_sharpe.clip(-10, 10)  # cap extreme spikes
            fig_rsharpe.add_trace(go.Scatter(
                x=roll_sharpe.index, y=roll_sharpe.values, mode="lines",
                name=method,
                line=dict(color=METHOD_COLORS.get(method, "#888"), width=1.5),
            ))

    fig_rsharpe.add_hline(y=0, line_dash="dash", line_color="#555")
    fig_rsharpe.update_layout(
        template="plotly_dark", height=380,
        title=f"Rolling {roll_window}-Day Sharpe Ratio (Top 5 Methods)",
        yaxis_title="Sharpe Ratio",
        legend=dict(orientation="h", y=-0.15),
        margin=dict(l=0, r=0, t=40, b=0),
    )
    st.plotly_chart(fig_rsharpe, use_container_width=True, config=PLOTLY_NOBAR)

    # Method return correlation
    st.subheader("Method Correlation Matrix")
    st.caption(
        "High correlation (> 0.8) between methods means they produce similar return streams — "
        "choosing between them matters less. Low correlation means genuinely different strategies."
    )

    method_returns = {}
    for method in ranked_methods:
        if method not in wf_results or not wf_results[method]:
            continue
        df_m = pd.DataFrame(wf_results[method]).set_index("date")
        df_m = df_m[~df_m.index.duplicated(keep="first")]
        method_returns[method] = df_m["return"]

    mr_df = pd.DataFrame(method_returns)
    method_corr = mr_df.corr()

    fig_mc = go.Figure(data=go.Heatmap(
        z=method_corr.values,
        x=method_corr.columns.tolist(),
        y=method_corr.index.tolist(),
        colorscale=[[0, "#ff4444"], [0.5, "#1a1a2e"], [1, "#00d1ff"]],
        zmid=0.5, zmin=0, zmax=1,
        text=[[f"{v:.2f}" for v in row] for row in method_corr.values],
        texttemplate="%{text}", textfont={"size": 10},
        colorbar=dict(title="Corr"),
    ))
    fig_mc.update_layout(
        template="plotly_dark",
        height=max(350, len(ranked_methods) * 35),
        title="Return Correlation Between Allocation Methods",
        margin=dict(l=0, r=0, t=40, b=0),
    )
    st.plotly_chart(fig_mc, use_container_width=True, config=PLOTLY_NOBAR)

    # Identify independent method pairs
    st.subheader("Method Independence")
    independent = []
    redundant = []
    cols = method_corr.columns.tolist()
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            r = method_corr.iloc[i, j]
            if r < 0.7:
                independent.append((cols[i], cols[j], r))
            elif r > 0.9:
                redundant.append((cols[i], cols[j], r))

    ic1, ic2 = st.columns(2)
    with ic1:
        st.markdown("**Most Independent Pairs (r < 0.7)**")
        if independent:
            for a, b, r in sorted(independent, key=lambda x: x[2])[:5]:
                st.markdown(f"- **{a}** vs **{b}**: r = {r:.2f}")
        else:
            st.caption("All methods are highly correlated (r > 0.7).")
    with ic2:
        st.markdown("**Redundant Pairs (r > 0.9)**")
        if redundant:
            for a, b, r in sorted(redundant, key=lambda x: -x[2])[:5]:
                st.markdown(f"- **{a}** vs **{b}**: r = {r:.2f}")
            st.caption("Redundant methods produce nearly identical returns — pick one.")
        else:
            st.caption("No highly redundant method pairs found.")

    # Rolling outperformance vs Equal Weight
    st.subheader("Rolling Outperformance vs Equal Weight")
    st.caption(
        "Cumulative excess return of each method over equal weight. "
        "Rising line = method is adding value. Falling = underperforming 1/N."
    )
    fig_excess = go.Figure()
    eq_ret = pd.DataFrame(wf_results["Equal Weight"]).set_index("date")
    eq_ret = eq_ret[~eq_ret.index.duplicated(keep="first")]["return"]

    for method in ranked_methods:
        if method in ("Equal Weight", "SPY Buy & Hold"):
            continue
        if method not in wf_results or not wf_results[method]:
            continue
        m_ret = pd.DataFrame(wf_results[method]).set_index("date")
        m_ret = m_ret[~m_ret.index.duplicated(keep="first")]["return"]
        common = m_ret.index.intersection(eq_ret.index)
        excess = (m_ret.loc[common] - eq_ret.loc[common]).cumsum() * 100
        fig_excess.add_trace(go.Scatter(
            x=excess.index, y=excess.values, mode="lines",
            name=method,
            line=dict(color=METHOD_COLORS.get(method, "#888"), width=1.5),
        ))

    fig_excess.add_hline(y=0, line_dash="dash", line_color="#555")
    fig_excess.update_layout(
        template="plotly_dark", height=380,
        title="Cumulative Excess Return vs Equal Weight (%)",
        yaxis_title="Excess Return (%)",
        legend=dict(orientation="h", y=-0.15),
        margin=dict(l=0, r=0, t=40, b=0),
    )
    st.plotly_chart(fig_excess, use_container_width=True, config=PLOTLY_NOBAR)

    st.caption(
        "Backtested results do not guarantee future returns. "
        "Walk-forward testing eliminates look-ahead bias but cannot eliminate estimation error. "
        "Methods that perform well across multiple universes and time periods are more trustworthy."
    )


# ═══════════════════════════════════════════════
# TAB 9: UNIVERSE GRID
# ═══════════════════════════════════════════════
with tab_grid, error_boundary("Universe Grid"):
    st.subheader("Universe Grid — All Presets Backtested")

    with st.expander("How to read this tab", expanded=False):
        st.markdown(
            "Runs the walk-forward backtest independently on **every preset group** "
            "using the same estimation window and rebalance frequency.\n\n"
            "The heatmap shows which **universe + method** combination works best. "
            "Methods that rank highly across many universes are more robust. "
            "Universes where all methods struggle may have structural issues.\n\n"
            "**Incremental analysis** shows the impact of adding each sector group "
            "to the Multi-Asset base — does adding energy improve or hurt Sharpe?"
        )

    grid_run = st.button("Run Grid Analysis", type="primary", key="ma_grid_run",
                          help="This runs a full walk-forward backtest on each of the 15 preset groups. Takes 30-60 seconds.")

    if grid_run:
        st.session_state["ma_grid_loaded"] = True

    if not st.session_state.get("ma_grid_loaded"):
        st.info("Click **Run Grid Analysis** to backtest all preset groups. "
                f"This runs {len(PRESET_GROUPS)} independent walk-forward backtests "
                f"with {len(BASE_METHODS)} methods each.")
        st.stop()

    # ── Download ALL unique tickers across all presets ──
    all_grid_tickers = sorted(set(t for tks in PRESET_GROUPS.values() for t in tks))

    with st.spinner(f"Downloading {len(all_grid_tickers)} tickers for grid analysis..."):
        grid_prices = _download_prices(tuple(all_grid_tickers), lookback_map[ma_lookback])

    if grid_prices.empty:
        st.error("Failed to download price data.")
        st.stop()

    grid_prices = grid_prices.dropna(axis=1, how="all")
    grid_returns = grid_prices.pct_change().dropna()
    available_tickers = set(grid_returns.columns)

    # ── Run walk-forward for each preset group ──
    grid_sharpe = {}
    grid_return = {}
    grid_maxdd = {}
    grid_sortino = {}

    progress = st.progress(0, text="Running grid analysis...")
    group_names = list(PRESET_GROUPS.keys())
    n_groups = len(group_names)

    for gi, gname in enumerate(group_names):
        progress.progress((gi + 1) / n_groups, text=f"Backtesting {gname} ({gi+1}/{n_groups})...")

        # Filter to tickers that have data
        group_tks = [t for t in PRESET_GROUPS[gname] if t in available_tickers]
        if len(group_tks) < 3:
            continue

        g_ret = grid_returns[group_tks]
        wf = _run_walkforward(g_ret, ma_est_days, rebal_period, ma_denoise)

        for method, daily_ret in wf.items():
            m = _portfolio_metrics(daily_ret, method)
            grid_sharpe[(gname, method)] = m["Sharpe"]
            grid_return[(gname, method)] = m["Ann. Return"]
            grid_maxdd[(gname, method)] = m["Max DD"]
            grid_sortino[(gname, method)] = m["Sortino"]

    progress.empty()

    if not grid_sharpe:
        st.error("No valid backtests completed. Check that preset groups have sufficient data.")
        st.stop()

    # ── Build grid DataFrames ──
    groups_with_data = sorted(set(g for g, _ in grid_sharpe.keys()))
    methods_in_grid = sorted(set(m for _, m in grid_sharpe.keys()),
                              key=lambda m: BASE_METHODS.index(m) if m in BASE_METHODS else 99)

    def _build_grid(data_dict):
        df = pd.DataFrame(index=groups_with_data, columns=methods_in_grid, dtype=float)
        for (g, m), v in data_dict.items():
            if g in df.index and m in df.columns:
                df.loc[g, m] = v
        return df

    sharpe_grid = _build_grid(grid_sharpe)
    return_grid = _build_grid(grid_return)
    maxdd_grid = _build_grid(grid_maxdd)
    sortino_grid = _build_grid(grid_sortino)

    # ── Sharpe Heatmap ──
    st.subheader("Sharpe Ratio — Universe x Method")
    fig_sg = go.Figure(data=go.Heatmap(
        z=sharpe_grid.values,
        x=sharpe_grid.columns.tolist(),
        y=sharpe_grid.index.tolist(),
        colorscale=[[0, "#ff4444"], [0.5, "#1a1a2e"], [1, "#00ff88"]],
        zmid=0,
        text=[[f"{v:.2f}" if pd.notna(v) else "" for v in row] for row in sharpe_grid.values],
        texttemplate="%{text}", textfont={"size": 9},
        colorbar=dict(title="Sharpe"),
    ))
    fig_sg.update_layout(
        template="plotly_dark",
        height=max(400, len(groups_with_data) * 28),
        title=f"Walk-Forward Sharpe Ratio ({ma_rebal}, {ma_est_days}D Window)",
        margin=dict(l=0, r=0, t=40, b=0),
    )
    st.plotly_chart(fig_sg, use_container_width=True, config=PLOTLY_NOBAR)

    # ── Return Heatmap ──
    st.subheader("Annualized Return — Universe x Method")
    fig_rg = go.Figure(data=go.Heatmap(
        z=return_grid.values * 100,
        x=return_grid.columns.tolist(),
        y=return_grid.index.tolist(),
        colorscale=[[0, "#ff4444"], [0.5, "#1a1a2e"], [1, "#00ff88"]],
        zmid=0,
        text=[[f"{v*100:.1f}%" if pd.notna(v) else "" for v in row] for row in return_grid.values],
        texttemplate="%{text}", textfont={"size": 9},
        colorbar=dict(title="Return %"),
    ))
    fig_rg.update_layout(
        template="plotly_dark",
        height=max(400, len(groups_with_data) * 28),
        title=f"Walk-Forward Annualized Return ({ma_rebal}, {ma_est_days}D Window)",
        margin=dict(l=0, r=0, t=40, b=0),
    )
    st.plotly_chart(fig_rg, use_container_width=True, config=PLOTLY_NOBAR)

    # ── Max Drawdown Heatmap ──
    st.subheader("Max Drawdown — Universe x Method")
    fig_dg = go.Figure(data=go.Heatmap(
        z=maxdd_grid.values * 100,
        x=maxdd_grid.columns.tolist(),
        y=maxdd_grid.index.tolist(),
        colorscale=[[0, "#ff4444"], [0.5, "#1a1a2e"], [1, "#00ff88"]],
        zmid=-15,
        text=[[f"{v*100:.1f}%" if pd.notna(v) else "" for v in row] for row in maxdd_grid.values],
        texttemplate="%{text}", textfont={"size": 9},
        colorbar=dict(title="Max DD %"),
    ))
    fig_dg.update_layout(
        template="plotly_dark",
        height=max(400, len(groups_with_data) * 28),
        title=f"Walk-Forward Max Drawdown ({ma_rebal}, {ma_est_days}D Window)",
        margin=dict(l=0, r=0, t=40, b=0),
    )
    st.plotly_chart(fig_dg, use_container_width=True, config=PLOTLY_NOBAR)

    # ── Sortino Heatmap ──
    st.subheader("Sortino Ratio — Universe x Method")
    fig_so = go.Figure(data=go.Heatmap(
        z=sortino_grid.values,
        x=sortino_grid.columns.tolist(),
        y=sortino_grid.index.tolist(),
        colorscale=[[0, "#ff4444"], [0.5, "#1a1a2e"], [1, "#00ff88"]],
        zmid=0,
        text=[[f"{v:.2f}" if pd.notna(v) else "" for v in row] for row in sortino_grid.values],
        texttemplate="%{text}", textfont={"size": 9},
        colorbar=dict(title="Sortino"),
    ))
    fig_so.update_layout(
        template="plotly_dark",
        height=max(400, len(groups_with_data) * 28),
        title=f"Walk-Forward Sortino Ratio ({ma_rebal}, {ma_est_days}D Window)",
        margin=dict(l=0, r=0, t=40, b=0),
    )
    st.plotly_chart(fig_so, use_container_width=True, config=PLOTLY_NOBAR)

    # ── Best Combinations Table ──
    st.subheader("Top 15 Universe + Method Combinations")
    combo_rows = []
    for (g, m), s in grid_sharpe.items():
        combo_rows.append({
            "Universe": g,
            "Method": m,
            "Sharpe": s,
            "Ann. Return": grid_return.get((g, m), 0),
            "Max DD": grid_maxdd.get((g, m), 0),
            "Sortino": grid_sortino.get((g, m), 0),
        })
    combo_df = pd.DataFrame(combo_rows).sort_values("Sharpe", ascending=False).head(15)
    combo_df["Rank"] = range(1, len(combo_df) + 1)
    combo_display = combo_df.copy()
    combo_display["Ann. Return"] = combo_display["Ann. Return"].apply(lambda v: f"{v*100:+.1f}%")
    combo_display["Max DD"] = combo_display["Max DD"].apply(lambda v: f"{v*100:.1f}%")
    combo_display["Sharpe"] = combo_display["Sharpe"].apply(lambda v: f"{v:.2f}")
    combo_display["Sortino"] = combo_display["Sortino"].apply(lambda v: f"{v:.2f}")
    st.dataframe(
        combo_display[["Rank", "Universe", "Method", "Sharpe", "Ann. Return", "Max DD", "Sortino"]],
        use_container_width=True, hide_index=True,
    )

    # ── Best Method per Universe ──
    st.subheader("Best Method per Universe")
    best_per_u = []
    for g in groups_with_data:
        row = sharpe_grid.loc[g]
        best_m = row.idxmax()
        best_s = row.max()
        worst_m = row.idxmin()
        worst_s = row.min()
        spread = best_s - worst_s
        best_per_u.append({
            "Universe": g,
            "Tickers": len(PRESET_GROUPS[g]),
            "Best Method": best_m,
            "Best Sharpe": f"{best_s:.2f}",
            "Worst Method": worst_m,
            "Worst Sharpe": f"{worst_s:.2f}",
            "Spread": f"{spread:.2f}",
        })
    st.dataframe(pd.DataFrame(best_per_u), use_container_width=True, hide_index=True)

    # ── Best Universe per Method ──
    st.subheader("Best Universe per Method")
    best_per_m = []
    for m in methods_in_grid:
        col = sharpe_grid[m].dropna()
        if col.empty:
            continue
        best_g = col.idxmax()
        best_s = col.max()
        avg_s = col.mean()
        best_per_m.append({
            "Method": m,
            "Best Universe": best_g,
            "Best Sharpe": f"{best_s:.2f}",
            "Avg Sharpe (all universes)": f"{avg_s:.2f}",
            "Universes Tested": len(col),
        })
    st.dataframe(pd.DataFrame(best_per_m), use_container_width=True, hide_index=True)

    # ── Method Consistency ──
    st.subheader("Method Consistency Across Universes")
    st.caption("Average Sharpe across all universes. Methods with high average AND low spread are the most reliable.")

    avg_sharpe = sharpe_grid.mean(axis=0).sort_values(ascending=False)
    std_sharpe = sharpe_grid.std(axis=0)

    fig_mc = go.Figure()
    fig_mc.add_trace(go.Bar(
        x=avg_sharpe.index, y=avg_sharpe.values,
        marker_color=[METHOD_COLORS.get(m, "#888") for m in avg_sharpe.index],
        error_y=dict(type="data", array=std_sharpe.loc[avg_sharpe.index].values,
                     color="#ffaa00", thickness=1.5, width=4),
        text=[f"{v:.2f}" for v in avg_sharpe.values], textposition="outside",
    ))
    fig_mc.update_layout(
        template="plotly_dark", height=380,
        title="Average Sharpe Across All Universes (with std dev error bars)",
        yaxis_title="Avg Sharpe",
        margin=dict(l=0, r=0, t=40, b=0),
    )
    st.plotly_chart(fig_mc, use_container_width=True, config=PLOTLY_NOBAR)

    # ── Cross-Group Correlation ──
    st.markdown("---")
    st.subheader("Cross-Group Correlation")
    st.caption(
        "Correlation between each group's best method OOS returns. "
        "Low correlation between groups means combining them adds diversification. "
        "High correlation means they move together — limited benefit from combining."
    )

    # Build best-method OOS returns for each group
    _group_oos_ret = {}
    for g in groups_with_data:
        g_tks = [t for t in PRESET_GROUPS[g] if t in available_tickers]
        if len(g_tks) < 3:
            continue
        g_ret = grid_returns[g_tks]
        g_wf = _run_walkforward(g_ret, ma_est_days, rebal_period, ma_denoise)
        if g_wf:
            g_sharpes = {m: _portfolio_metrics(r, m)["Sharpe"] for m, r in g_wf.items()}
            best_m = max(g_sharpes, key=g_sharpes.get)
            _group_oos_ret[g] = g_wf[best_m]

    if len(_group_oos_ret) >= 3:
        _corr_df = pd.DataFrame(_group_oos_ret).corr()

        fig_gc = go.Figure(data=go.Heatmap(
            z=_corr_df.values,
            x=_corr_df.columns.tolist(),
            y=_corr_df.index.tolist(),
            colorscale=[[0, "#00ff88"], [0.5, "#1a1a2e"], [1, "#ff4444"]],
            zmid=0.5,
            text=[[f"{v:.2f}" for v in row] for row in _corr_df.values],
            texttemplate="%{text}", textfont={"size": 9},
            colorbar=dict(title="Corr"),
        ))
        fig_gc.update_layout(
            template="plotly_dark",
            height=max(400, len(_corr_df) * 32),
            title="Cross-Group Return Correlation (Best Method per Group)",
            margin=dict(l=0, r=0, t=40, b=0),
        )
        st.plotly_chart(fig_gc, use_container_width=True, config=PLOTLY_NOBAR)

        # Most/least correlated pairs
        _pairs = []
        for i in range(len(_corr_df)):
            for j in range(i + 1, len(_corr_df)):
                _pairs.append({
                    "Group A": _corr_df.index[i],
                    "Group B": _corr_df.columns[j],
                    "Correlation": _corr_df.iloc[i, j],
                })
        if _pairs:
            _pairs_df = pd.DataFrame(_pairs).sort_values("Correlation")
            st.markdown("**Best diversifiers** (lowest correlation):")
            for _, p in _pairs_df.head(3).iterrows():
                st.caption(f"{p['Group A']} / {p['Group B']}: {p['Correlation']:.2f}")
            st.markdown("**Most redundant** (highest correlation):")
            for _, p in _pairs_df.tail(3).iterrows():
                st.caption(f"{p['Group A']} / {p['Group B']}: {p['Correlation']:.2f}")

    # ── Incremental Analysis ──
    st.markdown("---")
    st.subheader("Incremental Analysis — Adding Sectors to Multi-Asset")
    st.caption(
        "Starting from the Multi-Asset base (SPY, TLT, GLD, EFA, IWM, USO, HYG, VNQ), "
        "what happens to the best method's Sharpe when you add each sector group?"
    )

    base_group = "Multi-Asset"
    sector_groups = [g for g in groups_with_data if g != base_group and g not in ("Mega Caps", "Global Macro")]

    if base_group in groups_with_data and sector_groups:
        base_tks = [t for t in PRESET_GROUPS[base_group] if t in available_tickers]
        incr_results = []

        # Baseline
        base_ret = grid_returns[base_tks]
        base_wf = _run_walkforward(base_ret, ma_est_days, rebal_period, ma_denoise)
        base_sharpes = {m: _portfolio_metrics(r, m)["Sharpe"] for m, r in base_wf.items()}
        if not base_sharpes:
            st.warning("Multi-Asset base did not produce valid walk-forward results. "
                       "Try a shorter estimation window or longer lookback.")
        else:
            best_base_method = max(base_sharpes, key=base_sharpes.get)
            base_best_sharpe = base_sharpes[best_base_method]

            base_best_metrics = _portfolio_metrics(base_wf[best_base_method], best_base_method)

            incr_results.append({
                "Universe": "Multi-Asset (base)",
                "Tickers": len(base_tks),
                "Best Method": best_base_method,
                "Sharpe": base_best_sharpe,
                "Delta": 0,
                "Ann. Return": base_best_metrics["Ann. Return"],
                "Max DD": base_best_metrics["Max DD"],
                "Return Delta": 0,
                "DD Delta": 0,
            })

            for sg in sector_groups:
                combined_tks = sorted(set(base_tks + [t for t in PRESET_GROUPS[sg] if t in available_tickers]))
                if len(combined_tks) <= len(base_tks):
                    continue
                c_ret = grid_returns[combined_tks]
                c_wf = _run_walkforward(c_ret, ma_est_days, rebal_period, ma_denoise)
                c_sharpes = {m: _portfolio_metrics(r, m)["Sharpe"] for m, r in c_wf.items()}
                if not c_sharpes:
                    continue
                c_best_method = max(c_sharpes, key=c_sharpes.get)
                c_best_sharpe = c_sharpes[c_best_method]
                c_metrics = _portfolio_metrics(c_wf[c_best_method], c_best_method)
                incr_results.append({
                    "Universe": f"+ {sg}",
                    "Tickers": len(combined_tks),
                    "Best Method": c_best_method,
                    "Sharpe": c_best_sharpe,
                    "Delta": c_best_sharpe - base_best_sharpe,
                    "Ann. Return": c_metrics["Ann. Return"],
                    "Max DD": c_metrics["Max DD"],
                    "Return Delta": c_metrics["Ann. Return"] - base_best_metrics["Ann. Return"],
                    "DD Delta": c_metrics["Max DD"] - base_best_metrics["Max DD"],
                })

            if incr_results:
                incr_df = pd.DataFrame(incr_results)

                fig_incr = go.Figure()
                fig_incr.add_trace(go.Bar(
                    x=incr_df["Universe"], y=incr_df["Delta"],
                    marker_color=[
                        "#555" if d == 0 else "#00ff88" if d > 0 else "#ff4444"
                        for d in incr_df["Delta"]
                    ],
                    text=[f"{d:+.2f}" if d != 0 else "base" for d in incr_df["Delta"]],
                    textposition="outside",
                ))
                fig_incr.add_hline(y=0, line_dash="dash", line_color="#555")
                fig_incr.update_layout(
                    template="plotly_dark", height=380,
                    title=f"Sharpe Impact of Adding Each Sector to Multi-Asset Base ({best_base_method}: {base_best_sharpe:.2f})",
                    yaxis_title="Sharpe Change vs Base",
                    margin=dict(l=0, r=0, t=40, b=0),
                )
                st.plotly_chart(fig_incr, use_container_width=True, config=PLOTLY_NOBAR)

                # Detail table
                incr_display = incr_df.copy()
                incr_display["Sharpe"] = incr_display["Sharpe"].apply(lambda v: f"{v:.2f}")
                incr_display["Delta"] = incr_display["Delta"].apply(lambda v: f"{v:+.2f}" if v != 0 else "—")
                incr_display["Ann. Return"] = incr_display["Ann. Return"].apply(lambda v: f"{v*100:+.1f}%")
                incr_display["Max DD"] = incr_display["Max DD"].apply(lambda v: f"{v*100:.1f}%")
                incr_display["Return Delta"] = incr_display["Return Delta"].apply(lambda v: f"{v*100:+.1f}pp" if v != 0 else "—")
                incr_display["DD Delta"] = incr_display["DD Delta"].apply(lambda v: f"{v*100:+.1f}pp" if v != 0 else "—")
                st.dataframe(incr_display, use_container_width=True, hide_index=True)

    # ── Two-Layer Hierarchical Allocation ──
    st.markdown("---")
    st.subheader("Hierarchical Allocation — Two-Layer Portfolio Construction")
    st.caption(
        "**Layer 1:** Optimize capital allocation *between* qualifying groups. "
        "**Layer 2:** Within each group, use that group's best method to allocate *within* tickers. "
        "Final weight = group allocation x within-group weight."
    )

    _has_incr = "incr_results" in dir() and incr_results and len(incr_results) > 1

    if not _has_incr:
        st.info("Incremental analysis did not produce results. Cannot build hierarchical portfolio.")
    else:
        incr_for_filter = pd.DataFrame(incr_results)

        _max_sharpe = float(incr_for_filter["Sharpe"].max())
        _median_sharpe = float(incr_for_filter["Sharpe"].median())
        # Default to median — includes roughly half the groups for diversification
        _default_thresh = round(min(_median_sharpe, _max_sharpe * 0.5) / 0.05) * 0.05
        opt_threshold = st.slider(
            "Sharpe threshold (include sectors above this)",
            0.0, _max_sharpe,
            max(0.0, _default_thresh), 0.05, key="ma_opt_thresh",
        )

        qualifying = incr_for_filter[incr_for_filter["Sharpe"] >= opt_threshold]
        included_labels = qualifying["Universe"].tolist()

        included_groups = ["Multi-Asset"]
        for label in included_labels:
            if label.startswith("+ "):
                gname = label[2:]
                if gname in PRESET_GROUPS:
                    included_groups.append(gname)
        included_groups = sorted(set(included_groups))

        st.markdown(
            f"**{len(included_groups)} groups qualify** (Sharpe >= {opt_threshold:.2f}): "
            f"{', '.join(included_groups)}"
        )

        if len(included_groups) < 2:
            st.warning("Need at least 2 qualifying groups for hierarchical allocation.")
        else:
            with st.spinner(f"Building two-layer portfolio ({len(included_groups)} groups)..."):
                # ═══════════════════════════════════
                # LAYER 2: Within-group optimization
                # ═══════════════════════════════════
                group_daily_ret = {}       # group name -> pd.Series of daily OOS returns
                group_best_method = {}     # group name -> best method name
                group_within_weights = {}  # group name -> pd.Series of ticker weights
                group_tickers_map = {}     # group name -> list of tickers

                for gname in included_groups:
                    group_tks = [t for t in PRESET_GROUPS[gname] if t in available_tickers]
                    if len(group_tks) < 3:
                        continue
                    g_ret = grid_returns[group_tks]
                    g_wf = _run_walkforward(g_ret, ma_est_days, rebal_period, ma_denoise)
                    if not g_wf:
                        continue

                    # Best method for this group
                    g_sharpes = {m: _portfolio_metrics(r, m)["Sharpe"] for m, r in g_wf.items()}
                    best_m = max(g_sharpes, key=g_sharpes.get)
                    group_best_method[gname] = best_m
                    group_daily_ret[gname] = g_wf[best_m]

                    # Current within-group weights (with forecasts if enabled)
                    _g_fc = None
                    if forecast_daily is not None:
                        _g_idx = [tickers.index(t) for t in group_tks if t in tickers]
                        if len(_g_idx) == len(group_tks):
                            _g_fc = forecast_daily[_g_idx]
                    g_current = _compute_all_weights(
                        g_ret.iloc[-ma_est_days:], group_tks, ma_denoise,
                        forecast_mu=_g_fc,
                    )
                    group_within_weights[gname] = pd.Series(g_current[best_m], index=group_tks)
                    group_tickers_map[gname] = group_tks

            valid_groups = list(group_daily_ret.keys())

            if len(valid_groups) < 2:
                st.error("Not enough groups produced valid backtests for hierarchical allocation.")
            else:
                # ═══════════════════════════════════
                # LAYER 1: Group-level optimization
                # ═══════════════════════════════════
                st.subheader("Layer 1 — Group Allocation")
                st.caption(
                    "Each group is treated as a single asset (using its best method's OOS returns). "
                    "The optimizer determines how to split capital between groups."
                )

                group_ret_df = pd.DataFrame(group_daily_ret).dropna()

                if len(group_ret_df) < 63:
                    st.error("Not enough overlapping data between groups for meta-level optimization. Try a longer lookback.")
                    st.stop()

                # Estimation window for meta-level: 40% of history, capped at 252 trading days
                meta_est = min(252, max(63, int(len(group_ret_df) * 0.4)))

                meta_wf = _run_walkforward(group_ret_df, meta_est, rebal_period, ma_denoise)

                if not meta_wf:
                    st.error("Group-level walk-forward failed. Try a longer lookback.")
                else:
                    meta_metrics = {}
                    for m, r in meta_wf.items():
                        meta_metrics[m] = _portfolio_metrics(r, m)

                    meta_ranked = sorted(meta_metrics, key=lambda m: meta_metrics[m]["Sharpe"], reverse=True)
                    meta_best_name = meta_ranked[0]
                    meta_best = meta_metrics[meta_best_name]

                    # Current group-level weights
                    meta_current = _compute_all_weights(
                        group_ret_df.iloc[-meta_est:], valid_groups, ma_denoise,
                    )
                    group_weights = pd.Series(
                        meta_current[meta_best_name], index=valid_groups,
                    ).sort_values(ascending=False)

                    gw_nz = group_weights[group_weights > 0.005]

                    gw1, gw2 = st.columns([2, 1])
                    with gw1:
                        fig_gw = go.Figure(data=go.Pie(
                            labels=gw_nz.index.tolist(),
                            values=(gw_nz.values * 100).round(1),
                            hole=0.45,
                            textinfo="label+percent",
                            textfont=dict(size=11),
                            marker=dict(line=dict(color="#1a1a2e", width=2)),
                        ))
                        fig_gw.update_layout(
                            template="plotly_dark", height=380,
                            title=f"Group Allocation — {meta_best_name}",
                            margin=dict(l=0, r=0, t=40, b=0),
                            showlegend=False,
                        )
                        st.plotly_chart(fig_gw, use_container_width=True, config=PLOTLY_NOBAR)

                    with gw2:
                        st.markdown(f"**Group-level method:** {meta_best_name}")
                        st.markdown(f"**Sharpe:** {meta_best['Sharpe']:.2f}")
                        st.markdown(f"**Ann. Return:** {meta_best['Ann. Return']*100:.1f}%")
                        st.markdown("---")
                        for g in gw_nz.index:
                            st.markdown(
                                f"**{g}** — {gw_nz[g]*100:.1f}% "
                                f"(via {group_best_method.get(g, '?')})"
                            )

                    # Group-level metrics table
                    meta_table = []
                    for m in meta_ranked:
                        mm = meta_metrics[m]
                        meta_table.append({
                            "Method": m,
                            "Sharpe": f"{mm['Sharpe']:.2f}",
                            "Ann. Return": f"{mm['Ann. Return']*100:+.1f}%",
                            "Max DD": f"{mm['Max DD']*100:.1f}%",
                        })
                    with st.expander("All group-level methods"):
                        st.dataframe(pd.DataFrame(meta_table), use_container_width=True, hide_index=True)

                    # ═══════════════════════════════════
                    # LAYER 2: Within-group detail
                    # ═══════════════════════════════════
                    st.subheader("Layer 2 — Within-Group Allocation")
                    st.caption(
                        "For each group, the best method's current weights. "
                        "These are scaled by the group allocation from Layer 1."
                    )

                    layer2_rows = []
                    for gname in gw_nz.index:
                        gw_pct = group_weights[gname]
                        method = group_best_method.get(gname, "?")
                        within = group_within_weights.get(gname, pd.Series(dtype=float))
                        for ticker in within.sort_values(ascending=False).index:
                            tw = within[ticker]
                            if tw < 0.005:
                                continue
                            layer2_rows.append({
                                "Group": gname,
                                "Group Alloc": f"{gw_pct*100:.1f}%",
                                "Method": method,
                                "Ticker": ticker,
                                "Within-Group Wt": f"{tw*100:.1f}%",
                                "Final Wt": f"{gw_pct * tw * 100:.2f}%",
                            })

                    if layer2_rows:
                        st.dataframe(pd.DataFrame(layer2_rows), use_container_width=True, hide_index=True)

                    # ═══════════════════════════════════
                    # FINAL COMBINED PORTFOLIO
                    # ═══════════════════════════════════
                    st.subheader("Final Portfolio — Combined Weights")
                    st.caption(
                        "The product of Layer 1 (group allocation) and Layer 2 (within-group weights). "
                        "This is what you would actually hold."
                    )

                    final_weights = {}
                    for gname in valid_groups:
                        gw_pct = group_weights.get(gname, 0)
                        if gw_pct < 0.001:
                            continue
                        within = group_within_weights.get(gname, pd.Series(dtype=float))
                        for ticker, tw in within.items():
                            if tw < 0.001:
                                continue
                            final_weights[ticker] = final_weights.get(ticker, 0) + gw_pct * tw

                    fw_total = sum(final_weights.values())
                    if fw_total > 0:
                        final_weights = {k: v / fw_total for k, v in final_weights.items()}

                    fw_series = pd.Series(final_weights).sort_values(ascending=False)
                    fw_nz = fw_series[fw_series > 0.002]

                    dollar_rows = []
                    dollar_df = pd.DataFrame()
                    if fw_nz.empty:
                        st.warning("All final weights are near zero. Check group allocation.")
                    else:
                        # ── SPY comparison banner ──
                        if spy_row is not None:
                            h_sharpe = meta_best["Sharpe"]
                            h_ret = meta_best["Ann. Return"]
                            h_dd = meta_best["Max DD"]
                            spy_s = spy_row["Sharpe"]
                            spy_r = spy_row["Ann. Return"]
                            spy_d = spy_row["Max DD"]
                            h_beats = h_sharpe > spy_s
                            h_color = "#00ff88" if h_beats else "#ff4444"
                            _n = COLORS["text_muted"]
                            st.markdown(
                                f'<div style="background:{COLORS["card_bg"]};border:1px solid {h_color};'
                                f'border-radius:8px;padding:16px 20px;margin-bottom:16px;">'
                                f'<div style="color:{h_color};font-weight:700;font-size:1.1rem;margin-bottom:10px;">'
                                f'HIERARCHICAL PORTFOLIO {"OUTPERFORMS" if h_beats else "UNDERPERFORMS"} SPY</div>'
                                f'<table style="width:100%;border-collapse:collapse;color:{COLORS["text_primary"]};font-size:0.95rem;">'
                                f'<tr style="border-bottom:1px solid {COLORS["card_border"]};">'
                                f'<td style="padding:5px 0;"><b>{meta_best_name}</b> ({len(fw_nz)} positions, {len(gw_nz)} groups)</td>'
                                f'<td style="padding:5px 0;color:{_n};">vs SPY</td>'
                                f'<td style="padding:5px 0;color:{h_color};text-align:right;">Edge</td></tr>'
                                f'<tr style="border-bottom:1px solid {COLORS["card_border"]};">'
                                f'<td style="padding:5px 0;">Sharpe <b>{h_sharpe:.2f}</b></td>'
                                f'<td style="padding:5px 0;color:{_n};">{spy_s:.2f}</td>'
                                f'<td style="padding:5px 0;color:{h_color};text-align:right;">{h_sharpe-spy_s:+.2f}</td></tr>'
                                f'<tr style="border-bottom:1px solid {COLORS["card_border"]};">'
                                f'<td style="padding:5px 0;">Return <b>{h_ret*100:+.1f}%</b></td>'
                                f'<td style="padding:5px 0;color:{_n};">{spy_r*100:+.1f}%</td>'
                                f'<td style="padding:5px 0;color:{h_color};text-align:right;">{(h_ret-spy_r)*100:+.1f}pp</td></tr>'
                                f'<tr>'
                                f'<td style="padding:5px 0;">Max DD <b>{h_dd*100:.1f}%</b></td>'
                                f'<td style="padding:5px 0;color:{_n};">{spy_d*100:.1f}%</td>'
                                f'<td style="padding:5px 0;color:{h_color};text-align:right;">{(h_dd-spy_d)*100:+.1f}pp</td></tr>'
                                f'</table></div>',
                                unsafe_allow_html=True,
                            )

                        # ── Top positions quick view ──
                        hq_cols = st.columns(min(len(fw_nz), 8))
                        for qi, (qt, qw) in enumerate(fw_nz.head(8).items()):
                            hq_cols[qi].metric(qt, f"{qw*100:.1f}%")
                        if len(fw_nz) > 8:
                            st.caption(f"+ {len(fw_nz) - 8} more positions below.")

                        # ── Donut chart + detail ──
                        fc1, fc2 = st.columns([2, 1])
                        with fc1:
                            fig_fw = go.Figure(data=go.Pie(
                                labels=fw_nz.index.tolist(),
                                values=(fw_nz.values * 100).round(2),
                                hole=0.45,
                                textinfo="label+percent",
                                textfont=dict(size=9),
                                marker=dict(line=dict(color="#1a1a2e", width=1)),
                            ))
                            fig_fw.update_layout(
                                template="plotly_dark", height=450,
                                title="Final Hierarchical Portfolio",
                                margin=dict(l=0, r=0, t=40, b=0),
                                showlegend=False,
                            )
                            st.plotly_chart(fig_fw, use_container_width=True, config=PLOTLY_NOBAR)

                        with fc2:
                            hhi_f = sum(v ** 2 for v in final_weights.values())
                            st.markdown(f"**{len(fw_nz)} positions** · Effective N: {1/hhi_f:.1f}" if hhi_f > 0 else "")
                            st.markdown("---")
                            for t in fw_nz.head(15).index:
                                st.markdown(f"**{t}** — {fw_nz[t]*100:.2f}%")
                            if len(fw_nz) > 15:
                                st.caption(f"+ {len(fw_nz) - 15} more positions")

                        # ── Download buttons ──
                        dl_c1, dl_c2 = st.columns(2)

                        # Final weights CSV
                        dl_weights = pd.DataFrame({
                            "Ticker": fw_nz.index,
                            "Weight": fw_nz.values.round(4),
                            "Weight %": (fw_nz.values * 100).round(2),
                            "Dollar ($100)": (fw_nz.values * 100).round(2),
                        })
                        dl_c1.download_button(
                            "Download Final Weights (CSV)",
                            dl_weights.to_csv(index=False),
                            "hierarchical_portfolio_weights.csv",
                            "text/csv", key="ma_dl_hier_weights",
                        )

                        # Dollar allocation CSV
                        dollar_rows = []
                        for gname in gw_nz.index:
                            gw_pct = group_weights[gname]
                            g_dollars = gw_pct * 100
                            method = group_best_method.get(gname, "?")
                            within = group_within_weights.get(gname, pd.Series(dtype=float))
                            for ticker in within.sort_values(ascending=False).index:
                                tw = within[ticker]
                                if tw < 0.005:
                                    continue
                                t_dollars = g_dollars * tw
                                dollar_rows.append({
                                    "Group": gname,
                                    "Group $": round(g_dollars, 2),
                                    "Method": method,
                                    "Ticker": ticker,
                                    "Within Wt %": round(tw * 100, 1),
                                    "Ticker $": round(t_dollars, 2),
                                })

                        if dollar_rows:
                            dollar_df = pd.DataFrame(dollar_rows)
                            dl_c2.download_button(
                                "Download Dollar Allocation (CSV)",
                                dollar_df.to_csv(index=False),
                                "hierarchical_dollar_allocation.csv",
                                "text/csv", key="ma_dl_hier_dollars",
                            )

                    # ── Dollar allocation table ──
                    st.subheader("Dollar Allocation — $100 Example")
                    if not fw_nz.empty and dollar_rows:
                        display_dollar = dollar_df.copy()
                        display_dollar["Group $"] = display_dollar["Group $"].apply(lambda v: f"${v:.2f}")
                        display_dollar["Within Wt %"] = display_dollar["Within Wt %"].apply(lambda v: f"{v:.1f}%")
                        display_dollar["Ticker $"] = display_dollar["Ticker $"].apply(lambda v: f"${v:.2f}")
                        st.dataframe(display_dollar, use_container_width=True, hide_index=True)

    # ══════════════════════════════════════════════════════════════════════════
    # FACTOR ATTRIBUTION — Fama-French decomposition of hierarchical portfolio
    # ══════════════════════════════════════════════════════════════════════════
    if "meta_wf" in dir() and meta_wf and "meta_best_name" in dir():
        st.markdown("---")
        st.subheader("Factor Attribution — Hierarchical Portfolio")
        st.caption(
            "Decomposes the hierarchical portfolio's OOS returns into Fama-French "
            "5 factors + momentum. Shows what's driving performance: is it pure beta, "
            "value tilt, size exposure, or genuine alpha?"
        )

        with error_boundary("Factor Attribution"):
            _hier_ret = meta_wf.get(meta_best_name)
            if _hier_ret is not None and len(_hier_ret) > 60:
                # Fetch factor data from Ken French's library
                @st.cache_data(ttl=86400, show_spinner=False)
                def _fetch_ff_factors():
                    try:
                        import io, zipfile, requests
                        # Fama-French 5 factors + momentum (daily)
                        url5 = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_Research_Data_5_Factors_2x3_daily_CSV.zip"
                        urlm = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_Momentum_Factor_daily_CSV.zip"

                        r5 = requests.get(url5, timeout=15)
                        with zipfile.ZipFile(io.BytesIO(r5.content)) as z:
                            fname = [n for n in z.namelist() if n.endswith('.CSV')][0]
                            raw = z.read(fname).decode("utf-8")
                        # Parse: skip header rows, find data start
                        lines = raw.split("\n")
                        start = next(i for i, l in enumerate(lines) if l.strip()[:2].isdigit())
                        data = []
                        for l in lines[start:]:
                            parts = l.strip().split(",")
                            if len(parts) >= 6 and parts[0].strip().isdigit():
                                data.append([p.strip() for p in parts[:6]])
                        ff5 = pd.DataFrame(data, columns=["date", "Mkt-RF", "SMB", "HML", "RMW", "CMA"])
                        ff5["date"] = pd.to_datetime(ff5["date"], format="%Y%m%d")
                        for c in ["Mkt-RF", "SMB", "HML", "RMW", "CMA"]:
                            ff5[c] = pd.to_numeric(ff5[c], errors="coerce") / 100
                        ff5 = ff5.set_index("date")

                        # Momentum factor
                        rm = requests.get(urlm, timeout=15)
                        with zipfile.ZipFile(io.BytesIO(rm.content)) as z:
                            fname = [n for n in z.namelist() if n.endswith('.CSV')][0]
                            raw_m = z.read(fname).decode("utf-8")
                        lines_m = raw_m.split("\n")
                        start_m = next(i for i, l in enumerate(lines_m) if l.strip()[:2].isdigit())
                        data_m = []
                        for l in lines_m[start_m:]:
                            parts = l.strip().split(",")
                            if len(parts) >= 2 and parts[0].strip().isdigit():
                                data_m.append([parts[0].strip(), parts[1].strip()])
                        mom = pd.DataFrame(data_m, columns=["date", "Mom"])
                        mom["date"] = pd.to_datetime(mom["date"], format="%Y%m%d")
                        mom["Mom"] = pd.to_numeric(mom["Mom"], errors="coerce") / 100
                        mom = mom.set_index("date")

                        factors = ff5.join(mom, how="inner")
                        return factors
                    except Exception as e:
                        logger.warning(f"FF factor fetch failed: {e}")
                        return None

                ff = _fetch_ff_factors()
                if ff is not None and not ff.empty:
                    # Align portfolio returns with factor data
                    port_ret = _hier_ret.copy()
                    if not isinstance(port_ret.index, pd.DatetimeIndex):
                        port_ret.index = pd.to_datetime(port_ret.index)
                    common_idx = port_ret.index.intersection(ff.index)

                    if len(common_idx) > 30:
                        y = port_ret.loc[common_idx].values
                        X = ff.loc[common_idx].values
                        factor_names = list(ff.columns)

                        # OLS regression
                        X_const = np.column_stack([np.ones(len(X)), X])
                        try:
                            betas = np.linalg.lstsq(X_const, y, rcond=None)[0]
                            alpha_daily = betas[0]
                            factor_betas = betas[1:]

                            # Predicted returns & R²
                            y_hat = X_const @ betas
                            ss_res = np.sum((y - y_hat) ** 2)
                            ss_tot = np.sum((y - y.mean()) ** 2)
                            r_squared = 1 - ss_res / ss_tot if ss_tot > 0 else 0

                            alpha_annual = alpha_daily * 252

                            # Factor contributions (annualized)
                            factor_means = ff.loc[common_idx].mean().values * 252
                            factor_contribs = factor_betas * factor_means

                            # Display
                            fa1, fa2, fa3 = st.columns(3)
                            alpha_color = COLORS["success"] if alpha_annual > 0 else COLORS["danger"]
                            fa1.metric("Alpha (annualized)", f"{alpha_annual*100:+.2f}%")
                            fa2.metric("R-squared", f"{r_squared:.1%}")
                            fa3.metric("Observations", f"{len(common_idx)} days")

                            # Factor exposure table
                            factor_rows = []
                            for i, fn in enumerate(factor_names):
                                factor_rows.append({
                                    "Factor": fn,
                                    "Beta": f"{factor_betas[i]:.3f}",
                                    "Factor Return": f"{factor_means[i]*100:+.1f}%",
                                    "Contribution": f"{factor_contribs[i]*100:+.2f}%",
                                })
                            factor_rows.append({
                                "Factor": "Alpha",
                                "Beta": "—",
                                "Factor Return": "—",
                                "Contribution": f"{alpha_annual*100:+.2f}%",
                            })
                            st.dataframe(pd.DataFrame(factor_rows), use_container_width=True, hide_index=True)

                            # Attribution waterfall chart
                            wf_labels = factor_names + ["Alpha"]
                            wf_values = list(factor_contribs * 100) + [alpha_annual * 100]

                            fig_wf = go.Figure(go.Waterfall(
                                x=wf_labels, y=wf_values,
                                measure=["relative"] * len(factor_names) + ["total"],
                                connector=dict(line=dict(color=COLORS["text_muted"], width=1)),
                                increasing=dict(marker_color=COLORS["success"]),
                                decreasing=dict(marker_color=COLORS["danger"]),
                                totals=dict(marker_color=COLORS["accent"]),
                                text=[f"{v:+.2f}%" for v in wf_values],
                                textposition="outside",
                            ))
                            fig_wf.update_layout(
                                template="plotly_dark", height=380,
                                title="Return Attribution Waterfall (Annualized)",
                                yaxis_title="Contribution (%)",
                                margin=dict(l=50, r=20, t=40, b=50),
                            )
                            st.plotly_chart(fig_wf, use_container_width=True, config={"displayModeBar": False})

                            # Interpretation
                            mkt_beta = factor_betas[0]
                            if mkt_beta > 0.8:
                                st.caption(f"Market beta of {mkt_beta:.2f} — portfolio is mostly equity beta, not diversified alpha.")
                            elif mkt_beta < 0.3:
                                st.caption(f"Market beta of {mkt_beta:.2f} — portfolio has low market sensitivity, alpha-oriented.")
                            if alpha_annual > 0.02:
                                st.success(f"Positive alpha of {alpha_annual*100:+.2f}% after controlling for 6 factors — genuine skill signal.")
                            elif alpha_annual < -0.02:
                                st.warning(f"Negative alpha of {alpha_annual*100:+.2f}% — portfolio underperforms factor exposure.")

                        except Exception as e:
                            st.warning(f"Factor regression failed: {e}")
                    else:
                        st.info(f"Only {len(common_idx)} overlapping days with factor data — need 30+.")
                else:
                    st.info("Could not fetch Fama-French factor data.")
            else:
                st.info("Need 60+ days of hierarchical portfolio OOS returns for factor attribution.")

    st.caption(
        "Backtested results do not guarantee future performance. "
        "Grid analysis uses the same walk-forward methodology as the main backtest — no look-ahead bias."
    )

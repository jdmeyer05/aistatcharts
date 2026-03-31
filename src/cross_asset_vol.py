"""Cross-Asset Volatility Analysis — shared module for pages 46 and 48.

Provides universe definitions, parallel data loading, metrics computation,
smile interpolation, and implied correlation calculation.
"""

import logging
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ── Universe Definition ──────────────────────────────────────────────────────

SCAN_UNIVERSE = {
    "Sectors": {
        "XLE": "Energy", "XLF": "Financials", "XLK": "Technology",
        "XLV": "Healthcare", "XLI": "Industrials", "XLC": "Communication",
        "XLY": "Consumer Disc", "XLP": "Consumer Staples", "XLU": "Utilities",
        "XLB": "Materials", "XLRE": "Real Estate",
    },
    "Macro": {
        "SPY": "S&P 500", "QQQ": "Nasdaq 100", "IWM": "Russell 2000",
        "TLT": "Long Bonds", "GLD": "Gold", "USO": "Crude Oil",
        "EFA": "Intl Developed", "EEM": "Emerging Mkts", "HYG": "High Yield",
    },
}

ALL_TICKERS = {}
for _group in SCAN_UNIVERSE.values():
    ALL_TICKERS.update(_group)


def get_rfr():
    """Get risk-free rate from FRED (cached at call site)."""
    try:
        from src.market_data import fetch_fred_series
        df = fetch_fred_series("DGS3MO", periods=5)
        if not df.empty:
            return df["value"].iloc[-1] / 100
    except Exception:
        pass
    return 0.045


# ── ATM IV and Delta Helpers ─────────────────────────────────────────────────

def atm_iv(chain, spot, opt_type="call"):
    """Get ATM implied volatility from a chain DataFrame."""
    sub = chain[chain["contract_type"] == opt_type].reset_index(drop=True)
    if sub.empty:
        return 0.25
    atm_row = sub.loc[(sub["strike_price"] - spot).abs().idxmin()]
    iv = atm_row.get("implied_volatility", 0) or 0
    return iv if iv > 0 else 0.25


def find_delta_strike(chain, spot, target_delta, opt_type):
    """Find the strike closest to a target delta. Prefers OI > 0."""
    sub = chain[chain["contract_type"] == opt_type].copy()
    sub = sub[sub["implied_volatility"] > 0]
    if sub.empty:
        return None, None
    if "open_interest" in sub.columns:
        sub_liquid = sub[sub["open_interest"].fillna(0) > 0]
        if len(sub_liquid) >= 3:
            sub = sub_liquid
    sub["delta_abs"] = sub["delta"].abs()
    sub["delta_dist"] = (sub["delta_abs"] - abs(target_delta)).abs()
    sub = sub.dropna(subset=["delta_dist"])
    if sub.empty:
        return None, None
    best = sub.loc[sub["delta_dist"].idxmin()]
    return float(best["strike_price"]), float(best.get("implied_volatility", 0))


def interpolate_smile(chain, spot, moneyness_points=None):
    """Interpolate IV at standardized moneyness points from a chain.

    Returns dict: {moneyness: iv_value} or None if insufficient data.
    moneyness_points defaults to [0.90, 0.95, 0.98, 1.00, 1.02, 1.05, 1.10].
    """
    if moneyness_points is None:
        moneyness_points = [0.90, 0.95, 0.98, 1.00, 1.02, 1.05, 1.10]

    # Use puts below spot, calls at/above spot (OTM for both sides)
    calls = chain[(chain["contract_type"] == "call") & (chain["strike_price"] >= spot)]
    puts = chain[(chain["contract_type"] == "put") & (chain["strike_price"] <= spot)]
    combined = pd.concat([calls, puts])
    combined = combined[combined["implied_volatility"] > 0]

    # Prefer contracts with OI
    if "open_interest" in combined.columns:
        liquid = combined[combined["open_interest"].fillna(0) > 0]
        if len(liquid) >= 5:
            combined = liquid

    if len(combined) < 3:
        return None

    combined = combined.copy().reset_index(drop=True)
    combined["moneyness"] = combined["strike_price"] / spot

    result = {}
    for m in moneyness_points:
        target_strike = spot * m
        nearby = combined.iloc[(combined["strike_price"] - target_strike).abs().argsort()[:2]]
        if len(nearby) == 0:
            result[m] = None
            continue
        # If closest strike is within 3% of target moneyness, use its IV
        closest = nearby.iloc[0]
        if abs(closest["moneyness"] - m) < 0.03:
            result[m] = float(closest["implied_volatility"])
        else:
            result[m] = None

    return result


def compute_implied_correlation(spy_iv, sector_ivs, sector_weights=None):
    """Implied correlation from index vs sector IVs.

    Formula: rho = (sigma_index^2 - weighted_avg(sigma_sector^2)/N) / (weighted_avg(sigma_sector^2) * (1 - 1/N))

    Args:
        spy_iv: ATM IV of index (SPY)
        sector_ivs: list of sector ATM IVs
        sector_weights: optional weights (e.g., market cap proportional). Equal-weight if None.
    """
    if not sector_ivs or spy_iv <= 0:
        return None
    valid = [iv for iv in sector_ivs if iv > 0]
    n = len(valid)
    if n < 2:
        return None
    if sector_weights and len(sector_weights) == len(valid):
        w = np.array(sector_weights)
        w = w / w.sum()
        avg_sector_var = np.sum(w * np.array([iv ** 2 for iv in valid]))
    else:
        avg_sector_var = np.mean([iv ** 2 for iv in valid])
    if avg_sector_var <= 0:
        return None
    rho = (spy_iv ** 2 - avg_sector_var / n) / (avg_sector_var * (1 - 1 / n))
    return max(0.0, min(1.0, rho))


# ── Parallel Data Loading ────────────────────────────────────────────────────

def load_universe_data(tickers_dict, rfr=0.045):
    """Load price + options data for multiple tickers in parallel.

    Args:
        tickers_dict: {ticker: label} mapping
        rfr: risk-free rate

    Returns:
        dict: {ticker: {spot, chains, expirations, hv20, px_df, label}}
    """
    from src.data_engine import fetch_massive_data, get_expiration_dates, fetch_options_chain
    from src.options_models import fill_missing_options_data
    from concurrent.futures import ThreadPoolExecutor, as_completed

    today_str = pd.Timestamp.now().strftime("%Y-%m-%d")
    tickers = list(tickers_dict.keys())

    def _load_one(tk):
        try:
            px = fetch_massive_data(tk, 252)
            if px is None or px.empty:
                return None
            spot = float(px["Close"].iloc[-1])
            if pd.isna(spot) or spot <= 0:
                return None

            all_exps = get_expiration_dates(tk)
            valid_exps = [e for e in (all_exps or []) if e >= today_str]
            monthly = [e for e in valid_exps
                       if 15 <= pd.to_datetime(e).day <= 21 and pd.to_datetime(e).weekday() == 4][:3]
            if len(monthly) < 2:
                monthly = valid_exps[:3]

            chains = {}
            for exp in monthly:
                try:
                    cdf = fetch_options_chain(tk, exp)
                    if cdf is not None and not cdf.empty:
                        cdf = fill_missing_options_data(cdf, spot, risk_free_rate=rfr)
                        chains[exp] = cdf
                except Exception:
                    pass

            if chains:
                rets = px["Close"].pct_change().dropna()
                hv20 = float((rets.rolling(20).std() * np.sqrt(252)).dropna().iloc[-1]) if len(rets) > 20 else None
                return {
                    "tk": tk, "spot": spot, "chains": chains,
                    "expirations": sorted(chains.keys()),
                    "hv20": hv20, "px_df": px, "label": tickers_dict.get(tk, tk),
                }
        except Exception as e:
            logger.warning(f"Failed to load {tk}: {e}")
        return None

    result = {}
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(_load_one, tk): tk for tk in tickers}
        for future in as_completed(futures):
            data = future.result()
            if data:
                tk = data.pop("tk")
                result[tk] = data

    return result


# ── Metrics Computation ──────────────────────────────────────────────────────

def compute_cross_asset_metrics(ticker_data, rfr=0.045):
    """Compute per-ticker vol metrics from loaded chain data.

    Args:
        ticker_data: dict from load_universe_data()
        rfr: risk-free rate

    Returns:
        pd.DataFrame with columns: Ticker, Label, Group, Spot, Front_IV, Back_IV,
        IV_HV, Put_Skew, Risk_Rev, TS_Slope, VRP, Impl_Move, HV20, PC_Ratio, IV_Pctile
    """
    rows = []
    for tk, td in ticker_data.items():
        spot = td["spot"]
        chains = td["chains"]
        exps = td["expirations"]
        hv20 = td.get("hv20")
        label = td.get("label", tk)

        if not exps or not chains:
            continue

        # Determine group
        group = "Other"
        for gname, gtickers in SCAN_UNIVERSE.items():
            if tk in gtickers:
                group = gname
                break

        front_chain = chains[exps[0]]
        front_iv = atm_iv(front_chain, spot, "call")
        back_iv = atm_iv(chains[exps[-1]], spot, "call") if len(exps) >= 2 else front_iv

        # Put skew
        _, p25_iv = find_delta_strike(front_chain, spot, 0.25, "put")
        _, c25_iv = find_delta_strike(front_chain, spot, 0.25, "call")
        put_skew = (p25_iv / front_iv) if p25_iv and front_iv > 0 else 1.0
        risk_rev = ((c25_iv - p25_iv) * 100) if c25_iv and p25_iv else 0.0
        butterfly = ((p25_iv + c25_iv - 2 * front_iv) * 100) if p25_iv and c25_iv and front_iv > 0 else 0.0

        # Term structure slope
        front_dte = max((pd.to_datetime(exps[0]) - pd.Timestamp.now()).days, 1)
        back_dte = max((pd.to_datetime(exps[-1]) - pd.Timestamp.now()).days, 1) if len(exps) >= 2 else front_dte
        ts_slope = (back_iv - front_iv) / max(back_dte - front_dte, 1) * 30  # per month

        # VRP & IV/HV
        iv_hv = (front_iv / hv20) if hv20 and hv20 > 0 else None
        vrp = (front_iv ** 2 - hv20 ** 2) * 100 if hv20 and hv20 > 0 else None

        # Implied move
        impl_move = 0
        try:
            atm_calls = front_chain[(front_chain["contract_type"] == "call")].reset_index(drop=True)
            atm_puts = front_chain[(front_chain["contract_type"] == "put")].reset_index(drop=True)
            if not atm_calls.empty and not atm_puts.empty:
                c_row = atm_calls.loc[(atm_calls["strike_price"] - spot).abs().idxmin()]
                p_row = atm_puts.loc[(atm_puts["strike_price"] - spot).abs().idxmin()]
                c_mid = ((c_row.get("bid", 0) or 0) + (c_row.get("ask", 0) or 0)) / 2
                p_mid = ((p_row.get("bid", 0) or 0) + (p_row.get("ask", 0) or 0)) / 2
                # Fallback to last_price if mid is zero (wide/stale quotes)
                if c_mid <= 0:
                    c_mid = c_row.get("last_price", 0) or 0
                if p_mid <= 0:
                    p_mid = p_row.get("last_price", 0) or 0
                if c_mid > 0 and p_mid > 0:
                    impl_move = (c_mid + p_mid) * 0.798 / spot * 100
        except Exception:
            pass

        # P/C ratio
        try:
            put_vol = front_chain[front_chain["contract_type"] == "put"]["volume"].sum()
            call_vol = front_chain[front_chain["contract_type"] == "call"]["volume"].sum()
            pc_ratio = put_vol / call_vol if call_vol > 0 else 1.0
        except Exception:
            pc_ratio = 1.0

        # IV percentile — use IV/HV ratio method for universe scans (fast)
        # Historical IV percentile from options_history is too slow for 20-ticker batch
        # (each ticker requires ~200 Polygon API calls). Use it on single-ticker pages only.
        iv_pctile = None
        try:
            px_df = td.get("px_df")
            if px_df is not None and len(px_df) > 60 and hv20 and hv20 > 0:
                hv_series = (px_df["Close"].pct_change().rolling(20).std() * np.sqrt(252)).dropna()
                hv_vals = hv_series.values[hv_series.values > 0]
                if len(hv_vals) > 10:
                    iv_hv_history = front_iv / hv_vals
                    current_iv_hv = front_iv / hv20
                    iv_pctile = float((iv_hv_history < current_iv_hv).mean() * 100)
        except Exception:
            pass

        # VRP in vol terms (more intuitive: IV - HV, not variance)
        vrp_vol = (front_iv - hv20) if hv20 and hv20 > 0 else None

        # Front month DTE for context
        front_dte_val = front_dte

        rows.append({
            "Ticker": tk, "Label": label, "Group": group, "Spot": spot,
            "Front_IV": front_iv, "Back_IV": back_iv, "IV_HV": iv_hv,
            "Put_Skew": put_skew, "Risk_Rev": risk_rev, "Butterfly": butterfly,
            "TS_Slope": ts_slope, "VRP": vrp, "VRP_Vol": vrp_vol,
            "Impl_Move": impl_move, "HV20": hv20,
            "PC_Ratio": pc_ratio, "IV_Pctile": iv_pctile, "Front_DTE": front_dte_val,
        })

    return pd.DataFrame(rows)


# ── Divergence Detection ─────────────────────────────────────────────────────

# Known correlated pairs — when their vol profiles diverge, it's a signal
CORRELATED_PAIRS = [
    ("XLE", "USO", "Energy sector vs crude oil"),
    ("XLK", "QQQ", "Tech sector vs Nasdaq"),
    ("XLF", "TLT", "Financials vs long bonds (inverse)"),
    ("GLD", "TLT", "Gold vs bonds (safe havens)"),
    ("SPY", "IWM", "Large cap vs small cap"),
    ("XLE", "GLD", "Energy vs gold (inflation hedge)"),
    ("EEM", "EFA", "Emerging vs developed markets"),
    ("XLY", "XLP", "Consumer discretionary vs staples (risk-on/off)"),
    ("SPY", "HYG", "Equities vs high yield (credit risk)"),
]


def detect_divergences(mdf, top_n=5):
    """Find correlated pairs with divergent vol profiles.

    Returns list of dicts: {pair, ticker_a, ticker_b, description, metric, a_val, b_val, spread, signal}
    """
    results = []
    tickers_in_data = set(mdf["Ticker"].values)

    for tk_a, tk_b, desc in CORRELATED_PAIRS:
        if tk_a not in tickers_in_data or tk_b not in tickers_in_data:
            continue
        a = mdf[mdf["Ticker"] == tk_a].iloc[0]
        b = mdf[mdf["Ticker"] == tk_b].iloc[0]

        # Check IV/HV divergence
        if pd.notna(a.get("IV_HV")) and pd.notna(b.get("IV_HV")):
            spread = abs(a["IV_HV"] - b["IV_HV"])
            if spread > 0.3:
                richer = tk_a if a["IV_HV"] > b["IV_HV"] else tk_b
                cheaper = tk_b if richer == tk_a else tk_a
                results.append({
                    "pair": f"{tk_a}/{tk_b}", "ticker_a": tk_a, "ticker_b": tk_b,
                    "description": desc, "metric": "IV/HV",
                    "a_val": a["IV_HV"], "b_val": b["IV_HV"], "spread": spread,
                    "signal": f"{richer} vol is rich ({a['IV_HV'] if richer == tk_a else b['IV_HV']:.2f}x) while {cheaper} is cheap ({b['IV_HV'] if richer == tk_a else a['IV_HV']:.2f}x)",
                })

        # Check skew divergence
        skew_spread = abs(a["Put_Skew"] - b["Put_Skew"])
        if skew_spread > 0.08:
            steeper = tk_a if a["Put_Skew"] > b["Put_Skew"] else tk_b
            results.append({
                "pair": f"{tk_a}/{tk_b}", "ticker_a": tk_a, "ticker_b": tk_b,
                "description": desc, "metric": "Skew",
                "a_val": a["Put_Skew"], "b_val": b["Put_Skew"], "spread": skew_spread,
                "signal": f"{steeper} has much steeper skew ({a['Put_Skew'] if steeper == tk_a else b['Put_Skew']:.2f}x) — fear is concentrated there",
            })

        # Check term structure divergence (one inverted, other not)
        if a["TS_Slope"] * b["TS_Slope"] < 0:  # opposite signs
            inverted = tk_a if a["TS_Slope"] < 0 else tk_b
            results.append({
                "pair": f"{tk_a}/{tk_b}", "ticker_a": tk_a, "ticker_b": tk_b,
                "description": desc, "metric": "Term Structure",
                "a_val": a["TS_Slope"], "b_val": b["TS_Slope"], "spread": abs(a["TS_Slope"] - b["TS_Slope"]),
                "signal": f"{inverted} is inverted (backwardation) while its pair is in contango — event risk is asset-specific, not broad",
            })

    # Sort by spread magnitude, return top N
    results.sort(key=lambda x: x["spread"], reverse=True)
    return results[:top_n]


def compute_metric_changes(current_mdf, previous_mdf):
    """Compute changes between two metric snapshots.

    Returns DataFrame with _chg suffix columns merged onto current_mdf.
    """
    if previous_mdf is None or previous_mdf.empty:
        return current_mdf

    change_cols = ["Front_IV", "Put_Skew", "IV_HV", "TS_Slope", "VRP_Vol"]
    prev = previous_mdf[["Ticker"] + [c for c in change_cols if c in previous_mdf.columns]].copy()
    prev.columns = ["Ticker"] + [f"{c}_prev" for c in change_cols if c in previous_mdf.columns]

    merged = current_mdf.merge(prev, on="Ticker", how="left")
    for c in change_cols:
        prev_col = f"{c}_prev"
        chg_col = f"{c}_chg"
        if prev_col in merged.columns and c in merged.columns:
            merged[chg_col] = merged[c] - merged[prev_col]

    # Drop prev columns
    merged = merged.drop(columns=[c for c in merged.columns if c.endswith("_prev")], errors="ignore")
    return merged


def compute_correlation_matrix(ticker_data, min_days=60):
    """Build pairwise return correlation matrix from price histories.

    Returns (corr_df, tickers_used) or (None, []).
    """
    returns = {}
    for tk, td in ticker_data.items():
        px = td.get("px_df")
        if px is not None and len(px) > min_days:
            rets = px["Close"].pct_change().dropna()
            if len(rets) > min_days:
                returns[tk] = rets.values[-min_days:]

    if len(returns) < 3:
        return None, []

    # Align lengths
    min_len = min(len(v) for v in returns.values())
    aligned = {tk: v[-min_len:] for tk, v in returns.items()}
    tickers = sorted(aligned.keys())
    matrix = np.array([aligned[tk] for tk in tickers])
    corr = np.corrcoef(matrix)
    corr_df = pd.DataFrame(corr, index=tickers, columns=tickers)
    return corr_df, tickers


def fetch_earnings_dates(tickers):
    """Fetch next earnings date for multiple tickers via yfinance.

    Returns dict: {ticker: {"date": date_str, "days": int}} for tickers with earnings within 60 days.
    """
    import yfinance as yf
    from datetime import datetime as dt

    result = {}
    today = dt.now().date()

    for tk in tickers:
        try:
            info = yf.Ticker(tk).info or {}
            ts = info.get("earningsTimestampStart")
            if ts and ts > 0:
                ed = dt.utcfromtimestamp(ts).date()
                days = (ed - today).days
                if 0 < days <= 60:
                    result[tk] = {"date": ed.isoformat(), "days": days}
        except Exception:
            continue

    return result


def compute_benchmark_context(ticker_data, mdf):
    """Compare current metrics to 30-day rolling HV averages for context.

    Returns dict: {ticker: {metric: {current, avg_30d, pct_change}}}
    """
    benchmarks = {}
    for tk, td in ticker_data.items():
        px = td.get("px_df")
        if px is None or len(px) < 60:
            continue
        rets = px["Close"].pct_change().dropna()
        hv_series = (rets.rolling(20).std() * np.sqrt(252)).dropna()
        if len(hv_series) < 30:
            continue

        hv_30d_avg = float(hv_series.tail(30).mean())
        hv_current = float(hv_series.iloc[-1]) if len(hv_series) > 0 else None

        row = mdf[mdf["Ticker"] == tk]
        if row.empty:
            continue
        r = row.iloc[0]
        front_iv = r.get("Front_IV", 0)

        benchmarks[tk] = {
            "hv_30d_avg": hv_30d_avg,
            "hv_current": hv_current,
            "iv_vs_hv30d": ((front_iv / hv_30d_avg - 1) * 100) if hv_30d_avg > 0 and front_iv > 0 else None,
        }

    return benchmarks

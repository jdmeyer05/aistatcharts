"""Portfolio risk models — factor betas, regime estimation, stressed correlations.
Extracted from Scenario Analysis for reuse across risk/scenario pages."""
import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Sector classification for concentration detection
SECTOR_MAP = {
    "AAPL": "Technology", "MSFT": "Technology", "NVDA": "Technology", "GOOGL": "Technology",
    "AMZN": "Consumer Discretionary", "META": "Technology", "TSLA": "Consumer Discretionary",
    "BRK.B": "Financials", "JPM": "Financials", "V": "Financials",
    "JNJ": "Healthcare", "UNH": "Healthcare", "PG": "Consumer Staples",
    "XOM": "Energy", "CVX": "Energy", "COP": "Energy",
    "HD": "Consumer Discretionary", "KO": "Consumer Staples", "PEP": "Consumer Staples",
    "COST": "Consumer Staples", "WMT": "Consumer Staples",
    "LLY": "Healthcare", "ABBV": "Healthcare", "MRK": "Healthcare", "PFE": "Healthcare",
    "BA": "Industrials", "CAT": "Industrials", "GE": "Industrials", "HON": "Industrials",
    "NEE": "Utilities", "DUK": "Utilities", "SO": "Utilities",
    "XLE": "Energy", "XLF": "Financials", "XLK": "Technology", "XLV": "Healthcare",
    "XLI": "Industrials", "XLC": "Communication", "XLY": "Consumer Discretionary",
    "XLP": "Consumer Staples", "XLU": "Utilities",
    "XLB": "Materials", "XLRE": "Real Estate",
    "SPY": "Broad Equity", "IWM": "Broad Equity", "DIA": "Broad Equity", "QQQ": "Broad Equity",
    "TLT": "Bonds", "IEF": "Bonds", "SHY": "Bonds", "AGG": "Bonds",
    "GLD": "Gold", "SLV": "Silver", "GDX": "Gold Miners",
}


def exp_weights(n: int, halflife: int = 60) -> np.ndarray:
    """Generate exponential decay weights. More recent observations get higher weight."""
    lam = np.log(2) / halflife
    w = np.exp(lam * np.arange(n))
    return w / w.sum() * n


def compute_factor_betas(daily_returns: pd.DataFrame, factor_changes: pd.DataFrame) -> dict:
    """Exponentially-weighted OLS regression with interaction term and rolling stability check.

    Returns dict of {ticker: {alpha, betas, r2, residual_std, stressed_residual_std,
    beta_stability, n_obs}}.
    """
    common_idx = daily_returns.index.intersection(factor_changes.index)
    if len(common_idx) < 30:
        return {}

    Y = daily_returns.loc[common_idx]
    X = factor_changes.loc[common_idx]
    factor_names = list(X.columns)

    n = len(common_idx)
    ew = exp_weights(n, halflife=60)
    sqrt_w = np.sqrt(ew)

    # Stress mask from VIX
    vix_col = "VIXCLS" if "VIXCLS" in X.columns else None
    stress_mask = np.zeros(n, dtype=bool)
    if vix_col is not None:
        vix_changes = X[vix_col].values
        stress_threshold = np.percentile(np.abs(vix_changes), 75)
        stress_mask = np.abs(vix_changes) > stress_threshold

    results = {}
    for ticker in Y.columns:
        y = Y[ticker].values
        X_vals = X.values
        mask = ~(np.isnan(y) | np.any(np.isnan(X_vals), axis=1))
        if mask.sum() < 30:
            continue

        y_clean = y[mask]
        X_clean = np.column_stack([np.ones(mask.sum()), X_vals[mask]])
        w_clean = sqrt_w[mask]

        try:
            Xw = X_clean * w_clean[:, None]
            yw = y_clean * w_clean
            coeffs, _, _, _ = np.linalg.lstsq(Xw, yw, rcond=None)
            y_pred = X_clean @ coeffs
            residuals = y_clean - y_pred
            ss_res = np.sum(residuals ** 2)
            ss_tot = np.sum((y_clean - np.mean(y_clean)) ** 2)
            r2 = max(0, min(1, 1 - ss_res / ss_tot if ss_tot > 0 else 0))
            residual_std = np.sqrt(ss_res / max(1, len(y_clean) - len(coeffs)))

            betas = {name: coeffs[i + 1] for i, name in enumerate(factor_names)}

            # Rolling beta stability
            mid = mask.sum() // 2
            beta_stability = 1.0
            if mid > 20:
                try:
                    c1, _, _, _ = np.linalg.lstsq(X_clean[:mid], y_clean[:mid], rcond=None)
                    c2, _, _, _ = np.linalg.lstsq(X_clean[mid:], y_clean[mid:], rcond=None)
                    b1, b2 = c1[1:], c2[1:]
                    if np.std(b1) > 0 and np.std(b2) > 0:
                        beta_stability = float(np.corrcoef(b1, b2)[0, 1])
                        beta_stability = max(0, beta_stability)
                except Exception:
                    pass

            stress_residuals = residuals[stress_mask[mask]] if stress_mask[mask].sum() > 5 else residuals
            stressed_residual_std = np.std(stress_residuals) if len(stress_residuals) > 0 else residual_std

            results[ticker] = {
                "alpha": coeffs[0],
                "betas": betas,
                "r2": r2,
                "residual_std": residual_std,
                "stressed_residual_std": stressed_residual_std,
                "beta_stability": round(beta_stability, 2),
                "n_obs": int(mask.sum()),
            }
        except Exception:
            continue

    return results


def estimate_regime_returns(factor_betas: dict, regime_factor_moves: dict,
                            daily_returns: pd.DataFrame = None, factor_changes: pd.DataFrame = None,
                            horizon_days: int = 252) -> dict:
    """Estimate regime returns using factor betas + block bootstrap for CIs.
    Uses stressed residual std for downside regimes."""
    from scipy.stats import t as t_dist
    STRESS_REGIMES = {"Recession", "Financial Crisis", "Stagflation"}

    bootstrap_returns = {}
    if daily_returns is not None and factor_changes is not None:
        common_idx = daily_returns.index.intersection(factor_changes.index)
        if len(common_idx) > 60:
            Y = daily_returns.loc[common_idx]
            X = factor_changes.loc[common_idx]
            if "VIXCLS" in X.columns:
                vix = X["VIXCLS"].values
                high_stress = np.abs(vix) > np.percentile(np.abs(vix), 75)
                low_stress = np.abs(vix) < np.percentile(np.abs(vix), 25)
                bootstrap_returns["stress"] = Y.loc[common_idx[high_stress]]
                bootstrap_returns["calm"] = Y.loc[common_idx[low_stress]]

    estimates = {}
    for regime, fmoves in regime_factor_moves.items():
        regime_est = {}
        use_stressed = regime in STRESS_REGIMES

        for ticker, info in factor_betas.items():
            betas = info["betas"]
            point_est = sum(betas.get(f, 0) * fmoves.get(f, 0) * horizon_days for f in betas)
            point_pct = point_est * 100

            res_std = info["stressed_residual_std"] if use_stressed else info["residual_std"]
            annual_std = res_std * np.sqrt(horizon_days) * 100
            t_mult = t_dist.ppf(0.9, df=5)
            lo = point_pct - t_mult * annual_std
            hi = point_pct + t_mult * annual_std

            bs_key = "stress" if use_stressed else "calm"
            if bs_key in bootstrap_returns and ticker in bootstrap_returns[bs_key].columns:
                bs_data = bootstrap_returns[bs_key][ticker].dropna().values
                if len(bs_data) > 40:
                    block_size = 20
                    n_sims = 500
                    sim_returns = []
                    rng = np.random.default_rng(42)
                    for _ in range(n_sims):
                        blocks = []
                        total_days = 0
                        while total_days < horizon_days:
                            start = rng.integers(0, max(1, len(bs_data) - block_size))
                            block = bs_data[start:start + block_size]
                            blocks.extend(block)
                            total_days += len(block)
                        cum_ret = np.prod(1 + np.array(blocks[:horizon_days])) - 1
                        sim_returns.append(cum_ret * 100)
                    sim_returns = np.array(sim_returns)
                    bs_point = np.median(sim_returns)
                    point_pct = 0.5 * point_pct + 0.5 * bs_point
                    lo = np.percentile(sim_returns, 10)
                    hi = np.percentile(sim_returns, 90)

            regime_est[ticker] = {
                "point": round(point_pct, 1),
                "lo": round(lo, 1),
                "hi": round(hi, 1),
                "r2": round(info["r2"], 3),
                "beta_stability": info.get("beta_stability", 1.0),
                "source": "data-driven",
            }
        estimates[regime] = regime_est
    return estimates


def compute_stressed_correlations(daily_returns: pd.DataFrame, factor_changes: pd.DataFrame) -> dict:
    """Compute correlation matrices for normal and stressed periods."""
    common_idx = daily_returns.index.intersection(factor_changes.index)
    if len(common_idx) < 60 or "VIXCLS" not in factor_changes.columns:
        return {"normal": None, "stressed": None}

    Y = daily_returns.loc[common_idx]
    vix = factor_changes.loc[common_idx, "VIXCLS"]
    threshold = np.percentile(np.abs(vix), 75)

    stress_mask = np.abs(vix) > threshold
    normal_mask = ~stress_mask

    corr_normal = Y[normal_mask].corr() if normal_mask.sum() > 20 else Y.corr()
    corr_stressed = Y[stress_mask].corr() if stress_mask.sum() > 20 else Y.corr()

    return {"normal": corr_normal, "stressed": corr_stressed}


def detect_sector_concentration(tickers: list, sector_map: dict = None) -> dict:
    """Flag sector concentration risk."""
    if sector_map is None:
        sector_map = SECTOR_MAP
    sectors = {}
    for t in tickers:
        sec = sector_map.get(t, "Unknown")
        sectors.setdefault(sec, []).append(t)

    warnings = []
    for sec, sec_tickers in sectors.items():
        pct = len(sec_tickers) / len(tickers) * 100
        if pct > 40 and len(sec_tickers) > 1:
            warnings.append(f"**{sec}** concentration: {len(sec_tickers)}/{len(tickers)} tickers ({pct:.0f}%) — "
                            f"{', '.join(sec_tickers)}")
    return {"sectors": sectors, "warnings": warnings}


def blend_estimates(data_estimates: dict, ai_estimates: dict, factor_betas: dict) -> dict:
    """Blend data-driven and AI estimates. R²-adaptive + stability-adjusted weighting."""
    from scipy.stats import t as t_dist
    blended = {}
    for regime in data_estimates:
        blended[regime] = {}
        for ticker in data_estimates[regime]:
            data_est = data_estimates[regime][ticker]
            r2 = factor_betas.get(ticker, {}).get("r2", 0)
            stability = factor_betas.get(ticker, {}).get("beta_stability", 1.0)

            w_data = 0.3 + 0.3 * r2 + 0.2 * stability
            w_data = min(0.8, max(0.3, w_data))
            w_ai = 1 - w_data

            ai_val = None
            if ai_estimates and regime in ai_estimates and ticker in ai_estimates[regime]:
                ai_val = ai_estimates[regime][ticker]
                try:
                    ai_val = float(ai_val)
                except (ValueError, TypeError):
                    ai_val = None

            if ai_val is not None:
                point = w_data * data_est["point"] + w_ai * ai_val
                ai_uncertainty = abs(ai_val) * 0.25 + 5
                sigma_data = (data_est["hi"] - data_est["lo"]) / 2.56
                sigma_blend = np.sqrt(w_data**2 * sigma_data**2 + w_ai**2 * ai_uncertainty**2)
                t_mult = t_dist.ppf(0.9, df=5)
                lo = point - t_mult * sigma_blend
                hi = point + t_mult * sigma_blend
                source = f"blended ({w_data:.0%} data / {w_ai:.0%} AI, R²={r2:.2f}, stability={stability:.2f})"
            else:
                point = data_est["point"]
                lo = data_est["lo"]
                hi = data_est["hi"]
                source = f"data-driven (R²={r2:.2f}, stability={stability:.2f})"

            blended[regime][ticker] = {
                "point": round(point, 1),
                "lo": round(lo, 1),
                "hi": round(hi, 1),
                "r2": r2,
                "beta_stability": stability,
                "source": source,
            }
    return blended

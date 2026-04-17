"""Scenario Analysis — portfolio impact under macro regimes.

Wraps the factor-beta portfolio model (src/portfolio_models.py) + regime definitions
from the Streamlit page into POST endpoints consumable by Next.js.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.deps import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()


# ═══════════════════════════════════════════════
# REGIME DEFINITIONS
# ═══════════════════════════════════════════════

MACRO_REGIMES: Dict[str, dict] = {
    "Stagflation": {
        "description": "Iran oil shock + tariffs keep inflation above 3% while growth stalls below 1%. "
                       "Fed paralyzed — can't cut (inflation) or hike (weak growth). 1970s-lite analog.",
        "rationale": "Hormuz closure is a textbook supply-side shock: oil >$100 drives inflation higher while "
                     "squeezing consumers and margins. Tariffs (10.5% effective rate) compound price pressure. "
                     "Fed already projecting Core PCE at 2.7%. Feb NFP -92K shows growth already weakening.",
        "probability": 30,
        "driver_moves": {
            "CPIAUCSL": 1.2, "PCEPILFE": 0.8, "UNRATE": 0.8, "PAYEMS": 30,
            "FEDFUNDS": 0.0, "T10Y2Y": -0.3, "DGS10": 0.5, "DGS2": 0.7,
            "RSAFS": -2.5, "UMCSENT": -15, "INDPRO": -1.5, "GDP": 0.3,
        },
        "asset_betas": {"SPY": -18, "QQQ": -22, "TLT": -10, "USO": 25, "GLD": 18, "_default": -15},
    },
    "Recession": {
        "description": "Oil shock + tariff drag + weak labor market tip the economy into contraction. "
                       "Feb NFP already -92K. Fed eventually forced to cut despite above-target inflation.",
        "rationale": "Goldman (20%), RSM (30%), Morgan Stanley (15%) recession estimates were set before or early "
                     "in the Hormuz crisis. Feb NFP at -92K is the first negative print since COVID.",
        "probability": 25,
        "driver_moves": {
            "CPIAUCSL": -0.5, "PCEPILFE": -0.3, "UNRATE": 1.8, "PAYEMS": -120,
            "FEDFUNDS": -1.5, "T10Y2Y": 0.8, "DGS10": -1.0, "DGS2": -1.8,
            "RSAFS": -6.0, "UMCSENT": -22, "INDPRO": -4.0, "GDP": -1.5,
        },
        "asset_betas": {"SPY": -25, "QQQ": -32, "TLT": 14, "USO": -20, "GLD": 10, "_default": -22},
    },
    "Soft Landing": {
        "description": "Iran conflict resolves in weeks, oil normalizes below $80, tariff clarity from SCOTUS. "
                       "Fed resumes gradual cutting. Requires multiple tailwinds to materialize.",
        "rationale": "Was the consensus view through 2025, but now requires rapid Iran de-escalation, "
                     "oil back below $80, SCOTUS tariff clarity, and labor market stabilization.",
        "probability": 15,
        "driver_moves": {
            "CPIAUCSL": -0.8, "PCEPILFE": -0.5, "UNRATE": 0.2, "PAYEMS": 140,
            "FEDFUNDS": -0.75, "T10Y2Y": 0.3, "DGS10": -0.3, "DGS2": -0.7,
            "RSAFS": 2.0, "UMCSENT": 8, "INDPRO": 1.0, "GDP": 2.0,
        },
        "asset_betas": {"SPY": 12, "QQQ": 18, "TLT": 6, "USO": -10, "GLD": -3, "_default": 10},
    },
    "Financial Crisis": {
        "description": "Prolonged Hormuz closure cascades into sovereign debt stress, shipping/insurance market "
                       "seizes, credit contagion spreads. Emergency Fed response.",
        "rationale": "Hormuz carries 20% of global oil — a months-long closure would cripple oil-importing "
                     "economies. If credit stress in energy-dependent sovereigns triggers contagion, systemic.",
        "probability": 10,
        "driver_moves": {
            "CPIAUCSL": -1.5, "PCEPILFE": -1.2, "UNRATE": 3.0, "PAYEMS": -350,
            "FEDFUNDS": -2.5, "T10Y2Y": 1.2, "DGS10": -1.5, "DGS2": -2.5,
            "RSAFS": -10.0, "UMCSENT": -35, "INDPRO": -7.0, "GDP": -3.5,
        },
        "asset_betas": {"SPY": -38, "QQQ": -42, "TLT": 18, "USO": -35, "GLD": 15, "_default": -32},
    },
    "Re-Acceleration": {
        "description": "War ends quickly, oil drops sharply, pent-up demand surges, SCOTUS strikes tariffs. "
                       "Growth rebounds but inflation re-ignites — Fed forced to hold or hike.",
        "rationale": "Requires rapid war resolution + tariff rollback creating a demand surge. "
                     "Even then, labor market has already weakened.",
        "probability": 10,
        "driver_moves": {
            "CPIAUCSL": 0.5, "PCEPILFE": 0.3, "UNRATE": -0.2, "PAYEMS": 220,
            "FEDFUNDS": 0.25, "T10Y2Y": -0.4, "DGS10": 0.4, "DGS2": 0.7,
            "RSAFS": 4.0, "UMCSENT": 8, "INDPRO": 2.5, "GDP": 3.0,
        },
        "asset_betas": {"SPY": 10, "QQQ": 8, "TLT": -8, "USO": 10, "GLD": -5, "_default": 7},
    },
    "Goldilocks": {
        "description": "Best case: rapid de-escalation, oil normalizes, tariffs rolled back, Fed cuts 2-3x, "
                       "labor market stabilizes. Requires nearly everything to break right.",
        "rationale": "Historical base rate ~15-18% (mid-1960s, 1995-98, 2017). Reduced to 10% because "
                     "requires simultaneous resolution of war, oil, tariffs, AND labor market.",
        "probability": 10,
        "driver_moves": {
            "CPIAUCSL": -0.5, "PCEPILFE": -0.4, "UNRATE": 0.0, "PAYEMS": 170,
            "FEDFUNDS": -0.50, "T10Y2Y": 0.2, "DGS10": -0.2, "DGS2": -0.4,
            "RSAFS": 3.0, "UMCSENT": 12, "INDPRO": 2.0, "GDP": 2.5,
        },
        "asset_betas": {"SPY": 15, "QQQ": 20, "TLT": 5, "USO": -5, "GLD": -2, "_default": 12},
    },
}

REGIME_FACTOR_MOVES: Dict[str, Dict[str, float]] = {
    "Stagflation":      {"VIXCLS": 12, "DGS10": 0.5,  "BAMLH0A0HYM2": 1.5, "T5YIE": 0.5, "DTWEXBGS": -2, "DCOILWTICO": 25, "VIX_HY": 18},
    "Recession":        {"VIXCLS": 18, "DGS10": -1.0, "BAMLH0A0HYM2": 3.0, "T5YIE": -0.3, "DTWEXBGS": -3, "DCOILWTICO": -20, "VIX_HY": 54},
    "Soft Landing":     {"VIXCLS": -3, "DGS10": -0.3, "BAMLH0A0HYM2": -0.5, "T5YIE": -0.1, "DTWEXBGS": -1, "DCOILWTICO": -10, "VIX_HY": 1.5},
    "Financial Crisis": {"VIXCLS": 35, "DGS10": -1.5, "BAMLH0A0HYM2": 6.0, "T5YIE": -0.5, "DTWEXBGS": 5,  "DCOILWTICO": -30, "VIX_HY": 210},
    "Re-Acceleration":  {"VIXCLS": -2, "DGS10": 0.4,  "BAMLH0A0HYM2": -0.3, "T5YIE": 0.3, "DTWEXBGS": 2,  "DCOILWTICO": 15, "VIX_HY": 0.6},
    "Goldilocks":       {"VIXCLS": -5, "DGS10": -0.2, "BAMLH0A0HYM2": -0.5, "T5YIE": 0.0, "DTWEXBGS": -1, "DCOILWTICO": -5, "VIX_HY": 2.5},
}

FACTOR_SERIES = ["VIXCLS", "DGS10", "BAMLH0A0HYM2", "T5YIE", "DTWEXBGS", "DCOILWTICO"]

FED_DRIVERS = {
    "CPIAUCSL":  {"name": "CPI (All Items)",       "unit": "index",  "yoy": True,  "category": "Inflation"},
    "PCEPILFE":  {"name": "Core PCE",              "unit": "index",  "yoy": True,  "category": "Inflation"},
    "UNRATE":    {"name": "Unemployment Rate",     "unit": "%",      "yoy": False, "category": "Employment"},
    "PAYEMS":    {"name": "Nonfarm Payrolls",      "unit": "K",      "yoy": False, "category": "Employment"},
    "FEDFUNDS":  {"name": "Fed Funds Rate",        "unit": "%",      "yoy": False, "category": "Fed"},
    "T10Y2Y":    {"name": "2s10s Yield Spread",    "unit": "%",      "yoy": False, "category": "Rates"},
    "DGS10":     {"name": "10-Year Treasury Yield", "unit": "%",     "yoy": False, "category": "Rates"},
    "DGS2":      {"name": "2-Year Treasury Yield",  "unit": "%",     "yoy": False, "category": "Rates"},
    "RSAFS":     {"name": "Retail Sales",          "unit": "$M",     "yoy": True,  "category": "Consumer"},
    "UMCSENT":   {"name": "Consumer Sentiment",    "unit": "index",  "yoy": False, "category": "Consumer"},
    "INDPRO":    {"name": "Industrial Production", "unit": "index",  "yoy": True,  "category": "Production"},
    "GDP":       {"name": "Real GDP",              "unit": "$B",     "yoy": True,  "category": "Growth"},
}


# ═══════════════════════════════════════════════
# DATA FETCHING
# ═══════════════════════════════════════════════

def _fetch_prices(tickers: List[str], days: int) -> pd.DataFrame:
    from src.data_engine import fetch_massive_data, format_massive_ticker

    def _one(tk: str):
        try:
            df = fetch_massive_data(format_massive_ticker(tk), days)
            if df is None or df.empty:
                return tk, None
            s = df["Close"].copy()
            s.name = tk
            return tk, s
        except Exception as e:
            logger.warning(f"price fetch failed for {tk}: {e}")
            return tk, None

    result: Dict[str, pd.Series] = {}
    with ThreadPoolExecutor(max_workers=10) as ex:
        for tk, s in ex.map(_one, tickers):
            if s is not None:
                result[tk] = s
    if not result:
        return pd.DataFrame()
    return pd.DataFrame(result).sort_index()


def _fetch_factor_changes(days: int) -> pd.DataFrame:
    from src.market_data import fetch_fred_series

    def _one(sid: str):
        try:
            df = fetch_fred_series(sid, periods=days)
            if df is None or df.empty:
                return sid, None
            return sid, df.set_index("date")["value"]
        except Exception as e:
            logger.warning(f"FRED fetch failed for {sid}: {e}")
            return sid, None

    frames: Dict[str, pd.Series] = {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        for sid, s in ex.map(_one, FACTOR_SERIES):
            if s is not None:
                frames[sid] = s

    if not frames:
        return pd.DataFrame()
    factors = pd.DataFrame(frames).sort_index().ffill()
    changes = factors.diff().dropna()
    if "VIXCLS" in changes.columns and "BAMLH0A0HYM2" in changes.columns:
        changes["VIX_HY"] = changes["VIXCLS"] * changes["BAMLH0A0HYM2"]
    return changes


# ═══════════════════════════════════════════════
# REQUEST MODELS
# ═══════════════════════════════════════════════

class PortfolioImpactRequest(BaseModel):
    tickers: List[str]
    portfolio_value: float = 100_000
    lookback: int = 756              # days of history
    horizon_days: int = 252          # 63, 126, or 252
    user_probs: Optional[Dict[str, float]] = None  # regime_name -> probability (0-100)
    n_sims: int = 10_000


class GbmRequest(BaseModel):
    ticker: str
    lookback: int = 756
    proj_days: int = 252
    num_paths: int = 500
    bull_ret: float = 25.0
    base_ret: float = 10.0
    bear_ret: float = -20.0


# ═══════════════════════════════════════════════
# PORTFOLIO IMPACT ENDPOINT
# ═══════════════════════════════════════════════

@router.post("/portfolio-impact")
async def portfolio_impact(req: PortfolioImpactRequest, user: str = Depends(get_current_user)):
    from src.portfolio_models import (
        compute_factor_betas,
        compute_stressed_correlations, detect_sector_concentration,
        blend_estimates, SECTOR_MAP,
    )

    tickers = [t.strip().upper() for t in req.tickers if t.strip()]
    if len(tickers) < 1:
        return {"error": "At least 1 ticker required."}
    if req.horizon_days < 1 or req.horizon_days > 756:
        return {"error": "horizon_days must be between 1 and 756."}

    prices = _fetch_prices(tickers, req.lookback)
    if prices.empty:
        return {"error": "No price data loaded."}

    prices = prices.dropna(axis=1, how="all")
    usable = [t for t in tickers if t in prices.columns]
    failed = [t for t in tickers if t not in prices.columns]
    if not usable:
        return {"error": f"All tickers failed: {failed}"}

    portfolio_df = prices[usable].dropna()
    daily_returns = portfolio_df.pct_change().dropna()
    tickers = daily_returns.columns.tolist()
    n = len(tickers)
    port_val = float(req.portfolio_value)
    alloc_per = port_val / n
    horizon = int(req.horizon_days)
    horizon_scale = horizon / 252.0

    # Factor model
    factor_changes = _fetch_factor_changes(req.lookback)
    factor_betas: Dict[str, dict] = {}
    corr = {"normal": None, "stressed": None}
    if not factor_changes.empty:
        factor_betas = compute_factor_betas(daily_returns, factor_changes)
        corr = compute_stressed_correlations(daily_returns, factor_changes)

    # Regime estimates via factor-beta × total-factor-move (t-distribution for CIs).
    # Do NOT use src.portfolio_models.estimate_regime_returns — it double-scales by
    # horizon_days on top of the pre-scaled input. Compute the point estimate directly.
    from scipy.stats import t as t_dist
    STRESS_REGIMES = {"Recession", "Financial Crisis", "Stagflation"}
    t_mult = float(t_dist.ppf(0.9, df=5))

    data_estimates: Dict[str, dict] = {}
    if factor_betas:
        for regime, fmoves in REGIME_FACTOR_MOVES.items():
            scaled = {f: v * horizon_scale for f, v in fmoves.items()}
            regime_est = {}
            use_stress = regime in STRESS_REGIMES
            for ticker, info in factor_betas.items():
                betas = info.get("betas", {})
                point_pct = sum(betas.get(f, 0) * scaled.get(f, 0) for f in scaled) * 100
                res_std = info.get("stressed_residual_std" if use_stress else "residual_std", 0)
                horizon_std = float(res_std * np.sqrt(horizon) * 100)
                lo = point_pct - t_mult * horizon_std
                hi = point_pct + t_mult * horizon_std
                regime_est[ticker] = {
                    "point": round(float(point_pct), 1),
                    "lo": round(float(lo), 1),
                    "hi": round(float(hi), 1),
                    "r2": round(float(info.get("r2", 0)), 3),
                    "beta_stability": float(info.get("beta_stability", 1.0)),
                    "source": "data-driven",
                }
            data_estimates[regime] = regime_est

    # Blend (no AI layer here — pure data-driven; fallback to hardcoded if no factor_betas)
    if data_estimates:
        blended = blend_estimates(data_estimates, {}, factor_betas)
    else:
        blended = {}
        for regime, rdata in MACRO_REGIMES.items():
            blended[regime] = {}
            betas = rdata["asset_betas"]
            for t in tickers:
                val = float(betas.get(t, betas["_default"])) * horizon_scale
                ci = max(5.0, abs(val) * 0.3)
                blended[regime][t] = {
                    "point": round(val, 1), "lo": round(val - ci, 1), "hi": round(val + ci, 1),
                    "r2": 0, "beta_stability": 1.0, "source": "hardcoded fallback",
                }

    # User probabilities (default to base if not supplied)
    user_probs = req.user_probs or {r: MACRO_REGIMES[r]["probability"] for r in MACRO_REGIMES}
    total = sum(user_probs.values()) or 100
    user_probs_norm = {k: v / total for k, v in user_probs.items()}

    # Per-regime P&L
    regime_results = []
    for regime in MACRO_REGIMES:
        pnl = pnl_lo = pnl_hi = 0.0
        ticker_moves = {}
        for t in tickers:
            est = blended.get(regime, {}).get(t, {"point": 0, "lo": -5, "hi": 5,
                                                  "r2": 0, "beta_stability": 1.0, "source": "—"})
            move = est.get("point", 0) / 100
            impact = alloc_per * move
            pnl += impact
            pnl_lo += alloc_per * (est.get("lo", 0) / 100)
            pnl_hi += alloc_per * (est.get("hi", 0) / 100)
            ticker_moves[t] = est
        regime_results.append({
            "regime": regime,
            "pnl": pnl, "pnl_lo": pnl_lo, "pnl_hi": pnl_hi,
            "pnl_pct": (pnl / port_val) * 100 if port_val > 0 else 0,
            "prob": user_probs_norm.get(regime, 0),
            "ticker_moves": ticker_moves,
        })

    ev_pnl = sum(r["pnl"] * r["prob"] for r in regime_results)
    ev_lo = sum(r["pnl_lo"] * r["prob"] for r in regime_results)
    ev_hi = sum(r["pnl_hi"] * r["prob"] for r in regime_results)

    # Monte Carlo P&L simulation (Student-t, df=5 for fat tails)
    from scipy.stats import t as t_dist_mc
    rng = np.random.default_rng(42)
    regime_probs_arr = np.array([r["prob"] for r in regime_results])
    regime_probs_arr = regime_probs_arr / regime_probs_arr.sum() if regime_probs_arr.sum() > 0 else np.full(len(regime_results), 1 / len(regime_results))
    regime_points = np.array([r["pnl"] for r in regime_results])
    regime_sigmas = np.array([
        max(1.0, (r["pnl_hi"] - r["pnl_lo"]) / (2 * 1.476)) for r in regime_results
    ])
    n_sims = int(min(max(req.n_sims, 1000), 50_000))
    draws = rng.choice(len(regime_results), size=n_sims, p=regime_probs_arr)
    t_samples = t_dist_mc.rvs(df=5, size=n_sims, random_state=rng)
    sim_pnls = regime_points[draws] + regime_sigmas[draws] * t_samples

    var_95 = float(np.percentile(sim_pnls, 5))
    tail_95 = sim_pnls[sim_pnls <= var_95]
    cvar_95 = float(tail_95.mean()) if len(tail_95) > 0 else var_95
    mc = {
        "mean": float(np.mean(sim_pnls)),
        "median": float(np.median(sim_pnls)),
        "var_95": var_95,
        "cvar_95": cvar_95,
        "p10": float(np.percentile(sim_pnls, 10)),
        "p90": float(np.percentile(sim_pnls, 90)),
        "prob_loss": float(np.mean(sim_pnls < 0) * 100),
        "prob_gain": float(np.mean(sim_pnls > 0) * 100),
        "percentiles": {str(p): float(np.percentile(sim_pnls, p))
                        for p in [1, 5, 10, 25, 50, 75, 90, 95, 99]},
        "histogram": _build_histogram(sim_pnls, bins=80),
        "regime_draw_counts": {r["regime"]: int((draws == i).sum())
                               for i, r in enumerate(regime_results)},
    }

    # Sector concentration
    concentration = detect_sector_concentration(tickers)

    # Correlation matrices
    corr_out = {}
    if corr["normal"] is not None:
        corr_out["normal_methods"] = corr["normal"].columns.tolist()
        corr_out["normal"] = [[float(v) for v in row] for row in corr["normal"].values]
    if corr["stressed"] is not None:
        corr_out["stressed_methods"] = corr["stressed"].columns.tolist()
        corr_out["stressed"] = [[float(v) for v in row] for row in corr["stressed"].values]

    # Factor beta diagnostics
    diag_rows = []
    for t in tickers:
        fb = factor_betas.get(t)
        if fb:
            diag_rows.append({
                "ticker": t,
                "r2": round(float(fb["r2"]), 3),
                "beta_stability": float(fb.get("beta_stability", 0)),
                "n_obs": int(fb.get("n_obs", 0)),
                "residual_std": float(fb.get("residual_std", 0)),
                "stressed_residual_std": float(fb.get("stressed_residual_std", fb.get("residual_std", 0))),
                "sector": SECTOR_MAP.get(t, "Unknown"),
                "betas": {f: float(v) for f, v in fb.get("betas", {}).items()},
                "alpha": float(fb.get("alpha", 0)),
            })
        else:
            diag_rows.append({
                "ticker": t, "r2": 0, "beta_stability": 0, "n_obs": 0,
                "residual_std": 0, "stressed_residual_std": 0,
                "sector": SECTOR_MAP.get(t, "Unknown"),
                "betas": {}, "alpha": 0,
            })

    regimes_out = []
    for name, rdata in MACRO_REGIMES.items():
        regimes_out.append({
            "name": name,
            "description": rdata["description"],
            "rationale": rdata["rationale"],
            "base_probability": rdata["probability"],
            "driver_moves": rdata["driver_moves"],
        })

    return {
        "tickers": tickers,
        "failed": failed,
        "n_assets": n,
        "portfolio_value": port_val,
        "horizon_days": horizon,
        "alloc_per_ticker": alloc_per,
        "regimes": regimes_out,
        "driver_keys": list(FED_DRIVERS.keys()),
        "fed_drivers": FED_DRIVERS,
        "factor_series": FACTOR_SERIES,
        "regime_results": [
            {
                "regime": r["regime"],
                "pnl": r["pnl"],
                "pnl_lo": r["pnl_lo"],
                "pnl_hi": r["pnl_hi"],
                "pnl_pct": r["pnl_pct"],
                "prob": r["prob"],
                "ticker_moves": r["ticker_moves"],
            } for r in regime_results
        ],
        "ev_pnl": ev_pnl,
        "ev_lo": ev_lo,
        "ev_hi": ev_hi,
        "monte_carlo": mc,
        "concentration": concentration,
        "correlation": corr_out,
        "factor_diagnostics": diag_rows,
        "avg_r2": float(np.mean([d["r2"] for d in diag_rows if d["r2"] > 0])) if diag_rows else 0,
        "avg_stability": float(np.mean([d["beta_stability"] for d in diag_rows if d["beta_stability"] > 0])) if diag_rows else 0,
    }


def _build_histogram(values: np.ndarray, bins: int = 80) -> dict:
    counts, edges = np.histogram(values, bins=bins)
    return {
        "counts": [int(c) for c in counts],
        "edges": [float(e) for e in edges],
    }


# ═══════════════════════════════════════════════
# GBM PROJECTION ENDPOINT
# ═══════════════════════════════════════════════

@router.post("/gbm-projection")
async def gbm_projection(req: GbmRequest, user: str = Depends(get_current_user)):
    """Simulate bull/base/bear GBM paths for a single ticker."""
    tk = req.ticker.strip().upper()
    prices = _fetch_prices([tk], req.lookback)
    if prices.empty or tk not in prices.columns:
        return {"error": f"No data for {tk}"}

    closes = prices[tk].dropna()
    if len(closes) < 20:
        return {"error": f"Insufficient history for {tk}"}

    S0 = float(closes.iloc[-1])
    log_rets = np.log(closes / closes.shift(1)).dropna()
    hist_vol = float(log_rets.std() * np.sqrt(252))

    rng = np.random.default_rng(42)
    scenarios = {"Bull": req.bull_ret, "Base": req.base_ret, "Bear": req.bear_ret}
    proj_days = int(req.proj_days)
    num_paths = int(min(max(req.num_paths, 10), 2000))

    out = {}
    for name, annual_ret in scenarios.items():
        daily_drift = (annual_ret / 100) / 252
        daily_vol = hist_vol / np.sqrt(252)
        drift = daily_drift - 0.5 * daily_vol ** 2
        Z = rng.normal(0, 1, (proj_days, num_paths))
        daily_mult = np.exp(drift + daily_vol * Z)
        paths = np.vstack([np.ones(num_paths), np.cumprod(daily_mult, axis=0)]) * S0
        mean_path = np.mean(paths, axis=1)
        p10 = np.percentile(paths, 10, axis=1)
        p90 = np.percentile(paths, 90, axis=1)
        terminal = paths[-1, :]
        out[name] = {
            "mean_path": [float(v) for v in mean_path],
            "p10_path": [float(v) for v in p10],
            "p90_path": [float(v) for v in p90],
            "median_terminal": float(np.median(terminal)),
            "mean_terminal": float(np.mean(terminal)),
            "p10_terminal": float(np.percentile(terminal, 10)),
            "p90_terminal": float(np.percentile(terminal, 90)),
            "prob_profit": float(np.mean(terminal > S0) * 100),
            "annual_ret": annual_ret,
        }

    # 60-day history tail for the chart
    tail = closes.tail(60)
    return {
        "ticker": tk,
        "spot": S0,
        "hist_vol": hist_vol,
        "history": {
            "dates": [pd.Timestamp(d).strftime("%Y-%m-%d") for d in tail.index],
            "closes": [float(v) for v in tail.values],
        },
        "scenarios": out,
    }


# ═══════════════════════════════════════════════
# REGIME TRACK RECORD ENDPOINT
# ═══════════════════════════════════════════════

@router.get("/regime-track-record")
async def regime_track_record(user: str = Depends(get_current_user)):
    """Evaluate historical Grok regime predictions against SPY 30-day performance."""
    import os
    from src.analysis_history import load_history

    history_file = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "src", "grok_regime_history.json",
    )

    history = load_history(history_file) if os.path.exists(history_file) else []
    if len(history) < 2:
        return {"history_count": len(history), "evaluations": [], "accuracy": None}

    # Fetch SPY history
    try:
        import yfinance as yf
        spy = yf.Ticker("SPY").history(period="2y")
        if spy.empty:
            return {"history_count": len(history), "evaluations": [], "accuracy": None,
                    "error": "Could not fetch SPY history"}
    except Exception as e:
        return {"history_count": len(history), "evaluations": [], "accuracy": None,
                "error": str(e)}

    bullish = {"soft landing", "goldilocks", "expansion", "recovery", "reflation", "bull", "risk-on"}
    bearish = {"recession", "stagflation", "crisis", "hard landing", "contraction", "bear", "risk-off"}

    evals = []
    for entry in history:
        try:
            ts = pd.Timestamp(entry["timestamp"])
        except Exception:
            continue
        if ts.tzinfo is not None:
            ts = ts.tz_localize(None)
        age_days = (pd.Timestamp.now() - ts).days
        if age_days < 30:
            continue

        regimes = entry.get("regimes", [])
        if not regimes:
            continue
        top = max(regimes, key=lambda r: r.get("probability", 0))
        rname = top.get("name", "").lower()
        expected = ("Bullish" if any(b in rname for b in bullish)
                    else "Bearish" if any(b in rname for b in bearish) else "Neutral")

        spy_index = spy.index.tz_localize(None) if getattr(spy.index, "tz", None) is not None else spy.index
        after = spy.loc[spy_index >= ts]
        after_30 = after.head(22)
        if len(after_30) < 10:
            continue
        spy_start = float(after_30["Close"].iloc[0])
        spy_end = float(after_30["Close"].iloc[-1])
        spy_ret = (spy_end / spy_start - 1) * 100
        actual = "Bullish" if spy_ret > 0 else "Bearish"
        correct = None
        if expected != "Neutral":
            correct = (expected == actual)
        evals.append({
            "date": ts.strftime("%Y-%m-%d %H:%M"),
            "top_regime": top.get("name", "?"),
            "probability": top.get("probability", 0),
            "expected": expected,
            "spy_30d": spy_ret,
            "actual": actual,
            "correct": correct,
        })

    directional = [e for e in evals if e["correct"] is not None]
    correct_count = sum(1 for e in directional if e["correct"])
    accuracy = correct_count / len(directional) if directional else None

    return {
        "history_count": len(history),
        "evaluations_count": len(evals),
        "directional_count": len(directional),
        "correct_count": correct_count,
        "accuracy": accuracy,
        "evaluations": evals,
    }


# ═══════════════════════════════════════════════
# GROK LATEST CACHE
# ═══════════════════════════════════════════════

@router.get("/grok-latest")
async def grok_latest(user: str = Depends(get_current_user)):
    """Return the most recent cached Grok regime analysis (no API call)."""
    import os
    from src.analysis_history import load_history

    history_file = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "src", "grok_regime_history.json",
    )
    if not os.path.exists(history_file):
        return {"available": False}
    history = load_history(history_file)
    if not history:
        return {"available": False}

    latest = history[-1]
    return {
        "available": True,
        "timestamp": latest.get("timestamp"),
        "regimes": latest.get("regimes", []),
        "sentiment_summary": latest.get("sentiment_summary", ""),
        "change_summary": latest.get("change_summary", ""),
        "asset_estimates": latest.get("asset_estimates", {}),
    }

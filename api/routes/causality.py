"""Causality endpoints — macro-only causal research workbench.

Tab 1 of the page is CCF (Cross-Correlation Function) lead/lag. Tabs 2-5
(Granger, Transfer Entropy, VAR+IRF, Causal Discovery, Counterfactual) ship
incrementally and reuse `src.causality.aligned_panel` + `stationarize_panel`.

All compute is wrapped in the shared two-tier (memory + Supabase) cache so
repeat hits land warm and the daily pre-warm worker can populate Supabase
overnight.
"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.deps import get_current_user
from src._cache_util import result_cached
from src.causality import (
    ccf_pair, ccf_scan,
    granger_pair, granger_scan,
    transfer_entropy_pair, transfer_entropy_scan,
    var_basket,
)
from src.causality_universe import (
    SYMBOLS_BY_KEY,
    list_categories,
    list_universe,
)

logger = logging.getLogger(__name__)
router = APIRouter()


LookbackKey = Literal["1Y", "3Y", "5Y", "10Y"]


# ─────────────────────────────────────────────────────────────────
# Universe metadata
# ─────────────────────────────────────────────────────────────────

@router.get("/universe")
async def get_universe(user: str = Depends(get_current_user)):
    """Flat universe + grouped-by-category map. UI uses this to build the
    symbol picker on every causality tab."""
    return {
        "count": len(SYMBOLS_BY_KEY),
        "series": list_universe(),
        "categories": list_categories(),
    }


# ─────────────────────────────────────────────────────────────────
# CCF — Tab 1
# ─────────────────────────────────────────────────────────────────

@result_cached("causality_ccf_pair")
def _cached_ccf_pair(x: str, y: str, lookback: str, max_lag: int) -> dict:
    return ccf_pair(x, y, lookback=lookback, max_lag=max_lag)


@result_cached("causality_ccf_scan")
def _cached_ccf_scan(target: str, lookback: str, max_lag: int) -> dict:
    return ccf_scan(target, universe=None, lookback=lookback, max_lag=max_lag)


def _validate_symbols(*symbols: str) -> None:
    bad = [s for s in symbols if s not in SYMBOLS_BY_KEY]
    if bad:
        raise HTTPException(400, f"Unknown symbol(s): {', '.join(bad)}")


@router.get("/ccf")
async def get_ccf_pair(
    x: str = Query(..., description="Driver candidate (e.g., DXY)"),
    y: str = Query(..., description="Target candidate (e.g., EEM)"),
    lookback: LookbackKey = Query("5Y"),
    max_lag: int = Query(30, ge=5, le=120),
    user: str = Depends(get_current_user),
):
    """Pair-mode CCF. Returns full lag profile, 95% conf band, peak lag,
    contemporaneous ρ, and stationarity transform applied to each side.

    Sign convention: lag > 0 ⇒ X leads Y. Negative lag ⇒ Y leads X.
    """
    _validate_symbols(x, y)
    if x == y:
        raise HTTPException(400, "x and y must differ")
    try:
        return _cached_ccf_pair(x, y, lookback, max_lag)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        logger.exception(f"CCF pair compute failed for {x} vs {y}")
        raise HTTPException(500, f"CCF compute failed: {e}")


@router.get("/ccf-scan")
async def get_ccf_scan(
    target: str = Query(..., description="Target series (e.g., SPX)"),
    lookback: LookbackKey = Query("5Y"),
    max_lag: int = Query(30, ge=5, le=120),
    user: str = Depends(get_current_user),
):
    """Scan-mode CCF: for the chosen target, fan out across the universe and
    return each candidate driver's strongest lead-relationship metrics
    (x_leads_lag, x_leads_rho), strongest-overall, and contemporaneous ρ.

    Default sort (by |x_leads_rho|) ranks the universe by 'who leads target
    most strongly at any positive lag' — the standard 'who's driving X'
    trader question.
    """
    _validate_symbols(target)
    try:
        return _cached_ccf_scan(target, lookback, max_lag)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        logger.exception(f"CCF scan compute failed for {target}")
        raise HTTPException(500, f"CCF scan failed: {e}")


# ─────────────────────────────────────────────────────────────────
# Granger — Tab 2
# ─────────────────────────────────────────────────────────────────

@result_cached("causality_granger_pair")
def _cached_granger_pair(x: str, y: str, lookback: str, max_lag: int) -> dict:
    return granger_pair(x, y, lookback=lookback, max_lag=max_lag)


@result_cached("causality_granger_scan")
def _cached_granger_scan(target: str, lookback: str, max_lag: int) -> dict:
    return granger_scan(target, universe=None, lookback=lookback, max_lag=max_lag)


@router.get("/granger")
async def get_granger_pair(
    x: str = Query(..., description="Driver candidate"),
    y: str = Query(..., description="Target candidate"),
    lookback: LookbackKey = Query("5Y"),
    max_lag: int = Query(10, ge=1, le=30),
    user: str = Depends(get_current_user),
):
    """Bidirectional Granger causality test. Returns F-stat + p-value at every
    lag from 1..max_lag for BOTH X→Y and Y→X directions, plus the best (min-p)
    lag and a verdict label ('strong'|'moderate'|'weak'|'none' at α=0.05).

    Granger requires stationarity; the panel is auto-stationarized using each
    series' default transform (with ADF escalation if needed).
    """
    _validate_symbols(x, y)
    if x == y:
        raise HTTPException(400, "x and y must differ")
    try:
        return _cached_granger_pair(x, y, lookback, max_lag)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        logger.exception(f"Granger pair compute failed for {x} vs {y}")
        raise HTTPException(500, f"Granger compute failed: {e}")


@router.get("/granger-scan")
async def get_granger_scan(
    target: str = Query(...),
    lookback: LookbackKey = Query("5Y"),
    max_lag: int = Query(10, ge=1, le=30),
    user: str = Depends(get_current_user),
):
    """Scan-mode Granger: for the target, fan out across the universe and
    return each driver's best (min-p) lag in both directions, plus Bonferroni-
    adjusted p-values so the user can see what survives the family-wise
    correction across all tests run."""
    _validate_symbols(target)
    try:
        return _cached_granger_scan(target, lookback, max_lag)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        logger.exception(f"Granger scan compute failed for {target}")
        raise HTTPException(500, f"Granger scan failed: {e}")


# ─────────────────────────────────────────────────────────────────
# Transfer Entropy — Tab 3
# ─────────────────────────────────────────────────────────────────

@result_cached("causality_te_pair")
def _cached_te_pair(x: str, y: str, lookback: str, bins: int, n_perm: int) -> dict:
    return transfer_entropy_pair(x, y, lookback=lookback, bins=bins, n_perm=n_perm)


@result_cached("causality_te_scan")
def _cached_te_scan(target: str, lookback: str, bins: int, n_perm: int) -> dict:
    return transfer_entropy_scan(target, universe=None, lookback=lookback, bins=bins, n_perm=n_perm)


@router.get("/transfer-entropy")
async def get_te_pair(
    x: str = Query(...),
    y: str = Query(...),
    lookback: LookbackKey = Query("5Y"),
    bins: int = Query(3, ge=2, le=5),
    n_perm: int = Query(200, ge=50, le=500),
    user: str = Depends(get_current_user),
):
    """Bidirectional Transfer Entropy with permutation p-values. Returns
    Net TE = TE(X→Y) - TE(Y→X) as the trader-headline asymmetry score.

    Symbolic TE with rank-based binning (k=3 default) and history length
    l=1. Captures NONLINEAR info flow that Granger's linear-VAR test misses.
    """
    _validate_symbols(x, y)
    if x == y:
        raise HTTPException(400, "x and y must differ")
    try:
        return _cached_te_pair(x, y, lookback, bins, n_perm)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        logger.exception(f"TE pair compute failed for {x} vs {y}")
        raise HTTPException(500, f"TE compute failed: {e}")


@router.get("/transfer-entropy-scan")
async def get_te_scan(
    target: str = Query(...),
    lookback: LookbackKey = Query("5Y"),
    bins: int = Query(3, ge=2, le=5),
    n_perm: int = Query(100, ge=50, le=300),
    user: str = Depends(get_current_user),
):
    """Scan-mode TE: rank universe drivers by TE(driver → target). Lower
    n_perm than pair (compute budget). Surfaces null 95th-percentile per
    row so the UI can show the bar-above-noise threshold visually."""
    _validate_symbols(target)
    try:
        return _cached_te_scan(target, lookback, bins, n_perm)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        logger.exception(f"TE scan compute failed for {target}")
        raise HTTPException(500, f"TE scan failed: {e}")


# ─────────────────────────────────────────────────────────────────
# VAR + IRF — Tab 4
# ─────────────────────────────────────────────────────────────────

class VarBasketRequest(BaseModel):
    symbols: list[str] = Field(..., min_length=2, max_length=8)
    lookback: LookbackKey = "5Y"
    max_lag: int = Field(10, ge=2, le=20)
    irf_horizon: int = Field(20, ge=5, le=60)
    ic: Literal["aic", "bic"] = "aic"
    chol_order: list[str] | None = None  # explicit override of Cholesky order


@result_cached("causality_var_basket")
def _cached_var_basket(symbols_csv: str, lookback: str, max_lag: int, irf_horizon: int, ic: str, explicit_order: bool) -> dict:
    syms = symbols_csv.split(",")
    return var_basket(
        symbols=syms,
        lookback=lookback,
        max_lag=max_lag,
        irf_horizon=irf_horizon,
        ic=ic,
        chol_order=syms if explicit_order else None,
    )


@router.post("/var")
async def post_var_basket(
    body: VarBasketRequest = Body(...),
    user: str = Depends(get_current_user),
):
    """VAR fit + orthogonalized IRF + FEVD on a 2-8 series macro basket.
    Returns lag-selection table (AIC/BIC across 1..max_lag), full IRF
    coefficient grid, and sparse FEVD at horizons {1, 5, 10, irf_horizon}.

    If `chol_order` is provided, it determines the Cholesky ordering for
    orthogonalization (most-exogenous first). Otherwise the default ordering
    is by category exogeneity rank: Macro → Vol → Rates → Credit → FX →
    Commodity → Equity → Factor → Crypto.
    """
    _validate_symbols(*body.symbols)
    if len(set(body.symbols)) != len(body.symbols):
        raise HTTPException(400, "symbols must be unique")
    if body.chol_order is not None:
        if set(body.chol_order) != set(body.symbols):
            raise HTTPException(400, "chol_order must be a permutation of symbols")
        explicit = True
        cache_symbols = body.chol_order
    else:
        explicit = False
        cache_symbols = sorted(body.symbols)  # symbol order in cache key irrelevant when default-order is used
    try:
        return _cached_var_basket(
            ",".join(cache_symbols),
            body.lookback,
            body.max_lag,
            body.irf_horizon,
            body.ic,
            explicit,
        )
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        logger.exception(f"VAR basket failed for {body.symbols}")
        raise HTTPException(500, f"VAR compute failed: {e}")


# ─────────────────────────────────────────────────────────────────
# Pre-warm hook used by the lifespan startup task in api/main.py
# ─────────────────────────────────────────────────────────────────

_PREWARM_PAIRS: list[tuple[str, str]] = [
    ("DXY", "EEM"),
    ("UST10Y", "XLF"),
    ("UST10Y", "XLU"),
    ("WTI", "XLE"),
    ("VIX", "SPX"),
    ("HY_OAS", "SPX"),
    ("MOVE", "UST10Y"),
    ("REAL10Y", "GOLD"),
]
_PREWARM_TARGETS: list[str] = ["SPX", "VIX", "UST10Y", "DXY", "GOLD", "EEM"]


def prewarm_causality() -> None:
    """Hit the most-trafficked pair + scan computations once so the first
    request post-deploy lands warm. Safe to call from main.py lifespan."""
    for x, y in _PREWARM_PAIRS:
        try:
            _cached_ccf_pair(x, y, "5Y", 30)
        except Exception as e:
            logger.warning(f"causality prewarm CCF pair {x}->{y} failed: {e}")
    for tgt in _PREWARM_TARGETS:
        try:
            _cached_ccf_scan(tgt, "5Y", 30)
        except Exception as e:
            logger.warning(f"causality prewarm CCF scan {tgt} failed: {e}")
    for x, y in _PREWARM_PAIRS:
        try:
            _cached_granger_pair(x, y, "5Y", 10)
        except Exception as e:
            logger.warning(f"causality prewarm Granger pair {x}->{y} failed: {e}")
    for tgt in _PREWARM_TARGETS[:3]:
        # Granger scan is the slowest call (~60s); only pre-warm 3 anchors
        try:
            _cached_granger_scan(tgt, "5Y", 10)
        except Exception as e:
            logger.warning(f"causality prewarm Granger scan {tgt} failed: {e}")
    for x, y in _PREWARM_PAIRS[:4]:
        # TE pair is fast-ish (~5s) but quadratic in pair count; warm a few
        try:
            _cached_te_pair(x, y, "5Y", 3, 200)
        except Exception as e:
            logger.warning(f"causality prewarm TE pair {x}->{y} failed: {e}")
    # VAR default macro basket — the headline view most traders will land on
    try:
        default_basket = sorted(["SPX", "VIX", "UST10Y", "DXY", "WTI", "HY_OAS"])
        _cached_var_basket(",".join(default_basket), "5Y", 10, 20, "aic", False)
    except Exception as e:
        logger.warning(f"causality prewarm VAR default basket failed: {e}")
    logger.info("Causality caches pre-warmed")

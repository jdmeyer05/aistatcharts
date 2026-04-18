"""CFTC Commitments of Traders API — wide-universe positioning data.

Every endpoint serves normalized data from src/cftc.py. Heavy endpoints
(`/dashboard`, `/heatmap`) fan out to ~45 contracts × multiple reports via
the Socrata API; first-call warm-up is 5-15 seconds cold, sub-second warm
(in-memory cache in src/cftc.py has a 24h TTL).
"""

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import get_current_user
from src.cftc import (
    CONTRACTS,
    CONTRACTS_BY_CODE,
    contract_history,
    heatmap_snapshot,
    divergence_scan,
    regime_composites,
    cta_unwind_risk,
    flow_radar,
    positioning_dashboard,
)

logger = logging.getLogger(__name__)
router = APIRouter()


AssetClass = Literal["equity", "rates", "fx", "energy", "metals", "grains", "softs", "meats"]


@router.get("/contracts")
async def list_contracts(
    asset_class: AssetClass | None = Query(None),
    user: str = Depends(get_current_user),
):
    """Flat list of tracked contracts with metadata. Filter by asset class."""
    rows = [
        {
            "code": c.code,
            "symbol": c.symbol,
            "name": c.name,
            "asset_class": c.asset_class,
            "spec_report": c.spec_report,
            "track_legacy": c.track_legacy,
            "priority": c.priority,
        }
        for c in CONTRACTS
        if asset_class is None or c.asset_class == asset_class
    ]
    rows.sort(key=lambda r: -r["priority"])
    return {"count": len(rows), "contracts": rows}


@router.get("/history/{code}")
async def get_history(
    code: str,
    lookback_weeks: int = Query(260, ge=26, le=2080),
    user: str = Depends(get_current_user),
):
    """Full weekly history for a contract with derived metrics: percentiles,
    COT Index, z-score, weekly/4w changes, divergence Z."""
    spec = CONTRACTS_BY_CODE.get(code)
    if not spec:
        raise HTTPException(404, f"Unknown CFTC contract code: {code}")

    df = contract_history(code, lookback_weeks=lookback_weeks)
    if df.empty:
        return {
            "code": code,
            "symbol": spec.symbol,
            "name": spec.name,
            "asset_class": spec.asset_class,
            "count": 0,
            "data": [],
        }

    # Convert to serializable. NaN → None so JSON doesn't break.
    df_out = df.copy()
    df_out["date"] = df_out["date"].dt.strftime("%Y-%m-%d")
    records = df_out.where(df_out.notna(), None).to_dict(orient="records")

    return {
        "code": code,
        "symbol": spec.symbol,
        "name": spec.name,
        "asset_class": spec.asset_class,
        "spec_report": spec.spec_report,
        "count": len(records),
        "data": records,
    }


@router.get("/heatmap")
async def get_heatmap(user: str = Depends(get_current_user)):
    """Cross-asset positioning heatmap — one row per contract with percentiles,
    COT Index, z-score, 1w/4w deltas, divergence Z. Priority-sorted."""
    rows = heatmap_snapshot()
    return {"count": len(rows), "tiles": rows}


@router.get("/divergence")
async def get_divergence(
    min_abs_z: float = Query(1.0, ge=0.0, le=5.0),
    user: str = Depends(get_current_user),
):
    """Spec-vs-commercial divergence ranked by |Z|. Positive Z = specs long,
    commercials short (classic overbought). Negative = inverse."""
    rows = divergence_scan(min_abs_z=min_abs_z)
    return {"count": len(rows), "threshold": min_abs_z, "rows": rows}


@router.get("/regime")
async def get_regime(user: str = Depends(get_current_user)):
    """Four synthesized positioning composites: risk-on/off, reflation,
    safe-haven, dollar. Computed from z-scored managed-money net positions
    across multi-contract baskets."""
    return regime_composites()


@router.get("/cta-unwind")
async def get_cta_unwind(user: str = Depends(get_current_user)):
    """CTA forced-unwind risk scores. Higher = crowded positioning × elevated
    realized vol. When these align, trend-followers get stopped out on the
    next vol spike."""
    rows = cta_unwind_risk()
    return {"count": len(rows), "rows": rows}


@router.get("/flow-radar")
async def get_flow_radar(
    min_pct_oi: float = Query(3.0, ge=0.0, le=50.0),
    user: str = Depends(get_current_user),
):
    """Biggest weekly position changes by % of open interest. Shows what
    managed money / leveraged funds did THIS week."""
    rows = flow_radar(min_abs_chg_pct=min_pct_oi)
    return {"count": len(rows), "threshold_pct_oi": min_pct_oi, "rows": rows}


@router.get("/dashboard")
async def get_dashboard(user: str = Depends(get_current_user)):
    """Bundled landing-tab payload — regime composites + heatmap + top
    divergences + top flows + top unwind-risk. Single request for the
    flagship view."""
    return positioning_dashboard()

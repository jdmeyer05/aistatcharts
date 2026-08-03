"""AI / data center infrastructure endpoints.

Three reads, all backed by the shared two-tier cache. The EIA pulls behind
`/grid-load` and `/capacity-additions` take roughly 8s and 18s cold, so the
cache is doing real work here — both series move monthly at best, and the
12h TTL is still conservative against their true cadence.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import get_current_user
from src._cache_util import result_cached
from src.ai_infra import (
    BALANCING_AUTHORITIES,
    capacity_additions,
    capital_reference,
    grid_load_growth,
)
from src.ai_infra_capital import capital_financing

logger = logging.getLogger(__name__)
router = APIRouter()


# Cache keys carry a version suffix. `result_cached` hashes only (key, args),
# so a change to the payload SHAPE does not invalidate a warm entry — the
# page will happily serve a 12h-old response missing the new fields. Bump
# the suffix whenever these return shapes change.
@result_cached("ai_infra_grid_load_v2")
def _cached_grid_load(months_back: int) -> dict:
    return grid_load_growth(months_back=months_back)


@result_cached("ai_infra_capacity_additions_v2")
def _cached_capacity_additions(years_back: int) -> dict:
    return capacity_additions(years_back=years_back)


@result_cached("ai_infra_capital_financing_v1")
def _cached_capital_financing() -> dict:
    return capital_financing()


@router.get("/balancing-authorities")
async def get_balancing_authorities(user: str = Depends(get_current_user)):
    """The BA universe plus the editorial data center flag, so the UI can
    explain the tag rather than just render it."""
    return {
        "count": len(BALANCING_AUTHORITIES),
        "balancing_authorities": [
            {"ba": k, **v, "dc_flagged": v["dc_note"] is not None}
            for k, v in BALANCING_AUTHORITIES.items()
        ],
        "flag_basis": (
            "Editorial. EIA-930 does not separately meter data center load; the flag "
            "marks balancing authorities where data center activity is publicly "
            "concentrated."
        ),
    }


@router.get("/grid-load")
async def get_grid_load(
    months_back: int = Query(25, ge=25, le=49),
    user: str = Depends(get_current_user),
):
    """Realised demand growth by balancing authority — trailing 12m vs prior 12m."""
    try:
        return await asyncio.to_thread(_cached_grid_load, months_back)
    except Exception as e:
        logger.error(f"grid-load failed: {e}")
        raise HTTPException(502, f"EIA demand fetch failed: {e}")


@router.get("/capacity-additions")
async def get_capacity_additions(
    years_back: int = Query(4, ge=1, le=10),
    user: str = Depends(get_current_user),
):
    """Realised generation additions and planned retirements by balancing authority."""
    try:
        return await asyncio.to_thread(_cached_capacity_additions, years_back)
    except Exception as e:
        logger.error(f"capacity-additions failed: {e}")
        raise HTTPException(502, f"EIA inventory fetch failed: {e}")


@router.get("/capital-reference")
async def get_capital_reference(user: str = Depends(get_current_user)):
    """Curated capex guidance against observable revenue, at stated scopes.

    Not computed from a feed — these are disclosed figures carrying their own
    source and as-of date. Flagged `curated: true` so the UI labels them.
    """
    return capital_reference()


@router.get("/capital-financing")
async def get_capital_financing(user: str = Depends(get_current_user)):
    """Filed capital spending and its financing, from SEC EDGAR.

    The counterpart to `/capital-reference`: that endpoint carries what these
    companies SAY they will spend, this one carries what they have actually
    paid and how it was funded. Roughly 30 EDGAR calls cold, so it leans on the
    cache — 10-K and 10-Q figures change on an earnings cadence, which makes a
    12h TTL conservative by a wide margin.
    """
    return _cached_capital_financing()

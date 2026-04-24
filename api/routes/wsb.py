"""WallStreetBets endpoints — ticker-mention aggregate + bull/bear sentiment.

Uses `src.wsb_scraper` which hits Reddit's public `.json` endpoints.
Results are cached 15 minutes in Supabase so repeated page loads don't
re-scrape. A scheduled worker task (`wsb_scrape`) can pre-warm the cache
outside of user-facing load.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query

from api.deps import get_current_user
from api.routes.energy import _get_bundle_cache, _set_bundle_cache

logger = logging.getLogger(__name__)
router = APIRouter()

_CACHE_KEY = "wsb_mentions_scan"
_CACHE_TTL_MIN = 15


@router.get("/mentions")
async def wsb_mentions(
    force_refresh: bool = Query(False, description="Bypass the 15-min cache"),
    user: str = Depends(get_current_user),
):
    """Ranked ticker mentions from r/wallstreetbets + r/options + r/stocks.

    Backed by a 15-minute Supabase cache. First cold call takes ~15-25s
    (reddit + comment fetches run serially with 1.2s gaps for politeness);
    subsequent warm calls are instant.
    """
    if not force_refresh:
        try:
            cached = _get_bundle_cache(_CACHE_KEY, ttl_minutes=_CACHE_TTL_MIN)
            if cached:
                return {**cached, "cache_hit": True}
        except Exception as e:
            logger.debug(f"wsb cache read failed: {e}")

    try:
        from src.wsb_scraper import scan_wsb
        result = scan_wsb(include_comments=True, comments_top_n=8, min_mentions=2)
    except Exception as e:
        logger.warning(f"wsb scan failed: {e}")
        return {
            "error": str(e),
            "as_of_utc": datetime.now(timezone.utc).isoformat(),
            "tickers": [],
            "post_count": 0,
            "subreddits_scanned": [],
            "cache_hit": False,
        }

    result["cache_hit"] = False

    try:
        _set_bundle_cache(_CACHE_KEY, result, ttl_minutes=_CACHE_TTL_MIN)
    except Exception:
        pass

    return result

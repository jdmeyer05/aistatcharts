"""AI Response Cache — eliminates redundant AI calls across users and sessions.

Stores AI model responses keyed by a hash of the input context. If the same
ticker + same data state = same analysis, return cached response instantly
instead of calling the API again.

Usage:
    from src.ai_cache import get_cached_ai, cache_ai_response, build_cache_key

    key = build_cache_key("stock_analysis", "SPY", context_data)
    cached = get_cached_ai(key)
    if cached:
        return cached  # instant, no API call

    # ... call AI model ...
    cache_ai_response(key, response_text, model="gemini-2.5-pro",
                       source_page="stock_analysis", ticker="SPY", ttl_hours=2)
"""

import hashlib
import json
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


def _db():
    try:
        from src.db import get_client
        return get_client()
    except Exception:
        return None


def build_cache_key(source: str, ticker: str, context: str, model: str = "") -> str:
    """Build a deterministic cache key from the analysis inputs.

    The key is an MD5 hash of source + ticker + a truncated/normalized context.
    Context is truncated to first 2000 chars to avoid hashing massive prompts
    while still capturing meaningful data changes.
    """
    # Normalize: strip whitespace variations, truncate
    normalized = f"{source}:{ticker}:{model}:{context[:2000]}".lower().strip()
    return hashlib.md5(normalized.encode()).hexdigest()


def build_cache_key_from_metrics(source: str, ticker: str,
                                  spot: float = 0, iv: float = 0,
                                  skew: float = 0, vrp: float = 0) -> str:
    """Build a cache key from key metrics instead of full context text.

    Rounds metrics to reduce sensitivity — small price changes don't invalidate cache.
    Spot rounded to 0.5%, IV to 0.5%, skew to 0.02, VRP to 0.5%.
    """
    rounded = (
        f"{source}:{ticker}:"
        f"spot={round(spot * 2) / 2:.1f}:"  # round to nearest 0.5
        f"iv={round(iv * 200) / 200:.3f}:"  # round to nearest 0.005
        f"skew={round(skew * 50) / 50:.2f}:"  # round to nearest 0.02
        f"vrp={round(vrp * 200) / 200:.3f}"  # round to nearest 0.005
    )
    return hashlib.md5(rounded.encode()).hexdigest()


def get_cached_ai(cache_key: str) -> str | None:
    """Get a cached AI response if it exists and hasn't expired.

    Returns the response text or None.
    """
    db = _db()
    if not db:
        return None
    try:
        result = db.table("ai_response_cache").select("response")\
            .eq("input_hash", cache_key)\
            .gt("expires_at", datetime.now().isoformat())\
            .limit(1).execute()
        if result.data:
            return result.data[0]["response"]
    except Exception as e:
        logger.debug(f"AI cache read failed: {e}")
    return None


def cache_ai_response(cache_key: str, response: str, model: str = "",
                       source_page: str = "", ticker: str = "",
                       ttl_hours: float = 2, tokens_used: int = 0,
                       cost_estimate: float = 0, prompt_summary: str = "") -> None:
    """Store an AI response in the cache."""
    db = _db()
    if not db or not response:
        return
    try:
        db.table("ai_response_cache").upsert({
            "input_hash": cache_key,
            "model": model,
            "source_page": source_page,
            "ticker": ticker,
            "prompt_summary": prompt_summary[:500],
            "response": response,
            "tokens_used": tokens_used,
            "cost_estimate": cost_estimate,
            "created_at": datetime.now().isoformat(),
            "expires_at": (datetime.now() + timedelta(hours=ttl_hours)).isoformat(),
        }, on_conflict="input_hash").execute()
    except Exception as e:
        logger.debug(f"AI cache write failed: {e}")


def get_cache_stats() -> dict:
    """Return AI cache statistics."""
    db = _db()
    if not db:
        return {"status": "unavailable"}
    try:
        total = db.table("ai_response_cache").select("input_hash", count="exact").execute()
        active = db.table("ai_response_cache").select("input_hash", count="exact")\
            .gt("expires_at", datetime.now().isoformat()).execute()

        # Estimate savings
        cost_saved = 0
        try:
            saved = db.table("ai_response_cache").select("cost_estimate")\
                .gt("expires_at", datetime.now().isoformat()).execute()
            cost_saved = sum(r.get("cost_estimate", 0) for r in (saved.data or []))
        except Exception:
            pass

        return {
            "total_entries": total.count or 0,
            "active_entries": active.count or 0,
            "estimated_savings": round(cost_saved, 2),
            "status": "active",
        }
    except Exception:
        return {"status": "error"}


def invalidate_ticker(ticker: str) -> None:
    """Invalidate all cached AI responses for a ticker."""
    db = _db()
    if not db:
        return
    try:
        db.table("ai_response_cache").delete().eq("ticker", ticker).execute()
    except Exception:
        pass


def invalidate_page(source_page: str) -> None:
    """Invalidate all cached AI responses for a page."""
    db = _db()
    if not db:
        return
    try:
        db.table("ai_response_cache").delete().eq("source_page", source_page).execute()
    except Exception:
        pass

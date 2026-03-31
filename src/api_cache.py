"""API Response Cache — Supabase-backed caching layer for Polygon API calls.

Instead of Edge Functions, this caches API responses directly in Postgres.
First request hits Polygon (~1-2s), subsequent requests hit Supabase (~100ms).

Usage:
    from src.api_cache import cached_request
    data = cached_request("https://api.polygon.io/v2/aggs/...", params={...}, ttl=300)
"""

import json
import hashlib
import logging
import requests
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


def _db():
    try:
        from src.db import get_client
        return get_client()
    except Exception:
        return None


def _make_key(url: str, params: dict = None) -> str:
    """Generate a stable cache key from URL + params."""
    raw = url + json.dumps(params or {}, sort_keys=True)
    return hashlib.md5(raw.encode()).hexdigest()


def cached_request(url: str, params: dict = None, ttl: int = 300,
                    timeout: int = 30) -> dict | None:
    """Fetch URL with Supabase caching. Returns parsed JSON or None.

    Args:
        url: Full API URL
        params: Query parameters (excluding apiKey for cache key)
        ttl: Cache TTL in seconds (default 5 min)
        timeout: Request timeout in seconds
    """
    # Build cache key (exclude API keys so different users share cache)
    _key_excludes = {"apiKey", "api_key"}
    cache_params = {k: v for k, v in (params or {}).items() if k not in _key_excludes}
    key = _make_key(url.split("?")[0], cache_params)

    # Try cache first
    db = _db()
    if db:
        try:
            result = db.table("api_cache").select("response")\
                .eq("cache_key", key).gt("expires_at", datetime.now().isoformat())\
                .limit(1).execute()
            if result.data:
                return result.data[0]["response"]
        except Exception:
            pass  # Cache miss or error, proceed to API

    # Cache miss — hit the actual API
    try:
        r = requests.get(url, params=params, timeout=timeout)
        r.raise_for_status()
        data = r.json()

        # Store in cache
        if db and data:
            try:
                # Extract symbol from URL for indexing
                symbol = ""
                parts = url.split("/")
                for i, p in enumerate(parts):
                    if p in ("tickers", "ticker") and i + 1 < len(parts):
                        symbol = parts[i + 1]
                        break

                endpoint = url.split("api.polygon.io")[-1].split("?")[0] if "polygon" in url else url[:100]

                db.table("api_cache").upsert({
                    "cache_key": key,
                    "response": data,
                    "endpoint": endpoint[:200],
                    "symbol": symbol[:20],
                    "ttl_seconds": ttl,
                    "created_at": datetime.now().isoformat(),
                    "expires_at": (datetime.now() + timedelta(seconds=ttl)).isoformat(),
                }, on_conflict="cache_key").execute()
            except Exception as e:
                logger.debug(f"Cache write failed (non-critical): {e}")

        return data
    except Exception as e:
        logger.warning(f"API request failed: {url} — {e}")
        return None


def invalidate(symbol: str = None, endpoint: str = None) -> int:
    """Invalidate cache entries. Returns count deleted."""
    db = _db()
    if not db:
        return 0
    try:
        q = db.table("api_cache").delete()
        if symbol:
            q = q.eq("symbol", symbol)
        if endpoint:
            q = q.like("endpoint", f"%{endpoint}%")
        if not symbol and not endpoint:
            q = q.lt("expires_at", datetime.now().isoformat())
        result = q.execute()
        return len(result.data) if result.data else 0
    except Exception:
        return 0


def get_cache_stats() -> dict:
    """Return cache statistics."""
    db = _db()
    if not db:
        return {"status": "unavailable"}
    try:
        total = db.table("api_cache").select("cache_key", count="exact").execute()
        active = db.table("api_cache").select("cache_key", count="exact")\
            .gt("expires_at", datetime.now().isoformat()).execute()
        return {
            "total_entries": total.count if total.count else 0,
            "active_entries": active.count if active.count else 0,
            "status": "active",
        }
    except Exception:
        return {"status": "error"}

"""Two-tier cache utility for CFTC / CTA scan outputs.

In-memory TTL cache backed by a shared Supabase table. The persistence tier
means every Cloud Run cold start hydrates from Supabase instead of paying
the 30-60s direct-archive-download + rolling-percentile cost again.

Table: public.cftc_cache (see supabase_cftc_cache_schema.sql).
"""

from __future__ import annotations

import inspect
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

_RESULT_CACHE: dict[str, tuple[datetime, object]] = {}
_RESULT_TTL = timedelta(hours=12)


def _supabase_get(key: str) -> tuple[datetime, object] | None:
    try:
        from src.db import get_client
        client = get_client()
        if client is None:
            return None
        resp = client.table("cftc_cache").select("value, updated_at").eq("key", key).limit(1).execute()
        rows = resp.data or []
        if not rows:
            return None
        row = rows[0]
        updated_at = datetime.fromisoformat(row["updated_at"].replace("Z", "+00:00")).replace(tzinfo=None)
        return (updated_at, row["value"])
    except Exception as e:
        logger.debug(f"cftc_cache supabase get failed for {key}: {e}")
        return None


def _supabase_put(key: str, value: object) -> None:
    try:
        from src.db import get_client
        client = get_client()
        if client is None:
            return
        client.table("cftc_cache").upsert({
            "key": key,
            "value": value,
            "updated_at": datetime.utcnow().isoformat(),
        }).execute()
    except Exception as e:
        logger.debug(f"cftc_cache supabase put failed for {key}: {e}")


def _should_cache(value: object) -> bool:
    """Skip caching substantively-empty results so transient failures don't
    lock in bad state for the full TTL."""
    if value is None:
        return False
    if isinstance(value, (list, dict)) and len(value) == 0:
        return False
    if isinstance(value, dict) and value.get("error"):
        return False
    return True


def _stable_key(key: str, fn, args, kwargs) -> str:
    """Build a cache key that treats positional and keyword args as identical
    when they bind to the same parameter. Also skips any arg whose name starts
    with `_no_cache_` so big in-memory payloads don't bloat the key."""
    try:
        sig = inspect.signature(fn)
        bound = sig.bind(*args, **kwargs)
        bound.apply_defaults()
        normalized = {k: v for k, v in bound.arguments.items() if not k.startswith("_no_cache_")}
        # Only hash simple scalars to keep keys small. Complex values (dicts,
        # lists) drop out of the key entirely — functions that accept those
        # should fetch their own data internally to avoid this.
        items = sorted(
            (k, v) for k, v in normalized.items()
            if isinstance(v, (int, float, str, bool, type(None)))
        )
        return f"{key}:{items}"
    except (TypeError, ValueError):
        return f"{key}:{args}:{sorted(kwargs.items())}"


def result_cached(key: str):
    """Two-tier cache decorator. Memory first, then Supabase; on miss compute
    + write back. 12h TTL applies to both tiers. CFTC data is weekly-cadence
    so this is conservative."""
    def deco(fn):
        def wrapper(*args, **kwargs):
            full_key = _stable_key(key, fn, args, kwargs)
            entry = _RESULT_CACHE.get(full_key)
            if entry and (datetime.utcnow() - entry[0]) < _RESULT_TTL:
                return entry[1]
            sb = _supabase_get(full_key)
            if sb and (datetime.utcnow() - sb[0]) < _RESULT_TTL:
                _RESULT_CACHE[full_key] = sb
                return sb[1]
            v = fn(*args, **kwargs)
            if _should_cache(v):
                _RESULT_CACHE[full_key] = (datetime.utcnow(), v)
                _supabase_put(full_key, v)
            return v
        wrapper.__name__ = fn.__name__
        return wrapper
    return deco

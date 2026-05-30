"""Energy data endpoints — EIA natural gas, oil, Henry Hub.

Bundle endpoints cache the assembled response in two layers:

  L1: process-local TTLCache. Microsecond reads, survives the request boundary
      but not a Cloud Run instance restart.
  L2: Supabase ai_response_cache. Survives restarts, shared across instances,
      but each lookup is a ~100-300ms HTTP round-trip on the event loop.

Order: L1 hit → return. L1 miss → L2 read; on hit, hydrate L1. L2 miss → caller
rebuilds and writes through both layers. Without L1 every request paid for a
Supabase read even when the answer hadn't changed; on a warm instance that was
the dominant cost of /api/energy/{oil,natgas} after the 8 EIA fetches.
"""

import json
import logging
from datetime import datetime, timedelta
from cachetools import TTLCache
from fastapi import APIRouter, Depends, Query
from api._json_safe import df_records
from api.deps import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()

# L1: process-local. Sized for the handful of cacheable bundles this module
# manages (oil, natgas, ercot, fama-french, etc.) plus a little headroom. TTL
# matches the longest server-side cache we hand out; per-key reads still honour
# the caller's ttl_minutes by comparing the stored timestamp.
_L1_CACHE: TTLCache = TTLCache(maxsize=32, ttl=60 * 60 * 6)


def _get_bundle_cache(key: str, ttl_minutes: int = 30) -> dict | None:
    """Read a cached JSON bundle, L1 first then Supabase L2."""
    # L1: cheap dict access, no I/O.
    entry = _L1_CACHE.get(key)
    if entry is not None:
        stored_at, data = entry
        if (datetime.now() - stored_at).total_seconds() < ttl_minutes * 60:
            return data
        # Stale per caller's tighter TTL — drop and fall through to L2 / rebuild,
        # so subsequent reads don't re-pay the comparison.
        _L1_CACHE.pop(key, None)

    # L2: Supabase round-trip. Still sync — happens at most once per ttl_minutes
    # per Cloud Run instance per key.
    try:
        from src.db import get_client
        db = get_client()
        if not db:
            return None
        result = db.table("ai_response_cache").select("response")\
            .eq("input_hash", key)\
            .gt("expires_at", datetime.now().isoformat())\
            .limit(1).execute()
        if result.data:
            resp = result.data[0]["response"]
            data = json.loads(resp) if isinstance(resp, str) else resp
            _L1_CACHE[key] = (datetime.now(), data)
            return data
    except Exception:
        pass
    return None


def _set_bundle_cache(key: str, data: dict, ttl_minutes: int = 30) -> None:
    """Write a JSON bundle to L1 + Supabase L2."""
    _L1_CACHE[key] = (datetime.now(), data)
    try:
        from src.db import get_client
        db = get_client()
        if not db:
            return
        db.table("ai_response_cache").upsert({
            "input_hash": key,
            "model": "bundle_cache",
            "source_page": "energy",
            "ticker": "",
            "prompt_summary": key,
            "response": json.dumps(data),
            "tokens_used": 0,
            "cost_estimate": 0,
            "created_at": datetime.now().isoformat(),
            "expires_at": (datetime.now() + timedelta(minutes=ttl_minutes)).isoformat(),
        }, on_conflict="input_hash").execute()
    except Exception as e:
        logger.debug(f"Bundle cache write failed: {e}")


@router.get("/eia/{series_id:path}")
async def eia_series(
    series_id: str,
    rows: int = Query(260, ge=10, le=1000),
    user: str = Depends(get_current_user),
):
    """Fetch an EIA timeseries. Returns period, value, wow_change."""
    from src.eia_helpers import fetch_eia_data
    df = fetch_eia_data(series_id, tail_rows=rows)
    if df is None or df.empty:
        return {"series_id": series_id, "data": []}
    return {
        "series_id": series_id,
        "data": df_records(df[["period", "value", "wow_change"]]),
    }


@router.get("/natgas")
async def natgas_bundle(user: str = Depends(get_current_user)):
    """Fetch all natural gas series in parallel.

    First checks a 30-min bundle cache so repeat loads skip all 8 EIA round-trips.
    """
    CACHE_KEY = "energy_natgas_bundle"

    # Fast path: return cached bundle (~5ms)
    cached = _get_bundle_cache(CACHE_KEY, ttl_minutes=30)
    if cached:
        return cached

    # Slow path: fetch 8 series in parallel (~2-8s)
    from concurrent.futures import ThreadPoolExecutor
    from src.eia_helpers import fetch_eia_data

    series = [
        ("NG.NW2_EPG0_SWO_R48_BCF.W", 520),
        ("NG.NW2_EPG0_SWO_R31_BCF.W", 260),
        ("NG.NW2_EPG0_SWO_R32_BCF.W", 260),
        ("NG.NW2_EPG0_SWO_R33_BCF.W", 260),
        ("NG.NW2_EPG0_SWO_R34_BCF.W", 260),
        ("NG.NW2_EPG0_SWO_R35_BCF.W", 260),
        ("NG.RNGWHHD.W", 260),
        ("NG.N9140US2.M", 60),
    ]

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda args: fetch_eia_data(*args), series))

    def to_records(df):
        if df is None or df.empty:
            return []
        return df_records(df[["period", "value", "wow_change"]])

    bundle = {
        "storage": to_records(results[0]),
        "regions": {
            "East": to_records(results[1]),
            "Midwest": to_records(results[2]),
            "Mountain": to_records(results[3]),
            "Pacific": to_records(results[4]),
            "South Central": to_records(results[5]),
        },
        "henry_hub": to_records(results[6]),
        "consumption": to_records(results[7]),
    }

    # Cache the assembled bundle for 30 min
    _set_bundle_cache(CACHE_KEY, bundle, ttl_minutes=30)

    return bundle


@router.get("/oil")
async def oil_bundle(user: str = Depends(get_current_user)):
    """Fetch all oil fundamental series in parallel — inventories, production, prices, trade, products."""
    # _v2: bundle gained SPR + 5 PADDs in 2026-05-29. Bumped so the cache
    # doesn't serve pre-deploy 10-field rows after a schema expansion — the
    # frontend assumes the new fields exist and would crash on undefined.
    # _v3 (2026-05-30): Track B — global/OECD STEO series (oecd_stocks,
    # world_production, world_consumption, world_crude, world_stock_change).
    # Any future shape change should bump again.
    CACHE_KEY = "energy_oil_bundle_v3"

    cached = _get_bundle_cache(CACHE_KEY, ttl_minutes=30)
    if cached:
        return cached

    from concurrent.futures import ThreadPoolExecutor
    from src.eia_helpers import fetch_eia_data

    series = [
        ("PET.WCESTUS1.W", 520),   # 0  Commercial crude inventories
        ("PET.WCRFPUS2.W", 260),   # 1  US field production
        ("PET.WCRSTUS1.W", 260),   # 2  Cushing, OK storage
        ("PET.WPULEUS3.W", 260),   # 3  Refinery utilization
        ("PET.WCEIMUS2.W", 260),   # 4  Imports
        ("PET.WCREXUS2.W", 260),   # 5  Exports
        ("PET.RWTC.W", 260),       # 6  WTI spot price
        ("PET.WGTSTUS1.W", 260),   # 7  Gasoline inventories
        ("PET.WDISTUS1.W", 260),   # 8  Distillate inventories
        ("PET.WRPUPUS2.W", 520),   # 9  Product supplied (demand proxy) — 520 for DoS 5-yr trend
        ("PET.WCSSTUS1.W", 520),   # 10 Strategic Petroleum Reserve stocks
        ("PET.WCESTP11.W", 520),   # 11 PADD 1 East Coast crude (excl SPR)
        ("PET.WCESTP21.W", 520),   # 12 PADD 2 Midwest crude (excl SPR)
        ("PET.WCESTP31.W", 520),   # 13 PADD 3 Gulf Coast crude (excl SPR)
        ("PET.WCESTP41.W", 520),   # 14 PADD 4 Rocky Mountain crude (excl SPR)
        ("PET.WCESTP51.W", 520),   # 15 PADD 5 West Coast crude (excl SPR)
        # Track B — global / OECD (STEO, monthly). These series carry an ~18-mo
        # forecast tail past the current month; 144 rows = ~12 yrs history +
        # forecast, enough for the OECD 5-year seasonal band.
        ("STEO.PASC_OECD_T3.M", 144),   # 16 OECD commercial crude+liquids inventory (Mb, eop)
        ("STEO.PAPR_WORLD.M", 144),     # 17 World total liquids production (mb/d)
        ("STEO.PATC_WORLD.M", 144),     # 18 World total liquids consumption (mb/d)
        ("STEO.COPR_WORLD.M", 144),     # 19 World crude oil production (mb/d)
        ("STEO.T3_STCHANGE_WORLD.M", 144),  # 20 Net world inventory withdrawals (mb/d)
    ]

    # max_workers matches the series count so the fan-out stays single-batch.
    with ThreadPoolExecutor(max_workers=len(series)) as pool:
        results = list(pool.map(lambda args: fetch_eia_data(*args), series))

    def to_records(df):
        if df is None or df.empty:
            return []
        return df_records(df[["period", "value", "wow_change"]])

    bundle = {
        "inventories": to_records(results[0]),
        "production":  to_records(results[1]),
        "cushing":     to_records(results[2]),
        "refinery":    to_records(results[3]),
        "imports":     to_records(results[4]),
        "exports":     to_records(results[5]),
        "wti":         to_records(results[6]),
        "gasoline":    to_records(results[7]),
        "distillate":  to_records(results[8]),
        "supplied":    to_records(results[9]),
        "spr":         to_records(results[10]),
        "padd1":       to_records(results[11]),
        "padd2":       to_records(results[12]),
        "padd3":       to_records(results[13]),
        "padd4":       to_records(results[14]),
        "padd5":       to_records(results[15]),
        # Track B — global / OECD (STEO monthly, includes forecast tail)
        "oecd_stocks":       to_records(results[16]),
        "world_production":  to_records(results[17]),
        "world_consumption": to_records(results[18]),
        "world_crude":       to_records(results[19]),
        "world_stock_change": to_records(results[20]),
    }

    _set_bundle_cache(CACHE_KEY, bundle, ttl_minutes=30)
    return bundle


@router.get("/ercot/{endpoint}")
async def ercot_dashboard(
    endpoint: str,
    user: str = Depends(get_current_user),
):
    """Proxy to ERCOT public dashboard API. Endpoints: fuel-mix, supply-demand,
    loadForecastVsActual, ancillary-services, systemWidePrices, combinedWindAndSolar."""
    from src.ercot_api import fetch_dashboard
    data = fetch_dashboard(endpoint)
    if data is None:
        return {"error": f"ERCOT {endpoint} unavailable", "data": None}
    return data


@router.get("/ercot-bundle")
async def ercot_bundle(user: str = Depends(get_current_user)):
    """Fetch all ERCOT dashboard endpoints in parallel."""
    CACHE_KEY = "energy_ercot_bundle"

    cached = _get_bundle_cache(CACHE_KEY, ttl_minutes=5)
    if cached:
        return cached

    from concurrent.futures import ThreadPoolExecutor
    from src.ercot_api import fetch_dashboard

    endpoints = ["fuel-mix", "supply-demand", "loadForecastVsActual", "ancillary-services"]

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(fetch_dashboard, endpoints))

    bundle = {
        "fuel_mix": results[0],
        "supply_demand": results[1],
        "load_forecast": results[2],
        "ancillary": results[3],
    }

    _set_bundle_cache(CACHE_KEY, bundle, ttl_minutes=5)
    return bundle


@router.get("/ercot-capacity/months")
async def ercot_capacity_months(user: str = Depends(get_current_user)):
    """Discover the ERCOT monthly capacity reports currently available online.

    Returns a list of {date_path, month_label} ordered newest first. Cached 24h
    server-side.
    """
    from src.ercot_capacity import discover_months
    return {"months": discover_months(lookback=12)}


@router.get("/ercot-capacity")
async def ercot_capacity(
    month_label: str = Query(..., description="e.g. March_2026 — from /ercot-capacity/months"),
    date_path: str = Query(..., description="e.g. 2026/04/05 — from /ercot-capacity/months"),
    planned_only: bool = Query(False),
    user: str = Depends(get_current_user),
):
    """Download and parse one month of ERCOT capacity data.

    Returns all project rows with normalized fields. Cached 1h server-side per
    (date_path, month_label, planned_only) combo. An empty ``projects`` list
    indicates the report wasn't available or had no usable rows.
    """
    from src.ercot_capacity import fetch_capacity_file
    projects = fetch_capacity_file(date_path, month_label, planned_only=planned_only)
    return {
        "month_label": month_label,
        "date_path": date_path,
        "planned_only": planned_only,
        "projects": projects,
    }


@router.get("/futures-snapshot")
async def futures_snapshot(user: str = Depends(get_current_user)):
    """Fetch snapshot for all futures tickers across 6 asset classes."""
    CACHE_KEY = "energy_futures_snapshot"

    cached = _get_bundle_cache(CACHE_KEY, ttl_minutes=5)
    if cached:
        return cached

    from src.data_engine import polygon_batch_snapshot

    FUTURES = {
        "Indices": {"ES=F": "S&P 500", "NQ=F": "Nasdaq 100", "YM=F": "Dow Jones", "RTY=F": "Russell 2000"},
        "Energy": {"CL=F": "Crude Oil (WTI)", "NG=F": "Natural Gas", "RB=F": "Gasoline (RBOB)", "HO=F": "Heating Oil"},
        "Metals": {"GC=F": "Gold", "SI=F": "Silver", "HG=F": "Copper", "PL=F": "Platinum"},
        "Rates": {"ZB=F": "30-Year Bond", "ZN=F": "10-Year Note", "ZF=F": "5-Year Note", "ZT=F": "2-Year Note"},
        "Agriculture": {"ZC=F": "Corn", "ZS=F": "Soybeans", "ZW=F": "Wheat", "KC=F": "Coffee"},
        "FX": {"6E=F": "Euro", "6J=F": "Yen", "6B=F": "British Pound", "DX=F": "Dollar Index"},
    }

    all_tickers = [tk for sector in FUTURES.values() for tk in sector.keys()]
    snaps = polygon_batch_snapshot(all_tickers)

    result = {}
    for sector, tickers in FUTURES.items():
        items = []
        for tk, name in tickers.items():
            s = snaps.get(tk)
            if s and s.get("price"):
                prev = s.get("prev_close", s["price"])
                change = s["price"] - prev
                pct = (change / prev * 100) if prev else 0
                items.append({"ticker": tk, "name": name, "price": s["price"], "change": change, "pct_change": pct})
        result[sector] = items

    _set_bundle_cache(CACHE_KEY, result, ttl_minutes=5)
    return result

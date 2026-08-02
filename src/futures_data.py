"""CME futures bars from Massive's futures API.

THIS IS NOT THE SAME API as the stocks/options calls elsewhere in this codebase.
Futures live on a different host AND a different path:

    https://api.massive.com/futures/v1/aggs/{ticker}
    https://api.polygon.io/v2/aggs/ticker/{ticker}/...   <- stocks, unrelated

and they are keyed by per-expiry CME codes (`ESU6` = Sep 2026), not `ES=F`.
Asking the *stocks* endpoint for `ES` returns Eversource Energy — a real,
unrelated equity that quotes in a plausible price range — which is why futures
looked unavailable here for months. There is no continuous contract; the roll is
ours to define.

Entitlement, measured rather than assumed (Futures Basic, the free tier):
  - daily/minute/hour aggregates  ✓, 2y history, all of CME/CBOT/NYMEX/COMEX
  - the FULL Globex session ✓ — bars in every ET hour except 17:00, which is the
    settlement break, not missing data
  - second aggregates, trades, quotes  ✗ 403 (Starter/Developer tiers)
  - 5 API calls per minute — the binding constraint, hence the caching below
"""

from __future__ import annotations

import logging
import threading
from collections import deque
import time as _time
from datetime import date as _date

import pandas as pd

logger = logging.getLogger(__name__)

_BASE = "https://api.massive.com"
_TZ = "America/New_York"

# Basic allows 5 requests per MINUTE — not one every twelve seconds. Enforcing a
# fixed gap was needlessly strict in a way that showed up as fragility, not
# safety: resolving the front contract takes three calls and so took 25s before
# a single bar was fetched, and the ES levels card on Cloud Run went `degraded`
# waiting for it. A rolling window allows the burst the tier actually permits
# and only blocks once the window is genuinely full.
_MAX_PER_WINDOW = 4       # of 5, leaving headroom for a retry
_WINDOW_S = 60.0
_RATE_LIMIT_SLEEP = 15.0
_MAX_RETRIES = 6

_call_times: deque[float] = deque()
_pace_lock = threading.Lock()


def _pace() -> None:
    """Block only when the last minute is genuinely full."""
    with _pace_lock:
        now = _time.time()
        while _call_times and now - _call_times[0] >= _WINDOW_S:
            _call_times.popleft()
        if len(_call_times) >= _MAX_PER_WINDOW:
            _time.sleep(max(_WINDOW_S - (now - _call_times[0]) + 0.25, 0.0))
            now = _time.time()
            while _call_times and now - _call_times[0] >= _WINDOW_S:
                _call_times.popleft()
        _call_times.append(_time.time())

_bar_cache: dict[tuple, tuple[float, pd.DataFrame]] = {}
_front_cache: dict[str, tuple[_date, str]] = {}
_BAR_TTL = 300.0


def _get(path: str, **params) -> dict | None:
    """Authenticated GET with 429 backoff. None on failure."""
    try:
        from src.api_keys import get_secret
        import requests
        key = get_secret("MASSIVE_API_KEY")
        if not key:
            return None
        headers = {"Authorization": f"Bearer {key}"}
        for attempt in range(_MAX_RETRIES):
            _pace()
            r = requests.get(f"{_BASE}{path}", params=params, headers=headers, timeout=30)
            if r.status_code == 429:
                _time.sleep(_RATE_LIMIT_SLEEP)
                continue
            if r.status_code == 403:
                # Not an outage — this tier simply doesn't carry the schema.
                logger.info(f"futures {path}: not entitled ({r.text[:80]})")
                return None
            if r.status_code != 200:
                logger.warning(f"futures {path}: HTTP {r.status_code}")
                return None
            return r.json()
        logger.warning(f"futures {path}: still rate-limited after {_MAX_RETRIES} tries")
        return None
    except Exception as e:
        logger.warning(f"futures request failed for {path}: {e}")
        return None


def front_month(product_code: str = "ES", as_of: _date | None = None) -> str | None:
    """The contract that is actually trading, by volume — not by expiry alone.

    Rolling on expiry date alone puts you in a contract nobody is trading for the
    last week of its life: ES rolls about eight days early, and on 2026-07-31 the
    front contract turned over 1,736,589 lots against the next one's 901. So the
    two nearest expiries are compared on recent volume and the busier one wins,
    which reproduces the roll traders actually take without hardcoding a date.

    Cached per day — the answer changes four times a year.
    """
    today = as_of or _date.today()
    hit = _front_cache.get(product_code)
    if hit and hit[0] == today:
        return hit[1]

    j = _get("/futures/v1/contracts", product_code=product_code,
             date=today.isoformat(), limit=1000)
    if not j:
        return None
    singles = [c for c in (j.get("results") or [])
               if c.get("type") == "single" and (c.get("days_to_maturity") or -1) >= 0]
    if not singles:
        return None
    singles.sort(key=lambda c: c["days_to_maturity"])
    candidates = [c["ticker"] for c in singles[:2]]

    best, best_vol = None, -1.0
    for tkr in candidates:
        df = fetch_bars(tkr, resolution="1day", limit=3)
        vol = float(df["Volume"].sum()) if df is not None and not df.empty else 0.0
        if vol > best_vol:
            best, best_vol = tkr, vol
    if best is None:
        best = candidates[0]

    _front_cache[product_code] = (today, best)
    logger.info(f"{product_code} front month: {best} (volume {best_vol:,.0f})")
    return best


def fetch_bars(ticker: str, resolution: str = "5min", limit: int = 2000,
               window_start: str | None = None) -> pd.DataFrame:
    """OHLCV bars for one contract, indexed by tz-aware ET timestamps.

    Returns an EMPTY frame on any failure rather than raising — callers here
    degrade per-module, and an exception would take down cards that never asked
    for futures data.
    """
    ck = (ticker, resolution, limit, window_start)
    hit = _bar_cache.get(ck)
    if hit and (_time.time() - hit[0]) < _BAR_TTL:
        return hit[1]

    params = {"resolution": resolution, "limit": limit, "order": "desc"}
    if window_start:
        params["window_start"] = window_start
    j = _get(f"/futures/v1/aggs/{ticker}", **params)
    rows = (j or {}).get("results") or []
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    if "window_start" not in df.columns:
        return pd.DataFrame()
    df.index = (pd.to_datetime(df["window_start"], unit="ns", utc=True)
                .dt.tz_convert(_TZ))
    cols = {"open": "Open", "high": "High", "low": "Low",
            "close": "Close", "volume": "Volume"}
    missing = [c for c in cols if c not in df.columns]
    if missing:
        logger.warning(f"futures bars for {ticker} missing {missing}")
        return pd.DataFrame()
    out = df.rename(columns=cols)[list(cols.values())].sort_index()
    out = out[~out.index.duplicated(keep="last")].dropna()

    _bar_cache[ck] = (_time.time(), out)
    return out


def fetch_front_bars(product_code: str = "ES", resolution: str = "5min",
                     limit: int = 2000) -> tuple[pd.DataFrame, str | None]:
    """Bars for whatever contract is currently front month. Returns (bars, ticker)."""
    tkr = front_month(product_code)
    if not tkr:
        return pd.DataFrame(), None
    return fetch_bars(tkr, resolution=resolution, limit=limit), tkr

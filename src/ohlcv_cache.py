"""OHLCV cache backed by Supabase + yfinance/Polygon.

First call: full download from yfinance, store in Supabase.
Subsequent calls: fetch only missing days, append to cache.
During market hours: use Polygon for today's live bar.
"""

import logging
import pandas as pd
import math
from datetime import datetime, date, timedelta

logger = logging.getLogger(__name__)

# In-memory cache for the current server session (avoids hitting Supabase repeatedly)
_mem_cache: dict[str, tuple[pd.DataFrame, float]] = {}  # ticker -> (df, timestamp)
_MEM_TTL = 300  # 5 minutes


def _is_market_open() -> bool:
    """Check if US stock market is currently open."""
    try:
        from zoneinfo import ZoneInfo
        et = datetime.now(ZoneInfo("America/New_York"))
    except ImportError:
        from datetime import timezone, timedelta as td
        et = datetime.now(timezone(td(hours=-4)))  # EDT fallback
    if et.weekday() >= 5:
        return False
    # Market hours: 9:30 AM - 4:00 PM ET
    market_open = et.replace(hour=9, minute=30, second=0)
    market_close = et.replace(hour=16, minute=0, second=0)
    return market_open <= et <= market_close


# Polygon on this plan serves daily aggs for US equities/ETFs only, and only
# ~5 calendar years back. Indices (^GSPC), futures (ES=F), FX (EURUSD=X) and
# crypto (BTC-USD) come back as 200 with an EMPTY result set rather than an
# error, so gate on symbol shape instead of paying for a request that cannot
# succeed. Note we never rewrite a symbol to reach Polygon: a bare futures root
# quotes a real, unrelated equity there (ES -> Eversource, CL -> Colgate).
_POLYGON_MAX_HISTORY_DAYS = 365 * 5
_NON_EQUITY_SUFFIXES = ("-USD", "-USDT", "-EUR", "-GBP", "-JPY")


def _polygon_eligible(ticker: str) -> bool:
    """True only for plain US equity/ETF symbols Polygon can actually serve."""
    t = (ticker or "").upper()
    if not t:
        return False
    if t.startswith("^") or "=" in t:
        return False
    if t.endswith(_NON_EQUITY_SUFFIXES):
        return False
    return all(c.isalpha() or c in ".-" for c in t)


def _polygon_earliest() -> date:
    """Oldest date Polygon will serve on this plan."""
    return date.today() - timedelta(days=_POLYGON_MAX_HISTORY_DAYS)


def _fetch_polygon_daily(ticker: str, start_date: str, end_date: str) -> pd.DataFrame | None:
    """Fetch daily bars from Polygon for a date range."""
    try:
        from src.api_keys import get_secret
        import requests
        api_key = get_secret("MASSIVE_API_KEY")
        if not api_key:
            return None
        url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/{start_date}/{end_date}"
        r = requests.get(url, params={"adjusted": "true", "sort": "asc", "limit": 5000, "apiKey": api_key}, timeout=15)
        if r.status_code != 200:
            return None
        results = r.json().get("results", [])
        if not results:
            return None
        df = pd.DataFrame(results)
        df["date"] = pd.to_datetime(df["t"], unit="ms").dt.date
        df = df.rename(columns={"o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume"})
        df = df.set_index("date")
        return df[["Open", "High", "Low", "Close", "Volume"]]
    except Exception as e:
        logger.warning(f"Polygon fetch failed for {ticker}: {e}")
        return None


def _fetch_yfinance(ticker: str, period: str) -> pd.DataFrame | None:
    """Fetch from yfinance Ticker.history (thread-safe)."""
    try:
        import yfinance as yf
        df = yf.Ticker(ticker).history(period=period, auto_adjust=True)
        if df is None or len(df) < 10:
            return None
        df.index = df.index.date  # convert to date (no timezone)
        return df[["Open", "High", "Low", "Close", "Volume"]]
    except Exception as e:
        logger.warning(f"yfinance fetch failed for {ticker}: {e}")
        return None


# PostgREST enforces a server-side row cap (1000) that a client-side .limit()
# cannot raise. The old read asked for 3000 rows ASCENDING and got the OLDEST
# 1000 — silently discarding everything newer. A ticker with deep history read
# back as years stale, so every call fell through to a full yfinance
# re-download and the cache never once served it. Page explicitly.
_PAGE = 1000


def _load_from_supabase(ticker: str, since: date | None = None) -> tuple[pd.DataFrame | None, date | None]:
    """Load cached OHLCV from Supabase. Returns (df, latest_date) or (None, None)."""
    try:
        from src.db import get_client
        sb = get_client()
        if not sb:
            return None, None
        rows: list[dict] = []
        offset = 0
        while True:
            q = sb.table("ohlcv_cache").select("date,open,high,low,close,volume").eq("ticker", ticker)
            if since is not None:
                q = q.gte("date", since.isoformat())
            page = q.order("date").range(offset, offset + _PAGE - 1).execute()
            rows.extend(page.data or [])
            if not page.data or len(page.data) < _PAGE:
                break
            offset += _PAGE
        if not rows:
            return None, None
        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"]).dt.date
        df = df.set_index("date")
        df.columns = ["Open", "High", "Low", "Close", "Volume"]
        latest = df.index[-1]
        return df, latest
    except Exception as e:
        logger.warning(f"Supabase load failed for {ticker}: {e}")
        return None, None


def _save_to_supabase(ticker: str, df: pd.DataFrame):
    """Upsert OHLCV rows to Supabase."""
    try:
        from src.db import get_client
        sb = get_client()
        if not sb or df is None or len(df) == 0:
            return
        rows = []
        for dt, row in df.iterrows():
            o, h, l, c, v = float(row["Open"]), float(row["High"]), float(row["Low"]), float(row["Close"]), float(row["Volume"])
            # Skip rows with NaN values
            if any(math.isnan(x) for x in [o, h, l, c]):
                continue
            rows.append({
                "ticker": ticker,
                "date": str(dt),
                "open": round(o, 4),
                "high": round(h, 4),
                "low": round(l, 4),
                "close": round(c, 4),
                "volume": v if not math.isnan(v) else 0,
            })
        # Batch upsert in chunks of 500
        for i in range(0, len(rows), 500):
            sb.table("ohlcv_cache").upsert(rows[i:i+500], on_conflict="ticker,date").execute()
        logger.info(f"Cached {len(rows)} rows for {ticker}")
    except Exception as e:
        logger.warning(f"Supabase save failed for {ticker}: {e}")


def fetch_ohlcv(ticker: str, lookback_days: int = 1260) -> pd.DataFrame | None:
    """Fetch OHLCV with Supabase caching + Polygon for live data.

    1. Check Supabase cache
    2. If cached: fetch only missing recent days
    3. If not cached: full download from yfinance, store in Supabase
    4. During market hours: append today's bar from Polygon
    """
    import time as _time
    today = date.today()
    period_map = {252: "1y", 504: "2y", 756: "3y", 1260: "5y", 2520: "10y"}
    period = period_map.get(lookback_days, "5y")  # safe fallback
    earliest_needed = today - timedelta(days=int(lookback_days * 1.5))

    # Step 0: In-memory cache (same server session, < 5 min old)
    mem_key = f"{ticker}_{lookback_days}"
    if mem_key in _mem_cache:
        cached_mem, ts = _mem_cache[mem_key]
        if _time.time() - ts < _MEM_TTL:
            return cached_mem

    # Step 1: Check Supabase cache. Bounded to the window actually requested so
    # the common case is a single page.
    cached_df, latest_cached = _load_from_supabase(ticker, since=earliest_needed)

    if cached_df is not None and latest_cached is not None:
        # Step 2: Incremental update — only fetch missing days
        days_missing = (today - latest_cached).days
        gap_start = latest_cached + timedelta(days=1)

        if days_missing <= 1:
            # Cache is current (or market closed today)
            df = cached_df
        else:
            # Polygon fills a gap of any length inside its 5y window in one
            # request, so gap length is irrelevant to it. This used to be capped
            # at 30 days, and anything staler fell through to a full multi-year
            # yfinance re-download — which is self-reinforcing: once yfinance
            # throttles, nothing is saved, so the next call makes the same
            # expensive request against the same stale cache. SPY sat 50 days
            # behind on exactly this loop.
            new_df = None
            if _polygon_eligible(ticker) and gap_start >= _polygon_earliest():
                new_df = _fetch_polygon_daily(ticker, gap_start.isoformat(), today.isoformat())
            if (new_df is None or len(new_df) == 0) and days_missing <= 30:
                new_df = _fetch_yfinance(ticker, f"{days_missing + 5}d")
                if new_df is not None:
                    new_df = new_df[new_df.index > latest_cached]

            if new_df is not None and len(new_df) > 0:
                _save_to_supabase(ticker, new_df)
                df = pd.concat([cached_df, new_df])
                df = df[~df.index.duplicated(keep="last")]
                df = df.sort_index()
            elif days_missing > 30:
                # Neither source could patch the gap — rebuild from yfinance.
                full_df = _fetch_yfinance(ticker, period)
                if full_df is not None:
                    _save_to_supabase(ticker, full_df)
                    df = full_df
                else:
                    df = cached_df  # stale, but real bars beat no bars
            else:
                df = cached_df

        # Trim to requested lookback
        if len(df) > 0:
            df = df[df.index >= earliest_needed]
    else:
        # Step 3: No cache — full download. Prefer Polygon when it can cover the
        # whole requested window (it doesn't rate-limit); fall back to yfinance
        # for indices/futures/crypto and for depth beyond Polygon's 5y horizon.
        df = None
        eligible = _polygon_eligible(ticker)
        if eligible and earliest_needed >= _polygon_earliest():
            df = _fetch_polygon_daily(ticker, earliest_needed.isoformat(), today.isoformat())
        if df is None or len(df) == 0:
            df = _fetch_yfinance(ticker, period)
        if (df is None or len(df) == 0) and eligible:
            # Last resort: shallower than asked for, but real. Better than
            # blanking the caller because yfinance is throttling.
            df = _fetch_polygon_daily(ticker, _polygon_earliest().isoformat(), today.isoformat())
            if df is not None and len(df) > 0:
                logger.warning(f"{ticker}: yfinance unavailable, seeded from Polygon "
                               f"(5y max, {len(df)} bars) instead of {period}")
        if df is not None and len(df) > 0:
            _save_to_supabase(ticker, df)
        else:
            return None

    # Step 4: During market hours, get today's live bar from Polygon
    if _is_market_open() and df is not None and len(df) > 0:
        live = _fetch_polygon_daily(ticker, today.isoformat(), today.isoformat())
        if live is not None and len(live) > 0:
            df = pd.concat([df, live])
            df = df[~df.index.duplicated(keep="last")]
            df = df.sort_index()

    if df is None or len(df) < 50:
        return None

    # Ensure proper dtypes
    for col in ["Open", "High", "Low", "Close", "Volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # Store in memory cache
    _mem_cache[mem_key] = (df, _time.time())

    return df

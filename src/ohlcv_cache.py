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


def _load_from_supabase(ticker: str) -> tuple[pd.DataFrame | None, date | None]:
    """Load cached OHLCV from Supabase. Returns (df, latest_date) or (None, None)."""
    try:
        from src.db import get_client
        sb = get_client()
        if not sb:
            return None, None
        r = sb.table("ohlcv_cache").select("date,open,high,low,close,volume").eq("ticker", ticker).order("date").limit(3000).execute()
        if not r.data:
            return None, None
        df = pd.DataFrame(r.data)
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

    # Step 1: Check Supabase cache
    cached_df, latest_cached = _load_from_supabase(ticker)

    if cached_df is not None and latest_cached is not None:
        # Step 2: Incremental update — only fetch missing days
        days_missing = (today - latest_cached).days
        if days_missing <= 1:
            # Cache is current (or market closed today)
            df = cached_df
        elif days_missing <= 30:
            # Fetch just the missing days from Polygon (faster than yfinance for small ranges)
            start = (latest_cached + timedelta(days=1)).isoformat()
            end = today.isoformat()
            new_df = _fetch_polygon_daily(ticker, start, end)
            if new_df is None or len(new_df) == 0:
                # Fallback to yfinance for the missing period
                new_df = _fetch_yfinance(ticker, f"{days_missing + 5}d")
                if new_df is not None:
                    new_df = new_df[new_df.index > latest_cached]
            if new_df is not None and len(new_df) > 0:
                _save_to_supabase(ticker, new_df)
                df = pd.concat([cached_df, new_df])
                df = df[~df.index.duplicated(keep="last")]
                df = df.sort_index()
            else:
                df = cached_df
        else:
            # Too many days missing — re-download full history
            full_df = _fetch_yfinance(ticker, period)
            if full_df is not None:
                _save_to_supabase(ticker, full_df)
                df = full_df
            else:
                df = cached_df  # use stale cache as fallback

        # Trim to requested lookback
        if len(df) > 0:
            df = df[df.index >= earliest_needed]
    else:
        # Step 3: No cache — full download
        df = _fetch_yfinance(ticker, period)
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

import os
import logging
import pandas as pd
import numpy as np
import requests
from datetime import date, timedelta
from supabase import create_client, Client
import streamlit as st

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# POLYGON (MASSIVE) API HELPERS
# ─────────────────────────────────────────────

def _get_polygon_key():
    from src.api_keys import get_secret
    return get_secret("MASSIVE_API_KEY")


# Map common yfinance symbols to Polygon format
_POLYGON_SYMBOL_MAP = {
    # Indices & volatility
    "^GSPC": "I:SPX", "^VIX": "I:VIX", "^OVX": "I:OVX", "^GVZ": "I:GVZ",
    "^DJI": "I:DJI", "^IXIC": "I:COMP", "DX-Y.NYB": "I:DXY",
    "^TNX": "I:TNX", "^TYX": "I:TYX",
    # Index futures (Polygon uses bare symbols for continuous contracts)
    "ES=F": "ES", "NQ=F": "NQ", "YM=F": "YM", "RTY=F": "RTY",
    # Energy futures
    "CL=F": "CL", "BZ=F": "BZ", "NG=F": "NG", "TTF=F": "TTF",
    "RB=F": "RB", "HO=F": "HO",
    # Metals futures
    "GC=F": "GC", "SI=F": "SI", "HG=F": "HG", "PL=F": "PL",
    # Rates futures
    "ZB=F": "ZB", "ZN=F": "ZN", "ZF=F": "ZF", "ZT=F": "ZT",
    # Agriculture futures
    "ZC=F": "ZC", "ZS=F": "ZS", "ZW=F": "ZW", "KC=F": "KC",
    # FX futures
    "6E=F": "6E", "6J=F": "6J", "6B=F": "6B", "DX=F": "DX",
    # Crypto
    "BTC-USD": "X:BTCUSD", "ETH-USD": "X:ETHUSD",
}


def polygon_symbol(yf_symbol: str) -> str:
    """Convert a yfinance-style symbol to Polygon format."""
    if yf_symbol in _POLYGON_SYMBOL_MAP:
        return _POLYGON_SYMBOL_MAP[yf_symbol]
    # Handle specific contract months like CLK26.NYM → CLK2026
    if "." in yf_symbol and yf_symbol.endswith(".NYM"):
        base = yf_symbol.split(".")[0]  # e.g., CLK26
        return base
    return yf_symbol


@st.cache_data(ttl=300, show_spinner=False)
def polygon_snapshot(symbol: str) -> dict | None:
    """Get latest price + previous close for a single ticker via Polygon snapshot."""
    api_key = _get_polygon_key()
    if not api_key:
        return None
    sym = polygon_symbol(symbol)
    try:
        # Try stocks/indices snapshot first
        r = requests.get(
            f"https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers/{sym}",
            params={"apiKey": api_key}, timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            ticker = data.get("ticker", {})
            if ticker:
                day = ticker.get("day", {})
                prev = ticker.get("prevDay", {})
                price = day.get("c") or prev.get("c", 0)
                prev_close = prev.get("c", 0)
                return {"price": price, "prev_close": prev_close}
    except Exception:
        pass

    # Try aggs fallback (works for futures, forex, crypto)
    try:
        end = date.today()
        start = end - timedelta(days=7)
        r = requests.get(
            f"https://api.polygon.io/v2/aggs/ticker/{sym}/range/1/day/{start}/{end}",
            params={"apiKey": api_key, "sort": "desc", "limit": 2}, timeout=10,
        )
        if r.status_code == 200:
            results = r.json().get("results", [])
            if len(results) >= 2:
                return {"price": results[0]["c"], "prev_close": results[1]["c"]}
            elif len(results) == 1:
                return {"price": results[0]["c"], "prev_close": results[0]["o"]}
    except Exception as e:
        logger.warning(f"Polygon snapshot failed for {sym}: {e}")
    return None


def _load_snapshot_cache(symbols: list) -> dict:
    """Load recent snapshots from Supabase. Returns {symbol: {price, change}} for found symbols."""
    try:
        from src.db import get_client
        from datetime import datetime
        db = get_client()
        if not db:
            return {}
        result = db.table("api_cache").select("symbol, response")\
            .in_("symbol", symbols).eq("endpoint", "snapshot")\
            .gt("expires_at", datetime.now().isoformat()).execute()
        found = {}
        for row in (result.data or []):
            sym = row.get("symbol")
            data = row.get("response")
            if sym and data:
                found[sym] = data if isinstance(data, dict) else {}
        return found
    except Exception:
        return {}


def _save_snapshot_cache(results: dict) -> None:
    """Save snapshots to Supabase with 3-min TTL."""
    try:
        from src.db import get_client
        from datetime import datetime
        import json
        db = get_client()
        if not db:
            return
        rows = []
        expires = (datetime.now() + timedelta(minutes=3)).isoformat()
        for sym, data in results.items():
            rows.append({
                "cache_key": f"snap_{sym}",
                "response": data,
                "endpoint": "snapshot",
                "symbol": sym,
                "ttl_seconds": 180,
                "created_at": datetime.now().isoformat(),
                "expires_at": expires,
            })
        if rows:
            db.table("api_cache").upsert(rows, on_conflict="cache_key").execute()
    except Exception:
        pass


@st.cache_data(ttl=300, show_spinner=False)
def polygon_batch_snapshot(symbols: list) -> dict:
    """Get price snapshots for multiple symbols via Polygon all-tickers snapshot.
    Returns {original_symbol: {price, prev_close, change}}.
    Uses Supabase snapshot cache to avoid redundant API calls."""
    api_key = _get_polygon_key()
    results = {}
    if not api_key:
        return results

    # Check Supabase snapshot cache first
    cached = _load_snapshot_cache(symbols)
    if cached and len(cached) >= len(symbols) * 0.8:
        # Most symbols found in cache — return directly
        return cached
    # Merge any cached results we do have
    results.update(cached)
    # Only fetch symbols not in cache
    symbols_to_fetch = [s for s in symbols if s not in cached]
    if not symbols_to_fetch:
        return results

    # Try the bulk snapshot endpoint (one API call for remaining tickers)
    try:
        # Build a set of polygon-formatted symbols for matching — only uncached
        sym_map = {polygon_symbol(s): s for s in symbols_to_fetch}
        poly_syms = ",".join(sym_map.keys())
        r = requests.get(
            f"https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers",
            params={"tickers": poly_syms, "apiKey": api_key}, timeout=15,
        )
        if r.status_code == 200:
            for ticker in r.json().get("tickers", []):
                tsym = ticker.get("ticker", "")
                orig_sym = sym_map.get(tsym)
                if orig_sym:
                    day = ticker.get("day", {})
                    prev = ticker.get("prevDay", {})
                    price = day.get("c") or prev.get("c", 0)
                    prev_close = prev.get("c", 0)
                    if price and prev_close:
                        chg = ((price / prev_close) - 1) * 100
                        results[orig_sym] = {"price": price, "change": round(chg, 2)}
    except Exception as e:
        logger.warning(f"Polygon batch snapshot failed: {e}")

    # Sanity check batch results — reject implausible prices or zero-change (stale)
    for sym in list(results.keys()):
        price = results[sym].get("price", 0)
        change = results[sym].get("change", 0)
        bounds = _PRICE_SANITY_BOUNDS.get(sym)
        if bounds:
            low, high = bounds
            if not (low <= price <= high):
                logger.warning(
                    f"Polygon batch returned {sym}={price} — outside sanity bounds "
                    f"[{low}, {high}]. Will re-fetch via fallback."
                )
                del results[sym]
                continue
        # Zero change on a futures/index contract usually means stale weekend data
        if change == 0 and sym in _PRICE_SANITY_BOUNDS:
            logger.info(f"Polygon batch {sym}={price} has 0% change — likely stale, will re-fetch.")
            del results[sym]

    # Fill any missing or rejected symbols with individual calls + yfinance/FRED fallback
    for sym in symbols:
        if sym not in results:
            snap = polygon_snapshot_with_fallback(sym)
            if snap and snap.get("price"):
                prev = snap.get("prev_close") or snap["price"]
                chg = ((snap["price"] / prev) - 1) * 100 if prev > 0 else 0
                results[sym] = {"price": snap["price"], "change": round(chg, 2)}

    # Save all fetched results to Supabase cache
    new_results = {k: v for k, v in results.items() if k not in cached}
    if new_results:
        _save_snapshot_cache(new_results)

    return results


@st.cache_data(ttl=3600, show_spinner=False)
def polygon_history(symbol: str, days: int) -> pd.DataFrame:
    """Fetch daily OHLC history from Polygon, falling back to FRED for futures/indices/rates."""
    api_key = _get_polygon_key()
    if not api_key:
        return pd.DataFrame()
    sym = polygon_symbol(symbol)
    end = date.today()
    start = end - timedelta(days=days)
    try:
        r = requests.get(
            f"https://api.polygon.io/v2/aggs/ticker/{sym}/range/1/day/{start}/{end}",
            params={"apiKey": api_key, "sort": "asc", "limit": 50000}, timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        if "results" in data and data["results"]:
            df = pd.DataFrame(data["results"])
            df["Date"] = pd.to_datetime(df["t"], unit="ms")
            df.set_index("Date", inplace=True)
            rename_map = {"o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume"}
            available = {k: v for k, v in rename_map.items() if k in df.columns}
            df = df.rename(columns=available)
            return df[[v for v in available.values()]]
    except Exception as e:
        logger.warning(f"Polygon history failed for {sym}: {e}")

    # FRED fallback for tickers Polygon can't serve (futures, indices, rates)
    fred_series = _FRED_FALLBACK_MAP.get(symbol)
    if fred_series:
        return _fred_history(fred_series, days)
    return pd.DataFrame()

# --- HELPER FUNCTIONS ---
def get_supabase_client() -> Client:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key: return None
    try: return create_client(url, key)
    except Exception as e:
        logger.error(f"Failed to initialize Supabase: {e}")
        return None

def format_massive_ticker(user_input: str) -> str:
    if not user_input: return ""
    t = user_input.strip().upper()
    if ":" in t or t.startswith("ERCOT."): return t
    if "-USD" in t: return f"X:{t}"
    if any(x in t for x in ["HB_", "LZ_", "RT_", "DA_"]): return f"ERCOT.{t}"
    return t

def translate_to_yahoo(formatted_symbol: str) -> str:
    return formatted_symbol.replace("X:", "").replace("ERCOT.", "")

# --- PRICE DATA ENGINE ---

def _load_price_history(ticker: str, days: int) -> pd.DataFrame | None:
    """Load price history from Supabase. Returns DataFrame or None."""
    try:
        from src.db import get_client
        db = get_client()
        if not db:
            return None
        result = db.table("price_history").select("date, close")\
            .eq("ticker", ticker)\
            .gte("date", (date.today() - timedelta(days=days + 10)).isoformat())\
            .order("date", desc=False).execute()
        if result.data and len(result.data) >= max(days * 0.6, 2):
            df = pd.DataFrame(result.data)
            df["Date"] = pd.to_datetime(df["date"])
            df = df.set_index("Date")[["close"]].rename(columns={"close": "Close"})
            return df
    except Exception:
        pass
    return None


def _save_price_history(ticker: str, df: pd.DataFrame) -> None:
    """Save price bars to Supabase. Upserts to avoid duplicates."""
    try:
        from src.db import get_client
        db = get_client()
        if not db or df is None or df.empty:
            return
        rows = []
        for dt, row in df.iterrows():
            rows.append({
                "ticker": ticker,
                "date": dt.strftime("%Y-%m-%d"),
                "close": float(row["Close"]),
            })
        # Batch upsert in chunks of 100
        for i in range(0, len(rows), 100):
            chunk = rows[i:i+100]
            db.table("price_history").upsert(chunk, on_conflict="ticker,date").execute()
    except Exception as e:
        logger.debug(f"Price history save failed for {ticker}: {e}")


def _last_trading_day() -> date:
    """Return the most recent trading day (skip weekends).
    Uses UTC-5 (ET approximation) for the 4pm market close check."""
    from datetime import datetime, timezone
    # ET is UTC-5 (EST) or UTC-4 (EDT). Use UTC-5 as conservative estimate.
    et_now = datetime.now(timezone.utc) - timedelta(hours=5)
    d = et_now.date()
    # If before 4pm ET on a weekday, yesterday's bar is the latest
    if d.weekday() < 5 and et_now.hour < 16:
        d = d - timedelta(days=1)
    while d.weekday() >= 5:
        d = d - timedelta(days=1)
    return d


@st.cache_data(ttl=3600, show_spinner="Fetching market data...")
def fetch_massive_data(symbol: str, days: int) -> pd.DataFrame:
    end_date = date.today()
    start_date = end_date - timedelta(days=days)
    formatted_symbol = format_massive_ticker(symbol)

    api_key = _get_polygon_key()
    if not api_key:
        logger.error("No MASSIVE_API_KEY configured")
        return None

    # Step 1: Check Supabase price_history (fast path ~100ms)
    cached_df = _load_price_history(formatted_symbol, days)
    if cached_df is not None and not cached_df.empty:
        last_bar = cached_df.index[-1].date() if hasattr(cached_df.index[-1], 'date') else cached_df.index[-1]
        latest_trading = _last_trading_day()
        if last_bar >= latest_trading:
            # Data is current — return from cache
            st.session_state['current_data_source'] = "Supabase Cache"
            return cached_df.tail(days)
        else:
            # Data exists but stale — fetch only the gap
            gap_start = (last_bar + timedelta(days=1)).isoformat()
            try:
                url = f"https://api.polygon.io/v2/aggs/ticker/{formatted_symbol}/range/1/day/{gap_start}/{end_date.strftime('%Y-%m-%d')}"
                res = requests.get(url, params={"apiKey": api_key, "sort": "asc", "limit": 50000}, timeout=15)
                res.raise_for_status()
                data = res.json()
                if data and 'results' in data:
                    new_df = pd.DataFrame(data['results'])
                    new_df['Date'] = pd.to_datetime(new_df['t'], unit='ms')
                    new_df = new_df.set_index('Date')[['c']].rename(columns={'c': 'Close'})
                    # Merge and save
                    merged = pd.concat([cached_df, new_df])
                    merged = merged[~merged.index.duplicated(keep='last')]
                    _save_price_history(formatted_symbol, new_df)
                    st.session_state['current_data_source'] = "Supabase + Polygon (gap fill)"
                    return merged.tail(days)
            except Exception:
                # Gap fill failed — return stale cache (better than nothing)
                st.session_state['current_data_source'] = "Supabase Cache (stale)"
                return cached_df.tail(days)

    # Step 2: Full fetch from Polygon (cold start)
    try:
        url = f"https://api.polygon.io/v2/aggs/ticker/{formatted_symbol}/range/1/day/{start_date.strftime('%Y-%m-%d')}/{end_date.strftime('%Y-%m-%d')}"
        res = requests.get(url, params={"apiKey": api_key, "sort": "asc", "limit": 50000}, timeout=30)
        res.raise_for_status()
        data = res.json()
        if data and 'results' in data:
            st.session_state['current_data_source'] = "Polygon API (full fetch)"
            df = pd.DataFrame(data['results'])
            df['Date'] = pd.to_datetime(df['t'], unit='ms')
            df.set_index('Date', inplace=True)
            result_df = df[['c']].rename(columns={'c': 'Close'})
            # Save to Supabase for future fast loads
            _save_price_history(formatted_symbol, result_df)
            return result_df
    except Exception as e:
        logger.warning(f"Massive API fetch failed for {formatted_symbol}: {e}")
    return None

# --- OPTIONS ENGINE ---
def _get_massive_key():
    from src.api_keys import get_secret
    return get_secret("MASSIVE_API_KEY")


def polygon_paginate(url: str, api_key: str, max_pages: int = 20) -> list:
    """Paginate through Polygon API results. Public helper for options pages."""
    results = []
    pages = 0
    while url and pages < max_pages:
        res = requests.get(url, timeout=30)
        res.raise_for_status()
        data = res.json()
        results.extend(data.get('results', []))
        next_url = data.get('next_url')
        url = f"{next_url}&apiKey={api_key}" if next_url else None
        pages += 1
    return results


@st.cache_data(ttl=3600)
def get_expiration_dates(symbol: str):
    """Fetches all available expiration dates.

    Uses expired=false and contract_type=call (halves results) to minimize
    API calls. Only needs expiration_date from each contract.
    """
    api_key = _get_massive_key()
    if api_key:
        # Try efficient single-page fetch first (covers most tickers)
        try:
            url = (
                f"https://api.polygon.io/v3/reference/options/contracts"
                f"?underlying_ticker={symbol}&expired=false&contract_type=call"
                f"&limit=1000&order=asc&sort=expiration_date&apiKey={api_key}"
            )
            # Quick fetch — 10s timeout, max 3 pages. Falls back to yfinance if slow.
            res = requests.get(url, timeout=10)
            res.raise_for_status()
            data = res.json()
            contracts = data.get("results", [])
            exps = sorted(set(c['expiration_date'] for c in contracts))
            if exps:
                return exps
        except Exception as e:
            logger.warning(f"Massive expirations fetch failed for {symbol}: {e}")

    # yfinance fallback for expiration dates
    try:
        import yfinance as yf
        t = yf.Ticker(symbol)
        if t.options:
            return sorted(t.options)
    except Exception:
        pass

    return []


def _load_cached_chain(symbol: str, expiration: str = None) -> pd.DataFrame | None:
    """Load options chain from Supabase cache if fresh (< 2 hours)."""
    try:
        from src.db import get_client
        from datetime import datetime
        db = get_client()
        if not db:
            return None
        cache_key = f"chain_{symbol}_{expiration or 'all'}"
        result = db.table("api_cache").select("response")\
            .eq("cache_key", cache_key).gt("expires_at", datetime.now().isoformat())\
            .limit(1).execute()
        if result.data:
            import json
            rows = result.data[0]["response"]
            if isinstance(rows, str):
                rows = json.loads(rows)
            if rows:
                return pd.DataFrame(rows)
    except Exception:
        pass
    return None


def _save_cached_chain(symbol: str, expiration: str, df: pd.DataFrame) -> None:
    """Save options chain to Supabase cache with 2-hour TTL."""
    try:
        from src.db import get_client
        from datetime import datetime
        import json
        db = get_client()
        if not db or df is None or df.empty:
            return
        cache_key = f"chain_{symbol}_{expiration or 'all'}"
        db.table("api_cache").upsert({
            "cache_key": cache_key,
            "response": df.to_dict(orient="records"),
            "endpoint": f"/v3/snapshot/options/{symbol}",
            "symbol": symbol,
            "ttl_seconds": 7200,
            "created_at": datetime.now().isoformat(),
            "expires_at": (datetime.now() + timedelta(hours=2)).isoformat(),
        }, on_conflict="cache_key").execute()
    except Exception:
        pass


@st.cache_data(ttl=3600, show_spinner="Fetching options chain from Massive...")
def fetch_options_chain(symbol: str, expiration: str = None, max_pages: int = 20) -> pd.DataFrame:
    """Fetches full options chain with Greeks from Massive (Polygon), yfinance fallback.
    Pass expiration=None to fetch across ALL expirations (for vol surfaces).
    Uses Supabase cache (2h TTL) to avoid redundant API calls across pages."""

    # Try Supabase cache first (~100ms vs ~2s)
    cached = _load_cached_chain(symbol, expiration)
    if cached is not None and not cached.empty:
        st.session_state['current_data_source'] = "Supabase Cache (options)"
        return cached

    api_key = _get_massive_key()
    if api_key:
        try:
            url = f"https://api.polygon.io/v3/snapshot/options/{symbol}?limit=250&apiKey={api_key}"
            if expiration:
                url += f"&expiration_date={expiration}"
            results = polygon_paginate(url, api_key, max_pages=max_pages)
            if results:
                rows = []
                for r in results:
                    d = r.get('details', {})
                    g = r.get('greeks', {})
                    day = r.get('day', {})
                    quote = r.get('last_quote', {})

                    bid = quote.get('bid') or 0
                    ask = quote.get('ask') or 0
                    day_close = day.get('close', 0) or 0
                    day_vwap = day.get('vwap', 0) or 0

                    # Track whether this is a real live quote or synthetic from daily close
                    quote_is_live = bid > 0 and ask > 0

                    if bid == 0 and day_close > 0:
                        bid = day_close * 0.95  # wider synthetic spread (was 0.99)
                    if ask == 0 and day_close > 0:
                        ask = day_close * 1.05  # wider synthetic spread (was 1.01)

                    last_price = day_close or day_vwap or 0

                    rows.append({
                        'strike_price': d.get('strike_price'),
                        'contract_type': d.get('contract_type'),
                        'expiration_date': d.get('expiration_date'),
                        'bid': bid,
                        'ask': ask,
                        'last_price': last_price,
                        'quote_live': quote_is_live,
                        'volume': day.get('volume', 0),
                        'open_interest': r.get('open_interest', 0),
                        'implied_volatility': r.get('implied_volatility', 0),
                        'delta': g.get('delta', 0),
                        'gamma': g.get('gamma', 0),
                        'theta': g.get('theta', 0),
                        'vega': g.get('vega', 0),
                        'rho': g.get('rho', 0),
                        'day_open': day.get('open', 0) or 0,
                        'day_high': day.get('high', 0) or 0,
                        'day_low': day.get('low', 0) or 0,
                        'day_vwap': day_vwap,
                        'trade_count': day.get('trade_count', 0) or 0,
                    })
                df = pd.DataFrame(rows)
                st.session_state['current_data_source'] = "Massive API (Polygon)"
                # Cache in Supabase for other pages
                _save_cached_chain(symbol, expiration, df)
                return df
        except Exception as e:
            logger.warning(f"Massive options chain fetch failed for {symbol}: {e}")

    return None

@st.cache_data(ttl=86400, show_spinner=False)
def polygon_ticker_details(symbol: str) -> dict:
    """Fetch company metadata from Polygon Reference API. Replaces yf.Ticker().info."""
    api_key = _get_polygon_key()
    if not api_key:
        return {}
    sym = polygon_symbol(symbol)
    try:
        r = requests.get(
            f"https://api.polygon.io/v3/reference/tickers/{sym}",
            params={"apiKey": api_key}, timeout=10,
        )
        if r.status_code == 200:
            data = r.json().get("results", {})
            return {
                "shortName": data.get("name", symbol),
                "longName": data.get("name", symbol),
                "sector": data.get("sic_description", ""),
                "industry": data.get("sic_description", ""),
                "market_cap": data.get("market_cap", 0),
                "currency": data.get("currency_name", "USD"),
                "exchange": data.get("primary_exchange", ""),
                "type": data.get("type", ""),
                "locale": data.get("locale", ""),
            }
    except Exception as e:
        logger.warning(f"Polygon ticker details failed for {sym}: {e}")
    return {}


@st.cache_data(ttl=3600, show_spinner=False)
def polygon_intraday(symbol: str, interval_min: int = 5, bars: int = 100) -> pd.DataFrame:
    """Fetch intraday bars from Polygon. Returns DataFrame with Close, Open, High, Low, Volume."""
    api_key = _get_polygon_key()
    if not api_key:
        return pd.DataFrame()
    sym = polygon_symbol(symbol)
    end = date.today()
    # Go back enough days to get bars (weekends, holidays)
    start = end - timedelta(days=7)
    try:
        r = requests.get(
            f"https://api.polygon.io/v2/aggs/ticker/{sym}/range/{interval_min}/minute/{start}/{end}",
            params={"apiKey": api_key, "sort": "desc", "limit": bars}, timeout=15,
        )
        r.raise_for_status()
        results = r.json().get("results", [])
        if results:
            df = pd.DataFrame(results)
            df["Date"] = pd.to_datetime(df["t"], unit="ms")
            df.set_index("Date", inplace=True)
            df = df.sort_index()
            df = df.rename(columns={"o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume"})
            return df[["Open", "High", "Low", "Close", "Volume"]]
    except Exception as e:
        logger.warning(f"Polygon intraday failed for {sym}: {e}")
    return pd.DataFrame()


@st.cache_data(ttl=86400, show_spinner=False)
def polygon_financials(symbol: str, timeframe: str = "annual", limit: int = 4) -> dict:
    """Fetch income statement, balance sheet, and cash flow from Polygon financials API.
    Returns {'income': DataFrame, 'balance': DataFrame, 'cashflow': DataFrame}."""
    api_key = _get_polygon_key()
    if not api_key:
        return {"income": pd.DataFrame(), "balance": pd.DataFrame(), "cashflow": pd.DataFrame()}
    sym = polygon_symbol(symbol)
    try:
        r = requests.get(
            f"https://api.polygon.io/vX/reference/financials",
            params={"ticker": sym, "timeframe": timeframe, "limit": limit, "apiKey": api_key},
            timeout=15,
        )
        r.raise_for_status()
        results = r.json().get("results", [])
        if not results:
            return {"income": pd.DataFrame(), "balance": pd.DataFrame(), "cashflow": pd.DataFrame()}

        income_rows, balance_rows, cashflow_rows = [], [], []
        for filing in results:
            period = filing.get("fiscal_period", "")
            year = filing.get("fiscal_year", "")
            label = f"{period} {year}" if period else str(year)
            fins = filing.get("financials", {})

            # Income statement
            inc = fins.get("income_statement", {})
            inc_row = {"period": label}
            for key, val in inc.items():
                if isinstance(val, dict) and "value" in val:
                    inc_row[val.get("label", key)] = val["value"]
            if len(inc_row) > 1:
                income_rows.append(inc_row)

            # Balance sheet
            bs = fins.get("balance_sheet", {})
            bs_row = {"period": label}
            for key, val in bs.items():
                if isinstance(val, dict) and "value" in val:
                    bs_row[val.get("label", key)] = val["value"]
            if len(bs_row) > 1:
                balance_rows.append(bs_row)

            # Cash flow
            cf = fins.get("cash_flow_statement", {})
            cf_row = {"period": label}
            for key, val in cf.items():
                if isinstance(val, dict) and "value" in val:
                    cf_row[val.get("label", key)] = val["value"]
            if len(cf_row) > 1:
                cashflow_rows.append(cf_row)

        return {
            "income": pd.DataFrame(income_rows).set_index("period") if income_rows else pd.DataFrame(),
            "balance": pd.DataFrame(balance_rows).set_index("period") if balance_rows else pd.DataFrame(),
            "cashflow": pd.DataFrame(cashflow_rows).set_index("period") if cashflow_rows else pd.DataFrame(),
        }
    except Exception as e:
        logger.warning(f"Polygon financials failed for {sym}: {e}")
        return {"income": pd.DataFrame(), "balance": pd.DataFrame(), "cashflow": pd.DataFrame()}


@st.cache_data(ttl=86400, show_spinner=False)
def fetch_insider_transactions(symbol: str, limit: int = 20) -> pd.DataFrame:
    """Fetch insider transactions from SEC EDGAR (public domain, zero legal risk)."""
    sym = polygon_symbol(symbol).replace("X:", "").upper()
    try:
        # SEC EDGAR full-text search for Form 4 filings
        r = requests.get(
            "https://efts.sec.gov/LATEST/search-index",
            params={"q": sym, "forms": "4", "dateRange": "custom",
                    "startdt": (date.today() - timedelta(days=180)).isoformat(),
                    "enddt": date.today().isoformat()},
            headers={"User-Agent": "AIStatcharts/2.0 (jdmeyer05@gmail.com)"},
            timeout=10,
        )
        # Fallback: use Polygon insider transactions if available
        api_key = _get_polygon_key()
        if api_key:
            r2 = requests.get(
                f"https://api.polygon.io/vX/reference/insiders/transactions",
                params={"ticker": sym, "limit": limit, "apiKey": api_key},
                timeout=10,
            )
            if r2.status_code == 200:
                results = r2.json().get("results", [])
                if results:
                    rows = []
                    for txn in results:
                        rows.append({
                            "Date": txn.get("filing_date", ""),
                            "Insider": txn.get("name", ""),
                            "Title": txn.get("title", ""),
                            "Transaction": txn.get("transaction_type", ""),
                            "Shares": txn.get("shares", 0),
                            "Price": txn.get("price", 0),
                            "Value": txn.get("shares", 0) * txn.get("price", 0),
                        })
                    return pd.DataFrame(rows)
    except Exception as e:
        logger.warning(f"Insider transactions fetch failed for {sym}: {e}")
    return pd.DataFrame()


@st.cache_data(ttl=43200, show_spinner=False)
def fetch_analyst_recommendations(symbol: str) -> pd.DataFrame:
    """Fetch analyst recommendations from Finnhub (free tier, commercial use allowed)."""
    from src.api_keys import get_secret
    finnhub_key = get_secret("FINNHUB_API_KEY")
    if not finnhub_key:
        return pd.DataFrame()

    sym = polygon_symbol(symbol).replace("X:", "").upper()
    try:
        r = requests.get(
            f"https://finnhub.io/api/v1/stock/recommendation",
            params={"symbol": sym, "token": finnhub_key},
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            if data:
                df = pd.DataFrame(data)
                df = df.sort_values("period", ascending=False).head(12)
                return df
    except Exception as e:
        logger.warning(f"Analyst recommendations fetch failed for {sym}: {e}")
    return pd.DataFrame()


# FRED series for data Polygon can't provide (futures, indices, rates)
_FRED_FALLBACK_MAP = {
    "^VIX": "VIXCLS",
    "^OVX": "OVXCLS",
    "^TNX": "DGS10",
    "^TYX": "DGS30",
    "CL=F": "DCOILWTICO",
    "NG=F": "DHHNGSP",
    "GC=F": "GOLDPMGBD228NLBM",
    "SI=F": "SLVPRUSD",
    "DX-Y.NYB": "DTWEXBGS",
    "DX=F": "DTWEXBGS",
    "ZN=F": "DGS10",
    "ZB=F": "DGS30",
    "ZF=F": "DGS5",
    "ZT=F": "DGS2",
}


def _get_fred_key():
    from src.api_keys import get_secret
    return get_secret("FRED_API_KEY")


@st.cache_data(ttl=3600, show_spinner=False)
def _fred_latest(series_id: str) -> dict | None:
    """Fetch latest value + previous from FRED. Returns {price, prev_close} or None."""
    key = _get_fred_key()
    if not key:
        return None
    try:
        r = requests.get(
            "https://api.stlouisfed.org/fred/series/observations",
            params={"series_id": series_id, "api_key": key, "file_type": "json",
                    "sort_order": "desc", "limit": 5},
            timeout=10,
        )
        obs = r.json().get("observations", [])
        # Filter out "." placeholder values
        vals = [o for o in obs if o.get("value", ".") != "."]
        if len(vals) >= 2:
            return {"price": float(vals[0]["value"]), "prev_close": float(vals[1]["value"])}
        elif len(vals) == 1:
            return {"price": float(vals[0]["value"]), "prev_close": float(vals[0]["value"])}
    except Exception as e:
        logger.warning(f"FRED fetch failed for {series_id}: {e}")
    return None


@st.cache_data(ttl=3600, show_spinner=False)
def _fred_history(series_id: str, days: int) -> pd.DataFrame:
    """Fetch daily history from FRED. Returns DataFrame with Close column (same shape as polygon_history)."""
    key = _get_fred_key()
    if not key:
        return pd.DataFrame()
    try:
        end = date.today()
        start = end - timedelta(days=days)
        r = requests.get(
            "https://api.stlouisfed.org/fred/series/observations",
            params={
                "series_id": series_id, "api_key": key, "file_type": "json",
                "observation_start": start.isoformat(), "observation_end": end.isoformat(),
                "sort_order": "asc",
            },
            timeout=15,
        )
        obs = r.json().get("observations", [])
        rows = [{"Date": o["date"], "Close": float(o["value"])} for o in obs if o.get("value", ".") != "."]
        if rows:
            df = pd.DataFrame(rows)
            df["Date"] = pd.to_datetime(df["Date"])
            df.set_index("Date", inplace=True)
            return df
    except Exception as e:
        logger.warning(f"FRED history failed for {series_id}: {e}")
    return pd.DataFrame()


def _yfinance_snapshot(symbol: str) -> dict | None:
    """Get latest price via yfinance as a reliable fallback."""
    try:
        import yfinance as yf
        t = yf.Ticker(symbol)
        info = t.fast_info
        price = info.get("lastPrice") or info.get("last_price", 0)
        prev = info.get("previousClose") or info.get("previous_close", 0)
        if price and price > 0:
            return {"price": round(price, 4), "prev_close": round(prev, 4)}
    except Exception as e:
        logger.warning(f"yfinance snapshot failed for {symbol}: {e}")
    return None


# Sanity bounds for known symbols — if Polygon returns outside these, the data is bad
_PRICE_SANITY_BOUNDS = {
    "BZ=F": (40, 250),       # Brent crude: $40-250/bbl
    "CL=F": (30, 250),       # WTI crude: $30-250/bbl
    "NG=F": (1, 15),         # Henry Hub: $1-15/mmbtu
    "GC=F": (1500, 10000),   # Gold: $1500-10000/oz
    "DX-Y.NYB": (80, 115),   # DXY: 80-115
    "^VIX": (9, 90),         # VIX: 9-90
    "SI=F": (15, 100),       # Silver: $15-100/oz
}


def polygon_snapshot_with_fallback(symbol: str) -> dict | None:
    """Try Polygon first, validate with sanity bounds, fall back to yfinance then FRED."""
    snap = polygon_snapshot(symbol)

    # Sanity check: reject implausible prices or stale data (price == prev_close exactly)
    if snap and snap.get("price"):
        bounds = _PRICE_SANITY_BOUNDS.get(symbol)
        if bounds:
            low, high = bounds
            price = snap["price"]
            prev = snap.get("prev_close", 0)
            if not (low <= price <= high):
                logger.warning(
                    f"Polygon returned {symbol}={price} — outside sanity bounds "
                    f"[{low}, {high}]. Falling back to yfinance."
                )
                snap = None  # Force fallback
            elif price == prev and prev > 0:
                # price == prev_close exactly often means stale/weekend data
                logger.info(f"Polygon {symbol}={price} looks stale (price==prev_close). Trying yfinance.")
                yf_snap = _yfinance_snapshot(symbol)
                if yf_snap and yf_snap.get("price"):
                    return yf_snap
                # If yfinance also fails, keep Polygon data

    if snap and snap.get("price"):
        return snap

    # yfinance fallback — reliable for futures, indices, forex
    yf_snap = _yfinance_snapshot(symbol)
    if yf_snap:
        return yf_snap

    # FRED fallback for macro series
    fred_series = _FRED_FALLBACK_MAP.get(symbol)
    if fred_series:
        return _fred_latest(fred_series)
    return None


# ─────────────────────────────────────────────
# POLYGON TECHNICAL INDICATORS
# ─────────────────────────────────────────────

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_polygon_sma(symbol: str, window: int = 50, timespan: str = "day",
                       limit: int = 252) -> pd.DataFrame:
    """Fetch SMA from Polygon Technical Indicators API."""
    key = _get_polygon_key()
    if not key:
        return pd.DataFrame()
    try:
        symbol = format_massive_ticker(symbol)
        url = f"https://api.polygon.io/v1/indicators/sma/{symbol}"
        params = {"timespan": timespan, "window": window, "limit": limit,
                  "order": "desc", "apiKey": key}
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        results = r.json().get("results", {}).get("values", [])
        if not results:
            return pd.DataFrame()
        df = pd.DataFrame(results)
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        return df.set_index("timestamp").sort_index()
    except Exception as e:
        logger.warning(f"Polygon SMA failed for {symbol}: {e}")
        return pd.DataFrame()


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_polygon_rsi(symbol: str, window: int = 14, timespan: str = "day",
                       limit: int = 252) -> pd.DataFrame:
    """Fetch RSI from Polygon Technical Indicators API."""
    key = _get_polygon_key()
    if not key:
        return pd.DataFrame()
    try:
        symbol = format_massive_ticker(symbol)
        url = f"https://api.polygon.io/v1/indicators/rsi/{symbol}"
        params = {"timespan": timespan, "window": window, "limit": limit,
                  "order": "desc", "apiKey": key}
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        results = r.json().get("results", {}).get("values", [])
        if not results:
            return pd.DataFrame()
        df = pd.DataFrame(results)
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        return df.set_index("timestamp").sort_index()
    except Exception as e:
        logger.warning(f"Polygon RSI failed for {symbol}: {e}")
        return pd.DataFrame()


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_polygon_macd(symbol: str, short_window: int = 12, long_window: int = 26,
                        signal_window: int = 9, timespan: str = "day",
                        limit: int = 252) -> pd.DataFrame:
    """Fetch MACD from Polygon Technical Indicators API."""
    key = _get_polygon_key()
    if not key:
        return pd.DataFrame()
    try:
        symbol = format_massive_ticker(symbol)
        url = f"https://api.polygon.io/v1/indicators/macd/{symbol}"
        params = {"timespan": timespan, "short_window": short_window,
                  "long_window": long_window, "signal_window": signal_window,
                  "limit": limit, "order": "desc", "apiKey": key}
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        results = r.json().get("results", {}).get("values", [])
        if not results:
            return pd.DataFrame()
        df = pd.DataFrame(results)
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        return df.set_index("timestamp").sort_index()
    except Exception as e:
        logger.warning(f"Polygon MACD failed for {symbol}: {e}")
        return pd.DataFrame()


# ─────────────────────────────────────────────
# POLYGON CORPORATE ACTIONS
# ─────────────────────────────────────────────

@st.cache_data(ttl=86400, show_spinner=False)
def fetch_stock_splits(symbol: str) -> pd.DataFrame:
    """Fetch stock split history from Polygon."""
    key = _get_polygon_key()
    if not key:
        return pd.DataFrame()
    try:
        symbol = format_massive_ticker(symbol)
        url = "https://api.polygon.io/v3/reference/splits"
        params = {"ticker": symbol, "limit": 50, "apiKey": key}
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        results = r.json().get("results", [])
        if not results:
            return pd.DataFrame()
        df = pd.DataFrame(results)
        if "execution_date" in df.columns:
            df["execution_date"] = pd.to_datetime(df["execution_date"])
        return df
    except Exception as e:
        logger.warning(f"Polygon splits failed for {symbol}: {e}")
        return pd.DataFrame()


@st.cache_data(ttl=86400, show_spinner=False)
def fetch_dividends(symbol: str, limit: int = 50) -> pd.DataFrame:
    """Fetch dividend history from Polygon."""
    key = _get_polygon_key()
    if not key:
        return pd.DataFrame()
    try:
        symbol = format_massive_ticker(symbol)
        url = "https://api.polygon.io/v3/reference/dividends"
        params = {"ticker": symbol, "limit": limit, "order": "desc", "apiKey": key}
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        results = r.json().get("results", [])
        if not results:
            return pd.DataFrame()
        df = pd.DataFrame(results)
        for col in ["ex_dividend_date", "pay_date", "declaration_date"]:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors="coerce")
        return df
    except Exception as e:
        logger.warning(f"Polygon dividends failed for {symbol}: {e}")
        return pd.DataFrame()


# ─────────────────────────────────────────────
# POLYGON INTRADAY (MINUTE AGGREGATES)
# ─────────────────────────────────────────────

@st.cache_data(ttl=300, show_spinner=False)
def fetch_minute_bars(symbol: str, days_back: int = 1) -> pd.DataFrame:
    """Fetch minute-level OHLCV bars from Polygon.

    Returns DataFrame with columns: Open, High, Low, Close, Volume, VWAP.
    """
    key = _get_polygon_key()
    if not key:
        return pd.DataFrame()
    try:
        symbol = format_massive_ticker(symbol)
        end = date.today()
        start = end - timedelta(days=days_back)
        url = f"https://api.polygon.io/v2/aggs/ticker/{symbol}/range/1/minute/{start}/{end}"
        params = {"adjusted": "true", "sort": "asc", "limit": 50000, "apiKey": key}
        r = requests.get(url, params=params, timeout=20)
        r.raise_for_status()
        results = r.json().get("results", [])
        if not results:
            return pd.DataFrame()
        df = pd.DataFrame(results)
        df["timestamp"] = pd.to_datetime(df["t"], unit="ms")
        df = df.rename(columns={"o": "Open", "h": "High", "l": "Low",
                                  "c": "Close", "v": "Volume", "vw": "VWAP"})
        return df.set_index("timestamp")[["Open", "High", "Low", "Close", "Volume", "VWAP"]].sort_index()
    except Exception as e:
        logger.warning(f"Polygon minute bars failed for {symbol}: {e}")
        return pd.DataFrame()


# ─────────────────────────────────────────────
# POLYGON OPTIONS — HISTORICAL OI
# ─────────────────────────────────────────────

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_options_oi_history(symbol: str, strike: float, expiration: str,
                              contract_type: str = "call", days: int = 30) -> pd.DataFrame:
    """Fetch daily open interest history for a specific options contract.

    Uses Polygon options aggregate endpoint to get daily OI over time.
    """
    key = _get_polygon_key()
    if not key:
        return pd.DataFrame()
    try:
        symbol = format_massive_ticker(symbol)
        # Build options ticker: O:AAPL260620C00200000
        exp_fmt = pd.to_datetime(expiration).strftime("%y%m%d")
        ct = "C" if contract_type.lower() == "call" else "P"
        strike_fmt = f"{round(strike * 1000):08d}"
        options_ticker = f"O:{symbol}{exp_fmt}{ct}{strike_fmt}"

        end = date.today()
        start = end - timedelta(days=days)
        url = f"https://api.polygon.io/v2/aggs/ticker/{options_ticker}/range/1/day/{start}/{end}"
        params = {"adjusted": "true", "sort": "asc", "limit": 50000, "apiKey": key}
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        results = r.json().get("results", [])
        if not results:
            return pd.DataFrame()
        df = pd.DataFrame(results)
        df["date"] = pd.to_datetime(df["t"], unit="ms")
        df = df.rename(columns={"o": "open", "h": "high", "l": "low",
                                  "c": "close", "v": "volume"})
        return df.set_index("date")[["open", "high", "low", "close", "volume"]].sort_index()
    except Exception as e:
        logger.warning(f"Options OI history failed for {symbol}: {e}")
        return pd.DataFrame()


# ─────────────────────────────────────────────
# POLYGON OPTIONS — HISTORICAL SURFACE (bulk daily aggs)
# ─────────────────────────────────────────────

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_options_surface_history(symbol: str, days: int = 10, max_contracts: int = 200) -> dict:
    """Fetch daily close prices for the current options chain over the past N days.

    Returns dict keyed by date string, each value is a list of dicts:
        [{"strike": 200, "dte": 30, "type": "call", "close": 5.50, "exp": "2026-06-19"}, ...]

    Uses Polygon v3/snapshot for current chain (to get contract list),
    then v2/aggs for each contract's daily history. Parallelized with ThreadPoolExecutor.
    """
    key = _get_polygon_key()
    if not key:
        return {}

    from concurrent.futures import ThreadPoolExecutor, as_completed

    symbol = format_massive_ticker(symbol)

    # Step 1: Get current chain to identify contracts
    chain = fetch_options_chain(symbol, expiration=None, max_pages=10)
    if chain is None or chain.empty:
        return {}

    # Filter to near-the-money contracts with meaningful OI
    px_df = fetch_massive_data(symbol, 5)
    spot_now = float(px_df["Close"].iloc[-1]) if px_df is not None and not px_df.empty else None
    if not spot_now:
        return {}

    # Filter: ±25% of spot, OI > 10, DTE > 0 and < 180
    chain = chain[
        (chain["strike_price"] >= spot_now * 0.80) &
        (chain["strike_price"] <= spot_now * 1.20) &
        (chain["open_interest"] > 10)
    ].copy()
    chain["dte"] = (pd.to_datetime(chain["expiration_date"]) - pd.Timestamp.now()).dt.days
    chain = chain[(chain["dte"] > 0) & (chain["dte"] <= 180)]

    # Sample if too many contracts
    if len(chain) > max_contracts:
        chain = chain.nlargest(max_contracts, "open_interest")

    # Build Polygon option ticker for each contract
    def _build_ticker(row):
        exp_fmt = pd.to_datetime(row["expiration_date"]).strftime("%y%m%d")
        ct = "C" if str(row["contract_type"]).lower() == "call" else "P"
        strike_fmt = f"{int(row['strike_price'] * 1000):08d}"
        return f"O:{symbol}{exp_fmt}{ct}{strike_fmt}"

    chain["opt_ticker"] = chain.apply(_build_ticker, axis=1)

    # Step 2: Fetch daily aggs for each contract in parallel
    end_date = date.today()
    start_date = end_date - timedelta(days=days + 5)  # extra buffer for weekends

    def _fetch_one(row):
        try:
            url = f"https://api.polygon.io/v2/aggs/ticker/{row['opt_ticker']}/range/1/day/{start_date}/{end_date}"
            r = requests.get(url, params={"adjusted": "true", "sort": "asc", "limit": 50, "apiKey": key}, timeout=10)
            if r.status_code != 200:
                return []
            results = r.json().get("results", [])
            out = []
            for bar in results:
                bar_date = pd.to_datetime(bar["t"], unit="ms").strftime("%Y-%m-%d")
                out.append({
                    "date": bar_date,
                    "strike": float(row["strike_price"]),
                    "type": row["contract_type"],
                    "exp": row["expiration_date"],
                    "close": bar.get("c", 0),
                    "volume": bar.get("v", 0),
                })
            return out
        except Exception:
            return []

    all_bars = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(_fetch_one, row): row for _, row in chain.iterrows()}
        for future in as_completed(futures):
            all_bars.extend(future.result())

    if not all_bars:
        return {}

    # Step 3: Group by date
    df_bars = pd.DataFrame(all_bars)
    # Get underlying spot for each date
    spot_hist = fetch_massive_data(symbol, days + 10)
    spot_by_date = {}
    if spot_hist is not None and not spot_hist.empty:
        for d in spot_hist.index:
            spot_by_date[d.strftime("%Y-%m-%d")] = float(spot_hist.loc[d, "Close"])

    result = {}
    for dt, group in df_bars.groupby("date"):
        day_spot = spot_by_date.get(dt)
        if not day_spot:
            continue
        rows = []
        for _, r in group.iterrows():
            dte = max((pd.to_datetime(r["exp"]) - pd.to_datetime(dt)).days, 0)
            if dte <= 0 or r["close"] <= 0:
                continue
            rows.append({
                "strike": r["strike"], "dte": dte, "type": r["type"],
                "exp": r["exp"], "close": r["close"],
            })
        if len(rows) >= 20:
            result[dt] = {"spot": day_spot, "data": rows}

    return result


@st.cache_data(ttl=300, show_spinner=False)
def fetch_options_trades(symbol: str, expiration: str = None, limit: int = 500) -> pd.DataFrame:
    """Fetch recent options trades (tick-level) from Polygon v3/trades endpoint.

    Returns DataFrame with: timestamp, price, size, exchange, conditions, contract_ticker,
    strike, contract_type, expiration.

    Uses Options Starter plan. Rate-limited — use sparingly.
    """
    key = _get_polygon_key()
    if not key:
        return pd.DataFrame()

    symbol = format_massive_ticker(symbol)

    # First get the chain to know contract tickers
    chain = fetch_options_chain(symbol, expiration, max_pages=5)
    if chain is None or chain.empty:
        return pd.DataFrame()

    # Filter to liquid contracts (top by volume)
    chain = chain[chain["volume"] > 0].nlargest(min(50, len(chain)), "volume")

    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _build_opt_ticker(row):
        exp_fmt = pd.to_datetime(row["expiration_date"]).strftime("%y%m%d")
        ct = "C" if str(row["contract_type"]).lower() == "call" else "P"
        strike_fmt = f"{round(row['strike_price'] * 1000):08d}"
        return f"O:{symbol}{exp_fmt}{ct}{strike_fmt}"

    chain = chain.copy()
    chain["opt_ticker"] = chain.apply(_build_opt_ticker, axis=1)

    def _fetch_trades(opt_ticker, strike, ctype, exp):
        try:
            url = f"https://api.polygon.io/v3/trades/{opt_ticker}"
            r = requests.get(url, params={"limit": 50, "order": "desc", "sort": "timestamp", "apiKey": key}, timeout=10)
            if r.status_code != 200:
                return []
            results = r.json().get("results", [])
            out = []
            for t in results:
                out.append({
                    "timestamp": pd.to_datetime(t.get("sip_timestamp", 0), unit="ns"),
                    "price": t.get("price", 0),
                    "size": t.get("size", 0),
                    "exchange": t.get("exchange", 0),
                    "conditions": t.get("conditions", []),
                    "contract_ticker": opt_ticker,
                    "strike": strike,
                    "contract_type": ctype,
                    "expiration": exp,
                })
            return out
        except Exception:
            return []

    all_trades = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {}
        for _, row in chain.head(30).iterrows():  # Limit to top 30 contracts by volume
            otk = row["opt_ticker"]
            futures[executor.submit(_fetch_trades, otk, row["strike_price"],
                                     row["contract_type"], row["expiration_date"])] = otk
        for fut in as_completed(futures):
            all_trades.extend(fut.result())

    if not all_trades:
        return pd.DataFrame()

    df = pd.DataFrame(all_trades)
    df = df.sort_values("timestamp", ascending=False).head(limit)
    return df


# ─────────────────────────────────────────────
# POLYGON — GROUPED DAILY, NEWS, RELATED, FUNDAMENTALS, SNAPSHOTS, EMA
# ─────────────────────────────────────────────

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_grouped_daily(date_str: str) -> pd.DataFrame:
    """Fetch daily bars for ALL US tickers on a given date (one API call).

    Returns DataFrame with ticker, open, high, low, close, volume, vwap.
    """
    key = _get_polygon_key()
    if not key:
        return pd.DataFrame()
    try:
        url = f"https://api.polygon.io/v2/aggs/grouped/locale/us/market/stocks/{date_str}"
        params = {"adjusted": "true", "apiKey": key}
        r = requests.get(url, params=params, timeout=20)
        r.raise_for_status()
        results = r.json().get("results", [])
        if not results:
            return pd.DataFrame()
        df = pd.DataFrame(results)
        df = df.rename(columns={"T": "ticker", "o": "open", "h": "high", "l": "low",
                                  "c": "close", "v": "volume", "vw": "vwap"})
        return df
    except Exception as e:
        logger.warning(f"Grouped daily fetch failed for {date_str}: {e}")
        return pd.DataFrame()


@st.cache_data(ttl=1800, show_spinner=False)
def fetch_ticker_news(ticker: str, limit: int = 10) -> list[dict]:
    """Fetch recent news articles for a ticker from Polygon.

    Returns list of dicts with: title, author, published_utc, article_url, description.
    """
    key = _get_polygon_key()
    if not key:
        return []
    try:
        ticker = format_massive_ticker(ticker)
        url = "https://api.polygon.io/v2/reference/news"
        params = {"ticker": ticker, "limit": limit, "order": "desc",
                  "sort": "published_utc", "apiKey": key}
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        results = r.json().get("results", [])
        return [{
            "title": a.get("title", ""),
            "author": a.get("author", ""),
            "published": a.get("published_utc", ""),
            "url": a.get("article_url", ""),
            "description": a.get("description", ""),
            "keywords": a.get("keywords", []),
        } for a in results]
    except Exception as e:
        logger.warning(f"Ticker news failed for {ticker}: {e}")
        return []


@st.cache_data(ttl=86400, show_spinner=False)
def fetch_related_companies(ticker: str) -> list[str]:
    """Fetch related/peer company tickers from Polygon."""
    key = _get_polygon_key()
    if not key:
        return []
    try:
        ticker = format_massive_ticker(ticker)
        url = f"https://api.polygon.io/v1/related-companies/{ticker}"
        params = {"apiKey": key}
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        results = r.json().get("results", [])
        return [c.get("ticker", "") for c in results if c.get("ticker")]
    except Exception as e:
        logger.warning(f"Related companies failed for {ticker}: {e}")
        return []


@st.cache_data(ttl=86400, show_spinner=False)
def fetch_polygon_financials(ticker: str, timeframe: str = "quarterly",
                              limit: int = 8) -> pd.DataFrame:
    """Fetch standardized financials from Polygon vX endpoint.

    Returns DataFrame with income statement, balance sheet, cash flow fields.
    """
    key = _get_polygon_key()
    if not key:
        return pd.DataFrame()
    try:
        ticker = format_massive_ticker(ticker)
        url = "https://api.polygon.io/vX/reference/financials"
        params = {"ticker": ticker, "timeframe": timeframe, "limit": limit,
                  "order": "desc", "sort": "period_of_report_date", "apiKey": key}
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        results = r.json().get("results", [])
        if not results:
            return pd.DataFrame()
        rows = []
        for filing in results:
            row = {
                "period": filing.get("fiscal_period", ""),
                "fiscal_year": filing.get("fiscal_year", ""),
                "filing_date": filing.get("filing_date", ""),
                "period_date": filing.get("start_date", ""),
            }
            # Income statement
            inc = filing.get("financials", {}).get("income_statement", {})
            for field in ["revenues", "net_income_loss", "gross_profit",
                          "operating_income_loss", "basic_earnings_per_share"]:
                val = inc.get(field, {})
                row[field] = val.get("value") if isinstance(val, dict) else val
            # Balance sheet
            bs = filing.get("financials", {}).get("balance_sheet", {})
            for field in ["assets", "liabilities", "equity",
                          "current_assets", "current_liabilities"]:
                val = bs.get(field, {})
                row[field] = val.get("value") if isinstance(val, dict) else val
            # Cash flow
            cf = filing.get("financials", {}).get("cash_flow_statement", {})
            for field in ["net_cash_flow", "net_cash_flow_from_operating_activities",
                          "net_cash_flow_from_investing_activities"]:
                val = cf.get(field, {})
                row[field] = val.get("value") if isinstance(val, dict) else val
            rows.append(row)
        return pd.DataFrame(rows)
    except Exception as e:
        logger.warning(f"Polygon financials failed for {ticker}: {e}")
        return pd.DataFrame()


@st.cache_data(ttl=300, show_spinner=False)
def fetch_universal_snapshot(tickers: list[str]) -> pd.DataFrame:
    """Fetch snapshots for multiple tickers in one API call.

    Returns DataFrame with ticker, price, change, changePercent, volume, vwap.
    """
    key = _get_polygon_key()
    if not key:
        return pd.DataFrame()
    try:
        formatted = [format_massive_ticker(t) for t in tickers]
        url = "https://api.polygon.io/v3/snapshot"
        params = {"ticker.any_of": ",".join(formatted), "apiKey": key}
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        results = r.json().get("results", [])
        if not results:
            return pd.DataFrame()
        rows = []
        for snap in results:
            session = snap.get("session", {})
            rows.append({
                "ticker": snap.get("ticker", ""),
                "price": session.get("close", session.get("price")),
                "change": session.get("change"),
                "change_pct": session.get("change_percent"),
                "volume": session.get("volume"),
                "vwap": session.get("vwap"),
                "prev_close": session.get("previous_close"),
            })
        return pd.DataFrame(rows)
    except Exception as e:
        logger.warning(f"Universal snapshot failed: {e}")
        return pd.DataFrame()


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_open_close(ticker: str, date_str: str) -> dict:
    """Fetch open, close, pre-market, after-hours prices for a specific date."""
    key = _get_polygon_key()
    if not key:
        return {}
    try:
        ticker = format_massive_ticker(ticker)
        url = f"https://api.polygon.io/v1/open-close/{ticker}/{date_str}"
        params = {"adjusted": "true", "apiKey": key}
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        return {
            "open": data.get("open"),
            "high": data.get("high"),
            "low": data.get("low"),
            "close": data.get("close"),
            "volume": data.get("volume"),
            "pre_market": data.get("preMarket"),
            "after_hours": data.get("afterHours"),
        }
    except Exception as e:
        logger.warning(f"Open/close failed for {ticker} {date_str}: {e}")
        return {}


@st.cache_data(ttl=300, show_spinner=False)
def fetch_market_status() -> dict:
    """Fetch current market open/closed status from Polygon."""
    key = _get_polygon_key()
    if not key:
        return {}
    try:
        url = "https://api.polygon.io/v1/marketstatus/now"
        params = {"apiKey": key}
        r = requests.get(url, params=params, timeout=3)  # fast timeout — header can't block
        r.raise_for_status()
        data = r.json()
        return {
            "market": data.get("market", "unknown"),
            "exchanges": data.get("exchanges", {}),
            "server_time": data.get("serverTime"),
            "is_open": data.get("market") == "open",
        }
    except Exception as e:
        logger.warning(f"Market status failed: {e}")
        return {}


@st.cache_data(ttl=86400, show_spinner=False)
def fetch_ticker_details(ticker: str) -> dict:
    """Fetch company details from Polygon: name, description, sector, logo, etc."""
    key = _get_polygon_key()
    if not key:
        return {}
    try:
        ticker = format_massive_ticker(ticker)
        url = f"https://api.polygon.io/v3/reference/tickers/{ticker}"
        params = {"apiKey": key}
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        result = r.json().get("results", {})
        return {
            "name": result.get("name"),
            "description": result.get("description"),
            "sic_code": result.get("sic_code"),
            "sic_description": result.get("sic_description"),
            "market_cap": result.get("market_cap"),
            "homepage": result.get("homepage_url"),
            "locale": result.get("locale"),
            "exchange": result.get("primary_exchange"),
            "list_date": result.get("list_date"),
            "shares_outstanding": result.get("weighted_shares_outstanding"),
            "logo": result.get("branding", {}).get("icon_url"),
        }
    except Exception as e:
        logger.warning(f"Ticker details failed for {ticker}: {e}")
        return {}


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_polygon_ema(symbol: str, window: int = 20, timespan: str = "day",
                       limit: int = 252) -> pd.DataFrame:
    """Fetch EMA from Polygon Technical Indicators API."""
    key = _get_polygon_key()
    if not key:
        return pd.DataFrame()
    try:
        symbol = format_massive_ticker(symbol)
        url = f"https://api.polygon.io/v1/indicators/ema/{symbol}"
        params = {"timespan": timespan, "window": window, "limit": limit,
                  "order": "desc", "apiKey": key}
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        results = r.json().get("results", {}).get("values", [])
        if not results:
            return pd.DataFrame()
        df = pd.DataFrame(results)
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        return df.set_index("timestamp").sort_index()
    except Exception as e:
        logger.warning(f"Polygon EMA failed for {symbol}: {e}")
        return pd.DataFrame()


def render_data_source_footer():
    st.markdown("---")
    source = st.session_state.get('current_data_source', 'No data loaded yet')
    st.caption(f"📡 **Active Data Source:** `{source}`")

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


@st.cache_data(ttl=300, show_spinner=False)
def polygon_batch_snapshot(symbols: list) -> dict:
    """Get price snapshots for multiple symbols via Polygon all-tickers snapshot.
    Returns {original_symbol: {price, prev_close, change}}."""
    api_key = _get_polygon_key()
    results = {}
    if not api_key:
        return results

    # Try the bulk snapshot endpoint first (one API call for all tickers)
    try:
        # Build a set of polygon-formatted symbols for matching
        sym_map = {polygon_symbol(s): s for s in symbols}
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
@st.cache_data(ttl=3600, show_spinner="Fetching market data...")
def fetch_massive_data(symbol: str, days: int) -> pd.DataFrame:
    end_date = date.today()
    start_date = end_date - timedelta(days=days)
    formatted_symbol = format_massive_ticker(symbol)
    
    api_key = _get_polygon_key()
    if not api_key:
        logger.error("No MASSIVE_API_KEY configured")
        return None
    try:
        url = f"https://api.polygon.io/v2/aggs/ticker/{formatted_symbol}/range/1/day/{start_date.strftime('%Y-%m-%d')}/{end_date.strftime('%Y-%m-%d')}"
        res = requests.get(url, params={"apiKey": api_key, "sort": "asc", "limit": 50000}, timeout=30)
        res.raise_for_status()
        data = res.json()
        if 'results' in data:
            st.session_state['current_data_source'] = "Massive API (Live Feed)"
            df = pd.DataFrame(data['results'])
            df['Date'] = pd.to_datetime(df['t'], unit='ms')
            df.set_index('Date', inplace=True)
            return df[['c']].rename(columns={'c': 'Close'})
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


@st.cache_data(ttl=3600, show_spinner="Fetching options chain from Massive...")
def fetch_options_chain(symbol: str, expiration: str = None, max_pages: int = 20) -> pd.DataFrame:
    """Fetches full options chain with Greeks from Massive (Polygon), yfinance fallback.
    Pass expiration=None to fetch across ALL expirations (for vol surfaces)."""
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

                    # Bid/ask: try live quote first, fall back to day close ±1%
                    bid = quote.get('bid') or 0
                    ask = quote.get('ask') or 0
                    day_close = day.get('close', 0) or 0
                    day_vwap = day.get('vwap', 0) or 0

                    if bid == 0 and day_close > 0:
                        bid = day_close * 0.99
                    if ask == 0 and day_close > 0:
                        ask = day_close * 1.01

                    last_price = day_close or day_vwap or 0

                    rows.append({
                        'strike_price': d.get('strike_price'),
                        'contract_type': d.get('contract_type'),
                        'expiration_date': d.get('expiration_date'),
                        'bid': bid,
                        'ask': ask,
                        'last_price': last_price,
                        'volume': day.get('volume', 0),
                        'open_interest': r.get('open_interest', 0),
                        'implied_volatility': r.get('implied_volatility', 0),
                        'delta': g.get('delta', 0),
                        'gamma': g.get('gamma', 0),
                        'theta': g.get('theta', 0),
                        'vega': g.get('vega', 0),
                    })
                df = pd.DataFrame(rows)
                st.session_state['current_data_source'] = "Massive API (Polygon)"
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


def render_data_source_footer():
    st.markdown("---")
    source = st.session_state.get('current_data_source', 'No data loaded yet')
    st.caption(f"📡 **Active Data Source:** `{source}`")

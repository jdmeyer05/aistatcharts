"""SEC EDGAR + Smart Money endpoints — 13F, insider, 8-K, 13D, congressional, guidance, earnings calendar."""

import logging
import math

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from api.deps import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()


def _clean(v):
    if v is None:
        return None
    # pd.NaT is-a Timestamp and numeric-NaN floats both need null coercion
    # before the isinstance branches, otherwise strftime yields the string "NaT".
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, (np.floating, float)):
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    if isinstance(v, (np.integer, int)):
        return int(v)
    if isinstance(v, pd.Timestamp):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, np.ndarray):
        return [_clean(x) for x in v.tolist()]
    return v


def _records(df: pd.DataFrame) -> list[dict]:
    if df is None or df.empty:
        return []
    rows = df.to_dict(orient="records")
    return [{k: _clean(v) for k, v in r.items()} for r in rows]


@router.get("/13f/{cik}")
async def get_13f_holdings(
    cik: str,
    user: str = Depends(get_current_user),
):
    """Fetch 13F institutional holdings for a fund by CIK."""
    from src.edgar import fetch_13f_from_xml
    df = fetch_13f_from_xml(cik)
    if df is None or df.empty:
        return {"cik": cik, "count": 0, "holdings": [], "filing_date": None}
    filing_date = df["filing_date"].iloc[0] if "filing_date" in df.columns else None
    return {
        "cik": cik,
        "count": len(df),
        "filing_date": filing_date,
        "holdings": _records(df),
    }


@router.get("/insider/{ticker}")
async def get_insider_transactions(
    ticker: str,
    user: str = Depends(get_current_user),
):
    """Fetch recent Form 4 insider transactions via yfinance.

    Polygon's experimental insiders endpoint returns empty for most major
    tickers on our current tier; yfinance scrapes the same SEC Form 4 data
    from Yahoo Finance and returns the richer schema (Start Date, Insider,
    Position, Transaction, Shares, Value, Text) that the Smart Money frontend
    relies on.
    """
    import yfinance as yf
    try:
        txns = yf.Ticker(ticker.upper()).insider_transactions
        logger.warning(f"[insider] {ticker}: txns is None={txns is None}, empty={txns is None or txns.empty}, len={0 if txns is None or txns.empty else len(txns)}")
    except Exception as e:
        logger.warning(f"[insider] yfinance failed for {ticker}: {e}")
        return {"ticker": ticker, "count": 0, "data": []}
    if txns is None or txns.empty:
        return {"ticker": ticker, "count": 0, "data": []}
    txns = txns.head(50).copy()
    for col in txns.columns:
        if str(txns[col].dtype).startswith("datetime"):
            txns[col] = txns[col].dt.strftime("%Y-%m-%d")
    return {"ticker": ticker, "count": len(txns), "data": _records(txns)}


@router.get("/8k/{ticker}")
async def get_8k_events(
    ticker: str,
    days: int = Query(30, ge=1, le=365),
    user: str = Depends(get_current_user),
):
    """Fetch recent 8-K filings for a ticker (default 30 days)."""
    from src.edgar import fetch_recent_8k
    filings = fetch_recent_8k(ticker.upper(), days=days)
    if not filings:
        return {"ticker": ticker, "count": 0, "data": []}
    return {"ticker": ticker, "count": len(filings), "data": filings}


@router.get("/13d")
async def get_recent_13d(
    days: int = Query(90, ge=7, le=365),
    user: str = Depends(get_current_user),
):
    """Fetch recent 13D activist investor filings."""
    from src.edgar import fetch_recent_13d
    df = fetch_recent_13d(days=days)
    if df is None or df.empty:
        return {"days": days, "count": 0, "data": []}
    return {"days": days, "count": len(df), "data": _records(df)}


@router.get("/congressional-trades")
async def get_congressional_trades(
    year: int | None = Query(None),
    max_filings: int = Query(50, ge=10, le=500),
    user: str = Depends(get_current_user),
):
    """Parsed congressional stock trades (STOCK Act PTR filings)."""
    from src.edgar import fetch_parsed_congressional_trades
    df = fetch_parsed_congressional_trades(year=year, max_filings=max_filings)
    if df is None or df.empty:
        return {"year": year, "count": 0, "data": []}
    return {"year": year, "count": len(df), "data": _records(df)}


@router.get("/funds")
async def get_tracked_funds(user: str = Depends(get_current_user)):
    """Get list of tracked institutional funds with CIKs."""
    from src.edgar import TRACKED_FUNDS
    return {"funds": [{"name": name, "cik": cik} for name, cik in TRACKED_FUNDS.items()]}


@router.get("/shorts/{ticker}")
async def get_short_interest(
    ticker: str,
    user: str = Depends(get_current_user),
):
    """Short interest snapshot for a single ticker via yfinance.

    yfinance.Ticker(tk).info pulls Yahoo's latest reported short interest —
    shares short, float %, short ratio (days to cover). FINRA publishes
    biweekly; Yahoo tracks those with a short lag.
    """
    import yfinance as yf
    try:
        info = yf.Ticker(ticker.upper()).info
    except Exception as e:
        logger.warning(f"yfinance short lookup failed for {ticker}: {e}")
        return {"ticker": ticker, "ok": False, "error": str(e)}

    def _num(v):
        try:
            n = float(v)
            if math.isnan(n) or math.isinf(n):
                return None
            return n
        except (TypeError, ValueError):
            return None

    return {
        "ticker": ticker.upper(),
        "ok": True,
        "name": info.get("shortName") or info.get("longName"),
        "price": _num(info.get("currentPrice") or info.get("regularMarketPrice")),
        "market_cap": _num(info.get("marketCap")),
        "float_shares": _num(info.get("floatShares")),
        "shares_short": _num(info.get("sharesShort")),
        "shares_short_prior": _num(info.get("sharesShortPriorMonth")),
        "short_ratio": _num(info.get("shortRatio")),  # days to cover
        "short_pct_float": _num(info.get("shortPercentOfFloat")),
        "short_pct_outstanding": _num(info.get("sharesPercentSharesOut")),
        "avg_volume_10d": _num(info.get("averageDailyVolume10Day")),
        "last_updated": info.get("dateShortInterest"),
    }


@router.get("/shorts-watchlist")
async def get_shorts_watchlist(
    user: str = Depends(get_current_user),
):
    """Short interest summary for a curated watchlist of frequently-squeezed
    or heavily-shorted names. Runs yfinance lookups in parallel.
    """
    import yfinance as yf
    from concurrent.futures import ThreadPoolExecutor

    # Mix of meme/squeeze candidates, biotechs, and a few benchmark mega-caps
    # for context. Worth reviewing quarterly — short leaders rotate.
    WATCHLIST = [
        "GME", "AMC", "BBBYQ", "TSLA", "NVDA",
        "PLTR", "BYND", "UPST", "CVNA", "SOFI",
        "FUBO", "LCID", "RIVN", "NKLA", "MARA",
        "RIOT", "COIN", "AFRM", "W", "ROKU",
        "SNAP", "PINS", "PTON", "DKNG", "HOOD",
    ]

    def _fetch(tk: str):
        try:
            info = yf.Ticker(tk).info
            return {
                "ticker": tk,
                "name": info.get("shortName") or info.get("longName"),
                "price": info.get("currentPrice") or info.get("regularMarketPrice"),
                "market_cap": info.get("marketCap"),
                "short_pct_float": info.get("shortPercentOfFloat"),
                "short_ratio": info.get("shortRatio"),
                "shares_short": info.get("sharesShort"),
                "shares_short_prior": info.get("sharesShortPriorMonth"),
            }
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = [r for r in pool.map(_fetch, WATCHLIST) if r]

    cleaned = []
    for r in rows:
        out = {}
        for k, v in r.items():
            if isinstance(v, (np.floating, float)):
                f = float(v)
                out[k] = None if (math.isnan(f) or math.isinf(f)) else f
            else:
                out[k] = v
        cleaned.append(out)
    return {"count": len(cleaned), "data": cleaned}


@router.get("/buybacks/{ticker}")
async def get_buybacks(
    ticker: str,
    user: str = Depends(get_current_user),
):
    """Quarterly and annual share repurchase history via yfinance cashflow.

    yfinance cashflow reports "Repurchase of Capital Stock" (or similar) as a
    negative line item — we convert to positive dollars returned to
    shareholders. Paired with market cap for buyback-yield context.
    """
    import yfinance as yf
    try:
        t = yf.Ticker(ticker.upper())
        info = t.info
        # yfinance exposes .quarterly_cashflow and .cashflow (annual)
        q_cf = t.quarterly_cashflow
        a_cf = t.cashflow
    except Exception as e:
        logger.warning(f"yfinance buybacks failed for {ticker}: {e}")
        return {"ticker": ticker, "ok": False, "error": str(e)}

    REPURCHASE_KEYS = [
        "Repurchase Of Capital Stock",
        "Repurchase of Stock",
        "Purchase Of Stock",
        "Net Common Stock Issuance",  # negative when net buyer
        "Common Stock Issuance",
    ]
    DIVIDEND_KEYS = [
        "Cash Dividends Paid",
        "Common Stock Dividend Paid",
    ]

    def _extract(cf_df):
        """Pull repurchase + dividend rows out of the cashflow frame.
        yfinance transposes rows/cols — rows are line items, cols are periods."""
        rows = []
        if cf_df is None or cf_df.empty:
            return rows
        idx_norm = {str(i).strip().lower(): i for i in cf_df.index}
        def _find(keys):
            for k in keys:
                lk = k.lower()
                if lk in idx_norm:
                    return idx_norm[lk]
            return None
        rep_idx = _find(REPURCHASE_KEYS)
        div_idx = _find(DIVIDEND_KEYS)
        for col in cf_df.columns:
            date_str = col.strftime("%Y-%m-%d") if hasattr(col, "strftime") else str(col)
            rec = {"period": date_str, "repurchase": None, "dividend": None}
            if rep_idx is not None:
                v = cf_df.loc[rep_idx, col]
                if pd.notna(v):
                    rec["repurchase"] = -float(v)  # yfinance reports as negative outflow
            if div_idx is not None:
                v = cf_df.loc[div_idx, col]
                if pd.notna(v):
                    rec["dividend"] = -float(v)
            rows.append(rec)
        return rows

    quarterly = _extract(q_cf)
    annual = _extract(a_cf)

    market_cap = info.get("marketCap")
    ttm_repurchase = sum(r["repurchase"] or 0 for r in quarterly[:4])
    ttm_dividend = sum(r["dividend"] or 0 for r in quarterly[:4])
    buyback_yield = (ttm_repurchase / market_cap) if market_cap and market_cap > 0 else None
    dividend_yield = (ttm_dividend / market_cap) if market_cap and market_cap > 0 else None

    return {
        "ticker": ticker.upper(),
        "ok": True,
        "name": info.get("shortName") or info.get("longName"),
        "market_cap": market_cap,
        "ttm_repurchase": ttm_repurchase,
        "ttm_dividend": ttm_dividend,
        "buyback_yield": buyback_yield,
        "dividend_yield": dividend_yield,
        "total_shareholder_yield": (
            (buyback_yield or 0) + (dividend_yield or 0)
            if (buyback_yield is not None or dividend_yield is not None) else None
        ),
        "quarterly": quarterly,
        "annual": annual,
    }


@router.get("/global-funds")
async def get_global_funds(user: str = Depends(get_current_user)):
    """Sovereign wealth + public pensions + endowments with CIKs and categories."""
    from src.edgar import GLOBAL_TRACKED_FUNDS
    return {
        "funds": [
            {"name": name, "cik": meta["cik"], "category": meta["category"], "country": meta["country"]}
            for name, meta in GLOBAL_TRACKED_FUNDS.items()
        ]
    }


# ─── Company Guidance ─────────────────────────────────────────────


@router.get("/guidance/{ticker}")
async def get_guidance_history(
    ticker: str,
    num_quarters: int = Query(6, ge=2, le=12),
    user: str = Depends(get_current_user),
):
    """Parsed forward guidance from recent 8-K earnings press releases."""
    from src.edgar import fetch_guidance_history
    df = fetch_guidance_history(ticker.upper(), num_quarters=num_quarters)
    if df is None or df.empty:
        return {"ticker": ticker, "count": 0, "data": []}
    return {"ticker": ticker, "count": len(df), "data": _records(df)}


@router.get("/transcript-urls/{ticker}")
async def discover_transcript_urls(
    ticker: str,
    limit: int = Query(4, ge=1, le=10),
    user: str = Depends(get_current_user),
):
    """Auto-discover Motley Fool earnings call transcript URLs for a ticker."""
    from src.edgar import discover_fool_transcript_urls
    urls = discover_fool_transcript_urls(ticker.upper(), limit=limit)
    return {"ticker": ticker, "count": len(urls), "urls": urls}


class TranscriptGuidanceBody(BaseModel):
    ticker: str
    urls: list[str]


@router.post("/transcript-guidance")
async def get_transcript_guidance(
    body: TranscriptGuidanceBody,
    user: str = Depends(get_current_user),
):
    """Parse earnings-call transcript URLs for forward guidance."""
    from src.edgar import fetch_transcript_guidance
    df = fetch_transcript_guidance(body.ticker.upper(), body.urls)
    if df is None or df.empty:
        return {"ticker": body.ticker, "count": 0, "data": []}
    return {"ticker": body.ticker, "count": len(df), "data": _records(df)}


@router.get("/earnings-calendar")
async def get_earnings_calendar(
    days: int = Query(7, ge=1, le=90),
    user: str = Depends(get_current_user),
):
    """Recent 8-K Item 2.02 earnings releases."""
    from src.edgar import fetch_recent_earnings_calendar
    df = fetch_recent_earnings_calendar(days=days)
    if df is None or df.empty:
        return {"days": days, "count": 0, "data": []}
    return {"days": days, "count": len(df), "data": _records(df)}

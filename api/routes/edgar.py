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
    """Fetch recent insider transactions from Polygon."""
    from src.data_engine import fetch_insider_transactions
    txns = fetch_insider_transactions(ticker.upper())
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

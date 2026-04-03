"""SEC EDGAR endpoints — 13F holdings, insider transactions, 8-K events, congressional trades."""

import logging
from fastapi import APIRouter, Depends, Query
from api.deps import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/13f/{cik}")
async def get_13f_holdings(
    cik: str,
    user: str = Depends(get_current_user),
):
    """Fetch 13F institutional holdings for a fund by CIK."""
    from src.edgar import fetch_13f_from_xml
    holdings = fetch_13f_from_xml(cik)
    if not holdings:
        return {"cik": cik, "holdings": []}
    return {"cik": cik, "count": len(holdings), "holdings": holdings}


@router.get("/insider/{ticker}")
async def get_insider_transactions(
    ticker: str,
    user: str = Depends(get_current_user),
):
    """Fetch recent insider transactions from Polygon."""
    from src.data_engine import fetch_insider_transactions
    txns = fetch_insider_transactions(ticker.upper())
    if txns is None or txns.empty:
        return {"ticker": ticker, "data": []}
    txns = txns.head(50).copy()
    for col in txns.columns:
        if txns[col].dtype == "datetime64[ns]":
            txns[col] = txns[col].astype(str)
    return {"ticker": ticker, "count": len(txns), "data": txns.to_dict(orient="records")}


@router.get("/8k/{ticker}")
async def get_8k_events(
    ticker: str,
    user: str = Depends(get_current_user),
):
    """Fetch recent 8-K filings for a ticker."""
    from src.edgar import fetch_recent_8k
    filings = fetch_recent_8k(ticker.upper())
    if not filings:
        return {"ticker": ticker, "data": []}
    return {"ticker": ticker, "count": len(filings), "data": filings[:30]}


@router.get("/13d")
async def get_recent_13d(user: str = Depends(get_current_user)):
    """Fetch recent 13D activist investor filings."""
    from src.edgar import fetch_recent_13d
    filings = fetch_recent_13d()
    if not filings:
        return {"data": []}
    return {"count": len(filings), "data": filings[:30]}


@router.get("/congressional-trades")
async def get_congressional_trades(user: str = Depends(get_current_user)):
    """Fetch recent congressional stock trades."""
    from src.edgar import fetch_congressional_trades
    trades = fetch_congressional_trades()
    if not trades:
        return {"data": []}
    return {"count": len(trades), "data": trades[:50]}


@router.get("/funds")
async def get_tracked_funds(user: str = Depends(get_current_user)):
    """Get list of tracked institutional funds with CIKs."""
    from src.edgar import TRACKED_FUNDS
    return {"funds": [{"name": name, "cik": cik} for name, cik in TRACKED_FUNDS.items()]}

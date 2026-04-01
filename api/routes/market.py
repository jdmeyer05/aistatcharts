"""Market data endpoints — prices, snapshots, options chains."""

from fastapi import APIRouter, Depends, Query
from api.deps import get_current_user

router = APIRouter()


@router.get("/snapshot")
async def batch_snapshot(
    tickers: str = Query(..., description="Comma-separated tickers"),
    user: str = Depends(get_current_user),
):
    """Get current prices for multiple tickers."""
    from src.data_engine import polygon_batch_snapshot
    symbols = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    return polygon_batch_snapshot(symbols)


@router.get("/history/{ticker}")
async def price_history(
    ticker: str,
    days: int = Query(252, ge=1, le=5040),
    user: str = Depends(get_current_user),
):
    """Get daily OHLCV price history."""
    from src.data_engine import fetch_massive_data
    df = fetch_massive_data(ticker.upper(), days)
    if df is None or df.empty:
        return {"ticker": ticker, "data": []}
    df = df.reset_index()
    df.columns = [str(c) for c in df.columns]
    return {"ticker": ticker, "data": df.to_dict(orient="records")}


@router.get("/chain/{ticker}")
async def options_chain(
    ticker: str,
    expiration: str = Query(None, description="Specific expiration date (YYYY-MM-DD)"),
    user: str = Depends(get_current_user),
):
    """Get full options chain with Greeks."""
    from src.data_engine import fetch_options_chain
    df = fetch_options_chain(ticker.upper(), expiration)
    if df is None or df.empty:
        return {"ticker": ticker, "data": []}
    return {"ticker": ticker, "count": len(df), "data": df.to_dict(orient="records")}


@router.get("/news")
async def market_news(user: str = Depends(get_current_user)):
    """Get latest cached market news scan."""
    from src.ai_cache import get_cached_ai
    from datetime import datetime, timedelta
    now = datetime.now()
    # Try current hour, then previous
    for offset in [0, 1]:
        t = now - timedelta(hours=offset)
        key = f"market_news_{t.strftime('%Y%m%d_%H')}"
        content = get_cached_ai(key)
        if content:
            return {"content": content, "age_hours": offset}
    return {"content": None, "age_hours": None}

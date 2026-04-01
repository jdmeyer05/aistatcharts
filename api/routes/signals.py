"""Signal engine endpoints — trade ideas, composites, signal CRUD."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from api.deps import get_current_user

router = APIRouter()


class SignalInput(BaseModel):
    source: str
    ticker: str
    direction: str  # bull, bear, neutral
    conviction: float = 0.5
    vol_view: str = "neutral"
    reasoning: str = ""


@router.get("/summary")
async def signal_summary(user: str = Depends(get_current_user)):
    """Get portfolio-level signal summary (bullish/bearish counts)."""
    from src.signal_engine import get_signal_summary
    return get_signal_summary()


@router.get("/top")
async def top_trade_ideas(
    n: int = 5,
    user: str = Depends(get_current_user),
):
    """Get top N trade ideas ranked by conviction × agreement."""
    from src.signal_engine import get_top_trade_ideas
    return get_top_trade_ideas(n)


@router.get("/composite/{ticker}")
async def signal_composite(ticker: str, user: str = Depends(get_current_user)):
    """Get composite signal for a specific ticker."""
    from src.signal_engine import get_signals, compute_composite
    signals = get_signals(ticker=ticker.upper())
    if not signals:
        return {"ticker": ticker, "composite": None, "signals": []}
    composite = compute_composite(signals)
    return {"ticker": ticker, "composite": composite, "signals": signals}


@router.post("/write")
async def write_signal(signal: SignalInput, user: str = Depends(get_current_user)):
    """Write a new signal to the engine."""
    from src.signal_engine import write_signal
    write_signal(
        source=signal.source,
        ticker=signal.ticker.upper(),
        direction=signal.direction,
        conviction=signal.conviction,
        vol_view=signal.vol_view,
        reasoning=signal.reasoning,
    )
    return {"status": "ok"}

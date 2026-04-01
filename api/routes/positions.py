"""Position book endpoints — trade tracking, P&L, portfolio summary."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from api.deps import get_current_user

router = APIRouter()


class PositionInput(BaseModel):
    ticker: str
    type: str  # stock, iron_condor, calendar_spread, call, put
    qty: int
    entry_price: float
    details: dict = {}
    source_page: str = ""


class CloseInput(BaseModel):
    close_price: float
    exit_thesis: str = ""


@router.get("/")
async def list_positions(
    status: str = "open",
    user: str = Depends(get_current_user),
):
    """Get all positions, optionally filtered by status."""
    from src.position_book import get_positions
    return get_positions(status)


@router.get("/summary")
async def portfolio_summary(user: str = Depends(get_current_user)):
    """Get portfolio-level summary with P&L and alerts."""
    from src.position_book import get_portfolio_summary
    return get_portfolio_summary()


@router.post("/add")
async def add_position(pos: PositionInput, user: str = Depends(get_current_user)):
    """Add a new position to the book."""
    from src.position_book import add_position
    pos_id = add_position(
        ticker=pos.ticker.upper(),
        type=pos.type,
        qty=pos.qty,
        entry_price=pos.entry_price,
        details=pos.details,
        source_page=pos.source_page,
    )
    return {"id": pos_id}


@router.post("/{pos_id}/close")
async def close_position(pos_id: str, data: CloseInput, user: str = Depends(get_current_user)):
    """Close a position by ID."""
    from src.position_book import close_position
    close_position(pos_id, data.close_price, data.exit_thesis)
    return {"status": "closed", "id": pos_id}


@router.delete("/{pos_id}")
async def remove_position(pos_id: str, user: str = Depends(get_current_user)):
    """Remove a position entirely."""
    from src.position_book import remove_position
    remove_position(pos_id)
    return {"status": "removed", "id": pos_id}

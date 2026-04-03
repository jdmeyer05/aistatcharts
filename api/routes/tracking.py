"""Prediction tracking & signal performance endpoints."""

import logging
from fastapi import APIRouter, Depends, Query
from api.deps import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/predictions")
async def get_predictions(
    status: str = Query(None, description="Filter: pending, correct, incorrect"),
    source: str = Query(None, description="Filter by source tool"),
    limit: int = Query(100, ge=1, le=500),
    user: str = Depends(get_current_user),
):
    """Get prediction log with outcomes."""
    from src.prediction_tracker import load_predictions
    preds = load_predictions()
    if not preds:
        return {"count": 0, "data": []}
    if status:
        preds = [p for p in preds if p.get("status") == status]
    if source:
        preds = [p for p in preds if p.get("source") == source]
    preds = preds[-limit:]
    return {"count": len(preds), "data": preds}


@router.get("/accuracy")
async def get_accuracy_summary(user: str = Depends(get_current_user)):
    """Get accuracy summary by source tool."""
    from src.prediction_tracker import load_predictions
    preds = load_predictions()
    if not preds:
        return {"total": 0, "evaluated": 0, "correct": 0, "accuracy": 0, "by_source": {}}

    evaluated = [p for p in preds if p.get("status") in ("correct", "incorrect")]
    correct = [p for p in evaluated if p.get("status") == "correct"]

    # By source
    sources: dict = {}
    for p in evaluated:
        src = p.get("source", "unknown")
        if src not in sources:
            sources[src] = {"total": 0, "correct": 0}
        sources[src]["total"] += 1
        if p.get("status") == "correct":
            sources[src]["correct"] += 1

    for src in sources:
        sources[src]["accuracy"] = sources[src]["correct"] / sources[src]["total"] if sources[src]["total"] > 0 else 0

    return {
        "total": len(preds),
        "evaluated": len(evaluated),
        "correct": len(correct),
        "accuracy": len(correct) / len(evaluated) if evaluated else 0,
        "by_source": sources,
    }


@router.get("/closed-positions")
async def get_closed_positions(
    limit: int = Query(50, ge=1, le=200),
    user: str = Depends(get_current_user),
):
    """Get closed positions with P&L."""
    from src.position_book import get_positions
    positions = get_positions(status="closed")
    if not positions:
        return {"count": 0, "data": []}
    return {"count": len(positions), "data": positions[-limit:]}
